"""Automated tests for Document Text Extraction and AI Evidence Analysis."""

import io
from pathlib import Path
import pytest
from docx import Document
import openpyxl
from pypdf import PdfWriter

from document_extractor import (
    extract_document_text,
    ExtractionResult,
    DEFAULT_MAX_BYTES,
)
from ai_service import (
    analyze_extracted_document,
    heuristic_analyze_document,
    parse_document_analysis_json,
    DocumentAnalysisResult,
    DocumentEvidenceClaim,
)
from database import (
    initialize_database,
    create_user,
    create_workspace,
    ensure_startup,
    save_evidence,
    list_evidence,
    get_evidence,
)
from auth_service import hash_password


# ---------------------------------------------------------------------------
# 1. Document Extraction Tests
# ---------------------------------------------------------------------------

def test_txt_extraction():
    content = "Customer interview summary: Users spend 4 hours daily sorting medical files."
    data = content.encode("utf-8")
    res = extract_document_text(data, "interview.txt")
    assert res.success is True
    assert content in res.text
    assert res.file_type == ".txt"
    assert res.char_count == len(content)


def test_txt_encoding_fallback():
    content = "Café & naïve assumptions"
    data = content.encode("latin-1")
    res = extract_document_text(data, "notes.txt")
    assert res.success is True
    assert "Café" in res.text


def test_docx_extraction():
    doc = Document()
    doc.add_paragraph("First paragraph of customer feedback.")
    doc.add_paragraph("Second paragraph: 85% expressed willingness to pay $50/mo.")
    
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Metric"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Trial conversion"
    table.cell(1, 1).text = "12%"

    stream = io.BytesIO()
    doc.save(stream)
    data = stream.getvalue()

    res = extract_document_text(data, "feedback.docx")
    assert res.success is True
    assert "First paragraph" in res.text
    assert "willingness to pay" in res.text
    assert "Trial conversion | 12%" in res.text
    assert res.file_type == ".docx"


def test_csv_extraction():
    csv_content = "Customer,Score,Feedback\nClinic A,9,Faster than current tool\nClinic B,8,Would buy today"
    data = csv_content.encode("utf-8")
    res = extract_document_text(data, "survey.csv")
    assert res.success is True
    assert "Clinic A | 9 | Faster than current tool" in res.text
    assert res.file_type == ".csv"


def test_xlsx_extraction():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Financials"
    ws.append(["Category", "Monthly Cost", "Assumption"])
    ws.append(["Hosting", 450, "AWS quote"])
    ws.append(["Salaries", 12000, "2 engineers"])

    stream = io.BytesIO()
    wb.save(stream)
    data = stream.getvalue()

    res = extract_document_text(data, "model.xlsx")
    assert res.success is True
    assert "Sheet: Financials" in res.text
    assert "Hosting | 450 | AWS quote" in res.text
    assert res.file_type == ".xlsx"


def test_pdf_extraction():
    # Construct a minimal text PDF in memory using pypdf
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    # Even without text layer, it should parse without raising
    stream = io.BytesIO()
    writer.write(stream)
    data = stream.getvalue()

    res = extract_document_text(data, "document.pdf")
    # Empty page should be handled gracefully
    assert res.file_type == ".pdf"
    assert res.is_scanned_or_empty is True or res.success is True


def test_empty_file():
    res = extract_document_text(b"", "empty.txt")
    assert res.success is False
    assert "empty" in res.error.lower()


def test_unsupported_extension():
    res = extract_document_text(b"some content", "file.exe")
    assert res.success is False
    assert "unsupported" in res.error.lower()


def test_oversized_file():
    fake_huge = b"a" * 1000
    res = extract_document_text(fake_huge, "huge.txt", max_bytes=500)
    assert res.success is False
    assert "exceeds maximum" in res.error.lower()


def test_path_traversal_sanitization():
    res = extract_document_text(b"data", "../../../etc/passwd.txt")
    assert res.success is True
    assert res.file_type == ".txt"


# ---------------------------------------------------------------------------
# 2. AI Document Analysis & Evidence Extraction Tests
# ---------------------------------------------------------------------------

def test_heuristic_analyze_document():
    doc_text = """
    Customer interviews: Founders interviewed 25 emergency physicians.
    Survey results: 78% of respondents reported severe burnout due to paperwork.
    Pilot program: Signed LOI with Metro Hospital for a 30-day trial at $1,500/month.
    Financial model: Estimated costs are $4,000 per month with gross margin of 70%.
    Competitor research: Alternative solutions take 48 hours to return results vs our 20 minutes.
    """
    res = heuristic_analyze_document(doc_text, "validation_summary.txt")
    assert isinstance(res, DocumentAnalysisResult)
    assert len(res.claims) >= 3
    assert any(c.evidence_type == "Customer interviews" for c in res.claims)
    assert any(c.evidence_type == "Customer survey results" for c in res.claims)
    assert any("LOI" in c.claim_title or "Pilot" in c.claim_title for c in res.claims)
    assert any(c.evidence_type == "Financial assumptions" for c in res.claims)
    assert res.provider_used == "heuristic"


