"""Automated tests for Business Plan (DOCX) and Pitch Deck (PPTX) generators."""

import tempfile
from pathlib import Path
import pytest
import database
from docx import Document
from pptx import Presentation

from business_plan_generator import (
    BUSINESS_PLAN_SECTIONS, create_business_plan_docx, draft_business_plan_content,
)
from database import (
    create_user, create_workspace, ensure_startup, get_generated_document, initialize_database, list_generated_documents,
    resolve_generated_document_path,
    publish_generated_document,
    save_assessment, save_evidence, save_generated_document,
)
from pitch_deck_generator import (
    PITCH_DECK_SLIDES, create_pitch_deck_pptx, draft_pitch_deck_slides,
)
from recommendations import generate_recommendations
from scoring import DIMENSIONS, calculate_assessment


def sample_profile():
    return {
        "startup_name": "SolarGrid Dynamics",
        "industry": "CleanTech",
        "stage": "Seed",
        "startup_description": "Decentralized solar micro-grids with AI battery balancing.",
        "problem_statement": "Rural commercial hubs face 8-hour daily blackouts.",
        "proposed_solution": "Containerized hybrid solar arrays with automated battery dispatch.",
        "target_customers": "Rural agricultural cooperatives and food processors.",
        "revenue_model": "Power purchase agreements (PPA) at $0.18/kWh.",
        "pricing": "$0.18 per kWh with a $500 monthly connection fee.",
        "direct_competitors": "Diesel generator suppliers and regional utilities.",
        "differentiators": "60% cheaper LCOE than diesel generators with zero fuel downtime.",
        "estimated_costs": "Capex $120,000 per installation; monthly maintenance $800.",
        "cash_requirements": "$800,000 for 10 micro-grid deployments.",
        "traction": "3 operational pilots producing 45 MWh to date.",
    }


def sample_assessment(profile, path):
    inputs = {d.identifier: {"rating": 85, "reviewed": True, "evidence_status": "Supported information", "rationale": "Empirical pilot data"} for d in DIMENSIONS}
    res = calculate_assessment(inputs)
    recs = generate_recommendations(res)
    ass_id = save_assessment(profile, inputs, res, recs, "2026-11-01", path)
    from database import get_assessment
    return get_assessment(ass_id, path)


def test_business_plan_content_drafting_complete_sections():
    profile = sample_profile()
    sections = draft_business_plan_content(profile)
    assert len(sections) == 22
    for _, key in BUSINESS_PLAN_SECTIONS:
        assert key in sections
        assert len(sections[key]) > 0
    assert "SolarGrid Dynamics" in sections["executive_summary"]
    assert "CleanTech" in sections["startup_overview"]


def test_business_plan_placeholders_for_missing_fields():
    # Incomplete profile with missing competitors, costs, and market data
    bare_profile = {"startup_name": "Bare Bones"}
    sections = draft_business_plan_content(bare_profile)
    assert "[Founder input required:" in sections["competitor_analysis"]
    assert "[Founder input required:" in sections["financial_assumptions"]
    assert "[Founder input required:" in sections["market_opportunity"]


def test_business_plan_docx_validity(tmp_path):
    profile = sample_profile()
    sections = draft_business_plan_content(profile)
    out_file = tmp_path / "test_plan.docx"
    docx_bytes = create_business_plan_docx(profile, sections, out_file)

    assert len(docx_bytes) > 2000
    assert out_file.exists()

    # Parse with python-docx to verify integrity
    doc = Document(out_file)
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert any("Executive Summary" in h for h in headings)
    assert any("Risk Analysis" in h for h in headings)


def test_pitch_deck_slides_drafting():
    profile = sample_profile()
    slides = draft_pitch_deck_slides(profile)
    assert len(slides) == 12
    for _, key in PITCH_DECK_SLIDES:
        assert key in slides
        assert "title" in slides[key]
        assert "bullets" in slides[key]
        assert len(slides[key]["bullets"]) > 0


def test_pitch_deck_pptx_validity(tmp_path):
    profile = sample_profile()
    slides = draft_pitch_deck_slides(profile)
    out_file = tmp_path / "test_deck.pptx"
    pptx_bytes = create_pitch_deck_pptx(profile, slides, out_file)

    assert len(pptx_bytes) > 2000
    assert out_file.exists()

    # Parse with python-pptx to verify integrity
    prs = Presentation(out_file)
    assert len(prs.slides) == 12
    # Verify widescreen 16:9 dimensions
    assert prs.slide_width > prs.slide_height


