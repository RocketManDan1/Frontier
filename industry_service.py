"""
Industry service — business logic for deployed equipment, production jobs, and mining.

Handles:
  - Deploying/undeploying refineries & constructors from location inventory
  - Starting/cancelling refinery production jobs (recipe-based)
  - Starting/stopping mining jobs (constructor-based, surface sites only)
  - Settling completed jobs (settle-on-access pattern, like ship arrivals)
"""

import json
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

import catalog_service
from sim_service import game_now_s


GLOBAL_REFINERY_RECIPE_CATEGORIES = {"all_refineries"}

_SHIPYARD_OUTPUT_TO_RESEARCH_CATEGORY = {
    "thruster": "thrusters",
    "reactor": "reactors",
    "generator": "generators",
    "radiator": "radiators",
    "prospector": "robonauts",
    "robonaut": "robonauts",
    "constructor": "constructors",
    "miner": "miners",
    "printer": "printers",
    "isru": "isru",
    "refinery": "refineries",
}

# Refineries use subtree-specific research nodes (e.g. refineries_lithic_lvl_1)
# rather than a single refineries_lvl_N path.
_REFINERY_CATEGORY_TO_RESEARCH_PREFIX = {
    "lithic_processing": "refineries_lithic",
    "metallurgy": "refineries_metallurgy",
    "nuclear_exotic": "refineries_nuclear",
    "volatiles_cryogenics": "refineries_volatiles",
}

# ISRU modules use subtree-specific research nodes (isru_sifting_lvl_1, isru_heat_drill_lvl_1)
_ISRU_BRANCH_TO_RESEARCH_PREFIX = {
    "centrifugal_sifting": "isru_sifting",
    "electrostatic_centrifugal": "isru_sifting",
    "magnetic_resonance_separation": "isru_sifting",
    "plasma_assisted_separation": "isru_sifting",
    "resistive_thermal_drill": "isru_heat_drill",
    "microwave_thermal_drill": "isru_heat_drill",
    "plasma_thermal_drill": "isru_heat_drill",
    "fusion_thermal_drill": "isru_heat_drill",
}


# ── Helpers ────────────────────────────────────────────────────────────────────


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def _load_resource_name(resource_id: str) -> str:
    resources = catalog_service.load_resource_catalog()
    res = resources.get(resource_id)
    return str(res.get("name") or resource_id) if res else resource_id


def _is_recipe_compatible_with_refinery_specialization(recipe_category: str, specialization: str) -> bool:
    category = str(recipe_category or "").strip()
    spec = str(specialization or "").strip()
    if not category or category in GLOBAL_REFINERY_RECIPE_CATEGORIES:
        return True
    return bool(spec) and spec == category


# ── Settle Jobs (on-access pattern) ───────────────────────────────────────────


def _is_facility_powered(conn: sqlite3.Connection, facility_id: str) -> bool:
    """Check if a facility has non-negative electric surplus (power_ok).

    Returns True if there are no electric consumers or if supply >= demand.
    Facilities with zero equipment are considered powered (nothing to gate).
    """
    equipment = get_deployed_equipment(conn, "", facility_id=facility_id)
    if not equipment:
        return True
    pb = compute_site_power_balance(equipment)
    return bool(pb.get("power_ok", True))


def settle_industry(conn: sqlite3.Connection, location_id: Optional[str] = None, *, facility_id: Optional[str] = None) -> None:
    """
    Settle all industry systems:
      1. Complete finished production jobs (legacy refinery + refinery-slot jobs)
      2. Settle continuous mining from constructors in 'mine' mode
      3. Auto-start refinery slots with queued recipes
      4. Settle & auto-advance the construction queue
    If facility_id is given, only settle for that facility (preferred).
    If location_id is given, only settle for that location (performance).

    Power enforcement: mining, refinery auto-start, and construction auto-start
    are gated on the facility having non-negative electric surplus.  Already-
    running jobs (inputs consumed) still complete on their timer.
    """
    now = game_now_s()
    _settle_production_jobs(conn, now, location_id, facility_id=facility_id)
    _settle_mining_v2(conn, now, location_id, facility_id=facility_id)
    _settle_refinery_slots(conn, now, location_id, facility_id=facility_id)
    _settle_construction_queue(conn, now, location_id, facility_id=facility_id)


def _settle_production_jobs(conn: sqlite3.Connection, now: float, location_id: Optional[str] = None, *, facility_id: Optional[str] = None) -> None:
    """Complete production jobs whose completes_at <= now.
    Skips jobs owned by refinery slots or the construction queue — those are
    settled by their own dedicated functions."""
    where = "WHERE pj.status = 'active' AND pj.completes_at <= ?"
    params: list = [now]
    if facility_id:
        where += " AND pj.facility_id = ?"
        params.append(facility_id)
    elif location_id:
        where += " AND pj.location_id = ?"
        params.append(location_id)

    rows = conn.execute(
        f"""
        SELECT pj.id, pj.location_id, pj.equipment_id, pj.job_type,
               pj.recipe_id, pj.outputs_json, pj.completes_at, pj.corp_id, pj.facility_id
        FROM production_jobs pj
        {where}
          AND pj.id NOT IN (SELECT current_job_id FROM refinery_slots WHERE current_job_id IS NOT NULL)
          AND pj.id NOT IN (SELECT id FROM construction_queue WHERE status = 'active')
        """,
        params,
    ).fetchall()

    if not rows:
        return

    # Lazy import to avoid circular ref
    import main as _main

    # Build a lookup of all known part catalogs so we can distinguish parts from resources
    part_catalogs = {}
    for loader in (
        catalog_service.load_miner_catalog,
        catalog_service.load_printer_catalog,
        catalog_service.load_refinery_catalog,
        catalog_service.load_thruster_main_catalog,
        catalog_service.load_reactor_catalog,
        catalog_service.load_generator_catalog,
        catalog_service.load_radiator_catalog,
        catalog_service.load_robonaut_catalog,
        catalog_service.load_isru_catalog,
        catalog_service.load_storage_catalog,
    ):
        part_catalogs.update(loader())

    resource_catalog = catalog_service.load_resource_catalog()

    for row in rows:
        job_id = row["id"]
        loc_id = row["location_id"]
        equip_id = row["equipment_id"]
        job_corp_id = str(row["corp_id"] or "") if "corp_id" in row.keys() else ""
        job_fid = str(row["facility_id"] or "") if "facility_id" in row.keys() else ""

        # Deliver outputs to facility inventory
        outputs = json.loads(row["outputs_json"] or "[]")
        for out in outputs:
            item_id = str(out.get("item_id") or "").strip()
            qty = float(out.get("qty") or 0.0)
            if not item_id or qty <= 0:
                continue

            if item_id in part_catalogs:
                part_entry = dict(part_catalogs[item_id])
                _main.add_part_to_location_inventory(conn, loc_id, part_entry, count=qty, corp_id=job_corp_id)
            else:
                _main.add_resource_to_location_inventory(conn, loc_id, item_id, qty, corp_id=job_corp_id)

        # Mark job completed, free equipment
        conn.execute(
            "UPDATE production_jobs SET status = 'completed', completed_at = ? WHERE id = ?",
            (now, job_id),
        )
        conn.execute(
            "UPDATE deployed_equipment SET status = 'idle' WHERE id = ?",
            (equip_id,),
        )

    conn.commit()


def _settle_mining_v2(conn: sqlite3.Connection, now: float, location_id: Optional[str] = None, *, facility_id: Optional[str] = None) -> None:
    """
    Mining v2: constructors in 'mine' mode automatically mine at their full rate.
    Output is split proportionally among all site resource distributions.
    Uses deployed_equipment.mode = 'mine' instead of production_jobs.
    Tracks last_settled in config_json of the equipment.
    """
    where = "WHERE de.category IN ('miner', 'constructor', 'isru') AND de.mode = 'mine'"
    params: list = []
    if facility_id:
        where += " AND de.facility_id = ?"
        params.append(facility_id)
    elif location_id:
        where += " AND de.location_id = ?"
        params.append(location_id)

    miners = conn.execute(
        f"""
        SELECT de.id, de.location_id, de.config_json, de.corp_id, de.category, de.facility_id
        FROM deployed_equipment de
        {where}
        """,
        params,
    ).fetchall()

    if not miners:
        return

    import main as _main

    # Group miners by location for efficiency
    by_location: Dict[str, list] = {}
    for m in miners:
        loc_id = m["location_id"]
        by_location.setdefault(loc_id, []).append(m)

    # Pre-compute power status per facility to avoid repeated queries
    _power_cache: Dict[str, bool] = {}

    for loc_id, loc_miners in by_location.items():
        # Get site resource distribution
        site_resources = conn.execute(
            "SELECT resource_id, mass_fraction FROM surface_site_resources WHERE site_location_id = ?",
            (loc_id,),
        ).fetchall()
        if not site_resources:
            continue

        for miner in loc_miners:
            config = json.loads(miner["config_json"] or "{}")
            rate_kg_hr = float(config.get("mining_rate_kg_per_hr") or 0.0)
            if rate_kg_hr <= 0:
                continue

            corp_id = str(miner["corp_id"] or "")
            miner_fid = str(miner["facility_id"] or "") if "facility_id" in miner.keys() else ""
            last_settled = float(config.get("mining_last_settled") or now)
            elapsed_s = max(0.0, now - last_settled)
            elapsed_hr = elapsed_s / 3600.0
            total_mined_kg = rate_kg_hr * elapsed_hr

            if total_mined_kg < 0.01:
                continue

            # ── Power gate: skip output if facility is unpowered ──
            if miner_fid:
                if miner_fid not in _power_cache:
                    _power_cache[miner_fid] = _is_facility_powered(conn, miner_fid)
                if not _power_cache[miner_fid]:
                    # Still advance last_settled so no backlog accumulates
                    config["mining_last_settled"] = now
                    conn.execute(
                        "UPDATE deployed_equipment SET config_json = ? WHERE id = ?",
                        (_json_dumps(config), miner["id"]),
                    )
                    continue

            if miner["category"] == "isru":
                # ISRU: flat-rate water extraction — rate is NOT multiplied by ice fraction.
                # The ice fraction only gates deployment eligibility; output is the rated water_extraction_kg_per_hr.
                water_rate = float(config.get("water_extraction_kg_per_hr") or 0.0)
                if water_rate <= 0:
                    continue
                last_settled_isru = float(config.get("mining_last_settled") or now)
                elapsed_s_isru = max(0.0, now - last_settled_isru)
                elapsed_hr_isru = elapsed_s_isru / 3600.0
                water_kg = water_rate * elapsed_hr_isru
                if water_kg > 0.01:
                    output_resource_id = str(config.get("mining_output_resource_id") or "water")
                    _main.add_resource_to_location_inventory(conn, loc_id, output_resource_id, water_kg, corp_id=corp_id)
                # Update tracking (use water_kg as total_mined for ISRU)
                prev_total = float(config.get("mining_total_mined_kg") or 0.0)
                config["mining_last_settled"] = now
                config["mining_total_mined_kg"] = prev_total + water_kg
                conn.execute(
                    "UPDATE deployed_equipment SET config_json = ? WHERE id = ?",
                    (_json_dumps(config), miner["id"]),
                )
                continue
            else:
                # Split output by resource mass fractions
                for sr in site_resources:
                    res_id = sr["resource_id"]
                    fraction = float(sr["mass_fraction"])
                    mined_kg = total_mined_kg * fraction
                    if mined_kg > 0.01:
                        _main.add_resource_to_location_inventory(conn, loc_id, res_id, mined_kg, corp_id=corp_id)

            # Update last_settled and total mined tracking
            prev_total = float(config.get("mining_total_mined_kg") or 0.0)
            config["mining_last_settled"] = now
            config["mining_total_mined_kg"] = prev_total + total_mined_kg
            conn.execute(
                "UPDATE deployed_equipment SET config_json = ? WHERE id = ?",
                (_json_dumps(config), miner["id"]),
            )

    conn.commit()


