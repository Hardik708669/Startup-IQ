"""Local SQLite persistence for Startup IQ assessment snapshots and history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from datetime import date
from typing import Any

from recommendations import Recommendation
from scoring import AssessmentResult, DimensionScore

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "startup_iq.db"
DEFAULT_ATTACHMENT_DIR = PROJECT_ROOT / "data" / "attachments"
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_ATTACHMENT_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".png", ".jpg", ".jpeg"}
EVIDENCE_TYPES = (
    "Customer interviews", "Customer survey results", "Market research", "Competitor research",
    "Financial assumptions", "Pricing validation", "Letters of intent", "Pilot results",
    "Product benchmarks", "Legal or regulatory research", "Technical documentation", "Other",
)
VERIFICATION_STATUSES = ("Not reviewed", "Founder submitted", "Reviewed by founder", "Independently verified", "Needs follow-up")

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
CREATE TABLE IF NOT EXISTS evidence_records (
    evidence_id TEXT PRIMARY KEY,
    startup_id INTEGER NOT NULL REFERENCES startups(id),
    evidence_title TEXT NOT NULL,
    evidence_description TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    related_dimension TEXT NOT NULL,
    source_name TEXT,
    source_url TEXT,
    source_date TEXT,
    submission_date TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    founder_notes TEXT,
    attachment_path TEXT,
    attachment_original_name TEXT,
    attachment_size INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_evidence_startup ON evidence_records(startup_id, archived_at);
CREATE TABLE IF NOT EXISTS assessment_evidence (
    assessment_id INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (assessment_id, evidence_id)
);
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


def ensure_startup(profile: Mapping[str, Any], path: str | Path = DEFAULT_DB_PATH) -> int:
    """Create a startup snapshot for evidence registration and return its ID."""
    initialize_database(path)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(path) as connection:
        row = connection.execute("SELECT id FROM startups WHERE profile_json = ? ORDER BY id DESC LIMIT 1",
                                 (json.dumps(dict(profile), sort_keys=True, default=str),)).fetchone()
        if row:
            return int(row["id"])
        return int(connection.execute("INSERT INTO startups (profile_json, created_at) VALUES (?, ?)",
                                     (json.dumps(dict(profile), sort_keys=True, default=str), now)).lastrowid)


def _validate_evidence_fields(evidence: Mapping[str, Any]) -> None:
    if not str(evidence.get("evidence_title", "")).strip():
        raise ValueError("Evidence title is required.")
    if not str(evidence.get("evidence_description", "")).strip():
        raise ValueError("Evidence description is required.")
    if not str(evidence.get("evidence_type", "")).strip():
        raise ValueError("Evidence type is required.")
    if not str(evidence.get("related_dimension", "")).strip():
        raise ValueError("Related assessment dimension is required.")
    if evidence.get("verification_status") not in VERIFICATION_STATUSES:
        raise ValueError("Invalid verification status.")


def save_evidence(profile: Mapping[str, Any], evidence: Mapping[str, Any], path: str | Path = DEFAULT_DB_PATH) -> str:
    """Create one evidence record. The generated ID makes reruns safe when supplied."""
    _validate_evidence_fields(evidence)
    startup_id = ensure_startup(profile, path)
    evidence_id = str(evidence.get("evidence_id") or uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    values = (evidence_id, startup_id, str(evidence["evidence_title"]).strip(),
              str(evidence["evidence_description"]).strip(), evidence["evidence_type"],
              str(evidence["related_dimension"]).strip(), str(evidence.get("source_name") or "").strip(),
              str(evidence.get("source_url") or "").strip(), str(evidence.get("source_date") or "").strip(),
              str(evidence.get("submission_date") or date.today().isoformat()), evidence["verification_status"],
              str(evidence.get("founder_notes") or "").strip(), evidence.get("attachment_path"),
              evidence.get("attachment_original_name"), evidence.get("attachment_size"), now, now)
    initialize_database(path)
    with get_connection(path) as connection:
        connection.execute("""INSERT INTO evidence_records
            (evidence_id,startup_id,evidence_title,evidence_description,evidence_type,related_dimension,
             source_name,source_url,source_date,submission_date,verification_status,founder_notes,
             attachment_path,attachment_original_name,attachment_size,created_at,updated_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
    return evidence_id


def list_evidence(profile: Mapping[str, Any] | None = None, *, startup_id: int | None = None,
                  dimension: str | None = None, evidence_type: str | None = None,
                  verification_status: str | None = None, search: str = "",
                  include_archived: bool = False, path: str | Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    initialize_database(path)
    with get_connection(path) as connection:
        if startup_id is None and profile is not None:
            row = connection.execute("SELECT id FROM startups WHERE profile_json = ? ORDER BY id DESC LIMIT 1",
                                     (json.dumps(dict(profile), sort_keys=True, default=str),)).fetchone()
            startup_id = int(row["id"]) if row else None
        clauses, params = [], []
        if startup_id is not None: clauses.append("startup_id = ?"); params.append(startup_id)
        if dimension: clauses.append("related_dimension = ?"); params.append(dimension)
        if evidence_type: clauses.append("evidence_type = ?"); params.append(evidence_type)
        if verification_status: clauses.append("verification_status = ?"); params.append(verification_status)
        if search:
            clauses.append("(evidence_title LIKE ? OR evidence_description LIKE ? OR source_name LIKE ?)")
            params.extend([f"%{search}%"] * 3)
        if not include_archived: clauses.append("archived_at IS NULL")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = connection.execute("SELECT * FROM evidence_records" + where + " ORDER BY created_at DESC", params).fetchall()
    return [dict(row) for row in rows]


def get_evidence(evidence_id: str, path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    initialize_database(path)
    with get_connection(path) as connection:
        row = connection.execute("SELECT * FROM evidence_records WHERE evidence_id = ?", (evidence_id,)).fetchone()
    return dict(row) if row else None


def update_evidence(evidence_id: str, changes: Mapping[str, Any], path: str | Path = DEFAULT_DB_PATH) -> bool:
    current = get_evidence(evidence_id, path)
    if current is None: return False
    merged = {**current, **dict(changes)}
    _validate_evidence_fields(merged)
    allowed = {"evidence_title", "evidence_description", "evidence_type", "related_dimension", "source_name",
               "source_url", "source_date", "verification_status", "founder_notes", "attachment_path",
               "attachment_original_name", "attachment_size"}
    fields = [(key, merged[key]) for key in allowed if key in changes]
    if not fields: return True
    fields.append(("updated_at", datetime.now(timezone.utc).isoformat()))
    with get_connection(path) as connection:
        connection.execute("UPDATE evidence_records SET " + ", ".join(f"{key} = ?" for key, _ in fields) + " WHERE evidence_id = ?",
                           [value for _, value in fields] + [evidence_id])
    return True


def archive_evidence(evidence_id: str, path: str | Path = DEFAULT_DB_PATH) -> bool:
    initialize_database(path)
    with get_connection(path) as connection:
        cursor = connection.execute("UPDATE evidence_records SET archived_at = ?, updated_at = ? WHERE evidence_id = ? AND archived_at IS NULL",
                                    (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(), evidence_id))
    return cursor.rowcount == 1


def validate_attachment(filename: str, size: int, max_bytes: int = MAX_ATTACHMENT_BYTES) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_ATTACHMENT_EXTENSIONS: raise ValueError("Unsupported attachment type.")
    if size < 0 or size > max_bytes: raise ValueError(f"Attachment exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    return suffix


def store_attachment(filename: str, data: bytes, attachment_dir: str | Path = DEFAULT_ATTACHMENT_DIR) -> tuple[str, str, int]:
    """Store bytes under a UUID filename, never using user input as a path."""
    suffix = validate_attachment(filename, len(data))
    directory = Path(attachment_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}{suffix}"
    target = (directory / safe_name).resolve()
    if directory not in target.parents: raise ValueError("Unsafe attachment path.")
    target.write_bytes(data)
    return safe_name, Path(filename).name, len(data)


def make_submission_key(profile: Mapping[str, Any], inputs: Mapping[str, Any]) -> str:
    """Create a stable idempotency key for one exact profile/assessment snapshot."""
    payload = json.dumps({"profile": profile, "inputs": inputs}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save_assessment(profile: Mapping[str, Any], inputs: Mapping[str, Any], result: AssessmentResult,
                    recommendations: Sequence[Recommendation], assessment_date: str | None = None,
                    path: str | Path = DEFAULT_DB_PATH,
                    evidence_ids: Sequence[str] = ()) -> int:
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
        profile_json = json.dumps(dict(profile), sort_keys=True, default=str)
        startup = connection.execute("SELECT id FROM startups WHERE profile_json = ? ORDER BY id DESC LIMIT 1", (profile_json,)).fetchone()
        startup_id = startup["id"] if startup else connection.execute(
            "INSERT INTO startups (profile_json, created_at) VALUES (?, ?)", (profile_json, now)
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
        for evidence_id in evidence_ids:
            evidence = connection.execute("SELECT * FROM evidence_records WHERE evidence_id = ? AND startup_id = ? AND archived_at IS NULL",
                                          (evidence_id, startup_id)).fetchone()
            if evidence:
                connection.execute("INSERT INTO assessment_evidence (assessment_id, evidence_id, evidence_json) VALUES (?, ?, ?)",
                                   (assessment_id, evidence_id, json.dumps(dict(evidence), sort_keys=True, default=str)))
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
        evidence = connection.execute("SELECT evidence_json FROM assessment_evidence WHERE assessment_id = ? ORDER BY evidence_id",
                                      (assessment_id,)).fetchall()
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
        "evidence": [json.loads(item["evidence_json"]) for item in evidence],
        "assessment_date": row["assessment_date"],
        "created_at": row["created_at"],
    }
