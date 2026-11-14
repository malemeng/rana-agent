"""Password hashing and token primitives used by the HTTP API."""

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16
_JWT_ALGORITHM = "HS256"
MASKED_SECRET = "********"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    """Hash a password with scrypt and a fresh, cryptographically random salt."""
    if not password:
        raise ValueError("Password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            _b64encode(salt),
            _b64encode(digest),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password without revealing whether the stored value was malformed."""
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_b64decode(expected)),
        )
        return hmac.compare_digest(actual, _b64decode(expected))
    except (TypeError, ValueError):
        return False


def create_access_token(
    user_id: str,
    *,
    secret_key: str,
    expires_minutes: int = 30,
) -> str:
    """Create a signed, short-lived bearer token for a user."""
    if not secret_key:
        raise ValueError("A JWT secret key is required")
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str, *, secret_key: str) -> dict[str, Any]:
    """Decode an access token and enforce its signature, lifetime, and purpose."""
    if not secret_key:
        raise ValueError("A JWT secret key is required")
    payload = jwt.decode(
        token,
        secret_key,
        algorithms=[_JWT_ALGORITHM],
    )
    required_claims = {"sub", "type", "exp", "iat", "jti"}
    if not required_claims.issubset(payload):
        raise jwt.InvalidTokenError("Missing required access token claims")
    if (
        payload.get("type") != "access"
        or not isinstance(payload.get("sub"), str)
        or not isinstance(payload.get("jti"), str)
    ):
        raise jwt.InvalidTokenError("Invalid access token claims")
    return payload


def create_refresh_token() -> str:
    """Return an opaque refresh token; only its SHA-256 digest is persisted."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_sensitive_key(key: str) -> bool:
    """Identify configuration keys whose values must never be returned by the API."""
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    sensitive_names = {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "authorization",
        "private_key",
        "client_secret",
    }
    return normalized in sensitive_names or normalized.endswith(
        ("_password", "_secret", "_token", "_api_key", "_apikey", "_private_key")
    )


def mask_secret_data(value: Any, *, force: bool = False) -> Any:
    """Recursively redact sensitive dictionary values without leaking their shape."""
    if force:
        return MASKED_SECRET
    if isinstance(value, dict):
        return {
            key: mask_secret_data(item, force=is_sensitive_key(str(key)))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_secret_data(item) for item in value]
    return value


def restore_masked_data(incoming: Any, existing: Any) -> Any:
    """Restore secrets echoed back by a client while preserving intentional updates."""
    if incoming == MASKED_SECRET:
        return existing
    if isinstance(incoming, dict) and isinstance(existing, dict):
        return {
            key: restore_masked_data(value, existing.get(key)) for key, value in incoming.items()
        }
    if isinstance(incoming, list) and isinstance(existing, list):
        return [
            restore_masked_data(value, existing[index] if index < len(existing) else None)
            for index, value in enumerate(incoming)
        ]
    return incoming
