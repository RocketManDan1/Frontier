# Reactor-Engine Thermal Pairing System

Introduces **temperature-based Isp/thrust modifiers** when pairing reactors with engines, and adds three new reactor branches (pebble bed, liquid core, vapor core) plus a new liquid-core engine branch.

---

## Physics Basis

NTR specific impulse is governed by propellant temperature:

```
Isp = C * sqrt(T_chamber / M_propellant)
```

The **same engine nozzle** fed by a hotter reactor produces higher Isp because the propellant exits faster. However, at a fixed thermal power input, higher exhaust velocity means lower mass flow rate, so **thrust decreases**:

```
Thrust ∝ 1 / v_exhaust  (at constant power)
```

This gives a natural two-lever system:
- **Reactor temperature** modifies Isp (and inversely, thrust)
- **Reactor MW output** determines throttle cap (existing mechanic)

> **Note on MW values:** The `thermal_mw` requirements on engines are a **gameplay abstraction** — they do not equal the jet kinetic power ($F \cdot v_e / 2$), which would require much larger reactors. Instead, `thermal_mw` represents the fraction of reactor output coupled to the engine's propellant heating loop. The sqrt-temperature scaling for Isp/thrust is physically correct; the MW throttle mechanic is a separate game-balance lever.

---

## Data Model Changes

### Reactor JSON: Add `core_temp_k`

Each reactor gains a `core_temp_k` field in its `output` block representing the peak propellant temperature achievable:

```json
{
  "id": "lars1_crucible",
  "branch": "liquid_core",
  "output": {
    "thermal_mw": 2200,
    "core_temp_k": 5250
  }
}
```

Reference temperatures by branch:

| Branch | core_temp_k | Real-World Basis |
|---|---|---|
| `solid_core` | 2800 | NERVA/Rover test data |
| `pebble_bed` | 3200 | Timberwind/SNTP program |
| `liquid_core` | 5250 | LARS studies, high-pressure supercritical uranium limit |
| `vapor_core` | 8000 | Colloid/UF4 vapor studies |
| `gas_core` (GCNR-200) | 12000 | Closed-cycle lightbulb estimates |
| `gas_core` (GCNR-4000) | 25000 | Open-cycle plasma column estimates |

### Engine JSON: Add `rated_temp_k` and `compatible_reactor_branches`

Each engine gains:
- `rated_temp_k` in `performance` — the reactor temperature its stats are baselined to
- `compatible_reactor_branches` — which reactor branches it can accept

```json
{
  "id": "lcn_1_torrent",
  "branch": "liquid_core",
  "compatible_reactor_branches": ["liquid_core", "vapor_core"],
  "performance": {
    "max_thrust_kN": 450,
    "isp_s": 1100,
    "rated_temp_k": 5250
  }
}
```

### Existing Items: Backfill Values

Existing reactors and engines need these fields added:

**Reactors:**

| Reactor | core_temp_k |
|---|---|
| RD-0410 "Igrit" | 2800 |
| KIWI-B4E "Bison" | 2800 |
| PEWEE-1 "Sparrow" | 2800 |
| Phoebus-1B "Titan" | 2800 |
| NERVA-2 "Aegis" | 2800 |
| Phoebus-2A "Colossus" | 2800 |
| GCNR-200 "Alcyone" | 12000 |
| GCNR-4000 "Hyperion" | 25000 |

**Engines:**

| Engine | rated_temp_k | compatible_reactor_branches |
|---|---|---|
| SCN-1 "Pioneer" | 2800 | `["solid_core", "pebble_bed"]` |
| SCN-2 "Frontier" | 2800 | `["solid_core", "pebble_bed"]` |
| ASCN-1 "Venture" | 2800 | `["solid_core", "pebble_bed"]` |
| ASCN-2 "Atlas" | 2800 | `["solid_core", "pebble_bed"]` |
| LCN-1 "Torrent" | 5250 | `["liquid_core", "vapor_core"]` |
| LCN-2 "Cascade" | 5250 | `["liquid_core", "vapor_core", "gas_core"]` |
| CGN-1 "Helios" | 12000 | `["gas_core", "vapor_core"]` |
| CGN-2 "Prometheus" | 12000 | `["gas_core", "vapor_core"]` |
| OGN-1 "Icarus" | 25000 | `["gas_core"]` |
| OGN-2 "Daedalus" | 25000 | `["gas_core"]` |

---

## Runtime Calculation

In `catalog_service.py`, the power budget function gains a temperature modifier step. The calculation runs **after** the existing MW throttle cap:

```python
# Temperature modifier
temp_ratio = math.sqrt(reactor_core_temp_k / engine_rated_temp_k)
actual_isp = engine_isp_s * temp_ratio
actual_thrust = engine_max_thrust_kN / temp_ratio

# Existing MW throttle cap (unchanged)
throttle = min(1.0, reactor_thermal_mw / engine_thermal_mw)
final_thrust = actual_thrust * throttle
```

### Pairing Examples

**ASCN-2 Atlas (rated 2800K) + LARS-1 Crucible (5250K):**
```
temp_ratio  = sqrt(5250 / 2800) = 1.37
actual_isp  = 1000 * 1.37 = 1370s
actual_thrust = 600 / 1.37 = 438 kN
MW throttle = min(1, 2200 / 1250) = 1.0
final_thrust = 438 kN
```

**LCN-1 Torrent (rated 5250K) + VCR-400 Tempest (8000K):**
```
temp_ratio  = sqrt(8000 / 5250) = 1.23
actual_isp  = 1100 * 1.23 = 1356s
actual_thrust = 450 / 1.23 = 365 kN
MW throttle = min(1, 5000 / 1500) = 1.0
final_thrust = 365 kN
```

**SCN-1 Pioneer (rated 2800K) + PBR-40 Cinder (3200K):**
```
temp_ratio  = sqrt(3200 / 2800) = 1.07
actual_isp  = 850 * 1.07 = 909s
actual_thrust = 250 / 1.07 = 234 kN
MW throttle = min(1, 800 / 200) = 1.0
final_thrust = 234 kN
```

### Compatibility Enforcement

If the reactor's branch is NOT in the engine's `compatible_reactor_branches`:
- **Shipyard gate:** Reject the build. The ship designer should not allow incompatible pairings.
- **Power budget:** If somehow loaded, treat as throttle = 0 (no thrust).

---

## New Reactor Branches

### Pebble Bed (3 reactors)

Fuel geometry variant of solid core. Loose ceramic fuel pebbles centrifuged in a rotating chamber. Outstanding power-to-mass ratio (~2x solid core), but fuel elements degrade under mechanical stress — shorter core life when that system is implemented.

| ID | Name | TL | Mass (t) | MW | core_temp_k | Research Node |
|---|---|---|---|---|---|---|
| pbr40_cinder | PBR-40 "Cinder" | 1.5 | 0.45 | 800 | 3200 | pebble_bed_fission |
| twr45_stormwind | TWR-45 "Stormwind" | 2.0 | 0.75 | 1600 | 3200 | pebble_bed_fission |
| twr75_firebrand | TWR-75 "Firebrand" | 2.5 | 1.25 | 2800 | 3200 | advanced_pebble_bed_fission |

### Liquid Core (2 reactors)

Molten uranium held by rotation or sprayed as droplets. Operates at ~5,250K — far beyond any solid fuel element. Bridges solid and gas core.

| ID | Name | TL | Mass (t) | MW | core_temp_k | Research Node |
|---|---|---|---|---|---|---|
| lars1_crucible | LARS-1 "Crucible" | 2.5 | 1.80 | 2200 | 5250 | liquid_core_fission |
| dcr200_maelstrom | DCR-200 "Maelstrom" | 3.0 | 2.80 | 3800 | 5250 | liquid_core_fission |

### Vapor Core (2 reactors)

Fissile fuel as superheated vapor/colloid suspension. Operating at ~8,000K, nearly plasma conditions without full magnetic containment.

| ID | Name | TL | Mass (t) | MW | core_temp_k | Research Node |
|---|---|---|---|---|---|---|
| ccr1_mirage | CCR-1 "Mirage" | 3.0 | 3.00 | 3500 | 8000 | vapor_core_fission |
| vcr400_tempest | VCR-400 "Tempest" | 3.5 | 4.20 | 5000 | 8000 | vapor_core_fission |

---

## New Engine Branch: Liquid Core (2 engines)

Transpiration-cooled nozzles rated for liquid/vapor core thermal flux. Higher Isp than solid-core engines, lower than gas-core. The LCN-2 is also compatible with gas-core reactors.

| ID | Name | TL | Mass (t) | Thrust (kN) | Isp (s) | MW Req | rated_temp_k | Compatible Reactors |
|---|---|---|---|---|---|---|---|---|
| lcn_1_torrent | LCN-1 "Torrent" | 2.5 | 30 | 450 | 1100 | 1500 | 5250 | liquid_core, vapor_core |
| lcn_2_cascade | LCN-2 "Cascade" | 3.0 | 45 | 750 | 1250 | 2400 | 5250 | liquid_core, vapor_core, gas_core |

---

## UI Changes

### Shipyard — Ship Designer

#### Parts Picker: Compatibility Filtering

