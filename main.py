import hashlib
import json
import math
import os
import re
import sqlite3
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from admin_game_router import router as admin_game_router
from auth_router import router as auth_router
from auth_service import ensure_default_admin_account, get_current_user
from catalog_router import router as catalog_router
import catalog_service
from contract_router import router as contract_router
from facility_router import router as facility_router
from industry_router import router as industry_router
from inventory_router import router as inventory_router
from location_router import router as location_router
from mission_router import router as mission_router
from org_router import router as org_router
from shipyard_router import router as shipyard_router
import celestial_config
from db import APP_DIR, connect_db
from db_migrations import apply_migrations
from fleet_router import router as fleet_router
from sim_service import (
    export_simulation_state,
    game_now_s,
    import_simulation_state,
)

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def _html_no_cache(path: str) -> FileResponse:
    """Return an HTML FileResponse with no-cache headers."""
    resp = FileResponse(path)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _serve_authenticated_page(request: Request, filename: str):
    """Auth-gate a static HTML page: redirect to /login if not logged in."""
    conn = connect_db()
    try:
        if not get_current_user(conn, request):
            return RedirectResponse(url="/login", status_code=302)
    finally:
        conn.close()
    return _html_no_cache(str(APP_DIR / "static" / filename))
app.include_router(admin_game_router)
app.include_router(auth_router)
app.include_router(catalog_router)
app.include_router(contract_router)
app.include_router(facility_router)
app.include_router(fleet_router)
app.include_router(industry_router)
app.include_router(inventory_router)
app.include_router(location_router)
app.include_router(mission_router)
app.include_router(org_router)
app.include_router(shipyard_router)


@lru_cache(maxsize=1)
def _location_metadata_by_id() -> Dict[str, Dict[str, Any]]:
    try:
        return celestial_config.load_location_metadata()
    except celestial_config.CelestialConfigError as exc:
        print(f"[celestial-config] metadata load failed: {exc}")
        return {}


NTR_THRUSTER_SPEC: Dict[str, Any] = {
    "tech_category": "nuclear_thermal_rocket",
    "version": "0.1",
    "notes": [
        "One active engine per vessel.",
        "Reactor thermal power curve: P_th(MW) = 50 * R^2, where R is Reactor Rating 1..10.",
        "Engine full-power thrust is based on required thermal power P_req; if reactor can't supply P_req, throttle is capped: maxThrottle = min(1, P_th(R)/P_req).",
        "ActualThrust = maxThrust * throttle.",
        "Robotic shielding/hardening mass (optional ship rule): m_shield_t = 0.2 * R.",
    ],
    "reactor_model": {
        "rating_min": 1,
        "rating_max": 10,
        "thermal_power_mw_formula": "50 * R^2",
        "throttle_cap_formula": "min(1, P_th(R) / main.P_req_mw_th)",
    },
    "Main": [
        {
            "id": "ntr_m1_nerva_solid_core",
            "name": "NERVA-class Solid Core",
            "tier": 1,
            "engine_mass_t": 6.0,
            "min_reactor_rating": 4,
            "P_req_mw_th": 750,
            "isp_s": 850,
            "max_thrust_kN": 180,
            "branch": "solid_core",
            "consumables": {
                "reaction_mass": "water",
                "fissiles": {
                    "model": "core_life_years",
                    "core_life_years_at_full_power": 5,
                },
            },
        },
        {
            "id": "ntr_m2_dumbo_folded_flow",
            "name": "DUMBO Folded-Flow Solid Core",
            "tier": 2,
            "engine_mass_t": 2.5,
            "min_reactor_rating": 6,
            "P_req_mw_th": 1500,
            "isp_s": 920,
            "max_thrust_kN": 332,
            "branch": "solid_core",
            "consumables": {
                "reaction_mass": "water",
                "fissiles": {
                    "model": "core_life_years",
                    "core_life_years_at_full_power": 5,
                },
            },
        },
        {
            "id": "ntr_m3_particle_bed",
            "name": "Particle Bed NTR",
            "tier": 3,
            "engine_mass_t": 3.0,
            "min_reactor_rating": 7,
            "P_req_mw_th": 2200,
            "isp_s": 950,
            "max_thrust_kN": 472,
            "branch": "solid_core",
            "consumables": {
                "reaction_mass": "water",
                "fissiles": {
                    "model": "core_life_years",
                    "core_life_years_at_full_power": 5,
                },
            },
        },
        {
            "id": "ntr_m4_advanced_carbide_cermet",
            "name": "Advanced Carbide/Cermet Solid Core",
            "tier": 4,
            "engine_mass_t": 4.0,
            "min_reactor_rating": 8,
            "P_req_mw_th": 2500,
            "isp_s": 1050,
            "max_thrust_kN": 486,
            "branch": "solid_core",
            "consumables": {
                "reaction_mass": "water",
                "fissiles": {
                    "model": "core_life_years",
                    "core_life_years_at_full_power": 5,
                },
            },
        },
        {
            "id": "ntr_m5_closed_cycle_gas_core",
            "name": 'Closed-Cycle Gas Core ("Lightbulb")',
            "tier": 5,
            "engine_mass_t": 8.0,
            "min_reactor_rating": 9,
            "P_req_mw_th": 3500,
            "isp_s": 1800,
            "max_thrust_kN": 397,
            "branch": "gas_core_closed",
            "consumables": {
                "reaction_mass": "water",
                "fissiles": {
                    "model": "core_life_years",
                    "core_life_years_at_full_power": 5,
                },
            },
        },
        {
            "id": "ntr_m6_open_cycle_gas_core",
            "name": "Open-Cycle Gas Core",
            "tier": 6,
            "engine_mass_t": 10.0,
            "min_reactor_rating": 10,
            "P_req_mw_th": 5000,
            "isp_s": 3000,
            "max_thrust_kN": 340,
            "branch": "gas_core_open",
            "consumables": {
                "reaction_mass": "water",
                "fissiles": {
                    "model": "core_life_years",
                    "core_life_years_at_full_power": 5,
                },
            },
        },
    ],
    "Upgrade": [
        {
            "id": "ntr_u1_high_temp_fuel_elements",
            "name": "High-Temperature Fuel Elements",
            "tier_between_main": [1, 2],
            "prerequisites": ["ntr_m1_nerva_solid_core"],
            "applies_to_branches": ["solid_core"],
            "effects": [
                {"stat": "isp_s", "op": "mul", "value": 1.03},
                {"stat": "max_thrust_kN", "op": "mul", "value": 1.05},
            ],
            "tradeoffs": [
                {"system": "reactor", "stat": "build_cost", "op": "mul", "value": 1.10, "note": "Optional economy hook."}
            ],
        },
        {
            "id": "ntr_u2_neutronics_optimization",
            "name": "Compact Reflector & Neutronics Optimization",
            "tier_between_main": [2, 3],
            "prerequisites": ["ntr_m2_dumbo_folded_flow"],
            "applies_to_branches": ["solid_core"],
            "effects": [
                {"stat": "min_reactor_rating", "op": "add", "value": -1}
            ],
        },
        {
            "id": "ntr_u3_erosion_control",
            "name": "Particle Bed Containment & Erosion Control",
            "tier_between_main": [3, 4],
            "prerequisites": ["ntr_m3_particle_bed"],
            "applies_to_branches": ["solid_core"],
            "effects": [
                {"stat": "isp_s", "op": "mul", "value": 1.05}
            ],
            "tradeoffs": [
                {"stat": "max_thrust_kN", "op": "mul", "value": 0.95},
                {
                    "system": "fissiles",
                    "stat": "burn_rate",
                    "op": "mul",
                    "value": 0.90,
                    "note": "Interpreted as 10% longer core life at equal usage.",
                },
            ],
        },
        {
            "id": "ntr_u4_crack_resistant_nozzles",
            "name": "Carbide/Cermet Manufacturing & Crack-Resistant Nozzles",
            "tier_between_main": [4, 5],
            "prerequisites": ["ntr_m4_advanced_carbide_cermet"],
            "applies_to_branches": ["solid_core"],
            "effects": [
                {"stat": "max_thrust_kN", "op": "mul", "value": 1.05},
                {"stat": "engine_mass_t", "op": "mul", "value": 0.95},
            ],
            "unlocks": ["ntr_m5_closed_cycle_gas_core"],
        },
        {
            "id": "ntr_u5_plasma_stability_control",
            "name": "Radiative Cavity & Plasma Stability Control",
            "tier_between_main": [5, 6],
            "prerequisites": ["ntr_m5_closed_cycle_gas_core"],
            "applies_to_branches": ["gas_core_closed", "gas_core_open"],
            "effects": [
                {"stat": "isp_s", "op": "mul", "value": 1.10}
            ],
            "tradeoffs": [
                {
                    "system": "reactor",
                    "stat": "module_mass",
                    "op": "mul",
                    "value": 1.20,
                    "note": "Optional: apply to reactor module mass if you track gas-core control hardware separately.",
                }
            ],
            "unlocks": ["ntr_m6_open_cycle_gas_core"],
        },
    ],
}


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


def _path_to_legacy_stat(path: str) -> str:
    mapping = {
        "performance.isp_s": "isp_s",
        "performance.max_thrust_kN": "max_thrust_kN",
        "power_requirements.thermal_mw": "P_req_mw_th",
        "power_requirements.min_reactor_rating": "min_reactor_rating",
        "mass_t": "engine_mass_t",
    }
    if path in mapping:
        return mapping[path]
    parts = [p for p in path.split(".") if p]
    return parts[-1] if parts else "stat"


