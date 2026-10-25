"""Tests for SQLite persistence using temporary databases only."""

import sqlite3

import pytest

from database import get_assessment, initialize_database, list_assessments, save_assessment
from recommendations import generate_recommendations
from scoring import DIMENSIONS, calculate_assessment


def make_snapshot(ratings, name="Test Startup"):
    inputs = {
        dimension.identifier: {
            "rating": rating,
            "reviewed": True,
            "evidence_status": "Not yet known" if rating < 60 else "Supported information",
            "rationale": f"Rationale for {dimension.name}",
        }
        for dimension, rating in zip(DIMENSIONS, ratings)
    }
    result = calculate_assessment(inputs)
    profile = {"startup_name": name, "industry": "Testing", "stage": "Idea", "startup_description": "A test startup."}
    return profile, inputs, result, generate_recommendations(result)


def test_database_initialization_is_repeatable_and_creates_tables(tmp_path):
    path = tmp_path / "nested" / "startup_iq.db"
    initialize_database(path)
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"startups", "assessments", "assessment_dimensions"} <= tables


def test_save_and_retrieve_preserves_snapshot_and_recommendations(tmp_path):
    path = tmp_path / "startup_iq.db"
    profile, inputs, result, recommendations = make_snapshot([40, 80, 55, 70, 65, 90, 75])
    assessment_id = save_assessment(profile, inputs, result, recommendations, "2026-10-25", path)
    saved = get_assessment(assessment_id, path)
    assert saved is not None
    assert saved["profile"] == profile
    assert saved["inputs"] == inputs
    assert saved["result"].overall_score == result.overall_score
    assert [(item.identifier, item.rating, item.weight, item.contribution, item.evidence_status, item.missing_evidence) for item in saved["result"].dimensions] == [
        (item.identifier, item.rating, item.weight, item.contribution, item.evidence_status, item.missing_evidence) for item in result.dimensions
    ]
    assert saved["recommendations"] == recommendations


def test_identical_submission_is_idempotent_and_changed_submission_is_new(tmp_path):
    path = tmp_path / "startup_iq.db"
    profile, inputs, result, recommendations = make_snapshot([50] * 7)
    first = save_assessment(profile, inputs, result, recommendations, "2026-10-25", path)
    duplicate = save_assessment(profile, inputs, result, recommendations, "2026-10-25", path)
    changed_profile, changed_inputs, changed_result, changed_recommendations = make_snapshot([60] * 7, "Second Startup")
    second = save_assessment(changed_profile, changed_inputs, changed_result, changed_recommendations, "2026-10-26", path)
    assert duplicate == first
    assert second != first
    assert len(list_assessments(path)) == 2


def test_history_is_newest_first_and_empty_history_is_safe(tmp_path):
    path = tmp_path / "startup_iq.db"
    assert list_assessments(path) == []
    profile, inputs, result, recommendations = make_snapshot([50] * 7)
    save_assessment(profile, inputs, result, recommendations, "2026-10-25", path)
    profile2, inputs2, result2, recommendations2 = make_snapshot([70] * 7, "Newer Startup")
    save_assessment(profile2, inputs2, result2, recommendations2, "2026-10-26", path)
    history = list_assessments(path)
    assert history[0]["startup_name"] == "Newer Startup"
    assert history[1]["startup_name"] == "Test Startup"


def test_missing_assessment_returns_none(tmp_path):
    assert get_assessment(999, tmp_path / "startup_iq.db") is None


def test_database_failures_are_not_hidden(tmp_path):
    invalid_parent = tmp_path / "not-a-directory"
    invalid_parent.write_text("not a directory", encoding="utf-8")
    with pytest.raises((sqlite3.Error, OSError)):
        initialize_database(invalid_parent / "child.db")
