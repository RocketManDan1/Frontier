# Industry & Production

Reference for equipment deployment, production jobs, mining, power balance, recipes, and research gating.

---

## Overview

The industry system lets players deploy equipment at locations (orbital stations and surface sites), run production recipes to refine resources and construct parts, and mine raw materials from surface sites. Everything uses the **settle-on-access** pattern — jobs complete lazily when the relevant location's data is next requested.

All industry logic lives in two files:

| File | Role |
|---|---|
| `industry_service.py` | Business logic: deploy, undeploy, start/cancel jobs, settle, power balance |
| `industry_router.py` | API routes, request models, auth + corp-id wiring |

---

## Equipment Deployment

### Deployable Categories

Six equipment types can be deployed from location inventory:

| Category | Role | Surface-only? |
|---|---|---|
| `refinery` | Runs refinery/factory recipes | No |
| `constructor` | Runs shipyard (construction) recipes, mines resources | **Yes** |
| `robonaut` | Mines water ice → water | **Yes** |
| `reactor` | Produces thermal energy (MWth) | No |
| `generator` | Converts thermal → electric (MWe) + waste heat | No |
| `radiator` | Rejects waste heat | No |

### Deploy Flow

`POST /api/industry/deploy` with `{location_id, item_id}`:

1. Validates the location exists.
2. Looks up `item_id` across all deployable catalogs (refinery, constructor, robonaut, reactor, generator, radiator).
3. Checks category is in the deployable set.
4. **Surface restriction:** Constructors and robonauts can only deploy at surface sites (locations with a `surface_sites` row). Constructors also have a `min_surface_gravity_ms2` check.
5. Consumes 1× the part from location inventory (via `consume_parts_from_location_inventory`).
6. Inserts a `deployed_equipment` row with `status = 'idle'` and a `config_json` blob built from catalog properties.

### Config JSON by Category

Each category stores different properties in `config_json`:

| Category | Config keys |
|---|---|
| refinery | `mass_kg`, `electric_mw`, `specialization`, `throughput_mult`, `efficiency`, `max_recipe_tier`, `max_concurrent_recipes` |
| constructor | `mass_kg`, `electric_mw`, `mining_rate_kg_per_hr`, `construction_rate_kg_per_hr`, `excavation_type` |
| robonaut | `mass_kg`, `electric_mw`, `mining_rate_kg_per_hr`, `allowed_mining_resources: ["water_ice"]`, `mining_output_resource_id: "water"` |
| reactor | `mass_kg`, `electric_mw`, `thermal_mw` |
| generator | `mass_kg`, `electric_mw`, `thermal_mw_input`, `conversion_efficiency`, `waste_heat_mw` |
| radiator | `mass_kg`, `electric_mw`, `heat_rejection_mw`, `operating_temp_k` |

### Undeploy Flow

`POST /api/industry/undeploy` with `{equipment_id}`:

1. Finds the equipment row.
2. Verifies corp ownership.
3. **Blocks if any active jobs** are linked to this equipment — must cancel them first.
4. Restores the part to location inventory (via `add_part_to_location_inventory`).
5. Deletes completed/cancelled job history for that equipment.
6. Deletes the `deployed_equipment` row.

---

## Production Jobs

Production jobs consume input resources, occupy equipment for a duration, and deliver output items on completion. There are two sub-types:

| Job type | Equipment | Runs | Key difference |
|---|---|---|---|
| `refine` | Refinery | Refinery/factory recipes | Specialization matching, efficiency scaling |
| `construct` | Constructor | Shipyard recipes | Construction rate scaling, research gating |

### Starting a Job

`POST /api/industry/jobs/start` with `{equipment_id, recipe_id, batch_count}`:

1. Validates equipment exists and is `idle`.
2. Checks equipment is a refinery or constructor.
3. Verifies corp ownership.
4. Checks concurrent job limit (refinery `max_concurrent_recipes`, default 1).
5. Loads recipe from `catalog_service.load_recipe_catalog()`.
6. Routes by equipment type:
   - **Constructor** → recipe must have `facility_type: "shipyard"` → `job_type = "construct"`
   - **Refinery** → recipe must NOT be `"shipyard"` → `job_type = "refine"`