def _settle_refinery_slots(conn: sqlite3.Connection, now: float, location_id: Optional[str] = None, *, facility_id: Optional[str] = None) -> None:
    """
    Refinery slot settle logic:
    1. Complete any finished refinery-slot jobs → deliver outputs, mark slot idle.
    2. For idle slots with assigned recipes, check inputs → auto-start if available.
    Slots are processed in priority order (lower priority number = higher priority).
    """
    import main as _main

    # Step 0: Recover stuck slots (active but no job reference)
    if facility_id:
        where_loc_bare = "AND facility_id = ?"
        where_loc = "AND rs.facility_id = ?"
        params_loc: list = [facility_id]
    elif location_id:
        where_loc_bare = "AND location_id = ?"
        where_loc = "AND rs.location_id = ?"
        params_loc: list = [location_id]
    else:
        where_loc_bare = ""
        where_loc = ""
        params_loc: list = []

    conn.execute(
        f"""
        UPDATE refinery_slots SET status = 'idle', current_job_id = NULL
        WHERE status = 'active'
          AND (current_job_id IS NULL
               OR current_job_id NOT IN (SELECT id FROM production_jobs WHERE status = 'active'))
        {where_loc_bare}
        """,
        params_loc,
    )

    # Step 1: Complete finished slot jobs
    active_slots = conn.execute(
        f"""
        SELECT rs.id AS slot_id, rs.equipment_id, rs.location_id, rs.recipe_id,
               rs.current_job_id, rs.corp_id, rs.facility_id,
               pj.id AS job_id, pj.completes_at, pj.outputs_json
        FROM refinery_slots rs
        JOIN production_jobs pj ON pj.id = rs.current_job_id
        WHERE rs.status = 'active' AND pj.status = 'active' AND pj.completes_at <= ?
        {where_loc}
        """,
        [now] + params_loc,
    ).fetchall()

    # Build part catalog lookup for output delivery
    part_catalogs = {}
    resource_catalog = catalog_service.load_resource_catalog()
    if active_slots:
        for loader in (
            catalog_service.load_miner_catalog,
            catalog_service.load_printer_catalog,
            catalog_service.load_refinery_catalog,
            catalog_service.load_thruster_main_catalog,
            catalog_service.load_reactor_catalog,
            catalog_service.load_generator_catalog,
            catalog_service.load_radiator_catalog,
            catalog_service.load_robonaut_catalog,
            catalog_service.load_isru_catalog,
            catalog_service.load_storage_catalog,
        ):
            part_catalogs.update(loader())

    for slot in active_slots:
        # Deliver outputs
        outputs = json.loads(slot["outputs_json"] or "[]")
        slot_corp_id = str(slot["corp_id"] or "")
        slot_fid = str(slot["facility_id"] or "") if "facility_id" in slot.keys() else ""
        loc = slot["location_id"]
        for out in outputs:
            item_id = str(out.get("item_id") or "").strip()
            qty = float(out.get("qty") or 0.0)
            if not item_id or qty <= 0:
                continue
            if item_id in part_catalogs:
                part_entry = dict(part_catalogs[item_id])
                _main.add_part_to_location_inventory(conn, loc, part_entry, count=qty, corp_id=slot_corp_id)
            else:
                _main.add_resource_to_location_inventory(conn, loc, item_id, qty, corp_id=slot_corp_id)

        # Calculate total primary output qty for cumulative tracking
        primary_output_qty = 0.0
        for out in outputs:
            primary_output_qty += float(out.get("qty") or 0.0)

        # Mark job completed
        conn.execute("UPDATE production_jobs SET status = 'completed', completed_at = ? WHERE id = ?", (now, slot["job_id"]))
        # Mark slot idle, increment cumulative output
        conn.execute(
            "UPDATE refinery_slots SET status = 'idle', current_job_id = NULL, cumulative_output_qty = cumulative_output_qty + ? WHERE id = ?",
            (primary_output_qty, slot["slot_id"]),
        )

    # Step 2: Auto-start idle slots with recipes (in priority order)
    idle_slots = conn.execute(
        f"""
        SELECT rs.id AS slot_id, rs.equipment_id, rs.location_id, rs.recipe_id, rs.corp_id, rs.facility_id
        FROM refinery_slots rs
        WHERE rs.status = 'idle' AND rs.recipe_id IS NOT NULL AND rs.recipe_id != ''
        {where_loc}
        ORDER BY rs.priority ASC, rs.slot_index ASC
        """,
        params_loc,
    ).fetchall()

    all_recipes = catalog_service.load_recipe_catalog()

    # Power cache for facilities encountered during this settle pass
    _refinery_power_cache: Dict[str, bool] = {}

    for slot in idle_slots:
        recipe = all_recipes.get(slot["recipe_id"])
        if not recipe:
            continue

        loc = slot["location_id"]
        equip_id = slot["equipment_id"]
        corp_id = str(slot["corp_id"] or "")
        slot_fid = str(slot["facility_id"] or "") if "facility_id" in slot.keys() else ""

        # ── Power gate: don't auto-start if facility is unpowered ──
        if slot_fid:
            if slot_fid not in _refinery_power_cache:
                _refinery_power_cache[slot_fid] = _is_facility_powered(conn, slot_fid)
            if not _refinery_power_cache[slot_fid]:
                continue

        # Get equipment config for throughput
        equip = conn.execute(
            "SELECT config_json, status FROM deployed_equipment WHERE id = ?",
            (equip_id,),
        ).fetchone()
        if not equip:
            continue
        config = json.loads(equip["config_json"] or "{}")

        # Check if inputs are available (location-scoped)
        inputs = recipe.get("inputs") or []
        can_start = True
        for inp in inputs:
            inp_id = str(inp.get("item_id") or "").strip()
            inp_qty = float(inp.get("qty") or 0.0)
            if not inp_id or inp_qty <= 0:
                continue
            row = conn.execute(
                "SELECT quantity FROM location_inventory_stacks WHERE location_id = ? AND corp_id = ? AND stack_type = 'resource' AND stack_key = ?",
                (loc, corp_id, inp_id),
            ).fetchone()
            available = float(row["quantity"]) if row else 0.0
            if available < inp_qty - 1e-9:
                can_start = False
                break

        if not can_start:
            continue

        # Consume inputs
        for inp in inputs:
            inp_id = str(inp.get("item_id") or "").strip()
            inp_qty = float(inp.get("qty") or 0.0)
            if not inp_id or inp_qty <= 0:
                continue
            resources = catalog_service.load_resource_catalog()
            res_info = resources.get(inp_id) or {}
            density = max(0.0, float(res_info.get("mass_per_m3_kg") or 0.0))
            volume = (inp_qty / density) if density > 0.0 else 0.0
            _main._upsert_inventory_stack(
                conn, location_id=loc, stack_type="resource", stack_key=inp_id, item_id=inp_id,
                name=str(res_info.get("name") or inp_id),
                quantity_delta=-inp_qty, mass_delta_kg=-inp_qty, volume_delta_m3=-volume,
                payload_json=_json_dumps({"resource_id": inp_id}), corp_id=corp_id,
            )

        # Calculate completion time
        throughput_mult = max(0.01, float(config.get("throughput_mult") or 1.0))
        efficiency = max(0.0, float(config.get("efficiency") or 1.0))
        base_time = float(recipe.get("build_time_s") or 600)
        actual_time = base_time / throughput_mult
        completes_at = now + actual_time

        # Build outputs
        outputs_list = []
        output_item_id = str(recipe.get("output_item_id") or "").strip()
        output_qty = float(recipe.get("output_qty") or 0.0)
        if output_item_id and output_qty > 0:
            outputs_list.append({"item_id": output_item_id, "qty": output_qty * efficiency})
        for bp in (recipe.get("byproducts") or []):
            bp_id = str(bp.get("item_id") or "").strip()
            bp_qty = float(bp.get("qty") or 0.0)
            if bp_id and bp_qty > 0:
                outputs_list.append({"item_id": bp_id, "qty": bp_qty * efficiency})

        job_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO production_jobs
              (id, location_id, equipment_id, job_type, recipe_id, status,
               started_at, completes_at, inputs_json, outputs_json, created_by, corp_id, facility_id)
            VALUES (?, ?, ?, 'refine', ?, 'active', ?, ?, ?, ?, 'system', ?, ?)
            """,
            (job_id, loc, equip_id, slot["recipe_id"], now, completes_at,
             _json_dumps([{"item_id": inp["item_id"], "qty": float(inp.get("qty") or 0)} for inp in inputs if inp.get("item_id")]),
             _json_dumps(outputs_list), corp_id, slot_fid),
        )

        # Mark slot active
        conn.execute("UPDATE refinery_slots SET status = 'active', current_job_id = ? WHERE id = ?", (job_id, slot["slot_id"]))

    conn.commit()


def _settle_construction_queue(conn: sqlite3.Connection, now: float, location_id: Optional[str] = None, *, facility_id: Optional[str] = None) -> None:
    """
    Construction queue settle logic:
    1. Complete any finished construction queue items → deliver outputs.
    2. For the next queued item, compute pooled build speed and auto-start if materials ready.
    """
    import main as _main

    if facility_id:
        where_loc = "AND cq.facility_id = ?"
        params_loc: list = [facility_id]
    elif location_id:
        where_loc = "AND cq.location_id = ?"
        params_loc: list = [location_id]
    else:
        where_loc = ""
        params_loc: list = []

    # Step 1: Complete finished active items
    finished = conn.execute(
        f"""
        SELECT cq.id, cq.location_id, cq.recipe_id, cq.outputs_json, cq.corp_id, cq.facility_id
        FROM construction_queue cq
        WHERE cq.status = 'active' AND cq.completes_at <= ?
        {where_loc}
        """,
        [now] + params_loc,
    ).fetchall()

    part_catalogs = {}
    if finished:
        for loader in (
            catalog_service.load_miner_catalog,
            catalog_service.load_printer_catalog,
            catalog_service.load_refinery_catalog,
            catalog_service.load_thruster_main_catalog,
            catalog_service.load_reactor_catalog,
            catalog_service.load_generator_catalog,
            catalog_service.load_radiator_catalog,
            catalog_service.load_robonaut_catalog,
            catalog_service.load_isru_catalog,
            catalog_service.load_storage_catalog,
        ):
            part_catalogs.update(loader())

    for item in finished:
        outputs = json.loads(item["outputs_json"] or "[]")
        item_corp_id = str(item["corp_id"] or "")
        item_fid = str(item["facility_id"] or "") if "facility_id" in item.keys() else ""
        loc = item["location_id"]
        for out in outputs:
            out_id = str(out.get("item_id") or "").strip()
            qty = float(out.get("qty") or 0.0)
            if not out_id or qty <= 0:
                continue
            if out_id in part_catalogs:
                _main.add_part_to_location_inventory(conn, loc, dict(part_catalogs[out_id]), count=qty, corp_id=item_corp_id)
            else:
                _main.add_resource_to_location_inventory(conn, loc, out_id, qty, corp_id=item_corp_id)

        conn.execute("UPDATE construction_queue SET status = 'completed', completed_at = ? WHERE id = ?", (now, item["id"]))

    # Step 2: Auto-start next queued items (one per facility at a time)
    # Get facilities (or locations) with queued items
    facilities_with_queue = conn.execute(
        f"""
        SELECT DISTINCT cq.facility_id, cq.location_id
        FROM construction_queue cq
        WHERE cq.status = 'queued'
        {where_loc}
        """,
        params_loc,
    ).fetchall()

    all_recipes = catalog_service.load_recipe_catalog()

    # Power cache for construction queue facilities
    _cq_power_cache: Dict[str, bool] = {}

    for fq_row in facilities_with_queue:
        fq_fid = str(fq_row["facility_id"] or "") if "facility_id" in fq_row.keys() else ""
        loc = fq_row["location_id"]

        # ── Power gate: don't auto-start construction if facility is unpowered ──
        if fq_fid:
            if fq_fid not in _cq_power_cache:
                _cq_power_cache[fq_fid] = _is_facility_powered(conn, fq_fid)
            if not _cq_power_cache[fq_fid]:
                continue

        # Check if there's already an active job at this facility
        if fq_fid:
            active = conn.execute(
                "SELECT COUNT(*) as cnt FROM construction_queue WHERE facility_id = ? AND status = 'active'",
                (fq_fid,),
            ).fetchone()
        else:
            active = conn.execute(
                "SELECT COUNT(*) as cnt FROM construction_queue WHERE location_id = ? AND status = 'active'",
                (loc,),
            ).fetchone()
        if active and active["cnt"] > 0:
            continue

        # Get pooled construction speed (facility-scoped)
        pool_speed = _get_construction_pool_speed(conn, loc, facility_id=fq_fid)
        if pool_speed <= 0:
            continue

        # Get next queued item
        if fq_fid:
            next_item = conn.execute(
                "SELECT * FROM construction_queue WHERE facility_id = ? AND status = 'queued' ORDER BY queue_order ASC LIMIT 1",
                (fq_fid,),
            ).fetchone()
        else:
            next_item = conn.execute(
                "SELECT * FROM construction_queue WHERE location_id = ? AND status = 'queued' ORDER BY queue_order ASC LIMIT 1",
                (loc,),
            ).fetchone()
        if not next_item:
            continue

        recipe = all_recipes.get(next_item["recipe_id"])
        if not recipe:
            conn.execute("UPDATE construction_queue SET status = 'cancelled' WHERE id = ?", (next_item["id"],))
            continue

        corp_id = str(next_item["corp_id"] or "")
        next_fid = str(next_item["facility_id"] or "") if "facility_id" in next_item.keys() else fq_fid

        # Check inputs (facility-scoped)
        inputs = recipe.get("inputs") or []
        can_start = True
        for inp in inputs:
            inp_id = str(inp.get("item_id") or "").strip()
            inp_qty = float(inp.get("qty") or 0.0)
            if not inp_id or inp_qty <= 0:
                continue
            row = conn.execute(
                "SELECT quantity FROM location_inventory_stacks WHERE location_id = ? AND corp_id = ? AND stack_type = 'resource' AND stack_key = ?",
                (loc, corp_id, inp_id),
            ).fetchone()
            available = float(row["quantity"]) if row else 0.0
            if available < inp_qty - 1e-9:
                can_start = False
                break

        if not can_start:
            continue

        # Consume inputs
        for inp in inputs:
            inp_id = str(inp.get("item_id") or "").strip()
            inp_qty = float(inp.get("qty") or 0.0)
            if not inp_id or inp_qty <= 0:
                continue
            resources = catalog_service.load_resource_catalog()
            res_info = resources.get(inp_id) or {}
            density = max(0.0, float(res_info.get("mass_per_m3_kg") or 0.0))
            volume = (inp_qty / density) if density > 0.0 else 0.0
            _main._upsert_inventory_stack(
                conn, location_id=loc, stack_type="resource", stack_key=inp_id, item_id=inp_id,
                name=str(res_info.get("name") or inp_id),
                quantity_delta=-inp_qty, mass_delta_kg=-inp_qty, volume_delta_m3=-volume,
                payload_json=_json_dumps({"resource_id": inp_id}), corp_id=corp_id,
            )

        # Calculate build time using pooled speed
        base_time = float(recipe.get("build_time_s") or 600)
        throughput_mult = pool_speed / 50.0  # Normalize around 50 kg/hr baseline
        actual_time = base_time / max(0.01, throughput_mult)
        completes_at = now + actual_time

        # Build outputs
        outputs_list = []
        output_item_id = str(recipe.get("output_item_id") or "").strip()
        output_qty = float(recipe.get("output_qty") or 0.0)
        if output_item_id and output_qty > 0:
            outputs_list.append({"item_id": output_item_id, "qty": output_qty})
        for bp in (recipe.get("byproducts") or []):
            bp_id = str(bp.get("item_id") or "").strip()
            bp_qty = float(bp.get("qty") or 0.0)
            if bp_id and bp_qty > 0:
                outputs_list.append({"item_id": bp_id, "qty": bp_qty})

        conn.execute(
            """
            UPDATE construction_queue
            SET status = 'active', started_at = ?, completes_at = ?,
                inputs_json = ?, outputs_json = ?
            WHERE id = ?
            """,
            (now, completes_at,
             _json_dumps([{"item_id": inp["item_id"], "qty": float(inp.get("qty") or 0)} for inp in inputs if inp.get("item_id")]),
             _json_dumps(outputs_list),
             next_item["id"]),
        )

    conn.commit()


def _get_construction_pool_speed(conn: sqlite3.Connection, location_id: str, *, facility_id: str = "") -> float:
    """Sum of construction_rate_kg_per_hr from all printers (or legacy constructors) in 'construct' mode."""
    cat_clause = "category IN ('printer', 'constructor')"
    if facility_id:
        rows = conn.execute(
            f"SELECT config_json FROM deployed_equipment WHERE facility_id = ? AND {cat_clause} AND mode = 'construct'",
            (facility_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT config_json FROM deployed_equipment WHERE location_id = ? AND {cat_clause} AND mode = 'construct'",
            (location_id,),
        ).fetchall()
    total = 0.0
    for r in rows:
        config = json.loads(r["config_json"] or "{}")
        total += float(config.get("construction_rate_kg_per_hr") or 0.0)
    return total

    conn.commit()


# ── Deploy / Undeploy ──────────────────────────────────────────────────────────


DEPLOYABLE_CATEGORIES = ("refinery", "miner", "printer", "constructor", "prospector", "robonaut", "isru", "reactor", "generator", "radiator")


def _resolve_deployable_catalog_entry(item_id: str) -> Optional[Dict[str, Any]]:
    """Look up an item across all deployable catalogs."""
    for loader in (
        catalog_service.load_refinery_catalog,
        catalog_service.load_miner_catalog,
        catalog_service.load_printer_catalog,
        catalog_service.load_robonaut_catalog,
        catalog_service.load_isru_catalog,
        catalog_service.load_reactor_catalog,
        catalog_service.load_generator_catalog,
        catalog_service.load_radiator_catalog,
    ):
        cat = loader()
        if item_id in cat:
            return dict(cat[item_id])
    return None


def deploy_equipment(
    conn: sqlite3.Connection,
    location_id: str,
    item_id: str,
    username: str,
    corp_id: str = "",
    facility_id: str = "",
) -> Dict[str, Any]:
    """
    Deploy equipment from location inventory to the site.
    Supports refineries, constructors, reactors, generators, and radiators.
    Consumes the part from inventory and creates a deployed_equipment row.
    """
    # Validate location exists before checking inventory
    loc = conn.execute("SELECT id FROM locations WHERE id = ?", (location_id,)).fetchone()
    if not loc:
        raise ValueError(f"Location '{location_id}' not found")

    catalog_entry = _resolve_deployable_catalog_entry(item_id)
    if not catalog_entry:
        raise ValueError(f"Item '{item_id}' is not deployable equipment")

    category = str(catalog_entry.get("category_id") or catalog_entry.get("type") or "")
    if category not in DEPLOYABLE_CATEGORIES:
        raise ValueError(f"Item '{item_id}' is not deployable (category: {category})")

    # Miners, printers, prospectors, and ISRU require surface deployment.
    if category in ("miner", "printer", "constructor", "prospector", "robonaut", "isru"):
        site = conn.execute(
            "SELECT gravity_m_s2 FROM surface_sites WHERE location_id = ?",
            (location_id,),
        ).fetchone()
        if not site:
            if category == "miner":
                raise ValueError("Miners can only be deployed at surface sites")
            elif category == "printer":
                raise ValueError("Printers can only be deployed at surface sites")
            elif category == "constructor":
                raise ValueError("Constructors can only be deployed at surface sites")
            elif category == "isru":
                raise ValueError("ISRU modules can only be deployed at surface sites")
            raise ValueError("Prospectors can only be deployed at surface sites")

        site_grav = float(site["gravity_m_s2"])

        if category == "miner":
            miner_type = str(catalog_entry.get("miner_type") or "large_body")
            if miner_type == "large_body":
                min_grav = float(catalog_entry.get("min_surface_gravity_ms2") or 0.0)
                if site_grav < min_grav:
                    raise ValueError(
                        f"Large-body miner requires surface gravity >= {min_grav:.2f} m/s²; "
                        f"site has {site_grav:.2f} m/s²"
                    )
            elif miner_type == "microgravity":
                max_grav = float(catalog_entry.get("max_surface_gravity_ms2") or 1.0)
                if max_grav <= 0.0:
                    max_grav = 1.0
                if site_grav >= max_grav:
                    raise ValueError(
                        f"Microgravity miner requires surface gravity < {max_grav:.2f} m/s²; "
                        f"site has {site_grav:.2f} m/s²"
                    )
            elif miner_type == "cryovolatile":
                # Check that volatile + water ice mass fraction > 50%
                _VOLATILE_RESOURCE_IDS = {"water_ice", "carbon_volatiles", "nitrogen_volatiles"}
                vol_rows = conn.execute(
                    "SELECT resource_id, mass_fraction FROM surface_site_resources WHERE site_location_id = ?",
                    (location_id,),
                ).fetchall()
                volatile_fraction = sum(
                    float(r["mass_fraction"]) for r in vol_rows
                    if r["resource_id"] in _VOLATILE_RESOURCE_IDS
                )
                min_vol = float(catalog_entry.get("min_volatile_mass_fraction") or 0.4)
                if volatile_fraction < min_vol:
                    raise ValueError(
                        f"Cryovolatile miner requires site volatile mass fraction >= {min_vol:.0%}; "
                        f"site has {volatile_fraction:.0%} (water_ice + carbon/nitrogen volatiles)"
                    )

        elif category == "constructor":
            # Legacy: keep gravity check for backward-compat deployed items
            min_grav = float(catalog_entry.get("min_surface_gravity_ms2") or 0.0)
            if min_grav > 0.0 and site_grav < min_grav:
                raise ValueError(
                    f"Surface gravity {site_grav:.2f} m/s² is below minimum {min_grav:.2f} m/s²"
                )

        elif category == "isru":
            # Validate water ice fraction is within the ISRU module's operating range
            ice_row = conn.execute(
                "SELECT mass_fraction FROM surface_site_resources WHERE site_location_id = ? AND resource_id = 'water_ice'",
                (location_id,),
            ).fetchone()
            site_ice = float(ice_row["mass_fraction"]) if ice_row else 0.0
            min_ice = float(catalog_entry.get("min_water_ice_fraction") or 0.0)
            max_ice = float(catalog_entry.get("max_water_ice_fraction") or 1.0)
            if site_ice < min_ice:
                raise ValueError(
                    f"Site water ice fraction {site_ice:.1%} is below minimum {min_ice:.1%} for this ISRU module"
                )
            if site_ice > max_ice:
                raise ValueError(
                    f"Site water ice fraction {site_ice:.1%} is above maximum {max_ice:.1%} for this ISRU module"
                )

    # Consume part from location inventory
    import main as _main

    consumed = _main.consume_parts_from_location_inventory(conn, location_id, [item_id], corp_id=corp_id)
    if not consumed:
        raise ValueError(f"No '{catalog_entry.get('name', item_id)}' found in location inventory")

    # Build config from catalog data
    config: Dict[str, Any] = {
        "mass_kg": catalog_entry.get("mass_kg", 0),
        "electric_mw": catalog_entry.get("electric_mw", 0),
    }
    if category == "refinery":
        config.update({
            "specialization": catalog_entry.get("specialization", ""),
            "throughput_mult": catalog_entry.get("throughput_mult", 1.0),
            "efficiency": catalog_entry.get("efficiency", 1.0),
            "max_recipe_tier": catalog_entry.get("max_recipe_tier", 1),
            "max_concurrent_recipes": catalog_entry.get("max_concurrent_recipes", 1),
        })
    elif category == "miner":
        config.update({
            "miner_type": catalog_entry.get("miner_type", "large_body"),
            "mining_rate_kg_per_hr": catalog_entry.get("mining_rate_kg_per_hr", 0),
            "excavation_type": catalog_entry.get("excavation_type", ""),
            "operational_environment": catalog_entry.get("operational_environment", "surface_gravity"),
            "min_surface_gravity_ms2": catalog_entry.get("min_surface_gravity_ms2", 0.0),
            "max_surface_gravity_ms2": catalog_entry.get("max_surface_gravity_ms2", 0.0),
            "min_volatile_mass_fraction": catalog_entry.get("min_volatile_mass_fraction", 0.0),
        })
    elif category == "printer":
        config.update({
            "printer_type": catalog_entry.get("printer_type", "industrial"),
            "construction_rate_kg_per_hr": catalog_entry.get("construction_rate_kg_per_hr", 0),
            "fabrication_type": catalog_entry.get("fabrication_type", ""),
        })
    elif category == "constructor":
        # Legacy constructor — keep both fields for backward compat
        config.update({
            "mining_rate_kg_per_hr": catalog_entry.get("mining_rate_kg_per_hr", 0),
            "construction_rate_kg_per_hr": catalog_entry.get("construction_rate_kg_per_hr", 0),
            "excavation_type": catalog_entry.get("excavation_type", ""),
        })
    elif category in ("robonaut", "prospector"):
        config.update({
            "prospect_range_km": catalog_entry.get("prospect_range_km", 0),
            "scan_rate_km2_per_hr": catalog_entry.get("scan_rate_km2_per_hr", 0),
            "emission_type": catalog_entry.get("emission_type", ""),
        })
    elif category == "isru":
        config.update({
            "water_extraction_kg_per_hr": catalog_entry.get("water_extraction_kg_per_hr", 0),
            "extraction_method": catalog_entry.get("extraction_method", ""),
            "min_water_ice_fraction": catalog_entry.get("min_water_ice_fraction", 0.0),
            "max_water_ice_fraction": catalog_entry.get("max_water_ice_fraction", 1.0),
            "mining_output_resource_id": "water",
        })
    elif category == "reactor":
        config.update({
            "thermal_mw": catalog_entry.get("thermal_mw", 0),
        })
    elif category == "generator":
        config.update({
            "thermal_mw_input": catalog_entry.get("thermal_mw_input", 0),
            "electric_mw": catalog_entry.get("electric_mw", 0),
            "conversion_efficiency": catalog_entry.get("conversion_efficiency", 0),
            "waste_heat_mw": catalog_entry.get("waste_heat_mw", 0),
        })
    elif category == "radiator":
        config.update({
            "heat_rejection_mw": catalog_entry.get("heat_rejection_mw", 0),
            "operating_temp_k": catalog_entry.get("operating_temp_k", 0),
        })

    equip_id = str(uuid.uuid4())
    now = game_now_s()

    conn.execute(
        """
        INSERT INTO deployed_equipment
          (id, location_id, item_id, name, category, deployed_at, deployed_by, status, config_json, corp_id, mode, facility_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?, 'idle', ?)
        """,
        (equip_id, location_id, item_id, catalog_entry.get("name", item_id),
         category, now, username, _json_dumps(config), corp_id, facility_id),
    )

    # Create refinery slots if this is a refinery
    if category == "refinery":
        max_slots = int(config.get("max_concurrent_recipes") or 1)
        # Get current max priority at this facility for ordering
        if facility_id:
            max_pri = conn.execute(
                "SELECT COALESCE(MAX(priority), -1) as mp FROM refinery_slots WHERE facility_id = ?",
                (facility_id,),
            ).fetchone()
        else:
            max_pri = conn.execute(
                "SELECT COALESCE(MAX(priority), -1) as mp FROM refinery_slots WHERE location_id = ?",
                (location_id,),
            ).fetchone()
        base_priority = (max_pri["mp"] if max_pri else -1) + 1
        for i in range(max_slots):
            slot_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO refinery_slots (id, equipment_id, location_id, slot_index, priority, corp_id, facility_id) VALUES (?,?,?,?,?,?,?)",
                (slot_id, equip_id, location_id, i, base_priority + i, corp_id, facility_id),
            )

    conn.commit()

    return {
        "id": equip_id,
        "location_id": location_id,
        "facility_id": facility_id,
        "item_id": item_id,
        "name": catalog_entry.get("name", item_id),
        "category": category,
        "status": "idle",
        "config": config,
    }


def undeploy_equipment(
    conn: sqlite3.Connection,
    equipment_id: str,
    username: str,
    *,
    corp_id: str = "",
) -> Dict[str, Any]:
    """
    Undeploy equipment — returns it to location inventory as a part.
    Cannot undeploy while equipment has active jobs.
    """
    equip = conn.execute(
        "SELECT * FROM deployed_equipment WHERE id = ?",
        (equipment_id,),
    ).fetchone()
    if not equip:
        raise ValueError("Equipment not found")

    # Verify corp ownership — non-admin callers can only undeploy their own equipment
    equip_corp_id = str(equip["corp_id"] or "") if "corp_id" in equip.keys() else ""
    if corp_id and equip_corp_id and corp_id != equip_corp_id:
        raise ValueError("You do not own this equipment")

    # Check for active jobs
    active_jobs = conn.execute(
        "SELECT COUNT(*) as cnt FROM production_jobs WHERE equipment_id = ? AND status = 'active'",
        (equipment_id,),
    ).fetchone()
    if active_jobs and active_jobs["cnt"] > 0:
        raise ValueError("Cannot undeploy equipment with active jobs — cancel jobs first")

    # Check for active refinery slots
    active_slots = conn.execute(
        "SELECT COUNT(*) as cnt FROM refinery_slots WHERE equipment_id = ? AND status = 'active'",
        (equipment_id,),
    ).fetchone()
    if active_slots and active_slots["cnt"] > 0:
        raise ValueError("Cannot undeploy refinery with active slot jobs — wait for them to complete")

    # Constructors in mine/construct mode must be set to idle first
    equip_mode = str(equip["mode"]) if "mode" in equip.keys() else "idle"
    if equip_mode != "idle":
        raise ValueError(f"Set constructor to idle mode before undeploying (currently: {equip_mode})")

    location_id = equip["location_id"]
    item_id = equip["item_id"]
    category = equip["category"]
    equip_corp_id = str(equip["corp_id"] or "") if "corp_id" in equip.keys() else ""
    equip_fid = str(equip["facility_id"] or "") if "facility_id" in equip.keys() else ""

    # Look up the catalog entry to restore the part
    catalog_entry = _resolve_deployable_catalog_entry(item_id) or {}

    # Build a part dict to add back to inventory
    import main as _main

    part = {
        "item_id": item_id,
        "name": catalog_entry.get("name", equip["name"]),
        "type": category,
        "category_id": category,
        "mass_kg": catalog_entry.get("mass_kg", 0),
    }
    _main.add_part_to_location_inventory(conn, location_id, part, count=1.0, corp_id=equip_corp_id)

    # Delete equipment, its completed jobs, and refinery slots
    conn.execute("DELETE FROM refinery_slots WHERE equipment_id = ?", (equipment_id,))
    conn.execute("DELETE FROM production_jobs WHERE equipment_id = ? AND status != 'active'", (equipment_id,))
    conn.execute("DELETE FROM deployed_equipment WHERE id = ?", (equipment_id,))
    conn.commit()

    return {"undeployed": True, "item_id": item_id, "location_id": location_id}


# ── Site Power & Thermal Balance ───────────────────────────────────────────────


def compute_site_power_balance(equipment: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute power and thermal balance from deployed equipment at a site.

    Energy flow:
      Reactors → thermal_mw
      Generators consume thermal → produce electric + waste heat
      Radiators reject waste heat (excess MWth absorbed by celestial — no overheat)
      Refineries / Constructors consume electric

    Returns summary dict for the UI.
    """
    # Totals
    total_thermal_mw = 0.0       # reactor output
    total_thermal_consumed = 0.0 # generator thermal input
    total_electric_supply = 0.0  # generator electric output
    total_electric_demand = 0.0  # refinery + constructor demand
    total_waste_heat = 0.0       # generator waste heat
    total_heat_rejection = 0.0   # radiator capacity

    # Item breakdowns
    reactors = []
    generators = []
    radiators = []
    consumers = []

    for eq in equipment:
        cfg = eq.get("config") or {}
        cat = eq.get("category", "")

        if cat == "reactor":
            thermal = float(cfg.get("thermal_mw") or 0)
            total_thermal_mw += thermal
            reactors.append({
                "name": eq["name"], "thermal_mw": thermal,
            })

        elif cat == "generator":
            th_in = float(cfg.get("thermal_mw_input") or 0)
            el_out = float(cfg.get("electric_mw") or 0)
            waste = float(cfg.get("waste_heat_mw") or 0)
            total_thermal_consumed += th_in
            total_electric_supply += el_out
            total_waste_heat += waste
            generators.append({
                "name": eq["name"], "thermal_mw_input": th_in,
                "electric_mw": el_out, "waste_heat_mw": waste,
            })

        elif cat == "radiator":
            rejection = float(cfg.get("heat_rejection_mw") or 0)
            total_heat_rejection += rejection
            radiators.append({
                "name": eq["name"], "heat_rejection_mw": rejection,
            })

        elif cat in ("refinery", "miner", "printer", "constructor", "robonaut", "prospector"):
            demand = float(cfg.get("electric_mw") or 0)
            is_active = eq.get("status") == "active"
            if is_active:
                total_electric_demand += demand
            consumers.append({
                "name": eq["name"], "electric_mw": demand,
                "category": cat, "active": is_active,
            })

    # Derived values
    # Generator throttle: limited by available thermal
    if total_thermal_consumed > 0 and total_thermal_mw < total_thermal_consumed:
        gen_throttle = total_thermal_mw / total_thermal_consumed
        actual_electric = total_electric_supply * gen_throttle
        actual_waste_heat = total_waste_heat * gen_throttle
    else:
        gen_throttle = 1.0
        actual_electric = total_electric_supply
        actual_waste_heat = total_waste_heat

    electric_surplus = actual_electric - total_electric_demand
    thermal_surplus = total_thermal_mw - total_thermal_consumed
    waste_heat_surplus = actual_waste_heat - total_heat_rejection
    # Excess waste heat absorbed by celestial body — no overheat penalty

    return {
        "thermal_mw_supply": round(total_thermal_mw, 2),
        "thermal_mw_consumed": round(total_thermal_consumed, 2),
        "thermal_mw_surplus": round(thermal_surplus, 2),
        "electric_mw_supply": round(actual_electric, 2),
        "electric_mw_demand": round(total_electric_demand, 2),
        "electric_mw_surplus": round(electric_surplus, 2),
        "waste_heat_mw": round(actual_waste_heat, 2),
        "heat_rejection_mw": round(total_heat_rejection, 2),
        "waste_heat_surplus_mw": round(max(0, waste_heat_surplus), 2),
        "gen_throttle": round(gen_throttle, 4),
        "reactors": reactors,
        "generators": generators,
        "radiators": radiators,
        "consumers": consumers,
        "power_ok": electric_surplus >= -0.001,
    }


