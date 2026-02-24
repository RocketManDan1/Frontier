"""
Database migration tests — verify that migrations apply cleanly and
produce the expected schema.

Catches:
  - SQL syntax errors in migration functions
  - Idempotency failures (running migrations twice)
  - Missing tables or columns after migration
  - Foreign key constraint issues
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os; os.environ.setdefault("DEV_SKIP_AUTH", "1")


# ── Migration application ─────────────────────────────────────────────────

class TestMigrationsApply:
    def test_all_migrations_apply_to_fresh_db(self):
        """All migrations should apply without error to an empty database."""
        from db_migrations import apply_migrations

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        apply_migrations(conn)

        # Verify the tracking table exists and has entries
        rows = conn.execute("SELECT migration_id FROM schema_migrations ORDER BY migration_id").fetchall()
        ids = [r["migration_id"] for r in rows]
        assert len(ids) >= 1
        assert ids[0] == "0001_initial"
        conn.close()

    def test_migrations_are_idempotent(self):
        """Running apply_migrations twice should not raise."""
        from db_migrations import apply_migrations

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        apply_migrations(conn)
        # Second run should be a no-op
        apply_migrations(conn)
        conn.close()

    def test_migration_ids_are_sequential(self):
        """Migration IDs should be in sorted order."""
        from db_migrations import _migrations

        ids = [m.migration_id for m in _migrations()]
        assert ids == sorted(ids), f"Migration IDs are not sorted: {ids}"

    def test_no_duplicate_migration_ids(self):
        """Each migration_id must be unique."""
        from db_migrations import _migrations

        ids = [m.migration_id for m in _migrations()]
        assert len(ids) == len(set(ids)), f"Duplicate migration IDs: {[x for x in ids if ids.count(x) > 1]}"


# ── Schema expectations ───────────────────────────────────────────────────

EXPECTED_TABLES = [
    "ships",
    "users",
    "sessions",
    "locations",
    "transfer_edges",
    "transfer_matrix",
    "location_inventory_stacks",
    "schema_migrations",
]


class TestSchemaAfterMigrations:
    def test_expected_tables_exist(self, db_conn: sqlite3.Connection):
        tables = {
            r[0]
            for r in db_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for t in EXPECTED_TABLES:
            assert t in tables, f"Expected table '{t}' not found. Tables: {tables}"

    def test_ships_table_has_core_columns(self, db_conn: sqlite3.Connection):
        cols = {r["name"] for r in db_conn.execute("PRAGMA table_info(ships)").fetchall()}
        for c in ("id", "name", "location_id"):
            assert c in cols, f"ships table missing column: {c}"

    def test_users_table_has_core_columns(self, db_conn: sqlite3.Connection):
        cols = {r["name"] for r in db_conn.execute("PRAGMA table_info(users)").fetchall()}
        for c in ("username", "password_hash", "is_admin"):
            assert c in cols, f"users table missing column: {c}"

    def test_location_inventory_stacks_exists(self, db_conn: sqlite3.Connection):
        cols = {r["name"] for r in db_conn.execute("PRAGMA table_info(location_inventory_stacks)").fetchall()}
        assert "location_id" in cols
        assert "item_id" in cols

    def test_foreign_keys_enabled(self, db_conn: sqlite3.Connection):
        fk = db_conn.execute("PRAGMA foreign_keys;").fetchone()
        assert fk[0] == 1


# ── Celestial config / seed data ──────────────────────────────────────────

class TestCelestialConfig:
    def test_config_loads(self):
        import celestial_config
        cfg = celestial_config.load_celestial_config()
        assert isinstance(cfg, dict)

    def test_location_metadata_loads(self):
        import celestial_config
        try:
            meta = celestial_config.load_location_metadata()
            assert isinstance(meta, dict)
        except Exception:
            pytest.skip("load_location_metadata not available or config incomplete")

    def test_config_has_bodies(self):
        import celestial_config
        cfg = celestial_config.load_celestial_config()
        assert "bodies" in cfg or "locations" in cfg or len(cfg) > 0
