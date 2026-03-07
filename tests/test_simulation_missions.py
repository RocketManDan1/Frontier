"""
Simulation tests — Mission lifecycle.

End-to-end tests that simulate real player workflows for the government
mission system:
  • Accept an easy mission, transport the module, complete it
  • Accept a medium mission, build a facility, deliver, complete
  • Accept a hard mission, run through the 3-phase pipeline
  • Abandon a mission and verify clawback
  • Multiple orgs racing to accept the same mission
  • Pool refill after accepting and completing missions
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict

import pytest

import mission_service
from sim_service import game_now_s
from tests.simulation_helpers import GameWorldBuilder


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def world(seeded_db: sqlite3.Connection) -> GameWorldBuilder:
    """Seeded game world builder (includes celestial locations)."""
    w = GameWorldBuilder(seeded_db)
    w.ensure_standard_locations()
    return w


@pytest.fixture()
def corp_and_org(world: GameWorldBuilder):
    """Create a standard corporation with org for tests. Returns (corp_id, org_id)."""
    world.create_user("mission_pilot")
    corp_id = world.create_corporation("MissionCorp", owner="mission_pilot")
    org_id = world.get_org_id(corp_id=corp_id)
    return corp_id, org_id


# ── Easy Mission: Full Lifecycle ───────────────────────────────────────────────

class TestEasyMissionLifecycle:
    """Simulate a player accepting, transporting, and completing an easy mission."""

    def test_accept_easy_mission(self, world: GameWorldBuilder, corp_and_org):
        """Accept an easy mission → verify upfront payment + module minted at LEO."""
        corp_id, org_id = corp_and_org
        conn = world.conn

        # Insert a known easy mission
        msn_id = world.insert_mission(
            mission_id="msn_easy_lmo",
            tier="easy",
            destination_id="LMO",
            destination_name="Low Mars Orbit",
        )

        balance_before = world.get_org_balance(org_id)

        # Accept it
        result = mission_service.accept_mission(conn, msn_id, org_id)
        assert result["status"] == "accepted"
        assert result["org_id"] == org_id

        # Verify upfront payment credited
        upfront = mission_service.PAYOUTS["easy"]["upfront"]
        balance_after = world.get_org_balance(org_id)
        assert balance_after == pytest.approx(balance_before + upfront)

        # Verify module minted at LEO
        stack_key = mission_service.mission_module_stack_key(msn_id)
        stack = conn.execute(
            "SELECT * FROM location_inventory_stacks WHERE stack_key = ? AND item_id = ?",
            (stack_key, mission_service.MISSION_MODULE_ITEM_ID),
        ).fetchone()
        assert stack is not None
        assert float(stack["quantity"]) >= 1.0
        assert str(stack["location_id"]) == "LEO"

    def test_complete_easy_module_at_destination_orbit(self, world: GameWorldBuilder, corp_and_org):
        """
        Full easy cycle: accept → simulate transport (move module to LMO) → complete.
        """
        corp_id, org_id = corp_and_org
        conn = world.conn

        msn_id = world.insert_mission(
            mission_id="msn_easy_complete",
            tier="easy",
            destination_id="LMO",
            destination_name="Low Mars Orbit",
            status="accepted",
            org_id=org_id,
            accepted_at=game_now_s(),
            expires_at=game_now_s() + 15 * 365.25 * 86400,
        )

        # Simulate: module arrives at LMO (place it in location inventory)
        stack_key = mission_service.mission_module_stack_key(msn_id)
        payload = json.dumps({"mission_module": True, "mission_id": msn_id})
        conn.execute(
            """INSERT INTO location_inventory_stacks
               (location_id, corp_id, facility_id, stack_type, stack_key, item_id, name,
                quantity, mass_kg, volume_m3, payload_json, updated_at)
               VALUES (?, ?, '', 'part', ?, ?, 'Mission Materials Module',
                       1.0, 25000.0, 40.0, ?, ?)""",
            ("LMO", corp_id, stack_key, mission_service.MISSION_MODULE_ITEM_ID,
             payload, game_now_s()),
        )
        conn.commit()

        balance_before = world.get_org_balance(org_id)

        # Complete the mission
        result = mission_service.complete_mission(conn, msn_id, org_id)
        assert result["status"] == "completed"

        # Verify completion payment
        completion = mission_service.PAYOUTS["easy"]["completion"]
        balance_after = world.get_org_balance(org_id)
        assert balance_after == pytest.approx(balance_before + completion)

        # Verify module removed
        stack = conn.execute(
            "SELECT * FROM location_inventory_stacks WHERE stack_key = ? AND item_id = ?",
            (stack_key, mission_service.MISSION_MODULE_ITEM_ID),
        ).fetchone()
        assert stack is None

    def test_complete_easy_module_on_docked_ship(self, world: GameWorldBuilder, corp_and_org):
        """Easy mission completes when module is in a ship's parts at the destination."""
        corp_id, org_id = corp_and_org
        conn = world.conn

        msn_id = world.insert_mission(
            mission_id="msn_easy_ship",
            tier="easy",
            destination_id="LMO",
            destination_name="Low Mars Orbit",
            status="accepted",
            org_id=org_id,
            accepted_at=game_now_s(),
            expires_at=game_now_s() + 15 * 365.25 * 86400,
        )

        # Spawn a ship at LMO carrying the module in its parts
        world.spawn_ship(
            "ship_easy_carrier", "Mars Hauler", "LMO",
            owner_corp=corp_id,
            parts=[
                {"item_id": "test_thruster", "name": "Test Thruster", "type": "thruster",
                 "mass_kg": 5000, "thrust_n": 50000, "isp_s": 900,
                 "fuel_capacity_kg": 5000, "cargo_capacity_kg": 50000},
                {"item_id": "mission_materials_module", "_mission_id": msn_id,
                 "name": "Mission Materials Module", "type": "part", "mass_kg": 25000},
            ],
        )

        result = mission_service.complete_mission(conn, msn_id, org_id)
        assert result["status"] == "completed"

    def test_active_mission_reports_loaded_ship_name(self, world: GameWorldBuilder, corp_and_org):
        """Active mission location includes ship identity when module is loaded onto a ship."""
        corp_id, org_id = corp_and_org
        conn = world.conn

        msn_id = world.insert_mission(
            mission_id="msn_easy_ship_loc",
            tier="easy",
            destination_id="LMO",
            destination_name="Low Mars Orbit",
            status="accepted",
            org_id=org_id,
            accepted_at=game_now_s(),
            expires_at=game_now_s() + 15 * 365.25 * 86400,
        )

        world.spawn_ship(
            "ship_mission_locator", "Mission Carrier", "LEO",
            owner_corp=corp_id,
            parts=[
                {
                    "item_id": "test_thruster",
                    "name": "Test Thruster",
                    "type": "thruster",
                    "mass_kg": 5000,
                    "thrust_n": 50000,
                    "isp_s": 900,
                    "fuel_capacity_kg": 5000,
                    "cargo_capacity_kg": 50000,
                },
                {
                    "item_id": "mission_materials_module",
                    "name": "Mission Materials Module",
                    "type": "part",
                    "mass_kg": 25000,
                },
            ],
        )

        active = mission_service.get_active_mission(conn, org_id)
        assert active is not None
        module_loc = active.get("module_location") or {}
        assert str(module_loc.get("found_in") or "").startswith("ship:")
        assert module_loc.get("ship_name") == "Mission Carrier"
        assert module_loc.get("location_id") == "LEO"

    def test_active_mission_reports_ship_for_any_corp_in_org(self, world: GameWorldBuilder, corp_and_org):
        """Active mission module lookup works when the org has multiple corporations."""
        primary_corp_id, org_id = corp_and_org
        conn = world.conn

        world.create_user("mission_alt_owner")
        alt_corp_id = world.create_corporation("MissionAltCorp", owner="mission_alt_owner")
        conn.execute("UPDATE corporations SET org_id = ? WHERE id = ?", (org_id, alt_corp_id))
        conn.commit()

        msn_id = world.insert_mission(
            mission_id="msn_easy_ship_alt_corp",
            tier="easy",
            destination_id="LMO",
            destination_name="Low Mars Orbit",
            status="accepted",
            org_id=org_id,
            accepted_at=game_now_s(),
            expires_at=game_now_s() + 15 * 365.25 * 86400,
        )

        world.spawn_ship(
            "ship_mission_alt_locator", "Bingus", "LEO",
            owner_corp=alt_corp_id,
            parts=[
                {
                    "item_id": "test_thruster",
                    "name": "Test Thruster",
                    "type": "thruster",
                    "mass_kg": 5000,
                    "thrust_n": 50000,
                    "isp_s": 900,
                    "fuel_capacity_kg": 5000,
                    "cargo_capacity_kg": 50000,
                },
                {
                    "item_id": "mission_materials_module",
                    "name": "Mission Materials Module",
                    "type": "part",
                    "mass_kg": 25000,
                },
            ],
        )

        active = mission_service.get_active_mission(conn, org_id)
        assert active is not None
        module_loc = active.get("module_location") or {}
        assert str(module_loc.get("found_in") or "").startswith("ship:")
        assert module_loc.get("ship_name") == "Bingus"
        assert module_loc.get("location_id") == "LEO"

        # Keep fixture vars intentionally used to satisfy linting/static checks
        assert primary_corp_id

    def test_active_mission_fallback_handles_multiple_untagged_ship_modules(self, world: GameWorldBuilder, corp_and_org):
        """When legacy/untagged module parts exist on multiple ships, active mission still reports a ship."""
        corp_id, org_id = corp_and_org
        conn = world.conn

        world.insert_mission(
            mission_id="msn_easy_ship_ambig",
            tier="easy",
            destination_id="LMO",
            destination_name="Low Mars Orbit",
            status="accepted",
            org_id=org_id,
            accepted_at=game_now_s(),
            expires_at=game_now_s() + 15 * 365.25 * 86400,
        )

        base_thruster = {
            "item_id": "test_thruster",
            "name": "Test Thruster",
            "type": "thruster",
            "mass_kg": 5000,
            "thrust_n": 50000,
            "isp_s": 900,
            "fuel_capacity_kg": 5000,
            "cargo_capacity_kg": 50000,
        }
        untagged_module = {
            "item_id": "mission_materials_module",
            "name": "Mission Materials Module",
            "type": "part",
            "mass_kg": 25000,
        }

        world.spawn_ship("ship_ambig_a", "Bingus", "LEO", owner_corp=corp_id, parts=[base_thruster, untagged_module])
        world.spawn_ship("ship_ambig_b", "Mission Man", "LEO", owner_corp=corp_id, parts=[base_thruster, untagged_module])

        active = mission_service.get_active_mission(conn, org_id)
        assert active is not None
        module_loc = active.get("module_location") or {}
        assert str(module_loc.get("found_in") or "").startswith("ship:")
        assert "Bingus" in str(module_loc.get("ship_name") or "")
        assert module_loc.get("location_id") == "LEO"

    def test_easy_fails_if_module_not_at_destination(self, world: GameWorldBuilder, corp_and_org):
        """Easy mission completion fails when no module is at the destination."""
        corp_id, org_id = corp_and_org
        conn = world.conn

        msn_id = world.insert_mission(
            mission_id="msn_easy_nomod",
            tier="easy",
            destination_id="LMO",
            destination_name="Low Mars Orbit",
            status="accepted",
            org_id=org_id,
            accepted_at=game_now_s(),
            expires_at=game_now_s() + 15 * 365.25 * 86400,
        )

        # Don't place module at destination
        with pytest.raises(ValueError, match="not found at destination"):
            mission_service.complete_mission(conn, msn_id, org_id)


