"""Automated tests for AI-powered startup idea understanding and structured extraction."""

import json
import os
import unittest.mock as mock
import pytest

from AI.ai_service import (
    AnalysisResult,
    ExtractedField,
    MissingInfoItem,
    SuggestedEvidence,
    STATUS_APPROVED,
    STATUS_EDITED,
    STATUS_PENDING,
    STATUS_REJECTED,
    analyze_startup_idea,
    heuristic_extract_startup_idea,
    parse_ai_json_response,
)
from database import ensure_startup, get_evidence, initialize_database, list_evidence, save_evidence


def sample_startup_text():
    return (
        "We are building NeuroSync, an AI diagnostic headband for epilepsy patients and neurology clinics. "
        "The problem is that seizure onset is unpredictable and 50 million people suffer without early warning. "
        "Our solution uses edge EEG sensors with real-time predictive alerts. "
        "We interviewed 30 neurology clinics and 15 expressed interest in our $150/month SaaS subscription. "
        "Direct competitors include Empatica and Seer Medical. "
        "We need $500,000 for clinical trials and hardware certification."
    )


def test_heuristic_extraction_valid_text():
    text = sample_startup_text()
    res = heuristic_extract_startup_idea(text)
    assert res.provider_used == "heuristic"
    assert len(res.fields) == 25

    fields_by_key = {f.field_key: f for f in res.fields}
    assert "NeuroSync" in fields_by_key["startup_name"].suggested_value
    assert fields_by_key["industry"].suggested_value in ("HealthTech", "Artificial Intelligence", "Technology")
    assert fields_by_key["problem_statement"].suggested_value != ""
    assert fields_by_key["proposed_solution"].suggested_value != ""
    assert fields_by_key["revenue_model"].suggested_value != ""
    assert len(res.suggested_evidence) >= 1
    assert "Interview" in res.suggested_evidence[0].evidence_title


def test_empty_or_oversized_text():
    res, err = analyze_startup_idea("")
    assert err == "Startup description cannot be empty."
    assert len(res.fields) == 0

    res2, err2 = analyze_startup_idea("   ")
    assert err2 == "Startup description cannot be empty."

    oversized = "a" * 25001
    res3, err3 = analyze_startup_idea(oversized)
    assert "exceeds maximum character limit" in err3


def test_missing_information_detection():
    # Text with missing financial and competitor details
    bare_text = "We have an idea called PetSocial for dog owners to meet up in parks."
    res = heuristic_extract_startup_idea(bare_text)
    missing_keys = [m.dimension for m in res.missing_info]
    assert "Competitive Position" in missing_keys or "Business Model" in missing_keys or "Financial Readiness" in missing_keys
    for item in res.missing_info:
        assert item.missing_information
        assert item.why_it_matters
        assert item.suggested_question
        assert item.suggested_evidence


def test_directly_stated_vs_inferred():
    text = "We are building PayFlow for freelancers to handle invoices."
    res = heuristic_extract_startup_idea(text)
    fields_by_key = {f.field_key: f for f in res.fields}
    assert fields_by_key["startup_name"].is_inferred is False
    assert fields_by_key["startup_name"].supporting_text != ""


def test_parse_ai_json_response_success():
    mock_payload = {
        "fields": {
            "startup_name": {"value": "CloudGuard", "supporting_text": "CloudGuard", "is_inferred": False},
            "industry": {"value": "Cybersecurity", "supporting_text": "cloud security", "is_inferred": False},
            "stage": {"value": "Seed", "supporting_text": "Seed round", "is_inferred": False},
            "problem_statement": {"value": "Cloud misconfigurations cause data breaches.", "supporting_text": "misconfigurations", "is_inferred": False},
            "proposed_solution": {"value": "Automated posture management.", "supporting_text": "posture management", "is_inferred": False},
            "target_customers": {"value": "DevOps teams", "supporting_text": "DevOps", "is_inferred": False},
        },
        "missing_info": [
            {
                "section": "Financial and operational information",
                "dimension": "Financial Readiness",
                "missing_information": "Pricing is not defined.",
                "why_it_matters": "Cannot calculate LTV.",
                "suggested_question": "What is the monthly seat fee?",
                "suggested_evidence": "Pricing survey.",
            }
        ],
        "suggested_evidence": [
            {
                "evidence_title": "20 DevOps Pilot Trials",
                "evidence_description": "20 companies tested beta.",
                "evidence_type": "Pilot results",
                "related_dimension": "Problem & Solution Clarity",
                "source_name": "Founder statement",
                "verification_status": "Founder submitted",
                "supporting_excerpt": "20 companies tested beta",
            }
        ]
    }
    json_str = f"```json\n{json.dumps(mock_payload)}\n```"
    result = parse_ai_json_response(json_str, provider_name="openai")
    assert result.provider_used == "openai"
    assert len(result.fields) == 25
    field_dict = {f.field_key: f for f in result.fields}
    assert field_dict["startup_name"].suggested_value == "CloudGuard"
    assert field_dict["industry"].suggested_value == "Cybersecurity"
    assert len(result.missing_info) == 1
    assert result.missing_info[0].dimension == "Financial Readiness"
    assert len(result.suggested_evidence) == 1
    assert result.suggested_evidence[0].evidence_title == "20 DevOps Pilot Trials"


def test_malformed_and_incomplete_ai_json():
    with pytest.raises(Exception):
        parse_ai_json_response("{not-valid-json}")

    with pytest.raises(ValueError, match="not a valid JSON dictionary"):
        parse_ai_json_response('["array", "not", "dict"]')