# ── Production Jobs (Refinery recipes) ─────────────────────────────────────────


def start_production_job(
    conn: sqlite3.Connection,
    equipment_id: str,
    recipe_id: str,
    username: str,
    batch_count: int = 1,
    corp_id: str = "",
) -> Dict[str, Any]:
    """
    Start a production job on a deployed refinery or constructor.
    - Refineries run refinery/factory recipes (job_type='refine').
    - Constructors run shipyard recipes (job_type='construct').
    Consumes input resources from location inventory upfront.
    Job completes after build_time_s game-seconds.
    """
    # Validate equipment
    equip = conn.execute(
        "SELECT * FROM deployed_equipment WHERE id = ?",
        (equipment_id,),
    ).fetchone()
    if not equip:
        raise ValueError("Equipment not found")
    if equip["status"] != "idle":
        raise ValueError(f"Equipment is currently {equip['status']}, not idle")
    if equip["category"] not in ("refinery", "printer", "constructor"):
        raise ValueError("Production jobs require a refinery or printer")

    # Verify corp ownership — non-admin callers can only use their own equipment
    equip_corp_id = str(equip["corp_id"] or "") if "corp_id" in equip.keys() else ""
    if corp_id and equip_corp_id and corp_id != equip_corp_id:
        raise ValueError("You do not own this equipment")

    config = json.loads(equip["config_json"] or "{}")
    location_id = equip["location_id"]
    equip_fid = str(equip["facility_id"] or "") if "facility_id" in equip.keys() else ""

    # Check concurrent limit
    active_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM production_jobs WHERE equipment_id = ? AND status = 'active'",
        (equipment_id,),
    ).fetchone()
    max_concurrent = int(config.get("max_concurrent_recipes") or 1)
    if active_count and active_count["cnt"] >= max_concurrent:
        raise ValueError(f"Equipment already running {active_count['cnt']}/{max_concurrent} jobs")

    # Validate recipe
    recipes = catalog_service.load_recipe_catalog()
    recipe = recipes.get(recipe_id)
    if not recipe:
        raise ValueError(f"Recipe '{recipe_id}' not found")

    # Determine job type based on equipment category + recipe facility_type
    facility_type = str(recipe.get("facility_type") or "")
    equip_category = equip["category"]

    if equip_category in ("printer", "constructor"):
        # Printers can only run shipyard recipes (construction)
        if facility_type != "shipyard":
            raise ValueError("Printers can only run construction (shipyard) recipes")
        job_type = "construct"

        # Validate printer_type specialization (only for new 'printer' category)
        if equip_category == "printer":
            printer_type = str(config.get("printer_type") or "industrial")
            out_check_id = str(recipe.get("output_item_id") or "").strip()
            if out_check_id:
                _INDUSTRIAL_CATS = {"refinery", "miner", "constructor", "prospector", "robonaut", "printer", "isru"}
                _SHIP_CATS = {"thruster", "reactor", "generator", "radiator"}
                # Determine output category by searching all catalogs
                _out_cat = ""
                for _ldr in (
                    catalog_service.load_thruster_main_catalog,
                    catalog_service.load_reactor_catalog,
                    catalog_service.load_generator_catalog,
                    catalog_service.load_radiator_catalog,
                    catalog_service.load_miner_catalog,
                    catalog_service.load_printer_catalog,
                    catalog_service.load_robonaut_catalog,
                    catalog_service.load_isru_catalog,
                    catalog_service.load_refinery_catalog,
                ):
                    _hit = _ldr().get(out_check_id)
                    if _hit:
                        _out_cat = str(_hit.get("category_id") or _hit.get("type") or "")
                        break
                if printer_type == "industrial" and _out_cat and _out_cat not in _INDUSTRIAL_CATS:
                    raise ValueError(
                        f"Industrial printer cannot build output category '{_out_cat}'. "
                        f"Use an Aerospace Printer for thrusters, reactors, generators, and radiators."
                    )
                elif printer_type in ("ship", "aerospace") and _out_cat and _out_cat not in _SHIP_CATS:
                    raise ValueError(
                        f"Aerospace printer cannot build output category '{_out_cat}'. "
                        f"Use an Industrial Printer for refineries, miners, prospectors, printers, and ISRU."
                    )
    else:
        # Refineries run refinery/factory recipes
        if facility_type == "shipyard":
            raise ValueError("Refineries cannot run construction recipes — use a printer")
        job_type = "refine"

        # Check specialization match (refineries only)
        specialization = str(config.get("specialization") or "")
        recipe_category = str(recipe.get("refinery_category") or "")
        if not _is_recipe_compatible_with_refinery_specialization(recipe_category, specialization):
            raise ValueError(
                f"Refinery specialization '{specialization}' does not match recipe category '{recipe_category}'"
            )

    # Validate batch_count
    batch_count = max(1, int(batch_count))

    # Consume inputs from location inventory
    import main as _main

    inputs = recipe.get("inputs") or []
    for inp in inputs:
        inp_id = str(inp.get("item_id") or "").strip()
        inp_qty = float(inp.get("qty") or 0.0) * batch_count
        if not inp_id or inp_qty <= 0:
            continue

        # Check availability (location-scoped)
        row = conn.execute(
            """
            SELECT quantity FROM location_inventory_stacks
            WHERE location_id = ? AND corp_id = ? AND stack_type = 'resource' AND stack_key = ?
            """,
            (location_id, corp_id, inp_id),
        ).fetchone()
        available = float(row["quantity"]) if row else 0.0
        if available < inp_qty - 1e-9:
            raise ValueError(
                f"Insufficient '{_load_resource_name(inp_id)}': need {inp_qty:.2f}, have {available:.2f}"
            )

    # All checks passed — consume inputs
    for inp in inputs:
        inp_id = str(inp.get("item_id") or "").strip()
        inp_qty = float(inp.get("qty") or 0.0) * batch_count
        if not inp_id or inp_qty <= 0:
            continue
        # Negative delta to consume
        resources = catalog_service.load_resource_catalog()
        res_info = resources.get(inp_id) or {}
        density = max(0.0, float(res_info.get("mass_per_m3_kg") or 0.0))
        volume = (inp_qty / density) if density > 0.0 else 0.0

        _main._upsert_inventory_stack(
            conn,
            location_id=location_id,
            stack_type="resource",
            stack_key=inp_id,
            item_id=inp_id,
            name=str(res_info.get("name") or inp_id),
            quantity_delta=-inp_qty,
            mass_delta_kg=-inp_qty,
            volume_delta_m3=-volume,
            payload_json=_json_dumps({"resource_id": inp_id}),
            corp_id=corp_id,
        )

    # Calculate completion time
    now = game_now_s()
    base_time = float(recipe.get("build_time_s") or 600)
    if equip_category in ("printer", "constructor"):
        # Printers use construction_rate_kg_per_hr as a speed multiplier.
        # Higher rate = faster builds. Normalize around 50 kg/hr baseline.
        construction_rate = max(1.0, float(config.get("construction_rate_kg_per_hr") or 50.0))
        throughput_mult = construction_rate / 50.0
    else:
        throughput_mult = max(0.01, float(config.get("throughput_mult") or 1.0))
    actual_time = (base_time * batch_count) / throughput_mult
    completes_at = now + actual_time

    # Build outputs (scaled by batch_count)
    outputs = []
    output_item_id = str(recipe.get("output_item_id") or "").strip()
    output_qty = float(recipe.get("output_qty") or 0.0)
    efficiency = max(0.0, float(config.get("efficiency") or 1.0)) if equip_category == "refinery" else 1.0
    if output_item_id and output_qty > 0:
        outputs.append({"item_id": output_item_id, "qty": output_qty * efficiency * batch_count})
    for bp in (recipe.get("byproducts") or []):
        bp_id = str(bp.get("item_id") or "").strip()
        bp_qty = float(bp.get("qty") or 0.0)
        if bp_id and bp_qty > 0:
            outputs.append({"item_id": bp_id, "qty": bp_qty * efficiency * batch_count})

    job_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO production_jobs
          (id, location_id, equipment_id, job_type, recipe_id, status,
           started_at, completes_at, inputs_json, outputs_json, created_by, corp_id, facility_id)
        VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, location_id, equipment_id, job_type, recipe_id, now, completes_at,
         _json_dumps([{"item_id": inp["item_id"], "qty": float(inp.get("qty") or 0) * batch_count} for inp in inputs if inp.get("item_id")]),
         _json_dumps(outputs), username, corp_id, equip_fid),
    )

    # Mark equipment active
    conn.execute("UPDATE deployed_equipment SET status = 'active' WHERE id = ?", (equipment_id,))
    conn.commit()

    return {
        "job_id": job_id,
        "equipment_id": equipment_id,
        "recipe_id": recipe_id,
        "recipe_name": recipe.get("name", recipe_id),
        "started_at": now,
        "completes_at": completes_at,
        "duration_s": actual_time,
        "inputs": inputs,
        "outputs": outputs,
    }


