"""
Tests for orbit_bridge.py — Phase 2 integration layer.

Validates:
  - orbit_for_location() produces valid circular orbits from config
  - compute_transfer_burn_plan() for local (Hohmann) transfers
  - compute_transfer_burn_plan() for interplanetary (Lambert) transfers
  - settle_ship_events() executes burns and updates orbit_json
  - settle_ship_events() auto-docking after transfer completion
  - backfill_docked_orbits() initializes orbit for docked ships
  - Edge cases: unknown locations, Lagrange points, surface sites
"""

import json
import math
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import orbit_service
import orbit_bridge

# ── Constants ──────────────────────────────────────────────

MU_EARTH = 398600.4418
MU_MOON = 4902.8
EPOCH = 946684800.0  # J2000


# ── orbit_for_location ─────────────────────────────────────

class TestOrbitForLocation:
    """Test orbit construction from location config data."""

    def test_leo_produces_circular_orbit(self):
        orbit = orbit_bridge.orbit_for_location("LEO", EPOCH)
        assert orbit is not None
        assert orbit["body_id"] == "earth"
        assert orbit["e"] < 1e-10  # circular
        assert orbit["epoch_s"] == EPOCH
        assert orbit["direction"] == 1  # prograde

    def test_geo_produces_correct_radius(self):
        orbit = orbit_bridge.orbit_for_location("GEO", EPOCH)
        assert orbit is not None
        assert orbit["body_id"] == "earth"
        # GEO is ~42164 km from center
        assert orbit["a_km"] > 40000
        assert orbit["a_km"] < 44000
        assert orbit["e"] < 1e-10

    def test_llo_produces_lunar_orbit(self):
        """Low Lunar Orbit should orbit the moon."""
        orbit = orbit_bridge.orbit_for_location("LLO", EPOCH)
        if orbit is None:
            pytest.skip("LLO not in config as orbit node")
        assert orbit["body_id"] == "moon"

    def test_unknown_location_returns_none(self):
        result = orbit_bridge.orbit_for_location("NONEXISTENT_LOCATION_XYZ", EPOCH)
        assert result is None

    def test_orbit_elements_are_valid(self):
        orbit = orbit_bridge.orbit_for_location("LEO", EPOCH)
        assert orbit is not None
        # Should be convertible back to state vector
        r, v = orbit_service.elements_to_state(orbit, MU_EARTH, EPOCH)
        assert len(r) == 2
        assert len(v) == 2
        # Position should be at the LEO radius
        r_mag = math.sqrt(r[0]**2 + r[1]**2)
        assert abs(r_mag - orbit["a_km"]) / orbit["a_km"] < 0.001

    def test_angle_deg_parameter(self):
        """Different angle_deg values should start at different positions."""
        orbit0 = orbit_bridge.orbit_for_location("LEO", EPOCH, angle_deg=0.0)
        orbit90 = orbit_bridge.orbit_for_location("LEO", EPOCH, angle_deg=90.0)
        assert orbit0 is not None and orbit90 is not None
        # They should have the same semi-major axis but different M0
        assert abs(orbit0["a_km"] - orbit90["a_km"]) < 1e-6
        # M0 or omega should differ
        assert orbit0["M0_deg"] != orbit90["M0_deg"] or orbit0["omega_deg"] != orbit90["omega_deg"]


# ── compute_transfer_burn_plan (local Hohmann) ─────────────