def _validate_non_empty_str(entry: Dict[str, Any], key: str, file_path: Path, errors: List[str]) -> None:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{file_path}: '{key}' must be a non-empty string")


def _validate_number(entry: Dict[str, Any], key: str, file_path: Path, errors: List[str]) -> None:
    value = entry.get(key)
    if not isinstance(value, (int, float)):
        errors.append(f"{file_path}: '{key}' must be a number")


def _validate_string_list(entry: Dict[str, Any], key: str, file_path: Path, errors: List[str], required: bool = False) -> None:
    value = entry.get(key)
    if value is None and not required:
        return
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        errors.append(f"{file_path}: '{key}' must be a list of non-empty strings")


def _validate_thruster_main_entry(entry: Dict[str, Any], file_path: Path, errors: List[str]) -> None:
    _validate_non_empty_str(entry, "id", file_path, errors)
    _validate_non_empty_str(entry, "name", file_path, errors)
    _validate_number(entry, "tier", file_path, errors)
    _validate_number(entry, "mass_t", file_path, errors)

    performance = entry.get("performance")
    if not isinstance(performance, dict):
        errors.append(f"{file_path}: 'performance' must be an object")
    else:
        _validate_number(performance, "isp_s", file_path, errors)
        _validate_number(performance, "max_thrust_kN", file_path, errors)

    power = entry.get("power_requirements")
    if not isinstance(power, dict):
        errors.append(f"{file_path}: 'power_requirements' must be an object")
    else:
        _validate_number(power, "thermal_mw", file_path, errors)
        _validate_number(power, "min_reactor_rating", file_path, errors)


def _validate_thruster_upgrade_entry(entry: Dict[str, Any], file_path: Path, errors: List[str]) -> None:
    _validate_non_empty_str(entry, "id", file_path, errors)
    _validate_non_empty_str(entry, "name", file_path, errors)

    tier_between = entry.get("tier_between_main")
    if not (
        isinstance(tier_between, list)
        and len(tier_between) >= 2
        and isinstance(tier_between[0], (int, float))
        and isinstance(tier_between[1], (int, float))
    ):
        errors.append(f"{file_path}: 'tier_between_main' must be a list like [fromTier, toTier]")

    _validate_string_list(entry, "prerequisites", file_path, errors)
    _validate_string_list(entry, "applies_to_branches", file_path, errors)


def _normalize_legacy_effect(raw_effect: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_effect, dict):
        return None

    stat = str(raw_effect.get("stat") or "").strip()
    if not stat:
        stat = _path_to_legacy_stat(str(raw_effect.get("path") or ""))

    normalized: Dict[str, Any] = {
        "stat": stat,
        "op": str(raw_effect.get("op") or "set"),
        "value": raw_effect.get("value"),
    }

    system = str(raw_effect.get("system") or "").strip()
    note = str(raw_effect.get("note") or "").strip()
    if system:
        normalized["system"] = system
    if note:
        normalized["note"] = note
    return normalized


def _load_json_file(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Top-level JSON in {path} must be an object")
    return payload


def _normalize_main_from_item(entry: Dict[str, Any]) -> Dict[str, Any]:
    performance = entry.get("performance") or {}
    power = entry.get("power_requirements") or {}
    consumables = entry.get("consumables") or {}
    fissiles = consumables.get("fissiles") or {}
    fissiles_properties = fissiles.get("properties") or {}

    return {
        "id": str(entry.get("id") or ""),
        "name": str(entry.get("name") or ""),
        "tier": int(entry.get("tier") or 0),
        "engine_mass_t": float(entry.get("mass_t") or 0.0),
        "min_reactor_rating": power.get("min_reactor_rating"),
        "P_req_mw_th": power.get("thermal_mw"),
        "isp_s": performance.get("isp_s"),
        "max_thrust_kN": performance.get("max_thrust_kN"),
        "branch": str(entry.get("branch") or "core"),
        "consumables": {
            "reaction_mass": str(consumables.get("reaction_mass") or ""),
            "fissiles": {
                "model": str(fissiles.get("model") or ""),
                "core_life_years_at_full_power": fissiles_properties.get("core_life_years_at_full_power"),
            },
        },
    }


def _normalize_upgrade_from_item(entry: Dict[str, Any]) -> Dict[str, Any]:
    effects = [
        e
        for e in (_normalize_legacy_effect(raw) for raw in (entry.get("effects") or []))
        if isinstance(e, dict)
    ]
    tradeoffs = [
        t
        for t in (_normalize_legacy_effect(raw) for raw in (entry.get("tradeoffs") or []))
        if isinstance(t, dict)
    ]

    return {
        "id": str(entry.get("id") or ""),
        "name": str(entry.get("name") or "Upgrade"),
        "tier_between_main": [int(v) for v in (entry.get("tier_between_main") or [0, 0])[:2]],
        "prerequisites": [str(p) for p in (entry.get("prerequisites") or []) if str(p).strip()],
        "applies_to_branches": [str(b) for b in (entry.get("applies_to_branches") or []) if str(b).strip()],
        "effects": effects,
        "tradeoffs": tradeoffs,
        "unlocks": [str(u) for u in (entry.get("unlocks") or []) if str(u).strip()],
    }


def load_thruster_specs_from_items() -> List[Dict[str, Any]]:
    return catalog_service.load_thruster_specs_from_items()


@lru_cache(maxsize=1)
def load_thruster_main_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_thruster_main_catalog()


def _item_roots_for(*names: str) -> List[Path]:
    roots: List[Path] = []
    for name in names:
        roots.append(APP_DIR / "items" / name)
        roots.append(APP_DIR / "items" / name.lower())
    seen: set[str] = set()
    deduped: List[Path] = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


@lru_cache(maxsize=1)
def load_resource_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_resource_catalog()


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
def load_robonaut_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_robonaut_catalog()


@lru_cache(maxsize=1)
def load_constructor_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_constructor_catalog()


@lru_cache(maxsize=1)
def load_miner_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_miner_catalog()


@lru_cache(maxsize=1)
def load_printer_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_printer_catalog()


@lru_cache(maxsize=1)
def load_refinery_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_refinery_catalog()


@lru_cache(maxsize=1)
def load_storage_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_storage_catalog()


@lru_cache(maxsize=1)
def load_recipe_catalog() -> Dict[str, Dict[str, Any]]:
    return catalog_service.load_recipe_catalog()


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

        nodes.append(
            {
                "id": main_id,
                "name": str(main.get("name") or main_id),
                "kind": "main",
                "tier": tier,
                "x": main_x,
                "y": y,
                "requires": [],
                "effects": effects,
                "details": {
                    "branch": str(main.get("branch") or "core"),
                    "engine_mass_t": float(main.get("engine_mass_t") or 0.0),
                    "reaction_mass": str((main.get("consumables") or {}).get("reaction_mass") or ""),
                    "lane_id": lane_id,
                },
            }
        )

    mains_by_id: Dict[str, Dict[str, Any]] = {str(e.get("id") or ""): e for e in mains}
    sorted_main_ids = [str(e.get("id") or "") for e in mains if str(e.get("id") or "").strip()]
    for idx in range(1, len(sorted_main_ids)):
        prev_id = sorted_main_ids[idx - 1]
        current_id = sorted_main_ids[idx]
        edges.append({"from": prev_id, "to": current_id, "type": "progression"})

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

            nodes.append(
                {
                    "id": str(upgrade.get("id") or ""),
                    "name": str(upgrade.get("name") or "Upgrade"),
                    "kind": "upgrade",
                    "tier_between_main": [tier_a, tier_b],
                    "x": upgrade_x,
                    "y": y,
                    "requires": prereqs,
                    "effects": effects,
                    "tradeoffs": tradeoffs,
                    "details": {
                        "applies_to_branches": [str(b) for b in (upgrade.get("applies_to_branches") or [])],
                        "unlocks": unlocks,
                        "lane_id": lane_id,
                    },
                }
            )

    node_by_id = {str(n.get("id") or ""): n for n in nodes}
    valid_ids = set(node_by_id.keys())
    valid_edges = [e for e in edges if str(e.get("from") or "") in valid_ids and str(e.get("to") or "") in valid_ids]

    adjacency: Dict[str, set[str]] = {nid: set() for nid in valid_ids}
    for edge in valid_edges:
        src = str(edge.get("from") or "")
        dst = str(edge.get("to") or "")
        adjacency[src].add(dst)
        adjacency[dst].add(src)

    start_id = sorted_main_ids[0] if sorted_main_ids else (next(iter(valid_ids), None))
    visited: set[str] = set()
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
        anchor_id = anchor_ids[0]
        valid_edges.append({"from": anchor_id, "to": node_id, "type": "inferred_link"})

    final_disconnected: List[str] = []
    adjacency = {nid: set() for nid in valid_ids}
    for edge in valid_edges:
        src = str(edge.get("from") or "")
        dst = str(edge.get("to") or "")
        if src not in adjacency or dst not in adjacency:
            continue
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
        "nodes": nodes,
        "edges": valid_edges,
        "meta": {
            "source": str(spec.get("tech_category") or spec.get("propulsion_category") or "thrusters"),
            "version": str(spec.get("version") or ""),
            "notes": [str(n) for n in (spec.get("notes") or [])],
            "reactor_model": dict(spec.get("reactor_model") or {}),
            "layout": "vertical",
            "lane": {
                "id": lane_id or _slugify_lane_id(spec.get("tech_category") or "thrusters"),
                "label": lane_label or str(spec.get("display_name") or spec.get("tech_category") or "Thrusters"),
                "x": lane_x_offset,
                "width": lane_width,
                "reserved": False,
                "node_count": len(nodes),
            },
            "connectivity": {
                "connected": len(final_disconnected) == 0,
                "disconnected_nodes": final_disconnected,
            },
        },
    }


