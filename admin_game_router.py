"""
Admin game-management API routes.

Extracted from main.py — handles:
  /api/admin/simulation/toggle_pause
  /api/admin/reset_game
  /api/admin/spawn_ship
  /api/admin/ships/{ship_id}          (DELETE)
  /api/admin/ships/{ship_id}/refuel
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth_service import ensure_default_admin_account, require_admin
from db import get_db
from shipyard_router import _next_available_ship_id
from sim_service import (
    game_now_s,
    effective_time_scale,
    simulation_paused,
    set_simulation_paused,
    reset_simulation_clock,
)

router = APIRouter(tags=["admin"])


def _main():
    """Lazy import to avoid circular dependency with main.py."""
    import main
    return main


# ── Pydantic models ────────────────────────────────────────

class TeleportShipReq(BaseModel):
    to_location_id: str


class SpawnShipReq(BaseModel):
    name: str
    location_id: str
    ship_id: Optional[str] = None
    shape: str = "triangle"
    color: str = "#ffffff"
    size_px: float = 12
    notes: List[str] = Field(default_factory=list)
    parts: List[Any] = Field(default_factory=list)
    fuel_kg: Optional[float] = None


# ── Routes ─────────────────────────────────────────────────

@router.post("/api/admin/simulation/toggle_pause")
def api_admin_toggle_pause(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_admin(conn, request)
    next_paused = not simulation_paused()
    set_simulation_paused(next_paused)
    _main()._persist_simulation_clock_state(conn)
    conn.commit()

    return {
        "ok": True,
        "paused": simulation_paused(),
        "server_time": game_now_s(),
        "time_scale": effective_time_scale(),
    }


@router.post("/api/admin/reset_game")
def api_admin_reset_game(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_admin(conn, request)
    deleted_ships = 0
    deleted_accounts = 0
    deleted_inventory_stacks = 0

    cur = conn.execute("DELETE FROM ships")
    deleted_ships = int(cur.rowcount or 0)

    cur = conn.execute("DELETE FROM location_inventory_stacks")
    deleted_inventory_stacks = int(cur.rowcount or 0)

    user_rows = conn.execute("SELECT COUNT(*) AS c FROM users WHERE username <> 'admin'").fetchone()
    deleted_accounts = int(user_rows["c"] or 0)
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM users")
    ensure_default_admin_account(conn, reset_password=True)

    reset_simulation_clock()
    _main()._persist_simulation_clock_state(conn)
    conn.commit()

    return {
        "ok": True,
        "reset_to": "2000-01-01T00:00:00Z",
        "deleted_ships": deleted_ships,
        "deleted_inventory_stacks": deleted_inventory_stacks,
        "deleted_accounts": deleted_accounts,
        "paused": simulation_paused(),
        "server_time": game_now_s(),
        "time_scale": effective_time_scale(),
    }


@router.post("/api/admin/spawn_ship")
def api_admin_spawn_ship(req: SpawnShipReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    m = _main()
    name = (req.name or "").strip()
    location_id = (req.location_id or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not location_id:
        raise HTTPException(status_code=400, detail="location_id is required")

    require_admin(conn, request)
    loc = conn.execute(
        "SELECT id,is_group FROM locations WHERE id=?",
        (location_id,),
    ).fetchone()
    if not loc or int(loc["is_group"]):
        raise HTTPException(status_code=400, detail="location_id must be a valid non-group location")

    preferred_id = (req.ship_id or name).strip()
    ship_id = _next_available_ship_id(conn, preferred_id)

    notes = [str(n) for n in (req.notes or []) if str(n).strip()]
    parts = m.normalize_parts(req.parts or [])
    if not parts:
        parts = m.normalize_parts([
            {"item_id": "ntr_m1_nerva_solid_core"},
            {"name": "Radiator", "type": "radiator", "mass_kg": 600.0},
            {"item_id": "water_tank_10_m3"},
        ])

    stats = m.derive_ship_stats_from_parts(
        parts,
        current_fuel_kg=float(req.fuel_kg) if req.fuel_kg is not None else None,
    )
    fuel_capacity_kg = stats["fuel_capacity_kg"]
    fuel_kg = stats["fuel_kg"]
    dry_mass_kg = stats["dry_mass_kg"]
    isp_s = stats["isp_s"]

    shape = (req.shape or "triangle").strip() or "triangle"
    color = (req.color or "#ffffff").strip() or "#ffffff"
    size_px = max(4.0, min(36.0, float(req.size_px or 12)))

    conn.execute(
        """
        INSERT INTO ships (
          id,name,shape,color,size_px,notes_json,
          location_id,from_location_id,to_location_id,departed_at,arrives_at,
                        transfer_path_json,dv_planned_m_s,dock_slot,
                        parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ship_id,
            name,
            shape,
            color,
            size_px,
            json.dumps(notes),
            location_id,
            None,
            None,
            None,
            None,
            "[]",
            None,
            None,
            json.dumps(parts),
            fuel_kg,
            fuel_capacity_kg,
            dry_mass_kg,
            isp_s,
        ),
    )
    conn.commit()

    return {
        "ok": True,
        "ship": {
            "id": ship_id,
            "name": name,
            "shape": shape,
            "color": color,
            "size_px": size_px,
            "notes": notes,
            "location_id": location_id,
            "parts": parts,
            "fuel_kg": fuel_kg,
            "fuel_capacity_kg": fuel_capacity_kg,
            "dry_mass_kg": dry_mass_kg,
            "isp_s": isp_s,
            "delta_v_remaining_m_s": m.compute_delta_v_remaining_m_s(dry_mass_kg, fuel_kg, isp_s),
            "status": "docked",
        },
    }


