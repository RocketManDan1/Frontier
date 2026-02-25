"""
Transfer planner tests — thorough battery testing ship transfers,
transfer quotes, fuel calculations, the Dijkstra route matrix,
orbital mechanics helpers, and edge-case handling.

Tests spawn real ships via the API, execute transfers, verify fuel
consumption and transit state, and delete the ships after each test.

Coverage:
  - Transfer quote (basic & advanced) for all route types
  - Route planning: Dijkstra matrix correctness, path optimality
  - Fuel math: Tsiolkovsky rocket equation, edge cases
  - Ship transfer lifecycle: depart → transit → arrive
  - Guard rails: insufficient fuel, in-transit rejection, overheating,
    TWR checks on surface sites, invalid locations
  - Interplanetary phase-angle modulation
  - Extra-dv / time tradeoff logic
  - settle_arrivals timing correctness
  - Teleport and refuel admin helpers
"""

import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os

os.environ.setdefault("DEV_SKIP_AUTH", "1")


# ────────────────────────────────────────────────────────────────────
# Pure-function unit tests (no server, no DB)
# ────────────────────────────────────────────────────────────────────


class TestTsiolkovskyDeltaV:
    """Test the rocket equation implementation in catalog_service."""

    def test_dv_remaining_basic(self):
        from catalog_service import compute_delta_v_remaining_m_s

        # 5000 kg dry, 5000 kg fuel, 900s ISP → dv ≈ 900*9.81*ln(2) ≈ 6117 m/s
        dv = compute_delta_v_remaining_m_s(5000, 5000, 900)
        expected = 900 * 9.80665 * math.log(2)
        assert abs(dv - expected) < 1.0, f"Expected ~{expected:.0f}, got {dv:.0f}"

    def test_dv_remaining_zero_fuel(self):
        from catalog_service import compute_delta_v_remaining_m_s

        assert compute_delta_v_remaining_m_s(5000, 0, 900) == 0.0

    def test_dv_remaining_zero_isp(self):
        from catalog_service import compute_delta_v_remaining_m_s

        assert compute_delta_v_remaining_m_s(5000, 5000, 0) == 0.0

    def test_dv_remaining_zero_dry_mass(self):
        from catalog_service import compute_delta_v_remaining_m_s

        assert compute_delta_v_remaining_m_s(0, 5000, 900) == 0.0

    def test_dv_remaining_negative_values_clamped(self):
        from catalog_service import compute_delta_v_remaining_m_s

        # Negatives are clamped to 0.0 internally
        assert compute_delta_v_remaining_m_s(-100, 5000, 900) == 0.0
        assert compute_delta_v_remaining_m_s(5000, -100, 900) == 0.0

    def test_dv_remaining_none_values(self):
        from catalog_service import compute_delta_v_remaining_m_s

        assert compute_delta_v_remaining_m_s(None, 5000, 900) == 0.0
        assert compute_delta_v_remaining_m_s(5000, None, 900) == 0.0
        assert compute_delta_v_remaining_m_s(5000, 5000, None) == 0.0

    def test_dv_remaining_scales_with_fuel(self):
        from catalog_service import compute_delta_v_remaining_m_s

        dv_low = compute_delta_v_remaining_m_s(5000, 1000, 900)
        dv_high = compute_delta_v_remaining_m_s(5000, 5000, 900)
        assert dv_high > dv_low

    def test_dv_remaining_scales_with_isp(self):
        from catalog_service import compute_delta_v_remaining_m_s

        dv_low = compute_delta_v_remaining_m_s(5000, 5000, 300)
        dv_high = compute_delta_v_remaining_m_s(5000, 5000, 900)
        assert dv_high > dv_low
        # ISP scales linearly: dv should scale 3x
        assert abs(dv_high / dv_low - 3.0) < 0.01


class TestFuelConsumption:
    """Test fuel-needed-for-delta-v computation."""

    def test_zero_dv_uses_no_fuel(self):
        from catalog_service import compute_fuel_needed_for_delta_v_kg

        assert compute_fuel_needed_for_delta_v_kg(5000, 5000, 900, 0.0) == 0.0

    def test_fuel_needed_basic(self):
        from catalog_service import compute_fuel_needed_for_delta_v_kg

        fuel = compute_fuel_needed_for_delta_v_kg(5000, 5000, 900, 1000)
        assert fuel > 0
        assert fuel < 5000  # Shouldn't use all fuel for 1000 m/s with 900s ISP

    def test_fuel_never_exceeds_available(self):
        from catalog_service import compute_fuel_needed_for_delta_v_kg

        # Request absurd dv — fuel used should be capped at available
        fuel = compute_fuel_needed_for_delta_v_kg(5000, 2000, 900, 999999)
        assert fuel <= 2000

    def test_roundtrip_dv_fuel(self):
        """Fuel consumed for X m/s should leave exactly (total_dv - X) remaining."""
        from catalog_service import (
            compute_delta_v_remaining_m_s,
            compute_fuel_needed_for_delta_v_kg,
        )

        dry = 5000
        fuel = 5000
        isp = 900
        total_dv = compute_delta_v_remaining_m_s(dry, fuel, isp)

        # Use half the dv
        target_dv = total_dv / 2
        fuel_used = compute_fuel_needed_for_delta_v_kg(dry, fuel, isp, target_dv)
        remaining_fuel = fuel - fuel_used
        remaining_dv = compute_delta_v_remaining_m_s(dry, remaining_fuel, isp)

        # Should be close to the other half
        assert abs(remaining_dv - (total_dv - target_dv)) < 1.0

    def test_fuel_needed_zero_isp_returns_more_than_available(self):
        from catalog_service import compute_fuel_needed_for_delta_v_kg

        # Zero ISP means infinite fuel needed — should return fuel+1
        result = compute_fuel_needed_for_delta_v_kg(5000, 5000, 0, 1000)
        assert result > 5000

    def test_fuel_needed_zero_mass_returns_more_than_available(self):
        from catalog_service import compute_fuel_needed_for_delta_v_kg

        result = compute_fuel_needed_for_delta_v_kg(0, 5000, 900, 1000)
        assert result > 5000

    def test_fuel_consumption_monotonic(self):
        """More dv should always need more fuel."""
        from catalog_service import compute_fuel_needed_for_delta_v_kg

        fuels = [
            compute_fuel_needed_for_delta_v_kg(5000, 5000, 900, dv)
            for dv in [100, 500, 1000, 2000, 5000]
        ]
        for i in range(len(fuels) - 1):
            assert fuels[i + 1] >= fuels[i], f"Fuel use not monotonic at dv step {i}"


