"""Admin user store — SQLite-backed multi-admin authentication with roles.

Originally the admin panel had exactly one login, hardcoded in `.env`. That
doesn't scale to a real institution where several staff members need their
own accounts, and where you want *some* staff (a "super admin") able to grant
or revoke panel access without editing server config and restarting.

Why SQLite rather than adding user rows into Qdrant: this is a handful of
low-traffic, structured records (emails, password hashes, roles) — exactly
what a relational table is for. Qdrant is a vector index, not a general
database; shoehorning users into it would mean fake zero-vectors and manual
uniqueness checks for no benefit. SQLite needs no extra service to run and
ships in the Python standard library, matching this project's "no
infrastructure you don't need" approach.

Why a single persistent connection (not open/close per call, unlike the
per-request pattern you'd use with a client/server database): SQLite is an
embedded, file-backed engine — a long-lived connection is the normal way to
use it, and it's what makes `ADMIN_DB_FILE=:memory:` work for tests (a
freshly opened `:memory:` connection is a *new, empty* database every time;
only a connection kept open for the process lifetime persists data across
calls). This mirrors the singleton-client pattern already used for Gemini,
Cohere and Qdrant elsewhere in this codebase.
"""
from __future__ import annotations

import asyncio
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Literal

from passlib.context import CryptContext

from app.config import settings

Role = Literal["superadmin", "admin"]

# bcrypt via passlib: salted, slow-by-design hashing. Comparing a submitted
# password against this hash (CryptContext.verify) is itself timing-safe —
# this replaces the old `secrets.compare_digest` plaintext comparison with
# something that also survives a leaked database (hashes, not passwords, are
# ever stored).
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        if settings.admin_db_file in (":memory:", "memory"):
            path = ":memory:"
        else:
            settings.admin_db_path.parent.mkdir(parents=True, exist_ok=True)
            path = str(settings.admin_db_path)
        # check_same_thread=False: asyncio.to_thread runs each call on a
        # different worker thread from the default executor. Admin-user
        # operations are low-frequency (not the hot chat path), so a single
        # connection serving occasional calls from different threads is safe
        # here without adding a connection pool.
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def _init_sync() -> None:
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            id            TEXT PRIMARY KEY,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK (role IN ('superadmin', 'admin')),
            created_at    TEXT NOT NULL
        )
        """
    )
    conn.commit()

    # Seed exactly one superadmin from .env, but only if the table is empty —
    # a fresh install. Once any row exists, the database is authoritative and
    # .env's ADMIN_EMAIL/ADMIN_PASSWORD are never looked at again.
    total = conn.execute("SELECT COUNT(*) AS n FROM admin_users").fetchone()["n"]
    if total == 0:
        conn.execute(
            "INSERT INTO admin_users (id, email, password_hash, role, created_at) "
            "VALUES (?, ?, ?, 'superadmin', ?)",
            (
                uuid.uuid4().hex,
                settings.admin_email.strip().lower(),
                _pwd.hash(settings.admin_password),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


async def init() -> None:
    """Create the table (and seed the first superadmin) once at startup."""
    await asyncio.to_thread(_init_sync)


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "role": row["role"],
        "created_at": row["created_at"],
    }


def _get_by_email_sync(email: str) -> sqlite3.Row | None:
    conn = _get_conn()
    return conn.execute(
        "SELECT * FROM admin_users WHERE email = ?", (email.strip().lower(),)
    ).fetchone()


def _verify_sync(email: str, password: str) -> dict | None:
    row = _get_by_email_sync(email)
    if row is None or not _pwd.verify(password, row["password_hash"]):
        # Same "no match" outcome whether the email doesn't exist or the
        # password is wrong — the caller can't tell which, intentionally.
        return None
    return _row_to_dict(row)


async def verify_credentials(email: str, password: str) -> dict | None:
    """Returns {id, email, role, created_at} on success, None on failure."""
    return await asyncio.to_thread(_verify_sync, email, password)


def _list_sync() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, email, role, created_at FROM admin_users ORDER BY created_at"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


async def list_users() -> list[dict]:
    """Never includes password_hash — callers only ever see id/email/role."""
    return await asyncio.to_thread(_list_sync)


def _create_sync(email: str, password: str, role: Role) -> str:
    conn = _get_conn()
    user_id = uuid.uuid4().hex
    try:
        conn.execute(
            "INSERT INTO admin_users (id, email, password_hash, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                user_id,
                email.strip().lower(),
                _pwd.hash(password),
                role,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("That email is already registered") from exc
    return user_id


async def create_user(email: str, password: str, role: Role = "admin") -> str:
    return await asyncio.to_thread(_create_sync, email, password, role)


def _get_by_id_sync(user_id: str) -> sqlite3.Row | None:
    conn = _get_conn()
    return conn.execute("SELECT * FROM admin_users WHERE id = ?", (user_id,)).fetchone()


async def get_by_id(user_id: str) -> dict | None:
    row = await asyncio.to_thread(_get_by_id_sync, user_id)
    return _row_to_dict(row) if row else None


def _update_password_sync(user_id: str, new_password: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE admin_users SET password_hash = ? WHERE id = ?",
        (_pwd.hash(new_password), user_id),
    )
    conn.commit()


async def update_password(user_id: str, new_password: str) -> None:
    await asyncio.to_thread(_update_password_sync, user_id, new_password)


def _delete_sync(user_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM admin_users WHERE id = ?", (user_id,))
    conn.commit()


async def delete_user(user_id: str) -> None:
    await asyncio.to_thread(_delete_sync, user_id)


def _count_superadmins_sync() -> int:
    conn = _get_conn()
    return conn.execute(
        "SELECT COUNT(*) AS n FROM admin_users WHERE role = 'superadmin'"
    ).fetchone()["n"]


async def count_superadmins() -> int:
    """Used to block deleting the last remaining super admin — otherwise a
    single mistaken delete could lock everyone out of admin management."""
    return await asyncio.to_thread(_count_superadmins_sync)


def _ping_sync() -> bool:
    try:
        _get_conn().execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


async def ping() -> bool:
    """Cheap reachability check for the readiness probe (main.py's
    /api/health/ready) — a trivial query, not a real lookup, just enough to
    prove the SQLite file is actually openable and responsive."""
    return await asyncio.to_thread(_ping_sync)