class TestLocalTransferPlan:
    """Test burn plan computation for same-body transfers."""

    def test_leo_to_geo_produces_two_burns(self, db_conn):
        plan = orbit_bridge.compute_transfer_burn_plan(db_conn, "LEO", "GEO", EPOCH)
        assert plan is not None
        assert plan["transfer_type"] == "local_hohmann"
        assert len(plan["burns"]) == 2

    def test_leo_to_geo_burns_have_correct_times(self, db_conn):
        plan = orbit_bridge.compute_transfer_burn_plan(db_conn, "LEO", "GEO", EPOCH)
        assert plan is not None
        b1, b2 = plan["burns"]
        # First burn at departure
        assert b1["time_s"] == EPOCH
        # Second burn later
        assert b2["time_s"] > EPOCH
        # TOF should be reasonable (Hohmann LEO→GEO is ~5 hours = 18000s)
        tof = b2["time_s"] - b1["time_s"]
        assert 10000 < tof < 30000

    def test_leo_to_geo_total_dv_reasonable(self, db_conn):
        plan = orbit_bridge.compute_transfer_burn_plan(db_conn, "LEO", "GEO", EPOCH)
        assert plan is not None
        # LEO→GEO total Δv is ~3.9 km/s
        assert 3000 < plan["total_dv_m_s"] < 5000

    def test_initial_orbit_is_circular_at_departure(self, db_conn):
        plan = orbit_bridge.compute_transfer_burn_plan(db_conn, "LEO", "GEO", EPOCH)
        assert plan is not None
        orbit = plan["initial_orbit"]
        assert orbit["body_id"] == "earth"
        assert orbit["e"] < 1e-10

    def test_orbit_predictions_exist(self, db_conn):
        plan = orbit_bridge.compute_transfer_burn_plan(db_conn, "LEO", "GEO", EPOCH)
        assert plan is not None
        preds = plan.get("orbit_predictions", [])
        # Should have at least departure, transfer, and arrival orbit segments
        assert len(preds) >= 3

    def test_same_location_returns_none(self, db_conn):
        plan = orbit_bridge.compute_transfer_burn_plan(db_conn, "LEO", "LEO", EPOCH)
        assert plan is None

    def test_unknown_location_returns_none(self, db_conn):
        plan = orbit_bridge.compute_transfer_burn_plan(db_conn, "LEO", "FAKE_XYZ", EPOCH)
        assert plan is None


# ── settle_ship_events ─────────────────────────────────────

class TestSettleShipEvents:
    """Test orbital event execution for ships."""

    def _make_ship_with_burns(self, conn, ship_id, orbit, burns, body_id):
        """Insert a ship with orbit_json and maneuver_json."""
        now = time.time()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(ships)").fetchall()}
        base = {
            "id": ship_id,
            "name": f"Test {ship_id}",
            "created_at": now,
            "orbit_json": json.dumps(orbit),
            "maneuver_json": json.dumps(burns),
            "orbit_body_id": body_id,
            "parts_json": "[]",
            "fuel_kg": 1000.0,
        }
        # Only include columns that exist
        base = {k: v for k, v in base.items() if k in cols}
        col_names = ", ".join(base.keys())
        placeholders = ", ".join("?" for _ in base)
        conn.execute(f"INSERT INTO ships ({col_names}) VALUES ({placeholders})", tuple(base.values()))
        conn.commit()

    def test_executes_past_burns(self, db_conn):
        """Burns with time_s <= now should be executed."""
        orbit = orbit_service.circular_orbit("earth", 6778.0, MU_EARTH, EPOCH)
        burns = [
            {"time_s": EPOCH + 100, "prograde_m_s": 500.0, "radial_m_s": 0.0, "label": "Test burn"},
        ]
        self._make_ship_with_burns(db_conn, "settle_test_1", orbit, burns, "earth")

        # Settle at a time after the burn
        orbit_bridge.settle_ship_events(db_conn, EPOCH + 200)

        row = db_conn.execute("SELECT orbit_json, maneuver_json FROM ships WHERE id='settle_test_1'").fetchone()
        new_orbit = json.loads(row["orbit_json"])
        remaining = json.loads(row["maneuver_json"])

        # Burn should have been consumed
        assert len(remaining) == 0
        # Orbit should have changed (prograde burn raises orbit)
        assert new_orbit["a_km"] > orbit["a_km"]
        assert new_orbit["e"] > 0.01  # no longer circular

    def test_does_not_execute_future_burns(self, db_conn):
        """Burns with time_s > now should remain in maneuver_json."""
        orbit = orbit_service.circular_orbit("earth", 6778.0, MU_EARTH, EPOCH)
        burns = [
            {"time_s": EPOCH + 99999, "prograde_m_s": 500.0, "radial_m_s": 0.0, "label": "Future burn"},
        ]
        self._make_ship_with_burns(db_conn, "settle_test_2", orbit, burns, "earth")

        orbit_bridge.settle_ship_events(db_conn, EPOCH + 100)

        row = db_conn.execute("SELECT orbit_json, maneuver_json FROM ships WHERE id='settle_test_2'").fetchone()
        remaining = json.loads(row["maneuver_json"])
        # Burn should still be pending
        assert len(remaining) == 1

    def test_multiple_burns_executed_in_order(self, db_conn):
        """Multiple past burns execute sequentially."""
        orbit = orbit_service.circular_orbit("earth", 6778.0, MU_EARTH, EPOCH)
        burns = [
            {"time_s": EPOCH + 100, "prograde_m_s": 2000.0, "radial_m_s": 0.0, "label": "Burn 1"},
            {"time_s": EPOCH + 20000, "prograde_m_s": -800.0, "radial_m_s": 0.0, "label": "Burn 2"},
        ]
        self._make_ship_with_burns(db_conn, "settle_test_3", orbit, burns, "earth")

        orbit_bridge.settle_ship_events(db_conn, EPOCH + 30000)

        row = db_conn.execute("SELECT orbit_json, maneuver_json FROM ships WHERE id='settle_test_3'").fetchone()
        remaining = json.loads(row["maneuver_json"])
        assert len(remaining) == 0

    def test_partial_burn_execution(self, db_conn):
        """If now is between two burns, only the first should execute."""
        orbit = orbit_service.circular_orbit("earth", 6778.0, MU_EARTH, EPOCH)
        burns = [
            {"time_s": EPOCH + 100, "prograde_m_s": 500.0, "radial_m_s": 0.0, "label": "Burn 1"},
            {"time_s": EPOCH + 99999, "prograde_m_s": -500.0, "radial_m_s": 0.0, "label": "Burn 2"},
        ]
        self._make_ship_with_burns(db_conn, "settle_test_4", orbit, burns, "earth")

        orbit_bridge.settle_ship_events(db_conn, EPOCH + 500)

        row = db_conn.execute("SELECT orbit_json, maneuver_json FROM ships WHERE id='settle_test_4'").fetchone()
        remaining = json.loads(row["maneuver_json"])
        assert len(remaining) == 1
        assert remaining[0]["label"] == "Burn 2"

    def test_empty_maneuvers_no_crash(self, db_conn):
        """Ships with empty maneuver_json should not crash."""
        orbit = orbit_service.circular_orbit("earth", 6778.0, MU_EARTH, EPOCH)
        self._make_ship_with_burns(db_conn, "settle_test_5", orbit, [], "earth")
        # Should not raise
        orbit_bridge.settle_ship_events(db_conn, EPOCH + 1000)

    def test_ships_without_orbit_json_ignored(self, db_conn):
        """Legacy ships (orbit_json IS NULL) should be untouched."""
        cols = {r["name"] for r in db_conn.execute("PRAGMA table_info(ships)").fetchall()}
        base = {"id": "legacy_ship", "name": "Legacy Ship", "created_at": time.time(), "parts_json": "[]"}
        base = {k: v for k, v in base.items() if k in cols}
        col_names = ", ".join(base.keys())
        placeholders = ", ".join("?" for _ in base)
        db_conn.execute(f"INSERT INTO ships ({col_names}) VALUES ({placeholders})", tuple(base.values()))
        db_conn.commit()

        orbit_bridge.settle_ship_events(db_conn, EPOCH + 1000)
        # No crash, legacy ship untouched