def cancel_production_job(
    conn: sqlite3.Connection,
    job_id: str,
    username: str,
    *,
    corp_id: str = "",
) -> Dict[str, Any]:
    """
    Cancel an active production job. Returns partial inputs based on progress.
    Resources consumed so far are lost; remaining are returned.
    """
    job = conn.execute(
        "SELECT * FROM production_jobs WHERE id = ? AND status = 'active'",
        (job_id,),
    ).fetchone()
    if not job:
        raise ValueError("Active job not found")

    # Verify corp ownership — non-admin callers can only cancel their own jobs
    job_corp_id = str(job["corp_id"] or "") if "corp_id" in job.keys() else ""
    if corp_id and job_corp_id and corp_id != job_corp_id:
        raise ValueError("You do not own this job")

    now = game_now_s()
    started_at = float(job["started_at"])
    completes_at = float(job["completes_at"])
    total_duration = max(1.0, completes_at - started_at)
    elapsed = min(now - started_at, total_duration)
    progress = min(1.0, elapsed / total_duration)
    refund_fraction = max(0.0, 1.0 - progress)

    location_id = job["location_id"]
    equipment_id = job["equipment_id"]
    inputs = json.loads(job["inputs_json"] or "[]")
    job_corp_id = str(job["corp_id"] or "") if "corp_id" in job.keys() else ""
    job_fid = str(job["facility_id"] or "") if "facility_id" in job.keys() else ""

    import main as _main

    # Refund unconsumed portion
    if refund_fraction > 0.01:
        for inp in inputs:
            inp_id = str(inp.get("item_id") or "").strip()
            inp_qty = float(inp.get("qty") or 0.0)
            refund_qty = inp_qty * refund_fraction
            if inp_id and refund_qty > 0.01:
                _main.add_resource_to_location_inventory(conn, location_id, inp_id, refund_qty, corp_id=job_corp_id)

    conn.execute(
        "UPDATE production_jobs SET status = 'cancelled', completed_at = ? WHERE id = ?",
        (now, job_id),
    )
    conn.execute(
        "UPDATE deployed_equipment SET status = 'idle' WHERE id = ?",
        (equipment_id,),
    )
    conn.commit()

    return {
        "cancelled": True,
        "job_id": job_id,
        "progress": round(progress, 4),
        "refund_fraction": round(refund_fraction, 4),
    }


