"""
Industry API routes.

Handles:
  /api/sites                           — all locations with industry summaries
  /api/sites/{location_id}             — single site detail (inventory, equipment, jobs)
  /api/industry/{location_id}          — full industrial overview
  /api/industry/deploy                 — deploy equipment from inventory
  /api/industry/undeploy               — undeploy equipment back to inventory
  /api/industry/jobs/start             — start a production job
  /api/industry/jobs/cancel            — cancel a production job
  /api/industry/mining/start           — start mining
  /api/industry/mining/stop            — stop mining
"""

import sqlite3
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth_service import require_login
from db import get_db
import industry_service

router = APIRouter(tags=["industry"])


def _main():
    """Lazy import to avoid circular dependency."""
    import main
    return main


# ── Request Models ─────────────────────────────────────────────────────────────


class DeployRequest(BaseModel):
    location_id: str
    item_id: str


class UndeployRequest(BaseModel):
    equipment_id: str


class StartJobRequest(BaseModel):
    equipment_id: str
    recipe_id: str


class CancelJobRequest(BaseModel):
    job_id: str


class StartMiningRequest(BaseModel):
    equipment_id: str
    resource_id: str


class StopMiningRequest(BaseModel):
    job_id: str


# ── Sites Overview ─────────────────────────────────────────────────────────────


