"""
Shipyard API routes.

Extracted from main.py — handles:
  /api/shipyard/preview
  /api/shipyard/build
  /api/shipyard/refit
"""

import json
import re
import sqlite3
from collections import Counter
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


def _first_incompatibility(parts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    incompatible = catalog_service.find_incompatible_reactor_thruster_pairs(parts)
    if not incompatible:
        return None
    return incompatible[0]


def _invalid_build_item_ids(item_ids: List[str], resolved_parts: List[Dict[str, Any]]) -> List[str]:
    """Return requested item IDs that do not resolve to ship parts."""
    requested_counts = Counter(str(i).strip() for i in (item_ids or []) if str(i).strip())
    resolved_counts = Counter(
        str((p or {}).get("item_id") or "").strip()
        for p in (resolved_parts or [])
        if str((p or {}).get("item_id") or "").strip()
    )
    invalid: List[str] = []
    for item_id, req in requested_counts.items():
        if resolved_counts.get(item_id, 0) < req:
            invalid.append(item_id)
    return invalid


# ── Pydantic models ────────────────────────────────────────

class ShipyardPreviewReq(BaseModel):
    parts: List[Any] = Field(default_factory=list)
    source_location_id: Optional[str] = None
    fuel_kg: Optional[float] = None
    existing_fuel_kg: Optional[float] = None
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
    invalid_item_ids = _invalid_build_item_ids(item_ids, parts)
    if invalid_item_ids:
        invalid_joined = ", ".join(invalid_item_ids)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid non-part item(s) in ship build: {invalid_joined}. "
                "Use fuel loading for water/resources."
            ),
        )

    # Determine available water at location. In edit mode, the ship's current
    # fuel becomes reclaimable water once deconstructed, so callers can include
    # it via existing_fuel_kg for accurate preview clamping.
    if req.unlimited_fuel:
        # Boost mode: unlimited water (will be boosted from Earth)
        available_fuel_kg = 1e12
    else:
        available_fuel_kg = _get_available_water_kg(conn, source_location_id, corp_id=corp_id or "")
        available_fuel_kg += max(0.0, float(req.existing_fuel_kg or 0.0))

    # Apply requested fuel level (no hard cap — only limited by available water)
    requested_fuel = req.fuel_kg
    if requested_fuel is not None:
        fuel_kg = max(0.0, min(float(requested_fuel), available_fuel_kg))
    else:
        fuel_kg = 0.0

    stats = m.build_ship_stats_payload(parts, current_fuel_kg=fuel_kg)
    power_balance = catalog_service.compute_power_balance(parts)
    incompatibility = _first_incompatibility(parts)
    return {
        "build_location_id": source_location_id,
        "parts": parts,
        "stats": stats,
        "power_balance": power_balance,
        "available_fuel_kg": available_fuel_kg,
        "compatibility": {
            "ok": incompatibility is None,
            "error": (
                None
                if incompatibility is None
                else f"Engine {incompatibility['thruster_name']} is not compatible with {incompatibility['reactor_branch']} reactors"
            ),
            "incompatible_pairs": [] if incompatibility is None else [incompatibility],
        },
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

    requested_parts = m.shipyard_parts_from_item_ids(item_ids)
    invalid_item_ids = _invalid_build_item_ids(item_ids, requested_parts)
    if invalid_item_ids:
        invalid_joined = ", ".join(invalid_item_ids)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid non-part item(s) in ship build: {invalid_joined}. "
                "Use fuel loading for water/resources."
            ),
        )
    incompatibility = _first_incompatibility(requested_parts)
    if incompatibility is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Engine {incompatibility['thruster_name']} is not compatible with {incompatibility['reactor_branch']} reactors",
        )

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

    incompatibility = _first_incompatibility(parts)
    if incompatibility is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Engine {incompatibility['thruster_name']} is not compatible with {incompatibility['reactor_branch']} reactors",
        )

    # Determine how much fuel to load (no hard cap — only limited by available water)
    requested_fuel = req.fuel_kg
    if requested_fuel is not None and requested_fuel > 0:
        available_fuel_kg = _get_available_water_kg(conn, source_location_id, corp_id=corp_id or "")
        fuel_to_load = max(0.0, min(float(requested_fuel), available_fuel_kg))
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
          dv_planned_m_s,dock_slot,
          parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s,
          corp_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            None,
            None,
            m.merge_ship_parts_and_cargo(parts),
            stats["fuel_kg"],
            0,
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


# ── Refit (atomic in-place edit) ───────────────────────────

class ShipyardRefitReq(BaseModel):
    ship_id: str
    name: Optional[str] = None
    parts: List[Any] = Field(default_factory=list)
    fuel_kg: Optional[float] = None


