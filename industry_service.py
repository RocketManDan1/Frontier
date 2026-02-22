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
from typing import Any, Dict, List, Optional, Tuple

import catalog_service
from sim_service import game_now_s


# ── Helpers ────────────────────────────────────────────────────────────────────


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def _load_resource_name(resource_id: str) -> str:
    resources = catalog_service.load_resource_catalog()
    res = resources.get(resource_id)
    return str(res.get("name") or resource_id) if res else resource_id


# ── Settle Jobs (on-access pattern) ───────────────────────────────────────────


def settle_industry(conn: sqlite3.Connection, location_id: Optional[str] = None) -> None:
    """
    Settle all completed production + mining jobs.
    If location_id is given, only settle for that location (performance).
    Otherwise settles globally.
    """
    now = game_now_s()
    _settle_production_jobs(conn, now, location_id)
    _settle_mining_jobs(conn, now, location_id)


def _settle_production_jobs(conn: sqlite3.Connection, now: float, location_id: Optional[str] = None) -> None:
    """Complete production jobs whose completes_at <= now."""
    where = "WHERE pj.status = 'active' AND pj.completes_at <= ?"
    params: list = [now]
    if location_id:
        where += " AND pj.location_id = ?"
        params.append(location_id)

    rows = conn.execute(
        f"""
        SELECT pj.id, pj.location_id, pj.equipment_id, pj.job_type,
               pj.recipe_id, pj.outputs_json, pj.completes_at, pj.corp_id
        FROM production_jobs pj
        {where}
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
        catalog_service.load_constructor_catalog,
        catalog_service.load_refinery_catalog,
        catalog_service.load_thruster_main_catalog,
        catalog_service.load_reactor_catalog,
        catalog_service.load_generator_catalog,
        catalog_service.load_radiator_catalog,
        catalog_service.load_robonaut_catalog,
        catalog_service.load_storage_catalog,
    ):
        part_catalogs.update(loader())

    resource_catalog = catalog_service.load_resource_catalog()

    for row in rows:
        job_id = row["id"]
        loc_id = row["location_id"]
        equip_id = row["equipment_id"]
        job_corp_id = str(row["corp_id"] or "") if "corp_id" in row.keys() else ""

        # Deliver outputs to location inventory
        outputs = json.loads(row["outputs_json"] or "[]")
        for out in outputs:
            item_id = str(out.get("item_id") or "").strip()
            qty = float(out.get("qty") or 0.0)
            if not item_id or qty <= 0:
                continue

            if item_id in part_catalogs:
                # It's an equipment part — add with proper name, mass, and type
                part_entry = dict(part_catalogs[item_id])
                _main.add_part_to_location_inventory(conn, loc_id, part_entry, count=qty, corp_id=job_corp_id)
            else:
                # It's a resource or refined material
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


def _settle_mining_jobs(conn: sqlite3.Connection, now: float, location_id: Optional[str] = None) -> None:
    """
    Mining jobs are continuous — accumulate mined resources based on elapsed time.
    Mining jobs have job_type='mine', status='active', and never auto-complete.
    We store the last-settled timestamp in inputs_json as {"last_settled": <game_ts>}.
    """
    where = "WHERE pj.status = 'active' AND pj.job_type = 'mine'"
    params: list = []
    if location_id:
        where += " AND pj.location_id = ?"
        params.append(location_id)

    rows = conn.execute(
        f"""
        SELECT pj.id, pj.location_id, pj.equipment_id, pj.resource_id,
               pj.inputs_json, pj.started_at, pj.outputs_json, pj.corp_id
        FROM production_jobs pj
        {where}
        """,
        params,
    ).fetchall()

    if not rows:
        return

    import main as _main

    for row in rows:
        job_id = row["id"]
        loc_id = row["location_id"]
        resource_id = row["resource_id"]
        equip_id = row["equipment_id"]
        job_corp_id = str(row["corp_id"] or "") if "corp_id" in row.keys() else ""

        if not resource_id:
            continue

        # Get mining rate from equipment config
        equip = conn.execute(
            "SELECT config_json FROM deployed_equipment WHERE id = ?",
            (equip_id,),
        ).fetchone()
        if not equip:
            continue

        config = json.loads(equip["config_json"] or "{}")
        rate_kg_hr = float(config.get("mining_rate_kg_per_hr") or 0.0)
        if rate_kg_hr <= 0:
            continue

        # Scale mining rate by the resource's mass fraction at this site
        site_res = conn.execute(
            "SELECT mass_fraction FROM surface_site_resources WHERE site_location_id = ? AND resource_id = ?",
            (loc_id, resource_id),
        ).fetchone()
        mass_fraction = float(site_res["mass_fraction"]) if site_res else 0.0
        effective_rate = rate_kg_hr * mass_fraction
        if effective_rate <= 0:
            continue

        # Calculate elapsed time since last settle
        inputs = json.loads(row["inputs_json"] or "{}")
        last_settled = float(inputs.get("last_settled") or row["started_at"])
        elapsed_s = max(0.0, now - last_settled)
        elapsed_hr = elapsed_s / 3600.0
        mined_kg = effective_rate * elapsed_hr

        if mined_kg > 0.01:  # threshold to avoid tiny writes
            _main.add_resource_to_location_inventory(conn, loc_id, resource_id, mined_kg, corp_id=job_corp_id)

            # Update last_settled
            inputs["last_settled"] = now
            total_mined = float(inputs.get("total_mined_kg") or 0.0) + mined_kg
            inputs["total_mined_kg"] = total_mined
            conn.execute(
                "UPDATE production_jobs SET inputs_json = ? WHERE id = ?",
                (_json_dumps(inputs), job_id),
            )

    conn.commit()


# ── Deploy / Undeploy ──────────────────────────────────────────────────────────


DEPLOYABLE_CATEGORIES = ("refinery", "constructor", "reactor", "generator", "radiator")


def _resolve_deployable_catalog_entry(item_id: str) -> Optional[Dict[str, Any]]:
    """Look up an item across all deployable catalogs."""
    for loader in (
        catalog_service.load_refinery_catalog,
        catalog_service.load_constructor_catalog,
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
) -> Dict[str, Any]:
    """
    Deploy equipment from location inventory to the site.
    Supports refineries, constructors, reactors, generators, and radiators.
    Consumes the part from inventory and creates a deployed_equipment row.
    """
    catalog_entry = _resolve_deployable_catalog_entry(item_id)
    if not catalog_entry:
        raise ValueError(f"Item '{item_id}' is not deployable equipment")

    category = str(catalog_entry.get("category_id") or catalog_entry.get("type") or "")
    if category not in DEPLOYABLE_CATEGORIES:
        raise ValueError(f"Item '{item_id}' is not deployable (category: {category})")

    # Constructors require surface gravity — check if location is a surface site
    if category == "constructor":
        site = conn.execute(
            "SELECT gravity_m_s2 FROM surface_sites WHERE location_id = ?",
            (location_id,),
        ).fetchone()
        if not site:
            raise ValueError("Constructors can only be deployed at surface sites")
        min_grav = float(catalog_entry.get("min_surface_gravity_ms2") or 0.0)
        site_grav = float(site["gravity_m_s2"])
        if site_grav < min_grav:
            raise ValueError(
                f"Surface gravity {site_grav:.2f} m/s² is below minimum {min_grav:.2f} m/s²"
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
    elif category == "constructor":
        config.update({
            "mining_rate_kg_per_hr": catalog_entry.get("mining_rate_kg_per_hr", 0),
            "construction_rate_kg_per_hr": catalog_entry.get("construction_rate_kg_per_hr", 0),
            "excavation_type": catalog_entry.get("excavation_type", ""),
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
          (id, location_id, item_id, name, category, deployed_at, deployed_by, status, config_json, corp_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?)
        """,
        (equip_id, location_id, item_id, catalog_entry.get("name", item_id),
         category, now, username, _json_dumps(config), corp_id),
    )
    conn.commit()

    return {
        "id": equip_id,
        "location_id": location_id,
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

    # Check for active jobs
    active_jobs = conn.execute(
        "SELECT COUNT(*) as cnt FROM production_jobs WHERE equipment_id = ? AND status = 'active'",
        (equipment_id,),
    ).fetchone()
    if active_jobs and active_jobs["cnt"] > 0:
        raise ValueError("Cannot undeploy equipment with active jobs — cancel jobs first")

    location_id = equip["location_id"]
    item_id = equip["item_id"]
    category = equip["category"]
    equip_corp_id = str(equip["corp_id"] or "") if "corp_id" in equip.keys() else ""

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

    # Delete equipment and its completed jobs
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

        elif cat in ("refinery", "constructor"):
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
    if equip["category"] not in ("refinery", "constructor"):
        raise ValueError("Production jobs require a refinery or constructor")

    config = json.loads(equip["config_json"] or "{}")
    location_id = equip["location_id"]

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

    if equip_category == "constructor":
        # Constructors can only run shipyard recipes (construction)
        if facility_type != "shipyard":
            raise ValueError("Constructors can only run construction (shipyard) recipes")
        job_type = "construct"
    else:
        # Refineries run refinery/factory recipes
        if facility_type == "shipyard":
            raise ValueError("Refineries cannot run construction recipes — use a constructor")
        job_type = "refine"

        # Check specialization match (refineries only)
        specialization = str(config.get("specialization") or "")
        recipe_category = str(recipe.get("refinery_category") or "")
        if specialization and recipe_category and specialization != recipe_category:
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

        # Check availability
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
    if equip_category == "constructor":
        # Constructors use construction_rate_kg_per_hr as a speed multiplier
        # Higher rate = faster builds.  Normalize around 50 kg/hr baseline.
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
           started_at, completes_at, inputs_json, outputs_json, created_by, corp_id)
        VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
        """,
        (job_id, location_id, equipment_id, job_type, recipe_id, now, completes_at,
         _json_dumps([{"item_id": inp["item_id"], "qty": float(inp.get("qty") or 0) * batch_count} for inp in inputs if inp.get("item_id")]),
         _json_dumps(outputs), username, corp_id),
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
    Start a continuous mining job on a deployed constructor at a surface site.
    Constructor produces resource_id at its mining_rate_kg_per_hr.
    """
    equip = conn.execute(
        "SELECT * FROM deployed_equipment WHERE id = ?",
        (equipment_id,),
    ).fetchone()
    if not equip:
        raise ValueError("Equipment not found")
    if equip["status"] != "idle":
        raise ValueError(f"Equipment is currently {equip['status']}, not idle")
    if equip["category"] != "constructor":
        raise ValueError("Mining requires a constructor")

    location_id = equip["location_id"]
    config = json.loads(equip["config_json"] or "{}")

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

    base_rate = float(config.get("mining_rate_kg_per_hr") or 0.0)
    if base_rate <= 0:
        raise ValueError("Constructor has no mining capability")

    # Effective mining rate = base rate × resource mass fraction at this site
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
           started_at, completes_at, inputs_json, outputs_json, created_by, corp_id)
        VALUES (?, ?, ?, 'mine', ?, 'active', ?, ?, ?, ?, ?, ?)
        """,
        (job_id, location_id, equipment_id, resource_id, now, far_future,
         _json_dumps({"last_settled": now, "total_mined_kg": 0}),
         _json_dumps([{"item_id": resource_id, "rate_kg_per_hr": effective_rate}]),
         username, corp_id),
    )

    conn.execute("UPDATE deployed_equipment SET status = 'active' WHERE id = ?", (equipment_id,))
    conn.commit()

    return {
        "job_id": job_id,
        "equipment_id": equipment_id,
        "resource_id": resource_id,
        "resource_name": _load_resource_name(resource_id),
        "rate_kg_per_hr": effective_rate,
        "started_at": now,
    }


def stop_mining_job(
    conn: sqlite3.Connection,
    job_id: str,
    username: str,
) -> Dict[str, Any]:
    """Stop a running mining job. Settles any un-collected mined resources first."""
    job = conn.execute(
        "SELECT * FROM production_jobs WHERE id = ? AND status = 'active' AND job_type = 'mine'",
        (job_id,),
    ).fetchone()
    if not job:
        raise ValueError("Active mining job not found")

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


def get_deployed_equipment(conn: sqlite3.Connection, location_id: str) -> List[Dict[str, Any]]:
    """List all deployed equipment at a location."""
    rows = conn.execute(
        """
        SELECT id, location_id, item_id, name, category, deployed_at,
               deployed_by, status, config_json, corp_id
        FROM deployed_equipment
        WHERE location_id = ?
        ORDER BY category, name
        """,
        (location_id,),
    ).fetchall()

    result = []
    for r in rows:
        config = json.loads(r["config_json"] or "{}")
        entry = {
            "id": r["id"],
            "location_id": r["location_id"],
            "item_id": r["item_id"],
            "name": r["name"],
            "category": r["category"],
            "deployed_at": float(r["deployed_at"]),
            "deployed_by": r["deployed_by"],
            "status": r["status"],
            "config": config,
            "corp_id": str(r["corp_id"] or ""),
        }
        result.append(entry)
    return result


def get_active_jobs(conn: sqlite3.Connection, location_id: str) -> List[Dict[str, Any]]:
    """List all active jobs at a location."""
    now = game_now_s()
    rows = conn.execute(
        """
        SELECT pj.id, pj.location_id, pj.equipment_id, pj.job_type,
               pj.recipe_id, pj.resource_id, pj.status,
               pj.started_at, pj.completes_at, pj.inputs_json, pj.outputs_json,
               pj.created_by,
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
            entry["resource_id"] = r["resource_id"]
            entry["resource_name"] = _load_resource_name(r["resource_id"] or "")
            entry["rate_kg_per_hr"] = float(rate_info.get("rate_kg_per_hr") or 0)
            entry["total_mined_kg"] = float(inputs.get("total_mined_kg") or 0)
            entry["progress"] = None  # Mining has no end

        result.append(entry)
    return result


