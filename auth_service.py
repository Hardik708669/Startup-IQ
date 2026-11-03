"""
Authentication and session management service for Startup IQ.
Handles user registration, password hashing (Argon2id), JWT session tokens,
and validation.
"""
from __future__ import annotations

import re
import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
import jwt
import argon2
from database import DEFAULT_DB_PATH, create_auth_session, get_auth_session, revoke_auth_session, revoke_all_auth_sessions

logger = logging.getLogger(__name__)

# Argon2id password hasher instance
_hasher = argon2.PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    type=argon2.Type.ID
)

JWT_ALGORITHM = "HS256"
SESSION_EXPIRATION_HOURS = 24
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
SUPPORTED_APP_ENVIRONMENTS = {"development", "test", "production"}
DEVELOPMENT_FALLBACK_SECRET = "startup-iq-local-development-secret-key-32b-min"
PLACEHOLDER_SECRETS = {
    "your-32-char-min-secret-key-here",
    "your-32-char-min-cryptographic-secret-here",
    "change-me",
    "changeme",
    DEVELOPMENT_FALLBACK_SECRET,
}


def get_application_environment() -> str:
    """Return the normalized application environment or fail closed."""
    configured = os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development"
    environment = configured.strip().lower()
    if environment not in SUPPORTED_APP_ENVIRONMENTS:
        raise RuntimeError("Unsupported APP_ENV. Use one of: development, test, production.")
    return environment


def get_jwt_secret() -> str:
    """Return the JWT secret without exposing it in errors or logs."""
    environment = get_application_environment()
    configured_secret = os.getenv("AUTH_JWT_SECRET")

    if environment == "production":
        if not configured_secret or not configured_secret.strip():
            raise RuntimeError("CRITICAL SECURITY CONFIGURATION ERROR: AUTH_JWT_SECRET is required in production.")
        secret = configured_secret.strip()
        if secret.lower() in PLACEHOLDER_SECRETS:
            raise RuntimeError("CRITICAL SECURITY CONFIGURATION ERROR: AUTH_JWT_SECRET must not be a placeholder or development secret.")
        if len(secret) < 32:
            raise RuntimeError("CRITICAL SECURITY CONFIGURATION ERROR: AUTH_JWT_SECRET must be at least 32 characters in production.")
        return secret

    # Local development/test compatibility; retain SECRET_KEY as a legacy alias.
    return configured_secret.strip() if configured_secret and configured_secret.strip() else os.getenv("SECRET_KEY", "").strip() or DEVELOPMENT_FALLBACK_SECRET


