# Celestial Configuration & JPL Horizons Orbital Mechanics

Reference for the current orbital mechanics implementation and guide for adding new celestial objects. Includes an impact analysis for a potential future migration to fully dynamic JPL Horizons ephemeris data.

---

## Current Implementation

### Architecture Overview

The solar system is defined entirely in `config/celestial_config.json` and parsed by `celestial_config.py`. On startup `main.py`'s `ensure_solar_system_expansion()` reads this config, computes body positions for the current game time, and upserts all locations and transfer edges into SQLite. The frontend then requests positions dynamically via `/api/locations?dynamic=true` (handled by `location_router.py`), which re-evaluates Keplerian orbits at the current game clock time on every call.

There are **two independent rendering pipelines** that coexist:

| Pipeline | Source of truth | Parser / consumer | Used by |
|---|---|---|---|
| **Solar system map** | `config/celestial_config.json` | `celestial_config.py` → `locations` + `transfer_edges` tables | `app.js`, `location_router.py`, `fleet_router.py` |
| **Earth–Moon mini-map** | `db/schema.sql` + `db/seed.sql` | `sim.js` (PixiJS client-side) | `index.html` orbital canvas |

The mini-map uses its own `bodies`, `orbits`, and `derived_nodes` tables with `keplerian_2d` and `lagrange_cr3bp` models. These are separate from the main solar-system config and only cover the Earth–Moon system.

### `celestial_config.json` Structure

The config file has seven top-level arrays:

| Array | Purpose |
|---|---|
| `bodies` | Celestial bodies (planets, moons, asteroids). Each gets a position and optionally emits a group node in the location tree. |
| `groups` | UI grouping nodes (e.g., "Orbits", "Surface Sites") anchored to a body's position. |
| `orbit_nodes` | Gameplay locations at specific orbital altitudes around a body (LEO, GEO, LMO, etc.). These are the transferable leaf nodes. |
| `lagrange_systems` | Defines L1–L5 points between a primary and secondary body. Positions are computed geometrically from the two body positions. |
| `markers` | Non-interactive reference points positioned at a body's location (Sun marker, moon markers like Phobos/Deimos). |
| `transfer_edges` | Directed edges between leaf locations with delta-v (m/s) and time-of-flight (s) costs. |
| `surface_sites` | Landing sites on a body's surface with resource distributions and landing delta-v. Automatically generate bidirectional transfer edges to their parent orbit node. |

### Body Position Types

Bodies support three position types in their `position` object:

#### `"type": "fixed"`
Static coordinates. Used only for the Sun.
```json
{ "type": "fixed", "x_km": 0.0, "y_km": 0.0 }
```

#### `"type": "keplerian"`
Full Keplerian orbital elements propagated at the current game time. Used for planets and major asteroids. The parser solves Kepler's equation (Newton–Raphson iteration on eccentric anomaly) and applies the 3D→2D rotation matrix to produce (x, y) in km relative to the center body.

```json
{
  "type": "keplerian",
  "center_body_id": "sun",
  "a_km": 149598023.0,
  "e": 0.0167086,
  "i_deg": 0.00005,
  "Omega_deg": -11.26064,
  "omega_deg": 114.20783,
  "M0_deg": 357.529,
  "epoch_jd": 2451544.5,
  "period_s": 31558149.8
}
```

| Field | Description |
|---|---|
| `center_body_id` | Body this object orbits. Must be resolvable before this body. |
| `a_km` | Semi-major axis in km. |
| `e` | Eccentricity (0 ≤ e < 1). |
| `i_deg` | Inclination in degrees. |
| `Omega_deg` | Longitude of ascending node (Ω) in degrees. |
| `omega_deg` | Argument of periapsis (ω) in degrees. |
| `M0_deg` | Mean anomaly at epoch in degrees. |
| `epoch_jd` | Reference epoch as Julian Day (e.g., J2000.0 = 2451544.5). |
| `period_s` | Orbital period in seconds. |

#### `"type": "polar_from_body"`
Static polar offset from another body. Used for small bodies whose orbits are not modeled (Zoozve, Phobos, Deimos, Asteroid Belt centroid).

```json
{
  "type": "polar_from_body",
  "center_body_id": "venus",
  "radius_km": 1800000.0,
  "angle_deg": 37.0
}
```

### Position Resolution

`celestial_config.py`'s `_compute_body_positions()` resolves all body positions iteratively. Bodies whose `center_body_id` has already been resolved are computed next, so dependencies are handled automatically. Cycles or unresolvable references produce a `CelestialConfigError`.

All child locations (orbit nodes, Lagrange points, surface sites, markers, groups) compute their (x, y) relative to their parent body's resolved position.

### Dynamic Position Updates