# ── Mining Jobs (Constructor-based) ────────────────────────────────────────────


def start_mining_job(
    conn: sqlite3.Connection,
    equipment_id: str,
    resource_id: str,
    username: str,
    corp_id: str = "",
) -> Dict[str, Any]:
    """
    Start a continuous mining job on a deployed constructor or ISRU unit at a surface site.
    """
    equip = conn.execute(
        "SELECT * FROM deployed_equipment WHERE id = ?",
        (equipment_id,),
    ).fetchone()
    if not equip:
        raise ValueError("Equipment not found")
    if equip["status"] != "idle":
        raise ValueError(f"Equipment is currently {equip['status']}, not idle")
    equip_category = str(equip["category"] or "")
    if equip_category not in ("miner", "constructor", "isru"):
        raise ValueError("Mining requires a miner or ISRU unit")

    # Verify corp ownership — non-admin callers can only use their own equipment
    equip_corp_id = str(equip["corp_id"] or "") if "corp_id" in equip.keys() else ""
    if corp_id and equip_corp_id and corp_id != equip_corp_id:
        raise ValueError("You do not own this equipment")

    location_id = equip["location_id"]
    config = json.loads(equip["config_json"] or "{}")
    equip_fid = str(equip["facility_id"] or "") if "facility_id" in equip.keys() else ""

    # Verify this is a surface site
    site = conn.execute(
        "SELECT * FROM surface_sites WHERE location_id = ?",
        (location_id,),
    ).fetchone()
    if not site:
        raise ValueError("Mining can only be done at surface sites")

    # Verify the site has been prospected by the user's org
    import org_service
    if corp_id:
        org_id = org_service.get_org_id_for_corp(conn, corp_id)
    else:
        org_id = org_service.get_org_id_for_user(conn, username)
    if org_id:
        if not org_service.is_site_prospected(conn, org_id, location_id):
            raise ValueError("Site must be prospected before mining can begin")
    else:
        raise ValueError("You must belong to an organization to mine")

    # Verify the resource exists in the site's distribution
    site_resource = conn.execute(
        """
        SELECT mass_fraction FROM surface_site_resources
        WHERE site_location_id = ? AND resource_id = ?
        """,
        (location_id, resource_id),
    ).fetchone()
    if not site_resource:
        raise ValueError(f"Resource '{resource_id}' not available at this site")

    output_resource_id = resource_id
    if equip_category == "isru":
        output_resource_id = str(config.get("mining_output_resource_id") or "water").strip() or "water"

    if equip_category == "isru":
        base_rate = float(config.get("water_extraction_kg_per_hr") or 0.0)
    else:
        base_rate = float(config.get("mining_rate_kg_per_hr") or 0.0)
    if base_rate <= 0:
        raise ValueError("Equipment has no mining capability")

    # Effective mining rate
    if equip_category == "isru":
        effective_rate = round(base_rate, 4)
    else:
        # Constructor rate scales with resource abundance
        mass_fraction = float(site_resource["mass_fraction"])
        effective_rate = round(base_rate * mass_fraction, 4)

    now = game_now_s()
    job_id = str(uuid.uuid4())

    # Mining jobs use a far-future completes_at (they run until stopped)
    far_future = now + (365.25 * 24 * 3600 * 100)  # 100 years

    conn.execute(
        """
        INSERT INTO production_jobs
          (id, location_id, equipment_id, job_type, resource_id, status,
           started_at, completes_at, inputs_json, outputs_json, created_by, corp_id, facility_id)
        VALUES (?, ?, ?, 'mine', ?, 'active', ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, location_id, equipment_id, resource_id, now, far_future,
         _json_dumps({"last_settled": now, "total_mined_kg": 0}),
         _json_dumps([{
             "item_id": output_resource_id,
             "source_resource_id": resource_id,
             "rate_kg_per_hr": effective_rate,
         }]),
         username, corp_id, equip_fid),
    )

    conn.execute("UPDATE deployed_equipment SET status = 'active' WHERE id = ?", (equipment_id,))
    conn.commit()

    return {
        "job_id": job_id,
        "equipment_id": equipment_id,
        "resource_id": output_resource_id,
        "resource_name": _load_resource_name(output_resource_id),
        "source_resource_id": resource_id,
        "source_resource_name": _load_resource_name(resource_id),
        "rate_kg_per_hr": effective_rate,
        "started_at": now,
    }


def stop_mining_job(
    conn: sqlite3.Connection,
    job_id: str,
    username: str,
    *,
    corp_id: str = "",
) -> Dict[str, Any]:
    """Stop a running mining job. Settles any un-collected mined resources first."""
    job = conn.execute(
        "SELECT * FROM production_jobs WHERE id = ? AND status = 'active' AND job_type = 'mine'",
        (job_id,),
    ).fetchone()
    if not job:
        raise ValueError("Active mining job not found")

    # Verify corp ownership — non-admin callers can only stop their own jobs
    job_corp_id = str(job["corp_id"] or "") if "corp_id" in job.keys() else ""
    if corp_id and job_corp_id and corp_id != job_corp_id:
        raise ValueError("You do not own this job")

    # Settle any pending mined resources before stopping
    _settle_mining_jobs(conn, game_now_s(), job["location_id"])

    now = game_now_s()
    equipment_id = job["equipment_id"]

    conn.execute(
        "UPDATE production_jobs SET status = 'completed', completed_at = ? WHERE id = ?",
        (now, job_id),
    )
    conn.execute(
        "UPDATE deployed_equipment SET status = 'idle' WHERE id = ?",
        (equipment_id,),
    )
    conn.commit()

    inputs = json.loads(job["inputs_json"] or "{}")
    return {
        "stopped": True,
        "job_id": job_id,
        "total_mined_kg": float(inputs.get("total_mined_kg") or 0),
    }


# ── Query Helpers ──────────────────────────────────────────────────────────────


def get_deployed_equipment(conn: sqlite3.Connection, location_id: str, *, facility_id: str = "") -> List[Dict[str, Any]]:
    """List all deployed equipment at a location (optionally filtered by facility)."""
    if facility_id:
        rows = conn.execute(
            """
            SELECT id, location_id, item_id, name, category, deployed_at,
                   deployed_by, status, config_json, corp_id, mode, facility_id
            FROM deployed_equipment
            WHERE facility_id = ?
            ORDER BY category, name
            """,
            (facility_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, location_id, item_id, name, category, deployed_at,
                   deployed_by, status, config_json, corp_id, mode, facility_id
            FROM deployed_equipment
            WHERE location_id = ?
            ORDER BY category, name
            """,
            (location_id,),
        ).fetchall()

    result = []
    for r in rows:
        config = json.loads(r["config_json"] or "{}")
        mode = str(r["mode"]) if "mode" in r.keys() else "idle"
        entry = {
            "id": r["id"],
            "location_id": r["location_id"],
            "item_id": r["item_id"],
            "name": r["name"],
            "category": r["category"],
            "deployed_at": float(r["deployed_at"]),
            "deployed_by": r["deployed_by"],
            "status": r["status"],
            "mode": mode,
            "config": config,
            "corp_id": str(r["corp_id"] or ""),
            "facility_id": str(r["facility_id"] or "") if "facility_id" in r.keys() else "",
        }
        # For mining-capable equipment in mine mode, add mining stats
        if mode == "mine" and r["category"] in ("miner", "constructor", "isru"):
            entry["mining_total_kg"] = float(config.get("mining_total_mined_kg") or 0.0)
            if r["category"] == "isru":
                entry["mining_rate_kg_hr"] = float(config.get("water_extraction_kg_per_hr") or 0.0)
            else:
                entry["mining_rate_kg_hr"] = float(config.get("mining_rate_kg_per_hr") or 0.0)
        result.append(entry)
    return result


