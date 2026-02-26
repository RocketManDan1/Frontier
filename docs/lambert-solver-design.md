# Lambert Solver — Design & Implementation Steps

## Overview

Replace the current Hohmann + phase-angle approximation for interplanetary transfers with an exact **Lambert solver** — given two positions and a time-of-flight, compute the required velocity vectors. Add a **porkchop plot** endpoint that scans departure × arrival date grids to find optimal transfer windows. Support multi-revolution solutions.

---

## Current Architecture (what exists)

### Data sources (scattered & duplicated)

| Data | Where it lives | Notes |
|---|---|---|
| Body Keplerian elements | `celestial_config.json` → `bodies[].position` | Full 3D: a, e, i, Ω, ω, M₀, epoch_jd, period_s |
| Body mass, radius, surface gravity | `celestial_config.json` → `bodies[]` | `mass_kg`, `radius_km`, `gravity_m_s2` |
| Body μ (GM), parking altitude | `fleet_router.py` → `_BODY_CONSTANTS` | **Duplicate** — hand-maintained dict |
| Body semi-major axis, period | `fleet_router.py` → `_BODY_ORBITS` | **Duplicate** — subset of Keplerian elements |
| Body mean anomaly at epoch | `fleet_router.py` → `_EPOCH_MEAN_ANOMALY_DEG` | **Duplicate** — already in config `M0_deg` |
| Location → parent body mapping | `fleet_router.py` → `_LOCATION_PARENT_BODY` | **Duplicate** — hand-maintained ~60-line dict |
| Orbit node radius from body center | `celestial_config.json` → `orbit_nodes[].radius_km` | Already exists for all orbital locations |
| Position computation (2D projected) | `celestial_config.py` → `_compute_keplerian_position()` | Full 3D rotation matrix, but only returns (x, y) |
| Hohmann Δv calculator | `main.py` → `_hohmann_interplanetary_dv_tof()` | Patched-conic with hyperbolic excess |
| Phase-angle penalty | `fleet_router.py` → `_body_phase_solution()` | Cosine approximation: 1.0–1.4× multiplier |
| Static transfer edges | `celestial_config.json` → `transfer_edges[]` | ~190 hand-authored edges with fixed dv/tof |
| Dijkstra graph routing | `fleet_router.py` → `_solve_dynamic_route()` | Walks transfer_edges, overrides interplanetary legs |

### Key limitation

The Hohmann calculator only uses the *current radial distance* of each body — it assumes circular, coplanar orbits. The phase-angle penalty is a rough estimate. Real transfers between eccentric, inclined orbits need a Lambert solution operating on full 3D state vectors.

---

## Architecture After Lambert

### Step 0: Add `mu_km3_s2` to `celestial_config.json` bodies

Currently μ values are scattered across `_BODY_CONSTANTS` and `planetary` dicts. Move them to the single source of truth.

**Change**: Add `"mu_km3_s2"` field to each body entry in `celestial_config.json`:

```json
{
  "id": "earth",
  "mass_kg": 5.9722e24,
  "radius_km": 6378.137,
  "mu_km3_s2": 398600.4418,
  ...
}
```

Bodies to add μ for:
| Body | μ (km³/s²) |
|---|---|
| sun | 1.32712440018e+11 |
| mercury | 22031.86855 |
| venus | 324858.592 |
| earth | 398600.4418 |
| moon | 4902.8 |
| mars | 42828.375214 |
| phobos | 7.11e-4 |
| deimos | 9.85e-5 |
| ceres | 62.63 |
| vesta | 17.29 |
| pallas | 13.61 |
| hygiea | 5.56 |
| jupiter | 126686534.0 |
| io | 5959.916 |
| europa | 3202.739 |
| ganymede | 9887.834 |
| callisto | 7179.289 |

**Also add** `"soi_radius_km"` to bodies that are Lambert transfer endpoints (planets + major asteroids). SOI = $a \cdot (m_{body}/m_{parent})^{2/5}$:

| Body | SOI (km) |
|---|---|
| mercury | 112,000 |
| venus | 616,000 |
| earth | 924,000 |
| mars | 577,000 |
| ceres | 77,000 |
| vesta | 37,000 |
| pallas | 37,000 |
| hygiea | 27,000 |
| jupiter | 48,200,000 |

