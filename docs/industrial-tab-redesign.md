# Industrial Tab Redesign

Breaks the monolithic Industrial view into focused subtabs to reduce clutter and group related functions.

---

## Motivation

The current Industrial view packs everything into a single scrollable page: power balance, equipment deployment, miners, refineries, refinery slots, construction queue, production chain flow, and job history. As facilities grow in complexity this becomes overwhelming. Splitting into purpose-driven subtabs gives each concern room to breathe while keeping navigation fast.

---

## Subtab Structure

The Industrial tab gains a secondary tab bar with four subtabs:

| Subtab | Purpose |
|---|---|
| **Overview** | At-a-glance facility health: power/thermal balance, aggregate rates, mining output, job history |
| **Deployments** | Master equipment list — deploy, undeploy, per-equipment power/thermal breakdown |
| **Mining & Refining** | Miners, ISRU, refineries, and refinery slot management |
| **Printing** | Printers and a material-agnostic build queue |

The facility selector (location dropdown → facility grid → enter facility) remains unchanged above the subtab bar. Subtabs only appear once inside a facility.

---

## Subtab Detail

### 1. Overview

A read-only dashboard summarizing the facility's state.

**Power & Thermal Balance (summary)**
- Electric balance bar: total supply (reactors → generators) vs total demand. Surplus / deficit number.
- Thermal balance bar: total waste heat vs radiator rejection capacity. Surplus / deficit number.
- No per-equipment breakdown here (that moves to Deployments).

**Aggregate Rates**
- Total mining rate (kg/hr) across all active miners + ISRU.
- Total construction rate (kg/hr) across all active printers.
- Counts: N miners active, N refineries active, N printers active.

**Mining Output**
- The existing per-resource mining output summary table (resource, rate, accumulated).
- Only shown for surface sites with active mining jobs.

**Job History**
- The existing job history table, showing completed/cancelled jobs across all categories.
- Stays at the bottom of this tab as a facility-wide log.

---

### 2. Deployments

The master equipment management page. All deployed modules listed and grouped by type.

**Layout**
- Equipment grouped into collapsible sections by category:
  - **Power** — Reactors, Generators, Radiators
  - **Production** — Refineries, Printers
  - **Extraction** — Miners, ISRU
- Each section header shows count (e.g., "Miners (3)").

**Per-Equipment Row**
- Item icon + name + tech tier.
- Status badge (idle / active / unpowered).
- Key stat summary (e.g., electric draw, throughput, mining rate).
- **Power/thermal contribution**: Each equipment row shows its individual electric (MWe) and thermal (MWth / waste heat) impact. This is the detailed per-equipment breakdown that currently lives in the power panel.
- Undeploy button (disabled if equipment has an active job).

**Deploy Action**
- "Deploy Equipment" button opens the existing deploy modal (filtered by category, source from location inventory or docked ships).

**Per-Equipment Power Breakdown**
- Below or beside the equipment list, a summary table:
  - Reactors: thermal output per unit.
  - Generators: electric output, thermal input, waste heat per unit.
  - Radiators: heat rejection per unit.
  - Consumers: electric draw per miner/refinery/printer/ISRU.
- Totals row matching the Overview summary.

---

### 3. Mining & Refining

Focused on resource extraction and processing.

**Miners Section**
- Lists all deployed miners with:
  - Status (idle / mining).
  - Target resource + mining rate (kg/hr).
  - Start / Stop controls.
- "Start Mining" opens resource picker (filtered by site deposits + miner eligibility).

**ISRU Section**
- Lists deployed ISRU modules (robonauts / water ice extractors).
- Same start/stop pattern as miners.
- Shows target resource (water ice → water) and rate.

**Refineries Section**
- Lists deployed refineries with:
  - Specialization tag.
  - Current slot assignment status (N/M slots active).
  - Throughput multiplier, efficiency, max recipe tier.

**Refinery Slots**
- The existing priority-ordered slot list with drag-reorder.
- Each slot shows: priority, assigned recipe (or empty), active job progress bar, ETA.
- Assign / clear recipe via recipe picker modal (filtered by refinery specialization + tech unlocks).
- Auto-start behavior unchanged: when a slot finishes, the next queued recipe with available materials starts automatically.

---

### 4. Printing

Focused on module fabrication via printers.

**Printers Section**
- Lists all deployed printers with:
  - Status (idle / printing).
  - Current job (if any): recipe name, progress bar, ETA.
  - Print rate (kg/hr).

**Build Queue**
- A unified queue across all printers at the facility (pooled print speed, same as current construction queue).
- Drag-reorderable priority list.
- Each queue entry shows: recipe name, required materials (with availability indicator), progress (if active), ETA.

**Queue Enhancement: Material-Agnostic Queuing**
- Players can add recipes to the queue even when the required materials are not yet available.
- Queue entries show material requirements with missing amounts highlighted (red / grey).
- The system only starts fabricating a queue item when all its input materials are present in facility inventory.
- Items waiting on materials show a "Waiting for materials" status instead of blocking the queue — the queue skips to the next item that has materials available, or idles if none do.
- This lets players plan ahead: queue up a full production run and let it execute automatically as mining/refining delivers materials over time.

