"""
Shared game-service functions used by multiple routers.

This module extracts the business logic that was previously embedded
in main.py's 4000+ line monolith.  Routers import from here instead
of reaching across into each other.
"""

import hashlib
import heapq
import json
import math
import os
import re
import sqlite3
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

import catalog_service
import celestial_config
from constants import (
    ITEM_CATEGORIES,
    ITEM_CATEGORY_ALIASES,
    ITEM_CATEGORY_BY_ID,
    NTR_THRUSTER_SPEC,
    RESEARCH_CATEGORIES,
    THRUSTER_RESERVED_LANES,
)
from db import APP_DIR
from sim_service import (
    export_simulation_state,
    game_now_s,
    import_simulation_state,
)


# ── NTR spec (re-exported from constants for backward compat) ────────────
# If anything still does `from game_services import NTR_THRUSTER_SPEC`
# it will get the canonical copy.  The authoritative data lives in
# constants.py now.

# ── Catalog thin-wrappers (lru_cache, delegate to catalog_service) ───────

@lru_cache(maxsize=1)
def _location_metadata_by_id() -> Dict[str, Dict[str, Any]]:
    try:
        return celestial_config.load_location_metadata()
    except celestial_config.CelestialConfigError as exc:
        print(f"[celestial-config] metadata load failed: {exc}")
        return {}


def load_thruster_specs_from_items() -> List[Dict[str, Any]]:
    return catalog_service.load_thruster_specs_from_items()


@lru_cache(maxsize=1)
def load_thruster_main_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_thruster_main_catalog()


@lru_cache(maxsize=1)
def load_resource_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_resource_catalog()


@lru_cache(maxsize=1)
def load_storage_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_storage_catalog()


@lru_cache(maxsize=1)
def load_reactor_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_reactor_catalog()


@lru_cache(maxsize=1)
def load_generator_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_generator_catalog()


@lru_cache(maxsize=1)
def load_radiator_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_radiator_catalog()


@lru_cache(maxsize=1)
def load_recipe_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_recipe_catalog()


def canonical_item_category(raw: Any) -> str:
    return catalog_service.canonical_item_category(raw)


# ── Parts / Stats helpers ────────────────────────────────────────────────

def normalize_parts(raw_parts: Any) -> List[Dict[str, Any]]:
    return catalog_service.normalize_parts(
        raw_parts,
        thruster_catalog=load_thruster_main_catalog(),
        storage_catalog=load_storage_catalog(),
        canonical_item_category=canonical_item_category,
        reactor_catalog=load_reactor_catalog(),
        generator_catalog=load_generator_catalog(),
        radiator_catalog=load_radiator_catalog(),
    )


def derive_ship_stats_from_parts(
    parts: List[Dict[str, Any]],
    current_fuel_kg: Optional[float] = None,
) -> Dict[str, float]:
    return catalog_service.derive_ship_stats_from_parts(
        parts,
        resource_catalog=load_resource_catalog(),
        current_fuel_kg=current_fuel_kg,
    )


def compute_wet_mass_kg(dry_mass_kg: float, fuel_kg: float) -> float:
    return catalog_service.compute_wet_mass_kg(dry_mass_kg, fuel_kg)


def compute_acceleration_gs(dry_mass_kg: float, fuel_kg: float, thrust_kn: float) -> float:
    return catalog_service.compute_acceleration_gs(dry_mass_kg, fuel_kg, thrust_kn)


def compute_delta_v_remaining_m_s(dry_mass_kg: float, fuel_kg: float, isp_s: float) -> float:
    return catalog_service.compute_delta_v_remaining_m_s(dry_mass_kg, fuel_kg, isp_s)


def compute_fuel_needed_for_delta_v_kg(dry_mass_kg: float, fuel_kg: float, isp_s: float, dv_m_s: float) -> float:
    return catalog_service.compute_fuel_needed_for_delta_v_kg(dry_mass_kg, fuel_kg, isp_s, dv_m_s)


def normalize_shipyard_item_ids(raw_parts: Any) -> List[str]:
    return catalog_service.normalize_shipyard_item_ids(raw_parts)


def shipyard_parts_from_item_ids(item_ids: List[str]) -> List[Dict[str, Any]]:
    return catalog_service.shipyard_parts_from_item_ids(item_ids, normalize_parts_fn=normalize_parts)


def build_ship_stats_payload(parts: List[Dict[str, Any]], current_fuel_kg: Optional[float] = None) -> Dict[str, float]:
    return catalog_service.build_ship_stats_payload(
        parts,
        resource_catalog=load_resource_catalog(),
        current_fuel_kg=current_fuel_kg,
    )


def build_shipyard_catalog_payload() -> Dict[str, Any]:
    return catalog_service.build_shipyard_catalog_payload(
        thruster_catalog=load_thruster_main_catalog(),
        storage_catalog=load_storage_catalog(),
        resource_catalog=load_resource_catalog(),
        recipe_catalog=load_recipe_catalog(),
        reactor_catalog=load_reactor_catalog(),
        generator_catalog=load_generator_catalog(),
        radiator_catalog=load_radiator_catalog(),
    )


def build_research_payload() -> Dict[str, Any]:
    return catalog_service.build_research_payload()


# ── Resource phase classification ────────────────────────────────────────

def classify_resource_phase(resource_id: str, resource_name: str, density_kg_m3: float) -> str:
    rid = str(resource_id or "").strip().lower()
    rname = str(resource_name or "").strip().lower()
    text = f"{rid} {rname}"

    gas_hints = ("helium", "hydrogen", "nitrogen", "oxygen", "argon", "methane", "deuterium")
    liquid_hints = ("water", "propellant", "hydrolox", "ammonia", "fuel")

    if any(h in text for h in gas_hints):
        return "gas"
    if any(h in text for h in liquid_hints):
        return "liquid"

    d = max(0.0, float(density_kg_m3 or 0.0))
    if d > 0.0:
        if d < 200.0:
            return "gas"
        if d < 2000.0:
            return "liquid"
    return "solid"


# ── Ship inventory helpers ───────────────────────────────────────────────

def _is_storage_part(part: Dict[str, Any]) -> bool:
    capacity_m3 = max(0.0, float(part.get("capacity_m3") or 0.0))
    if capacity_m3 > 0.0:
        return True
    ptype = str(part.get("type") or "").strip().lower()
    pcat = str(part.get("category_id") or "").strip().lower()
    return ptype in {"storage", "cargo"} or pcat in {"storage", "cargo"}


def _has_explicit_container_fill(part: Dict[str, Any]) -> bool:
    if not isinstance(part, dict):
        return False
    for key in (
        "cargo_used_m3",
        "used_m3",
        "fill_m3",
        "stored_m3",
        "current_m3",
        "cargo_mass_kg",
        "contents_mass_kg",
        "stored_mass_kg",
        "current_mass_kg",
    ):
        if key in part:
            return True
    return False


def _harden_ship_parts(parts: List[Dict[str, Any]], fuel_kg: float) -> Tuple[List[Dict[str, Any]], bool]:
    hardened: List[Dict[str, Any]] = []
    changed = False
    resources = load_resource_catalog()

    legacy_water_rows: List[Dict[str, Any]] = []
    legacy_total_capacity_kg = 0.0

    for raw_part in parts or []:
        part = dict(raw_part or {})
        if _is_storage_part(part):
            if not str(part.get("container_uid") or "").strip():
                part["container_uid"] = str(uuid.uuid4())
                changed = True

            resource_id = str(part.get("resource_id") or "").strip().lower()
            capacity_m3 = max(0.0, float(part.get("capacity_m3") or 0.0))
            if resource_id == "water" and capacity_m3 > 0.0 and not _has_explicit_container_fill(part):
                density = max(
                    0.0,
                    float(part.get("mass_per_m3_kg") or (resources.get(resource_id) or {}).get("mass_per_m3_kg") or 0.0),
                )
                if density > 0.0:
                    legacy_water_rows.append(part)
                    legacy_total_capacity_kg += capacity_m3 * density

        hardened.append(part)

    if legacy_water_rows and legacy_total_capacity_kg > 0.0:
        ratio = min(1.0, max(0.0, float(fuel_kg or 0.0)) / legacy_total_capacity_kg)
        for part in legacy_water_rows:
            density = max(
                0.0,
                float(part.get("mass_per_m3_kg") or (resources.get("water") or {}).get("mass_per_m3_kg") or 0.0),
            )
            capacity_m3 = max(0.0, float(part.get("capacity_m3") or 0.0))
            used_m3 = capacity_m3 * ratio
            cargo_mass = used_m3 * density

            for key in ("cargo_used_m3", "used_m3", "fill_m3", "stored_m3", "current_m3"):
                part[key] = used_m3
            for key in ("cargo_mass_kg", "contents_mass_kg", "stored_mass_kg", "current_mass_kg", "water_kg", "fuel_kg"):
                part[key] = cargo_mass
            changed = True

    return hardened, changed


