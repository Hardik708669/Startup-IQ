"""Validated offline backup and staging-restore utilities for Startup IQ.

The SQLite backup API provides a consistent database snapshot. Attachments and
generated files are copied from the same stopped-application maintenance
window; the CLI therefore requires an explicit ``--offline`` acknowledgement.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
from typing import Any

from database import DEFAULT_ATTACHMENT_DIR, DEFAULT_DB_PATH, DEFAULT_DOCUMENTS_DIR

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
DATA_DIR_NAME = "data"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"Unsafe backup path: {value!r}")
    windows = __import__("pathlib").PureWindowsPath(value)
    path = PurePosixPath(value)
    if (path.is_absolute() or windows.is_absolute() or windows.drive or windows.anchor
            or value.startswith(("/", "//", "\\")) or ".." in path.parts
            or "." in path.parts or any(part == "" for part in path.parts)):
        raise ValueError(f"Unsafe backup path: {value!r}")
    return path


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _assert_safe_entry(path: Path, root: Path, relative: PurePosixPath) -> Path:
    if _is_reparse_point(path):
        raise ValueError(f"Unsafe link or reparse point: {relative}")
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"Unsafe source path: {relative}")
    if not resolved.is_file():
        raise ValueError(f"Backup entry is not a regular file: {relative}")
    return resolved


def _ensure_inside(path: Path, root: Path, label: str) -> Path:
    if path.exists() and _is_reparse_point(path):
        raise ValueError(f"{label} cannot be a symlink or reparse point")
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"{label} must be inside {root_resolved}")
    return resolved


def _sqlite_checks(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise ValueError("SQLite foreign-key check failed.")
    finally:
        connection.close()


def _database_file_entries(db_path: Path, data_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        for row in connection.execute("SELECT attachment_path FROM evidence_records WHERE attachment_path IS NOT NULL AND attachment_path != ''"):
            rel = _safe_relative_path(str(row["attachment_path"]))
            entries.append({"path": str(PurePosixPath(DATA_DIR_NAME, "attachments", *rel.parts)), "kind": "attachment"})
        for row in connection.execute("SELECT file_path FROM generated_documents WHERE file_path IS NOT NULL AND file_path != ''"):
            rel = _safe_relative_path(str(row["file_path"]))
            if rel.parts[0] != "documents":
                raise ValueError("Generated document path is outside documents storage.")
            entries.append({"path": str(PurePosixPath(DATA_DIR_NAME, *rel.parts)), "kind": "generated_document"})
        return entries
    finally:
        connection.close()


def _collect_files(root: Path, archive_root: PurePosixPath) -> list[tuple[Path, PurePosixPath]]:
    if not root.exists() or not root.is_dir() or _is_reparse_point(root):
        raise ValueError(f"Storage root is missing or unsafe: {archive_root}")
    result = []
    def visit(directory: Path, prefix: PurePosixPath) -> None:
        for entry in sorted(os.scandir(directory), key=lambda item: item.name.casefold()):
            relative = _safe_relative_path(str(prefix / entry.name))
            entry_path = Path(entry.path)
            if _is_reparse_point(entry_path):
                raise ValueError(f"Unsafe link or reparse point: {relative}")
            if entry.is_dir(follow_symlinks=False):
                visit(entry_path, prefix / entry.name)
            elif entry.is_file(follow_symlinks=False):
                result.append((_assert_safe_entry(entry_path, root, relative), archive_root / relative))
            else:
                raise ValueError(f"Unsupported storage entry: {relative}")
    visit(root, PurePosixPath())
    return result


def _inventory(root: Path) -> list[tuple[Path, PurePosixPath]]:
    """Enumerate regular files without following links."""
    result: list[tuple[Path, PurePosixPath]] = []
    if not root.is_dir() or _is_reparse_point(root):
        raise ValueError("Backup root is missing or unsafe.")

    def visit(directory: Path, prefix: PurePosixPath) -> None:
        for entry in sorted(os.scandir(directory), key=lambda item: item.name.casefold()):
            relative = _safe_relative_path(str(prefix / entry.name))
            path = Path(entry.path)
            if _is_reparse_point(path):
                raise ValueError(f"Unsafe link or reparse point in backup: {relative}")
            if entry.is_dir(follow_symlinks=False):
                visit(path, relative)
            elif entry.is_file(follow_symlinks=False):
                result.append((path, relative))
            else:
                raise ValueError(f"Unsupported backup entry: {relative}")
    visit(root, PurePosixPath())
    return result


def _copied_storage_inventory(temp_dir: Path) -> set[str]:
    """Independently enumerate copied attachment/document files."""
    inventory: set[str] = set()
    for category in ("attachments", "documents"):
        for _, relative in _inventory(temp_dir / DATA_DIR_NAME / category):
            canonical = str(PurePosixPath(DATA_DIR_NAME, category, *relative.parts))
            if canonical.casefold() in {item.casefold() for item in inventory}:
                raise ValueError(f"Duplicate copied path: {canonical}")
            inventory.add(canonical)
    return inventory


def _source_signature(path: Path) -> tuple[int, int]:
    stat_result = path.stat()
    return stat_result.st_size, stat_result.st_mtime_ns


def _lexical_absolute(path: str | Path) -> Path:
    """Make an absolute normalized path without resolving symlinks."""
    return Path(os.path.abspath(os.fspath(path)))


def _prepare_restore_parent(target: str | Path) -> tuple[Path, Path]:
    """Validate and incrementally create a restore target's parent path."""
    requested = _lexical_absolute(target)
    if _is_reparse_point(requested):
        raise ValueError("Restore target already exists as a symlink or reparse point.")
    parent = requested.parent
    current = Path(parent.anchor)
    for component in parent.parts[1:] if parent.anchor else parent.parts:
        current = current / component
        if current.exists():
            if _is_reparse_point(current) or not current.is_dir():
                raise ValueError("Restore target parent contains an unsafe component.")
        else:
            try:
                current.mkdir()
            except FileExistsError:
                pass
            if _is_reparse_point(current) or not current.is_dir():
                raise ValueError("Restore target parent became unsafe.")
    if _is_reparse_point(parent) or not parent.is_dir():
        raise ValueError("Restore target parent is unsafe.")
    return requested, parent


