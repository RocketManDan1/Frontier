#!/usr/bin/env python3
"""Bulk-update research_node fields in item JSON files for the unified research tree."""
import json
import os

ITEMS_DIR = os.path.join(os.path.dirname(__file__), "..", "items")

# Mapping: item_id → new unified research_node
ITEM_NODE_MAP = {
    # ── Starter Corp (auto-unlocked) ──────────────────────────────
    "scn_1_pioneer":       "starter_corp",
    "rd0410_igrit":        "starter_corp",
    "snap_sige_wren":      "starter_corp",
    "lapwing_al_osr":      "starter_corp",
    "rbn_1a_argus":        "starter_corp",

    # ── Nuclear Fission ───────────────────────────────────────────
    "kiwi_b4e_bison":      "nuclear_fission",
    "scn_2_frontier":      "nuclear_fission",

    # ── Electrical and Thermal Systems ────────────────────────────
    "sp100_te_ibis":       "electrical_thermal_systems",
    "heron_ti_hec":        "electrical_thermal_systems",

    # ── Prospecting and Mining ────────────────────────────────────
    "rbn_1b_cyclops":      "prospecting_and_mining",
    "gcn_1a_antaeus":      "prospecting_and_mining",
    "mgm_1a_phaethon":     "prospecting_and_mining",
    "cvm_1a_chione":       "prospecting_and_mining",

    # ── Interplanetary Industry ───────────────────────────────────
    "rfl_1_sagger":        "interplanetary_industry",
    "rfm_1_bloomery":      "interplanetary_industry",

    # ── In Situ Refueling ─────────────────────────────────────────
    "des_1a_dustwalker":   "in_situ_refueling",
    "hdc_1a_frostbite":    "in_situ_refueling",

    # ── Reactor/Thruster tree ─────────────────────────────────────
    "ascn_1_venture":      "advanced_solid_core_ii",
    "pewee1_sparrow":      "advanced_solid_core_ii",

    "ascn_2_atlas":        "advanced_solid_core_iii",
    "phoebus1b_titan":     "advanced_solid_core_iii",

    "nerva2_aegis":        "high_power_solid_core",

    "phoebus2a_colossus":  "high_power_solid_core_ii",

    "pbr40_cinder":        "pebble_bed",
    "twr45_stormwind":     "pebble_bed",

    "twr75_firebrand":     "advanced_pebble_bed",

    "lcn_1_torrent":       "liquid_core",
    "lars1_crucible":      "liquid_core",

    "lcn_2_cascade":       "advanced_liquid_core",
    "dcr200_maelstrom":    "advanced_liquid_core",

    "ccr1_mirage":         "vapor_core",

    "vcr400_tempest":      "vapor_core_ii",

    "gcnr200_alcyone":     "closed_cycle_gas_core",
    "cgn_1_helios":        "closed_cycle_gas_core",
    "cgn_2_prometheus":    "closed_cycle_gas_core",

    "gcnr4000_hyperion":   "open_cycle_gas_core",
    "ogn_1_icarus":        "open_cycle_gas_core",
    "ogn_2_daedalus":      "open_cycle_gas_core",

    # ── Generator / Radiator tree ─────────────────────────────────
    "sige_segmented_crane":    "solid_state_thermoelectrics",
    "advanced_skut_egret":     "solid_state_thermoelectrics_ii",
    "stork_cc_refractory":     "rigid_panel_radiators",
    "albatross_adv_cc":        "high_temp_rigid_panel_radiators",

    # ── Prospector chain ──────────────────────────────────────────
    "rbn_2a_meridian":     "uv_laser_prospecting",
    "rbn_2b_zenith":       "uv_laser_prospecting",
    "rbn_3a_solaris":      "free_electron_laser_prospecting",
    "rbn_3b_helion":       "free_electron_laser_prospecting",
    "rbn_4a_quasar":       "coherent_gamma_prospecting",
    "rbn_4b_pulsar":       "coherent_gamma_prospecting",

    # ── Constructor / miner chains ────────────────────────────────
    "gcn_1b_talpa":        "early_gravity_mining",
    "gcn_2a_hephaestus":   "laser_gravity_mining",
    "gcn_2b_kyklops":      "laser_gravity_mining",
    "gcn_3a_golem":        "industrial_plasma_gravity_mining",
    "gcn_3b_archon":       "industrial_plasma_gravity_mining",
    "gcn_4a_titan":        "vibrothermal_gravity_mining",
    "gcn_4b_demiurge":     "vibrothermal_gravity_mining",

    "mgm_2a_ixion":        "advanced_microgravity_mining",
    "mgm_3a_sedna":        "plasma_microgravity_mining",
    "mgm_4a_quaoar":       "industrial_microgravity_mining",

    "cvm_2a_boreas":       "advanced_cryovolatile_mining",
    "cvm_3a_themis":       "laser_cryovolatile_mining",

    # ── ISRU chains ───────────────────────────────────────────────
    "des_1b_grainmill":    "deep_efficiency_sifting",
    "des_2a_sandstorm":    "electrostatic_regolith_processing",
    "des_2b_dervish":      "electrostatic_regolith_processing",
    "des_3a_leviathan":    "magnetic_resonance_sifting",
    "des_3b_behemoth":     "magnetic_resonance_sifting",
    "des_4a_colossus":     "plasma_sifting_extraction",
    "des_4b_worldeater":   "plasma_sifting_extraction",

    "hdc_1b_permafrost":   "heat_drill_capture",
    "hdc_2a_borealis":     "microwave_thermal_extraction",
    "hdc_2b_cryovein":     "microwave_thermal_extraction",
    "hdc_3a_geysir":       "plasma_thermal_drilling",
    "hdc_3b_magmaworm":    "plasma_thermal_drilling",
    "hdc_4a_hellfrost":    "fission_pulse_thermal_extraction",
    "hdc_4b_absolutezero": "fission_pulse_thermal_extraction",

    # ── Refinery chains ───────────────────────────────────────────
    "rfl_2_calciner":          "advanced_ceramics_fabrication",
    "rfl_3_plasmatron":        "plasma_synthesis",
    "rfl_4_synthesiser":       "molecular_deposition",
    "rfm_2_induction_forge":   "vacuum_metallurgy",
    "rfm_3_vacuum_arc":        "electron_beam_metallurgy",
    "rfm_4_plasma_refinery":   "plasma_metallurgy",
    "rfn_1_pelletiser":        "basic_nuclear_fuels",
    "rfn_2_thermal_diffuser":  "isotope_separation",
    "rfn_3_centrifuge_cascade":"centrifuge_enrichment",
    "rfn_4_ion_plasma_sep":    "plasma_isotope_separation",
    "rfv_1_electrolyser":      "basic_chemical_processing",
    "rfv_2_cryo_separator":    "cryogenic_separation",
    "rfv_3_steam_reformer":    "catalytic_reforming",
    "rfv_4_plasma_cracker":    "thermochemical_cracking",

    # ── Printer chains ────────────────────────────────────────────
    "spr_1a_lathe":        "early_aerospace_printing",
    "spr_2a_mill":         "advanced_aerospace_printing",
    "spr_3a_press":        "laser_ship_printing",
    "spr_4a_fabricator":   "industrial_plasma_ship_printing",
    "ipr_1a_mold":         "early_industrial_printing",
    "ipr_2a_forge":        "advanced_industrial_printing",
    "ipr_3a_foundry":      "laser_industrial_printing",
    "ipr_4a_colossus":     "industrial_plasma_printing",
}

updated = 0
unchanged = 0
unmapped = 0

for root, _dirs, files in os.walk(ITEMS_DIR):
    for fname in sorted(files):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(root, fname)
        try:
            with open(fpath, "r") as f:
                data = json.load(f)
        except Exception:
            continue

        if not isinstance(data, dict) or "id" not in data:
            continue

        item_id = str(data["id"])
        if item_id in ITEM_NODE_MAP:
            new_node = ITEM_NODE_MAP[item_id]
            old_node = data.get("research_node", "")
            if old_node != new_node:
                data["research_node"] = new_node
                with open(fpath, "w") as f:
                    json.dump(data, f, indent=2)
                    f.write("\n")
                print(f"  UPDATED  {item_id}: {old_node} -> {new_node}")
                updated += 1
            else:
                unchanged += 1
        else:
            rn = data.get("research_node", "")
            if rn:
                print(f"  UNMAPPED {item_id}: research_node={rn}")
                unmapped += 1

print(f"\nDone. Updated={updated}, Unchanged={unchanged}, Unmapped={unmapped}")
