import hashlib
import json
import math
import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from auth_router import router as auth_router
from auth_service import ensure_default_admin_account, get_current_user, require_admin, require_login
import catalog_service
import celestial_config
from db import APP_DIR, connect_db
from db_migrations import apply_migrations
from sim_service import (
    effective_time_scale,
    export_simulation_state,
    game_now_s,
    import_simulation_state,
    reset_simulation_clock,
    set_simulation_paused,
    simulation_paused,
)

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
app.include_router(auth_router)


@lru_cache(maxsize=1)
def _location_metadata_by_id() -> Dict[str, Dict[str, Any]]:
    try:
        return celestial_config.load_location_metadata()
    except celestial_config.CelestialConfigError as exc:
        print(f"[celestial-config] metadata load failed: {exc}")
        return {}


ITEM_CATEGORIES: List[Dict[str, Any]] = [
    {
        "id": "thruster",
        "name": "Thruster",
        "kind": "ship_component",
        "description": "Produces thrust to drive ships.",
    },
    {
        "id": "reactor",
        "name": "Reactor",
        "kind": "ship_component",
        "description": "Uses fission or fusion reactions to produce energy.",
    },
    {
        "id": "generator",
        "name": "Generator",
        "kind": "ship_component",
        "description": "Converts reactor or other energy sources into electricity.",
    },
    {
        "id": "robonaut",
        "name": "Robonaut",
        "kind": "ship_component",
        "description": "Robotic apparatus for mining, scouting, and construction in space.",
    },
    {
        "id": "refinery",
        "name": "Refinery",
        "kind": "ship_component",
        "description": "Processes raw materials into finished materials.",
    },
    {
        "id": "radiator",
        "name": "Radiator",
        "kind": "ship_component",
        "description": "Radiates heat and waste energy from ship systems.",
    },
    {
        "id": "storage",
        "name": "Storage",
        "kind": "ship_component",
        "description": "Wet or dry storage, typically rated by storable volume in m3.",
    },
    {
        "id": "fuel",
        "name": "Fuel",
        "kind": "material",
        "description": "Consumable propellant mass such as water or other fuel sources.",
    },
    {
        "id": "raw_material",
        "name": "Raw Material",
        "kind": "material",
        "description": "Unprocessed resources such as iron oxide or silica powder.",
    },
    {
        "id": "finished_material",
        "name": "Finished Material",
        "kind": "material",
        "description": "Processed resources ready for manufacturing and construction.",
    },
    {
        "id": "generic",
        "name": "Generic",
        "kind": "unknown",
        "description": "Fallback category for uncategorized parts.",
    },
]

ITEM_CATEGORY_BY_ID: Dict[str, Dict[str, Any]] = {c["id"]: c for c in ITEM_CATEGORIES}

ITEM_CATEGORY_ALIASES: Dict[str, str] = {
    "thrusters": "thruster",
    "engine": "thruster",
    "engines": "thruster",
    "reactors": "reactor",
    "fission": "reactor",
    "fusion": "reactor",
    "generators": "generator",
    "power_generator": "generator",
    "power": "generator",
    "robot": "robonaut",
    "robots": "robonaut",
    "drone": "robonaut",
    "drones": "robonaut",
    "robonauts": "robonaut",
    "refineries": "refinery",
    "radiators": "radiator",
    "cooler": "radiator",
    "cooling": "radiator",
    "tank": "storage",
    "tanks": "storage",
    "cargo": "storage",
    "wet_storage": "storage",
    "dry_storage": "storage",
    "fuels": "fuel",
    "propellant": "fuel",
    "propellants": "fuel",
    "raw": "raw_material",
    "raw_materials": "raw_material",
    "ore": "raw_material",
    "ores": "raw_material",
    "feedstock": "raw_material",
    "finished": "finished_material",
    "finished_materials": "finished_material",
    "alloy": "finished_material",
    "alloys": "finished_material",
    "metal": "finished_material",
    "metals": "finished_material",
}

RESEARCH_CATEGORIES: List[Dict[str, str]] = [
    {"id": "thrusters", "label": "Thrusters"},
    {"id": "reactors", "label": "Reactors"},
    {"id": "generators", "label": "Generators"},
    {"id": "robonauts", "label": "Robonauts"},
    {"id": "refineries", "label": "Refineries"},
    {"id": "radiators", "label": "Radiators"},
]