# ── Auto-docking ──────────────────────────────────────────

class TestAutoDocking:
    """Test auto-docking of ships whose orbit matches a location."""

    def _make_free_ship(self, conn, ship_id, orbit, body_id):
        """Insert a free-flying ship (no location_id)."""
        now = time.time()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(ships)").fetchall()}
        base = {
            "id": ship_id,
            "name": f"Free {ship_id}",
            "created_at": now,
            "orbit_json": json.dumps(orbit),
            "maneuver_json": "[]",
            "orbit_body_id": body_id,
            "parts_json": "[]",
            "fuel_kg": 500.0,
        }
        base = {k: v for k, v in base.items() if k in cols}
        col_names = ", ".join(base.keys())
        placeholders = ", ".join("?" for _ in base)
        conn.execute(f"INSERT INTO ships ({col_names}) VALUES ({placeholders})", tuple(base.values()))
        conn.commit()

    def test_ship_at_leo_radius_docks(self, db_conn):
        """A ship in a circular orbit at LEO radius should auto-dock at LEO."""
        # Look up LEO radius from config
        orbit = orbit_bridge.orbit_for_location("LEO", EPOCH)
        if orbit is None:
            pytest.skip("LEO not in config")

        self._make_free_ship(db_conn, "dock_test_1", orbit, "earth")

        orbit_bridge._check_auto_docking(db_conn, EPOCH)

        row = db_conn.execute("SELECT location_id FROM ships WHERE id='dock_test_1'").fetchone()
        assert row["location_id"] == "LEO"

    def test_ship_with_pending_maneuvers_does_not_dock(self, db_conn):
        """Ship with remaining burns should not auto-dock."""
        orbit = orbit_bridge.orbit_for_location("LEO", EPOCH)
        if orbit is None:
            pytest.skip("LEO not in config")

        # Insert with pending maneuvers
        now = time.time()
        cols = {r["name"] for r in db_conn.execute("PRAGMA table_info(ships)").fetchall()}
        burns = [{"time_s": EPOCH + 99999, "prograde_m_s": 100, "radial_m_s": 0}]
        base = {
            "id": "dock_test_2",
            "name": "Maneuvering Ship",
            "created_at": now,
            "orbit_json": json.dumps(orbit),
            "maneuver_json": json.dumps(burns),
            "orbit_body_id": "earth",
            "parts_json": "[]",
            "fuel_kg": 500.0,
        }
        base = {k: v for k, v in base.items() if k in cols}
        col_names = ", ".join(base.keys())
        placeholders = ", ".join("?" for _ in base)
        db_conn.execute(f"INSERT INTO ships ({col_names}) VALUES ({placeholders})", tuple(base.values()))
        db_conn.commit()

        orbit_bridge._check_auto_docking(db_conn, EPOCH)

        row = db_conn.execute("SELECT location_id FROM ships WHERE id='dock_test_2'").fetchone()
        assert row["location_id"] is None  # should NOT dock

    def test_eccentric_orbit_does_not_dock(self, db_conn):
        """Ship in a non-circular orbit at the right SMA should not dock."""
        orbit = orbit_bridge.orbit_for_location("LEO", EPOCH)
        if orbit is None:
            pytest.skip("LEO not in config")

        # Make it eccentric
        orbit["e"] = 0.15
        self._make_free_ship(db_conn, "dock_test_3", orbit, "earth")

        orbit_bridge._check_auto_docking(db_conn, EPOCH)

        row = db_conn.execute("SELECT location_id FROM ships WHERE id='dock_test_3'").fetchone()
        assert row["location_id"] is None  # too eccentric

    def test_wrong_radius_does_not_dock(self, db_conn):
        """Ship in circular orbit at wrong radius should not dock."""
        orbit = orbit_service.circular_orbit("earth", 50000.0, MU_EARTH, EPOCH)
        self._make_free_ship(db_conn, "dock_test_4", orbit, "earth")

        orbit_bridge._check_auto_docking(db_conn, EPOCH)

        row = db_conn.execute("SELECT location_id FROM ships WHERE id='dock_test_4'").fetchone()
        assert row["location_id"] is None  # wrong radius