def _finalize_no_clobber(staging: Path, requested: Path) -> None:
    """Rename a staging directory without replacement where the OS guarantees it."""
    if os.name != "nt":
        raise RuntimeError("Safe no-clobber directory finalization is unavailable on this platform.")
    if requested.exists():
        raise FileExistsError("Restore destination appeared before finalization.")
    # Windows os.rename fails if the destination exists; unlike os.replace it
    # does not intentionally replace a competing directory.
    os.rename(staging, requested)


def create_backup(
    destination: str | Path,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    attachment_dir: str | Path = DEFAULT_ATTACHMENT_DIR,
    documents_dir: str | Path = DEFAULT_DOCUMENTS_DIR,
    application_stopped: bool = False,
) -> Path:
    """Create and validate an atomic backup directory outside the source data directory."""
    if not application_stopped:
        raise ValueError("Backup requires application_stopped=True; stop Streamlit before backing up files.")
    db = Path(db_path).resolve()
    data_root = db.parent
    attachments = _ensure_inside(Path(attachment_dir), data_root, "Attachment directory")
    documents = _ensure_inside(Path(documents_dir), data_root, "Document directory")
    destination_root = Path(destination).resolve()
    if destination_root == data_root or data_root in destination_root.parents:
        raise ValueError("Backup destination must be outside the source data directory.")
    if not db.exists():
        raise FileNotFoundError(f"Database does not exist: {db}")
    _sqlite_checks(db)
    destination_root.mkdir(parents=True, exist_ok=True)
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    final_dir = destination_root / f"startup-iq-backup-{backup_id}"
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{final_dir.name}-", dir=destination_root))
    try:
        backup_db = temp_dir / DATA_DIR_NAME / db.name
        backup_db.parent.mkdir(parents=True)
        source = sqlite3.connect(db)
        target = sqlite3.connect(backup_db)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
        _sqlite_checks(backup_db)

        (temp_dir / DATA_DIR_NAME / "attachments").mkdir(parents=True, exist_ok=True)
        (temp_dir / DATA_DIR_NAME / "documents").mkdir(parents=True, exist_ok=True)
        storage_files = _collect_files(attachments, PurePosixPath("attachments"))
        storage_files += _collect_files(documents, PurePosixPath("documents"))
        expected_inventory = {str(archive_path) for _, archive_path in storage_files}
        if len(expected_inventory) != len(storage_files):
            raise ValueError("Duplicate source inventory path.")
        files = [(backup_db, PurePosixPath(DATA_DIR_NAME, db.name))]
        required = _database_file_entries(backup_db, data_root)
        paths = {str(PurePosixPath(DATA_DIR_NAME, *archive_path.parts)) for _, archive_path in storage_files}
        for required_entry in required:
            if required_entry["path"] not in paths:
                raise ValueError(f"Required stored file is missing: {required_entry['path']}")
        for source_path, storage_path in storage_files:
            source_signature = _source_signature(source_path)
            archive_path = PurePosixPath(DATA_DIR_NAME, *storage_path.parts)
            target_path = temp_dir / Path(*archive_path.parts)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            if _source_signature(source_path) != source_signature:
                raise ValueError(f"Source changed during backup: {storage_path}")
        copied_inventory = _copied_storage_inventory(temp_dir)
        expected_archive_inventory = {str(PurePosixPath(DATA_DIR_NAME, *path.split("/"))) for path in expected_inventory}
        if copied_inventory != expected_archive_inventory:
            raise ValueError("Copied storage inventory does not match source inventory.")
        files = [(backup_db, PurePosixPath(DATA_DIR_NAME, db.name))]
        files.extend((temp_dir / Path(*PurePosixPath(path).parts), PurePosixPath(path)) for path in copied_inventory)
        file_records: list[dict[str, Any]] = []
        for _, archive_path in files:
            target_path = temp_dir / Path(*archive_path.parts)
            file_records.append({
                "path": str(archive_path),
                "size": target_path.stat().st_size,
                "sha256": _sha256(target_path),
            })
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "backup_id": backup_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": str(PurePosixPath(DATA_DIR_NAME, db.name)),
            "files": file_records,
            "required_files": required,
        }
        (temp_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        validate_backup(temp_dir)
        temp_dir.replace(final_dir)
        return final_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def validate_backup(backup_path: str | Path) -> dict[str, Any]:
    """Validate manifest, safe paths, checksums, required files, and SQLite integrity."""
    raw_root = Path(backup_path)
    if raw_root.exists() and _is_reparse_point(raw_root):
        raise ValueError("Backup root cannot be a symlink or reparse point.")
    root = raw_root.resolve()
    manifest_path = root / MANIFEST_NAME
    if not root.is_dir() or _is_reparse_point(manifest_path) or not manifest_path.is_file():
        raise ValueError("Backup is incomplete or has no manifest.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Malformed backup manifest.") from error
    if not isinstance(manifest, dict) or type(manifest.get("manifest_version")) is not int or manifest.get("manifest_version") != MANIFEST_VERSION or not isinstance(manifest.get("backup_id"), str) or not manifest["backup_id"]:
        raise ValueError("Unsupported or invalid backup manifest.")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ValueError("Manifest files must be a list.")
    listed: set[str] = set()
    listed_casefold: set[str] = set()
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise ValueError("Invalid manifest file entry.")
        rel = _safe_relative_path(item["path"])
        canonical = str(rel)
        if canonical in listed or canonical.casefold() in listed_casefold:
            raise ValueError(f"Duplicate manifest path: {canonical}")
        if type(item["size"]) is not int or item["size"] < 0 or not isinstance(item["sha256"], str) or not SHA256_RE.fullmatch(item["sha256"]):
            raise ValueError(f"Invalid manifest metadata: {canonical}")
        listed.add(canonical)
        listed_casefold.add(canonical.casefold())
        file_path = _ensure_inside(root / Path(*rel.parts), root, "Manifest file")
        if _is_reparse_point(file_path) or not file_path.is_file() or file_path.stat().st_size != item["size"] or _sha256(file_path) != item["sha256"]:
            raise ValueError(f"Backup file failed validation: {rel}")
    actual = {str(relative) for path, relative in _inventory(root) if str(relative) != MANIFEST_NAME}
    if actual != listed:
        raise ValueError("Backup file inventory does not match the manifest.")
    required = manifest.get("required_files")
    if not isinstance(required, list):
        raise ValueError("Manifest required_files must be a list.")
    for item in required:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("kind"), str):
            raise ValueError("Invalid required file entry.")
        required_path = str(_safe_relative_path(item["path"]))
        if required_path not in listed:
            raise ValueError(f"Required backup file missing from manifest: {required_path}")
    if not isinstance(manifest.get("database"), str):
        raise ValueError("Manifest database path is invalid.")
    database_rel = _safe_relative_path(manifest["database"])
    if str(database_rel) not in listed:
        raise ValueError("Backup database is not listed in the manifest.")
    database_path = root / Path(*database_rel.parts)
    _sqlite_checks(database_path)
    return manifest