def get_active_jobs(conn: sqlite3.Connection, location_id: str, *, facility_id: str = "") -> List[Dict[str, Any]]:
    """List all active jobs at a location (optionally filtered by facility)."""
    now = game_now_s()
    if facility_id:
        rows = conn.execute(
            """
            SELECT pj.id, pj.location_id, pj.equipment_id, pj.job_type,
                   pj.recipe_id, pj.resource_id, pj.status,
                   pj.started_at, pj.completes_at, pj.inputs_json, pj.outputs_json,
                   pj.created_by, pj.facility_id,
                   de.name AS equipment_name, de.item_id AS equipment_item_id
            FROM production_jobs pj
            JOIN deployed_equipment de ON de.id = pj.equipment_id
            WHERE pj.facility_id = ? AND pj.status = 'active'
            ORDER BY pj.started_at
            """,
            (facility_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT pj.id, pj.location_id, pj.equipment_id, pj.job_type,
                   pj.recipe_id, pj.resource_id, pj.status,
                   pj.started_at, pj.completes_at, pj.inputs_json, pj.outputs_json,
                   pj.created_by, pj.facility_id,
                   de.name AS equipment_name, de.item_id AS equipment_item_id
            FROM production_jobs pj
            JOIN deployed_equipment de ON de.id = pj.equipment_id
            WHERE pj.location_id = ? AND pj.status = 'active'
            ORDER BY pj.started_at
            """,
            (location_id,),
        ).fetchall()

    recipes = catalog_service.load_recipe_catalog()
    result = []
    for r in rows:
        started = float(r["started_at"])
        completes = float(r["completes_at"])
        total_dur = max(1.0, completes - started)
        elapsed = min(now - started, total_dur)
        progress = min(1.0, max(0.0, elapsed / total_dur))

        job_type = r["job_type"]
        entry: Dict[str, Any] = {
            "id": r["id"],
            "location_id": r["location_id"],
            "equipment_id": r["equipment_id"],
            "equipment_name": r["equipment_name"],
            "job_type": job_type,
            "status": r["status"],
            "started_at": started,
            "completes_at": completes,
            "progress": round(progress, 4),
            "created_by": r["created_by"],
        }

        if job_type in ("refine", "construct"):
            recipe = recipes.get(r["recipe_id"] or "")
            entry["recipe_id"] = r["recipe_id"]
            entry["recipe_name"] = recipe.get("name", r["recipe_id"]) if recipe else r["recipe_id"]
            entry["inputs"] = json.loads(r["inputs_json"] or "[]")
            entry["outputs"] = json.loads(r["outputs_json"] or "[]")
        elif job_type == "mine":
            inputs = json.loads(r["inputs_json"] or "{}")
            outputs = json.loads(r["outputs_json"] or "[]")
            rate_info = outputs[0] if outputs else {}
            output_resource_id = str(rate_info.get("item_id") or r["resource_id"] or "")
            source_resource_id = str(rate_info.get("source_resource_id") or r["resource_id"] or "")
            entry["resource_id"] = output_resource_id
            entry["resource_name"] = _load_resource_name(output_resource_id)
            entry["source_resource_id"] = source_resource_id
            entry["source_resource_name"] = _load_resource_name(source_resource_id)
            entry["rate_kg_per_hr"] = float(rate_info.get("rate_kg_per_hr") or 0)
            entry["total_mined_kg"] = float(inputs.get("total_mined_kg") or 0)
            entry["progress"] = None  # Mining has no end

        result.append(entry)
    return result


def get_job_history(
    conn: sqlite3.Connection,
    location_id: str,
    limit: int = 20,
    *,
    facility_id: str = "",
) -> List[Dict[str, Any]]:
    """List recent completed/cancelled jobs at a location."""
    if facility_id:
        rows = conn.execute(
            """
            SELECT pj.id, pj.job_type, pj.recipe_id, pj.resource_id,
                   pj.status, pj.started_at, pj.completed_at,
                   pj.outputs_json, pj.inputs_json,
                   de.name AS equipment_name
            FROM production_jobs pj
            JOIN deployed_equipment de ON de.id = pj.equipment_id
            WHERE pj.facility_id = ? AND pj.status IN ('completed', 'cancelled')
            ORDER BY pj.completed_at DESC
            LIMIT ?
            """,
            (facility_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT pj.id, pj.job_type, pj.recipe_id, pj.resource_id,
                   pj.status, pj.started_at, pj.completed_at,
                   pj.outputs_json, pj.inputs_json,
                   de.name AS equipment_name
            FROM production_jobs pj
            JOIN deployed_equipment de ON de.id = pj.equipment_id
            WHERE pj.location_id = ? AND pj.status IN ('completed', 'cancelled')
            ORDER BY pj.completed_at DESC
            LIMIT ?
            """,
            (location_id, limit),
        ).fetchall()

    recipes = catalog_service.load_recipe_catalog()
    result = []
    for r in rows:
        recipe = recipes.get(r["recipe_id"] or "") if r["recipe_id"] else None
        result.append({
            "id": r["id"],
            "job_type": r["job_type"],
            "recipe_name": recipe.get("name", r["recipe_id"]) if recipe else None,
            "resource_id": r["resource_id"],
            "resource_name": _load_resource_name(r["resource_id"]) if r["resource_id"] else None,
            "equipment_name": r["equipment_name"],
            "status": r["status"],
            "started_at": float(r["started_at"] or 0),
            "completed_at": float(r["completed_at"] or 0),
        })
    return result


def get_available_recipes_for_location(
    conn: sqlite3.Connection,
    location_id: str,
    corp_id: str = "",
    unlocked_tech_ids: Optional[set[str]] = None,
    facility_id: str = "",
) -> List[Dict[str, Any]]:
    """
    Get all recipes that could be run at a location, based on deployed equipment.
    - Refinery/factory recipes matched to deployed refineries (by specialization).
    - Shipyard recipes matched to deployed constructors (any constructor can build any).
    Also annotates each recipe with whether the location has sufficient inputs.
    """
    equipment = get_deployed_equipment(conn, location_id, facility_id=facility_id)
    refineries = [e for e in equipment if e["category"] == "refinery"]
    constructors = [e for e in equipment if e["category"] in ("printer", "constructor")]

    if not refineries and not constructors:
        return []

    # Gather inventory for availability checks (location-scoped)
    import main as _main
    inv = _main.get_location_inventory_payload(conn, location_id, corp_id=corp_id or None)
    resource_stock: Dict[str, float] = {}
    for res in inv.get("resources") or []:
        rid = str(res.get("resource_id") or res.get("item_id") or "")
        resource_stock[rid] = float(res.get("quantity") or 0)

    all_recipes = catalog_service.load_recipe_catalog()
    resource_catalog = catalog_service.load_resource_catalog()

    # Build output_item_id → metadata map from all part catalogs
    _output_meta_map: Dict[str, Dict[str, str]] = {}
    for _loader, _cat in [
        (catalog_service.load_thruster_main_catalog, "thruster"),
        (catalog_service.load_reactor_catalog, "reactor"),
        (catalog_service.load_generator_catalog, "generator"),
        (catalog_service.load_radiator_catalog, "radiator"),
        (catalog_service.load_robonaut_catalog, "prospector"),
        (catalog_service.load_miner_catalog, "miner"),
        (catalog_service.load_printer_catalog, "printer"),
        (catalog_service.load_constructor_catalog, "constructor"),
        (catalog_service.load_isru_catalog, "isru"),
        (catalog_service.load_refinery_catalog, "refinery"),
    ]:
        try:
            for _item_id, _entry in _loader().items():
                _output_meta_map[_item_id] = {
                    "category": _cat,
                    "research_node": str((_entry or {}).get("research_node") or "").strip(),
                }
        except Exception:
            pass

    result = []
    for recipe in sorted(all_recipes.values(), key=lambda r: r.get("name", "")):
        recipe_cat = str(recipe.get("refinery_category") or "")
        facility_type = str(recipe.get("facility_type") or "")

        compatible_refineries = []
        compatible_constructors = []

        if facility_type == "shipyard":
            # Shipyard recipes: filter printers by printer_type specialization
            _INDUSTRIAL_CATS = {"refinery", "miner", "constructor", "prospector", "robonaut", "printer", "isru"}
            _SHIP_CATS = {"thruster", "reactor", "generator", "radiator"}
            out_item_id_r = str(recipe.get("output_item_id") or "")
            out_cat_r = (_output_meta_map.get(out_item_id_r) or {}).get("category", "")
            for c in constructors:
                equip_cat = str(c.get("category") or "")
                if equip_cat == "printer":
                    ptype = str((c.get("config") or {}).get("printer_type") or "industrial")
                    if ptype == "industrial" and out_cat_r and out_cat_r not in _INDUSTRIAL_CATS:
                        continue
                    if ptype in ("ship", "aerospace") and out_cat_r and out_cat_r not in _SHIP_CATS:
                        continue
                compatible_constructors.append(c)
        else:
            # Refinery/factory recipes: match by specialization (no tier restriction)
            for ref in refineries:
                cfg = ref.get("config") or {}
                spec = str(cfg.get("specialization") or "")
                if _is_recipe_compatible_with_refinery_specialization(recipe_cat, spec):
                    compatible_refineries.append(ref)

        if not compatible_refineries and not compatible_constructors:
            continue

        # Shipyard recipes are visible only when researched for the org.
        if facility_type == "shipyard" and unlocked_tech_ids is not None:
            out_id = str(recipe.get("output_item_id") or "")
            out_meta = _output_meta_map.get(out_id) or {}
            required_node_id = str(recipe.get("required_tech_id") or "").strip()
            if not required_node_id:
                required_node_id = str(out_meta.get("research_node") or "").strip()

            if required_node_id:
                if required_node_id not in unlocked_tech_ids:
                    continue
            else:
                required_tier = max(0, int(recipe.get("min_tech_tier") or 0))
                if required_tier > 0:
                    output_category = str(out_meta.get("category") or "other")
                    research_category = _SHIPYARD_OUTPUT_TO_RESEARCH_CATEGORY.get(output_category)
                    if research_category:
                        # Refineries have per-branch subtrees in the research tree
                        if output_category == "refinery":
                            ref_cat = str(recipe.get("refinery_category") or "")
                            subtree_prefix = _REFINERY_CATEGORY_TO_RESEARCH_PREFIX.get(ref_cat)
                            if subtree_prefix:
                                required_node_id = f"{subtree_prefix}_lvl_{required_tier}"
                            else:
                                required_node_id = f"{research_category}_lvl_{required_tier}"
                        elif output_category == "isru":
                            # ISRU has per-branch subtrees (sifting / heat_drill)
                            out_id_isru = str(recipe.get("output_item_id") or "")
                            isru_catalog = catalog_service.load_isru_catalog()
                            isru_entry = isru_catalog.get(out_id_isru, {})
                            isru_branch = str(isru_entry.get("branch") or "")
                            subtree_prefix = _ISRU_BRANCH_TO_RESEARCH_PREFIX.get(isru_branch)
                            if subtree_prefix:
                                required_node_id = f"{subtree_prefix}_lvl_{required_tier}"
                            else:
                                required_node_id = f"{research_category}_lvl_{required_tier}"
                        else:
                            required_node_id = f"{research_category}_lvl_{required_tier}"
                        if required_node_id not in unlocked_tech_ids:
                            continue

        # Check input availability and compute max_batches
        inputs_status = []
        can_start = True
        max_batches = None  # None = unlimited (no inputs)
        for inp in (recipe.get("inputs") or []):
            inp_id = str(inp.get("item_id") or "")
            inp_qty = float(inp.get("qty") or 0)
            available = resource_stock.get(inp_id, 0)
            res_info = resource_catalog.get(inp_id) or {}
            sufficient = available >= inp_qty - 1e-9
            if not sufficient:
                can_start = False
            # How many batches can this input support?
            if inp_qty > 0:
                batches_for_input = int(available / inp_qty)
                if max_batches is None:
                    max_batches = batches_for_input
                else:
                    max_batches = min(max_batches, batches_for_input)
            inputs_status.append({
                "item_id": inp_id,
                "name": str(res_info.get("name") or inp_id),
                "qty_needed": inp_qty,
                "qty_available": available,
                "sufficient": sufficient,
            })
        if max_batches is None:
            max_batches = 1
        max_batches = max(max_batches, 0)

        # Find idle equipment
        idle_refineries = [r for r in compatible_refineries if r["status"] == "idle"]
        idle_constructors = [c for c in compatible_constructors if c["status"] == "idle"]
        has_idle = len(idle_refineries) > 0 or len(idle_constructors) > 0

        # Determine output category for grouping
        out_id = str(recipe.get("output_item_id") or "")
        output_category = str((_output_meta_map.get(out_id) or {}).get("category") or "other")

        result.append({
            **recipe,
            "output_category": output_category,
            "inputs_status": inputs_status,
            "max_batches": max_batches,
            "can_start": can_start and has_idle,
            "compatible_refineries": [
                {"id": r["id"], "name": r["name"], "status": r["status"]}
                for r in compatible_refineries
            ],
            "idle_refineries": [
                {"id": r["id"], "name": r["name"]}
                for r in idle_refineries
            ],
            "compatible_constructors": [
                {"id": c["id"], "name": c["name"], "status": c["status"]}
                for c in compatible_constructors
            ],
            "idle_constructors": [
                {"id": c["id"], "name": c["name"]}
                for c in idle_constructors
            ],
        })

    return result


def get_location_industry_summary(conn: sqlite3.Connection, location_id: str, *, facility_id: str = "") -> Dict[str, Any]:
    """Get a summary of industrial activity at a location (optionally scoped to facility)."""
    if facility_id:
        equip_counts = conn.execute(
            """
            SELECT category, COUNT(*) as cnt,
                   SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_cnt
            FROM deployed_equipment
            WHERE facility_id = ?
            GROUP BY category
            """,
            (facility_id,),
        ).fetchall()
        job_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM production_jobs WHERE facility_id = ? AND status = 'active'",
            (facility_id,),
        ).fetchone()
    else:
        equip_counts = conn.execute(
            """
            SELECT category, COUNT(*) as cnt,
                   SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_cnt
            FROM deployed_equipment
            WHERE location_id = ?
            GROUP BY category
            """,
            (location_id,),
        ).fetchall()
        job_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM production_jobs WHERE location_id = ? AND status = 'active'",
            (location_id,),
        ).fetchone()

    equipment_summary: Dict[str, Any] = {}
    for row in equip_counts:
        equipment_summary[row["category"]] = {
            "total": row["cnt"],
            "active": row["active_cnt"],
        }

    return {
        "location_id": location_id,
        "equipment": equipment_summary,
        "active_jobs": job_count["cnt"] if job_count else 0,
    }


