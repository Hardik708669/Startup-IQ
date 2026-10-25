"""Local SQLite persistence for Startup IQ assessment snapshots and history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from recommendations import Recommendation
from scoring import AssessmentResult, DimensionScore

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "startup_iq.db"

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS startups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    startup_id INTEGER NOT NULL REFERENCES startups(id),
    submission_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    overall_score REAL NOT NULL,
    rubric_version TEXT NOT NULL,
    assessment_date TEXT,
    inputs_json TEXT NOT NULL,
    recommendations_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assessment_dimensions (
    assessment_id INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    dimension_id TEXT NOT NULL,
    dimension_name TEXT NOT NULL,
    rating REAL NOT NULL,
    weight INTEGER NOT NULL,
    contribution REAL NOT NULL,
    rationale TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    missing_evidence INTEGER NOT NULL,
    PRIMARY KEY (assessment_id, dimension_id)
);
CREATE INDEX IF NOT EXISTS idx_assessments_created_at ON assessments(created_at DESC);
"""


def get_connection(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection, creating its parent directory if needed."""
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(path: str | Path = DEFAULT_DB_PATH) -> Path:
    """Create the local schema safely and return the database path."""
    database_path = Path(path)
    with get_connection(database_path) as connection:
        connection.executescript(SCHEMA)
    return database_path


def make_submission_key(profile: Mapping[str, Any], inputs: Mapping[str, Any]) -> str:
    """Create a stable idempotency key for one exact profile/assessment snapshot."""
    payload = json.dumps({"profile": profile, "inputs": inputs}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save_assessment(profile: Mapping[str, Any], inputs: Mapping[str, Any], result: AssessmentResult,
                    recommendations: Sequence[Recommendation], assessment_date: str | None = None,
                    path: str | Path = DEFAULT_DB_PATH) -> int:
    """Save one immutable assessment snapshot, returning its ID.

    Re-submitting identical profile and assessment inputs returns the existing
    record; changed inputs create a new historical assessment.
    """
    initialize_database(path)
    submission_key = make_submission_key(profile, inputs)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(path) as connection:
        existing = connection.execute("SELECT id FROM assessments WHERE submission_key = ?", (submission_key,)).fetchone()
        if existing:
            return int(existing["id"])
        startup_id = connection.execute(
            "INSERT INTO startups (profile_json, created_at) VALUES (?, ?)",
            (json.dumps(dict(profile), sort_keys=True, default=str), now),
        ).lastrowid
        assessment_id = connection.execute(
            """INSERT INTO assessments
            (startup_id, submission_key, created_at, overall_score, rubric_version, assessment_date, inputs_json, recommendations_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (startup_id, submission_key, now, result.overall_score, result.rubric_version,
             assessment_date, json.dumps(dict(inputs), sort_keys=True, default=str),
             json.dumps([item.as_dict() for item in recommendations], sort_keys=True)),
        ).lastrowid
        connection.executemany(
            """INSERT INTO assessment_dimensions
            (assessment_id, dimension_id, dimension_name, rating, weight, contribution, rationale, evidence_status, missing_evidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(assessment_id, item.identifier, item.name, item.rating, item.weight, item.contribution,
              item.rationale, item.evidence_status, int(item.missing_evidence)) for item in result.dimensions],
        )
        return int(assessment_id)


def list_assessments(path: str | Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Return assessment summaries newest first."""
    initialize_database(path)
    with get_connection(path) as connection:
        rows = connection.execute(
            """SELECT a.id, a.created_at, a.assessment_date, a.overall_score, a.rubric_version,
                      json_extract(s.profile_json, '$.startup_name') AS startup_name,
                      json_extract(s.profile_json, '$.industry') AS industry,
                      json_extract(s.profile_json, '$.stage') AS stage
               FROM assessments a JOIN startups s ON s.id = a.startup_id
               ORDER BY a.created_at DESC, a.id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_assessment(assessment_id: int, path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    """Retrieve one immutable assessment snapshot, or ``None`` if absent."""
    initialize_database(path)
    with get_connection(path) as connection:
        row = connection.execute(
            "SELECT a.*, s.profile_json FROM assessments a JOIN startups s ON s.id = a.startup_id WHERE a.id = ?",
            (assessment_id,),
        ).fetchone()
        if row is None:
            return None
        dimensions = connection.execute(
            "SELECT * FROM assessment_dimensions WHERE assessment_id = ? ORDER BY rowid", (assessment_id,)
        ).fetchall()
    result_dimensions = tuple(DimensionScore(
        identifier=item["dimension_id"], name=item["dimension_name"], rating=item["rating"],
        weight=item["weight"], contribution=item["contribution"], rationale=item["rationale"],
        evidence_status=item["evidence_status"], missing_evidence=bool(item["missing_evidence"]),
    ) for item in dimensions)
    return {
        "id": int(row["id"]),
        "profile": json.loads(row["profile_json"]),
        "inputs": json.loads(row["inputs_json"]),
        "result": AssessmentResult(float(row["overall_score"]), row["rubric_version"], result_dimensions),
        "recommendations": tuple(Recommendation(**item) for item in json.loads(row["recommendations_json"])),
        "assessment_date": row["assessment_date"],
        "created_at": row["created_at"],
    }
