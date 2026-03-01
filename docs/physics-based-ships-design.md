# Physics-Based Ships — Design & Implementation Plan

## Goal

Replace the current "timer teleport" ship model with **real 2D patched-conic orbital mechanics**. Ships carry actual orbital state, coast on Keplerian orbits between burns, and execute engine burns that change their orbit. The transfer planner still exists — it computes the optimal burn sequence — but the ship's motion is the physical consequence of those burns, not a scripted animation.

This eliminates all trajectory rendering hacks (snapshot coordinates, Bézier/Hohmann fallbacks, body-centric fake ellipses, `transit_from_x/y` workarounds) because the rendered path **is** the ship's actual orbit.

---

## Current Architecture (what we're replacing)

### Ship state today

```
ships table:
  location_id          — non-null when docked, NULL when in transit
  from_location_id     — set during transit
  to_location_id       — set during transit
  departed_at          — game time of departure
  arrives_at           — game time of guaranteed arrival
  dv_planned_m_s       — total Δv committed
  transit_from_x/y     — snapshot coordinates at departure
  transit_to_x/y       — snapshot coordinates at arrival
  trajectory_json      — cosmetic polyline for rendering
```

### Transfer flow today

1. Player clicks transfer destination
2. Server computes route quote (Lambert for interplanetary, static edges for local)
3. Server deducts fuel, sets `arrives_at = now + tof`
4. Ship exists in limbo until `settle_arrivals()` fires when `game_time >= arrives_at`
5. Ship teleports to `to_location_id`
6. Frontend renders a cosmetic trajectory (Bézier, Hohmann arc, or Lambert polyline) that has no physical meaning

### Problems this causes

- `trajectory_json` is a frozen polyline computed at departure time but planets move during transit → visual drift
- `transit_from_x/y` and `transit_to_x/y` are departure-time snapshots → stale for long transfers
- Frontend has complex warping logic (`computeCurveWarp`, `trackStartOrig`, `trackEndOrig`) to compensate for moving bodies → fragile, buggy
- Body-centric SOI trajectories (`compute_soi_transfer_trajectory`) are fake Hohmann half-ellipses, not actual transfer orbits
- No notion of "the ship is at position X right now" — only "it's somewhere between A and B"

---

## New Architecture

### Core principle

**A ship IS an orbit.** At any game time `t`, the ship's position is deterministically computed from its orbital elements + the central body's position. No interpolation, no snapshots, no warping.

### Ship state (new)

```sql
-- New columns (migration 0016_orbit_state)
orbit_json      TEXT    -- Keplerian elements: {body_id, a_km, e, omega_deg, M0_deg, epoch_s, direction}
maneuver_json   TEXT    -- Scheduled burns: [{time_s, prograde_m_s, radial_m_s, node_label}, ...]
orbit_body_id   TEXT    -- Denormalized: which body the ship orbits (for fast queries)
```

`orbit_json` encodes a 2D Keplerian orbit:

```json
{
  "body_id": "earth",
  "a_km": 6778.0,
  "e": 0.0,
  "omega_deg": 0.0,
  "M0_deg": 45.0,
  "epoch_s": 946684800.0,
  "direction": 1
}
```