7. **Specialization check** (refineries only): recipe's `refinery_category` must match the refinery's `specialization`. Exception: recipes with category `"all_refineries"` or empty category run on any refinery.
8. Validates input availability for `qty × batch_count`.
9. **Consumes inputs** immediately (delta-based upsert with negative quantities).
10. Computes duration and outputs (see formulas below).
11. Inserts `production_jobs` row with `status = 'active'`, marks equipment `status = 'active'`.

### Duration Formula

$$\text{duration\_s} = \frac{\text{base\_time\_s} \times \text{batch\_count}}{\text{throughput\_mult}}$$

Where `throughput_mult` depends on equipment type:
- **Refinery:** Uses `throughput_mult` from config (default 1.0).
- **Constructor:** Uses `construction_rate_kg_per_hr / 50.0` (normalized around a 50 kg/hr baseline).

### Output Formula

$$\text{output\_qty} = \text{recipe\_output\_qty} \times \text{efficiency} \times \text{batch\_count}$$

- `efficiency` comes from refinery config (default 1.0). Constructors always use efficiency = 1.0.
- Byproducts are also scaled by `efficiency × batch_count`.

### Cancelling a Job

`POST /api/industry/jobs/cancel` with `{job_id}`:

1. Finds the active job.
2. Verifies corp ownership.
3. Computes progress:

$$\text{progress} = \min\!\Bigl(1,\;\frac{\text{now} - \text{started\_at}}{\text{completes\_at} - \text{started\_at}}\Bigr)$$

4. Computes refund fraction = `1.0 - progress`.
5. Returns each input resource × refund fraction to location inventory.
6. Marks job `status = 'cancelled'`, equipment back to `'idle'`.

**Example:** A job 30% complete refunds 70% of each input resource.

---

## Mining

Mining is a **continuous** job — it runs indefinitely until manually stopped. Resources accumulate proportionally to elapsed time.

### Who Can Mine

- **Constructors** — mine any resource at the surface site (generic mining).
- **Robonauts** — mine water ice only, output is water (ISRU conversion).

### Starting Mining

`POST /api/industry/mining/start` with `{equipment_id, resource_id}`:

1. Validates equipment is idle constructor or robonaut.
2. Verifies the location is a surface site.
3. Checks the org has **prospected** this site (required before mining).
4. Validates the resource exists in `surface_site_resources` for this site.
5. Robonaut restriction: can only mine resources in `allowed_mining_resources` (default: `["water_ice"]`), and output is always the configured `mining_output_resource_id` (default: `"water"`).
6. Computes effective rate:

$$\text{effective\_rate} = \text{mining\_rate\_kg\_per\_hr} \times \text{mass\_fraction}$$

7. Creates a `production_jobs` row with `job_type = 'mine'`, `completes_at` = 100 years in the future (sentinel).
8. `inputs_json` stores `{"last_settled": <now>, "total_mined_kg": 0}` for incremental settlement.

### Mining Settlement

`_settle_mining_jobs()` runs on every access to the location:

```
elapsed_hr     = (now - last_settled) / 3600
mined_kg       = effective_rate × elapsed_hr
```

If `mined_kg > 0.01`: delivers resources to location inventory, updates `last_settled` and `total_mined_kg` in `inputs_json`.

### Stopping Mining

`POST /api/industry/mining/stop` with `{job_id}`:

1. Settles pending mined resources.
2. Marks job `status = 'completed'`, equipment `'idle'`.
3. Returns `total_mined_kg` — the lifetime total for this job.

---

## Settle-on-Access Pattern

Nothing ticks in the background. Instead, `settle_industry(conn, location_id)` is called at the top of every industry-related API route. It runs two settlement passes:

### Production Settlement (`_settle_production_jobs`)

Finds all `production_jobs` where `status = 'active'` AND `completes_at ≤ now`.

