"""Evidence registry and secure attachment tests using temporary storage."""

import pytest

from database import (
    EVIDENCE_TYPES, VERIFICATION_STATUSES, archive_evidence, ensure_startup,
    get_assessment, get_evidence, initialize_database, list_evidence, save_assessment,
    save_evidence, store_attachment, update_evidence, validate_attachment,
)
from recommendations import generate_recommendations
from scoring import DIMENSIONS, calculate_assessment


def profile(name="Evidence Test"):
    return {"startup_name": name, "industry": "Testing", "stage": "Idea"}


def evidence(**overrides):
    value = {
        "evidence_title": "Interview notes",
        "evidence_description": "Founder-submitted interview summary.",
        "evidence_type": "Customer interviews",
        "related_dimension": "Market Potential",
        "source_name": "Founder notes",
        "source_url": "https://example.test/source",
        "source_date": "2026-10-25",
        "verification_status": "Founder submitted",
        "founder_notes": "Requires independent follow-up.",
    }
    value.update(overrides)
    return value


def snapshot():
    inputs = {d.identifier: {"rating": 70, "reviewed": True, "evidence_status": "Estimate", "rationale": "Rationale"} for d in DIMENSIONS}
    result = calculate_assessment(inputs)
    return inputs, result, generate_recommendations(result)


def test_evidence_schema_is_repeatable_and_record_crud(tmp_path):
    path = tmp_path / "nested" / "startup.db"
    initialize_database(path)
    initialize_database(path)
    startup_id = ensure_startup(profile(), path)
    evidence_id = save_evidence(profile(), evidence(), path)
    saved = get_evidence(evidence_id, path)
    assert saved["startup_id"] == startup_id
    assert list_evidence(profile(), path=path)[0]["evidence_title"] == "Interview notes"
    assert list_evidence(startup_id=startup_id, dimension="Market Potential", path=path)
    assert list_evidence(startup_id=startup_id, search="interview", path=path)
    assert update_evidence(evidence_id, {"verification_status": "Reviewed by founder"}, path)
    assert get_evidence(evidence_id, path)["verification_status"] == "Reviewed by founder"
    assert archive_evidence(evidence_id, path)
    assert list_evidence(profile(), path=path) == []
    assert list_evidence(profile(), include_archived=True, path=path)[0]["archived_at"]


def test_evidence_validation_types_and_statuses(tmp_path):
    path = tmp_path / "startup.db"
    with pytest.raises(ValueError): save_evidence(profile(), evidence(evidence_title=""), path)
    with pytest.raises(ValueError): save_evidence(profile(), evidence(verification_status="Fake"), path)
    custom = save_evidence(profile(), evidence(evidence_type="Founder workshop"), path)
    assert get_evidence(custom, path)["evidence_type"] == "Founder workshop"
    assert EVIDENCE_TYPES and VERIFICATION_STATUSES


def test_attachment_validation_and_safe_storage(tmp_path):
    with pytest.raises(ValueError): validate_attachment("../../evil.exe", 1)
    with pytest.raises(ValueError): validate_attachment("large.txt", 11, max_bytes=10)
    stored, original, size = store_attachment("../../notes.txt", b"safe", tmp_path / "private")
    assert stored == f"{stored.split('.')[0]}.txt"
    assert original == "notes.txt"
    assert size == 4
    assert (tmp_path / "private" / stored).read_bytes() == b"safe"


def test_historical_assessment_preserves_evidence_snapshot(tmp_path):
    path = tmp_path / "startup.db"
    evidence_id = save_evidence(profile(), evidence(), path)
    inputs, result, recommendations = snapshot()
    assessment_id = save_assessment(profile(), inputs, result, recommendations, "2026-10-25", path, [evidence_id])
    update_evidence(evidence_id, {"evidence_title": "Changed later"}, path)
    saved = get_assessment(assessment_id, path)
    assert saved["evidence"][0]["evidence_title"] == "Interview notes"
    assert saved["result"].overall_score == result.overall_score


def test_evidence_filtering_and_searching(tmp_path):
    path = tmp_path / "startup.db"
    p = profile("Search Co")
    startup_id = ensure_startup(p, path)
    
    ev1 = evidence(evidence_title="Customer Interview 1", evidence_type="Customer interviews", related_dimension="Market Potential", verification_status="Founder submitted")
    ev2 = evidence(evidence_title="Competitor Pricing Grid", evidence_type="Competitor research", related_dimension="Competitive Position", verification_status="Independently verified")
    ev3 = evidence(evidence_title="Financial Model v1", evidence_type="Financial assumptions", related_dimension="Financial Readiness", verification_status="Needs follow-up")
    
    id1 = save_evidence(p, ev1, path)
    id2 = save_evidence(p, ev2, path)
    id3 = save_evidence(p, ev3, path)
    
    # Filter by dimension
    by_dim = list_evidence(p, dimension="Market Potential", path=path)
    assert len(by_dim) == 1
    assert by_dim[0]["evidence_id"] == id1
    
    # Filter by type
    by_type = list_evidence(p, evidence_type="Competitor research", path=path)
    assert len(by_type) == 1
    assert by_type[0]["evidence_id"] == id2
    
    # Filter by status
    by_status = list_evidence(p, verification_status="Needs follow-up", path=path)
    assert len(by_status) == 1
    assert by_status[0]["evidence_id"] == id3
    
    # Search
    search_res = list_evidence(p, search="Pricing", path=path)
    assert len(search_res) == 1
    assert search_res[0]["evidence_id"] == id2


def test_attachment_security_and_edge_cases(tmp_path):
    # Unsafe extension
    with pytest.raises(ValueError, match="Unsupported attachment type"):
        validate_attachment("script.sh", 100)
    with pytest.raises(ValueError, match="Unsupported attachment type"):
        validate_attachment("malicious.php", 100)
        
    # File size limit
    with pytest.raises(ValueError, match="Attachment exceeds"):
        validate_attachment("big.pdf", 11 * 1024 * 1024, max_bytes=10 * 1024 * 1024)
        
    # Valid types
    for ext in [".pdf", ".docx", ".xlsx", ".csv", ".txt", ".png", ".jpg", ".jpeg"]:
        assert validate_attachment(f"test{ext}", 500) == ext
        
    # Path traversal protection
    private_dir = tmp_path / "private_attachments"
    stored_name, orig_name, size = store_attachment("..\\..\\secret.pdf", b"%PDF-dummy", private_dir)
    assert orig_name == "secret.pdf"
    assert not stored_name.startswith("..")
    assert (private_dir / stored_name).exists()


def test_historical_report_with_evidence_snapshots(tmp_path):
    path = tmp_path / "startup.db"
    p = profile("Historical Report Co")
    ev = evidence(evidence_title="Alpha Pilot Data", evidence_type="Pilot results", verification_status="Independently verified")
    ev_id = save_evidence(p, ev, path)
    
    inputs, result, recommendations = snapshot()
    assessment_id = save_assessment(p, inputs, result, recommendations, "2026-10-30", path, [ev_id])
    
    # Update evidence later
    update_evidence(ev_id, {"evidence_title": "Mutated Pilot Data", "verification_status": "Needs follow-up"}, path)
    
    record = get_assessment(assessment_id, path)
    from report_generator import generate_report
    html_report = generate_report(record["profile"], record["result"], record["recommendations"], record["assessment_date"], record["evidence"])
    
    assert "Alpha Pilot Data" in html_report
    assert "Mutated Pilot Data" not in html_report
    assert "Independently verified" in html_report


