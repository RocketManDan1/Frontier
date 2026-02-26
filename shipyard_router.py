"""
Shipyard API routes.

Extracted from main.py — handles:
  /api/shipyard/preview
  /api/shipyard/build
"""

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth_service import require_login
import catalog_service
from db import get_db

router = APIRouter(tags=["shipyard"])


def _main():
    """Lazy import to avoid circular dependency with main.py."""
    import main
    return main


# ── Helpers ────────────────────────────────────────────────

def _slugify_ship_id(raw: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", raw.strip().lower()).strip("_")
    return text or "ship"


def _next_available_ship_id(conn: sqlite3.Connection, preferred: str) -> str:
    base = _slugify_ship_id(preferred)
    candidate = base
    suffix = 2
    while conn.execute("SELECT 1 FROM ships WHERE id=?", (candidate,)).fetchone():
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _get_available_water_kg(conn: sqlite3.Connection, location_id: str, *, corp_id: str = "") -> float:
    """Return total kg of water resource available at a location for the given corp."""
    if corp_id:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(mass_kg), 0.0) AS total_kg
            FROM location_inventory_stacks
            WHERE location_id=? AND corp_id=? AND stack_type='resource' AND stack_key='water'
            """,
            (location_id, corp_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(mass_kg), 0.0) AS total_kg
            FROM location_inventory_stacks
            WHERE location_id=? AND stack_type='resource' AND stack_key='water'
            """,
            (location_id,),
        ).fetchone()
    return max(0.0, float(row["total_kg"] if row else 0.0))


def _consume_water_from_location(conn: sqlite3.Connection, location_id: str, amount_kg: float, *, corp_id: str = "") -> float:
    """Consume water resource from a location's inventory. Returns actual amount consumed."""
    m = _main()
    amount = max(0.0, float(amount_kg or 0.0))
    if amount <= 0.0:
        return 0.0

    resources = m.load_resource_catalog()
    water = resources.get("water") or {}
    name = str(water.get("name") or "Water")
    density = max(0.0, float(water.get("mass_per_m3_kg") or 1000.0))
    volume = (amount / density) if density > 0.0 else 0.0

    payload_json = json.dumps({"resource_id": "water"}, sort_keys=True, separators=(",", ":"))

    m._upsert_inventory_stack(
        conn,
        location_id=location_id,
        stack_type="resource",
        stack_key="water",
        item_id="water",
        name=name,
        quantity_delta=-amount,
        mass_delta_kg=-amount,
        volume_delta_m3=-volume,
        payload_json=payload_json,
        corp_id=corp_id,
    )
    return amount


# ── Pydantic models ────────────────────────────────────────

class ShipyardPreviewReq(BaseModel):
    parts: List[Any] = Field(default_factory=list)
    source_location_id: Optional[str] = None
    fuel_kg: Optional[float] = None
    unlimited_fuel: bool = False


class ShipyardBuildReq(BaseModel):
    name: str
    ship_id: Optional[str] = None
    notes: List[str] = Field(default_factory=list)
    parts: List[Any] = Field(default_factory=list)
    source_location_id: Optional[str] = None
    fuel_kg: Optional[float] = None


# ── Routes ─────────────────────────────────────────────────

