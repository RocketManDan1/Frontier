"""
Canonical shared constants for the Frontier: Sol 2000 game server.

Both catalog_service.py and main.py previously held their own copies;
this module is the single source of truth.
"""

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Item categories
# ---------------------------------------------------------------------------

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
        "id": "constructor",
        "name": "Constructor",
        "kind": "ship_component",
        "description": "Surface-deployed robotic constructor for bulk excavation and infrastructure fabrication on gravity bodies.",
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
    "constructors": "constructor",
    "builder": "constructor",
    "builders": "constructor",
    "surface_constructor": "constructor",
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

# ---------------------------------------------------------------------------
# Research categories
# ---------------------------------------------------------------------------

RESEARCH_CATEGORIES: List[Dict[str, str]] = [
    {"id": "thrusters", "label": "Thrusters"},
    {"id": "reactors", "label": "Reactors"},
    {"id": "generators", "label": "Generators"},
    {"id": "robonauts", "label": "Robonauts"},
    {"id": "constructors", "label": "Constructors"},
    {"id": "refineries", "label": "Refineries"},
    {"id": "radiators", "label": "Radiators"},
]

THRUSTER_RESERVED_LANES: List[Dict[str, str]] = [
    {"id": "cryo", "label": "Cryo"},
    {"id": "solar", "label": "Solar"},
    {"id": "pulse", "label": "Pulse"},
]
