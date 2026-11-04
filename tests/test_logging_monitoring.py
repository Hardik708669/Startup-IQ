import logging

from database import cleanup_audit_events, initialize_database, record_audit_event
from diagnostics import run_diagnostics
from logging_config import configure_logging, sanitize


def test_logging_configuration_is_rerun_safe(tmp_path):
    configure_logging(level="WARNING", log_file=tmp_path / "startup.log")
    module_logger = logging.getLogger("app")
    count = len(logging.getLogger().handlers)
    configure_logging(level="WARNING", log_file=tmp_path / "startup.log")
    assert len(logging.getLogger().handlers) == count
    assert logging.getLogger().level == logging.WARNING


def test_module_logger_routes_to_file_and_redacts_secrets(tmp_path):
    path = tmp_path / "application.log"
    configure_logging(level="INFO", log_file=path)
    logging.getLogger("app").info("request password=secret API_KEY=hidden")
    text = path.read_text(encoding="utf-8")
    assert "request" in text
    assert "secret" not in text
    assert "hidden" not in text


def test_repeated_configuration_does_not_duplicate_module_entries(tmp_path):
    path = tmp_path / "duplicate.log"
    configure_logging(level="INFO", log_file=path)
    configure_logging(level="INFO", log_file=path)
    logging.getLogger("database").info("single-event")
    assert path.read_text(encoding="utf-8").count("single-event") == 1


def test_third_party_logger_is_not_captured(tmp_path):
    path = tmp_path / "filtered.log"
    configure_logging(level="INFO", log_file=path)
    logging.getLogger("urllib3").info("third-party-event")
    assert "third-party-event" not in path.read_text(encoding="utf-8")


def test_secret_sanitization():
    value = sanitize("password=secret API_KEY=abc Authorization: Bearer jwt-value")
    assert "secret" not in value
    assert "abc" not in value
    assert "jwt-value" not in value
    assert "REDACTED" in value


def test_audit_event_persistence_and_retention(tmp_path):
    db = tmp_path / "audit.db"
    initialize_database(db)
    record_audit_event("upload_rejected", outcome="failure", severity="WARNING", user_id=4, reason_category="invalid_format", path=db)
    with __import__("sqlite3").connect(db) as connection:
        assert connection.execute("SELECT event_type, severity, outcome FROM audit_events").fetchone() == ("upload_rejected", "WARNING", "failure")
    assert cleanup_audit_events(retention_days=90, path=db) == 0


def test_diagnostics_reports_sqlite_and_storage(tmp_path, monkeypatch):
    db = tmp_path / "diagnostic.db"
    monkeypatch.setattr("diagnostics.DEFAULT_ATTACHMENT_DIR", tmp_path / "attachments")
    monkeypatch.setattr("diagnostics.DEFAULT_DOCUMENTS_DIR", tmp_path / "documents")
    result = run_diagnostics(db)
    assert result["sqlite"] == "ok"
    assert result["attachments"] == "writable"
    assert result["documents"] == "writable"
    assert result["logging"] == "available"
