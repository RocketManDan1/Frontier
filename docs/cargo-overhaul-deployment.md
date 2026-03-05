# Cargo System Overhaul — Deployment Document

## Summary

Remove the **Storage container category** entirely. Replace the volume-based, per-container, per-phase cargo system with a **mass-budget model** featuring a **power-law containment surcharge** that rewards bulk shipping (economies of scale) and penalizes low-density cargo.

**Fuel capacity** (water) moves from water tank parts to a new `fuel_capacity_kg` field on **thruster** definitions — your propellant tankage is bundled with your engine, sized to match its class.

---

## Design Specification

### Cargo Capacity

Ships gain a new stat: **`cargo_capacity_kg`**, derived from thruster parts. Each thruster defines how much cargo its associated hull structure can carry. There are no separate container items.

Transfers to a ship check:

```
current_cargo_mass_kg + transfer_mass_kg ≤ cargo_capacity_kg
```

No phase matching. No per-container routing. One number.

### Cargo Mass Surcharge (Power Law)

When computing delta-v and acceleration, cargo mass is adjusted upward by a **containment surcharge** that models the mass of racks, bins, tie-downs, thermal management, and pressure shells:

```
overhead_kg = A × cargo_kg ^ B × density_modifier
effective_cargo_mass = cargo_kg + overhead_kg
```

**Constants:** `A = 0.456`, `B = 0.867`

Anchored at:
- ~20% overhead at 500 kg (small courier)
- ~8% overhead at 500,000 kg (bulk freighter)

### Density Modifier

Resources already have `mass_per_m3_kg` (density). Low-density cargo costs more to contain:

```
density_modifier = (REF_DENSITY / avg_cargo_density) ^ DENSITY_EXP
```

**Constants:** `REF_DENSITY = 2500.0 kg/m³`, `DENSITY_EXP = 0.4`

| Resource type | Density (kg/m³) | Modifier | Net effect |
|---|---|---|---|
| Precious/Rare Metals | 20,000 | 0.51 | 49% cheaper containment |
| Radioactives | 11,000 | 0.60 | 40% cheaper |
| Structural Alloys | 7,000 | 0.73 | 27% cheaper |
| Iron Oxides | 5,200 | 0.80 | 20% cheaper |
| Silicate Rock (regolith) | 2,700 | 0.97 | ~Baseline |
| Carbon Composites | 1,600 | 1.17 | 17% penalty |
| Water | 1,000 | 1.46 | 46% penalty |
| Nitrogen Volatiles | 800 | 1.56 | 56% penalty |
| High-Energy Propellant | 500 | 1.84 | 84% penalty |
| Fusion Fuel | 200 | 2.84 | 184% penalty |
| Helium-3 | 60 | 4.59 | 359% penalty |

**Mixed cargo:** compute weighted average density from all loaded resources.

### Fuel Capacity

**Fuel capacity moves to thrusters.** Each thruster JSON gains a `fuel_capacity_kg` field representing the integrated propellant tankage sized for that engine class. The `derive_ship_stats_from_parts()` function sums `fuel_capacity_kg` from all thruster parts instead of looking for water tank `capacity_m3` fields.

Example values (to be tuned):

| Thruster | Current mass_t | Suggested fuel_capacity_kg |
|---|---|---|
| SCN-1 Pioneer (TL1) | 20t | 50,000 |
| SCN-2 Frontier (TL1.5) | 25t | 75,000 |
| ASCN-1 Venture (TL2) | 30t | 100,000 |
| ASCN-2 Atlas (TL2.5) | 35t | 125,000 |
| CGN-1 Helios (TL3) | 50t | 200,000 |
| CGN-2 Prometheus (TL3.5) | 60t | 300,000 |
| OGN-1 Icarus (TL4) | 70t | 500,000 |
| OGN-2 Daedalus (TL4.5) | 80t | 750,000 |

> **Note:** These are starter values. Fuel capacity should be tuned so that a single-thruster ship has a reasonable delta-v budget for its intended operational range (e.g., Pioneer should handle LEO↔Luna round trips, Daedalus should reach Saturn).

### Cargo Capacity on Thrusters

Similarly, each thruster gains a `cargo_capacity_kg` field for the hull cargo structure associated with that engine class:

| Thruster | Suggested cargo_capacity_kg |
|---|---|
| SCN-1 Pioneer | 25,000 |
| SCN-2 Frontier | 50,000 |
| ASCN-1 Venture | 75,000 |
| ASCN-2 Atlas | 100,000 |
| CGN-1 Helios | 200,000 |
| CGN-2 Prometheus | 350,000 |
| OGN-1 Icarus | 500,000 |
| OGN-2 Daedalus | 750,000 |

Multiple thrusters on one ship = summed capacity (parallel engine clusters on a larger hull).

---

## Implementation Phases

### Phase 1: Constants & Core Formula

**Files:** `constants.py`, `catalog_service.py`

#### 1a. Add surcharge constants to `constants.py`

```python
# ── Cargo containment surcharge (power-law) ──
CARGO_SURCHARGE_A = 0.456            # coefficient
CARGO_SURCHARGE_B = 0.867            # exponent (< 1 = economies of scale)
CARGO_SURCHARGE_REF_DENSITY = 2500.0 # kg/m³ — baseline (regolith/mineral ore)
CARGO_SURCHARGE_DENSITY_EXP = 0.4    # density modifier exponent
```

#### 1b. Add surcharge function to `catalog_service.py`

```python
def compute_cargo_surcharge_kg(
    cargo_mass_kg: float,
    avg_density_kg_m3: float,
) -> float:
    """Power-law containment overhead with density modifier."""
    cargo = max(0.0, float(cargo_mass_kg or 0.0))
    if cargo < 0.01:
        return 0.0
    density = max(1.0, float(avg_density_kg_m3 or CARGO_SURCHARGE_REF_DENSITY))
    base_overhead = CARGO_SURCHARGE_A * (cargo ** CARGO_SURCHARGE_B)
    density_mod = (CARGO_SURCHARGE_REF_DENSITY / density) ** CARGO_SURCHARGE_DENSITY_EXP
    return round(base_overhead * density_mod, 2)
```

#### 1c. Modify `derive_ship_stats_from_parts()` in `catalog_service.py`

**Before:** Sums `capacity_m3 × water_density` from water tank parts for fuel capacity.  
**After:** Sums `fuel_capacity_kg` from thruster parts. Sums `cargo_capacity_kg` from thruster parts.

```python
def derive_ship_stats_from_parts(
    parts: List[Dict[str, Any]],
    resource_catalog: Dict[str, Dict[str, Any]],
    current_fuel_kg: Optional[float] = None,
    cargo_mass_kg: float = 0.0,          # NEW
    cargo_avg_density: float = 2500.0,    # NEW
) -> Dict[str, float]:
    dry_mass_kg = 0.0
    fuel_capacity_kg = 0.0    # NEW: from thrusters
    cargo_capacity_kg = 0.0   # NEW: from thrusters
    isp_values = []
    thrust_total_kn = 0.0

    for part in parts:
        part_type = str(part.get("type") or "").lower()
        dry_mass_kg += max(0.0, float(part.get("mass_kg") or 0.0))

        # Fuel capacity: from thruster fuel_capacity_kg
        if part_type == "thruster" or "thruster" in str(part.get("category_id") or ""):
            fuel_capacity_kg += max(0.0, float(part.get("fuel_capacity_kg") or 0.0))
            cargo_capacity_kg += max(0.0, float(part.get("cargo_capacity_kg") or 0.0))

        part_isp = float(part.get("isp_s") or 0.0)
        if part_isp > 0.0 and (part_type == "thruster" or ...):
            isp_values.append(part_isp)
        thrust_total_kn += max(0.0, float(part.get("thrust_kn") or 0.0))

    # ── Legacy fallback: if no thruster has fuel_capacity_kg, ──
    # ── check for old water tank parts (migration compat)     ──
    if fuel_capacity_kg <= 0.0:
        for part in parts:
            capacity_m3 = max(0.0, float(part.get("capacity_m3") or 0.0))
            resource_id = str(part.get("resource_id") or "").strip()
            if capacity_m3 > 0.0 and resource_id == "water":
                water = resource_catalog.get("water") or {}
                density = max(0.0, float(water.get("mass_per_m3_kg") or 1000.0))
                fuel_capacity_kg += capacity_m3 * density

    # Cargo surcharge for delta-v calculations
    surcharge_kg = compute_cargo_surcharge_kg(cargo_mass_kg, cargo_avg_density)
    effective_cargo_kg = cargo_mass_kg + surcharge_kg

    resolved_fuel_capacity_kg = max(0.0, fuel_capacity_kg)
    resolved_fuel_kg = resolved_fuel_capacity_kg if current_fuel_kg is None else \
                       max(0.0, min(float(current_fuel_kg or 0.0), resolved_fuel_capacity_kg))
    resolved_isp_s = max(isp_values) if isp_values else 0.0

    return {
        "dry_mass_kg": max(0.0, dry_mass_kg),
        "fuel_kg": resolved_fuel_kg,
        "fuel_capacity_kg": resolved_fuel_capacity_kg,
        "cargo_capacity_kg": max(0.0, cargo_capacity_kg),    # NEW
        "cargo_mass_kg": cargo_mass_kg,                       # NEW
        "cargo_surcharge_kg": surcharge_kg,                   # NEW
        "effective_cargo_mass_kg": effective_cargo_kg,         # NEW
        "isp_s": resolved_isp_s,
        "thrust_kn": thrust_total_kn,
    }
```