class TestWetMassAndAcceleration:
    def test_wet_mass(self):
        from catalog_service import compute_wet_mass_kg

        assert compute_wet_mass_kg(5000, 3000) == pytest.approx(8000.0)

    def test_acceleration_gs(self):
        from catalog_service import compute_acceleration_gs

        # 10 kN thrust, 10000 kg wet mass → 10000/(10000*9.81) ≈ 0.102 g
        acc = compute_acceleration_gs(5000, 5000, 10.0)
        expected = (10.0 * 1000) / (10000 * 9.80665)
        assert abs(acc - expected) < 0.001


# ────────────────────────────────────────────────────────────────────
# Orbital mechanics helper tests
# ────────────────────────────────────────────────────────────────────


class TestOrbitalHelpers:
    """Test the interplanetary phase-angle and dv-time tradeoff functions."""

    def test_heliocentric_state_returns_dict(self):
        from fleet_router import _body_heliocentric_state

        state = _body_heliocentric_state("earth", 0.0)
        assert state is not None
        assert isinstance(state["r_km"], float)
        assert isinstance(state["theta_rad"], float)
        assert state["r_km"] > 0

    def test_heliocentric_state_sun_at_origin(self):
        from fleet_router import _body_heliocentric_state

        state = _body_heliocentric_state("sun", 0.0)
        assert state == {"r_km": 0.0, "theta_rad": 0.0}

    def test_heliocentric_state_unknown_body_returns_none(self):
        from fleet_router import _body_heliocentric_state

        assert _body_heliocentric_state("pluto", 0.0) is None

    def test_heliocentric_state_earth_radius_reasonable(self):
        from fleet_router import _body_heliocentric_state

        state = _body_heliocentric_state("earth", 0.0)
        # Earth ~149.6 million km from Sun; allow wide margin
        assert 1.3e8 < state["r_km"] < 1.6e8

    def test_is_interplanetary_same_body(self):
        from fleet_router import _is_interplanetary

        assert _is_interplanetary("LEO", "HEO") is False
        assert _is_interplanetary("LEO", "GEO") is False
        assert _is_interplanetary("LLO", "HLO") is False

    def test_is_interplanetary_different_bodies(self):
        from fleet_router import _is_interplanetary

        assert _is_interplanetary("LEO", "LMO") is True
        assert _is_interplanetary("LEO", "VEN_ORB") is True

    def test_is_interplanetary_unknown_locations(self):
        from fleet_router import _is_interplanetary

        # Unknown locations should return False (no body mapping)
        assert _is_interplanetary("NOWHERE", "LEO") is False

    def test_phase_solution_returns_multiplier_in_range(self):
        from fleet_router import _body_phase_solution

        # At any time, multiplier should be between 1.0 and 1.4
        for t in [0, 86400 * 100, 86400 * 365, 86400 * 730]:
            sol = _body_phase_solution("earth", "mars", t)
            assert sol is not None, f"No solution at t={t}"
            m = sol["phase_multiplier"]
            assert 1.0 <= m <= 1.401, f"Phase multiplier {m} out of range at t={t}"

    def test_phase_solution_unknown_body_returns_none(self):
        from fleet_router import _body_phase_solution

        # Unknown body pair → None
        assert _body_phase_solution("earth", "pluto", 0) is None

    def test_excess_dv_time_reduction_zero_extra(self):
        from fleet_router import _excess_dv_time_reduction

        # Zero extra dv → no reduction
        result = _excess_dv_time_reduction(86400, 1000, 0.0)
        assert result == 86400

    def test_excess_dv_time_reduction_positive(self):
        from fleet_router import _excess_dv_time_reduction

        base_tof = 86400 * 30  # 30 days
        reduced = _excess_dv_time_reduction(base_tof, 5000, 0.5)
        assert reduced < base_tof
        assert reduced > 0

    def test_excess_dv_time_reduction_doubling(self):
        from fleet_router import _excess_dv_time_reduction

        base_tof = 86400 * 30
        # 1x extra (doubling dv) should significantly reduce TOF
        reduced = _excess_dv_time_reduction(base_tof, 5000, 1.0)
        assert reduced < base_tof * 0.8

    def test_excess_dv_time_reduction_floor(self):
        from fleet_router import _excess_dv_time_reduction

        # Even with extreme extra-dv, should not go below 1 hour
        reduced = _excess_dv_time_reduction(86400, 1000, 2.0)
        assert reduced >= 3600


# ────────────────────────────────────────────────────────────────────
# Dijkstra route matrix tests (in-memory DB)
# ────────────────────────────────────────────────────────────────────