def test_document_metadata_and_versioning(tmp_path):
    db_path = tmp_path / "test_docs.db"
    initialize_database(db_path)
    profile = sample_profile()
    user_id = create_user("Docs Owner", "docs-owner@example.test", "hash", db_path)
    workspace_id = create_workspace(user_id, "Docs Workspace", path=db_path)
    startup_id = ensure_startup(profile, db_path, workspace_id=workspace_id, user_id=user_id)
    inputs = {d.identifier: {"rating": 85, "reviewed": True, "evidence_status": "Supported information", "rationale": "Empirical pilot data"} for d in DIMENSIONS}
    result = calculate_assessment(inputs)
    ass_id = save_assessment(profile, inputs, result, generate_recommendations(result), path=db_path, workspace_id=workspace_id, user_id=user_id)

    # Save version 1
    doc1_id = save_generated_document(
        startup_id=startup_id,
        assessment_id=ass_id,
        document_type="Business Plan",
        title="SolarGrid Business Plan v1",
        file_path="documents/bp-v1.docx",
        file_format="docx",
        generation_mode="verified_snapshot",
        path=db_path,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    d1 = get_generated_document(doc1_id, db_path)
    assert d1["document_version"] == 1
    assert d1["document_type"] == "Business Plan"

    # Save version 2 for the same assessment and document type
    doc2_id = save_generated_document(
        startup_id=startup_id,
        assessment_id=ass_id,
        document_type="Business Plan",
        title="SolarGrid Business Plan v2",
        file_path="documents/bp-v2.docx",
        file_format="docx",
        generation_mode="verified_snapshot",
        path=db_path,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    d2 = get_generated_document(doc2_id, db_path)
    assert d2["document_version"] == 2

    # Query document history
    docs = list_generated_documents(assessment_id=ass_id, path=db_path)
    assert len(docs) == 2
    assert docs[0]["document_version"] == 2
    assert docs[1]["document_version"] == 1


def test_historical_assessment_document_consistency(tmp_path):
    db_path = tmp_path / "test_hist_docs.db"
    initialize_database(db_path)
    p1 = sample_profile()
    ass1 = sample_assessment(p1, db_path)

    # Draft documents using historical snapshot
    sections = draft_business_plan_content(ass1["profile"], assessment=ass1, evidence=ass1.get("evidence", []))
    assert "SolarGrid Dynamics" in sections["startup_overview"]
    assert "Analytical Readiness Score: 85.00 / 100" in sections["assessment_summary"]


def test_same_startup_name_documents_are_isolated_on_disk(tmp_path):
    db_path = tmp_path / "isolated_docs.db"
    documents = tmp_path / "documents"
    initialize_database(db_path)
    user_a = create_user("User A", "docs-a@example.test", "hash-a", db_path)
    user_b = create_user("User B", "docs-b@example.test", "hash-b", db_path)
    workspace_a = create_workspace(user_a, "Workspace A", path=db_path)
    workspace_b = create_workspace(user_b, "Workspace B", path=db_path)
    profile = sample_profile()
    inputs = {d.identifier: {"rating": 80, "reviewed": True, "evidence_status": "Estimate", "rationale": "Test"} for d in DIMENSIONS}
    result = calculate_assessment(inputs)
    recs = generate_recommendations(result)
    ass_a = save_assessment(profile, inputs, result, recs, path=db_path, workspace_id=workspace_a, user_id=user_a)
    ass_b = save_assessment(profile, inputs, result, recs, path=db_path, workspace_id=workspace_b, user_id=user_b)
    startup_a = ensure_startup(profile, db_path, workspace_id=workspace_a, user_id=user_a)
    startup_b = ensure_startup(profile, db_path, workspace_id=workspace_b, user_id=user_b)
    files = []
    for uid, ws, startup, assessment, kind, marker in [
        (user_a, workspace_a, startup_a, ass_a, "Business Plan", b"USER_A_PLAN"),
        (user_b, workspace_b, startup_b, ass_b, "Business Plan", b"USER_B_PLAN"),
        (user_a, workspace_a, startup_a, ass_a, "Pitch Deck", b"USER_A_DECK"),
        (user_b, workspace_b, startup_b, ass_b, "Pitch Deck", b"USER_B_DECK"),
    ]:
        doc_id = f"{uid}-{kind.replace(' ', '-').lower()}"
        filename = f"{kind.replace(' ', '-').lower()}-neurosync-{doc_id}.{'docx' if kind == 'Business Plan' else 'pptx'}"
        relative = f"documents/workspace-{ws}/{filename}"
        target = documents / f"workspace-{ws}" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(marker)
        save_generated_document(startup, assessment, kind, filename, relative, target.suffix[1:], path=db_path, workspace_id=ws, user_id=uid, document_id=doc_id)
        files.append((doc_id, uid, ws, marker))
    paths = [resolve_generated_document_path(doc_id, user_id=uid, workspace_id=ws, path=db_path, documents_dir=documents) for doc_id, uid, ws, _ in files]
    assert len({str(p) for p in paths}) == 4
    for (doc_id, uid, ws, marker), file_path in zip(files, paths):
        assert file_path.read_bytes() == marker
        other = user_b if uid == user_a else user_a
        other_ws = workspace_b if uid == user_a else workspace_a
        with pytest.raises(PermissionError):
            resolve_generated_document_path(doc_id, user_id=other, workspace_id=other_ws, path=db_path, documents_dir=documents)


def test_generated_document_publish_cleans_temporary_file_on_write_failure(tmp_path, monkeypatch):
    documents = tmp_path / "documents"
    real_link = database.os.link
    monkeypatch.setattr(database.os, "link", lambda source, target: (_ for _ in ()).throw(OSError("publish failure")))
    with pytest.raises(OSError, match="publish failure"):
        publish_generated_document("doc-write-failure", b"complete bytes", "documents/workspace-1/plan.docx", documents_dir=documents)
    assert not list(documents.rglob("*.tmp"))
    assert not list(documents.rglob("*.docx"))


def test_generated_document_cleanup_failure_is_logged_and_original_error_preserved(tmp_path, monkeypatch, caplog):
    documents = tmp_path / "documents"
    real_unlink = database.Path.unlink

    monkeypatch.setattr(database.os, "link", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("publication failed")))

    def fail_temporary_cleanup(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise OSError("cleanup failed")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(database.Path, "unlink", fail_temporary_cleanup)
    with caplog.at_level("ERROR", logger="database"):
        with pytest.raises(OSError, match="publication failed"):
            publish_generated_document("cleanup-log", b"bytes", "documents/workspace-1/plan.docx", documents_dir=documents)
    assert "temporary cleanup failed" in caplog.text
    for temporary in documents.rglob("*.tmp"):
        real_unlink(temporary)


def test_save_generated_document_validates_complete_ownership_graph(tmp_path):
    db_path = tmp_path / "ownership.db"
    initialize_database(db_path)
    user_a = create_user("A", "owner-a@example.test", "hash-a", db_path)
    user_b = create_user("B", "owner-b@example.test", "hash-b", db_path)
    workspace_a = create_workspace(user_a, "A Workspace", path=db_path)
    workspace_b = create_workspace(user_b, "B Workspace", path=db_path)
    profile = sample_profile()
    inputs = {d.identifier: {"rating": 80, "reviewed": True, "evidence_status": "Estimate", "rationale": "Test"} for d in DIMENSIONS}
    result = calculate_assessment(inputs)
    recs = generate_recommendations(result)
    assessment_a = save_assessment(profile, inputs, result, recs, path=db_path, workspace_id=workspace_a, user_id=user_a)
    startup_a = ensure_startup(profile, db_path, workspace_id=workspace_a, user_id=user_a)
    with pytest.raises((PermissionError, ValueError)):
        save_generated_document(startup_a, assessment_a, "Business Plan", "bad", "documents/bad.docx", "docx", path=db_path, workspace_id=workspace_b, user_id=user_b)
    with pytest.raises(ValueError):
        save_generated_document(startup_a, assessment_a, "Business Plan", "missing", "documents/missing.docx", "docx", path=db_path)
    document_id = "duplicate-document-id"
    save_generated_document(startup_a, assessment_a, "Business Plan", "good", "documents/good.docx", "docx", path=db_path, workspace_id=workspace_a, user_id=user_a, document_id=document_id)
    with pytest.raises(ValueError, match="already exists"):
        save_generated_document(startup_a, assessment_a, "Business Plan", "duplicate", "documents/duplicate.docx", "docx", path=db_path, workspace_id=workspace_a, user_id=user_a, document_id=document_id)
    assert len(list_generated_documents(path=db_path, workspace_id=workspace_a, user_id=user_a)) == 1


def test_unowned_legacy_records_cannot_accept_new_generated_documents(tmp_path):
    db_path = tmp_path / "legacy-write.db"
    initialize_database(db_path)
    profile = sample_profile()
    startup_id = ensure_startup(profile, db_path)
    assessment_id = sample_assessment(profile, db_path)["id"]
    user_id = create_user("New Owner", "new-owner@example.test", "hash", db_path)
    workspace_id = create_workspace(user_id, "New Workspace", path=db_path)
    with pytest.raises(ValueError, match="ownership"):
        save_generated_document(
            startup_id, assessment_id, "Business Plan", "legacy", "documents/legacy.docx", "docx",
            path=db_path, workspace_id=workspace_id, user_id=user_id,
        )
    assert list_generated_documents(path=db_path) == []