def build_research_payload() -> Dict[str, Any]:
    return catalog_service.build_research_payload()


def canonical_item_category(raw: Any) -> str:
    return catalog_service.canonical_item_category(raw)


def seed_locations_and_edges_if_empty(conn: sqlite3.Connection) -> None:
    cnt = conn.execute("SELECT COUNT(*) AS c FROM locations").fetchone()["c"]
    if cnt and int(cnt) > 0:
        return

    # Groups
    groups = [
        ("grp_earth", "Earth", None, 1, 10, 0, 0),
        ("grp_earth_orbits", "Orbits", "grp_earth", 1, 10, 0, 0),
        ("grp_em_lpoints", "Earth–Luna Lagrange", "grp_earth", 1, 20, 0, 0),
        ("grp_moon", "Luna", None, 1, 20, 384400, 0),
        ("grp_moon_orbits", "Orbits", "grp_moon", 1, 10, 384400, 0),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO locations (id,name,parent_id,is_group,sort_order,x,y) VALUES (?,?,?,?,?,?,?)",
        groups,
    )

    # Leaves (coords just for drawing an abstract map)
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

    # Seed a small, gameplay-friendly direct edge network (placeholder numbers).
    # You can tune these later; the matrix will auto-regenerate.
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
    # Sort rows so parents are inserted before children (topological order).
    # Each row is (id, name, parent_id, is_group, sort_order, x, y).
    inserted: set = set()
    existing = {r["id"] for r in conn.execute("SELECT id FROM locations").fetchall()}
    inserted.update(existing)

    remaining = list(rows)
    while remaining:
        progress = False
        next_remaining = []
        for row in remaining:
            parent_id = row[2]
            if parent_id is None or parent_id in inserted:
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
                inserted.add(row[0])
                progress = True
            else:
                next_remaining.append(row)
        remaining = next_remaining
        if not progress:
            # Fall back to inserting remaining rows without FK check
            for row in remaining:
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
            break


def upsert_transfer_edges(conn: sqlite3.Connection, rows: List[Tuple[str, str, float, float, str]]) -> None:
    for row in rows:
        conn.execute(
            """
            INSERT INTO transfer_edges (from_id,to_id,dv_m_s,tof_s,edge_type)
            VALUES (?,?,?,?,?)
            ON CONFLICT(from_id,to_id) DO UPDATE SET
              dv_m_s=excluded.dv_m_s,
              tof_s=excluded.tof_s,
              edge_type=excluded.edge_type
            """,
            row,
        )


def _upsert_surface_sites(
    conn: sqlite3.Connection,
    site_rows: list,
    resource_rows: list,
) -> None:
    """Upsert surface_sites and surface_site_resources from config data."""
    for location_id, body_id, orbit_node_id, gravity_m_s2 in site_rows:
        conn.execute(
            """
            INSERT INTO surface_sites (location_id, body_id, orbit_node_id, gravity_m_s2)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(location_id) DO UPDATE SET
              body_id=excluded.body_id,
              orbit_node_id=excluded.orbit_node_id,
              gravity_m_s2=excluded.gravity_m_s2
            """,
            (location_id, body_id, orbit_node_id, gravity_m_s2),
        )
    for site_location_id, resource_id, mass_fraction in resource_rows:
        conn.execute(
            """
            INSERT INTO surface_site_resources (site_location_id, resource_id, mass_fraction)
            VALUES (?, ?, ?)
            ON CONFLICT(site_location_id, resource_id) DO UPDATE SET
              mass_fraction=excluded.mass_fraction
            """,
            (site_location_id, resource_id, mass_fraction),
        )


def _hohmann_interplanetary_dv_tof(
    r1_km: float,
    r2_km: float,
    mu_sun_km3_s2: float,
    mu_origin_km3_s2: float,
    rp_origin_km: float,
    mu_dest_km3_s2: float,
    rp_dest_km: float,
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
        current_game_time = game_now_s()
        location_rows, edge_rows = celestial_config.load_locations_and_edges(
            game_time_s=current_game_time,
        )
        upsert_locations(conn, location_rows)
        upsert_transfer_edges(conn, edge_rows)

        # Remove stale transfer edges no longer in the config
        config_edge_pairs = {(r[0], r[1]) for r in edge_rows}
        db_edges = conn.execute("SELECT from_id, to_id FROM transfer_edges").fetchall()
        for row in db_edges:
            if (row["from_id"], row["to_id"]) not in config_edge_pairs:
                conn.execute("DELETE FROM transfer_edges WHERE from_id=? AND to_id=?", (row["from_id"], row["to_id"]))

        # Seed surface site data
        try:
            site_rows, resource_rows = celestial_config.load_surface_site_data()
            _upsert_surface_sites(conn, site_rows, resource_rows)
        except celestial_config.CelestialConfigError as exc:
            print(f"[celestial-config] surface site error: {exc}")

        return
    except celestial_config.CelestialConfigError as exc:
        print(f"[celestial-config] {exc} -- falling back to built-in expansion")

    sun_x, sun_y = 0.0, 0.0

    def polar_xy(radius_km: float, angle_deg: float) -> Tuple[float, float]:
        a = math.radians(angle_deg)
        return radius_km * math.cos(a), radius_km * math.sin(a)

    # Planet heliocentric distances (semi-major-axis approximations, km)
    mercury_x, mercury_y = polar_xy(57_909_227.0, -16.0)
    venus_x, venus_y = polar_xy(108_209_475.0, 11.0)
    earth_x, earth_y = polar_xy(149_597_870.7, 0.0)
    mars_x, mars_y = polar_xy(227_943_824.0, -7.0)

    # Earth-Luna geometry
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

    # Earth-Luna L points (approximate from Earth frame, km)
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
        ("grp_em_lpoints", "Earth–Luna Lagrange", "grp_earth", 1, 20, earth_x, earth_y),
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
    node_to_body = {
        "LEO": "earth",
        "MERC_ORB": "mercury",
        "VEN_ORB": "venus",
        "LMO": "mars",
    }

    computed_edges: List[Tuple[str, str, float, float, str]] = []
    nodes = list(node_to_body.keys())
    for from_id in nodes:
        for to_id in nodes:
            if from_id == to_id:
                continue
            from_body = planetary[node_to_body[from_id]]
            to_body = planetary[node_to_body[to_id]]
            dv_m_s, tof_s = _hohmann_interplanetary_dv_tof(
                from_body["a_km"],
                to_body["a_km"],
                mu_sun,
                from_body["mu"],
                from_body["radius_km"] + from_body["alt_km"],
                to_body["mu"],
                to_body["radius_km"] + to_body["alt_km"],
            )
            computed_edges.append((from_id, to_id, round(dv_m_s, 2), round(tof_s, 1), "interplanetary"))

    mars_mu = planetary["mars"]["mu"]
    r_lmo = planetary["mars"]["radius_km"] + 250.0
    r_phobos = 9376.0
    r_deimos = 23463.2

    lmo_phobos_dv, lmo_phobos_tof = _hohmann_orbit_change_dv_tof(mars_mu, r_lmo, r_phobos)
    lmo_deimos_dv, lmo_deimos_tof = _hohmann_orbit_change_dv_tof(mars_mu, r_lmo, r_deimos)
    phobos_deimos_dv, phobos_deimos_tof = _hohmann_orbit_change_dv_tof(mars_mu, r_phobos, r_deimos)

    computed_edges.extend(
        [
            ("LMO", "PHOBOS", round(lmo_phobos_dv, 2), round(lmo_phobos_tof, 1), "local"),
            ("PHOBOS", "LMO", round(lmo_phobos_dv, 2), round(lmo_phobos_tof, 1), "local"),
            ("LMO", "DEIMOS", round(lmo_deimos_dv, 2), round(lmo_deimos_tof, 1), "local"),
            ("DEIMOS", "LMO", round(lmo_deimos_dv, 2), round(lmo_deimos_tof, 1), "local"),
            ("PHOBOS", "DEIMOS", round(phobos_deimos_dv, 2), round(phobos_deimos_tof, 1), "local"),
            ("DEIMOS", "PHOBOS", round(phobos_deimos_dv, 2), round(phobos_deimos_tof, 1), "local"),
        ]
    )

    # Approximate heliocentric transfer between Earth LEO and near-Sun orbit marker.
    computed_edges.extend(
        [
            ("LEO", "SUN", 28000.0, 130.0 * 24.0 * 3600.0, "interplanetary"),
            ("SUN", "LEO", 28000.0, 130.0 * 24.0 * 3600.0, "interplanetary"),
            ("MERC_ORB", "SUN", 12000.0, 55.0 * 24.0 * 3600.0, "interplanetary"),
            ("SUN", "MERC_ORB", 12000.0, 55.0 * 24.0 * 3600.0, "interplanetary"),
            ("VEN_ORB", "SUN", 19000.0, 90.0 * 24.0 * 3600.0, "interplanetary"),
            ("SUN", "VEN_ORB", 19000.0, 90.0 * 24.0 * 3600.0, "interplanetary"),
            ("LMO", "SUN", 22000.0, 180.0 * 24.0 * 3600.0, "interplanetary"),
            ("SUN", "LMO", 22000.0, 180.0 * 24.0 * 3600.0, "interplanetary"),
        ]
    )

    upsert_transfer_edges(conn, computed_edges)


def purge_test_ships(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM ships
        WHERE id LIKE 'test_%'
           OR id LIKE 'stack_test_%'
           OR lower(name) LIKE 'test[%'
           OR lower(name) LIKE 'stack test%'
        """
    )


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def normalize_parts(raw_parts: Any) -> List[Dict[str, Any]]:
    return catalog_service.normalize_parts(
        raw_parts,
        thruster_catalog=load_thruster_main_catalog(),
        storage_catalog=load_storage_catalog(),
        canonical_item_category=canonical_item_category,
        reactor_catalog=load_reactor_catalog(),
        generator_catalog=load_generator_catalog(),
        radiator_catalog=load_radiator_catalog(),
        robonaut_catalog=load_robonaut_catalog(),
        constructor_catalog={**load_miner_catalog(), **load_printer_catalog()},
        refinery_catalog=load_refinery_catalog(),
    )


def derive_ship_stats_from_parts(
    parts: List[Dict[str, Any]],
    current_fuel_kg: Optional[float] = None,
    cargo_mass_kg: float = 0.0,
    cargo_avg_density: float = 2500.0,
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


def split_ship_parts_and_cargo(parts_json: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse ship parts_json in either legacy-list or object form.

    Supported shapes:
    - [ ...parts... ]
    - {"parts": [...], "cargo": [...]} (cargo retained for forward compatibility)
    """
    try:
        raw = json.loads(parts_json or "[]")
    except Exception:
        return [], []

    if isinstance(raw, dict):
        raw_parts = raw.get("parts") or []
        raw_cargo = raw.get("cargo") or []
        parts = [p for p in raw_parts if isinstance(p, dict)]
        cargo = [c for c in raw_cargo if isinstance(c, dict)]
        return parts, cargo

    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)], []

    return [], []