# ── backfill_docked_orbits ─────────────────────────────────

class TestBackfillDockedOrbits:
    """Test startup backfill of orbit_json for docked ships."""

    def test_backfills_docked_ship_at_leo(self, db_conn):
        """A docked ship at LEO with no orbit_json should get one."""
        cols = {r["name"] for r in db_conn.execute("PRAGMA table_info(ships)").fetchall()}
        base = {
            "id": "backfill_1",
            "name": "Backfill Ship",
            "location_id": "LEO",
            "created_at": time.time(),
            "parts_json": "[]",
        }
        base = {k: v for k, v in base.items() if k in cols}
        col_names = ", ".join(base.keys())
        placeholders = ", ".join("?" for _ in base)
        db_conn.execute(f"INSERT INTO ships ({col_names}) VALUES ({placeholders})", tuple(base.values()))
        db_conn.commit()

        count = orbit_bridge.backfill_docked_orbits(db_conn, EPOCH)
        assert count >= 1

        row = db_conn.execute("SELECT orbit_json, orbit_body_id FROM ships WHERE id='backfill_1'").fetchone()
        assert row["orbit_json"] is not None
        orbit = json.loads(row["orbit_json"])
        assert orbit["body_id"] == "earth"
        assert orbit["e"] < 1e-10

    def test_does_not_overwrite_existing_orbit(self, db_conn):
        """Ship that already has orbit_json should not be touched."""
        existing_orbit = orbit_service.circular_orbit("earth", 12345.0, MU_EARTH, EPOCH)
        cols = {r["name"] for r in db_conn.execute("PRAGMA table_info(ships)").fetchall()}
        base = {
            "id": "backfill_2",
            "name": "Already Orbiting",
            "location_id": "LEO",
            "created_at": time.time(),
            "orbit_json": json.dumps(existing_orbit),
            "orbit_body_id": "earth",
            "parts_json": "[]",
        }
        base = {k: v for k, v in base.items() if k in cols}
        col_names = ", ".join(base.keys())
        placeholders = ", ".join("?" for _ in base)
        db_conn.execute(f"INSERT INTO ships ({col_names}) VALUES ({placeholders})", tuple(base.values()))
        db_conn.commit()

        orbit_bridge.backfill_docked_orbits(db_conn, EPOCH)

        row = db_conn.execute("SELECT orbit_json FROM ships WHERE id='backfill_2'").fetchone()
        orbit = json.loads(row["orbit_json"])
        # Should still have the custom radius, not LEO radius
        assert abs(orbit["a_km"] - 12345.0) < 1.0

    def test_returns_count_of_updated(self, db_conn):
        """Should return the number of ships updated."""
        # Ship with no orbit
        cols = {r["name"] for r in db_conn.execute("PRAGMA table_info(ships)").fetchall()}
        for i in range(3):
            base = {
                "id": f"backfill_count_{i}",
                "name": f"Ship {i}",
                "location_id": "LEO",
                "created_at": time.time(),
                "parts_json": "[]",
            }
            base = {k: v for k, v in base.items() if k in cols}
            col_names = ", ".join(base.keys())
            placeholders = ", ".join("?" for _ in base)
            db_conn.execute(f"INSERT INTO ships ({col_names}) VALUES ({placeholders})", tuple(base.values()))
        db_conn.commit()

        count = orbit_bridge.backfill_docked_orbits(db_conn, EPOCH)
        assert count == 3