def test_ai_provider_network_and_timeout_fallbacks():
    # When an external network/API call fails or times out, it must fall back to heuristic without crashing
    text = sample_startup_text()
    with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
        res, err = analyze_startup_idea(text, api_key="test-key", provider="openai")
        assert res.provider_used == "heuristic"
        assert "timed out" in err
        assert len(res.fields) == 25

    with mock.patch("urllib.request.urlopen", side_effect=Exception("API connection failure")):
        res, err = analyze_startup_idea(text, api_key="AIzaSyTestGeminiKey", provider="gemini")
        assert res.provider_used == "heuristic"
        assert "fallback" in err


def test_provider_success_records_safe_usage_metadata(monkeypatch):
    payload = {"fields": {}, "missing_info": [], "suggested_evidence": []}
    monkeypatch.setattr("AI.ai_service._call_openai_api", lambda *args, **kwargs: json.dumps(payload))
    result, error = analyze_startup_idea("A founder description", api_key="test-key", provider="openai")
    assert error is None
    assert result.provider_used == "openai"
    assert result.usage_metadata["provider"] == "openai"
    assert result.usage_metadata["success"] is True
    assert "test-key" not in json.dumps(result.usage_metadata)


def test_transient_provider_failures_are_retried_then_fallback(monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("timeout")
    monkeypatch.setattr("AI.ai_service._call_openai_api", fail)
    result, error = analyze_startup_idea("A founder description", api_key="test-key", provider="openai")
    assert result.provider_used == "heuristic"
    assert "timed out" in error
    assert len(calls) == 3


def test_provider_supporting_excerpt_must_be_grounded(monkeypatch):
    payload = {"fields": {"startup_name": {"value": "X", "supporting_text": "not in source", "is_inferred": False}}, "missing_info": [], "suggested_evidence": []}
    monkeypatch.setattr("AI.ai_service._call_openai_api", lambda *args, **kwargs: json.dumps(payload))
    result, _ = analyze_startup_idea("X is a startup.", api_key="test-key", provider="openai")
    field = next(item for item in result.fields if item.field_key == "startup_name")
    assert field.supporting_text == ""
    assert field.is_inferred is True


def test_unexpected_provider_schema_is_rejected():
    with pytest.raises(ValueError, match="unexpected"):
        parse_ai_json_response('{"fields": {}, "unexpected": true}')


def test_evidence_suggestions_integration_with_db(tmp_path):
    path = tmp_path / "test_ai_evidence.db"
    initialize_database(path)
    profile = {"startup_name": "NeuroSync", "industry": "HealthTech", "stage": "Idea"}
    startup_id = ensure_startup(profile, path)

    evidence_item = SuggestedEvidence(
        evidence_title="Founder Clinical Interview Notes",
        evidence_description="Interviewed 30 neurology clinics.",
        evidence_type="Customer interviews",
        related_dimension="Problem & Solution Clarity",
        source_name="Founder statement",
        verification_status="Founder submitted",
        supporting_excerpt="Interviewed 30 neurology clinics",
        is_approved=True,
    )

    ev_id = save_evidence(profile, evidence_item.as_dict(), path)
    saved = get_evidence(ev_id, path)
    assert saved is not None
    assert saved["evidence_title"] == "Founder Clinical Interview Notes"
    assert saved["verification_status"] == "Founder submitted"
    assert saved["evidence_type"] == "Customer interviews"

    # Listed under startup
    listed = list_evidence(startup_id=startup_id, path=path)
    assert len(listed) == 1
    assert listed[0]["evidence_id"] == ev_id
    assert listed[0]["supporting_excerpt"] == "Interviewed 30 neurology clinics"


def test_heuristic_cash_requirements_regression():
    # 1. $500,000 required in seed funding -> amount extracted
    res1 = heuristic_extract_startup_idea("We are building TechCorp. $500,000 required in seed funding.")
    f1 = {f.field_key: f for f in res1.fields}
    assert "$500,000" in f1["cash_requirements"].suggested_value
    assert f1["cash_requirements"].supporting_text != ""
    assert f1["cash_requirements"].is_inferred is False

    # 2. We require INR 25 lakh in initial capital -> amount extracted
    res2 = heuristic_extract_startup_idea("We require INR 25 lakh in initial capital to scale operations.")
    f2 = {f.field_key: f for f in res2.fields}
    assert "INR 25 lakh" in f2["cash_requirements"].suggested_value
    assert f2["cash_requirements"].is_inferred is False

    # 3. $99 monthly subscription -> must not be identified as cash requirements
    res3 = heuristic_extract_startup_idea("We charge a $99 monthly subscription for our B2B software tool.")
    f3 = {f.field_key: f for f in res3.fields}
    assert f3["cash_requirements"].suggested_value == ""
    assert f3["cash_requirements"].is_inferred is True

    # 4. $5 million company valuation -> must not be identified as cash requirements
    res4 = heuristic_extract_startup_idea("We are an early stage startup with a $5 million company valuation.")
    f4 = {f.field_key: f for f in res4.fields}
    assert f4["cash_requirements"].suggested_value == ""
    assert f4["cash_requirements"].is_inferred is True

    # 5. No funding figure in the document -> field remains empty
    res5 = heuristic_extract_startup_idea("We are building PetSocial for dog owners to connect in parks.")
    f5 = {f.field_key: f for f in res5.fields}
    assert f5["cash_requirements"].suggested_value == ""
    assert f5["cash_requirements"].is_inferred is True

    # 6. Extracted values retain their correct supporting source text
    res6 = heuristic_extract_startup_idea("We need $500,000 for clinical trials and hardware certification.")
    f6 = {f.field_key: f for f in res6.fields}
    assert "$500,000" in f6["cash_requirements"].suggested_value
    assert f6["cash_requirements"].supporting_text == f6["cash_requirements"].suggested_value
