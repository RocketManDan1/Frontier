# Adding Celestial Bodies — Complete Guide

How to add planets, asteroids, moons, Lagrange-hosted bodies, and surface sites to Frontier: Sol 2000.

---

## Overview

Every celestial body touches **three systems** that must stay in sync:

| System | Files | What it controls |
|---|---|---|
| **Config** | `config/celestial_config.json` | Bodies, groups, orbits, markers, edges, sites, Lagrange systems |
| **Backend** | `celestial_config.py`, `transfer_planner.py` | Position computation, routing, Lambert transfers |
| **Frontend** | `static/js/app.js` | Map projection, orbit ring animation, icon rendering |

The general workflow for any new body:

1. Add the **body** entry (physics + position)
2. Add **groups** (orbit, sites, moons sub-groups)
3. Add **orbit nodes** (parking orbits)
4. Add a **marker** (if the body is a moon or Lagrange-hosted asteroid)
5. Add **surface sites** with resource distributions
6. Add **transfer edges** (local + lagrange; interplanetary are auto-generated)
7. Register orbit nodes in the **frontend** (`ORBIT_IDS`, `orbitDefs`)
8. Add **frontend projection** rules if the body needs local orbit expansion
9. **Rebuild** and verify

---

## 1. Body Entry

Add to the `"bodies"` array in `celestial_config.json`.

### Required fields

