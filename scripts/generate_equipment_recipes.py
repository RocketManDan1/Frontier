#!/usr/bin/env python3
"""
Generate assembly recipes for all equipment items that don't have recipes yet.

Efficiency scaling:
  T1: ~50%  (total_input = mass / 0.50 = 2.0× mass)
  T2: ~60%  (total_input = mass / 0.60 ≈ 1.667× mass)
  T3: ~70%  (total_input = mass / 0.70 ≈ 1.429× mass)
  T4: ~80%  (total_input = mass / 0.80 = 1.25× mass)

Material tier rules:
  T1: structural_alloys, advanced_ceramics, megastructure_mass (all moon-sourceable)
  T2 refineries: T1 materials only (bootstrapping - no circular dependency)
  T2 other: T1 + T2 finished goods (advanced_aerospace_alloys, semiconductor_materials, carbon_composites)
  T3: T1 + T2 + T3 finished goods (radiation_composite_armor, superconductors)
  T4: T1 + T2 + T3 + strategic_elements, high_grade_reactor_pellets
"""
import json
import os
import math

RECIPES_DIR = os.path.join(os.path.dirname(__file__), "..", "items", "Recipes")

EFFICIENCY = {1: 0.50, 2: 0.60, 3: 0.70, 4: 0.80}

def total_input(mass_t, tier):
    return mass_t / EFFICIENCY[tier]

def r2(x):
    """Round to 2 decimal places."""
    return round(x, 2)

def distribute(total, fracs):
    """
    Distribute `total` mass across materials given fractional weights.
    fracs: list of (item_id, weight)
    Returns list of (item_id, qty) with quantities summing to total (within rounding).
    """
    weight_sum = sum(w for _, w in fracs)
    result = []
    running = 0.0
    for i, (item_id, w) in enumerate(fracs):
        if i == len(fracs) - 1:
            # Last item gets the remainder to ensure exact total
            qty = r2(total - running)
        else:
            qty = r2(total * w / weight_sum)
            running += qty
        if qty > 0:
            result.append((item_id, qty))
    return result

# ── Material mix templates by tier and equipment category ──────────────────────

# T1: All moon-sourceable
MIX_T1_DEFAULT = [
    ("structural_alloys",   0.55),
    ("advanced_ceramics",   0.30),
    ("megastructure_mass",  0.15),
]

# T2 for refineries (bootstrapping: T1 materials ONLY)
MIX_T2_REFINERY = [
    ("structural_alloys",   0.55),
    ("advanced_ceramics",   0.30),
    ("megastructure_mass",  0.15),
]

# T2 for non-refinery equipment
MIX_T2_DEFAULT = [
    ("structural_alloys",         0.38),
    ("advanced_aerospace_alloys", 0.22),
    ("advanced_ceramics",         0.18),
    ("semiconductor_materials",   0.14),
    ("megastructure_mass",        0.08),
]

# T3 default
MIX_T3_DEFAULT = [
    ("advanced_aerospace_alloys",  0.24),
    ("structural_alloys",          0.24),
    ("advanced_ceramics",          0.15),
    ("semiconductor_materials",    0.13),
    ("radiation_composite_armor",  0.12),
    ("carbon_composites",          0.12),
]

# T4 default
MIX_T4_DEFAULT = [
    ("structural_alloys",          0.18),
    ("advanced_aerospace_alloys",  0.18),
    ("semiconductor_materials",    0.13),
    ("advanced_ceramics",          0.13),
    ("superconductors",            0.12),
    ("radiation_composite_armor",  0.10),
    ("carbon_composites",          0.10),
    ("strategic_elements",         0.06),
]

# ── Thematic variations ────────────────────────────────────────────────────────

# Reactors: more ceramics (moderator/reflector), some shielding at higher tiers
MIX_T1_REACTOR = [
    ("structural_alloys",   0.52),
    ("advanced_ceramics",   0.35),
    ("megastructure_mass",  0.13),
]

MIX_T2_REACTOR = [
    ("structural_alloys",         0.34),
    ("advanced_ceramics",         0.26),
    ("advanced_aerospace_alloys", 0.18),
    ("semiconductor_materials",   0.14),
    ("megastructure_mass",        0.08),
]

MIX_T3_REACTOR = [
    ("advanced_aerospace_alloys",  0.26),
    ("radiation_composite_armor",  0.20),
    ("structural_alloys",          0.18),
    ("semiconductor_materials",    0.14),
    ("advanced_ceramics",          0.12),
    ("carbon_composites",          0.10),
]

