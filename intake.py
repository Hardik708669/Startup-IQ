"""Data shape and validation helpers for the Day 2 startup intake form."""

from __future__ import annotations

from typing import Any


REQUIRED_FIELDS = {
    "startup_name": "Startup name",
    "startup_description": "Short startup description",
    "problem_statement": "Problem statement",
    "proposed_solution": "Proposed solution",
    "target_customers": "Target customers",
}


def validate_submission(data: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors without scoring the submission."""
    errors = [
        f"{label} is required."
        for key, label in REQUIRED_FIELDS.items()
        if not str(data.get(key, "")).strip()
    ]
    if len(str(data.get("startup_description", "")).strip()) < 20:
        errors.append("Short startup description should be at least 20 characters.")
    return errors


def split_lines(value: str) -> list[str]:
    """Convert a multiline field into clean, non-empty entries."""
    return [line.strip() for line in value.splitlines() if line.strip()]