Positions are **not** static snapshots. The `/api/locations` endpoint (in `location_router.py`) accepts `?dynamic=true` (the default) and optionally `?t=<game_time>` to recompute all positions from Keplerian elements at any game time. The frontend calls this on each page load.

For interplanetary transfer calculations, `fleet_router.py` caches position lookups in 5-minute game-time buckets (`_DYN_LOC_BUCKET_S = 300`) via `_dynamic_locations_by_id()` to keep the LRU cache effective at 48× game speed.

### Transfer Network

**Intra-body transfers** (e.g., LEO↔GEO, LMO↔HMO) use static delta-v values from the `transfer_edges` array. These costs are constant regardless of time.

**Interplanetary transfers** (e.g., LEO→LMO) are handled by a two-layer system:
1. **Static base graph**: `transfer_edges` entries provide baseline Hohmann delta-v and time-of-flight. At startup, `dijkstra_all_pairs()` builds a precomputed `transfer_matrix` using delta-v as the Dijkstra weight.
2. **Dynamic phase-angle adjustment**: At transfer-quote time, `fleet_router.py` computes the real-time heliocentric positions of the departure and arrival bodies, calculates the current phase angle vs. the optimal Hohmann phase angle, and applies a ±40% cosine multiplier (`_body_phase_solution()`). This makes interplanetary transfer costs time-dependent without a full Lambert solver.

The `_LOCATION_PARENT_BODY` dict in `fleet_router.py` maps every leaf location to its parent body for heliocentric state lookups. The `_BODY_CONSTANTS` dict provides gravitational parameters (`mu_km3_s2`), body radius, and parking orbit altitude for Hohmann calculations.

### Surface Sites

Surface sites are landing locations on a body's surface. Each site specifies:
- `body_id` + `angle_deg` → position computed from the body's radius and angular offset.
- `orbit_node_id` → the orbit location ships transfer to/from.
- `landing_dv_m_s` / `landing_tof_s` → bidirectional transfer edges are auto-generated.
- `resource_distribution` → fractional abundance of mineable resources at the site.

Surface site metadata (gravity, resources) is parsed by `build_surface_site_data()` and stored in the `surface_sites` and `surface_site_resources` DB tables.

---

## Adding New Celestial Objects

### Adding a New Planet or Major Body

**Example: adding Jupiter.**

#### 1. Add the body to `celestial_config.json` → `bodies`

Place it in `sort_order` sequence with the other planets. Get Keplerian elements from [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/) or [JPL Small-Body Database](https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html). Use J2000.0 epoch (`epoch_jd: 2451544.5`) for consistency with existing bodies.

```json
{
  "id": "jupiter",
  "name": "Jupiter",
  "group_id": "grp_jupiter",
  "symbol": "♃",
  "wikipedia_title": "Jupiter",
  "wikipedia_url": "https://en.wikipedia.org/wiki/Jupiter",
  "parent_group_id": "grp_sun",
  "sort_order": 40,
  "mass_kg": 1.8982e27,
  "gravity_m_s2": 24.79,
  "radius_km": 69911.0,
  "position": {
    "type": "keplerian",
    "center_body_id": "sun",
    "a_km": 778570000.0,
    "e": 0.0489,
    "i_deg": 1.303,
    "Omega_deg": 100.464,
    "omega_deg": 273.867,
    "M0_deg": 20.02,
    "epoch_jd": 2451544.5,
    "period_s": 374335700.0
  }
}
```

#### 2. Add UI groups to `groups`

```json
{ "id": "grp_jupiter_orbits", "name": "Orbits", "parent_id": "grp_jupiter", "sort_order": 10, "anchor_body_id": "jupiter" },
{ "id": "grp_jupiter_moons", "name": "Moons", "parent_id": "grp_jupiter", "sort_order": 20, "anchor_body_id": "jupiter" },
{ "id": "grp_jupiter_sites", "name": "Surface Sites", "parent_id": "grp_jupiter", "sort_order": 30, "anchor_body_id": "jupiter" }
```

#### 3. Add orbit nodes to `orbit_nodes`

```json
{ "id": "JUP_LO", "name": "Low Jupiter Orbit", "parent_id": "grp_jupiter_orbits", "sort_order": 10, "body_id": "jupiter", "radius_km": 75000.0, "angle_deg": 0.0 },
{ "id": "JUP_HO", "name": "High Jupiter Orbit", "parent_id": "grp_jupiter_orbits", "sort_order": 20, "body_id": "jupiter", "radius_km": 200000.0, "angle_deg": 0.0 }
```

`radius_km` is the orbital radius from the body center. Alternatively, use `altitude_km` for altitude above the surface (the parser adds `body.radius_km` automatically).

#### 4. Add transfer edges to `transfer_edges`

