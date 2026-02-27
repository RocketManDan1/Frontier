# Remove Multi-Step Path System — Direct A→B Transfers

Design document for eliminating the intermediate-step/path system in ship transfers and replacing it with direct A→B movement using Lambert-solved trajectories.

---

## Motivation

The current transfer system routes ships through multi-hop Dijkstra paths (e.g., GEO → LEO → LMO) even though:

1. **Ships never stop at intermediate waypoints.** The path is stored but `settle_arrivals()` teleports the ship directly to `to_location_id` — intermediate nodes are never visited.
2. **The Lambert solver already computes complete interplanetary arcs.** It produces exact Δv, TOF, and a trajectory polyline from departure body to arrival body. The Dijkstra path is redundant for cost computation.
3. **Path reconstruction is fragile.** When rendering in-transit ships, the backend re-derives leg timelines from `transfer_path_json` at the original departure time. Edge data changes, malformed paths, and timing ambiguity cause rendering glitches.
4. **Three competing rendering pipelines coexist on the frontend** — `buildCompositeCurve()` (multi-leg stitching), `buildTrajectoryArc()` (Lambert polyline), and `computeHohmannArc()` (estimated Hohmann fallback). These interact uneasily and produce visual inconsistencies.

### Goal

Every ship transfer is a single **A→B** operation. No intermediate path, no stored legs. The Lambert solver (interplanetary) or static edge lookup (local) provides all cost/trajectory data. Rendering uses a single trajectory polyline or a simple local-orbit curve.

---

## Architecture: Option 3 — Auto-Compute Gateway Costs

When a player requests a transfer from a non-gateway orbit to a destination on another body (e.g., GEO → LMO), the backend:

1. **Identifies the gateway nodes** — the nearest orbit node on each body that participates in an interplanetary edge (e.g., LEO for Earth, LMO for Mars).
2. **Sums the local Δv/TOF** for departure-side local hops (GEO → LEO) and arrival-side local hops (if any).
3. **Runs the Lambert solver** for the interplanetary segment (LEO → LMO).
4. **Returns a single combined quote** — total Δv = local departure + Lambert + local arrival; total TOF = sum of all.
5. **Stores only `from_id` and `to_id`** on the ship — no intermediate path.

The ship "teleports" through the local orbit change (instantaneously consuming its Δv) and then flies the interplanetary arc. This is physically reasonable — local orbit changes take hours while interplanetary transfers take weeks/months.

---

## Transfer Categories

### 1. Local Transfers (Same Body)

**Examples:** LEO ↔ GEO, LLO ↔ HLO, LMO ↔ PHOBOS_LO

- **Cost source:** Static `transfer_edges` table (direct edge lookup)
- **Rendering:** Bézier curve around parent body (existing `computeTransitCurve()` non-interplanetary path)
- **Path needed:** No — direct edges exist for all local pairs
- **Change:** Minimal. Works exactly as today but without wrapping in a path array.

### 2. Interplanetary Transfers from Gateway

**Examples:** LEO → LMO, LEO → VEN_ORB, MERC_ORB → LEO

- **Cost source:** Lambert solver via `transfer_planner.compute_interplanetary_leg()`
- **Rendering:** Server-computed trajectory polyline (`trajectory_json`) rendered via `buildTrajectoryArc()`
- **Path needed:** No — single A→B
- **Change:** Minimal. Already works as a single leg today when the endpoints are gateways.

### 3. Interplanetary Transfers from Non-Gateway (NEW LOGIC)

**Examples:** GEO → LMO, HLO → MERC_ORB, L1 → VEN_ORB

- **Cost source:** Auto-computed as: local Δv (origin → gateway) + Lambert Δv (gateway → gateway) + local Δv (gateway → destination)
- **Rendering:** Server-computed Lambert trajectory polyline (the local orbit change is invisible at solar-system scale)
- **Path needed:** No — stored as single A→B
- **Change:** New function `_compute_direct_transfer_quote()` replaces Dijkstra pathfinding.

### 4. Surface Site Transfers

**Examples:** MARS_OLYMPUS → LMO, LEO → LUNA_SOUTH_POLE