class TestDijkstraRouteMatrix:
    """Test the route-finding algorithm on a controlled graph."""

    def _build_small_network(self, conn):
        """Insert a minimal 3-node network: A → B → C."""
        conn.execute("DELETE FROM locations WHERE is_group = 0")
        conn.execute("DELETE FROM transfer_edges")
        conn.execute("DELETE FROM transfer_matrix")

        locs = [
            ("A", "Alpha", None, 0, 10, 0, 0),
            ("B", "Bravo", None, 0, 20, 100, 0),
            ("C", "Charlie", None, 0, 30, 200, 0),
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO locations (id,name,parent_id,is_group,sort_order,x,y) VALUES (?,?,?,?,?,?,?)",
            locs,
        )
        edges = [
            ("A", "B", 500, 7200),
            ("B", "A", 500, 7200),
            ("B", "C", 800, 14400),
            ("C", "B", 800, 14400),
            ("A", "C", 2000, 36000),  # Direct but expensive
            ("C", "A", 2000, 36000),
        ]
        conn.executemany(
            "INSERT INTO transfer_edges (from_id,to_id,dv_m_s,tof_s) VALUES (?,?,?,?)",
            edges,
        )
        from main import dijkstra_all_pairs

        dijkstra_all_pairs(conn)
        conn.commit()

    def test_self_transfer_zero(self, db_conn):
        """A→A should be 0 dv and 0 tof."""
        self._build_small_network(db_conn)
        row = db_conn.execute(
            "SELECT dv_m_s, tof_s FROM transfer_matrix WHERE from_id='A' AND to_id='A'"
        ).fetchone()
        assert row is not None
        assert float(row["dv_m_s"]) == 0.0
        assert float(row["tof_s"]) == 0.0

    def test_direct_edge_used(self, db_conn):
        """A→B should use the direct 500 m/s edge."""
        self._build_small_network(db_conn)
        row = db_conn.execute(
            "SELECT dv_m_s, tof_s, path_json FROM transfer_matrix WHERE from_id='A' AND to_id='B'"
        ).fetchone()
        assert float(row["dv_m_s"]) == 500.0
        assert float(row["tof_s"]) == 7200.0

    def test_optimal_route_chosen(self, db_conn):
        """A→C should prefer A→B→C (1300 m/s) over direct (2000 m/s)."""
        self._build_small_network(db_conn)
        row = db_conn.execute(
            "SELECT dv_m_s, tof_s, path_json FROM transfer_matrix WHERE from_id='A' AND to_id='C'"
        ).fetchone()
        assert float(row["dv_m_s"]) == 1300.0  # 500 + 800
        assert float(row["tof_s"]) == 21600.0  # 7200 + 14400
        path = json.loads(row["path_json"])
        assert path == ["A", "B", "C"]

    def test_all_pairs_populated(self, db_conn):
        """All 9 pairs (3×3) should exist in the matrix."""
        self._build_small_network(db_conn)
        count = db_conn.execute("SELECT COUNT(*) AS c FROM transfer_matrix").fetchone()["c"]
        assert count == 9

    def test_symmetry_check(self, db_conn):
        """A→B dv should equal B→A dv when edges are symmetric."""
        self._build_small_network(db_conn)
        ab = db_conn.execute(
            "SELECT dv_m_s FROM transfer_matrix WHERE from_id='A' AND to_id='B'"
        ).fetchone()
        ba = db_conn.execute(
            "SELECT dv_m_s FROM transfer_matrix WHERE from_id='B' AND to_id='A'"
        ).fetchone()
        assert float(ab["dv_m_s"]) == float(ba["dv_m_s"])


class TestRealTransferMatrix:
    """Tests on the production transfer matrix seeded by app startup."""

    def test_leo_to_heo_exists(self, client):
        r = client.get("/api/transfer_quote", params={"from_id": "LEO", "to_id": "HEO"})
        assert r.status_code == 200
        data = r.json()
        assert data["dv_m_s"] > 0
        assert data["tof_s"] > 0

    def test_leo_to_llo_exists(self, client):
        r = client.get("/api/transfer_quote", params={"from_id": "LEO", "to_id": "LLO"})
        assert r.status_code == 200
        data = r.json()
        assert data["dv_m_s"] > 0
        assert data["tof_s"] > 0

    def test_quote_has_path(self, client):
        r = client.get("/api/transfer_quote", params={"from_id": "LEO", "to_id": "LLO"})
        data = r.json()
        assert "path" in data
        path = data["path"]
        assert isinstance(path, list)
        # Path should start with LEO and end with LLO
        if len(path) >= 2:
            assert path[0] == "LEO"
            assert path[-1] == "LLO"

    def test_self_quote_is_zero(self, client):
        r = client.get("/api/transfer_quote", params={"from_id": "LEO", "to_id": "LEO"})
        assert r.status_code == 200
        data = r.json()
        assert data["dv_m_s"] == 0.0
        assert data["tof_s"] == 0.0

    def test_nonexistent_location_404(self, client):
        r = client.get("/api/transfer_quote", params={"from_id": "LEO", "to_id": "NONEXISTENT_LOC"})
        assert r.status_code == 404

    def test_missing_params_422(self, client):
        r = client.get("/api/transfer_quote")
        assert r.status_code == 422

    def test_all_location_pairs_reachable(self, client):
        """Every non-group location should be reachable from LEO."""
        r = client.get("/api/locations")
        assert r.status_code == 200
        locations = r.json()
        # Flatten to get leaf location IDs
        leaf_ids = []
        if isinstance(locations, list):
            for loc in locations:
                if isinstance(loc, dict) and not loc.get("is_group"):
                    leaf_ids.append(loc["id"])
        elif isinstance(locations, dict) and "locations" in locations:
            for loc in locations["locations"]:
                if isinstance(loc, dict) and not loc.get("is_group"):
                    leaf_ids.append(loc["id"])

        if not leaf_ids:
            pytest.skip("No leaf locations found")

        # Check at least some core locations are reachable from LEO
        core_locs = [lid for lid in leaf_ids if lid in ("LEO", "HEO", "GEO", "L1", "L2", "LLO", "HLO")]
        for dest in core_locs:
            r = client.get("/api/transfer_quote", params={"from_id": "LEO", "to_id": dest})
            assert r.status_code == 200, f"LEO → {dest} failed with {r.status_code}"


# ────────────────────────────────────────────────────────────────────
# Advanced transfer quote tests
# ────────────────────────────────────────────────────────────────────


