"""Admin authentication — multi-user, role-based (admin / superadmin).

Two roles: `admin` can log in and use the ingestion/browse tools; only
`superadmin` can create, list, reset the password of, or delete other admin
accounts. This mirrors how a real institution would run it — an IT admin
(superadmin) provisions accounts for department staff (admin), who never
need to touch account management themselves.

Passwords are verified via `services/users.py`, which stores bcrypt hashes
(never plaintext) in a small SQLite table — see that module for why SQLite
and why a single connection.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TypedDict

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings
from app.services import users

ALGORITHM = "HS256"
bearer = HTTPBearer(auto_error=False)  # auto_error=False so we can raise our own 401 with a clear message


class CurrentAdmin(TypedDict):
    id: str
    email: str
    role: str


async def verify_admin(email: str, password: str) -> dict | None:
    """Returns the user record on success, None on failure.

    Same outcome (None) whether the email doesn't exist or the password is
    wrong — the caller can't tell which, so a login attempt can't be used to
    enumerate valid admin emails.
    """
    return await users.verify_credentials(email, password)


def create_token(user: dict) -> tuple[str, int]:
    expires_in = settings.jwt_expire_minutes * 60
    payload = {
        "sub": user["id"],
        "email": user["email"],
        "role": user["role"],
        "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), expires_in


async def require_admin(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentAdmin:
    """FastAPI dependency — attach `Depends(require_admin)` to any route that
    should reject non-admin callers. Decodes and validates the JWT itself
    (signature + expiry), so there's no server-side session lookup at all;
    the token's own claims (id/email/role) are trusted once the signature
    checks out.
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

    role = payload.get("role")
    if role not in ("admin", "superadmin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not an admin")
    return CurrentAdmin(id=str(payload.get("sub", "")), email=str(payload.get("email", "")), role=role)


async def require_superadmin(current: CurrentAdmin = Depends(require_admin)) -> CurrentAdmin:
    """Stacks on top of `require_admin` — any route depending on this first
    proves the caller is *some* admin, then additionally checks the role.
    Attach to every admin-account-management route (create/list/reset/delete)."""
    if current["role"] != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required"
        )
    return current
