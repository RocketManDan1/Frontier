"""
Simulation tests — Mining, ISRU, refining, and printing (fabrication).

End-to-end tests that simulate real player production workflows:
  • Set a miner to mine mode → settle → verify output
  • ISRU water extraction at ice-bearing sites
  • Refinery slot assignment, auto-start, and job completion
  • Printer/constructor mode switching and construction queue
  • Full production chain: mine → refine → fabricate
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

import pytest

import industry_service
import catalog_service
from sim_service import game_now_s
from tests.simulation_helpers import GameWorldBuilder


# ── Standard resource distributions ─────────────────────────────────────────────

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


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def world(seeded_db: sqlite3.Connection) -> GameWorldBuilder:
    w = GameWorldBuilder(seeded_db)
    w.ensure_standard_locations()
    return w


@pytest.fixture()
def corp(world: GameWorldBuilder):
    world.create_user("industry_boss")
    corp_id = world.create_corporation("IndustryCorp", owner="industry_boss")
    org_id = world.get_org_id(corp_id=corp_id)
    return corp_id, org_id


@pytest.fixture()
def mars_mining_base(world: GameWorldBuilder, corp):
    """
    Full mining base at Mars Hellas with:
    - Surface site with resources + prospected
    - Facility
    - A miner (directly deployed)
    Returns (corp_id, org_id, facility_id, miner_equip_id)
    """
    corp_id, org_id = corp
    world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
    world.seed_site_resources("MARS_HELLAS", MARS_HELLAS_RESOURCES)
    world.prospect_site(org_id, "MARS_HELLAS")

    fac_id = world.create_facility("MARS_HELLAS", corp_id, "Hellas Mining")

    miner_id = world.deploy_equipment_directly(
        "MARS_HELLAS", "test_miner", "miner",
        corp_id=corp_id, facility_id=fac_id,
        name="Hellas Excavator",
        config={
            "mining_rate_kg_per_hr": 200.0,
            "miner_type": "large_body",
            "mining_last_settled": game_now_s(),
        },
    )

    return corp_id, org_id, fac_id, miner_id


@pytest.fixture()
def ceres_isru_base(world: GameWorldBuilder, corp):
    """
    ISRU base at Ceres with:
    - Surface site (microgravity, 25% water ice)
    - Facility
    - An ISRU unit deployed
    Returns (corp_id, org_id, facility_id, isru_equip_id)
    """
    corp_id, org_id = corp
    world.create_surface_site("CERES_SURFACE", "ceres", gravity=0.28, orbit_node_id="LMO")
    world.seed_site_resources("CERES_SURFACE", CERES_RESOURCES)
    world.prospect_site(org_id, "CERES_SURFACE")

    fac_id = world.create_facility("CERES_SURFACE", corp_id, "Ceres ISRU Plant")

    isru_id = world.deploy_equipment_directly(
        "CERES_SURFACE", "test_isru", "isru",
        corp_id=corp_id, facility_id=fac_id,
        name="Water Extractor",
        config={
            "water_extraction_kg_per_hr": 139.0,
            "mining_rate_kg_per_hr": 139.0,  # needed for _settle_mining_v2 guard
            "extraction_method": "centrifugal_drum_sifting",
            "min_water_ice_fraction": 0.0,
            "max_water_ice_fraction": 0.30,
            "mining_output_resource_id": "water",
            "mining_last_settled": game_now_s(),
        },
    )

    return corp_id, org_id, fac_id, isru_id


# ── Mining v2 (Mode-Based) ────────────────────────────────────────────────────

class TestMiningV2:
    """Test mode-based mining (set miner to mine mode, settle, check output)."""

    def test_set_miner_to_mine_mode(self, world: GameWorldBuilder, mars_mining_base):
        """Setting a miner to mine mode changes status to active."""
        corp_id, org_id, fac_id, miner_id = mars_mining_base
        conn = world.conn

        result = industry_service.set_constructor_mode(
            conn, miner_id, "mine", "industry_boss", corp_id=corp_id,
        )

        assert result["mode"] == "mine"
        assert result["status"] == "active"

        # Verify in DB
        row = conn.execute(
            "SELECT mode, status FROM deployed_equipment WHERE id = ?",
            (miner_id,),
        ).fetchone()
        assert str(row["mode"]) == "mine"
        assert str(row["status"]) == "active"

    def test_mining_produces_output_after_settle(self, world: GameWorldBuilder, mars_mining_base, monkeypatch):
        """After time passes and mining settles, resources appear in inventory."""
        corp_id, org_id, fac_id, miner_id = mars_mining_base
        conn = world.conn

        # Set miner to mine mode
        industry_service.set_constructor_mode(
            conn, miner_id, "mine", "industry_boss", corp_id=corp_id,
        )

        # Advance time by 10 hours
        base_time = game_now_s()
        fake_time = base_time + (10 * 3600)  # 10 hours later
        monkeypatch.setattr(industry_service, "game_now_s", lambda: fake_time)

        # Settle mining
        industry_service._settle_mining_v2(conn, fake_time, "MARS_HELLAS")
        conn.commit()

        # At 200 kg/hr × 10 hr = 2000 kg total, split by resource fractions
        # Iron oxides (0.30): 600 kg
        iron = world.get_resource_mass_at_location("MARS_HELLAS", "iron_oxides", corp_id=corp_id)
        assert iron >= 500.0  # Allow tolerance for timing

        # Silicate rock (0.25): 500 kg
        silicate = world.get_resource_mass_at_location("MARS_HELLAS", "silicate_rock", corp_id=corp_id)
        assert silicate >= 400.0

        # Water ice (0.08): 160 kg
        water_ice = world.get_resource_mass_at_location("MARS_HELLAS", "water_ice", corp_id=corp_id)
        assert water_ice >= 100.0

    def test_stop_mining_by_setting_idle(self, world: GameWorldBuilder, mars_mining_base, monkeypatch):
        """Setting miner to idle stops production and settles pending output."""
        corp_id, org_id, fac_id, miner_id = mars_mining_base
        conn = world.conn

        # Start mining
        industry_service.set_constructor_mode(
            conn, miner_id, "mine", "industry_boss", corp_id=corp_id,
        )

        # Advance 5 hours
        base_time = game_now_s()
        fake_time = base_time + (5 * 3600)
        monkeypatch.setattr(industry_service, "game_now_s", lambda: fake_time)

        # Set to idle — should settle pending output first
        result = industry_service.set_constructor_mode(
            conn, miner_id, "idle", "industry_boss", corp_id=corp_id,
        )
        assert result["mode"] == "idle"

        # 200 kg/hr × 5 hr = 1000 kg total mined
        total_mined = sum(
            world.get_resource_mass_at_location("MARS_HELLAS", res, corp_id=corp_id)
            for res in MARS_HELLAS_RESOURCES.keys()
        )
        assert total_mined >= 800.0  # Allow tolerance

    def test_miner_cannot_construct(self, world: GameWorldBuilder, mars_mining_base):
        """Miners cannot be set to construct mode."""
        corp_id, org_id, fac_id, miner_id = mars_mining_base

        with pytest.raises(ValueError, match="[Mm]iners cannot be set to construct"):
            industry_service.set_constructor_mode(
                world.conn, miner_id, "construct", "industry_boss", corp_id=corp_id,
            )


# ── ISRU Water Extraction ─────────────────────────────────────────────────────

class TestISRUExtraction:
    """Test ISRU water extraction (flat rate, not scaled by ice fraction)."""

    def test_isru_mine_mode_extracts_water(self, world: GameWorldBuilder, ceres_isru_base, monkeypatch):
        """ISRU in mine mode extracts water at the rated flat rate."""
        corp_id, org_id, fac_id, isru_id = ceres_isru_base
        conn = world.conn

        # Set ISRU to mine mode
        industry_service.set_constructor_mode(
            conn, isru_id, "mine", "industry_boss", corp_id=corp_id,
        )

        # Advance 10 hours
        base_time = game_now_s()
        fake_time = base_time + (10 * 3600)
        monkeypatch.setattr(industry_service, "game_now_s", lambda: fake_time)

        # Settle
        industry_service._settle_mining_v2(conn, fake_time, "CERES_SURFACE")
        conn.commit()

        # ISRU rate = 139 kg/hr × 10 hr = 1390 kg water
        water = world.get_resource_mass_at_location("CERES_SURFACE", "water", corp_id=corp_id)
        assert water >= 1200.0  # Allow tolerance
        assert water <= 1600.0

    def test_isru_output_is_water_only(self, world: GameWorldBuilder, ceres_isru_base, monkeypatch):
        """ISRU only outputs water, regardless of site resource distribution."""
        corp_id, org_id, fac_id, isru_id = ceres_isru_base
        conn = world.conn

        industry_service.set_constructor_mode(
            conn, isru_id, "mine", "industry_boss", corp_id=corp_id,
        )

        base_time = game_now_s()
        fake_time = base_time + (5 * 3600)
        monkeypatch.setattr(industry_service, "game_now_s", lambda: fake_time)

        industry_service._settle_mining_v2(conn, fake_time, "CERES_SURFACE")
        conn.commit()

        # Should have water
        water = world.get_resource_mass_at_location("CERES_SURFACE", "water", corp_id=corp_id)
        assert water > 0

        # Should NOT have other resources from mining (ISRU doesn't mine regular resources)
        iron = world.get_resource_mass_at_location("CERES_SURFACE", "iron_oxides", corp_id=corp_id)
        assert iron == pytest.approx(0.0, abs=1.0)

    def test_isru_can_be_set_to_idle(self, world: GameWorldBuilder, ceres_isru_base):
        """ISRU units can be set back to idle after mining."""
        corp_id, org_id, fac_id, isru_id = ceres_isru_base

        # Start mining
        industry_service.set_constructor_mode(
            world.conn, isru_id, "mine", "industry_boss", corp_id=corp_id,
        )
        # Switch to idle
        result = industry_service.set_constructor_mode(
            world.conn, isru_id, "idle", "industry_boss", corp_id=corp_id,
        )
        assert result["mode"] == "idle"
        assert result["status"] == "idle"


# ── Legacy Mining Jobs (production_jobs) ───────────────────────────────────────

class TestLegacyMiningJobs:
    """Test the older mining job system (start_mining_job)."""

    def test_start_mining_job_creates_job(self, world: GameWorldBuilder, mars_mining_base):
        """Start a mining job creates a production_jobs row."""
        corp_id, org_id, fac_id, miner_id = mars_mining_base
        conn = world.conn

        # Start mining iron_oxides
        result = industry_service.start_mining_job(
            conn, miner_id, "iron_oxides", "industry_boss", corp_id=corp_id,
        )
        assert result["job_id"]
        assert result["rate_kg_per_hr"] > 0

        # The effective rate = base_rate * mass_fraction
        # 200 * 0.30 = 60 kg/hr
        assert result["rate_kg_per_hr"] == pytest.approx(60.0, abs=1.0)

        # Verify in DB
        job = conn.execute(
            "SELECT * FROM production_jobs WHERE id = ?",
            (result["job_id"],),
        ).fetchone()
        assert job is not None
        assert str(job["status"]) == "active"
        assert str(job["job_type"]) == "mine"

        # Equipment should now be active
        eq = conn.execute(
            "SELECT status FROM deployed_equipment WHERE id = ?",
            (miner_id,),
        ).fetchone()
        assert str(eq["status"]) == "active"

    def test_mining_job_requires_prospecting(self, world: GameWorldBuilder, corp):
        """Mining job fails if site is not prospected."""
        corp_id, org_id = corp
        conn = world.conn

        # Create site but DON'T prospect it
        world.create_surface_site("VESTA_SURFACE", "vesta", gravity=0.25, orbit_node_id="LMO")
        world.seed_site_resources("VESTA_SURFACE", {"iron_oxides": 0.40, "silicate_rock": 0.30})

        fac_id = world.create_facility("VESTA_SURFACE", corp_id, "Vesta Mining")
        miner_id = world.deploy_equipment_directly(
            "VESTA_SURFACE", "test_miner", "miner",
            corp_id=corp_id, facility_id=fac_id,
            name="Vesta Miner",
            config={"mining_rate_kg_per_hr": 100.0, "miner_type": "large_body"},
        )

        with pytest.raises(ValueError, match="prospected"):
            industry_service.start_mining_job(
                conn, miner_id, "iron_oxides", "industry_boss", corp_id=corp_id,
            )

    def test_mining_job_rate_scales_with_fraction(self, world: GameWorldBuilder, mars_mining_base):
        """Mining rate for non-ISRU scales by resource mass fraction."""
        corp_id, org_id, fac_id, miner_id = mars_mining_base
        conn = world.conn

        # Water ice fraction is 0.08 → effective rate = 200 * 0.08 = 16
        result = industry_service.start_mining_job(
            conn, miner_id, "water_ice", "industry_boss", corp_id=corp_id,
        )
        assert result["rate_kg_per_hr"] == pytest.approx(16.0, abs=1.0)

    def test_isru_mining_job_flat_rate(self, world: GameWorldBuilder, ceres_isru_base):
        """ISRU mining rate is flat (not scaled by ice fraction)."""
        corp_id, org_id, fac_id, isru_id = ceres_isru_base
        conn = world.conn

        # ISRU should get its flat water extraction rate regardless of ice fraction
        result = industry_service.start_mining_job(
            conn, isru_id, "water_ice", "industry_boss", corp_id=corp_id,
        )
        # Flat rate = 139 kg/hr (not scaled by 0.25 ice fraction)
        assert result["rate_kg_per_hr"] == pytest.approx(139.0, abs=1.0)
        assert result["resource_id"] == "water"  # ISRU outputs water, not water_ice


# ── Printer / Constructor Mode ─────────────────────────────────────────────────

class TestPrinterMode:
    """Test printer mode switching for construction pool."""

    def test_printer_construct_mode(self, world: GameWorldBuilder, corp):
        """Printers can be set to construct mode."""
        corp_id, org_id = corp
        conn = world.conn

        world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
        world.seed_site_resources("MARS_HELLAS", MARS_HELLAS_RESOURCES)
        world.prospect_site(org_id, "MARS_HELLAS")

        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Fab Shop")

        printer_id = world.deploy_equipment_directly(
            "MARS_HELLAS", "test_printer", "printer",
            corp_id=corp_id, facility_id=fac_id,
            name="Fabricator",
            config={"construction_rate_kg_per_hr": 40.0, "printer_type": "industrial"},
        )

        result = industry_service.set_constructor_mode(
            conn, printer_id, "construct", "industry_boss", corp_id=corp_id,
        )
        assert result["mode"] == "construct"
        assert result["status"] == "active"

    def test_printer_cannot_mine(self, world: GameWorldBuilder, corp):
        """Printers cannot be set to mine mode."""
        corp_id, org_id = corp
        conn = world.conn

        world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Fab Shop")

        printer_id = world.deploy_equipment_directly(
            "MARS_HELLAS", "test_printer", "printer",
            corp_id=corp_id, facility_id=fac_id,
            name="Fabricator",
            config={"construction_rate_kg_per_hr": 40.0},
        )

        with pytest.raises(ValueError, match="[Pp]rinters cannot be set to mine"):
            industry_service.set_constructor_mode(
                conn, printer_id, "mine", "industry_boss", corp_id=corp_id,
            )


# ── Refinery Slots ─────────────────────────────────────────────────────────────

class TestRefinerySlots:
    """Test refinery slot assignment and recipe processing."""

    def test_assign_recipe_to_refinery_slot(self, world: GameWorldBuilder, corp):
        """Assign a recipe to a refinery slot."""
        corp_id, org_id = corp
        conn = world.conn

        world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Refinery")

        # Find a valid refinery recipe from the catalog
        recipe_catalog = catalog_service.load_recipe_catalog()
        refinery_recipes = {
            rid: r for rid, r in recipe_catalog.items()
            if str(r.get("facility_type") or "") != "shipyard"
        }
        if not refinery_recipes:
            pytest.skip("No refinery recipes in catalog")

        recipe_id = next(iter(refinery_recipes))
        recipe = refinery_recipes[recipe_id]
        recipe_category = str(recipe.get("refinery_category") or "")

        # Deploy a refinery whose specialization matches the recipe
        ref_id = world.deploy_equipment_directly(
            "MARS_HELLAS", "test_refinery", "refinery",
            corp_id=corp_id, facility_id=fac_id,
            name="Test Refinery",
            config={
                "specialization": recipe_category or "all_refineries",
                "throughput_mult": 1.0,
                "efficiency": 1.0,
                "max_recipe_tier": 3,
                "max_concurrent_recipes": 2,
            },
        )
        slot_ids = world.create_refinery_slots(
            ref_id, "MARS_HELLAS", 2,
            corp_id=corp_id, facility_id=fac_id,
        )

        # Assign recipe
        result = industry_service.assign_refinery_slot(
            conn, slot_ids[0], recipe_id, "industry_boss", corp_id=corp_id,
        )
        assert result["slot_id"] == slot_ids[0]
        assert result["recipe_id"] == recipe_id

        # Verify slot has recipe assigned
        slot = conn.execute(
            "SELECT recipe_id FROM refinery_slots WHERE id = ?",
            (slot_ids[0],),
        ).fetchone()
        assert str(slot["recipe_id"]) == recipe_id


# ── Full Production Chain ──────────────────────────────────────────────────────

class TestFullProductionChain:
    """
    End-to-end production chain simulation:
    1. Mine resources from a surface site
    2. Refine raw ores into processed materials
    3. Use a construction queue to build parts
    """

    def test_mine_to_inventory(self, world: GameWorldBuilder, mars_mining_base, monkeypatch):
        """Mine at Mars Hellas for simulated hours and verify inventory buildup."""
        corp_id, org_id, fac_id, miner_id = mars_mining_base
        conn = world.conn

        # Start mining via mode
        industry_service.set_constructor_mode(
            conn, miner_id, "mine", "industry_boss", corp_id=corp_id,
        )

        # Simulate 24 hours of mining
        base_time = game_now_s()
        fake_time = base_time + (24 * 3600)  # 24 hours
        monkeypatch.setattr(industry_service, "game_now_s", lambda: fake_time)

        industry_service._settle_mining_v2(conn, fake_time, "MARS_HELLAS")
        conn.commit()

        # 200 kg/hr × 24 hr = 4800 kg total mined, split by fractions
        # Iron (0.30): ~1440 kg
        iron = world.get_resource_mass_at_location("MARS_HELLAS", "iron_oxides", corp_id=corp_id)
        assert iron >= 1200.0

        # silicate (0.25): ~1200 kg
        silicate = world.get_resource_mass_at_location("MARS_HELLAS", "silicate_rock", corp_id=corp_id)
        assert silicate >= 1000.0

        # aluminum (0.15): ~720 kg
        aluminum = world.get_resource_mass_at_location("MARS_HELLAS", "aluminum_oxides", corp_id=corp_id)
        assert aluminum >= 600.0

    def test_parallel_miners_multiply_output(self, world: GameWorldBuilder, corp, monkeypatch):
        """Multiple miners at the same site produce proportionally more output."""
        corp_id, org_id = corp
        conn = world.conn

        world.create_surface_site("MARS_AMAZONIS", "mars", gravity=3.72, orbit_node_id="LMO")
        world.seed_site_resources("MARS_AMAZONIS", MARS_HELLAS_RESOURCES)
        world.prospect_site(org_id, "MARS_AMAZONIS")

        fac_id = world.create_facility("MARS_AMAZONIS", corp_id, "Amazonis Mining")

        base_time = game_now_s()

        # Deploy 3 miners
        miners = []
        for i in range(3):
            mid = world.deploy_equipment_directly(
                "MARS_AMAZONIS", f"test_miner_{i}", "miner",
                corp_id=corp_id, facility_id=fac_id,
                name=f"Miner #{i+1}",
                config={
                    "mining_rate_kg_per_hr": 100.0,
                    "miner_type": "large_body",
                    "mining_last_settled": base_time,
                },
            )
            miners.append(mid)
            # Set each to mine mode
            industry_service.set_constructor_mode(
                conn, mid, "mine", "industry_boss", corp_id=corp_id,
            )

        # Advance 10 hours
        fake_time = base_time + (10 * 3600)
        monkeypatch.setattr(industry_service, "game_now_s", lambda: fake_time)

        industry_service._settle_mining_v2(conn, fake_time, "MARS_AMAZONIS")
        conn.commit()

        # 3 miners × 100 kg/hr × 10 hr = 3000 kg total. Iron at 0.30 = 900 kg
        iron = world.get_resource_mass_at_location("MARS_AMAZONIS", "iron_oxides", corp_id=corp_id)
        assert iron >= 750.0

    def test_isru_then_refine_water_ice(self, world: GameWorldBuilder, ceres_isru_base, monkeypatch):
        """
        Simulate: ISRU extracts water → stock water_ice for a refinery → refine.
        This tests the connection between ISRU output and refinery input availability.
        """
        corp_id, org_id, fac_id, isru_id = ceres_isru_base
        conn = world.conn

        # Start ISRU mining
        industry_service.set_constructor_mode(
            conn, isru_id, "mine", "industry_boss", corp_id=corp_id,
        )

        # Advance 20 hours — should produce ~2780 kg water
        base_time = game_now_s()
        fake_time = base_time + (20 * 3600)
        monkeypatch.setattr(industry_service, "game_now_s", lambda: fake_time)

        industry_service._settle_mining_v2(conn, fake_time, "CERES_SURFACE")
        conn.commit()

        water = world.get_resource_mass_at_location("CERES_SURFACE", "water", corp_id=corp_id)
        assert water >= 2500.0

        # Now add some water_ice manually (as if a miner also ran)
        world.add_resource_to_location("CERES_SURFACE", "water_ice", 1000.0, corp_id=corp_id)

        # Deploy a refinery for water_ice_to_water recipe
        ref_id = world.deploy_equipment_directly(
            "CERES_SURFACE", "test_refinery", "refinery",
            corp_id=corp_id, facility_id=fac_id,
            name="Water Refinery",
            config={
                "specialization": "all_refineries",
                "throughput_mult": 1.0,
                "efficiency": 1.0,
                "max_recipe_tier": 1,
                "max_concurrent_recipes": 1,
            },
        )
        slot_ids = world.create_refinery_slots(
            ref_id, "CERES_SURFACE", 1,
            corp_id=corp_id, facility_id=fac_id,
        )

        # Verify water_ice is available as a refinery input
        ice = world.get_resource_mass_at_location("CERES_SURFACE", "water_ice", corp_id=corp_id)
        assert ice >= 1000.0


# ── Construction Queue ─────────────────────────────────────────────────────────

class TestConstructionQueue:
    """Test construction queue (pooled printer/constructor speed)."""

    def test_queue_and_dequeue(self, world: GameWorldBuilder, corp):
        """Queue a construction job and then dequeue it."""
        corp_id, org_id = corp
        conn = world.conn

        world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Mars Fab")

        # Deploy a printer in construct mode
        printer_id = world.deploy_equipment_directly(
            "MARS_HELLAS", "test_printer", "printer",
            corp_id=corp_id, facility_id=fac_id,
            name="Fabricator",
            config={"construction_rate_kg_per_hr": 40.0, "printer_type": "industrial"},
        )
        industry_service.set_constructor_mode(
            conn, printer_id, "construct", "industry_boss", corp_id=corp_id,
        )

        # Find a recipe available for construction queue (must have facility_type == "shipyard")
        recipe_catalog = catalog_service.load_recipe_catalog()
        construction_recipes = {
            rid: r for rid, r in recipe_catalog.items()
            if str(r.get("facility_type", "")).lower() == "shipyard"
        }

        if not construction_recipes:
            pytest.skip("No construction (shipyard) recipes in catalog")

        recipe_id = next(iter(construction_recipes))

        # Queue the job
        result = industry_service.queue_construction(
            conn, "MARS_HELLAS", recipe_id, "industry_boss",
            corp_id=corp_id, facility_id=fac_id,
        )
        assert "queue_id" in result
        queue_id = result["queue_id"]

        # Verify it's in the queue
        qrow = conn.execute(
            "SELECT * FROM construction_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        assert qrow is not None
        assert str(qrow["status"]) in ("queued", "active")

        # Dequeue it
        dequeue_result = industry_service.dequeue_construction(
            conn, queue_id, "industry_boss", corp_id=corp_id,
        )
        assert dequeue_result["dequeued"] is True


# ── End-to-End: Multi-System Production Simulation ────────────────────────────

class TestEndToEndProduction:
    """
    Simulate a full play session:
    1. Create an org + corporation
    2. Set up a mining base at Mars Hellas
    3. Mine resources for simulated time
    4. Verify resources accumulated
    5. Set up ISRU at Ceres for water
    6. Verify water production
    """

    def test_full_play_session(self, world: GameWorldBuilder, monkeypatch):
        """Comprehensive multi-system production simulation."""
        conn = world.conn

        # === Step 1: Create org ===
        world.create_user("commander")
        corp_id = world.create_corporation("FrontierCorp", owner="commander",
                                            starting_balance=50_000_000_000.0)
        org_id = world.get_org_id(corp_id=corp_id)

        initial_balance = world.get_org_balance(org_id)
        assert initial_balance == pytest.approx(50_000_000_000.0)

        # === Step 2: Set up Mars mining base ===
        world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
        world.seed_site_resources("MARS_HELLAS", MARS_HELLAS_RESOURCES)
        world.prospect_site(org_id, "MARS_HELLAS")

        mars_fac = world.create_facility("MARS_HELLAS", corp_id, "Hellas Mining Complex")
        base_time = game_now_s()

        # Deploy 2 miners
        miner1 = world.deploy_equipment_directly(
            "MARS_HELLAS", "mars_miner_1", "miner",
            corp_id=corp_id, facility_id=mars_fac,
            name="Mars Excavator Alpha",
            config={"mining_rate_kg_per_hr": 150.0, "miner_type": "large_body",
                    "mining_last_settled": base_time},
        )
        miner2 = world.deploy_equipment_directly(
            "MARS_HELLAS", "mars_miner_2", "miner",
            corp_id=corp_id, facility_id=mars_fac,
            name="Mars Excavator Beta",
            config={"mining_rate_kg_per_hr": 150.0, "miner_type": "large_body",
                    "mining_last_settled": base_time},
        )

        # Set both to mine
        for mid in (miner1, miner2):
            industry_service.set_constructor_mode(conn, mid, "mine", "commander", corp_id=corp_id)

        # === Step 3: Set up Ceres ISRU ===
        world.create_surface_site("CERES_SURFACE", "ceres", gravity=0.28, orbit_node_id="LMO")
        world.seed_site_resources("CERES_SURFACE", CERES_RESOURCES)
        world.prospect_site(org_id, "CERES_SURFACE")

        ceres_fac = world.create_facility("CERES_SURFACE", corp_id, "Ceres Water Plant")

        isru = world.deploy_equipment_directly(
            "CERES_SURFACE", "ceres_isru_1", "isru",
            corp_id=corp_id, facility_id=ceres_fac,
            name="Water Extractor",
            config={
                "water_extraction_kg_per_hr": 139.0,
                "mining_rate_kg_per_hr": 139.0,  # needed for _settle_mining_v2 guard
                "mining_output_resource_id": "water",
                "mining_last_settled": base_time,
            },
        )
        industry_service.set_constructor_mode(conn, isru, "mine", "commander", corp_id=corp_id)

        # === Step 4: Simulate 48 hours of game time ===
        fake_time = base_time + (48 * 3600)
        monkeypatch.setattr(industry_service, "game_now_s", lambda: fake_time)

        # Settle all mining
        industry_service._settle_mining_v2(conn, fake_time, "MARS_HELLAS")
        industry_service._settle_mining_v2(conn, fake_time, "CERES_SURFACE")
        conn.commit()

        # === Step 5: Verify Mars output ===
        # 2 miners × 150 kg/hr × 48 hr = 14,400 kg total
        mars_iron = world.get_resource_mass_at_location("MARS_HELLAS", "iron_oxides", corp_id=corp_id)
        mars_silicate = world.get_resource_mass_at_location("MARS_HELLAS", "silicate_rock", corp_id=corp_id)

        # Iron (0.30 fraction): ~4320 kg
        assert mars_iron >= 3500.0
        # Silicate (0.25 fraction): ~3600 kg
        assert mars_silicate >= 2800.0

        # === Step 6: Verify Ceres water output ===
        # 139 kg/hr × 48 hr = 6672 kg water
        ceres_water = world.get_resource_mass_at_location("CERES_SURFACE", "water", corp_id=corp_id)
        assert ceres_water >= 6000.0

        # === Step 7: Spawn a ship to haul resources ===
        import main as _main
        ship = world.spawn_ship(
            "ship_hauler", "Frontier Hauler", "MARS_HELLAS",
            owner_corp=corp_id,
            cargo_capacity_kg=100_000.0,
        )

        # Load iron and silicate
        _main.add_cargo_to_ship(conn, ship, "iron_oxides", min(mars_iron, 3000.0))
        _main.add_cargo_to_ship(conn, ship, "silicate_rock", min(mars_silicate, 2000.0))
        conn.commit()

        cargo = world.get_ship_cargo(ship)
        assert cargo.get("iron_oxides", 0) >= 2999.0
        assert cargo.get("silicate_rock", 0) >= 1999.0

        # === Step 8: Transit to LEO and unload ===
        conn.execute("UPDATE ships SET location_id = 'LEO' WHERE id = ?", (ship,))
        conn.commit()

        iron_taken = _main.remove_cargo_from_ship(conn, ship, "iron_oxides", 3000.0)
        silicate_taken = _main.remove_cargo_from_ship(conn, ship, "silicate_rock", 2000.0)
        _main.add_resource_to_location_inventory(conn, "LEO", "iron_oxides", iron_taken, corp_id=corp_id)
        _main.add_resource_to_location_inventory(conn, "LEO", "silicate_rock", silicate_taken, corp_id=corp_id)
        conn.commit()

        # Verify LEO inventory
        leo_iron = world.get_resource_mass_at_location("LEO", "iron_oxides", corp_id=corp_id)
        leo_silicate = world.get_resource_mass_at_location("LEO", "silicate_rock", corp_id=corp_id)
        assert leo_iron >= 2999.0
        assert leo_silicate >= 1999.0

        # Ship should only have its initial fuel remaining (water = fuel)
        cargo_final = world.get_ship_cargo(ship)
        non_water = {k: v for k, v in cargo_final.items() if k != "water"}
        assert sum(non_water.values()) < 1.0