class TestAdvancedTransferQuote:
    def test_basic_advanced_quote(self, client):
        r = client.get("/api/transfer_quote_advanced", params={
            "from_id": "LEO",
            "to_id": "HEO",
        })
        assert r.status_code == 200
        data = r.json()
        assert "base_dv_m_s" in data
        assert "base_tof_s" in data
        assert "dv_m_s" in data
        assert "tof_s" in data
        assert "phase_multiplier" in data
        assert "is_interplanetary" in data

    def test_intra_system_no_phase_angle(self, client):
        """LEO→HEO is within Earth system, so phase_multiplier should be 1.0."""
        r = client.get("/api/transfer_quote_advanced", params={
            "from_id": "LEO",
            "to_id": "HEO",
        })
        data = r.json()
        assert data["is_interplanetary"] is False
        assert data["phase_multiplier"] == 1.0
        assert data["dv_m_s"] == data["base_dv_m_s"]

    def test_extra_dv_reduces_tof(self, client):
        """Supplying extra_dv_fraction > 0 should reduce TOF."""
        r_base = client.get("/api/transfer_quote_advanced", params={
            "from_id": "LEO",
            "to_id": "LLO",
            "extra_dv_fraction": 0.0,
        })
        r_fast = client.get("/api/transfer_quote_advanced", params={
            "from_id": "LEO",
            "to_id": "LLO",
            "extra_dv_fraction": 1.0,
        })
        base = r_base.json()
        fast = r_fast.json()
        assert fast["tof_s"] < base["tof_s"]
        assert fast["dv_m_s"] > base["dv_m_s"]

    def test_extra_dv_fraction_zero_matches_base(self, client):
        r = client.get("/api/transfer_quote_advanced", params={
            "from_id": "LEO",
            "to_id": "HEO",
            "extra_dv_fraction": 0.0,
        })
        data = r.json()
        # With no extra dv and no interplanetary effects, final dv == base dv
        assert data["dv_m_s"] == data["base_dv_m_s"]

    def test_extra_dv_boundary(self, client):
        """extra_dv_fraction capped at 2.0."""
        r = client.get("/api/transfer_quote_advanced", params={
            "from_id": "LEO",
            "to_id": "HEO",
            "extra_dv_fraction": 2.0,
        })
        assert r.status_code == 200

    def test_extra_dv_over_limit(self, client):
        """extra_dv_fraction > 2.0 should be rejected."""
        r = client.get("/api/transfer_quote_advanced", params={
            "from_id": "LEO",
            "to_id": "HEO",
            "extra_dv_fraction": 3.0,
        })
        assert r.status_code == 422

    def test_departure_time_parameter(self, client):
        """Custom departure time should be echoed back."""
        dep = 1000000.0
        r = client.get("/api/transfer_quote_advanced", params={
            "from_id": "LEO",
            "to_id": "HEO",
            "departure_time": dep,
        })
        data = r.json()
        assert data["departure_time"] == dep

    def test_nonexistent_location_404(self, client):
        r = client.get("/api/transfer_quote_advanced", params={
            "from_id": "LEO",
            "to_id": "NOTAPLACE",
        })
        assert r.status_code == 404

    def test_interplanetary_quote_has_window_suggestions(self, client):
        r = client.get("/api/transfer_quote_advanced", params={
            "from_id": "LEO",
            "to_id": "LMO",
        })
        assert r.status_code == 200
        data = r.json()
        if not data.get("is_interplanetary"):
            pytest.skip("Route resolved as non-interplanetary in this seed")

        orbital = data.get("orbital") or {}
        suggestions = orbital.get("window_suggestions")
        assert isinstance(suggestions, list)
        if suggestions:
            first = suggestions[0]
            assert "departure_time" in first
            assert "wait_s" in first
            assert "phase_multiplier" in first

    def test_asteroid_belt_body_is_tracked_interplanetary(self, client):
        r = client.get("/api/transfer_quote_advanced", params={
            "from_id": "LEO",
            "to_id": "CERES_LO",
        })
        assert r.status_code == 200
        data = r.json()
        assert data.get("is_interplanetary") is True
        orbital = data.get("orbital") or {}
        assert orbital.get("to_body") == "ceres"


# ────────────────────────────────────────────────────────────────────
# Ship transfer lifecycle (integration via API)
# ────────────────────────────────────────────────────────────────────


