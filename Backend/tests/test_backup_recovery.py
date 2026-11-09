"""Isolated backup and staging-restore coverage."""

import json
import os
import sqlite3
from pathlib import Path
import subprocess

import pytest
import backup_recovery

from backup_recovery import create_backup, main, restore_backup, validate_backup
from database import (
    create_user, create_workspace, initialize_database, save_evidence,
    save_assessment, save_generated_document, store_attachment,
)
from recommendations import generate_recommendations
from scoring import DIMENSIONS, calculate_assessment


def _make_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junctions are only available on Windows")
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"Windows junction creation unavailable: {result.stderr or result.stdout}")


def _remove_junction(link: Path) -> None:
    if link.exists() or link.is_symlink():
        link.rmdir()


def test_custom_storage_roots_backup_and_staged_restore(tmp_path, monkeypatch):
    db = tmp_path / "database" / "startup.db"
    attachments = tmp_path / "separate-attachments"
    documents = tmp_path / "separate-documents"
    backups = tmp_path / "separate-backups"
    initialize_database(db)
    attachments.mkdir()
    documents.mkdir()
    (attachments / "proof.txt").write_text("attachment", encoding="utf-8")
    nested = documents / "workspace-7"
    nested.mkdir()
    (nested / "plan.docx").write_bytes(b"document")
    backup = create_backup(backups, db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    manifest = validate_backup(backup)
    paths = {item["path"] for item in manifest["files"]}
    assert "data/attachments/proof.txt" in paths
    assert "data/documents/workspace-7/plan.docx" in paths
    monkeypatch.setattr(backup_recovery, "_finalize_no_clobber", lambda staging, destination: staging.rename(destination))
    restored = restore_backup(backup, tmp_path / "staging" / "restored")
    assert (restored / "data/attachments/proof.txt").read_text(encoding="utf-8") == "attachment"
    assert (restored / "data/documents/workspace-7/plan.docx").read_bytes() == b"document"


def test_default_backup_directory_is_outside_default_data_root():
    from database import DEFAULT_BACKUP_DIR, DATA_ROOT
    assert DEFAULT_BACKUP_DIR != DATA_ROOT / "backups"
    assert DATA_ROOT not in DEFAULT_BACKUP_DIR.parents


def _fixture_data(tmp_path: Path):
    data = tmp_path / "data"
    db = data / "startup_iq.db"
    attachments = data / "attachments"
    documents = data / "documents"
    initialize_database(db)
    user = create_user("Backup User", "backup@example.test", "not-a-real-password-hash", db)
    workspace = create_workspace(user, "Backup Workspace", path=db)
    attachment, original, size = store_attachment("proof.txt", b"attachment-marker", attachments, db_path=db, user_id=user, workspace_id=workspace)
    profile = {"startup_name": "Backup Co", "industry": "Testing", "stage": "Idea"}
    evidence_id = save_evidence(
        profile,
        {
            "evidence_title": "Proof", "evidence_description": "Stored proof",
            "evidence_type": "Customer interviews", "related_dimension": "Market Potential",
            "source_name": "Test", "verification_status": "Founder submitted",
            "supporting_excerpt": "original excerpt", "attachment_path": attachment,
            "attachment_original_name": original, "attachment_size": size,
        }, db, workspace_id=workspace, user_id=user,
    )
    documents.mkdir(parents=True)
    files = {
        "business-plan.docx": b"DOCX backup marker",
        "pitch-deck.pptx": b"PPTX backup marker",
        "historical-report.html": b"<html>historical report marker</html>",
    }
    for name, content in files.items():
        (documents / name).write_bytes(content)
    inputs = {d.identifier: {"rating": 70, "reviewed": True, "evidence_status": "Estimate", "rationale": "Test"} for d in DIMENSIONS}
    result = calculate_assessment(inputs)
    assessment_id = save_assessment(profile, inputs, result, generate_recommendations(result), path=db, evidence_ids=[evidence_id], workspace_id=workspace, user_id=user)
    startup_id = 1
    save_generated_document(startup_id, assessment_id, "business_plan", "Plan", "documents/business-plan.docx", "docx", path=db, workspace_id=workspace, user_id=user)
    save_generated_document(startup_id, assessment_id, "pitch_deck", "Deck", "documents/pitch-deck.pptx", "pptx", path=db, workspace_id=workspace, user_id=user)
    return db, attachments, documents, evidence_id


def test_backup_manifest_and_restore_preserve_database_and_files(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    manifest = validate_backup(backup)
    assert manifest["files"]
    restored = restore_backup(backup, tmp_path / "restored")
    assert (restored / "data" / "attachments").iterdir()
    assert (restored / "data" / "documents" / "business-plan.docx").read_bytes() == b"DOCX backup marker"
    assert (restored / "data" / "documents" / "pitch-deck.pptx").read_bytes() == b"PPTX backup marker"
    assert (restored / "data" / "documents" / "historical-report.html").exists()
    assert (restored / "data" / "startup_iq.db").exists()


def test_workspace_scoped_generated_document_backup_restore_preserves_reference(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    nested = documents / "workspace-1" / "business-plan-neurosync-unique.docx"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"workspace-scoped document")
    document_id = save_generated_document(
        1, 1, "business_plan", "Scoped Plan",
        "documents/workspace-1/business-plan-neurosync-unique.docx", "docx",
        path=db, workspace_id=1, user_id=1, document_id="workspace-scoped-doc",
    )
    backup = create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    manifest = validate_backup(backup)
    assert "data/documents/workspace-1/business-plan-neurosync-unique.docx" in {item["path"] for item in manifest["files"]}
    restored = restore_backup(backup, tmp_path / "restored-scoped")
    restored_db = restored / "data" / "startup_iq.db"
    with sqlite3.connect(restored_db) as connection:
        row = connection.execute("SELECT file_path FROM generated_documents WHERE document_id = ?", (document_id,)).fetchone()
        assert row == ("documents/workspace-1/business-plan-neurosync-unique.docx",)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (restored / "data" / row[0]).read_bytes() == b"workspace-scoped document"


def test_backup_requires_offline_and_rejects_destination_inside_source(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    with pytest.raises(ValueError, match="application_stopped"):
        create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents)
    with pytest.raises(ValueError, match="outside"):
        create_backup(db.parent / "nested-backup", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)


def test_validation_detects_corruption_missing_files_and_traversal(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    target = backup / manifest["files"][1]["path"]
    target.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="failed validation"):
        validate_backup(backup)
    target.write_bytes(b"attachment-marker")
    manifest["files"][0]["path"] = "../escape.db"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unsafe"):
        validate_backup(backup)


def test_restore_rejects_nonempty_target_and_repeatable_operations(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    with pytest.raises(ValueError, match="must not already exist"):
        restore_backup(backup, target)
    second = create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    assert validate_backup(second)["backup_id"] != validate_backup(backup)["backup_id"]
    restore_backup(backup, tmp_path / "restored-one")
    restore_backup(second, tmp_path / "restored-two")


def test_validation_rejects_missing_manifest_and_malformed_manifest(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    manifest = backup / "manifest.json"
    manifest.unlink()
    with pytest.raises(ValueError, match="manifest"):
        validate_backup(backup)
    backup = create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    (backup / "manifest.json").write_text("not json")
    with pytest.raises(ValueError, match="Malformed"):
        validate_backup(backup)


def test_validation_rejects_missing_referenced_file_and_unexpected_file(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    manifest = json.loads((backup / "manifest.json").read_text())
    (backup / manifest["files"][1]["path"]).unlink()
    with pytest.raises(ValueError, match="failed validation"):
        validate_backup(backup)
    backup = create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    (backup / "data" / "unexpected.bin").write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="inventory"):
        validate_backup(backup)


def test_source_and_backup_symlinks_are_rejected_when_supported(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    try:
        (attachments / "escape.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this platform")
    with pytest.raises(ValueError, match="Unsafe"):
        create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)


def test_existing_symlink_restore_target_is_not_modified_when_supported(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    outside = tmp_path / "outside-target"
    outside.mkdir()
    target = tmp_path / "restore-link"
    try:
        target.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlink creation is unavailable on this platform")
    with pytest.raises(ValueError, match="already exist"):
        restore_backup(backup, target)
    assert not any(outside.iterdir())


def test_backup_compares_independent_copied_inventory_and_leaves_no_completed_backup(tmp_path, monkeypatch):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    original = backup_recovery._copied_storage_inventory
    monkeypatch.setattr(
        backup_recovery,
        "_copied_storage_inventory",
        lambda temp_dir: original(temp_dir) - {"data/documents/historical-report.html"},
    )
    destination = tmp_path / "backups"
    with pytest.raises(ValueError, match="inventory"):
        backup_recovery.create_backup(destination, db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    assert not list(destination.glob("startup-iq-backup-*"))


def test_backup_copy_failure_cleans_operation_temporary_directory(tmp_path, monkeypatch):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    destination = tmp_path / "backups"
    real_copy = backup_recovery.shutil.copy2

    def fail_on_document(source, target, *args, **kwargs):
        if str(source).endswith("business-plan.docx"):
            raise OSError("simulated copy failure")
        return real_copy(source, target, *args, **kwargs)

    monkeypatch.setattr(backup_recovery.shutil, "copy2", fail_on_document)
    with pytest.raises(OSError, match="simulated"):
        backup_recovery.create_backup(destination, db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    assert not list(destination.glob("startup-iq-backup-*"))
    assert (documents / "business-plan.docx").exists()


def test_restore_parent_symlink_is_rejected_before_child_creation(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = backup_recovery.create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "outside-parent"
    outside.mkdir()
    try:
        (trusted / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlink creation is unavailable on this platform")
    with pytest.raises(ValueError, match="unsafe"):
        backup_recovery.restore_backup(backup, trusted / "link" / "missing" / "restore")
    assert not (outside / "missing").exists()
    assert not list(trusted.glob("*.restore-*"))


def test_restore_competing_destination_is_not_replaced(tmp_path, monkeypatch):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = backup_recovery.create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    requested = tmp_path / "race" / "restore"
    competing = requested

    original_rename = os.rename

    def race(staging, destination):
        competing.parent.mkdir(parents=True, exist_ok=True)
        competing.mkdir()
        (competing / "owner.txt").write_text("competing")
        return original_rename(staging, destination)

    monkeypatch.setattr(backup_recovery.os, "rename", race)
    with pytest.raises(FileExistsError):
        backup_recovery.restore_backup(backup, requested)
    assert (competing / "owner.txt").read_text() == "competing"


def test_manifest_symlink_is_rejected_before_external_contents_are_parsed(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = backup_recovery.create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    external = tmp_path / "external-manifest.json"
    external.write_text("not valid json")
    manifest = backup / "manifest.json"
    manifest.unlink()
    try:
        manifest.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("Manifest symlink creation is unavailable on this platform")
    with pytest.raises(ValueError, match="incomplete|unsafe|manifest"):
        backup_recovery.validate_backup(backup)
    assert external.read_text() == "not valid json"
    manifest.unlink()


def test_cli_returns_error_code_for_platform_finalization_limit(tmp_path, monkeypatch, capsys):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = backup_recovery.create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    monkeypatch.setattr(backup_recovery, "_finalize_no_clobber", lambda staging, destination: (_ for _ in ()).throw(RuntimeError("platform limitation")))
    result = main(["restore", "--backup", str(backup), "--target", str(tmp_path / "restore")])
    captured = capsys.readouterr()
    assert result == 2
    assert "ERROR: platform limitation" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("manifest_version", True),
        ("files[0].size", True),
        ("files[0].sha256", "not-a-sha256"),
        ("files[0].path", "C:/outside.txt"),
        ("files[0].path", "//server/share/file.txt"),
        ("files[0].path", "data\\mixed.txt"),
    ],
)
def test_manifest_rejects_invalid_types_and_windows_paths(tmp_path, field, value):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = backup_recovery.create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    target = manifest
    parts = field.replace("]", "").replace("[", ".").split(".")
    for part in parts[:-1]:
        target = target[int(part)] if part.isdigit() else target[part]
    target[parts[-1]] = value
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        backup_recovery.validate_backup(backup)


def test_restore_finalization_failure_cleans_only_owned_staging(tmp_path, monkeypatch):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = backup_recovery.create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    requested = tmp_path / "nested" / "restore"
    monkeypatch.setattr(backup_recovery, "_finalize_no_clobber", lambda staging, destination: (_ for _ in ()).throw(OSError("finalize failure")))
    with pytest.raises(OSError, match="finalize failure"):
        backup_recovery.restore_backup(backup, requested)
    assert not requested.exists()
    assert not list(requested.parent.glob(f".{requested.name}.restore-*"))


def test_junction_in_attachments_is_rejected_without_reading_target(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    outside = tmp_path / "outside-attachments"
    outside.mkdir()
    (outside / "private.txt").write_text("must not be copied")
    junction = attachments / "external-directory"
    _make_junction(junction, outside)
    try:
        with pytest.raises(ValueError, match="Unsafe"):
            backup_recovery.create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
        assert (outside / "private.txt").read_text() == "must not be copied"
        assert not list((tmp_path / "backups").glob("startup-iq-backup-*"))
    finally:
        _remove_junction(junction)


def test_junction_in_documents_is_rejected_without_reading_target(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    outside = tmp_path / "outside-documents"
    outside.mkdir()
    (outside / "private.docx").write_bytes(b"must not be copied")
    junction = documents / "external-directory"
    _make_junction(junction, outside)
    try:
        with pytest.raises(ValueError, match="Unsafe"):
            backup_recovery.create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
        assert (outside / "private.docx").read_bytes() == b"must not be copied"
    finally:
        _remove_junction(junction)


def test_junction_restore_parent_is_rejected_before_external_child_creation(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = backup_recovery.create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    trusted = tmp_path / "trusted-junction"
    trusted.mkdir()
    outside = tmp_path / "external-restore-target"
    outside.mkdir()
    junction = trusted / "external"
    _make_junction(junction, outside)
    try:
        with pytest.raises(ValueError, match="unsafe"):
            backup_recovery.restore_backup(backup, junction / "missing" / "restored")
        assert not (outside / "missing").exists()
        assert not list(trusted.glob("*.restore-*"))
    finally:
        _remove_junction(junction)


def test_junction_inside_backup_is_rejected_by_verification(tmp_path):
    db, attachments, documents, _ = _fixture_data(tmp_path)
    backup = backup_recovery.create_backup(tmp_path / "backups", db_path=db, attachment_dir=attachments, documents_dir=documents, application_stopped=True)
    outside = tmp_path / "outside-backup"
    outside.mkdir()
    (outside / "untrusted.txt").write_text("untrusted")
    junction = backup / "data" / "attachments" / "external-directory"
    _make_junction(junction, outside)
    try:
        with pytest.raises(ValueError, match="Unsafe"):
            backup_recovery.validate_backup(backup)
        assert (outside / "untrusted.txt").read_text() == "untrusted"
    finally:
        _remove_junction(junction)
