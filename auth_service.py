import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from typing import Any, Optional

from fastapi import HTTPException, Request

SESSION_COOKIE_NAME = "session_token"
AUTH_PASSWORD_SALT = os.environ.get("AUTH_PASSWORD_SALT", "earthmoon_auth_salt_v1")
DEV_SKIP_AUTH = os.environ.get("DEV_SKIP_AUTH", "").strip().lower() in ("1", "true", "yes")


class _FakeAdminRow:
    """Dict-like object returned when auth is bypassed via DEV_SKIP_AUTH."""
    def __getitem__(self, key: str) -> Any:
        return {"username": "admin", "is_admin": 1, "created_at": 0.0}.get(key)
    def __contains__(self, key: str) -> bool:
        return key in ("username", "is_admin", "created_at")
    def get(self, key: str, default: Any = None) -> Any:
        return self[key] if key in self else default

_FAKE_ADMIN = _FakeAdminRow()


def hash_password(username: str, password: str) -> str:
    payload = f"{AUTH_PASSWORD_SALT}:{username.strip().lower()}:{password}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def valid_username(raw: Any) -> bool:
    username = str(raw or "").strip().lower()
    return bool(re.fullmatch(r"[a-z0-9_]{3,32}", username))


def create_session(conn: sqlite3.Connection, username: str) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token,username,created_at) VALUES (?,?,?)",
        (token, username, time.time()),
    )
    return token


def get_user_by_session_token(conn: sqlite3.Connection, token: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT u.username,u.is_admin,u.created_at
        FROM sessions s
        JOIN users u ON u.username=s.username
        WHERE s.token=?
        """,
        (token,),
    ).fetchone()


def get_current_user(conn: sqlite3.Connection, request: Request) -> Optional[sqlite3.Row]:
    if DEV_SKIP_AUTH:
        return _FAKE_ADMIN  # type: ignore[return-value]
    token = (request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if not token:
        return None
    return get_user_by_session_token(conn, token)


def require_login(conn: sqlite3.Connection, request: Request) -> sqlite3.Row:
    if DEV_SKIP_AUTH:
        return _FAKE_ADMIN  # type: ignore[return-value]
    user = get_current_user(conn, request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_admin(conn: sqlite3.Connection, request: Request) -> sqlite3.Row:
    if DEV_SKIP_AUTH:
        return _FAKE_ADMIN  # type: ignore[return-value]
    user = require_login(conn, request)
    if not int(user["is_admin"]):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def ensure_default_admin_account(conn: sqlite3.Connection, reset_password: bool = False) -> None:
    row = conn.execute("SELECT username FROM users WHERE username='admin'").fetchone()
    admin_hash = hash_password("admin", "admin")
    if not row:
        conn.execute(
            "INSERT INTO users (username,password_hash,is_admin,created_at) VALUES (?,?,1,?)",
            ("admin", admin_hash, time.time()),
        )
        return
    if reset_password:
        conn.execute(
            "UPDATE users SET password_hash=?, is_admin=1 WHERE username='admin'",
            (admin_hash,),
        )