---

### Step 1: Extend `celestial_config.py` — state vectors

The existing `_compute_keplerian_position()` already builds the full 3D rotation matrix but returns only `(x, y)`. Extend it to also return **velocity**.

**New function**: `compute_body_state_vector(body_id, game_time_s) → (r_vec3, v_vec3)`

The velocity in the orbital plane is:
$$\dot{x}_{orb} = -\frac{n \cdot a}{\sqrt{1 - e^2}} \cdot \frac{\sin E}{1 - e \cos E} \cdot a$$
$$\dot{y}_{orb} = \frac{n \cdot a \sqrt{1 - e^2}}{\sqrt{1 - e^2}} \cdot \frac{\cos E}{1 - e \cos E}$$

Actually, using the standard form:
$$\dot{x}_{orb} = \frac{-n \cdot a \cdot \sin(E)}{1 - e \cos(E)}$$
$$\dot{y}_{orb} = \frac{n \cdot a \sqrt{1 - e^2} \cdot \cos(E)}{1 - e \cos(E)}$$

where $n = 2\pi / P$ is the mean motion. Then rotate by the same 3D matrix [R] to get heliocentric velocity.

**Implementation**: Add a new function in `celestial_config.py`:

```python
def compute_body_state(
    bodies_by_id: Dict[str, Dict],    # loaded config bodies
    body_id: str,
    game_time_s: float,
) -> Tuple[Tuple[float,float,float], Tuple[float,float,float]]:
    """Returns ((x,y,z), (vx,vy,vz)) in km and km/s, heliocentric."""
```

This resolves the parent chain (e.g., for Moon: Moon→Earth→Sun) and accumulates positions and velocities.

---

### Step 2: `lambert.py` — the core solver

**New file** at repo root. Pure math, no database or config dependencies.

**Algorithm**: Izzo's method (2015) — the most robust and efficient single-function Lambert solver. Handles:
- Zero-revolution (short/long arc)
- Multi-revolution (N ≥ 1, short/long arc per revolution count)
- Hyperbolic transfers
- Near-180° transfer angles

**Interface**:
```python
def solve_lambert(
    r1: Tuple[float, float, float],   # departure position (km)
    r2: Tuple[float, float, float],   # arrival position (km)
    tof: float,                        # time of flight (seconds)
    mu: float,                         # gravitational parameter (km³/s²)
    max_revs: int = 0,                 # 0 = direct only, N = include up to N revolutions
    clockwise: bool = False,           # retrograde transfer
) -> List[Tuple[Tuple[float,float,float], Tuple[float,float,float]]]:
    """
    Returns list of (v1, v2) solution pairs.
    For max_revs=0: up to 1 solution.
    For max_revs=N: up to 1 + 2*N solutions (short & long path per rev).
    """
```

**Internal components**:
1. `_stumpff_c2(psi)` and `_stumpff_c3(psi)` — Stumpff functions for universal variable
2. `_householder_iteration(...)` — 3rd-order root finding
3. `_compute_tof(x, ...)` — time-of-flight as function of free parameter x
4. `_find_x_for_tof(tof, ...)` — invert the TOF function

**Validation targets** (from Vallado & Curtis textbooks):
- Earth→Mars Hohmann: should reproduce known Δv ≈ 5.6 km/s
- Earth→Venus: Δv ≈ 3.5 km/s
- 180° transfer: must not blow up
- Multi-rev: should find lower-energy solutions for patient transfers

---

### Step 3: `transfer_planner.py` — patched-conic integration

**New file** at repo root. Combines Lambert with SOI departure/arrival burns.

**Interplanetary transfer computation**:
```
1. Get heliocentric state of departure body at t_depart
2. Get heliocentric state of arrival body at t_arrive  
3. Solve Lambert(r1, r2, tof, μ_sun) → (v1_transfer, v2_transfer)
4. v_inf_depart = |v1_transfer - v1_body|  (hyperbolic excess at departure)
5. v_inf_arrive  = |v2_transfer - v2_body|  (hyperbolic excess at arrival)
6. Δv_depart = √(v_inf² + 2μ/r_park) - √(μ/r_park)  (from parking orbit)
7. Δv_arrive = √(v_inf² + 2μ/r_park) - √(μ/r_park)  (into parking orbit)
8. Total Δv = Δv_depart + Δv_arrive
```

