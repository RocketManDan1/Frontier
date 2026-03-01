"""
Phase 1 tests — Chain Mission (hierarchy walker) system.

Tests cover:
  - ``_build_chain_legs()`` decomposition for various route types
  - ``_classify_leg()`` classification (local / SOI / interplanetary)
  - ``orbit_summary()`` formatting
  - ``plan_chain_mission()`` end-to-end planning (local, SOI, multi-leg)
  - ``GET /api/transfer/mission_preview`` endpoint
  - ``POST /api/ships/{id}/transfer`` with ``departure_time`` and chain fallback
"""

import json
import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DEV_SKIP_AUTH", "1")


# ────────────────────────────────────────────────────────────────────
# Unit tests: hierarchy walker helpers (no DB, no server)
# ────────────────────────────────────────────────────────────────────


class TestClassifyLeg:
    """Test _classify_leg() classification."""

    def test_same_body_is_local(self):
        from orbit_bridge import _classify_leg
        assert _classify_leg("earth", "earth") == "local"

    def test_moon_earth_is_soi(self):
        from orbit_bridge import _classify_leg
        assert _classify_leg("moon", "earth") == "soi"

    def test_earth_moon_is_soi(self):
        from orbit_bridge import _classify_leg
        assert _classify_leg("earth", "moon") == "soi"

    def test_earth_mars_is_interplanetary(self):
        from orbit_bridge import _classify_leg
        assert _classify_leg("earth", "mars") == "interplanetary"

    def test_moon_mars_is_interplanetary(self):
        from orbit_bridge import _classify_leg
        assert _classify_leg("moon", "mars") == "interplanetary"

    def test_jupiter_saturn_is_interplanetary(self):
        from orbit_bridge import _classify_leg
        # Both orbit sun directly
        assert _classify_leg("jupiter", "saturn") == "interplanetary"