> **Important:** `compute_wet_mass_kg`, `compute_delta_v_remaining_m_s`, and `compute_fuel_needed_for_delta_v_kg` must now include `effective_cargo_mass_kg` in the mass stack. The dry mass for Tsiolkovsky becomes: `dry_mass_kg + effective_cargo_mass_kg`.

---

### Phase 2: Thruster JSON Updates

**Files:** All 8 files in `items/thrusters/nuclear_thermal_rocket/main/`

Add `fuel_capacity_kg` and `cargo_capacity_kg` to the `performance` block of each thruster. Example for SCN-1 Pioneer:

```json
{
  "id": "scn_1_pioneer",
  "performance": {
    "max_thrust_kN": 250,
    "isp_s": 850,
    "fuel_capacity_kg": 50000,
    "cargo_capacity_kg": 25000
  }
}
```

Update `load_thruster_main_catalog()` in `catalog_service.py` to extract these new fields into the runtime catalog dict.

---

### Phase 3: Ship Inventory Model Rewrite

**Files:** `main.py`, `inventory_router.py`

This is the largest phase. The entire container-based inventory system is replaced with a flat mass-budget model.

#### 3a. New ship inventory storage model

Ship cargo moves from `parts_json` container manifests to a new structure. Two options:

**Option A — Cargo in `parts_json` (minimal schema change):**  
Add a top-level `cargo` array to the ship's `parts_json` (not nested inside individual parts):

```json
{
  "parts": [ ... thruster, reactor, etc ... ],
  "cargo": [
    {"resource_id": "iron_oxides", "mass_kg": 5000.0},
    {"resource_id": "water", "mass_kg": 2000.0}
  ]
}
```

**Option B — Cargo in `location_inventory_stacks` (normalized):**  
Add a `ship_id` column and redesign keys/indexes (or create a new `ship_inventory_stacks` table) so ship cargo lives in the same DB table as location inventory.

> **Important:** The current `location_inventory_stacks` primary key is `(facility_id, stack_type, stack_key)`. Reusing it for ships without schema redesign will cause collisions (e.g. common stack keys like `water`).

**Recommendation: Option A** for this phase (minimal DB changes), with Option B as a future optimization.

#### 3b. Functions to DELETE (main.py)

| Function | Lines | Reason |
|---|---|---|
| `classify_resource_phase()` | ~1189–1222 | No phase matching needed |
| `_is_storage_part()` | ~1225–1231 | No storage parts |
| `_has_explicit_container_fill()` | ~1234–1239 | No container fills |
| `_harden_ship_parts()` | ~1260–1440 | Container UID/manifest migration logic (~180 lines) |
| `compute_ship_inventory_containers()` | ~1442–1593 | Replaced by flat mass budget (~150 lines) |
| `compute_ship_capacity_summary()` | ~1672–1701 | Replaced by simple `{used_kg, capacity_kg, free_kg}` |
| `_apply_ship_container_fill()` | ~2156–2219 | No per-container fills |
| `_inventory_container_groups_for_ship()` | ~2387–2507 | No container groups |

**Total lines deleted from main.py: ~700**

#### 3c. Functions to REWRITE (main.py)

| Function | Change |
|---|---|
| `compute_ship_inventory_resources()` | Read cargo from top-level `cargo` array instead of iterating containers |
| `_load_ship_inventory_state()` | Remove container/capacity_summary computation; add cargo_mass_kg, cargo_capacity_kg |
| `normalize_parts()` | Remove `storage_catalog` parameter |

