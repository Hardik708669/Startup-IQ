"""Centralized validation, quarantine, scanning, and promotion for uploads."""
from __future__ import annotations

import io
import hashlib
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import uuid
import zipfile

from database import DEFAULT_ATTACHMENT_DIR, DEFAULT_QUARANTINE_DIR, MAX_ATTACHMENT_BYTES, record_upload_security_event

logger = logging.getLogger(__name__)

MAX_ARCHIVE_ENTRIES = 1000
MAX_ARCHIVE_UNCOMPRESSED = 100 * 1024 * 1024
MAX_EXTRACTED_TEXT = 2 * 1024 * 1024


class UploadSecurityError(ValueError):
    pass


def content_fingerprint(data: bytes) -> str:
    """Return the identity of the uploaded bytes, independent of filename."""
    return hashlib.sha256(data).hexdigest()


def _unsafe_component(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attrs = os.lstat(path).st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attrs & getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _prepare_directory(path: str | Path) -> Path:
    """Create a directory lexically, rejecting unsafe existing components."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    components = absolute.parts[1:] if absolute.anchor else absolute.parts
    for component in components:
        current = current / component
        if current.exists() or current.is_symlink():
            if _unsafe_component(current) or not current.is_dir():
                raise UploadSecurityError("Upload storage contains an unsafe path component.")
        else:
            current.mkdir()
            if _unsafe_component(current) or not current.is_dir():
                raise UploadSecurityError("Upload storage path became unsafe.")
    return absolute


def upload_max_bytes() -> int:
    try:
        return max(1, int(os.getenv("UPLOAD_MAX_BYTES", str(MAX_ATTACHMENT_BYTES))))
    except (TypeError, ValueError):
        return MAX_ATTACHMENT_BYTES


def _safe_name(filename: str) -> str:
    name = Path(str(filename or "")).name
    if not name or name in {".", ".."} or name != str(filename).replace("\\", "/").split("/")[-1]:
        raise UploadSecurityError("Unsafe upload filename.")
    return name


def _validate_zip(data: bytes, required: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise UploadSecurityError("Archive contains too many entries.")
            total = 0
            for info in infos:
                path = PurePosixPath(info.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts or info.flag_bits & 0x1:
                    raise UploadSecurityError("Archive contains an unsafe entry.")
                total += info.file_size
                if total > MAX_ARCHIVE_UNCOMPRESSED or info.compress_size and info.file_size / info.compress_size > 1000:
                    raise UploadSecurityError("Archive exceeds resource limits.")
            if required not in archive.namelist():
                raise UploadSecurityError("Archive is not a supported Office document.")
    except zipfile.BadZipFile as error:
        raise UploadSecurityError("Malformed archive.") from error


def validate_upload(data: bytes, filename: str, *, max_bytes: int | None = None) -> str:
    name = _safe_name(filename)
    suffix = Path(name).suffix.lower()
    if len(data) == 0 or len(data) > (upload_max_bytes() if max_bytes is None else max_bytes):
        raise UploadSecurityError("Upload exceeds the configured size limit or is empty.")
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise UploadSecurityError("File content does not match its extension.")
    elif suffix == ".docx":
        _validate_zip(data, "word/document.xml")
    elif suffix == ".xlsx":
        _validate_zip(data, "xl/workbook.xml")
    elif suffix in {".txt", ".csv"}:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise UploadSecurityError("Text file encoding is unsupported.") from error
        if "\x00" in text or sum(ord(c) < 32 and c not in "\r\n\t" for c in text) > max(1, len(text) // 100):
            raise UploadSecurityError("Text file contains unexpected binary control data.")
    elif suffix == ".png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise UploadSecurityError("File content does not match its extension.")
    elif suffix in {".jpg", ".jpeg"}:
        if not data.startswith(b"\xff\xd8\xff"):
            raise UploadSecurityError("File content does not match its extension.")
    else:
        raise UploadSecurityError("Unsupported attachment type.")
    return suffix


def scan_quarantined_file(path: Path) -> str:
    mode = os.getenv("UPLOAD_SCAN_MODE", "disabled").strip().lower()
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    if app_env == "production" and mode != "required":
        raise UploadSecurityError("Production uploads require malware scanning.")
    if mode == "disabled":
        return "skipped"
    if mode != "required":
        raise UploadSecurityError("Unsupported upload scan mode.")
    try:
        result = subprocess.run(["clamscan", "--no-summary", str(path)], capture_output=True, text=True, timeout=30, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UploadSecurityError("Malware scanner is unavailable.") from error
    if result.returncode == 0:
        return "clean"
    if result.returncode == 1:
        raise UploadSecurityError("Upload was rejected by malware scanning.")
    raise UploadSecurityError("Malware scanner failed.")


def secure_store_attachment(filename: str, data: bytes, attachment_dir: str | Path = DEFAULT_ATTACHMENT_DIR, *, user_id: int | None = None, workspace_id: int | None = None, db_path: str | Path | None = None, quarantine_dir: str | Path | None = None) -> tuple[str, str, int, str]:
    """Validate, quarantine, scan, and atomically promote an upload."""
    suffix = validate_upload(data, filename)
    root = _prepare_directory(attachment_dir)
    quarantine_path = quarantine_dir if quarantine_dir is not None else (DEFAULT_QUARANTINE_DIR if Path(attachment_dir) == Path(DEFAULT_ATTACHMENT_DIR) else Path(attachment_dir).parent / "quarantine")
    quarantine = _prepare_directory(quarantine_path)
    internal = f"{uuid.uuid4().hex}{suffix}"
    quarantined = quarantine / internal
    approved = root / internal
    try:
        with quarantined.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            scan_status = scan_quarantined_file(quarantined)
        except UploadSecurityError as error:
            status = "infected" if "malware scanning" in str(error) else "error"
            if db_path is not None:
                record_upload_security_event(internal, suffix, len(data), status, reason_category=status, user_id=user_id, workspace_id=workspace_id, path=db_path)
            raise
        if db_path is not None:
            record_upload_security_event(internal, suffix, len(data), scan_status, reason_category=None, user_id=user_id, workspace_id=workspace_id, path=db_path)
        with quarantined.open("rb") as source, approved.open("xb") as destination:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        quarantined.unlink(missing_ok=True)
        return f"{internal}", _safe_name(filename), len(data), scan_status
    except Exception as original_error:
        try:
            if approved.exists():
                approved.unlink()
        except Exception as cleanup_error:
            logger.exception("Upload cleanup failed for upload %s: %s", internal, cleanup_error)
        # Suspicious quarantine files are deliberately retained for review.
        raise original_error


def remove_approved_attachment(relative_name: str, attachment_dir: str | Path = DEFAULT_ATTACHMENT_DIR) -> None:
    """Remove only a previously promoted internal attachment name."""
    name = Path(str(relative_name)).name
    if name != str(relative_name).replace("\\", "/").split("/")[-1] or not name:
        raise UploadSecurityError("Unsafe attachment cleanup path.")
    root = _prepare_directory(attachment_dir)
    target = root / name
    if _unsafe_component(target) or (target.exists() and not target.is_file()):
        raise UploadSecurityError("Unsafe attachment cleanup path.")
    target.unlink(missing_ok=True)


def validate_cached_attachment(receipt: dict, data: bytes, *, filename: str,
                               user_id: int, workspace_id: int,
                               attachment_dir: str | Path = DEFAULT_ATTACHMENT_DIR) -> bool:
    """Validate a session-cached upload receipt before it is reused."""
    if not isinstance(receipt, dict):
        return False
    if receipt.get("fingerprint") != content_fingerprint(data):
        return False
    if receipt.get("user_id") != user_id or receipt.get("workspace_id") != workspace_id:
        return False
    scan_mode = os.getenv("UPLOAD_SCAN_MODE", "disabled").strip().lower()
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    receipt_status = receipt.get("scan_status")
    if receipt_status not in {"clean", "skipped"}:
        return False
    if (app_env == "production" or scan_mode == "required") and receipt_status != "clean":
        return False
    try:
        validate_upload(data, filename)
        stored = str(receipt["attachment_path"])
        if Path(stored).name != stored or not stored:
            return False
        root = _prepare_directory(attachment_dir)
        target = root / stored
        if _unsafe_component(target) or not target.is_file():
            return False
        if target.stat().st_size != len(data):
            return False
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return digest == receipt["fingerprint"]
    except (OSError, KeyError, TypeError, UploadSecurityError):
        return False


def secure_register_evidence(profile, evidence: dict, filename: str, data: bytes, *, user_id: int, workspace_id: int, db_path, attachment_dir=DEFAULT_ATTACHMENT_DIR, existing_attachment: tuple[str, str, int] | dict | None = None) -> str:
    """Publish a validated attachment and register its evidence with cleanup."""
    from database import save_evidence, get_user_by_id, get_workspace
    if not get_user_by_id(user_id, path=db_path) or not (get_workspace(workspace_id, path=db_path) or {}).get("owner_user_id") == user_id:
        raise PermissionError("Upload registration ownership context is invalid.")
    if existing_attachment is None:
        stored, original, size, _ = secure_store_attachment(filename, data, attachment_dir, user_id=user_id, workspace_id=workspace_id, db_path=db_path)
    else:
        if isinstance(existing_attachment, dict):
            if not validate_cached_attachment(existing_attachment, data, filename=filename, user_id=user_id, workspace_id=workspace_id, attachment_dir=attachment_dir):
                raise UploadSecurityError("Cached upload receipt is no longer valid.")
            stored = existing_attachment["attachment_path"]
            original = existing_attachment["attachment_original_name"]
            size = existing_attachment["attachment_size"]
        else:
            stored, original, size = existing_attachment
            if size != len(data):
                raise UploadSecurityError("Cached upload receipt is no longer valid.")
            root = _prepare_directory(attachment_dir)
            target = root / str(stored)
            if _unsafe_component(target) or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != content_fingerprint(data):
                raise UploadSecurityError("Cached upload receipt is no longer valid.")
    payload = dict(evidence)
    payload.update({"attachment_path": stored, "attachment_original_name": original, "attachment_size": size})
    try:
        return save_evidence(profile, payload, path=db_path, workspace_id=workspace_id, user_id=user_id)
    except Exception as original_error:
        try:
            remove_approved_attachment(stored, attachment_dir)
        except Exception as cleanup_error:
            logger.exception("Approved attachment cleanup failed for upload %s: %s", stored, cleanup_error)
        raise original_error