- **Cost source:** Surface landing/launch Δv (from `surface_sites` config) + local/interplanetary Δv as above
- **Rendering:** Same as categories 1–3 depending on whether it's local or interplanetary
- **Change:** Surface-site edges are already direct. TWR gate check simplifies (only check origin and destination, not intermediate path nodes).

---

## Implementation Plan

### Phase 1: New Quote Function (Backend)

Replace `_solve_dynamic_route()` (Dijkstra) with `_compute_direct_quote()`.

**File:** `fleet_router.py`  

#### New Function: `_compute_direct_quote()`

```python
def _compute_direct_quote(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    departure_time_s: float,
    extra_dv_fraction: float,
) -> Optional[Dict[str, Any]]:
    """Compute a direct A→B transfer quote.

    For local transfers (same body): look up the direct edge.
    For interplanetary transfers: auto-resolve gateways, sum local +
    Lambert costs, return a single-segment quote.
    """
```

Logic:

1. If `from_id == to_id` → return zero-cost quote.
2. Check if a **direct edge** exists in `transfer_edges`. If so, use `_compute_leg_at_departure()` and return.
3. If `_is_interplanetary(from_id, to_id)`:
   a. Find the cheapest **gateway pair** — the interplanetary edge endpoints closest to `from_id` and `to_id` within their respective bodies.
   b. Sum: local departure Δv/TOF (from_id → departure gateway, via direct edge or zero if same) + Lambert Δv/TOF (gateway → gateway) + local arrival Δv/TOF (arrival gateway → to_id, via direct edge or zero if same).
   c. Return combined quote with `is_interplanetary: true`.
4. If no interplanetary edge and no direct edge → return `None` (unreachable).

#### New Helper: `_find_gateway_pair()`

```python
def _find_gateway_pair(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
) -> Optional[Tuple[str, str, float, float, float, float]]:
    """Find the best interplanetary gateway pair for a cross-body transfer.

    Returns (dep_gateway, arr_gateway,
             local_dep_dv, local_dep_tof,
             local_arr_dv, local_arr_tof)
    or None if no interplanetary connection exists.
    """
```

Logic:

1. Get the parent body of `from_id` and `to_id` from `transfer_planner.location_parent_body()`.
2. Query `transfer_edges WHERE edge_type = 'interplanetary'` to find all interplanetary edges whose `from_id` is on the same body as origin and `to_id` is on the same body as destination.
3. For each candidate gateway pair, look up local edge costs (gateway ↔ origin/destination). Pick the pair with lowest total Δv.
4. Return the gateway IDs and local hop costs.

#### Functions to Remove

| Function | File | Reason |
|---|---|---|
| `_solve_dynamic_route()` | `fleet_router.py` | Dijkstra pathfinder — replaced by `_compute_direct_quote()` |
| `_compute_route_quote_from_path()` | `fleet_router.py` | Path-based re-quote for in-transit ships — no longer needed |
| `_get_transfer_graph()` | `fleet_router.py` | Graph builder for Dijkstra — no longer needed |
| `_load_matrix_path()` | `fleet_router.py` | Path JSON loader/fixer — no longer needed |
| `_route_legs_timeline()` | `fleet_router.py` | Leg timeline builder for frontend — no longer needed |
| `_TRANSFER_GRAPH_CACHE` | `fleet_router.py` | Graph cache dict — no longer needed |

#### Modified Functions

| Function | File | Change |
|---|---|---|
| `_compute_route_quote()` | `fleet_router.py` | Call `_compute_direct_quote()` instead of `_solve_dynamic_route()` |
| `api_ship_transfer()` | `fleet_router.py` | Stop storing `transfer_path_json`, stop computing `transfer_legs`. Store only `from_id`, `to_id`, `trajectory_json`. |
| `api_state()` | `fleet_router.py` | Stop sending `transfer_path`, `transfer_legs` to frontend. Send `trajectory` only. |
| `api_transfer_quote()` | `fleet_router.py` | Remove `path` from response; simplify response shape. |
| `api_transfer_quote_advanced()` | `fleet_router.py` | Remove `path` and multi-leg orbital fields; simplify. |
| `settle_arrivals()` | `main.py` | Remove `transfer_path_json = '[]'` reset (column deprecated). |