When a reactor is already selected on the ship blueprint, the engine picker should **grey out incompatible engines** (reactor branch not in engine's `compatible_reactor_branches`). Similarly, when an engine is selected first, grey out incompatible reactors.

- Greyed-out items remain visible but unselectable
- Tooltip on hover explains: "Requires [liquid_core, vapor_core] reactor"
- If the player removes a reactor, incompatible engines already placed are flagged with a warning icon

#### Stats Panel: Effective Performance

The right-side stats panel currently shows engine stats as fixed values. With thermal pairing, show **effective** values based on the selected reactor:

```
Engine: LCN-1 "Torrent"
Reactor: VCR-400 "Tempest" (8000K)

Isp:    1,100s  -->  1,356s  (+23%)
Thrust: 450 kN  -->  365 kN  (-19%)
Throttle Cap: 100%
```

Display format:
- Base stat in normal weight
- Arrow separator
- Effective stat in bold
- Percentage delta in green (Isp bonus) or amber (thrust reduction)
- If no reactor selected yet, show base stats with a note: "Effective stats depend on reactor pairing"

#### Build Validation

The "Build Ship" button should enforce compatibility:
- If reactor and engine branches are incompatible, disable the build button
- Show error message: "Engine [name] is not compatible with [reactor_branch] reactors"

### Item Info Modal — Attributes Tab

Add new rows to the attributes display for both reactors and engines:

**Reactor attributes (new rows):**

| Label | Value | Notes |
|---|---|---|
| Core Temperature | 5,250 K | Format with comma separator |
| Branch | Liquid Core | Capitalize and space the branch name |

**Engine attributes (new rows):**

| Label | Value | Notes |
|---|---|---|
| Rated Temperature | 5,250 K | The baseline temp for listed stats |
| Compatible Reactors | Liquid Core, Vapor Core | Comma-separated, human-readable |

### Item Info Modal — Description Tab

No changes needed. The description text in each JSON already explains the reactor/engine concept.

### Tooltip (item_display.js)

For thrusters, if reactor context is available (e.g. in the shipyard), show effective Isp/thrust in the tooltip. Otherwise show base stats with a "(rated)" suffix:

```
LCN-1 "Torrent"
Isp: 1,100s (rated)
Thrust: 450 kN (rated)
```

Or with reactor context:

```
LCN-1 "Torrent"
Isp: 1,356s (eff.)
Thrust: 365 kN (eff.)
```

---

## Implementation Checklist

### Data Layer
- [ ] Add `core_temp_k` to all reactor JSON files (new + existing backfill)
- [ ] Add `rated_temp_k` to all engine JSON `performance` blocks (new + existing backfill)
- [ ] Add `compatible_reactor_branches` to all engine JSON files (new + existing backfill)
- [ ] Create 7 new reactor JSON files in `items/reactors/fission/`
- [ ] Create 2 new engine JSON files in `items/thrusters/nuclear_thermal_rocket/main/`
- [ ] Create 9 new recipe JSON files in `items/Recipes/`
- [ ] Update `items/reactors/fission/family.json` with new mainline_files
- [ ] Update `items/thrusters/nuclear_thermal_rocket/family.json` with new mainline_files
- [ ] Update `parts_list.csv` with new entries

### Backend
- [ ] `catalog_service.py` — Parse `core_temp_k` from reactor JSON into catalog dict
- [ ] `catalog_service.py` — Parse `rated_temp_k` and `compatible_reactor_branches` from engine JSON
- [ ] `catalog_service.py` — Add `_compute_thermal_pairing()` function:
  - Input: reactor catalog entry, engine catalog entry
  - Output: `{ effective_isp, effective_thrust, temp_ratio, compatible: bool }`
- [ ] `catalog_service.py` — Update `compute_ship_power_budget()` to apply temp modifier
- [ ] `catalog_service.py` — Validate `compatible_reactor_branches` in thruster validation
- [ ] Shipyard build endpoint — Reject incompatible reactor/engine pairings
- [ ] Ship detail API — Return effective Isp/thrust alongside base values

### Frontend
- [ ] `shipyard.js` — Grey out incompatible engines/reactors in parts picker
- [ ] `shipyard.js` — Show effective stats with delta percentages in stats panel
- [ ] `shipyard.js` — Validate compatibility before enabling build button
- [ ] `item_info.js` — Add Core Temperature / Rated Temperature / Compatible Reactors rows
- [ ] `item_display.js` — Show effective or rated stats in tooltips based on context

### Tests
- [ ] `test_catalog_integrity.py` — Validate all reactors have `core_temp_k`
- [ ] `test_catalog_integrity.py` — Validate all engines have `rated_temp_k` and `compatible_reactor_branches`
- [ ] `test_game_logic.py` — Test thermal pairing math (temp_ratio, effective Isp/thrust)
- [ ] `test_game_logic.py` — Test compatibility rejection for invalid pairings