**Queue Entry States**
| State | Meaning |
|---|---|
| `queued` | Waiting in line, materials available |
| `waiting_materials` | Missing one or more inputs — skipped until materials arrive |
| `printing` | Currently being fabricated |
| `completed` | Finished, output delivered to facility inventory |
| `cancelled` | Removed by player |

---

## Navigation & UX Notes

- Subtab selection persists while switching between facilities at the same location (so you can compare Mining tabs across facilities without resetting to Overview each time).
- The facility header (name, location, corp) and the facility selector remain above the subtab bar and are always visible.
- Deep-linking: URL hash updates to reflect subtab (e.g., `#industrial/mining`) so browser back/forward works.
- Badge hints on subtab labels when noteworthy: e.g., "Printing (2 waiting)" if queue entries are blocked on materials.

---

## Current → New Mapping

What moves where from the existing single-page Industrial view:

| Current Element | New Home |
|---|---|
| Power & Thermal balance (compact bars) | **Overview** (summary only) |
| Power per-equipment breakdown | **Deployments** |
| Deploy Equipment button + modal | **Deployments** |
| Miners list + start/stop | **Mining & Refining** |
| ISRU / Robonauts | **Mining & Refining** |
| Refineries list | **Mining & Refining** |
| Refinery Slots (priority list) | **Mining & Refining** |
| Mining Output summary | **Overview** |
| Printers list | **Printing** |
| Construction Queue | **Printing** (now "Build Queue") |
| Production Chain Flow | *Removed* |
| Job History | **Overview** (bottom) |

---

## Backend Changes

### New queue state: `waiting_materials`

The construction queue currently only starts items when materials are present. The enhancement adds explicit queue states so the frontend can distinguish "waiting in line" from "waiting on materials."

**Schema change** — `construction_queue` table:
- Add column: `status TEXT NOT NULL DEFAULT 'queued'` with values: `queued`, `waiting_materials`, `printing`, `completed`, `cancelled`.

**Settlement logic change** — `_settle_construction_queue()`:
- When scanning the queue top-down for the next item to start, if an item's inputs aren't available, mark it `waiting_materials` and continue to the next item instead of stopping.
- When materials arrive (via mining settlement, refinery output, or inventory transfer), re-scan waiting items.

### New endpoint (optional, for badge counts)

`GET /api/industry/facility/{facility_id}/summary` — lightweight counts for subtab badges:
- `miners_active`, `refineries_active`, `printers_active`
- `queue_waiting_materials` count
- `power_ok` boolean

No other backend changes required — existing endpoints already return all the data needed, the restructuring is purely frontend.

---

## Existing Bug Audit — Issues to Fix Alongside Redesign

A code audit of the current industry and inventory systems surfaced several bugs ranging from critical to low severity. These should be addressed as part of (or before) the redesign work, since the new subtabs will surface the same underlying logic.

### CRITICAL: Settle-on-access race → resource duplication

**Files:** `industry_service.py` — `_settle_production_jobs()`, `_settle_refinery_slots()`, `_settle_construction_queue()`, `_settle_mining_v2()`

**Problem:** `settle_industry()` and its sub-functions have no transaction isolation. Two concurrent requests hitting the same facility both query active/completed jobs, both see the same finished job, both deliver outputs to inventory via additive `_upsert_inventory_stack` deltas, then both mark the job completed. Resources are delivered **twice**.

**Fix:** Wrap each settle sub-function in `BEGIN IMMEDIATE`. Re-query completable jobs inside the transaction so the second caller sees the already-updated state. Pattern:
```python
def _settle_production_jobs(conn, now, location_id, *, facility_id=None):
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute("SELECT ... WHERE status = 'active' AND completes_at <= ?", ...)
        # ... deliver outputs, mark completed ...
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
```

---

### CRITICAL: Inventory transfer TOCTOU → resource duplication

**File:** `inventory_router.py` — `api_inventory_transfer()`

**Problem:** `available_mass` is read ~60 lines before the `BEGIN IMMEDIATE` block. The withdrawal inside the transaction uses the stale `move_mass_kg` capped to the pre-transaction availability. If another thread consumed the resource between the read and the lock, `_upsert_inventory_stack` clamps the source to `max(0.0, ...)` preventing negative inventory — but the **target** still receives the full amount. Net effect: resources created from nothing.

**Fix:** Move all inventory reads (`_resource_stack_row`, `_load_ship_inventory_state`, `available_mass` calculation) inside the `BEGIN IMMEDIATE` block so the check and mutation are atomic.

---

### CRITICAL: Contract bidding race → negative org balance

**File:** `contract_router.py` — `_deduct_money()`

**Problem:** `_deduct_money()` does a `SELECT balance_usd` check, then an `UPDATE balance_usd = balance_usd - ?` decrement with no transaction wrapping. Two concurrent bids from the same org can both pass the balance check and both deduct, driving the balance negative.

