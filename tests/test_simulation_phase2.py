"""
Phase-2 simulation tests to close realism gaps:

- Real printer production completion (start_production_job -> settle -> outputs delivered)
- Construction queue auto-start and completion (not just queue/dequeue)
- Mission API accept/complete flow on a deterministic overridden DB
- Industry API deploy/mode/details flow on overridden DB
- Site-claim transfer gate (403 when another corp has a refinery at destination)
- No-teleport travel flow via transfer endpoint and arrival settlement
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Tuple
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth_service
import catalog_service
import db
import industry_service
import mission_service
from main import app
from sim_service import game_now_s
from tests.simulation_helpers import GameWorldBuilder


@pytest.fixture()
def world(seeded_db: sqlite3.Connection) -> GameWorldBuilder:
    w = GameWorldBuilder(seeded_db)
    w.ensure_standard_locations()
    return w


@pytest.fixture()
def api_db_conn(tmp_path) -> sqlite3.Connection:
    """Thread-safe sqlite connection for API tests (used by TestClient worker threads)."""
    from db_migrations import apply_migrations

    db_path = tmp_path / "phase2_api.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")

    apply_migrations(conn)
    project_root = Path(__file__).resolve().parent.parent
    schema_sql = (project_root / "db" / "schema.sql").read_text()
    seed_sql = (project_root / "db" / "seed.sql").read_text()
    conn.executescript(schema_sql)
    conn.executescript(seed_sql)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def api_world(api_db_conn: sqlite3.Connection) -> GameWorldBuilder:
    w = GameWorldBuilder(api_db_conn)
    w.ensure_standard_locations()
    return w


@pytest.fixture()
def api_client_db(api_db_conn: sqlite3.Connection):
    """TestClient bound to the seeded in-memory DB via dependency override."""

    def _override_get_db():
        yield api_db_conn

    app.dependency_overrides[db.get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _enable_corp_auth(client: TestClient, conn: sqlite3.Connection, corp_id: str, monkeypatch) -> str:
    """Disable DEV auth bypass and attach a corp session cookie to client."""
    monkeypatch.setattr(auth_service, "DEV_SKIP_AUTH", False)
    token = auth_service.create_corp_session(conn, corp_id)
    conn.commit()
    client.cookies.set(auth_service.SESSION_COOKIE_NAME, token)
    return token


def _pick_shipyard_recipe() -> Tuple[str, Dict[str, Any]]:
    recipes = catalog_service.load_recipe_catalog()
    for recipe_id, recipe in recipes.items():
        if str(recipe.get("facility_type") or "").lower() == "shipyard":
            return recipe_id, recipe
    raise RuntimeError("No shipyard recipe found in catalog")


def _seed_recipe_inputs(world: GameWorldBuilder, location_id: str, corp_id: str, recipe: Dict[str, Any], mult: float = 1.0) -> None:
    for inp in (recipe.get("inputs") or []):
        item_id = str(inp.get("item_id") or "").strip()
        qty = float(inp.get("qty") or 0.0) * mult
        if item_id and qty > 0:
            world.add_resource_to_location(location_id, item_id, qty + 50.0, corp_id=corp_id)


class TestPrintingCompletion:
    def test_start_production_job_completes_and_delivers_output(self, world: GameWorldBuilder, monkeypatch):
        """Real printer job completion path: consume input, complete later, output delivered to inventory."""
        conn = world.conn
        world.create_user("printboss")
        corp_id = world.create_corporation("PrintCorp", owner="printboss")

        world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Printer Bay")

        printer_id = world.deploy_equipment_directly(
            "MARS_HELLAS",
            "ipr_sim_1",
            "printer",
            corp_id=corp_id,
            facility_id=fac_id,
            name="Simulation Printer",
            config={"construction_rate_kg_per_hr": 50.0, "printer_type": "ship", "max_concurrent_recipes": 1},
        )

        recipe_id, recipe = _pick_shipyard_recipe()
        _seed_recipe_inputs(world, "MARS_HELLAS", corp_id, recipe)

        output_item_id = str(recipe.get("output_item_id") or "").strip()
        output_qty = float(recipe.get("output_qty") or 0.0)
        assert output_item_id and output_qty > 0

        result = industry_service.start_production_job(
            conn,
            printer_id,
            recipe_id,
            "printboss",
            batch_count=1,
            corp_id=corp_id,
        )
        assert result["job_id"]

        done_time = float(result["completes_at"]) + 1.0
        monkeypatch.setattr(industry_service, "game_now_s", lambda: done_time)
        industry_service._settle_production_jobs(conn, done_time, "MARS_HELLAS")

        job = conn.execute("SELECT status FROM production_jobs WHERE id = ?", (result["job_id"],)).fetchone()
        assert job is not None
        assert str(job["status"]) == "completed"

        eq = conn.execute("SELECT status FROM deployed_equipment WHERE id = ?", (printer_id,)).fetchone()
        assert eq is not None
        assert str(eq["status"]) == "idle"

        inventory = world.get_location_inventory("MARS_HELLAS", corp_id=corp_id)
        out_stacks = [s for s in inventory if s["item_id"] == output_item_id]
        assert out_stacks, f"Expected output stack for {output_item_id}"

    def test_construction_queue_auto_start_and_complete(self, world: GameWorldBuilder, monkeypatch):
        """Queue item should auto-start with construct-mode printer and complete with delivered output."""
        conn = world.conn
        world.create_user("queueboss")
        corp_id = world.create_corporation("QueueCorp", owner="queueboss")

        world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Queue Fab")

        printer_id = world.deploy_equipment_directly(
            "MARS_HELLAS",
            "ipr_sim_2",
            "printer",
            corp_id=corp_id,
            facility_id=fac_id,
            name="Queue Printer",
            config={"construction_rate_kg_per_hr": 60.0, "printer_type": "ship"},
        )
        industry_service.set_constructor_mode(conn, printer_id, "construct", "queueboss", corp_id=corp_id)

        recipe_id, recipe = _pick_shipyard_recipe()
        _seed_recipe_inputs(world, "MARS_HELLAS", corp_id, recipe)

        queued = industry_service.queue_construction(
            conn,
            "MARS_HELLAS",
            recipe_id,
            "queueboss",
            corp_id=corp_id,
            facility_id=fac_id,
        )
        qid = queued["queue_id"]

        now = game_now_s()
        industry_service._settle_construction_queue(conn, now, "MARS_HELLAS")

        active = conn.execute(
            "SELECT status, completes_at FROM construction_queue WHERE id = ?",
            (qid,),
        ).fetchone()
        assert active is not None
        assert str(active["status"]) == "active"

        done_time = float(active["completes_at"]) + 1.0
        monkeypatch.setattr(industry_service, "game_now_s", lambda: done_time)
        industry_service._settle_construction_queue(conn, done_time, "MARS_HELLAS")

        completed = conn.execute("SELECT status FROM construction_queue WHERE id = ?", (qid,)).fetchone()
        assert completed is not None
        assert str(completed["status"]) == "completed"


class TestApiFlows:
    def test_mission_api_accept_and_complete(self, api_client_db: TestClient, api_world: GameWorldBuilder, monkeypatch):
        """Mission API flow with corp auth: accept mission then complete after placing module at destination."""
        conn = api_world.conn
        api_world.create_user("missioncorp")
        corp_id = api_world.create_corporation("MissionCorp", owner="missioncorp")
        org_id = api_world.get_org_id(corp_id=corp_id)

        _enable_corp_auth(api_client_db, conn, corp_id, monkeypatch)

        mission_id = api_world.insert_mission(
            mission_id="phase2_msn_easy",
            tier="easy",
            destination_id="LMO",
            destination_name="Low Mars Orbit",
            status="available",
        )

        resp_accept = api_client_db.post(f"/api/missions/{mission_id}/accept")
        assert resp_accept.status_code == 200
        payload = resp_accept.json()
        assert payload["mission"]["status"] == "accepted"

        stack_key = mission_service.mission_module_stack_key(mission_id)
        conn.execute(
            "UPDATE location_inventory_stacks SET location_id = 'LMO' WHERE corp_id = ? AND stack_type = 'part' AND stack_key = ?",
            (corp_id, stack_key),
        )
        conn.commit()

        resp_complete = api_client_db.post(f"/api/missions/{mission_id}/complete")
        assert resp_complete.status_code == 200
        payload2 = resp_complete.json()
        assert payload2["mission"]["status"] == "completed"

    def test_industry_api_deploy_mode_and_overview(self, api_client_db: TestClient, api_world: GameWorldBuilder, monkeypatch):
        """Industry API flow: deploy from inventory, set mode, and verify overview reflects active equipment."""
        conn = api_world.conn
        api_world.create_user("indcorp")
        corp_id = api_world.create_corporation("IndustryApiCorp", owner="indcorp")
        org_id = api_world.get_org_id(corp_id=corp_id)

        _enable_corp_auth(api_client_db, conn, corp_id, monkeypatch)

        api_world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
        api_world.seed_site_resources("MARS_HELLAS", {"iron_oxides": 0.4, "silicate_rock": 0.3, "water_ice": 0.1})
        api_world.prospect_site(org_id, "MARS_HELLAS")
        fac_id = api_world.create_facility("MARS_HELLAS", corp_id, "Api Facility")

        api_world.add_part_to_location("MARS_HELLAS", "ipr_1a_mold", corp_id=corp_id)

        resp_deploy = api_client_db.post(
            "/api/industry/deploy",
            json={"location_id": "MARS_HELLAS", "item_id": "ipr_1a_mold", "facility_id": fac_id},
        )
        assert resp_deploy.status_code == 200
        deploy_payload = resp_deploy.json()
        assert deploy_payload["ok"] is True
        equipment_id = deploy_payload["id"]

        resp_mode = api_client_db.post(
            "/api/industry/constructor/mode",
            json={"equipment_id": equipment_id, "mode": "construct"},
        )
        assert resp_mode.status_code == 200
        assert resp_mode.json()["mode"] == "construct"

        resp_overview = api_client_db.get("/api/industry/MARS_HELLAS")
        assert resp_overview.status_code == 200
        overview = resp_overview.json()
        assert any(str(eq.get("id")) == equipment_id for eq in overview.get("equipment", []))


class TestClaimGateAndNoTeleport:
    def test_transfer_blocked_by_site_claim_gate(self, api_client_db: TestClient, api_world: GameWorldBuilder, monkeypatch):
        """Corp B cannot transfer onto a surface site where Corp A has a refinery deployed."""
        conn = api_world.conn

        api_world.create_user("corp_a")
        corp_a = api_world.create_corporation("ClaimantCorp", owner="corp_a")
        api_world.create_user("corp_b")
        corp_b = api_world.create_corporation("VisitorCorp", owner="corp_b")

        api_world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
        fac_a = api_world.create_facility("MARS_HELLAS", corp_a, "Claim Refinery")

        # Ensure transfer planner has an explicit route to this test site.
        conn.execute(
            "INSERT OR REPLACE INTO transfer_edges (from_id, to_id, dv_m_s, tof_s) VALUES (?, ?, ?, ?)",
            ("LMO", "MARS_HELLAS", 1200.0, 7200.0),
        )
        conn.execute(
            "INSERT OR REPLACE INTO transfer_edges (from_id, to_id, dv_m_s, tof_s) VALUES (?, ?, ?, ?)",
            ("MARS_HELLAS", "LMO", 1200.0, 7200.0),
        )
        conn.commit()

        api_world.deploy_equipment_directly(
            "MARS_HELLAS",
            "claim_refinery",
            "refinery",
            corp_id=corp_a,
            facility_id=fac_a,
            name="Claim Anchor Refinery",
            config={"specialization": "metallurgy", "throughput_mult": 1.0, "efficiency": 1.0},
        )

        strong_parts = [{
            "item_id": "test_thruster_heavy",
            "name": "Heavy Thruster",
            "type": "thruster",
            "mass_kg": 10000,
            "thrust_kn": 300.0,
            "isp_s": 900.0,
            "water_kg": 12000.0,
            "cargo_capacity_kg": 50000,
        }]
        ship_id = api_world.spawn_ship(
            "ship_claim_test",
            "Claim Gate Runner",
            "LMO",
            owner_corp=corp_b,
            parts=strong_parts,
            fuel_kg=8000,
            fuel_capacity_kg=10000,
        )

        _enable_corp_auth(api_client_db, conn, corp_b, monkeypatch)

        resp = api_client_db.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "MARS_HELLAS"})
        assert resp.status_code == 403, resp.text
        assert "claimed by another corporation" in str(resp.json().get("detail", "")).lower()

    def test_no_teleport_transfer_and_arrival_settlement(self, api_client_db: TestClient, api_world: GameWorldBuilder, monkeypatch):
        """Ship must move via transfer endpoint and arrive later via settle_arrivals, not instant teleport."""
        conn = api_world.conn

        api_world.create_user("navcorp")
        corp_id = api_world.create_corporation("NavigatorCorp", owner="navcorp")
        _enable_corp_auth(api_client_db, conn, corp_id, monkeypatch)

        conn.execute(
            "INSERT OR REPLACE INTO transfer_edges (from_id, to_id, dv_m_s, tof_s) VALUES (?, ?, ?, ?)",
            ("LEO", "GEO", 3900.0, 21600.0),
        )
        conn.execute(
            "INSERT OR REPLACE INTO transfer_edges (from_id, to_id, dv_m_s, tof_s) VALUES (?, ?, ?, ?)",
            ("GEO", "LEO", 3900.0, 21600.0),
        )
        conn.commit()

        transfer_parts = [{
            "item_id": "test_thruster_nav",
            "name": "Navigator Thruster",
            "type": "thruster",
            "mass_kg": 9000,
            "thrust_kn": 250.0,
            "isp_s": 900.0,
            "water_kg": 10000.0,
            "cargo_capacity_kg": 30000,
        }]

        ship_id = api_world.spawn_ship(
            "ship_nav_test",
            "Navigator",
            "LEO",
            owner_corp=corp_id,
            parts=transfer_parts,
            fuel_kg=9000,
            fuel_capacity_kg=10000,
        )

        resp_transfer = api_client_db.post(f"/api/ships/{ship_id}/transfer", json={"to_location_id": "GEO"})
        assert resp_transfer.status_code == 200
        data = resp_transfer.json()
        assert data.get("to") == "GEO"

        row = conn.execute(
            "SELECT location_id, to_location_id, arrives_at FROM ships WHERE id = ?",
            (ship_id,),
        ).fetchone()
        assert row is not None
        assert row["location_id"] is None
        assert str(row["to_location_id"]) == "GEO"
        arrives_at = float(row["arrives_at"])

        import fleet_router

        monkeypatch.setattr(fleet_router, "game_now_s", lambda: arrives_at + 1.0)
        resp_state = api_client_db.get("/api/state")
        assert resp_state.status_code == 200

        row2 = conn.execute(
            "SELECT location_id, to_location_id, arrives_at FROM ships WHERE id = ?",
            (ship_id,),
        ).fetchone()
        assert row2 is not None
        assert str(row2["location_id"]) == "GEO"
        assert row2["to_location_id"] is None
        assert row2["arrives_at"] is None