def restore_backup(backup_path: str | Path, target: str | Path) -> Path:
    """Restore a validated backup into a new, empty staging directory."""
    manifest = validate_backup(backup_path)
    requested, parent = _prepare_restore_parent(target)
    if requested.exists():
        raise ValueError("Restore target must not already exist; active data will not be overwritten.")
    staging = Path(tempfile.mkdtemp(prefix=f".{requested.name}.restore-", dir=parent))
    try:
        source_root = Path(backup_path).resolve()
        shutil.copy2(source_root / MANIFEST_NAME, staging / MANIFEST_NAME)
        for item in manifest["files"]:
            rel = _safe_relative_path(item["path"])
            source = _assert_safe_entry(source_root / Path(*rel.parts), source_root, rel)
            target_path = _ensure_inside(staging / Path(*rel.parts), staging, "Restore file")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target_path)
        validate_backup_from_root(staging)
        _finalize_no_clobber(staging, requested)
        return requested
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def validate_backup_from_root(root: Path) -> dict[str, Any]:
    return validate_backup(root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create, verify, and stage-restore Startup IQ backups.")
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--destination", required=True)
    backup.add_argument("--offline", action="store_true", help="Confirm Streamlit is stopped for file/database consistency.")
    verify = sub.add_parser("verify")
    verify.add_argument("--backup", required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--target", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            path = create_backup(args.destination, application_stopped=args.offline)
            print(f"Backup validated and finalized: {path}")
        elif args.command == "verify":
            manifest = validate_backup(args.backup)
            print(f"Backup verified: {manifest['backup_id']}")
        else:
            path = restore_backup(args.backup, args.target)
            print(f"Backup restored and validated in staging: {path}")
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
