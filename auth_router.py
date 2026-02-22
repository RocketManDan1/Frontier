import os
import re
import sqlite3
import time
import uuid
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from auth_service import (
    SESSION_COOKIE_NAME,
    create_session,
    create_corp_session,
    hash_password,
    require_admin,
    require_login,
    valid_username,
)
from auth_repository import (
    account_exists,
    create_account,
    delete_account,
    find_user_for_login,
    list_accounts,
    set_password,
)
from db import connect_db
import org_service

router = APIRouter(tags=["auth"])

COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax")


class LoginReq(BaseModel):
    username: str
    password: str


class CorpLoginReq(BaseModel):
    corp_name: str
    password: str


class CorpRegisterReq(BaseModel):
    corp_name: str
    password: str
    color: str = "#ffffff"


class AccountCreateReq(BaseModel):
    username: str
    password: str


class AccountPasswordReq(BaseModel):
    password: str


@router.post("/api/auth/login")
def api_auth_login(req: LoginReq, response: Response) -> Dict[str, Any]:
    username = (req.username or "").strip().lower()
    password = str(req.password or "")
    if not valid_username(username):
        raise HTTPException(status_code=400, detail="Invalid username format")
    if not password:
        raise HTTPException(status_code=400, detail="password is required")

    conn = connect_db()
    try:
        row = find_user_for_login(conn, username)
        expected = hash_password(username, password)
        if not row or not hmac_compare(str(row["password_hash"]), expected):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        token = create_session(conn, username)
        conn.commit()

        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite=COOKIE_SAMESITE,
            path="/",
        )

        return {
            "ok": True,
            "user": {
                "username": row["username"],
                "is_admin": bool(row["is_admin"]),
            },
        }
    finally:
        conn.close()


