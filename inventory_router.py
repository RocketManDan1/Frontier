"""
Inventory API routes.

Extracted from main.py — handles:
  /api/inventory/location/{location_id}
  /api/inventory/ship/{ship_id}
  /api/inventory/context/{kind}/{entity_id}
  /api/stack/context/ship/{ship_id}
  /api/inventory/transfer
  /api/stack/transfer
"""

import json
import sqlite3
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth_service import require_login
from db import get_db
from sim_service import game_now_s

router = APIRouter(tags=["inventory"])


def _main():
    """Lazy import to avoid circular dependency with main.py."""
    import main
    return main


@router.get("/api/inventory/location/{location_id}")
def api_location_inventory(location_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    loc_id = (location_id or "").strip()
    if not loc_id:
        raise HTTPException(status_code=400, detail="location_id is required")

    require_login(conn, request)
    loc = conn.execute("SELECT id,is_group,name FROM locations WHERE id=?", (loc_id,)).fetchone()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    if int(loc["is_group"]):
        raise HTTPException(status_code=400, detail="location_id must be a non-group location")

    payload = _main().get_location_inventory_payload(conn, loc_id)
    payload["location_name"] = str(loc["name"])
    return payload


@router.get("/api/inventory/ship/{ship_id}")
def api_ship_inventory(ship_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_login(conn, request)
    _main().settle_arrivals(conn, game_now_s())
    state = _main()._load_ship_inventory_state(conn, ship_id)
    row = state["row"]
    payload = {
        "ship_id": str(row["id"]),
        "ship_name": str(row["name"]),
        "location_id": state["location_id"],
        "is_docked": bool(state["is_docked"]),
        "items": _main()._inventory_items_for_ship(state),
        "container_groups": _main()._inventory_container_groups_for_ship(state),
        "capacity_summary": state["capacity_summary"],
        "containers": state["containers"],
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

    require_login(conn, request)
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
        location_payload = _main().get_location_inventory_payload(conn, location_id)
        inventories.append(
            {
                "inventory_kind": "location",
                "id": location_id,
                "name": f"{location_name} Site Inventory",
                "location_id": location_id,
                "capacity_summary": None,
                "items": _main()._inventory_items_for_location(location_payload),
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

        for ship_row in ship_rows:
            ship_state = _main()._load_ship_inventory_state(conn, str(ship_row["id"]))
            inventories.append(
                {
                    "inventory_kind": "ship",
                    "id": str(ship_row["id"]),
                    "name": str(ship_row["name"]),
                    "location_id": location_id,
                    "capacity_summary": ship_state.get("capacity_summary"),
                    "items": _main()._inventory_items_for_ship(ship_state),
                    "container_groups": _main()._inventory_container_groups_for_ship(ship_state),
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
                "capacity_summary": ship_state.get("capacity_summary"),
                "items": _main()._inventory_items_for_ship(ship_state),
                "container_groups": _main()._inventory_container_groups_for_ship(ship_state),
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

    require_login(conn, request)
    _main().settle_arrivals(conn, game_now_s())
    conn.commit()

    anchor_ship = _main()._load_ship_inventory_state(conn, sid)
    if not anchor_ship["is_docked"]:
        raise HTTPException(status_code=400, detail="Ship must be docked to view transferable stack")

    location_id = str(anchor_ship["location_id"])
    loc_row = _main()._get_location_row(conn, location_id)
    location_name = str(loc_row["name"])

    stacks: List[Dict[str, Any]] = []
    location_payload = _main().get_location_inventory_payload(conn, location_id)
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
    source_kind: Literal["ship_container", "ship_resource", "location_resource"]
    source_id: str
    source_key: str
    target_kind: Literal["ship", "location", "ship_container"]
    target_id: str
    target_key: Optional[str] = None
    amount: Optional[float] = None


class StackTransferReq(BaseModel):
    source_kind: Literal["ship_part", "location_part"]
    source_id: str
    source_key: str
    target_kind: Literal["ship", "location"]
    target_id: str


@router.post("/api/inventory/transfer")
def api_inventory_transfer(req: InventoryTransferReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    source_kind = str(req.source_kind or "").strip().lower()
    source_id = str(req.source_id or "").strip()
    source_key = str(req.source_key or "").strip()
    target_kind = str(req.target_kind or "").strip().lower()
    target_id = str(req.target_id or "").strip()
    target_key = str(req.target_key or "").strip()

    if source_kind not in {"ship_container", "ship_resource", "location_resource"}:
        raise HTTPException(status_code=400, detail="source_kind must be ship_container, ship_resource, or location_resource")
    if target_kind not in {"ship", "location", "ship_container"}:
        raise HTTPException(status_code=400, detail="target_kind must be ship, location, or ship_container")
    if not source_id or not source_key:
        raise HTTPException(status_code=400, detail="source_id and source_key are required")
    if not target_id:
        raise HTTPException(status_code=400, detail="target_id is required")
    if target_kind == "ship_container" and not target_key:
        raise HTTPException(status_code=400, detail="target_key is required for target_kind=ship_container")

    require_login(conn, request)
    _main().settle_arrivals(conn, game_now_s())

    resources = _main().load_resource_catalog()

    target_location_id = ""
    target_ship_state: Optional[Dict[str, Any]] = None
    target_container_idx: Optional[int] = None
    if target_kind == "location":
        loc = _main()._get_location_row(conn, target_id)
        target_location_id = str(loc["id"])
    else:
        target_ship_state = _main()._load_ship_inventory_state(conn, target_id)
        if not target_ship_state["is_docked"]:
            raise HTTPException(status_code=400, detail="Target ship must be docked")
        target_location_id = str(target_ship_state["location_id"])
        if target_kind == "ship_container":
            try:
                target_container_idx = int(target_key)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="target_key must be a ship container index") from exc

    source_location_id = ""
    move_resource_id = ""
    move_mass_kg = max(0.0, float(req.amount or 0.0))
    source_ship_state: Optional[Dict[str, Any]] = None
    source_resource_row: Optional[sqlite3.Row] = None
    source_container_idx: Optional[int] = None

    if source_kind in {"ship_container", "ship_resource"}:
        source_ship_state = _main()._load_ship_inventory_state(conn, source_id)
        if not source_ship_state["is_docked"]:
            raise HTTPException(status_code=400, detail="Source ship must be docked")
        source_location_id = str(source_ship_state["location_id"])

        if source_kind == "ship_container":
            try:
                source_container_idx = int(source_key)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="source_key must be a ship container index") from exc

            src_container = next(
                (c for c in source_ship_state["containers"] if int(c.get("container_index") or -1) == source_container_idx),
                None,
            )
            if not src_container:
                raise HTTPException(status_code=404, detail="Source container not found")

            move_resource_id = str(src_container.get("resource_id") or "").strip()
            available_mass = max(0.0, float(src_container.get("cargo_mass_kg") or 0.0))
            if not move_resource_id or available_mass <= 1e-9:
                raise HTTPException(status_code=400, detail="Source container has no transferable cargo")
            if move_mass_kg <= 1e-9:
                move_mass_kg = available_mass
            move_mass_kg = max(0.0, min(move_mass_kg, available_mass))
        else:
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
        source_location_id = source_id
        _main()._get_location_row(conn, source_location_id)
        source_resource_row = _main()._resource_stack_row(conn, source_location_id, source_key)
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

    if source_kind in {"ship_container", "ship_resource"} and target_kind == "ship" and source_id == target_id:
        raise HTTPException(status_code=400, detail="Cannot transfer cargo to the same ship")
    if source_kind == "ship_resource" and target_kind == "ship_container" and source_id == target_id:
        raise HTTPException(status_code=400, detail="Use a specific source container for intra-ship container transfer")

    if source_kind == "ship_container" and target_kind == "ship_container" and source_id == target_id:
        if not source_ship_state:
            raise HTTPException(status_code=500, detail="Source ship state unavailable")
        if source_container_idx is None or target_container_idx is None:
            raise HTTPException(status_code=400, detail="Source and target container indices are required")
        if source_container_idx == target_container_idx:
            raise HTTPException(status_code=400, detail="Cannot transfer cargo to the same container")

        ship_parts = list(source_ship_state["parts"])
        ship_containers = list(source_ship_state["containers"])

        src_container = next(
            (c for c in ship_containers if int(c.get("container_index") or -1) == source_container_idx),
            None,
        )
        dst_container = next(
            (c for c in ship_containers if int(c.get("container_index") or -1) == target_container_idx),
            None,
        )
        if not src_container:
            raise HTTPException(status_code=404, detail="Source container not found")
        if not dst_container:
            raise HTTPException(status_code=404, detail="Target container not found")

        src_mass = max(0.0, float(src_container.get("cargo_mass_kg") or 0.0))
        if src_mass <= 1e-9:
            raise HTTPException(status_code=400, detail="Source container has no transferable cargo")

        resource_meta = resources.get(move_resource_id) or {}
        resource_phase = _main().classify_resource_phase(
            move_resource_id,
            str(resource_meta.get("name") or move_resource_id),
            float(resource_meta.get("mass_per_m3_kg") or 0.0),
        )

        dst_cap = max(0.0, float(dst_container.get("capacity_m3") or 0.0))
        dst_used = max(0.0, float(dst_container.get("used_m3") or 0.0))
        dst_free = max(0.0, dst_cap - dst_used)
        if dst_free <= 1e-9:
            raise HTTPException(status_code=400, detail="Target container has no free capacity")

        dst_phase = str(dst_container.get("tank_phase") or dst_container.get("phase") or "solid").strip().lower()
        if dst_phase not in {"solid", "liquid", "gas"}:
            dst_phase = "solid"
        if dst_phase != resource_phase:
            raise HTTPException(status_code=400, detail="Target container phase is not compatible")

        dst_resource = str(dst_container.get("resource_id") or "").strip()
        if dst_resource and dst_resource != move_resource_id:
            raise HTTPException(status_code=400, detail="Target container already stores a different resource")

        resolved_density = max(
            0.0,
            float(
                dst_container.get("density_kg_m3")
                or src_container.get("density_kg_m3")
                or (resource_meta.get("mass_per_m3_kg") or 0.0)
            ),
        )
        if resolved_density <= 0.0:
            raise HTTPException(status_code=400, detail="Resource density is unavailable for transfer")

        max_target_mass = dst_free * resolved_density
        accepted_mass_kg = min(move_mass_kg, src_mass, max_target_mass)
        if accepted_mass_kg <= 1e-9:
            raise HTTPException(status_code=400, detail="Target container rejected transfer")

        src_density = max(1e-9, float(src_container.get("density_kg_m3") or resolved_density))
        src_used = max(0.0, float(src_container.get("used_m3") or 0.0))
        src_next_mass = max(0.0, src_mass - accepted_mass_kg)
        src_next_used = max(0.0, src_used - (accepted_mass_kg / src_density))

        dst_mass = max(0.0, float(dst_container.get("cargo_mass_kg") or 0.0))
        dst_next_mass = dst_mass + accepted_mass_kg
        dst_next_used = dst_used + (accepted_mass_kg / resolved_density)

        if source_container_idx < 0 or source_container_idx >= len(ship_parts):
            raise HTTPException(status_code=400, detail="Source container index is invalid")
        if target_container_idx < 0 or target_container_idx >= len(ship_parts):
            raise HTTPException(status_code=400, detail="Target container index is invalid")

        ship_parts[source_container_idx] = _main()._apply_ship_container_fill(
            ship_parts[source_container_idx],
            resource_id=move_resource_id,
            cargo_mass_kg=src_next_mass,
            used_m3=src_next_used,
            density_kg_m3=src_density,
        )
        ship_parts[target_container_idx] = _main()._apply_ship_container_fill(
            ship_parts[target_container_idx],
            resource_id=move_resource_id,
            cargo_mass_kg=dst_next_mass,
            used_m3=dst_next_used,
            density_kg_m3=resolved_density,
        )

        source_fuel_kg = max(0.0, float(source_ship_state["fuel_kg"] or 0.0))
        _main()._persist_ship_inventory_state(
            conn,
            ship_id=str(source_ship_state["row"]["id"]),
            parts=ship_parts,
            fuel_kg=source_fuel_kg,
        )

        conn.commit()
        return {
            "ok": True,
            "source_kind": source_kind,
            "source_id": source_id,
            "source_key": source_key,
            "target_kind": target_kind,
            "target_id": target_id,
            "target_key": target_key,
            "resource_id": move_resource_id,
            "moved_mass_kg": accepted_mass_kg,
            "destroyed_mass_kg": 0.0,
            "destroyed_in_space": False,
            "location_id": source_location_id,
        }

    accepted_mass_kg = move_mass_kg
    destroyed_mass_kg = 0.0
    density = max(0.0, float((resources.get(move_resource_id) or {}).get("mass_per_m3_kg") or 0.0))

    if target_kind == "location":
        destroyed_mass_kg = accepted_mass_kg
    else:
        if not target_ship_state:
            raise HTTPException(status_code=500, detail="Target ship state unavailable")

        target_parts = list(target_ship_state["parts"])
        target_containers = list(target_ship_state["containers"])

        resource_meta = resources.get(move_resource_id) or {}
        resource_phase = _main().classify_resource_phase(
            move_resource_id,
            str(resource_meta.get("name") or move_resource_id),
            float(resource_meta.get("mass_per_m3_kg") or density or 0.0),
        )

        compatible: List[Tuple[Dict[str, Any], float, float]] = []
        total_free_mass_kg = 0.0
        for container in target_containers:
            if target_container_idx is not None and int(container.get("container_index") or -1) != target_container_idx:
                continue
            cap = max(0.0, float(container.get("capacity_m3") or 0.0))
            used = max(0.0, float(container.get("used_m3") or 0.0))
            free = max(0.0, cap - used)
            tank_phase = str(container.get("tank_phase") or container.get("phase") or "solid").strip().lower()
            container_resource = str(container.get("resource_id") or "").strip()
            if free <= 1e-9:
                continue
            if tank_phase not in {"solid", "liquid", "gas"}:
                tank_phase = "solid"
            if tank_phase != resource_phase:
                continue
            if container_resource and container_resource != move_resource_id:
                continue
            resolved_density = max(0.0, float(container.get("density_kg_m3") or density or 0.0))
            if resolved_density <= 0.0:
                continue
            free_mass_kg = free * resolved_density
            if free_mass_kg <= 1e-9:
                continue
            compatible.append((container, resolved_density, free_mass_kg))
            total_free_mass_kg += free_mass_kg

        if not compatible:
            raise HTTPException(status_code=400, detail="No compatible destination tank with free capacity")

        accepted_mass_kg = min(accepted_mass_kg, total_free_mass_kg)
        if accepted_mass_kg <= 1e-9:
            raise HTTPException(status_code=400, detail="Destination tank has no usable free capacity")

        remaining_to_place = accepted_mass_kg
        for compatible_container, resolved_density, free_mass_kg in compatible:
            if remaining_to_place <= 1e-9:
                break

            to_place = min(remaining_to_place, free_mass_kg)
            idx = int(compatible_container.get("container_index") or -1)
            if idx < 0 or idx >= len(target_parts):
                raise HTTPException(status_code=400, detail="Destination container index is invalid")

            used = max(0.0, float(compatible_container.get("used_m3") or 0.0))
            next_used = used + (to_place / resolved_density)
            next_mass = max(0.0, float(compatible_container.get("cargo_mass_kg") or 0.0)) + to_place
            target_parts[idx] = _main()._apply_ship_container_fill(
                target_parts[idx],
                resource_id=move_resource_id,
                cargo_mass_kg=next_mass,
                used_m3=next_used,
                density_kg_m3=resolved_density,
            )
            remaining_to_place -= to_place

        accepted_mass_kg = max(0.0, accepted_mass_kg - remaining_to_place)
        if accepted_mass_kg <= 1e-9:
            raise HTTPException(status_code=400, detail="Destination tank rejected transfer")

        target_fuel_kg = max(0.0, float(target_ship_state["fuel_kg"] or 0.0))
        if move_resource_id.lower() == "water":
            target_fuel_kg += accepted_mass_kg

        _main()._persist_ship_inventory_state(
            conn,
            ship_id=str(target_ship_state["row"]["id"]),
            parts=target_parts,
            fuel_kg=target_fuel_kg,
        )

    if source_kind in {"ship_container", "ship_resource"}:
        if not source_ship_state:
            raise HTTPException(status_code=500, detail="Source ship state unavailable")
        src_parts = list(source_ship_state["parts"])
        src_containers = list(source_ship_state["containers"])
        consumed_mass_kg = 0.0

        if source_kind == "ship_container":
            src_idx = int(source_key)
            src_container = next((c for c in src_containers if int(c.get("container_index") or -1) == src_idx), None)
            if not src_container:
                raise HTTPException(status_code=404, detail="Source container not found")

            src_density = max(1e-9, float(src_container.get("density_kg_m3") or density or 0.0))
            src_used = max(0.0, float(src_container.get("used_m3") or 0.0))
            src_mass = max(0.0, float(src_container.get("cargo_mass_kg") or 0.0))
            consumed_mass_kg = min(accepted_mass_kg, src_mass)
            next_src_mass = max(0.0, src_mass - consumed_mass_kg)
            next_src_used = max(0.0, src_used - (consumed_mass_kg / src_density))

            if src_idx < 0 or src_idx >= len(src_parts):
                raise HTTPException(status_code=400, detail="Source container index is invalid")

            src_parts[src_idx] = _main()._apply_ship_container_fill(
                src_parts[src_idx],
                resource_id=move_resource_id,
                cargo_mass_kg=next_src_mass,
                used_m3=next_src_used,
                density_kg_m3=src_density,
            )
        else:
            remaining_to_take = accepted_mass_kg
            for src_container in src_containers:
                if remaining_to_take <= 1e-9:
                    break

                container_resource = str(src_container.get("resource_id") or "").strip()
                if container_resource != move_resource_id:
                    continue

                src_mass = max(0.0, float(src_container.get("cargo_mass_kg") or 0.0))
                if src_mass <= 1e-9:
                    continue

                src_idx = int(src_container.get("container_index") or -1)
                if src_idx < 0 or src_idx >= len(src_parts):
                    continue

                src_density = max(1e-9, float(src_container.get("density_kg_m3") or density or 0.0))
                src_used = max(0.0, float(src_container.get("used_m3") or 0.0))
                take_mass = min(src_mass, remaining_to_take)
                next_src_mass = max(0.0, src_mass - take_mass)
                next_src_used = max(0.0, src_used - (take_mass / src_density))

                src_parts[src_idx] = _main()._apply_ship_container_fill(
                    src_parts[src_idx],
                    resource_id=move_resource_id,
                    cargo_mass_kg=next_src_mass,
                    used_m3=next_src_used,
                    density_kg_m3=src_density,
                )
                remaining_to_take -= take_mass
                consumed_mass_kg += take_mass

            if consumed_mass_kg <= 1e-9:
                raise HTTPException(status_code=400, detail="Source ship has no transferable cargo")

        accepted_mass_kg = consumed_mass_kg

        source_fuel_kg = max(0.0, float(source_ship_state["fuel_kg"] or 0.0))
        if move_resource_id.lower() == "water":
            source_fuel_kg = max(0.0, source_fuel_kg - accepted_mass_kg)

        _main()._persist_ship_inventory_state(
            conn,
            ship_id=str(source_ship_state["row"]["id"]),
            parts=src_parts,
            fuel_kg=source_fuel_kg,
        )
    else:
        if not source_resource_row:
            raise HTTPException(status_code=500, detail="Source resource stack unavailable")
        _main()._consume_location_resource_mass(conn, source_resource_row, accepted_mass_kg)

    conn.commit()
    return {
        "ok": True,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_key": source_key,
        "target_kind": target_kind,
        "target_id": target_id,
        "target_key": target_key,
        "resource_id": move_resource_id,
        "moved_mass_kg": accepted_mass_kg,
        "destroyed_mass_kg": destroyed_mass_kg,
        "destroyed_in_space": destroyed_mass_kg > 1e-9,
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

    require_login(conn, request)
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
        source_part_row = _main()._part_stack_row(conn, source_location_id, source_key)
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

        _main().add_part_to_location_inventory(conn, destination_location_id, moved_part, count=1.0)

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


