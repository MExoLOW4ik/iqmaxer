"""
auth.py — iQmaxer authentication helpers

Password hashing uses PBKDF2-HMAC-SHA256 from the standard library (no external
dependencies). Session tokens are random, opaque, and stored in the `sessions`
table; the frontend keeps the token in localStorage and sends it as a Bearer
header. A FastAPI dependency (`current_user`) resolves the token to a user.
"""

import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException

import database

_PBKDF2_ITERATIONS = 200_000
_ALGO = "pbkdf2_sha256"


# ── Password hashing ─────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Return a self-describing hash string: 'pbkdf2_sha256$iters$salt$hash'."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{_ALGO}${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of a password against a stored hash string."""
    try:
        algo, iters_s, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        iterations = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


# ── Tokens ───────────────────────────────────────────────────────────────────

def new_token() -> str:
    """Generate a random, URL-safe session token."""
    return secrets.token_urlsafe(32)


# ── FastAPI dependency ───────────────────────────────────────────────────────

def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


async def current_user(authorization: str = Header(None)) -> dict:
    """Resolve the Authorization header to a user, or raise 401."""
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = database.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user