#### 3d. New functions (main.py)

```python
def compute_ship_cargo_summary(parts, cargo_items, resource_catalog):
    """Simple mass-budget cargo summary."""
    cargo_capacity_kg = sum(
        max(0.0, float(p.get("cargo_capacity_kg") or 0.0))
        for p in parts
        if str(p.get("type") or "").lower() == "thruster"
    )
    total_cargo_kg = sum(
        max(0.0, float(c.get("mass_kg") or 0.0))
        for c in (cargo_items or [])
    )
    # Compute average density for surcharge display
    total_volume_m3 = 0.0
    for c in (cargo_items or []):
        rid = c.get("resource_id", "")
        mass = max(0.0, float(c.get("mass_kg") or 0.0))
        res = resource_catalog.get(rid) or {}
        density = max(1.0, float(res.get("mass_per_m3_kg") or 2500.0))
        total_volume_m3 += mass / density
    avg_density = (total_cargo_kg / total_volume_m3) if total_volume_m3 > 0 else 2500.0
    surcharge_kg = compute_cargo_surcharge_kg(total_cargo_kg, avg_density)

    return {
        "cargo_capacity_kg": cargo_capacity_kg,
        "cargo_used_kg": total_cargo_kg,
        "cargo_free_kg": max(0.0, cargo_capacity_kg - total_cargo_kg),
        "cargo_surcharge_kg": surcharge_kg,
        "cargo_effective_kg": total_cargo_kg + surcharge_kg,
        "avg_density_kg_m3": round(avg_density, 1),
    }

def add_cargo_to_ship(parts_data, resource_id, mass_kg, cargo_capacity_kg):
    """Add resource mass to ship cargo. Enforces mass budget."""
    cargo = list(parts_data.get("cargo") or [])
    current_total = sum(max(0.0, float(c.get("mass_kg") or 0.0)) for c in cargo)
    available = max(0.0, cargo_capacity_kg - current_total)
    accepted = min(mass_kg, available)
    if accepted <= 0.01:
        raise ValueError("Ship cargo is full")
    # Find or create entry
    entry = next((c for c in cargo if c.get("resource_id") == resource_id), None)
    if entry:
        entry["mass_kg"] = max(0.0, float(entry["mass_kg"])) + accepted
    else:
        cargo.append({"resource_id": resource_id, "mass_kg": accepted})
    parts_data["cargo"] = cargo
    return accepted

def remove_cargo_from_ship(parts_data, resource_id, mass_kg):
    """Remove resource mass from ship cargo."""
    cargo = list(parts_data.get("cargo") or [])
    entry = next((c for c in cargo if c.get("resource_id") == resource_id), None)
    if not entry:
        raise ValueError(f"No {resource_id} in ship cargo")
    available = max(0.0, float(entry["mass_kg"]))
    taken = min(mass_kg, available)
    entry["mass_kg"] = available - taken
    if entry["mass_kg"] < 0.01:
        cargo.remove(entry)
    parts_data["cargo"] = cargo
    return taken
```

---

### Phase 4: Transfer Logic Rewrite

**Files:** `inventory_router.py`, `org_service.py`

#### 4a. inventory_router.py — Transfers to/from ships

**Replace** the container-iteration transfer logic (~L595–670 and ~L686–751) with:

```python
# Destination is a ship
accepted = add_cargo_to_ship(target_parts_data, resource_id, mass_kg, cargo_capacity_kg)

# Source is a ship
taken = remove_cargo_from_ship(source_parts_data, resource_id, mass_kg)
```

**Delete:**
- `ship_container` as a source/target kind in the transfer model
- Container index parameters
- Phase compatibility checks
- All `_apply_ship_container_fill` calls

**Remove container-group fields from all response payloads:**
- `container_groups` (5+ occurrences)
- `containers` in state dicts

#### 4b. org_service.py — `_consume_ship_resource_mass()`

Replace the container-iteration loop (~L155–200) with a simple call to `remove_cargo_from_ship()`.

---

### Phase 5: Fleet & Shipyard API Updates

**Files:** `fleet_router.py`, `shipyard_router.py`, `catalog_service.py`

#### 5a. Fleet list response

Replace in fleet ship data:
```python
# OLD
"inventory_containers": inventory_containers,
"inventory_capacity_summary": inventory_capacity_summary,

# NEW
"cargo_summary": compute_ship_cargo_summary(parts, cargo_items, resource_catalog),
```

