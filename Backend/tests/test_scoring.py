"""Tests for the deterministic weighted scoring engine."""

import pytest

from assessment import EVIDENCE_STATUSES
from scoring import DIMENSIONS, RUBRIC_VERSION, calculate_assessment, validate_rubric


def entries_for(ratings, *, evidence_status="Supported information", rationale="Evidence or rationale"):
    return {
        dimension.identifier: {
            "rating": rating,
            "reviewed": True,
            "evidence_status": evidence_status,
            "rationale": rationale,
        }
        for dimension, rating in zip(DIMENSIONS, ratings)
    }


def test_reference_case_scores_72():
    result = calculate_assessment(entries_for([70, 80, 60, 75, 65, 70, 90]))
    assert result.overall_score == 72.00
    assert [item.contribution for item in result.dimensions] == [14, 12, 9, 11.25, 9.75, 7, 9]


def test_minimum_and_maximum_ratings():
    assert calculate_assessment(entries_for([0] * 7)).overall_score == 0
    assert calculate_assessment(entries_for([100] * 7)).overall_score == 100


@pytest.mark.parametrize("rating", [-1, 101, "50", True])
def test_invalid_ratings_are_rejected(rating):
    entries = entries_for([50] * 7)
    entries[DIMENSIONS[0].identifier]["rating"] = rating
    with pytest.raises(ValueError, match="between 0 and 100"):
        calculate_assessment(entries)


def test_rubric_validation_and_version_remain_canonical():
    validate_rubric()
    assert sum(dimension.weight for dimension in DIMENSIONS) == 100
    assert RUBRIC_VERSION == "v1.0"


def test_calculation_is_reproducible():
    entries = entries_for([70, 80, 60, 75, 65, 70, 90])
    assert calculate_assessment(entries) == calculate_assessment(entries)


def test_missing_or_incomplete_inputs_are_rejected():
    entries = entries_for([50] * 7)
    entries.pop(DIMENSIONS[-1].identifier)
    with pytest.raises(ValueError, match="canonical rubric"):
        calculate_assessment(entries)
    entries = entries_for([50] * 7)
    entries[DIMENSIONS[0].identifier]["reviewed"] = False
    with pytest.raises(ValueError, match="explicitly reviewed"):
        calculate_assessment(entries)


def test_missing_evidence_is_flagged_without_changing_score():
    entries = entries_for([50] * 7, evidence_status=EVIDENCE_STATUSES[0], rationale="")
    result = calculate_assessment(entries)
    assert result.overall_score == 50
    assert all(item.missing_evidence for item in result.dimensions)
    assert all(item.evidence_status == "Not yet known" for item in result.dimensions)


def test_contributions_reconcile_with_displayed_score():
    result = calculate_assessment(entries_for([70, 80, 60, 75, 65, 70, 90]))
    assert round(sum(item.contribution for item in result.dimensions), 2) == result.overall_score
