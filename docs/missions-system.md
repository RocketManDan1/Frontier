# Missions System — Design Document

## Overview

Missions are **government-contracted objectives** issued by major Earth governments, offering substantial payouts as an incentive for players to venture beyond the Earth–Moon system. Each mission requires hauling a **Mission Materials Module** to a distant destination and completing objectives that vary by difficulty tier.

Missions are generated randomly and drawn from a rolling pool of ~5 available at any time. Only one mission may be active per organization. No missions target the Moon — all destinations are interplanetary (Mercury, Venus, Mars, asteroids, Jupiter system, Saturn system).

---

## Mission Tiers

| Tier | Complexity | Objective | Typical Destinations | Payout | Upfront (50%) |
|------|-----------|-----------|---------------------|--------|---------------|
| **Easy** | ★ | Deliver Mission Materials module to an **orbit** node and leave it | Inner planets, Mars orbits, near asteroids | $5,000,000,000 | $2,500,000,000 |
| **Medium** | ★★ | Deliver module, **prospect** the target site (existing prospecting system), **land**, and deliver the module to the surface site | Mars sites, asteroid belt sites, inner Jovian moons | $10,000,000,000 | $5,000,000,000 |
| **Hard** | ★★★ | Deliver module, land, **power it with electricity** for a sustained period (90 game-days), then **return the module to Earth** (LEO/HEO/GEO) | Outer Jovian moons, Jupiter trojans, Saturn system | $15,000,000,000 | $7,500,000,000 |

### Destination Weighting

Harder missions are weighted toward the outer system to reward deep-space capability:

| Zone | Easy weight | Medium weight | Hard weight |
|------|------------|---------------|-------------|
| Mercury / Venus | 20% | 10% | 0% |
| Mars system | 35% | 25% | 10% |
| Asteroid Belt | 35% | 30% | 15% |
| Jupiter system | 10% | 25% | 40% |
| Saturn system | 0% | 10% | 35% |

**Destination type by tier:**
- **Easy**: orbit nodes only (e.g. `LMO`, `CERES_LO`, `JUP_HO`)
- **Medium**: surface sites only (e.g. `MARS_HELLAS`, `VESTA_RHEASILVIA`, `EUROPA_CONAMARA`)
- **Hard**: surface sites only, weighted to outer system (e.g. `CALLISTO_VALHALLA`, `TITAN_LO`, `ENCELADUS_LO`)

### Excluded Destinations

The following are **never** valid mission destinations:
- All Earth orbits: `LEO`, `HEO`, `GEO`
- All Luna orbits: `LLO`, `HLO`
- Earth–Moon Lagrange points: `L1`–`L5`
- All Luna surface sites: `LUNA_*`

---

## Mission Materials Module

A new **catalog item** representing the payload that must be transported.

```json
{
  "id": "mission_materials_module",
  "name": "Mission Materials Module",
  "type": "mission_module",
  "category_id": "mission",
  "mass_kg": 25000,
  "volume_m3": 40,
  "description": "A sealed government-issued research and materials package. Must be delivered to the designated mission destination."
}
```

- **Mass**: 25,000 kg (~25 tonnes) — heavy enough to require a capable ship but not absurdly so.
- **Volume**: 40 m³ — requires cargo capacity, but fits in a medium-to-large hold.
- One module is minted into the org's inventory at the mission's departure location (LEO) upon acceptance.
- The module is **non-tradeable** and **non-splittable** — it exists as a single inventory stack of quantity 1.
- On mission completion or failure, the module is removed from the game.

---

## Mission Lifecycle

```
┌──────────┐   accept    ┌───────────┐   deliver    ┌───────────┐   (hard only)   ┌──────────┐
│ AVAILABLE ├────────────►│  ACCEPTED  ├────────────►│ DELIVERED  ├───────────────►│ POWERED   │
└──────────┘             └───────────┘              └───────────┘                └────┬─────┘
                              │                          │                           │
                              │ expire/abandon           │ expire                    │ return to Earth
                              ▼                          ▼                           ▼
                         ┌──────────┐              ┌──────────┐              ┌───────────┐
                         │  FAILED  │              │  FAILED  │              │ COMPLETED  │
                         └──────────┘              └──────────┘              └───────────┘
```

### States

