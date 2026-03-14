# Economy & Organizations

Reference for the organization system, income model, research teams, loans, LEO boosts, marketplace, tech research tree, prospecting, and the corporation auth model.

---

## Overview

Every player (or corporation) has an **organization** — the entity that owns money, research points, ships, equipment, and inventory. Organizations earn a fixed monthly income, spend money on research teams, LEO boosts, and loan repayments, and earn research points to unlock technology.

All economy accrual uses the **settle-on-access** pattern: nothing ticks in the background. Income, research points, and loan repayments are calculated lazily when any org-related API endpoint is called.

---

## Organizations

### Creation

Organizations are auto-created on first access:

| Actor | Starting balance | Starting RP | Org name |
|---|---|---|---|
| User (legacy) | $1,000,000,000 | 0 | `"{username}'s Organization"` |
| Corporation | $1,000,000,000 | 20 | Corporation's display name |

Corps start with 20 RP so they can unlock a first tech node immediately.

### Key Fields

| Column | Description |
|---|---|
| `balance_usd` | Current cash balance (can go negative) |
| `research_points` | Accumulated unspent RP |
| `last_settled_at` | Game-time of last settlement — used to compute accrued income/costs |
| `team_count` | Number of active research teams (denormalized for quick reads) |

---

## Settle-on-Access Pattern

`settle_org()` is called at the top of every route that touches organization data (`/api/org`, `/api/org/marketplace`, `/api/org/boost`, etc.). It computes everything that happened since the last settlement:

```
elapsed_s    = game_now_s() - last_settled_at
months       = elapsed_s / 2,592,000          (30 game-days per month)
weeks        = elapsed_s / 604,800            (7 game-days per week)

income       = months × $1,000,000,000
team_costs   = team_count × months × $150,000,000
rp_earned    = team_count × weeks × 5.0

new_balance  = old_balance + income - team_costs - loan_payments
new_rp       = old_research_points + rp_earned
```

Loan repayments are also processed during settlement (see Loans section below).

**Important:** Balance can go negative. There are no bankruptcy guards. If team costs + loan payments exceed income, the org will gradually lose money.

### Real-Time Equivalents

At the default 48× game speed:

| Game duration | Real-world equivalent |
|---|---|
| 1 game month (2,592,000 s) | 15 hours |
| 1 game week (604,800 s) | 3.5 hours |
| 1 game day (86,400 s) | 30 minutes |

---

## Income

Every organization earns **$1,000,000,000 per game month** (30 game-days). This is automatic and unconditional — no player action required beyond the org existing.

Income accrues continuously (proportional to elapsed time), not in discrete monthly payments. An org that hasn't been accessed for 2.5 months will receive $2,500,000,000 on next access.

---

## Research Teams

Research teams generate research points (RP) over time. They cost money to maintain.

| Stat | Value |
|---|---|
| Hire cost | $150,000,000 (first month prepaid on hire) |
| Monthly maintenance | $150,000,000 per team |
| RP generation | 5.0 RP per team per game-week |
| Fire refund | None |

### Hiring

`POST /api/org/hire-team` — Settles the org, checks balance ≥ $150M, creates the team, deducts $150M immediately.

### Firing

`POST /api/org/fire-team` — Deletes the team row outright. No refund. Ongoing costs stop immediately (effective at next settlement).

### RP Accrual

RP generation is computed during `settle_org()`: `team_count × (elapsed_s / 604,800) × 5.0`. Like income, it accrues continuously.

---

## Loans

Three loan products are available:

| Loan code | Principal | Term | APR | Total payable | Monthly payment |
|---|---|---|---|---|---|
| `loan_1b_1y_5pct` | $1 B | 12 months | 5% | $1.05 B | $87.5 M |
| `loan_3b_5y_11pct` | $3 B | 60 months | 11% | $3.33 B | $55.5 M |
| `loan_5b_10y_19pct` | $5 B | 120 months | 19% | $5.95 B | $49.58 M |

**Interest is simple**, not compound: `total_payable = principal × (1 + APR)`.

### Activation

`POST /api/org/loans/activate` with `{loan_code: "loan_1b_1y_5pct"}`:

1. Settles the org first.
2. Verifies the loan code is valid and not already active.
3. Inserts an `org_loans` row with `remaining_balance_usd = total_payable`.
4. Credits the **principal** (not total payable) to the org's balance.

### Repayment

