"""
Organization router — API routes for org management, LEO boosts, research teams, and prospecting.

Routes:
  /api/org                         — get org state (settled)
  /api/org/hire-team               — hire a research team
  /api/org/fire-team               — fire a research team
  /api/org/boostable-items         — list items eligible for Earth-to-LEO boost
  /api/org/boost                   — boost item to LEO
  /api/org/boost-cost              — calculate boost cost for a mass
  /api/org/research/unlocks        — get unlocked techs
  /api/org/research/unlock         — unlock a tech node
  /api/org/prospecting/sites       — get prospected sites
  /api/org/prospecting/prospect    — prospect a surface site
"""

import sqlite3
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth_service import require_login
from db import get_db
import org_service

router = APIRouter()


def _get_org_id(conn: sqlite3.Connection, user) -> str:
    """Resolve org_id from either a corp session or a legacy user session."""
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    if corp_id:
        return org_service.ensure_org_for_corp(conn, corp_id)
    return _get_org_id(conn, user)


# ── Request models ─────────────────────────────────────────────────────────────

class FireTeamRequest(BaseModel):
    team_id: str

class BoostRequest(BaseModel):
    item_id: str
    quantity: float = 1.0

class BoostCostRequest(BaseModel):
    mass_kg: float

class UnlockTechRequest(BaseModel):
    tech_id: str
    cost: float
    prerequisites: list[str] = []

class ProspectRequest(BaseModel):
    ship_id: str
    site_location_id: str


# ── Org State ──────────────────────────────────────────────────────────────────

@router.get("/api/org")
def api_get_org(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """Get the current org state with settled finances."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    org_state = org_service.settle_org(conn, org_id)

    # Include research unlocks
    unlocks = org_service.get_unlocked_techs(conn, org_id)
    org_state["research_unlocks"] = unlocks

    # Include prospected sites
    prospected = org_service.get_prospected_sites(conn, org_id)
    org_state["prospected_sites"] = prospected

    return {"org": org_state}


# ── Research Teams ─────────────────────────────────────────────────────────────

@router.post("/api/org/hire-team")
def api_hire_team(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """Hire a new research team."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    try:
        result = org_service.hire_research_team(conn, org_id)
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/org/fire-team")
def api_fire_team(
    body: FireTeamRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Dismiss a research team."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    try:
        result = org_service.fire_research_team(conn, org_id, body.team_id)
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── LEO Boost ──────────────────────────────────────────────────────────────────

@router.get("/api/org/boostable-items")
def api_boostable_items(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """List items eligible for Earth-to-LEO boost (filtered by org's unlocked techs)."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    items = org_service.get_boostable_items(conn, org_id)
    return {
        "items": items,
        "base_cost_usd": org_service.LEO_BOOST_BASE_COST,
        "cost_per_kg_usd": org_service.LEO_BOOST_COST_PER_KG,
    }


@router.post("/api/org/boost-cost")
def api_boost_cost(
    body: BoostCostRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Calculate the cost to boost a given mass to LEO."""
    require_login(conn, request)
    cost = org_service.calculate_boost_cost(body.mass_kg)
    return {"mass_kg": body.mass_kg, "cost_usd": cost}


@router.post("/api/org/boost")
def api_boost_to_leo(
    body: BoostRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Boost an item from Earth to LEO."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    try:
        result = org_service.boost_to_leo(conn, org_id, body.item_id, body.quantity)
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/org/boost-history")
def api_boost_history(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """Return recent LEO boost launches for the org."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    history = org_service.get_boost_history(conn, org_id)
    return {"history": history}


# ── Research Unlock ────────────────────────────────────────────────────────────

@router.get("/api/org/research/unlocks")
def api_get_unlocks(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """Get all unlocked techs for the org."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    unlocks = org_service.get_unlocked_techs(conn, org_id)
    return {"unlocks": unlocks}


@router.post("/api/org/research/unlock")
def api_unlock_tech(
    body: UnlockTechRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Unlock a tech node using research points."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    try:
        result = org_service.unlock_tech(
            conn, org_id, body.tech_id, body.cost, body.prerequisites or None
        )
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Prospecting ────────────────────────────────────────────────────────────────

@router.get("/api/org/prospecting/sites")
def api_prospected_sites(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """Get all sites prospected by the org."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    sites = org_service.get_prospected_sites(conn, org_id)
    return {"sites": sites}


@router.get("/api/org/prospecting/in_range/{ship_id}")
def api_sites_in_range(ship_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """Get all surface sites within prospecting range of a ship's robonaut."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    try:
        result = org_service.get_sites_in_range(conn, org_id, ship_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/org/prospecting/prospect")
def api_prospect_site(
    body: ProspectRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Prospect a surface site with a ship that has a robonaut."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    try:
        result = org_service.prospect_site(conn, org_id, body.ship_id, body.site_location_id)
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