def compute_ship_inventory_containers(parts: List[Dict[str, Any]], current_fuel_kg: float) -> List[Dict[str, Any]]:
    resources = load_resource_catalog()
    rows: List[Dict[str, Any]] = []

    for idx, part in enumerate(parts):
        capacity_m3 = max(0.0, float(part.get("capacity_m3") or 0.0))
        ptype = str(part.get("type") or "").strip().lower()
        pcat = str(part.get("category_id") or "").strip().lower()
        if capacity_m3 <= 0.0 and ptype not in {"storage", "cargo"} and pcat not in {"storage", "cargo"}:
            continue

        resource_id = str(part.get("resource_id") or "").strip()
        resource = resources.get(resource_id) or {}
        resource_name = str(resource.get("name") or resource_id or "Unknown resource")
        density = max(0.0, float(part.get("mass_per_m3_kg") or resource.get("mass_per_m3_kg") or 0.0))

        explicit_m3 = 0.0
        for key in ("cargo_used_m3", "used_m3", "fill_m3", "stored_m3", "current_m3"):
            if key in part:
                explicit_m3 = max(0.0, float(part.get(key) or 0.0))
                break

        explicit_mass_kg = 0.0
        for key in ("cargo_mass_kg", "contents_mass_kg", "stored_mass_kg", "current_mass_kg", "water_kg", "fuel_kg"):
            if key in part:
                explicit_mass_kg = max(0.0, float(part.get(key) or 0.0))
                break

        used_m3 = 0.0
        cargo_mass_kg = 0.0
        if explicit_m3 > 0.0:
            used_m3 = min(capacity_m3, explicit_m3) if capacity_m3 > 0.0 else explicit_m3
            cargo_mass_kg = used_m3 * density if density > 0.0 else explicit_mass_kg
        elif explicit_mass_kg > 0.0 and density > 0.0:
            cargo_mass_kg = explicit_mass_kg
            used_m3 = min(capacity_m3, cargo_mass_kg / density) if capacity_m3 > 0.0 else cargo_mass_kg / density

        dry_mass_kg = max(0.0, float(part.get("mass_kg") or 0.0))

        tank_phase = str(part.get("tank_phase") or "").strip().lower()
        if tank_phase not in {"solid", "liquid", "gas"}:
            tank_phase = classify_resource_phase(resource_id, resource_name, density)
        resource_phase = classify_resource_phase(resource_id, resource_name, density)

        row = {
            "container_index": idx,
            "container_uid": str(part.get("container_uid") or ""),
            "name": str(part.get("name") or f"Container {idx + 1}"),
            "resource_id": resource_id,
            "resource_name": resource_name,
            "phase": tank_phase,
            "tank_phase": tank_phase,
            "resource_phase": resource_phase,
            "capacity_m3": capacity_m3,
            "used_m3": used_m3,
            "density_kg_m3": density,
            "cargo_mass_kg": cargo_mass_kg,
            "dry_mass_kg": dry_mass_kg,
            "total_mass_kg": dry_mass_kg + cargo_mass_kg,
        }

        rows.append(row)

    return rows