THRUSTER_RESERVED_LANES: List[Dict[str, str]] = [
    {"id": "cryo", "label": "Cryo"},
    {"id": "solar", "label": "Solar"},
    {"id": "pulse", "label": "Pulse"},
]

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

    computed_edges: List[Tuple[str, str, float, float]] = []
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
            computed_edges.append((from_id, to_id, round(dv_m_s, 2), round(tof_s, 1)))

    mars_mu = planetary["mars"]["mu"]
    r_lmo = planetary["mars"]["radius_km"] + 250.0
    r_phobos = 9376.0
    r_deimos = 23463.2

    lmo_phobos_dv, lmo_phobos_tof = _hohmann_orbit_change_dv_tof(mars_mu, r_lmo, r_phobos)
    lmo_deimos_dv, lmo_deimos_tof = _hohmann_orbit_change_dv_tof(mars_mu, r_lmo, r_deimos)
    phobos_deimos_dv, phobos_deimos_tof = _hohmann_orbit_change_dv_tof(mars_mu, r_phobos, r_deimos)

    computed_edges.extend(
        [
            ("LMO", "PHOBOS", round(lmo_phobos_dv, 2), round(lmo_phobos_tof, 1)),
            ("PHOBOS", "LMO", round(lmo_phobos_dv, 2), round(lmo_phobos_tof, 1)),
            ("LMO", "DEIMOS", round(lmo_deimos_dv, 2), round(lmo_deimos_tof, 1)),
            ("DEIMOS", "LMO", round(lmo_deimos_dv, 2), round(lmo_deimos_tof, 1)),
            ("PHOBOS", "DEIMOS", round(phobos_deimos_dv, 2), round(phobos_deimos_tof, 1)),
            ("DEIMOS", "PHOBOS", round(phobos_deimos_dv, 2), round(phobos_deimos_tof, 1)),
        ]
    )

    # Approximate heliocentric transfer between Earth LEO and near-Sun orbit marker.
    computed_edges.extend(
        [
            ("LEO", "SUN", 28000.0, 130.0 * 24.0 * 3600.0),
            ("SUN", "LEO", 28000.0, 130.0 * 24.0 * 3600.0),
            ("MERC_ORB", "SUN", 12000.0, 55.0 * 24.0 * 3600.0),
            ("SUN", "MERC_ORB", 12000.0, 55.0 * 24.0 * 3600.0),
            ("VEN_ORB", "SUN", 19000.0, 90.0 * 24.0 * 3600.0),
            ("SUN", "VEN_ORB", 19000.0, 90.0 * 24.0 * 3600.0),
            ("LMO", "SUN", 22000.0, 180.0 * 24.0 * 3600.0),
            ("SUN", "LMO", 22000.0, 180.0 * 24.0 * 3600.0),
        ]
    )

    upsert_transfer_edges(conn, computed_edges)


