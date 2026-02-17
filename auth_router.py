import os
import sqlite3
import time
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from auth_service import (
    SESSION_COOKIE_NAME,
    create_session,
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

router = APIRouter(tags=["auth"])

COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax")


class LoginReq(BaseModel):
    username: str
    password: str


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
        return {
            "ok": True,
            "user": {
                "username": user["username"],
                "is_admin": bool(user["is_admin"]),
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
