# Facility Layer Design

Introduces an intermediate **Facility** entity between locations and player equipment/cargo/jobs, enabling multi-tenant sites and future site-wide upgrade projects.

---

## Motivation

Today, all industry is keyed directly to `(location_id, corp_id)`. This has two problems:

1. **No multi-tenant identity.** Multiple corps deploying equipment at the same site have no named grouping — their equipment, cargo, and jobs coexist as filtered rows in the same tables with no visible boundary.
2. **No hook for site-wide projects.** Future cooperative site upgrades (shared infrastructure, landing pads, etc.) need a clean entity graph: *site → facilities → equipment*. Without a facility layer, upgrades would have to target raw `(location_id, corp_id)` tuples.

### Goals

- A corp can create one or more **named facilities** at any location (orbital or surface).
- Each facility has its own independent power grid, equipment, inventory, and production queues.
- Multiple corps can coexist at the same site; facilities from other corps are visible (name + basic stats).
- The Sites → Industrial tab gains a new **facility grid** view between site selection and the current equipment/job management view.
- A sidebar placeholder is added for future site-wide upgrades.

---

## Data Model

### New Table: `facilities`

```sql
CREATE TABLE facilities (
    id              TEXT PRIMARY KEY,        -- UUID
    location_id     TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
    corp_id         TEXT NOT NULL,            -- owning corporation
    name            TEXT NOT NULL,            -- player-chosen name
    created_at      REAL NOT NULL,            -- game-time epoch
    created_by      TEXT NOT NULL             -- actor string (user or "corp:<name>")
);
CREATE INDEX idx_facilities_location ON facilities(location_id);
CREATE INDEX idx_facilities_corp ON facilities(corp_id);
CREATE UNIQUE INDEX uq_facilities_loc_corp_name ON facilities(location_id, corp_id, name);
```

**No unique constraint on `(location_id, corp_id)`** — a corp may create multiple facilities at the same location.

### Modified Tables

All industry/inventory tables gain a `facility_id` column. During migration, existing rows are assigned to auto-created facilities.

| Table | New Column | Notes |
|---|---|---|
| `deployed_equipment` | `facility_id TEXT REFERENCES facilities(id)` | Equipment belongs to a facility |
| `location_inventory_stacks` | `facility_id TEXT` | Cargo/parts scoped to a facility |
| `production_jobs` | `facility_id TEXT` | Jobs run within a facility |
| `refinery_slots` | `facility_id TEXT` | Refinery slots are per-facility |
| `construction_queue` | `facility_id TEXT` | Build queue is per-facility |

The existing `location_id` and `corp_id` columns are **retained** (not removed) for:
- Query convenience: "everything at this location" queries stay fast.
- Backwards compatibility during migration.
- Location-level aggregation (site overview stats).

`facility_id` becomes the primary ownership/scoping key for all industry operations.

### Inventory Keying Change (Critical)

Because a corp can now have multiple facilities at one location, inventory keys must include `facility_id`.

Current key:
- `(location_id, corp_id, stack_type, stack_key)`

Required key after migration:
- `(facility_id, stack_type, stack_key)` (preferred), or
- `(location_id, corp_id, facility_id, stack_type, stack_key)`

If this is not changed, two facilities owned by the same corp at the same location will overwrite/merge each other's stacks.

### Entity Hierarchy

```
Location (LEO, Shackleton Rim, etc.)
├── Facility "Alpha Base" (CorpA)
│   ├── deployed_equipment (reactors, refineries, etc.)
│   ├── location_inventory_stacks (cargo, parts)
│   ├── refinery_slots
│   ├── construction_queue
│   └── production_jobs
├── Facility "Mining Op 2" (CorpA)
│   └── ...
├── Facility "New Tokyo" (CorpB)
│   └── ...
└── [Future] Site Upgrades (shared infrastructure)
```

---

## Migration Strategy

### Migration `0024_facilities`

`0023` is already used by missions in the current migration list, so the facility migration must use the next available id.

1. **Create** the `facilities` table.
2. **Add** `facility_id` column to all five modified tables.
3. **Recreate `location_inventory_stacks`** with `facility_id` included in uniqueness/primary-key semantics.
    - Backfill existing rows with generated facility ids before swapping tables.
    - Recreate indexes for `(facility_id, stack_type, item_id)` and `(location_id, corp_id, facility_id)` lookup paths.
4. **Auto-create facilities** for every distinct `(location_id, corp_id)` pair that appears in `deployed_equipment` OR `location_inventory_stacks`:

```python
# Pseudocode
pairs = conn.execute("""
    SELECT DISTINCT location_id, corp_id FROM deployed_equipment
    UNION
    SELECT DISTINCT location_id, corp_id FROM location_inventory_stacks
    WHERE corp_id != ''
""").fetchall()

for location_id, corp_id in pairs:
    facility_id = str(uuid4())
    # Name: "{location_name} Facility" or "{corp_name} Facility"
    conn.execute("""
        INSERT INTO facilities (id, location_id, corp_id, name, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (facility_id, location_id, corp_id, f"Facility", game_now_s(), "migration"))

    # Back-fill all rows for this (location_id, corp_id)
    for table in ['deployed_equipment', 'location_inventory_stacks',
                   'production_jobs', 'refinery_slots', 'construction_queue']:
        conn.execute(f"""
            UPDATE {table} SET facility_id = ?
            WHERE location_id = ? AND corp_id = ?
        """, (facility_id, location_id, corp_id))
```

5. After backfill, enforce that all corp-owned industry rows have `facility_id` populated.
    - `deployed_equipment`, `production_jobs`, `refinery_slots`, and `construction_queue` should be treated as required-facility rows in app logic.
    - For inventory, unowned/admin edge rows may remain nullable only if explicitly needed.

---

## API Changes

### New Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/facilities/{location_id}` | List all facilities at a location (name, corp, basic stats) |
| POST | `/api/facilities/create` | Create a new facility: `{location_id, name}` |
| PATCH | `/api/facilities/{facility_id}/rename` | Rename a facility: `{name}` |
| DELETE | `/api/facilities/{facility_id}` | Delete a facility (must be empty — no equipment or cargo) |

#### `GET /api/facilities/{location_id}` Response

```json
{
    "location_id": "LUNA_SHACKLETON",
    "location_name": "Shackleton Rim (S Pole)",
    "facilities": [
        {
            "id": "uuid-1",
            "name": "Alpha Base",
            "corp_id": "corp-abc",
            "corp_name": "Stellar Industries",
            "is_mine": true,
            "stats": {
                "equipment_count": 12,
                "power_mwe": 6.4,
                "power_used_mwe": 4.2,
                "active_jobs": 3
            }
        },
        {
            "id": "uuid-2",
            "name": "Helium Station",
            "corp_id": "corp-xyz",
            "corp_name": "Lunar Corp",
            "is_mine": false,
            "stats": {
                "equipment_count": 8,
                "power_mwe": 3.0,
                "power_used_mwe": 2.1,
                "active_jobs": 1
            }
        }
    ]
}
```

### Modified Endpoints

All industry endpoints shift from `location_id` to `facility_id` as their primary scope:

| Endpoint | Change |
|---|---|
| `GET /api/industry/{location_id}` | → `GET /api/industry/facility/{facility_id}` |
| `POST /api/industry/deploy` | Body adds `facility_id`; equipment deployed to facility |
| `POST /api/industry/undeploy` | Equipment returned to facility's inventory |
| `POST /api/industry/refinery/assign` | Scoped to facility |
| `POST /api/industry/construction/queue` | Scoped to facility |
| `POST /api/industry/constructor/mode` | Scoped to facility |
| `GET /api/inventory/location/{location_id}` | → `GET /api/inventory/facility/{facility_id}` (or add `facility_id` query param) |
| `POST /api/inventory/transfer` | Transfers between facility inventories / ships |
| `GET /api/cargo/context/{location_id}` | Add `facility_id` query param to scope cargo view |

**Backward compatibility**: The old `GET /api/industry/{location_id}` can remain as a redirect or aggregate view during transition. If the location has exactly one facility for the requesting corp, auto-resolve to it.

### Auth and Isolation Rules

Every endpoint that accepts `facility_id` must verify:
- Facility exists.
- Facility belongs to requesting `corp_id` for write operations.
- Read operations exposing other corps must return only approved summary fields.

Never trust `location_id` from the client when `facility_id` is provided; resolve location from facility server-side.

### Sites Overview Endpoint

`GET /api/sites` response gains a `facility_count` field per location. `GET /api/sites/{location_id}` includes a `facilities[]` summary.

---

## Frontend Changes

### Site Selection → Facility Grid (New View)

When the user selects a location in the Industrial tab dropdown, instead of immediately loading the industry view, they see a **facility grid**:

