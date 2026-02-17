import sqlite3
from typing import Optional


def find_user_for_login(conn: sqlite3.Connection, username: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT username,password_hash,is_admin FROM users WHERE username=?",
        (username,),
    ).fetchone()


def list_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT username,is_admin,created_at FROM users ORDER BY username"
    ).fetchall()


def account_exists(conn: sqlite3.Connection, username: str) -> bool:
    row = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
    return bool(row)


def create_account(conn: sqlite3.Connection, username: str, password_hash: str, created_at: float) -> None:
    conn.execute(
        "INSERT INTO users (username,password_hash,is_admin,created_at) VALUES (?,?,0,?)",
        (username, password_hash, created_at),
    )


def set_password(conn: sqlite3.Connection, username: str, password_hash: str) -> None:
    conn.execute(
        "UPDATE users SET password_hash=? WHERE username=?",
        (password_hash, username),
    )


def delete_account(conn: sqlite3.Connection, username: str) -> None:
    conn.execute("DELETE FROM users WHERE username=?", (username,))
