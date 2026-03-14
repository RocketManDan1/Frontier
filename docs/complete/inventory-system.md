# Inventory System

Reference for the stack-based inventory model, ship containers, resource/part transfers, and the API endpoints that tie them together.

---

## Overview

Inventory exists in two places:

| Storage | Backing | Tracks |
|---|---|---|
| **Location inventory** | `location_inventory_stacks` table | Resources and parts sitting at an orbital station or surface site |
| **Ship inventory** | `ships.parts_json` column + `ships.fuel_kg` | Parts installed on the ship and cargo stored in their containers |

Location inventory uses a **stack model** — identical items merge into a single row with a quantity counter. Ship inventory stores parts as an ordered JSON array where each element is a full part dict with its own container state.

---

## Location Inventory — Stack Model

### The `location_inventory_stacks` Table

| Column | Type | Notes |
|---|---|---|
| `location_id` | TEXT | Which orbital node or surface site |
| `corp_id` | TEXT | Corporation that owns this stack (empty string for unowned) |
| `stack_type` | TEXT | `"resource"` or `"part"` |
| `stack_key` | TEXT | Unique identity within the stack type (see below) |
| `item_id` | TEXT | Canonical item identifier (e.g. `"water"`, `"ntr_100"`) |
| `name` | TEXT | Human-readable display name |
| `quantity` | REAL | Count (parts) or mass in kg (resources) |
| `mass_kg` | REAL | Total stack mass in kg |
| `volume_m3` | REAL | Total stack volume in m³ |
| `payload_json` | TEXT | JSON metadata — `{"resource_id": "..."}` for resources, `{"part": {...}}` for parts |
| `updated_at` | REAL | Game-time of last modification |

**Primary key:** `(location_id, corp_id, stack_type, stack_key)`

### Stack Keys

**Resources** use the `resource_id` string directly as their stack key (e.g. `"water"`, `"iron_oxides"`). All units of the same resource at the same location for the same corp merge into one row.

**Parts** use a SHA1 hash of their normalized properties. The hash is computed by:
1. Normalizing the part dict through the catalog (hydrating canonical fields).
2. Stripping all runtime-only keys (`cargo_manifest`, `container_uid`, `used_m3`, `fuel_kg`, etc.).
3. Producing a stable JSON string: `json.dumps({"part": <normalized>}, sort_keys=True, separators=(",",":"))`.
4. Hashing with SHA1.

This means parts with identical specs stack together, even if they came from different ships. Parts that differ in any catalog property get separate stacks.

### Resource Quantity Semantics

For resources, **quantity equals mass** — `quantity`, `mass_kg`, and the delta values all represent kilograms. Volume is derived from mass using the resource catalog's density: `volume_m3 = mass_kg / density_kg_per_m3`.

### Part Quantity Semantics