MIX_T4_REACTOR = [
    ("advanced_aerospace_alloys",  0.22),
    ("radiation_composite_armor",  0.18),
    ("superconductors",            0.15),
    ("semiconductor_materials",    0.14),
    ("structural_alloys",          0.12),
    ("carbon_composites",          0.10),
    ("advanced_ceramics",          0.05),
    ("strategic_elements",         0.04),
]

# Generators: heavier on semiconductors (thermoelectric elements)
MIX_T2_GENERATOR = [
    ("structural_alloys",         0.32),
    ("semiconductor_materials",   0.24),
    ("advanced_ceramics",         0.20),
    ("advanced_aerospace_alloys", 0.16),
    ("megastructure_mass",        0.08),
]

MIX_T3_GENERATOR = [
    ("semiconductor_materials",    0.22),
    ("advanced_aerospace_alloys",  0.22),
    ("structural_alloys",          0.20),
    ("advanced_ceramics",          0.14),
    ("radiation_composite_armor",  0.12),
    ("carbon_composites",          0.10),
]

MIX_T4_GENERATOR = [
    ("semiconductor_materials",    0.18),
    ("advanced_aerospace_alloys",  0.17),
    ("superconductors",            0.16),
    ("structural_alloys",          0.15),
    ("radiation_composite_armor",  0.12),
    ("advanced_ceramics",          0.10),
    ("carbon_composites",          0.07),
    ("strategic_elements",         0.05),
]

# Radiators: T2 heavy on aerospace alloys (titanium), T3-T4 heavy on carbon composites
MIX_T2_RADIATOR = [
    ("advanced_aerospace_alloys", 0.42),
    ("advanced_ceramics",         0.24),
    ("structural_alloys",         0.18),
    ("semiconductor_materials",   0.08),
    ("carbon_composites",         0.08),
]

MIX_T3_RADIATOR = [
    ("carbon_composites",          0.35),
    ("advanced_aerospace_alloys",  0.22),
    ("advanced_ceramics",          0.18),
    ("radiation_composite_armor",  0.13),
    ("semiconductor_materials",    0.07),
    ("structural_alloys",          0.05),
]

MIX_T4_RADIATOR = [
    ("carbon_composites",          0.38),
    ("superconductors",            0.18),
    ("advanced_aerospace_alloys",  0.16),
    ("advanced_ceramics",          0.12),
    ("radiation_composite_armor",  0.08),
    ("structural_alloys",          0.05),
    ("strategic_elements",         0.03),
]

# Robonauts: heavier on electronics/sensors
MIX_T2_ROBONAUT = [
    ("semiconductor_materials",   0.26),
    ("structural_alloys",         0.28),
    ("advanced_aerospace_alloys", 0.20),
    ("advanced_ceramics",         0.18),
    ("megastructure_mass",        0.08),
]

MIX_T3_ROBONAUT = [
    ("semiconductor_materials",    0.22),
    ("advanced_aerospace_alloys",  0.22),
    ("structural_alloys",          0.18),
    ("advanced_ceramics",          0.14),
    ("carbon_composites",          0.12),
    ("radiation_composite_armor",  0.12),
]

MIX_T4_ROBONAUT = [
    ("semiconductor_materials",    0.18),
    ("superconductors",            0.16),
    ("advanced_aerospace_alloys",  0.18),
    ("structural_alloys",          0.14),
    ("advanced_ceramics",          0.10),
    ("radiation_composite_armor",  0.10),
    ("carbon_composites",          0.08),
    ("strategic_elements",         0.06),
]

# ── Build time & power scaling ─────────────────────────────────────────────────

def build_time_s(mass_t, tier, complexity="normal"):
    """
    Calculate build time using mass^0.7 scaling.
    complexity: "normal", "high" (reactors/generators), "low" (radiators, storage)
    """
    base = {"normal": 300, "high": 350, "low": 250}[complexity]
    tier_mult = {1: 1.0, 2: 1.15, 3: 1.3, 4: 1.5}[tier]
    raw = base * tier_mult * (mass_t ** 0.7)
    # Round to nearest 300s, minimum 600s
    rounded = max(600, int(round(raw / 300)) * 300)
    return rounded

def power_kw(mass_t, tier, complexity="normal"):
    """Calculate power draw during assembly."""
    base = {"normal": 50, "high": 65, "low": 40}[complexity]
    tier_mult = {1: 1.0, 2: 1.4, 3: 1.8, 4: 2.2}[tier]
    raw = base * tier_mult * (mass_t ** 0.6)
    # Round to nearest 10kW, minimum 100kW
    rounded = max(100, int(round(raw / 10)) * 10)
    return rounded


