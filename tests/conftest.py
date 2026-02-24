"""
Shared pytest fixtures for Frontier: Sol 2000 tests.

Provides:
  - In-memory SQLite DB with migrations applied
  - FastAPI TestClient with auth bypassed
  - Catalog accessors
  - Helper functions for spawning ships, inventory, etc.
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Generator, Optional

import pytest

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so we can import app modules
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Force DEV_SKIP_AUTH so all endpoints act as admin by default.
os.environ.setdefault("DEV_SKIP_AUTH", "1")

# Use a writable temp directory for the test DB so the app startup succeeds.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="frontier_test_")
os.environ["DB_DIR"] = _TEST_DB_DIR


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield an in-memory SQLite connection with all migrations applied.

    The connection is rolled back / closed after the test, so tests are isolated.
    """
    from db_migrations import apply_migrations

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")

    # Apply the game migrations (same as startup)
    apply_migrations(conn)

    yield conn
    conn.close()


@pytest.fixture()
def seeded_db(db_conn: sqlite3.Connection) -> sqlite3.Connection:
    """db_conn with seed data loaded (celestial locations + transfer matrix)."""
    schema_sql = (PROJECT_ROOT / "db" / "schema.sql").read_text()
    seed_sql = (PROJECT_ROOT / "db" / "seed.sql").read_text()
    db_conn.executescript(schema_sql)
    db_conn.executescript(seed_sql)
    return db_conn


# ---------------------------------------------------------------------------
# FastAPI TestClient
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Return a Starlette TestClient wired to the FastAPI app.

    Auth is bypassed via DEV_SKIP_AUTH=1.
    """
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_client(client):
    """Alias for clarity — identical to `client` when DEV_SKIP_AUTH=1."""
    return client


# ---------------------------------------------------------------------------
# Catalog helpers (no DB needed)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def resource_catalog() -> Dict[str, Dict[str, Any]]:
    import catalog_service
    return catalog_service.load_resource_catalog()


@pytest.fixture(scope="session")
def storage_catalog() -> Dict[str, Dict[str, Any]]:
    import catalog_service
    return catalog_service.load_storage_catalog()


@pytest.fixture(scope="session")
def thruster_catalog() -> Dict[str, Dict[str, Any]]:
    import catalog_service
    return catalog_service.load_thruster_main_catalog()


@pytest.fixture(scope="session")
def reactor_catalog() -> Dict[str, Dict[str, Any]]:
    import catalog_service
    return catalog_service.load_reactor_catalog()


@pytest.fixture(scope="session")
def generator_catalog() -> Dict[str, Dict[str, Any]]:
    import catalog_service
    return catalog_service.load_generator_catalog()


@pytest.fixture(scope="session")
def radiator_catalog() -> Dict[str, Dict[str, Any]]:
    import catalog_service
    return catalog_service.load_radiator_catalog()


@pytest.fixture(scope="session")
def recipe_catalog() -> Dict[str, Dict[str, Any]]:
    import catalog_service
    return catalog_service.load_recipe_catalog()


@pytest.fixture(scope="session")
def all_catalogs(
    resource_catalog,
    storage_catalog,
    thruster_catalog,
    reactor_catalog,
    generator_catalog,
    radiator_catalog,
    recipe_catalog,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Aggregated dict of all loaded catalogs."""
    return {
        "resource": resource_catalog,
        "storage": storage_catalog,
        "thruster": thruster_catalog,
        "reactor": reactor_catalog,
        "generator": generator_catalog,
        "radiator": radiator_catalog,
        "recipe": recipe_catalog,
    }


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    """Stateless helper methods for common test-data operations."""

    @staticmethod
    def create_test_user(conn: sqlite3.Connection, username: str = "testuser", is_admin: bool = False) -> str:
        """Insert a user row and return a session token."""
        from auth_service import hash_password
        import secrets

        pw_hash = hash_password(username, "password123")
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, is_admin, created_at) VALUES (?,?,?,?)",
            (username, pw_hash, int(is_admin), time.time()),
        )
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO sessions (token, username, created_at) VALUES (?,?,?)",
            (token, username, time.time()),
        )
        conn.commit()
        return token

    @staticmethod
    def spawn_ship(
        conn: sqlite3.Connection,
        *,
        ship_id: str = "test_ship_1",
        name: str = "Test Ship",
        location_id: str = "LEO",
        owner: str = "testuser",
        dry_mass_kg: float = 5000.0,
        fuel_capacity_kg: float = 2000.0,
        fuel_kg: float = 2000.0,
        status: str = "docked",
    ) -> str:
        """Insert a minimal ship row. Returns the ship_id."""
        now = time.time()
        # Gracefully handle missing columns
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(ships)").fetchall()}

        base = {
            "id": ship_id,
            "name": name,
            "location_id": location_id,
            "created_at": now,
        }
        optional = {
            "owner": owner,
            "dry_mass_kg": dry_mass_kg,
            "fuel_capacity_kg": fuel_capacity_kg,
            "fuel_kg": fuel_kg,
            "parts_json": "[]",
            "color": "#ffffff",
            "size_px": 12,
            "status": status,
        }
        for k, v in optional.items():
            if k in cols:
                base[k] = v

        col_names = ", ".join(base.keys())
        placeholders = ", ".join("?" for _ in base)
        conn.execute(f"INSERT INTO ships ({col_names}) VALUES ({placeholders})", tuple(base.values()))
        conn.commit()
        return ship_id

    @staticmethod
    def add_location_inventory(
        conn: sqlite3.Connection,
        location_id: str,
        item_id: str,
        quantity: float = 1.0,
        item_type: str = "resource",
    ) -> None:
        """Add an item stack to location inventory."""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(location_inventory_stacks)").fetchall()}
        payload: Dict[str, Any] = {
            "location_id": location_id,
            "item_id": item_id,
            "item_type": item_type,
            "quantity": quantity,
        }
        if "payload_json" in cols:
            payload["payload_json"] = json.dumps({"id": item_id, "name": item_id, "type": item_type})
        col_names = ", ".join(payload.keys())
        placeholders = ", ".join("?" for _ in payload)
        conn.execute(
            f"INSERT INTO location_inventory_stacks ({col_names}) VALUES ({placeholders})",
            tuple(payload.values()),
        )
        conn.commit()


@pytest.fixture()
def helpers() -> TestHelpers:
    return TestHelpers()


# ---------------------------------------------------------------------------
# Simulation clock helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_sim_clock():
    """Ensure the simulation clock is reset between tests."""
    from sim_service import reset_simulation_clock
    reset_simulation_clock()
    yield
    reset_simulation_clock()
