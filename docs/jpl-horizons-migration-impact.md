# Impact Analysis: Dynamic JPL Horizons Orbital Mechanics

Assessment of what would break if celestial bodies tracked real JPL Horizons Keplerian elements with time-varying positions and delta-v.

---

## 1. Transfer Delta-V Becomes Time-Dependent (HIGH — core game mechanic)

Currently, `transfer_edges` and `transfer_matrix` store **static** delta-v and time-of-flight values, computed once at startup and cached in SQLite. Dijkstra's all-pairs shortest path (`main.py`, `dijkstra_all_pairs()`) runs over this fixed graph.

**What breaks:**
- **Interplanetary delta-v is not constant** — a Hohmann transfer Earth→Mars costs ~4.3 km/s at an optimal window, but up to ~6+ km/s at bad alignment. The current `transfer_matrix` returns the same number regardless of when you depart.
- The pre-computed Dijkstra table can't accommodate time. Every call to `/api/transfer_quote`, `/api/transfer_quote_advanced`, and `/api/ships/{ship_id}/transfer` in `fleet_router.py` pulls from this static table.
- The existing **partial workaround** in `fleet_router.py` (`_phase_angle_multiplier`) already models this as a ±40% cosine penalty on top of the static base Hohmann cost — but it's an approximation, not a proper Lambert solver. This would need to be replaced or significantly upgraded.

**To fix:** Replace the static `transfer_matrix` with a per-request porkchop-plot or Lambert solver, or pre-compute a time-indexed lookup table that gets regenerated periodically.

---

## 2. Location Positions in DB Become Stale (HIGH — map rendering)

The `locations` table stores static `(x, y)` coordinates in km, written once at startup by `ensure_solar_system_expansion()`. The frontend (`app.js` `syncLocationsOnce()`) reads these via `/api/locations` and uses `l.x`/`l.y` to project planet group positions on the map.

**What breaks:**
- Planet group positions (`grp_mercury`, `grp_venus`, `grp_earth`, `grp_mars`, etc.) are stored as a single (x,y) snapshot. With real orbits these change continuously.
- The entire location tree — orbit nodes, Lagrange points, surface sites — is positioned **relative to** its parent body's static (x,y). If the body moves, all descendants must move.
- The frontend does a single `syncLocationsOnce()` on load; it assumes positions don't change. It would need to either re-fetch positions every tick, or compute Keplerian positions client-side (like `sim.js` already does for the Earth-Moon mini-simulation).

**To fix:** Either (a) add a `/api/locations?t=<game_time>` parameter that recomputes positions on the fly, or (b) move position computation to the frontend JS using orbital elements, similar to how `sim.js` already handles `keplerian_2d` for the Moon.

---

## 3. Lagrange Point Positions Change (MEDIUM)

Lagrange points (L1–L5) are computed in `celestial_config.py` using the vector from primary→secondary body. If both Earth and Moon move in real Keplerian orbits, the L points must be recomputed each frame. The frontend's `sim.js` already has a `lagrange_cr3bp` derived-node model that does this correctly for the Earth-Moon case — but only for the `seed.sql` bodies, not the `celestial_config.json` solar system.

**What breaks:** The L-point positions stored in the `locations` table become wrong as the Moon orbits. The `celestial_config.py` parser only runs once.

---

## 4. Transfer In-Progress Ships (MEDIUM)

When a ship transfers between locations, it records `from_location_id`, `to_location_id`, `departed_at`, and `arrives_at` in the `ships` table. The frontend interpolates the ship's position linearly between the from/to locations during transit.

**What breaks:**
- If origin and destination locations move while the ship is in transit, the interpolated path is now a moving target.
- A ship that departed from Mars when it was at (x₁, y₁) will visually snap if Mars has moved to (x₂, y₂) by arrival.
- The `arrives_at` time was computed from a static `tof_s` — but real transfer orbits have trajectory-specific arrival times.

---

## 5. Two Separate Position Systems (MEDIUM — architectural)

There are **two independent rendering pipelines**:
1. **`db/schema.sql` + `db/seed.sql`** → `bodies` + `orbits` tables → consumed by `sim.js` → renders in the PixiJS orbital mini-map (index.html). This already supports `keplerian_2d`.
2. **`celestial_config.json`** → `locations` table → consumed by `app.js` → renders the full solar system map. This is entirely static.

**What breaks:** These two systems are not unified. Adding Keplerian propagation to `celestial_config.json` / the `locations` table doesn't automatically update the seed.sql system and vice versa. You'd need to reconcile them — probably by having the `app.js` map use the same Keplerian engine as `sim.js`.

---

## 6. `celestial_config.json` Schema (LOW)

Currently only supports `"type": "fixed"` and `"type": "polar_from_body"` for body positions. You'd need a new type like `"type": "keplerian"` with fields for: `a_km`, `e`, `i_deg`, `Omega_deg`, `omega_deg`, `M0_deg`, `epoch_jd`, `period_s`.

The `celestial_config.py` parser's `_compute_body_positions()` would need a `game_time_s` parameter and a Kepler equation solver (eccentric anomaly iteration).

---

## 7. Static Transfer Edge Weights (LOW)

The hand-authored edges in `celestial_config.json` `transfer_edges` (e.g., LEO→GEO = 3856 m/s) are for **local** orbit-change maneuvers within one body's sphere of influence. These are not time-dependent (LEO→GEO costs the same regardless of planetary alignment) and would be **unaffected**.

Only interplanetary edges and their Hohmann calculations in `main.py` `ensure_solar_system_expansion()` are impacted.

---

## Summary Table

| Component | Impact | Effort |
|---|---|---|
| `transfer_matrix` / Dijkstra solver | Must become time-dependent | **High** |
| `/api/transfer_quote` + `/api/ships/{id}/transfer` | Must accept departure time for interplanetary legs | **High** |
| `locations` table (x, y) | Must be computed per-tick or served dynamically | **High** |
| `app.js` map rendering | Must handle moving body positions | **High** |
| `celestial_config.json` schema | New `keplerian` position type needed | Medium |
| `celestial_config.py` parser | Needs Kepler solver + time parameter | Medium |
| Lagrange point positions | Need per-tick recomputation | Medium |
| In-transit ship interpolation | Needs trajectory-aware interpolation | Medium |
| `sim.js` Earth-Moon mini-map | Already handles Keplerian — needs extension to solar system | Low |
| Intra-body edges (LEO↔GEO etc.) | **No change** — not time-dependent | None |

## Key Takeaway

The architecture fundamentally assumes **positions and transfer costs are static** — computed once at startup and stored in SQLite. Moving to real ephemeris-style tracking requires rethinking this into either a server-side compute-on-request model or pushing the orbital propagation to the client.