### Phase 2: Simplify Ship DB Schema

**File:** `db_migrations.py`

Add a new migration to mark `transfer_path_json` as deprecated / set all values to `'[]'`:

```python
def _migration_NNNN_remove_transfer_path(conn: sqlite3.Connection) -> None:
    """Deprecate transfer_path_json — all transfers are now direct A→B."""
    conn.execute("UPDATE ships SET transfer_path_json = '[]' WHERE transfer_path_json != '[]'")
```

#### Ship Columns — Status After Migration

| Column | Status | Notes |
|---|---|---|
| `from_location_id` | **KEEP** | Origin of current transfer |
| `to_location_id` | **KEEP** | Destination of current transfer |
| `departed_at` | **KEEP** | Game time of departure |
| `arrives_at` | **KEEP** | Game time of arrival |
| `transit_from_x/y` | **KEEP** | Snapshot coordinates for fallback rendering |
| `transit_to_x/y` | **KEEP** | Snapshot coordinates for fallback rendering |
| `trajectory_json` | **KEEP** | Lambert trajectory polyline |
| `dv_planned_m_s` | **KEEP** | Total Δv consumed |
| `transfer_path_json` | **DEPRECATE** | Always `'[]'`, stop reading/writing |

### Phase 3: Simplify API Response Shape

#### `/api/state` Ship Object — Before vs After

```jsonc
// BEFORE
{
  "id": "ship_1",
  "from_location_id": "GEO",
  "to_location_id": "LMO",
  "transfer_path": ["GEO", "LEO", "LMO"],  // REMOVE
  "transfer_legs": [                         // REMOVE
    { "from_id": "GEO", "to_id": "LEO", "departure_time": 1000, "arrival_time": 1600, ... },
    { "from_id": "LEO", "to_id": "LMO", "departure_time": 1600, "arrival_time": 50000, ... }
  ],
  "trajectory": [                            // KEEP
    { "from_id": "LEO", "to_id": "LMO", "points": [[x,y], ...] }
  ],
  "transit_from_x": 100.0,
  "transit_to_x": 500.0,
  ...
}

// AFTER
{
  "id": "ship_1",
  "from_location_id": "GEO",
  "to_location_id": "LMO",
  "is_interplanetary": true,                 // NEW: flag for frontend rendering
  "trajectory": [[x,y], [x,y], ...],         // SIMPLIFIED: flat array of points (or null for local)
  "transit_from_x": 100.0,
  "transit_to_x": 500.0,
  ...
}
```

Key changes:
- `transfer_path` removed — frontend shows `from_location_id → to_location_id` directly.
- `transfer_legs` removed — no multi-leg timeline.
- `trajectory` simplified — flat `[[x,y], ...]` array instead of array-of-leg-objects. `null` for local transfers (frontend computes Bézier).
- `is_interplanetary` added — so the frontend knows which rendering path to use.

#### `/api/transfer_quote` Response — Before vs After

```jsonc
// BEFORE
{ "from_id": "GEO", "to_id": "LMO", "dv_m_s": 7300, "tof_s": 22500000,
  "path": ["GEO", "LEO", "LMO"], "is_interplanetary": true }

// AFTER
{ "from_id": "GEO", "to_id": "LMO", "dv_m_s": 7300, "tof_s": 22500000,
  "is_interplanetary": true,
  "local_departure_dv_m_s": 900,   // optional breakdown
  "interplanetary_dv_m_s": 5700,
  "local_arrival_dv_m_s": 0 }
```

### Phase 4: Simplify Frontend Rendering

**File:** `static/js/app.js`

#### Functions to Remove

| Function | Lines | Reason |
|---|---|---|
| `buildCompositeCurve()` | ~3288–3370 | Multi-leg curve stitcher — no more legs |
| `compositePoint()` | ~3372–3400 | Composite curve interpolator — no more composite curves |

#### Functions to Simplify