def make_recipe(item_id, name, mass_t, tier, mix, category, facility="shipyard",
                complexity="normal"):
    ti = total_input(mass_t, tier)
    inputs_raw = distribute(ti, mix)
    inputs = [{"item_id": iid, "qty": qty} for iid, qty in inputs_raw]

    return {
        "recipe_id": item_id,
        "name": f"Assemble {name}",
        "output_item_id": item_id,
        "output_qty": 1,
        "inputs": inputs,
        "build_time_s": build_time_s(mass_t, tier, complexity),
        "facility_type": facility,
        "refinery_category": category,
        "min_tech_tier": tier,
        "power_kw": power_kw(mass_t, tier, complexity),
        "byproducts": []
    }


def get_mix(tier, equipment_type, is_refinery=False):
    """Get appropriate material mix for the given tier and equipment type."""
    if tier == 1:
        if equipment_type == "reactor":
            return MIX_T1_REACTOR
        return MIX_T1_DEFAULT

    if tier == 2:
        if is_refinery:
            return MIX_T2_REFINERY
        if equipment_type == "reactor":
            return MIX_T2_REACTOR
        if equipment_type == "generator":
            return MIX_T2_GENERATOR
        if equipment_type == "radiator":
            return MIX_T2_RADIATOR
        if equipment_type == "robonaut":
            return MIX_T2_ROBONAUT
        return MIX_T2_DEFAULT

    if tier == 3:
        if equipment_type == "reactor":
            return MIX_T3_REACTOR
        if equipment_type == "generator":
            return MIX_T3_GENERATOR
        if equipment_type == "radiator":
            return MIX_T3_RADIATOR
        if equipment_type == "robonaut":
            return MIX_T3_ROBONAUT
        return MIX_T3_DEFAULT

    # tier == 4
    if equipment_type == "reactor":
        return MIX_T4_REACTOR
    if equipment_type == "generator":
        return MIX_T4_GENERATOR
    if equipment_type == "radiator":
        return MIX_T4_RADIATOR
    if equipment_type == "robonaut":
        return MIX_T4_ROBONAUT
    return MIX_T4_DEFAULT


# ══════════════════════════════════════════════════════════════════════════════
#  EQUIPMENT DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

recipes = []

# ── REACTORS (8 units, 4 tiers × 2) ──────────────────────────────────────────
REACTORS = [
    # (item_id, display_name, mass_t, tier)
    ("rd0410_igrit",        'RD-0410 "Igrit"',       0.52,  1),
    ("kiwi_b4e_bison",      'KIWI-B4E "Bison"',      0.95,  1),
    ("pewee1_sparrow",      'PEWEE-1 "Sparrow"',      0.647, 2),
    ("phoebus1b_titan",     'Phoebus-1B "Titan"',     1.02,  2),
    ("nerva2_aegis",        'NERVA-2 "Aegis"',        1.80,  3),
    ("phoebus2a_colossus",  'Phoebus-2A "Colossus"',  2.27,  3),
    ("gcnr200_alcyone",     'GCNR-200 "Alcyone"',     3.80,  4),
    ("gcnr4000_hyperion",   'GCNR-4000 "Hyperion"',   7.50,  4),
]

for item_id, name, mass, tier in REACTORS:
    mix = get_mix(tier, "reactor")
    recipes.append(make_recipe(item_id, name, mass, tier, mix,
                               category="nuclear_exotic", complexity="high"))

# ── GENERATORS (4 units, 4 tiers) ────────────────────────────────────────────
GENERATORS = [
    ("snap_sige_wren",        'SNAP-SiGe "Wren"',          5.0,   1),
    ("sp100_te_ibis",         'SP-100 TE "Ibis"',          12.0,  2),
    ("sige_segmented_crane",  'SiGe-Segmented "Crane"',    24.5,  3),
    ("advanced_skut_egret",   'ASC-Skutterudite "Egret"',  42.0,  4),
]

for item_id, name, mass, tier in GENERATORS:
    mix = get_mix(tier, "generator")
    recipes.append(make_recipe(item_id, name, mass, tier, mix,
                               category="nuclear_exotic", complexity="high"))

