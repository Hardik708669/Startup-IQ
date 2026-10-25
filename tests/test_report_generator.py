"""Tests for standalone HTML assessment reports."""

import html

import pytest

from recommendations import generate_recommendations
from report_generator import generate_report, report_filename
from scoring import DIMENSIONS, calculate_assessment


def assessment_result():
    entries = {
        dimension.identifier: {
            "rating": rating,
            "reviewed": True,
            "evidence_status": status,
            "rationale": rationale,
        }
        for dimension, rating, status, rationale in zip(
            DIMENSIONS,
            [70, 80, 60, 75, 65, 70, 90],
            ["Supported information", "Estimate", "Not yet known", "Supported information", "Estimate", "Unvalidated hypothesis", "Supported information"],
            ["Customer notes", "Solution rationale", "", "Pricing rationale", "Cost estimate", "Risk hypothesis", "Pitch rationale"],
        )
    }
    return calculate_assessment(entries)


def test_report_contains_current_score_dimensions_weights_and_contributions():
    result = assessment_result()
    report = generate_report({"startup_name": "GreenLoop Packaging"}, result, generate_recommendations(result), "2026-10-25")
    assert "GreenLoop Packaging" in report
    assert "72.00 / 100" in report
    for dimension in DIMENSIONS:
        assert html.escape(dimension.name) in report
        assert f"{dimension.weight}%" in report
    for item in result.dimensions:
        assert f"{item.contribution:.2f}" in report


def test_report_contains_rationale_evidence_flags_recommendations_and_disclaimer():
    result = assessment_result()
    report = generate_report({"startup_name": "GreenLoop"}, result, generate_recommendations(result), "2026-10-25")
    assert "Customer notes" in report
    assert "Not yet known" in report
    assert "Missing evidence" in report
    assert "Recommended action" in report
    assert "analytical indicator" in report


def test_report_handles_missing_optional_profile_fields():
    report = generate_report({"startup_name": "Minimal"}, assessment_result())
    assert "Not provided" in report


def test_report_requires_a_real_assessment_result():
    with pytest.raises(ValueError, match="valid assessment result"):
        generate_report({"startup_name": "No Result"}, None)


def test_report_escapes_user_controlled_html():
    startup = '<script>alert("x")</script>'
    report = generate_report({"startup_name": startup, "startup_description": "<b>unsafe</b>"}, assessment_result())
    assert html.escape(startup) in report
    assert "<script>alert" not in report
    assert "<b>unsafe</b>" not in report


def test_report_filename_is_safe_and_predictable():
    assert report_filename("GreenLoop / Packaging", "2026-10-25") == "startup-iq-greenloop-packaging-2026-10-25.html"