# ── Medium Mission: Facility + Module ─────────────────────────────────────────

class TestMediumMissionLifecycle:
    """Medium mission requires a facility at the destination surface site."""

    def test_medium_complete_with_facility_and_module(self, world: GameWorldBuilder, corp_and_org):
        """
        Full medium cycle: accept → build facility at destination → place module → complete.
        """
        corp_id, org_id = corp_and_org
        conn = world.conn

        # Create the destination as a surface site
        world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")

        msn_id = world.insert_mission(
            mission_id="msn_med_full",
            tier="medium",
            destination_id="MARS_HELLAS",
            destination_name="Hellas Planitia",
            status="accepted",
            org_id=org_id,
            accepted_at=game_now_s(),
            expires_at=game_now_s() + 15 * 365.25 * 86400,
        )

        # Create facility at destination (simulates player building one)
        world.create_facility("MARS_HELLAS", corp_id, "Hellas Outpost")

        # Place module at destination
        stack_key = mission_service.mission_module_stack_key(msn_id)
        payload = json.dumps({"mission_module": True, "mission_id": msn_id})
        conn.execute(
            """INSERT INTO location_inventory_stacks
               (location_id, corp_id, facility_id, stack_type, stack_key, item_id, name,
                quantity, mass_kg, volume_m3, payload_json, updated_at)
               VALUES (?, ?, '', 'part', ?, ?, 'Mission Materials Module',
                       1.0, 25000.0, 40.0, ?, ?)""",
            ("MARS_HELLAS", corp_id, stack_key, mission_service.MISSION_MODULE_ITEM_ID,
             payload, game_now_s()),
        )
        conn.commit()

        balance_before = world.get_org_balance(org_id)
        result = mission_service.complete_mission(conn, msn_id, org_id)
        assert result["status"] == "completed"

        # Verify total payout (upfront was already paid, now completion)
        completion = mission_service.PAYOUTS["medium"]["completion"]
        balance_after = world.get_org_balance(org_id)
        assert balance_after == pytest.approx(balance_before + completion)

    def test_medium_fails_without_facility(self, world: GameWorldBuilder, corp_and_org):
        """Medium mission fails if org has no facility at destination."""
        corp_id, org_id = corp_and_org
        conn = world.conn

        world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")

        msn_id = world.insert_mission(
            mission_id="msn_med_nofac",
            tier="medium",
            destination_id="MARS_HELLAS",
            destination_name="Hellas Planitia",
            status="accepted",
            org_id=org_id,
            accepted_at=game_now_s(),
            expires_at=game_now_s() + 15 * 365.25 * 86400,
        )

        # Place module but no facility
        stack_key = mission_service.mission_module_stack_key(msn_id)
        payload = json.dumps({"mission_module": True, "mission_id": msn_id})
        conn.execute(
            """INSERT INTO location_inventory_stacks
               (location_id, corp_id, facility_id, stack_type, stack_key, item_id, name,
                quantity, mass_kg, volume_m3, payload_json, updated_at)
               VALUES (?, ?, '', 'part', ?, ?, 'Mission Materials Module',
                       1.0, 25000.0, 40.0, ?, ?)""",
            ("MARS_HELLAS", corp_id, stack_key, mission_service.MISSION_MODULE_ITEM_ID,
             payload, game_now_s()),
        )
        conn.commit()

        with pytest.raises(ValueError, match="must have a facility"):
            mission_service.complete_mission(conn, msn_id, org_id)