def test_parse_document_analysis_json():
    sample_json = """{
      "document_summary": "Pilot validation report for clinic AI.",
      "claims": [
        {
          "claim_title": "Clinic Pilot Agreement",
          "claim_description": "Clinic signed a 6-month trial contract",
          "evidence_type": "Pilot results",
          "related_dimension": "Pitch Readiness",
          "supporting_excerpt": "Signed trial contract on Sept 14",
          "is_directly_stated": true,
          "confidence": "High",
          "recommended_verification": "Inspect signed contract copy"
        }
      ],
      "missing_evidence_gaps": [
        {
          "section": "Validation Gap",
          "dimension": "Financial Readiness",
          "missing_information": "Pricing tier structure not validated",
          "why_it_matters": "Revenue clarity required",
          "suggested_question": "What is the per-seat pricing?",
          "suggested_evidence": "Pricing survey"
        }
      ]
    }"""
    res = parse_document_analysis_json(sample_json, "trial.pdf", "mock-ai")
    assert len(res.claims) == 1
    assert res.claims[0].claim_title == "Clinic Pilot Agreement"
    assert res.claims[0].supporting_excerpt == "Signed trial contract on Sept 14"
    assert len(res.missing_evidence_gaps) == 1
    assert res.provider_used == "mock-ai"


def test_analyze_extracted_document_fallback_without_keys():
    # When no API key is passed, gracefully returns heuristic result without error
    res = analyze_extracted_document(
        "Founders interviewed 12 users who confirmed problem exists.",
        "interviews.txt",
        api_key=None
    )
    assert res.provider_used == "heuristic"
    assert len(res.claims) >= 1
    assert res.claims[0].evidence_type == "Customer interviews"


# ---------------------------------------------------------------------------
# 3. Evidence Registry Integration & Ownership Tests
# ---------------------------------------------------------------------------

def test_approved_claim_saved_with_ownership(tmp_path: Path):
    db_path = tmp_path / "test_doc_ext.db"
    initialize_database(db_path)

    uid = create_user("Doc Founder", "doc@founder.com", hash_password("Password123!"), path=db_path)
    ws_id = create_workspace(uid, "Doc Workspace", path=db_path)

    profile = {
        "startup_name": "DocTest AI", "industry": "Tech", "stage": "Idea",
        "geography": "US", "startup_description": "AI extractor",
        "problem_statement": "Manual doc review is slow", "affected_people": "Lawyers",
        "proposed_solution": "Auto extractor", "value_proposition": "Fast",
        "problem_evidence": "Interviews", "target_customers": "Law firms",
        "target_geography": "US", "market_size": "Large",
        "customer_research": "None", "demand_evidence": "None",
        "direct_competitors": "None", "indirect_competitors": "None",
        "differentiators": "Speed", "competitive_evidence": "None",
        "revenue_model": "SaaS", "pricing": "$50", "acquisition": "Direct",
        "delivery": "Cloud", "estimated_costs": "$500", "revenue_assumptions": "10 sales",
        "cash_requirements": "$2000", "unit_economics": "High", "progress": "Alpha",
        "interviews_trials": "5", "traction": "None", "capabilities": "Python",
        "capability_gaps": "Sales", "business_risks": "None",
        "technical_operational_risks": "None", "mitigations": "None",
        "evidence_status": "Mostly supported", "evidence_records": [], "unknown_information": [],
    }

    # Extract & Analyze
    text = "Founders interviewed 15 legal partners who confirmed documents take 10 hours to audit."
    analysis = heuristic_analyze_document(text, "legal_interviews.docx")
    assert len(analysis.claims) >= 1
    claim = analysis.claims[0]

    # Save approved claim into Evidence Registry
    ev_id = save_evidence(
        profile,
        {
            "evidence_title": claim.claim_title,
            "evidence_description": f"{claim.claim_description} Excerpt: {claim.supporting_excerpt}",
            "evidence_type": claim.evidence_type,
            "related_dimension": claim.related_dimension,
            "source_name": claim.source_document,
            "verification_status": "Founder submitted",
            "founder_notes": "Extracted from uploaded interview summary.",
        },
        path=db_path,
        workspace_id=ws_id,
        user_id=uid,
    )

    # Verify record in DB under User & Workspace
    records = list_evidence(profile, path=db_path, workspace_id=ws_id, user_id=uid)
    assert len(records) == 1
    assert records[0]["evidence_id"] == ev_id
    assert records[0]["source_name"] == "legal_interviews.docx"

    # User B cannot see it
    uid_b = create_user("Other User", "other@founder.com", hash_password("Password123!"), path=db_path)
    ws_b = create_workspace(uid_b, "Other Workspace", path=db_path)
    b_records = list_evidence(profile, path=db_path, workspace_id=ws_b, user_id=uid_b)
    assert len(b_records) == 0