| Function | Change |
|---|---|
| Ship transit rendering block (~L5245–5300) | Remove `transfer_legs` iteration and `buildCompositeCurve()` call. Use `buildTrajectoryArc()` if `ship.trajectory` exists, else `computeTransitCurve()`. |
| `ensureTransitAnchorsForShips()` (~L5666–5685) | Remove leg-level bucket collection. Only need departure/arrival buckets. |
| Ship tooltip (~L7148) | Replace `transfer_path` display with simple `from → to`. |

**File:** `static/js/fleet.js`

| Location | Change |
|---|---|
| Line ~689 | Replace `transfer_path.join(" → ")` with `${ship.from_location_id} → ${ship.to_location_id}` |

#### Rendering Decision Tree (Simplified)

```
Ship in transit?
 ├─ ship.trajectory exists (interplanetary)?
 │   └─ buildTrajectoryArc(ship.trajectory)
 │       → Kepler-propagated polyline, warp-adjusted for planet motion
 └─ No trajectory (local transfer)?
     └─ computeTransitCurve(fromLoc, toLoc, isInterplanetary=false)
         → Bézier curve around parent body
```

### Phase 5: Clean Up Transfer Planner

**File:** `transfer_planner.py` — No changes needed. The Lambert solver, porkchop plot, `compute_interplanetary_leg()`, `compute_leg_trajectory()`, and `is_interplanetary()` all remain as-is. They are already single-leg A→B functions.

**File:** `celestial_config.py` — No changes needed. Body state vectors, position computation, and edge/location parsing are unaffected.

---

## Detailed Gateway Resolution Algorithm

When a player at GEO wants to fly to MARS_OLYMPUS:

```
1. from_id = "GEO",  to_id = "MARS_OLYMPUS"
2. is_interplanetary("GEO", "MARS_OLYMPUS") → True (earth vs mars)

3. Resolve departure gateway:
   - from_body = "earth"
   - Find interplanetary edges FROM earth-body locations TO mars-body locations
   - Candidates: LEO→LMO (dv=6500, gateway pair)
   - Local cost GEO→LEO: lookup direct edge → dv=900, tof=21600

4. Resolve arrival gateway:
   - to_body = "mars"
   - LMO is the arrival gateway
   - Need to reach MARS_OLYMPUS from LMO
   - MARS_OLYMPUS is a surface site → lookup edge LMO→MARS_OLYMPUS → dv=3800, tof=5400

5. Lambert solve: LEO → LMO at current game time
   - Returns: dv=5700, tof=22464000, trajectory polyline

6. Combined quote:
   - total_dv = 900 (GEO→LEO) + 5700 (Lambert) + 3800 (LMO→MARS_OLYMPUS) = 10400 m/s
   - total_tof = 21600 + 22464000 + 5400 = 22491000 s
   - trajectory = Lambert polyline (local legs invisible at solar scale)
   - is_interplanetary = true

7. Ship record:
   - from_location_id = "GEO"
   - to_location_id = "MARS_OLYMPUS"
   - trajectory_json = <Lambert polyline>
   - arrives_at = departed_at + total_tof
```

---

## Edge Cases

### 1. No Direct Edge and No Interplanetary Route

If `from_id` and `to_id` are on the same body but have no direct edge (e.g., two surface sites on the same body), the system should still find a path through their shared orbit node. This requires a **local mini-graph lookup** within the body — walk up to the common orbit node and back down.

**Resolution:** For same-body transfers without a direct edge, do a 2-hop lookup: `from_id → orbit_node → to_id`. Surface sites always have a direct edge to their `orbit_node_id`, so this covers all cases.

### 2. Multiple Gateway Pairs

For bodies with multiple orbit nodes connected to different interplanetary edges (e.g., Earth could have both LEO→LMO and GEO→something), pick the gateway pair with the lowest total Δv (local + Lambert + local).

**Resolution:** In the initial implementation, try all interplanetary edge pairs and pick the cheapest. For the current edge graph this is at most a handful of candidates per body pair.

### 3. In-Transit Ships During Migration

Ships already in transit with a stored `transfer_path_json` need to complete their journey normally. Their `from_location_id` and `to_location_id` are already set correctly, and `arrives_at` determines when they arrive.

**Resolution:** `settle_arrivals()` already only uses `to_location_id` — it doesn't read the path. In-transit ships will complete normally. The frontend may briefly lose the multi-leg rendering for these ships and fall back to a single-arc rendering, which is acceptable.