Keep `inventory_items` (resource list) — just source it from `cargo` array instead of containers.

#### 5b. Jettison endpoint rewrite

`api_ship_inventory_jettison()` (~L1303–1374): Simplify to remove a resource entry from the `cargo` array. No more container index lookup.

#### 5c. Shipyard preview/build

- Remove `storage` from the catalog categories passed to the shipyard
- Add `fuel_capacity_kg` and `cargo_capacity_kg` to the ship stats preview response
- `build_shipyard_catalog_payload()`: remove `storage_catalog` parameter and the loop that builds storage items

#### 5d. Ship stats payload

`build_ship_stats_payload()` should include:
```python
{
    "cargo_capacity_kg": stats["cargo_capacity_kg"],
    "cargo_surcharge_kg": stats["cargo_surcharge_kg"],
    "effective_cargo_mass_kg": stats["effective_cargo_mass_kg"],
}
```

---

### Phase 6: Storage Category Removal

**Files:** `constants.py`, `catalog_service.py`, `catalog_router.py`, `industry_service.py`, `org_service.py`

#### 6a. Delete from constants.py
- Remove `{"id": "storage", ...}` from `ITEM_CATEGORIES`
- Remove `"tank"`, `"tanks"`, `"cargo"`, `"wet_storage"`, `"dry_storage"` from `ITEM_CATEGORY_ALIASES`

#### 6b. Delete from catalog_service.py
- Delete `load_storage_catalog()` function
- Remove from all catalog loader tuples (3 occurrences)
- Remove `"storage": ["Storage"]` from `_find_raw_item_json` search paths
- Remove `storage_catalog` parameter from `normalize_parts()` and `build_shipyard_catalog_payload()`

#### 6c. Delete from catalog_router.py
- Remove `("storage", catalog_service.load_storage_catalog)` from module_loaders

#### 6d. Delete from industry_service.py
- Remove `catalog_service.load_storage_catalog` from 3 part_catalogs loader loops (~L124, ~L318, ~L499)

#### 6e. Delete from org_service.py
- Remove `("storage", catalog_service.load_storage_catalog)` from boost part candidates (~L855)
- Remove `"storage": catalog_service.load_storage_catalog` from 2 resolve/payload functions (~L1124, ~L1155)

---

### Phase 7: Storage Item & Recipe Cleanup

**Files:** `items/Storage/`, `items/Recipes/`

#### 7a. Delete all 9 storage JSON files
```
items/Storage/solid_tank_10_m3.json
items/Storage/solid_tank_50_m3.json
items/Storage/solid_tank_100_m3.json
items/Storage/water_tank_10_m3.json
items/Storage/water_tank_50_m3.json
items/Storage/water_tank_100_m3.json
items/Storage/gas_tank_10_m3.json
items/Storage/gas_tank_50_m3.json
items/Storage/gas_tank_100_m3.json
```

#### 7b. Delete 9 corresponding recipe JSON files
```
items/Recipes/solid_tank_10_m3.json
items/Recipes/solid_tank_50_m3.json
items/Recipes/solid_tank_100_m3.json
items/Recipes/water_tank_10_m3.json
items/Recipes/water_tank_50_m3.json
items/Recipes/water_tank_100_m3.json
items/Recipes/gas_tank_10_m3.json
items/Recipes/gas_tank_50_m3.json
items/Recipes/gas_tank_100_m3.json
```

#### 7c. Keep `items/Resources/*.json` — `mass_per_m3_kg` stays
This field is now used by the density modifier in the surcharge formula. Keep all resource definitions as-is.

---

### Phase 8: Frontend Updates

**Files:** `static/js/shipyard.js`, `static/js/fleet.js`, `static/js/app.js`

#### 8a. shipyard.js
- Remove the `{ id: "storage", label: "Storage", ... }` folder definition
- Remove `partFolderId()` cases that return `"storage"`
- Remove `capacity_m3` tooltip line
- Add `fuel_capacity_kg` and `cargo_capacity_kg` to stats display

#### 8b. fleet.js — `renderCargoSection()`
**Rewrite** to show:
```
Cargo: 45,000 / 100,000 kg (45%)  [====------]
Surcharge: 5,280 kg (11.7%)
Effective mass: 50,280 kg
```
- Remove per-container breakdown (`fleetContainerBreakdown`, `fleetContainerRow`, etc.)
- Remove m³ capacity bars
- Keep the resource item grid (unchanged, just reads from `inventory_items`)

