import os
import sqlite3
from pathlib import Path
from typing import Generator

APP_DIR = Path(__file__).resolve().parent
DB_DIR = Path(os.environ.get("DB_DIR", str(APP_DIR / "data")))
DB_PATH = Path(os.environ.get("DB_PATH", str(DB_DIR / "game.db")))


def connect_db() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency that yields a DB connection and closes it after the request."""
    conn = connect_db()
    try:
        yield conn
    finally:
        conn.close()