**Fix:** Wrap `_deduct_money` in `BEGIN IMMEDIATE`:
```python
def _deduct_money(conn, org_id, amount):
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        bal = _org_balance(conn, org_id)
        if bal < amount:
            conn.rollback()
            raise HTTPException(400, "Insufficient funds")
        conn.execute("UPDATE organizations SET balance_usd = balance_usd - ? WHERE id = ?", (amount, org_id))
        conn.commit()
    except HTTPException:
        raise
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
```

---

### HIGH: Market sell — resource consumed, payment lost on error

**File:** `org_service.py` — `sell_market_resource()`

**Problem:** `_consume_location_resource_mass()` runs first, then the org balance UPDATE follows, with a single `conn.commit()` at the end. If any exception fires between consumption and the balance update, the resource is gone but the org never gets paid.

**Fix:** Wrap the entire consume-and-pay sequence in a `BEGIN IMMEDIATE` / rollback block.

---

### HIGH: LEO boost — money deducted, partial items delivered

**File:** `org_service.py` — `boost_manifest_to_leo()`

**Problem:** The full cost is deducted first, then items are inserted in a loop. If the loop fails on iteration 3 of 5, the org paid for 5 items but only received 2. No transaction wrapper with rollback.

**Fix:** Wrap the entire deduction + item insertion loop in a single `BEGIN IMMEDIATE` / rollback block.

---

### HIGH: Clock reset causes double-mining

**File:** `industry_service.py` — `_settle_mining_v2()`

**Problem:** When an admin resets the game clock backward, `elapsed_s` is clamped to 0 (safe), but `mining_last_settled` is updated to the new earlier time. When the clock advances past the original time again, the full period from the reset point to the original time gets mined a second time.

**Fix:** Skip updating `mining_last_settled` when `elapsed_s == 0` (i.e., when time went backward):
```python
elapsed_s = max(0.0, now - last_settled)
if elapsed_s <= 0.0:
    continue  # Don't update last_settled on backward time
```

---

### MEDIUM: Ship inventory context leaks cargo to other corps

**File:** `inventory_router.py` — `api_inventory_context()`

**Problem:** When `kind=ship`, the endpoint loads any ship's full inventory state without calling `_check_ship_ownership()`. Other endpoints (e.g., `api_stack_context_ship`) do check ownership. A player can view (but not modify) any ship's cargo by guessing the ship UUID.

**Fix:** Add `_check_ship_ownership(conn, user, inv_id)` before loading ship state.

---

### MEDIUM: Facility owner guard bypassed for null-corp facilities

**File:** `facility_service.py` — `require_facility_owner()`

**Problem:** The guard `if corp_id and fac["corp_id"] and corp_id != fac["corp_id"]` skips authorization when either side is falsy. Any facility with `corp_id = NULL` is accessible to all users.

**Fix:** Tighten the guard:
```python
if fac["corp_id"] is None or not corp_id or corp_id != fac["corp_id"]:
    raise HTTPException(403, "You do not own this facility")
```

---

### LOW: No CHECK constraints on inventory quantities

**File:** `db_migrations.py` — `location_inventory_stacks` schema

**Problem:** `quantity`, `mass_kg`, and `volume_m3` columns have no `CHECK(col >= 0)` constraints. The application layer uses `max(0.0, ...)` clamping, but the DB can't independently prevent negative values if a code path bypasses the clamp.

**Fix:** Add a migration with CHECK constraints:
```sql
-- Cannot ALTER existing columns in SQLite; would need table rebuild or
-- add a trigger-based guard:
CREATE TRIGGER IF NOT EXISTS trg_lis_no_negative
BEFORE UPDATE ON location_inventory_stacks
FOR EACH ROW WHEN NEW.quantity < 0 OR NEW.mass_kg < 0
BEGIN
    SELECT RAISE(ABORT, 'Negative inventory quantity');
END;
```

---

### Summary Table

| # | Issue | Severity | File(s) | Fix Effort |
|---|---|---|---|---|
| 1 | Settle race → duplicate resources | **CRITICAL** | `industry_service.py` | Medium |
| 2 | Transfer TOCTOU → duplication | **CRITICAL** | `inventory_router.py` | Low |
| 3 | Bidding race → negative balance | **CRITICAL** | `contract_router.py` | Low |
| 4 | Market sell partial failure | **HIGH** | `org_service.py` | Low |
| 5 | LEO boost partial failure | **HIGH** | `org_service.py` | Low |
| 6 | Clock reset double-mining | **HIGH** | `industry_service.py` | Low |
| 7 | Ship inventory context leak | **MEDIUM** | `inventory_router.py` | Low |
| 8 | Null-corp facility bypass | **MEDIUM** | `facility_service.py` | Low |
| 9 | No DB-level quantity guards | **LOW** | `db_migrations.py` | Low |

Issues 1–3 are exploitable in a multi-user environment and should be fixed before or alongside the subtab frontend work. Issues 4–6 are triggered by errors or admin actions — lower likelihood but still data-corrupting. Issues 7–9 are hardening.

---

## Resolved Questions

- **Repeat queuing:** Build Queue supports "repeat N times" per recipe entry.
- **Completed items:** Auto-clear from the queue on completion.
- **ETA estimation:** When printers are idle waiting on materials, the UI shows an estimated time based on current mining/refining inflow rates.
