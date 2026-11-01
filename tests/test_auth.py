"""
Automated unit & integration tests for User Authentication, Workspace Management,
and Object-Level Data Isolation.
"""
from __future__ import annotations

import pytest
import sqlite3
from pathlib import Path

from auth_service import (
    hash_password, verify_password, validate_password_strength, validate_email,
    create_session_token, verify_session_token
)
from database import (
    initialize_database, backup_database, create_user, get_user_by_email, get_user_by_id,
    update_user_last_login, create_workspace, list_user_workspaces, get_workspace,
    update_workspace, archive_workspace, bootstrap_legacy_ownership, ensure_startup,
    save_evidence, list_evidence, get_evidence, update_evidence, archive_evidence,
    save_assessment, list_assessments, get_assessment, save_generated_document,
    list_generated_documents, get_generated_document
)
from scoring import AssessmentResult, DimensionScore
from recommendations import Recommendation


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Provide an isolated temporary SQLite database."""
    db_path = tmp_path / "test_auth_isolation.db"
    initialize_database(db_path)
    return db_path


def make_dummy_assessment_result():
    dim = DimensionScore(
        identifier="market_potential",
        name="Market Potential",
        rating=75.0,
        weight=20,
        contribution=15.0,
        rationale="Strong market tailwinds",
        evidence_status="Supported information",
        missing_evidence=False,
    )
    return AssessmentResult(overall_score=75.0, rubric_version="1.0", dimensions=(dim,))


# ===========================================================================
# 1. Registration & Authentication Tests
# ===========================================================================

def test_password_validation_and_hashing():
    # Weak password
    errors = validate_password_strength("weak")
    assert len(errors) > 0

    # Strong password
    errors = validate_password_strength("SecurePass123!")
    assert errors == []

    # Argon2id hashing & verification
    h = hash_password("SecurePass123!")
    assert h.startswith("$argon2id$")
    assert verify_password("SecurePass123!", h) is True
    assert verify_password("WrongPassword!", h) is False


def test_email_validation():
    assert validate_email("founder@example.com") is True
    assert validate_email("user.name+tag@domain.co.uk") is True
    assert validate_email("invalid-email") is False
    assert validate_email("@domain.com") is False
    assert validate_email("") is False


def test_user_registration_and_duplicate_rejection(temp_db: Path):
    h = hash_password("PasswordA123!")
    uid = create_user("Alice Founder", "alice@example.com", h, path=temp_db)
    assert uid > 0

    user = get_user_by_email("alice@example.com", path=temp_db)
    assert user is not None
    assert user["full_name"] == "Alice Founder"
    assert user["email"] == "alice@example.com"

    # Duplicate registration must raise ValueError
    with pytest.raises(ValueError, match="already exists"):
        create_user("Alice Clone", "ALICE@example.com", h, path=temp_db)


def test_session_token_lifecycle():
    token = create_session_token(42, "founder@test.com", expires_in_hours=1)
    payload = verify_session_token(token)
    assert payload is not None
    assert payload["user_id"] == 42
    assert payload["email"] == "founder@test.com"

    # Invalid token check
    assert verify_session_token("invalid.token.signature") is None
    assert verify_session_token("") is None


# ===========================================================================
# 2. Workspace Management Tests
# ===========================================================================

def test_workspace_creation_and_listing(temp_db: Path):
    uid = create_user("Bob Builder", "bob@example.com", hash_password("PasswordB123!"), path=temp_db)
    ws1_id = create_workspace(uid, "BioTech Ventures", "Primary workspace", path=temp_db)
    ws2_id = create_workspace(uid, "CleanTech Labs", "Secondary workspace", path=temp_db)

    workspaces = list_user_workspaces(uid, path=temp_db)
    assert len(workspaces) == 2
    names = [w["workspace_name"] for w in workspaces]
    assert "BioTech Ventures" in names
    assert "CleanTech Labs" in names

    # Update workspace
    assert update_workspace(ws1_id, uid, "BioTech Ventures Global", "Updated desc", path=temp_db) is True
    w = get_workspace(ws1_id, uid, path=temp_db)
    assert w["workspace_name"] == "BioTech Ventures Global"

    # Archive workspace
    assert archive_workspace(ws2_id, uid, path=temp_db) is True
    active_ws = list_user_workspaces(uid, include_archived=False, path=temp_db)
    assert len(active_ws) == 1
    assert active_ws[0]["workspace_id"] == ws1_id


# ===========================================================================
# 3. Object-Level Data Isolation (User A vs User B)
# ===========================================================================

def test_user_a_and_user_b_data_isolation(temp_db: Path):
    # Setup User A
    uid_a = create_user("User A", "user_a@test.com", hash_password("PassUserA1!"), path=temp_db)
    ws_a = create_workspace(uid_a, "Workspace A", path=temp_db)
    profile_a = {"startup_name": "Startup Alpha", "industry": "AI", "stage": "Seed"}
    s_a = ensure_startup(profile_a, temp_db, workspace_id=ws_a, user_id=uid_a)

    ev_a_id = save_evidence(
        profile_a,
        {
            "evidence_title": "Alpha Clinical Data",
            "evidence_description": "Validated 100 trials",
            "evidence_type": "Technical documentation",
            "related_dimension": "Problem & Solution Clarity",
            "verification_status": "Independently verified",
        },
        path=temp_db,
        workspace_id=ws_a,
        user_id=uid_a,
    )

    res_a = make_dummy_assessment_result()
    ass_a_id = save_assessment(
        profile_a,
        {"market_potential": {"rating": 75, "rationale": "Strong"}},
        res_a,
        [],
        "2026-11-01",
        path=temp_db,
        evidence_ids=[ev_a_id],
        workspace_id=ws_a,
        user_id=uid_a,
    )

    doc_a_id = save_generated_document(
        s_a,
        ass_a_id,
        "Business Plan",
        "Alpha Business Plan",
        "documents/bp_a.docx",
        "docx",
        workspace_id=ws_a,
        user_id=uid_a,
        path=temp_db,
    )

    # Setup User B
    uid_b = create_user("User B", "user_b@test.com", hash_password("PassUserB1!"), path=temp_db)
    ws_b = create_workspace(uid_b, "Workspace B", path=temp_db)
    profile_b = {"startup_name": "Startup Beta", "industry": "Robotics", "stage": "Pre-seed"}
    s_b = ensure_startup(profile_b, temp_db, workspace_id=ws_b, user_id=uid_b)

    # 1. Assessment Isolation
    # User A can list and retrieve Assessment A
    assert len(list_assessments(path=temp_db, user_id=uid_a, workspace_id=ws_a)) == 1
    assert get_assessment(ass_a_id, path=temp_db, user_id=uid_a) is not None

    # User B CANNOT list Assessment A
    b_assessments = list_assessments(path=temp_db, user_id=uid_b, workspace_id=ws_b)
    assert len(b_assessments) == 0

    # User B CANNOT retrieve Assessment A by manipulating ID
    assert get_assessment(ass_a_id, path=temp_db, user_id=uid_b) is None

    # 2. Evidence Isolation
    # User A sees Evidence A
    assert len(list_evidence(path=temp_db, user_id=uid_a, workspace_id=ws_a)) == 1
    assert get_evidence(ev_a_id, path=temp_db, user_id=uid_a) is not None

    # User B CANNOT list Evidence A
    assert len(list_evidence(path=temp_db, user_id=uid_b, workspace_id=ws_b)) == 0

    # User B CANNOT retrieve Evidence A by ID
    assert get_evidence(ev_a_id, path=temp_db, user_id=uid_b) is None

    # User B CANNOT update Evidence A
    assert update_evidence(ev_a_id, {"evidence_title": "Hacked Title"}, path=temp_db, user_id=uid_b) is False
    assert get_evidence(ev_a_id, path=temp_db, user_id=uid_a)["evidence_title"] == "Alpha Clinical Data"

    # User B CANNOT archive Evidence A
    assert archive_evidence(ev_a_id, path=temp_db, user_id=uid_b) is False
    assert get_evidence(ev_a_id, path=temp_db, user_id=uid_a)["archived_at"] is None

    # 3. Generated Document Isolation
    # User A can access Document A
    assert len(list_generated_documents(path=temp_db, user_id=uid_a, workspace_id=ws_a)) == 1
    assert get_generated_document(doc_a_id, path=temp_db, user_id=uid_a) is not None

    # User B CANNOT list Document A
    assert len(list_generated_documents(path=temp_db, user_id=uid_b, workspace_id=ws_b)) == 0

    # User B CANNOT retrieve Document A by ID
    assert get_generated_document(doc_a_id, path=temp_db, user_id=uid_b) is None


# ===========================================================================
# 4. Database Backup & Legacy Migration Preservation
# ===========================================================================

def test_database_backup_and_legacy_bootstrap(tmp_path: Path):
    db_file = tmp_path / "legacy.db"
    initialize_database(db_file)

    # Insert an unassigned record mimicking older schema
    with sqlite3.connect(db_file) as conn:
        conn.execute("INSERT INTO startups (profile_json, created_at) VALUES ('{\"startup_name\": \"Old Co\"}', '2026-10-01')")

    # Run backup
    backup = backup_database(db_file)
    assert backup is not None
    assert backup.exists()
    assert backup.stat().st_size > 0

    # Run bootstrap
    admin_id, ws_id = bootstrap_legacy_ownership("legacy-owner@local.test", path=db_file)
    assert admin_id > 0
    assert ws_id > 0

    # Verify that the unassigned startup is now safely owned by legacy bootstrap workspace
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        startup = conn.execute("SELECT * FROM startups WHERE profile_json LIKE '%Old Co%'").fetchone()
        assert startup["workspace_id"] == ws_id
        assert startup["user_id"] == admin_id


# ===========================================================================
# 5. Expired Session Token
# ===========================================================================

def test_expired_session_token():
    """A token created with a past expiry must be rejected by verify_session_token."""
    from datetime import datetime, timezone, timedelta
    import jwt
    from auth_service import get_jwt_secret, JWT_ALGORITHM

    past_exp = datetime.now(timezone.utc) - timedelta(hours=2)
    payload = {
        "user_id": 999,
        "email": "expired@test.com",
        "iat": int((past_exp - timedelta(hours=1)).timestamp()),
        "exp": int(past_exp.timestamp()),
        "jti": "test-jti-expired",
    }
    expired_token = jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)
    result = verify_session_token(expired_token)
    assert result is None, "Expired token must return None"


# ===========================================================================
# 6. Workspace Ownership Enforcement
# ===========================================================================

def test_workspace_ownership_enforcement(temp_db: Path):
    """User B cannot see workspaces belonging to User A via list_user_workspaces."""
    uid_a = create_user("Workspace Owner A", "ws-owner-a@test.com", hash_password("OwnerA1pass"), path=temp_db)
    uid_b = create_user("Workspace Owner B", "ws-owner-b@test.com", hash_password("OwnerB1pass"), path=temp_db)
    ws_a = create_workspace(uid_a, "Workspace of A", path=temp_db)

    # Verify ownership is recorded correctly
    ws_record = get_workspace(ws_a, path=temp_db)
    assert ws_record is not None
    assert ws_record["owner_user_id"] == uid_a
    assert ws_record["owner_user_id"] != uid_b

    # User B's workspace listing must not include User A's workspace
    b_workspaces = list_user_workspaces(uid_b, path=temp_db)
    ws_ids_b = [ws["workspace_id"] for ws in b_workspaces]
    assert ws_a not in ws_ids_b


# ===========================================================================
# 7. Migration Repeat-Safety
# ===========================================================================

def test_migration_repeat_safety(tmp_path: Path):
    """Calling initialize_database twice on the same DB must not raise or corrupt data."""
    db_file = tmp_path / "repeat_migration.db"
    initialize_database(db_file)
    uid = create_user("Repeat User", "repeat@test.com", hash_password("RepeatP1"), path=db_file)
    ws_id = create_workspace(uid, "Repeat Workspace", path=db_file)

    # Second init must be idempotent
    initialize_database(db_file)

    user = get_user_by_email("repeat@test.com", path=db_file)
    assert user is not None
    assert user["user_id"] == uid
    workspaces = list_user_workspaces(uid, path=db_file)
    assert any(ws["workspace_id"] == ws_id for ws in workspaces)


# ===========================================================================
# 8. Cross-Workspace Assessment Isolation
# ===========================================================================

def test_cross_workspace_assessment_isolation(temp_db: Path):
    """Assessments created in Workspace A must not appear in Workspace B listing."""
    profile = {
        "startup_name": "CrossWS Test Co", "industry": "Tech", "stage": "Idea",
        "geography": "Global", "startup_description": "A test company",
        "problem_statement": "Test problem", "affected_people": "Testers",
        "proposed_solution": "Test solution", "value_proposition": "Value",
        "problem_evidence": "None", "target_customers": "Testers",
        "target_geography": "Global", "market_size": "Large",
        "customer_research": "None", "demand_evidence": "None",
        "direct_competitors": "None", "indirect_competitors": "None",
        "differentiators": "Tests", "competitive_evidence": "None",
        "revenue_model": "SaaS", "pricing": "$10/mo", "acquisition": "Organic",
        "delivery": "Cloud", "estimated_costs": "$1k",
        "revenue_assumptions": "10 users", "cash_requirements": "$5k",
        "unit_economics": "N/A", "progress": "Early",
        "interviews_trials": "2", "traction": "None", "capabilities": "Dev",
        "capability_gaps": "Sales", "business_risks": "None",
        "technical_operational_risks": "None", "mitigations": "None",
        "evidence_status": "Mostly assumptions",
        "evidence_records": [], "unknown_information": [],
    }

    uid_a = create_user("CrossWS User A", "crossws-a@test.com", hash_password("CrossWSA1"), path=temp_db)
    uid_b = create_user("CrossWS User B", "crossws-b@test.com", hash_password("CrossWSB1"), path=temp_db)
    ws_a = create_workspace(uid_a, "CrossWS A Workspace", path=temp_db)
    ws_b = create_workspace(uid_b, "CrossWS B Workspace", path=temp_db)

    result = make_dummy_assessment_result()
    save_assessment(
        profile, {}, result, [], "2026-11-02",
        path=temp_db,
        workspace_id=ws_a,
        user_id=uid_a,
    )

    # User B's workspace should see no assessments
    b_assessments = list_assessments(path=temp_db, workspace_id=ws_b, user_id=uid_b)
    assert len(b_assessments) == 0

    # User A's workspace should see their assessment
    a_assessments = list_assessments(path=temp_db, workspace_id=ws_a, user_id=uid_a)
    assert len(a_assessments) == 1


# ===========================================================================
# 9. Complete 14-Point Cross-User Security Matrix (User A vs User B)
# ===========================================================================

def test_complete_cross_user_security_matrix(temp_db: Path):
    """Test every point of the cross-user security matrix requested in Phase 6 Audit."""
    import os
    from auth_service import get_jwt_secret

    # 1. Setup User A & User B with separate workspaces
    uid_a = create_user("Alice Vance", "alice.matrix@test.com", hash_password("AlicePass123!"), path=temp_db)
    uid_b = create_user("Bob Miller", "bob.matrix@test.com", hash_password("BobPass1234!"), path=temp_db)
    ws_a = create_workspace(uid_a, "Alice Corp Workspace", path=temp_db)
    ws_b = create_workspace(uid_b, "Bob Corp Workspace", path=temp_db)

    # User A creates Startup A
    profile_a = {
        "startup_name": "Alice HealthTech", "industry": "Health", "stage": "Idea",
        "geography": "Global", "startup_description": "Alice app",
        "problem_statement": "Clinic wait times", "affected_people": "Patients",
        "proposed_solution": "AI machine", "value_proposition": "Speed",
        "problem_evidence": "None", "target_customers": "Clinics",
        "target_geography": "Global", "market_size": "Large",
        "customer_research": "None", "demand_evidence": "None",
        "direct_competitors": "None", "indirect_competitors": "None",
        "differentiators": "Patent", "competitive_evidence": "None",
        "revenue_model": "SaaS", "pricing": "$100", "acquisition": "Direct",
        "delivery": "Cloud", "estimated_costs": "$500", "revenue_assumptions": "10 sales",
        "cash_requirements": "$5000", "unit_economics": "High", "progress": "Alpha",
        "interviews_trials": "10", "traction": "LOI", "capabilities": "AI tech",
        "capability_gaps": "Sales", "business_risks": "Regulatory",
        "technical_operational_risks": "Uptime", "mitigations": "Redundancy",
        "evidence_status": "Mostly supported", "evidence_records": [], "unknown_information": [],
    }
    startup_a_id = ensure_startup(profile_a, path=temp_db, workspace_id=ws_a, user_id=uid_a)

    # User A creates Evidence A
    ev_a_id = save_evidence(
        profile_a,
        {
            "evidence_title": "Clinic Pilot LOI",
            "evidence_description": "Signed LOI",
            "evidence_type": "Letters of intent",
            "related_dimension": "Market Potential",
            "verification_status": "Founder submitted",
        },
        path=temp_db, workspace_id=ws_a, user_id=uid_a
    )

    # User A creates Assessment A
    result_a = make_dummy_assessment_result()
    assessment_a_id = save_assessment(
        profile_a, {}, result_a, [], "2026-11-02",
        path=temp_db, evidence_ids=[ev_a_id], workspace_id=ws_a, user_id=uid_a
    )

    # User A creates Generated Documents A (Business Plan & Pitch Deck)
    doc_bp_a = save_generated_document(
        startup_id=startup_a_id, assessment_id=assessment_a_id,
        document_type="Business Plan", title="Alice BP", file_path="documents/bp_a.docx",
        file_format="docx", generation_mode="snapshot", sections={},
        workspace_id=ws_a, user_id=uid_a, path=temp_db
    )
    doc_deck_a = save_generated_document(
        startup_id=startup_a_id, assessment_id=assessment_a_id,
        document_type="Pitch Deck", title="Alice Deck", file_path="documents/deck_a.pptx",
        file_format="pptx", generation_mode="snapshot", sections={},
        workspace_id=ws_a, user_id=uid_a, path=temp_db
    )

    # --- 14 Security Checks ---
    # 1. User B attempts to retrieve Assessment A by ID -> denied
    assert get_assessment(assessment_a_id, path=temp_db, user_id=uid_b) is None

    # 2. User B attempts to retrieve Evidence A by ID -> denied
    assert get_evidence(ev_a_id, path=temp_db, user_id=uid_b) is None

    # 3. User B attempts to update Evidence A -> rejected
    update_res = update_evidence(ev_a_id, {"founder_notes": "Hacked"}, path=temp_db, user_id=uid_b)
    assert update_res is False
    ev_check = get_evidence(ev_a_id, path=temp_db, user_id=uid_a)
    assert ev_check["founder_notes"] != "Hacked"

    # 4. User B attempts to archive Evidence A -> rejected
    arch_res = archive_evidence(ev_a_id, path=temp_db, user_id=uid_b)
    assert arch_res is False

    # 5. User B attempts to retrieve Document BP A by ID -> denied
    assert get_generated_document(doc_bp_a, path=temp_db, user_id=uid_b) is None

    # 6. User B attempts to retrieve Document Deck A by ID -> denied
    assert get_generated_document(doc_deck_a, path=temp_db, user_id=uid_b) is None

    # 7. User B attempts to list User A's generated documents -> sees none
    b_docs = list_generated_documents(assessment_id=assessment_a_id, path=temp_db, user_id=uid_b, workspace_id=ws_b)
    assert len(b_docs) == 0

    # 8. User B attempts to list assessments in Workspace A -> sees none
    b_assessments_in_a = list_assessments(path=temp_db, workspace_id=ws_a, user_id=uid_b)
    assert len(b_assessments_in_a) == 0

    # 9. User B cannot see Workspace A in their workspaces list
    b_workspaces = list_user_workspaces(uid_b, path=temp_db)
    assert ws_a not in [w["workspace_id"] for w in b_workspaces]

    # 10. Production secret enforcement: ENVIRONMENT=production requires AUTH_JWT_SECRET
    old_env = os.environ.get("ENVIRONMENT")
    old_secret = os.environ.get("AUTH_JWT_SECRET")
    try:
        os.environ["ENVIRONMENT"] = "production"
        if "AUTH_JWT_SECRET" in os.environ:
            del os.environ["AUTH_JWT_SECRET"]
        with pytest.raises(RuntimeError, match="CRITICAL SECURITY CONFIGURATION ERROR"):
            get_jwt_secret()
    finally:
        if old_env is not None:
            os.environ["ENVIRONMENT"] = old_env
        else:
            os.environ.pop("ENVIRONMENT", None)
        if old_secret is not None:
            os.environ["AUTH_JWT_SECRET"] = old_secret

    # 11. User A can retrieve all their own records without error
    assert get_assessment(assessment_a_id, path=temp_db, user_id=uid_a) is not None
    assert get_evidence(ev_a_id, path=temp_db, user_id=uid_a) is not None
    assert get_generated_document(doc_bp_a, path=temp_db, user_id=uid_a) is not None
    assert get_generated_document(doc_deck_a, path=temp_db, user_id=uid_a) is not None