| Field | Meaning |
|---|---|
| `body_id` | Central body (patched-conic: ship is always in exactly one body's SOI) |
| `a_km` | Semi-major axis (km). Negative for hyperbolic orbits. |
| `e` | Eccentricity. 0=circular, 0<e<1=elliptic, e≥1=hyperbolic escape |
| `omega_deg` | Argument of periapsis — orbit orientation in the 2D plane (degrees) |
| `M0_deg` | Mean anomaly at epoch (degrees). For hyperbolic: mean hyperbolic anomaly. |
| `epoch_s` | Game time when these elements are valid |
| `direction` | +1 prograde (CCW), -1 retrograde (CW) |

This is enough for a full 2D orbit. No inclination/Ω needed since we're 2D.

### Columns retired

These become unnecessary and can be phased out (nullable, ignored by new code):

| Column | Why retired |
|---|---|
| `from_location_id` | Ship is at a computed position, not "between" two locations |
| `to_location_id` | Same |
| `departed_at` | Replaced by maneuver schedule |
| `arrives_at` | No guaranteed arrival — ship arrives when its orbit reaches the target |
| `transit_from_x/y` | No snapshots needed — position is computed |
| `transit_to_x/y` | Same |
| `trajectory_json` | Orbit IS the trajectory — render it from elements |
| `dv_planned_m_s` | Replaced by maneuver_json sum |

These columns stay but `location_id` gains new semantics:
- `location_id` — non-null when **docked** at a location (orbit node like LEO, GEO, etc.). NULL when in free flight. Note: there are no physical station objects yet — a "location" is an orbit-node definition from `celestial_config.json` with a `body_id` + `radius_km`.

---

## Three Ship States

### 1. Docked

```
location_id = "LEO"
orbit_json = {"body_id": "earth", "a_km": 6778, "e": 0.0, ...}  -- the location's orbit
maneuver_json = NULL or "[]"
```

Ship is parked at a location (orbit node). It has orbital elements matching the location's orbit (so undocking is seamless). Rendered at the location's position — same as today.

### 2. Free flight (coasting)

```
location_id = NULL
orbit_json = {"body_id": "sun", "a_km": 225000000, "e": 0.21, ...}
maneuver_json = "[]"
```

Ship is on a Keplerian orbit. Position at any time `t` is computed analytically:

```
1. Compute mean anomaly: M = M0 + n × (t - epoch_s)  where n = √(μ/a³)
2. Solve Kepler's equation: M → E (eccentric anomaly)
3. Compute true anomaly: E → ν
4. Compute radius: r = a(1-e²) / (1+e·cos(ν))
5. Compute position in perifocal frame: (r·cos(ν), r·sin(ν))
6. Rotate by ω to parent body frame
7. Add parent body's position (from celestial_config) for heliocentric coordinates
```

**This is zero server cost per tick.** The orbit propagates analytically to any time.

### 3. Maneuvering (has scheduled burns)

```
location_id = NULL
orbit_json = {"body_id": "earth", "a_km": 6778, "e": 0.0, ...}  -- current coast orbit
maneuver_json = [
  {"time_s": 946685000, "prograde_m_s": 3200, "radial_m_s": 0, "label": "Trans-Mars injection"},
  {"time_s": 964905000, "prograde_m_s": -1100, "radial_m_s": 0, "label": "Mars orbit insertion"}
]
```

Ship is coasting on `orbit_json` until the next burn time. When `game_time >= maneuver[0].time_s`:

```
1. Propagate orbit to burn time → get (r, v) at that instant
2. Compute burn direction: prograde = v̂, radial = r̂
3. Apply Δv: v_new = v + prograde·v̂ + radial·r̂
4. Convert (r, v_new) → new orbital elements
5. Overwrite orbit_json with new orbit
6. Pop the executed maneuver from maneuver_json
7. Check for SOI transitions (see below)
```

---

## State Vector ↔ Orbital Elements Conversion

### Elements → State Vector (already exists)

`celestial_config._compute_keplerian_state_3d()` does exactly this. For 2D ships, we use the same math but skip the 3D rotation (i=0, Ω=0, only ω matters).

### State Vector → Elements (new — needed after burns)

After applying a Δv burn, we have `(r_vec, v_vec)` and need to convert back to `(a, e, ω, M0)`:

```python
def state_to_elements_2d(r_vec, v_vec, mu):
    """Convert 2D state vector to Keplerian elements.
    
    r_vec = (x, y) in km, relative to central body
    v_vec = (vx, vy) in km/s
    mu = gravitational parameter (km³/s²)
    
    Returns: (a_km, e, omega_deg, nu_deg)
    """
    r = |r_vec|
    v = |v_vec|
    
    # Specific orbital energy → semi-major axis
    energy = v²/2 - μ/r
    a = -μ / (2 × energy)    # positive for ellipse, negative for hyperbola
    
    # Eccentricity vector
    e_vec = ((v² - μ/r) × r_vec - (r_vec · v_vec) × v_vec) / μ
    e = |e_vec|
    
    # Argument of periapsis (angle of e_vec from x-axis)
    ω = atan2(e_vec.y, e_vec.x)
    
    # True anomaly (angle from periapsis to current position)
    ν = atan2(r_vec.y, r_vec.x) - ω
    
    # True anomaly → eccentric anomaly → mean anomaly (for epoch storage)
    E = atan2(√(1-e²) × sin(ν), e + cos(ν))
    M = E - e × sin(E)
    
    return a, e, ω, M
```

This is textbook orbital mechanics (`rv2coe` in Vallado). ~30 lines of code.

---

## SOI Transitions

### Detection

When a ship's orbit is propagated forward, check if its distance from any nearby body crosses that body's SOI radius:

```python
def check_soi_transition(ship_orbit, current_time, bodies):
    """Check if ship crosses an SOI boundary."""
    ship_pos = propagate(ship_orbit, current_time)  # in parent frame
    
    # Check children of current central body (entering child SOI)
    for child in get_children(ship_orbit.body_id):
        child_pos = get_body_position(child, current_time)
        dist = |ship_pos - child_pos|
        if dist < child.soi_radius_km:
            return ("enter", child.id, ship_pos, ship_vel)
    
    # Check if leaving current body's SOI (distance > SOI radius)
    if ship_orbit.body_id != "sun":
        dist_from_parent = |ship_pos|  # already in parent-centric frame
        body = get_body(ship_orbit.body_id)
        if dist_from_parent > body.soi_radius_km:
            return ("exit", parent_of(body), ship_pos, ship_vel)
    
    return None
```

### Frame Transform

When entering a child body's SOI:

```python
# Ship state in parent frame
r_parent, v_parent = propagate(ship_orbit, transition_time)

# Child body state in parent frame
r_child, v_child = get_body_state(child_id, transition_time)

# Convert to child-centric frame
r_local = r_parent - r_child
v_local = v_parent - v_child

# Compute new orbit in child's frame
new_elements = state_to_elements_2d(r_local, v_local, mu_child)
ship.orbit_json = {body_id: child_id, ...new_elements}
```

When exiting to parent:

```python
# Ship state in body-centric frame
r_local, v_local = propagate(ship_orbit, transition_time)

# Body state in parent frame
r_body, v_body = get_body_state(ship.body_id, transition_time)

# Convert to parent frame
r_parent = r_local + r_body
v_parent = v_local + v_body

new_elements = state_to_elements_2d(r_parent, v_parent, mu_parent)
ship.orbit_json = {body_id: parent_id, ...new_elements}
```

### When to check

SOI transitions only need checking for ships on escape/hyperbolic orbits (`e >= 1.0`) or highly elliptical orbits that reach beyond the SOI. Ships in stable circular orbits around a body never cross SOI boundaries.

Logic:
- At burn execution: check immediately (burn may put ship on escape trajectory)
- Periodic: for ships with `e >= 0.9` or `apoapsis > 0.8 × SOI_radius`, compute the time of SOI crossing analytically and schedule it like a maneuver event.

The SOI crossing time can be pre-computed: find `t` where `r(t) = SOI_radius`. This is a Kepler equation solve — we already have the machinery. Store it as a special entry in `maneuver_json`:

```json
{"time_s": 946900000, "type": "soi_exit", "to_body_id": "sun"}
```

This way SOI transitions are event-driven, not polled.

---

## Transfer Planner (Modified)

### What changes

The transfer planner currently returns a single `{dv_m_s, tof_s}` quote. It needs to return a **burn sequence** instead:

```python
def plan_transfer(from_location, to_location, departure_time):
    """Plan a physically-realizable transfer as a sequence of burns.
    
    Returns:
        {
            "burns": [
                {
                    "time_s": 946685000,
                    "prograde_m_s": 3200.0,
                    "radial_m_s": 0.0,
                    "body_id": "earth",
                    "label": "Trans-Mars injection",
                    "orbit_after": {...}  # predicted orbit elements after burn
                },
                {
                    "time_s": ...,
                    "prograde_m_s": -1100.0,
                    "radial_m_s": 0.0,
                    "body_id": "mars",
                    "label": "Mars orbit insertion",
                    "orbit_after": {...}
                }
            ],
            "total_dv_m_s": 4300.0,
            "total_tof_s": 18220000,
            "predicted_arrival_s": 964905000,
            "soi_transitions": [
                {"time_s": 946690000, "from_body": "earth", "to_body": "sun"},
                {"time_s": 964890000, "from_body": "sun", "to_body": "mars"}
            ],
            "orbit_predictions": [
                {"from_s": 946685000, "to_s": 946690000, "body_id": "earth", "elements": {...}},
                {"from_s": 946690000, "to_s": 964890000, "body_id": "sun", "elements": {...}},
                {"from_s": 964890000, "to_s": 964905000, "body_id": "mars", "elements": {...}},
                {"from_s": 964905000, "to_s": null, "body_id": "mars", "elements": {...}}
            ]
        }
    """
```

### How it works internally

The existing Lambert machinery does the hard work:

1. **Departure burn**: Lambert gives us `v1_transfer` (heliocentric velocity needed). The ship is currently in a parking orbit around the departure body at velocity `v_circular`. The burn is:
   - In body-centric frame: `Δv = v_hyperbolic_departure - v_circular`
   - Decompose into prograde + radial components relative to the circular orbit

2. **Heliocentric coast**: The Lambert solution arc IS the orbit. Convert `(r1, v1_transfer)` to heliocentric Keplerian elements → this becomes the ship's `orbit_json` after the departure burn + SOI exit.

3. **Arrival burn**: Lambert gives us `v2_transfer` (heliocentric arrival velocity). After entering the destination body's SOI, the ship is on a hyperbolic approach. The capture burn is:
   - `Δv = v_circular_target - v_hyperbolic_arrival`

4. **SOI transition times**: Pre-compute when the ship exits departure body's SOI and enters destination body's SOI from the heliocentric orbit.

### Local transfers (same body)

For LEO→GEO type transfers, the Hohmann transfer is computed as two burns:

```
Burn 1 (at periapsis): Δv₁ = v_transfer_periapsis - v_circular_LEO
Burn 2 (at apoapsis):  Δv₂ = v_circular_GEO - v_transfer_apoapsis
```

The intermediate orbit is a transfer ellipse with periapsis at LEO radius and apoapsis at GEO radius.

### What stays the same

- Lambert solver (`lambert.py`) — unchanged
- Porkchop plot endpoint — unchanged (still scans Δv grids)
- `compute_body_state()` — unchanged
- Transfer edge topology — still defines which locations connect
- Fuel/Δv/TWR checks — still validated before committing

---

## Event-Driven Execution (replaces `settle_arrivals`)

### New function: `settle_ship_events(conn, now_s)`

```python
def settle_ship_events(conn, now_s):
    """Process all ship orbital events up to now_s."""
    
    # 1. Execute pending burns where time_s <= now_s
    ships = query("SELECT * FROM ships WHERE maneuver_json IS NOT NULL AND maneuver_json != '[]'")
    for ship in ships:
        maneuvers = json.loads(ship.maneuver_json)
        orbit = json.loads(ship.orbit_json)
        
        while maneuvers and maneuvers[0]["time_s"] <= now_s:
            m = maneuvers.pop(0)
            
            if m.get("type") == "soi_exit" or m.get("type") == "soi_enter":
                orbit = execute_soi_transition(orbit, m)
            else:
                orbit = execute_burn(orbit, m)
                # After burn: check if new orbit escapes SOI
                maybe_schedule_soi_transition(orbit, maneuvers)
            
        update_ship(ship.id, orbit, maneuvers)
    
    # 2. Auto-dock: ships whose current orbit matches a location's orbit
    check_auto_docking(conn, now_s)
```

### Auto-docking

A ship auto-docks at a **location** (orbit node) when:
- Its `orbit_body_id` matches the location's body
- Its orbital radius (for circular/near-circular) is within tolerance of the location's `radius_km`
- Its eccentricity is below a docking threshold (e.g., `e < 0.05`)
- Its `maneuver_json` is empty (not mid-maneuver sequence)

> **Note:** Locations are orbit-node entries from `celestial_config.json` (LEO, GEO, lunar orbit, L-points, etc.) — not physical station objects. Ships park directly at these orbital locations. Station objects may be added later.

```python
def check_auto_docking(conn, now_s):
    """Dock free-flying ships that have arrived at matching location orbits."""
    free_ships = query("SELECT * FROM ships WHERE location_id IS NULL AND orbit_json IS NOT NULL")
    
    for ship in free_ships:
        orbit = json.loads(ship.orbit_json)
        if orbit["e"] > 0.05:
            continue  # not circular enough to dock
        
        # Find orbit-node locations around this body at this radius
        locations = get_orbit_nodes_for_body(orbit["body_id"])
        for loc in locations:
            r_diff = abs(orbit["a_km"] - loc["radius_km"]) / loc["radius_km"]
            if r_diff < 0.01:  # within 1% of location orbit
                dock_ship(ship.id, loc["id"])
                break
```

This replaces the current `settle_arrivals()` which just sets `location_id = to_location_id` on a timer.

---

## Frontend Changes

### Orbit rendering (replaces trajectory_json)

The server sends orbital elements instead of trajectory polylines. The client renders ellipses directly:

```javascript
function drawShipOrbit(ship, bodyPositions, gameTime) {
    const orbit = ship.orbit;
    const body = bodyPositions[orbit.body_id];
    if (!body) return;
    
    const mu = body.mu_km3_s2;
    const a = orbit.a_km;
    const e = orbit.e;
    const omega = orbit.omega_deg * DEG2RAD;
    
    // Draw orbital ellipse
    const b = a * Math.sqrt(1 - e * e);
    const cx = body.x - a * e * Math.cos(omega);  // center of ellipse
    const cy = body.y - a * e * Math.sin(omega);
    
    graphics.ellipse(cx * SCALE, cy * SCALE, a * SCALE, b * SCALE);
    graphics.rotation = omega;
    
    // Compute ship position on orbit at current time
    const n = Math.sqrt(mu / (a * a * a));
    const dt = gameTime - orbit.epoch_s;
    const M = (orbit.M0_deg * DEG2RAD + n * dt) % (2 * Math.PI);
    const E = solveKepler(M, e);
    const nu = 2 * Math.atan2(Math.sqrt(1+e) * Math.sin(E/2), Math.sqrt(1-e) * Math.cos(E/2));
    const r = a * (1 - e * e) / (1 + e * Math.cos(nu));
    
    ship.rx = body.x + r * Math.cos(nu + omega);
    ship.ry = body.y + r * Math.sin(nu + omega);
}
```

### Maneuver visualization

When a ship has a maneuver plan, the client draws predicted future orbits:

```javascript
function drawManeuverPlan(ship) {
    const predictions = ship.orbit_predictions;  // from server
    
    for (const pred of predictions) {
        const color = pred === predictions[predictions.length-1] ? 0x00ff00 : 0xffff44;
        drawOrbitEllipse(pred.body_id, pred.elements, color, {dashed: true});
    }
    
    // Draw burn markers
    for (const burn of ship.maneuvers) {
        const pos = propagateOrbit(ship.orbit, burn.time_s);
        drawBurnMarker(pos, burn);
    }
}
```

### What gets deleted from frontend

- `computeTransitCurve()` — Bézier curve computation
- `buildTrajectoryArc()` — heliocentric polyline rendering
- `buildBodyCentricArc()` — SOI fake-ellipse rendering  
- `computeCurveWarp()` — endpoint warping for moving bodies
- `trackStartOrig/trackEndOrig` — snapshot tracking
- All `transit_from_x/y`, `transit_to_x/y` handling
- The entire `transitKey` cache invalidation system

Replaced by: `drawShipOrbit()` (render ellipse from elements) + `solveKepler()` (position on ellipse).

### API response changes

**Current** `/api/state` ship data:
```json
{
  "location_id": null,
  "from_location_id": "LEO",
  "to_location_id": "LMO",
  "departed_at": 946684800,
  "arrives_at": 964905000,
  "transit_from_x": 149598023,
  "transit_from_y": 0,
  "trajectory": [[149598023, 0], [149000000, 2000000], ...]
}
```

**New** `/api/state` ship data:
```json
{
  "location_id": null,
  "orbit": {
    "body_id": "sun",
    "a_km": 188000000,
    "e": 0.21,
    "omega_deg": 12.5,
    "M0_deg": 0.0,
    "epoch_s": 946685000,
    "direction": 1
  },
  "maneuvers": [
    {"time_s": 964905000, "prograde_m_s": -1100, "label": "Mars orbit insertion"}
  ],
  "orbit_predictions": [
    {"from_s": 946685000, "body_id": "sun", "elements": {...}},
    {"from_s": 964900000, "body_id": "mars", "elements": {...}}
  ]
}
```

---

## Transfer Flow (New)

### Player initiates transfer

```
1. Player selects ship docked at LEO
2. Clicks "Plan Transfer to LMO (Mars)"
3. Client calls GET /api/transfer_plan?ship_id=X&to=LMO
4. Server runs Lambert solver → returns burn sequence + predicted orbits
5. Client displays: 
   - Current orbit (blue ellipse at LEO)
   - Departure burn marker (prograde arrow)
   - Transfer orbit (yellow dashed ellipse, heliocentric)
   - SOI exit/entry markers
   - Arrival burn marker (retrograde arrow)
   - Final orbit (green dashed ellipse at Mars)
   - Fuel cost, total Δv, estimated arrival time
6. Player clicks "Execute Transfer"
7. Client calls POST /api/ships/{id}/execute_plan
8. Server:
   a. Validates fuel, TWR, etc. (same as today)
   b. Deducts fuel for ALL burns upfront (committed Δv)
   c. Sets maneuver_json = burn sequence (including SOI transitions)
   d. Sets location_id = NULL (undock)
   e. orbit_json stays as LEO circular (ship coasts until first burn)
9. As game time advances:
   a. settle_ship_events() fires burns at scheduled times
   b. orbit_json updates to reflect each burn's result  
   c. SOI transitions fire automatically
   d. After final burn: ship is in Mars parking orbit
   e. Auto-dock detects orbit match → location_id = "LMO"
```

### What if things go wrong?

In the current system, arrival is guaranteed. In the physics model:
- **Fuel runs out mid-sequence**: Remaining burns are cancelled. Ship stranded on whatever orbit it achieves. Player must plan a rescue or use remaining Δv to adjust.
- **Player cancels mid-transfer**: Unexecuted burns are removed from `maneuver_json`. Ship continues on current orbit. Player must plan new burns to reach a useful destination.
- **Missed docking window**: Ship flies past the destination. Player needs to plan a correction burn. (In practice, the server executes burns deterministically at exact times, so this only happens if the player manually cancels the capture burn.)

This adds **consequence** to transfers — fuel management matters, and stranding is a real risk.

### Fuel deduction strategy

Two options:

**Option A: Upfront deduction (recommended for v1)**
All fuel for the planned burn sequence is deducted when the plan is committed. If the plan is cancelled, unused fuel is refunded proportionally. Simple, predictable, no surprise fuel shortages.

**Option B: Per-burn deduction**
Fuel is deducted as each burn executes. More realistic, but if a load change (inventory transfer) increases mass between burns, the remaining fuel might be insufficient. Adds complexity.

Start with Option A. It matches the current behavior (fuel deducted at transfer start) and avoids mid-transfer fuel accounting.

---

## Implementation Phases

### Phase 1: Orbital state foundation (backend, no behavior change)

**New file: `orbit_service.py`** — pure math, no DB dependencies

```python
# 2D orbital mechanics utilities

def elements_to_state(elements, mu, game_time):
    """Orbital elements → (r_vec, v_vec) at game_time."""
    
def state_to_elements(r_vec, v_vec, mu, game_time):
    """(r_vec, v_vec) → orbital elements at game_time."""
    
def propagate_position(elements, mu, game_time):
    """Compute position on orbit at game_time (fast, for rendering)."""
    
def apply_burn(elements, mu, burn_time, prograde, radial):
    """Apply Δv burn → return new elements."""
    
def compute_soi_exit_time(elements, mu, soi_radius):
    """Find time when orbit crosses SOI boundary (or None if bound)."""
    
def orbit_matches_location(elements, location_body, location_radius, tolerance=0.01):
    """Check if orbit is close enough to a location (orbit node) to dock."""
    
def hohmann_burns(mu, r1, r2, departure_time):
    """Compute 2-burn Hohmann transfer between circular orbits."""
    
def lambert_burns(from_body, to_body, departure_time, tof, ...):
    """Compute departure + arrival burns from Lambert solution."""
```

**Migration: `0016_orbit_state`**

```python
def _migration_0016_orbit_state(conn):
    _safe_add_column(conn, "ships", "orbit_json", "TEXT")
    _safe_add_column(conn, "ships", "maneuver_json", "TEXT")
    _safe_add_column(conn, "ships", "orbit_body_id", "TEXT")
```

**Initialize existing ships**: All currently docked ships get `orbit_json` set to a circular orbit matching their station's orbital parameters. In-transit ships get `orbit_json` set to a transfer orbit computed from their `trajectory_json` or `from_location_id/to_location_id`.

**Tests**: `test_orbit_service.py` — validate elements↔state conversion, burn application, SOI detection against known cases.

### Phase 2: Wire up transfer execution

- Modify `POST /api/ships/{id}/transfer` to compute a burn sequence and store it in `maneuver_json` + set initial `orbit_json`
- New `settle_ship_events()` replaces `settle_arrivals()` — executes burns, SOI transitions, auto-docking
- Keep `settle_arrivals()` as fallback for ships still using the old model (migration bridge)
- Ship state API returns `orbit` + `maneuvers` + `orbit_predictions` in addition to legacy fields

### Phase 3: Frontend orbit rendering

- Add `solveKepler()` to client JS (or port from existing `celestial_config.py` Kepler solver)
- `drawShipOrbit()` renders ellipse from elements for free-flying ships
- `drawManeuverPlan()` shows predicted orbits and burn markers
- Ship position computed client-side from elements (no server polling needed for position updates)
- Remove Bézier/polyline/warp rendering code

### Phase 4: Transfer planner UI

- `GET /api/transfer_plan` endpoint returns burn sequence + orbit predictions
- Client shows departure burn, transfer orbit, arrival burn before execution
- Integration with existing porkchop plot for departure window selection
- "Cancel transfer" button removes remaining maneuvers, ship keeps current orbit

### Phase 5: Polish + edge cases

- Surface site landing/takeoff (vertical burns, not orbital)
- Lagrange point station-keeping
- Stranded ship rescue UX
- Orbit visualization polish (apoapsis/periapsis markers, orbital period display)
- Remove legacy columns and code paths

---

## Reuse Map

| Existing code | Reuse | Notes |
|---|---|---|
| `lambert.py` (entire file) | **100%** | Core solver unchanged |
| `transfer_planner.compute_interplanetary_leg()` | **90%** | Returns burns instead of flat dv/tof |
| `transfer_planner.compute_trajectory_points()` | **Retire** | Replaced by client-side Kepler rendering |
| `transfer_planner._kepler_propagate_state()` | **Move** to orbit_service, extend to return velocity |
| `celestial_config.compute_body_state()` | **100%** | Unchanged |
| `celestial_config._compute_keplerian_state_3d()` | **100%** | Reference for elements→state math |
| `celestial_config.get_body_mu()` | **100%** | Unchanged |
| `celestial_config.get_body_soi()` | **100%** | Used for SOI transition detection |
| `celestial_config.get_orbit_node_radius()` | **100%** | Used for station orbit matching |
| `fleet_router._compute_route_quote()` | **Refactor** | Returns burn sequence not flat quote |
| `fleet_router.settle_arrivals()` → `settle_ship_events()` | **Replace** | New event-driven system |
| `sim_service.game_now_s()` | **100%** | Unchanged |
| Porkchop endpoint | **100%** | Unchanged |
| Lambert cache | **100%** | Unchanged |
| `app.js` ship rendering | **Rewrite** | Ellipses from elements, not polylines |
| `app.js` computeTransitCurve/buildTrajectoryArc` | **Delete** | No longer needed |

---

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Kepler solver numerical issues for near-parabolic orbits | Medium | Clamp `e` to (0, 0.9999) for elliptic; switch to hyperbolic equations for `e ≥ 1.0` — well-known boundary handling |
| SOI transition timing precision | Low | Pre-compute crossing time analytically and schedule as event. Newton iteration on `r(t) = r_soi` converges fast. |
| Client-side Kepler solve performance | Low | Single solve is ~5µs in JS. Even 100 ships = 0.5ms per frame. |
| Existing ships in transit during migration | Medium | Keep `settle_arrivals()` as fallback. Old-model ships complete normally. New transfers use orbit model. |
| Player confusion: "where is my ship?" | Medium | Always show orbit ellipse + ship position dot. Add apoapsis/periapsis markers for context. |
| Stranded ships with no fuel | Medium | Add "emergency beacon" mechanic or admin rescue tool. Gameplay feature, not a bug. |
| Surface sites: no orbit to dock to | Low | Landing = special vertical burn. Ship state transitions from orbital to "landed" (`location_id` set, `orbit_json` stores suborbital state). |
| Hyperbolic orbits rendering past SOI | Low | Clip rendered hyperbola at SOI boundary. Only draw the relevant arc. |

---

## Summary

The core insight is that your Lambert solver, Kepler propagator, patched-conic SOI model, and body state vector infrastructure **already solve the hard physics problems**. The remaining work is:

1. **State representation** — ships store orbital elements instead of from/to/arrives_at (~2 weeks)
2. **Burn execution** — deterministic event-driven maneuver processing (~1 week) 
3. **Frontend rendering** — draw ellipses from elements instead of polylines (~2 weeks)
4. **Transfer planner adaptation** — return burn sequences instead of flat quotes (~1 week)
5. **Polish** — SOI transitions, auto-docking, edge cases (~2 weeks)

**Total: ~8 weeks**, with Phase 1 deployable independently for testing.

The biggest win: **eliminating all trajectory rendering hacks**. No more snapshots, no more warping, no more stale polylines. The orbit IS the ship's state, and rendering it is a direct geometric operation — an ellipse defined by 4 numbers.