Add local orbit-change edges (always bidirectional):
```json
{ "from_id": "JUP_LO", "to_id": "JUP_HO", "dv_m_s": 3000, "tof_s": 14400 },
{ "from_id": "JUP_HO", "to_id": "JUP_LO", "dv_m_s": 3000, "tof_s": 14400 }
```

Add interplanetary connections to the existing network:
```json
{ "from_id": "LEO", "to_id": "JUP_LO", "dv_m_s": 9200, "tof_s": 78624000 },
{ "from_id": "JUP_LO", "to_id": "LEO", "dv_m_s": 9200, "tof_s": 78624000 }
```

#### 5. Update `fleet_router.py` constants

Add entries to `_LOCATION_PARENT_BODY`:
```python
"JUP_LO": "jupiter", "JUP_HO": "jupiter",
```

Add to `_BODY_CONSTANTS`:
```python
"jupiter": {"mu_km3_s2": 126686534.0, "radius_km": 69911.0, "parking_alt_km": 5000.0},
```

#### 6. Rebuild

```bash
sudo docker compose up -d --build frontier-dev
```

The server re-reads `celestial_config.json` and re-runs `ensure_solar_system_expansion()` on startup. New locations and edges appear immediately.

### Adding a Moon to an Existing Body

For a small moon that doesn't need its own Keplerian orbit (e.g., Phobos-style), use `polar_from_body`:

1. Add the body with `"emit_group": false` if it shouldn't appear as a collapsible group in the UI (or with a `group_id` if it should).
2. Add a marker in `markers` so it appears on the map.
3. Optionally add surface sites.

For a larger moon with orbital motion (like Luna), use `"type": "keplerian"` with `center_body_id` pointing to the parent planet.

### Adding a New Asteroid

Same pattern as adding a planet, but:
- Set `parent_group_id` to `"grp_asteroid_belt"` (or a new parent group).
- Use `"type": "keplerian"` with `center_body_id: "sun"`.
- Orbit node radii and landing delta-v will be much smaller.

### Adding Surface Sites to an Existing Body

Add entries to the `surface_sites` array:

```json
{
  "id": "JUPITER_EXAMPLE_SITE",
  "name": "Example Site",
  "body_id": "jupiter",
  "orbit_node_id": "JUP_LO",
  "parent_group_id": "grp_jupiter_sites",
  "sort_order": 10,
  "angle_deg": 45,
  "landing_dv_m_s": 5000,
  "landing_tof_s": 7200,
  "resource_distribution": {
    "water_ice": 0.30,
    "silicate_rock": 0.50,
    "iron_oxides": 0.20
  }
}
```

Bidirectional transfer edges to `orbit_node_id` are auto-generated. Remember to add any new surface site IDs to `_LOCATION_PARENT_BODY` in `fleet_router.py`.

### Adding a Lagrange System

To add L-points for a new primary/secondary pair:

```json
{
  "id": "sun_jupiter",
  "primary_body_id": "sun",
  "secondary_body_id": "jupiter",
  "parent_group_id": "grp_jupiter_lpoints",
  "points": [
    { "id": "SJ_L1", "name": "Sun–Jupiter L1", "sort_order": 10, "model": "line_primary_plus", "distance_km": 54200000.0 },
    { "id": "SJ_L2", "name": "Sun–Jupiter L2", "sort_order": 20, "model": "line_primary_plus", "distance_km": 54200000.0 },
    { "id": "SJ_L3", "name": "Sun–Jupiter L3", "sort_order": 30, "model": "line_primary_minus", "distance_km": 778570000.0 },
    { "id": "SJ_L4", "name": "Sun–Jupiter L4 (Greeks)", "sort_order": 40, "model": "triangle_plus" },
    { "id": "SJ_L5", "name": "Sun–Jupiter L5 (Trojans)", "sort_order": 50, "model": "triangle_minus" }
  ]
}
```

Lagrange point models:
- `line_primary_plus` — on the primary→secondary line at `distance_km` from primary (L1, L2).
- `line_primary_minus` — opposite side of primary from secondary (L3).
- `triangle_plus` / `triangle_minus` — equilateral triangle points (L4, L5). Distance is auto-derived from the primary–secondary separation.

### Checklist for Any New Object

1. **`celestial_config.json`** — body, groups, orbit nodes, transfer edges, surface sites, markers, lagrange systems as needed.
2. **`fleet_router.py`** — add entries to `_LOCATION_PARENT_BODY` for every new leaf location and to `_BODY_CONSTANTS` for any new body that participates in interplanetary Hohmann calculations.
3. **`config/celestial_config.schema.json`** — update if adding new field types (usually not needed).
4. **Rebuild the dev server** — `sudo docker compose up -d --build frontier-dev`.
5. **Test** — verify the new body appears on the map, orbit nodes show up in the location tree, and transfer quotes work from existing locations to the new ones.