def merge_ship_parts_and_cargo(parts: List[Dict[str, Any]], cargo: Optional[List[Dict[str, Any]]] = None) -> str:
    """Serialize ship parts/cargo to DB JSON.

    Writes legacy list form when cargo is empty to minimize compatibility risk.
    """
    safe_parts = [dict(p) for p in (parts or []) if isinstance(p, dict)]
    safe_cargo = [dict(c) for c in (cargo or []) if isinstance(c, dict)]
    if safe_cargo:
        return json.dumps({"parts": safe_parts, "cargo": safe_cargo}, sort_keys=True)
    return json.dumps(safe_parts)


def compute_ship_inventory_resources(
    ship_id: str,
    cargo_stacks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build resource display items from ship cargo stacks (DB rows)."""
    resources_catalog = load_resource_catalog()
    rows: List[Dict[str, Any]] = []
    for stack in cargo_stacks or []:
        resource_id = str(stack.get("resource_id") or "").strip()
        mass_kg = max(0.0, float(stack.get("mass_kg") or 0.0))
        if not resource_id or mass_kg <= 1e-9:
            continue
        meta = resources_catalog.get(resource_id) or {}
        label = str(meta.get("name") or resource_id)
        res_category_id = str(meta.get("category_id") or "resource")
        res_icon = str(meta.get("icon") or "")
        rows.append({
            "item_uid": f"ship:{ship_id}:resource:{resource_id}",
            "item_kind": "resource",
            "item_id": resource_id,
            "label": label,
            "subtitle": f"Cargo · {mass_kg:,.0f} kg",
            "category": res_category_id,
            "category_id": res_category_id,
            "resource_id": resource_id,
            "icon": res_icon,
            "mass_kg": mass_kg,
            "quantity": mass_kg,
            "icon_seed": f"ship_resource::{resource_id}",
            "transfer": {
                "source_kind": "ship_resource",
                "source_id": ship_id,
                "source_key": resource_id,
                "amount": mass_kg,
            },
        })
    rows.sort(key=lambda r: str(r.get("label") or r.get("resource_id") or ""))
    return rows


def get_ship_cargo_stacks(conn: sqlite3.Connection, ship_id: str) -> List[Dict[str, Any]]:
    """Read all cargo resource stacks for a ship from DB."""
    db_rows = conn.execute(
        "SELECT resource_id, mass_kg FROM ship_cargo_stacks WHERE ship_id=? AND mass_kg > 0",
        (str(ship_id),),
    ).fetchall()
    return [{"resource_id": str(r["resource_id"]), "mass_kg": float(r["mass_kg"])} for r in db_rows]


def add_cargo_to_ship(
    conn: sqlite3.Connection,
    ship_id: str,
    resource_id: str,
    mass_kg: float,
    cargo_capacity_kg: float = 0.0,
) -> float:
    """Add resource mass to ship cargo. Enforces ship cargo mass budget. Returns accepted kg."""
    sid = str(ship_id)
    rid = str(resource_id)
    requested = max(0.0, float(mass_kg))
    if requested <= 0.01:
        raise ValueError("Nothing to load")

    if cargo_capacity_kg > 0.0:
        capacity_kg = max(0.0, float(cargo_capacity_kg))
    else:
        ship_row = conn.execute("SELECT parts_json FROM ships WHERE id=?", (sid,)).fetchone()
        if not ship_row:
            raise ValueError("Ship not found")
        raw_parts, _raw_cargo = split_ship_parts_and_cargo(ship_row["parts_json"] or "[]")
        parts = normalize_parts(raw_parts)
        capacity_kg = sum(
            max(0.0, float(part.get("cargo_capacity_kg") or 0.0))
            for part in parts
            if str(part.get("type") or "").lower() == "thruster"
        )

    row = conn.execute(
        "SELECT COALESCE(SUM(mass_kg), 0.0) AS total_mass_kg FROM ship_cargo_stacks WHERE ship_id=?",
        (sid,),
    ).fetchone()
    used_kg = max(0.0, float(row["total_mass_kg"] if row else 0.0))
    available_kg = max(0.0, capacity_kg - used_kg)
    accepted = min(requested, available_kg)
    if accepted <= 0.01:
        raise ValueError("Ship cargo is full")

    conn.execute(
        """
        INSERT INTO ship_cargo_stacks (ship_id, resource_id, mass_kg)
        VALUES (?, ?, ?)
        ON CONFLICT(ship_id, resource_id)
        DO UPDATE SET mass_kg = mass_kg + excluded.mass_kg
        """,
        (sid, rid, accepted),
    )
    return accepted


def remove_cargo_from_ship(
    conn: sqlite3.Connection,
    ship_id: str,
    resource_id: str,
    mass_kg: float,
) -> float:
    """Remove resource mass from ship cargo. Returns actual amount taken."""
    sid = str(ship_id)
    rid = str(resource_id)
    row = conn.execute(
        "SELECT mass_kg FROM ship_cargo_stacks WHERE ship_id=? AND resource_id=?",
        (sid, rid),
    ).fetchone()
    if not row:
        raise ValueError(f"No {resource_id} in ship cargo")
    available = max(0.0, float(row["mass_kg"]))
    taken = min(max(0.0, float(mass_kg)), available)
    if taken <= 0.01:
        return 0.0
    remaining = available - taken
    if remaining < 0.01:
        conn.execute(
            "DELETE FROM ship_cargo_stacks WHERE ship_id=? AND resource_id=?",
            (sid, rid),
        )
    else:
        conn.execute(
            "UPDATE ship_cargo_stacks SET mass_kg=? WHERE ship_id=? AND resource_id=?",
            (remaining, sid, rid),
        )
    return taken


def wipe_ship_cargo(conn: sqlite3.Connection, ship_id: str) -> None:
    """Delete all cargo stacks for a ship."""
    conn.execute("DELETE FROM ship_cargo_stacks WHERE ship_id=?", (str(ship_id),))


def compute_ship_cargo_summary(
    parts: List[Dict[str, Any]],
    cargo_stacks: List[Dict[str, Any]],
    resource_catalog: Dict[str, Any],
) -> Dict[str, Any]:
    """Mass-budget cargo summary with surcharge."""
    cargo_capacity_kg = sum(
        max(0.0, float(part.get("cargo_capacity_kg") or 0.0))
        for part in (parts or [])
        if str(part.get("type") or "").lower() == "thruster"
    )
    total_cargo_kg = sum(
        max(0.0, float(c.get("mass_kg") or 0.0))
        for c in (cargo_stacks or [])
    )
    total_volume_m3 = 0.0
    for c in (cargo_stacks or []):
        rid = c.get("resource_id", "")
        mass = max(0.0, float(c.get("mass_kg") or 0.0))
        res = resource_catalog.get(rid) or {}
        density = max(1.0, float(res.get("mass_per_m3_kg") or 2500.0))
        total_volume_m3 += mass / density
    avg_density = (total_cargo_kg / total_volume_m3) if total_volume_m3 > 0 else 2500.0
    surcharge_kg = 0.0  # future: density-based surcharge
    return {
        "cargo_capacity_kg": round(cargo_capacity_kg, 2),
        "cargo_used_kg": total_cargo_kg,
        "cargo_free_kg": round(max(0.0, cargo_capacity_kg - total_cargo_kg), 2),
        "cargo_surcharge_kg": round(surcharge_kg, 2),
        "cargo_effective_kg": round(total_cargo_kg + surcharge_kg, 2),
        "avg_density_kg_m3": round(avg_density, 1),
    }


def _json_dumps_stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _part_stack_identity(part: Dict[str, Any]) -> Tuple[str, str, str, str]:
    clean = dict(part or {})
    normalized = normalize_parts([clean])
    payload_part = normalized[0] if normalized else dict(clean)
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
    corp_id: str = "",
    facility_id: str = "",  # kept for caller compat, always ignored
) -> None:
    # PK is (location_id, corp_id, stack_type, stack_key) — cargo is location-scoped
    cid = corp_id or ""
    row = conn.execute(
        """
        SELECT quantity,mass_kg,volume_m3
        FROM location_inventory_stacks
        WHERE location_id=? AND corp_id=? AND stack_type=? AND stack_key=?
        """,
        (location_id, cid, stack_type, stack_key),
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
              location_id,corp_id,facility_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (location_id, cid, "", stack_type, stack_key, item_id, name, qty, mass, vol, payload_json, now),
        )
        return

    qty = max(0.0, float(row["quantity"] or 0.0) + float(quantity_delta or 0.0))
    mass = max(0.0, float(row["mass_kg"] or 0.0) + float(mass_delta_kg or 0.0))
    vol = max(0.0, float(row["volume_m3"] or 0.0) + float(volume_delta_m3 or 0.0))

    if qty <= 1e-9 and mass <= 1e-9 and vol <= 1e-9:
        conn.execute(
            "DELETE FROM location_inventory_stacks WHERE location_id=? AND corp_id=? AND stack_type=? AND stack_key=?",
            (location_id, cid, stack_type, stack_key),
        )
        return

    conn.execute(
        """
        UPDATE location_inventory_stacks
        SET item_id=?, name=?, quantity=?, mass_kg=?, volume_m3=?, payload_json=?, updated_at=?
        WHERE location_id=? AND corp_id=? AND stack_type=? AND stack_key=?
        """,
        (item_id, name, qty, mass, vol, payload_json, now, location_id, cid, stack_type, stack_key),
    )


def add_resource_to_location_inventory(conn: sqlite3.Connection, location_id: str, resource_id: str, mass_kg: float, *, corp_id: str = "", facility_id: str = "") -> None:
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
        corp_id=corp_id,
    )


def add_part_to_location_inventory(conn: sqlite3.Connection, location_id: str, part: Dict[str, Any], count: float = 1.0, *, corp_id: str = "", facility_id: str = "") -> None:
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
        corp_id=corp_id,
    )


def get_location_inventory_payload(conn: sqlite3.Connection, location_id: str, *, corp_id: str = None, facility_id: str = "") -> Dict[str, Any]:
    # facility_id param kept for caller compat but ignored — cargo is location-scoped
    if corp_id is not None:
        rows = conn.execute(
            """
            SELECT location_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
            FROM location_inventory_stacks
            WHERE location_id=? AND corp_id=?
            ORDER BY stack_type, item_id, stack_key
            """,
            (location_id, corp_id),
        ).fetchall()
    else:
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
    part_catalog_ids = _part_catalog_item_ids()
    resource_ids = set(str(k) for k in load_resource_catalog().keys())
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
        if stack_type == "resource" and not _is_part_like_stack(r, payload, part_catalog_ids, resource_ids):
            rid = str(payload.get("resource_id") or base["item_id"])
            base["resource_id"] = rid
            res_meta = load_resource_catalog().get(rid) or {}
            base["phase"] = str(res_meta.get("phase") or "solid").strip().lower()
            base["category_id"] = str(res_meta.get("category_id") or "resource")
            resources.append(base)
            continue
        if stack_type in ("part", "resource"):
            part = payload.get("part") if isinstance(payload, dict) else None
            if not isinstance(part, dict) or not part:
                part = _resolve_inventory_part_fallback(base["item_id"], base["name"], base["mass_kg"], base["quantity"])
            else:
                part = dict(part)
                part.setdefault("item_id", base["item_id"])
                part.setdefault("name", base["name"])
            base["part"] = part
            parts.append(base)

    return {
        "location_id": location_id,
        "resources": resources,
        "parts": parts,
    }


def _resolve_inventory_part_fallback(item_id: str, name: str, stack_mass_kg: float, quantity: float) -> Dict[str, Any]:
    loaders = (
        load_thruster_main_catalog,
        load_reactor_catalog,
        load_generator_catalog,
        load_radiator_catalog,
        load_miner_catalog,
        load_printer_catalog,
        load_refinery_catalog,
        load_robonaut_catalog,
    )
    item_key = str(item_id or "").strip()
    for loader in loaders:
        part = loader().get(item_key)
        if isinstance(part, dict) and part:
            hydrated = dict(part)
            hydrated.setdefault("item_id", item_key)
            hydrated.setdefault("name", str(name or item_key))
            return hydrated

    qty = max(0.0, float(quantity or 0.0))
    per_unit_mass = max(0.0, float(stack_mass_kg or 0.0)) / qty if qty > 0.0 else 0.0
    return {
        "item_id": item_key,
        "name": str(name or item_key),
        "type": "generic",
        "category_id": "generic",
        "mass_kg": per_unit_mass,
    }


def _part_catalog_item_ids() -> set[str]:
    item_ids: set[str] = set()
    for loader in (
        load_thruster_main_catalog,
        load_reactor_catalog,
        load_generator_catalog,
        load_radiator_catalog,
        load_miner_catalog,
        load_printer_catalog,
        load_refinery_catalog,
        load_robonaut_catalog,
    ):
        item_ids.update(str(k) for k in loader().keys())
    return item_ids


def _is_part_like_stack(
    row: sqlite3.Row,
    payload: Dict[str, Any],
    part_catalog_ids: set[str],
    resource_ids: set[str],
) -> bool:
    stack_type = str(row["stack_type"] or "").strip().lower()
    if stack_type == "part":
        return True
    if stack_type != "resource":
        return False

    item_id = str(row["item_id"] or "").strip()
    if item_id and item_id in part_catalog_ids and item_id not in resource_ids:
        return True

    part_payload = payload.get("part") if isinstance(payload, dict) else None
    if isinstance(part_payload, dict):
        part_item_id = str(part_payload.get("item_id") or part_payload.get("id") or "").strip()
        if part_item_id and part_item_id in part_catalog_ids:
            return True

    return False


def consume_parts_from_location_inventory(
    conn: sqlite3.Connection,
    location_id: str,
    requested_item_ids: List[str],
    *,
    corp_id: str = "",
    facility_id: str = "",  # kept for caller compat, ignored
) -> List[Dict[str, Any]]:
    requested = [str(x).strip() for x in (requested_item_ids or []) if str(x).strip()]
    if not requested:
        return []

    # Cargo is location-scoped: query by (location_id, corp_id)
    if corp_id:
        available_rows = conn.execute(
            """
            SELECT location_id,corp_id,facility_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
            FROM location_inventory_stacks
            WHERE location_id=? AND corp_id=?
            ORDER BY item_id, updated_at, stack_key
            """,
            (location_id, corp_id),
        ).fetchall()
    else:
        available_rows = conn.execute(
            """
            SELECT location_id,corp_id,facility_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
            FROM location_inventory_stacks
            WHERE location_id=?
            ORDER BY item_id, updated_at, stack_key
            """,
            (location_id,),
        ).fetchall()

    part_catalog_ids = _part_catalog_item_ids()
    resource_ids = set(str(k) for k in load_resource_catalog().keys())

    by_item: Dict[str, List[sqlite3.Row]] = {}
    for row in available_rows:
        payload = json.loads(row["payload_json"] or "{}")
        if not _is_part_like_stack(row, payload, part_catalog_ids, resource_ids):
            continue
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
            part = _resolve_inventory_part_fallback(
                item_id,
                str(chosen["name"] or item_id),
                float(chosen["mass_kg"] or 0.0),
                float(chosen["quantity"] or 0.0),
            )
        consumed_parts.append(part)

        qty_before = max(0.0, float(chosen["quantity"] or 0.0))
        mass_before = max(0.0, float(chosen["mass_kg"] or 0.0))
        mass_per = mass_before / qty_before if qty_before > 0 else max(0.0, float(part.get("mass_kg") or 0.0))
        volume_before = max(0.0, float(chosen["volume_m3"] or 0.0))
        volume_per = volume_before / qty_before if qty_before > 0 else 0.0
        chosen_stack_type = str(chosen["stack_type"] or "part")

        row_corp_id = str(chosen["corp_id"]) if "corp_id" in chosen.keys() else corp_id
        _upsert_inventory_stack(
            conn,
            location_id=location_id,
            stack_type=chosen_stack_type,
            stack_key=str(chosen["stack_key"]),
            item_id=str(chosen["item_id"]),
            name=str(chosen["name"]),
            quantity_delta=-1.0,
            mass_delta_kg=-mass_per,
            volume_delta_m3=-volume_per,
            payload_json=str(chosen["payload_json"] or "{}"),
            corp_id=row_corp_id,
        )

        updated_row = conn.execute(
            """
            SELECT location_id,corp_id,facility_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
            FROM location_inventory_stacks
            WHERE location_id=? AND corp_id=? AND stack_type=? AND stack_key=?
            """,
            (location_id, row_corp_id, chosen_stack_type, str(chosen["stack_key"])),
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


def _get_location_row(conn: sqlite3.Connection, location_id: str) -> sqlite3.Row:
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


def _load_ship_inventory_state(conn: sqlite3.Connection, ship_id: str) -> Dict[str, Any]:
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

    raw_parts, _raw_cargo = split_ship_parts_and_cargo(row["parts_json"] or "[]")
    parts = normalize_parts(raw_parts)
    fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))
    cargo_stacks = get_ship_cargo_stacks(conn, sid)
    resource_catalog = load_resource_catalog()
    resources = compute_ship_inventory_resources(sid, cargo_stacks)
    cargo_summary = compute_ship_cargo_summary(parts, cargo_stacks, resource_catalog)
    location_id = str(row["location_id"] or "").strip()
    is_docked = bool(location_id) and row["arrives_at"] is None
    return {
        "row": row,
        "parts": parts,
        "fuel_kg": fuel_kg,
        "cargo_stacks": cargo_stacks,
        "resources": resources,
        "cargo_summary": cargo_summary,
        "location_id": location_id,
        "is_docked": is_docked,
    }


def _persist_ship_inventory_state(
    conn: sqlite3.Connection,
    *,
    ship_id: str,
    parts: List[Dict[str, Any]],
    fuel_kg: float,
) -> None:
    """Persist ship parts, fuel, and derived stats. Cargo lives in ship_cargo_stacks."""
    cargo_stacks = get_ship_cargo_stacks(conn, ship_id)
    resource_catalog = load_resource_catalog()
    total_cargo_kg = sum(max(0.0, float(c.get("mass_kg") or 0.0)) for c in cargo_stacks)
    total_volume_m3 = 0.0
    for c in cargo_stacks:
        rid = c.get("resource_id", "")
        mass = max(0.0, float(c.get("mass_kg") or 0.0))
        res = resource_catalog.get(rid) or {}
        density = max(1.0, float(res.get("mass_per_m3_kg") or 2500.0))
        total_volume_m3 += mass / density
    avg_density = (total_cargo_kg / total_volume_m3) if total_volume_m3 > 0 else 2500.0

    stats = derive_ship_stats_from_parts(
        parts,
        current_fuel_kg=max(0.0, float(fuel_kg or 0.0)),
        cargo_mass_kg=total_cargo_kg,
        cargo_avg_density=avg_density,
    )
    if len(parts) == 0 and max(0.0, float(stats.get("fuel_kg") or 0.0)) <= 1e-9:
        conn.execute("DELETE FROM ships WHERE id=?", (ship_id,))
        wipe_ship_cargo(conn, ship_id)
        return
    conn.execute(
        """
        UPDATE ships
        SET parts_json=?, fuel_kg=?, fuel_capacity_kg=?, dry_mass_kg=?, isp_s=?
        WHERE id=?
        """,
        (
            merge_ship_parts_and_cargo(parts),
            stats["fuel_kg"],
            stats["fuel_capacity_kg"],
            stats["dry_mass_kg"],
            stats["isp_s"],
            ship_id,
        ),
    )


def _resource_stack_row(
    conn: sqlite3.Connection,
    location_id: str,
    stack_key: str,
    *,
    corp_id: str = None,
    facility_id: str = "",  # kept for caller compat, ignored
) -> sqlite3.Row:
    # Cargo is location-scoped: query by (location_id, corp_id) only
    if corp_id is not None:
        row = conn.execute(
            """
            SELECT location_id,corp_id,facility_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
            FROM location_inventory_stacks
            WHERE location_id=? AND corp_id=? AND stack_type='resource' AND stack_key=?
            """,
            (location_id, corp_id, stack_key),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT location_id,corp_id,facility_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
            FROM location_inventory_stacks
            WHERE location_id=? AND stack_type='resource' AND stack_key=?
            """,
            (location_id, stack_key),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Resource stack not found")
    return row


def _consume_location_resource_mass(conn: sqlite3.Connection, row: sqlite3.Row, mass_kg: float) -> float:
    available_mass = max(0.0, float(row["mass_kg"] or 0.0))
    amount = max(0.0, min(available_mass, float(mass_kg or 0.0)))
    if amount <= 0.0:
        return 0.0

    available_vol = max(0.0, float(row["volume_m3"] or 0.0))
    volume_delta = -(available_vol * (amount / available_mass)) if available_mass > 1e-9 else 0.0

    row_corp_id = str(row["corp_id"]) if "corp_id" in row.keys() else ""
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
        corp_id=row_corp_id,
    )
    return amount