@router.post("/api/shipyard/refit")
def api_shipyard_refit(req: ShipyardRefitReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """Atomic in-place ship refit: swap parts and adjust fuel in one transaction.

    Unlike deconstruct+rebuild, this preserves the ship record, cargo, and
    rolls back cleanly if any step fails.
    """
    m = _main()
    from fleet_router import _require_ship_ownership

    ship_id = (req.ship_id or "").strip()
    if not ship_id:
        raise HTTPException(status_code=400, detail="ship_id is required")

    user = require_login(conn, request)
    _require_ship_ownership(conn, request, ship_id)

    corp_id = user.get("corp_id") if hasattr(user, "get") else None

    # Load existing ship
    row = conn.execute(
        "SELECT id, name, location_id, arrives_at, parts_json, fuel_kg, corp_id FROM ships WHERE id=?",
        (ship_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ship not found")

    location_id = str(row["location_id"] or "").strip()
    if not location_id or row["arrives_at"] is not None:
        raise HTTPException(status_code=400, detail="Ship must be docked at a location to refit")

    # ── Parse old parts ──
    old_parts_raw, old_cargo = m.split_ship_parts_and_cargo(row["parts_json"] or "[]")
    old_parts = m.normalize_parts(old_parts_raw)
    old_fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))

    # ── Resolve new parts ──
    new_item_ids = m.normalize_shipyard_item_ids(req.parts)
    if not new_item_ids:
        raise HTTPException(status_code=400, detail="At least one part is required")

    new_parts_resolved = m.shipyard_parts_from_item_ids(new_item_ids)
    invalid_item_ids = _invalid_build_item_ids(new_item_ids, new_parts_resolved)
    if invalid_item_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid non-part item(s): {', '.join(invalid_item_ids)}. Use fuel loading for water/resources.",
        )
    incompatibility = _first_incompatibility(new_parts_resolved)
    if incompatibility is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Engine {incompatibility['thruster_name']} is not compatible with {incompatibility['reactor_branch']} reactors",
        )

    # ── Diff parts: figure out what to return and what to consume ──
    old_counts: Dict[str, int] = {}
    for p in old_parts:
        pid = str(p.get("item_id") or "")
        if pid:
            old_counts[pid] = old_counts.get(pid, 0) + 1

    new_counts: Dict[str, int] = {}
    for pid in new_item_ids:
        new_counts[pid] = new_counts.get(pid, 0) + 1

    # Parts to return to location (old minus new)
    parts_to_return: Dict[str, int] = {}
    # Parts to consume from location (new minus old)
    parts_to_consume: Dict[str, int] = {}

    all_ids = set(old_counts.keys()) | set(new_counts.keys())
    for pid in all_ids:
        old_n = old_counts.get(pid, 0)
        new_n = new_counts.get(pid, 0)
        if old_n > new_n:
            parts_to_return[pid] = old_n - new_n
        elif new_n > old_n:
            parts_to_consume[pid] = new_n - old_n

    # ── Return removed parts to location inventory ──
    old_parts_by_id: Dict[str, List[Dict[str, Any]]] = {}
    for p in old_parts:
        pid = str(p.get("item_id") or "")
        if pid:
            old_parts_by_id.setdefault(pid, []).append(p)

    for pid, count in parts_to_return.items():
        part_templates = old_parts_by_id.get(pid, [])
        template = part_templates[0] if part_templates else {"item_id": pid}
        m.add_part_to_location_inventory(conn, location_id, template, count=count, corp_id=corp_id or "")

    # ── Consume added parts from location inventory ──
    consume_ids: List[str] = []
    for pid, count in parts_to_consume.items():
        consume_ids.extend([pid] * count)

    if consume_ids:
        m.consume_parts_from_location_inventory(conn, location_id, consume_ids, corp_id=corp_id)

    # ── Resolve final parts list from catalog (authoritative) ──
    final_parts = m.shipyard_parts_from_item_ids(new_item_ids)

    # ── Handle fuel delta ──
    # Total available water = what's at location + old fuel being released
    available_water = _get_available_water_kg(conn, location_id, corp_id=corp_id or "") + old_fuel_kg
    requested_fuel = req.fuel_kg
    if requested_fuel is not None and requested_fuel >= 0:
        fuel_to_load = max(0.0, min(float(requested_fuel), available_water))
    else:
        # No fuel_kg specified: keep old fuel level
        fuel_to_load = old_fuel_kg

    # Net water change at location: old fuel returned minus new fuel loaded
    water_delta = fuel_to_load - old_fuel_kg
    if water_delta > 0:
        # Need to consume additional water from location
        _consume_water_from_location(conn, location_id, water_delta, corp_id=corp_id or "")
    elif water_delta < 0:
        # Return excess fuel as water to location
        m.add_resource_to_location_inventory(conn, location_id, "water", -water_delta, corp_id=corp_id or "")

    # ── Compute final stats & update ship ──
    cargo_stacks = m.get_ship_cargo_stacks(conn, ship_id)
    cargo_mass = sum(max(0.0, float(s.get("mass_kg") or 0.0)) for s in cargo_stacks)
    stats = m.build_ship_stats_payload(final_parts, current_fuel_kg=fuel_to_load, cargo_mass_kg=cargo_mass)

    new_name = (req.name or "").strip() or str(row["name"] or ship_id)

    conn.execute(
        """
        UPDATE ships
        SET name=?, parts_json=?, fuel_kg=?, fuel_capacity_kg=?, dry_mass_kg=?, isp_s=?
        WHERE id=?
        """,
        (
            new_name,
            m.merge_ship_parts_and_cargo(final_parts, old_cargo if old_cargo else None),
            stats["fuel_kg"],
            0,
            stats["dry_mass_kg"],
            stats["isp_s"],
            ship_id,
        ),
    )
    conn.commit()

    return {
        "ok": True,
        "ship": {
            "id": ship_id,
            "name": new_name,
            "location_id": location_id,
            "parts": final_parts,
            "corp_id": corp_id,
            **stats,
            "status": "docked",
        },
    }
