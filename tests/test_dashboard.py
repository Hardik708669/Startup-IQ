"""Tests for pure results-dashboard presentation helpers."""

from assessment import EVIDENCE_STATUSES
from dashboard import DISCLAIMER, dimension_rows, evidence_status_counts
from scoring import DIMENSIONS, calculate_assessment


def result_for(ratings, status="Supported information"):
    entries = {
        dimension.identifier: {
            "rating": rating,
            "reviewed": True,
            "evidence_status": status,
            "rationale": "Submitted rationale",
        }
        for dimension, rating in zip(DIMENSIONS, ratings)
    }
    return calculate_assessment(entries)


def test_dashboard_rows_use_engine_values_and_weights():
    result = result_for([70, 80, 60, 75, 65, 70, 90])
    rows = dimension_rows(result)
    assert len(rows) == 7
    assert [row["rating"] for row in rows] == [70, 80, 60, 75, 65, 70, 90]
    assert [row["weight"] for row in rows] == [20, 15, 15, 15, 15, 10, 10]
    assert round(sum(row["contribution"] for row in rows), 2) == result.overall_score


def test_missing_evidence_is_visible_in_rows_and_counts():
    result = result_for([50] * 7, status=EVIDENCE_STATUSES[0])
    rows = dimension_rows(result)
    assert all(row["missing_evidence"] for row in rows)
    assert evidence_status_counts(result) == {"Not yet known": 7}


def test_dashboard_disclaimer_is_present():
    assert "analytical indicator" in DISCLAIMER
    assert "does not" not in DISCLAIMER
    assert "prediction of startup success" in DISCLAIMER