| Status | Description |
|--------|-------------|
| `available` | In the mission pool, visible to all players, not yet claimed |
| `accepted` | Claimed by an org; Mission Materials module placed in LEO inventory; 50% upfront paid; timer started |
| `delivered` | Module detected at destination (Easy: complete; Medium: complete after landing; Hard: power phase begins) |
| `powered` | Hard missions only — module is deployed and receiving power at destination site |
| `completed` | All objectives met; remaining 50% payout issued |
| `failed` | Contract expired or org abandoned; module removed; no further payout |
| `abandoned` | Org voluntarily gave up; 50% upfront is NOT returned |

### Completion Conditions

**Easy**: Module is in the ship's cargo or location inventory at the destination orbit → auto-detected or manual "Complete" action → status `completed`, remaining 50% paid.

**Medium**: Module is in the location inventory at the destination **surface site** (not orbit) → requires prior prospecting + landing → manual "Complete" action → status `completed`, remaining 50% paid.

**Hard**: Module is deployed at the destination surface site → must receive power (any positive net power from facility equipment) for **90 consecutive game-days** → module returned to any Earth orbit (`LEO`, `HEO`, or `GEO`) → manual "Complete" action → status `completed`, remaining 50% paid.

### Contract Length

All missions have a **15 game-year** contract window from acceptance. At 48× time scale, that's ~114 real-world days. If the mission is not completed by expiry, it fails automatically.

```
15 game-years = 15 × 365.25 × 86400 = 473,385,000 game-seconds
At 48× scale: 473,385,000 / 48 ≈ 9,862,187 real-seconds ≈ 114 real-days
```

---

## Mission Generation

### Rolling Pool

The system maintains a pool of **5 available missions** at all times. When a mission is accepted, expires unclaimed, or the pool drops below 5, new missions are generated to refill.

Pool check occurs on-access (settle-on-access pattern, consistent with org income and industry): whenever the missions list is fetched via the API, the system checks the pool count and generates new missions as needed.

### Generation Algorithm

1. Roll tier: 40% Easy, 35% Medium, 25% Hard
2. Roll destination zone using tier-specific weights (see table above)
3. Pick a random valid location within that zone (orbit for Easy, surface site for Medium/Hard)
4. Generate a thematic title from templates:
   - Easy: "Orbital Survey — {destination_name}"
   - Medium: "Surface Expedition — {destination_name}"  
   - Hard: "Deep Space Research — {destination_name}"
5. Set payout, upfront amount, and expiry window
6. Insert into `missions` table with status `available`

### Unclaimed Expiry

Available missions that sit unclaimed for **5 game-years** are retired and replaced. This prevents stale pools.

```
5 game-years ≈ 157,795,000 game-seconds ≈ 38 real-days at 48×
```

---

## Database Schema

### New table: `missions`

```sql
CREATE TABLE IF NOT EXISTS missions (
    id                  TEXT PRIMARY KEY,
    tier                TEXT NOT NULL CHECK (tier IN ('easy', 'medium', 'hard')),
    title               TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    destination_id      TEXT NOT NULL,
    destination_name    TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'available'
                        CHECK (status IN ('available','accepted','delivered','powered','completed','failed','abandoned')),
    payout_total        REAL NOT NULL,
    payout_upfront      REAL NOT NULL,
    payout_completion   REAL NOT NULL,
    org_id              TEXT,
    accepted_at         REAL,
    expires_at          REAL,
    delivered_at        REAL,
    power_started_at    REAL,
    power_required_s    REAL NOT NULL DEFAULT 0,
    completed_at        REAL,
    created_at          REAL NOT NULL,
    available_expires_at REAL
);

CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status);
CREATE INDEX IF NOT EXISTS idx_missions_org_id ON missions(org_id);
```

**Migration**: `0023_missions` — appended to the `MIGRATIONS` list in `db_migrations.py`.

---

## API Endpoints

New router: `mission_router.py` mounted at `/api/missions/*`.

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `GET /api/missions` | GET | login | List all available missions (triggers pool refill) |
| `GET /api/missions/active` | GET | login | Get the org's current active mission (if any) |
| `GET /api/missions/{id}` | GET | login | Mission detail |
| `POST /api/missions/{id}/accept` | POST | login | Accept a mission — mints module to LEO, pays 50% upfront |
| `POST /api/missions/{id}/complete` | POST | login | Attempt to complete — validates objectives, pays remaining 50% |
| `POST /api/missions/{id}/abandon` | POST | login | Abandon the mission — module removed, no refund |
| `GET /api/missions/history` | GET | login | Org's completed/failed/abandoned missions |

