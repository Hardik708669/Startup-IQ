"""Automated tests for Business Plan (DOCX) and Pitch Deck (PPTX) generators."""

import tempfile
from pathlib import Path
import pytest
from docx import Document
from pptx import Presentation

from business_plan_generator import (
    BUSINESS_PLAN_SECTIONS, create_business_plan_docx, draft_business_plan_content,
)
from database import (
    ensure_startup, get_generated_document, initialize_database, list_generated_documents,
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
    startup_id = ensure_startup(profile, db_path)
    ass_record = sample_assessment(profile, db_path)
    ass_id = ass_record["id"]

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

