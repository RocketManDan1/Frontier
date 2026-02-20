import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from db import APP_DIR
from constants import (
    ITEM_CATEGORIES,
    ITEM_CATEGORY_ALIASES,
    ITEM_CATEGORY_BY_ID,
    RESEARCH_CATEGORIES,
    THRUSTER_RESERVED_LANES,
)


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
    tier_value = entry.get("tier")
    if tier_value is not None and not isinstance(tier_value, (int, float)):
        errors.append(f"{file_path}: 'tier' must be a number when provided")
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
    thruster_root = APP_DIR / "items" / "thrusters"
    if not thruster_root.exists() or not thruster_root.is_dir():
        return []

    specs: List[Dict[str, Any]] = []
    validation_errors: List[str] = []
    for family_dir in sorted([p for p in thruster_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        family_manifest_path = family_dir / "family.json"
        if not family_manifest_path.exists():
            validation_errors.append(f"{family_manifest_path}: required manifest is missing")
            continue

        try:
            family_manifest = _load_json_file(family_manifest_path)
        except ValueError as exc:
            validation_errors.append(str(exc))
            continue

        _validate_non_empty_str(family_manifest, "tech_category", family_manifest_path, validation_errors)
        _validate_string_list(family_manifest, "mainline_files", family_manifest_path, validation_errors, required=True)
        _validate_string_list(family_manifest, "upgrade_files", family_manifest_path, validation_errors)

        main_rel_paths = [str(v) for v in (family_manifest.get("mainline_files") or []) if str(v).strip()]
        if not main_rel_paths:
            validation_errors.append(f"{family_manifest_path}: 'mainline_files' must include at least one entry")
            continue
        upgrade_rel_paths = [str(v) for v in (family_manifest.get("upgrade_files") or []) if str(v).strip()]

        mains: List[Dict[str, Any]] = []
        for rel in main_rel_paths:
            file_path = family_dir / rel
            try:
                entry = _load_json_file(file_path)
            except ValueError as exc:
                validation_errors.append(str(exc))
                continue
            _validate_thruster_main_entry(entry, file_path, validation_errors)
            mains.append(_normalize_main_from_item(entry))

        upgrades: List[Dict[str, Any]] = []
        for rel in upgrade_rel_paths:
            file_path = family_dir / rel
            try:
                entry = _load_json_file(file_path)
            except ValueError as exc:
                validation_errors.append(str(exc))
                continue
            _validate_thruster_upgrade_entry(entry, file_path, validation_errors)
            upgrades.append(_normalize_upgrade_from_item(entry))

        reactor_model = dict(family_manifest.get("power_interface") or {})
        spec = {
            "tech_category": str(family_manifest.get("tech_category") or family_dir.name),
            "display_name": str(family_manifest.get("display_name") or family_dir.name.replace("_", " ").title()),
            "version": str(family_manifest.get("schema_version") or "1.0"),
            "notes": [str(n) for n in (family_manifest.get("notes") or [])],
            "reactor_model": reactor_model,
            "Main": mains,
            "Upgrade": upgrades,
        }
        specs.append(spec)

    if validation_errors:
        joined = "\n".join(f"- {msg}" for msg in validation_errors)
        raise RuntimeError(f"Item manifest validation failed:\n{joined}")

    return specs


@lru_cache(maxsize=1)
def load_thruster_main_catalog() -> Dict[str, Dict[str, Any]]:
    catalog: Dict[str, Dict[str, Any]] = {}
    specs = load_thruster_specs_from_items()
    if not specs:
        specs = [NTR_THRUSTER_SPEC]
    for spec in specs:
        tech_category = str(spec.get("tech_category") or "thruster")
        for main in (spec.get("Main") or spec.get("engines") or []):
            item_id = str(main.get("id") or "").strip()
            if not item_id:
                continue

            catalog[item_id] = {
                "item_id": item_id,
                "name": str(main.get("name") or item_id),
                "type": "thruster",
                "category_id": "thruster",
                "thruster_family": tech_category,
                "branch": str(main.get("branch") or "core"),
                "mass_kg": max(0.0, float(main.get("engine_mass_t") or 0.0) * 1000.0),
                "isp_s": max(0.0, float(main.get("isp_s") or 0.0)),
                "thrust_kn": max(0.0, float(main.get("max_thrust_kN") or 0.0)),
                "thermal_mw": float(main.get("P_req_mw_th") or 0.0),
                "reaction_mass": str((main.get("consumables") or {}).get("reaction_mass") or ""),
            }

    return catalog


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
    catalog: Dict[str, Dict[str, Any]] = {}
    for root in _item_roots_for("Resources"):
        if not root.exists() or not root.is_dir():
            continue
        for path in sorted(root.glob("*.json"), key=lambda p: p.name.lower()):
            entry = _load_json_file(path)
            item_id = str(entry.get("id") or "").strip()
            if not item_id:
                continue
            catalog[item_id] = {
                "id": item_id,
                "name": str(entry.get("name") or item_id),
                "type": str(entry.get("type") or "resource"),
                "category_id": str(entry.get("category_id") or "fuel"),
                "mass_per_m3_kg": float(entry.get("mass_per_m3_kg") or 0.0),
            }
    return catalog


@lru_cache(maxsize=1)
def load_storage_catalog() -> Dict[str, Dict[str, Any]]:
    catalog: Dict[str, Dict[str, Any]] = {}
    for root in _item_roots_for("Storage"):
        if not root.exists() or not root.is_dir():
            continue
        for path in sorted(root.glob("*.json"), key=lambda p: p.name.lower()):
            entry = _load_json_file(path)
            item_id = str(entry.get("id") or "").strip()
            if not item_id:
                continue
            catalog[item_id] = {
                "item_id": item_id,
                "name": str(entry.get("name") or item_id),
                "type": str(entry.get("type") or "storage"),
                "category_id": str(entry.get("category_id") or "storage"),
                "mass_kg": max(0.0, float(entry.get("mass_kg") or 0.0)),
                "capacity_m3": max(0.0, float(entry.get("capacity_m3") or 0.0)),
                "resource_id": str(entry.get("resource_id") or ""),
            }
    return catalog


@lru_cache(maxsize=1)
def load_reactor_catalog() -> Dict[str, Dict[str, Any]]:
    """Load all reactor items from items/reactors/<family>/*.json."""
    catalog: Dict[str, Dict[str, Any]] = {}
    reactor_root = APP_DIR / "items" / "reactors"
    if not reactor_root.exists() or not reactor_root.is_dir():
        return catalog
    for family_dir in sorted(
        [p for p in reactor_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()
    ):
        family_path = family_dir / "family.json"
        if not family_path.exists():
            continue
        try:
            family = _load_json_file(family_path)
        except ValueError:
            continue
        mainline_files = [str(v) for v in (family.get("mainline_files") or []) if str(v).strip()]
        for rel in mainline_files:
            file_path = family_dir / rel
            if not file_path.exists():
                continue
            try:
                entry = _load_json_file(file_path)
            except ValueError:
                continue
            item_id = str(entry.get("id") or "").strip()
            if not item_id:
                continue
            output = entry.get("output") or {}
            catalog[item_id] = {
                "item_id": item_id,
                "name": str(entry.get("name") or item_id),
                "type": "reactor",
                "category_id": "reactor",
                "mass_kg": max(0.0, float(entry.get("mass_t") or 0.0) * 1000.0),
                "thermal_mw": max(0.0, float(output.get("thermal_mw") or 0.0)),
                "branch": str(entry.get("branch") or ""),
            }
    return catalog


@lru_cache(maxsize=1)
def load_generator_catalog() -> Dict[str, Dict[str, Any]]:
    """Load all generator items from items/generators/<family>/*.json."""
    catalog: Dict[str, Dict[str, Any]] = {}
    gen_root = APP_DIR / "items" / "generators"
    if not gen_root.exists() or not gen_root.is_dir():
        return catalog
    for family_dir in sorted(
        [p for p in gen_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()
    ):
        family_path = family_dir / "family.json"
        if not family_path.exists():
            continue
        try:
            family = _load_json_file(family_path)
        except ValueError:
            continue
        mainline_files = [str(v) for v in (family.get("mainline_files") or []) if str(v).strip()]
        for rel in mainline_files:
            file_path = family_dir / rel
            if not file_path.exists():
                continue
            try:
                entry = _load_json_file(file_path)
            except ValueError:
                continue
            item_id = str(entry.get("id") or "").strip()
            if not item_id:
                continue
            inp = entry.get("input") or {}
            out = entry.get("output") or {}
            efficiency = max(0.0, min(1.0, float(entry.get("conversion_efficiency") or 0.0)))
            thermal_input = max(0.0, float(inp.get("thermal_mw_rated") or 0.0))
            electric_output = max(0.0, float(out.get("electric_mw") or 0.0))
            waste_heat_mw = thermal_input * (1.0 - efficiency) if efficiency > 0.0 else thermal_input
            catalog[item_id] = {
                "item_id": item_id,
                "name": str(entry.get("name") or item_id),
                "type": "generator",
                "category_id": "generator",
                "mass_kg": max(0.0, float(entry.get("mass_t") or 0.0) * 1000.0),
                "thermal_mw_input": thermal_input,
                "electric_mw": electric_output,
                "conversion_efficiency": efficiency,
                "waste_heat_mw": waste_heat_mw,
                "branch": str(entry.get("branch") or ""),
            }
    return catalog


@lru_cache(maxsize=1)
def load_radiator_catalog() -> Dict[str, Dict[str, Any]]:
    """Load all radiator items from items/radiators/<family>/*.json."""
    catalog: Dict[str, Dict[str, Any]] = {}
    rad_root = APP_DIR / "items" / "radiators"
    if not rad_root.exists() or not rad_root.is_dir():
        return catalog
    for family_dir in sorted(
        [p for p in rad_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()
    ):
        family_path = family_dir / "family.json"
        if not family_path.exists():
            continue
        try:
            family = _load_json_file(family_path)
        except ValueError:
            continue
        mainline_files = [str(v) for v in (family.get("mainline_files") or []) if str(v).strip()]
        for rel in mainline_files:
            file_path = family_dir / rel
            if not file_path.exists():
                continue
            try:
                entry = _load_json_file(file_path)
            except ValueError:
                continue
            item_id = str(entry.get("id") or "").strip()
            if not item_id:
                continue
            out = entry.get("output") or {}
            catalog[item_id] = {
                "item_id": item_id,
                "name": str(entry.get("name") or item_id),
                "type": "radiator",
                "category_id": "radiator",
                "mass_kg": max(0.0, float(entry.get("mass_t") or 0.0) * 1000.0),
                "heat_rejection_mw": max(0.0, float(out.get("heat_rejection_mw") or 0.0)),
                "operating_temp_k": max(0.0, float(entry.get("operating_temp_k") or 0.0)),
                "branch": str(entry.get("branch") or ""),
            }
    return catalog


@lru_cache(maxsize=1)
def load_robonaut_catalog() -> Dict[str, Dict[str, Any]]:
    """Load all robonaut items from items/robonauts/<family>/*.json."""
    catalog: Dict[str, Dict[str, Any]] = {}
    rob_root = APP_DIR / "items" / "robonauts"
    if not rob_root.exists() or not rob_root.is_dir():
        return catalog
    for family_dir in sorted(
        [p for p in rob_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()
    ):
        family_path = family_dir / "family.json"
        if not family_path.exists():
            continue
        try:
            family = _load_json_file(family_path)
        except ValueError:
            continue
        mainline_files = [str(v) for v in (family.get("mainline_files") or []) if str(v).strip()]
        for rel in mainline_files:
            file_path = family_dir / rel
            if not file_path.exists():
                continue
            try:
                entry = _load_json_file(file_path)
            except ValueError:
                continue
            item_id = str(entry.get("id") or "").strip()
            if not item_id:
                continue
            perf = entry.get("performance") or {}
            power_req = entry.get("power_requirements") or {}
            catalog[item_id] = {
                "item_id": item_id,
                "name": str(entry.get("name") or item_id),
                "type": "robonaut",
                "category_id": "robonaut",
                "mass_kg": max(0.0, float(entry.get("mass_t") or 0.0) * 1000.0),
                "electric_mw": max(0.0, float(power_req.get("electric_mw") or 0.0)),
                "prospect_range_km": max(0.0, float(perf.get("prospect_range_km") or 0.0)),
                "scan_rate_km2_per_hr": max(0.0, float(perf.get("scan_rate_km2_per_hr") or 0.0)),
                "melt_rate_t_per_hr": max(0.0, float(perf.get("melt_rate_t_per_hr") or 0.0)),
                "emission_type": str(perf.get("emission_type") or ""),
                "branch": str(entry.get("branch") or ""),
                "research_unlock_level": max(1, int(entry.get("research_unlock_level") or 1)),
            }
    return catalog


@lru_cache(maxsize=1)
def load_constructor_catalog() -> Dict[str, Dict[str, Any]]:
    """Load all constructor items from items/constructors/<family>/*.json."""
    catalog: Dict[str, Dict[str, Any]] = {}
    con_root = APP_DIR / "items" / "constructors"
    if not con_root.exists() or not con_root.is_dir():
        return catalog
    for family_dir in sorted(
        [p for p in con_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()
    ):
        family_path = family_dir / "family.json"
        if not family_path.exists():
            continue
        try:
            family = _load_json_file(family_path)
        except ValueError:
            continue
        mainline_files = [str(v) for v in (family.get("mainline_files") or []) if str(v).strip()]
        for rel in mainline_files:
            file_path = family_dir / rel
            if not file_path.exists():
                continue
            try:
                entry = _load_json_file(file_path)
            except ValueError:
                continue
            item_id = str(entry.get("id") or "").strip()
            if not item_id:
                continue
            perf = entry.get("performance") or {}
            power_req = entry.get("power_requirements") or {}
            catalog[item_id] = {
                "item_id": item_id,
                "name": str(entry.get("name") or item_id),
                "type": "constructor",
                "category_id": "constructor",
                "mass_kg": max(0.0, float(entry.get("mass_t") or 0.0) * 1000.0),
                "electric_mw": max(0.0, float(power_req.get("electric_mw") or 0.0)),
                "mining_rate_kg_per_hr": max(0.0, float(perf.get("mining_rate_kg_per_hr") or 0.0)),
                "construction_rate_kg_per_hr": max(0.0, float(perf.get("construction_rate_kg_per_hr") or 0.0)),
                "excavation_type": str(perf.get("excavation_type") or ""),
                "operational_environment": str(entry.get("operational_environment") or "surface_gravity"),
                "min_surface_gravity_ms2": max(0.0, float(entry.get("min_surface_gravity_ms2") or 0.0)),
                "branch": str(entry.get("branch") or ""),
                "research_unlock_level": max(1, int(entry.get("research_unlock_level") or 1)),
            }
    return catalog


def compute_power_balance(parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute the thermal/electric/waste-heat balance from a list of normalized parts."""
    reactor_thermal_mw = 0.0
    thruster_thermal_mw = 0.0
    generator_thermal_mw_input = 0.0
    generator_electric_mw = 0.0
    generator_waste_heat_mw = 0.0
    radiator_heat_rejection_mw = 0.0
    robonaut_electric_mw = 0.0
    constructor_electric_mw = 0.0

    for part in parts:
        cat = str(part.get("category_id") or part.get("type") or "").lower()
        if cat == "reactor":
            reactor_thermal_mw += max(0.0, float(part.get("thermal_mw") or 0.0))
        elif cat == "thruster":
            thruster_thermal_mw += max(0.0, float(part.get("thermal_mw") or 0.0))
        elif cat == "generator":
            generator_thermal_mw_input += max(0.0, float(part.get("thermal_mw_input") or 0.0))
            generator_electric_mw += max(0.0, float(part.get("electric_mw") or 0.0))
            generator_waste_heat_mw += max(0.0, float(part.get("waste_heat_mw") or 0.0))
        elif cat == "radiator":
            radiator_heat_rejection_mw += max(0.0, float(part.get("heat_rejection_mw") or 0.0))
        elif cat == "robonaut":
            robonaut_electric_mw += max(0.0, float(part.get("electric_mw") or 0.0))
        elif cat == "constructor":
            constructor_electric_mw += max(0.0, float(part.get("electric_mw") or 0.0))

    # Total thermal demand = thruster + generator input
    total_thermal_demand_mw = thruster_thermal_mw + generator_thermal_mw_input
    thermal_surplus_mw = reactor_thermal_mw - total_thermal_demand_mw

    # Total waste heat = generator waste heat (reactor waste heat goes into thrust for NTR)
    waste_heat_surplus_mw = generator_waste_heat_mw - radiator_heat_rejection_mw

    # Electric balance: generator output minus all electric consumers
    electric_surplus_mw = generator_electric_mw - robonaut_electric_mw - constructor_electric_mw

    # Throttle cap: if reactor can't supply thruster, throttle is limited
    if thruster_thermal_mw > 0.0 and reactor_thermal_mw > 0.0:
        max_throttle = min(1.0, reactor_thermal_mw / thruster_thermal_mw)
    elif thruster_thermal_mw > 0.0:
        max_throttle = 0.0
    else:
        max_throttle = 1.0

    return {
        "reactor_thermal_mw": round(reactor_thermal_mw, 1),
        "thruster_thermal_mw": round(thruster_thermal_mw, 1),
        "generator_thermal_mw_input": round(generator_thermal_mw_input, 1),
        "total_thermal_demand_mw": round(total_thermal_demand_mw, 1),
        "thermal_surplus_mw": round(thermal_surplus_mw, 1),
        "generator_electric_mw": round(generator_electric_mw, 1),
        "generator_waste_heat_mw": round(generator_waste_heat_mw, 1),
        "radiator_heat_rejection_mw": round(radiator_heat_rejection_mw, 1),
        "waste_heat_surplus_mw": round(waste_heat_surplus_mw, 1),
        "robonaut_electric_mw": round(robonaut_electric_mw, 1),
        "constructor_electric_mw": round(constructor_electric_mw, 1),
        "electric_surplus_mw": round(electric_surplus_mw, 1),
        "max_throttle": round(max_throttle, 4),
    }


@lru_cache(maxsize=1)
def load_recipe_catalog() -> Dict[str, Dict[str, Any]]:
    catalog: Dict[str, Dict[str, Any]] = {}
    for root in _item_roots_for("Recipes"):
        if not root.exists() or not root.is_dir():
            continue
        for path in sorted(root.glob("*.json"), key=lambda p: p.name.lower()):
            entry = _load_json_file(path)
            recipe_id = str(entry.get("recipe_id") or entry.get("id") or "").strip()
            if not recipe_id:
                continue

            inputs: List[Dict[str, Any]] = []
            for raw_input in (entry.get("inputs") or []):
                if not isinstance(raw_input, dict):
                    continue
                input_item_id = str(raw_input.get("item_id") or raw_input.get("id") or "").strip()
                if not input_item_id:
                    continue
                inputs.append(
                    {
                        "item_id": input_item_id,
                        "qty": max(0.0, float(raw_input.get("qty") or 0.0)),
                    }
                )

            byproducts: List[Dict[str, Any]] = []
            for raw_output in (entry.get("byproducts") or []):
                if not isinstance(raw_output, dict):
                    continue
                output_item_id = str(raw_output.get("item_id") or raw_output.get("id") or "").strip()
                if not output_item_id:
                    continue
                byproducts.append(
                    {
                        "item_id": output_item_id,
                        "qty": max(0.0, float(raw_output.get("qty") or 0.0)),
                    }
                )

            catalog[recipe_id] = {
                "recipe_id": recipe_id,
                "name": str(entry.get("name") or recipe_id),
                "output_item_id": str(entry.get("output_item_id") or "").strip(),
                "output_qty": max(0.0, float(entry.get("output_qty") or 0.0)),
                "inputs": inputs,
                "build_time_s": max(0.0, float(entry.get("build_time_s") or 0.0)),
                "facility_type": str(entry.get("facility_type") or "factory"),
                "refinery_category": str(entry.get("refinery_category") or "unassigned"),
                "min_tech_tier": max(0, int(entry.get("min_tech_tier") or 0)),
                "power_kw": max(0.0, float(entry.get("power_kw") or 0.0)),
                "byproducts": byproducts,
            }
    return catalog


def build_recipe_categories_payload(recipe_catalog: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    ordered_category_ids = [
        "lithic_processing",
        "metallurgy",
        "volatiles_cryogenics",
        "nuclear_exotic",
        "unassigned",
    ]
    category_labels = {
        "lithic_processing": "Lithic Processing",
        "metallurgy": "Metallurgy",
        "volatiles_cryogenics": "Volatiles & Cryogenics",
        "nuclear_exotic": "Nuclear & Exotic",
        "unassigned": "Unassigned",
    }

    grouped: Dict[str, List[Dict[str, Any]]] = {category_id: [] for category_id in ordered_category_ids}
    for recipe in sorted(recipe_catalog.values(), key=lambda r: str(r.get("name") or "").lower()):
        category_id = str(recipe.get("refinery_category") or "unassigned").strip() or "unassigned"
        if category_id not in grouped:
            grouped[category_id] = []
        grouped[category_id].append(recipe)

    categories: List[Dict[str, Any]] = []
    for category_id in ordered_category_ids:
        categories.append(
            {
                "id": category_id,
                "label": category_labels.get(category_id, category_id.replace("_", " ").title()),
                "recipes": grouped.get(category_id, []),
            }
        )

    extra_category_ids = sorted(cid for cid in grouped.keys() if cid not in ordered_category_ids)
    for category_id in extra_category_ids:
        categories.append(
            {
                "id": category_id,
                "label": category_labels.get(category_id, category_id.replace("_", " ").title()),
                "recipes": grouped.get(category_id, []),
            }
        )

    return {
        "categories": categories,
    }


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
    trees: Dict[str, Dict[str, Any]] = {c["id"]: {"nodes": [], "edges": [], "meta": {}} for c in RESEARCH_CATEGORIES}

    loaded_specs = load_thruster_specs_from_items()
    if not loaded_specs:
        loaded_specs = [NTR_THRUSTER_SPEC]

    lane_width = 420
    lane_gap = 60
    lane_start_x = 80

    lane_specs_by_id: Dict[str, Dict[str, Any]] = {}
    lane_order: List[Dict[str, Any]] = []

    for spec in loaded_specs:
        lane_id = _slugify_lane_id(spec.get("tech_category") or spec.get("display_name") or "thrusters")
        lane_label = str(spec.get("display_name") or spec.get("tech_category") or lane_id).strip() or lane_id
        lane_specs_by_id[lane_id] = spec
        lane_order.append({"id": lane_id, "label": lane_label, "reserved": False})

    for reserved in THRUSTER_RESERVED_LANES:
        reserved_id = _slugify_lane_id(reserved.get("id") or "")
        if reserved_id in lane_specs_by_id:
            continue
        lane_order.append({"id": reserved_id, "label": str(reserved.get("label") or reserved_id), "reserved": True})

    thruster_nodes: List[Dict[str, Any]] = []
    thruster_edges: List[Dict[str, Any]] = []
    thruster_notes: List[str] = []
    disconnected_all: List[str] = []
    lane_meta: List[Dict[str, Any]] = []

    for idx, lane in enumerate(lane_order):
        lane_id = str(lane.get("id") or f"lane_{idx}")
        lane_label = str(lane.get("label") or lane_id)
        lane_x = lane_start_x + (idx * (lane_width + lane_gap))
        spec = lane_specs_by_id.get(lane_id)

        if spec:
            lane_tree = build_thruster_tree_from_spec(
                spec,
                lane_x_offset=lane_x,
                lane_width=lane_width,
                lane_id=lane_id,
                lane_label=lane_label,
            )
            thruster_nodes.extend(lane_tree.get("nodes") or [])
            thruster_edges.extend(lane_tree.get("edges") or [])
            lane_notes = [str(n) for n in ((lane_tree.get("meta") or {}).get("notes") or []) if str(n).strip()]
            thruster_notes.extend(lane_notes)
            disconnected_all.extend([str(n) for n in (((lane_tree.get("meta") or {}).get("connectivity") or {}).get("disconnected_nodes") or [])])
            lane_info = dict((lane_tree.get("meta") or {}).get("lane") or {})
            lane_info["reserved"] = False
            lane_info["node_count"] = len(lane_tree.get("nodes") or [])
            lane_meta.append(lane_info)
        else:
            lane_meta.append(
                {
                    "id": lane_id,
                    "label": lane_label,
                    "x": lane_x,
                    "width": lane_width,
                    "reserved": True,
                    "node_count": 0,
                }
            )

    trees["thrusters"] = {
        "nodes": thruster_nodes,
        "edges": thruster_edges,
        "meta": {
            "source": "items/thrusters",
            "version": "dynamic",
            "notes": thruster_notes,
            "layout": "lanes_vertical",
            "lane_flow": "main-upgrade-main-upgrade",
            "lanes": lane_meta,
            "connectivity": {
                "connected": len(disconnected_all) == 0,
                "disconnected_nodes": sorted(set(disconnected_all)),
            },
        },
    }

    return {
        "categories": RESEARCH_CATEGORIES,
        "trees": trees,
    }


def canonical_item_category(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return "generic"
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if text in ITEM_CATEGORY_BY_ID:
        return text
    return ITEM_CATEGORY_ALIASES.get(text, "generic")


def normalize_parts(
    raw_parts: Any,
    thruster_catalog: Dict[str, Dict[str, Any]],
    storage_catalog: Dict[str, Dict[str, Any]],
    canonical_item_category: Callable[[Any], str],
    reactor_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
    generator_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
    radiator_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
    robonaut_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
    constructor_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(raw_parts, list):
        return []

    _reactor_catalog = reactor_catalog or {}
    _generator_catalog = generator_catalog or {}
    _radiator_catalog = radiator_catalog or {}
    _robonaut_catalog = robonaut_catalog or {}
    _constructor_catalog = constructor_catalog or {}

    normalized: List[Dict[str, Any]] = []
    for entry in raw_parts:
        if isinstance(entry, str):
            label = entry.strip()
            if not label:
                continue
            if label in thruster_catalog:
                normalized.append(dict(thruster_catalog[label]))
                continue
            if label in storage_catalog:
                normalized.append(dict(storage_catalog[label]))
                continue
            if label in _reactor_catalog:
                normalized.append(dict(_reactor_catalog[label]))
                continue
            if label in _generator_catalog:
                normalized.append(dict(_generator_catalog[label]))
                continue
            if label in _radiator_catalog:
                normalized.append(dict(_radiator_catalog[label]))
                continue
            if label in _robonaut_catalog:
                normalized.append(dict(_robonaut_catalog[label]))
                continue
            if label in _constructor_catalog:
                normalized.append(dict(_constructor_catalog[label]))
                continue
            category_id = canonical_item_category(label)
            normalized.append({"name": label, "type": category_id, "category_id": category_id})
            continue
        if isinstance(entry, dict):
            raw_item_id = str(entry.get("item_id") or entry.get("id") or "").strip()
            if raw_item_id and raw_item_id in thruster_catalog:
                merged = dict(thruster_catalog[raw_item_id])
                merged.update(entry)
                normalized.append(merged)
                continue
            if raw_item_id and raw_item_id in storage_catalog:
                merged = dict(storage_catalog[raw_item_id])
                merged.update(entry)
                normalized.append(merged)
                continue
            if raw_item_id and raw_item_id in _reactor_catalog:
                merged = dict(_reactor_catalog[raw_item_id])
                merged.update(entry)
                normalized.append(merged)
                continue
            if raw_item_id and raw_item_id in _generator_catalog:
                merged = dict(_generator_catalog[raw_item_id])
                merged.update(entry)
                normalized.append(merged)
                continue
            if raw_item_id and raw_item_id in _radiator_catalog:
                merged = dict(_radiator_catalog[raw_item_id])
                merged.update(entry)
                normalized.append(merged)
                continue
            if raw_item_id and raw_item_id in _robonaut_catalog:
                merged = dict(_robonaut_catalog[raw_item_id])
                merged.update(entry)
                normalized.append(merged)
                continue
            if raw_item_id and raw_item_id in _constructor_catalog:
                merged = dict(_constructor_catalog[raw_item_id])
                merged.update(entry)
                normalized.append(merged)
                continue

            name = str(entry.get("name") or entry.get("type") or "part").strip()
            if not name:
                continue
            item = dict(entry)
            category_input = item.get("category_id") or item.get("type") or item.get("category") or name
            category_id = canonical_item_category(category_input)
            item["name"] = name
            item["type"] = category_id
            item["category_id"] = category_id
            normalized.append(item)
    return normalized


def derive_ship_stats_from_parts(
    parts: List[Dict[str, Any]],
    resource_catalog: Dict[str, Dict[str, Any]],
    current_fuel_kg: Optional[float] = None,
) -> Dict[str, float]:
    dry_mass_kg = 0.0
    water_mass_kg = 0.0
    isp_values: List[float] = []
    thrust_total_kn = 0.0

    for part in parts:
        part_type = str(part.get("type") or "").lower()
        dry_mass_kg += max(0.0, float(part.get("mass_kg") or 0.0))
        part_water_kg = max(0.0, float(part.get("water_kg") or part.get("fuel_kg") or 0.0))
        if part_water_kg <= 0.0:
            capacity_m3 = max(0.0, float(part.get("capacity_m3") or 0.0))
            resource_id = str(part.get("resource_id") or "").strip()
            if capacity_m3 > 0.0 and resource_id:
                resource = resource_catalog.get(resource_id) or {}
                density = max(0.0, float(resource.get("mass_per_m3_kg") or 0.0))
                if density > 0.0 and resource_id == "water":
                    part_water_kg = capacity_m3 * density
        water_mass_kg += part_water_kg

        part_isp = float(part.get("isp_s") or 0.0)
        if part_isp > 0.0 and (part_type == "thruster" or "thruster" in str(part.get("name", "")).lower()):
            isp_values.append(part_isp)

        thrust_total_kn += max(0.0, float(part.get("thrust_kn") or 0.0))

    resolved_dry_mass_kg = max(0.0, dry_mass_kg)
    resolved_fuel_capacity_kg = max(0.0, water_mass_kg)
    if current_fuel_kg is None:
        resolved_fuel_kg = resolved_fuel_capacity_kg
    else:
        resolved_fuel_kg = max(0.0, min(float(current_fuel_kg or 0.0), resolved_fuel_capacity_kg))

    resolved_isp_s = max(isp_values) if isp_values else 0.0

    return {
        "dry_mass_kg": resolved_dry_mass_kg,
        "fuel_kg": resolved_fuel_kg,
        "fuel_capacity_kg": resolved_fuel_capacity_kg,
        "isp_s": resolved_isp_s,
        "thrust_kn": thrust_total_kn,
    }


def compute_wet_mass_kg(dry_mass_kg: float, fuel_kg: float) -> float:
    return max(0.0, float(dry_mass_kg or 0.0)) + max(0.0, float(fuel_kg or 0.0))


def compute_acceleration_gs(dry_mass_kg: float, fuel_kg: float, thrust_kn: float) -> float:
    wet_mass_kg = compute_wet_mass_kg(dry_mass_kg, fuel_kg)
    if wet_mass_kg <= 0.0:
        return 0.0
    thrust_n = max(0.0, float(thrust_kn or 0.0)) * 1000.0
    return thrust_n / (wet_mass_kg * 9.80665)


def normalize_shipyard_item_ids(raw_parts: Any) -> List[str]:
    if not isinstance(raw_parts, list):
        return []
    out: List[str] = []
    for entry in raw_parts:
        if isinstance(entry, str):
            item_id = entry.strip()
            if item_id:
                out.append(item_id)
            continue
        if isinstance(entry, dict):
            item_id = str(entry.get("item_id") or entry.get("id") or "").strip()
            if item_id:
                out.append(item_id)
    return out


def shipyard_parts_from_item_ids(
    item_ids: List[str],
    normalize_parts_fn: Callable[[Any], List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    return normalize_parts_fn([{"item_id": item_id} for item_id in item_ids if str(item_id).strip()])


def compute_delta_v_remaining_m_s(dry_mass_kg: float, fuel_kg: float, isp_s: float) -> float:
    dry = max(0.0, float(dry_mass_kg or 0.0))
    fuel = max(0.0, float(fuel_kg or 0.0))
    isp = max(0.0, float(isp_s or 0.0))
    if dry <= 0.0 or fuel <= 0.0 or isp <= 0.0:
        return 0.0
    g0 = 9.80665
    return isp * g0 * math.log((dry + fuel) / dry)


def compute_fuel_needed_for_delta_v_kg(dry_mass_kg: float, fuel_kg: float, isp_s: float, dv_m_s: float) -> float:
    dry = max(0.0, float(dry_mass_kg or 0.0))
    fuel = max(0.0, float(fuel_kg or 0.0))
    isp = max(0.0, float(isp_s or 0.0))
    dv = max(0.0, float(dv_m_s or 0.0))
    if dv <= 0.0:
        return 0.0
    if dry <= 0.0 or fuel <= 0.0 or isp <= 0.0:
        return fuel + 1.0

    g0 = 9.80665
    m0 = dry + fuel
    mf = m0 / math.exp(dv / (isp * g0))
    used = m0 - mf
    return max(0.0, min(used, fuel))


def build_ship_stats_payload(
    parts: List[Dict[str, Any]],
    resource_catalog: Dict[str, Dict[str, Any]],
    current_fuel_kg: Optional[float] = None,
) -> Dict[str, float]:
    stats = derive_ship_stats_from_parts(parts, resource_catalog, current_fuel_kg=current_fuel_kg)
    wet_mass_kg = compute_wet_mass_kg(stats["dry_mass_kg"], stats["fuel_kg"])
    delta_v_remaining_m_s = compute_delta_v_remaining_m_s(stats["dry_mass_kg"], stats["fuel_kg"], stats["isp_s"])
    accel_g = compute_acceleration_gs(stats["dry_mass_kg"], stats["fuel_kg"], stats["thrust_kn"])
    return {
        "dry_mass_kg": stats["dry_mass_kg"],
        "fuel_kg": stats["fuel_kg"],
        "fuel_capacity_kg": stats["fuel_capacity_kg"],
        "wet_mass_kg": wet_mass_kg,
        "isp_s": stats["isp_s"],
        "thrust_kn": stats["thrust_kn"],
        "delta_v_remaining_m_s": delta_v_remaining_m_s,
        "accel_g": accel_g,
    }


def build_shipyard_catalog_payload(
    thruster_catalog: Dict[str, Dict[str, Any]],
    storage_catalog: Dict[str, Dict[str, Any]],
    resource_catalog: Dict[str, Dict[str, Any]],
    recipe_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
    reactor_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
    generator_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
    radiator_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
    robonaut_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
    constructor_catalog: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = []

    for item in (reactor_catalog or {}).values():
        parts.append(
            {
                "item_id": item.get("item_id"),
                "name": item.get("name"),
                "type": "reactor",
                "category_id": "reactor",
                "mass_kg": float(item.get("mass_kg") or 0.0),
                "thermal_mw": float(item.get("thermal_mw") or 0.0),
                "branch": str(item.get("branch") or ""),
            }
        )

    for item in thruster_catalog.values():
        parts.append(
            {
                "item_id": item.get("item_id"),
                "name": item.get("name"),
                "type": "thruster",
                "category_id": "thruster",
                "mass_kg": float(item.get("mass_kg") or 0.0),
                "thrust_kn": float(item.get("thrust_kn") or 0.0),
                "isp_s": float(item.get("isp_s") or 0.0),
                "thermal_mw": float(item.get("thermal_mw") or 0.0),
                "family": str(item.get("thruster_family") or ""),
                "branch": str(item.get("branch") or ""),
            }
        )

    for item in (generator_catalog or {}).values():
        parts.append(
            {
                "item_id": item.get("item_id"),
                "name": item.get("name"),
                "type": "generator",
                "category_id": "generator",
                "mass_kg": float(item.get("mass_kg") or 0.0),
                "thermal_mw_input": float(item.get("thermal_mw_input") or 0.0),
                "electric_mw": float(item.get("electric_mw") or 0.0),
                "conversion_efficiency": float(item.get("conversion_efficiency") or 0.0),
                "waste_heat_mw": float(item.get("waste_heat_mw") or 0.0),
                "branch": str(item.get("branch") or ""),
            }
        )

    for item in (radiator_catalog or {}).values():
        parts.append(
            {
                "item_id": item.get("item_id"),
                "name": item.get("name"),
                "type": "radiator",
                "category_id": "radiator",
                "mass_kg": float(item.get("mass_kg") or 0.0),
                "heat_rejection_mw": float(item.get("heat_rejection_mw") or 0.0),
                "operating_temp_k": float(item.get("operating_temp_k") or 0.0),
                "branch": str(item.get("branch") or ""),
            }
        )

    for item in (robonaut_catalog or {}).values():
        parts.append(
            {
                "item_id": item.get("item_id"),
                "name": item.get("name"),
                "type": "robonaut",
                "category_id": "robonaut",
                "mass_kg": float(item.get("mass_kg") or 0.0),
                "electric_mw": float(item.get("electric_mw") or 0.0),
                "prospect_range_km": float(item.get("prospect_range_km") or 0.0),
                "scan_rate_km2_per_hr": float(item.get("scan_rate_km2_per_hr") or 0.0),
                "melt_rate_t_per_hr": float(item.get("melt_rate_t_per_hr") or 0.0),
                "emission_type": str(item.get("emission_type") or ""),
                "branch": str(item.get("branch") or ""),
                "research_unlock_level": int(item.get("research_unlock_level") or 1),
            }
        )

    for item in (constructor_catalog or {}).values():
        parts.append(
            {
                "item_id": item.get("item_id"),
                "name": item.get("name"),
                "type": "constructor",
                "category_id": "constructor",
                "mass_kg": float(item.get("mass_kg") or 0.0),
                "electric_mw": float(item.get("electric_mw") or 0.0),
                "mining_rate_kg_per_hr": float(item.get("mining_rate_kg_per_hr") or 0.0),
                "construction_rate_kg_per_hr": float(item.get("construction_rate_kg_per_hr") or 0.0),
                "excavation_type": str(item.get("excavation_type") or ""),
                "operational_environment": str(item.get("operational_environment") or "surface_gravity"),
                "min_surface_gravity_ms2": float(item.get("min_surface_gravity_ms2") or 0.0),
                "branch": str(item.get("branch") or ""),
                "research_unlock_level": int(item.get("research_unlock_level") or 1),
            }
        )

    for item in storage_catalog.values():
        resource_id = str(item.get("resource_id") or "")
        density = float((resource_catalog.get(resource_id) or {}).get("mass_per_m3_kg") or 0.0)
        capacity_m3 = float(item.get("capacity_m3") or 0.0)
        parts.append(
            {
                "item_id": item.get("item_id"),
                "name": item.get("name"),
                "type": "storage",
                "category_id": "storage",
                "mass_kg": float(item.get("mass_kg") or 0.0),
                "capacity_m3": capacity_m3,
                "resource_id": resource_id,
                "fuel_capacity_kg": capacity_m3 * density if resource_id == "water" and density > 0.0 else 0.0,
            }
        )

    parts.sort(key=lambda p: (str(p.get("category_id") or ""), str(p.get("name") or "")))
    recipes = sorted((recipe_catalog or {}).values(), key=lambda r: str(r.get("name") or "").lower())
    return {
        "build_location_id": "LEO",
        "parts": parts,
        "recipes": recipes,
    }