### 4. Transfer Quote vs Transfer Execution Consistency

The quote and execution must use the same logic. Currently the quote calls `_compute_route_quote()` and the transfer POST also calls it. This pattern continues — just with `_compute_direct_quote()` instead.

---

## Migration Checklist

### Backend (`fleet_router.py`)

- [x] Add `_find_gateway_pair()` helper
- [x] Add `_compute_direct_quote()` function
- [x] Replace `_compute_route_quote()` internals to call `_compute_direct_quote()`
- [x] Remove `_solve_dynamic_route()`
- [x] Remove `_compute_route_quote_from_path()`
- [x] Remove `_get_transfer_graph()` and `_TRANSFER_GRAPH_CACHE`
- [x] Remove `_load_matrix_path()`
- [x] Remove `_route_legs_timeline()`
- [x] Simplify `api_ship_transfer()` — stop storing path, stop computing legs
- [x] Simplify `api_state()` — stop sending `transfer_path`, `transfer_legs`; flatten `trajectory`
- [x] Simplify `api_transfer_quote()` — remove `path` from response
- [x] Simplify `api_transfer_quote_advanced()` — remove `path` and multi-leg fields
- [x] Update TWR gate in `api_ship_transfer()` — check only origin and destination, not path nodes
- [x] Update surface-site claim gate — check only destination

### Backend (`main.py`)

- [x] Simplify `settle_arrivals()` — remove `transfer_path_json` reset

### Backend (`db_migrations.py`)

- [x] Add migration to null out `transfer_path_json` for all ships

### Backend (`shipyard_router.py`, `admin_game_router.py`)

- [x] Remove `transfer_path_json` from ship INSERT statements
- [x] Remove `transfer_path_json` reset from teleport UPDATE

### Frontend (`static/js/app.js`)

- [x] Remove `buildCompositeCurve()` function
- [x] Remove `compositePoint()` / `compositeTangent()` / `compositeDistAtT()` functions
- [x] Remove `drawFutureTransitLegs()` function
- [x] Remove `pickActiveTransferLeg()` function
- [x] Remove `drawTransitLegMarkers()` function
- [x] Remove composite case from `curvePoint()` / `curveTangent()` dispatchers
- [x] Simplify ship transit rendering — remove `transfer_legs` iteration
- [x] Simplify `ensureTransitAnchorsForShips()` — remove leg-level bucket collection
- [x] Update ship tooltip — replace `transfer_path` display with `from → to`
- [x] Update transit curve selection — use `ship.trajectory` (flat array) or `computeTransitCurve()` fallback

### Frontend (`static/js/fleet.js`)

- [x] Replace `transfer_path.join(" → ")` display with `from_location_id → to_location_id`

### Tests

- [x] Update `test_game_logic.py` — transfer tests should expect no path/legs in responses
- [x] Add test for gateway auto-resolution (GEO → LMO should work and include local Δv)
- [x] Add test for same-body non-direct-edge transfers (surface site → surface site)
- [x] Verify porkchop endpoint still works (no changes expected)

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| In-transit ships lose multi-leg rendering during migration | Certain | Low | They fall back to single-arc. Acceptable for the brief overlap period. |
| Gateway resolution picks suboptimal route | Low | Medium | The edge graph is small — brute-force all candidates. Add a unit test. |
| Surface-to-surface same-body transfer fails | Medium | Medium | Handle the 2-hop case explicitly (up to orbit node, back down). |
| Frontend breaks from missing `transfer_legs` | Certain | Low | The frontend already has fallback paths for when legs are empty/missing. Clean up to remove dead branches. |
| External tools relying on `path` in API response | Low | Low | No known external consumers. |

---

## Success Criteria

1. Ship transfers from any location to any reachable location produce correct Δv and TOF.
2. No `transfer_path_json` is written or read (column contains only `'[]'`).
3. No `transfer_legs` appear in API responses.
4. Interplanetary ships render along Lambert trajectory polylines.
5. Local ships render along Bézier curves.
6. All existing tests pass; new gateway-resolution tests pass.
7. Porkchop plot endpoint works identically.