### Where to Get Orbital Elements

- **Planets**: [JPL Horizons Web Interface](https://ssd.jpl.nasa.gov/horizons/app.html) — query the body, select "Orbital Elements", set epoch to J2000.0 (JD 2451544.5).
- **Asteroids**: [JPL Small-Body Database Lookup](https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html) — search by name or number, elements are shown on the summary page.
- **Moons**: JPL Horizons with the moon's ID and the parent planet as center body.
- **Period**: If not directly listed, compute from $T = 2\pi\sqrt{a^3 / \mu}$ where $a$ is in km and $\mu$ is the parent body's gravitational parameter in km³/s².

All existing bodies use J2000.0 epoch (`epoch_jd: 2451544.5`) for consistency.

---

## Future Migration Impact: Fully Dynamic JPL Horizons Tracking

Assessment of what would need to change if celestial bodies tracked real JPL Horizons ephemeris data with fully time-varying positions and delta-v, beyond the current Keplerian approximation.

### 1. Transfer Delta-V Becomes Fully Time-Dependent (HIGH — core game mechanic)

Currently, `transfer_edges` and `transfer_matrix` store **static** delta-v and time-of-flight values for the Dijkstra graph. Interplanetary legs get a dynamic phase-angle adjustment (±40%) at quote time, but the base Hohmann cost and the precomputed shortest-path graph remain fixed.

**What would break:**
- **Interplanetary delta-v is not constant** — a Hohmann transfer Earth→Mars costs ~4.3 km/s at an optimal window, but up to ~6+ km/s at bad alignment. The current phase-multiplier approximates this but is not a proper Lambert solver.
- The precomputed Dijkstra `transfer_matrix` can't fully accommodate time. Multi-hop interplanetary routes may have different optimal orderings depending on departure date.

**To fix:** Replace the static `transfer_matrix` with a per-request porkchop-plot or Lambert solver, or pre-compute a time-indexed lookup table that gets regenerated periodically.

### 2. Keplerian Approximation Drift (MEDIUM)

The current implementation propagates two-body Keplerian elements from a fixed J2000.0 epoch. Over decades of game time, perturbations (N-body effects, precession) cause these to drift from real ephemeris positions. This is acceptable for gameplay but would be incorrect for scientific accuracy.

**To fix:** Periodically update the elements in `celestial_config.json` to newer epochs, or switch to server-side SPICE kernel lookups.

### 3. Transfer In-Progress Ships (MEDIUM)

When a ship transfers between locations, it records `from_location_id`, `to_location_id`, `departed_at`, and `arrives_at`. The frontend interpolates the ship's position linearly between the from/to locations during transit.

**What would break:**
- If using true ephemeris, origin and destination locations move while the ship is in transit, making the interpolated path a moving target.
- A ship that departed from Mars when it was at $(x_1, y_1)$ will visually snap if Mars has moved to $(x_2, y_2)$ by arrival.

### 4. Two Separate Position Systems (MEDIUM — architectural)

The `db/seed.sql` Earth–Moon mini-map pipeline (`sim.js`) is independent from the `celestial_config.json` solar system map. These two systems are not unified — extending to a full ephemeris model would require reconciling them, probably by having the `app.js` map use the same Keplerian engine as `sim.js`.

### 5. Static Transfer Edge Weights (LOW)

Intra-body edges (LEO↔GEO, LMO↔HMO, etc.) are for local orbit-change maneuvers within one body's sphere of influence. These are **not time-dependent** and would be unaffected by any ephemeris migration.

### Migration Summary Table

| Component | Current state | Impact of full ephemeris |
|---|---|---|
| Body positions | Keplerian propagation at game time | Would need SPICE kernels or osculating elements |
| `transfer_matrix` / Dijkstra | Static graph, phase-adjusted at quote time | Must become fully time-dependent |
| `/api/locations` | Already dynamic (recomputes per request) | **No change needed** |
| `celestial_config.py` parser | Already has Kepler solver + `game_time_s` | Would need SPICE integration |
| Lagrange point positions | Recomputed per request from body positions | **No change needed** |
| In-transit ship interpolation | Linear between static endpoints | Needs trajectory-aware interpolation |
| `sim.js` Earth–Moon mini-map | Already handles `keplerian_2d` | Needs extension to solar system |
| Intra-body edges (LEO↔GEO etc.) | Static delta-v | **No change** — not time-dependent |

### Key Takeaway

The architecture has already moved significantly toward dynamic positions — bodies use Keplerian elements, positions are recomputed per-request, and interplanetary transfers use phase-angle adjustments. The remaining gap is replacing the static Dijkstra transfer matrix with a fully time-dependent solver and potentially upgrading from two-body Keplerian propagation to real ephemeris data for long-duration accuracy.