@router.post("/api/shipyard/preview")
def api_shipyard_preview(req: ShipyardPreviewReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    m = _main()
    source_location_id = (req.source_location_id or "").strip() or "LEO"

    user = require_login(conn, request)
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    loc = conn.execute("SELECT id,is_group FROM locations WHERE id=?", (source_location_id,)).fetchone()
    if not loc or int(loc["is_group"]):
        raise HTTPException(status_code=400, detail="source_location_id must be a valid non-group location")

    item_ids = m.normalize_shipyard_item_ids(req.parts)
    parts = m.shipyard_parts_from_item_ids(item_ids)

    # Compute stats with 0 fuel first to get fuel_capacity_kg
    base_stats = m.build_ship_stats_payload(parts, current_fuel_kg=0.0)
    fuel_capacity_kg = base_stats.get("fuel_capacity_kg", 0.0)

    # Determine available water at location
    if req.unlimited_fuel:
        # Boost mode: unlimited water (will be boosted from Earth)
        available_fuel_kg = fuel_capacity_kg
    else:
        available_fuel_kg = _get_available_water_kg(conn, source_location_id, corp_id=corp_id or "")

    # Apply requested fuel level
    requested_fuel = req.fuel_kg
    if requested_fuel is not None:
        fuel_kg = max(0.0, min(float(requested_fuel), fuel_capacity_kg, available_fuel_kg))
    else:
        fuel_kg = 0.0

    stats = m.build_ship_stats_payload(parts, current_fuel_kg=fuel_kg)
    power_balance = catalog_service.compute_power_balance(parts)
    return {
        "build_location_id": source_location_id,
        "parts": parts,
        "stats": stats,
        "power_balance": power_balance,
        "available_fuel_kg": available_fuel_kg,
    }


@router.post("/api/shipyard/build")
def api_shipyard_build(req: ShipyardBuildReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    m = _main()
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    source_location_id = (req.source_location_id or "").strip() or "LEO"

    item_ids = m.normalize_shipyard_item_ids(req.parts)
    if not item_ids:
        raise HTTPException(status_code=400, detail="At least one part is required")

    user = require_login(conn, request)
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    corp_color = user.get("corp_color") if hasattr(user, "get") else None

    loc = conn.execute(
        "SELECT id,is_group FROM locations WHERE id=?",
        (source_location_id,),
    ).fetchone()
    if not loc or int(loc["is_group"]):
        raise HTTPException(status_code=400, detail="source_location_id must be a valid non-group location")

    parts = m.consume_parts_from_location_inventory(conn, source_location_id, item_ids, corp_id=corp_id)

    if not parts:
        raise HTTPException(status_code=400, detail="No valid parts found for build")

    # Compute stats with 0 fuel first to get fuel_capacity_kg
    base_stats = m.build_ship_stats_payload(parts, current_fuel_kg=0.0)
    fuel_capacity_kg = base_stats.get("fuel_capacity_kg", 0.0)

    # Determine how much fuel to load
    requested_fuel = req.fuel_kg
    if requested_fuel is not None and requested_fuel > 0 and fuel_capacity_kg > 0:
        available_fuel_kg = _get_available_water_kg(conn, source_location_id, corp_id=corp_id or "")
        fuel_to_load = max(0.0, min(float(requested_fuel), fuel_capacity_kg, available_fuel_kg))
        if fuel_to_load > 0:
            _consume_water_from_location(conn, source_location_id, fuel_to_load, corp_id=corp_id or "")
    else:
        fuel_to_load = 0.0

    stats = m.build_ship_stats_payload(parts, current_fuel_kg=fuel_to_load)

    preferred_id = (req.ship_id or name).strip()
    ship_id = _next_available_ship_id(conn, preferred_id)
    notes = [str(n) for n in (req.notes or []) if str(n).strip()]
    ship_color = corp_color or "#ffffff"

    conn.execute(
        """
        INSERT INTO ships (
          id,name,shape,color,size_px,notes_json,
          location_id,from_location_id,to_location_id,departed_at,arrives_at,
          transfer_path_json,dv_planned_m_s,dock_slot,
          parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s,
          corp_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ship_id,
            name,
            "triangle",
            ship_color,
            12.0,
            json.dumps(notes),
            source_location_id,
            None,
            None,
            None,
            None,
            "[]",
            None,
            None,
            json.dumps(parts),
            stats["fuel_kg"],
            stats["fuel_capacity_kg"],
            stats["dry_mass_kg"],
            stats["isp_s"],
            corp_id,
        ),
    )
    conn.commit()

    return {
        "ok": True,
        "ship": {
            "id": ship_id,
            "name": name,
            "location_id": source_location_id,
            "parts": parts,
            "notes": notes,
            "source_location_id": source_location_id,
            "corp_id": corp_id,
            **stats,
            "status": "docked",
        },
    }