Loan payments are deducted automatically during `settle_org()`:

```
payment_due     = months_elapsed × monthly_payment_usd
payment_applied = min(remaining_balance, payment_due)
remaining       = remaining_balance - payment_applied
```

If the remaining balance reaches 0, the loan is marked `status = 'paid_off'`.

---

## LEO Boost System

LEO boosts are how items get from Earth's surface to Low Earth Orbit. Players pay cash to "launch" resources or parts to LEO.

### Cost Formula

$$\text{cost} = \$100{,}000{,}000 + \$5{,}000 \times \text{mass\_kg}$$

### What Can Be Boosted

- **Resources:** Only water (identified by name containing "water"). No tech gate.
- **Parts:** Only items at tech levels {1, 1.5, 2, 2.5}. Must be from a tech node the org has unlocked. If the org's unlocked set would filter out everything, the full tech-level-gated list is shown instead (so boost options never vanish entirely).

### Boost Flow

`POST /api/org/boost` with `{items: [{item_id: "ntr_100", quantity: 3}, ...]}`:

1. Settles the org.
2. Normalizes the manifest (merges duplicate item IDs).
3. Validates all items are boostable.
4. Computes per-line mass (`mass_per_unit × quantity`) → total mass → total cost.
5. Checks balance ≥ cost.
6. Deducts cost, inserts `leo_boosts` ledger rows.
7. Adds items to LEO location inventory:
   - Resources → `add_resource_to_location_inventory`
   - Parts → `add_part_to_location_inventory` (catalog lookup for full part dict)

Cost is split proportionally by mass across line items for the ledger.

---

## Marketplace

Organizations can sell resources at market prices. Sales are restricted to the LEO location.

### Price Modifiers

Market prices fluctuate monthly with a **deterministic pseudo-random modifier**:

```
month_index = floor(game_now_s / 2,592,000)
hash        = SHA-256("{resource_id}::{month_index}")
modifier    = map first 8 bytes to float in [-0.25, +0.25]
market_price = base_price × (1.0 + modifier)
```

The same resource in the same game-month always produces the same price modifier. Different resources get different modifiers.

### Sellable Inventory

`GET /api/org/marketplace` returns:
- Market prices for all resources.
- Sellable inventory from the **LEO location** (filtered by corp ownership).
- Sellable inventory from **ships docked at LEO** (not in transit, owned by the corp).

### Selling

`POST /api/org/marketplace/sell` with `{source_kind, source_id, resource_id, mass_kg}`:

- `source_kind: "location"` — must be LEO. Consumes mass from location inventory.
- `source_kind: "ship"` — ship must be docked at LEO, not in transit, owned by the corp. Drains resource from ship containers.
- Proceeds = `sold_mass × market_price_per_kg`.
- Credits proceeds to org balance.

---

## Research / Tech Tree

### Tree Structure

The tech tree has **7 categories**, each a vertical chain of nodes by tech level:

- Thrusters
- Reactors
- Generators
- Radiators
- Robonauts
- Constructors
- Refineries (splits into 4 subtrees: Lithic, Metallurgy, Nuclear, Volatiles)

Node IDs follow the pattern `"{category}_lvl_{level}"` (e.g. `thrusters_lvl_1`, `refineries_lithic_lvl_2`). Each node requires the previous level in the same chain as a prerequisite.

### RP Costs by Tech Level

| Tech Level | RP Cost |
|---|---|
| 1 | 5 |
| 1.5 | 8 |
| 2 | 10 |
| 2.5 | 15 |
| 3 | 20 |
| 3.5 | 30 |
| 4 | 40 |
| 4.5 | 60 |

### Unlocking

`POST /api/org/research/unlock` with `{tech_id, cost, prerequisites}`:

1. Settles the org.
2. Verifies not already unlocked.
3. Validates all prerequisites are unlocked.
4. Checks `research_points ≥ cost`.
5. Deducts RP, records the unlock.

Unlocked techs gate:
- **LEO boosts** — only parts whose tech node is unlocked can be boosted.
- **Shipyard recipes** — construction recipes with `min_tech_tier > 0` require the corresponding research node.
- **Ship building** — the shipyard validates parts against research unlocks.

---

## Prospecting

Before mining at a surface site, an organization must **prospect** it to reveal available resources.

### Requirements

- Ship must have a **robonaut** with `prospect_range_km > 0`.
- Distance from the ship's location to the site's parent orbit node must be within robonaut range.

