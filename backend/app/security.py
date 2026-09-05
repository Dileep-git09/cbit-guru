"""Admin authentication — single-admin JWT, enough for a campus deployment.

There's exactly one admin account (from .env), not a user database — the
admin panel is an internal tool for CBIT staff, not a multi-tenant system.
JWT still buys us stateless auth: no session store, and the token itself
carries its own expiry.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"
bearer = HTTPBearer(auto_error=False)  # auto_error=False so we can raise our own 401 with a clear message


def verify_admin(email: str, password: str) -> bool:
    """Constant-time comparison so the endpoint is not a timing oracle.

    `==` on strings short-circuits at the first mismatched character, so its
    runtime leaks how many leading characters were correct — an attacker can
    recover the password one character at a time by timing responses.
    `secrets.compare_digest` always takes the same time regardless of where
    the strings differ. This is the concrete answer to "how did you secure
    the admin login?" in the viva.
    """
    return secrets.compare_digest(email.strip().lower(), settings.admin_email.lower()) and \
        secrets.compare_digest(password, settings.admin_password)


def create_token(subject: str) -> tuple[str, int]:
    expires_in = settings.jwt_expire_minutes * 60
    payload = {
        "sub": subject,
        "role": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), expires_in


async def require_admin(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    """FastAPI dependency — attach `Depends(require_admin)` to any route that
    should reject non-admin callers. Decodes and validates the JWT itself
    (signature + expiry), so there's no server-side session lookup at all.
    """
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=[ALGORITHM])
    except JWTError as exc:
        # Covers both an invalid signature (tampered/forged token) and an
        # expired one (jose checks `exp` automatically during decode).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not an admin")
    return str(payload.get("sub", ""))
