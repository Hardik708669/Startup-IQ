"""Local SQLite persistence for Startup IQ assessment snapshots and history.

Extended with User Accounts, Workspaces, and Object-Level Ownership.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

from recommendations import Recommendation
from scoring import AssessmentResult, DimensionScore

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "startup_iq.db"
DEFAULT_ATTACHMENT_DIR = PROJECT_ROOT / "data" / "attachments"
DEFAULT_DOCUMENTS_DIR = PROJECT_ROOT / "data" / "documents"
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_ATTACHMENT_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".png", ".jpg", ".jpeg"}
EVIDENCE_TYPES = (
    "Customer interviews", "Customer survey results", "Market research", "Competitor research",
    "Financial assumptions", "Pricing validation", "Letters of intent", "Pilot results",
    "Product benchmarks", "Legal or regulatory research", "Technical documentation", "Other",
)
VERIFICATION_STATUSES = ("Not reviewed", "Founder submitted", "Reviewed by founder", "Independently verified", "Needs follow-up")

# Default system/legacy bootstrap identifier
LEGACY_BOOTSTRAP_USER_EMAIL = "legacy-admin@startup-iq.local"
LEGACY_BOOTSTRAP_WORKSPACE_NAME = "Default Legacy Workspace"

SCHEMA_TABLES = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    account_status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    workspace_name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS startups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    startup_id INTEGER NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
    workspace_id INTEGER REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
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

CREATE TABLE IF NOT EXISTS evidence_records (
    evidence_id TEXT PRIMARY KEY,
    startup_id INTEGER NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
    workspace_id INTEGER REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
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
    supporting_excerpt TEXT,
    attachment_path TEXT,
    attachment_original_name TEXT,
    attachment_size INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS assessment_evidence (
    assessment_id INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY (assessment_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS generated_documents (
    document_id TEXT PRIMARY KEY,
    startup_id INTEGER NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
    assessment_id INTEGER NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    workspace_id INTEGER REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    document_type TEXT NOT NULL,
    document_version INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_format TEXT NOT NULL,
    generation_mode TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'Draft',
    sections_json TEXT,
    created_at TEXT NOT NULL
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_workspaces_owner ON workspaces(owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_startups_workspace ON startups(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assessments_created_at ON assessments(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assessments_workspace ON assessments(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_startup ON evidence_records(startup_id, archived_at);
CREATE INDEX IF NOT EXISTS idx_evidence_workspace ON evidence_records(workspace_id, archived_at);
CREATE INDEX IF NOT EXISTS idx_docs_assessment ON generated_documents(assessment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_docs_workspace ON generated_documents(workspace_id, created_at DESC);
"""

SCHEMA = SCHEMA_TABLES + SCHEMA_INDEXES