def purge_test_ships(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM ships
        WHERE id LIKE 'test_%' OR lower(name) LIKE 'test[%'
        """
    )


def normalize_parts(raw_parts: Any) -> List[Dict[str, Any]]:
    return catalog_service.normalize_parts(
        raw_parts,
        thruster_catalog=load_thruster_main_catalog(),
        storage_catalog=load_storage_catalog(),
        canonical_item_category=canonical_item_category,
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


def compute_ship_inventory_containers(parts: List[Dict[str, Any]], current_fuel_kg: float) -> List[Dict[str, Any]]:
    resources = load_resource_catalog()
    rows: List[Dict[str, Any]] = []

    water_rows: List[Dict[str, Any]] = []
    total_water_capacity_kg = 0.0

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
        is_implicit = True
        if explicit_m3 > 0.0:
            used_m3 = min(capacity_m3, explicit_m3) if capacity_m3 > 0.0 else explicit_m3
            cargo_mass_kg = used_m3 * density if density > 0.0 else explicit_mass_kg
            is_implicit = False
        elif explicit_mass_kg > 0.0 and density > 0.0:
            cargo_mass_kg = explicit_mass_kg
            used_m3 = min(capacity_m3, cargo_mass_kg / density) if capacity_m3 > 0.0 else cargo_mass_kg / density
            is_implicit = False

        dry_mass_kg = max(0.0, float(part.get("mass_kg") or 0.0))

        tank_phase = str(part.get("tank_phase") or "").strip().lower()
        if tank_phase not in {"solid", "liquid", "gas"}:
            tank_phase = classify_resource_phase(resource_id, resource_name, density)
        resource_phase = classify_resource_phase(resource_id, resource_name, density)

        row = {
            "container_index": idx,
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
        if is_implicit and resource_id.lower() == "water" and density > 0.0 and capacity_m3 > 0.0:
            water_rows.append(row)
            total_water_capacity_kg += capacity_m3 * density

    if water_rows and total_water_capacity_kg > 0.0:
        fuel = max(0.0, float(current_fuel_kg or 0.0))
        ratio = min(1.0, fuel / total_water_capacity_kg)
        for row in water_rows:
            used = row["capacity_m3"] * ratio
            cargo_mass = used * row["density_kg_m3"]
            row["used_m3"] = used
            row["cargo_mass_kg"] = cargo_mass
            row["total_mass_kg"] = row["dry_mass_kg"] + cargo_mass

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

    parts = normalize_parts(json.loads(row["parts_json"] or "[]"))
    fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))
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


def _apply_ship_container_fill(
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


def _persist_ship_inventory_state(
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


def _resource_stack_row(conn: sqlite3.Connection, location_id: str, stack_key: str) -> sqlite3.Row:
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


def _consume_location_resource_mass(conn: sqlite3.Connection, row: sqlite3.Row, mass_kg: float) -> float:
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


def _inventory_items_for_ship(ship_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = list(ship_state.get("resources") or [])
    rows.sort(key=lambda r: (str(r.get("phase") or ""), str(r.get("label") or r.get("item_id") or "")))
    return rows


def _inventory_items_for_location(location_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
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
                "resource_id": "",
                "mass_kg": max(0.0, float(part.get("mass_kg") or 0.0)),
                "volume_m3": max(0.0, float(part.get("volume_m3") or 0.0)),
                "quantity": qty,
                "icon_seed": f"part::{part.get('item_id') or stack_key}",
                "transfer": None,
            }
        )
    return rows


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
              transfer_path_json,dv_planned_m_s,dock_slot,
              parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                "[]",
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
        fuel_capacity_kg = starter_stats["fuel_capacity_kg"]
        resolved_fuel_kg = min(current_fuel_kg, fuel_capacity_kg)
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
                fuel_capacity_kg,
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
        "SELECT from_id,to_id,dv_m_s,tof_s FROM transfer_edges ORDER BY from_id,to_id"
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
          transfer_path_json = '[]'
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
        purge_test_ships(conn)
        ensure_inventory_baseline_ship(conn)
        regenerate_matrix_if_needed(conn)
        conn.commit()
    finally:
        conn.close()

    load_thruster_main_catalog()
    load_resource_catalog()
    load_storage_catalog()


@app.get("/")
def root(request: Request):
    conn = connect_db()
    try:
        if not get_current_user(conn, request):
            return RedirectResponse(url="/login", status_code=302)
    finally:
        conn.close()
    return FileResponse(str(APP_DIR / "static" / "index.html"))


@app.get("/fleet")
def fleet(request: Request):
    conn = connect_db()
    try:
        if not get_current_user(conn, request):
            return RedirectResponse(url="/login", status_code=302)
    finally:
        conn.close()
    return FileResponse(str(APP_DIR / "static" / "fleet.html"))


@app.get("/research")
def research(request: Request):
    conn = connect_db()
    try:
        if not get_current_user(conn, request):
            return RedirectResponse(url="/login", status_code=302)
    finally:
        conn.close()
    return FileResponse(str(APP_DIR / "static" / "research.html"))


@app.get("/shipyard")
def shipyard(request: Request):
    conn = connect_db()
    try:
        if not get_current_user(conn, request):
            return RedirectResponse(url="/login", status_code=302)
    finally:
        conn.close()
    return FileResponse(str(APP_DIR / "static" / "shipyard.html"))


@app.get("/profile")
def profile(request: Request):
    conn = connect_db()
    try:
        if not get_current_user(conn, request):
            return RedirectResponse(url="/login", status_code=302)
    finally:
        conn.close()
    if request.query_params.get("embed") != "1":
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(str(APP_DIR / "static" / "profile.html"))


@app.get("/login")
def login_page(request: Request):
    conn = connect_db()
    try:
        if get_current_user(conn, request):
            return RedirectResponse(url="/", status_code=302)
    finally:
        conn.close()
    return FileResponse(str(APP_DIR / "static" / "login.html"))


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
    return FileResponse(str(APP_DIR / "static" / "admin.html"))


@app.get("/api/locations")
def api_locations(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_login(conn, request)
        rows = conn.execute(
            "SELECT id,name,parent_id,is_group,sort_order,x,y FROM locations ORDER BY sort_order, name"
        ).fetchall()
        metadata_by_id = _location_metadata_by_id()
        locations = []
        for row in rows:
            item = dict(row)
            extra = metadata_by_id.get(str(item.get("id") or ""), {})
            if extra:
                item.update(extra)
            locations.append(item)
        return {"locations": locations}
    finally:
        conn.close()


def build_tree(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    nodes: Dict[str, Dict[str, Any]] = {}
    children_by_parent: Dict[Optional[str], List[str]] = {}

    for r in rows:
        nodes[r["id"]] = {
            "id": r["id"],
            "name": r["name"],
            "is_group": bool(r["is_group"]),
            "sort_order": int(r["sort_order"]),
            "children": [],
        }
        children_by_parent.setdefault(r["parent_id"], []).append(r["id"])

    def sort_key(nid: str) -> Tuple[int, str]:
        n = nodes[nid]
        # groups first, then sort_order, then name
        return (0 if n["is_group"] else 1, n["sort_order"], n["name"].lower())

    def attach(parent_id: Optional[str]) -> List[Dict[str, Any]]:
        kids = children_by_parent.get(parent_id, [])
        kids.sort(key=sort_key)
        out = []
        for kid in kids:
            n = nodes[kid]
            n["children"] = attach(kid)
            out.append(n)
        return out

    return attach(None)


@app.get("/api/locations/tree")
def api_locations_tree(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_login(conn, request)
        rows = conn.execute(
            "SELECT id,name,parent_id,is_group,sort_order FROM locations"
        ).fetchall()
        return {"tree": build_tree(rows)}
    finally:
        conn.close()


@app.get("/api/inventory/location/{location_id}")
def api_location_inventory(location_id: str, request: Request) -> Dict[str, Any]:
    loc_id = (location_id or "").strip()
    if not loc_id:
        raise HTTPException(status_code=400, detail="location_id is required")

    conn = connect_db()
    try:
        require_login(conn, request)
        loc = conn.execute("SELECT id,is_group,name FROM locations WHERE id=?", (loc_id,)).fetchone()
        if not loc:
            raise HTTPException(status_code=404, detail="Location not found")
        if int(loc["is_group"]):
            raise HTTPException(status_code=400, detail="location_id must be a non-group location")

        payload = get_location_inventory_payload(conn, loc_id)
        payload["location_name"] = str(loc["name"])
        return payload
    finally:
        conn.close()


@app.get("/api/inventory/ship/{ship_id}")
def api_ship_inventory(ship_id: str, request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_login(conn, request)
        settle_arrivals(conn, game_now_s())
        state = _load_ship_inventory_state(conn, ship_id)
        row = state["row"]
        return {
            "ship_id": str(row["id"]),
            "ship_name": str(row["name"]),
            "location_id": state["location_id"],
            "is_docked": bool(state["is_docked"]),
            "items": _inventory_items_for_ship(state),
            "capacity_summary": state["capacity_summary"],
            "containers": state["containers"],
        }
    finally:
        conn.close()


@app.get("/api/inventory/context/{kind}/{entity_id}")
def api_inventory_context(kind: str, entity_id: str, request: Request) -> Dict[str, Any]:
    inventory_kind = str(kind or "").strip().lower()
    inv_id = str(entity_id or "").strip()
    if inventory_kind not in {"ship", "location"}:
        raise HTTPException(status_code=400, detail="kind must be 'ship' or 'location'")
    if not inv_id:
        raise HTTPException(status_code=400, detail="entity_id is required")

    conn = connect_db()
    try:
        require_login(conn, request)
        settle_arrivals(conn, game_now_s())
        conn.commit()

        location_id = ""
        location_name = ""
        anchor_name = inv_id

        if inventory_kind == "ship":
            ship_state = _load_ship_inventory_state(conn, inv_id)
            location_id = ship_state["location_id"] if ship_state["is_docked"] else ""
            anchor_name = str(ship_state["row"]["name"])
            if location_id:
                loc_row = _get_location_row(conn, location_id)
                location_name = str(loc_row["name"])
        else:
            loc_row = _get_location_row(conn, inv_id)
            location_id = str(loc_row["id"])
            location_name = str(loc_row["name"])
            anchor_name = location_name

        inventories: List[Dict[str, Any]] = []
        if location_id:
            location_payload = get_location_inventory_payload(conn, location_id)
            inventories.append(
                {
                    "inventory_kind": "location",
                    "id": location_id,
                    "name": f"{location_name} Site Inventory",
                    "location_id": location_id,
                    "capacity_summary": None,
                    "items": _inventory_items_for_location(location_payload),
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
                ship_state = _load_ship_inventory_state(conn, str(ship_row["id"]))
                inventories.append(
                    {
                        "inventory_kind": "ship",
                        "id": str(ship_row["id"]),
                        "name": str(ship_row["name"]),
                        "location_id": location_id,
                        "capacity_summary": ship_state.get("capacity_summary"),
                        "items": _inventory_items_for_ship(ship_state),
                    }
                )
        elif inventory_kind == "ship":
            ship_state = _load_ship_inventory_state(conn, inv_id)
            inventories.append(
                {
                    "inventory_kind": "ship",
                    "id": str(ship_state["row"]["id"]),
                    "name": str(ship_state["row"]["name"]),
                    "location_id": "",
                    "capacity_summary": ship_state.get("capacity_summary"),
                    "items": _inventory_items_for_ship(ship_state),
                }
            )

        return {
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
    finally:
        conn.close()


@app.post("/api/inventory/transfer")
def api_inventory_transfer(req: "InventoryTransferReq", request: Request) -> Dict[str, Any]:
    source_kind = str(req.source_kind or "").strip().lower()
    source_id = str(req.source_id or "").strip()
    source_key = str(req.source_key or "").strip()
    target_kind = str(req.target_kind or "").strip().lower()
    target_id = str(req.target_id or "").strip()

    if source_kind not in {"ship_container", "ship_resource", "location_resource"}:
        raise HTTPException(status_code=400, detail="source_kind must be ship_container, ship_resource, or location_resource")
    if not source_id or not source_key:
        raise HTTPException(status_code=400, detail="source_id and source_key are required")
    if not target_id:
        raise HTTPException(status_code=400, detail="target_id is required")

    conn = connect_db()
    try:
        require_login(conn, request)
        settle_arrivals(conn, game_now_s())

        resources = load_resource_catalog()

        target_location_id = ""
        target_ship_state: Optional[Dict[str, Any]] = None
        if target_kind == "location":
            loc = _get_location_row(conn, target_id)
            target_location_id = str(loc["id"])
        else:
            target_ship_state = _load_ship_inventory_state(conn, target_id)
            if not target_ship_state["is_docked"]:
                raise HTTPException(status_code=400, detail="Target ship must be docked")
            target_location_id = str(target_ship_state["location_id"])

        source_location_id = ""
        move_resource_id = ""
        move_mass_kg = max(0.0, float(req.amount or 0.0))
        source_ship_state: Optional[Dict[str, Any]] = None
        source_resource_row: Optional[sqlite3.Row] = None

        if source_kind in {"ship_container", "ship_resource"}:
            source_ship_state = _load_ship_inventory_state(conn, source_id)
            if not source_ship_state["is_docked"]:
                raise HTTPException(status_code=400, detail="Source ship must be docked")
            source_location_id = str(source_ship_state["location_id"])

            if source_kind == "ship_container":
                try:
                    src_idx = int(source_key)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="source_key must be a ship container index") from exc

                src_container = next(
                    (c for c in source_ship_state["containers"] if int(c.get("container_index") or -1) == src_idx),
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
            _get_location_row(conn, source_location_id)
            source_resource_row = _resource_stack_row(conn, source_location_id, source_key)
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

        accepted_mass_kg = move_mass_kg
        density = max(0.0, float((resources.get(move_resource_id) or {}).get("mass_per_m3_kg") or 0.0))

        if target_kind == "location":
            add_resource_to_location_inventory(conn, target_location_id, move_resource_id, accepted_mass_kg)
        else:
            if not target_ship_state:
                raise HTTPException(status_code=500, detail="Target ship state unavailable")

            target_parts = list(target_ship_state["parts"])
            target_containers = list(target_ship_state["containers"])

            resource_meta = resources.get(move_resource_id) or {}
            resource_phase = classify_resource_phase(
                move_resource_id,
                str(resource_meta.get("name") or move_resource_id),
                float(resource_meta.get("mass_per_m3_kg") or density or 0.0),
            )

            compatible: List[Tuple[Dict[str, Any], float, float]] = []
            total_free_mass_kg = 0.0
            for container in target_containers:
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
                target_parts[idx] = _apply_ship_container_fill(
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

            _persist_ship_inventory_state(
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

                src_parts[src_idx] = _apply_ship_container_fill(
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

                    src_parts[src_idx] = _apply_ship_container_fill(
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

            _persist_ship_inventory_state(
                conn,
                ship_id=str(source_ship_state["row"]["id"]),
                parts=src_parts,
                fuel_kg=source_fuel_kg,
            )
        else:
            if not source_resource_row:
                raise HTTPException(status_code=500, detail="Source resource stack unavailable")
            _consume_location_resource_mass(conn, source_resource_row, accepted_mass_kg)

        conn.commit()
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
    finally:
        conn.close()


@app.get("/api/transfer_quote")
def api_transfer_quote(from_id: str, to_id: str, request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_login(conn, request)
        row = conn.execute(
            "SELECT dv_m_s,tof_s,path_json FROM transfer_matrix WHERE from_id=? AND to_id=?",
            (from_id, to_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No transfer data for that pair")
        return {
            "from_id": from_id,
            "to_id": to_id,
            "dv_m_s": float(row["dv_m_s"]),
            "tof_s": float(row["tof_s"]),
            "path": json.loads(row["path_json"] or "[]"),
        }
    finally:
        conn.close()


@app.get("/api/catalog/items")
def api_catalog_items(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_login(conn, request)
    finally:
        conn.close()
    return {
        "item_categories": catalog_service.ITEM_CATEGORIES,
    }


@app.get("/api/health")
def api_health() -> Dict[str, Any]:
    conn = connect_db()
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()
    return {
        "ok": True,
        "service": "earthmoon-db",
    }


@app.get("/api/shipyard/catalog")
def api_shipyard_catalog(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_login(conn, request)
        loc_rows = conn.execute(
            "SELECT id,name FROM locations WHERE is_group=0 ORDER BY sort_order, name"
        ).fetchall()
        summary_rows = conn.execute(
            """
            SELECT location_id,
                   SUM(CASE WHEN stack_type='part' THEN quantity ELSE 0 END) AS part_qty,
                   SUM(CASE WHEN stack_type='resource' THEN mass_kg ELSE 0 END) AS resource_mass_kg
            FROM location_inventory_stacks
            GROUP BY location_id
            """
        ).fetchall()
        inv_summary = {
            str(r["location_id"]): {
                "part_qty": float(r["part_qty"] or 0.0),
                "resource_mass_kg": float(r["resource_mass_kg"] or 0.0),
            }
            for r in summary_rows
        }
    finally:
        conn.close()

    payload = build_shipyard_catalog_payload()
    payload["build_source_locations"] = [
        {
            "id": str(loc["id"]),
            "name": str(loc["name"]),
            "inventory_part_qty": inv_summary.get(str(loc["id"]), {}).get("part_qty", 0.0),
            "inventory_resource_mass_kg": inv_summary.get(str(loc["id"]), {}).get("resource_mass_kg", 0.0),
        }
        for loc in loc_rows
        if str(loc["id"]) == "LEO"
        or inv_summary.get(str(loc["id"]), {}).get("part_qty", 0.0) > 0.0
        or inv_summary.get(str(loc["id"]), {}).get("resource_mass_kg", 0.0) > 0.0
    ]
    return payload


@app.get("/api/catalog/recipes")
def api_catalog_recipes(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_login(conn, request)
    finally:
        conn.close()
    return {
        "recipes": sorted(load_recipe_catalog().values(), key=lambda r: str(r.get("name") or "").lower()),
    }


@app.get("/api/catalog/recipes/by-category")
def api_catalog_recipes_by_category(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_login(conn, request)
    finally:
        conn.close()
    return catalog_service.build_recipe_categories_payload(load_recipe_catalog())


class ShipyardPreviewReq(BaseModel):
    parts: List[Any] = Field(default_factory=list)
    source_location_id: Optional[str] = None


@app.post("/api/shipyard/preview")
def api_shipyard_preview(req: ShipyardPreviewReq, request: Request) -> Dict[str, Any]:
    source_location_id = (req.source_location_id or "").strip() or "LEO"

    conn = connect_db()
    try:
        require_login(conn, request)
        loc = conn.execute("SELECT id,is_group FROM locations WHERE id=?", (source_location_id,)).fetchone()
        if not loc or int(loc["is_group"]):
            raise HTTPException(status_code=400, detail="source_location_id must be a valid non-group location")
    finally:
        conn.close()

    item_ids = normalize_shipyard_item_ids(req.parts)
    parts = shipyard_parts_from_item_ids(item_ids)
    stats = build_ship_stats_payload(parts)
    return {
        "build_location_id": source_location_id,
        "parts": parts,
        "stats": stats,
    }


@app.get("/api/research/tree")
def api_research_tree(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_login(conn, request)
    finally:
        conn.close()
    return build_research_payload()


@app.get("/api/time")
def api_time(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_login(conn, request)
    finally:
        conn.close()
    return {
        "server_time": game_now_s(),
        "time_scale": effective_time_scale(),
        "paused": simulation_paused(),
    }


@app.get("/api/state")
def api_state(request: Request) -> Dict[str, Any]:
    now_s = game_now_s()
    conn = connect_db()
    try:
        user = require_login(conn, request)
        settle_arrivals(conn, now_s)
        conn.commit()

        rows = conn.execute(
            """
            SELECT id,name,shape,color,size_px,notes_json,
                   location_id,from_location_id,to_location_id,departed_at,arrives_at,
                     transfer_path_json,dv_planned_m_s,dock_slot,
                     parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s
            FROM ships
            ORDER BY id
            """
        ).fetchall()

        ships = []
        for r in rows:
            parts = normalize_parts(json.loads(r["parts_json"] or "[]"))
            stats = derive_ship_stats_from_parts(
                parts,
                current_fuel_kg=float(r["fuel_kg"] or 0.0),
            )
            inventory_containers = compute_ship_inventory_containers(parts, stats["fuel_kg"])
            inventory_items = compute_ship_inventory_resources(str(r["id"]), inventory_containers)
            inventory_capacity_summary = compute_ship_capacity_summary(inventory_containers)
            ships.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "shape": r["shape"],
                    "color": r["color"],
                    "size_px": r["size_px"],
                    "notes": json.loads(r["notes_json"] or "[]"),
                    "location_id": r["location_id"],
                    "from_location_id": r["from_location_id"],
                    "to_location_id": r["to_location_id"],
                    "departed_at": r["departed_at"],
                    "arrives_at": r["arrives_at"],
                    "transfer_path": json.loads(r["transfer_path_json"] or "[]"),
                    "dv_planned_m_s": r["dv_planned_m_s"],
                    "dock_slot": r["dock_slot"],
                    "parts": parts,
                    "inventory_containers": inventory_containers,
                    "inventory_items": inventory_items,
                    "inventory_capacity_summary": inventory_capacity_summary,
                    "fuel_kg": stats["fuel_kg"],
                    "fuel_capacity_kg": stats["fuel_capacity_kg"],
                    "dry_mass_kg": stats["dry_mass_kg"],
                    "isp_s": stats["isp_s"],
                    "thrust_kn": stats["thrust_kn"],
                    "delta_v_remaining_m_s": compute_delta_v_remaining_m_s(
                        stats["dry_mass_kg"],
                        stats["fuel_kg"],
                        stats["isp_s"],
                    ),
                    "status": "transit" if r["arrives_at"] else "docked",
                }
            )

        return {
            "user": {
                "username": user["username"],
                "is_admin": bool(user["is_admin"]),
            },
            "server_time": now_s,
            "time_scale": effective_time_scale(),
            "paused": simulation_paused(),
            "ships": ships,
        }
    finally:
        conn.close()


class TransferReq(BaseModel):
    to_location_id: str


class InventoryContainerReq(BaseModel):
    container_index: int


class InventoryTransferReq(BaseModel):
    source_kind: Literal["ship_container", "ship_resource", "location_resource"]
    source_id: str
    source_key: str
    target_kind: Literal["ship", "location"]
    target_id: str
    amount: Optional[float] = None


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


class ShipyardBuildReq(BaseModel):
    name: str
    ship_id: Optional[str] = None
    notes: List[str] = Field(default_factory=list)
    parts: List[Any] = Field(default_factory=list)
    source_location_id: Optional[str] = None


class ShipDeconstructReq(BaseModel):
    keep_ship_record: bool = False


@app.post("/api/admin/simulation/toggle_pause")
def api_admin_toggle_pause(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    try:
        require_admin(conn, request)
        next_paused = not simulation_paused()
        set_simulation_paused(next_paused)
        _persist_simulation_clock_state(conn)
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "paused": simulation_paused(),
        "server_time": game_now_s(),
        "time_scale": effective_time_scale(),
    }


@app.post("/api/admin/reset_game")
def api_admin_reset_game(request: Request) -> Dict[str, Any]:
    conn = connect_db()
    deleted_ships = 0
    deleted_accounts = 0
    deleted_inventory_stacks = 0
    try:
        require_admin(conn, request)
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
        _persist_simulation_clock_state(conn)
        conn.commit()
    finally:
        conn.close()

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


@app.post("/api/shipyard/build")
def api_shipyard_build(req: ShipyardBuildReq, request: Request) -> Dict[str, Any]:
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    source_location_id = (req.source_location_id or "").strip() or "LEO"

    item_ids = normalize_shipyard_item_ids(req.parts)
    if not item_ids:
        raise HTTPException(status_code=400, detail="At least one part is required")

    conn = connect_db()
    try:
        require_login(conn, request)

        loc = conn.execute(
            "SELECT id,is_group FROM locations WHERE id=?",
            (source_location_id,),
        ).fetchone()
        if not loc or int(loc["is_group"]):
            raise HTTPException(status_code=400, detail="source_location_id must be a valid non-group location")

        using_inventory_source = source_location_id != "LEO"
        if using_inventory_source:
            parts = consume_parts_from_location_inventory(conn, source_location_id, item_ids)
        else:
            parts = shipyard_parts_from_item_ids(item_ids)

        if not parts:
            raise HTTPException(status_code=400, detail="No valid parts found for build")

        stats = build_ship_stats_payload(parts)

        preferred_id = (req.ship_id or name).strip()
        ship_id = _next_available_ship_id(conn, preferred_id)
        notes = [str(n) for n in (req.notes or []) if str(n).strip()]

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
                "triangle",
                "#ffffff",
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
                **stats,
                "status": "docked",
            },
        }
    finally:
        conn.close()


@app.post("/api/admin/spawn_ship")
def api_admin_spawn_ship(req: SpawnShipReq, request: Request) -> Dict[str, Any]:
    name = (req.name or "").strip()
    location_id = (req.location_id or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not location_id:
        raise HTTPException(status_code=400, detail="location_id is required")

    conn = connect_db()
    try:
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
        parts = normalize_parts(req.parts or [])
        if not parts:
            parts = normalize_parts([
                {"item_id": "ntr_m1_nerva_solid_core"},
                {"name": "Radiator", "type": "radiator", "mass_kg": 600.0},
                {"item_id": "water_tank_10_m3"},
            ])

        stats = derive_ship_stats_from_parts(
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
                "delta_v_remaining_m_s": compute_delta_v_remaining_m_s(dry_mass_kg, fuel_kg, isp_s),
                "status": "docked",
            },
        }
    finally:
        conn.close()


@app.delete("/api/admin/ships/{ship_id}")
def api_admin_delete_ship(ship_id: str, request: Request) -> Dict[str, Any]:
    sid = (ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    conn = connect_db()
    try:
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
    finally:
        conn.close()


@app.post("/api/admin/ships/{ship_id}/refuel")
def api_admin_refuel_ship(ship_id: str, request: Request) -> Dict[str, Any]:
    sid = (ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    conn = connect_db()
    try:
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

        parts = normalize_parts(json.loads(row["parts_json"] or "[]"))
        stats = derive_ship_stats_from_parts(
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
                "delta_v_remaining_m_s": compute_delta_v_remaining_m_s(
                    stats["dry_mass_kg"],
                    stats["fuel_capacity_kg"],
                    stats["isp_s"],
                ),
            },
        }
    finally:
        conn.close()


@app.post("/api/ships/{ship_id}/transfer")
def api_ship_transfer(ship_id: str, req: TransferReq, request: Request) -> Dict[str, Any]:
    now_s = game_now_s()
    to_id = req.to_location_id

    conn = connect_db()
    try:
        require_login(conn, request)
        settle_arrivals(conn, now_s)

        ship = conn.execute(
            """
                        SELECT
                            id,location_id,from_location_id,to_location_id,arrives_at,
                            parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s
            FROM ships WHERE id=?
            """,
            (ship_id,),
        ).fetchone()
        if not ship:
            raise HTTPException(status_code=404, detail="Ship not found")

        # Must be docked to issue a transfer
        if ship["arrives_at"] is not None:
            raise HTTPException(status_code=400, detail="Ship is already in transit")

        from_id = ship["location_id"]
        if not from_id:
            raise HTTPException(status_code=400, detail="Ship has no current location_id")

        # Lookup transfer matrix entry
        row = conn.execute(
            "SELECT dv_m_s,tof_s,path_json FROM transfer_matrix WHERE from_id=? AND to_id=?",
            (from_id, to_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No transfer data for that destination")

        dv = float(row["dv_m_s"])
        tof = float(row["tof_s"])
        path_json = row["path_json"] or "[]"

        parts = normalize_parts(json.loads(ship["parts_json"] or "[]"))
        stats = derive_ship_stats_from_parts(
            parts,
            current_fuel_kg=float(ship["fuel_kg"] or 0.0),
        )

        delta_v_remaining = compute_delta_v_remaining_m_s(
            stats["dry_mass_kg"],
            stats["fuel_kg"],
            stats["isp_s"],
        )
        if dv > delta_v_remaining + 1e-6:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient fuel for transfer (need {int(round(dv))} m/s, have {int(round(delta_v_remaining))} m/s)",
            )

        fuel_used_kg = compute_fuel_needed_for_delta_v_kg(
            stats["dry_mass_kg"],
            stats["fuel_kg"],
            stats["isp_s"],
            dv,
        )
        fuel_remaining_kg = max(0.0, stats["fuel_kg"] - fuel_used_kg)

        dep = now_s
        arr = now_s + max(1.0, tof)

        conn.execute(
            """
            UPDATE ships
            SET
              location_id=NULL,
              from_location_id=?,
              to_location_id=?,
              departed_at=?,
              arrives_at=?,
              transfer_path_json=?,
                            dv_planned_m_s=?,
                            fuel_kg=?
            WHERE id=?
            """,
                        (from_id, to_id, dep, arr, path_json, dv, fuel_remaining_kg, ship_id),
        )
        conn.commit()

        return {
            "ok": True,
            "ship_id": ship_id,
            "from": from_id,
            "to": to_id,
            "dv_m_s": dv,
            "tof_s": tof,
            "fuel_used_kg": fuel_used_kg,
            "fuel_remaining_kg": fuel_remaining_kg,
            "departed_at": dep,
            "arrives_at": arr,
        }
    finally:
        conn.close()


@app.post("/api/ships/{ship_id}/inventory/jettison")
def api_ship_inventory_jettison(ship_id: str, req: InventoryContainerReq, request: Request) -> Dict[str, Any]:
    sid = (ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    conn = connect_db()
    try:
        require_login(conn, request)

        row = conn.execute(
            """
            SELECT id,name,parts_json,fuel_kg
            FROM ships
            WHERE id=?
            """,
            (sid,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Ship not found")

        parts = normalize_parts(json.loads(row["parts_json"] or "[]"))
        current_fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))
        inventory = compute_ship_inventory_containers(parts, current_fuel_kg)
        target = next((c for c in inventory if int(c["container_index"]) == int(req.container_index)), None)
        if not target:
            raise HTTPException(status_code=404, detail="Container not found")

        target_idx = int(target["container_index"])
        target_resource = str(target.get("resource_id") or "").lower()
        target_cargo_mass = max(0.0, float(target.get("cargo_mass_kg") or 0.0))

        if target_resource == "water":
            current_fuel_kg = max(0.0, current_fuel_kg - target_cargo_mass)

        if 0 <= target_idx < len(parts):
            part = dict(parts[target_idx] or {})
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
                "water_kg",
                "fuel_kg",
            ):
                if key in part:
                    part[key] = 0.0
            parts[target_idx] = part

        stats = derive_ship_stats_from_parts(parts, current_fuel_kg=current_fuel_kg)
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
                sid,
            ),
        )
        conn.commit()

        return {
            "ok": True,
            "ship_id": sid,
            "container_index": target_idx,
            "action": "jettison",
        }
    finally:
        conn.close()


@app.post("/api/ships/{ship_id}/deconstruct")
def api_ship_deconstruct(ship_id: str, req: ShipDeconstructReq, request: Request) -> Dict[str, Any]:
    sid = (ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    conn = connect_db()
    try:
        require_login(conn, request)
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

        location_id = str(row["location_id"] or "").strip()
        if not location_id or row["arrives_at"] is not None:
            raise HTTPException(status_code=400, detail="Ship must be docked at a location to deconstruct")

        parts = normalize_parts(json.loads(row["parts_json"] or "[]"))
        fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))
        containers = compute_ship_inventory_containers(parts, fuel_kg)
        by_index = {int(c["container_index"]): c for c in containers}

        transferred_fuel_like_kg = 0.0
        for idx, part in enumerate(parts):
            clean_part = dict(part)
            cargo = by_index.get(idx)
            if cargo:
                resource_id = str(cargo.get("resource_id") or "").strip()
                cargo_mass_kg = max(0.0, float(cargo.get("cargo_mass_kg") or 0.0))
                if resource_id and cargo_mass_kg > 0.0:
                    add_resource_to_location_inventory(conn, location_id, resource_id, cargo_mass_kg)
                    if resource_id.lower() == "water":
                        transferred_fuel_like_kg += cargo_mass_kg
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
                    "water_kg",
                    "fuel_kg",
                ):
                    clean_part.pop(key, None)

            add_part_to_location_inventory(conn, location_id, clean_part)

        if fuel_kg > transferred_fuel_like_kg + 1e-6:
            add_resource_to_location_inventory(conn, location_id, "water", fuel_kg - transferred_fuel_like_kg)

        if req.keep_ship_record:
            conn.execute(
                """
                UPDATE ships
                SET parts_json='[]', fuel_kg=0, fuel_capacity_kg=0, dry_mass_kg=0, isp_s=0
                WHERE id=?
                """,
                (sid,),
            )
        else:
            conn.execute("DELETE FROM ships WHERE id=?", (sid,))

        conn.commit()
        return {
            "ok": True,
            "ship_id": sid,
            "location_id": location_id,
            "parts_deconstructed": len(parts),
            "resources_transferred_kg": max(0.0, fuel_kg),
            "ship_deleted": not req.keep_ship_record,
        }
    finally:
        conn.close()


@app.post("/api/ships/{ship_id}/inventory/deploy")
def api_ship_inventory_deploy(ship_id: str, req: InventoryContainerReq, request: Request) -> Dict[str, Any]:
    sid = (ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    conn = connect_db()
    try:
        require_login(conn, request)

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

        location_id = str(row["location_id"] or "").strip()
        if not location_id or row["arrives_at"] is not None:
            raise HTTPException(status_code=400, detail="Ship must be docked to deploy a container")

        parts = normalize_parts(json.loads(row["parts_json"] or "[]"))
        current_fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))
        inventory = compute_ship_inventory_containers(parts, current_fuel_kg)
        target = next((c for c in inventory if int(c["container_index"]) == int(req.container_index)), None)
        if not target:
            raise HTTPException(status_code=404, detail="Container not found")

        target_idx = int(target["container_index"])
        target_resource = str(target.get("resource_id") or "").lower()
        target_cargo_mass = max(0.0, float(target.get("cargo_mass_kg") or 0.0))

        if target_resource == "water":
            current_fuel_kg = max(0.0, current_fuel_kg - target_cargo_mass)

        if not (0 <= target_idx < len(parts)):
            raise HTTPException(status_code=404, detail="Container not found")

        deployed_part = dict(parts.pop(target_idx) or {})
        if max(0.0, float(target.get("cargo_mass_kg") or 0.0)) > 0.0:
            deployed_part["resource_id"] = str(target.get("resource_id") or deployed_part.get("resource_id") or "")
            deployed_part["cargo_mass_kg"] = max(0.0, float(target.get("cargo_mass_kg") or 0.0))
            deployed_part["cargo_used_m3"] = max(0.0, float(target.get("used_m3") or 0.0))

        add_part_to_location_inventory(conn, location_id, deployed_part)

        stats = derive_ship_stats_from_parts(parts, current_fuel_kg=current_fuel_kg)
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
                sid,
            ),
        )
        conn.commit()

        return {
            "ok": True,
            "ship_id": sid,
            "location_id": location_id,
            "container_index": target_idx,
            "action": "deploy",
            "deployed_container": {
                "name": str((deployed_part or {}).get("name") or f"Container {target_idx + 1}"),
            },
        }
    finally:
        conn.close()