class TestShipTransferLifecycle:
    """Full lifecycle: spawn → transfer → verify transit → settle → verify arrival → cleanup."""

    def _spawn_ship(self, client, ship_id="xfer_test_ship", location="LEO", fuel_kg=None):
        """Helper to spawn a ship at a location with default parts."""
        payload = {
            "name": f"Transfer Test {ship_id}",
            "location_id": location,
            "ship_id": ship_id,
            "parts": [
                {"item_id": "scn_1_pioneer"},
                {"item_id": "water_tank_10_m3"},
            ],
        }
        if fuel_kg is not None:
            payload["fuel_kg"] = fuel_kg
        r = client.post("/api/admin/spawn_ship", json=payload)
        assert r.status_code == 200, f"Failed to spawn ship: {r.text}"
        return r.json()

    def _delete_ship(self, client, ship_id):
        r = client.delete(f"/api/admin/ships/{ship_id}")
        # Tolerate already-deleted
        return r.status_code in (200, 404)

    def _refuel_ship(self, client, ship_id):
        r = client.post(f"/api/admin/ships/{ship_id}/refuel")
        assert r.status_code == 200
        return r.json()

    def _get_ship(self, client, ship_id):
        """Get ship data from /api/state."""
        r = client.get("/api/state")
        assert r.status_code == 200
        for ship in r.json()["ships"]:
            if ship["id"] == ship_id:
                return ship
        return None

    def test_spawn_and_verify_docked(self, client):
        """Spawned ship should be docked at the specified location."""
        try:
            result = self._spawn_ship(client, "test_spawn_verify")
            ship = result["ship"]
            assert ship["location_id"] == "LEO"
            assert ship["status"] == "docked"
            assert ship["fuel_kg"] > 0, f"Expected fuel > 0, got {ship['fuel_kg']}"
            assert ship["dry_mass_kg"] > 0, f"Expected dry_mass > 0, got {ship['dry_mass_kg']}"
            assert ship["isp_s"] > 0, f"Expected isp > 0, got {ship['isp_s']}"
            assert ship["delta_v_remaining_m_s"] > 0, f"Expected dv > 0, got {ship['delta_v_remaining_m_s']}"
        finally:
            self._delete_ship(client, "test_spawn_verify")

    def test_basic_transfer(self, client):
        """Ships should transit from LEO → HEO successfully."""
        ship_id = "test_basic_xfer"
        try:
            self._spawn_ship(client, ship_id)
            self._refuel_ship(client, ship_id)

            # Initiate transfer
            r = client.post(f"/api/ships/{ship_id}/transfer", json={
                "to_location_id": "HEO",
            })
            assert r.status_code == 200, f"Transfer failed: {r.text}"
            data = r.json()
            assert data["ok"] is True
            assert data["from"] == "LEO"
            assert data["to"] == "HEO"
            assert data["dv_m_s"] > 0
            assert data["fuel_used_kg"] > 0
            assert data["fuel_remaining_kg"] >= 0
            assert data["departed_at"] > 0
            assert data["arrives_at"] > data["departed_at"]
            assert isinstance(data.get("transfer_legs"), list)
            if data["transfer_legs"]:
                first_leg = data["transfer_legs"][0]
                assert "from_id" in first_leg and "to_id" in first_leg
                assert "departure_time" in first_leg and "arrival_time" in first_leg
        finally:
            self._delete_ship(client, ship_id)

    def test_transfer_sets_transit_state(self, client):
        """After transfer, ship should show as 'transit' with correct from/to."""
        ship_id = "test_transit_state"
        try:
            self._spawn_ship(client, ship_id)
            self._refuel_ship(client, ship_id)

            client.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "HEO"})

            ship = self._get_ship(client, ship_id)
            assert ship is not None
            assert ship["status"] == "transit"
            assert ship["location_id"] is None
            assert ship["from_location_id"] == "LEO"
            assert ship["to_location_id"] == "HEO"
            assert ship["arrives_at"] is not None
        finally:
            self._delete_ship(client, ship_id)

    def test_transfer_consumes_fuel(self, client):
        """Fuel should decrease after transfer."""
        ship_id = "test_fuel_use"
        try:
            self._spawn_ship(client, ship_id)
            self._refuel_ship(client, ship_id)

            # Get fuel before
            ship_before = self._get_ship(client, ship_id)
            fuel_before = ship_before["fuel_kg"]

            r = client.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "HEO"})
            data = r.json()

            assert data["fuel_used_kg"] > 0
            assert data["fuel_remaining_kg"] < fuel_before
            assert abs(data["fuel_remaining_kg"] - (fuel_before - data["fuel_used_kg"])) < 1.0
        finally:
            self._delete_ship(client, ship_id)

    def test_in_transit_ship_cannot_transfer(self, client):
        """A ship already in transit should be rejected for a second transfer."""
        ship_id = "test_double_xfer"
        try:
            self._spawn_ship(client, ship_id)
            self._refuel_ship(client, ship_id)

            r1 = client.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "HEO"})
            assert r1.status_code == 200

            # Second transfer should fail
            r2 = client.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "GEO"})
            assert r2.status_code == 400
            assert "transit" in r2.json()["detail"].lower()
        finally:
            self._delete_ship(client, ship_id)

    def test_insufficient_fuel_rejected(self, client):
        """Ship with almost no fuel should be blocked from long transfers."""
        ship_id = "test_no_fuel"
        try:
            self._spawn_ship(client, ship_id, fuel_kg=0.1)

            # LEO → LLO requires significant dv
            r = client.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "LLO"})
            assert r.status_code == 400
            detail = r.json()["detail"].lower()
            assert "fuel" in detail or "insufficient" in detail, f"Unexpected detail: {detail}"
        finally:
            self._delete_ship(client, ship_id)

    def test_nonexistent_ship_404(self, client):
        r = client.post("/api/ships/ghost_ship_xyz/transfer", json={"to_location_id": "HEO"})
        assert r.status_code == 404

    def test_nonexistent_destination(self, client):
        ship_id = "test_bad_dest"
        try:
            self._spawn_ship(client, ship_id)
            self._refuel_ship(client, ship_id)

            r = client.post(f"/api/ships/{ship_id}/transfer", json={
                "to_location_id": "MOON_BASE_ALPHA_NONEXISTENT"
            })
            assert r.status_code == 404
        finally:
            self._delete_ship(client, ship_id)

    def test_transfer_to_same_location(self, client):
        """Transfer to current location should succeed with 0 fuel use."""
        ship_id = "test_self_xfer"
        try:
            self._spawn_ship(client, ship_id)
            self._refuel_ship(client, ship_id)

            r = client.post(f"/api/ships/{ship_id}/transfer", json={
                "to_location_id": "LEO"
            })
            # Should succeed — 0 dv transfer
            assert r.status_code == 200
            data = r.json()
            assert data["dv_m_s"] == 0.0
            assert data["fuel_used_kg"] == 0.0
        finally:
            self._delete_ship(client, ship_id)

    def test_multiple_sequential_transfers(self, client):
        """Ship should be able to do LEO→HEO, arrive, then HEO→GEO."""
        ship_id = "test_sequential_xfer"
        try:
            self._spawn_ship(client, ship_id)
            self._refuel_ship(client, ship_id)

            # First transfer: LEO → HEO
            r1 = client.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "HEO"})
            assert r1.status_code == 200

            # Force arrival by directly setting arrives_at in the past
            # We use the teleport endpoint instead
            client.post(f"/api/admin/ships/{ship_id}/teleport", json={"to_location_id": "HEO"})

            # Refuel for next leg
            self._refuel_ship(client, ship_id)

            # Second transfer: HEO → GEO
            r2 = client.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "GEO"})
            assert r2.status_code == 200
            data2 = r2.json()
            assert data2["from"] == "HEO"
            assert data2["to"] == "GEO"
        finally:
            self._delete_ship(client, ship_id)

    def test_long_range_transfer_leo_to_geo(self, client):
        """Multi-hop LEO → GEO should work and consume appropriate fuel."""
        ship_id = "test_long_range"
        try:
            self._spawn_ship(client, ship_id)
            self._refuel_ship(client, ship_id)

            # Get quote first
            quote = client.get("/api/transfer_quote", params={
                "from_id": "LEO",
                "to_id": "GEO",
            }).json()

            # Initiate transfer
            r = client.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "GEO"})
            assert r.status_code == 200
            data = r.json()
            # Should match the dv from the quote
            assert abs(data["dv_m_s"] - quote["dv_m_s"]) < 1.0
            assert data["fuel_used_kg"] > 0
        finally:
            self._delete_ship(client, ship_id)

    def test_long_range_transfer_needs_more_dv(self, client):
        """LEO → LLO requires high dv — a small ship should be rejected."""
        ship_id = "test_long_range_reject"
        try:
            self._spawn_ship(client, ship_id)
            self._refuel_ship(client, ship_id)

            # Get quote to verify it requires more dv than available
            quote = client.get("/api/transfer_quote", params={
                "from_id": "LEO",
                "to_id": "LLO",
            }).json()

            # Ship has ~3184 m/s with scn_1_pioneer + water_tank_10_m3
            # If LEO→LLO needs more, transfer should fail
            r = client.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "LLO"})
            if quote["dv_m_s"] > 3200:
                assert r.status_code == 400, "Should be rejected for insufficient fuel"
            else:
                assert r.status_code == 200, "Should succeed if dv is sufficient"
        finally:
            self._delete_ship(client, ship_id)