def get_minable_resources(conn: sqlite3.Connection, location_id: str) -> List[Dict[str, Any]]:
    """Get resources that can be mined at a surface site."""
    resources = conn.execute(
        """
        SELECT ssr.resource_id, ssr.mass_fraction
        FROM surface_site_resources ssr
        WHERE ssr.site_location_id = ?
        ORDER BY ssr.mass_fraction DESC
        """,
        (location_id,),
    ).fetchall()

    resource_catalog = catalog_service.load_resource_catalog()
    result = []
    for r in resources:
        rid = r["resource_id"]
        info = resource_catalog.get(rid) or {}
        result.append({
            "resource_id": rid,
            "name": str(info.get("name") or rid),
            "mass_fraction": float(r["mass_fraction"]),
            "mass_fraction_pct": round(float(r["mass_fraction"]) * 100, 2),
        })
    return result


# ── Constructor Mode ───────────────────────────────────────────────────────────


def set_constructor_mode(
    conn: sqlite3.Connection,
    equipment_id: str,
    mode: str,
    username: str,
    corp_id: str = "",
) -> Dict[str, Any]:
    """
    Set a constructor's mode: 'mine', 'construct', or 'idle'.
    - mine: constructor mines at full rate, output split by site resource distribution.
    - construct: construction speed added to site pool.
    - idle: does nothing.
    """
    if mode not in ("mine", "construct", "idle"):
        raise ValueError(f"Invalid mode '{mode}' — must be 'mine', 'construct', or 'idle'")

    equip = conn.execute(
        "SELECT * FROM deployed_equipment WHERE id = ?",
        (equipment_id,),
    ).fetchone()
    if not equip:
        raise ValueError("Equipment not found")

    if equip["category"] not in ("miner", "printer", "constructor", "isru", "robonaut", "prospector"):
        raise ValueError("Only miners, printers, ISRU units, and prospectors can change mode")

    # Prospectors and ISRU cannot perform mining or construction jobs.
    if equip["category"] in ("robonaut", "prospector") and mode in ("mine", "construct"):
        raise ValueError("Prospectors can only be set to idle")
    # Miners can mine or idle, but not construct.
    if equip["category"] == "miner" and mode == "construct":
        raise ValueError("Miners cannot be set to construct mode — use a Printer for fabrication")
    # Printers can construct or idle, but not mine.
    if equip["category"] == "printer" and mode == "mine":
        raise ValueError("Printers cannot be set to mine mode — use a Miner for excavation")

    # Verify corp ownership
    equip_corp_id = str(equip["corp_id"] or "") if "corp_id" in equip.keys() else ""
    if corp_id and equip_corp_id and corp_id != equip_corp_id:
        raise ValueError("You do not own this equipment")

    # Verify surface site for mining
    if mode == "mine":
        site = conn.execute(
            "SELECT location_id FROM surface_sites WHERE location_id = ?",
            (equip["location_id"],),
        ).fetchone()
        if not site:
            raise ValueError("Mining requires a surface site")

    config = json.loads(equip["config_json"] or "{}")
    now = game_now_s()

    # When switching from mining, settle any pending mined resources first
    old_mode = str(equip["mode"]) if "mode" in equip.keys() else "idle"
    if old_mode == "mine" and mode != "mine":
        _settle_mining_v2(conn, now, equip["location_id"])

    # When switching to mining, initialize last_settled timestamp
    if mode == "mine" and old_mode != "mine":
        config["mining_last_settled"] = now
        conn.execute(
            "UPDATE deployed_equipment SET config_json = ? WHERE id = ?",
            (_json_dumps(config), equipment_id),
        )

    conn.execute(
        "UPDATE deployed_equipment SET mode = ?, status = ? WHERE id = ?",
        (mode, "active" if mode != "idle" else "idle", equipment_id),
    )
    conn.commit()

    return {
        "equipment_id": equipment_id,
        "mode": mode,
        "status": "active" if mode != "idle" else "idle",
    }


# ── Refinery Slot Management ──────────────────────────────────────────────────