def hash_password(password: str) -> str:
    """Hash password using Argon2id."""
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against stored Argon2id hash."""
    if not password or not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (argon2.exceptions.VerifyMismatchError, argon2.exceptions.VerificationError, Exception):
        return False


def validate_password_strength(password: str) -> list[str]:
    """Validate password strength rules."""
    errors = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters long.")
    if not any(c.isupper() for c in password):
        errors.append("Password must contain at least one uppercase letter.")
    if not any(c.islower() for c in password):
        errors.append("Password must contain at least one lowercase letter.")
    if not any(c.isdigit() for c in password):
        errors.append("Password must contain at least one number.")
    return errors


def validate_email(email: str) -> bool:
    """Check if string is a valid email."""
    if not email or not isinstance(email, str):
        return False
    email = email.strip()
    return bool(EMAIL_REGEX.match(email))


def normalize_login_identifier(identifier: str) -> str:
    return (identifier or "").strip().lower()


def get_login_rate_limit_config() -> dict[str, int]:
    def read(name: str, default: int, minimum: int) -> int:
        try:
            return max(minimum, int(os.getenv(name, str(default))))
        except (TypeError, ValueError):
            return default
    return {
        "max_attempts": read("AUTH_LOGIN_MAX_ATTEMPTS", 5, 1),
        "window_seconds": read("AUTH_LOGIN_WINDOW_SECONDS", 900, 60),
        "cooldown_seconds": read("AUTH_LOGIN_COOLDOWN_SECONDS", 900, 60),
    }


def security_event(event: str, *, outcome: str, user_id: int | None = None, jti: str | None = None, reason: str | None = None) -> None:
    """Emit a structured, non-sensitive security event."""
    fields = {"event": event, "outcome": outcome}
    if user_id is not None:
        fields["user_id"] = user_id
    if jti:
        fields["jti"] = jti
    if reason:
        fields["reason"] = str(reason)[:80]
    logger.info("security_event %s", fields, extra={"security_event": fields})


def create_session_token(user_id: int, email: str, expires_in_hours: int = SESSION_EXPIRATION_HOURS, *, path: str | Path = DEFAULT_DB_PATH) -> str:
    """Create a signed JWT session token with expiration."""
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "email": email.strip().lower(),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=expires_in_hours)).timestamp()),
        "jti": str(uuid.uuid4())
    }
    token = jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)
    create_auth_session(payload["jti"], user_id, datetime.fromtimestamp(payload["iat"], timezone.utc).isoformat(), datetime.fromtimestamp(payload["exp"], timezone.utc).isoformat(), path)
    security_event("login_success", outcome="success", user_id=user_id, jti=payload["jti"])
    return token


def verify_session_token(token: str, *, path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    """Verify and decode JWT session token. Returns payload dict or None if invalid/expired."""
    if not token or not isinstance(token, str):
        return None
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        jti = payload.get("jti")
        if not isinstance(jti, str):
            security_event("token_verification_failure", outcome="failure", reason="missing_session_id")
            return None
        session = get_auth_session(jti, path)
        if (not session or session.get("jti") != jti or session.get("revoked_at")
                or int(session.get("user_id", -1)) != int(payload.get("user_id", -2))):
            security_event("token_verification_failure", outcome="failure", jti=jti, reason="unknown_or_revoked_session")
            return None
        stored_exp = datetime.fromisoformat(str(session["expires_at"])).timestamp()
        if abs(stored_exp - int(payload["exp"])) > 1 or stored_exp <= datetime.now(timezone.utc).timestamp():
            security_event("token_verification_failure", outcome="failure", jti=jti, reason="session_expired_or_mismatched")
            return None
        security_event("token_verified", outcome="success", user_id=payload.get("user_id"), jti=jti)
        return payload
    except jwt.ExpiredSignatureError:
        security_event("session_expired", outcome="failure", reason="expired_token")
        return None
    except (jwt.InvalidTokenError, Exception):
        security_event("token_verification_failure", outcome="failure", reason="invalid_token")
        return None


def revoke_session_token(token: str, *, path: str | Path = DEFAULT_DB_PATH) -> bool:
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM], options={"verify_exp": False})
        jti = payload.get("jti")
        user_id = payload.get("user_id")
        if not isinstance(jti, str) or not jti or not isinstance(user_id, int):
            security_event("token_revocation", outcome="failure", reason="malformed_session")
            return False
        session = get_auth_session(jti, path)
        if not session or int(session.get("user_id", -1)) != user_id:
            security_event("token_revocation", outcome="failure", jti=jti, reason="unknown_session")
            return False
        if revoke_auth_session(jti, user_id, path):
            security_event("token_revocation", outcome="success", user_id=user_id, jti=jti)
            return True
        security_event("token_revocation", outcome="failure", user_id=user_id, jti=jti, reason="already_revoked")
        return False
    except jwt.InvalidTokenError:
        security_event("token_revocation", outcome="failure", reason="invalid_token")
        return False
    except (ValueError, TypeError, KeyError):
        security_event("token_revocation", outcome="failure", reason="malformed_session")
        return False
    except Exception as error:
        logger.error("Session revocation unavailable: %s", type(error).__name__)
        security_event("token_revocation", outcome="failure", reason="storage_unavailable")
        raise RuntimeError("Session revocation is temporarily unavailable.") from error


def revoke_all_sessions(user_id: int, *, path: str | Path = DEFAULT_DB_PATH) -> None:
    revoke_all_auth_sessions(user_id, path)
    security_event("token_revocation_all", outcome="success", user_id=user_id)

