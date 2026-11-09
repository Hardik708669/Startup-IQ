import io
import os
import zipfile
import subprocess

import pytest

from upload_security import UploadSecurityError, secure_store_attachment, scan_quarantined_file, validate_upload, content_fingerprint, validate_cached_attachment
from backup_recovery import create_backup, validate_backup
from database import initialize_database


def _zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return output.getvalue()


def test_format_signatures_and_office_structure():
    assert validate_upload(b"%PDF-1.7\n", "proof.pdf") == ".pdf"
    assert validate_upload(_zip({"[Content_Types].xml": b"x", "word/document.xml": b"x"}), "proof.docx") == ".docx"
    assert validate_upload(_zip({"[Content_Types].xml": b"x", "xl/workbook.xml": b"x"}), "model.xlsx") == ".xlsx"
    with pytest.raises(UploadSecurityError):
        validate_upload(b"MZ" + b"x" * 20, "proof.pdf")
    with pytest.raises(UploadSecurityError):
        validate_upload(b"binary\x00data", "notes.txt")
    with pytest.raises(UploadSecurityError):
        validate_upload(_zip({"xl/workbook.xml": b"x"}), "proof.docx")


def test_quarantine_scan_and_uuid_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "disabled")
    stored, original, size, status = secure_store_attachment("Founder Notes.TXT", b"safe text", tmp_path / "attachments")
    assert original == "Founder Notes.TXT"
    assert status == "skipped"
    assert size == 9
    assert (tmp_path / "attachments" / stored).read_bytes() == b"safe text"
    assert not list((tmp_path / "quarantine").iterdir())


def test_required_scanner_fails_closed_and_infected_scan_rejects(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "required")
    with pytest.raises(UploadSecurityError, match="unavailable"):
        scan_quarantined_file(tmp_path / "missing.txt")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "disabled")
    with pytest.raises(UploadSecurityError, match="require malware"):
        scan_quarantined_file(tmp_path / "missing.txt")


@pytest.mark.parametrize("returncode, expected", [(0, "clean"), (1, "infected")])
def test_clamav_clean_and_infected_results(tmp_path, monkeypatch, returncode, expected):
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "required")
    monkeypatch.setattr("upload_security.subprocess.run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode, "", ""))
    if expected == "clean":
        assert scan_quarantined_file(tmp_path / "file.txt") == expected
    else:
        with pytest.raises(UploadSecurityError, match="malware"):
            scan_quarantined_file(tmp_path / "file.txt")


@pytest.mark.parametrize("side_effect", [OSError("not installed"), subprocess.TimeoutExpired("clamscan", 30)])
def test_clamav_unavailable_and_timeout_fail_closed(tmp_path, monkeypatch, side_effect):
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "required")
    monkeypatch.setattr("upload_security.subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(side_effect))
    with pytest.raises(UploadSecurityError, match="unavailable"):
        scan_quarantined_file(tmp_path / "file.txt")


def test_clamav_scanner_error_and_invalid_output_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "required")
    monkeypatch.setattr("upload_security.subprocess.run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 2, "unexpected", "error"))
    with pytest.raises(UploadSecurityError, match="failed"):
        scan_quarantined_file(tmp_path / "file.txt")


def test_infected_upload_is_not_promoted_and_outcome_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "required")
    monkeypatch.setattr("upload_security.subprocess.run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "FOUND", ""))
    db = tmp_path / "uploads.db"
    with pytest.raises(UploadSecurityError):
        secure_store_attachment("bad.txt", b"text", tmp_path / "attachments", db_path=db)
    assert not list((tmp_path / "attachments").glob("*"))
    import sqlite3
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT scan_status FROM upload_security_events").fetchone()[0] == "infected"


@pytest.mark.parametrize("unsafe_kind", ["attachment", "quarantine"])
def test_symlinked_storage_roots_are_rejected_without_touching_target(tmp_path, unsafe_kind):
    external = tmp_path / "external"
    external.mkdir()
    root = tmp_path / "attachments"
    quarantine = tmp_path / "quarantine"
    target = root if unsafe_kind == "attachment" else quarantine
    try:
        target.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlink creation unavailable")
    if unsafe_kind == "quarantine":
        root.mkdir()
    with pytest.raises(UploadSecurityError):
        secure_store_attachment("notes.txt", b"safe", root)
    assert not list(external.iterdir())


def test_legacy_store_attachment_uses_signature_validation(tmp_path):
    from database import store_attachment
    with pytest.raises(ValueError):
        store_attachment("fake.pdf", b"MZ executable", tmp_path / "attachments", db_path=tmp_path / "db.sqlite", user_id=1, workspace_id=1)


def test_legacy_store_attachment_requires_persistence_context(tmp_path):
    from database import store_attachment
    with pytest.raises(TypeError):
        store_attachment("notes.txt", b"safe", tmp_path / "attachments")


def test_legacy_store_attachment_persists_scan_outcome(tmp_path, monkeypatch):
    from database import store_attachment
    import sqlite3
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "disabled")
    db = tmp_path / "uploads.db"
    stored, _, _ = store_attachment("notes.txt", b"safe", tmp_path / "attachments", db_path=db, user_id=7, workspace_id=8)
    assert (tmp_path / "attachments" / stored).exists()
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT user_id, workspace_id, scan_status FROM upload_security_events").fetchone() == (7, 8, "skipped")