class TestBuildChainLegs:
    """Test _build_chain_legs() decomposition logic."""

    def test_same_body_local_transfer(self):
        """LEO → GEO = single local leg (both orbit Earth)."""
        from orbit_bridge import _build_chain_legs
        legs = _build_chain_legs("LEO", "GEO")
        assert legs is not None
        assert len(legs) == 1
        assert legs[0]["from"] == "LEO"
        assert legs[0]["to"] == "GEO"

    def test_soi_transfer_single_leg(self):
        """LEO → LLO should be a single SOI leg (Earth → Moon, shared parent)."""
        from orbit_bridge import _build_chain_legs
        legs = _build_chain_legs("LEO", "LLO")
        assert legs is not None
        assert len(legs) == 1
        assert legs[0]["from"] == "LEO"
        assert legs[0]["to"] == "LLO"

    def test_interplanetary_single_leg(self):
        """LEO → LMO = single interplanetary leg when gateways match endpoints."""
        from orbit_bridge import _build_chain_legs, _resolve_gateway_for_body
        from celestial_config import load_celestial_config
        cfg = load_celestial_config()
        earth_gw = _resolve_gateway_for_body("earth")
        mars_gw = _resolve_gateway_for_body("mars")

        legs = _build_chain_legs("LEO", "LMO")
        assert legs is not None
        # If LEO is earth gateway and LMO is mars gateway, it's a single leg.
        # Otherwise there will be ascent/descent legs.
        if earth_gw == "LEO" and mars_gw == "LMO":
            assert len(legs) == 1
        else:
            # Multi-leg: ascent to gateway + interplanetary + descent from gateway
            assert len(legs) >= 1

    def test_lmo_to_llo_multi_leg(self):
        """LMO → LLO requires interplanetary leg(s) through gateways
        because Mars and Moon are in different helio systems."""
        from orbit_bridge import _build_chain_legs
        legs = _build_chain_legs("LMO", "LLO")
        assert legs is not None
        # Mars is helio body "mars", Moon orbits Earth which is helio body "earth"
        # So chain: LMO → [mars_gw] → [earth_gw] → LLO
        assert len(legs) >= 2  # At minimum interplanetary + descent

    def test_lunar_surface_to_leo_single_soi(self):
        """Lunar surface → LEO: single SOI leg (surface resolves to Moon body)."""
        from orbit_bridge import _build_chain_legs
        legs = _build_chain_legs("LUNA_SHACKLETON", "LEO")
        assert legs is not None
        # Surface site resolves to orbit_node's body (Moon), and Moon→Earth
        # is a direct SOI transfer, so this is a single leg.
        assert len(legs) == 1
        assert legs[0]["from"] == "LUNA_SHACKLETON"
        assert legs[0]["to"] == "LEO"

    def test_leo_to_lunar_surface_single_soi(self):
        """LEO → Lunar surface: single SOI leg (surface resolves to Moon body)."""
        from orbit_bridge import _build_chain_legs
        legs = _build_chain_legs("LEO", "LUNA_SHACKLETON")
        assert legs is not None
        # Surface site resolves to Moon body; Earth→Moon is direct SOI.
        assert len(legs) == 1
        assert legs[0]["from"] == "LEO"
        assert legs[0]["to"] == "LUNA_SHACKLETON"

    def test_lunar_surface_to_lmo_multi_leg(self):
        """Lunar surface → Mars orbit: multi-leg chain through gateways."""
        from orbit_bridge import _build_chain_legs
        legs = _build_chain_legs("LUNA_SHACKLETON", "LMO")
        assert legs is not None
        # Moon resolves to helio body "earth", Mars to "mars".
        # Chain: LUNA_SHACKLETON → LEO (SOI ascent) → LMO (interplanetary)
        # or possibly more legs depending on gateway resolution.
        assert len(legs) >= 2
        # First leg starts from LUNA_SHACKLETON
        assert legs[0]["from"] == "LUNA_SHACKLETON"
        # Last leg ends at LMO
        assert legs[-1]["to"] == "LMO"
        # Should connect: each leg's "to" = next leg's "from"
        for i in range(len(legs) - 1):
            assert legs[i]["to"] == legs[i + 1]["from"], (
                f"Leg {i} to={legs[i]['to']} != leg {i+1} from={legs[i+1]['from']}"
            )

    def test_llo_to_llo_identity(self):
        """LLO → LLO: same location should return a single trivial leg."""
        from orbit_bridge import _build_chain_legs
        legs = _build_chain_legs("LLO", "LLO")
        # Same body, same location — but our implementation should still return something
        # since from_node_body == to_node_body (both moon)
        assert legs is not None
        assert len(legs) == 1

    def test_unknown_location_returns_none(self):
        """Unknown location_id → None."""
        from orbit_bridge import _build_chain_legs
        assert _build_chain_legs("NONEXISTENT", "LEO") is None
        assert _build_chain_legs("LEO", "NONEXISTENT") is None

    def test_chain_legs_continuity(self):
        """All chain legs should form a continuous path (to[i] == from[i+1])."""
        from orbit_bridge import _build_chain_legs
        # Test several multi-leg routes
        test_cases = [
            ("LUNA_SHACKLETON", "LMO"),
            ("LEO", "LUNA_SHACKLETON"),
            ("LMO", "LLO"),
        ]
        for from_id, to_id in test_cases:
            legs = _build_chain_legs(from_id, to_id)
            if legs and len(legs) > 1:
                for i in range(len(legs) - 1):
                    assert legs[i]["to"] == legs[i + 1]["from"], (
                        f"Route {from_id}→{to_id}: Leg {i} to={legs[i]['to']} "
                        f"!= leg {i+1} from={legs[i+1]['from']}"
                    )


class TestResolveLocationBody:
    """Test _resolve_location_body() for various location types."""

    def test_orbit_node(self):
        from orbit_bridge import _resolve_location_body
        assert _resolve_location_body("LEO") == "earth"
        assert _resolve_location_body("LLO") == "moon"
        assert _resolve_location_body("LMO") == "mars"

    def test_unknown_location(self):
        from orbit_bridge import _resolve_location_body
        assert _resolve_location_body("NONEXISTENT") == ""

    def test_surface_site(self):
        from orbit_bridge import _resolve_location_body
        body = _resolve_location_body("LUNA_SHACKLETON")
        assert body == "moon"