#### 8c. app.js
- Remove `capacity_m3` tooltip lines in ship parts grid
- Remove capacity summary rendering that uses `used_m3`/`capacity_m3`
- Remove `container_index` from `runInventoryAction()` body
- **Careful:** Do NOT touch any `PIXI.Container` references (graphics objects, unrelated)

---

### Phase 9: Wipe-Based Cutover (No DB Migration)

Because this rollout includes a **server wipe/restart**, do not ship a ship-cargo migration. Start with a clean DB and seed data under the new model.

#### 9a. Cutover steps

1. Stop services
2. Snapshot old DB for archive/debug (`data/game.db`)
3. Remove old DB
4. Start server so schema/migrations/bootstrap run from scratch
5. Run smoke tests + focused inventory/fleet/transfer checks

```bash
sudo docker compose down
cp data/game.db data/game.db.pre-cargo-overhaul.$(date +%Y%m%d-%H%M%S)
rm -f data/game.db
sudo docker compose up -d --build frontier-dev
./run_tests.sh
```

#### 9b. Why this is safer

- Avoids brittle JSON-shape migration over `ships.parts_json`
- Avoids legacy container-field edge cases (`cargo_manifest`, `used_m3`, fallback keys)
- Prevents partial-state bugs if migration is interrupted
- Reduces implementation scope and rollback complexity

---

### Phase 10: Test Updates

**Files:** `tests/`

#### Delete
- `TestStorageCatalog` class in `test_catalog_integrity.py`
- `storage_catalog` fixtures in `conftest.py`
- `test_preview_with_storage_only`, `test_preview_every_storage`, `test_valid_storage_id`, etc. in `test_item_audit.py`
- `canonical_item_category("storage") == "storage"` assertions in `test_game_logic.py`

#### Modify
- All `water_tank_10_m3` references in `test_transfer_planner.py` (15+ occurrences) — replace with thruster-only ship builds since fuel capacity now comes from thrusters
- Recipe validation tests that check storage recipe outputs — remove or redirect
- `test_derive_stats_fast_with_many_parts` — update to use new `derive_ship_stats_from_parts` signature

#### New tests
- `test_cargo_surcharge_power_law()` — verify formula produces expected values at anchor points
- `test_cargo_surcharge_density_modifier()` — verify dense cargo pays less, gas pays more
- `test_cargo_capacity_from_thruster()` — verify fuel_capacity_kg and cargo_capacity_kg sum from thrusters
- `test_cargo_transfer_mass_budget()` — verify ships reject cargo over capacity
- `test_cargo_overhaul_cold_start()` — verify fresh DB startup works without storage items/categories

---

## Critical Risks & Must-Have Mitigations

### 1) `parts_json` shape compatibility risk (highest)

Many paths currently assume `ships.parts_json` is a JSON array and do `json.loads(... or "[]")` directly. If shape changes to `{"parts": [...], "cargo": [...]}`, these will break unless updated:

- `fleet_router.py` (multiple list loaders)
- `admin_game_router.py`
- `mission_service.py`
- `contract_router.py`
- `org_service.py`
- `main.py` (`_load_ship_inventory_state` and helpers)

**Mitigation:** add one shared helper used everywhere:

```python
def split_ship_parts_and_cargo(parts_json: str) -> tuple[list[dict], list[dict]]:
    raw = json.loads(parts_json or "[]")
    if isinstance(raw, dict):
        return list(raw.get("parts") or []), list(raw.get("cargo") or [])
    if isinstance(raw, list):
        return list(raw), []
    return [], []
```

And a single serializer for writes:

```python
def merge_ship_parts_and_cargo(parts: list[dict], cargo: list[dict]) -> str:
    return json.dumps({"parts": parts, "cargo": cargo}, sort_keys=True)
```

### 2) Hidden UI/API contract breakage

The current plan covers `shipyard.js`, `fleet.js`, `app.js`, but additional UIs still rely on containers:

- `static/js/map_windows.js` (`container_groups`, `ship_container`, capacity m³)
- `static/js/sites.js` (`entity.container_groups`, `capacity_summary`)

**Mitigation:** explicitly include both files in frontend scope and remove `ship_container` transfer mode in both backend and UI payload builders.

### 3) Water fuel vs water cargo double-count risk