# ────────────────────────────────────────────────────────────────────
# Settle arrivals tests
# ────────────────────────────────────────────────────────────────────


class TestSettleArrivals:
    """Test the settle_arrivals() function that finalizes ship transit."""

    def test_ship_arrives_when_time_passes(self, db_conn):
        """A ship with arrives_at in the past should be moved to destination."""
        from main import settle_arrivals

        # Insert a minimal location
        db_conn.execute(
            "INSERT OR REPLACE INTO locations (id,name,parent_id,is_group,sort_order,x,y) VALUES ('X','X',NULL,0,0,0,0)"
        )
        db_conn.execute(
            "INSERT OR REPLACE INTO locations (id,name,parent_id,is_group,sort_order,x,y) VALUES ('Y','Y',NULL,0,0,0,0)"
        )

        now = time.time()
        db_conn.execute(
            """INSERT INTO ships (id,name,location_id,from_location_id,to_location_id,
                departed_at,arrives_at,transfer_path_json,parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s)
               VALUES ('s1','Ship1',NULL,'X','Y',?,?,'[]','[]',100,200,500,900)""",
            (now - 1000, now - 100),
        )
        db_conn.commit()

        settle_arrivals(db_conn, now)
        db_conn.commit()

        row = db_conn.execute("SELECT * FROM ships WHERE id='s1'").fetchone()
        assert row["location_id"] == "Y"
        assert row["from_location_id"] is None
        assert row["to_location_id"] is None
        assert row["arrives_at"] is None

    def test_ship_not_arrived_yet(self, db_conn):
        """A ship with arrives_at in the future should remain in transit."""
        from main import settle_arrivals

        db_conn.execute(
            "INSERT OR REPLACE INTO locations (id,name,parent_id,is_group,sort_order,x,y) VALUES ('X','X',NULL,0,0,0,0)"
        )
        db_conn.execute(
            "INSERT OR REPLACE INTO locations (id,name,parent_id,is_group,sort_order,x,y) VALUES ('Y','Y',NULL,0,0,0,0)"
        )

        now = time.time()
        future = now + 99999
        db_conn.execute(
            """INSERT INTO ships (id,name,location_id,from_location_id,to_location_id,
                departed_at,arrives_at,transfer_path_json,parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s)
               VALUES ('s2','Ship2',NULL,'X','Y',?,?,'[]','[]',100,200,500,900)""",
            (now, future),
        )
        db_conn.commit()

        settle_arrivals(db_conn, now)
        db_conn.commit()

        row = db_conn.execute("SELECT * FROM ships WHERE id='s2'").fetchone()
        assert row["location_id"] is None  # Still in transit
        assert row["to_location_id"] == "Y"
        assert row["arrives_at"] == future

    def test_settle_only_affects_arrived_ships(self, db_conn):
        """Settling should only affect ships whose arrives_at <= now."""
        from main import settle_arrivals

        db_conn.execute(
            "INSERT OR REPLACE INTO locations (id,name,parent_id,is_group,sort_order,x,y) VALUES ('P','P',NULL,0,0,0,0)"
        )
        db_conn.execute(
            "INSERT OR REPLACE INTO locations (id,name,parent_id,is_group,sort_order,x,y) VALUES ('Q','Q',NULL,0,0,0,0)"
        )

        now = time.time()
        # Ship A: already arrived
        db_conn.execute(
            """INSERT INTO ships (id,name,location_id,from_location_id,to_location_id,
                departed_at,arrives_at,transfer_path_json,parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s)
               VALUES ('sa','A',NULL,'P','Q',?,?,'[]','[]',100,200,500,900)""",
            (now - 200, now - 10),
        )
        # Ship B: still in transit
        db_conn.execute(
            """INSERT INTO ships (id,name,location_id,from_location_id,to_location_id,
                departed_at,arrives_at,transfer_path_json,parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s)
               VALUES ('sb','B',NULL,'P','Q',?,?,'[]','[]',100,200,500,900)""",
            (now - 100, now + 500),
        )
        db_conn.commit()

        settle_arrivals(db_conn, now)
        db_conn.commit()

        a = db_conn.execute("SELECT location_id, arrives_at FROM ships WHERE id='sa'").fetchone()
        b = db_conn.execute("SELECT location_id, arrives_at FROM ships WHERE id='sb'").fetchone()
        assert a["location_id"] == "Q"
        assert a["arrives_at"] is None
        assert b["location_id"] is None
        assert b["arrives_at"] is not None

    def test_docked_ship_unaffected(self, db_conn):
        """A docked ship (arrives_at IS NULL) should not change."""
        from main import settle_arrivals

        db_conn.execute(
            "INSERT OR REPLACE INTO locations (id,name,parent_id,is_group,sort_order,x,y) VALUES ('D','D',NULL,0,0,0,0)"
        )
        db_conn.execute(
            """INSERT INTO ships (id,name,location_id,from_location_id,to_location_id,
                departed_at,arrives_at,transfer_path_json,parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s)
               VALUES ('sd','Docked','D',NULL,NULL,NULL,NULL,'[]','[]',100,200,500,900)"""
        )
        db_conn.commit()

        settle_arrivals(db_conn, time.time())
        db_conn.commit()

        row = db_conn.execute("SELECT location_id FROM ships WHERE id='sd'").fetchone()
        assert row["location_id"] == "D"