class TestResolveGateway:
    """Test _resolve_gateway_for_body()."""

    def test_earth_gateway(self):
        from orbit_bridge import _resolve_gateway_for_body
        gw = _resolve_gateway_for_body("earth")
        assert gw is not None
        assert gw == "LEO"

    def test_moon_gateway(self):
        from orbit_bridge import _resolve_gateway_for_body
        gw = _resolve_gateway_for_body("moon")
        assert gw is not None
        assert gw == "LLO"

    def test_mars_gateway(self):
        from orbit_bridge import _resolve_gateway_for_body
        gw = _resolve_gateway_for_body("mars")
        assert gw is not None
        assert gw == "LMO"

    def test_unknown_body(self):
        from orbit_bridge import _resolve_gateway_for_body
        gw = _resolve_gateway_for_body("pluto")
        assert gw is None


class TestOrbitSummary:
    """Test orbit_summary() formatting."""

    def test_circular_orbit(self):
        from orbit_bridge import orbit_summary
        elements = {
            "body_id": "earth",
            "a_km": 6778.0,
            "e": 0.0,
        }
        summary = orbit_summary(elements)
        assert summary["body"] == "earth"
        assert summary["type"] == "circular"
        assert summary["ecc"] == 0.0
        assert summary["altitude_km"] is not None
        assert summary["altitude_km"] > 0

    def test_elliptical_orbit(self):
        from orbit_bridge import orbit_summary
        elements = {
            "body_id": "earth",
            "a_km": 26000.0,
            "e": 0.5,
        }
        summary = orbit_summary(elements)
        assert summary["type"] == "elliptical"
        assert summary["altitude_km"] is None  # Not set for elliptical
        assert summary["pe_km"] is not None
        assert summary["ap_km"] is not None
        assert summary["pe_km"] < summary["ap_km"]

    def test_hyperbolic_orbit(self):
        from orbit_bridge import orbit_summary
        elements = {
            "body_id": "earth",
            "a_km": -50000.0,  # negative for hyperbolic
            "e": 1.5,
        }
        summary = orbit_summary(elements)
        assert summary["type"] == "hyperbolic"
        assert summary["ap_km"] is None  # infinite for hyperbolic

    def test_near_circular(self):
        from orbit_bridge import orbit_summary
        elements = {
            "body_id": "earth",
            "a_km": 6778.0,
            "e": 0.005,
        }
        summary = orbit_summary(elements)
        assert summary["type"] == "circular"  # e < 0.01 threshold


# ────────────────────────────────────────────────────────────────────
# Integration tests: plan_chain_mission (requires DB)
# ────────────────────────────────────────────────────────────────────


