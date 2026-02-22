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
        return {"username": "admin", "is_admin": 1, "created_at": 0.0,
                "corp_id": None, "corp_name": None, "corp_color": None}.get(key)
    def __contains__(self, key: str) -> bool:
        return key in ("username", "is_admin", "created_at", "corp_id", "corp_name", "corp_color")
    def get(self, key: str, default: Any = None) -> Any:
        return self[key] if key in self else default

_FAKE_ADMIN = _FakeAdminRow()


class _CorpRow:
    """Dict-like object for corporation sessions."""
    def __init__(self, corp_id: str, corp_name: str, corp_color: str):
        self._data = {
            "username": None,
            "is_admin": 0,
            "created_at": 0.0,
            "corp_id": corp_id,
            "corp_name": corp_name,
            "corp_color": corp_color,
        }
    def __getitem__(self, key: str) -> Any:
        return self._data[key]
    def __contains__(self, key: str) -> bool:
        return key in self._data
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


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


def create_corp_session(conn: sqlite3.Connection, corp_id: str) -> str:
    """Create a session token for a corporation login."""
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO corp_sessions (token,corp_id,created_at) VALUES (?,?,?)",
        (token, corp_id, time.time()),
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


def get_corp_by_session_token(conn: sqlite3.Connection, token: str) -> Optional[Any]:
    """Look up a corporation by session token. Returns a _CorpRow or None."""
    row = conn.execute(
        """
        SELECT c.id, c.name, c.color
        FROM corp_sessions cs
        JOIN corporations c ON c.id = cs.corp_id
        WHERE cs.token = ?
        """,
        (token,),
    ).fetchone()
    if not row:
        return None
    return _CorpRow(
        corp_id=str(row["id"]),
        corp_name=str(row["name"]),
        corp_color=str(row["color"]),
    )


def get_current_user(conn: sqlite3.Connection, request: Request) -> Optional[Any]:
    if DEV_SKIP_AUTH:
        return _FAKE_ADMIN
    token = (request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if not token:
        return None
    # Try admin/user session first
    user = get_user_by_session_token(conn, token)
    if user:
        return user
    # Try corporation session
    return get_corp_by_session_token(conn, token)


def require_login(conn: sqlite3.Connection, request: Request) -> Any:
    if DEV_SKIP_AUTH:
        return _FAKE_ADMIN
    user = get_current_user(conn, request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_corp(conn: sqlite3.Connection, request: Request) -> Any:
    """Require a corp session (not admin). Returns dict-like with corp_id, corp_name, corp_color."""
    user = require_login(conn, request)
    corp_id = user.get("corp_id") if hasattr(user, "get") else user["corp_id"] if "corp_id" in user else None
    if not corp_id:
        raise HTTPException(status_code=403, detail="Corporation login required")
    return user


def require_admin(conn: sqlite3.Connection, request: Request) -> Any:
    if DEV_SKIP_AUTH:
        return _FAKE_ADMIN
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
