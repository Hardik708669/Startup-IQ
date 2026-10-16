"""Small SQLite connection boundary for future local persistence.

The eventual schema will store startup profiles and assessments, including the
rubric version and score breakdown. SQLite remains in the Python standard library.
"""

import sqlite3
from pathlib import Path


def get_connection(path: str | Path = "startup_iq.db") -> sqlite3.Connection:
    """Return a SQLite connection without creating application tables yet."""
    return sqlite3.connect(path)
