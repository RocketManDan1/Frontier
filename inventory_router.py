"""
Inventory API routes.

Extracted from main.py — handles:
  /api/inventory/location/{location_id}
  /api/inventory/ship/{ship_id}
  /api/inventory/context/{kind}/{entity_id}
  /api/stack/context/ship/{ship_id}
  /api/inventory/transfer
  /api/stack/transfer
  /api/hangar/context/{ship_id}
"""

import json
import sqlite3
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth_service import require_login
import catalog_service
from db import get_db
from sim_service import game_now_s

router = APIRouter(tags=["inventory"])


def _main():
    """Lazy import to avoid circular dependency with main.py."""
    import main
    return main


def _check_ship_ownership(conn, user, ship_id: str):
    """Verify the requesting corp owns the ship. Raises 403 if not."""
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    if not corp_id:
        return  # Admin or legacy user — no restriction
    row = conn.execute("SELECT corp_id FROM ships WHERE id=?", (ship_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ship not found")
    if str(row["corp_id"] or "") != corp_id:
        raise HTTPException(status_code=403, detail="This ship belongs to another corporation")


def _resolve_body_name(conn: sqlite3.Connection, location_id: str) -> str:
    loc = conn.execute("SELECT parent_id FROM locations WHERE id = ?", (location_id,)).fetchone()
    if not loc:
        return ""
    parent_id = str(loc["parent_id"] or "")
    if not parent_id:
        return ""

    groups = conn.execute("SELECT id, name, parent_id, is_group FROM locations").fetchall()
    group_map: Dict[str, Dict[str, Any]] = {}
    for g in groups:
        if int(g["is_group"] or 0):
            group_map[str(g["id"])] = {
                "name": str(g["name"] or ""),
                "parent_id": str(g["parent_id"] or ""),
            }

    if parent_id not in group_map:
        return ""

    group = group_map[parent_id]
    group_name = group.get("name") or ""
    group_parent = group.get("parent_id") or ""
    leaf_names = {"orbits", "surface sites", "moons", "asteroids"}
    if group_name.lower() in leaf_names or "lagrange" in group_name.lower():
        if group_parent and group_parent in group_map:
            return group_map[group_parent].get("name") or ""
    return group_name


@router.get("/api/inventory/location/{location_id}")
def api_location_inventory(location_id: str, request: Request, facility_id: str = "", conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    loc_id = (location_id or "").strip()
    if not loc_id:
        raise HTTPException(status_code=400, detail="location_id is required")
    fid = str(facility_id or "").strip()

    user = require_login(conn, request)
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    loc = conn.execute("SELECT id,is_group,name FROM locations WHERE id=?", (loc_id,)).fetchone()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    if int(loc["is_group"]):
        raise HTTPException(status_code=400, detail="location_id must be a non-group location")

    payload = _main().get_location_inventory_payload(conn, loc_id, corp_id=corp_id)
    payload["location_name"] = str(loc["name"])
    return payload


@router.get("/api/inventory/ship/{ship_id}")
def api_ship_inventory(ship_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    user = require_login(conn, request)
    _check_ship_ownership(conn, user, ship_id)
    _main().settle_arrivals(conn, game_now_s())
    state = _main()._load_ship_inventory_state(conn, ship_id)
    row = state["row"]
    payload = {
        "ship_id": str(row["id"]),
        "ship_name": str(row["name"]),
        "location_id": state["location_id"],
        "is_docked": bool(state["is_docked"]),
        "items": _main()._inventory_items_for_ship(state),
        "cargo_summary": state.get("cargo_summary", {}),
    }
    conn.commit()
    return payload


@router.get("/api/inventory/context/{kind}/{entity_id}")
def api_inventory_context(kind: str, entity_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    inventory_kind = str(kind or "").strip().lower()
    inv_id = str(entity_id or "").strip()
    if inventory_kind not in {"ship", "location"}:
        raise HTTPException(status_code=400, detail="kind must be 'ship' or 'location'")
    if not inv_id:
        raise HTTPException(status_code=400, detail="entity_id is required")

    user = require_login(conn, request)
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    _main().settle_arrivals(conn, game_now_s())
    conn.commit()

    location_id = ""
    location_name = ""
    anchor_name = inv_id

    if inventory_kind == "ship":
        ship_state = _main()._load_ship_inventory_state(conn, inv_id)
        location_id = ship_state["location_id"] if ship_state["is_docked"] else ""
        anchor_name = str(ship_state["row"]["name"])
        if location_id:
            loc_row = _main()._get_location_row(conn, location_id)
            location_name = str(loc_row["name"])
    else:
        loc_row = _main()._get_location_row(conn, inv_id)
        location_id = str(loc_row["id"])
        location_name = str(loc_row["name"])
        anchor_name = location_name

    inventories: List[Dict[str, Any]] = []
    if location_id:
        location_payload = _main().get_location_inventory_payload(conn, location_id, corp_id=corp_id)
        inventories.append(
            {
                "inventory_kind": "location",
                "id": location_id,
                "name": f"{location_name} Site Inventory",
                "location_id": location_id,
                "cargo_summary": None,
                "items": _main()._inventory_items_for_location(location_payload),
            }
        )

        # Only show own ships at this location
        if corp_id:
            ship_rows = conn.execute(
                """
                SELECT id,name
                FROM ships
                WHERE location_id=? AND arrives_at IS NULL AND corp_id=?
                ORDER BY name, id
                """,
                (location_id, corp_id),
            ).fetchall()
        else:
            ship_rows = conn.execute(
                """
                SELECT id,name
                FROM ships
                WHERE location_id=? AND arrives_at IS NULL
                ORDER BY name, id
                """,
                (location_id,),
            ).fetchall()

        for ship_row in ship_rows:
            ship_state = _main()._load_ship_inventory_state(conn, str(ship_row["id"]))
            inventories.append(
                {
                    "inventory_kind": "ship",
                    "id": str(ship_row["id"]),
                    "name": str(ship_row["name"]),
                    "location_id": location_id,
                    "cargo_summary": ship_state.get("cargo_summary", {}),
                    "items": _main()._inventory_items_for_ship(ship_state),
                }
            )
    elif inventory_kind == "ship":
        ship_state = _main()._load_ship_inventory_state(conn, inv_id)
        inventories.append(
            {
                "inventory_kind": "ship",
                "id": str(ship_state["row"]["id"]),
                "name": str(ship_state["row"]["name"]),
                "location_id": "",
                "cargo_summary": ship_state.get("cargo_summary", {}),
                "items": _main()._inventory_items_for_ship(ship_state),
            }
        )

    payload = {
        "anchor": {
            "kind": inventory_kind,
            "id": inv_id,
            "name": anchor_name,
            "location_id": location_id,
        },
        "location": {
            "id": location_id,
            "name": location_name,
        },
        "inventories": inventories,
    }
    conn.commit()
    return payload


@router.get("/api/stack/context/ship/{ship_id}")
def api_stack_context_ship(ship_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    sid = str(ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    user = require_login(conn, request)
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    _check_ship_ownership(conn, user, sid)
    _main().settle_arrivals(conn, game_now_s())
    conn.commit()

    anchor_ship = _main()._load_ship_inventory_state(conn, sid)
    if not anchor_ship["is_docked"]:
        raise HTTPException(status_code=400, detail="Ship must be docked to view transferable stack")

    location_id = str(anchor_ship["location_id"])
    loc_row = _main()._get_location_row(conn, location_id)
    location_name = str(loc_row["name"])

    stacks: List[Dict[str, Any]] = []
    location_payload = _main().get_location_inventory_payload(conn, location_id, corp_id=corp_id)
    loc_items = _main()._stack_items_for_location(location_payload)
    stacks.append(
        {
            "stack_kind": "location",
            "id": location_id,
            "name": f"{location_name} Site Inventory",
            "location_id": location_id,
            "items": loc_items,
        }
    )

    ship_rows = conn.execute(
        """
        SELECT id,name
        FROM ships
        WHERE location_id=? AND arrives_at IS NULL
        ORDER BY name, id
        """,
        (location_id,),
    ).fetchall()

    for row in ship_rows:
        ship_state = _main()._load_ship_inventory_state(conn, str(row["id"]))
        stacks.append(
            {
                "stack_kind": "ship",
                "id": str(row["id"]),
                "name": str(row["name"]),
                "location_id": location_id,
                "items": _main()._stack_items_for_ship(ship_state),
            }
        )

    payload = {
        "anchor": {
            "kind": "ship",
            "id": sid,
            "name": str(anchor_ship["row"]["name"]),
            "location_id": location_id,
        },
        "location": {
            "id": location_id,
            "name": location_name,
        },
        "stacks": stacks,
    }
    conn.commit()
    return payload


class InventoryTransferReq(BaseModel):
    source_kind: Literal["ship_resource", "location_resource"]
    source_id: str
    source_key: str
    target_kind: Literal["ship", "location"]
    target_id: str
    target_key: Optional[str] = None
    amount: Optional[float] = None
    resource_id: Optional[str] = None
    facility_id: Optional[str] = None


class StackTransferReq(BaseModel):
    source_kind: Literal["ship_part", "location_part"]
    source_id: str
    source_key: str
    target_kind: Literal["ship", "location"]
    target_id: str
    facility_id: Optional[str] = None


@router.post("/api/inventory/transfer")
def api_inventory_transfer(req: InventoryTransferReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    source_kind = str(req.source_kind or "").strip().lower()
    source_id = str(req.source_id or "").strip()
    source_key = str(req.source_key or "").strip()
    target_kind = str(req.target_kind or "").strip().lower()
    target_id = str(req.target_id or "").strip()

    if source_kind not in {"ship_resource", "location_resource"}:
        raise HTTPException(status_code=400, detail="source_kind must be ship_resource or location_resource")
    if target_kind not in {"ship", "location"}:
        raise HTTPException(status_code=400, detail="target_kind must be ship or location")
    if not source_id or not source_key:
        raise HTTPException(status_code=400, detail="source_id and source_key are required")
    if not target_id:
        raise HTTPException(status_code=400, detail="target_id is required")

    user = require_login(conn, request)
    corp_id = user.get("corp_id") if hasattr(user, "get") else None

    # Verify ownership of referenced ships
    if source_kind == "ship_resource":
        _check_ship_ownership(conn, user, source_id)
    if target_kind == "ship":
        _check_ship_ownership(conn, user, target_id)

    m = _main()
    m.settle_arrivals(conn, game_now_s())

    # ── Resolve target location ──
    target_location_id = ""
    target_ship_state: Optional[Dict[str, Any]] = None
    if target_kind == "location":
        loc = m._get_location_row(conn, target_id)
        target_location_id = str(loc["id"])
    else:
        target_ship_state = m._load_ship_inventory_state(conn, target_id)
        if not target_ship_state["is_docked"]:
            raise HTTPException(status_code=400, detail="Target ship must be docked")
        target_location_id = str(target_ship_state["location_id"])

    # ── Resolve source and amount ──
    source_location_id = ""
    move_resource_id = ""
    move_mass_kg = max(0.0, float(req.amount or 0.0))
    source_ship_state: Optional[Dict[str, Any]] = None
    source_resource_row: Optional[sqlite3.Row] = None

    if source_kind == "ship_resource":
        source_ship_state = m._load_ship_inventory_state(conn, source_id)
        if not source_ship_state["is_docked"]:
            raise HTTPException(status_code=400, detail="Source ship must be docked")
        source_location_id = str(source_ship_state["location_id"])

        move_resource_id = source_key
        src_resource = next(
            (
                item
                for item in (source_ship_state.get("resources") or [])
                if str(item.get("resource_id") or item.get("item_id") or "").strip() == move_resource_id
            ),
            None,
        )
        available_mass = max(0.0, float((src_resource or {}).get("mass_kg") or 0.0))
        if not move_resource_id or available_mass <= 1e-9:
            raise HTTPException(status_code=400, detail="Source ship has no transferable cargo for that resource")
        if move_mass_kg <= 1e-9:
            move_mass_kg = available_mass
        move_mass_kg = max(0.0, min(move_mass_kg, available_mass))
    else:
        # location_resource
        source_location_id = source_id
        m._get_location_row(conn, source_location_id)
        source_resource_row = m._resource_stack_row(
            conn,
            source_location_id,
            source_key,
            corp_id=corp_id or "",
        )
        payload = json.loads(source_resource_row["payload_json"] or "{}")
        move_resource_id = str(payload.get("resource_id") or source_resource_row["item_id"] or "").strip()
        available_mass = max(0.0, float(source_resource_row["mass_kg"] or 0.0))
        if not move_resource_id or available_mass <= 1e-9:
            raise HTTPException(status_code=400, detail="Source resource stack has no transferable cargo")
        if move_mass_kg <= 1e-9:
            move_mass_kg = available_mass
        move_mass_kg = max(0.0, min(move_mass_kg, available_mass))

    if move_mass_kg <= 1e-9:
        raise HTTPException(status_code=400, detail="Nothing to transfer")

    if source_location_id != target_location_id:
        raise HTTPException(status_code=400, detail="Inventories must be at the same location")

    if source_kind == "ship_resource" and target_kind == "ship" and source_id == target_id:
        raise HTTPException(status_code=400, detail="Cannot transfer cargo to the same ship")

    accepted_mass_kg = move_mass_kg
    try:
        conn.execute("BEGIN IMMEDIATE")

        # ── Execute source withdrawal ──
        if source_kind == "ship_resource":
            if not source_ship_state:
                raise HTTPException(status_code=500, detail="Source ship state unavailable")
            try:
                taken = m.remove_cargo_from_ship(conn, source_id, move_resource_id, accepted_mass_kg)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            accepted_mass_kg = taken
            if accepted_mass_kg <= 1e-9:
                raise HTTPException(status_code=400, detail="Source ship has no transferable cargo")
            # Update fuel tracking if water
            source_fuel_kg = max(0.0, float(source_ship_state["fuel_kg"] or 0.0))
            if move_resource_id.lower() == "water":
                source_fuel_kg = max(0.0, source_fuel_kg - accepted_mass_kg)
            m._persist_ship_inventory_state(
                conn,
                ship_id=str(source_ship_state["row"]["id"]),
                parts=list(source_ship_state["parts"]),
                fuel_kg=source_fuel_kg,
            )
        else:
            if not source_resource_row:
                raise HTTPException(status_code=500, detail="Source resource stack unavailable")
            m._consume_location_resource_mass(conn, source_resource_row, accepted_mass_kg)

        # ── Execute target deposit ──
        if target_kind == "location":
            m.add_resource_to_location_inventory(conn, target_location_id, move_resource_id, accepted_mass_kg, corp_id=corp_id or "")
        else:
            if not target_ship_state:
                raise HTTPException(status_code=500, detail="Target ship state unavailable")
            try:
                accepted = m.add_cargo_to_ship(conn, target_id, move_resource_id, accepted_mass_kg)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            accepted_mass_kg = accepted
            # Update fuel tracking if water
            target_fuel_kg = max(0.0, float(target_ship_state["fuel_kg"] or 0.0))
            if move_resource_id.lower() == "water":
                target_fuel_kg += accepted_mass_kg
            m._persist_ship_inventory_state(
                conn,
                ship_id=str(target_ship_state["row"]["id"]),
                parts=list(target_ship_state["parts"]),
                fuel_kg=target_fuel_kg,
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "ok": True,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_key": source_key,
        "target_kind": target_kind,
        "target_id": target_id,
        "resource_id": move_resource_id,
        "moved_mass_kg": accepted_mass_kg,
        "location_id": source_location_id,
    }


@router.post("/api/stack/transfer")
def api_stack_transfer(req: StackTransferReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    source_kind = str(req.source_kind or "").strip().lower()
    source_id = str(req.source_id or "").strip()
    source_key = str(req.source_key or "").strip()
    target_kind = str(req.target_kind or "").strip().lower()
    target_id = str(req.target_id or "").strip()

    if source_kind not in {"ship_part", "location_part"}:
        raise HTTPException(status_code=400, detail="source_kind must be ship_part or location_part")
    if not source_id or not source_key:
        raise HTTPException(status_code=400, detail="source_id and source_key are required")
    if target_kind not in {"ship", "location"}:
        raise HTTPException(status_code=400, detail="target_kind must be ship or location")
    if not target_id:
        raise HTTPException(status_code=400, detail="target_id is required")

    user = require_login(conn, request)
    corp_id = str(user.get("corp_id") or "") if hasattr(user, "get") else ""
    # Verify ownership of referenced ships
    if source_kind == "ship_part":
        _check_ship_ownership(conn, user, source_id)
    if target_kind == "ship":
        _check_ship_ownership(conn, user, target_id)

    _main().settle_arrivals(conn, game_now_s())

    source_location_id = ""
    source_ship: Optional[Dict[str, Any]] = None
    source_parts: List[Dict[str, Any]] = []
    source_part_index: Optional[int] = None
    source_part_row: Optional[sqlite3.Row] = None
    moved_part: Dict[str, Any]

    if source_kind == "ship_part":
        source_ship = _main()._load_ship_inventory_state(conn, source_id)
        if not source_ship["is_docked"]:
            raise HTTPException(status_code=400, detail="Source ship must be docked")

        try:
            source_part_index = int(source_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="source_key must be a ship part index") from exc

        source_parts = list(source_ship.get("parts") or [])
        if source_part_index < 0 or source_part_index >= len(source_parts):
            raise HTTPException(status_code=404, detail="Source part not found")

        source_location_id = str(source_ship["location_id"])
        moved_part = dict(source_parts[source_part_index]) if isinstance(source_parts[source_part_index], dict) else {"item_id": "part"}
    else:
        source_location_id = str(_main()._get_location_row(conn, source_id)["id"])
        source_part_row = _main()._part_stack_row(
            conn,
            source_location_id,
            source_key,
            corp_id=corp_id,
        )
        moved_part = _main()._consume_location_part_unit(conn, source_part_row)

    if target_kind == "ship":
        if source_kind == "ship_part" and target_id == source_id:
            raise HTTPException(status_code=400, detail="Cannot transfer a part to the same ship")

        target_ship = _main()._load_ship_inventory_state(conn, target_id)
        if not target_ship["is_docked"]:
            raise HTTPException(status_code=400, detail="Target ship must be docked")

        target_location_id = str(target_ship["location_id"])
        if target_location_id != source_location_id:
            raise HTTPException(status_code=400, detail="Stacks must be at the same location")

        target_parts = list(target_ship.get("parts") or [])
        if source_kind == "ship_part":
            if source_part_index is None:
                raise HTTPException(status_code=500, detail="Missing source part index")
            source_parts.pop(source_part_index)
        target_parts.append(moved_part)

        if source_kind == "ship_part":
            if not source_ship:
                raise HTTPException(status_code=500, detail="Source ship state unavailable")
            source_fuel_kg = max(0.0, float(source_ship.get("fuel_kg") or 0.0))
            _main()._persist_ship_inventory_state(
                conn,
                ship_id=str(source_ship["row"]["id"]),
                parts=source_parts,
                fuel_kg=source_fuel_kg,
            )

        target_fuel_kg = max(0.0, float(target_ship.get("fuel_kg") or 0.0))
        _main()._persist_ship_inventory_state(
            conn,
            ship_id=str(target_ship["row"]["id"]),
            parts=target_parts,
            fuel_kg=target_fuel_kg,
        )
        destination_location_id = target_location_id
    else:
        loc_row = _main()._get_location_row(conn, target_id)
        destination_location_id = str(loc_row["id"])
        if destination_location_id != source_location_id:
            raise HTTPException(status_code=400, detail="Stacks must be at the same location")

        if source_kind == "ship_part":
            if source_part_index is None:
                raise HTTPException(status_code=500, detail="Missing source part index")
            source_parts.pop(source_part_index)

            if not source_ship:
                raise HTTPException(status_code=500, detail="Source ship state unavailable")
            source_fuel_kg = max(0.0, float(source_ship.get("fuel_kg") or 0.0))
            _main()._persist_ship_inventory_state(
                conn,
                ship_id=str(source_ship["row"]["id"]),
                parts=source_parts,
                fuel_kg=source_fuel_kg,
            )

        _main().add_part_to_location_inventory(conn, destination_location_id, moved_part, count=1.0, corp_id=corp_id)

    conn.commit()
    return {
        "ok": True,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_key": source_key,
        "target_kind": target_kind,
        "target_id": target_id,
        "location_id": source_location_id,
        "moved_part_item_id": str(moved_part.get("item_id") or moved_part.get("id") or moved_part.get("type") or "part"),
        "moved_part_name": str(moved_part.get("name") or moved_part.get("item_id") or "Part"),
    }


# ──────────────────────────────────────────────────────────────
# Unified Hangar endpoint — combines stack + inventory + stats
# ──────────────────────────────────────────────────────────────

@router.get("/api/hangar/context/{ship_id}")
def api_hangar_context(ship_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """
    Unified endpoint returning ship modules, stats, power balance,
    inventory (containers + cargo), and all sibling ships/location
    inventory at the same dock — everything needed for the Hangar window.
    """
    sid = str(ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    user = require_login(conn, request)
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    _check_ship_ownership(conn, user, sid)
    m = _main()
    m.settle_arrivals(conn, game_now_s())
    conn.commit()

    anchor_ship = m._load_ship_inventory_state(conn, sid)
    if not anchor_ship["is_docked"]:
        raise HTTPException(status_code=400, detail="Ship must be docked to open hangar")

    location_id = str(anchor_ship["location_id"])
    loc_row = m._get_location_row(conn, location_id)
    location_name = str(loc_row["name"])

    # ── Build entity list ──
    entities: List[Dict[str, Any]] = []

    # 1. Location entity
    location_payload = m.get_location_inventory_payload(conn, location_id, corp_id=corp_id)
    loc_inv_items = m._inventory_items_for_location(location_payload)
    loc_stack_items = m._stack_items_for_location(location_payload)
    entities.append({
        "entity_kind": "location",
        "id": location_id,
        "name": f"{location_name} Site Inventory",
        "location_id": location_id,
        "parts": [],
        "stats": None,
        "power_balance": None,
        "inventory_items": loc_inv_items,
        "cargo_summary": None,
        "stack_items": loc_stack_items,
    })

    # 2. All docked ships at this location (own corp only)
    if corp_id:
        ship_rows = conn.execute(
            """
            SELECT id, name
            FROM ships
            WHERE location_id=? AND arrives_at IS NULL AND corp_id=?
            ORDER BY name, id
            """,
            (location_id, corp_id),
        ).fetchall()
    else:
        ship_rows = conn.execute(
            """
            SELECT id, name
            FROM ships
            WHERE location_id=? AND arrives_at IS NULL
            ORDER BY name, id
            """,
            (location_id,),
        ).fetchall()

    for sr in ship_rows:
        ship_state = m._load_ship_inventory_state(conn, str(sr["id"]))
        parts = ship_state["parts"]
        fuel_kg = ship_state["fuel_kg"]

        # Compute stats
        stats = m.derive_ship_stats_from_parts(parts, current_fuel_kg=fuel_kg)
        dv = m.compute_delta_v_remaining_m_s(
            stats["dry_mass_kg"], stats["fuel_kg"], stats["isp_s"]
        )
        wet_mass = m.compute_wet_mass_kg(stats["dry_mass_kg"], stats["fuel_kg"])
        accel_g = m.compute_acceleration_gs(
            stats["dry_mass_kg"], stats["fuel_kg"], stats["thrust_kn"]
        )
        power_balance = catalog_service.compute_power_balance(parts)

        entities.append({
            "entity_kind": "ship",
            "id": str(sr["id"]),
            "name": str(sr["name"]),
            "location_id": location_id,
            "parts": parts,
            "stats": {
                "dry_mass_kg": stats["dry_mass_kg"],
                "fuel_kg": stats["fuel_kg"],
                "fuel_capacity_kg": stats["fuel_capacity_kg"],
                "wet_mass_kg": wet_mass,
                "isp_s": stats["isp_s"],
                "thrust_kn": stats["thrust_kn"],
                "delta_v_remaining_m_s": dv,
                "accel_g": accel_g,
            },
            "power_balance": power_balance,
            "inventory_items": m._inventory_items_for_ship(ship_state),
            "cargo_summary": ship_state.get("cargo_summary", {}),
            "stack_items": m._stack_items_for_ship(ship_state),
        })

    return {
        "anchor": {
            "kind": "ship",
            "id": sid,
            "name": str(anchor_ship["row"]["name"]),
            "location_id": location_id,
        },
        "location": {
            "id": location_id,
            "name": location_name,
        },
        "entities": entities,
    }


# ──────────────────────────────────────────────────────────────
# Location-based cargo context — for Sites cargo transfer tab
# ──────────────────────────────────────────────────────────────

@router.get("/api/cargo/context/{location_id}")
def api_cargo_context(location_id: str, request: Request, facility_id: str = "", conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """
    Location-centric cargo context. Returns site inventory and every
    docked ship's containers/cargo at this location — everything the
    Sites cargo-transfer tab needs.
    If facility_id is provided, the site inventory is scoped to that facility.
    """
    lid = str(location_id or "").strip()
    if not lid:
        raise HTTPException(status_code=400, detail="location_id is required")
    fid = str(facility_id or "").strip()

    user = require_login(conn, request)
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    m = _main()
    m.settle_arrivals(conn, game_now_s())
    conn.commit()

    loc_row = m._get_location_row(conn, lid)
    location_name = str(loc_row["name"])
    body_name = _resolve_body_name(conn, lid)

    my_facilities: List[Dict[str, Any]] = []
    selected_facility_name = ""
    corp_id_str = str(corp_id or "")
    if corp_id_str:
        import facility_service
        all_facilities = facility_service.list_facilities_at_location(
            conn,
            lid,
            viewer_corp_id=corp_id_str,
        )
        my_facilities = [f for f in all_facilities if f.get("is_mine")]
        if fid:
            selected = next((f for f in my_facilities if str(f.get("id") or "") == fid), None)
            if selected is None:
                raise HTTPException(status_code=403, detail="You do not own this facility")
            selected_facility_name = str(selected.get("name") or "")

    # ── Build entity list ──
    entities: List[Dict[str, Any]] = []

    # 1. Location / facility entity
    location_payload = m.get_location_inventory_payload(conn, lid, corp_id=corp_id)
    loc_inv_items = m._inventory_items_for_location(location_payload)
    loc_stack_items = m._stack_items_for_location(location_payload)
    # Resolve facility name for display
    facility_name = selected_facility_name
    if fid and not facility_name:
        fac_row = conn.execute("SELECT name FROM facilities WHERE id = ?", (fid,)).fetchone()
        facility_name = str(fac_row["name"]) if fac_row else fid
    entity_label = f"{facility_name} Inventory" if facility_name else f"{location_name} Site Inventory"

    entities.append({
        "entity_kind": "location",
        "id": lid,
        "name": entity_label,
        "location_id": lid,
        "facility_id": fid,
        "parts": [],
        "stats": None,
        "power_balance": None,
        "inventory_items": loc_inv_items,
        "cargo_summary": None,
        "stack_items": loc_stack_items,
    })

    # 2. All docked ships at this location (own corp only)
    if corp_id:
        ship_rows = conn.execute(
            """
            SELECT id, name
            FROM ships
            WHERE location_id=? AND arrives_at IS NULL AND corp_id=?
            ORDER BY name, id
            """,
            (lid, corp_id),
        ).fetchall()
    else:
        ship_rows = conn.execute(
            """
            SELECT id, name
            FROM ships
            WHERE location_id=? AND arrives_at IS NULL
            ORDER BY name, id
            """,
            (lid,),
        ).fetchall()

    for sr in ship_rows:
        ship_state = m._load_ship_inventory_state(conn, str(sr["id"]))
        parts = ship_state["parts"]
        fuel_kg = ship_state["fuel_kg"]

        stats = m.derive_ship_stats_from_parts(parts, current_fuel_kg=fuel_kg)
        dv = m.compute_delta_v_remaining_m_s(
            stats["dry_mass_kg"], stats["fuel_kg"], stats["isp_s"]
        )
        wet_mass = m.compute_wet_mass_kg(stats["dry_mass_kg"], stats["fuel_kg"])

        entities.append({
            "entity_kind": "ship",
            "id": str(sr["id"]),
            "name": str(sr["name"]),
            "location_id": lid,
            "parts": parts,
            "stats": {
                "dry_mass_kg": stats["dry_mass_kg"],
                "fuel_kg": stats["fuel_kg"],
                "fuel_capacity_kg": stats["fuel_capacity_kg"],
                "wet_mass_kg": wet_mass,
                "isp_s": stats["isp_s"],
                "thrust_kn": stats["thrust_kn"],
                "delta_v_remaining_m_s": dv,
            },
            "inventory_items": m._inventory_items_for_ship(ship_state),
            "cargo_summary": ship_state.get("cargo_summary", {}),
            "stack_items": m._stack_items_for_ship(ship_state),
        })

    return {
        "facility_id": fid,
        "facility_name": facility_name,
        "location_id": lid,
        "location_name": location_name,
        "body_name": body_name,
        "my_facilities": my_facilities,
        "location": {
            "id": lid,
            "name": location_name,
        },
        "entities": entities,
    }
