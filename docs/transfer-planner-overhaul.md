# Transfer Planner & Orbit Visualization Overhaul

> Design document for overhauling the transfer planner UI, orbital path visualization,
> burn marker interaction, and intercept ghost projection systems.

## Table of Contents

1. [Overview & Goals](#1-overview--goals)
2. [Auto-Chain Mission System](#2-auto-chain-mission-system)
3. [Unified Transfer Planner UX](#3-unified-transfer-planner-ux)
4. [Interplanetary Advanced View (Porkchop)](#4-interplanetary-advanced-view-porkchop)
5. [Orbital Path Visualization](#5-orbital-path-visualization)
6. [Burn Marker Interaction](#6-burn-marker-interaction)
7. [Ghost Projection (KSP-Style Intercept)](#7-ghost-projection-ksp-style-intercept)
8. [Backend Changes](#8-backend-changes)
9. [Data Contracts](#9-data-contracts)
10. [Implementation Plan](#10-implementation-plan)

---

## 1. Overview & Goals

### Inspiration
- **KSP Transfer Planner**: Porkchop plots with adjustable departure/arrival windows, maneuver node detail
- **KSP Maneuver Execution**: Visible burn chain along orbit, prograde/retrograde burn indicators
- **Eve Online / High Frontier**: Data-dense readout panels, faint-to-highlighted orbit selection

### Current State

The transfer planner (`app.js` lines 6297–7320) already has:
- Hierarchical destination tree with zone/group accordion
- Advanced transfer quote with orbital alignment data
- Porkchop plot canvas (interplanetary only) with TOF slider
- Physics-based burn planning via `orbit_bridge.compute_transfer_burn_plan()`
- Orbit rendering with prediction segments (departure/transfer/arrival colored arcs)
- Burn diamond markers on the map (orange=future, grey=past)

**What's missing or needs overhaul:**

| Gap | Description |
|---|---|
| Non-interplanetary mission planning | No burn preview, no optimization window — just a flat Δv quote and "go" |
| Departure date picker hidden | The `<div>` exists but has `display:none` |
| Porkchop is read-only | Clicking a cell doesn't select that departure+TOF for the actual transfer |
| No arrival slider | TOF slider alone — no independent departure and arrival date control |
| Orbit opacity | All orbit paths drawn at fixed alpha regardless of selection state |
| Burn chain not visible | Only the current prediction segment is highlighted; no connected chain |
| No burn hover info | Burn diamonds exist but have no tooltip/interaction |
| No ghost projection | No way to see where the destination body will be at a future time along the path |

### Design Principles

1. **Management-first UX** — player picks a destination, game auto-plans the full mission. Real patched conics under the hood, but the player never manually solves orbital mechanics.
2. **Every transfer uses the same flow** — local, SOI, interplanetary, and multi-leg missions all present identically: auto-optimal window → burn plan card → confirm.
3. **Porkchop is an advanced/optional view** — interplanetary transfers auto-select the best window. The porkchop plot is a collapsible section for players who want to pick an alternative.
4. **Selected ships tell their whole orbital story** — faint orbits for background, highlighted burn chain for selected.
5. **Burns are inspectable** — hover any burn marker to see what it does.
6. **Intercept is visualizable** — mouse along the transfer arc shows the destination's future position.

---

## 2. Auto-Chain Mission System

### 2.1 Core Concept

The player says "Lunar Surface → Mars" and the game auto-resolves the full physical
chain of patched-conic legs. No manual multi-step planning required.

The game walks the **celestial body hierarchy** to build a chain of legs, each of which
is a real patched-conic segment (local Hohmann, SOI transfer, or interplanetary Lambert).
The ship executes them sequentially. The player sees one unified burn plan and clicks
one "Execute Mission" button.

### 2.2 Hierarchy Walker

Given a source location and destination location, the hierarchy walker produces an
ordered list of transfer legs by navigating the body tree:

```
Body Hierarchy:
  Sun
  ├── Earth
  │   ├── LEO, GEO, HEO     (orbits)
  │   ├── Moon
  │   │   ├── LLO            (orbit)
  │   │   └── Lunar Surface  (surface)
  │   └── L1, L2, L4, L5    (Lagrange points)
  ├── Mars
  │   ├── LMO               (orbit)
  │   └── Mars Surface       (surface)
  └── Jupiter
      ├── JOI               (orbit)
      ├── Io, Europa, ...   (moons)
      └── ...
```

**Algorithm:** Find the lowest common ancestor (LCA) body of source and destination,
then build legs going "up" from source to the LCA transfer frame, across, and "down"
to destination.

#### Example: Lunar Surface → LMO (Mars)

```
Source: Lunar Surface  →  body: Moon
Dest:   LMO            →  body: Mars
LCA:    Sun

Legs:
1. Lunar Surface → LLO     (local Hohmann, body=Moon)    — ascent to orbit
2. LLO → LEO               (SOI transfer, parent=Earth)  — escape Moon
3. LEO → LMO               (interplanetary Lambert, Sun)  — Earth→Mars
```

#### Example: LEO → LLO

```
Source: LEO  →  body: Earth
Dest:   LLO  →  body: Moon
LCA:    Earth

Legs:
1. LEO → LLO               (SOI transfer, parent=Earth)  — single leg
```

#### Example: LEO → GEO

```
Source: LEO  →  body: Earth
Dest:   GEO  →  body: Earth
LCA:    Earth

Legs:
1. LEO → GEO               (local Hohmann, body=Earth)   — single leg
```

#### Example: LLO → Europa Orbit

```
Source: LLO          →  body: Moon
Dest:   Europa Orbit →  body: Europa
LCA:    Sun

Legs:
1. LLO → LEO               (SOI transfer, parent=Earth)  — escape Moon
2. LEO → JOI                (interplanetary Lambert, Sun)  — Earth→Jupiter
3. JOI → Europa Orbit       (SOI transfer, parent=Jupiter) — descend to Europa
```

### 2.3 Leg Types and Resolution

Each leg in the chain uses one of the three existing patched-conic planners:

| Leg Type | When Used | Planner |
|---|---|---|
| **Local Hohmann** | Source and dest orbit the same body | `_plan_local_transfer()` |
| **SOI Transfer** | Source orbits a sub-body (moon) or dest does, common parent | `_plan_soi_transfer()` |
| **Interplanetary** | Source and dest orbit different heliocentric bodies | `_plan_interplanetary_transfer()` |

**Gateway resolution:** When ascending from a moon to interplanetary space, the system
auto-selects the gateway orbit.
- Ascending from Moon → uses LEO (Earth's standard low orbit) as the departure gateway
- Descending to a moon → uses the planet's standard low orbit as the arrival gateway
- Surface → orbit: uses the body's standard low orbit (e.g., Lunar Surface → LLO)

Gateways are defined in the celestial config per body: `"gateway_orbit": "leo"` etc.

### 2.4 Chained Burn Plan

The hierarchy walker produces a unified burn plan that combines all legs:

```
┌───────────────────────────────────────────────────────────┐
│ Mission: Lunar Surface → LMO (Mars)                      │
│                                                           │
│ ▸ Mission Window                                         │
│   Departure: 2157-06-14 04:00 (in 3d 12h)               │
│   Arrival:   2158-01-20 08:00 (~220d)                    │
│                                                           │
│ ▸ Mission Plan                                  3 legs   │
│   ┌──────────────────────────────────────────────────┐   │
│   │ Leg 1: Lunar Surface → LLO         1,680 m/s    │   │
│   │   1. Launch burn    +1,680 m/s prograde  T+0     │   │
│   │      → LLO (100 km circular)        ▸ 8 min     │   │
│   ├──────────────────────────────────────────────────┤   │
│   │ Leg 2: LLO → LEO (escape Moon)     3,964 m/s    │   │
│   │   2. TLI Burn       +3,132 m/s prograde  T+12m  │   │
│   │      → Transfer orbit (Ap: 384,399 km)           │   │
│   │   3. LEO Insertion    -832 m/s retro     T+2d18h │   │
│   │      → LEO (400 km circular)        ▸ 2d 18h    │   │
│   ├──────────────────────────────────────────────────┤   │
│   │ Leg 3: LEO → LMO (Earth→Mars)      4,520 m/s    │   │
│   │   4. TMI Burn       +3,310 m/s prograde  T+3d    │   │
│   │      → Heliocentric transfer orbit               │   │
│   │   5. MOI Burn       -1,210 m/s retro     T+204d  │   │
│   │      → LMO (250 km circular)        ▸ 201d      │   │
│   └──────────────────────────────────────────────────┘   │
│                                                           │
│ Total: 5 burns • 10,164 m/s • ~204 days                  │
│ Fuel required: 14,200 kg                                  │
│ Ship Δv remaining: 1,203 m/s                              │
│                                                           │
│         [ Cancel ]            [ Execute Mission ]         │
└───────────────────────────────────────────────────────────┘
```

### 2.5 Timing and Sequencing

The hierarchy walker plans legs sequentially, using the arrival time of each leg
as the departure time for the next:

1. **Interplanetary legs drive timing.** If the chain includes an interplanetary leg,
   the optimal departure window for that leg (from the porkchop/Lambert scan) determines
   the overall mission timing. Earlier legs are planned backwards to arrive at the
   interplanetary departure gateway on time.

2. **SOI legs use optimal departure scan.** Within the constraint of needing to arrive
   by a certain time, the system picks the lowest-Δv departure angle.

3. **Local Hohmann legs depart immediately** (Δv is constant for circular-to-circular).
   They're scheduled to complete just before the next leg's optimal departure.

4. **Auto-transition between legs.** Once a leg completes (ship arrives at waypoint orbit),
   the ship automatically begins the next leg — no player intervention. The ship does NOT
   dock at intermediate waypoints; it's a continuous mission.

### 2.6 Waypoints vs Stops

Intermediate locations (like LEO when going Moon → Mars) are **waypoints**, not stops:
- The ship does NOT dock at the location
- The ship does NOT appear in the location's inventory/fleet list
- The ship's `to_location_id` remains the final destination
- The intermediate orbit is just a prediction segment in the burn chain
- The ship auto-transitions to the next leg when it reaches the waypoint orbit

This means from the player's perspective, the ship is on a single continuous mission
to the final destination. The intermediate legs are implementation details visible
only in the burn plan.

### 2.7 Fuel and Δv Validation

The hierarchy walker validates the **entire chain** before allowing execution:
- Total Δv across all legs must not exceed ship capability
- Total fuel across all legs must not exceed fuel on board
- Any single-leg infeasibility fails the whole mission
- The burn plan preview shows cumulative fuel consumption per burn

If the ship can reach LLO but not Mars, the error message says:
> "Insufficient Δv: mission requires 10,164 m/s, ship has 8,200 m/s.
> Ship can reach LEO (leg 2 of 3) but cannot complete leg 3 (LEO → LMO)."

---

## 3. Unified Transfer Planner UX

### 3.1 One Flow for Everything

Every transfer — local, SOI, interplanetary, multi-leg — uses the same planner flow:

1. Player selects destination from the hierarchical tree
2. Backend auto-plans the full mission (hierarchy walker + optimal window scan)
3. Planner displays the mission burn plan card
4. Player clicks "Execute Mission"

No sliders, no manual orbital mechanics. The game does the work.

### 3.2 Planner Layout

```
┌────────────────────────────────────────────┐
│ TRANSFER PLANNER                     [X]   │
│                                             │
│ From: LEO (Earth)                          │
│ ────────────────────────────               │
│ ▸ Earth Zone                               │
│   ▸ Orbits                                 │
│     GEO  •  HEO  •  ...                   │
│   ▸ Moon                                   │
│     LLO  •  Lunar Surface                  │
│   ▸ Lagrange Points                        │
│     L1  •  L2  •  L4  •  L5              │
│ ▸ Mars Zone                                │
│   LMO  •  Mars Surface  •  Phobos         │
│ ▸ Jupiter Zone                             │
│   ...                                      │
│                                             │
│ ═══════════════════════════════════════     │
│                                             │
│ [Mission burn plan appears here when       │
│  a destination is selected]                │
│                                             │
│ [For interplanetary: collapsible           │
│  "Advanced: Window Map" section with       │
│  porkchop plot + best windows table]       │
│                                             │
│ ═══════════════════════════════════════     │
│                                             │
│     [ Cancel ]       [ Execute Mission ]   │
└────────────────────────────────────────────┘
```

### 3.3 Auto-Optimal Window

When the player selects a destination:

1. **Backend computes the optimal mission** — full chain with best departure timing.
   - Local Hohmann: immediate departure (Δv is constant)
   - SOI: scan 2 game-days for optimal departure angle
   - Interplanetary: pick the best Lambert window from porkchop scan
   - Multi-leg: interplanetary window anchors the timing; earlier legs planned to arrive on time

2. **Frontend displays the unified burn plan card** (as shown in Section 2.4).

3. **Ship always waits for the optimal window.** Departure time is displayed as
   read-only info: "Departure: 2157-06-14 04:00 (in 3d 12h)".

### 3.4 Next Best Windows List

For interplanetary missions (or SOI missions where timing matters), show a compact
list of alternative windows below the burn plan:

```
▸ Alternative Windows
  Window 1 ★  Jun 14  •  4,520 m/s  •  201d transit
  Window 2     Jul 02  •  4,800 m/s  •  220d transit
  Window 3     Aug 19  •  5,100 m/s  •  245d transit
```

Clicking an alternative window replaces the burn plan with that window's plan.
This gives players choice without requiring them to understand a porkchop plot.

### 3.5 "No Window Available" Handling

If the next interplanetary window is very far away:

```
⚠ Next Mars window: 2158-06-14 (in 400d)
  No efficient transfer available sooner.
  Best available: 12,500 m/s (2.8× optimal)
```

The planner still shows the best option but warns about the Δv penalty.

### 3.6 Transfer Initiation

When the player clicks "Execute Mission":

1. Frontend sends `POST /api/ships/{id}/transfer` with:
   ```json
   {
     "to_location_id": "lmo",
     "departure_time": 1710412920.0,
     "tof_s": 19008000.0
   }
   ```

2. Backend builds the full chain, stores orbit + maneuvers + predictions.

3. Ship enters transit. Intermediate waypoints auto-transition without player input.

---

## 4. Interplanetary Advanced View (Porkchop)

### 4.1 Role

The porkchop plot is a **collapsible advanced section**, closed by default, that
appears in the planner only for missions with an interplanetary leg. It lets
knowledgeable players choose a non-optimal window if they have strategic reasons
(e.g., arriving earlier at higher Δv cost).

### 4.2 The Porkchop Section

```
▸ Advanced: Departure Window Map            [▼ expand]
┌───────────────────────────────────────────────────┐
│ TOF  │ . . . . . █ █ █ . .                       │
│  ↕   │ . . . █ █ ◉ █ █ . .   ◉ = selected       │
│      │ . . █ █ █ █ ★ █ . .   ★ = best solution   │
│      │ . . . █ █ █ █ . . .                        │
│      │ . . . . . █ . . . .                        │
│      └──────────────────────                      │
│        Departure date →                           │
│                                                   │
│ ◄━━━━[▓▓]━━━━━━━━━━━━► Departure date slider     │
│ ◄━━━━━━━━━━[▓▓]━━━━━━► Time of flight slider     │
│                                                   │
│ Best Windows (click to select):                   │
│   Jun 14  •  201d  •  4,520 m/s  ★               │
│   Jul 02  •  220d  •  4,800 m/s                   │
│   Aug 19  •  245d  •  5,100 m/s                   │
└───────────────────────────────────────────────────┘
```

### 4.3 Interaction

1. **Click a porkchop cell** → selects that (departure, TOF) pair, updates the main
   burn plan above with the new window's plan.

2. **Drag departure slider** → moves vertical crosshair. Auto-selects best TOF in
   that column.

3. **Drag TOF slider** → moves horizontal crosshair. Updates Δv readout from grid.

4. **Click a Best Windows row** → jumps sliders to that solution, updates burn plan.

5. **Double-click porkchop** → re-fetches a zoomed sub-region at higher resolution.

All interactions update the main burn plan card in the planner above the porkchop
section. The porkchop is a control for the burn plan — not a separate view.

### 4.4 Backend: Departure-Specific Lambert

**Extend** `_plan_interplanetary_transfer()` in `orbit_bridge.py` to accept an optional
`tof_s` parameter. When provided, the Lambert solver uses that specific TOF rather than
sweeping 14 candidates to find the best.

**Extend** `compute_interplanetary_leg()` in `transfer_planner.py` to accept an optional
`tof_override_s` parameter. When set, skip the multi-candidate sweep and solve Lambert
for exactly that TOF.

---

## 5. Orbital Path Visualization

### 5.1 Current Rendering

Transit ships (when selected) draw:
- Active orbit segment: solid colored arc (departure=blue, transfer=orange, arrival=green)
- Prediction segments: dashed arcs with distinct colors and alpha
- Burn diamond markers: orange (future), grey (past)

Docked ships (when selected) draw:
- Orbit ellipse with Ap/Pe markers

**All ships not selected draw nothing** — their orbits are invisible.

### 5.2 Target: Faint/Highlighted Orbit System

#### Location-Scoped Faint Orbits

**Ships at the currently selected/viewed location or body** show a faint orbit path:
- **Docked ships**: Faint circular/elliptical orbit around their parent body
- **Transit ships**: Faint prediction chain (all segments connected)

Only ships relevant to the active view are drawn — no global orbit rendering.

Rendering parameters:

| State | Line width | Alpha | Color |
|---|---|---|---|
| Unselected docked | 0.5 px | 0.08 | White (`0xffffff`) |
| Unselected transit (active segment) | 0.8 px | 0.12 | Segment color (blue/orange/green) |
| Unselected transit (other segments) | 0.5 px | 0.06 | Segment color |
| **Selected/hovered docked** | **1.5 px** | **0.5** | **Orbit color** |
| **Selected transit (active segment)** | **2.0 px** | **0.7** | **Segment color** |
| **Selected transit (other segments)** | **1.0 px** | **0.35** | **Segment color** |

#### Performance Considerations

Scoping orbits to the active location already limits draw count. Additional mitigations:
- **Distance culling**: Skip faint orbits when the orbit radius in screen pixels is < 3 px
- **Batch rendering**: Render all faint orbits in a single PIXI.Graphics object, rebuilt each frame,
  rather than per-ship Graphics objects
- **LOD**: At low zoom, draw faint orbits with fewer points (16 instead of 64)

### 5.3 Burn Chain Visualization

When a transit ship is selected, draw the **complete burn chain** as a connected visual story:

```
  [Coast in LEO]  ──◆──  [Transfer ellipse]  ──◆──  [Arrival orbit at Moon]
       blue          burn1      orange           burn2       green
```

The chain connects each prediction segment visually:
- Each segment is drawn as an arc-clipped orbit path (as today, but with enhanced alpha)
- **Connecting lines**: A thin dashed line connects the end of one segment to the start of the
  next if there's a body-frame change (e.g., Earth → Sun → Moon). This handles the visual
  discontinuity when the reference body changes.
- **Burn diamonds** are drawn ON the orbit path at the exact burn position (already implemented)
- **Arrow heads**: Small arrow chevrons along the transfer arc indicate direction of travel

#### Active Segment Highlight

The currently active prediction segment (the one the ship is traversing right now) gets:
- Thicker line (2.0 px vs 1.0 px)
- Higher alpha (0.7 vs 0.35)
- Solid line style (vs dashed for non-active segments)
- A small pulsing dot at the ship's current position along the arc

---

## 6. Burn Marker Interaction

### 6.1 Current State

Burn diamonds are drawn at each maneuver position (orange=future, grey=past) but have
no interactivity — no hover, no click, no tooltip.

### 6.2 Target: Hover Tooltip on Burn Markers

When the player hovers over a burn diamond marker, display a tooltip panel:

```
┌──────────────────────────────────┐
│ Burn 1: TLI Burn                 │
│ ─────────────────────────────    │
│ Δv:        3,132 m/s prograde    │
│ Time:      2157-03-14 08:22      │
│ Countdown: T-1h 14m              │
│                                  │
│ Before:                          │
│   LEO circular (400 km)          │
│   Period: 92 min                 │
│                                  │
│ After:                           │
│   Transfer orbit                 │
│   Pe: 400 km  Ap: 384,399 km    │
│   Period: 4d 22h                 │
└──────────────────────────────────┘
```

### 6.3 Implementation

1. **Hit detection**: Each burn marker is a diamond ~8–12 px on screen. On `pointermove`,
   check distance from cursor to each visible burn marker position. If within 12 px and
   the ship's burn chain is visible, show the tooltip.

2. **Tooltip data source**: The server already stores maneuvers with `prograde_m_s`,
   `radial_m_s`, `time_s`, and `label`. The prediction segments contain the before/after
   orbital elements. The tooltip content can be assembled client-side from:
   - `ship.maneuvers[i]` → Δv components, time, label
   - `ship.orbit_predictions[i]` → pre-burn orbit elements
   - `ship.orbit_predictions[i+1]` → post-burn orbit elements
   - `OrbitRenderer.orbitSummary()` → formatted Ap/Pe/Period/Ecc

3. **Tooltip rendering**: Use a DOM overlay `<div>` (not PixiJS) for crisp text rendering.
   Position it near the burn marker, offset to avoid cursor overlap. Use the same style
   as the existing ship info panel tooltips.

4. **Tooltip for completed burns**: Show the same info but with a "COMPLETED" badge and
   muted styling (same pattern as the info panel burn schedule).

### 6.4 Burn Marker Enhancement

Upgrade burn markers from simple diamonds to directional indicators:
- **Prograde burn** (Δv > 0): Forward-pointing chevron (▶) or arrow along orbit direction
- **Retrograde burn** (Δv < 0): Backward-pointing chevron (◀)
- **Radial component**: Small perpendicular tick mark if radial_m_s ≠ 0
- **Size proportional to Δv**: Larger markers for bigger burns (clamped to 6–16 px range)

---

## 7. Ghost Projection (KSP-Style Intercept)

### 7.1 Concept

As the player moves their mouse along the transfer arc of a selected transit ship,
project a **ghost image** of the destination body at the game-time corresponding to
that position on the arc. This lets the player visualize the intercept geometry — seeing
both where the ship will be and where the target will be at the same moment.

### 7.2 Interaction Flow

1. Player selects a transit ship → burn chain becomes visible
2. Player hovers mouse near the **transfer segment** arc
3. As mouse moves along the arc, the system:
   a. Finds the closest point on the transfer arc to the mouse position
   b. Computes the **game-time** at that arc position (from true anomaly → mean anomaly → time)
   c. Computes the **destination body's position** at that future game-time
   d. Draws a **ghost circle** for the destination body at that computed position
   e. Draws a **thin dashed line** from the arc point to the ghost body (intercept line)
   f. Shows a small label: time offset (`T+142d`), distance (`243,000 km`)

### 7.3 Visual Design

```
                    Ghost body (semi-transparent)
                         ◯ ···
                        ╱     ·  dashed intercept line
  Ship orbit arc ──── ✱      ·
  (highlighted)      ╲     ·
                      ◆ (burn marker)
```

- **Ghost body**: Semi-transparent circle at 0.25 alpha, same color as the body's normal rendering.
  Size matches the body's visual radius at current zoom.
- **Intercept line**: Thin dashed white line from the arc point to the ghost body center.
  Alpha 0.3.
- **Distance label**: Small text near the midpoint of the intercept line showing separation
  distance in km or AU.
- **Time label**: Near the arc point, showing `T+Xd Yh` from the ship's departure.

### 7.4 Implementation

#### Time-from-Arc-Position

Given a mouse world position near the transfer arc, compute the corresponding game-time:

1. **Project mouse to nearest point on the rendered arc** — iterate through the arc's
   polyline points, find the closest one.
2. **Map point index to true anomaly** — the arc points are generated at evenly-spaced
   true anomaly intervals by `orbitPoints()`. The index gives the true anomaly.
3. **True anomaly → mean anomaly → time**:
   ```
   E = atan2(sqrt(1-e²) * sin(ν), e + cos(ν))   // eccentric anomaly
   M = E - e * sin(E)                              // mean anomaly
   t = epoch + (M - M0) / n                        // time from mean motion
   ```
   This is the inverse of `trueAnomalyAtTime()`.

4. The prediction segment's `from_s` and `to_s` bound the valid time range.

#### Destination Body Position

Given the computed future game-time:
1. Look up the destination body's state vector at that time using the same
   `bodyPositions` / `projectDeepPosition()` infrastructure already in `app.js`.
2. For heliocentric bodies (Mars, Jupiter, etc.): propagate from the body's
   `state_vectors_helio` using Kepler elements.
3. For moons (Moon, Io, etc.): propagate the moon's orbit around its parent.

#### Performance

- Only compute ghost projection when the mouse is within ~20 px of the transfer arc
- Throttle to 60 Hz max (requestAnimationFrame is already the render loop)
- Cache body propagation results — only recompute when the time changes by > 60 s

### 7.5 Extended: Pre-Transfer Ghost Preview

In the **transfer planner** (before confirming), when the player hovers over the porkchop
plot or adjusts sliders, show a ghost preview on the orbital map:
- Draw the planned transfer arc as a dashed preview line
- Show the ghost destination body at the arrival time
- This gives spatial context to the porkchop selection

This requires the planner to emit a "preview orbit" event that the map renderer picks up,
drawing a temporary ghost orbit without committing the transfer.

---

## 8. Backend Changes

### 8.1 Hierarchy Walker — `plan_chain_mission()`

**New core function** in `orbit_bridge.py`:

```python
def plan_chain_mission(
    from_location_id: str,
    to_location_id: str,
    now_s: float,
    tof_override_s: float | None = None,
) -> dict:
    """
    Auto-plan a complete multi-leg mission from any location to any location.
    
    1. Resolve from/to locations to their body hierarchy positions
    2. Find lowest common ancestor (LCA) body
    3. Build ordered list of legs: ascend → cross → descend
    4. For each leg, call the appropriate planner
    5. Chain timing: interplanetary window anchors; earlier legs planned backwards
    6. Combine into unified burn plan with sequential predictions
    """
```

Returns:
```json
{
  "legs": [
    {
      "leg_index": 0,
      "from_location_id": "lunar_surface",
      "to_location_id": "llo",
      "transfer_type": "local_hohmann",
      "burns": [ ... ],
      "predictions": [ ... ],
      "total_dv_m_s": 1680.0,
      "total_tof_s": 480.0
    },
    {
      "leg_index": 1,
      "from_location_id": "llo",
      "to_location_id": "leo",
      "transfer_type": "soi_hohmann",
      "burns": [ ... ],
      "predictions": [ ... ],
      "total_dv_m_s": 3964.0,
      "total_tof_s": 242400.0
    },
    {
      "leg_index": 2,
      "from_location_id": "leo",
      "to_location_id": "lmo",
      "transfer_type": "interplanetary_lambert",
      "burns": [ ... ],
      "predictions": [ ... ],
      "total_dv_m_s": 4520.0,
      "total_tof_s": 17366400.0
    }
  ],
  "burns": [ /* all burns flattened, sequentially numbered */ ],
  "orbit_predictions": [ /* all predictions flattened, sequentially ordered */ ],
  "initial_orbit": { /* first leg's departure orbit */ },
  "orbit_body_id": "moon",
  "total_dv_m_s": 10164.0,
  "total_tof_s": 17609280.0,
  "departure_time": 1710412920.0,
  "arrival_time": 1728022200.0,
  "from_location_id": "lunar_surface",
  "to_location_id": "lmo",
  "transfer_type": "chain_mission",
  "alternative_windows": [
    { "departure_s": 1710412920.0, "total_dv_m_s": 10164.0, "tof_s": 17609280.0 },
    { "departure_s": 1711968000.0, "total_dv_m_s": 10800.0, "tof_s": 19008000.0 },
    { "departure_s": 1715500000.0, "total_dv_m_s": 11500.0, "tof_s": 21168000.0 }
  ]
}
```

### 8.2 Gateway Configuration

Each body in `celestial_config.json` gains a `gateway_orbit` field:

```json
{
  "earth": { "gateway_orbit": "leo" },
  "moon": { "gateway_orbit": "llo" },
  "mars": { "gateway_orbit": "lmo" },
  "jupiter": { "gateway_orbit": "joi" }
}
```

When the hierarchy walker needs to ascend from a sub-body or descend to one, it uses
the gateway as the intermediate waypoint orbit.

### 8.3 New/Modified Endpoints

#### `GET /api/transfer/mission_preview`

**New endpoint** replacing `burn_plan_preview`. Returns the full chain mission preview.

Parameters:
- `ship_id` (required) — the ship to plan for
- `to_location_id` (required) — final destination
- `departure_time` (optional, default: auto-optimal)
- `tof_s` (optional, for interplanetary leg — specific TOF override)

Response: Same structure as `plan_chain_mission()` return, plus:
```json
{
  "fuel_required_kg": 14200.0,
  "fuel_remaining_kg": 800.0,
  "ship_dv_remaining_m_s": 1203.0,
  "is_feasible": true,
  "infeasibility_reason": null,
  "infeasibility_leg": null
}
```

This endpoint does NOT modify the database. It's the preview for the transfer planner.

#### `POST /api/ships/{id}/transfer` — Extension

Add optional parameters:
- `departure_time` (float, optional) — game-time epoch seconds for departure
- `tof_s` (float, optional) — time-of-flight override for interplanetary Lambert

Internally calls `plan_chain_mission()` to compute the full chain, then stores the
unified burn plan (all legs merged into one maneuver queue + one prediction chain).

The ship's `to_location_id` is set to the **final destination** only. Intermediate
waypoints are encoded in the maneuver queue and prediction segments but do not
create separate transit records.

### 8.4 Leg Transition via Maneuver Queue

The existing `settle_ship_events()` processes maneuvers sequentially. For chain missions,
leg transitions are encoded as maneuver entries:

```python
{
  "time_s": 1710655320.0,
  "type": "soi_transition",  # existing type, already handled
  "body_from": "earth",
  "body_to": "moon"
}
```

No new maneuver types are needed — the existing `_execute_burn()` and
`_execute_soi_transition()` handle all cases. The chain mission just produces a longer
maneuver queue with more entries.

### 8.5 Optimal Window Scanning

**New function** in `orbit_bridge.py`:

```python
def scan_optimal_departure(
    from_location_id: str,
    to_location_id: str,
    now_s: float,
    window_s: float = 2 * 86400,
    steps: int = 48
) -> dict:
    """
    For SOI transfers: sweep departure times, return the one with minimum Δv.
    For local Hohmann: return now (Δv is constant for circular-to-circular).
    For interplanetary: use porkchop best_solutions.
    For chain missions: find the interplanetary window first, then plan backwards.
    """
```

### 8.6 Burn Orbit Summaries

**New helper** in `orbit_service.py` or `orbit_bridge.py`:

```python
def orbit_summary(elements: dict, body_id: str) -> dict:
    """
    Convert raw orbital elements to a human-readable summary.
    Returns: { type, altitude_km/pe_km/ap_km, body, period_s, ecc }
    """
```

Called by the mission preview to annotate each burn with before/after orbit descriptions.

---

## 9. Data Contracts

### 9.1 Chain Mission Preview (Frontend ↔ Backend)

The frontend mission planner consumes this structure from the `mission_preview` endpoint:

```typescript
interface ChainMissionPreview {
  legs: MissionLeg[];
  burns: BurnPreview[];          // all burns flattened across legs
  total_dv_m_s: number;
  total_tof_s: number;
  departure_time: number;        // game epoch seconds
  arrival_time: number;
  fuel_required_kg: number;
  fuel_remaining_kg: number;
  ship_dv_remaining_m_s: number;
  transfer_type: "local_hohmann" | "soi_hohmann" | "interplanetary_lambert" | "chain_mission";
  orbit_predictions: PredictionSegment[];
  alternative_windows: WindowOption[];
  is_feasible: boolean;
  infeasibility_reason: string | null;
  infeasibility_leg: number | null;
}

interface MissionLeg {
  leg_index: number;
  from_location_id: string;
  to_location_id: string;
  transfer_type: "local_hohmann" | "soi_hohmann" | "interplanetary_lambert";
  burns: BurnPreview[];
  predictions: PredictionSegment[];
  total_dv_m_s: number;
  total_tof_s: number;
}

interface BurnPreview {
  time_s: number;
  label: string;
  prograde_m_s: number;
  radial_m_s: number;
  dv_m_s: number;               // magnitude
  countdown_s: number;           // seconds from now
  leg_index: number;             // which leg this burn belongs to
  before_orbit: OrbitSummary;
  after_orbit: OrbitSummary;
}

interface OrbitSummary {
  type: "circular" | "elliptical" | "hyperbolic";
  body: string;                  // display name
  altitude_km?: number;          // for circular
  pe_km?: number;                // for elliptical
  ap_km?: number;                // for elliptical
  period_s?: number;
  ecc?: number;
}

interface WindowOption {
  departure_s: number;
  total_dv_m_s: number;
  tof_s: number;
}
```

### 9.2 Ghost Projection (Client-Side Only)

No backend changes needed — the ghost projection is computed entirely client-side using
existing body position propagation and orbit math.

```typescript
interface GhostProjection {
  arc_game_time: number;        // game-time at the mouse position on the arc
  arc_world_pos: { x: number, y: number };
  dest_body_world_pos: { x: number, y: number };
  distance_km: number;
  time_offset_s: number;        // seconds from departure
}
```

### 9.3 Porkchop Selection State (Client-Side)

```typescript
interface PorkchopSelection {
  departure_time: number;       // game epoch seconds
  tof_s: number;                // seconds
  arrival_time: number;         // derived: departure + tof
  dv_m_s: number;               // from grid lookup
  grid_col: number;             // departure column index
  grid_row: number;             // TOF row index
  source: "click" | "slider" | "table" | "auto";
}
```

---

## 10. Implementation Plan

### Phase 0: Remove Multi-Hop Routing & Add Gateway Config

**Files**: `fleet_router.py`, `orbit_bridge.py`, `celestial_config.py`, `config/celestial_config.json`

1. Strip Dijkstra multi-hop pathfinding from `_compute_route_quote()`
2. Remove transfer-edge graph walking and intermediate-stop resolution
3. Add `gateway_orbit` field to body entries in `celestial_config.json`
4. Add body hierarchy traversal helpers to `celestial_config.py`
5. Update tests — remove multi-hop test cases, add direct-transfer coverage
6. Document which location pairs lose connectivity (to be restored by auto-chain)

### Phase 1: Hierarchy Walker & Chain Mission Backend

**Files**: `orbit_bridge.py`, `orbit_service.py`, `fleet_router.py`

1. Implement `plan_chain_mission()` — LCA-based hierarchy walker
2. Implement backward-planning for interplanetary-anchored timing
3. Add `orbit_summary()` helper for human-readable orbit descriptions
4. Add `scan_optimal_departure()` for SOI window scanning
5. Create `GET /api/transfer/mission_preview` endpoint
6. Extend `POST /api/ships/{id}/transfer` to accept `departure_time` and `tof_s`
7. Extend `compute_interplanetary_leg()` to accept `tof_override_s`
8. Ensure `settle_ship_events()` handles multi-leg maneuver queues (already works)
9. Comprehensive tests: single-leg, multi-leg, cross-system, fuel validation

### Phase 2: Unified Transfer Planner UI

**Files**: `static/js/app.js`

1. Replace `fetchAndRenderQuote()` to call `mission_preview` endpoint
2. Render unified burn plan card with per-leg sections
3. Show mission window info (departure time as read-only)
4. Show alternative windows list for interplanetary missions
5. Wire alternative window clicks to re-fetch mission preview
6. Display fuel/Δv validation across full chain
7. "Execute Mission" button sends `departure_time` + `tof_s`

### Phase 3: Interplanetary Advanced View (Porkchop)

**Files**: `static/js/app.js`

1. Move porkchop plot into collapsible "Advanced: Window Map" section (closed by default)
2. Make porkchop cells clickable → updates main burn plan above
3. Add departure date + TOF sliders wired to porkchop crosshairs
4. Make Best Windows table rows clickable
5. Porkchop zoom (double-click sub-region)
6. Cache last 3 porkchop results client-side

### Phase 4: Orbital Path Visualization

**Files**: `static/js/app.js`, `static/js/orbit_renderer.js`

1. Draw faint orbits for ships at the active location (with distance culling)
2. Enhanced alpha/width for selected ship orbits
3. Draw full burn chain (all prediction segments) for selected transit ships
4. Direction arrows along transfer arcs
5. Connecting lines between segments at body-frame boundaries

### Phase 5: Burn Marker Interaction

**Files**: `static/js/app.js`

1. Hit detection for burn diamond markers
2. DOM tooltip overlay with burn details (Δv, countdown, before/after orbit)
3. Directional burn marker sprites (prograde/retrograde chevrons)
4. Size proportional to Δv

### Phase 6: Ghost Projection

**Files**: `static/js/app.js`, `static/js/orbit_renderer.js`

1. Mouse → arc proximity detection
2. Arc position → game-time conversion (inverse of trueAnomalyAtTime)
3. Body position propagation at future time
4. Ghost body circle rendering
5. Intercept line + distance/time labels
6. Pre-transfer ghost preview from planner selections

---

## Appendix A: Design Decisions

Resolved during review:

1. **Scheduled departure UX**: Ship always waits for the optimal departure window. No
   "depart now (suboptimal)" option. The planner computes the best window and queues the
   ship for that time automatically.

2. **Multi-hop routes**: **Replaced by auto-chain missions.** The Dijkstra multi-hop routing
   is stripped out. In its place, a hierarchy walker (`plan_chain_mission()`) auto-resolves
   multi-leg chains using the celestial body tree. Every leg is a real patched-conic segment.
   The player sees one unified mission and clicks one button.

3. **Unified UX**: All transfer types (local, SOI, interplanetary, multi-leg) use the same
   planner flow: auto-optimal window → burn plan card → confirm. The porkchop plot is a
   collapsible advanced view, not the primary interface.

4. **Faint orbit density**: Orbits are only drawn for ships at the currently selected/viewed
   location or body. No global "show all orbits" toggle — scoping to the active location
   keeps the map clean and performant.

5. **Ghost projection for SOI transfers**: Yes, ghost projection applies to all transfer types
   including Earth-Moon. Even small body motion matters for intercept geometry, and it
   provides consistent UX.

6. **Porkchop caching**: Cache the last 3 porkchop results client-side, keyed by
   (from, to, departure range, tof range).

7. **Transfer preview on map**: Yes — when the planner is open and a destination is selected,
   draw a dashed preview arc on the orbital map with a ghost destination body at the arrival
   time. Full spatial context before confirming.

8. **Waypoints vs stops**: Intermediate locations in a chain mission are waypoints only.
   The ship does not dock, does not appear in location inventories, and auto-transitions
   to the next leg. From the player's perspective, it's one continuous mission.