def compute_ship_inventory_resources(
    ship_id: str,
    containers: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_resource: Dict[str, Dict[str, Any]] = {}

    for container in containers or []:
        resource_id = str(container.get("resource_id") or "").strip()
        if not resource_id:
            continue

        mass_kg = max(0.0, float(container.get("cargo_mass_kg") or 0.0))
        volume_m3 = max(0.0, float(container.get("used_m3") or 0.0))
        if mass_kg <= 1e-9 and volume_m3 <= 1e-9:
            continue

        phase = str(container.get("resource_phase") or container.get("tank_phase") or container.get("phase") or "solid").strip().lower()
        if phase not in {"solid", "liquid", "gas"}:
            phase = "solid"

        entry = by_resource.get(resource_id)
        if not entry:
            label = str(container.get("resource_name") or resource_id)
            entry = {
                "item_uid": f"ship:{ship_id}:resource:{resource_id}",
                "item_kind": "resource",
                "item_id": resource_id,
                "label": label,
                "subtitle": f"{phase.title()} cargo",
                "category": "resource",
                "resource_id": resource_id,
                "phase": phase,
                "mass_kg": 0.0,
                "volume_m3": 0.0,
                "quantity": 0.0,
                "icon_seed": f"ship_resource::{resource_id}",
                "transfer": {
                    "source_kind": "ship_resource",
                    "source_id": ship_id,
                    "source_key": resource_id,
                    "amount": 0.0,
                },
            }
            by_resource[resource_id] = entry

        entry["mass_kg"] = max(0.0, float(entry.get("mass_kg") or 0.0)) + mass_kg
        entry["volume_m3"] = max(0.0, float(entry.get("volume_m3") or 0.0)) + volume_m3
        entry["quantity"] = max(0.0, float(entry.get("quantity") or 0.0)) + mass_kg
        transfer = entry.get("transfer") if isinstance(entry.get("transfer"), dict) else None
        if transfer is not None:
            transfer["amount"] = max(0.0, float(transfer.get("amount") or 0.0)) + mass_kg

    rows = list(by_resource.values())
    rows.sort(key=lambda r: (str(r.get("phase") or ""), str(r.get("label") or r.get("resource_id") or "")))
    return rows


def compute_ship_capacity_summary(containers: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_phase: Dict[str, Dict[str, float]] = {
        "solid": {"used_m3": 0.0, "capacity_m3": 0.0, "free_m3": 0.0, "utilization": 0.0},
        "liquid": {"used_m3": 0.0, "capacity_m3": 0.0, "free_m3": 0.0, "utilization": 0.0},
        "gas": {"used_m3": 0.0, "capacity_m3": 0.0, "free_m3": 0.0, "utilization": 0.0},
    }

    for container in containers or []:
        phase = str(container.get("tank_phase") or container.get("phase") or "solid").strip().lower()
        if phase not in by_phase:
            phase = "solid"

        cap = max(0.0, float(container.get("capacity_m3") or 0.0))
        used = max(0.0, float(container.get("used_m3") or 0.0))

        by_phase[phase]["capacity_m3"] += cap
        by_phase[phase]["used_m3"] += min(cap, used)

    total_capacity = 0.0
    total_used = 0.0
    for row in by_phase.values():
        row["free_m3"] = max(0.0, row["capacity_m3"] - row["used_m3"])
        row["utilization"] = (row["used_m3"] / row["capacity_m3"]) if row["capacity_m3"] > 1e-9 else 0.0
        total_capacity += row["capacity_m3"]
        total_used += row["used_m3"]

    return {
        "used_m3": total_used,
        "capacity_m3": total_capacity,
        "free_m3": max(0.0, total_capacity - total_used),
        "utilization": (total_used / total_capacity) if total_capacity > 1e-9 else 0.0,
        "by_phase": by_phase,
    }


# ── Location inventory helpers ───────────────────────────────────────────

def _json_dumps_stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _part_stack_identity(part: Dict[str, Any]) -> Tuple[str, str, str, str]:
    normalized = normalize_parts([part])
    payload_part = normalized[0] if normalized else dict(part)
    payload_json = _json_dumps_stable({"part": payload_part})
    stack_key = hashlib.sha1(payload_json.encode("utf-8")).hexdigest()
    item_id = str(payload_part.get("item_id") or payload_part.get("id") or payload_part.get("name") or payload_part.get("type") or "part").strip() or "part"
    name = str(payload_part.get("name") or item_id)
    return stack_key, item_id, name, payload_json


def _upsert_inventory_stack(
    conn: sqlite3.Connection,
    *,
    location_id: str,
    stack_type: str,
    stack_key: str,
    item_id: str,
    name: str,
    quantity_delta: float,
    mass_delta_kg: float,
    volume_delta_m3: float,
    payload_json: str,
) -> None:
    row = conn.execute(
        """
        SELECT quantity,mass_kg,volume_m3
        FROM location_inventory_stacks
        WHERE location_id=? AND stack_type=? AND stack_key=?
        """,
        (location_id, stack_type, stack_key),
    ).fetchone()

    now = game_now_s()
    if not row:
        qty = max(0.0, float(quantity_delta or 0.0))
        mass = max(0.0, float(mass_delta_kg or 0.0))
        vol = max(0.0, float(volume_delta_m3 or 0.0))
        if qty <= 0.0 and mass <= 0.0 and vol <= 0.0:
            return
        conn.execute(
            """
            INSERT INTO location_inventory_stacks (
              location_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (location_id, stack_type, stack_key, item_id, name, qty, mass, vol, payload_json, now),
        )
        return

    qty = max(0.0, float(row["quantity"] or 0.0) + float(quantity_delta or 0.0))
    mass = max(0.0, float(row["mass_kg"] or 0.0) + float(mass_delta_kg or 0.0))
    vol = max(0.0, float(row["volume_m3"] or 0.0) + float(volume_delta_m3 or 0.0))

    if qty <= 1e-9 and mass <= 1e-9 and vol <= 1e-9:
        conn.execute(
            "DELETE FROM location_inventory_stacks WHERE location_id=? AND stack_type=? AND stack_key=?",
            (location_id, stack_type, stack_key),
        )
        return

    conn.execute(
        """
        UPDATE location_inventory_stacks
        SET item_id=?, name=?, quantity=?, mass_kg=?, volume_m3=?, payload_json=?, updated_at=?
        WHERE location_id=? AND stack_type=? AND stack_key=?
        """,
        (item_id, name, qty, mass, vol, payload_json, now, location_id, stack_type, stack_key),
    )


def add_resource_to_location_inventory(conn: sqlite3.Connection, location_id: str, resource_id: str, mass_kg: float) -> None:
    rid = str(resource_id or "").strip()
    amount_kg = max(0.0, float(mass_kg or 0.0))
    if not rid or amount_kg <= 0.0:
        return

    resources = load_resource_catalog()
    resource = resources.get(rid) or {}
    name = str(resource.get("name") or rid)
    density = max(0.0, float(resource.get("mass_per_m3_kg") or 0.0))
    volume = (amount_kg / density) if density > 0.0 else 0.0
    payload_json = _json_dumps_stable({"resource_id": rid})

    _upsert_inventory_stack(
        conn,
        location_id=location_id,
        stack_type="resource",
        stack_key=rid,
        item_id=rid,
        name=name,
        quantity_delta=amount_kg,
        mass_delta_kg=amount_kg,
        volume_delta_m3=volume,
        payload_json=payload_json,
    )


def add_part_to_location_inventory(conn: sqlite3.Connection, location_id: str, part: Dict[str, Any], count: float = 1.0) -> None:
    if not isinstance(part, dict):
        return
    qty = max(0.0, float(count or 0.0))
    if qty <= 0.0:
        return

    stack_key, item_id, name, payload_json = _part_stack_identity(part)
    mass_per_part = max(0.0, float(part.get("mass_kg") or 0.0))

    _upsert_inventory_stack(
        conn,
        location_id=location_id,
        stack_type="part",
        stack_key=stack_key,
        item_id=item_id,
        name=name,
        quantity_delta=qty,
        mass_delta_kg=mass_per_part * qty,
        volume_delta_m3=0.0,
        payload_json=payload_json,
    )


def get_location_inventory_payload(conn: sqlite3.Connection, location_id: str) -> Dict[str, Any]:
    rows = conn.execute(
        """
        SELECT location_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
        FROM location_inventory_stacks
        WHERE location_id=?
        ORDER BY stack_type, item_id, stack_key
        """,
        (location_id,),
    ).fetchall()

    resources: List[Dict[str, Any]] = []
    parts: List[Dict[str, Any]] = []
    for r in rows:
        stack_type = str(r["stack_type"] or "")
        base = {
            "stack_key": str(r["stack_key"]),
            "item_id": str(r["item_id"]),
            "name": str(r["name"]),
            "quantity": float(r["quantity"] or 0.0),
            "mass_kg": float(r["mass_kg"] or 0.0),
            "volume_m3": float(r["volume_m3"] or 0.0),
            "updated_at": float(r["updated_at"] or 0.0),
        }
        payload = json.loads(r["payload_json"] or "{}")
        if stack_type == "resource":
            base["resource_id"] = str(payload.get("resource_id") or base["item_id"])
            resources.append(base)
            continue
        if stack_type == "part":
            base["part"] = payload.get("part") if isinstance(payload, dict) else None
            parts.append(base)

    return {
        "location_id": location_id,
        "resources": resources,
        "parts": parts,
    }


def consume_parts_from_location_inventory(
    conn: sqlite3.Connection,
    location_id: str,
    requested_item_ids: List[str],
) -> List[Dict[str, Any]]:
    requested = [str(x).strip() for x in (requested_item_ids or []) if str(x).strip()]
    if not requested:
        return []

    available_rows = conn.execute(
        """
        SELECT location_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
        FROM location_inventory_stacks
        WHERE location_id=? AND stack_type='part'
        ORDER BY item_id, updated_at, stack_key
        """,
        (location_id,),
    ).fetchall()

    by_item: Dict[str, List[sqlite3.Row]] = {}
    for row in available_rows:
        item_id = str(row["item_id"] or "")
        by_item.setdefault(item_id, []).append(row)

    needed: Dict[str, int] = {}
    for item_id in requested:
        needed[item_id] = needed.get(item_id, 0) + 1

    for item_id, req_count in needed.items():
        available_count = int(sum(max(0.0, float(r["quantity"] or 0.0)) for r in by_item.get(item_id, [])))
        if available_count < req_count:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient inventory at {location_id}: need {req_count}x {item_id}, have {available_count}",
            )

    consumed_parts: List[Dict[str, Any]] = []
    for item_id in requested:
        rows = by_item.get(item_id, [])
        chosen = None
        for row in rows:
            if float(row["quantity"] or 0.0) >= 1.0:
                chosen = row
                break
        if chosen is None:
            raise HTTPException(status_code=400, detail=f"Inventory race while consuming {item_id}")

        payload = json.loads(chosen["payload_json"] or "{}")
        part = payload.get("part") if isinstance(payload, dict) else None
        if not isinstance(part, dict):
            part = {"item_id": item_id}
        consumed_parts.append(part)

        qty_before = max(0.0, float(chosen["quantity"] or 0.0))
        mass_before = max(0.0, float(chosen["mass_kg"] or 0.0))
        mass_per = mass_before / qty_before if qty_before > 0 else max(0.0, float(part.get("mass_kg") or 0.0))

        _upsert_inventory_stack(
            conn,
            location_id=location_id,
            stack_type="part",
            stack_key=str(chosen["stack_key"]),
            item_id=str(chosen["item_id"]),
            name=str(chosen["name"]),
            quantity_delta=-1.0,
            mass_delta_kg=-mass_per,
            volume_delta_m3=0.0,
            payload_json=str(chosen["payload_json"] or "{}"),
        )

        updated_row = conn.execute(
            """
            SELECT location_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
            FROM location_inventory_stacks
            WHERE location_id=? AND stack_type='part' AND stack_key=?
            """,
            (location_id, str(chosen["stack_key"])),
        ).fetchone()

        if updated_row is None:
            by_item[item_id] = [r for r in rows if str(r["stack_key"]) != str(chosen["stack_key"])]
        else:
            next_rows = []
            for row in rows:
                if str(row["stack_key"]) == str(chosen["stack_key"]):
                    next_rows.append(updated_row)
                else:
                    next_rows.append(row)
            by_item[item_id] = next_rows

    return normalize_parts(consumed_parts)


# ── Ship state load / persist ────────────────────────────────────────────

def get_location_row(conn: sqlite3.Connection, location_id: str) -> sqlite3.Row:
    loc_id = str(location_id or "").strip()
    if not loc_id:
        raise HTTPException(status_code=400, detail="location_id is required")
    row = conn.execute(
        "SELECT id,name,is_group FROM locations WHERE id=?",
        (loc_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Location not found")
    if int(row["is_group"]):
        raise HTTPException(status_code=400, detail="location_id must be a non-group location")
    return row


def load_ship_inventory_state(conn: sqlite3.Connection, ship_id: str) -> Dict[str, Any]:
    sid = str(ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    row = conn.execute(
        """
        SELECT id,name,location_id,arrives_at,parts_json,fuel_kg
        FROM ships
        WHERE id=?
        """,
        (sid,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ship not found")

    parts = normalize_parts(json.loads(row["parts_json"] or "[]"))
    fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))
    parts, hardened_changed = _harden_ship_parts(parts, fuel_kg)
    if hardened_changed:
        conn.execute(
            "UPDATE ships SET parts_json=? WHERE id=?",
            (json.dumps(parts), sid),
        )
    containers = compute_ship_inventory_containers(parts, fuel_kg)
    resources = compute_ship_inventory_resources(sid, containers)
    capacity_summary = compute_ship_capacity_summary(containers)
    location_id = str(row["location_id"] or "").strip()
    is_docked = bool(location_id) and row["arrives_at"] is None
    return {
        "row": row,
        "parts": parts,
        "fuel_kg": fuel_kg,
        "containers": containers,
        "resources": resources,
        "capacity_summary": capacity_summary,
        "location_id": location_id,
        "is_docked": is_docked,
    }


def apply_ship_container_fill(
    part: Dict[str, Any],
    *,
    resource_id: str,
    cargo_mass_kg: float,
    used_m3: float,
    density_kg_m3: float,
) -> Dict[str, Any]:
    next_part = dict(part or {})
    rid = str(resource_id or "").strip()
    mass = max(0.0, float(cargo_mass_kg or 0.0))
    used = max(0.0, float(used_m3 or 0.0))
    density = max(0.0, float(density_kg_m3 or 0.0))

    if rid:
        next_part["resource_id"] = rid
    if density > 0.0:
        next_part["mass_per_m3_kg"] = density

    for key in ("cargo_used_m3", "used_m3", "fill_m3", "stored_m3", "current_m3"):
        next_part[key] = used
    for key in ("cargo_mass_kg", "contents_mass_kg", "stored_mass_kg", "current_mass_kg", "water_kg", "fuel_kg"):
        next_part[key] = mass

    return next_part


def persist_ship_inventory_state(
    conn: sqlite3.Connection,
    *,
    ship_id: str,
    parts: List[Dict[str, Any]],
    fuel_kg: float,
) -> None:
    stats = derive_ship_stats_from_parts(parts, current_fuel_kg=max(0.0, float(fuel_kg or 0.0)))
    conn.execute(
        """
        UPDATE ships
        SET parts_json=?, fuel_kg=?, fuel_capacity_kg=?, dry_mass_kg=?, isp_s=?
        WHERE id=?
        """,
        (
            json.dumps(parts),
            stats["fuel_kg"],
            stats["fuel_capacity_kg"],
            stats["dry_mass_kg"],
            stats["isp_s"],
            ship_id,
        ),
    )


def resource_stack_row(conn: sqlite3.Connection, location_id: str, stack_key: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT location_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
        FROM location_inventory_stacks
        WHERE location_id=? AND stack_type='resource' AND stack_key=?
        """,
        (location_id, stack_key),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Resource stack not found")
    return row


def consume_location_resource_mass(conn: sqlite3.Connection, row: sqlite3.Row, mass_kg: float) -> float:
    available_mass = max(0.0, float(row["mass_kg"] or 0.0))
    amount = max(0.0, min(available_mass, float(mass_kg or 0.0)))
    if amount <= 0.0:
        return 0.0

    available_vol = max(0.0, float(row["volume_m3"] or 0.0))
    volume_delta = -(available_vol * (amount / available_mass)) if available_mass > 1e-9 else 0.0

    _upsert_inventory_stack(
        conn,
        location_id=str(row["location_id"]),
        stack_type="resource",
        stack_key=str(row["stack_key"]),
        item_id=str(row["item_id"]),
        name=str(row["name"]),
        quantity_delta=-amount,
        mass_delta_kg=-amount,
        volume_delta_m3=volume_delta,
        payload_json=str(row["payload_json"] or "{}"),
    )
    return amount


def part_stack_row(conn: sqlite3.Connection, location_id: str, stack_key: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT location_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
        FROM location_inventory_stacks
        WHERE location_id=? AND stack_type='part' AND stack_key=?
        """,
        (location_id, stack_key),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Part stack not found")
    return row


def consume_location_part_unit(conn: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
    qty_before = max(0.0, float(row["quantity"] or 0.0))
    if qty_before < 1.0:
        raise HTTPException(status_code=400, detail="Part stack is empty")

    mass_before = max(0.0, float(row["mass_kg"] or 0.0))
    volume_before = max(0.0, float(row["volume_m3"] or 0.0))
    unit_mass = (mass_before / qty_before) if qty_before > 1e-9 else 0.0
    unit_volume = (volume_before / qty_before) if qty_before > 1e-9 else 0.0

    payload = json.loads(row["payload_json"] or "{}")
    part = payload.get("part") if isinstance(payload, dict) else None
    if not isinstance(part, dict):
        part = {
            "item_id": str(row["item_id"] or "part"),
            "name": str(row["name"] or row["item_id"] or "Part"),
            "mass_kg": unit_mass,
        }
    normalized = normalize_parts([part])
    if normalized:
        part = normalized[0]

    _upsert_inventory_stack(
        conn,
        location_id=str(row["location_id"]),
        stack_type="part",
        stack_key=str(row["stack_key"]),
        item_id=str(row["item_id"]),
        name=str(row["name"]),
        quantity_delta=-1.0,
        mass_delta_kg=-unit_mass,
        volume_delta_m3=-unit_volume,
        payload_json=str(row["payload_json"] or "{}"),
    )

    return dict(part)


# ── Inventory display item builders ──────────────────────────────────────

def inventory_items_for_ship(ship_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = list(ship_state.get("resources") or [])
    rows.sort(key=lambda r: (str(r.get("phase") or ""), str(r.get("label") or r.get("item_id") or "")))
    return rows


def inventory_container_groups_for_ship(ship_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    ship_row = ship_state.get("row")
    if isinstance(ship_row, sqlite3.Row):
        ship_id = str(ship_row["id"] or "")
    elif isinstance(ship_row, dict):
        ship_id = str(ship_row.get("id") or "")
    else:
        ship_id = ""

    groups: List[Dict[str, Any]] = []
    for container in ship_state.get("containers") or []:
        idx = int(container.get("container_index") or -1)
        if idx < 0:
            continue

        name = str(container.get("name") or f"Container {idx + 1}")
        phase = str(container.get("tank_phase") or container.get("phase") or "solid").strip().lower()
        if phase not in {"solid", "liquid", "gas"}:
            phase = "solid"

        capacity_m3 = max(0.0, float(container.get("capacity_m3") or 0.0))
        used_m3 = max(0.0, float(container.get("used_m3") or 0.0))
        cargo_mass_kg = max(0.0, float(container.get("cargo_mass_kg") or 0.0))
        resource_id = str(container.get("resource_id") or "").strip()
        resource_name = str(container.get("resource_name") or resource_id or "Cargo")

        items: List[Dict[str, Any]] = []
        if resource_id and cargo_mass_kg > 1e-9:
            items.append(
                {
                    "item_uid": f"ship:{ship_id}:container:{idx}:resource:{resource_id}",
                    "item_kind": "resource",
                    "item_id": resource_id,
                    "label": resource_name,
                    "subtitle": f"{phase.title()} cargo · {used_m3:.2f} m³",
                    "category": "resource",
                    "resource_id": resource_id,
                    "phase": phase,
                    "mass_kg": cargo_mass_kg,
                    "volume_m3": used_m3,
                    "quantity": cargo_mass_kg,
                    "capacity_m3": capacity_m3,
                    "icon_seed": f"ship_container::{ship_id}::{idx}::{resource_id}",
                    "transfer": {
                        "source_kind": "ship_container",
                        "source_id": ship_id,
                        "source_key": str(idx),
                        "amount": cargo_mass_kg,
                    },
                }
            )

        groups.append(
            {
                "group_id": f"ship:{ship_id}:container:{idx}",
                "group_kind": "container",
                "container_index": idx,
                "name": name,
                "phase": phase,
                "capacity_m3": capacity_m3,
                "used_m3": used_m3,
                "free_m3": max(0.0, capacity_m3 - used_m3),
                "resource_id": resource_id,
                "resource_name": resource_name if resource_id else "",
                "item_count": len(items),
                "items": items,
            }
        )

    groups.sort(key=lambda g: int(g.get("container_index") or 0))
    return groups


def stack_items_for_ship(ship_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    ship_row = ship_state.get("row")
    if isinstance(ship_row, sqlite3.Row):
        ship_id = str(ship_row["id"] or "")
    elif isinstance(ship_row, dict):
        ship_id = str(ship_row.get("id") or "")
    else:
        ship_id = ""
    can_transfer = bool(ship_state.get("is_docked"))
    containers_by_index: Dict[int, Dict[str, Any]] = {}
    for container in ship_state.get("containers") or []:
        try:
            idx = int(container.get("container_index") or -1)
        except Exception:
            idx = -1
        if idx >= 0:
            containers_by_index[idx] = container

    rows: List[Dict[str, Any]] = []

    for idx, part in enumerate(ship_state.get("parts") or []):
        part_payload = part if isinstance(part, dict) else {}
        item_id = str(part_payload.get("item_id") or part_payload.get("id") or part_payload.get("type") or f"part_{idx}")
        label = str(part_payload.get("name") or item_id or f"Part {idx + 1}")
        ptype = str(part_payload.get("type") or part_payload.get("category_id") or "module")
        mass_kg = max(0.0, float(part_payload.get("mass_kg") or 0.0))
        volume_m3 = 0.0
        subtitle = ptype

        container = containers_by_index.get(idx)
        if isinstance(container, dict):
            phase = str(container.get("tank_phase") or container.get("phase") or "solid").strip().lower()
            if phase not in {"solid", "liquid", "gas"}:
                phase = "solid"
            cap_m3 = max(0.0, float(container.get("capacity_m3") or 0.0))
            used_m3 = max(0.0, float(container.get("used_m3") or 0.0))
            cargo_mass_kg = max(0.0, float(container.get("cargo_mass_kg") or 0.0))
            total_mass_kg = max(0.0, float(container.get("total_mass_kg") or (mass_kg + cargo_mass_kg)))
            resource_label = str(container.get("resource_name") or container.get("resource_id") or "").strip()

            mass_kg = total_mass_kg
            volume_m3 = used_m3
            if resource_label and cargo_mass_kg > 1e-9:
                subtitle = f"{phase.title()} tank · {resource_label} {cargo_mass_kg:.0f} kg · {used_m3:.2f}/{cap_m3:.2f} m³"
            else:
                subtitle = f"{phase.title()} tank · Empty · {used_m3:.2f}/{cap_m3:.2f} m³"

        transfer = None
        if can_transfer and ship_id:
            transfer = {
                "source_kind": "ship_part",
                "source_id": ship_id,
                "source_key": str(idx),
                "amount": 1.0,
            }

        part_category = str(part_payload.get("type") or part_payload.get("category_id") or "module").strip().lower()
        thrust_kn = float(part_payload.get("thrust_kn") or 0)
        isp_s = float(part_payload.get("isp_s") or 0)
        power_mw = float(part_payload.get("thermal_mw") or part_payload.get("power_mw") or 0)
        cap_m3_val = float(part_payload.get("capacity_m3") or 0)

        rows.append(
            {
                "item_uid": f"ship:{ship_id}:part:{idx}",
                "item_kind": "part",
                "part_index": idx,
                "item_id": item_id,
                "label": label,
                "subtitle": subtitle,
                "category": part_category,
                "resource_id": "",
                "mass_kg": mass_kg,
                "volume_m3": volume_m3,
                "quantity": 1.0,
                "thrust_kn": thrust_kn if thrust_kn > 0 else None,
                "isp_s": isp_s if isp_s > 0 else None,
                "power_mw": power_mw if power_mw > 0 else None,
                "capacity_m3": cap_m3_val if cap_m3_val > 0 else None,
                "icon_seed": f"ship_part::{item_id}::{idx}",
                "transfer": transfer,
            }
        )

    return rows


def stack_items_for_location(location_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    location_id = str(location_payload.get("location_id") or "")
    rows: List[Dict[str, Any]] = []
    resources = load_resource_catalog()
    for part in location_payload.get("parts") or []:
        stack_key = str(part.get("stack_key") or "")
        qty = max(0.0, float(part.get("quantity") or 0.0))
        if qty <= 1e-9:
            continue

        subtitle = f"Count: {int(round(qty))}"
        part_payload = part.get("part") if isinstance(part.get("part"), dict) else None
        if isinstance(part_payload, dict):
            capacity_m3 = max(0.0, float(part_payload.get("capacity_m3") or 0.0))
            resource_id = str(part_payload.get("resource_id") or "").strip()
            if capacity_m3 > 0.0:
                density = max(
                    0.0,
                    float(
                        part_payload.get("mass_per_m3_kg")
                        or (resources.get(resource_id) or {}).get("mass_per_m3_kg")
                        or 0.0
                    ),
                )
                used_m3 = 0.0
                for key in ("cargo_used_m3", "used_m3", "fill_m3", "stored_m3", "current_m3"):
                    if key in part_payload:
                        used_m3 = max(0.0, float(part_payload.get(key) or 0.0))
                        break

                cargo_mass_kg = 0.0
                for key in ("cargo_mass_kg", "contents_mass_kg", "stored_mass_kg", "current_mass_kg", "water_kg", "fuel_kg"):
                    if key in part_payload:
                        cargo_mass_kg = max(0.0, float(part_payload.get(key) or 0.0))
                        break
                if cargo_mass_kg <= 1e-9 and used_m3 > 1e-9 and density > 0.0:
                    cargo_mass_kg = used_m3 * density
                elif used_m3 <= 1e-9 and cargo_mass_kg > 1e-9 and density > 0.0:
                    used_m3 = cargo_mass_kg / density

                phase = str(part_payload.get("tank_phase") or "").strip().lower()
                if phase not in {"solid", "liquid", "gas"}:
                    phase = classify_resource_phase(resource_id, resource_id, density)

                if resource_id and cargo_mass_kg > 1e-9:
                    subtitle = f"Count: {int(round(qty))} · {phase.title()} · {resource_id} {cargo_mass_kg:.0f} kg · {used_m3:.2f}/{capacity_m3:.2f} m³"
                else:
                    subtitle = f"Count: {int(round(qty))} · {phase.title()} · Empty · {used_m3:.2f}/{capacity_m3:.2f} m³"

        part_payload_loc = part.get("part") if isinstance(part.get("part"), dict) else {}
        loc_part_category = str(part_payload_loc.get("type") or part_payload_loc.get("category_id") or "module").strip().lower()
        loc_thrust = float(part_payload_loc.get("thrust_kn") or 0)
        loc_isp = float(part_payload_loc.get("isp_s") or 0)
        loc_power = float(part_payload_loc.get("thermal_mw") or part_payload_loc.get("power_mw") or 0)
        loc_cap = float(part_payload_loc.get("capacity_m3") or 0)

        rows.append(
            {
                "item_uid": f"location:{location_id}:part:{stack_key}",
                "item_kind": "part",
                "item_id": str(part.get("item_id") or "part"),
                "label": str(part.get("name") or part.get("item_id") or "Part"),
                "subtitle": subtitle,
                "category": loc_part_category,
                "resource_id": "",
                "mass_kg": max(0.0, float(part.get("mass_kg") or 0.0)),
                "volume_m3": max(0.0, float(part.get("volume_m3") or 0.0)),
                "quantity": qty,
                "thrust_kn": loc_thrust if loc_thrust > 0 else None,
                "isp_s": loc_isp if loc_isp > 0 else None,
                "power_mw": loc_power if loc_power > 0 else None,
                "capacity_m3": loc_cap if loc_cap > 0 else None,
                "icon_seed": f"stack_part::{part.get('item_id') or stack_key}",
                "transfer": {
                    "source_kind": "location_part",
                    "source_id": location_id,
                    "source_key": stack_key,
                    "amount": 1.0,
                },
            }
        )
    return rows


def inventory_items_for_location(location_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    location_id = str(location_payload.get("location_id") or "")
    rows: List[Dict[str, Any]] = []
    for resource in location_payload.get("resources") or []:
        stack_key = str(resource.get("stack_key") or "")
        mass_kg = max(0.0, float(resource.get("mass_kg") or 0.0))
        rows.append(
            {
                "item_uid": f"location:{location_id}:resource:{stack_key}",
                "item_kind": "resource",
                "item_id": str(resource.get("resource_id") or resource.get("item_id") or "resource"),
                "label": str(resource.get("name") or resource.get("item_id") or "Resource"),
                "subtitle": "Location Resource",
                "category": "resource",
                "resource_id": str(resource.get("resource_id") or resource.get("item_id") or ""),
                "mass_kg": mass_kg,
                "volume_m3": max(0.0, float(resource.get("volume_m3") or 0.0)),
                "quantity": mass_kg,
                "icon_seed": f"resource::{resource.get('resource_id') or resource.get('item_id') or stack_key}",
                "transfer": {
                    "source_kind": "location_resource",
                    "source_id": location_id,
                    "source_key": stack_key,
                    "amount": mass_kg,
                },
            }
        )

    for part in location_payload.get("parts") or []:
        stack_key = str(part.get("stack_key") or "")
        qty = max(0.0, float(part.get("quantity") or 0.0))
        rows.append(
            {
                "item_uid": f"location:{location_id}:part:{stack_key}",
                "item_kind": "part",
                "item_id": str(part.get("item_id") or "part"),
                "label": str(part.get("name") or part.get("item_id") or "Part"),
                "subtitle": f"Count: {int(round(qty))}",
                "category": str((part.get("part") or {}).get("type") or (part.get("part") or {}).get("category_id") or "module").strip().lower() if isinstance(part.get("part"), dict) else "module",
                "resource_id": "",
                "mass_kg": max(0.0, float(part.get("mass_kg") or 0.0)),
                "volume_m3": max(0.0, float(part.get("volume_m3") or 0.0)),
                "quantity": qty,
                "icon_seed": f"part::{part.get('item_id') or stack_key}",
                "transfer": None,
            }
        )
    return rows


# ── Location / map seed & expansion ──────────────────────────────────────

def seed_locations_and_edges_if_empty(conn: sqlite3.Connection) -> None:
    cnt = conn.execute("SELECT COUNT(*) AS c FROM locations").fetchone()["c"]
    if cnt and int(cnt) > 0:
        return

    groups = [
        ("grp_earth", "Earth", None, 1, 10, 0, 0),
        ("grp_earth_orbits", "Orbits", "grp_earth", 1, 10, 0, 0),
        ("grp_em_lpoints", "Earth\u2013Luna Lagrange", "grp_earth", 1, 20, 0, 0),
        ("grp_moon", "Luna", None, 1, 20, 384400, 0),
        ("grp_moon_orbits", "Orbits", "grp_moon", 1, 10, 384400, 0),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO locations (id,name,parent_id,is_group,sort_order,x,y) VALUES (?,?,?,?,?,?,?)",
        groups,
    )

    leaves = [
        ("LEO", "Low Earth Orbit", "grp_earth_orbits", 0, 10, 9000, 0),
        ("HEO", "High Earth Orbit", "grp_earth_orbits", 0, 20, 20000, 0),
        ("GEO", "Earth Geostationary", "grp_earth_orbits", 0, 30, 42164, 0),
        ("L1", "L1", "grp_em_lpoints", 0, 10, 326000, 0),
        ("L2", "L2", "grp_em_lpoints", 0, 20, 450000, 0),
        ("L3", "L3", "grp_em_lpoints", 0, 30, -384400, 0),
        ("L4", "L4", "grp_em_lpoints", 0, 40, 192200, 332900),
        ("L5", "L5", "grp_em_lpoints", 0, 50, 192200, -332900),
        ("LLO", "Low Luna Orbit", "grp_moon_orbits", 0, 10, 389500, 0),
        ("HLO", "High Luna Orbit", "grp_moon_orbits", 0, 20, 396000, 0),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO locations (id,name,parent_id,is_group,sort_order,x,y) VALUES (?,?,?,?,?,?,?)",
        leaves,
    )

    edges = [
        ("LEO", "HEO", 900, 7200),
        ("HEO", "LEO", 900, 7200),
        ("HEO", "GEO", 1200, 14400),
        ("GEO", "HEO", 700, 14400),
        ("LEO", "GEO", 1800, 21600),
        ("GEO", "LEO", 900, 21600),
        ("GEO", "L1", 1200, 43200),
        ("L1", "GEO", 500, 43200),
        ("L1", "L2", 150, 21600),
        ("L2", "L1", 150, 21600),
        ("L1", "L4", 250, 86400),
        ("L4", "L1", 250, 86400),
        ("L1", "L5", 250, 86400),
        ("L5", "L1", 250, 86400),
        ("L1", "L3", 450, 129600),
        ("L3", "L1", 450, 129600),
        ("L1", "LLO", 900, 21600),
        ("LLO", "L1", 900, 21600),
        ("LLO", "HLO", 450, 7200),
        ("HLO", "LLO", 450, 7200),
        ("L2", "HLO", 900, 21600),
        ("HLO", "L2", 900, 21600),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO transfer_edges (from_id,to_id,dv_m_s,tof_s) VALUES (?,?,?,?)",
        edges,
    )


def upsert_locations(conn: sqlite3.Connection, rows: List[Tuple[str, str, Optional[str], int, int, float, float]]) -> None:
    for row in rows:
        conn.execute(
            """
            INSERT INTO locations (id,name,parent_id,is_group,sort_order,x,y)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              parent_id=excluded.parent_id,
              is_group=excluded.is_group,
              sort_order=excluded.sort_order,
              x=excluded.x,
              y=excluded.y
            """,
            row,
        )


def upsert_transfer_edges(conn: sqlite3.Connection, rows: List[Tuple[str, str, float, float]]) -> None:
    for row in rows:
        conn.execute(
            """
            INSERT INTO transfer_edges (from_id,to_id,dv_m_s,tof_s)
            VALUES (?,?,?,?)
            ON CONFLICT(from_id,to_id) DO UPDATE SET
              dv_m_s=excluded.dv_m_s,
              tof_s=excluded.tof_s
            """,
            row,
        )


def _hohmann_interplanetary_dv_tof(
    r1_km: float, r2_km: float, mu_sun_km3_s2: float,
    mu_origin_km3_s2: float, rp_origin_km: float,
    mu_dest_km3_s2: float, rp_dest_km: float,
) -> Tuple[float, float]:
    a_t = 0.5 * (r1_km + r2_km)
    v1 = math.sqrt(mu_sun_km3_s2 / r1_km)
    v2 = math.sqrt(mu_sun_km3_s2 / r2_km)
    vt1 = math.sqrt(mu_sun_km3_s2 * ((2.0 / r1_km) - (1.0 / a_t)))
    vt2 = math.sqrt(mu_sun_km3_s2 * ((2.0 / r2_km) - (1.0 / a_t)))
    v_inf_depart = abs(vt1 - v1)
    v_inf_arrive = abs(v2 - vt2)
    dv_depart = math.sqrt((v_inf_depart ** 2) + (2.0 * mu_origin_km3_s2 / rp_origin_km)) - math.sqrt(mu_origin_km3_s2 / rp_origin_km)
    dv_arrive = math.sqrt((v_inf_arrive ** 2) + (2.0 * mu_dest_km3_s2 / rp_dest_km)) - math.sqrt(mu_dest_km3_s2 / rp_dest_km)
    tof_s = math.pi * math.sqrt((a_t ** 3) / mu_sun_km3_s2)
    return (dv_depart + dv_arrive) * 1000.0, tof_s


def _hohmann_orbit_change_dv_tof(mu_km3_s2: float, r1_km: float, r2_km: float) -> Tuple[float, float]:
    if r1_km <= 0.0 or r2_km <= 0.0:
        return 0.0, 0.0
    a_t = 0.5 * (r1_km + r2_km)
    dv1 = math.sqrt(mu_km3_s2 / r1_km) * (math.sqrt((2.0 * r2_km) / (r1_km + r2_km)) - 1.0)
    dv2 = math.sqrt(mu_km3_s2 / r2_km) * (1.0 - math.sqrt((2.0 * r1_km) / (r1_km + r2_km)))
    tof_s = math.pi * math.sqrt((a_t ** 3) / mu_km3_s2)
    return (abs(dv1) + abs(dv2)) * 1000.0, tof_s


def ensure_solar_system_expansion(conn: sqlite3.Connection) -> None:
    try:
        location_rows, edge_rows = celestial_config.load_locations_and_edges()
        upsert_locations(conn, location_rows)
        upsert_transfer_edges(conn, edge_rows)
        return
    except celestial_config.CelestialConfigError as exc:
        print(f"[celestial-config] {exc} -- falling back to built-in expansion")

    sun_x, sun_y = 0.0, 0.0

    def polar_xy(radius_km: float, angle_deg: float) -> Tuple[float, float]:
        a = math.radians(angle_deg)
        return radius_km * math.cos(a), radius_km * math.sin(a)

    mercury_x, mercury_y = polar_xy(57_909_227.0, -16.0)
    venus_x, venus_y = polar_xy(108_209_475.0, 11.0)
    earth_x, earth_y = polar_xy(149_597_870.7, 0.0)
    mars_x, mars_y = polar_xy(227_943_824.0, -7.0)

    moon_offset_x, moon_offset_y = polar_xy(384_400.0, 10.0)
    moon_x = earth_x + moon_offset_x
    moon_y = earth_y + moon_offset_y

    em_dx = moon_x - earth_x
    em_dy = moon_y - earth_y
    em_r = max(1e-9, math.hypot(em_dx, em_dy))
    em_ux = em_dx / em_r
    em_uy = em_dy / em_r
    em_tx = -em_uy
    em_ty = em_ux

    l1_x = earth_x + em_ux * 326_400.0
    l1_y = earth_y + em_uy * 326_400.0
    l2_x = earth_x + em_ux * 448_900.0
    l2_y = earth_y + em_uy * 448_900.0
    l3_x = earth_x - em_ux * 381_700.0
    l3_y = earth_y - em_uy * 381_700.0
    l4_x = earth_x + (0.5 * em_ux + (math.sqrt(3.0) / 2.0) * em_tx) * em_r
    l4_y = earth_y + (0.5 * em_uy + (math.sqrt(3.0) / 2.0) * em_ty) * em_r
    l5_x = earth_x + (0.5 * em_ux - (math.sqrt(3.0) / 2.0) * em_tx) * em_r
    l5_y = earth_y + (0.5 * em_uy - (math.sqrt(3.0) / 2.0) * em_ty) * em_r

    phobos_offset_x, phobos_offset_y = polar_xy(9_376.0, 28.0)
    deimos_offset_x, deimos_offset_y = polar_xy(23_463.2, -12.0)

    groups = [
        ("grp_sun", "Sun", None, 1, 1, sun_x, sun_y),
        ("grp_mercury", "Mercury", "grp_sun", 1, 8, mercury_x, mercury_y),
        ("grp_venus", "Venus", "grp_sun", 1, 9, venus_x, venus_y),
        ("grp_earth", "Earth", "grp_sun", 1, 10, earth_x, earth_y),
        ("grp_earth_orbits", "Orbits", "grp_earth", 1, 10, earth_x, earth_y),
        ("grp_em_lpoints", "Earth\u2013Luna Lagrange", "grp_earth", 1, 20, earth_x, earth_y),
        ("grp_moon", "Luna", "grp_earth", 1, 20, moon_x, moon_y),
        ("grp_moon_orbits", "Orbits", "grp_moon", 1, 10, moon_x, moon_y),
        ("grp_mars", "Mars", "grp_sun", 1, 30, mars_x, mars_y),
        ("grp_mars_orbits", "Orbits", "grp_mars", 1, 10, mars_x, mars_y),
        ("grp_mars_moons", "Moons", "grp_mars", 1, 20, mars_x, mars_y),
    ]
    upsert_locations(conn, groups)

    leaves = [
        ("LEO", "Low Earth Orbit", "grp_earth_orbits", 0, 10, earth_x + 6_778.137, earth_y),
        ("HEO", "High Earth Orbit", "grp_earth_orbits", 0, 20, earth_x + 26_600.0, earth_y),
        ("GEO", "Earth Geostationary", "grp_earth_orbits", 0, 30, earth_x + 42_164.137, earth_y),
        ("L1", "L1", "grp_em_lpoints", 0, 10, l1_x, l1_y),
        ("L2", "L2", "grp_em_lpoints", 0, 20, l2_x, l2_y),
        ("L3", "L3", "grp_em_lpoints", 0, 30, l3_x, l3_y),
        ("L4", "L4", "grp_em_lpoints", 0, 40, l4_x, l4_y),
        ("L5", "L5", "grp_em_lpoints", 0, 50, l5_x, l5_y),
        ("LLO", "Low Luna Orbit", "grp_moon_orbits", 0, 10, moon_x + 1_837.4, moon_y),
        ("HLO", "High Luna Orbit", "grp_moon_orbits", 0, 20, moon_x + 4_400.0, moon_y),
        ("SUN", "Sun", "grp_sun", 0, 1, sun_x, sun_y),
        ("MERC_ORB", "Low Mercury Orbit", "grp_mercury", 0, 10, mercury_x + 2_639.7, mercury_y),
        ("VEN_ORB", "Low Venus Orbit", "grp_venus", 0, 10, venus_x + 6_301.8, venus_y),
        ("LMO", "Low Mars Orbit", "grp_mars_orbits", 0, 10, mars_x + 3_639.5, mars_y),
        ("PHOBOS", "Phobos", "grp_mars_moons", 0, 20, mars_x + phobos_offset_x, mars_y + phobos_offset_y),
        ("DEIMOS", "Deimos", "grp_mars_moons", 0, 30, mars_x + deimos_offset_x, mars_y + deimos_offset_y),
    ]
    upsert_locations(conn, leaves)

    mu_sun = 1.32712440018e11
    planetary = {
        "earth": {"a_km": 149597870.7, "mu": 398600.4418, "radius_km": 6378.137, "alt_km": 400.0},
        "mercury": {"a_km": 57909227.0, "mu": 22031.86855, "radius_km": 2439.7, "alt_km": 200.0},
        "venus": {"a_km": 108209475.0, "mu": 324858.592, "radius_km": 6051.8, "alt_km": 250.0},
        "mars": {"a_km": 227943824.0, "mu": 42828.375214, "radius_km": 3389.5, "alt_km": 250.0},
    }
    node_to_body = {"LEO": "earth", "MERC_ORB": "mercury", "VEN_ORB": "venus", "LMO": "mars"}

    computed_edges: List[Tuple[str, str, float, float]] = []
    nodes = list(node_to_body.keys())
    for from_id in nodes:
        for to_id in nodes:
            if from_id == to_id:
                continue
            from_body = planetary[node_to_body[from_id]]
            to_body = planetary[node_to_body[to_id]]
            dv_m_s, tof_s = _hohmann_interplanetary_dv_tof(
                from_body["a_km"], to_body["a_km"], mu_sun,
                from_body["mu"], from_body["radius_km"] + from_body["alt_km"],
                to_body["mu"], to_body["radius_km"] + to_body["alt_km"],
            )
            computed_edges.append((from_id, to_id, round(dv_m_s, 2), round(tof_s, 1)))

    mars_mu = planetary["mars"]["mu"]
    r_lmo = planetary["mars"]["radius_km"] + 250.0
    r_phobos = 9376.0
    r_deimos = 23463.2

    lmo_phobos_dv, lmo_phobos_tof = _hohmann_orbit_change_dv_tof(mars_mu, r_lmo, r_phobos)
    lmo_deimos_dv, lmo_deimos_tof = _hohmann_orbit_change_dv_tof(mars_mu, r_lmo, r_deimos)
    phobos_deimos_dv, phobos_deimos_tof = _hohmann_orbit_change_dv_tof(mars_mu, r_phobos, r_deimos)

    computed_edges.extend([
        ("LMO", "PHOBOS", round(lmo_phobos_dv, 2), round(lmo_phobos_tof, 1)),
        ("PHOBOS", "LMO", round(lmo_phobos_dv, 2), round(lmo_phobos_tof, 1)),
        ("LMO", "DEIMOS", round(lmo_deimos_dv, 2), round(lmo_deimos_tof, 1)),
        ("DEIMOS", "LMO", round(lmo_deimos_dv, 2), round(lmo_deimos_tof, 1)),
        ("PHOBOS", "DEIMOS", round(phobos_deimos_dv, 2), round(phobos_deimos_tof, 1)),
        ("DEIMOS", "PHOBOS", round(phobos_deimos_dv, 2), round(phobos_deimos_tof, 1)),
    ])

    computed_edges.extend([
        ("LEO", "SUN", 28000.0, 130.0 * 24.0 * 3600.0),
        ("SUN", "LEO", 28000.0, 130.0 * 24.0 * 3600.0),
        ("MERC_ORB", "SUN", 12000.0, 55.0 * 24.0 * 3600.0),
        ("SUN", "MERC_ORB", 12000.0, 55.0 * 24.0 * 3600.0),
        ("VEN_ORB", "SUN", 19000.0, 90.0 * 24.0 * 3600.0),
        ("SUN", "VEN_ORB", 19000.0, 90.0 * 24.0 * 3600.0),
        ("LMO", "SUN", 22000.0, 180.0 * 24.0 * 3600.0),
        ("SUN", "LMO", 22000.0, 180.0 * 24.0 * 3600.0),
    ])

    upsert_transfer_edges(conn, computed_edges)


# ── Transfer matrix (Dijkstra) ───────────────────────────────────────────

def hash_edges(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT from_id,to_id,dv_m_s,tof_s FROM transfer_edges ORDER BY from_id,to_id"
    ).fetchall()
    blob = json.dumps([dict(r) for r in rows], separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def dijkstra_all_pairs(conn: sqlite3.Connection) -> None:
    edges = conn.execute("SELECT from_id,to_id,dv_m_s,tof_s FROM transfer_edges").fetchall()
    locs = conn.execute("SELECT id,is_group FROM locations WHERE is_group=0").fetchall()
    node_ids = [r["id"] for r in locs]

    adj: Dict[str, List[Tuple[str, float, float]]] = {nid: [] for nid in node_ids}
    for e in edges:
        if e["from_id"] in adj and e["to_id"] in adj:
            adj[e["from_id"]].append((e["to_id"], float(e["dv_m_s"]), float(e["tof_s"])))

    matrix_rows = []
    for src in node_ids:
        dist: Dict[str, float] = {src: 0.0}
        tof: Dict[str, float] = {src: 0.0}
        prev: Dict[str, Optional[str]] = {src: None}

        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d != dist.get(u, float("inf")):
                continue
            for v, w_dv, w_tof in adj.get(u, []):
                nd = d + w_dv
                if nd < dist.get(v, float("inf")) - 1e-9:
                    dist[v] = nd
                    tof[v] = tof[u] + w_tof
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        for dst in node_ids:
            if dst == src:
                matrix_rows.append((src, dst, 0.0, 0.0, json.dumps([src])))
                continue
            if dst not in dist:
                continue
            path: List[str] = []
            cur: Optional[str] = dst
            while cur is not None:
                path.append(cur)
                cur = prev.get(cur)
            path.reverse()
            matrix_rows.append((src, dst, dist[dst], tof[dst], json.dumps(path)))

    conn.execute("DELETE FROM transfer_matrix")
    conn.executemany(
        "INSERT OR REPLACE INTO transfer_matrix (from_id,to_id,dv_m_s,tof_s,path_json) VALUES (?,?,?,?,?)",
        matrix_rows,
    )


def regenerate_matrix_if_needed(conn: sqlite3.Connection) -> None:
    current_hash = hash_edges(conn)
    stored = conn.execute("SELECT value FROM transfer_meta WHERE key='edges_hash'").fetchone()
    matrix_cnt = conn.execute("SELECT COUNT(*) AS c FROM transfer_matrix").fetchone()["c"]
    if (not stored) or stored["value"] != current_hash or int(matrix_cnt) == 0:
        dijkstra_all_pairs(conn)
        conn.execute(
            "INSERT OR REPLACE INTO transfer_meta (key,value) VALUES ('edges_hash',?)",
            (current_hash,),
        )


def settle_arrivals(conn: sqlite3.Connection, now_s: float) -> None:
    conn.execute(
        """
        UPDATE ships
        SET
          location_id = to_location_id,
          from_location_id = NULL,
          to_location_id = NULL,
          departed_at = NULL,
          arrives_at = NULL,
          transfer_path_json = '[]'
        WHERE arrives_at IS NOT NULL AND arrives_at <= ?
        """,
        (now_s,),
    )


# ── Simulation clock persistence ─────────────────────────────────────────

SIM_CLOCK_META_REAL_ANCHOR = "sim_real_time_anchor_s"
SIM_CLOCK_META_GAME_ANCHOR = "sim_game_time_anchor_s"
SIM_CLOCK_META_PAUSED = "sim_paused"


def persist_simulation_clock_state(conn: sqlite3.Connection) -> None:
    state = export_simulation_state()
    kv_rows = [
        (SIM_CLOCK_META_REAL_ANCHOR, str(float(state["real_time_anchor_s"]))),
        (SIM_CLOCK_META_GAME_ANCHOR, str(float(state["game_time_anchor_s"]))),
        (SIM_CLOCK_META_PAUSED, "1" if bool(state["paused"]) else "0"),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO transfer_meta (key,value) VALUES (?,?)",
        kv_rows,
    )


def load_simulation_clock_state(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT key,value FROM transfer_meta WHERE key IN (?,?,?)",
        (SIM_CLOCK_META_REAL_ANCHOR, SIM_CLOCK_META_GAME_ANCHOR, SIM_CLOCK_META_PAUSED),
    ).fetchall()
    by_key = {str(r["key"]): str(r["value"]) for r in rows}

    real_raw = by_key.get(SIM_CLOCK_META_REAL_ANCHOR)
    game_raw = by_key.get(SIM_CLOCK_META_GAME_ANCHOR)
    paused_raw = by_key.get(SIM_CLOCK_META_PAUSED)

    if real_raw is None or game_raw is None or paused_raw is None:
        persist_simulation_clock_state(conn)
        return

    try:
        real_anchor_s = float(real_raw)
        game_anchor_s = float(game_raw)
        paused = str(paused_raw).strip().lower() in {"1", "true", "yes", "on"}
    except (TypeError, ValueError):
        persist_simulation_clock_state(conn)
        return

    import_simulation_state(real_anchor_s, game_anchor_s, paused)


# ── Misc startup helpers ─────────────────────────────────────────────────

def purge_test_ships(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM ships
        WHERE id LIKE 'test_%' OR lower(name) LIKE 'test[%'
        """
    )


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def ensure_inventory_baseline_ship(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM ships WHERE id='artemis_iii'")

    starter_id = "shipyard_starter"
    starter_parts = [
        {"item_id": "ntr_m2_dumbo_folded_flow"},
        {"name": "Radiator", "type": "radiator", "mass_kg": 2000.0},
        {"item_id": "water_tank_10_m3"},
    ]
    starter_stats = derive_ship_stats_from_parts(starter_parts, current_fuel_kg=10000.0)
    row = conn.execute("SELECT * FROM ships WHERE id=?", (starter_id,)).fetchone()
    if not row:
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
                starter_id, "Shipyard Starter", "triangle", "#ffffff", 12,
                json.dumps(["Shipyard baseline hull"]),
                "LEO", None, None, None, None, "[]", None, None,
                json.dumps(starter_parts),
                starter_stats["fuel_kg"], starter_stats["fuel_capacity_kg"],
                starter_stats["dry_mass_kg"], starter_stats["isp_s"],
            ),
        )
    else:
        current_fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))
        fuel_capacity_kg = starter_stats["fuel_capacity_kg"]
        resolved_fuel_kg = min(current_fuel_kg, fuel_capacity_kg)
        conn.execute(
            """
            UPDATE ships
            SET name='Shipyard Starter', notes_json=?, parts_json=?,
                fuel_kg=?, fuel_capacity_kg=?, dry_mass_kg=?, isp_s=?
            WHERE id=?
            """,
            (
                json.dumps(["Shipyard baseline hull"]),
                json.dumps(starter_parts),
                resolved_fuel_kg, fuel_capacity_kg,
                starter_stats["dry_mass_kg"], starter_stats["isp_s"],
                starter_id,
            ),
        )
        if not row["location_id"] and not row["from_location_id"]:
            conn.execute("UPDATE ships SET location_id='LEO' WHERE id=?", (starter_id,))