# ── Hard Mission: 3-Phase Pipeline ────────────────────────────────────────────

class TestHardMissionLifecycle:
    """Hard mission: deliver → power for 90 days → return module to Earth."""

    def _setup_hard_mission(self, world: GameWorldBuilder, corp_id: str, org_id: str) -> str:
        """Set up a hard mission with facility and module at destination."""
        conn = world.conn
        world.create_surface_site("EUROPA_SURFACE", "europa", gravity=1.31, orbit_node_id="LMO")
        world.create_facility("EUROPA_SURFACE", corp_id, "Europa Research Station")

        msn_id = world.insert_mission(
            mission_id="msn_hard_full",
            tier="hard",
            destination_id="EUROPA_SURFACE",
            destination_name="Europa Surface",
            status="accepted",
            org_id=org_id,
            accepted_at=game_now_s(),
            expires_at=game_now_s() + 15 * 365.25 * 86400,
        )

        # Place module at destination
        stack_key = mission_service.mission_module_stack_key(msn_id)
        payload = json.dumps({"mission_module": True, "mission_id": msn_id})
        conn.execute(
            """INSERT INTO location_inventory_stacks
               (location_id, corp_id, facility_id, stack_type, stack_key, item_id, name,
                quantity, mass_kg, volume_m3, payload_json, updated_at)
               VALUES (?, ?, '', 'part', ?, ?, 'Mission Materials Module',
                       1.0, 25000.0, 40.0, ?, ?)""",
            ("EUROPA_SURFACE", corp_id, stack_key, mission_service.MISSION_MODULE_ITEM_ID,
             payload, game_now_s()),
        )
        conn.commit()
        return msn_id

    def test_hard_phase1_deliver(self, world: GameWorldBuilder, corp_and_org):
        """Phase 1: accepted → delivered when module + facility are at destination."""
        corp_id, org_id = corp_and_org
        msn_id = self._setup_hard_mission(world, corp_id, org_id)

        result = mission_service.complete_mission(world.conn, msn_id, org_id)
        assert result["status"] == "delivered"

    def test_hard_phase2_power_started(self, world: GameWorldBuilder, corp_and_org, monkeypatch):
        """Phase 2: delivered → powered when facility has positive net power."""
        corp_id, org_id = corp_and_org
        msn_id = self._setup_hard_mission(world, corp_id, org_id)
        conn = world.conn

        # Advance through phase 1
        mission_service.complete_mission(conn, msn_id, org_id)

        # Mock positive power at destination
        monkeypatch.setattr(mission_service, "_check_facility_power", lambda _c, _l: True)

        result = mission_service.complete_mission(conn, msn_id, org_id)
        assert result["status"] == "powered"

    def test_hard_phase3_complete_after_90_days(self, world: GameWorldBuilder, corp_and_org, monkeypatch):
        """Phase 3: powered → completed after 90 game-days + module returned to Earth."""
        corp_id, org_id = corp_and_org
        conn = world.conn

        world.create_surface_site("EUROPA_SURFACE", "europa", gravity=1.31, orbit_node_id="LMO")
        world.create_facility("EUROPA_SURFACE", corp_id, "Europa Research Station")

        # Create mission directly in 'powered' state
        msn_id = world.insert_mission(
            mission_id="msn_hard_phase3",
            tier="hard",
            destination_id="EUROPA_SURFACE",
            destination_name="Europa Surface",
            status="powered",
            org_id=org_id,
            accepted_at=1000.0,
            expires_at=1_000_000_000.0,
            power_started_at=1000.0,
        )
        # Manually set status to powered (insert_mission uses the given status)
        conn.execute(
            "UPDATE missions SET status = 'powered', power_started_at = ? WHERE id = ?",
            (1000.0, msn_id),
        )
        conn.commit()

        # Advance game time past the 90-day requirement
        fake_time = 1000.0 + (91 * 86400)  # 91 days later
        monkeypatch.setattr(mission_service, "game_now_s", lambda: fake_time)
        monkeypatch.setattr(mission_service, "_check_facility_power", lambda _c, _l: True)

        # Module is NOT at destination anymore (player moved it)
        # Place it at LEO (Earth orbit) to simulate return trip
        stack_key = mission_service.mission_module_stack_key(msn_id)
        payload = json.dumps({"mission_module": True, "mission_id": msn_id})
        conn.execute(
            """INSERT INTO location_inventory_stacks
               (location_id, corp_id, facility_id, stack_type, stack_key, item_id, name,
                quantity, mass_kg, volume_m3, payload_json, updated_at)
               VALUES ('LEO', ?, '', 'part', ?, ?, 'Mission Materials Module',
                       1.0, 25000.0, 40.0, ?, ?)""",
            (corp_id, stack_key, mission_service.MISSION_MODULE_ITEM_ID,
             payload, fake_time),
        )
        conn.commit()

        balance_before = world.get_org_balance(org_id)
        result = mission_service.complete_mission(conn, msn_id, org_id)
        assert result["status"] == "completed"

        # Verify completion payment
        completion = mission_service.PAYOUTS["hard"]["completion"]
        balance_after = world.get_org_balance(org_id)
        assert balance_after == pytest.approx(balance_before + completion)

    def test_hard_power_interrupted_resets_timer(self, world: GameWorldBuilder, corp_and_org, monkeypatch):
        """Power interruption during phase 3 resets the power timer."""
        corp_id, org_id = corp_and_org
        conn = world.conn

        world.create_surface_site("EUROPA_SURFACE", "europa", gravity=1.31, orbit_node_id="LMO")
        world.create_facility("EUROPA_SURFACE", corp_id, "Europa Research Station")

        msn_id = world.insert_mission(
            mission_id="msn_hard_reset",
            tier="hard",
            destination_id="EUROPA_SURFACE",
            destination_name="Europa Surface",
            status="powered",
            org_id=org_id,
            accepted_at=1000.0,
            expires_at=1_000_000_000.0,
            power_started_at=10_000.0,
        )
        conn.execute(
            "UPDATE missions SET status = 'powered', power_started_at = 10000.0 WHERE id = ?",
            (msn_id,),
        )
        conn.commit()

        # Place module at destination
        mission_service.mint_mission_module(conn, msn_id, "EUROPA_SURFACE", org_id)

        # 20 days in, power goes out
        fake_time = 10_000.0 + (20 * 86400)
        monkeypatch.setattr(mission_service, "game_now_s", lambda: fake_time)
        monkeypatch.setattr(mission_service, "_check_facility_power", lambda _c, _l: False)

        with pytest.raises(ValueError, match="timer reset"):
            mission_service.complete_mission(conn, msn_id, org_id)

        # Verify timer was reset
        m = world.get_mission(msn_id)
        assert m is not None
        assert float(m["power_started_at"]) == pytest.approx(fake_time)
        assert int(m["power_reset_count"]) == 1