@router.post("/api/auth/logout")
def api_auth_logout(request: Request, response: Response) -> Dict[str, Any]:
    token = (request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    conn = connect_db()
    try:
        if token:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            conn.execute("DELETE FROM corp_sessions WHERE token=?", (token,))
            conn.commit()
    finally:
        conn.close()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
def api_auth_me(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        user = require_login(conn, request)
        corp_id = user.get("corp_id") if hasattr(user, "get") else None
        if corp_id:
            return {
                "ok": True,
                "user": {
                    "corp_id": corp_id,
                    "corp_name": user.get("corp_name"),
                    "corp_color": user.get("corp_color"),
                    "is_admin": False,
                },
            }
        return {
            "ok": True,
            "user": {
                "username": user["username"],
                "is_admin": bool(user["is_admin"]),
            },
        }
    finally:
        conn.close()


# ── Corporation Auth ──────────────────────────────────────────────────────────

def _valid_corp_name(name: str) -> bool:
    """Corp names: 2-40 chars, letters/numbers/spaces/hyphens/underscores."""
    return bool(re.fullmatch(r"[A-Za-z0-9 _\-]{2,40}", (name or "").strip()))


@router.get("/api/auth/corps")
def api_list_corps() -> Dict[str, Any]:
    """List all corporation names (for the login dropdown)."""
    conn = connect_db()
    try:
        rows = conn.execute(
            "SELECT id, name, color FROM corporations ORDER BY name"
        ).fetchall()
        return {
            "corps": [
                {"id": str(r["id"]), "name": str(r["name"]), "color": str(r["color"])}
                for r in rows
            ]
        }
    finally:
        conn.close()


@router.get("/api/auth/online-corps")
def api_online_corps() -> Dict[str, Any]:
    """Return corporations that have sent a heartbeat within the last 90 seconds."""
    conn = connect_db()
    try:
        cutoff = time.time() - 90
        rows = conn.execute(
            """SELECT DISTINCT c.id, c.name, c.color
               FROM corp_sessions cs
               JOIN corporations c ON c.id = cs.corp_id
               WHERE cs.last_seen IS NOT NULL AND cs.last_seen > ?
               ORDER BY c.name""",
            (cutoff,),
        ).fetchall()
        return {
            "corps": [
                {"id": str(r["id"]), "name": str(r["name"]), "color": str(r["color"])}
                for r in rows
            ]
        }
    finally:
        conn.close()


@router.post("/api/auth/heartbeat")
def api_auth_heartbeat(request: Request) -> Dict[str, Any]:
    """Update last_seen timestamp for the current corp session."""
    token = (request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if not token:
        return {"ok": True}
    conn = connect_db()
    try:
        conn.execute(
            "UPDATE corp_sessions SET last_seen = ? WHERE token = ?",
            (time.time(), token),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@router.post("/api/auth/corp/register")
def api_corp_register(req: CorpRegisterReq, response: Response) -> Dict[str, Any]:
    """Create a new corporation and log in."""
    corp_name = (req.corp_name or "").strip()
    password = str(req.password or "")
    color = (req.color or "#ffffff").strip()

    if not _valid_corp_name(corp_name):
        raise HTTPException(status_code=400, detail="Corp name must be 2-40 chars (letters, numbers, spaces, hyphens)")
    if len(password) < 3:
        raise HTTPException(status_code=400, detail="Password must be at least 3 characters")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#ffffff"

    conn = connect_db()
    try:
        # Check if name already taken
        existing = conn.execute(
            "SELECT id FROM corporations WHERE name = ? COLLATE NOCASE",
            (corp_name,),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Corporation name already taken")

        corp_id = str(uuid.uuid4())
        pw_hash = hash_password(corp_name.lower(), password)
        now = time.time()

        # Create the corporation's organization (economy/research)
        org_id = org_service.create_org_for_corp(conn, corp_id, corp_name)

        conn.execute(
            """INSERT INTO corporations (id, name, password_hash, color, org_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (corp_id, corp_name, pw_hash, color, org_id, now),
        )

        token = create_corp_session(conn, corp_id)
        conn.commit()

        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite=COOKIE_SAMESITE,
            path="/",
        )

        return {
            "ok": True,
            "corp": {"id": corp_id, "name": corp_name, "color": color},
        }
    finally:
        conn.close()


@router.post("/api/auth/corp/login")
def api_corp_login(req: CorpLoginReq, response: Response) -> Dict[str, Any]:
    """Log in as a corporation."""
    corp_name = (req.corp_name or "").strip()
    password = str(req.password or "")

    if not corp_name:
        raise HTTPException(status_code=400, detail="Corporation name is required")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")

    conn = connect_db()
    try:
        row = conn.execute(
            "SELECT id, name, password_hash, color FROM corporations WHERE name = ? COLLATE NOCASE",
            (corp_name,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid corporation or password")

        expected = hash_password(str(row["name"]).lower(), password)
        if not hmac_compare(str(row["password_hash"]), expected):
            raise HTTPException(status_code=401, detail="Invalid corporation or password")

        # Clear old sessions for this corp
        conn.execute("DELETE FROM corp_sessions WHERE corp_id = ?", (str(row["id"]),))
        token = create_corp_session(conn, str(row["id"]))
        conn.commit()

        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite=COOKIE_SAMESITE,
            path="/",
        )

        return {
            "ok": True,
            "corp": {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "color": str(row["color"]),
            },
        }
    finally:
        conn.close()


@router.get("/api/admin/accounts")
def api_admin_accounts(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_admin(conn, request)
        rows = list_accounts(conn)
        return {
            "accounts": [
                {
                    "username": r["username"],
                    "is_admin": bool(r["is_admin"]),
                    "created_at": float(r["created_at"] or 0.0),
                }
                for r in rows
            ]
        }
    finally:
        conn.close()


@router.post("/api/admin/accounts")
def api_admin_create_account(req: AccountCreateReq, request: Request) -> Dict[str, Any]:
    username = (req.username or "").strip().lower()
    password = str(req.password or "")
    if not valid_username(username):
        raise HTTPException(status_code=400, detail="username must be 3-32 chars [a-z0-9_]")
    if len(password) < 3:
        raise HTTPException(status_code=400, detail="password must be at least 3 characters")
    if username == "admin":
        raise HTTPException(status_code=400, detail="admin account is reserved")

    conn = connect_db()
    try:
        require_admin(conn, request)
        if account_exists(conn, username):
            raise HTTPException(status_code=409, detail="Account already exists")

        create_account(conn, username, hash_password(username, password), time.time())
        conn.commit()
        return {"ok": True, "username": username}
    finally:
        conn.close()


@router.post("/api/admin/accounts/{username}/password")
def api_admin_change_password(username: str, req: AccountPasswordReq, request: Request) -> Dict[str, Any]:
    uname = (username or "").strip().lower()
    password = str(req.password or "")
    if not valid_username(uname):
        raise HTTPException(status_code=400, detail="Invalid username")
    if len(password) < 3:
        raise HTTPException(status_code=400, detail="password must be at least 3 characters")

    conn = connect_db()
    try:
        require_admin(conn, request)
        if not account_exists(conn, uname):
            raise HTTPException(status_code=404, detail="Account not found")

        set_password(conn, uname, hash_password(uname, password))
        conn.execute("DELETE FROM sessions WHERE username=?", (uname,))
        conn.commit()
        return {"ok": True, "username": uname}
    finally:
        conn.close()


@router.delete("/api/admin/accounts/{username}")
def api_admin_delete_account(username: str, request: Request) -> Dict[str, Any]:
    uname = (username or "").strip().lower()
    if not valid_username(uname):
        raise HTTPException(status_code=400, detail="Invalid username")
    if uname == "admin":
        raise HTTPException(status_code=400, detail="Cannot delete admin account")

    conn = connect_db()
    try:
        require_admin(conn, request)
        if not account_exists(conn, uname):
            raise HTTPException(status_code=404, detail="Account not found")

        delete_account(conn, uname)
        conn.commit()
        return {"ok": True, "username": uname}
    finally:
        conn.close()


def hmac_compare(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a, b)