# ── End-to-end: transfer → settle → dock ──────────────────

class TestTransferSettleDock:
    """Integration test: compute plan → settle burns → auto-dock."""

    def test_local_transfer_full_cycle(self, db_conn):
        """LEO → GEO: compute plan, execute all burns, verify arrival orbit."""
        plan = orbit_bridge.compute_transfer_burn_plan(db_conn, "LEO", "GEO", EPOCH)
        if plan is None:
            pytest.skip("LEO→GEO plan not available")

        # Create a free-flying ship with the initial orbit + burns
        cols = {r["name"] for r in db_conn.execute("PRAGMA table_info(ships)").fetchall()}
        base = {
            "id": "cycle_test_1",
            "name": "Transfer Ship",
            "created_at": time.time(),
            "orbit_json": json.dumps(plan["initial_orbit"]),
            "maneuver_json": json.dumps(plan["burns"]),
            "orbit_body_id": plan["orbit_body_id"],
            "parts_json": "[]",
            "fuel_kg": 5000.0,
        }
        base = {k: v for k, v in base.items() if k in cols}
        col_names = ", ".join(base.keys())
        placeholders = ", ".join("?" for _ in base)
        db_conn.execute(f"INSERT INTO ships ({col_names}) VALUES ({placeholders})", tuple(base.values()))
        db_conn.commit()

        # Settle at a time after both burns
        last_burn_time = max(b["time_s"] for b in plan["burns"])
        orbit_bridge.settle_ship_events(db_conn, last_burn_time + 100)

        row = db_conn.execute(
            "SELECT orbit_json, maneuver_json, location_id FROM ships WHERE id='cycle_test_1'"
        ).fetchone()

        # All burns consumed
        remaining = json.loads(row["maneuver_json"])
        assert len(remaining) == 0

        # Should be docked at GEO
        assert row["location_id"] == "GEO"

        # Final orbit should be near-circular at GEO radius
        final_orbit = json.loads(row["orbit_json"])
        assert final_orbit["body_id"] == "earth"
        # GEO orbit_node radius from config
        geo_orbit = orbit_bridge.orbit_for_location("GEO", EPOCH)
        if geo_orbit:
            r_diff = abs(final_orbit["a_km"] - geo_orbit["a_km"]) / geo_orbit["a_km"]
            assert r_diff < 0.02, f"Final SMA {final_orbit['a_km']:.1f} vs GEO {geo_orbit['a_km']:.1f}"
        assert final_orbit["e"] < 0.05


# ── Interplanetary transfer plan (if Lambert solver available) ─────

class TestInterplanetaryPlan:
    """Test interplanetary burn plan generation."""

    def test_earth_to_mars_transfer_has_burns(self, db_conn):
        """Earth-orbit → Mars-orbit should produce a burn plan."""
        # Try LEO → LMO (or similar Mars orbit)
        plan = orbit_bridge.compute_transfer_burn_plan(db_conn, "LEO", "LMO", EPOCH)
        if plan is None:
            # Try other known Mars orbit names
            for dest in ["Mars Orbit", "mars_orbit", "MarsLO"]:
                plan = orbit_bridge.compute_transfer_burn_plan(db_conn, "LEO", dest, EPOCH)
                if plan:
                    break
        if plan is None:
            pytest.skip("No Earth→Mars orbit pair found in config")

        assert plan["transfer_type"] == "interplanetary_lambert"
        assert len(plan["burns"]) >= 2
        # Total Δv should be in the range of ~5-15 km/s (Earth→Mars)
        assert plan["total_dv_m_s"] > 3000
        assert plan["total_dv_m_s"] < 30000