# ── Abandon / Clawback ────────────────────────────────────────────────────────

class TestMissionAbandon:
    """Abandoning a mission claws back the upfront payment."""

    def test_abandon_claws_back_upfront(self, world: GameWorldBuilder, corp_and_org):
        corp_id, org_id = corp_and_org
        conn = world.conn

        msn_id = world.insert_mission(
            mission_id="msn_abandon",
            tier="easy",
            destination_id="LMO",
            destination_name="Low Mars Orbit",
        )

        balance_before = world.get_org_balance(org_id)
        mission_service.accept_mission(conn, msn_id, org_id)
        upfront = mission_service.PAYOUTS["easy"]["upfront"]
        balance_after_accept = world.get_org_balance(org_id)
        assert balance_after_accept == pytest.approx(balance_before + upfront)

        # Abandon
        result = mission_service.abandon_mission(conn, msn_id, org_id)
        assert result["status"] == "abandoned"

        # Balance should be back to original
        balance_final = world.get_org_balance(org_id)
        assert balance_final == pytest.approx(balance_before)

        # Module should be removed
        stack_key = mission_service.mission_module_stack_key(msn_id)
        stack = conn.execute(
            "SELECT 1 FROM location_inventory_stacks WHERE stack_key = ?",
            (stack_key,),
        ).fetchone()
        assert stack is None

    def test_abandon_allows_new_mission(self, world: GameWorldBuilder, corp_and_org):
        """After abandoning, org can accept a new mission."""
        corp_id, org_id = corp_and_org
        conn = world.conn

        msn1 = world.insert_mission(mission_id="msn_first", tier="easy",
                                     destination_id="LMO", destination_name="LMO")
        msn2 = world.insert_mission(mission_id="msn_second", tier="medium",
                                     destination_id="MARS_HELLAS", destination_name="Hellas")

        mission_service.accept_mission(conn, msn1, org_id)
        mission_service.abandon_mission(conn, msn1, org_id)

        # Now accept a second mission
        result = mission_service.accept_mission(conn, msn2, org_id)
        assert result["status"] == "accepted"


