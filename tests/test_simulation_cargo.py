"""
Simulation tests — Cargo & inventory transfers.

End-to-end tests that simulate real player workflows for moving resources
and parts between ships and locations:
  • Load resources from location onto a docked ship
  • Unload resources from ship to location
  • Transfer resources between two docked ships
  • Part (stack) transfer between ship and location
  • Capacity enforcement on ship cargo
  • Water as fuel tracking
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict

import pytest

from sim_service import game_now_s
from tests.simulation_helpers import GameWorldBuilder


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def world(seeded_db: sqlite3.Connection) -> GameWorldBuilder:
    w = GameWorldBuilder(seeded_db)
    w.ensure_standard_locations()
    return w


@pytest.fixture()
def corp_and_ships(world: GameWorldBuilder):
    """
    Create a corp with two ships docked at LEO, plus resources at LEO.
    Returns (corp_id, org_id, ship_a, ship_b).
    """
    world.create_user("cargo_pilot")
    corp_id = world.create_corporation("CargoCorp", owner="cargo_pilot")
    org_id = world.get_org_id(corp_id=corp_id)

    ship_a = world.spawn_ship(
        "ship_cargo_a", "Cargo Hauler A", "LEO",
        owner_corp=corp_id,
        cargo_capacity_kg=100_000.0,
        fuel_kg=5000.0,
    )
    ship_b = world.spawn_ship(
        "ship_cargo_b", "Cargo Hauler B", "LEO",
        owner_corp=corp_id,
        cargo_capacity_kg=50_000.0,
        fuel_kg=3000.0,
    )

    # Stock LEO with resources
    world.add_resource_to_location("LEO", "water", 200_000.0, corp_id=corp_id)
    world.add_resource_to_location("LEO", "iron_oxides", 50_000.0, corp_id=corp_id)
    world.add_resource_to_location("LEO", "aluminum_oxides", 30_000.0, corp_id=corp_id)

    return corp_id, org_id, ship_a, ship_b


# ── Location → Ship Resource Transfer ─────────────────────────────────────────

class TestLocationToShipTransfer:
    """Load resources from a location onto a docked ship."""

    def test_load_water_onto_ship(self, world: GameWorldBuilder, corp_and_ships):
        """Load water from LEO onto ship A — verifies ship cargo and location deduction."""
        corp_id, org_id, ship_a, ship_b = corp_and_ships
        conn = world.conn
        import main as _main

        # Ship A starts with 5,000 kg fuel (water)
        initial_fuel = 5_000.0

        # Load 10,000 kg water onto ship
        accepted = _main.add_cargo_to_ship(conn, ship_a, "water", 10_000.0)
        conn.commit()
        assert accepted >= 10_000.0

        # Verify ship cargo (includes initial fuel since water = fuel)
        cargo = world.get_ship_cargo(ship_a)
        assert "water" in cargo
        assert cargo["water"] == pytest.approx(initial_fuel + 10_000.0)

    def test_load_is_unbounded(self, world: GameWorldBuilder, corp_and_ships):
        """Ship cargo loads are no longer capped by ship capacity."""
        corp_id, org_id, ship_a, ship_b = corp_and_ships
        conn = world.conn
        import main as _main

        # Ship B starts with 3,000 kg fuel (water)
        initial_fuel = 3_000.0

        # Try to load 80,000 kg onto a nominally 50,000 kg-capacity ship.
        accepted = _main.add_cargo_to_ship(conn, ship_b, "water", 80_000.0)
        conn.commit()

        assert accepted == pytest.approx(80_000.0)
        cargo = world.get_ship_cargo(ship_b)
        assert cargo.get("water", 0.0) == pytest.approx(initial_fuel + 80_000.0)

    def test_load_multiple_resources(self, world: GameWorldBuilder, corp_and_ships):
        """Load different resources onto the same ship."""
        corp_id, org_id, ship_a, ship_b = corp_and_ships
        conn = world.conn
        import main as _main

        # Ship A starts with 5,000 kg fuel (water)
        initial_fuel = 5_000.0

        _main.add_cargo_to_ship(conn, ship_a, "water", 20_000.0)
        _main.add_cargo_to_ship(conn, ship_a, "iron_oxides", 15_000.0)
        _main.add_cargo_to_ship(conn, ship_a, "aluminum_oxides", 5_000.0)
        conn.commit()

        cargo = world.get_ship_cargo(ship_a)
        assert cargo["water"] == pytest.approx(initial_fuel + 20_000.0)
        assert cargo["iron_oxides"] == pytest.approx(15_000.0)
        assert cargo["aluminum_oxides"] == pytest.approx(5_000.0)

        # Total cargo can exceed legacy nominal capacity.
        total = sum(cargo.values())
        assert total == pytest.approx(initial_fuel + 40_000.0)


# ── Ship → Location Resource Transfer ─────────────────────────────────────────

class TestShipToLocationTransfer:
    """Unload resources from a ship to a location."""

    def test_unload_water_from_ship(self, world: GameWorldBuilder, corp_and_ships):
        """Unload water from ship cargo to location inventory."""
        corp_id, org_id, ship_a, ship_b = corp_and_ships
        conn = world.conn
        import main as _main

        # First load some cargo
        _main.add_cargo_to_ship(conn, ship_a, "iron_oxides", 10_000.0)
        conn.commit()

        water_before = world.get_resource_mass_at_location("LEO", "iron_oxides", corp_id=corp_id)

        # Now unload 5,000 kg
        taken = _main.remove_cargo_from_ship(conn, ship_a, "iron_oxides", 5_000.0)
        _main.add_resource_to_location_inventory(conn, "LEO", "iron_oxides", taken, corp_id=corp_id)
        conn.commit()

        assert taken == pytest.approx(5_000.0)

        # Ship should have 5,000 remaining
        cargo = world.get_ship_cargo(ship_a)
        assert cargo.get("iron_oxides", 0.0) == pytest.approx(5_000.0)

        # Location should have the unloaded amount added back
        water_after = world.get_resource_mass_at_location("LEO", "iron_oxides", corp_id=corp_id)
        assert water_after == pytest.approx(water_before + 5_000.0)

    def test_unload_all_removes_stack(self, world: GameWorldBuilder, corp_and_ships):
        """Unloading all added cargo returns to the original fuel level."""
        corp_id, org_id, ship_a, ship_b = corp_and_ships
        conn = world.conn
        import main as _main

        # Ship A starts with 5,000 kg fuel (water)
        initial_fuel = 5_000.0

        _main.add_cargo_to_ship(conn, ship_a, "water", 1_000.0)
        conn.commit()

        taken = _main.remove_cargo_from_ship(conn, ship_a, "water", 1_000.0)
        conn.commit()

        assert taken == pytest.approx(1_000.0)
        cargo = world.get_ship_cargo(ship_a)
        # Water = fuel; original fuel remains
        assert cargo.get("water", 0.0) == pytest.approx(initial_fuel)


# ── Ship → Ship Resource Transfer ─────────────────────────────────────────────

class TestShipToShipTransfer:
    """Transfer resources between two docked ships at the same location."""

    def test_transfer_between_ships(self, world: GameWorldBuilder, corp_and_ships):
        """Move water from ship A to ship B."""
        corp_id, org_id, ship_a, ship_b = corp_and_ships
        conn = world.conn
        import main as _main

        # Initial fuel levels
        initial_fuel_a = 5_000.0
        initial_fuel_b = 3_000.0

        # Load water onto ship A
        _main.add_cargo_to_ship(conn, ship_a, "water", 30_000.0)
        conn.commit()

        # Transfer 10,000 kg from A to B
        taken = _main.remove_cargo_from_ship(conn, ship_a, "water", 10_000.0)
        _main.add_cargo_to_ship(conn, ship_b, "water", taken)
        conn.commit()

        cargo_a = world.get_ship_cargo(ship_a)
        cargo_b = world.get_ship_cargo(ship_b)

        assert cargo_a["water"] == pytest.approx(initial_fuel_a + 20_000.0)
        assert cargo_b["water"] == pytest.approx(initial_fuel_b + 10_000.0)

    def test_transfer_ignores_target_capacity(self, world: GameWorldBuilder, corp_and_ships):
        """Ship-to-ship transfer is not capped by legacy target capacity."""
        corp_id, org_id, ship_a, ship_b = corp_and_ships
        conn = world.conn
        import main as _main

        # Initial fuel levels
        initial_fuel_b = 3_000.0

        # Fill ship B heavily, then transfer should still accept all cargo.
        _main.add_cargo_to_ship(conn, ship_b, "iron_oxides", 45_000.0)
        conn.commit()

        # Load water on ship A
        _main.add_cargo_to_ship(conn, ship_a, "water", 20_000.0)
        conn.commit()

        # Try to transfer all 20,000 — all should be accepted.
        taken = _main.remove_cargo_from_ship(conn, ship_a, "water", 20_000.0)
        accepted = _main.add_cargo_to_ship(conn, ship_b, "water", taken)
        # Put back the excess
        if taken > accepted:
            _main.add_cargo_to_ship(conn, ship_a, "water", taken - accepted)
        conn.commit()

        assert accepted == pytest.approx(20_000.0)
        cargo_b = world.get_ship_cargo(ship_b)
        assert cargo_b.get("water", 0.0) == pytest.approx(initial_fuel_b + 20_000.0)
        total_b = sum(cargo_b.values())
        assert total_b == pytest.approx(initial_fuel_b + 65_000.0)


# ── Part (Stack) Transfers ─────────────────────────────────────────────────────

class TestPartTransfer:
    """Transfer equipment parts between ships and locations via inventory."""

    def test_add_part_to_location_and_verify(self, world: GameWorldBuilder, corp_and_ships):
        """Add a part to location inventory and verify it appears."""
        corp_id, org_id, ship_a, ship_b = corp_and_ships

        world.add_part_to_location(
            "LEO", "mgm_1a_phaethon",
            corp_id=corp_id,
            name="MGM-1A Phaethon",
            mass_kg=1200.0,
        )

        inv = world.get_location_inventory("LEO", corp_id=corp_id)
        part_stacks = [s for s in inv if s["stack_type"] == "part"]
        matching = [s for s in part_stacks if s["item_id"] == "mgm_1a_phaethon"]
        assert len(matching) >= 1
        assert float(matching[0]["quantity"]) >= 1.0

    def test_add_multiple_identical_parts_merge(self, world: GameWorldBuilder, corp_and_ships):
        """Adding identical parts should merge into the same stack."""
        corp_id, org_id, ship_a, ship_b = corp_and_ships

        for _ in range(3):
            world.add_part_to_location(
                "LEO", "ipr_1a_mold",
                corp_id=corp_id,
                name="IPR-1A Mold",
                mass_kg=2500.0,
            )

        inv = world.get_location_inventory("LEO", corp_id=corp_id)
        matching = [s for s in inv if s["item_id"] == "ipr_1a_mold"]
        assert len(matching) >= 1
        total_qty = sum(float(s["quantity"]) for s in matching)
        assert total_qty == pytest.approx(3.0)


# ── API-level Transfer Tests (via TestClient) ─────────────────────────────────

class TestCargoTransferAPI:
    """Test cargo transfers through the actual API endpoints."""

    def test_api_location_inventory(self, client, seeded_db):
        """GET /api/inventory/location/LEO returns inventory data."""
        r = client.get("/api/inventory/location/LEO")
        assert r.status_code == 200
        data = r.json()
        # Should have the inventory structure
        assert isinstance(data, dict)

    def test_api_ship_inventory_not_found(self, client):
        """GET /api/inventory/ship/nonexistent returns 404."""
        r = client.get("/api/inventory/ship/nonexistent_ship_xyz")
        assert r.status_code in (404, 400, 500)

    def test_api_transfer_requires_source_and_target(self, client):
        """POST /api/inventory/transfer with missing fields returns error."""
        r = client.post("/api/inventory/transfer", json={})
        assert r.status_code == 422

        r = client.post("/api/inventory/transfer", json={
            "source_kind": "location_resource",
            "source_id": "LEO",
            "source_key": "water",
            "target_kind": "ship",
            "target_id": "",  # missing
        })
        assert r.status_code in (400, 422)


# ── Full Cargo Workflow Simulation ─────────────────────────────────────────────

class TestFullCargoWorkflow:
    """
    Simulate a full cargo logistics cycle:
    1. Org has resources at LEO
    2. Load resources onto a ship
    3. (Simulate transit — just move ship to LMO)
    4. Unload resources at destination
    """

    def test_leo_to_lmo_cargo_run(self, world: GameWorldBuilder, corp_and_ships):
        """Full cargo run: load at LEO → transit → unload at LMO."""
        corp_id, org_id, ship_a, ship_b = corp_and_ships
        conn = world.conn
        import main as _main

        # Ship A starts with 5,000 kg fuel (water)
        initial_fuel = 5_000.0

        # Step 1: Load cargo at LEO
        _main.add_cargo_to_ship(conn, ship_a, "water", 40_000.0)
        _main.add_cargo_to_ship(conn, ship_a, "iron_oxides", 20_000.0)
        conn.commit()

        leo_water_before = world.get_resource_mass_at_location("LEO", "water", corp_id=corp_id)

        # Step 2: Simulate transit (move ship to LMO)
        conn.execute("UPDATE ships SET location_id = 'LMO' WHERE id = ?", (ship_a,))
        conn.commit()

        # Step 3: Unload at LMO
        water_taken = _main.remove_cargo_from_ship(conn, ship_a, "water", 40_000.0)
        iron_taken = _main.remove_cargo_from_ship(conn, ship_a, "iron_oxides", 20_000.0)

        _main.add_resource_to_location_inventory(conn, "LMO", "water", water_taken, corp_id=corp_id)
        _main.add_resource_to_location_inventory(conn, "LMO", "iron_oxides", iron_taken, corp_id=corp_id)
        conn.commit()

        # Verify resources at LMO
        lmo_water = world.get_resource_mass_at_location("LMO", "water", corp_id=corp_id)
        lmo_iron = world.get_resource_mass_at_location("LMO", "iron_oxides", corp_id=corp_id)
        assert lmo_water == pytest.approx(40_000.0)
        assert lmo_iron == pytest.approx(20_000.0)

        # Ship still has its initial fuel (water = fuel)
        cargo = world.get_ship_cargo(ship_a)
        assert cargo.get("water", 0.0) == pytest.approx(initial_fuel)
        assert cargo.get("iron_oxides", 0.0) < 1.0

    def test_round_trip_cargo(self, world: GameWorldBuilder, corp_and_ships):
        """Load at LEO, haul to LMO, unload, then return empty."""
        corp_id, org_id, ship_a, ship_b = corp_and_ships
        conn = world.conn
        import main as _main

        # Ship A starts with 5,000 kg fuel (water)
        initial_fuel = 5_000.0

        leo_water_initial = world.get_resource_mass_at_location("LEO", "water", corp_id=corp_id)

        # Load
        _main.add_cargo_to_ship(conn, ship_a, "water", 25_000.0)
        conn.commit()

        # Transit to LMO
        conn.execute("UPDATE ships SET location_id = 'LMO' WHERE id = ?", (ship_a,))
        conn.commit()

        # Unload
        taken = _main.remove_cargo_from_ship(conn, ship_a, "water", 25_000.0)
        _main.add_resource_to_location_inventory(conn, "LMO", "water", taken, corp_id=corp_id)
        conn.commit()

        # Return to LEO (only initial fuel remains)
        conn.execute("UPDATE ships SET location_id = 'LEO' WHERE id = ?", (ship_a,))
        conn.commit()

        cargo = world.get_ship_cargo(ship_a)
        assert cargo.get("water", 0.0) == pytest.approx(initial_fuel)

        # LMO has the water
        lmo_water = world.get_resource_mass_at_location("LMO", "water", corp_id=corp_id)
        assert lmo_water >= 25_000.0