# ── RADIATORS (4 units, 4 tiers) ──────────────────────────────────────────────
RADIATORS = [
    ("lapwing_al_osr",         'Al/OSR Panel "Lapwing"',       3.5,   1),
    ("heron_ti_hec",           'Ti-HEC Panel "Heron"',         8.0,   2),
    ("stork_cc_refractory",    'C/C Refractory "Stork"',       9.0,   3),
    ("albatross_adv_cc",       'Adv. C/C Panel "Albatross"',  10.5,   4),
]

for item_id, name, mass, tier in RADIATORS:
    mix = get_mix(tier, "radiator")
    recipes.append(make_recipe(item_id, name, mass, tier, mix,
                               category="metallurgy", complexity="low"))

# ── REFINERIES (16 units, 4 families × 4 tiers) ──────────────────────────────

REFINERIES_LITHIC = [
    ("rfl_1_sagger",      'RFL-1 "Sagger"',       8.0,    1),
    ("rfl_2_calciner",    'RFL-2 "Calciner"',     22.0,   2),
    ("rfl_3_plasmatron",  'RFL-3 "Plasmatron"',   60.0,   3),
    ("rfl_4_synthesiser", 'RFL-4 "Synthesiser"',  180.0,  4),
]

REFINERIES_METALLURGY = [
    ("rfm_1_bloomery",       'RFM-1 "Bloomery"',        14.0,   1),
    ("rfm_2_induction_forge",'RFM-2 "Induction Forge"',  38.0,  2),
    ("rfm_3_vacuum_arc",     'RFM-3 "Vacuum Arc"',      110.0,  3),
    ("rfm_4_plasma_refinery",'RFM-4 "Plasma Refinery"',  300.0, 4),
]

REFINERIES_VOLATILES = [
    ("rfv_1_electrolyser",  'RFV-1 "Electrolyser"',    6.0,    1),
    ("rfv_2_cryo_separator",'RFV-2 "Cryo Separator"',  16.0,   2),
    ("rfv_3_steam_reformer",'RFV-3 "Steam Reformer"',  50.0,   3),
    ("rfv_4_plasma_cracker",'RFV-4 "Plasma Cracker"',  140.0,  4),
]

REFINERIES_NUCLEAR = [
    ("rfn_1_pelletiser",       'RFN-1 "Pelletiser"',        12.0,   1),
    ("rfn_2_thermal_diffuser", 'RFN-2 "Thermal Diffuser"',  35.0,   2),
    ("rfn_3_centrifuge_cascade",'RFN-3 "Centrifuge Cascade"', 95.0,  3),
    ("rfn_4_ion_plasma_sep",   'RFN-4 "Ion Plasma Sep"',    260.0,  4),
]

# Refinery category mapping
REFINERY_CATEGORIES = {
    "lithic": "lithic_processing",
    "metallurgy": "metallurgy",
    "volatiles": "volatiles_cryogenics",
    "nuclear": "nuclear_exotic",
}

for family_name, family_list in [
    ("lithic", REFINERIES_LITHIC),
    ("metallurgy", REFINERIES_METALLURGY),
    ("volatiles", REFINERIES_VOLATILES),
    ("nuclear", REFINERIES_NUCLEAR),
]:
    for item_id, name, mass, tier in family_list:
        mix = get_mix(tier, "refinery", is_refinery=True)
        recipes.append(make_recipe(item_id, name, mass, tier, mix,
                                   category=REFINERY_CATEGORIES[family_name]))

# ── CONSTRUCTORS (8 units, 4 tiers × 2 a/b variants) ────────────────────────
CONSTRUCTORS = [
    ("gcn_1a_antaeus",    'GCN-1A "Antaeus"',      2.5,    1),
    ("gcn_1b_talpa",      'GCN-1B "Talpa"',        4.8,    1),
    ("gcn_2a_hephaestus", 'GCN-2A "Hephaestus"',  10.0,    2),
    ("gcn_2b_kyklops",    'GCN-2B "Kyklops"',     20.0,    2),
    ("gcn_3a_golem",      'GCN-3A "Golem"',       45.0,    3),
    ("gcn_3b_archon",     'GCN-3B "Archon"',      85.0,    3),
    ("gcn_4a_titan",      'GCN-4A "Titan"',      180.0,    4),
    ("gcn_4b_demiurge",   'GCN-4B "Demiurge"',   350.0,    4),
]

for item_id, name, mass, tier in CONSTRUCTORS:
    mix = get_mix(tier, "constructor")
    recipes.append(make_recipe(item_id, name, mass, tier, mix,
                               category="metallurgy"))