@router.delete("/api/admin/ships/{ship_id}")
def api_admin_delete_ship(ship_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    sid = (ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    require_admin(conn, request)
    row = conn.execute("SELECT id,name FROM ships WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ship not found")

    conn.execute("DELETE FROM ships WHERE id=?", (sid,))
    conn.commit()

    return {
        "ok": True,
        "deleted": {
            "id": row["id"],
            "name": row["name"],
        },
    }


@router.post("/api/admin/ships/{ship_id}/refuel")
def api_admin_refuel_ship(ship_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    m = _main()
    sid = (ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    require_admin(conn, request)
    row = conn.execute(
        """
        SELECT id,name,parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s
        FROM ships
        WHERE id=?
        """,
        (sid,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ship not found")

    parts = m.normalize_parts(json.loads(row["parts_json"] or "[]"))
    stats = m.derive_ship_stats_from_parts(
        parts,
        current_fuel_kg=float(row["fuel_kg"] or 0.0),
    )

    conn.execute(
        """
        UPDATE ships
        SET fuel_kg=?, fuel_capacity_kg=?, dry_mass_kg=?, isp_s=?
        WHERE id=?
        """,
        (
            stats["fuel_capacity_kg"],
            stats["fuel_capacity_kg"],
            stats["dry_mass_kg"],
            stats["isp_s"],
            sid,
        ),
    )
    conn.commit()

    return {
        "ok": True,
        "ship": {
            "id": row["id"],
            "name": row["name"],
            "fuel_kg": stats["fuel_capacity_kg"],
            "fuel_capacity_kg": stats["fuel_capacity_kg"],
            "delta_v_remaining_m_s": m.compute_delta_v_remaining_m_s(
                stats["dry_mass_kg"],
                stats["fuel_capacity_kg"],
                stats["isp_s"],
            ),
        },
    }


@router.post("/api/admin/ships/{ship_id}/teleport")
def api_admin_teleport_ship(
    ship_id: str,
    req: TeleportShipReq,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Admin-only: instantly move a ship to any location."""
    require_admin(conn, request)

    sid = (ship_id or "").strip()
    dest = (req.to_location_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")
    if not dest:
        raise HTTPException(status_code=400, detail="to_location_id is required")

    ship = conn.execute("SELECT id, name, location_id FROM ships WHERE id = ?", (sid,)).fetchone()
    if not ship:
        raise HTTPException(status_code=404, detail="Ship not found")

    loc = conn.execute("SELECT id, name FROM locations WHERE id = ?", (dest,)).fetchone()
    if not loc:
        raise HTTPException(status_code=404, detail=f"Location '{dest}' not found")

    conn.execute(
        """
        UPDATE ships
        SET location_id = ?,
            from_location_id = NULL,
            to_location_id = NULL,
            departed_at = NULL,
            arrives_at = NULL,
            transfer_path_json = '[]'
        WHERE id = ?
        """,
        (dest, sid),
    )
    conn.commit()

    return {
        "ok": True,
        "ship": {
            "id": ship["id"],
            "name": ship["name"],
            "from_location": ship["location_id"],
            "location_id": dest,
            "location_name": loc["name"],
        },
    }