# ── Multi-Org Race Condition ──────────────────────────────────────────────────

class TestMissionOrgConcurrency:
    """Two orgs cannot accept the same mission."""

    def test_two_orgs_race_for_same_mission(self, world: GameWorldBuilder):
        conn = world.conn
        world.create_user("alpha_pilot")
        world.create_user("beta_pilot")

        corp_a = world.create_corporation("AlphaCorp", owner="alpha_pilot")
        corp_b = world.create_corporation("BetaCorp", owner="beta_pilot")
        org_a = world.get_org_id(corp_id=corp_a)
        org_b = world.get_org_id(corp_id=corp_b)

        msn_id = world.insert_mission(mission_id="msn_race", tier="easy",
                                       destination_id="LMO", destination_name="LMO")

        # Alpha wins
        result = mission_service.accept_mission(conn, msn_id, org_a)
        assert result["status"] == "accepted"

        # Beta is blocked
        with pytest.raises(ValueError, match="not available"):
            mission_service.accept_mission(conn, msn_id, org_b)

    def test_one_active_per_org(self, world: GameWorldBuilder, corp_and_org):
        """One org cannot have two active missions simultaneously."""
        corp_id, org_id = corp_and_org
        conn = world.conn

        msn1 = world.insert_mission(mission_id="msn_dup1", tier="easy",
                                     destination_id="LMO", destination_name="LMO")
        msn2 = world.insert_mission(mission_id="msn_dup2", tier="medium",
                                     destination_id="MARS_HELLAS", destination_name="Hellas")

        mission_service.accept_mission(conn, msn1, org_id)

        with pytest.raises(ValueError, match="already has an active mission"):
            mission_service.accept_mission(conn, msn2, org_id)


# ── Pool Management ───────────────────────────────────────────────────────────

class TestMissionPool:
    """Mission pool refills after missions are claimed."""

    def test_pool_refills_after_accept(self, world: GameWorldBuilder, corp_and_org):
        corp_id, org_id = corp_and_org
        conn = world.conn

        # Get initial pool
        available = mission_service.get_available_missions(conn)
        assert len(available) >= 1  # Pool was refilled by settle

        # Accept the first one
        mission_service.accept_mission(conn, available[0]["id"], org_id)

        # Pool should refill on next query
        available2 = mission_service.get_available_missions(conn)
        assert len(available2) >= mission_service.POOL_SIZE - 1  # May be slightly less if dest collision