def _part_stack_row(
    conn: sqlite3.Connection,
    location_id: str,
    stack_key: str,
    *,
    corp_id: str = None,
    facility_id: str = "",  # kept for caller compat, ignored
) -> sqlite3.Row:
    # Cargo is location-scoped: query by (location_id, corp_id) only
    if corp_id is not None:
        row = conn.execute(
            """
            SELECT location_id,corp_id,facility_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
            FROM location_inventory_stacks
            WHERE location_id=? AND corp_id=? AND stack_type='part' AND stack_key=?
            """,
            (location_id, corp_id, stack_key),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT location_id,corp_id,facility_id,stack_type,stack_key,item_id,name,quantity,mass_kg,volume_m3,payload_json,updated_at
            FROM location_inventory_stacks
            WHERE location_id=? AND stack_type='part' AND stack_key=?
            """,
            (location_id, stack_key),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Part stack not found")
    return row


def _consume_location_part_unit(conn: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
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

    item_id = str(part.get("item_id") or row["item_id"] or "")
    if item_id == "mission_materials_module" and not part.get("_mission_id"):
        mission_id = ""
        if isinstance(payload, dict):
            mission_id = str(payload.get("mission_id") or "")
            if not mission_id:
                payload_part = payload.get("part")
                if isinstance(payload_part, dict):
                    mission_id = str(payload_part.get("_mission_id") or payload_part.get("mission_id") or "")
        if mission_id:
            part["_mission_id"] = mission_id

    row_corp_id = str(row["corp_id"]) if "corp_id" in row.keys() else ""
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
        corp_id=row_corp_id,
    )

    return dict(part)


def _inventory_items_for_ship(ship_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = list(ship_state.get("resources") or [])
    rows.sort(key=lambda r: (str(r.get("phase") or ""), str(r.get("label") or r.get("item_id") or "")))
    return rows


def _stack_items_for_ship(ship_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    ship_row = ship_state.get("row")
    if isinstance(ship_row, sqlite3.Row):
        ship_id = str(ship_row["id"] or "")
    elif isinstance(ship_row, dict):
        ship_id = str(ship_row.get("id") or "")
    else:
        ship_id = ""
    can_transfer = bool(ship_state.get("is_docked"))

    rows: List[Dict[str, Any]] = []

    for idx, part in enumerate(ship_state.get("parts") or []):
        part_payload = part if isinstance(part, dict) else {}
        item_id = str(part_payload.get("item_id") or part_payload.get("id") or part_payload.get("type") or f"part_{idx}")
        label = str(part_payload.get("name") or item_id or f"Part {idx + 1}")
        ptype = str(part_payload.get("type") or part_payload.get("category_id") or "module")
        mass_kg = max(0.0, float(part_payload.get("mass_kg") or 0.0))
        volume_m3 = 0.0
        subtitle = ptype

        transfer = None
        if can_transfer and ship_id:
            transfer = {
                "source_kind": "ship_part",
                "source_id": ship_id,
                "source_key": str(idx),
                "amount": 1.0,
            }

        part_category = str(part_payload.get("type") or part_payload.get("category_id") or "module").strip().lower()
        tooltip_lines = []
        thrust_kn = float(part_payload.get("thrust_kn") or 0)
        isp_s = float(part_payload.get("isp_s") or 0)
        power_mw = float(part_payload.get("thermal_mw") or part_payload.get("power_mw") or 0)
        cap_m3_val = float(part_payload.get("capacity_m3") or 0)

        # Equipment-specific fields for deploy modal
        ship_electric_mw = float(part_payload.get("electric_mw") or 0)
        ship_thermal_mw = float(part_payload.get("thermal_mw") or 0)
        ship_thermal_mw_input = float(part_payload.get("thermal_mw_input") or 0)
        ship_waste_heat_mw = float(part_payload.get("waste_heat_mw") or 0)
        ship_heat_rejection_mw = float(part_payload.get("heat_rejection_mw") or 0)
        ship_water_extraction_rate = float(part_payload.get("water_extraction_kg_per_hr") or 0)
        ship_min_water_ice_fraction = float(part_payload.get("min_water_ice_fraction") or 0)
        ship_max_water_ice_fraction = float(part_payload.get("max_water_ice_fraction") or 1)
        ship_mining_rate = float(part_payload.get("mining_rate_kg_per_hr") or 0)
        ship_construction_rate = float(part_payload.get("construction_rate_kg_per_hr") or 0)
        ship_conversion_eff = float(part_payload.get("conversion_efficiency") or 0)
        ship_excavation_type = str(part_payload.get("excavation_type") or "")
        ship_miner_type = str(part_payload.get("miner_type") or "")
        ship_printer_type = str(part_payload.get("printer_type") or "")
        ship_fabrication_type = str(part_payload.get("fabrication_type") or "")
        ship_specialization = str(part_payload.get("specialization") or "")
        ship_max_recipe_tier = int(part_payload.get("max_recipe_tier") or 0)
        ship_throughput_mult = float(part_payload.get("throughput_mult") or 0)
        ship_min_gravity = float(part_payload.get("min_surface_gravity_ms2") or 0)
        ship_operating_temp_k = float(part_payload.get("operating_temp_k") or 0)
        ship_branch = str(part_payload.get("branch") or "")
        ship_tech_level = float(part_payload.get("tech_level") or 0)

        ship_row: Dict[str, Any] = {
            "item_uid": f"ship:{ship_id}:part:{idx}",
            "item_kind": "part",
            "part_index": idx,
            "item_id": item_id,
            "label": label,
            "subtitle": subtitle,
            "category": part_category,
            "category_id": part_category,
            "type": part_category,
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
        if ship_electric_mw > 0:
            ship_row["electric_mw"] = ship_electric_mw
        if ship_thermal_mw > 0:
            ship_row["thermal_mw"] = ship_thermal_mw
        if ship_thermal_mw_input > 0:
            ship_row["thermal_mw_input"] = ship_thermal_mw_input
        if ship_waste_heat_mw > 0:
            ship_row["waste_heat_mw"] = ship_waste_heat_mw
        if ship_heat_rejection_mw > 0:
            ship_row["heat_rejection_mw"] = ship_heat_rejection_mw
        if ship_water_extraction_rate > 0:
            ship_row["water_extraction_kg_per_hr"] = ship_water_extraction_rate
        if "min_water_ice_fraction" in part_payload:
            ship_row["min_water_ice_fraction"] = ship_min_water_ice_fraction
        if "max_water_ice_fraction" in part_payload:
            ship_row["max_water_ice_fraction"] = ship_max_water_ice_fraction
        if ship_mining_rate > 0:
            ship_row["mining_rate_kg_per_hr"] = ship_mining_rate
        if ship_construction_rate > 0:
            ship_row["construction_rate_kg_per_hr"] = ship_construction_rate
        if ship_conversion_eff > 0:
            ship_row["conversion_efficiency"] = ship_conversion_eff
        if ship_excavation_type:
            ship_row["excavation_type"] = ship_excavation_type
        if ship_miner_type:
            ship_row["miner_type"] = ship_miner_type
        if ship_printer_type:
            ship_row["printer_type"] = ship_printer_type
        if ship_fabrication_type:
            ship_row["fabrication_type"] = ship_fabrication_type
        if ship_specialization:
            ship_row["specialization"] = ship_specialization
        if ship_max_recipe_tier > 0:
            ship_row["max_recipe_tier"] = ship_max_recipe_tier
        if ship_throughput_mult > 0:
            ship_row["throughput_mult"] = ship_throughput_mult
        if ship_min_gravity > 0:
            ship_row["min_surface_gravity_ms2"] = ship_min_gravity
        if ship_operating_temp_k > 0:
            ship_row["operating_temp_k"] = ship_operating_temp_k
        if ship_branch:
            ship_row["branch"] = ship_branch
        if ship_tech_level > 0:
            ship_row["tech_level"] = ship_tech_level

        rows.append(ship_row)

    return rows


def _stack_items_for_location(location_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
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
                    phase = "solid"

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

        # Equipment-specific fields for deploy modal
        electric_mw = float(part_payload_loc.get("electric_mw") or 0)
        thermal_mw = float(part_payload_loc.get("thermal_mw") or 0)
        thermal_mw_input = float(part_payload_loc.get("thermal_mw_input") or 0)
        waste_heat_mw = float(part_payload_loc.get("waste_heat_mw") or 0)
        heat_rejection_mw = float(part_payload_loc.get("heat_rejection_mw") or 0)
        water_extraction_rate = float(part_payload_loc.get("water_extraction_kg_per_hr") or 0)
        min_water_ice_fraction = float(part_payload_loc.get("min_water_ice_fraction") or 0)
        max_water_ice_fraction = float(part_payload_loc.get("max_water_ice_fraction") or 1)
        mining_rate = float(part_payload_loc.get("mining_rate_kg_per_hr") or 0)
        construction_rate = float(part_payload_loc.get("construction_rate_kg_per_hr") or 0)
        conversion_eff = float(part_payload_loc.get("conversion_efficiency") or 0)
        excavation_type = str(part_payload_loc.get("excavation_type") or "")
        miner_type = str(part_payload_loc.get("miner_type") or "")
        printer_type = str(part_payload_loc.get("printer_type") or "")
        fabrication_type = str(part_payload_loc.get("fabrication_type") or "")
        specialization = str(part_payload_loc.get("specialization") or "")
        max_recipe_tier = int(part_payload_loc.get("max_recipe_tier") or 0)
        throughput_mult = float(part_payload_loc.get("throughput_mult") or 0)
        min_gravity = float(part_payload_loc.get("min_surface_gravity_ms2") or 0)
        operating_temp_k = float(part_payload_loc.get("operating_temp_k") or 0)
        branch = str(part_payload_loc.get("branch") or "")
        tech_level = float(part_payload_loc.get("tech_level") or 0)

        row_dict: Dict[str, Any] = {
            "item_uid": f"location:{location_id}:part:{stack_key}",
            "item_kind": "part",
            "item_id": str(part.get("item_id") or "part"),
            "label": str(part.get("name") or part.get("item_id") or "Part"),
            "subtitle": subtitle,
            "category": loc_part_category,
            "category_id": loc_part_category,
            "type": loc_part_category,
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
        # Include equipment fields when present
        if electric_mw > 0:
            row_dict["electric_mw"] = electric_mw
        if thermal_mw > 0:
            row_dict["thermal_mw"] = thermal_mw
        if thermal_mw_input > 0:
            row_dict["thermal_mw_input"] = thermal_mw_input
        if waste_heat_mw > 0:
            row_dict["waste_heat_mw"] = waste_heat_mw
        if heat_rejection_mw > 0:
            row_dict["heat_rejection_mw"] = heat_rejection_mw
        if water_extraction_rate > 0:
            row_dict["water_extraction_kg_per_hr"] = water_extraction_rate
        if "min_water_ice_fraction" in part_payload_loc:
            row_dict["min_water_ice_fraction"] = min_water_ice_fraction
        if "max_water_ice_fraction" in part_payload_loc:
            row_dict["max_water_ice_fraction"] = max_water_ice_fraction
        if mining_rate > 0:
            row_dict["mining_rate_kg_per_hr"] = mining_rate
        if construction_rate > 0:
            row_dict["construction_rate_kg_per_hr"] = construction_rate
        if conversion_eff > 0:
            row_dict["conversion_efficiency"] = conversion_eff
        if excavation_type:
            row_dict["excavation_type"] = excavation_type
        if miner_type:
            row_dict["miner_type"] = miner_type
        if printer_type:
            row_dict["printer_type"] = printer_type
        if fabrication_type:
            row_dict["fabrication_type"] = fabrication_type
        if specialization:
            row_dict["specialization"] = specialization
        if max_recipe_tier > 0:
            row_dict["max_recipe_tier"] = max_recipe_tier
        if throughput_mult > 0:
            row_dict["throughput_mult"] = throughput_mult
        if min_gravity > 0:
            row_dict["min_surface_gravity_ms2"] = min_gravity
        if operating_temp_k > 0:
            row_dict["operating_temp_k"] = operating_temp_k
        if branch:
            row_dict["branch"] = branch
        if tech_level > 0:
            row_dict["tech_level"] = tech_level

        rows.append(row_dict)
    return rows


def _inventory_items_for_location(location_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    location_id = str(location_payload.get("location_id") or "")
    resources_catalog = load_resource_catalog()
    rows: List[Dict[str, Any]] = []
    for resource in location_payload.get("resources") or []:
        stack_key = str(resource.get("stack_key") or "")
        mass_kg = max(0.0, float(resource.get("mass_kg") or 0.0))
        rid = str(resource.get("resource_id") or resource.get("item_id") or "resource")
        res_meta = resources_catalog.get(rid) or {}
        res_phase = str(resource.get("phase") or res_meta.get("phase") or "solid").strip().lower()
        res_category_id = str(resource.get("category_id") or res_meta.get("category_id") or "resource")
        rows.append(
            {
                "item_uid": f"location:{location_id}:resource:{stack_key}",
                "item_kind": "resource",
                "item_id": rid,
                "label": str(resource.get("name") or resource.get("item_id") or "Resource"),
                "subtitle": "Location Resource",
                "category": res_category_id,
                "category_id": res_category_id,
                "resource_id": str(resource.get("resource_id") or resource.get("item_id") or ""),
                "phase": res_phase,
                "icon": str(res_meta.get("icon") or ""),
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

    # Parts are NOT included here — they are shown via stack_items
    # (the PARTS section) on the cargo transfer tab. Including them here
    # caused them to appear alongside resources under "RESOURCES", making
    # them look like cargo and confusing the deploy workflow.
    return rows


def normalize_shipyard_item_ids(raw_parts: Any) -> List[str]:
    return catalog_service.normalize_shipyard_item_ids(raw_parts)


def shipyard_parts_from_item_ids(item_ids: List[str]) -> List[Dict[str, Any]]:
    return catalog_service.shipyard_parts_from_item_ids(item_ids, normalize_parts_fn=normalize_parts)


def build_ship_stats_payload(
    parts: List[Dict[str, Any]],
    current_fuel_kg: Optional[float] = None,
    cargo_mass_kg: float = 0.0,
    cargo_avg_density: float = 2500.0,
) -> Dict[str, float]:
    return catalog_service.build_ship_stats_payload(
        parts,
        resource_catalog=load_resource_catalog(),
        current_fuel_kg=current_fuel_kg,
        cargo_mass_kg=cargo_mass_kg,
        cargo_avg_density=cargo_avg_density,
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
        robonaut_catalog=load_robonaut_catalog(),
        constructor_catalog=load_constructor_catalog(),
        refinery_catalog=load_refinery_catalog(),
    )


def ensure_inventory_baseline_ship(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM ships WHERE id='artemis_iii'")

    starter_id = "shipyard_starter"
    starter_parts = [
        {
            "item_id": "ntr_m2_dumbo_folded_flow",
        },
        {
            "name": "Radiator",
            "type": "radiator",
            "mass_kg": 2000.0,
        },
        {
            "item_id": "water_tank_10_m3",
        },
    ]
    starter_stats = derive_ship_stats_from_parts(
        starter_parts,
        current_fuel_kg=10000.0,
    )
    row = conn.execute("SELECT * FROM ships WHERE id=?", (starter_id,)).fetchone()
    if not row:
        conn.execute(
            """
            INSERT INTO ships (
              id,name,shape,color,size_px,notes_json,
              location_id,from_location_id,to_location_id,departed_at,arrives_at,
              dv_planned_m_s,dock_slot,
              parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                starter_id,
                "Shipyard Starter",
                "triangle",
                "#ffffff",
                12,
                json.dumps(["Shipyard baseline hull"]),
                "LEO",
                None,
                None,
                None,
                None,
                None,
                None,
                json.dumps(starter_parts),
                starter_stats["fuel_kg"],
                starter_stats["fuel_capacity_kg"],
                starter_stats["dry_mass_kg"],
                starter_stats["isp_s"],
            ),
        )
    else:
        current_fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))
        resolved_fuel_kg = current_fuel_kg
        conn.execute(
            """
            UPDATE ships
            SET
              name='Shipyard Starter',
              notes_json=?,
              parts_json=?,
              fuel_kg=?,
              fuel_capacity_kg=?,
              dry_mass_kg=?,
              isp_s=?
            WHERE id=?
            """,
            (
                json.dumps(["Shipyard baseline hull"]),
                json.dumps(starter_parts),
                resolved_fuel_kg,
                starter_stats["fuel_capacity_kg"],
                starter_stats["dry_mass_kg"],
                starter_stats["isp_s"],
                starter_id,
            ),
        )
        if not row["location_id"] and not row["from_location_id"]:
            conn.execute("UPDATE ships SET location_id='LEO' WHERE id=?", (starter_id,))


