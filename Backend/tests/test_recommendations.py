"""Tests for deterministic, dimension-specific recommendations."""

from assessment import EVIDENCE_STATUSES
from recommendations import generate_recommendations
from scoring import DIMENSIONS, calculate_assessment


def result_for(ratings, statuses=None, rationales=None):
    statuses = statuses or ["Supported information"] * 7
    rationales = rationales or ["Submitted rationale"] * 7
    entries = {
        dimension.identifier: {
            "rating": rating,
            "reviewed": True,
            "evidence_status": status,
            "rationale": rationale,
        }
        for dimension, rating, status, rationale in zip(DIMENSIONS, ratings, statuses, rationales)
    }
    return calculate_assessment(entries)


def test_each_dimension_has_specific_low_rating_recommendation():
    result = result_for([40] * 7)
    recommendations = generate_recommendations(result)
    assert {item.dimension_id for item in recommendations} == {dimension.identifier for dimension in DIMENSIONS}
    assert all(item.priority == "High" for item in recommendations)
    assert all(item.recommended_action and item.suggested_evidence for item in recommendations)


def test_moderate_supported_dimension_gets_refinement_recommendation():
    result = result_for([70] * 7)
    recommendations = generate_recommendations(result)
    assert len(recommendations) == 7
    assert all(item.priority == "Medium" for item in recommendations)


def test_strong_supported_assessment_has_no_unnecessary_recommendations():
    result = result_for([90] * 7)
    assert generate_recommendations(result) == ()


def test_strong_weak_evidence_triggers_validation_recommendation():
    result = result_for([90] * 7, statuses=[EVIDENCE_STATUSES[0]] * 7, rationales=[""] * 7)
    recommendations = generate_recommendations(result)
    assert len(recommendations) == 7
    assert all(item.priority == "High" for item in recommendations)
    assert all("evidence" in item.issue.lower() for item in recommendations)


def test_unvalidated_hypotheses_and_empty_rationales_are_flagged():
    statuses = [EVIDENCE_STATUSES[3]] + ["Supported information"] * 6
    rationales = [""] + ["Submitted rationale"] * 6
    recommendations = generate_recommendations(result_for([75] * 7, statuses, rationales))
    assert recommendations[0].dimension_id == DIMENSIONS[0].identifier
    assert recommendations[0].priority == "Medium"


def test_multiple_weaknesses_are_reproducible_and_mapped_correctly():
    result = result_for([45, 85, 55, 90, 50, 85, 75], statuses=["Supported information"] * 7)
    first = generate_recommendations(result)
    second = generate_recommendations(result)
    assert first == second
    assert {item.dimension_id for item in first} == {
        "market_potential", "competitive_position", "financial_readiness", "pitch_readiness"
    }


def test_low_rating_and_missing_evidence_are_combined_not_contradictory():
    result = result_for([40] * 7, statuses=[EVIDENCE_STATUSES[0]] * 7, rationales=[""] * 7)
    recommendations = generate_recommendations(result)
    assert all(item.issue == "Low rating and insufficient supporting evidence" for item in recommendations)
