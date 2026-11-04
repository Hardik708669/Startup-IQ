"""Safe operational diagnostics for local operators."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from database import DEFAULT_ATTACHMENT_DIR, DEFAULT_DB_PATH, DEFAULT_DOCUMENTS_DIR, initialize_database
from logging_config import configure_logging


def run_diagnostics(db_path=DEFAULT_DB_PATH) -> dict[str, str]:
    result = {}
    try:
        initialize_database(db_path)
        with sqlite3.connect(db_path) as connection:
            result["sqlite"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
    except Exception:
        result["sqlite"] = "error"
    for name, path in (("attachments", DEFAULT_ATTACHMENT_DIR), ("documents", DEFAULT_DOCUMENTS_DIR)):
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            probe = Path(path) / ".startup_iq_write_probe"
            probe.write_bytes(b"probe")
            probe.unlink()
            result[name] = "writable"
        except Exception:
            result[name] = "unavailable"
    try:
        configure_logging().info("diagnostics_completed", extra={"diagnostic_status": result})
        result["logging"] = "available"
    except Exception:
        result["logging"] = "unavailable"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("diagnose", nargs="?", default="diagnose")
    args = parser.parse_args()
    print(run_diagnostics())