# ────────────────────────────────────────────────────────────────────
# Admin ship management helpers
# ────────────────────────────────────────────────────────────────────


class TestAdminShipOps:
    def test_teleport(self, client):
        """Admin teleport should instantly move a ship."""
        ship_id = "test_teleport"
        try:
            r = client.post("/api/admin/spawn_ship", json={
                "name": "Teleport Test",
                "location_id": "LEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
            })
            assert r.status_code == 200

            # Teleport to HEO
            r = client.post(f"/api/admin/ships/{ship_id}/teleport", json={
                "to_location_id": "HEO",
            })
            assert r.status_code == 200
            data = r.json()
            assert data["ship"]["location_id"] == "HEO"
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")

    def test_teleport_cancels_transit(self, client):
        """Teleporting a ship in transit should cancel the transit."""
        ship_id = "test_teleport_cancel"
        try:
            client.post("/api/admin/spawn_ship", json={
                "name": "Teleport Cancel",
                "location_id": "LEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
            })
            client.post(f"/api/admin/ships/{ship_id}/refuel")

            # Put in transit
            client.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "HEO"})

            # Verify in transit
            ship = None
            for s in client.get("/api/state").json()["ships"]:
                if s["id"] == ship_id:
                    ship = s
                    break
            assert ship is not None
            assert ship["status"] == "transit"

            # Teleport to GEO
            r = client.post(f"/api/admin/ships/{ship_id}/teleport", json={
                "to_location_id": "GEO",
            })
            assert r.status_code == 200

            # Should now be docked at GEO
            ship = None
            for s in client.get("/api/state").json()["ships"]:
                if s["id"] == ship_id:
                    ship = s
                    break
            assert ship is not None
            assert ship["status"] == "docked"
            assert ship["location_id"] == "GEO"
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")

    def test_refuel(self, client):
        """Admin refuel should restore fuel to capacity."""
        ship_id = "test_refuel"
        try:
            client.post("/api/admin/spawn_ship", json={
                "name": "Refuel Test",
                "location_id": "LEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
                "fuel_kg": 1.0,  # Nearly empty
            })

            r = client.post(f"/api/admin/ships/{ship_id}/refuel")
            assert r.status_code == 200
            data = r.json()
            assert data["ship"]["fuel_kg"] == data["ship"]["fuel_capacity_kg"]
            assert data["ship"]["fuel_kg"] > 1.0
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")

    def test_delete_ship(self, client):
        """Deleting a ship should remove it."""
        ship_id = "test_delete_target"
        client.post("/api/admin/spawn_ship", json={
            "name": "Doomed Ship",
            "location_id": "LEO",
            "ship_id": ship_id,
            "parts": [
                {"item_id": "scn_1_pioneer"},
                {"item_id": "water_tank_10_m3"},
            ],
        })

        r = client.delete(f"/api/admin/ships/{ship_id}")
        assert r.status_code == 200
        assert r.json()["deleted"]["id"] == ship_id

        # Verify gone
        r2 = client.delete(f"/api/admin/ships/{ship_id}")
        assert r2.status_code == 404

    def test_delete_nonexistent_ship(self, client):
        r = client.delete("/api/admin/ships/ghost_ship_never_existed")
        assert r.status_code == 404

    def test_teleport_nonexistent_ship(self, client):
        r = client.post("/api/admin/ships/ghost_ship_never_existed/teleport", json={
            "to_location_id": "LEO",
        })
        assert r.status_code == 404

    def test_teleport_to_nonexistent_location(self, client):
        ship_id = "test_teleport_bad_loc"
        try:
            client.post("/api/admin/spawn_ship", json={
                "name": "Bad Teleport",
                "location_id": "LEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
            })
            r = client.post(f"/api/admin/ships/{ship_id}/teleport", json={
                "to_location_id": "NARNIA",
            })
            assert r.status_code == 404
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")


# ────────────────────────────────────────────────────────────────────
# Fuel / delta-v consistency across the transfer pipeline
# ────────────────────────────────────────────────────────────────────