class TestPlanChainMission:
    """Integration tests for plan_chain_mission()."""

    def test_local_transfer_delegates(self, seeded_db):
        """Local transfer (LEO → GEO) delegates to compute_transfer_burn_plan()."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LEO", "GEO", 0.0)
        assert result is not None
        assert result["transfer_type"] == "local_hohmann"
        assert result["total_dv_m_s"] > 0
        assert result["total_tof_s"] > 0
        assert len(result["burns"]) >= 1
        assert result["initial_orbit"] is not None

    def test_soi_transfer_delegates(self, seeded_db):
        """SOI transfer (LEO → LLO) delegates to compute_transfer_burn_plan()."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LEO", "LLO", 0.0)
        assert result is not None
        assert result["transfer_type"] == "soi_hohmann"
        assert result["total_dv_m_s"] > 0
        assert result["total_tof_s"] > 0
        assert len(result["burns"]) >= 1

    def test_interplanetary_transfer_delegates(self, seeded_db):
        """Interplanetary (LEO → LMO) single-leg: delegates to compute_transfer_burn_plan()."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LEO", "LMO", 0.0)
        assert result is not None
        assert result["transfer_type"] == "interplanetary_lambert"
        assert result["total_dv_m_s"] > 0
        assert result["total_tof_s"] > 0

    def test_single_leg_soi_from_surface(self, seeded_db):
        """LUNA_SHACKLETON → LEO: single SOI leg (surface resolves to Moon body)."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LUNA_SHACKLETON", "LEO", 0.0)
        assert result is not None
        # Single-leg SOI transfer delegated directly
        assert result["transfer_type"] == "soi_hohmann"
        assert result["total_dv_m_s"] > 0
        assert result["total_tof_s"] > 0
        assert len(result["burns"]) >= 1

    def test_multi_leg_chain(self, seeded_db):
        """Multi-leg: LUNA_SHACKLETON → LMO (surface → LEO → LMO chain)."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LUNA_SHACKLETON", "LMO", 0.0)
        assert result is not None
        assert result["transfer_type"] == "chain_mission"
        assert result["leg_count"] >= 2
        assert len(result["legs"]) >= 2
        assert result["total_dv_m_s"] > 0
        assert result["total_tof_s"] > 0

        # Check leg structure
        legs = result["legs"]
        assert legs[0]["from_id"] == "LUNA_SHACKLETON"
        assert legs[-1]["to_id"] == "LMO"
        for leg in legs:
            assert leg["dv_m_s"] > 0
            assert leg["tof_s"] > 0

    def test_chain_mission_burns_have_leg_index(self, seeded_db):
        """Burns in multi-leg chain are annotated with leg_index."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LUNA_SHACKLETON", "LMO", 0.0)
        assert result is not None
        assert result["transfer_type"] == "chain_mission"
        for burn in result["burns"]:
            assert "leg_index" in burn
            assert isinstance(burn["leg_index"], int)

    def test_chain_mission_burns_time_ordered(self, seeded_db):
        """Burns across all legs should be monotonically increasing in time."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LUNA_SHACKLETON", "LMO", 0.0)
        assert result is not None
        burns = result["burns"]
        for i in range(len(burns) - 1):
            assert burns[i]["time_s"] <= burns[i + 1]["time_s"], (
                f"Burn {i} time {burns[i]['time_s']} > burn {i+1} time {burns[i+1]['time_s']}"
            )

    def test_chain_total_dv_equals_leg_sum(self, seeded_db):
        """Total Δv should equal sum of per-leg Δv."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LUNA_SHACKLETON", "LMO", 0.0)
        assert result is not None
        if result.get("legs"):
            leg_dv_sum = sum(leg["dv_m_s"] for leg in result["legs"])
            assert abs(result["total_dv_m_s"] - leg_dv_sum) < 1.0, (
                f"Total Δv {result['total_dv_m_s']:.1f} != leg sum {leg_dv_sum:.1f}"
            )

    def test_chain_total_tof_equals_leg_sum(self, seeded_db):
        """Total TOF should equal sum of per-leg TOF."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LUNA_SHACKLETON", "LMO", 0.0)
        assert result is not None
        if result.get("legs"):
            leg_tof_sum = sum(leg["tof_s"] for leg in result["legs"])
            assert abs(result["total_tof_s"] - leg_tof_sum) < 1.0, (
                f"Total TOF {result['total_tof_s']:.1f} != leg sum {leg_tof_sum:.1f}"
            )

    def test_same_location_returns_none(self, seeded_db):
        """Same src/dst → None."""
        from orbit_bridge import plan_chain_mission
        assert plan_chain_mission(seeded_db, "LEO", "LEO", 0.0) is None

    def test_unknown_location_returns_none(self, seeded_db):
        """Unknown location → None."""
        from orbit_bridge import plan_chain_mission
        assert plan_chain_mission(seeded_db, "NONEXISTENT", "LEO", 0.0) is None

    def test_chain_result_has_initial_orbit(self, seeded_db):
        """Chain mission result includes initial_orbit from leg 0."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LUNA_SHACKLETON", "LMO", 0.0)
        assert result is not None
        assert "initial_orbit" in result
        assert result["initial_orbit"] is not None
        orbit = result["initial_orbit"]
        assert "body_id" in orbit
        assert "a_km" in orbit

    def test_chain_result_has_orbit_predictions(self, seeded_db):
        """Chain mission concatenates orbit_predictions from all legs."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LUNA_SHACKLETON", "LMO", 0.0)
        assert result is not None
        preds = result.get("orbit_predictions", [])
        assert len(preds) >= 1


class TestPlanChainMissionDescend:
    """Test chain mission descent legs (gateway → inner destination)."""

    def test_leo_to_lunar_surface(self, seeded_db):
        """LEO → LUNA_SHACKLETON: single SOI leg (surface resolves to Moon body)."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LEO", "LUNA_SHACKLETON", 0.0)
        assert result is not None
        # Surface site resolves to Moon body, so this is a direct SOI transfer
        assert result["transfer_type"] == "soi_hohmann"
        assert result["total_dv_m_s"] > 0
        assert result["total_tof_s"] > 0