# ── ROBONAUTS (8 units, 4 tiers × 2 a/b variants) ───────────────────────────
ROBONAUTS = [
    ("rbn_1a_argus",   'RBN-1A "Argus"',     1.5,    1),
    ("rbn_1b_cyclops", 'RBN-1B "Cyclops"',   3.2,    1),
    ("rbn_2a_meridian",'RBN-2A "Meridian"',   7.5,    2),
    ("rbn_2b_zenith",  'RBN-2B "Zenith"',    15.0,    2),
    ("rbn_3a_solaris", 'RBN-3A "Solaris"',   48.0,    3),
    ("rbn_3b_helion",  'RBN-3B "Helion"',    95.0,    3),
    ("rbn_4a_quasar",  'RBN-4A "Quasar"',   220.0,    4),
    ("rbn_4b_pulsar",  'RBN-4B "Pulsar"',   420.0,    4),
]

for item_id, name, mass, tier in ROBONAUTS:
    mix = get_mix(tier, "robonaut")
    recipes.append(make_recipe(item_id, name, mass, tier, mix,
                               category="nuclear_exotic"))

# ══════════════════════════════════════════════════════════════════════════════
#  WRITE FILES & VERIFY
# ══════════════════════════════════════════════════════════════════════════════

print(f"Generating {len(recipes)} recipe files in {RECIPES_DIR}\n")
print(f"{'Recipe ID':<32} {'Tier':>4} {'Mass':>7} {'Input':>7} {'Eff%':>6} {'Time':>6} {'kW':>6}")
print("-" * 80)

for recipe in recipes:
    rid = recipe["recipe_id"]
    tier = recipe["min_tech_tier"]
    out_mass = 0
    # Find original mass from our data
    for lst in [REACTORS, GENERATORS, RADIATORS,
                REFINERIES_LITHIC, REFINERIES_METALLURGY,
                REFINERIES_VOLATILES, REFINERIES_NUCLEAR,
                CONSTRUCTORS, ROBONAUTS]:
        for item_id, _, mass, _ in lst:
            if item_id == rid:
                out_mass = mass
                break
        if out_mass:
            break

    total_in = sum(inp["qty"] for inp in recipe["inputs"])
    eff = out_mass / total_in * 100 if total_in > 0 else 0

    print(f"{rid:<32} T{tier:>2}  {out_mass:>6.2f}t {total_in:>6.2f}t {eff:>5.1f}% "
          f"{recipe['build_time_s']:>5}s {recipe['power_kw']:>5}")

    # Write file
    filepath = os.path.join(RECIPES_DIR, f"{rid}.json")
    with open(filepath, "w") as f:
        json.dump(recipe, f, indent=2)
        f.write("\n")

print(f"\nDone! Generated {len(recipes)} recipe files.")

# Verification summary by tier
print("\n── Efficiency Summary by Tier ──")
tier_effs = {}
for recipe in recipes:
    rid = recipe["recipe_id"]
    tier = recipe["min_tech_tier"]
    out_mass = 0
    for lst in [REACTORS, GENERATORS, RADIATORS,
                REFINERIES_LITHIC, REFINERIES_METALLURGY,
                REFINERIES_VOLATILES, REFINERIES_NUCLEAR,
                CONSTRUCTORS, ROBONAUTS]:
        for item_id, _, mass, _ in lst:
            if item_id == rid:
                out_mass = mass
                break
        if out_mass:
            break
    total_in = sum(inp["qty"] for inp in recipe["inputs"])
    eff = out_mass / total_in * 100 if total_in > 0 else 0
    tier_effs.setdefault(tier, []).append(eff)

for tier in sorted(tier_effs):
    effs = tier_effs[tier]
    avg = sum(effs) / len(effs)
    print(f"  T{tier}: {len(effs)} recipes, avg efficiency {avg:.1f}% "
          f"(range {min(effs):.1f}%-{max(effs):.1f}%)")

# Verify T1 moon-sourceability
print("\n── T1 Moon-Sourceability Check ──")
MOON_MATERIALS = {"structural_alloys", "advanced_ceramics", "megastructure_mass",
                   "volatile_propellants_hydrolox", "water"}
for recipe in recipes:
    if recipe["min_tech_tier"] == 1:
        non_moon = [inp["item_id"] for inp in recipe["inputs"]
                    if inp["item_id"] not in MOON_MATERIALS]
        status = "✓ Moon OK" if not non_moon else f"✗ Non-moon: {non_moon}"
        print(f"  {recipe['recipe_id']:<32} {status}")