def test_backup_excludes_quarantine_and_includes_approved_attachment(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "disabled")
    data = tmp_path / "data"
    db = data / "startup_iq.db"
    attachments = data / "attachments"
    initialize_database(db)
    stored, _, _, _ = secure_store_attachment("approved.txt", b"approved", attachments, db_path=db)
    quarantine = data / "quarantine"
    quarantine.mkdir(exist_ok=True)
    (quarantine / "review-me.txt").write_bytes(b"unapproved")
    (data / "documents").mkdir()
    backup = create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=data / "documents", application_stopped=True)
    paths = {item["path"] for item in validate_backup(backup)["files"]}
    assert f"data/attachments/{stored}" in paths
    assert not any("quarantine" in path for path in paths)


def test_secure_registration_cleans_file_and_preserves_registration_error(tmp_path, monkeypatch):
    import database
    from upload_security import secure_register_evidence
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "disabled")
    monkeypatch.setattr(database, "get_user_by_id", lambda *args, **kwargs: {"user_id": 1})
    monkeypatch.setattr(database, "get_workspace", lambda *args, **kwargs: {"owner_user_id": 1})
    monkeypatch.setattr(database, "save_evidence", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("registration sentinel")))
    with pytest.raises(RuntimeError, match="registration sentinel"):
        secure_register_evidence({}, {"evidence_title": "x"}, "x.txt", b"content", user_id=1, workspace_id=1, db_path=tmp_path / "db.sqlite", attachment_dir=tmp_path / "attachments")
    assert not list((tmp_path / "attachments").glob("*"))


def test_secure_registration_cleanup_failure_is_logged_without_hiding_error(tmp_path, monkeypatch, caplog):
    import database
    import upload_security
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "disabled")
    monkeypatch.setattr(database, "get_user_by_id", lambda *args, **kwargs: {"user_id": 1})
    monkeypatch.setattr(database, "get_workspace", lambda *args, **kwargs: {"owner_user_id": 1})
    monkeypatch.setattr(database, "save_evidence", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("registration sentinel")))
    monkeypatch.setattr(upload_security, "remove_approved_attachment", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cleanup sentinel")))
    with caplog.at_level("ERROR", logger="upload_security"):
        with pytest.raises(RuntimeError, match="registration sentinel"):
            upload_security.secure_register_evidence({}, {"evidence_title": "x"}, "x.txt", b"content", user_id=1, workspace_id=1, db_path=tmp_path / "db.sqlite", attachment_dir=tmp_path / "attachments")
    assert "Approved attachment cleanup failed" in caplog.text


def test_secure_registration_attempts_cleanup_once(tmp_path, monkeypatch):
    import database
    import upload_security
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "disabled")
    monkeypatch.setattr(database, "get_user_by_id", lambda *args, **kwargs: {"user_id": 1})
    monkeypatch.setattr(database, "get_workspace", lambda *args, **kwargs: {"owner_user_id": 1})
    monkeypatch.setattr(database, "save_evidence", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("registration sentinel")))
    calls = []
    monkeypatch.setattr(upload_security, "remove_approved_attachment", lambda *args, **kwargs: calls.append(args[0]))
    with pytest.raises(RuntimeError, match="registration sentinel"):
        upload_security.secure_register_evidence({}, {"evidence_title": "x"}, "x.txt", b"content", user_id=1, workspace_id=1, db_path=tmp_path / "db.sqlite", attachment_dir=tmp_path / "attachments")
    assert len(calls) == 1