**Local orbit changes** (same body):
- Use Hohmann formula: `_hohmann_orbit_change_dv_tof(mu, r1, r2)` (already exists in main.py)
- No Lambert needed for these — circular orbit assumption is fine for gameplay LEO↔GEO etc.

**Interface**:
```python
def compute_transfer(
    from_location_id: str,
    to_location_id: str,
    departure_time_s: float,
    config: Dict,              # loaded celestial_config
) -> TransferSolution:
    """Full patched-conic transfer with Lambert heliocentric arc."""

def compute_porkchop(
    from_body_id: str,
    to_body_id: str,
    departure_start_s: float,
    departure_end_s: float,
    arrival_start_s: float,
    arrival_end_s: float,
    grid_steps: int = 50,
    max_revs: int = 2,
    config: Dict = None,
) -> PorkchopResult:
    """Scan departure × arrival grid, return Δv matrix + best solutions."""
```

---

### Step 4: Consolidate duplicate data in `fleet_router.py`

Delete these hand-maintained dicts and read from config instead:

| Delete | Replace with |
|---|---|
| `_BODY_CONSTANTS` | `celestial_config.json` → `bodies[].mu_km3_s2` + `radius_km` |
| `_BODY_ORBITS` | `celestial_config.json` → `bodies[].position.a_km` + `period_s` |
| `_EPOCH_MEAN_ANOMALY_DEG` | `celestial_config.json` → `bodies[].position.M0_deg` |
| `_LOCATION_PARENT_BODY` | `celestial_config.json` → `orbit_nodes[].body_id` + `markers[].body_id` + `surface_sites[].body_id` |
| `_body_heliocentric_state()` | `celestial_config.compute_body_state()` |
| `_body_phase_solution()` | `transfer_planner.py` (Lambert replaces phase approx) |
| `_compute_interplanetary_leg_quote()` | `transfer_planner.compute_transfer()` |
| `_hohmann_interplanetary_dv_tof()` in main.py | `transfer_planner.py` (Lambert) |

**What stays**:
- `_solve_dynamic_route()` — Dijkstra graph walk, but legs call Lambert instead of Hohmann
- `_compute_leg_at_departure()` — still dispatches local vs interplanetary, but calls new solver
- Transfer edges in `celestial_config.json` — keep for **topology** (which locations connect), but dv/tof become dynamically computed rather than static values
- `_scan_departure_windows()` — replaced by the porkchop scanner

---

### Step 5: Porkchop plot API endpoint

**Endpoint**: `GET /api/transfer/porkchop`

**Parameters**:
| Param | Type | Description |
|---|---|---|
| `from_id` | str | Departure location ID |
| `to_id` | str | Destination location ID |
| `departure_start` | float | Earliest departure (game time seconds) |
| `departure_end` | float | Latest departure (game time seconds) |  
| `tof_min_days` | float | Minimum time of flight in days |
| `tof_max_days` | float | Maximum time of flight in days |
| `grid_size` | int | Grid resolution (default 40, max 100) |
| `max_revs` | int | Max revolution count (default 2) |

**Response**:
```json
{
  "from_body": "earth",
  "to_body": "mars",
  "departure_times": [1234567890, ...],
  "tof_values": [259200, ...],
  "dv_grid": [[5600.2, 5580.1, ...], ...],
  "best_solutions": [
    {
      "departure_time": 1234567890,
      "arrival_time": 1256789000,
      "tof_s": 22221110,
      "dv_m_s": 5560.3,
      "dv_depart_m_s": 3600.1,
      "dv_arrive_m_s": 1960.2,
      "v_inf_depart_km_s": 2.94,
      "v_inf_arrive_km_s": 2.65,
      "revolutions": 0,
      "type": "short"
    }
  ]
}
```

---

### Step 6: Update transfer edge semantics

**Before**: `transfer_edges` contain fixed `dv_m_s` and `tof_s`.