### `GET /api/missions` — Response

```json
{
  "missions": [
    {
      "id": "msn_a1b2c3",
      "tier": "easy",
      "title": "Orbital Survey — Low Mars Orbit",
      "description": "Deliver a Mission Materials Module to LMO.",
      "destination_id": "LMO",
      "destination_name": "Low Mars Orbit",
      "payout_total": 5000000000,
      "payout_upfront": 2500000000,
      "payout_completion": 2500000000,
      "status": "available",
      "created_at": 978307200.0,
      "available_expires_at": 1135987200.0
    }
  ],
  "active_mission": null,
  "game_time_s": 978307200.0
}
```

### `POST /api/missions/{id}/accept` — Logic

1. Verify org has no active mission (status `accepted`, `delivered`, or `powered`)
2. Verify mission status is `available`
3. Pay 50% upfront to org via `_credit_money()`
4. Mint 1× Mission Materials Module into org's LEO inventory
5. Update mission: `status='accepted'`, `org_id`, `accepted_at`, `expires_at = now + 15 years`
6. Return updated mission

### `POST /api/missions/{id}/complete` — Logic

**Easy:**
1. Check module exists at destination orbit (in location inventory or aboard a ship docked there)
2. Remove module from inventory
3. Pay remaining 50%
4. Set `status='completed'`, `completed_at`

**Medium:**
1. Check module exists at destination surface site inventory
2. Remove module
3. Pay remaining 50%
4. Set `status='completed'`, `completed_at`

**Hard:**
1. If `status='accepted'` → check module at destination site → advance to `delivered`
2. If `status='delivered'` → check module deployed + facility has positive net power → advance to `powered`, set `power_started_at`
3. If `status='powered'` → check 90 game-days have elapsed since `power_started_at` AND module returned to Earth orbit (`LEO`/`HEO`/`GEO`) → remove module, pay 50%, set `completed_at`

### `POST /api/missions/{id}/abandon`

1. Remove Mission Materials Module from wherever it currently is
2. Set `status='abandoned'` — the upfront payment is **not** returned
3. Slot frees up for a new mission

---

## Frontend

### New page: `missions.html`

Accessible via top nav bar and left dock sidebar. Position between **Contracts** and **Admin**.

### Layout

```
┌─────────────────────────────────────────────────────────┐
│  Nav: Map | Fleet | Shipyard | Sites | Research | Org   │
│        | Contracts | [Missions] | Admin                 │
├──────────────┬──────────────────────────────────────────┤
│              │                                          │
│  Available   │   Mission Detail                         │
│  Missions    │                                          │
│  ─────────── │   Title: Deep Space Research — Titan     │
│  ★   LMO     │   Tier: ★★★ Hard                        │
│  ★★  Mars    │   Destination: TITAN_LO                  │
│  ★★  Ceres   │   Payout: $15,000,000,000               │
│  ★★★ Titan   │   Upfront: $7,500,000,000               │
│  ★   Venus   │   Contract Length: 15 years              │
│              │   Description: ...                       │
│              │                                          │
│              │   [ Accept Mission ]                     │
│              │                                          │
├──────────────┴──────────────────────────────────────────┤
│  Active Mission (if any)                                │
│  ────────────────────                                   │
│  Title: Surface Expedition — Vesta Rheasilvia           │
│  Status: ACCEPTED — Module in LEO                       │
│  Time Remaining: 14y 237d                               │
│  [ Complete ] [ Abandon ]                               │
└─────────────────────────────────────────────────────────┘
```

### Sub-sections

1. **Available Missions** (left list) — All missions with `status='available'`, sortable by tier/destination/payout. Click to show detail on the right.
2. **Mission Detail** (right panel) — Selected mission info + Accept button (greyed if org already has active mission).
3. **Active Mission** (bottom bar) — Current org's in-progress mission with status, progress indicator, time remaining, Complete/Abandon buttons.
4. **History tab** — Past completed/failed/abandoned missions.

### Scripts

- `static/js/missions.js` — IIFE module following same pattern as `contracts.js`
- Uses `item_display.js` for rendering the Mission Materials Module in the active mission panel
- `clock.js` for live countdown display

---

## Implementation Phases

### Phase 1 — Data Model & Migration
- Add `0023_missions` migration to `db_migrations.py`
- Create `items/mission_materials_module.json`