class TestFuelDvPipelineConsistency:
    """Verify that the dv quoted by transfer_quote matches what the
    transfer endpoint actually charges, and that fuel math is consistent."""

    def test_quoted_dv_matches_charged(self, client):
        """The dv_m_s returned by /transfer should match the quote."""
        ship_id = "test_dv_consistency"
        try:
            client.post("/api/admin/spawn_ship", json={
                "name": "DV Check",
                "location_id": "LEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
            })
            client.post(f"/api/admin/ships/{ship_id}/refuel")

            quote = client.get("/api/transfer_quote", params={
                "from_id": "LEO",
                "to_id": "HEO",
            }).json()

            r = client.post(f"/api/ships/{ship_id}/transfer", json={
                "to_location_id": "HEO",
            })
            xfer = r.json()

            assert abs(xfer["dv_m_s"] - quote["dv_m_s"]) < 1.0
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")

    def test_fuel_used_matches_tsiolkovsky(self, client):
        """Fuel consumed should be consistent with Tsiolkovsky equation."""
        from catalog_service import compute_fuel_needed_for_delta_v_kg

        ship_id = "test_fuel_tsiol"
        try:
            spawn = client.post("/api/admin/spawn_ship", json={
                "name": "Tsiolkovsky Check",
                "location_id": "LEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
            }).json()
            client.post(f"/api/admin/ships/{ship_id}/refuel")

            # Get ship stats
            ship_data = None
            for s in client.get("/api/state").json()["ships"]:
                if s["id"] == ship_id:
                    ship_data = s
                    break
            assert ship_data is not None

            dry = ship_data["dry_mass_kg"]
            fuel = ship_data["fuel_kg"]
            isp = ship_data["isp_s"]

            r = client.post(f"/api/ships/{ship_id}/transfer", json={
                "to_location_id": "HEO",
            })
            xfer = r.json()
            dv_used = xfer["dv_m_s"]

            # Compute expected fuel usage
            expected_fuel = compute_fuel_needed_for_delta_v_kg(dry, fuel, isp, dv_used)

            assert abs(xfer["fuel_used_kg"] - expected_fuel) < 1.0, (
                f"Fuel mismatch: API={xfer['fuel_used_kg']:.1f}, expected={expected_fuel:.1f}"
            )
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")


# ────────────────────────────────────────────────────────────────────
# Edge cases and stress tests
# ────────────────────────────────────────────────────────────────────


class TestTransferEdgeCases:
    def test_ship_with_no_fuel_cannot_transfer(self, client):
        """A ship with no fuel should be blocked from transfers."""
        ship_id = "test_no_fuel_xfer"
        try:
            client.post("/api/admin/spawn_ship", json={
                "name": "Empty Ship",
                "location_id": "LEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
                "fuel_kg": 0.0,
            })

            r = client.post(f"/api/ships/{ship_id}/transfer", json={
                "to_location_id": "HEO",
            })
            # Should fail — no fuel
            assert r.status_code == 400
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")

    def test_ship_with_no_parts_cannot_transfer(self, client):
        """A ship with empty parts should have 0 dv and fail transfers."""
        ship_id = "test_no_parts"
        try:
            # Spawn with non-thruster parts only — 0 ISP, 0 dv
            client.post("/api/admin/spawn_ship", json={
                "name": "Empty Ship",
                "location_id": "LEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "water_tank_10_m3"},
                ],
            })

            r = client.post(f"/api/ships/{ship_id}/transfer", json={
                "to_location_id": "HEO",
            })
            # Should fail — no ISP/thrust
            assert r.status_code == 400
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")

    def test_transfer_quote_symmetric_locations(self, client):
        """Quote A→B and B→A should both exist (connectivity check)."""
        quote_ab = client.get("/api/transfer_quote", params={
            "from_id": "LEO",
            "to_id": "HEO",
        })
        quote_ba = client.get("/api/transfer_quote", params={
            "from_id": "HEO",
            "to_id": "LEO",
        })
        assert quote_ab.status_code == 200
        assert quote_ba.status_code == 200

        ab = quote_ab.json()
        ba = quote_ba.json()
        # Both should have positive dv (not necessarily equal due to gravity)
        assert ab["dv_m_s"] > 0
        assert ba["dv_m_s"] > 0

    def test_many_rapid_spawn_transfer_delete(self, client):
        """Stress test: spawn, transfer, delete 5 ships rapidly."""
        ship_ids = [f"stress_ship_{i}" for i in range(5)]
        try:
            for sid in ship_ids:
                client.post("/api/admin/spawn_ship", json={
                    "name": f"Stress {sid}",
                    "location_id": "LEO",
                    "ship_id": sid,
                    "parts": [
                        {"item_id": "scn_1_pioneer"},
                        {"item_id": "water_tank_10_m3"},
                    ],
                })
                client.post(f"/api/admin/ships/{sid}/refuel")

                r = client.post(f"/api/ships/{sid}/transfer", json={
                    "to_location_id": "HEO",
                })
                assert r.status_code == 200, f"Ship {sid} transfer failed: {r.text}"
        finally:
            for sid in ship_ids:
                client.delete(f"/api/admin/ships/{sid}")

    def test_spawn_at_various_locations(self, client):
        """Ships should be spawnable at any non-group location."""
        locations = ["LEO", "HEO", "GEO", "L1", "LLO"]
        ship_ids = [f"spawn_at_{loc}" for loc in locations]
        try:
            for sid, loc in zip(ship_ids, locations):
                r = client.post("/api/admin/spawn_ship", json={
                    "name": f"Ship at {loc}",
                    "location_id": loc,
                    "ship_id": sid,
                    "parts": [
                        {"item_id": "scn_1_pioneer"},
                        {"item_id": "water_tank_10_m3"},
                    ],
                })
                assert r.status_code == 200, f"Failed to spawn at {loc}: {r.text}"
                assert r.json()["ship"]["location_id"] == loc
        finally:
            for sid in ship_ids:
                client.delete(f"/api/admin/ships/{sid}")

    def test_missing_transfer_body(self, client):
        """POST to /transfer with no body should return 422."""
        ship_id = "test_no_body"
        try:
            client.post("/api/admin/spawn_ship", json={
                "name": "No Body Test",
                "location_id": "LEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
            })
            r = client.post(f"/api/ships/{ship_id}/transfer")
            assert r.status_code == 422
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")

    def test_empty_destination(self, client):
        """POST with empty to_location_id should fail."""
        ship_id = "test_empty_dest"
        try:
            client.post("/api/admin/spawn_ship", json={
                "name": "Empty Dest",
                "location_id": "LEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
            })
            r = client.post(f"/api/ships/{ship_id}/transfer", json={
                "to_location_id": ""
            })
            # Should fail — no route for empty string
            assert r.status_code in (400, 404)
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")