For each completed job:
1. Parses `outputs_json` — a list of `{item_id, qty}` entries.
2. For each output:
   - If `item_id` matches a known part catalog → `add_part_to_location_inventory()`
   - Otherwise → `add_resource_to_location_inventory()`
3. Marks job `status = 'completed'`.
4. Sets equipment back to `status = 'idle'`.

### Mining Settlement (`_settle_mining_jobs`)

Finds all `production_jobs` where `status = 'active'` AND `job_type = 'mine'`.

For each mining job:
1. Reads `mining_rate_kg_per_hr` from equipment `config_json`.
2. Looks up `mass_fraction` from `surface_site_resources`.
3. Computes `elapsed_hr` since `last_settled`.
4. Delivers `effective_rate × elapsed_hr` kg to location inventory.
5. Updates `last_settled` and `total_mined_kg` trackers.

---

## Recipe Schema

Recipes are JSON files in `items/Recipes/`. Key fields:

```json
{
  "recipe_id": "carbon_composites",
  "name": "Carbon Composites",
  "facility_type": "factory",
  "refinery_category": "lithic_processing",
  "min_tech_tier": 1,
  "build_time_s": 3600,
  "output_item_id": "carbon_composites",
  "output_qty": 100.0,
  "inputs": [
    {"item_id": "regolith", "qty": 200.0}
  ],
  "byproducts": [
    {"item_id": "slag", "qty": 50.0}
  ]
}
```

| Field | Description |
|---|---|
| `recipe_id` | Unique identifier |
| `facility_type` | `"factory"` (refinery) or `"shipyard"` (constructor) |
| `refinery_category` | Specialization required: `lithic_processing`, `metallurgy`, `volatiles_cryogenics`, `nuclear_exotic`, or `all_refineries` |
| `min_tech_tier` | Minimum tech level needed (for research gating on shipyard recipes) |
| `build_time_s` | Base build time in game-seconds (before throughput scaling) |
| `output_item_id` | What the recipe produces |
| `output_qty` | Base output quantity per batch (before efficiency scaling) |
| `inputs` | Array of `{item_id, qty}` consumed per batch |
| `byproducts` | Array of `{item_id, qty}` additional outputs per batch |

### Refinery Specializations

Four refinery specializations gate which factory recipes a refinery can run:

| Specialization | Example recipes |
|---|---|
| `lithic_processing` | Carbon composites, advanced ceramics, biosphere materials |
| `metallurgy` | Advanced aerospace alloys |
| `volatiles_cryogenics` | Cryo polymers, fusion fuel |
| `nuclear_exotic` | (Nuclear processing recipes) |

Recipes with `refinery_category: "all_refineries"` or empty category can run on any refinery.

---

## Research Gating for Shipyard Recipes

When the recipe list is built for a location (`get_available_recipes_for_location`), shipyard recipes are filtered by research unlocks:

1. Look up the recipe's `output_item_id` in all part catalogs to find the output's **category** (thruster, reactor, generator, etc.).
2. Map the category to a research category via `_SHIPYARD_OUTPUT_TO_RESEARCH_CATEGORY`:

   | Part category | Research category |
   |---|---|
   | thruster | thrusters |
   | reactor | reactors |
   | generator | generators |
   | radiator | radiators |
   | robonaut | robonauts |
   | constructor | constructors |
   | refinery | refineries |

3. Build the required node ID: `"{research_category}_lvl_{min_tech_tier}"`.
4. If the org hasn't unlocked that node → recipe is hidden.

---

## Power & Thermal Balance

Each site with deployed equipment has a computed power balance. This is informational for the UI — there is no penalty for power deficit currently, but the `power_ok` flag signals to the player.

### Energy Flow

```
Reactors → thermal_mw (MWth)
    ↓
Generators (consume thermal → produce electric + waste_heat)
    ↓                          ↓
Refineries/Constructors    Radiators (reject waste heat)
(consume electric_mw)
```

### Generator Throttle

If total `thermal_mw` from reactors < total `thermal_mw_input` demanded by generators, generators throttle proportionally:

