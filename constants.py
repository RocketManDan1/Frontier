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
        "id": "prospector",
        "name": "Prospector",
        "kind": "ship_component",
        "description": "Directed-energy prospecting module for remote scanning and site characterization.",
    },
    {
        "id": "miner",
        "name": "Miner",
        "kind": "ship_component",
        "description": "Surface-deployed robotic miner for bulk excavation and ore extraction. Subtypes: large_body (gravity > 1 m/s²), microgravity (< 1 m/s²), cryovolatile (ice-rich sites).",
    },
    {
        "id": "printer",
        "name": "Printer",
        "kind": "ship_component",
        "description": "Surface-deployed fabrication printer for manufacturing equipment. Subtypes: industrial (refineries, miners, prospectors, printers) and ship (thrusters, reactors, generators, radiators, ISRU).",
    },
    {
        "id": "constructor",
        "name": "Constructor",
        "kind": "ship_component",
        "description": "Legacy category — superseded by miner and printer. Retained for database backward compatibility.",
    },
    {
        "id": "isru",
        "name": "ISRU",
        "kind": "ship_component",
        "description": "In-Situ Resource Utilization module for dedicated water extraction from surface sites.",
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
    "robot": "prospector",
    "robots": "prospector",
    "drone": "prospector",
    "drones": "prospector",
    "robonaut": "prospector",
    "robonauts": "prospector",
    "prospector": "prospector",
    "prospectors": "prospector",
    "refineries": "refinery",
    "isru_modules": "isru",
    "isru_unit": "isru",
    "isru_units": "isru",
    "water_extractor": "isru",
    "water_extraction": "isru",
    "sifting": "isru",
    "heat_drill": "isru",
    "constructor": "miner",
    "constructors": "miner",
    "builder": "printer",
    "builders": "printer",
    "surface_constructor": "miner",
    "surface_printer": "printer",
    "printer": "printer",
    "printers": "printer",
    "industrial_printer": "printer",
    "ship_printer": "printer",
    "miner": "miner",
    "miners": "miner",
    "large_body_miner": "miner",
    "microgravity_miner": "miner",
    "cryovolatile_miner": "miner",
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
    {"id": "robonauts", "label": "Prospectors"},
    {"id": "constructors", "label": "Constructors"},
    {"id": "miners", "label": "Miners"},
    {"id": "printers", "label": "Printers"},
    {"id": "isru", "label": "ISRU"},
    {"id": "refineries", "label": "Refineries"},
    {"id": "radiators", "label": "Radiators"},
]

THRUSTER_RESERVED_LANES: List[Dict[str, str]] = [
    {"id": "cryo", "label": "Cryo"},
    {"id": "solar", "label": "Solar"},
    {"id": "pulse", "label": "Pulse"},
]
