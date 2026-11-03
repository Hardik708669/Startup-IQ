"""
Authentication and session management service for Startup IQ.
Handles user registration, password hashing (Argon2id), JWT session tokens,
and validation.
"""
from __future__ import annotations

import re
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
import jwt
import argon2

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


def create_session_token(user_id: int, email: str, expires_in_hours: int = SESSION_EXPIRATION_HOURS) -> str:
    """Create a signed JWT session token with expiration."""
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "email": email.strip().lower(),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=expires_in_hours)).timestamp()),
        "jti": str(uuid.uuid4())
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def verify_session_token(token: str) -> dict[str, Any] | None:
    """Verify and decode JWT session token. Returns payload dict or None if invalid/expired."""
    if not token or not isinstance(token, str):
        return None
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, Exception):
        return None