### Flow

`POST /api/org/prospecting/prospect` with `{ship_id, site_location_id}`:

1. Validates the ship has a robonaut.
2. Validates the target is a surface site.
3. Checks distance ≤ robonaut's prospect range.
4. Checks the org hasn't already prospected this site.
5. Copies `surface_site_resources` entries into `prospecting_results`.
6. Returns the resource distribution (resource IDs + mass fractions).

Prospecting results persist permanently — prospect once, mine forever.

### Related Endpoints

- `GET /api/org/prospecting/sites` — all sites this org has prospected.
- `GET /api/org/prospecting/in_range/{ship_id}` — surface sites within the ship's robonaut range, with prospected status.

---

## Corporation Auth Model

### Dual Session System

The game supports two types of authenticated actors:

| Actor type | Session table | Identity fields | How they register |
|---|---|---|---|
| **User** (admin) | `sessions` | `username`, `is_admin=1` | Pre-created or via admin panel |
| **Corporation** | `corp_sessions` | `corp_id`, `corp_name`, `corp_color` | `POST /api/auth/corp/register` |

`require_login()` checks both session tables. It first tries the `sessions` table (users), then `corp_sessions` (corps). The returned object duck-types as a dict with at minimum:
- `username` — set for users, `None` for corps
- `is_admin` — `1` for admin users, `0` for corps
- `corp_id` — set for corps, `None` for users

### Corp Registration

`POST /api/auth/corp/register` with `{name, password, color}`:
- Name: 2–40 chars, `[A-Za-z0-9 _-]`
- Password: ≥ 3 chars
- Name uniqueness: case-insensitive
- Auto-creates an organization with $1B + 20 RP
- Sets session cookie

### Password Hashing

`SHA256("earthmoon_auth_salt_v1:{name_lower}:{password}")`

### Online Status

- `POST /api/auth/heartbeat` — updates `corp_sessions.last_seen` timestamp.
- `GET /api/auth/online-corps` — returns corps with `last_seen` within the last 90 seconds.

### The `_get_org_id()` / `_get_corp_id()` Pattern

Every router that needs the current actor's organization uses a helper like:
```python
def _get_org_id(conn, user):
    if hasattr(user, "corp_id") and user.corp_id:
        return ensure_org_for_corp(conn, user.corp_id)
    return ensure_org_for_user(conn, user["username"])
```

The `corp_id` is also used as the ownership key for ships, deployed equipment, production jobs, and inventory stacks. All queries filter by `corp_id` to enforce ownership isolation.

---

## API Route Reference

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/org` | Full org state: balance, RP, teams, unlocks, prospected sites, loan offers |
| GET | `/api/org/loans` | Available loan products with active status |
| POST | `/api/org/loans/activate` | Take out a loan |
| POST | `/api/org/hire-team` | Hire a research team ($150M) |
| POST | `/api/org/fire-team` | Fire a research team |
| GET | `/api/org/boostable-items` | Items eligible for LEO boost |
| POST | `/api/org/boost-cost` | Calculate boost cost for a given mass |
| POST | `/api/org/boost` | Launch items to LEO |
| GET | `/api/org/boost-history` | Recent boost launches (limit 20) |
| GET | `/api/org/marketplace` | Market prices + sellable LEO inventory |
| POST | `/api/org/marketplace/sell` | Sell resources at market price |
| GET | `/api/org/research/unlocks` | List unlocked tech nodes |
| POST | `/api/org/research/unlock` | Unlock a tech node for RP |
| GET | `/api/org/prospecting/sites` | All prospected surface sites |
| GET | `/api/org/prospecting/in_range/{ship_id}` | Sites in robonaut range |
| POST | `/api/org/prospecting/prospect` | Prospect a surface site |

---

## Key Formulas Summary

| What | Formula |
|---|---|
| Monthly income | $1,000,000,000 per game-month (2,592,000 game-seconds) |
| Team maintenance | $150,000,000 per team per game-month |
| RP generation | 5.0 per team per game-week (604,800 game-seconds) |
| Loan total payable | principal × (1 + APR) |
| Loan monthly payment | total_payable / term_months |
| LEO boost cost | $100,000,000 + $5,000 × mass_kg |
| Market price | base_price × (1.0 + SHA256_modifier), modifier ∈ [-0.25, +0.25] |
| Game time scale | 48× real-time (configurable via `GAME_TIME_SCALE` env var) |