```
┌──────────────────────────────────────────────────────────┐
│  SITE: Shackleton Rim (S Pole)  ▼                        │
├──────────────────────┬───────────────────────────────────┤
│                      │                                   │
│  ┌─────────────────┐ │  ┌─────────────────┐              │
│  │  Alpha Base      │ │  │       +         │              │
│  │  ⚡ 6.4 MWe      │ │  │  Create New     │              │
│  │  🏭 12 equip     │ │  │  Facility       │              │
│  │  ⚙ 3 active     │ │  │                 │              │
│  │  [ENTER]         │ │  │                 │              │
│  └─────────────────┘ │  └─────────────────┘              │
│                      │                                   │
│  ┌─────────────────┐ │                                   │
│  │  Helium Station  │ │       SITE UPGRADES              │
│  │  Lunar Corp      │ │       (Coming Soon)              │
│  │  ⚡ 3.0 MWe      │ │                                   │
│  │  🏭 8 equip      │ │       ┌─────────────┐            │
│  │                  │ │       │ placeholder │            │
│  └─────────────────┘ │       └─────────────┘            │
│                      │                                   │
│  FACILITIES          │                                   │
└──────────────────────┴───────────────────────────────────┘
```

**Layout:**
- **Left panel**: Scrollable grid of facility cards.
  - **Own facilities**: Show name, power stats, equipment count, active job count. Clickable → enters the existing industry management view scoped to that facility.
  - **Other corps' facilities**: Show name, corp name, basic stats (equipment count, power). Not clickable (read-only).
  - **"+" card**: Always shown at the end. Clicking prompts for a facility name, then calls `POST /api/facilities/create`. On success, enters the new (empty) facility's industry view.
- **Right sidebar**: Placeholder panel for future site-wide upgrade projects. Displays "Site Upgrades — Coming Soon" with an empty bordered area.

### Facility Industry View

Once inside a facility, the existing industry layout (power balance, constructors, refineries, construction queue, etc.) renders exactly as today, but scoped to `facility_id` instead of `(location_id, corp_id)`.

A **breadcrumb** or back-button is added at the top:

```
← Back to Shackleton Rim    |    Facility: Alpha Base
```

### Facility Creation Modal

Simple modal triggered by the "+" card:

```
┌────────────────────────────────┐
│   Create New Facility          │
│                                │
│   Location: Shackleton Rim     │
│                                │
│   Name: [________________]     │
│                                │
│   [Cancel]         [Create]    │
└────────────────────────────────┘
```

Validation:
- Name is required, 1–50 characters.
- Name must be unique per (location, corp) — server enforces.

### Cargo Tab

The current cargo tab is a three-column layout (location list → source | staging | dest) keyed by `location_id`. It needs facility awareness and clear location context.

#### Location → Facility Navigation

The left-hand location list stays as-is (filterable, group-by-body). When the user clicks a location row, a **facility sub-list** expands inline beneath that row showing all of the player's facilities at that location. Each facility row displays:

```
  Shackleton Rim (S Pole)           Luna
    ├─ Alpha Base            ⚡ 6.4 MWe   📦 34 stacks
    └─ Mining Op 2           ⚡ 1.2 MWe   📦 8 stacks
  Low Earth Orbit                   Earth
    └─ Orbital Depot         ⚡ 3.0 MWe   📦 12 stacks
```

Clicking a facility row loads it as the cargo context — the source/dest entity selectors then show that facility's inventory + any ships docked at the parent location.

#### Header Context

When a facility is selected, the cargo panel header clearly shows **three levels** of context:

```
Body: Luna  →  Site: Shackleton Rim (S Pole)  →  Facility: Alpha Base
```

This breadcrumb appears above both the source and destination columns so the player always knows where they are.

#### Source/Dest Entity Selectors