### Phase 2 — Backend Service & Router
- Create `mission_service.py` — pool management, generation logic, completion validation
- Create `mission_router.py` — API endpoints
- Register router in `main.py`
- Add `/missions` page route in `main.py`

### Phase 3 — Frontend
- Create `static/missions.html` with nav tabs, layout panels
- Create `static/js/missions.js` with mission list, detail, accept/complete/abandon flows
- Add "Missions" link to all page nav bars + map dock sidebar

### Phase 4 — Expiry & Settlement
- Add settle-on-access expiry check: when pool is loaded, expire overdue available missions and auto-fail overdue accepted missions
- Hard mission power tracking: on `/complete` call, query facility power state at destination

### Phase 5 — Polish
- Mission description templates with flavor text
- Tier-based icon/color coding in the list (★/★★/★★★)
- Toast notifications on accept/complete
- History tab with sortable columns

---

## Recommended Additions (Pre-Build Guardrails)

### 1) Anti-Exploit Rules (Required)

The upfront payment loop can be exploited if players accept then abandon repeatedly. Add one of the following safeguards before launch:

- **Preferred:** Upfront is an **advance**, and is clawed back (or converted to debt) on `abandoned` / `failed`.
- **Alternative:** Require mission collateral at accept time, returned only on completion.
- **Optional reinforcement:** Add org cooldown/standing penalties after abandon/fail.

### 2) Data Integrity Constraints (Required)

Enforce mission invariants at DB level where possible:

- One active mission per org (`accepted`, `delivered`, `powered`) via a partial unique index.
- Mission acceptance in a single transaction (check + claim + payout + module mint), so two orgs cannot claim the same mission during race conditions.
- Keep payout split invariant: `payout_total = payout_upfront + payout_completion`.

### 3) Mission Module Tracking Rules (Required)

Treat mission modules as unique physical artifacts per mission:

- Use a per-mission stack key (e.g. `mission_module_<mission_id>`) so completion checks are unambiguous.
- Completion validator must search both:
  - `location_inventory_stacks` (location cargo)
  - ship `parts_json` / cargo state (docked ships)
- On completion/failure/abandon, remove the exact mission module instance.

### 4) Hard Mission Power Semantics (Required)

Define and persist exact timer behavior:

- 90 game-days must be **consecutive** with positive net electric surplus.
- If power drops below threshold, timer resets.
- Persist `power_started_at` and reset events so state survives restarts.

### 5) Destination Eligibility and Validation (Required)

Use one shared eligibility function for both generation and completion checks to avoid drift:

- Exclude Earth/Luna local nodes (`LEO`, `HEO`, `GEO`, `LLO`, `HLO`, `L1`–`L5`, `LUNA_*`).
- Easy → orbit nodes only.
- Medium/Hard → surface sites only.
- Keep hard-tier weighting biased to outer system.

### 6) Routing and UI Integration Notes (Required)

- Register static routes (`/api/missions/history`, `/api/missions/active`) before dynamic `/api/missions/{id}` to avoid path capture.
- Add Missions link consistently to all top nav bars.
- Add Missions app entry to map dock/window config (`map_windows.js`), not just the HTML dock button.

### 7) Test Gates (Required Before Merge)

Minimum validation matrix:

1. Accept mission credits upfront once and blocks second active mission per org.
2. Concurrent accept attempts: only one succeeds.
3. Abandon/fail path applies anti-exploit policy correctly (clawback/collateral/penalty).
4. Completion succeeds when module is at destination in location inventory.
5. Completion succeeds when module is in a docked ship at destination.
6. Hard mission power timer resets on interruption and completes only after full consecutive duration.
7. Expiry settlement auto-fails overdue accepted missions and refreshes expired available pool entries.

---

## Open Questions / Future Considerations

1. **Partial power for hard missions** — If power is interrupted, does the 90-day timer reset or pause? Current design: **resets** (must be 90 consecutive days).
2. **Module tracking** — Should the active mission panel show where the module currently is? (Yes, via inventory search across location + ship cargo.)
3. **Multi-org competition** — Could two orgs race to complete different missions to the same destination? (Yes — missions are independent.)
4. **Scaling payouts** — As the economy matures, should mission payouts inflate? (Deferred — fixed for now.)
5. **Mission variety** — Future tiers could include multi-stop missions, sample return, crew deployment, etc.
