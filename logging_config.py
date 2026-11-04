"""Central, rerun-safe logging configuration and safe event helpers."""
from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+|api[_-]?key\s*[:=]\s*|password(?:Hash)?\s*[:=]\s*)[^\s,;]+")


def sanitize(value: object) -> str:
    text = str(value)
    text = _SECRET.sub(lambda match: match.group(0).split(":")[0].split("=")[0] + "=[REDACTED]", text)
    return text[:500]


def configure_logging(*, level: str | None = None, log_file: str | Path | None = None) -> logging.Logger:
    logger = logging.getLogger("startup_iq")
    configured_level = getattr(logging, (level or os.getenv("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    logger.setLevel(configured_level)
    logger.propagate = False
    if not any(getattr(handler, "_startup_iq_handler", False) for handler in logger.handlers):
        console = logging.StreamHandler()
        console._startup_iq_handler = True
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(console)
    target = log_file or os.getenv("LOG_FILE")
    if target and not any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == str(Path(target).resolve()) for h in logger.handlers):
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=int(os.getenv("LOG_MAX_BYTES", "5242880")), backupCount=int(os.getenv("LOG_BACKUP_COUNT", "3")), encoding="utf-8")
        handler._startup_iq_handler = True
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return logger


def event(name: str, *, outcome: str, severity: str = "INFO", **fields: object) -> None:
    safe = {key: sanitize(value) for key, value in fields.items() if key in {"user_id", "workspace_id", "reason", "category", "correlation_id", "provider", "model", "latency_ms", "status"}}
    safe.update(event=name, outcome=outcome)
    configure_logging().log(getattr(logging, severity.upper(), logging.INFO), "security_or_operational_event %s", safe)