**After**: Edges define **topology only** + edge type:
- `"type": "local"` — same-body orbit change → Hohmann formula using orbit radii from config
- `"type": "interplanetary"` — cross-SOI transfer → Lambert solver at departure time
- `"type": "landing"` — surface site access → fixed dv/tof (already in `surface_sites[].landing_dv_m_s`)
- `"type": "lagrange"` — L-point transfer → low-energy approximation

The `dv_m_s` and `tof_s` in the config become **fallback/display estimates** only. At route-computation time, the actual values are calculated dynamically.

Future: auto-generate edges from topology rules rather than hand-authoring.

---

## Implementation Order

### Phase 1: Foundation (no behavior changes)
1. **Add `mu_km3_s2` and `soi_radius_km` to `celestial_config.json` bodies**
2. **Extend `celestial_config.py`** — add `compute_body_state()` returning 3D position + velocity
3. **Create `lambert.py`** — core solver with multi-revolution support
4. **Create `tests/test_lambert.py`** — validate against known transfers

### Phase 2: Integration (replaces Hohmann)  
5. **Create `transfer_planner.py`** — patched-conic wrapper combining Lambert + SOI burns
6. **Wire into `fleet_router.py`** — `_compute_leg_at_departure()` calls transfer_planner for interplanetary legs
7. **Delete duplicate dicts** — `_BODY_CONSTANTS`, `_BODY_ORBITS`, `_EPOCH_MEAN_ANOMALY_DEG`, `_LOCATION_PARENT_BODY` read from config

### Phase 3: Porkchop & polish
8. **Add porkchop endpoint** to fleet_router or new transfer_router
9. **Update frontend** transfer planner to show porkchop data
10. **Add edge type field** to transfer_edges for future auto-generation

### Phase 4: Performance & robustness
11. **Cache Lambert results** by departure-time bucket — avoid redundant solves for the same leg within a time window
12. **Battin's method fallback** for near-180° transfers where the universal-variable solver loses precision
13. **Multi-rev quality scoring** — rank multi-revolution solutions by a cost function (Δv + TOF penalty) for the porkchop planner
14. **Auto-generate edges from topology** — derive transfer_edges from body hierarchy + SOI nesting rules instead of hand-authoring JSON

### Phase 5: Accurate orbital trajectory rendering
15. **Kepler propagator in `transfer_planner.py`** — `compute_trajectory_points(r1, v1, mu, tof, n)` propagates the Lambert transfer orbit forward, returning sampled heliocentric (x, y) positions
16. **Store trajectory on ship departure** — compute the trajectory polyline for each interplanetary leg and persist as `trajectory_json` on the ship row (new DB column + migration)
17. **Serve trajectory in ship API** — include trajectory points in the fleet/ships response for in-transit ships
18. **Frontend renders real trajectories** — when trajectory data is present, use the polyline directly instead of `computeHohmannArc`; ship position sampled at `t = elapsed / tof` for accurate placement along the actual transfer orbit

---

## What does NOT change

- **Location system**: The hierarchy of groups → orbit_nodes → markers → surface_sites is fine. No rebuild needed.
- **Graph topology**: Dijkstra over transfer_edges stays. Lambert just computes better edge weights.
- **Frontend orbital map**: Still renders from the same location positions. 
- **Transfer execution**: Ships still depart/arrive via the same mechanism. Only the dv/tof numbers change.
- **Local orbit changes**: LEO↔GEO etc. still use Hohmann (because they're circular orbit changes around the same body — Lambert is overkill).
- **Surface landings**: Fixed dv/tof from config — not orbital mechanics problems.
- **Database schema**: No changes needed.

---

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Lambert solver numerical instability near 180° transfers | Izzo's method handles this; add fallback to Battin's method |
| Performance: Lambert per route query | Single solve is ~10μs; cache aggressively by departure-time bucket |
| Multi-rev solutions may confuse routing | Use 0-rev for Dijkstra routing; expose multi-rev only in porkchop/advanced planner |
| Breaking existing transfer costs | Phase 1 adds Lambert alongside; Phase 2 switches over with validation against Hohmann baseline |
| `celestial_config.json` getting larger | ~20 lines added (μ + SOI per body) — negligible |