def get_job_history(conn: sqlite3.Connection, location_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """List recent completed/cancelled jobs at a location."""
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
) -> List[Dict[str, Any]]:
    """
    Get all recipes that could be run at a location, based on deployed equipment.
    - Refinery/factory recipes matched to deployed refineries (by specialization + tier).
    - Shipyard recipes matched to deployed constructors (any constructor can build any).
    Also annotates each recipe with whether the location has sufficient inputs.
    """
    equipment = get_deployed_equipment(conn, location_id)
    refineries = [e for e in equipment if e["category"] == "refinery"]
    constructors = [e for e in equipment if e["category"] == "constructor"]

    if not refineries and not constructors:
        return []

    # Gather location inventory for availability checks
    import main as _main
    inv = _main.get_location_inventory_payload(conn, location_id, corp_id=corp_id or None)
    resource_stock: Dict[str, float] = {}
    for res in inv.get("resources") or []:
        rid = str(res.get("resource_id") or res.get("item_id") or "")
        resource_stock[rid] = float(res.get("quantity") or 0)

    all_recipes = catalog_service.load_recipe_catalog()
    resource_catalog = catalog_service.load_resource_catalog()

    # Build output_item_id → category_id map from all part catalogs
    _output_category_map: Dict[str, str] = {}
    for _loader, _cat in [
        (catalog_service.load_thruster_main_catalog, "thruster"),
        (catalog_service.load_reactor_catalog, "reactor"),
        (catalog_service.load_generator_catalog, "generator"),
        (catalog_service.load_radiator_catalog, "radiator"),
        (catalog_service.load_robonaut_catalog, "robonaut"),
        (catalog_service.load_constructor_catalog, "constructor"),
        (catalog_service.load_refinery_catalog, "refinery"),
    ]:
        try:
            for _item_id in _loader():
                _output_category_map[_item_id] = _cat
        except Exception:
            pass

    result = []
    for recipe in sorted(all_recipes.values(), key=lambda r: r.get("name", "")):
        recipe_cat = str(recipe.get("refinery_category") or "")
        facility_type = str(recipe.get("facility_type") or "")

        compatible_refineries = []
        compatible_constructors = []

        if facility_type == "shipyard":
            # Shipyard recipes: any constructor can build
            compatible_constructors = list(constructors)
        else:
            # Refinery/factory recipes: match by specialization (no tier restriction)
            for ref in refineries:
                cfg = ref.get("config") or {}
                spec = str(cfg.get("specialization") or "")
                if spec == recipe_cat:
                    compatible_refineries.append(ref)

        if not compatible_refineries and not compatible_constructors:
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
        output_category = _output_category_map.get(out_id, "other")

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


def get_location_industry_summary(conn: sqlite3.Connection, location_id: str) -> Dict[str, Any]:
    """Get a summary of industrial activity at a location."""
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
