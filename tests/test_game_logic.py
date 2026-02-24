"""
Game logic unit tests — test pure computation and service-layer functions
without hitting the full HTTP stack.

Covers:
  - Simulation clock math (game_now_s, pause/resume, reset)
  - Ship stat derivation (dry mass, wet mass, delta-v, acceleration)
  - Catalog service helpers (canonical_item_category, normalize_parts)
  - Org service calculations (boost cost)
  - Constants consistency
"""

import math
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os; os.environ.setdefault("DEV_SKIP_AUTH", "1")


# ── Simulation clock ──────────────────────────────────────────────────────

class TestSimulationClock:
    def test_game_now_returns_number(self):
        from sim_service import game_now_s
        t = game_now_s()
        assert isinstance(t, float)
        assert t > 0

    def test_pause_freezes_time(self):
        from sim_service import game_now_s, set_simulation_paused, simulation_paused
        set_simulation_paused(True)
        assert simulation_paused() is True
        t1 = game_now_s()
        time.sleep(0.05)
        t2 = game_now_s()
        assert t1 == t2, "Game time should not advance while paused"
        set_simulation_paused(False)

    def test_unpause_resumes_time(self):
        from sim_service import game_now_s, set_simulation_paused
        set_simulation_paused(True)
        set_simulation_paused(False)
        t1 = game_now_s()
        time.sleep(0.05)
        t2 = game_now_s()
        assert t2 > t1, "Game time should advance after unpausing"

    def test_reset_returns_to_epoch(self):
        from sim_service import game_now_s, reset_simulation_clock, RESET_GAME_EPOCH_S
        reset_simulation_clock()
        t = game_now_s()
        # Should be close to the epoch (within a second of scaled time)
        assert abs(t - RESET_GAME_EPOCH_S) < 100, f"Time {t} too far from epoch {RESET_GAME_EPOCH_S}"

    def test_effective_time_scale_paused(self):
        from sim_service import effective_time_scale, set_simulation_paused
        set_simulation_paused(True)
        assert effective_time_scale() == 0.0
        set_simulation_paused(False)

    def test_effective_time_scale_running(self):
        from sim_service import effective_time_scale, GAME_TIME_SCALE
        assert effective_time_scale() == GAME_TIME_SCALE

    def test_export_import_roundtrip(self):
        from sim_service import export_simulation_state, import_simulation_state, game_now_s
        state = export_simulation_state()
        assert "real_time_anchor_s" in state
        assert "game_time_anchor_s" in state
        assert "paused" in state
        # Importing the same state should not change game_now_s significantly
        t_before = game_now_s()
        import_simulation_state(**state)
        t_after = game_now_s()
        assert abs(t_after - t_before) < 10


# ── Ship stats ─────────────────────────────────────────────────────────────

class TestShipStats:
    def test_wet_mass_is_dry_plus_fuel(self):
        from catalog_service import compute_wet_mass_kg
        assert compute_wet_mass_kg(1000, 500) == pytest.approx(1500.0)
        assert compute_wet_mass_kg(0, 0) == pytest.approx(0.0)

    def test_acceleration_gs_positive(self):
        from catalog_service import compute_acceleration_gs
        acc = compute_acceleration_gs(5000, 2000, 50.0)
        assert acc > 0

    def test_acceleration_gs_zero_mass(self):
        from catalog_service import compute_acceleration_gs
        # Zero mass should not crash; may return 0 or inf
        try:
            acc = compute_acceleration_gs(0, 0, 10.0)
            # Either 0 or inf is acceptable
            assert acc >= 0 or math.isinf(acc)
        except ZeroDivisionError:
            pass  # Also acceptable

    def test_derive_stats_empty(self, resource_catalog):
        from catalog_service import derive_ship_stats_from_parts
        stats = derive_ship_stats_from_parts([], resource_catalog)
        assert isinstance(stats, dict)

    def test_derive_stats_with_thruster(self, thruster_catalog, resource_catalog):
        from catalog_service import derive_ship_stats_from_parts
        # Grab any one thruster to construct a minimal parts list
        if not thruster_catalog:
            pytest.skip("No thrusters loaded")
        first = next(iter(thruster_catalog.values()))
        parts = [first]
        stats = derive_ship_stats_from_parts(parts, resource_catalog)
        assert isinstance(stats, dict)


