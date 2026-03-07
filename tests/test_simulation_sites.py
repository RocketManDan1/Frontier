"""
Simulation tests — Sites, facilities, and equipment deployment.

End-to-end tests that simulate real player workflows for:
  • Creating facilities at surface sites
  • Deploying equipment (miners, printers, refineries, ISRU, reactors, etc.)
  • Validating deployment constraints (surface-only, gravity, ice fraction)
  • Facility lifecycle (create, rename, delete)
  • Multi-facility per location
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

import pytest

import industry_service
import facility_service
from sim_service import game_now_s
from tests.simulation_helpers import GameWorldBuilder


# ── Standard site resource distributions ────────────────────────────────────────

MARS_HELLAS_RESOURCES = {
    "iron_oxides": 0.30,
    "aluminum_oxides": 0.15,
    "silicate_rock": 0.25,
    "water_ice": 0.08,
    "magnesium_oxides": 0.10,
    "titanium_oxides": 0.05,
    "carbon_volatiles": 0.04,
    "nitrogen_volatiles": 0.03,
}

CERES_RESOURCES = {
    "water_ice": 0.25,
    "carbon_volatiles": 0.15,
    "iron_oxides": 0.20,
    "silicate_rock": 0.20,
    "nitrogen_volatiles": 0.10,
    "magnesium_oxides": 0.10,
}

VESTA_RESOURCES = {
    "iron_oxides": 0.40,
    "silicate_rock": 0.25,
    "magnesium_oxides": 0.15,
    "aluminum_oxides": 0.10,
    "titanium_oxides": 0.05,
    "carbon_volatiles": 0.03,
    "water_ice": 0.02,
}


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def world(seeded_db: sqlite3.Connection) -> GameWorldBuilder:
    w = GameWorldBuilder(seeded_db)
    w.ensure_standard_locations()
    return w


@pytest.fixture()
def corp(world: GameWorldBuilder):
    """Corp with org. Returns (corp_id, org_id)."""
    world.create_user("site_manager")
    corp_id = world.create_corporation("SiteCorp", owner="site_manager")
    org_id = world.get_org_id(corp_id=corp_id)
    return corp_id, org_id


@pytest.fixture()
def mars_site(world: GameWorldBuilder, corp):
    """Mars Hellas surface site with resources and prospecting done."""
    corp_id, org_id = corp
    world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
    world.seed_site_resources("MARS_HELLAS", MARS_HELLAS_RESOURCES)
    world.prospect_site(org_id, "MARS_HELLAS")
    return "MARS_HELLAS"


@pytest.fixture()
def ceres_site(world: GameWorldBuilder, corp):
    """Ceres surface site (microgravity) with resources and prospecting done."""
    corp_id, org_id = corp
    world.create_surface_site("CERES_SURFACE", "ceres", gravity=0.28, orbit_node_id="LMO")
    world.seed_site_resources("CERES_SURFACE", CERES_RESOURCES)
    world.prospect_site(org_id, "CERES_SURFACE")
    return "CERES_SURFACE"


# ── Facility Lifecycle ─────────────────────────────────────────────────────────

class TestFacilityLifecycle:
    """Creating, renaming, and deleting facilities."""

    def test_create_facility(self, world: GameWorldBuilder, corp, mars_site):
        corp_id, org_id = corp
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Hellas Base Alpha")

        # Verify it exists
        fac = facility_service.resolve_facility(world.conn, fac_id)
        assert fac["name"] == "Hellas Base Alpha"
        assert fac["location_id"] == "MARS_HELLAS"
        assert fac["corp_id"] == corp_id

    def test_multiple_facilities_per_location(self, world: GameWorldBuilder, corp, mars_site):
        """A corp can have multiple named facilities at the same location."""
        corp_id, org_id = corp
        fac1 = world.create_facility("MARS_HELLAS", corp_id, "Mining Hub")
        fac2 = world.create_facility("MARS_HELLAS", corp_id, "Refinery Complex")
        fac3 = world.create_facility("MARS_HELLAS", corp_id, "Power Station")

        assert fac1 != fac2 != fac3

        # All should show up
        facs = facility_service.list_facilities_at_location(
            world.conn, "MARS_HELLAS", viewer_corp_id=corp_id,
        )
        names = {f["name"] for f in facs}
        assert "Mining Hub" in names
        assert "Refinery Complex" in names
        assert "Power Station" in names

    def test_different_corps_at_same_location(self, world: GameWorldBuilder, mars_site):
        """Two different corps can both have facilities at the same site."""
        world.create_user("alpha_boss")
        world.create_user("beta_boss")
        corp_a = world.create_corporation("AlphaMining", owner="alpha_boss")
        corp_b = world.create_corporation("BetaMining", owner="beta_boss")
        org_a = world.get_org_id(corp_id=corp_a)
        org_b = world.get_org_id(corp_id=corp_b)

        # Prospect for both orgs
        world.prospect_site(org_a, "MARS_HELLAS")
        world.prospect_site(org_b, "MARS_HELLAS")

        fac_a = world.create_facility("MARS_HELLAS", corp_a, "Alpha Outpost")
        fac_b = world.create_facility("MARS_HELLAS", corp_b, "Beta Outpost")

        facs = facility_service.list_facilities_at_location(
            world.conn, "MARS_HELLAS", viewer_is_admin=True,
        )
        assert len(facs) >= 2

    def test_delete_empty_facility(self, world: GameWorldBuilder, corp, mars_site):
        """Can delete a facility with no equipment or active jobs."""
        corp_id, org_id = corp
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Temp Base")

        # Should succeed
        world.conn.execute("DELETE FROM facilities WHERE id = ?", (fac_id,))
        world.conn.commit()

        fac = world.conn.execute("SELECT 1 FROM facilities WHERE id = ?", (fac_id,)).fetchone()
        assert fac is None


# ── Equipment Deployment ───────────────────────────────────────────────────────

class TestEquipmentDeployment:
    """Deploy equipment from location inventory to a facility."""

    def test_deploy_miner_at_surface_site(self, world: GameWorldBuilder, corp, mars_site):
        """Deploy a microgravity miner at a low-gravity site (Ceres-like)."""
        corp_id, org_id = corp

        # Create a microgravity site
        world.create_surface_site("CERES_SURFACE", "ceres", gravity=0.28, orbit_node_id="LMO")
        world.seed_site_resources("CERES_SURFACE", CERES_RESOURCES)
        world.prospect_site(org_id, "CERES_SURFACE")

        fac_id = world.create_facility("CERES_SURFACE", corp_id, "Ceres Mining")

        # Add a microgravity miner to inventory
        world.add_part_to_location("CERES_SURFACE", "mgm_1a_phaethon", corp_id=corp_id)

        # Deploy it
        result = industry_service.deploy_equipment(
            world.conn, "CERES_SURFACE", "mgm_1a_phaethon",
            "site_manager", corp_id=corp_id, facility_id=fac_id,
        )

        assert result["category"] == "miner"
        assert result["status"] == "idle"
        assert result["facility_id"] == fac_id

        # Equipment should be in deployed_equipment
        count = world.count_deployed_equipment("CERES_SURFACE", category="miner")
        assert count >= 1

    def test_deploy_printer_at_surface_site(self, world: GameWorldBuilder, corp, mars_site):
        """Deploy an industrial printer at Mars Hellas."""
        corp_id, org_id = corp
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Fabrication")

        world.add_part_to_location("MARS_HELLAS", "ipr_1a_mold", corp_id=corp_id)

        result = industry_service.deploy_equipment(
            world.conn, "MARS_HELLAS", "ipr_1a_mold",
            "site_manager", corp_id=corp_id, facility_id=fac_id,
        )

        assert result["category"] == "printer"
        assert result["status"] == "idle"

    def test_deploy_refinery_creates_slots(self, world: GameWorldBuilder, corp, mars_site):
        """Deploying a refinery auto-creates refinery slots."""
        corp_id, org_id = corp
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Refinery")

        # Add a refinery to inventory (use catalog to get real item_id)
        import catalog_service
        ref_catalog = catalog_service.load_refinery_catalog()
        if not ref_catalog:
            pytest.skip("No refineries in catalog")

        ref_id = next(iter(ref_catalog))
        world.add_part_to_location("MARS_HELLAS", ref_id, corp_id=corp_id)

        result = industry_service.deploy_equipment(
            world.conn, "MARS_HELLAS", ref_id,
            "site_manager", corp_id=corp_id, facility_id=fac_id,
        )
        assert result["category"] == "refinery"

        # Check refinery slots were created
        slots = world.conn.execute(
            "SELECT * FROM refinery_slots WHERE equipment_id = ?",
            (result["id"],),
        ).fetchall()
        assert len(slots) >= 1

    def test_deploy_isru_at_ice_bearing_site(self, world: GameWorldBuilder, corp, ceres_site):
        """Deploy an ISRU unit at a site with appropriate water ice fraction."""
        corp_id, org_id = corp
        fac_id = world.create_facility("CERES_SURFACE", corp_id, "ISRU Plant")

        # Check available ISRU items
        import catalog_service
        isru_catalog = catalog_service.load_isru_catalog()
        if not isru_catalog:
            pytest.skip("No ISRU units in catalog")

        # Find an ISRU compatible with Ceres' 25% water ice
        compatible = None
        for isru_id, entry in isru_catalog.items():
            min_ice = float(entry.get("min_water_ice_fraction") or 0.0)
            max_ice = float(entry.get("max_water_ice_fraction") or 1.0)
            if min_ice <= 0.25 <= max_ice:
                compatible = isru_id
                break

        if not compatible:
            pytest.skip("No ISRU compatible with 25% ice fraction")

        world.add_part_to_location("CERES_SURFACE", compatible, corp_id=corp_id)

        result = industry_service.deploy_equipment(
            world.conn, "CERES_SURFACE", compatible,
            "site_manager", corp_id=corp_id, facility_id=fac_id,
        )
        assert result["category"] == "isru"

    def test_deploy_miner_at_orbital_fails(self, world: GameWorldBuilder, corp):
        """Miners cannot be deployed at orbital locations (non-surface)."""
        corp_id, org_id = corp
        world.add_part_to_location("LEO", "mgm_1a_phaethon", corp_id=corp_id)

        with pytest.raises(ValueError, match="surface"):
            industry_service.deploy_equipment(
                world.conn, "LEO", "mgm_1a_phaethon",
                "site_manager", corp_id=corp_id,
            )

    def test_deploy_reactor_at_orbital(self, world: GameWorldBuilder, corp):
        """Reactors CAN be deployed at orbital locations."""
        corp_id, org_id = corp
        import catalog_service
        reactor_catalog = catalog_service.load_reactor_catalog()
        if not reactor_catalog:
            pytest.skip("No reactors in catalog")

        reactor_id = next(iter(reactor_catalog))
        world.add_part_to_location("LEO", reactor_id, corp_id=corp_id)

        # Reactors should be deployable at orbital locations
        fac_id = world.create_facility("LEO", corp_id, "LEO Power Station")

        result = industry_service.deploy_equipment(
            world.conn, "LEO", reactor_id,
            "site_manager", corp_id=corp_id, facility_id=fac_id,
        )
        assert result["category"] == "reactor"


# ── Undeploy Equipment ─────────────────────────────────────────────────────────

class TestEquipmentUndeploy:
    """Undeploy equipment returns it to inventory."""

    def test_undeploy_idle_equipment(self, world: GameWorldBuilder, corp, mars_site):
        """Undeploying idle equipment restores part to inventory."""
        corp_id, org_id = corp
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Temp Mining")

        world.add_part_to_location("MARS_HELLAS", "ipr_1a_mold", corp_id=corp_id)

        deployed = industry_service.deploy_equipment(
            world.conn, "MARS_HELLAS", "ipr_1a_mold",
            "site_manager", corp_id=corp_id, facility_id=fac_id,
        )

        # Now undeploy
        result = industry_service.undeploy_equipment(
            world.conn, deployed["id"], "site_manager", corp_id=corp_id,
        )
        assert result["undeployed"] is True

        # Equipment should be gone
        count = world.count_deployed_equipment("MARS_HELLAS", facility_id=fac_id)
        assert count == 0

        # Part should be back in inventory
        inv = world.get_location_inventory("MARS_HELLAS", corp_id=corp_id)
        parts = [s for s in inv if s["item_id"] == "ipr_1a_mold"]
        assert len(parts) >= 1


# ── Full Site Setup Workflow ───────────────────────────────────────────────────

class TestFullSiteSetup:
    """
    Simulate a player setting up a full mining operation at a surface site:
    1. Create facility
    2. Deploy power infrastructure (reactor + generator + radiator)
    3. Deploy mining equipment
    4. Deploy refinery
    5. Verify complete setup
    """

    def test_full_mining_base_setup(self, world: GameWorldBuilder, corp, mars_site):
        """End-to-end: build a complete mining base at Mars Hellas."""
        corp_id, org_id = corp
        conn = world.conn

        # 1. Create facilities
        mining_fac = world.create_facility("MARS_HELLAS", corp_id, "Mining Operations")
        refinery_fac = world.create_facility("MARS_HELLAS", corp_id, "Refinery Complex")

        # 2. Deploy equipment directly (bypassing inventory for speed)
        # Power: reactor
        reactor_id = world.deploy_equipment_directly(
            "MARS_HELLAS", "test_reactor", "reactor",
            corp_id=corp_id, facility_id=mining_fac,
            name="Test Reactor", config={"thermal_mw": 10.0},
        )

        # Power: generator
        gen_id = world.deploy_equipment_directly(
            "MARS_HELLAS", "test_generator", "generator",
            corp_id=corp_id, facility_id=mining_fac,
            name="Test Generator",
            config={"thermal_mw_input": 10.0, "electric_mw": 5.0, "waste_heat_mw": 5.0},
        )

        # Thermal: radiator
        rad_id = world.deploy_equipment_directly(
            "MARS_HELLAS", "test_radiator", "radiator",
            corp_id=corp_id, facility_id=mining_fac,
            name="Test Radiator", config={"heat_rejection_mw": 10.0},
        )

        # Miner
        miner_id = world.deploy_equipment_directly(
            "MARS_HELLAS", "test_miner", "miner",
            corp_id=corp_id, facility_id=mining_fac,
            name="Test Miner",
            config={"mining_rate_kg_per_hr": 100.0, "miner_type": "large_body"},
        )

        # Refinery
        ref_id = world.deploy_equipment_directly(
            "MARS_HELLAS", "test_refinery", "refinery",
            corp_id=corp_id, facility_id=refinery_fac,
            name="Test Refinery",
            config={
                "specialization": "metallurgy",
                "throughput_mult": 1.0,
                "efficiency": 0.95,
                "max_recipe_tier": 2,
                "max_concurrent_recipes": 2,
            },
        )

        # 3. Verify the full setup
        mining_count = world.count_deployed_equipment("MARS_HELLAS", facility_id=mining_fac)
        refinery_count = world.count_deployed_equipment("MARS_HELLAS", facility_id=refinery_fac)

        assert mining_count == 4  # reactor, generator, radiator, miner
        assert refinery_count == 1  # refinery

        # Total equipment at location
        total = world.count_deployed_equipment("MARS_HELLAS")
        assert total == 5

    def test_sites_api_shows_equipment(self, world: GameWorldBuilder, corp, mars_site):
        """GET /api/sites should reflect the deployed equipment count."""
        corp_id, org_id = corp
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Check Site")

        world.deploy_equipment_directly(
            "MARS_HELLAS", "test_miner", "miner",
            corp_id=corp_id, facility_id=fac_id,
            name="Miner", config={"mining_rate_kg_per_hr": 50.0},
        )

        # Query deployed equipment directly
        equip = industry_service.get_deployed_equipment(world.conn, "MARS_HELLAS", facility_id=fac_id)
        assert len(equip) >= 1
        assert any(e["category"] == "miner" for e in equip)