@router.get("/api/sites")
def api_sites(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """List all locations with industry summaries."""
    user = require_login(conn, request)
    _main().settle_arrivals(conn, _main().game_now_s())
    industry_service.settle_industry(conn)

    # Get all non-group locations
    locations = conn.execute(
        """
        SELECT l.id, l.name, l.parent_id, l.is_group, l.sort_order
        FROM locations l
        WHERE l.is_group = 0
        ORDER BY l.sort_order, l.name
        """
    ).fetchall()

    # Get surface site data
    surface_sites = {}
    for row in conn.execute("SELECT location_id, body_id, gravity_m_s2 FROM surface_sites").fetchall():
        surface_sites[row["location_id"]] = {
            "body_id": row["body_id"],
            "gravity_m_s2": float(row["gravity_m_s2"]),
        }

    # Get equipment counts per location
    equip_rows = conn.execute(
        """
        SELECT location_id, category,
               COUNT(*) as total,
               SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active
        FROM deployed_equipment
        GROUP BY location_id, category
        """
    ).fetchall()
    equip_by_loc: Dict[str, Dict] = {}
    for r in equip_rows:
        loc = r["location_id"]
        equip_by_loc.setdefault(loc, {})
        equip_by_loc[loc][r["category"]] = {"total": r["total"], "active": r["active"]}

    # Get active job counts per location
    job_rows = conn.execute(
        """
        SELECT location_id, COUNT(*) as cnt
        FROM production_jobs
        WHERE status = 'active'
        GROUP BY location_id
        """
    ).fetchall()
    jobs_by_loc = {r["location_id"]: r["cnt"] for r in job_rows}

    # Get inventory summary per location (count of resource types + total mass)
    inv_rows = conn.execute(
        """
        SELECT location_id,
               COUNT(*) as stack_count,
               SUM(mass_kg) as total_mass_kg
        FROM location_inventory_stacks
        GROUP BY location_id
        """
    ).fetchall()
    inv_by_loc = {}
    for r in inv_rows:
        inv_by_loc[r["location_id"]] = {
            "stack_count": r["stack_count"],
            "total_mass_kg": float(r["total_mass_kg"] or 0),
        }

    # Get ship counts per location
    ship_rows = conn.execute(
        """
        SELECT location_id, COUNT(*) as cnt
        FROM ships
        WHERE location_id IS NOT NULL
        GROUP BY location_id
        """
    ).fetchall()
    ships_by_loc = {r["location_id"]: r["cnt"] for r in ship_rows}

    # Metadata
    metadata = _main()._location_metadata_by_id()

    result = []
    for loc in locations:
        loc_id = loc["id"]
        meta = metadata.get(loc_id, {})
        is_surface = loc_id in surface_sites
        site_info = surface_sites.get(loc_id, {})

        entry = {
            "id": loc_id,
            "name": loc["name"],
            "parent_id": loc["parent_id"],
            "is_surface_site": is_surface,
            "body_id": site_info.get("body_id") or meta.get("body_id", ""),
            "gravity_m_s2": site_info.get("gravity_m_s2", 0),
            "symbol": meta.get("symbol", ""),
            "equipment": equip_by_loc.get(loc_id, {}),
            "active_jobs": jobs_by_loc.get(loc_id, 0),
            "inventory": inv_by_loc.get(loc_id, {"stack_count": 0, "total_mass_kg": 0}),
            "ships_docked": ships_by_loc.get(loc_id, 0),
        }
        result.append(entry)

    return {"sites": result}


@router.get("/api/sites/{location_id}")
def api_site_detail(
    location_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Detailed view of a single site."""
    user = require_login(conn, request)
    _main().settle_arrivals(conn, _main().game_now_s())
    industry_service.settle_industry(conn, location_id)

    # Location info
    loc = conn.execute(
        "SELECT id, name, parent_id, is_group FROM locations WHERE id = ?",
        (location_id,),
    ).fetchone()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")

    metadata = _main()._location_metadata_by_id()
    meta = metadata.get(location_id, {})

    # Surface site data
    site = conn.execute(
        "SELECT body_id, orbit_node_id, gravity_m_s2 FROM surface_sites WHERE location_id = ?",
        (location_id,),
    ).fetchone()

    # Inventory
    inv = _main().get_location_inventory_payload(conn, location_id)

    # Deployed equipment
    equipment = industry_service.get_deployed_equipment(conn, location_id)

    # Active jobs
    jobs = industry_service.get_active_jobs(conn, location_id)

    # Docked ships
    ships = conn.execute(
        "SELECT id, name FROM ships WHERE location_id = ?",
        (location_id,),
    ).fetchall()

    result: Dict[str, Any] = {
        "id": location_id,
        "name": loc["name"],
        "parent_id": loc["parent_id"],
        "symbol": meta.get("symbol", ""),
        "body_id": meta.get("body_id", ""),
        "is_surface_site": site is not None,
        "inventory": inv,
        "equipment": equipment,
        "active_jobs": jobs,
        "ships": [{"id": s["id"], "name": s["name"]} for s in ships],
    }

    if site:
        result["surface"] = {
            "body_id": site["body_id"],
            "orbit_node_id": site["orbit_node_id"],
            "gravity_m_s2": float(site["gravity_m_s2"]),
        }
        result["minable_resources"] = industry_service.get_minable_resources(conn, location_id)

    return result


# ── Industry Overview ──────────────────────────────────────────────────────────


@router.get("/api/industry/{location_id}")
def api_industry_overview(
    location_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Full industrial overview: equipment, jobs, available recipes, resources."""
    user = require_login(conn, request)
    _main().settle_arrivals(conn, _main().game_now_s())
    industry_service.settle_industry(conn, location_id)

    equipment = industry_service.get_deployed_equipment(conn, location_id)
    active_jobs = industry_service.get_active_jobs(conn, location_id)
    history = industry_service.get_job_history(conn, location_id)
    available_recipes = industry_service.get_available_recipes_for_location(conn, location_id)
    inv = _main().get_location_inventory_payload(conn, location_id)

    # Surface site info for mining
    site = conn.execute(
        "SELECT body_id, gravity_m_s2 FROM surface_sites WHERE location_id = ?",
        (location_id,),
    ).fetchone()
    minable = industry_service.get_minable_resources(conn, location_id) if site else []

    # Idle constructors for mining
    idle_constructors = [
        {"id": e["id"], "name": e["name"], "config": e["config"]}
        for e in equipment
        if e["category"] == "constructor" and e["status"] == "idle"
    ]

    return {
        "location_id": location_id,
        "equipment": equipment,
        "active_jobs": active_jobs,
        "job_history": history,
        "available_recipes": available_recipes,
        "inventory": inv,
        "is_surface_site": site is not None,
        "minable_resources": minable,
        "idle_constructors": idle_constructors,
    }


# ── Deploy / Undeploy ─────────────────────────────────────────────────────────


@router.post("/api/industry/deploy")
def api_deploy_equipment(
    body: DeployRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Deploy a refinery or constructor from location inventory."""
    user = require_login(conn, request)
    try:
        result = industry_service.deploy_equipment(
            conn, body.location_id, body.item_id, user["username"]
        )
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/industry/undeploy")
def api_undeploy_equipment(
    body: UndeployRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Undeploy equipment back to location inventory."""
    user = require_login(conn, request)
    try:
        result = industry_service.undeploy_equipment(conn, body.equipment_id, user["username"])
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Production Jobs ────────────────────────────────────────────────────────────


@router.post("/api/industry/jobs/start")
def api_start_job(
    body: StartJobRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Start a refinery production job."""
    user = require_login(conn, request)
    industry_service.settle_industry(conn)
    try:
        result = industry_service.start_production_job(
            conn, body.equipment_id, body.recipe_id, user["username"]
        )
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/industry/jobs/cancel")
def api_cancel_job(
    body: CancelJobRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Cancel an active production job. Returns partial refund."""
    user = require_login(conn, request)
    try:
        result = industry_service.cancel_production_job(conn, body.job_id, user["username"])
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Mining ─────────────────────────────────────────────────────────────────────


@router.post("/api/industry/mining/start")
def api_start_mining(
    body: StartMiningRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Start mining a resource at a surface site."""
    user = require_login(conn, request)
    industry_service.settle_industry(conn)
    try:
        result = industry_service.start_mining_job(
            conn, body.equipment_id, body.resource_id, user["username"]
        )
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/industry/mining/stop")
def api_stop_mining(
    body: StopMiningRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Stop an active mining job."""
    user = require_login(conn, request)
    try:
        result = industry_service.stop_mining_job(conn, body.job_id, user["username"])
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
