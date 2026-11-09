"""Regression coverage for application-level ownership scoping."""

import pytest

from business_plan_generator import draft_business_plan_content
from database import (
    create_user,
    create_workspace,
    get_assessment,
    get_evidence,
    initialize_database,
    list_evidence,
    save_assessment,
    save_evidence,
)
from pitch_deck_generator import draft_pitch_deck_slides
from recommendations import generate_recommendations
from report_generator import generate_report
from scoring import DIMENSIONS, calculate_assessment


MARKER = "CONFIDENTIAL_USER_A_EVIDENCE"


def _assessment_inputs():
    return {
        dimension.identifier: {
            "rating": 70,
            "reviewed": True,
            "evidence_status": "Supported information",
            "rationale": "Owner-scoped test rationale",
        }
        for dimension in DIMENSIONS
    }


def _setup(tmp_path):
    db_path = tmp_path / "ownership-regression.db"
    initialize_database(db_path)
    user_a = create_user("User A", "owner-a@example.test", "hash-a", db_path)
    user_b = create_user("User B", "owner-b@example.test", "hash-b", db_path)
    workspace_a = create_workspace(user_a, "A Workspace", path=db_path)
    workspace_b = create_workspace(user_b, "B Workspace", path=db_path)
    profile = {
        "startup_name": "Identical Startup",
        "industry": "Testing",
        "stage": "Idea",
        "startup_description": "The same profile is intentionally used by both users.",
    }
    evidence_id = save_evidence(
        profile,
        {
            "evidence_title": "Private Owner Evidence",
            "evidence_description": MARKER,
            "evidence_type": "Other",
            "related_dimension": "Market Potential",
            "verification_status": "Founder submitted",
            "supporting_excerpt": MARKER,
        },
        db_path,
        workspace_id=workspace_a,
        user_id=user_a,
    )
    return db_path, user_a, user_b, workspace_a, workspace_b, profile, evidence_id


def test_same_profile_collision_and_fail_closed_listing(tmp_path):
    db_path, user_a, user_b, workspace_a, workspace_b, profile, evidence_id = _setup(tmp_path)

    assert get_evidence(evidence_id, db_path, user_id=user_a)["evidence_description"] == MARKER
    assert get_evidence(evidence_id, db_path, user_id=user_b) is None
    assert list_evidence(profile, workspace_id=workspace_b, user_id=user_b, path=db_path) == []
    with pytest.raises(ValueError, match="require workspace_id or user_id"):
        list_evidence(profile, path=db_path)
    assert list_evidence(profile, workspace_id=workspace_a, user_id=user_a, path=db_path)[0]["supporting_excerpt"] == MARKER


def test_report_and_generated_content_use_only_scoped_evidence(tmp_path):
    db_path, user_a, user_b, workspace_a, workspace_b, profile, evidence_id = _setup(tmp_path)
    result = calculate_assessment(_assessment_inputs())
    recommendations = generate_recommendations(result)
    user_b_evidence = list_evidence(profile, workspace_id=workspace_b, user_id=user_b, path=db_path)

    report = generate_report(profile, result, recommendations, "2026-11-03", user_b_evidence)
    business_plan = draft_business_plan_content(profile, assessment={"result": result, "evidence": user_b_evidence})
    pitch_deck = draft_pitch_deck_slides(profile, assessment={"result": result, "evidence": user_b_evidence})

    assert MARKER not in report
    assert MARKER not in "\n".join(business_plan.values())
    assert MARKER not in "\n".join(" ".join(slide["bullets"]) for slide in pitch_deck.values())


def test_ai_registered_evidence_and_historical_snapshot_keep_owner_scope(tmp_path):
    db_path, user_a, user_b, workspace_a, workspace_b, profile, evidence_id = _setup(tmp_path)
    ai_evidence_id = save_evidence(
        profile,
        {
            "evidence_title": "AI Approved Owner Claim",
            "evidence_description": "Approved by User B's AI review",
            "evidence_type": "Other",
            "related_dimension": "Problem & Solution Clarity",
            "verification_status": "Founder submitted",
            "supporting_excerpt": "USER_B_AI_EXCERPT",
        },
        db_path,
        workspace_id=workspace_b,
        user_id=user_b,
    )
    assert get_evidence(ai_evidence_id, db_path, user_id=user_b)["user_id"] == user_b
    assert get_evidence(ai_evidence_id, db_path, user_id=user_a) is None

    inputs = _assessment_inputs()
    result = calculate_assessment(inputs)
    assessment_id = save_assessment(
        profile,
        inputs,
        result,
        generate_recommendations(result),
        "2026-11-03",
        db_path,
        [evidence_id],
        workspace_id=workspace_a,
        user_id=user_a,
    )
    owner_snapshot = get_assessment(assessment_id, db_path, user_id=user_a)
    foreign_snapshot = get_assessment(assessment_id, db_path, user_id=user_b)
    assert owner_snapshot["evidence"][0]["supporting_excerpt"] == MARKER
    assert foreign_snapshot is None