$$\text{gen\_throttle} = \frac{\text{reactor\_thermal\_mw}}{\text{generator\_thermal\_demand}}$$

$$\text{actual\_electric} = \text{electric\_supply} \times \text{gen\_throttle}$$

If thermal supply ≥ demand, `gen_throttle = 1.0`.

### Balance Output

`compute_site_power_balance()` returns:

| Field | Description |
|---|---|
| `thermal_mw_supply` | Total reactor output |
| `thermal_mw_consumed` | Total generator thermal input |
| `electric_mw_supply` | Generator electric output (after throttle) |
| `electric_mw_demand` | Active refinery + constructor demand |
| `electric_mw_surplus` | Supply - demand (negative = deficit) |
| `waste_heat_mw` | Generator waste heat (after throttle) |
| `heat_rejection_mw` | Total radiator capacity |
| `gen_throttle` | 0.0–1.0 throttle ratio |
| `power_ok` | `true` if electric surplus ≥ 0 |

---

## Job State Machine

```
           start
             │
             ▼
         ┌─────────┐
         │  active  │
         └────┬─────┘
              │
      ┌───────┴───────┐
      │               │
  completes_at     user cancels
   ≤ now              │
      │               ▼
      ▼         ┌───────────┐
 ┌──────────┐   │ cancelled │  ← partial refund
 │completed │   └───────────┘
 └──────────┘
      ↑
 (settle or
  manual stop
  for mining)
```

Equipment transitions: `idle → active` (on job start) → `idle` (on job complete/cancel/stop).

---

## API Route Reference

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/sites` | All locations with industry summaries (equipment, jobs, inventory, ships) |
| GET | `/api/sites/{location_id}` | Single site detail: inventory, equipment, jobs, minable resources |
| GET | `/api/industry/{location_id}` | Full industrial overview: equipment, jobs, recipes, power balance |
| POST | `/api/industry/deploy` | Deploy equipment from inventory to a location |
| POST | `/api/industry/undeploy` | Undeploy equipment back to inventory |
| POST | `/api/industry/jobs/start` | Start a production job (refine or construct) |
| POST | `/api/industry/jobs/cancel` | Cancel an active production job (partial refund) |
| POST | `/api/industry/mining/start` | Start mining at a surface site |
| POST | `/api/industry/mining/stop` | Stop an active mining job |

---

## Database Tables

### `deployed_equipment`

| Column | Type | Description |
|---|---|---|
| `id` | TEXT PK | UUID |
| `location_id` | TEXT | Where equipment is deployed |
| `item_id` | TEXT | Catalog item ID |
| `name` | TEXT | Display name |
| `category` | TEXT | refinery / constructor / robonaut / reactor / generator / radiator |
| `deployed_at` | REAL | Game-time of deployment |
| `deployed_by` | TEXT | Actor who deployed |
| `status` | TEXT | `idle` or `active` |
| `config_json` | TEXT | Category-specific config blob |
| `corp_id` | TEXT | Owning corporation |

### `production_jobs`

| Column | Type | Description |
|---|---|---|
| `id` | TEXT PK | UUID |
| `location_id` | TEXT | Where the job runs |
| `equipment_id` | TEXT FK | Which equipment is running it |
| `job_type` | TEXT | `refine`, `construct`, or `mine` |
| `recipe_id` | TEXT | Recipe used (null for mining) |
| `resource_id` | TEXT | Resource being mined (null for production) |
| `status` | TEXT | `active`, `completed`, or `cancelled` |
| `started_at` | REAL | Game-time job started |
| `completes_at` | REAL | Game-time job will finish (far-future sentinel for mining) |
| `completed_at` | REAL | Game-time job actually completed/cancelled |
| `inputs_json` | TEXT | Consumed inputs (list for production, dict for mining with `last_settled`, `total_mined_kg`) |
| `outputs_json` | TEXT | Expected outputs `[{item_id, qty}]` or mining rate info |
| `created_by` | TEXT | Actor who started the job |
| `corp_id` | TEXT | Owning corporation |