def slugify_ship_id(raw: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", raw.strip().lower()).strip("_")
    return text or "ship"


def next_available_ship_id(conn: sqlite3.Connection, preferred: str) -> str:
    base = slugify_ship_id(preferred)
    candidate = base
    suffix = 2
    while conn.execute("SELECT 1 FROM ships WHERE id=?", (candidate,)).fetchone():
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


# ── Research tree builder (thruster tree) ────────────────────────────────

def _effect_to_text(raw_effect: Dict[str, Any]) -> str:
    stat = str(raw_effect.get("stat") or "stat")
    op = str(raw_effect.get("op") or "set")
    value = raw_effect.get("value")
    system = str(raw_effect.get("system") or "").strip()
    note = str(raw_effect.get("note") or "").strip()

    if op == "mul":
        pct = (float(value) - 1.0) * 100.0
        text = f"{stat}: {pct:+.0f}%"
    elif op == "add":
        text = f"{stat}: {float(value):+g}"
    else:
        text = f"{stat}: {value}"

    if system:
        text = f"{system}.{text}"
    if note:
        text = f"{text} ({note})"
    return text


def _slugify_lane_id(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "thruster_lane"


def build_thruster_tree_from_spec(
    spec: Dict[str, Any],
    lane_x_offset: int = 0,
    lane_width: int = 420,
    lane_id: str = "",
    lane_label: str = "",
) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []

    mains = sorted(spec.get("Main") or spec.get("engines") or [], key=lambda e: int(e.get("tier") or 0))
    upgrades = sorted(
        spec.get("Upgrade") or spec.get("upgrades") or [],
        key=lambda u: tuple(u.get("tier_between_main") or u.get("tier_between_engines") or [0, 0]),
    )

    tier_to_main_ids: Dict[int, List[str]] = {}
    main_x = lane_x_offset + 102
    upgrade_x = lane_x_offset + 128
    y_step = 260
    top_y = 120

    for main in mains:
        main_id = str(main.get("id") or "")
        tier = int(main.get("tier") or 0)
        y = top_y + (max(1, tier) - 1) * y_step
        tier_to_main_ids.setdefault(tier, []).append(main_id)
        effects = [
            f"Isp: {float(main.get('isp_s') or 0):.0f} s",
            f"Thrust: {float(main.get('max_thrust_kN') or 0):.0f} kN",
            f"Thermal power req: {float(main.get('P_req_mw_th') or 0):.0f} MW(th)",
            f"Min reactor rating: {int(main.get('min_reactor_rating') or 0)}",
        ]
        nodes.append({
            "id": main_id, "name": str(main.get("name") or main_id),
            "kind": "main", "tier": tier, "x": main_x, "y": y,
            "requires": [], "effects": effects,
            "details": {
                "branch": str(main.get("branch") or "core"),
                "engine_mass_t": float(main.get("engine_mass_t") or 0.0),
                "reaction_mass": str((main.get("consumables") or {}).get("reaction_mass") or ""),
                "lane_id": lane_id,
            },
        })

    sorted_main_ids = [str(e.get("id") or "") for e in mains if str(e.get("id") or "").strip()]
    for idx in range(1, len(sorted_main_ids)):
        edges.append({"from": sorted_main_ids[idx - 1], "to": sorted_main_ids[idx], "type": "progression"})

    node_by_id: Dict[str, Dict[str, Any]] = {str(n.get("id") or ""): n for n in nodes}
    for idx in range(1, len(sorted_main_ids)):
        current_id = sorted_main_ids[idx]
        prev_id = sorted_main_ids[idx - 1]
        if current_id in node_by_id:
            node_requires = node_by_id[current_id].setdefault("requires", [])
            if prev_id not in node_requires:
                node_requires.append(prev_id)

    upgrades_by_tier_pair: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for upgrade in upgrades:
        between = upgrade.get("tier_between_main") or upgrade.get("tier_between_engines") or [0, 0]
        tier_a = int(between[0]) if len(between) > 0 else 0
        tier_b = int(between[1]) if len(between) > 1 else tier_a
        upgrades_by_tier_pair.setdefault((tier_a, tier_b), []).append(upgrade)

    for (tier_a, tier_b), bucket in sorted(upgrades_by_tier_pair.items(), key=lambda item: item[0]):
        y1 = top_y + ((max(1, tier_a) - 1) * y_step)
        y2 = top_y + ((max(1, tier_b) - 1) * y_step)
        center_y = int((y1 + y2) * 0.5)

        for index, upgrade in enumerate(bucket):
            y = center_y + (index * 72)
            prereqs = [str(p) for p in (upgrade.get("prerequisites") or []) if str(p).strip()]
            for prereq in prereqs:
                edges.append({"from": prereq, "to": str(upgrade.get("id") or ""), "type": "prereq"})
            unlocks = [str(u) for u in (upgrade.get("unlocks") or []) if str(u).strip()]
            for unlock in unlocks:
                edges.append({"from": str(upgrade.get("id") or ""), "to": unlock, "type": "unlock"})
            effects = [_effect_to_text(e) for e in (upgrade.get("effects") or []) if isinstance(e, dict)]
            tradeoffs = [_effect_to_text(t) for t in (upgrade.get("tradeoffs") or []) if isinstance(t, dict)]
            nodes.append({
                "id": str(upgrade.get("id") or ""),
                "name": str(upgrade.get("name") or "Upgrade"),
                "kind": "upgrade", "tier_between_main": [tier_a, tier_b],
                "x": upgrade_x, "y": y,
                "requires": prereqs, "effects": effects, "tradeoffs": tradeoffs,
                "details": {
                    "applies_to_branches": [str(b) for b in (upgrade.get("applies_to_branches") or [])],
                    "unlocks": unlocks, "lane_id": lane_id,
                },
            })

    node_by_id = {str(n.get("id") or ""): n for n in nodes}
    valid_ids = set(node_by_id.keys())
    valid_edges = [e for e in edges if str(e.get("from") or "") in valid_ids and str(e.get("to") or "") in valid_ids]

    adjacency: Dict[str, set] = {nid: set() for nid in valid_ids}
    for edge in valid_edges:
        adjacency[str(edge.get("from") or "")].add(str(edge.get("to") or ""))
        adjacency[str(edge.get("to") or "")].add(str(edge.get("from") or ""))

    start_id = sorted_main_ids[0] if sorted_main_ids else (next(iter(valid_ids), None))
    visited: set = set()
    if start_id:
        stack = [start_id]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for nxt in adjacency.get(current, set()):
                if nxt not in visited:
                    stack.append(nxt)

    disconnected = sorted(valid_ids - visited)
    for node_id in disconnected:
        node = node_by_id.get(node_id) or {}
        node_tier = int(node.get("tier") or (node.get("tier_between_main") or node.get("tier_between_engines") or [1, 1])[1] or 1)
        anchor_tier = max(1, node_tier - 1)
        anchor_ids = tier_to_main_ids.get(anchor_tier) or sorted_main_ids[:1]
        if not anchor_ids:
            continue
        valid_edges.append({"from": anchor_ids[0], "to": node_id, "type": "inferred_link"})

    adjacency = {nid: set() for nid in valid_ids}
    for edge in valid_edges:
        src = str(edge.get("from") or "")
        dst = str(edge.get("to") or "")
        if src in adjacency and dst in adjacency:
            adjacency[src].add(dst)
            adjacency[dst].add(src)

    visited = set()
    if start_id:
        stack = [start_id]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for nxt in adjacency.get(current, set()):
                if nxt not in visited:
                    stack.append(nxt)
    final_disconnected = sorted(valid_ids - visited)

    return {
        "nodes": nodes, "edges": valid_edges,
        "meta": {
            "source": str(spec.get("tech_category") or spec.get("propulsion_category") or "thrusters"),
            "version": str(spec.get("version") or ""),
            "notes": [str(n) for n in (spec.get("notes") or [])],
            "reactor_model": dict(spec.get("reactor_model") or {}),
            "layout": "vertical",
            "lane": {
                "id": lane_id or _slugify_lane_id(spec.get("tech_category") or "thrusters"),
                "label": lane_label or str(spec.get("display_name") or spec.get("tech_category") or "Thrusters"),
                "x": lane_x_offset, "width": lane_width,
                "reserved": False, "node_count": len(nodes),
            },
            "connectivity": {"connected": len(final_disconnected) == 0, "disconnected_nodes": final_disconnected},
        },
    }


# ── Location tree builder ────────────────────────────────────────────────

def build_tree(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    nodes_map: Dict[str, Dict[str, Any]] = {}
    children_by_parent: Dict[Optional[str], List[str]] = {}

    for r in rows:
        nodes_map[r["id"]] = {
            "id": r["id"], "name": r["name"],
            "is_group": bool(r["is_group"]),
            "sort_order": int(r["sort_order"]),
            "children": [],
        }
        children_by_parent.setdefault(r["parent_id"], []).append(r["id"])

    def sort_key(nid: str) -> Tuple[int, str, str]:
        n = nodes_map[nid]
        return (0 if n["is_group"] else 1, n["sort_order"], n["name"].lower())

    def attach(parent_id: Optional[str]) -> List[Dict[str, Any]]:
        kids = children_by_parent.get(parent_id, [])
        kids.sort(key=sort_key)
        out = []
        for kid in kids:
            n = nodes_map[kid]
            n["children"] = attach(kid)
            out.append(n)
        return out

    return attach(None)