def get_connection(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection, creating its parent directory if needed."""
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def backup_database(path: str | Path = DEFAULT_DB_PATH) -> Path | None:
    """Create a timestamped backup of the database before running migrations."""
    db_file = Path(path)
    if not db_file.exists():
        return None
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_file = db_file.parent / f"{db_file.stem}_backup_{now_str}{db_file.suffix}"
    shutil.copy2(db_file, backup_file)
    return backup_file


def _migrate_existing_schema(connection: sqlite3.Connection) -> None:
    """Inspect existing schema and add workspace_id / user_id columns safely if missing."""
    table_columns: dict[str, set[str]] = {}
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in tables:
        cols = {row[1] for row in connection.execute(f"PRAGMA table_info({t})").fetchall()}
        table_columns[t] = cols

    # Alter startups table if missing columns
    if "startups" in table_columns:
        if "workspace_id" not in table_columns["startups"]:
            connection.execute("ALTER TABLE startups ADD COLUMN workspace_id INTEGER REFERENCES workspaces(workspace_id) ON DELETE CASCADE")
        if "user_id" not in table_columns["startups"]:
            connection.execute("ALTER TABLE startups ADD COLUMN user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL")

    # Alter assessments table if missing columns
    if "assessments" in table_columns:
        if "workspace_id" not in table_columns["assessments"]:
            connection.execute("ALTER TABLE assessments ADD COLUMN workspace_id INTEGER REFERENCES workspaces(workspace_id) ON DELETE CASCADE")
        if "user_id" not in table_columns["assessments"]:
            connection.execute("ALTER TABLE assessments ADD COLUMN user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL")

    # Alter evidence_records table if missing columns
    if "evidence_records" in table_columns:
        if "workspace_id" not in table_columns["evidence_records"]:
            connection.execute("ALTER TABLE evidence_records ADD COLUMN workspace_id INTEGER REFERENCES workspaces(workspace_id) ON DELETE CASCADE")
        if "user_id" not in table_columns["evidence_records"]:
            connection.execute("ALTER TABLE evidence_records ADD COLUMN user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL")
        if "supporting_excerpt" not in table_columns["evidence_records"]:
            connection.execute("ALTER TABLE evidence_records ADD COLUMN supporting_excerpt TEXT")

    # Alter generated_documents table if missing columns
    if "generated_documents" in table_columns:
        if "workspace_id" not in table_columns["generated_documents"]:
            connection.execute("ALTER TABLE generated_documents ADD COLUMN workspace_id INTEGER REFERENCES workspaces(workspace_id) ON DELETE CASCADE")
        if "user_id" not in table_columns["generated_documents"]:
            connection.execute("ALTER TABLE generated_documents ADD COLUMN user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL")


def initialize_database(path: str | Path = DEFAULT_DB_PATH) -> Path:
    """Create the local schema safely, apply migrations, and return the database path."""
    database_path = Path(path)
    with get_connection(database_path) as connection:
        connection.executescript(SCHEMA_TABLES)
        _migrate_existing_schema(connection)
        connection.executescript(SCHEMA_INDEXES)
    return database_path


# ---------------------------------------------------------------------------
# User Authentication & Account Operations
# ---------------------------------------------------------------------------

def create_user(
    full_name: str,
    email: str,
    password_hash: str,
    path: str | Path = DEFAULT_DB_PATH,
) -> int:
    """Create a new user record. Enforces unique email (case-insensitive)."""
    initialize_database(path)
    now = datetime.now(timezone.utc).isoformat()
    clean_email = email.strip().lower()
    clean_name = full_name.strip()
    with get_connection(path) as connection:
        existing = connection.execute("SELECT user_id FROM users WHERE email = ?", (clean_email,)).fetchone()
        if existing:
            raise ValueError(f"An account with email '{clean_email}' already exists.")
        cursor = connection.execute(
            """INSERT INTO users (full_name, email, password_hash, account_status, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, ?)""",
            (clean_name, clean_email, password_hash, now, now)
        )
        return int(cursor.lastrowid)


def get_user_by_email(email: str, path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    """Fetch user by email (case-insensitive)."""
    initialize_database(path)
    clean_email = email.strip().lower()
    with get_connection(path) as connection:
        row = connection.execute("SELECT * FROM users WHERE email = ?", (clean_email,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int, path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    """Fetch user by integer ID."""
    initialize_database(path)
    with get_connection(path) as connection:
        row = connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def update_user_last_login(user_id: int, path: str | Path = DEFAULT_DB_PATH) -> None:
    """Update last_login_at timestamp for a user."""
    initialize_database(path)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(path) as connection:
        connection.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE user_id = ?", (now, now, user_id))


# ---------------------------------------------------------------------------
# Workspace Operations
# ---------------------------------------------------------------------------

def create_workspace(
    owner_user_id: int,
    workspace_name: str,
    description: str = "",
    path: str | Path = DEFAULT_DB_PATH,
) -> int:
    """Create a new workspace for an owner user."""
    initialize_database(path)
    now = datetime.now(timezone.utc).isoformat()
    clean_name = workspace_name.strip()
    if not clean_name:
        raise ValueError("Workspace name cannot be empty.")
    with get_connection(path) as connection:
        # Verify user exists
        user = connection.execute("SELECT user_id FROM users WHERE user_id = ?", (owner_user_id,)).fetchone()
        if not user:
            raise ValueError(f"User ID {owner_user_id} does not exist.")
        cursor = connection.execute(
            """INSERT INTO workspaces (owner_user_id, workspace_name, description, status, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, ?)""",
            (owner_user_id, clean_name, description.strip(), now, now)
        )
        return int(cursor.lastrowid)


def list_user_workspaces(user_id: int, include_archived: bool = False, path: str | Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """List workspaces owned by the user."""
    initialize_database(path)
    with get_connection(path) as connection:
        status_clause = "" if include_archived else "AND status = 'active'"
        rows = connection.execute(
            f"SELECT * FROM workspaces WHERE owner_user_id = ? {status_clause} ORDER BY created_at ASC",
            (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_workspace(workspace_id: int, user_id: int | None = None, path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    """Fetch workspace by ID, enforcing user ownership if user_id is provided."""
    initialize_database(path)
    with get_connection(path) as connection:
        if user_id is not None:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ? AND owner_user_id = ?",
                (workspace_id, user_id)
            ).fetchone()
        else:
            row = connection.execute("SELECT * FROM workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
    return dict(row) if row else None


def update_workspace(workspace_id: int, user_id: int, workspace_name: str, description: str = "", path: str | Path = DEFAULT_DB_PATH) -> bool:
    """Update workspace details, ensuring ownership."""
    clean_name = workspace_name.strip()
    if not clean_name:
        raise ValueError("Workspace name cannot be empty.")
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(path) as connection:
        cursor = connection.execute(
            "UPDATE workspaces SET workspace_name = ?, description = ?, updated_at = ? WHERE workspace_id = ? AND owner_user_id = ?",
            (clean_name, description.strip(), now, workspace_id, user_id)
        )
        return cursor.rowcount == 1


def archive_workspace(workspace_id: int, user_id: int, path: str | Path = DEFAULT_DB_PATH) -> bool:
    """Archive a workspace owned by the user."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(path) as connection:
        cursor = connection.execute(
            "UPDATE workspaces SET status = 'archived', updated_at = ? WHERE workspace_id = ? AND owner_user_id = ? AND status = 'active'",
            (now, workspace_id, user_id)
        )
        return cursor.rowcount == 1


# ---------------------------------------------------------------------------
# Legacy Data Bootstrapping
# ---------------------------------------------------------------------------

def bootstrap_legacy_ownership(admin_email: str = LEGACY_BOOTSTRAP_USER_EMAIL, path: str | Path = DEFAULT_DB_PATH) -> tuple[int, int]:
    """Ensure an administrator user and default workspace exist to own unassigned legacy data.

    Returns (bootstrap_user_id, bootstrap_workspace_id).
    """
    initialize_database(path)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(path) as connection:
        user_row = connection.execute("SELECT user_id FROM users WHERE email = ?", (admin_email,)).fetchone()
        if user_row:
            user_id = int(user_row["user_id"])
        else:
            # Deterministic placeholder hash for bootstrap
            dummy_hash = "$argon2id$v=19$m=65536,t=3,p=4$bootstrap$localonlydummyhashnotforlogin"
            user_id = int(connection.execute(
                """INSERT INTO users (full_name, email, password_hash, account_status, created_at, updated_at)
                VALUES (?, ?, ?, 'system', ?, ?)""",
                ("Legacy Administrator", admin_email, dummy_hash, now, now)
            ).lastrowid)

        ws_row = connection.execute("SELECT workspace_id FROM workspaces WHERE owner_user_id = ? AND workspace_name = ?",
                                    (user_id, LEGACY_BOOTSTRAP_WORKSPACE_NAME)).fetchone()
        if ws_row:
            ws_id = int(ws_row["workspace_id"])
        else:
            ws_id = int(connection.execute(
                """INSERT INTO workspaces (owner_user_id, workspace_name, description, status, created_at, updated_at)
                VALUES (?, ?, 'Workspace automatically preserving legacy assessment and startup records.', 'active', ?, ?)""",
                (user_id, LEGACY_BOOTSTRAP_WORKSPACE_NAME, now, now)
            ).lastrowid)

        # Associate any unassigned records with legacy workspace
        connection.execute("UPDATE startups SET workspace_id = ?, user_id = ? WHERE workspace_id IS NULL", (ws_id, user_id))
        connection.execute("UPDATE assessments SET workspace_id = ?, user_id = ? WHERE workspace_id IS NULL", (ws_id, user_id))
        connection.execute("UPDATE evidence_records SET workspace_id = ?, user_id = ? WHERE workspace_id IS NULL", (ws_id, user_id))
        connection.execute("UPDATE generated_documents SET workspace_id = ?, user_id = ? WHERE workspace_id IS NULL", (ws_id, user_id))

    return user_id, ws_id


# ---------------------------------------------------------------------------
# Object-Level Ownership: Startup Profiles
# ---------------------------------------------------------------------------

def ensure_startup(
    profile: Mapping[str, Any],
    path: str | Path = DEFAULT_DB_PATH,
    workspace_id: int | None = None,
    user_id: int | None = None,
) -> int:
    """Create a startup snapshot within a workspace, or return existing snapshot ID."""
    initialize_database(path)
    now = datetime.now(timezone.utc).isoformat()
    profile_json = json.dumps(dict(profile), sort_keys=True, default=str)
    with get_connection(path) as connection:
        if workspace_id is not None:
            row = connection.execute(
                "SELECT id FROM startups WHERE profile_json = ? AND workspace_id = ? ORDER BY id DESC LIMIT 1",
                (profile_json, workspace_id)
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT id FROM startups WHERE profile_json = ? ORDER BY id DESC LIMIT 1",
                (profile_json,)
            ).fetchone()
        if row:
            return int(row["id"])
        return int(connection.execute(
            "INSERT INTO startups (profile_json, workspace_id, user_id, created_at) VALUES (?, ?, ?, ?)",
            (profile_json, workspace_id, user_id, now)
        ).lastrowid)


def list_workspace_startups(workspace_id: int, user_id: int | None = None, path: str | Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """List distinct startups within a workspace with ownership enforcement."""
    initialize_database(path)
    with get_connection(path) as connection:
        # Verify workspace belongs to user if user_id is provided
        if user_id is not None:
            ws = connection.execute("SELECT workspace_id FROM workspaces WHERE workspace_id = ? AND owner_user_id = ?",
                                    (workspace_id, user_id)).fetchone()
            if not ws:
                return []
        rows = connection.execute(
            "SELECT id, profile_json, created_at FROM startups WHERE workspace_id = ? ORDER BY id DESC",
            (workspace_id,)
        ).fetchall()
    results = []
    seen_names = set()
    for r in rows:
        p = json.loads(r["profile_json"])
        s_name = p.get("startup_name", "Unnamed")
        if s_name not in seen_names:
            seen_names.add(s_name)
            results.append({"id": r["id"], "profile": p, "created_at": r["created_at"]})
    return results


# ---------------------------------------------------------------------------
# Object-Level Ownership: Evidence Records
# ---------------------------------------------------------------------------

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


def save_evidence(
    profile: Mapping[str, Any],
    evidence: Mapping[str, Any],
    path: str | Path = DEFAULT_DB_PATH,
    workspace_id: int | None = None,
    user_id: int | None = None,
) -> str:
    """Create one evidence record linked to a startup and workspace."""
    _validate_evidence_fields(evidence)
    startup_id = ensure_startup(profile, path, workspace_id=workspace_id, user_id=user_id)
    evidence_id = str(evidence.get("evidence_id") or uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    values = (
        evidence_id, startup_id, workspace_id, user_id, str(evidence["evidence_title"]).strip(),
        str(evidence["evidence_description"]).strip(), evidence["evidence_type"],
        str(evidence["related_dimension"]).strip(), str(evidence.get("source_name") or "").strip(),
        str(evidence.get("source_url") or "").strip(), str(evidence.get("source_date") or "").strip(),
        str(evidence.get("submission_date") or date.today().isoformat()), evidence["verification_status"],
        str(evidence.get("founder_notes") or "").strip(),
        str(evidence.get("supporting_excerpt") or "").strip() or None,
        evidence.get("attachment_path"),
        evidence.get("attachment_original_name"), evidence.get("attachment_size"), now, now
    )
    initialize_database(path)
    with get_connection(path) as connection:
        connection.execute(
            """INSERT INTO evidence_records
            (evidence_id, startup_id, workspace_id, user_id, evidence_title, evidence_description, evidence_type,
             related_dimension, source_name, source_url, source_date, submission_date, verification_status,
             founder_notes, supporting_excerpt, attachment_path, attachment_original_name, attachment_size, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values
        )
    return evidence_id


def list_evidence(
    profile: Mapping[str, Any] | None = None,
    *,
    startup_id: int | None = None,
    dimension: str | None = None,
    evidence_type: str | None = None,
    verification_status: str | None = None,
    search: str = "",
    include_archived: bool = False,
    workspace_id: int | None = None,
    user_id: int | None = None,
    path: str | Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """List evidence scoped to startup, workspace, and owner user."""
    if profile is not None and workspace_id is None and user_id is None:
        raise ValueError("Evidence profile queries require workspace_id or user_id ownership scope.")
    initialize_database(path)
    with get_connection(path) as connection:
        if startup_id is None and profile is not None:
            if workspace_id is not None:
                row = connection.execute(
                    "SELECT id FROM startups WHERE profile_json = ? AND workspace_id = ? ORDER BY id DESC LIMIT 1",
                    (json.dumps(dict(profile), sort_keys=True, default=str), workspace_id)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT id FROM startups WHERE profile_json = ? ORDER BY id DESC LIMIT 1",
                    (json.dumps(dict(profile), sort_keys=True, default=str),)
                ).fetchone()
            startup_id = int(row["id"]) if row else None

        clauses, params = [], []
        if startup_id is not None:
            clauses.append("startup_id = ?")
            params.append(startup_id)
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if user_id is not None:
            clauses.append("(user_id = ? OR workspace_id IN (SELECT workspace_id FROM workspaces WHERE owner_user_id = ?))")
            params.extend([user_id, user_id])
        if dimension:
            clauses.append("related_dimension = ?")
            params.append(dimension)
        if evidence_type:
            clauses.append("evidence_type = ?")
            params.append(evidence_type)
        if verification_status:
            clauses.append("verification_status = ?")
            params.append(verification_status)
        if search:
            clauses.append("(evidence_title LIKE ? OR evidence_description LIKE ? OR source_name LIKE ?)")
            params.extend([f"%{search}%"] * 3)
        if not include_archived:
            clauses.append("archived_at IS NULL")

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = connection.execute(
            f"SELECT * FROM evidence_records{where} ORDER BY created_at DESC", params
        ).fetchall()
    return [dict(row) for row in rows]


def get_evidence(
    evidence_id: str,
    path: str | Path = DEFAULT_DB_PATH,
    *,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    """Retrieve evidence by ID, enforcing user ownership if user_id is passed."""
    initialize_database(path)
    with get_connection(path) as connection:
        if user_id is not None:
            row = connection.execute(
                """SELECT e.* FROM evidence_records e
                LEFT JOIN workspaces w ON e.workspace_id = w.workspace_id
                WHERE e.evidence_id = ? AND (e.user_id = ? OR w.owner_user_id = ?)""",
                (evidence_id, user_id, user_id)
            ).fetchone()
        else:
            row = connection.execute("SELECT * FROM evidence_records WHERE evidence_id = ?", (evidence_id,)).fetchone()
    return dict(row) if row else None


def update_evidence(
    evidence_id: str,
    changes: Mapping[str, Any],
    path: str | Path = DEFAULT_DB_PATH,
    *,
    user_id: int | None = None,
) -> bool:
    """Update evidence with object-level ownership check."""
    current = get_evidence(evidence_id, path=path, user_id=user_id)
    if current is None:
        return False
    merged = {**current, **dict(changes)}
    _validate_evidence_fields(merged)
    allowed = {
        "evidence_title", "evidence_description", "evidence_type", "related_dimension", "source_name",
        "source_url", "source_date", "verification_status", "founder_notes", "supporting_excerpt", "attachment_path",
        "attachment_original_name", "attachment_size"
    }
    fields = [(key, merged[key]) for key in allowed if key in changes]
    if not fields:
        return True
    fields.append(("updated_at", datetime.now(timezone.utc).isoformat()))
    with get_connection(path) as connection:
        if user_id is not None:
            res = connection.execute(
                "UPDATE evidence_records SET " + ", ".join(f"{key} = ?" for key, _ in fields) +
                " WHERE evidence_id = ? AND (user_id = ? OR workspace_id IN (SELECT workspace_id FROM workspaces WHERE owner_user_id = ?))",
                [value for _, value in fields] + [evidence_id, user_id, user_id]
            )
        else:
            res = connection.execute(
                "UPDATE evidence_records SET " + ", ".join(f"{key} = ?" for key, _ in fields) + " WHERE evidence_id = ?",
                [value for _, value in fields] + [evidence_id]
            )
        return res.rowcount == 1


def archive_evidence(
    evidence_id: str,
    path: str | Path = DEFAULT_DB_PATH,
    *,
    user_id: int | None = None,
) -> bool:
    """Soft-delete evidence with ownership check."""
    initialize_database(path)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(path) as connection:
        if user_id is not None:
            cursor = connection.execute(
                """UPDATE evidence_records SET archived_at = ?, updated_at = ?
                WHERE evidence_id = ? AND archived_at IS NULL
                AND (user_id = ? OR workspace_id IN (SELECT workspace_id FROM workspaces WHERE owner_user_id = ?))""",
                (now, now, evidence_id, user_id, user_id)
            )
        else:
            cursor = connection.execute(
                "UPDATE evidence_records SET archived_at = ?, updated_at = ? WHERE evidence_id = ? AND archived_at IS NULL",
                (now, now, evidence_id)
            )
    return cursor.rowcount == 1


def validate_attachment(filename: str, size: int, max_bytes: int = MAX_ATTACHMENT_BYTES) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise ValueError("Unsupported attachment type.")
    if size < 0 or size > max_bytes:
        raise ValueError(f"Attachment exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    return suffix


def store_attachment(filename: str, data: bytes, attachment_dir: str | Path = DEFAULT_ATTACHMENT_DIR) -> tuple[str, str, int]:
    """Store bytes under a UUID filename, preventing path traversal."""
    suffix = validate_attachment(filename, len(data))
    directory = Path(attachment_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}{suffix}"
    target = (directory / safe_name).resolve()
    if directory not in target.parents:
        raise ValueError("Unsafe attachment path.")
    target.write_bytes(data)
    return safe_name, Path(filename).name, len(data)


def make_submission_key(profile: Mapping[str, Any], inputs: Mapping[str, Any], workspace_id: int | None = None) -> str:
    """Create a stable idempotency key for one exact profile/assessment snapshot."""
    payload = json.dumps({"profile": profile, "inputs": inputs, "workspace_id": workspace_id}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Object-Level Ownership: Assessments
# ---------------------------------------------------------------------------

def save_assessment(
    profile: Mapping[str, Any],
    inputs: Mapping[str, Any],
    result: AssessmentResult,
    recommendations: Sequence[Recommendation],
    assessment_date: str | None = None,
    path: str | Path = DEFAULT_DB_PATH,
    evidence_ids: Sequence[str] = (),
    workspace_id: int | None = None,
    user_id: int | None = None,
) -> int:
    """Save an immutable assessment snapshot scoped to workspace and user."""
    initialize_database(path)
    submission_key = make_submission_key(profile, inputs, workspace_id)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(path) as connection:
        existing = connection.execute("SELECT id FROM assessments WHERE submission_key = ?", (submission_key,)).fetchone()
        if existing:
            return int(existing["id"])
        startup_id = ensure_startup(profile, path, workspace_id=workspace_id, user_id=user_id)
        assessment_id = connection.execute(
            """INSERT INTO assessments
            (startup_id, workspace_id, user_id, submission_key, created_at, overall_score, rubric_version,
             assessment_date, inputs_json, recommendations_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (startup_id, workspace_id, user_id, submission_key, now, result.overall_score, result.rubric_version,
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
            evidence = connection.execute(
                "SELECT * FROM evidence_records WHERE evidence_id = ? AND startup_id = ? AND archived_at IS NULL",
                (evidence_id, startup_id)
            ).fetchone()
            if evidence:
                connection.execute(
                    "INSERT INTO assessment_evidence (assessment_id, evidence_id, evidence_json) VALUES (?, ?, ?)",
                    (assessment_id, evidence_id, json.dumps(dict(evidence), sort_keys=True, default=str))
                )
        return int(assessment_id)


def list_assessments(
    path: str | Path = DEFAULT_DB_PATH,
    *,
    workspace_id: int | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return assessment summaries newest first, enforcing workspace and user scoping."""
    initialize_database(path)
    clauses, params = [], []
    if workspace_id is not None:
        clauses.append("a.workspace_id = ?")
        params.append(workspace_id)
    if user_id is not None:
        clauses.append("(a.user_id = ? OR a.workspace_id IN (SELECT workspace_id FROM workspaces WHERE owner_user_id = ?))")
        params.extend([user_id, user_id])

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_connection(path) as connection:
        rows = connection.execute(
            f"""SELECT a.id, a.created_at, a.assessment_date, a.overall_score, a.rubric_version,
                      a.workspace_id, a.user_id,
                      json_extract(s.profile_json, '$.startup_name') AS startup_name,
                      json_extract(s.profile_json, '$.industry') AS industry,
                      json_extract(s.profile_json, '$.stage') AS stage
               FROM assessments a JOIN startups s ON s.id = a.startup_id
               {where}
               ORDER BY a.created_at DESC, a.id DESC""",
            params
        ).fetchall()
    return [dict(row) for row in rows]


def get_assessment(
    assessment_id: int,
    path: str | Path = DEFAULT_DB_PATH,
    *,
    user_id: int | None = None,
    workspace_id: int | None = None,
) -> dict[str, Any] | None:
    """Retrieve one assessment snapshot, strictly enforcing ownership if user_id or workspace_id is given."""
    initialize_database(path)
    with get_connection(path) as connection:
        clauses = ["a.id = ?"]
        params: list[Any] = [assessment_id]
        if workspace_id is not None:
            clauses.append("a.workspace_id = ?")
            params.append(workspace_id)
        if user_id is not None:
            clauses.append("(a.user_id = ? OR a.workspace_id IN (SELECT workspace_id FROM workspaces WHERE owner_user_id = ?))")
            params.extend([user_id, user_id])

        where = " AND ".join(clauses)
        row = connection.execute(
            f"SELECT a.*, s.profile_json FROM assessments a JOIN startups s ON s.id = a.startup_id WHERE {where}",
            params,
        ).fetchone()
        if row is None:
            return None
        dimensions = connection.execute(
            "SELECT * FROM assessment_dimensions WHERE assessment_id = ? ORDER BY rowid", (assessment_id,)
        ).fetchall()
        evidence = connection.execute(
            "SELECT evidence_json FROM assessment_evidence WHERE assessment_id = ? ORDER BY evidence_id",
            (assessment_id,)
        ).fetchall()
    result_dimensions = tuple(DimensionScore(
        identifier=item["dimension_id"], name=item["dimension_name"], rating=item["rating"],
        weight=item["weight"], contribution=item["contribution"], rationale=item["rationale"],
        evidence_status=item["evidence_status"], missing_evidence=bool(item["missing_evidence"]),
    ) for item in dimensions)
    return {
        "id": int(row["id"]),
        "workspace_id": row["workspace_id"],
        "user_id": row["user_id"],
        "profile": json.loads(row["profile_json"]),
        "inputs": json.loads(row["inputs_json"]),
        "result": AssessmentResult(float(row["overall_score"]), row["rubric_version"], result_dimensions),
        "recommendations": tuple(Recommendation(**item) for item in json.loads(row["recommendations_json"])),
        "evidence": [json.loads(item["evidence_json"]) for item in evidence],
        "assessment_date": row["assessment_date"],
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------------------
# Object-Level Ownership: Generated Documents
# ---------------------------------------------------------------------------

def save_generated_document(
    startup_id: int,
    assessment_id: int,
    document_type: str,
    title: str,
    file_path: str,
    file_format: str,
    generation_mode: str = "heuristic",
    sections: Mapping[str, Any] | None = None,
    review_status: str = "Draft",
    workspace_id: int | None = None,
    user_id: int | None = None,
    path: str | Path = DEFAULT_DB_PATH,
    document_id: str | None = None,
) -> str:
    """Record a generated document linked to startup, assessment, workspace, and owner user."""
    initialize_database(path)
    doc_id = document_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(path) as connection:
        assessment = connection.execute("SELECT id, startup_id, workspace_id, user_id FROM assessments WHERE id = ?", (assessment_id,)).fetchone()
        startup = connection.execute("SELECT id, workspace_id, user_id FROM startups WHERE id = ?", (startup_id,)).fetchone()
        if not assessment or not startup:
            raise ValueError("Generated document references a missing assessment or startup.")
        if workspace_id is None or user_id is None:
            raise ValueError("user_id and workspace_id are required for generated-document registration.")
        user = connection.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
        workspace = connection.execute("SELECT workspace_id, owner_user_id FROM workspaces WHERE workspace_id = ?", (workspace_id,)).fetchone()
        if not user or not workspace or workspace["owner_user_id"] != user_id:
            raise PermissionError("User is not authorized for the generated document workspace.")
        legacy_unowned = assessment["workspace_id"] is None and assessment["user_id"] is None and startup["workspace_id"] is None and startup["user_id"] is None
        if assessment["startup_id"] != startup_id or assessment["workspace_id"] != workspace_id or startup["workspace_id"] != workspace_id:
            raise ValueError("Generated document ownership relationships do not match.")
        if assessment["user_id"] not in (None, user_id) or startup["user_id"] not in (None, user_id):
            raise PermissionError("Generated document owner does not match the assessment or startup owner.")
        duplicate = connection.execute("SELECT 1 FROM generated_documents WHERE document_id = ?", (doc_id,)).fetchone()
        if duplicate:
            raise ValueError("Generated document ID already exists.")
        # Determine next document version
        row = connection.execute(
            "SELECT MAX(document_version) as max_v FROM generated_documents WHERE assessment_id = ? AND document_type = ?",
            (assessment_id, document_type)
        ).fetchone()
        next_version = (row["max_v"] or 0) + 1 if row else 1

        connection.execute(
            """INSERT INTO generated_documents
            (document_id, startup_id, assessment_id, workspace_id, user_id, document_type, document_version,
             title, file_path, file_format, generation_mode, review_status, sections_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, startup_id, assessment_id, workspace_id, user_id, document_type, next_version,
             title, file_path, file_format.lower(), generation_mode, review_status,
             json.dumps(dict(sections), sort_keys=True, default=str) if sections else None, now)
        )
    return doc_id


def _is_unsafe_document_component(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _prepare_document_directory(path: Path) -> Path:
    """Validate and create a directory one component at a time."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:] if absolute.anchor else absolute.parts:
        current = current / component
        if current.exists() or current.is_symlink():
            if _is_unsafe_document_component(current) or not current.is_dir():
                raise ValueError("Generated document storage contains an unsafe path component.")
        else:
            current.mkdir()
            if _is_unsafe_document_component(current) or not current.is_dir():
                raise ValueError("Generated document storage path became unsafe.")
    return absolute


def publish_generated_document(
    document_id: str,
    data: bytes,
    file_path: str,
    *,
    documents_dir: str | Path = DEFAULT_DOCUMENTS_DIR,
) -> Path:
    """Publish bytes with temporary-file durability and exclusive no-clobber semantics."""
    raw = str(file_path).replace("\\", "/")
    parts = raw.split("/")
    if not parts or parts[0].lower() != "documents" or len(parts) < 2 or any(not p or p in {".", ".."} for p in parts):
        raise ValueError("Generated document path is unsafe.")
    root = _prepare_document_directory(Path(documents_dir))
    final_path = root.joinpath(*parts[1:])
    _prepare_document_directory(final_path.parent)
    if _is_unsafe_document_component(final_path) or final_path.exists():
        raise FileExistsError("Generated document already exists.")
    temporary_path: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{document_id}-", suffix=".tmp", dir=final_path.parent)
        temporary_path = Path(temporary_name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary_path.stat().st_size != len(data) or temporary_path.stat().st_size == 0:
            raise IOError("Generated document temporary file failed validation.")
        # Hard-link publication is exclusive and same-filesystem; it fails if
        # another document already occupies the final path.
        os.link(temporary_path, final_path)
        temporary_path.unlink()
        temporary_path = None
        return final_path
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except Exception as cleanup_error:
                logger.error("Generated document temporary cleanup failed for document %s: %s", document_id, cleanup_error)
        raise


def resolve_generated_document_path(
    document_id: str,
    *,
    user_id: int,
    workspace_id: int,
    path: str | Path = DEFAULT_DB_PATH,
    documents_dir: str | Path = DEFAULT_DOCUMENTS_DIR,
) -> Path:
    """Resolve an owned generated document to a safe regular file path."""
    record = get_generated_document(document_id, path=path, user_id=user_id, workspace_id=workspace_id)
    if record is None:
        raise PermissionError("Generated document is not available to this user.")
    raw = str(record.get("file_path") or "").replace("\\", "/")
    parts = raw.split("/")
    if parts and parts[0].lower() == "documents":
        parts = parts[1:]
    if not parts or any(not part or part in {".", ".."} for part in parts) or Path(raw).drive or raw.startswith(("/", "//")):
        raise ValueError("Generated document path is unsafe.")
    root = Path(documents_dir).resolve()
    candidate = root.joinpath(*parts)
    if candidate.is_symlink() or not candidate.exists():
        raise FileNotFoundError("Generated document is missing or unsafe.")
    resolved = candidate.resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError("Generated document path is outside storage.")
    if not resolved.is_file():
        raise ValueError("Generated document is not a regular file.")
    return resolved


def list_generated_documents(
    assessment_id: int | None = None,
    startup_id: int | None = None,
    workspace_id: int | None = None,
    user_id: int | None = None,
    document_type: str | None = None,
    path: str | Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """List historical generated documents with ownership filtering."""
    initialize_database(path)
    clauses, params = [], []
    if assessment_id is not None:
        clauses.append("assessment_id = ?")
        params.append(assessment_id)
    if startup_id is not None:
        clauses.append("startup_id = ?")
        params.append(startup_id)
    if workspace_id is not None:
        clauses.append("workspace_id = ?")
        params.append(workspace_id)
    if user_id is not None:
        clauses.append("(user_id = ? OR workspace_id IN (SELECT workspace_id FROM workspaces WHERE owner_user_id = ?))")
        params.extend([user_id, user_id])
    if document_type is not None:
        clauses.append("document_type = ?")
        params.append(document_type)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_connection(path) as connection:
        rows = connection.execute(
            f"SELECT * FROM generated_documents{where} ORDER BY created_at DESC", params
        ).fetchall()
    return [dict(r) for r in rows]


def get_generated_document(
    document_id: str,
    path: str | Path = DEFAULT_DB_PATH,
    *,
    user_id: int | None = None,
    workspace_id: int | None = None,
) -> dict[str, Any] | None:
    """Retrieve document metadata by document ID, enforcing ownership."""
    initialize_database(path)
    with get_connection(path) as connection:
        clauses = ["document_id = ?"]
        params: list[Any] = [document_id]
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if user_id is not None:
            clauses.append("(user_id = ? OR workspace_id IN (SELECT workspace_id FROM workspaces WHERE owner_user_id = ?))")
            params.extend([user_id, user_id])

        where = " AND ".join(clauses)
        row = connection.execute(f"SELECT * FROM generated_documents WHERE {where}", params).fetchone()
    if not row:
        return None
    data = dict(row)
    if data.get("sections_json"):
        data["sections"] = json.loads(data["sections_json"])
    return data