def compute_delta_v_remaining_m_s(dry_mass_kg: float, fuel_kg: float, isp_s: float) -> float:
    return catalog_service.compute_delta_v_remaining_m_s(dry_mass_kg, fuel_kg, isp_s)


def compute_fuel_needed_for_delta_v_kg(dry_mass_kg: float, fuel_kg: float, isp_s: float, dv_m_s: float) -> float:
    return catalog_service.compute_fuel_needed_for_delta_v_kg(dry_mass_kg, fuel_kg, isp_s, dv_m_s)


def hash_edges(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT from_id,to_id,dv_m_s,tof_s,edge_type FROM transfer_edges ORDER BY from_id,to_id"
    ).fetchall()
    blob = json.dumps([dict(r) for r in rows], separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def dijkstra_all_pairs(conn: sqlite3.Connection) -> None:
    """
    Generate transfer_matrix from transfer_edges using DV as the weight.
    TOF is summed along the chosen DV-min path.
    """
    edges = conn.execute("SELECT from_id,to_id,dv_m_s,tof_s FROM transfer_edges").fetchall()
    locs = conn.execute("SELECT id,is_group FROM locations WHERE is_group=0").fetchall()
    node_ids = [r["id"] for r in locs]

    adj: Dict[str, List[Tuple[str, float, float]]] = {nid: [] for nid in node_ids}
    for e in edges:
        if e["from_id"] in adj and e["to_id"] in adj:
            adj[e["from_id"]].append((e["to_id"], float(e["dv_m_s"]), float(e["tof_s"])))

    import heapq

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

        # Build rows for all reachable dst
        for dst in node_ids:
            if dst == src:
                matrix_rows.append((src, dst, 0.0, 0.0, json.dumps([src])))
                continue
            if dst not in dist:
                continue
            # reconstruct path
            path = []
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
          transit_from_x = NULL,
          transit_from_y = NULL,
          transit_to_x = NULL,
          transit_to_y = NULL,
          trajectory_json = NULL
        WHERE arrives_at IS NOT NULL AND arrives_at <= ?
        """,
        (now_s,),
    )


SIM_CLOCK_META_REAL_ANCHOR = "sim_real_time_anchor_s"
SIM_CLOCK_META_GAME_ANCHOR = "sim_game_time_anchor_s"
SIM_CLOCK_META_PAUSED = "sim_paused"


def _persist_simulation_clock_state(conn: sqlite3.Connection) -> None:
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


def _load_simulation_clock_state(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT key,value FROM transfer_meta WHERE key IN (?,?,?)",
        (SIM_CLOCK_META_REAL_ANCHOR, SIM_CLOCK_META_GAME_ANCHOR, SIM_CLOCK_META_PAUSED),
    ).fetchall()
    by_key = {str(r["key"]): str(r["value"]) for r in rows}

    real_raw = by_key.get(SIM_CLOCK_META_REAL_ANCHOR)
    game_raw = by_key.get(SIM_CLOCK_META_GAME_ANCHOR)
    paused_raw = by_key.get(SIM_CLOCK_META_PAUSED)

    if real_raw is None or game_raw is None or paused_raw is None:
        _persist_simulation_clock_state(conn)
        return

    try:
        real_anchor_s = float(real_raw)
        game_anchor_s = float(game_raw)
        paused = str(paused_raw).strip().lower() in {"1", "true", "yes", "on"}
    except (TypeError, ValueError):
        _persist_simulation_clock_state(conn)
        return

    import_simulation_state(real_anchor_s, game_anchor_s, paused)


@app.on_event("startup")
def _startup():
    conn = connect_db()
    try:
        apply_migrations(conn)
        _load_simulation_clock_state(conn)
        ensure_default_admin_account(conn)
        seed_locations_and_edges_if_empty(conn)
        ensure_solar_system_expansion(conn)
        if _env_flag("EARTHMOON_PURGE_TEST_SHIPS_ON_STARTUP", default=False):
            purge_test_ships(conn)
        ensure_inventory_baseline_ship(conn)
        regenerate_matrix_if_needed(conn)
        conn.commit()
    finally:
        conn.close()

    load_thruster_main_catalog()
    load_resource_catalog()

# ── Server environment info (for UI banner) ─────────────────────────
_ENV_LABEL = os.environ.get("ENV_LABEL", "").strip().upper()


@app.get("/api/server/info")
def server_info():
    """Return non-secret server metadata so the UI can show an environment banner."""
    return {
        "env_label": _ENV_LABEL,  # "", "TEST", "DEV", etc.
    }


@app.get("/")
def root(request: Request):
    return _serve_authenticated_page(request, "index.html")


@app.get("/fleet")
def fleet(request: Request):
    return _serve_authenticated_page(request, "fleet.html")


@app.get("/research")
def research(request: Request):
    return _serve_authenticated_page(request, "research.html")


@app.get("/shipyard")
def shipyard(request: Request):
    return _serve_authenticated_page(request, "shipyard.html")


@app.get("/sites")
def sites(request: Request):
    return _serve_authenticated_page(request, "sites.html")


@app.get("/organization")
def organization(request: Request):
    return _serve_authenticated_page(request, "organization.html")


@app.get("/contracts")
def contracts(request: Request):
    return _serve_authenticated_page(request, "contracts.html")


@app.get("/missions")
def missions(request: Request):
    return _serve_authenticated_page(request, "missions.html")


@app.get("/contracts/create")
def contracts_create(request: Request):
    return _serve_authenticated_page(request, "contract_create.html")


@app.get("/profile")
def profile(request: Request):
    """Legacy redirect: /profile → /organization"""
    return RedirectResponse(url="/organization", status_code=302)


@app.get("/login")
def login_page(request: Request):
    conn = connect_db()
    try:
        if get_current_user(conn, request):
            return RedirectResponse(url="/", status_code=302)
    finally:
        conn.close()
    return _html_no_cache(str(APP_DIR / "static" / "login.html"))


@app.get("/admin")
def admin(request: Request):
    conn = connect_db()
    try:
        user = get_current_user(conn, request)
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        if not int(user["is_admin"]):
            return RedirectResponse(url="/", status_code=302)
    finally:
        conn.close()
    return _html_no_cache(str(APP_DIR / "static" / "admin.html"))