def get_refinery_slots(conn: sqlite3.Connection, location_id: str, *, facility_id: str = "") -> List[Dict[str, Any]]:
    """Get all refinery slots at a location (optionally filtered by facility)."""
    if facility_id:
        rows = conn.execute(
            """
            SELECT rs.id, rs.equipment_id, rs.location_id, rs.slot_index,
                   rs.recipe_id, rs.priority, rs.status, rs.current_job_id, rs.corp_id,
                   rs.cumulative_output_qty, rs.facility_id,
                   de.name AS equipment_name, de.config_json
            FROM refinery_slots rs
            JOIN deployed_equipment de ON de.id = rs.equipment_id
            WHERE rs.facility_id = ?
            ORDER BY rs.priority ASC, rs.slot_index ASC
            """,
            (facility_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT rs.id, rs.equipment_id, rs.location_id, rs.slot_index,
                   rs.recipe_id, rs.priority, rs.status, rs.current_job_id, rs.corp_id,
                   rs.cumulative_output_qty, rs.facility_id,
                   de.name AS equipment_name, de.config_json
            FROM refinery_slots rs
            JOIN deployed_equipment de ON de.id = rs.equipment_id
            WHERE rs.location_id = ?
            ORDER BY rs.priority ASC, rs.slot_index ASC
            """,
            (location_id,),
        ).fetchall()

    recipes = catalog_service.load_recipe_catalog()
    # Pre-fetch current job timing for active slots
    job_timing: Dict[str, Dict[str, float]] = {}
    active_job_ids = [str(r["current_job_id"]) for r in rows if r["current_job_id"]]
    if active_job_ids:
        placeholders = ",".join("?" for _ in active_job_ids)
        job_rows = conn.execute(
            f"SELECT id, started_at, completes_at FROM production_jobs WHERE id IN ({placeholders})",
            active_job_ids,
        ).fetchall()
        for jr in job_rows:
            job_timing[str(jr["id"])] = {
                "started_at": float(jr["started_at"] or 0),
                "completes_at": float(jr["completes_at"] or 0),
            }

    result = []
    for r in rows:
        recipe_id = r["recipe_id"]
        recipe = recipes.get(recipe_id) if recipe_id else None
        config = json.loads(r["config_json"] or "{}")
        corp_id = str(r["corp_id"] or "")
        cumulative = float(r["cumulative_output_qty"] if r["cumulative_output_qty"] else 0)

        # Calculate how many batches of inputs are available in storage
        batches_available = 0
        if recipe:
            inputs = recipe.get("inputs") or []
            if inputs:
                min_batches = float("inf")
                for inp in inputs:
                    inp_id = str(inp.get("item_id") or "").strip()
                    inp_qty = float(inp.get("qty") or 0.0)
                    if not inp_id or inp_qty <= 0:
                        continue
                    row_inv = conn.execute(
                        "SELECT quantity FROM location_inventory_stacks WHERE location_id = ? AND corp_id = ? AND stack_type = 'resource' AND stack_key = ?",
                        (r["location_id"], corp_id, inp_id),
                    ).fetchone()
                    available = float(row_inv["quantity"]) if row_inv else 0.0
                    batches_for_input = available / inp_qty if inp_qty > 0 else 0.0
                    min_batches = min(min_batches, batches_for_input)
                batches_available = int(min_batches) if min_batches != float("inf") else 0

        # Attach job timing if this slot has an active job
        cur_job_id = str(r["current_job_id"] or "")
        jt = job_timing.get(cur_job_id, {})

        result.append({
            "id": r["id"],
            "equipment_id": r["equipment_id"],
            "equipment_name": r["equipment_name"],
            "slot_index": r["slot_index"],
            "recipe_id": recipe_id,
            "recipe_name": recipe.get("name") if recipe else None,
            "priority": r["priority"],
            "status": r["status"],
            "cumulative_output_qty": cumulative,
            "batches_available": batches_available,
            "specialization": config.get("specialization", ""),
            "throughput_mult": config.get("throughput_mult", 1.0),
            "corp_id": corp_id,
            "job_started_at": jt.get("started_at"),
            "job_completes_at": jt.get("completes_at"),
        })
    return result


def assign_refinery_slot(
    conn: sqlite3.Connection,
    slot_id: str,
    recipe_id: Optional[str],
    username: str,
    corp_id: str = "",
) -> Dict[str, Any]:
    """Assign (or clear) a recipe on a refinery slot."""
    slot = conn.execute("SELECT * FROM refinery_slots WHERE id = ?", (slot_id,)).fetchone()
    if not slot:
        raise ValueError("Refinery slot not found")

    # Verify corp ownership
    slot_corp_id = str(slot["corp_id"] or "")
    if corp_id and slot_corp_id and corp_id != slot_corp_id:
        raise ValueError("You do not own this slot")

    if recipe_id:
        # Validate recipe exists and is compatible
        recipes = catalog_service.load_recipe_catalog()
        recipe = recipes.get(recipe_id)
        if not recipe:
            raise ValueError(f"Recipe '{recipe_id}' not found")
        if str(recipe.get("facility_type") or "") == "shipyard":
            raise ValueError("Refinery slots cannot run construction recipes")

        # Check specialization compatibility
        equip = conn.execute(
            "SELECT config_json FROM deployed_equipment WHERE id = ?",
            (slot["equipment_id"],),
        ).fetchone()
        if equip:
            config = json.loads(equip["config_json"] or "{}")
            spec = str(config.get("specialization") or "")
            recipe_cat = str(recipe.get("refinery_category") or "")
            if not _is_recipe_compatible_with_refinery_specialization(recipe_cat, spec):
                raise ValueError(f"Recipe category '{recipe_cat}' not compatible with refinery specialization '{spec}'")

    conn.execute(
        "UPDATE refinery_slots SET recipe_id = ?, cumulative_output_qty = 0 WHERE id = ?",
        (recipe_id if recipe_id else None, slot_id),
    )
    conn.commit()

    return {"slot_id": slot_id, "recipe_id": recipe_id}


def reorder_refinery_slots(
    conn: sqlite3.Connection,
    location_id: str,
    slot_ids: List[str],
    corp_id: str = "",
    facility_id: str = "",
) -> Dict[str, Any]:
    """Reorder refinery slot priorities. slot_ids should be in desired priority order."""
    if not slot_ids:
        return {"reordered": True, "count": 0}

    placeholders = ",".join("?" for _ in slot_ids)
    if facility_id:
        rows = conn.execute(
            f"""
            SELECT id, corp_id FROM refinery_slots
            WHERE facility_id = ? AND id IN ({placeholders})
            """,
            [facility_id] + slot_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT id, corp_id FROM refinery_slots
            WHERE location_id = ? AND id IN ({placeholders})
            """,
            [location_id] + slot_ids,
        ).fetchall()

    found_ids = {str(r["id"]) for r in rows}
    missing = [sid for sid in slot_ids if sid not in found_ids]
    if missing:
        raise ValueError("Some refinery slots are invalid for this scope")

    if corp_id:
        for r in rows:
            row_corp_id = str(r["corp_id"] or "")
            if row_corp_id and row_corp_id != corp_id:
                raise ValueError("You do not own one or more refinery slots")

    for i, sid in enumerate(slot_ids):
        if facility_id:
            conn.execute(
                "UPDATE refinery_slots SET priority = ? WHERE id = ? AND facility_id = ?",
                (i, sid, facility_id),
            )
        else:
            conn.execute(
                "UPDATE refinery_slots SET priority = ? WHERE id = ? AND location_id = ?",
                (i, sid, location_id),
            )
    conn.commit()
    return {"reordered": True, "count": len(slot_ids)}


# ── Construction Queue ─────────────────────────────────────────────────────────


def get_construction_queue(conn: sqlite3.Connection, location_id: str, *, facility_id: str = "") -> Dict[str, Any]:
    """Get the construction queue and pool stats for a location (optionally filtered by facility)."""
    if facility_id:
        rows = conn.execute(
            """
            SELECT id, recipe_id, queue_order, status, started_at, completes_at,
                   inputs_json, outputs_json, created_by, corp_id, facility_id
            FROM construction_queue
            WHERE facility_id = ? AND status IN ('queued', 'active')
            ORDER BY
                CASE WHEN status = 'active' THEN 0 ELSE 1 END,
                queue_order ASC
            """,
            (facility_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, recipe_id, queue_order, status, started_at, completes_at,
                   inputs_json, outputs_json, created_by, corp_id, facility_id
            FROM construction_queue
            WHERE location_id = ? AND status IN ('queued', 'active')
            ORDER BY
                CASE WHEN status = 'active' THEN 0 ELSE 1 END,
                queue_order ASC
            """,
            (location_id,),
        ).fetchall()

    recipes = catalog_service.load_recipe_catalog()
    now = game_now_s()
    queue = []
    for r in rows:
        recipe = recipes.get(r["recipe_id"]) if r["recipe_id"] else None
        entry: Dict[str, Any] = {
            "id": r["id"],
            "recipe_id": r["recipe_id"],
            "recipe_name": recipe.get("name") if recipe else r["recipe_id"],
            "queue_order": r["queue_order"],
            "status": r["status"],
            "corp_id": str(r["corp_id"] or ""),
        }
        if r["status"] == "active" and r["started_at"] and r["completes_at"]:
            started = float(r["started_at"])
            completes = float(r["completes_at"])
            total_dur = max(1.0, completes - started)
            elapsed = min(now - started, total_dur)
            progress = min(1.0, max(0.0, elapsed / total_dur))
            remaining = max(0.0, completes - now)
            entry.update({
                "started_at": started,
                "completes_at": completes,
                "progress": round(progress, 4),
                "remaining_s": round(remaining, 1),
            })
        if recipe:
            entry["inputs"] = recipe.get("inputs") or []
            entry["output_item_id"] = recipe.get("output_item_id", "")
            entry["output_qty"] = recipe.get("output_qty", 0)
        queue.append(entry)

    pool_speed = _get_construction_pool_speed(conn, location_id, facility_id=facility_id)

    return {
        "queue": queue,
        "pool_speed_kg_per_hr": round(pool_speed, 2),
        "pool_throughput_mult": round(pool_speed / 50.0, 4) if pool_speed > 0 else 0,
    }


def queue_construction(
    conn: sqlite3.Connection,
    location_id: str,
    recipe_id: str,
    username: str,
    corp_id: str = "",
    facility_id: str = "",
) -> Dict[str, Any]:
    """Add a recipe to the construction queue."""
    recipes = catalog_service.load_recipe_catalog()
    recipe = recipes.get(recipe_id)
    if not recipe:
        raise ValueError(f"Recipe '{recipe_id}' not found")
    if str(recipe.get("facility_type") or "") != "shipyard":
        raise ValueError("Only construction (shipyard) recipes can be queued")

    # Get next queue order
    if facility_id:
        max_order = conn.execute(
            "SELECT COALESCE(MAX(queue_order), -1) as mo FROM construction_queue WHERE facility_id = ? AND status IN ('queued', 'active')",
            (facility_id,),
        ).fetchone()
    else:
        max_order = conn.execute(
            "SELECT COALESCE(MAX(queue_order), -1) as mo FROM construction_queue WHERE location_id = ? AND status IN ('queued', 'active')",
            (location_id,),
        ).fetchone()
    next_order = (max_order["mo"] if max_order else -1) + 1

    queue_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO construction_queue (id, location_id, recipe_id, queue_order, status, created_by, corp_id, facility_id)
        VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
        """,
        (queue_id, location_id, recipe_id, next_order, username, corp_id, facility_id),
    )
    conn.commit()

    return {
        "queue_id": queue_id,
        "recipe_id": recipe_id,
        "recipe_name": recipe.get("name", recipe_id),
        "queue_order": next_order,
    }


def dequeue_construction(
    conn: sqlite3.Connection,
    queue_id: str,
    username: str,
    corp_id: str = "",
) -> Dict[str, Any]:
    """Remove an item from the construction queue. Active items get cancelled with partial refund."""
    item = conn.execute(
        "SELECT * FROM construction_queue WHERE id = ?",
        (queue_id,),
    ).fetchone()
    if not item:
        raise ValueError("Queue item not found")

    item_corp_id = str(item["corp_id"] or "")
    if corp_id and item_corp_id and corp_id != item_corp_id:
        raise ValueError("You do not own this queue item")

    if item["status"] == "active":
        # Partial refund based on progress
        now = game_now_s()
        started = float(item["started_at"] or now)
        completes = float(item["completes_at"] or now)
        total_dur = max(1.0, completes - started)
        elapsed = min(now - started, total_dur)
        progress = min(1.0, elapsed / total_dur)
        refund_fraction = max(0.0, 1.0 - progress)

        if refund_fraction > 0.01:
            import main as _main
            inputs = json.loads(item["inputs_json"] or "[]")
            item_fid = str(item["facility_id"] or "") if "facility_id" in item.keys() else ""
            for inp in inputs:
                inp_id = str(inp.get("item_id") or "").strip()
                inp_qty = float(inp.get("qty") or 0.0)
                refund_qty = inp_qty * refund_fraction
                if inp_id and refund_qty > 0.01:
                    _main.add_resource_to_location_inventory(
                        conn, item["location_id"], inp_id, refund_qty, corp_id=item_corp_id
                    )

    conn.execute(
        "UPDATE construction_queue SET status = 'cancelled', completed_at = ? WHERE id = ?",
        (game_now_s(), queue_id),
    )
    conn.commit()

    return {"dequeued": True, "queue_id": queue_id}


def reorder_construction_queue(
    conn: sqlite3.Connection,
    location_id: str,
    queue_ids: List[str],
    corp_id: str = "",
    facility_id: str = "",
) -> Dict[str, Any]:
    """Reorder construction queue items. queue_ids in desired order."""
    if not queue_ids:
        return {"reordered": True, "count": 0}

    placeholders = ",".join("?" for _ in queue_ids)
    if facility_id:
        rows = conn.execute(
            f"""
            SELECT id, corp_id FROM construction_queue
            WHERE facility_id = ? AND id IN ({placeholders})
            """,
            [facility_id] + queue_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT id, corp_id FROM construction_queue
            WHERE location_id = ? AND id IN ({placeholders})
            """,
            [location_id] + queue_ids,
        ).fetchall()

    found_ids = {str(r["id"]) for r in rows}
    missing = [qid for qid in queue_ids if qid not in found_ids]
    if missing:
        raise ValueError("Some construction queue items are invalid for this scope")

    if corp_id:
        for r in rows:
            row_corp_id = str(r["corp_id"] or "")
            if row_corp_id and row_corp_id != corp_id:
                raise ValueError("You do not own one or more queue items")

    for i, qid in enumerate(queue_ids):
        if facility_id:
            conn.execute(
                "UPDATE construction_queue SET queue_order = ? WHERE id = ? AND facility_id = ?",
                (i, qid, facility_id),
            )
        else:
            conn.execute(
                "UPDATE construction_queue SET queue_order = ? WHERE id = ? AND location_id = ?",
                (i, qid, location_id),
            )
    conn.commit()
    return {"reordered": True, "count": len(queue_ids)}

