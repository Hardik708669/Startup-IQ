"""Tests for Day 3 assessment input helpers."""

from assessment import EVIDENCE_STATUSES, reset_assessment_state, validate_assessment_entries
from scoring import DIMENSIONS


def complete_entries(rationale: str = "Evidence is being reviewed."):
    return {
        dimension.identifier: {
            "rating": 50,
            "reviewed": True,
            "evidence_status": EVIDENCE_STATUSES[0],
            "rationale": rationale,
        }
        for dimension in DIMENSIONS
    }


def test_complete_assessment_entries_are_valid():
    assert validate_assessment_entries(complete_entries()) == []


def test_all_canonical_dimensions_are_required():
    entries = complete_entries()
    entries.pop(DIMENSIONS[0].identifier)
    assert validate_assessment_entries(entries)


def test_missing_rationale_is_not_silently_accepted():
    entries = complete_entries(rationale="")
    errors = validate_assessment_entries(entries)
    assert "Market Potential requires supporting rationale or evidence." in errors


def test_unreviewed_default_rating_is_rejected():
    entries = complete_entries()
    entries[DIMENSIONS[0].identifier]["reviewed"] = False
    errors = validate_assessment_entries(entries)
    assert "Market Potential rating must be explicitly reviewed and confirmed." in errors


def test_evidence_status_defaults_to_not_yet_known():
    assert EVIDENCE_STATUSES[0] == "Not yet known"


def test_rating_range_is_validated():
    entries = complete_entries()
    entries[DIMENSIONS[0].identifier]["rating"] = 101
    assert "Market Potential rating must be between 0 and 100." in validate_assessment_entries(entries)


def test_reset_assessment_state_removes_previous_startup_inputs_and_result():
    state = {
        "assessment_entries": {"market_potential": {"rating": 88}},
        "assessment_result": "old-result",
        "assessment_date": "2026-10-20",
        "dashboard_view": True,
        "rating_market_potential": 88,
        "reviewed_market_potential": True,
        "status_market_potential": "Supported information",
        "rationale_market_potential": "Old startup rationale",
        "unrelated_state": "preserve",
    }
    reset_assessment_state(state)
    assert state["assessment_entries"] is None
    assert state["assessment_result"] is None
    assert state["recommendations"] is None
    assert state["assessment_date"] is None
    assert state["dashboard_view"] is False
    assert "rating_market_potential" not in state
    assert "rationale_market_potential" not in state
    assert state["unrelated_state"] == "preserve"