class TestPlanChainMissionInterplanetary:
    """Test chain missions that include interplanetary legs."""

    def test_lunar_surface_to_lmo(self, seeded_db):
        """LUNA_SHACKLETON → LMO: multi-leg chain through Earth gateway."""
        from orbit_bridge import plan_chain_mission
        result = plan_chain_mission(seeded_db, "LUNA_SHACKLETON", "LMO", 0.0)
        assert result is not None
        assert result["transfer_type"] == "chain_mission"
        assert result["leg_count"] >= 2
        legs = result["legs"]
        # First leg starts from surface
        assert legs[0]["from_id"] == "LUNA_SHACKLETON"
        # Last leg arrives at LMO
        assert legs[-1]["to_id"] == "LMO"
        # All legs connect
        for i in range(len(legs) - 1):
            assert legs[i]["to_id"] == legs[i + 1]["from_id"]


# ────────────────────────────────────────────────────────────────────
# API endpoint integration tests (require TestClient)
# ────────────────────────────────────────────────────────────────────


class TestMissionPreviewEndpoint:
    """Test GET /api/transfer/mission_preview."""

    def test_basic_preview(self, client):
        """Basic preview for LEO → GEO (single local leg)."""
        r = client.get("/api/transfer/mission_preview", params={
            "from_id": "LEO",
            "to_id": "GEO",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["from_id"] == "LEO"
        assert data["to_id"] == "GEO"
        assert data["total_dv_m_s"] > 0
        assert data["total_tof_s"] > 0
        assert data["leg_count"] >= 1
        assert data["burn_count"] >= 1
        assert "transfer_type" in data

    def test_preview_soi(self, client):
        """Preview for LEO → LLO (SOI transfer)."""
        r = client.get("/api/transfer/mission_preview", params={
            "from_id": "LEO",
            "to_id": "LLO",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["total_dv_m_s"] > 0
        assert data["total_tof_s"] > 0

    def test_preview_multi_leg(self, client):
        """Preview for LUNA_SHACKLETON → LMO (multi-leg chain)."""
        r = client.get("/api/transfer/mission_preview", params={
            "from_id": "LUNA_SHACKLETON",
            "to_id": "LMO",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["leg_count"] >= 2
        assert len(data["legs"]) >= 2
        assert data["transfer_type"] == "chain_mission"
        # Check leg structure
        for leg in data["legs"]:
            assert "index" in leg
            assert "from_id" in leg
            assert "to_id" in leg
            assert "dv_m_s" in leg
            assert "tof_s" in leg
            assert "transfer_type" in leg

    def test_preview_burns_have_leg_index(self, client):
        """Burns in multi-leg preview carry leg_index annotation."""
        r = client.get("/api/transfer/mission_preview", params={
            "from_id": "LUNA_SHACKLETON",
            "to_id": "LMO",
        })
        assert r.status_code == 200
        data = r.json()
        for burn in data["burns"]:
            assert "leg_index" in burn
            assert "time_s" in burn
            assert "prograde_m_s" in burn
            assert "label" in burn

    def test_preview_with_departure_time(self, client):
        """Preview accepts custom departure time."""
        dep = 100000.0
        r = client.get("/api/transfer/mission_preview", params={
            "from_id": "LEO",
            "to_id": "GEO",
            "departure_time": dep,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["departure_time"] == dep
        assert data["arrival_time"] > dep

    def test_preview_with_ship_fuel_check(self, client):
        """Preview with ship_id returns fuel feasibility info."""
        ship_id = "test_preview_ship"
        try:
            client.post("/api/admin/spawn_ship", json={
                "name": "Preview Ship",
                "location_id": "LEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
            })
            r = client.get("/api/transfer/mission_preview", params={
                "from_id": "LEO",
                "to_id": "GEO",
                "ship_id": ship_id,
            })
            assert r.status_code == 200
            data = r.json()
            assert "ship" in data
            ship_info = data["ship"]
            assert ship_info["ship_id"] == ship_id
            assert "dv_remaining_m_s" in ship_info
            assert "fuel_needed_kg" in ship_info
            assert "feasible" in ship_info
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")

    def test_preview_unknown_route_404(self, client):
        """Unknown route returns 404."""
        r = client.get("/api/transfer/mission_preview", params={
            "from_id": "NONEXISTENT",
            "to_id": "LEO",
        })
        assert r.status_code == 404


class TestTransferEndpointDepartureTime:
    """Test POST /api/ships/{id}/transfer with departure_time."""

    def test_transfer_with_departure_time(self, client):
        """Transfer with explicit departure_time uses that value."""
        ship_id = "test_dep_time_ship"
        try:
            client.post("/api/admin/spawn_ship", json={
                "name": "DepTime Ship",
                "location_id": "GEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
            })
            # Refuel to full
            client.post(f"/api/admin/ships/{ship_id}/refuel")

            dep = 5000.0
            r = client.post(f"/api/ships/{ship_id}/transfer", json={
                "to_location_id": "HEO",
                "departure_time": dep,
            })
            assert r.status_code == 200
            data = r.json()
            assert data["departed_at"] == dep
            assert data["arrives_at"] > dep
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")

    def test_transfer_without_departure_time(self, client):
        """Transfer without departure_time departs at game-now."""
        ship_id = "test_no_dep_time"
        try:
            client.post("/api/admin/spawn_ship", json={
                "name": "No DepTime Ship",
                "location_id": "GEO",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
            })
            client.post(f"/api/admin/ships/{ship_id}/refuel")

            r = client.post(f"/api/ships/{ship_id}/transfer", json={
                "to_location_id": "HEO",
            })
            assert r.status_code == 200
            data = r.json()
            # Should depart at approximately game-now (within a few seconds)
            assert data["departed_at"] is not None
            assert data["arrives_at"] > data["departed_at"]
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")


class TestTransferEndpointChainFallback:
    """Test POST /api/ships/{id}/transfer chain mission fallback."""

    def test_chain_mission_transfer_surface_to_orbit(self, client):
        """Transfer from lunar surface to LEO should succeed via chain mission."""
        ship_id = "test_chain_surface"
        try:
            # Spawn at lunar surface
            client.post("/api/admin/spawn_ship", json={
                "name": "Chain Ship",
                "location_id": "LUNA_SHACKLETON",
                "ship_id": ship_id,
                "parts": [
                    {"item_id": "scn_1_pioneer"},
                    {"item_id": "water_tank_10_m3"},
                ],
            })
            client.post(f"/api/admin/ships/{ship_id}/refuel")

            r = client.post(f"/api/ships/{ship_id}/transfer", json={
                "to_location_id": "LEO",
            })
            # This may succeed or fail depending on Δv budget and TWR checks.
            # We verify it doesn't return 404 (no route), which was the Phase 0 behavior.
            # It should either succeed (200) or fail with fuel/TWR check (400).
            assert r.status_code in (200, 400), (
                f"Expected 200 or 400, got {r.status_code}: {r.text}"
            )
        finally:
            client.delete(f"/api/admin/ships/{ship_id}")