Without a single-source-of-truth policy, water can be counted both in `fuel_kg` and cargo mass.

**Decision (required):**

- Keep water propulsion only in `ships.fuel_kg`
- Exclude fuel mass from cargo manifest
- If hauling tradable water as cargo is desired later, use a distinct resource id (e.g. `water_bulk`) or explicit split fields

### 4) Mass model consistency risk across transfer math and gates

`delta_v_remaining_m_s`, `compute_fuel_needed_for_delta_v_kg`, `wet_mass_kg`, and TWR gate must all use the same effective mass model (`dry + effective_cargo + fuel`).

**Mitigation:** centralize to one `compute_effective_ship_mass()` helper and ban ad-hoc sums in route/transfer endpoints.

### 5) Transfer race conditions (overfill)

Two concurrent transfers can pass free-capacity checks and overfill cargo.

**Mitigation:** wrap ship transfer mutations in transaction boundaries with re-read/re-check (`BEGIN IMMEDIATE` in SQLite) before commit.

### 6) Outlier surcharge values on ultra-low-density cargo

Power-law + density multiplier can produce extreme surcharge for low-density resources.

**Mitigation:** clamp density modifier and/or total surcharge:

```python
density_mod = min(MAX_DENSITY_MOD, max(MIN_DENSITY_MOD, density_mod))
overhead_kg = min(cargo_mass_kg * MAX_SURCHARGE_FRAC, overhead_kg)
```

---

## Rollout Order

| Step | Phase | Risk | Rollback |
|---|---|---|---|
| 1 | Phase 1 (constants + formula) | Low | Revert constants.py, catalog_service.py |
| 2 | Phase 2 (thruster JSON) | Low | Revert JSON files |
| 3 | Phase 6 (remove storage category) | Medium | Re-add category constants |
| 4 | Phase 7 (delete storage items/recipes) | Medium | Restore from git |
| 5 | Phase 3 (main.py rewrite) | **High** | Revert main.py — most complex change |
| 6 | Phase 4 (transfer logic) | **High** | Revert inventory_router.py, org_service.py |
| 7 | Phase 5 (fleet/shipyard API) | Medium | Revert fleet_router.py, shipyard_router.py |
| 8 | Phase 9 (wipe-based cutover) | Medium | Restore archived DB snapshot |
| 9 | Phase 8 (frontend) | Low | Revert JS files |
| 10 | Phase 10 (tests) | Low | Revert test files |

**Pre-deployment:**
```bash
# Backup the database
cp data/game.db data/game.db.backup.$(date +%Y%m%d)
```

---

## Files Changed Summary

| File | Action | Estimated lines changed |
|---|---|---|
| `constants.py` | Modify | +5 / -8 |
| `catalog_service.py` | Heavy modify | +60 / -80 |
| `catalog_router.py` | Modify | -2 |
| `main.py` | **Heavy rewrite** | +100 / -700 |
| `inventory_router.py` | **Heavy rewrite** | +40 / -300 |
| `fleet_router.py` | Heavy modify | +30 / -150 |
| `shipyard_router.py` | Light modify | +5 / -5 |
| `industry_service.py` | Light modify | -3 |
| `org_service.py` | Moderate modify | +15 / -50 |
| `db_migrations.py` | No change (wipe cutover) | 0 |
| `items/thrusters/*/main/*.json` | Modify (8 files) | +16 |
| `items/Storage/*.json` | **Delete** (9 files) | -100 |
| `items/Recipes/*_tank_*.json` | **Delete** (9 files) | -180 |
| `static/js/shipyard.js` | Modify | +5 / -25 |
| `static/js/fleet.js` | Rewrite cargo section | +30 / -100 |
| `static/js/app.js` | Modify | +5 / -20 |
| `tests/*` | Heavy modify | +80 / -200 |
| **Total** | | **~+430 / -1920** |

**Net: ~1,500 lines of code removed.** The codebase gets meaningfully simpler.

---

## Open Questions

1. **`parts_json` compatibility helper** — Required before rollout. All readers/writers must go through shared helper functions.

2. **Fuel policy** — Lock decision to separate `fuel_kg` from cargo mass (no dual-use water in cargo for this release).

3. **Marketplace cleanup** — Existing storage-tank listings must be purged during wipe cutover.

4. **Courier/mission special items** — Validate courier container and mission-module flows (`contract_router.py`, `mission_service.py`) still work under new ship inventory format.