For parts, **quantity is a unit count** (1, 2, 3...). `mass_kg` is the total stack mass (per-unit mass × count). `volume_m3` is always stored as 0 (parts don't consume location volume).

### The Core Write Primitive: `_upsert_inventory_stack()`

All location inventory writes go through this single function. It uses **delta-based upsert** logic:

1. Look up the existing row by `(location_id, corp_id, stack_type, stack_key)`.
2. If **no row exists** and the deltas are positive → INSERT a new row.
3. If a **row exists** → add the deltas to current values.
   - If the resulting quantity drops to ≤ 0 → DELETE the row entirely.
   - Otherwise → UPDATE with new totals.

This means you never need to check whether a stack exists before writing to it. Pass positive deltas to add, negative deltas to remove.

### Convenience Wrappers

| Function | What it does |
|---|---|
| `add_resource_to_location_inventory(conn, location_id, resource_id, mass_kg, corp_id)` | Looks up density from catalog, computes volume, calls `_upsert_inventory_stack` |
| `add_part_to_location_inventory(conn, location_id, part, count, corp_id)` | Computes stack key via SHA1, calls `_upsert_inventory_stack` |
| `consume_parts_from_location_inventory(conn, location_id, item_ids, corp_id)` | Finds stacks matching each `item_id`, decrements by 1 each, returns the consumed part dicts |

---

## Ship Inventory — Container Model

Ships don't use the `location_inventory_stacks` table. Instead, a ship's parts are stored as a JSON array in `ships.parts_json`, and its water/fuel mass is tracked in `ships.fuel_kg`.

Each storage part (tanks, cargo bays) in the parts array acts as a **container** with its own capacity, phase restriction, and cargo manifest.

### Container Properties

Every storage part carries these fields:

| Field | Description |
|---|---|
| `container_uid` | UUID assigned when the part is first hardened, used to identify it across transfers |
| `capacity_m3` | Total volume this container can hold |
| `tank_phase` | `"solid"`, `"liquid"`, or `"gas"` — restricts what resource phases can be stored |
| `cargo_manifest` | Array of `{resource_id, mass_kg, volume_m3, density_kg_m3}` — supports multiple resource types per container |

Legacy single-resource fields (`resource_id`, `cargo_mass_kg`, `used_m3`, `water_kg`, `fuel_kg`) are maintained alongside `cargo_manifest` for backward compatibility and are recalculated from the manifest automatically.

### Resource Phase Classification

Every resource is classified as `"solid"`, `"liquid"`, or `"gas"` before it can be placed in a container. The classification uses three priorities:

1. **Explicit `phase` field** in the resource catalog entry.
2. **Name heuristics** — gas: helium, hydrogen, nitrogen, oxygen, argon, methane, deuterium; liquid: water, propellant, hydrolox, ammonia, fuel.
3. **Density fallback** — below 200 kg/m³ = gas, below 2000 kg/m³ = liquid, otherwise solid.

A resource can only be stored in a container whose `tank_phase` matches.

### Water = Fuel

Water is special. The `ships.fuel_kg` column tracks total water mass across all liquid containers. When water is added to or removed from a ship's containers, `fuel_kg` is adjusted in sync. This is because the game uses water as reaction mass for propulsion.

### Part Hardening

The first time a ship's inventory is accessed, `_harden_ship_parts()` runs to normalize all storage parts:

1. Assigns `container_uid` to any container that doesn't have one.
2. Migrates legacy single-resource fields into `cargo_manifest` format.
3. Synchronizes manifest totals back to legacy fields (for any code that still reads them).
4. Proportionally distributes `fuel_kg` across water-capable containers by capacity.

If hardening changes anything, the updated `parts_json` is immediately persisted. This migration runs transparently on every inventory access.

### Loading Ship Inventory State

`_load_ship_inventory_state()` is the central read function. It returns:

```
{
  "row":              <ships table row>,
  "parts":            [<normalized, hardened part dicts>],
  "fuel_kg":          5000.0,
  "containers":       [<container summary dicts>],
  "resources":        [<aggregated resource list>],
  "capacity_summary": {<per-phase utilization>},
  "location_id":      "LEO",
  "is_docked":        true
}
```

The `containers` list is built by `compute_ship_inventory_containers()`. Each entry includes:

```
{
  "container_index":  3,              // index in the parts array
  "container_uid":    "...",
  "name":             "Water Tank 50m³",
  "phase":            "liquid",
  "capacity_m3":      50.0,
  "used_m3":          25.0,
  "cargo_mass_kg":    25000.0,
  "dry_mass_kg":      200.0,
  "total_mass_kg":    25200.0,
  "cargo_manifest":   [{...}]
}
```

The `resources` list aggregates all resources across all containers, deduplicated by `resource_id`. Used for the cargo overview UI.

The `capacity_summary` breaks down utilization by phase (solid/liquid/gas) and total.

### Writing Ship Inventory State

`_persist_ship_inventory_state()` saves updated parts and fuel:
- Recomputes derived stats (dry mass, fuel capacity, ISP) from the parts list.
- If the parts list is empty and fuel ≤ 0, **deletes the ship entirely**.
- Otherwise updates `parts_json`, `fuel_kg`, and derived stat columns.

---

## Two Item Shape Builders

The API serves ship contents through two different lenses, each with its own builder:

| Builder | Returns | Used for |
|---|---|---|
| `_inventory_items_for_ship()` | Aggregated **resources** (cargo) with `source_kind: "ship_resource"` | Cargo transfer UI — what resources are onboard |
| `_stack_items_for_ship()` | Individual **parts** (modules) with `source_kind: "ship_part"` | Module fitting UI — what equipment is installed |

Similarly for locations:

| Builder | Returns | Used for |
|---|---|---|
| `_inventory_items_for_location()` | **Resources** at the location with `source_kind: "location_resource"` | Cargo transfer UI |
| `_stack_items_for_location()` | **Parts** at the location with `source_kind: "location_part"` | Module transfer UI |

The `_inventory_items_*` functions are used in the cargo/resource transfer context. The `_stack_items_*` functions are used in the part/module transfer context.

---

## API Endpoints

### Inventory Views

**`GET /api/inventory/location/{location_id}`** — Returns all resource and part stacks at a location, filtered by the requesting corp's ownership.

**`GET /api/inventory/ship/{ship_id}`** — Returns the ship's containers, resources, and capacity summary. Settles any pending arrivals first. Requires ship ownership.

**`GET /api/inventory/context/{kind}/{entity_id}`** — The multi-panel cargo transfer view.
- `kind=ship` → loads the ship plus all sibling ships at the same dock plus the location's inventory.
- `kind=location` → loads the location plus all docked ships.
- Returns an `inventories[]` array where each entry has a source entity's items and container groups.

**`GET /api/stack/context/ship/{ship_id}`** — The module/part transfer view. Returns location parts + all docked ships' parts at the same location. Ship must be docked.

**`GET /api/hangar/context/{ship_id}`** — Unified hangar endpoint combining fitting + cargo + transfer. Returns modules, stats, power balance, containers, cargo, capacity, and stack items for the ship and all siblings.

**`GET /api/cargo/context/{location_id}`** — Location-centric cargo view. Returns location inventory + all docked ships' containers and cargo.

### Transfers

**`POST /api/inventory/transfer`** — Move resources/cargo between entities.

Request body:
```json
{
  "source_kind": "ship_container | ship_resource | location_resource",
  "source_id":   "<ship_id or location_id>",
  "source_key":  "<container_index or resource_id>",
  "target_kind": "ship | location | ship_container",
  "target_id":   "<ship_id or location_id>",
  "target_key":  "<container_index for ship_container targets>",
  "amount":      1000.0,
  "resource_id": "water"
}
```

**Validation rules:**
- Source and target must be at the same location.
- Both ships (if applicable) must be docked (not in transit).
- Cannot transfer from a ship to itself (unless doing container-to-container within the same ship).
- For intra-ship container transfers, source and target containers must have matching phases.

**Transfer flow:**

| Source | Target | What happens |
|---|---|---|
| `ship_container` | `ship_container` (same ship) | Phase-matched intra-ship move. Subtracts from source manifest, adds to target manifest. |
| `ship_container` or `ship_resource` | `ship` (different ship) | Auto-distributes to compatible containers on target by phase, filling in sequence. |
| `ship_container` or `ship_resource` | `location` | Adds to location inventory via `add_resource_to_location_inventory`. |
| `location_resource` | `ship` | Auto-distributes to compatible containers on target. |
| `location_resource` | `location` | Not applicable (same entity). |

Water transfers adjust `fuel_kg` on both source and target ships.

**`POST /api/stack/transfer`** — Move parts/modules between entities.

Request body:
```json
{
  "source_kind": "ship_part | location_part",
  "source_id":   "<ship_id or location_id>",
  "source_key":  "<part_index (ship) or stack_key (location)>",
  "target_kind": "ship | location",
  "target_id":   "<ship_id or location_id>"
}
```

- From ship: pops the part at the given array index.
- From location: decrements the stack by 1, returns the part dict.
- To ship: appends to the target ship's parts array.
- To location: adds to location inventory via `add_part_to_location_inventory`.

---

## How Things Connect

### Adding a resource to a location

Any system that delivers resources to a location (production output, mining yield, LEO boost) calls `add_resource_to_location_inventory()`. This looks up density from the resource catalog and calls `_upsert_inventory_stack` with positive deltas.

### Consuming resources from a location

Production jobs and ships consume resources via `_upsert_inventory_stack` with negative deltas. The shipyard build process uses `consume_parts_from_location_inventory()` to remove parts.

### Ship builds

The shipyard consumes parts from location inventory → builds a `parts_json` array → inserts a new `ships` row. The parts become the ship's installed modules and containers.

### Transfers between ships and locations

The `/api/inventory/transfer` and `/api/stack/transfer` endpoints handle all resource and part movement. Everything must be at the same location, and ships must be docked.

### Ship destruction

When a ship is deconstructed or its last part is removed, `_persist_ship_inventory_state` detects an empty parts list and deletes the ship row. Parts removed during deconstruction go back to location inventory.