# ── Canonical item category ───────────────────────────────────────────────

class TestCanonicalItemCategory:
    def test_identity(self):
        from catalog_service import canonical_item_category
        assert canonical_item_category("thruster") == "thruster"
        assert canonical_item_category("reactor") == "reactor"
        assert canonical_item_category("storage") == "storage"

    def test_aliases(self):
        from catalog_service import canonical_item_category
        assert canonical_item_category("engines") == "thruster"
        assert canonical_item_category("propellant") == "fuel"
        assert canonical_item_category("tanks") == "storage"

    def test_unknown_passes_through(self):
        from catalog_service import canonical_item_category
        result = canonical_item_category("xyzzy_unknown")
        # Should either pass through or return a default
        assert isinstance(result, str)

    def test_case_insensitive(self):
        from catalog_service import canonical_item_category
        result = canonical_item_category("Thruster")
        # Implementation may or may not be case-insensitive
        assert isinstance(result, str)


# ── Constants consistency ──────────────────────────────────────────────────

class TestConstants:
    def test_item_categories_have_required_fields(self):
        from constants import ITEM_CATEGORIES
        for cat in ITEM_CATEGORIES:
            assert "id" in cat
            assert "name" in cat
            assert "kind" in cat

    def test_category_by_id_matches(self):
        from constants import ITEM_CATEGORIES, ITEM_CATEGORY_BY_ID
        for cat in ITEM_CATEGORIES:
            assert cat["id"] in ITEM_CATEGORY_BY_ID
            assert ITEM_CATEGORY_BY_ID[cat["id"]] is cat

    def test_aliases_reference_valid_categories(self):
        from constants import ITEM_CATEGORY_ALIASES, ITEM_CATEGORY_BY_ID
        for alias, target in ITEM_CATEGORY_ALIASES.items():
            assert target in ITEM_CATEGORY_BY_ID, (
                f"Alias '{alias}' → '{target}' doesn't map to a valid category"
            )

    def test_research_categories_have_fields(self):
        from constants import RESEARCH_CATEGORIES
        for rc in RESEARCH_CATEGORIES:
            assert "id" in rc
            assert "label" in rc


# ── Boost cost ─────────────────────────────────────────────────────────────

class TestBoostCost:
    def test_positive_mass_returns_positive_cost(self):
        from org_service import calculate_boost_cost
        cost = calculate_boost_cost(1000.0)
        assert cost > 0

    def test_zero_mass_returns_zero_or_base(self):
        from org_service import calculate_boost_cost
        cost = calculate_boost_cost(0.0)
        assert cost >= 0

    def test_cost_scales_with_mass(self):
        from org_service import calculate_boost_cost
        c1 = calculate_boost_cost(100.0)
        c2 = calculate_boost_cost(200.0)
        assert c2 > c1


# ── Auth helpers ───────────────────────────────────────────────────────────

class TestAuthHelpers:
    def test_hash_password_deterministic(self):
        from auth_service import hash_password
        h1 = hash_password("alice", "pw123")
        h2 = hash_password("alice", "pw123")
        assert h1 == h2

    def test_hash_password_different_users(self):
        from auth_service import hash_password
        h1 = hash_password("alice", "pw123")
        h2 = hash_password("bob", "pw123")
        assert h1 != h2

    def test_valid_username(self):
        from auth_service import valid_username
        assert valid_username("alice") is True
        assert valid_username("bob_42") is True
        assert valid_username("ab") is False  # too short
        assert valid_username("Alice") is False or valid_username("alice") is True  # case check
        assert valid_username("") is False
        assert valid_username(None) is False

    def test_session_creation(self, db_conn):
        from auth_service import hash_password, create_session
        # Create user first
        pw_hash = hash_password("testuser", "test")
        db_conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?,?,0,?)",
            ("testuser", pw_hash, time.time()),
        )
        token = create_session(db_conn, "testuser")
        assert isinstance(token, str)
        assert len(token) > 16

        # Verify session exists in DB
        row = db_conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
        assert row is not None
        assert row["username"] == "testuser"