def test_clamav_invocation_uses_shell_false_and_timeout(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "required")
    def fake_run(*args, **kwargs):
        calls.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, "", "")
    monkeypatch.setattr("upload_security.subprocess.run", fake_run)
    assert scan_quarantined_file(tmp_path / "file.txt") == "clean"
    assert calls["shell"] is False
    assert calls["timeout"] == 30


def _receipt(tmp_path, data=b"same-size", user_id=1, workspace_id=2):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "disabled")
    stored, original, size, status = secure_store_attachment("notes.txt", data, tmp_path / "attachments")
    monkeypatch.undo()
    return {
        "attachment_path": stored,
        "attachment_original_name": original,
        "attachment_size": size,
        "scan_status": status,
        "fingerprint": content_fingerprint(data),
        "user_id": user_id,
        "workspace_id": workspace_id,
    }


def test_same_name_and_size_different_content_does_not_reuse_cached_upload(tmp_path):
    first = b"12345678"
    second = b"87654321"
    receipt = _receipt(tmp_path, first)
    assert not validate_cached_attachment(receipt, second, filename="notes.txt", user_id=1, workspace_id=2, attachment_dir=tmp_path / "attachments")


def test_modified_approved_file_invalidates_cached_receipt(tmp_path):
    data = b"unchanged"
    receipt = _receipt(tmp_path, data)
    (tmp_path / "attachments" / receipt["attachment_path"]).write_bytes(b"modified!")
    assert not validate_cached_attachment(receipt, data, filename="notes.txt", user_id=1, workspace_id=2, attachment_dir=tmp_path / "attachments")


def test_cached_upload_from_another_workspace_is_rejected(tmp_path):
    data = b"workspace scoped"
    receipt = _receipt(tmp_path, data, workspace_id=10)
    assert not validate_cached_attachment(receipt, data, filename="notes.txt", user_id=1, workspace_id=11, attachment_dir=tmp_path / "attachments")


def test_matching_content_fingerprint_reuses_unchanged_receipt(tmp_path):
    data = b"stable content"
    receipt = _receipt(tmp_path, data)
    assert validate_cached_attachment(receipt, data, filename="notes.txt", user_id=1, workspace_id=2, attachment_dir=tmp_path / "attachments")


def test_skipped_receipt_is_rejected_when_scanning_becomes_required(tmp_path, monkeypatch):
    data = b"policy transition"
    receipt = _receipt(tmp_path, data)
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "required")
    assert not validate_cached_attachment(receipt, data, filename="notes.txt", user_id=1, workspace_id=2, attachment_dir=tmp_path / "attachments")


def test_clean_receipt_remains_valid_when_scanning_is_required(tmp_path, monkeypatch):
    data = b"clean receipt"
    receipt = _receipt(tmp_path, data)
    receipt["scan_status"] = "clean"
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "required")
    assert validate_cached_attachment(receipt, data, filename="notes.txt", user_id=1, workspace_id=2, attachment_dir=tmp_path / "attachments")


def test_skipped_receipt_is_rejected_in_production(tmp_path, monkeypatch):
    data = b"production policy"
    receipt = _receipt(tmp_path, data)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("UPLOAD_SCAN_MODE", "disabled")
    assert not validate_cached_attachment(receipt, data, filename="notes.txt", user_id=1, workspace_id=2, attachment_dir=tmp_path / "attachments")


def test_evidence_registration_rejects_mismatched_upload_receipt(tmp_path, monkeypatch):
    import database
    from upload_security import secure_register_evidence
    data = b"current content"
    receipt = _receipt(tmp_path, b"old content")
    monkeypatch.setattr(database, "get_user_by_id", lambda *args, **kwargs: {"user_id": 1})
    monkeypatch.setattr(database, "get_workspace", lambda *args, **kwargs: {"owner_user_id": 1})
    with pytest.raises(UploadSecurityError, match="receipt"):
        secure_register_evidence({}, {"evidence_title": "x"}, "notes.txt", data, user_id=1, workspace_id=2,
                                 db_path=tmp_path / "db.sqlite", attachment_dir=tmp_path / "attachments", existing_attachment=receipt)