The source and destination dropdowns change from listing raw locations to listing:
- **This facility** (the selected facility's inventory)
- **Other facilities** at the same location (player's own only)
- **Docked ships** at the parent location

Transfers between two of the player's own facilities at the same location are zero-cost (local transfer, no ship needed).

#### API Change

`GET /api/cargo/context/{location_id}` gains a required `facility_id` query parameter. The response includes facility metadata for display:

```json
{
    "facility_id": "uuid-1",
    "facility_name": "Alpha Base",
    "location_id": "LUNA_SHACKLETON",
    "location_name": "Shackleton Rim (S Pole)",
    "body_name": "Luna",
    "inventory": [...],
    "docked_ships": [...]
}
```

### Overview Tab

The Overview tab's detail panel gains a "Facilities" section listing all facilities at the selected location, replacing the current flat equipment grid.

---

## Power Balance

Each facility has its own independent power grid. The existing `compute_power_balance()` function in `industry_service.py` is already scoped by querying equipment at a location — it needs to be further scoped by `facility_id`:

```python
# Before
equipment = conn.execute(
    "SELECT * FROM deployed_equipment WHERE location_id = ? AND corp_id = ?",
    (location_id, corp_id)
).fetchall()

# After
equipment = conn.execute(
    "SELECT * FROM deployed_equipment WHERE facility_id = ?",
    (facility_id,)
).fetchall()
```

---

## Settle-on-Access

The existing settle pattern (`settle_industry()`) currently settles by `location_id`. This shifts to settle by `facility_id`:
- When a facility is loaded, settle all its refinery slots and construction queue.
- When the site overview is loaded, settle all facilities at that location.

Construction queue semantics must also shift from "one active queue item per location" to "one active queue item per facility". Constructor pool speed is summed from constructors in `construct` mode within the same facility only.

Mining remains infinite-resource, but mined output must be credited to the miner's facility inventory, not pooled location inventory.

---

## Deployment Risks and Mitigations

1. **Schema conflict risk**
    - Risk: migration id collision with existing `0023`.
    - Mitigation: use `0024_facilities` and keep ordered append-only migration policy.

2. **Inventory collision risk (highest)**
    - Risk: same-corp multi-facility stacks collide if key excludes `facility_id`.
    - Mitigation: re-key `location_inventory_stacks` during migration and update all inventory read/write helpers before enabling multi-facility creation.

3. **Cross-facility production leakage**
    - Risk: location-scoped queue settlement, input checks, and output delivery blend facilities.
    - Mitigation: make settle, queue, slot, and power queries facility-scoped first; keep location-level aggregations read-only summaries.

4. **Permission bypass risk**
    - Risk: using location-based routes can accidentally allow writes across a corp's facilities or expose extra details.
    - Mitigation: centralize `facility_id -> (location_id, corp_id)` resolution and enforce ownership checks in one shared helper.

5. **UI ambiguity risk**
    - Risk: users lose context of planet/site/facility when switching cargo entities.
    - Mitigation: always render Body → Site → Facility breadcrumb, and group facility rows under each location in the Cargo tab.

6. **Transition breakage risk**
    - Risk: existing tests and UI expect location-scoped endpoints.
    - Mitigation: ship dual-path compatibility temporarily, migrate UI to facility routes, then remove legacy paths in a final cleanup release.

---

## Open Questions / Future Considerations

1. **Facility capacity limits**: Should there be a max number of facilities per location? Per corp? This could be gated by a future site upgrade or research node.

2. **Shared resources**: Not applicable — all site resources are infinite. Each facility mines independently with no depletion or competition.

3. **Inter-facility transfers**: Can a player transfer cargo between two of their own facilities at the same site without a ship? **Recommendation**: Yes, as a zero-cost local transfer.

4. **Facility deletion**: What happens to equipment and cargo? **Recommendation**: Must undeploy all equipment and remove all cargo first (or transfer to another facility). Facility must be empty to delete.

5. **Default facility auto-creation**: When a player first deploys equipment at a new location, should the system auto-create a default facility (prompting for name)? Or require explicit creation first? **Recommendation**: Prompt for name on first deploy if no facility exists at that location.

6. **Site upgrade sidebar**: The sidebar is a placeholder. Future design doc will cover cooperative site projects (landing pads, fuel depots, communication arrays, etc.) that benefit all facilities.

---

## Implementation Phases

### Phase 1: Data Model + Migration
- Add `facilities` table and `facility_id` columns via migration `0024_facilities`.
- Re-key `location_inventory_stacks` to include `facility_id` in uniqueness.
- Backfill existing data with auto-created facilities.
- Add facility CRUD endpoints.

### Phase 2: Backend Scoping
- Modify all industry/inventory endpoints to accept and scope by `facility_id`.
- Update `settle_industry()` to settle per-facility.
- Update power balance to be per-facility.
- Move construction queue and constructor pool logic to per-facility semantics.
- Keep old `location_id`-based endpoints working (with auto-resolve for single-facility cases).

### Phase 3: Frontend — Facility Grid
- Add facility grid view to the Industrial tab.
- Add facility creation modal.
- Add breadcrumb/back navigation from facility → site.
- Add site upgrades sidebar placeholder.

### Phase 4: Frontend — Scoped Views
- Scope the Industrial management view to facility_id.
- Update Cargo tab with facility selector.
- Update Overview tab to show facility list in detail panel.

### Phase 5: Cleanup
- Remove legacy location-only query paths once all data has facility_ids.
- Add facility-related fields to the admin panel.

### Phase 6: Hardening + Cutover
- Remove dual-path fallbacks after telemetry/test pass.
- Enforce strict facility ownership checks on all write routes.
- Add migration verification checks (no corp-owned industry rows with NULL `facility_id`).
