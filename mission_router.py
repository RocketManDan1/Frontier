"""
Mission router — API endpoints for the government missions system.

Routes:
  GET  /api/missions           — list available missions (triggers pool refill)
  GET  /api/missions/active    — current org's active mission
  GET  /api/missions/history   — org's past missions
  GET  /api/missions/{id}      — mission detail
  POST /api/missions/{id}/accept   — accept a mission
  POST /api/missions/{id}/complete — attempt completion
  POST /api/missions/{id}/abandon  — abandon a mission
"""

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_service import require_login
from db import get_db
from sim_service import game_now_s
import mission_service
from org_service import ensure_org_for_corp, ensure_org_for_user

router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_org_id(conn: sqlite3.Connection, user) -> str:
    """Resolve org_id from either a corp session or a legacy user session."""
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    if corp_id:
        return ensure_org_for_corp(conn, corp_id)
    username = user.get("username") if hasattr(user, "get") else user["username"]
    return ensure_org_for_user(conn, username)


# ── Static routes first (before parameterized {id}) ───────────────────────────

@router.get("/api/missions/active")
def missions_active(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Get the org's current active mission (if any)."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    active = mission_service.get_active_mission(conn, org_id)
    return {
        "mission": active,
        "game_time_s": game_now_s(),
    }


@router.get("/api/missions/history")
def missions_history(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Get the org's completed/failed/abandoned missions."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    history = mission_service.get_mission_history(conn, org_id)
    return {
        "missions": history,
        "game_time_s": game_now_s(),
    }


# ── List available missions ───────────────────────────────────────────────────

@router.get("/api/missions")
def missions_list(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """List all available missions. Triggers pool settle/refill."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)

    available = mission_service.get_available_missions(conn)
    active = mission_service.get_active_mission(conn, org_id)

    return {
        "missions": available,
        "active_mission": active,
        "game_time_s": game_now_s(),
    }


# ── Parameterized routes ──────────────────────────────────────────────────────

@router.get("/api/missions/{mission_id}")
def mission_detail(mission_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Get mission detail."""
    require_login(conn, request)
    mission = mission_service.get_mission_by_id(conn, mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return {
        "mission": mission,
        "game_time_s": game_now_s(),
    }


@router.post("/api/missions/{mission_id}/accept")
def mission_accept(mission_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Accept a mission. Pays 50% upfront and mints the module to LEO."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    try:
        mission = mission_service.accept_mission(conn, mission_id, org_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "mission": mission,
        "message": f"Mission accepted! ${mission.get('payout_upfront', 0):,.0f} credited. Module placed in LEO.",
        "game_time_s": game_now_s(),
    }


@router.post("/api/missions/{mission_id}/complete")
def mission_complete(mission_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Attempt to complete a mission."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    try:
        mission = mission_service.complete_mission(conn, mission_id, org_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    status = mission.get("status", "")
    if status == "completed":
        msg = f"Mission completed! ${mission.get('payout_completion', 0):,.0f} completion payment issued."
    elif status == "delivered":
        msg = "Module delivered to destination. Proceed to power phase."
    elif status == "powered":
        msg = "Power phase started. Maintain power for 90 game-days."
    else:
        msg = f"Mission advanced to status: {status}"

    return {
        "mission": mission,
        "message": msg,
        "game_time_s": game_now_s(),
    }


@router.post("/api/missions/{mission_id}/abandon")
def mission_abandon(mission_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Abandon a mission. Upfront payment is clawed back."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)
    try:
        mission = mission_service.abandon_mission(conn, mission_id, org_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "mission": mission,
        "message": "Mission abandoned. Upfront payment has been clawed back.",
        "game_time_s": game_now_s(),
    }