| Field | Type | Description |
|---|---|---|
| `id` | string | Lowercase identifier, used everywhere as foreign key |
| `name` | string | Display name |
| `group_id` | string | Auto-emitted group node ID (convention: `grp_<id>`) |
| `parent_group_id` | string | Parent in the location tree (see [Group Hierarchy](#group-hierarchy)) |
| `sort_order` | int | Display ordering among siblings |
| `mass_kg` | float | Body mass |
| `mu_km3_s2` | float | Gravitational parameter (GM). Required for Lambert solver |
| `gravity_m_s2` | float | Surface gravity |
| `radius_km` | float | Mean radius. Used to position surface sites |
| `position` | object | How to compute the body's position (see [Position Types](#position-types)) |

### Optional fields

| Field | Type | Description |
|---|---|---|
| `symbol` | string | Unicode symbol for map display (e.g. `"♂"` for Mars) |
| `wikipedia_title` | string | Wikipedia article title |
| `wikipedia_url` | string | Wikipedia URL |
| `soi_radius_km` | float | Sphere of influence radius. Formula: $a \cdot (m_{body}/m_{parent})^{2/5}$ |
| `gateway_location_id` | string | Override which orbit node the auto-edge generator uses as the interplanetary gateway (default: lowest orbit) |

### Position types

**`keplerian`** — Standard orbital elements. Used for planets, asteroids, moons.

```json
"position": {
  "type": "keplerian",
  "center_body_id": "sun",
  "a_km": 227939200.0,
  "e": 0.0934,
  "i_deg": 1.85,
  "Omega_deg": 49.578,
  "omega_deg": 286.462,
  "M0_deg": 19.412,
  "epoch_jd": 2451544.5,
  "period_s": 59355072.0
}
```

| Param | Description |
|---|---|
| `center_body_id` | Body this orbits (`"sun"` for planets/asteroids, planet id for moons) |
| `a_km` | Semi-major axis in km |
| `e` | Eccentricity (0 = circular) |
| `i_deg` | Inclination in degrees |
| `Omega_deg` | Longitude of ascending node |
| `omega_deg` | Argument of periapsis |
| `M0_deg` | Mean anomaly at epoch |
| `epoch_jd` | Epoch as Julian Date (J2000 = `2451544.5`) |
| `period_s` | Orbital period in seconds |

> **Gameplay tip — simplified orbits**: For bodies that should visually cluster near a specific point (like Trojan asteroids near L4), use `e: 0, i_deg: 0, Omega_deg: 0, omega_deg: 0` so that `M0_deg` maps directly to the map angle. Set `period_s` equal to the host planet's period so they co-orbit. Spread `M0_deg` values ±5–15° around the target angle and vary `a_km` ±3% for visual scatter.

**`fixed`** — Static position. Rarely used for bodies.

```json
"position": {
  "type": "fixed",
  "x_km": 0,
  "y_km": 0
}
```

**`polar_from_body`** — Offset from another body at a fixed angle and radius.

```json
"position": {
  "type": "polar_from_body",
  "center_body_id": "sun",
  "radius_km": 414000000,
  "angle_deg": 45.0
}
```

### Examples by body type

<details>
<summary>Planet (Mars)</summary>

```json
{
  "id": "mars",
  "name": "Mars",
  "group_id": "grp_mars",
  "symbol": "♂",
  "wikipedia_title": "Mars",
  "wikipedia_url": "https://en.wikipedia.org/wiki/Mars",
  "parent_group_id": "grp_sun",
  "sort_order": 30,
  "mass_kg": 6.4171e+23,
  "mu_km3_s2": 42828.375214,
  "soi_radius_km": 577000.0,
  "gravity_m_s2": 3.71,
  "radius_km": 3389.5,
  "position": {
    "type": "keplerian",
    "center_body_id": "sun",
    "a_km": 227939200.0,
    "e": 0.0934,
    "i_deg": 1.85061,
    "Omega_deg": 49.57854,
    "omega_deg": 286.4623,
    "M0_deg": 19.412,
    "epoch_jd": 2451544.5,
    "period_s": 59355072.0
  }
}
```
</details>

<details>
<summary>Main-belt asteroid (Ceres)</summary>

```json
{
  "id": "ceres",
  "name": "Ceres",
  "group_id": "grp_ceres",
  "wikipedia_title": "Ceres (dwarf planet)",
  "wikipedia_url": "https://en.wikipedia.org/wiki/Ceres_(dwarf_planet)",
  "parent_group_id": "grp_asteroid_belt",
  "sort_order": 10,
  "mass_kg": 9.3835e+20,
  "mu_km3_s2": 62.63,
  "soi_radius_km": 77000.0,
  "gravity_m_s2": 0.28,
  "radius_km": 473.0,
  "position": {
    "type": "keplerian",
    "center_body_id": "sun",
    "a_km": 413767000.0,
    "e": 0.0758,
    "i_deg": 10.593,
    "Omega_deg": 80.305,
    "omega_deg": 73.597,
    "M0_deg": 95.989,
    "epoch_jd": 2451544.5,
    "period_s": 145166000.0
  }
}
```
</details>

<details>
<summary>Moon (Luna)</summary>

```json
{
  "id": "moon",
  "name": "Luna",
  "group_id": "grp_moon",
  "symbol": "☾",
  "wikipedia_title": "Moon",
  "wikipedia_url": "https://en.wikipedia.org/wiki/Moon",
  "parent_group_id": "grp_earth",
  "sort_order": 20,
  "mass_kg": 7.342e+22,
  "mu_km3_s2": 4902.8,
  "gravity_m_s2": 1.62,
  "radius_km": 1737.4,
  "position": {
    "type": "keplerian",
    "center_body_id": "earth",
    "a_km": 384399.0,
    "e": 0.0549,
    "i_deg": 5.145,
    "Omega_deg": 125.08,
    "omega_deg": 318.15,
    "M0_deg": 115.3654,
    "epoch_jd": 2451544.5,
    "period_s": 2360591.5
  }
}
```
</details>

<details>
<summary>Trojan asteroid (simplified orbit near L4)</summary>

```json
{
  "id": "hektor",
  "name": "624 Hektor",
  "group_id": "grp_hektor",
  "wikipedia_title": "624 Hektor",
  "wikipedia_url": "https://en.wikipedia.org/wiki/624_Hektor",
  "parent_group_id": "grp_sj_l4_greeks",
  "sort_order": 10,
  "mass_kg": 7.9e+18,
  "mu_km3_s2": 0.527,
  "soi_radius_km": 22000.0,
  "gravity_m_s2": 0.042,
  "radius_km": 112.5,
  "position": {
    "type": "keplerian",
    "center_body_id": "sun",
    "a_km": 790000000.0,
    "e": 0,
    "i_deg": 0,
    "Omega_deg": 0,
    "omega_deg": 0,
    "M0_deg": 86.4,
    "epoch_jd": 2451544.5,
    "period_s": 374335700.0
  }
}
```

Key: `period_s` matches Jupiter's exactly, `e/i/Omega/omega` are all zero, `M0_deg` is offset from L4's angle at J2000. `a_km` varies ±3% from Jupiter's for radial scatter.
</details>

---

## 2. Group Hierarchy

<a id="group-hierarchy"></a>

Every body auto-emits a group node from its `group_id` field. Sub-groups for orbits, moons, and surface sites go in the `"groups"` array.

### Parent group conventions

| Body type | `parent_group_id` on the body |
|---|---|
| Planet | `"grp_sun"` |
| Main-belt asteroid | `"grp_asteroid_belt"` |
| Moon of a planet | `"grp_<planet>"` (e.g. `"grp_earth"`, `"grp_jupiter"`) |
| Trojan asteroid at L4 | `"grp_sj_l4_greeks"` |
| Trojan asteroid at L5 | `"grp_sj_l5_trojans"` |

### Standard sub-groups

For a body with id `<body>`, add these to the `"groups"` array:

```json
{
  "id": "grp_<body>_orbits",
  "name": "Orbits",
  "parent_id": "grp_<body>",
  "sort_order": 10,
  "anchor_body_id": "<body>"
},
{
  "id": "grp_<body>_sites",
  "name": "Surface Sites",
  "parent_id": "grp_<body>",
  "sort_order": 30,
  "anchor_body_id": "<body>"
}
```

If the body has moons, also add:

```json
{
  "id": "grp_<body>_moons",
  "name": "Moons",
  "parent_id": "grp_<body>",
  "sort_order": 20,
  "anchor_body_id": "<body>"
}
```

---

## 3. Orbit Nodes

Add to the `"orbit_nodes"` array. These are the parking orbits players can travel to.

```json
{
  "id": "NEWBODY_LO",
  "name": "Low NewBody Orbit",
  "parent_id": "grp_newbody_orbits",
  "sort_order": 10,
  "body_id": "newbody",
  "radius_km": 500.0,
  "angle_deg": 0.0
}
```

| Field | Description |
|---|---|
| `id` | Uppercase. Convention: `<BODY>_LO` (low orbit), `<BODY>_HO` (high orbit) |
| `parent_id` | Must be `grp_<body>_orbits` |
| `body_id` | The body this orbit is around |
| `radius_km` | Orbital radius from body center (NOT altitude — includes body radius) |
| `angle_deg` | Starting angle on map (usually `0.0`) |

The **lowest orbit** automatically becomes the interplanetary gateway for auto-generated edges (unless overridden by `gateway_location_id` on the body).

---

## 4. Markers

Markers are non-orbit leaf locations displayed at the body's position. **Required for**:
- Moons (displayed in their parent planet's moon group)
- Trojan/Lagrange-hosted asteroids (displayed in the Greeks/Trojans group)

**Not needed for**: Planets and main-belt asteroids (their group node is sufficient).

```json
{
  "id": "NEWMOON",
  "name": "NewMoon",
  "parent_id": "grp_<planet>_moons",
  "sort_order": 10,
  "body_id": "newmoon"
}
```

For Trojan asteroids:
```json
{
  "id": "NEWASTEROID",
  "name": "624 NewAsteroid",
  "parent_id": "grp_sj_l4_greeks",
  "sort_order": 10,
  "body_id": "newasteroid"
}
```

> **Important**: The marker ID is used as the `center` in frontend `orbitDefs` for moons and small bodies. Orbit rings animate around the marker position.

---

## 5. Surface Sites

Add to the `"surface_sites"` array. Landing/ascent edges are **auto-generated** from `landing_dv_m_s` and `landing_tof_s`.

```json
{
  "id": "NEWBODY_SITENAME",
  "name": "Site Display Name",
  "body_id": "newbody",
  "orbit_node_id": "NEWBODY_LO",
  "parent_group_id": "grp_newbody_sites",
  "sort_order": 10,
  "angle_deg": 0,
  "landing_dv_m_s": 1870,
  "landing_tof_s": 3600,
  "resource_distribution": {
    "silicate_rock": 0.45,
    "iron_oxides": 0.06,
    "water_ice": 0.18
  }
}
```

| Field | Description |
|---|---|
| `orbit_node_id` | Which orbit node landing edges connect to. For bodies with markers, use the **marker ID** instead of the orbit node |
| `landing_dv_m_s` | Δv for landing/ascent (default 1870 if omitted). Low-gravity asteroids: 5–30 m/s |
| `landing_tof_s` | Time for landing/ascent in seconds (default 3600) |
| `angle_deg` | Angular position on the body surface |
| `resource_distribution` | Key-value pairs of resource_id → fraction (should sum to ~1.0). Resource IDs must match `items/Resources/*.json` |

The auto-generator creates bidirectional `"landing"` edges:
- `orbit_node_id ↔ site_id` (or `marker_id ↔ site_id`)

For asteroid bodies with markers, connect sites through the **marker** (not the orbit):
- `orbit_node → marker` (via a local edge)
- `marker → site` (via landing edge: set `orbit_node_id` to the marker ID)

---

## 6. Transfer Edges

Add to the `"transfer_edges"` array. Edges are **directional** — add both directions.

### Edge types

| Type | Use case | Δv/TOF |
|---|---|---|
| `"local"` | Same-body orbit changes (LEO ↔ GEO) | Hand-authored, fixed |
| `"interplanetary"` | Cross-body transfers (LEO ↔ LMO) | **Auto-generated** when `auto_interplanetary_edges: true`. Lambert solver overrides at query time |
| `"lagrange"` | L-point ↔ orbit transfers | Hand-authored, fixed |
| `"landing"` | Orbit ↔ surface site | **Auto-generated** from surface site config |

### What you need to hand-author

**Local edges** between orbit nodes of the same body:
```json
{ "from_id": "NEWBODY_LO", "to_id": "NEWBODY_HO", "dv_m_s": 500, "tof_s": 7200, "type": "local" },
{ "from_id": "NEWBODY_HO", "to_id": "NEWBODY_LO", "dv_m_s": 500, "tof_s": 7200, "type": "local" }
```

**Local edges** between orbit and marker (for moons/small bodies):
```json
{ "from_id": "NEWBODY_LO", "to_id": "NEWBODY", "dv_m_s": 50, "tof_s": 600, "type": "local" },
{ "from_id": "NEWBODY", "to_id": "NEWBODY_LO", "dv_m_s": 50, "tof_s": 600, "type": "local" }
```

**Lagrange edges** from L-point to nearby orbit:
```json
{ "from_id": "SJ_L4", "to_id": "NEWBODY_LO", "dv_m_s": 200, "tof_s": 86400, "type": "lagrange" },
{ "from_id": "NEWBODY_LO", "to_id": "SJ_L4", "dv_m_s": 200, "tof_s": 86400, "type": "lagrange" }
```

### What is auto-generated

With `"auto_interplanetary_edges": true` at the config root:
- Every heliocentric body (parent = sun) that has at least one orbit node gets **bidirectional interplanetary edges** to every other such body
- The gateway (endpoint) is the orbit node with the smallest `radius_km` (or `gateway_location_id` if set)
- Placeholder Hohmann Δv/TOF estimates are stored but the Lambert solver overrides them at query time
- Hand-authored interplanetary edges are **replaced** by auto-generated ones when this flag is on

---

## 7. Lagrange Systems

If you need L-points for a new primary–secondary pair, add to `"lagrange_systems"`:

```json
{
  "id": "newprimary_newsecondary",
  "primary_body_id": "newprimary",
  "secondary_body_id": "newsecondary",
  "parent_group_id": "grp_newprimary_lpoints",
  "points": [
    { "id": "NP_L1", "name": "L1", "sort_order": 10, "model": "line_primary_plus", "distance_km": 326400.0 },
    { "id": "NP_L2", "name": "L2", "sort_order": 20, "model": "line_primary_plus", "distance_km": 448900.0 },
    { "id": "NP_L3", "name": "L3", "sort_order": 30, "model": "line_primary_minus", "distance_km": 381700.0 },
    { "id": "NP_L4", "name": "L4", "sort_order": 40, "model": "triangle_plus" },
    { "id": "NP_L5", "name": "L5", "sort_order": 50, "model": "triangle_minus" }
  ]
}
```

| Model | Description |
|---|---|
| `line_primary_plus` | On the primary→secondary line, `distance_km` from primary |
| `line_primary_minus` | Opposite side of primary from secondary |
| `triangle_plus` | L4 — leading equilateral triangle point (+60°) |
| `triangle_minus` | L5 — trailing equilateral triangle point (−60°) |

> **Note**: Lagrange points map to the **primary** body for routing purposes. The `is_interplanetary()` function returns `False` when either body is `"sun"`, so Sun-X L-points are **not** treated as interplanetary destinations — they connect to the local system via lagrange edges instead.

---

## 8. Frontend Registration

### `static/js/app.js` — Three things to update

#### a) `ORBIT_IDS` Set

Add all new orbit node IDs so they render as **animated rings** instead of dots:

```javascript
const ORBIT_IDS = new Set([
  // ... existing ...
  "NEWBODY_LO", "NEWBODY_HO",   // ← add here
]);
```

**If you skip this**, orbit nodes render as asteroid-style hexagonal icons instead of rings.

#### b) `orbitDefs` Array

Add matching entries that define ring animation:

```javascript
const orbitDefs = [
  // ... existing ...
  { id: "NEWBODY_LO", center: "NEWBODY", period_s: 120 },   // ← for moons/small bodies
  { id: "NEWBODY_HO", center: "NEWBODY", period_s: 160 },
];
```

**Center ID convention**:
- **Planets**: use `"grp_<body>"` (e.g. `center: "grp_mars"`)
- **Moons and small bodies with markers**: use the **marker ID** (e.g. `center: "HEKTOR"`, `center: "IO"`)

`period_s` is the visual animation period in seconds (purely cosmetic).

#### c) Projection rules in `projectLocationsForMap`

This is the most complex part. The function determines how each location's raw `(x, y)` km coordinates are converted to screen positions.

**Default behavior**: All locations get `projectDeepPosition()` — heliocentric linear projection. This works for bodies orbiting the sun at unique heliocentric positions.

**When you need local orbit expansion**: If the body has orbit nodes or surface sites that are very close to it (compared to its distance from the Sun), those will overlap on the map. You need a local projection scale to spread them out.

Add a new `hasAncestor` check in the `if/else` chain:

```javascript
} else if (!l.is_group && hasAncestor(l.id, "grp_newbody_orbits", parentById) && newbody) {
  rx = newbodyRx + (Number(l.x) - Number(newbody.x)) * NEWBODY_ORBIT_SCALE;
  ry = newbodyRy + (Number(l.y) - Number(newbody.y)) * NEWBODY_ORBIT_SCALE;
}
```

You also need to:
1. Find the body's location: `const newbody = projectedLocations.find((l) => l.id === "grp_newbody");`
2. Project it: `const newbodyProjected = newbody ? projectDeepPosition(newbody.x, newbody.y) : { rx: 0, ry: 0 };`
3. Define the scale constant: `const NEWBODY_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;`
4. Add group snapping: `} else if (l.id === "grp_newbody") { rx = newbodyRx; ry = newbodyRy; }`

**When you DON'T need any frontend changes**:
- Bodies whose descendants all use heliocentric (`projectDeepPosition`) projection — e.g. Trojan asteroids near L4 that are already far from Jupiter. Their projection is handled by the `grp_sj_l4_greeks` ancestor check.

**Special case — bodies under an existing L-point group**: Descendants of `grp_sj_l4_greeks` or `grp_sj_l5_trojans` are automatically projected heliocentrically (not relative to Jupiter). If you add more asteroids to these groups, no frontend changes are needed.

#### d) `ASTEROID_HINTS` Array (optional)

If the new body should render with the hexagonal asteroid icon style, add a hint:

```javascript
const ASTEROID_HINTS = ["asteroid", "ceres", "vesta", /* ... */ "newbody"];
```

The `isAsteroidLocation()` function walks up the parent chain checking if any ancestor's ID or name contains one of these hints. If the body's group name already contains an existing hint (like "trojan" or "greek"), you don't need to add anything.

---

## 9. Routing — How It Works

Understanding the routing system helps debug "No transfer data" errors.

### Location → body mapping

Every location (orbit node, marker, surface site, L-point) maps to a `body_id`. This mapping is built automatically from the config:
- `orbit_nodes[].body_id`
- `markers[].body_id`
- `surface_sites[].body_id`
- `lagrange_systems[].points[].id` → maps to the system's `primary_body_id`

### Interplanetary detection

`is_interplanetary(from, to)` resolves both locations to their **heliocentric parent body** by walking up the `center_body_id` chain until it finds a body whose parent is `"sun"`. Returns `True` if the two heliocentric bodies differ.

Example chains:
- `IO_LO → io → jupiter` (heliocentric = jupiter)
- `LEO → earth` (heliocentric = earth)
- `HEKTOR_LO → hektor` (heliocentric = hektor)
- `SJ_L4 → sun` → **filtered out** (`is_interplanetary` returns `False` when either body is `"sun"`)

### Route resolution order

1. **Direct edge** — is there a `transfer_edges` row from A to B?
2. **Interplanetary** — if `is_interplanetary(A, B)`, find gateway pair and compute Lambert
3. **Local multi-hop** — Dijkstra over non-interplanetary edges

### Common "No transfer data" causes

| Symptom | Cause | Fix |
|---|---|---|
| No transfer data for any destination | Body has no orbit nodes → no gateway → no auto edges | Add at least one orbit node |
| No transfer data to specific body | Missing local edges from orbit to destination | Add local/lagrange edges |
| Can reach orbit but not surface | Missing `orbit_node_id` or it points to wrong node | Check surface site config |
| Transfers work to planet's moons but not to the body | The body's orbit node maps to the body, but moon locations map to the moon | Ensure the orbit_node `body_id` is correct |

---

## Complete Checklist

### Adding a new planet

- [ ] Body entry with keplerian position, `parent_group_id: "grp_sun"`
- [ ] Groups: `grp_<id>_orbits`, `grp_<id>_sites`, optionally `grp_<id>_moons`
- [ ] Orbit nodes (at least one: `<ID>_LO`)
- [ ] Surface sites with resources
- [ ] Local edges between orbits
- [ ] Frontend: `ORBIT_IDS`, `orbitDefs`, projection scale + `hasAncestor` rule, group snap
- [ ] Interplanetary edges: auto-generated ✓

### Adding a new main-belt asteroid

- [ ] Body entry with keplerian position, `parent_group_id: "grp_asteroid_belt"`
- [ ] Groups: `grp_<id>_orbits`, `grp_<id>_sites`
- [ ] Orbit node(s)
- [ ] Surface sites with resources
- [ ] Local edges (orbit ↔ marker if applicable)
- [ ] Frontend: `ORBIT_IDS`, `orbitDefs`, projection if needed, `ASTEROID_HINTS` if icon should be asteroid-style
- [ ] Interplanetary edges: auto-generated ✓

### Adding a new moon

- [ ] Body entry with keplerian position, `center_body_id: "<planet>"`, `parent_group_id: "grp_<planet>"`
- [ ] Groups: `grp_<id>_orbits`, `grp_<id>_sites`
- [ ] **Marker** in `grp_<planet>_moons`
- [ ] Orbit node(s)
- [ ] Surface sites (use marker ID as `orbit_node_id` if connecting through marker)
- [ ] Local edges: orbit ↔ marker
- [ ] Lagrange edges from parent planet's orbit → moon orbit (if desired)
- [ ] Frontend: `ORBIT_IDS`, `orbitDefs` (center = marker ID), projection scale
- [ ] Interplanetary edges: NOT needed — moons are reached via local edges from the planet's gateway

### Adding a Trojan asteroid (L4 Greeks / L5 Trojans)

- [ ] Body entry with simplified keplerian orbit locked to Jupiter's period
- [ ] `parent_group_id: "grp_sj_l4_greeks"` (L4) or `"grp_sj_l5_trojans"` (L5)
- [ ] Groups: `grp_<id>_orbits`, `grp_<id>_sites`
- [ ] Orbit node (one `<ID>_LO`)
- [ ] **Marker** in `grp_sj_l4_greeks` or `grp_sj_l5_trojans`
- [ ] Surface sites with resources
- [ ] Local edges: orbit ↔ marker (small Δv)
- [ ] Lagrange edges: `SJ_L4 ↔ <ID>_LO` (or `SJ_L5 ↔ ...`)
- [ ] Frontend: `ORBIT_IDS`, `orbitDefs` (center = marker ID)
- [ ] **No projection changes needed** — `grp_sj_l4_greeks` descendants already use heliocentric projection
- [ ] **No `ASTEROID_HINTS` needed** — parent groups already contain "greek" / "trojan"
- [ ] Interplanetary edges: auto-generated ✓

### Adding a Lagrange system

- [ ] Lagrange system entry with primary/secondary bodies and point definitions
- [ ] Group for L-points: `grp_<primary>_lpoints` (must exist in groups array)
- [ ] Lagrange edges connecting L-points to nearby orbits
- [ ] Frontend projection: L4/L5 (heliocentric) vs L1/L2/L3 (local to secondary body)

---

## Build & Verify

```bash
# Validate JSON
python3 -c "import json; json.load(open('config/celestial_config.json'))"

# Test position computation
python3 -c "
import celestial_config
cfg = celestial_config.load_celestial_config()
locs, edges = celestial_config.build_locations_and_edges(cfg)
print(f'Locations: {len(locs)}, Edges: {len(edges)}')
# Check your new body appears
for l in locs:
    if 'newbody' in l[0].lower():
        print(f'  {l[0]:25s} x={l[5]:15.1f} y={l[6]:15.1f}')
"

# Test auto-generated edges
python3 -c "
import celestial_config
cfg = celestial_config.load_celestial_config()
_, edges = celestial_config.build_locations_and_edges(cfg)
for e in edges:
    if 'NEWBODY' in e[0] or 'NEWBODY' in e[1]:
        print(f'  {e[0]:20s} -> {e[1]:20s}  dv={e[2]:8.0f} m/s  type={e[4]}')
"

# Rebuild
sudo docker compose up -d --build frontier-dev
```

After rebuilding:
1. Open the map and verify the body appears near its expected position
2. Open Transfer Planner from a ship and verify the new body appears in the destination tree
3. Try computing a transfer — it should show Lambert-computed Δv and TOF
4. Zoom in and verify orbit rings animate correctly

---

## Reference: Keplerian Elements at J2000

Epoch JD `2451544.5` corresponds to 2000-01-01 12:00 UTC (J2000.0). All `M0_deg` values in the config are relative to this epoch. To add a new body, obtain its orbital elements at J2000 from [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/) or [JPL Small-Body Database](https://ssd.jpl.nasa.gov/sbdb.cgi).

Key conversions:
- Period: $T = 2\pi\sqrt{a^3/\mu_{parent}}$ (seconds)
- SOI radius: $r_{SOI} = a \cdot (m_{body}/m_{parent})^{2/5}$ (km)
- μ from mass: $\mu = G \cdot m$ where $G = 6.674 \times 10^{-20}$ km³/(kg·s²)
