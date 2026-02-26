"""
Organization service — business logic for org finances, research teams, LEO boosts, and prospecting.

Economy model:
  - Each org earns $1,000,000,000 (1B) per 30-day game month
  - Research teams cost $150,000,000/month, generate 5 research points per 7-day game week
  - LEO boosts cost $100,000,000 base + $5,000 per kg payload
  - Only tech-level 1 and 2 items + water can be boosted from Earth

Settle-on-access pattern: income and research point generation are calculated
from elapsed game time since last settlement, not from a background tick.
"""

import json
import hashlib
import math
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Tuple

import catalog_service
from sim_service import game_now_s

# ── Constants ──────────────────────────────────────────────────────────────────

MONTHLY_INCOME_USD = 1_000_000_000.0  # $1B per game month
GAME_MONTH_SECONDS = 30.0 * 24.0 * 3600.0  # 30 game days
GAME_WEEK_SECONDS = 7.0 * 24.0 * 3600.0  # 7 game days

RESEARCH_TEAM_COST_PER_MONTH = 150_000_000.0  # $150M
RESEARCH_TEAM_POINTS_PER_WEEK = 5.0

LEO_BOOST_BASE_COST = 100_000_000.0  # $100M
LEO_BOOST_COST_PER_KG = 5_000.0  # $5,000/kg
MARKET_MONTHLY_MODIFIER_ABS_MAX = 0.25  # ±25%

LOAN_PRODUCTS: List[Dict[str, Any]] = [
    {
        "loan_code": "loan_1b_1y_5pct",
        "principal_usd": 1_000_000_000.0,
        "term_months": 12,
        "annual_interest_rate": 0.05,
    },
    {
        "loan_code": "loan_3b_5y_11pct",
        "principal_usd": 3_000_000_000.0,
        "term_months": 60,
        "annual_interest_rate": 0.11,
    },
    {
        "loan_code": "loan_5b_10y_19pct",
        "principal_usd": 5_000_000_000.0,
        "term_months": 120,
        "annual_interest_rate": 0.19,
    },
]

# Tech levels that can be boosted from Earth
BOOSTABLE_TECH_LEVELS = {1, 1.5, 2, 2.5}

# LEO destination location — resolved at startup
LEO_LOCATION_ID = "LEO"

# Refinery branch → tech-tree subtree mapping (mirrors catalog_service)
_REFINERY_BRANCH_TO_SUB = {
    "lithic_processing": "refineries_lithic",
    "metallurgy": "refineries_metallurgy",
    "nuclear_exotic": "refineries_nuclear",
    "volatiles_cryogenics": "refineries_volatiles",
}


def _tech_node_id_for_item(loader_name: str, tech_level: float, branch: str = "") -> Optional[str]:
    """Compute the tech-tree node ID that gates a catalog item.

    Items not on the tech tree (resources, storage) return None.
    """
    _LOADER_TO_TREE_PREFIX = {
        "thruster": "thrusters",
        "reactor": "reactors",
        "generator": "generators",
        "radiator": "radiators",
        "constructor": "constructors",
        "robonaut": "robonauts",
        "refinery": None,  # handled via branch lookup
    }
    prefix = _LOADER_TO_TREE_PREFIX.get(loader_name)
    if prefix is None and loader_name == "refinery":
        prefix = _REFINERY_BRANCH_TO_SUB.get(branch, "refineries")
    if prefix is None:
        return None  # storage / resource — no tech gate
    lvl_str = str(int(tech_level)) if tech_level == int(tech_level) else str(tech_level)
    return f"{prefix}_lvl_{lvl_str}"


def _main():
    """Lazy import to avoid circular dependency with main.py."""
    import main
    return main


def _resolve_leo_location_id(conn: sqlite3.Connection) -> str:
    exact = conn.execute("SELECT id FROM locations WHERE id='LEO' LIMIT 1").fetchone()
    if exact:
        return str(exact["id"])
    leo_loc = conn.execute(
        "SELECT id FROM locations WHERE id LIKE '%LEO%' OR id LIKE '%leo%' LIMIT 1"
    ).fetchone()
    return str(leo_loc["id"]) if leo_loc else LEO_LOCATION_ID


def _game_month_index(now_s: Optional[float] = None) -> int:
    current_s = float(game_now_s() if now_s is None else now_s)
    return int(max(0.0, current_s) // GAME_MONTH_SECONDS)


def _market_monthly_multiplier(resource_id: str, month_index: int) -> float:
    token = f"{str(resource_id).strip().lower()}::{int(month_index)}".encode("utf-8")
    digest = hashlib.sha256(token).digest()
    raw = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
    signed = (raw * 2.0) - 1.0
    modifier = signed * MARKET_MONTHLY_MODIFIER_ABS_MAX
    return 1.0 + modifier


def _build_market_prices(now_s: Optional[float] = None) -> Dict[str, Any]:
    month_index = _game_month_index(now_s)
    resources = catalog_service.load_resource_catalog()
    rows: List[Dict[str, Any]] = []
    for resource_id, resource in resources.items():
        rid = str(resource_id)
        base_price = max(0.0, float(resource.get("price_per_kg") or 0.0))
        multiplier = _market_monthly_multiplier(rid, month_index)
        modifier_pct = (multiplier - 1.0) * 100.0
        adjusted = max(0.0, base_price * multiplier)
        rows.append(
            {
                "resource_id": rid,
                "resource_name": str(resource.get("name") or rid),
                "base_price_per_kg": base_price,
                "modifier_pct": modifier_pct,
                "market_price_per_kg": adjusted,
                "category_id": str(resource.get("category_id") or ""),
            }
        )
    rows.sort(key=lambda r: (str(r["resource_name"]).lower(), str(r["resource_id"]).lower()))
    return {
        "month_index": month_index,
        "prices": rows,
        "price_by_resource": {str(r["resource_id"]): float(r["market_price_per_kg"]) for r in rows},
    }


def _consume_ship_resource_mass(conn: sqlite3.Connection, ship_id: str, resource_id: str, mass_kg: float) -> float:
    m = _main()
    ship_state = m._load_ship_inventory_state(conn, ship_id)
    source_parts = list(ship_state.get("parts") or [])
    source_containers = list(ship_state.get("containers") or [])

    rid = str(resource_id or "").strip()
    if not rid:
        return 0.0

    resource_meta = catalog_service.load_resource_catalog().get(rid) or {}
    fallback_density = max(0.0, float(resource_meta.get("mass_per_m3_kg") or 0.0))

    remaining_to_take = max(0.0, float(mass_kg or 0.0))
    consumed_mass = 0.0

    for container in source_containers:
        if remaining_to_take <= 1e-9:
            break

        manifest = list(container.get("cargo_manifest") or [])
        entry = next((e for e in manifest if str(e.get("resource_id") or "") == rid), None)
        if not entry:
            continue

        entry_mass = max(0.0, float(entry.get("mass_kg") or 0.0))
        if entry_mass <= 1e-9:
            continue

        raw_idx = container.get("container_index")
        part_idx = int(raw_idx) if raw_idx is not None else -1
        if part_idx < 0 or part_idx >= len(source_parts):
            continue

        density = max(0.0, float(entry.get("density_kg_m3") or 0.0))
        if density <= 0.0:
            density = fallback_density
        if density <= 0.0:
            density = 1.0

        entry_vol = max(0.0, float(entry.get("volume_m3") or 0.0))
        take_mass = min(entry_mass, remaining_to_take)
        next_mass = max(0.0, entry_mass - take_mass)
        next_vol = max(0.0, entry_vol - (take_mass / density))

        source_parts[part_idx] = m._apply_ship_container_fill(
            source_parts[part_idx],
            resource_id=rid,
            cargo_mass_kg=next_mass,
            used_m3=next_vol,
            density_kg_m3=density,
        )
        remaining_to_take -= take_mass
        consumed_mass += take_mass

    if consumed_mass <= 1e-9:
        return 0.0

    source_fuel_kg = max(0.0, float(ship_state.get("fuel_kg") or 0.0))
    if rid.lower() == "water":
        source_fuel_kg = max(0.0, source_fuel_kg - consumed_mass)

    m._persist_ship_inventory_state(
        conn,
        ship_id=str(ship_state["row"]["id"]),
        parts=source_parts,
        fuel_kg=source_fuel_kg,
    )
    return consumed_mass


def get_marketplace_snapshot(conn: sqlite3.Connection, org_id: str, *, corp_id: str = "") -> Dict[str, Any]:
    settle_org(conn, org_id)

    market = _build_market_prices()
    price_by_resource = dict(market["price_by_resource"])
    leo_location_id = _resolve_leo_location_id(conn)
    resources = catalog_service.load_resource_catalog()
    corp_scope = str(corp_id or "")

    sellable: List[Dict[str, Any]] = []

    location_rows = conn.execute(
        """
        SELECT item_id, name, mass_kg
        FROM location_inventory_stacks
        WHERE location_id = ? AND corp_id = ? AND stack_type = 'resource' AND mass_kg > 0
        ORDER BY name, item_id
        """,
        (leo_location_id, corp_scope),
    ).fetchall()

    for row in location_rows:
        resource_id = str(row["item_id"] or "").strip()
        if not resource_id:
            continue
        available_mass = max(0.0, float(row["mass_kg"] or 0.0))
        if available_mass <= 1e-9:
            continue
        unit_price = max(0.0, float(price_by_resource.get(resource_id) or 0.0))
        resource_name = str((resources.get(resource_id) or {}).get("name") or row["name"] or resource_id)
        sellable.append(
            {
                "source_kind": "location",
                "source_id": leo_location_id,
                "source_name": "Low Earth Orbit",
                "resource_id": resource_id,
                "resource_name": resource_name,
                "available_mass_kg": available_mass,
                "unit_price_usd_per_kg": unit_price,
                "estimated_value_usd": available_mass * unit_price,
            }
        )

    if corp_scope:
        ship_rows = conn.execute(
            """
            SELECT id, name
            FROM ships
            WHERE location_id = ? AND arrives_at IS NULL AND corp_id = ?
            ORDER BY name, id
            """,
            (leo_location_id, corp_scope),
        ).fetchall()
    else:
        ship_rows = conn.execute(
            """
            SELECT id, name
            FROM ships
            WHERE location_id = ? AND arrives_at IS NULL
            ORDER BY name, id
            """,
            (leo_location_id,),
        ).fetchall()

    for ship in ship_rows:
        ship_id = str(ship["id"])
        ship_name = str(ship["name"])
        ship_state = _main()._load_ship_inventory_state(conn, ship_id)
        for item in ship_state.get("resources") or []:
            resource_id = str(item.get("resource_id") or item.get("item_id") or "").strip()
            if not resource_id:
                continue
            available_mass = max(0.0, float(item.get("mass_kg") or 0.0))
            if available_mass <= 1e-9:
                continue
            unit_price = max(0.0, float(price_by_resource.get(resource_id) or 0.0))
            resource_name = str((resources.get(resource_id) or {}).get("name") or item.get("label") or resource_id)
            sellable.append(
                {
                    "source_kind": "ship",
                    "source_id": ship_id,
                    "source_name": ship_name,
                    "resource_id": resource_id,
                    "resource_name": resource_name,
                    "available_mass_kg": available_mass,
                    "unit_price_usd_per_kg": unit_price,
                    "estimated_value_usd": available_mass * unit_price,
                }
            )

    sellable.sort(
        key=lambda row: (
            str(row.get("resource_name") or "").lower(),
            str(row.get("source_kind") or ""),
            str(row.get("source_name") or "").lower(),
        )
    )

    return {
        "month_index": int(market["month_index"]),
        "leo_location_id": leo_location_id,
        "prices": market["prices"],
        "sellable": sellable,
    }


def sell_market_resource(
    conn: sqlite3.Connection,
    org_id: str,
    *,
    source_kind: str,
    source_id: str,
    resource_id: str,
    mass_kg: float,
    corp_id: str = "",
) -> Dict[str, Any]:
    settle_org(conn, org_id)

    src_kind = str(source_kind or "").strip().lower()
    if src_kind not in {"location", "ship"}:
        raise ValueError("source_kind must be 'location' or 'ship'")

    rid = str(resource_id or "").strip()
    if not rid:
        raise ValueError("resource_id is required")

    requested_mass = max(0.0, float(mass_kg or 0.0))
    if requested_mass <= 1e-9:
        raise ValueError("mass_kg must be greater than 0")

    resources = catalog_service.load_resource_catalog()
    if rid not in resources:
        raise ValueError("Unknown resource")

    market = _build_market_prices()
    unit_price = max(0.0, float(market["price_by_resource"].get(rid) or 0.0))
    if unit_price <= 0.0:
        raise ValueError("This resource has no market price")

    leo_location_id = _resolve_leo_location_id(conn)
    corp_scope = str(corp_id or "")

    sold_mass = 0.0
    sold_from_name = ""

    if src_kind == "location":
        src = str(source_id or "").strip() or leo_location_id
        if src != leo_location_id:
            raise ValueError("Location sales are only allowed from Low Earth Orbit")

        stack_row = _main()._resource_stack_row(conn, leo_location_id, rid, corp_id=corp_scope)
        available_mass = max(0.0, float(stack_row["mass_kg"] or 0.0))
        sold_mass = min(requested_mass, available_mass)
        if sold_mass <= 1e-9:
            raise ValueError("No sellable mass available at LEO")
        _main()._consume_location_resource_mass(conn, stack_row, sold_mass)
        sold_from_name = "Low Earth Orbit"
    else:
        ship_id = str(source_id or "").strip()
        if not ship_id:
            raise ValueError("source_id is required for ship sales")

        ship_row = conn.execute(
            "SELECT id, name, location_id, arrives_at, corp_id FROM ships WHERE id = ?",
            (ship_id,),
        ).fetchone()
        if not ship_row:
            raise ValueError("Ship not found")
        if str(ship_row["location_id"] or "") != leo_location_id or ship_row["arrives_at"] is not None:
            raise ValueError("Ship sales are only allowed for docked ships in Low Earth Orbit")
        if corp_scope and str(ship_row["corp_id"] or "") != corp_scope:
            raise ValueError("This ship belongs to another corporation")

        sold_mass = _consume_ship_resource_mass(conn, ship_id, rid, requested_mass)
        if sold_mass <= 1e-9:
            raise ValueError("Ship has no sellable mass for that resource")
        sold_from_name = str(ship_row["name"])

    proceeds_usd = sold_mass * unit_price
    conn.execute(
        "UPDATE organizations SET balance_usd = balance_usd + ? WHERE id = ?",
        (proceeds_usd, org_id),
    )
    org_row = conn.execute("SELECT balance_usd FROM organizations WHERE id = ?", (org_id,)).fetchone()
    new_balance = float(org_row["balance_usd"] or 0.0) if org_row else 0.0

    conn.commit()
    return {
        "source_kind": src_kind,
        "source_id": source_id,
        "source_name": sold_from_name,
        "resource_id": rid,
        "resource_name": str((resources.get(rid) or {}).get("name") or rid),
        "sold_mass_kg": sold_mass,
        "unit_price_usd_per_kg": unit_price,
        "proceeds_usd": proceeds_usd,
        "new_balance_usd": new_balance,
        "month_index": int(market["month_index"]),
    }

# ── Org Creation / Lookup ─────────────────────────────────────────────────────


def ensure_org_for_user(conn: sqlite3.Connection, username: str) -> str:
    """Ensure the user has an org. Create one if not. Returns org_id."""
    row = conn.execute(
        "SELECT org_id FROM org_members WHERE username = ?", (username,)
    ).fetchone()
    if row:
        return str(row["org_id"])

    # Create a new personal org
    org_id = str(uuid.uuid4())
    now = game_now_s()
    conn.execute(
        """INSERT INTO organizations (id, name, balance_usd, research_points, last_settled_at, created_at)
           VALUES (?, ?, ?, 0.0, ?, ?)""",
        (org_id, f"{username}'s Organization", MONTHLY_INCOME_USD, now, now),
    )
    conn.execute(
        "INSERT INTO org_members (username, org_id) VALUES (?, ?)",
        (username, org_id),
    )
    conn.commit()
    return org_id


def create_org_for_corp(conn: sqlite3.Connection, corp_id: str, corp_name: str) -> str:
    """Create an organization linked to a corporation. Returns org_id."""
    org_id = str(uuid.uuid4())
    now = game_now_s()
    conn.execute(
        """INSERT INTO organizations (id, name, balance_usd, research_points, last_settled_at, created_at)
           VALUES (?, ?, ?, 20.0, ?, ?)""",
        (org_id, f"{corp_name}", MONTHLY_INCOME_USD, now, now),
    )
    return org_id


def get_org_id_for_corp(conn: sqlite3.Connection, corp_id: str) -> Optional[str]:
    """Get the org_id linked to a corporation."""
    row = conn.execute(
        "SELECT org_id FROM corporations WHERE id = ?", (corp_id,)
    ).fetchone()
    return str(row["org_id"]) if row and row["org_id"] else None


def ensure_org_for_corp(conn: sqlite3.Connection, corp_id: str) -> str:
    """Get or create org for a corp. Returns org_id."""
    org_id = get_org_id_for_corp(conn, corp_id)
    if org_id:
        return org_id
    # Shouldn't normally happen — org is created at registration
    row = conn.execute("SELECT name FROM corporations WHERE id = ?", (corp_id,)).fetchone()
    corp_name = str(row["name"]) if row else corp_id
    org_id = create_org_for_corp(conn, corp_id, corp_name)
    conn.execute("UPDATE corporations SET org_id = ? WHERE id = ?", (org_id, corp_id))
    conn.commit()
    return org_id


def get_org_id_for_user(conn: sqlite3.Connection, username: str) -> Optional[str]:
    row = conn.execute(
        "SELECT org_id FROM org_members WHERE username = ?", (username,)
    ).fetchone()
    return str(row["org_id"]) if row else None


# ── Settlement (on-access income + research accrual) ──────────────────────────


def settle_org(conn: sqlite3.Connection, org_id: str) -> Dict[str, Any]:
    """
    Settle accumulated income and research points for an org.
    Returns the updated org state.
    """
    now = game_now_s()
    org = conn.execute(
        "SELECT * FROM organizations WHERE id = ?", (org_id,)
    ).fetchone()
    if not org:
        return {}

    last_settled = float(org["last_settled_at"] or 0.0)
    elapsed_s = max(0.0, now - last_settled)

    if elapsed_s <= 0:
        return _org_to_dict(org, conn)

    # Monthly income accrual
    months_elapsed = elapsed_s / GAME_MONTH_SECONDS
    income = months_elapsed * MONTHLY_INCOME_USD

    # Research team point + cost accrual
    active_teams = conn.execute(
        "SELECT COUNT(*) as cnt FROM research_teams WHERE org_id = ? AND status = 'active'",
        (org_id,),
    ).fetchone()
    team_count = int(active_teams["cnt"]) if active_teams else 0

    team_costs = team_count * RESEARCH_TEAM_COST_PER_MONTH * months_elapsed
    weeks_elapsed = elapsed_s / GAME_WEEK_SECONDS
    research_gained = team_count * RESEARCH_TEAM_POINTS_PER_WEEK * weeks_elapsed

    # Loan repayment accrual
    active_loans = conn.execute(
        """SELECT id, remaining_balance_usd, monthly_payment_usd
           FROM org_loans
           WHERE org_id = ? AND status = 'active'""",
        (org_id,),
    ).fetchall()

    loan_payments_total = 0.0
    for loan in active_loans:
        monthly_payment = float(loan["monthly_payment_usd"] or 0.0)
        remaining = float(loan["remaining_balance_usd"] or 0.0)
        if remaining <= 0.0 or monthly_payment <= 0.0:
            continue
        payment_due = months_elapsed * monthly_payment
        payment_applied = min(remaining, payment_due)
        new_remaining = max(0.0, remaining - payment_applied)
        loan_payments_total += payment_applied

        if new_remaining <= 0.0:
            conn.execute(
                """UPDATE org_loans
                   SET remaining_balance_usd = 0.0, status = 'paid_off', paid_off_at = ?
                   WHERE id = ?""",
                (now, str(loan["id"])),
            )
        else:
            conn.execute(
                "UPDATE org_loans SET remaining_balance_usd = ? WHERE id = ?",
                (new_remaining, str(loan["id"])),
            )

    new_balance = float(org["balance_usd"]) + income - team_costs - loan_payments_total
    new_research = float(org["research_points"]) + research_gained

    conn.execute(
        """UPDATE organizations
           SET balance_usd = ?, research_points = ?, last_settled_at = ?
           WHERE id = ?""",
        (new_balance, new_research, now, org_id),
    )
    conn.commit()

    # Re-fetch after update
    org = conn.execute(
        "SELECT * FROM organizations WHERE id = ?", (org_id,)
    ).fetchone()
    return _org_to_dict(org, conn)


def _org_to_dict(org: sqlite3.Row, conn: sqlite3.Connection) -> Dict[str, Any]:
    org_id = str(org["id"])
    teams = conn.execute(
        "SELECT id, hired_at, cost_per_month_usd, points_per_week, status FROM research_teams WHERE org_id = ?",
        (org_id,),
    ).fetchall()

    members = conn.execute(
        "SELECT username FROM org_members WHERE org_id = ?", (org_id,)
    ).fetchall()

    active_loans = conn.execute(
        """SELECT id, loan_code, principal_usd, annual_interest_rate, term_months,
                  total_payable_usd, monthly_payment_usd, remaining_balance_usd,
                  started_at, paid_off_at, status
           FROM org_loans
           WHERE org_id = ? AND status = 'active'
           ORDER BY started_at ASC""",
        (org_id,),
    ).fetchall()

    monthly_loan_payments = sum(float(loan["monthly_payment_usd"] or 0.0) for loan in active_loans)
    active_team_count = sum(1 for t in teams if str(t["status"]) == "active")
    monthly_team_expenses = active_team_count * RESEARCH_TEAM_COST_PER_MONTH

    return {
        "id": org_id,
        "name": str(org["name"]),
        "balance_usd": float(org["balance_usd"]),
        "research_points": float(org["research_points"]),
        "last_settled_at": float(org["last_settled_at"]),
        "created_at": float(org["created_at"]),
        "members": [str(m["username"]) for m in members],
        "research_teams": [
            {
                "id": str(t["id"]),
                "hired_at": float(t["hired_at"]),
                "cost_per_month_usd": float(t["cost_per_month_usd"]),
                "points_per_week": float(t["points_per_week"]),
                "status": str(t["status"]),
            }
            for t in teams
        ],
        "active_loans": [
            {
                "id": str(loan["id"]),
                "loan_code": str(loan["loan_code"]),
                "principal_usd": float(loan["principal_usd"]),
                "annual_interest_rate": float(loan["annual_interest_rate"]),
                "term_months": int(loan["term_months"]),
                "total_payable_usd": float(loan["total_payable_usd"]),
                "monthly_payment_usd": float(loan["monthly_payment_usd"]),
                "remaining_balance_usd": float(loan["remaining_balance_usd"]),
                "started_at": float(loan["started_at"]),
                "paid_off_at": float(loan["paid_off_at"] or 0.0),
                "status": str(loan["status"]),
            }
            for loan in active_loans
        ],
        "income_per_month_usd": MONTHLY_INCOME_USD,
        "team_cost_per_month_usd": RESEARCH_TEAM_COST_PER_MONTH,
        "team_points_per_week": RESEARCH_TEAM_POINTS_PER_WEEK,
        "monthly_loan_payments_usd": monthly_loan_payments,
        "monthly_expenses_usd": monthly_team_expenses + monthly_loan_payments,
    }


def _loan_product_map() -> Dict[str, Dict[str, Any]]:
    return {str(offer["loan_code"]): dict(offer) for offer in LOAN_PRODUCTS}


def _loan_totals(principal_usd: float, annual_interest_rate: float) -> Tuple[float, float]:
    total_payable = principal_usd * (1.0 + annual_interest_rate)
    total_interest = total_payable - principal_usd
    return total_payable, total_interest


def list_loan_offers_with_status(conn: sqlite3.Connection, org_id: str) -> List[Dict[str, Any]]:
    active_rows = conn.execute(
        """SELECT loan_code, id, principal_usd, annual_interest_rate, term_months,
                  total_payable_usd, monthly_payment_usd, remaining_balance_usd,
                  started_at, paid_off_at, status
           FROM org_loans
           WHERE org_id = ? AND status = 'active'""",
        (org_id,),
    ).fetchall()
    active_by_code = {str(r["loan_code"]): r for r in active_rows}

    offers: List[Dict[str, Any]] = []
    for offer in LOAN_PRODUCTS:
        loan_code = str(offer["loan_code"])
        principal = float(offer["principal_usd"])
        apr = float(offer["annual_interest_rate"])
        term_months = int(offer["term_months"])
        total_payable, total_interest = _loan_totals(principal, apr)
        monthly_payment = total_payable / max(1, term_months)

        active = active_by_code.get(loan_code)
        if active:
            remaining = float(active["remaining_balance_usd"] or 0.0)
            tracker = {
                "loan_id": str(active["id"]),
                "remaining_balance_usd": remaining,
                "remaining_percent": 0.0 if total_payable <= 0 else max(0.0, min(1.0, remaining / total_payable)),
                "started_at": float(active["started_at"] or 0.0),
                "status": str(active["status"]),
            }
            is_active = True
        else:
            tracker = None
            is_active = False

        offers.append(
            {
                "loan_code": loan_code,
                "principal_usd": principal,
                "annual_interest_rate": apr,
                "term_months": term_months,
                "term_years": term_months // 12,
                "total_interest_usd": total_interest,
                "total_payable_usd": total_payable,
                "monthly_payment_usd": monthly_payment,
                "can_activate": not is_active,
                "is_active": is_active,
                "tracker": tracker,
            }
        )
    return offers


def activate_loan(conn: sqlite3.Connection, org_id: str, loan_code: str) -> Dict[str, Any]:
    settle_org(conn, org_id)

    offer_map = _loan_product_map()
    loan_product = offer_map.get(loan_code)
    if not loan_product:
        raise ValueError("Unknown loan option")

    already_active = conn.execute(
        "SELECT id FROM org_loans WHERE org_id = ? AND loan_code = ? AND status = 'active'",
        (org_id, loan_code),
    ).fetchone()
    if already_active:
        raise ValueError("This loan is already active and must be paid off before reactivation")

    principal = float(loan_product["principal_usd"])
    annual_interest_rate = float(loan_product["annual_interest_rate"])
    term_months = int(loan_product["term_months"])
    total_payable, _ = _loan_totals(principal, annual_interest_rate)
    monthly_payment = total_payable / max(1, term_months)

    loan_id = str(uuid.uuid4())
    now = game_now_s()

    conn.execute(
        """INSERT INTO org_loans
           (id, org_id, loan_code, principal_usd, annual_interest_rate, term_months,
            total_payable_usd, monthly_payment_usd, remaining_balance_usd, status, started_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
        (
            loan_id,
            org_id,
            loan_code,
            principal,
            annual_interest_rate,
            term_months,
            total_payable,
            monthly_payment,
            total_payable,
            now,
        ),
    )
    conn.execute(
        "UPDATE organizations SET balance_usd = balance_usd + ? WHERE id = ?",
        (principal, org_id),
    )
    conn.commit()

    return {
        "loan_id": loan_id,
        "loan_code": loan_code,
        "principal_usd": principal,
        "term_months": term_months,
        "annual_interest_rate": annual_interest_rate,
        "monthly_payment_usd": monthly_payment,
        "total_payable_usd": total_payable,
        "remaining_balance_usd": total_payable,
        "started_at": now,
    }


# ── Research Teams ─────────────────────────────────────────────────────────────


def hire_research_team(conn: sqlite3.Connection, org_id: str) -> Dict[str, Any]:
    """Hire a new research team. Deducts first month's cost immediately."""
    # Settle first to get current balance
    settle_org(conn, org_id)
    org = conn.execute("SELECT balance_usd FROM organizations WHERE id = ?", (org_id,)).fetchone()
    if not org:
        raise ValueError("Organization not found")

    balance = float(org["balance_usd"])
    if balance < RESEARCH_TEAM_COST_PER_MONTH:
        raise ValueError(f"Insufficient funds. Need ${RESEARCH_TEAM_COST_PER_MONTH:,.0f}, have ${balance:,.0f}")

    team_id = str(uuid.uuid4())
    now = game_now_s()
    conn.execute(
        """INSERT INTO research_teams (id, org_id, hired_at, cost_per_month_usd, points_per_week, status)
           VALUES (?, ?, ?, ?, ?, 'active')""",
        (team_id, org_id, now, RESEARCH_TEAM_COST_PER_MONTH, RESEARCH_TEAM_POINTS_PER_WEEK),
    )

    # Deduct first month
    conn.execute(
        "UPDATE organizations SET balance_usd = balance_usd - ? WHERE id = ?",
        (RESEARCH_TEAM_COST_PER_MONTH, org_id),
    )
    conn.commit()
    return {"team_id": team_id, "hired_at": now}


def fire_research_team(conn: sqlite3.Connection, org_id: str, team_id: str) -> Dict[str, Any]:
    """Dismiss a research team."""
    team = conn.execute(
        "SELECT id, org_id FROM research_teams WHERE id = ? AND org_id = ?",
        (team_id, org_id),
    ).fetchone()
    if not team:
        raise ValueError("Research team not found")

    conn.execute("DELETE FROM research_teams WHERE id = ?", (team_id,))
    conn.commit()
    return {"fired": team_id}


# ── LEO Boost ──────────────────────────────────────────────────────────────────


def get_boostable_items(conn: sqlite3.Connection, org_id: str) -> List[Dict[str, Any]]:
    """
    Return catalog items eligible for Earth-to-LEO boost.
    Only tech level 1-2 items + water resource, filtered to items
    whose tech-tree node the org has actually unlocked.
    """
    # Fetch org's unlocked tech node IDs
    unlocked_rows = conn.execute(
        "SELECT tech_id FROM research_unlocks WHERE org_id = ?", (org_id,)
    ).fetchall()
    unlocked_ids = {str(r["tech_id"]) for r in unlocked_rows}

    boostable = []

    # Resources: only water (for fuel) — no tech gate
    resources = catalog_service.load_resource_catalog()
    for rid, res in resources.items():
        name = str(res.get("name") or "").lower()
        if "water" in name:
            boostable.append({
                "item_id": rid,
                "name": res.get("name", rid),
                "type": "resource",
                "mass_per_unit_kg": float(res.get("mass_per_m3_kg") or 1000.0),
                "tech_level": 1,
            })

    # Parts: tech level 1 and 2 only.
    # If org has unlocked techs, we try to filter by unlocks; if that yields no parts,
    # fall back to base TL gating so boost options never disappear due unlock drift.
    part_candidates: List[Dict[str, Any]] = []
    for loader_name, loader_fn in [
        ("thruster", catalog_service.load_thruster_main_catalog),
        ("reactor", catalog_service.load_reactor_catalog),
        ("generator", catalog_service.load_generator_catalog),
        ("radiator", catalog_service.load_radiator_catalog),
        ("constructor", catalog_service.load_constructor_catalog),
        ("refinery", catalog_service.load_refinery_catalog),
        ("robonaut", catalog_service.load_robonaut_catalog),
        ("storage", catalog_service.load_storage_catalog),
    ]:
        catalog = loader_fn()
        for iid, item in catalog.items():
            tech_lvl = float(item.get("tech_level") or 1)
            if tech_lvl not in BOOSTABLE_TECH_LEVELS:
                continue
            branch = str(item.get("branch") or "")
            node_id = _tech_node_id_for_item(loader_name, tech_lvl, branch)
            mass = float(item.get("mass_kg") or item.get("dry_mass_kg") or 0.0)
            part_candidates.append({
                "item_id": iid,
                "name": item.get("name") or iid,
                "type": loader_name,
                "mass_per_unit_kg": mass,
                "tech_level": tech_lvl,
                "required_tech_id": node_id,
            })

    unlocked_part_candidates = [
        p for p in part_candidates
        if p.get("required_tech_id") is None or p.get("required_tech_id") in unlocked_ids
    ]

    # Prefer unlock-gated set only when it is non-empty; otherwise use TL-only base set.
    chosen_parts = unlocked_part_candidates if unlocked_part_candidates else part_candidates
    for p in chosen_parts:
        out = dict(p)
        out.pop("required_tech_id", None)
        boostable.append(out)

    return boostable


def calculate_boost_cost(mass_kg: float) -> float:
    return LEO_BOOST_BASE_COST + (LEO_BOOST_COST_PER_KG * mass_kg)


def boost_manifest_to_leo(
    conn: sqlite3.Connection,
    org_id: str,
    items: List[Dict[str, Any]],
    *,
    corp_id: str = "",
    fuel_kg: float | None = None,
) -> Dict[str, Any]:
    """
    Boost multiple items from Earth to LEO in a single launch.
    One base launch cost is charged for the combined payload mass.
    If fuel_kg is provided, water is also added to the destination and its
    mass is included in the total cost.
    """
    settle_org(conn, org_id)

    normalized_qty: Dict[str, float] = {}
    for raw in items or []:
        item_id = str((raw or {}).get("item_id") or "").strip()
        quantity = float((raw or {}).get("quantity") or 0.0)
        if not item_id:
            continue
        if quantity <= 0.0:
            raise ValueError(f"Invalid quantity for item '{item_id}'")
        normalized_qty[item_id] = normalized_qty.get(item_id, 0.0) + quantity

    if not normalized_qty:
        raise ValueError("No boost items selected")

    boostable = get_boostable_items(conn, org_id)
    boostable_by_id = {str(b["item_id"]): b for b in boostable}

    launch_lines: List[Dict[str, Any]] = []
    for item_id, quantity in normalized_qty.items():
        item = boostable_by_id.get(item_id)
        if not item:
            raise ValueError(f"Item '{item_id}' is not eligible for Earth-to-LEO boost")
        line_mass_kg = float(item["mass_per_unit_kg"]) * quantity
        launch_lines.append(
            {
                "item_id": item_id,
                "item_name": str(item["name"]),
                "item_type": str(item["type"]),
                "quantity": quantity,
                "mass_kg": line_mass_kg,
            }
        )

    total_mass_kg = sum(float(line["mass_kg"]) for line in launch_lines)

    # Include fuel (water) mass in the boost cost if requested
    boost_fuel_kg = max(0.0, float(fuel_kg or 0.0))
    total_mass_kg += boost_fuel_kg

    total_cost = calculate_boost_cost(total_mass_kg)

    org = conn.execute("SELECT balance_usd FROM organizations WHERE id = ?", (org_id,)).fetchone()
    if not org:
        raise ValueError("Organization not found")
    balance = float(org["balance_usd"])
    if balance < total_cost:
        raise ValueError(f"Insufficient funds. Need ${total_cost:,.0f}, have ${balance:,.0f}")

    leo_loc = conn.execute(
        "SELECT id FROM locations WHERE id LIKE '%LEO%' OR id LIKE '%leo%' LIMIT 1"
    ).fetchone()
    dest_location_id = str(leo_loc["id"]) if leo_loc else LEO_LOCATION_ID

    conn.execute(
        "UPDATE organizations SET balance_usd = balance_usd - ? WHERE id = ?",
        (total_cost, org_id),
    )

    now = game_now_s()
    if total_mass_kg > 0.0:
        for line in launch_lines:
            line["cost_usd"] = total_cost * (float(line["mass_kg"]) / total_mass_kg)
    else:
        equal_share = total_cost / max(1, len(launch_lines))
        for line in launch_lines:
            line["cost_usd"] = equal_share

    for line in launch_lines:
        boost_id = str(uuid.uuid4())
        line["boost_id"] = boost_id
        conn.execute(
            """INSERT INTO leo_boosts (id, org_id, item_id, item_name, quantity, mass_kg, cost_usd, boosted_at, destination_location_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                boost_id,
                org_id,
                line["item_id"],
                line["item_name"],
                float(line["quantity"]),
                float(line["mass_kg"]),
                float(line["cost_usd"]),
                now,
                dest_location_id,
            ),
        )

        _add_to_location_inventory(
            conn,
            dest_location_id,
            str(line["item_id"]),
            str(line["item_name"]),
            str(line["item_type"]),
            float(line["quantity"]),
            float(line["mass_kg"]),
            now,
            corp_id=corp_id,
        )

    # If fuel (water) was requested, add it to the destination inventory
    if boost_fuel_kg > 0.0:
        import main as _main_mod
        resources = _main_mod.load_resource_catalog()
        water_res = resources.get("water") or {}
        water_name = str(water_res.get("name") or "Water")
        water_density = max(0.0, float(water_res.get("mass_per_m3_kg") or 1000.0))
        water_volume_m3 = (boost_fuel_kg / water_density) if water_density > 0.0 else 0.0
        import json as _json
        water_payload = _json.dumps({"resource_id": "water"}, sort_keys=True, separators=(",", ":"))
        _main_mod._upsert_inventory_stack(
            conn,
            location_id=dest_location_id,
            stack_type="resource",
            stack_key="water",
            item_id="water",
            name=water_name,
            quantity_delta=boost_fuel_kg,
            mass_delta_kg=boost_fuel_kg,
            volume_delta_m3=water_volume_m3,
            payload_json=water_payload,
            corp_id=corp_id,
        )

    conn.commit()

    result: Dict[str, Any] = {
        "destination": dest_location_id,
        "mass_kg": total_mass_kg,
        "fuel_kg": boost_fuel_kg,
        "cost_usd": total_cost,
        "item_count": len(launch_lines),
        "items": [
            {
                "boost_id": str(line["boost_id"]),
                "item_id": str(line["item_id"]),
                "item_name": str(line["item_name"]),
                "quantity": float(line["quantity"]),
                "mass_kg": float(line["mass_kg"]),
                "cost_usd": float(line["cost_usd"]),
            }
            for line in launch_lines
        ],
    }

    if len(launch_lines) == 1:
        single = launch_lines[0]
        result.update(
            {
                "boost_id": str(single["boost_id"]),
                "item_id": str(single["item_id"]),
                "item_name": str(single["item_name"]),
                "quantity": float(single["quantity"]),
            }
        )

    return result


def boost_to_leo(
    conn: sqlite3.Connection,
    org_id: str,
    item_id: str,
    quantity: float,
    *,
    corp_id: str = "",
) -> Dict[str, Any]:
    return boost_manifest_to_leo(
        conn,
        org_id,
        [{"item_id": item_id, "quantity": quantity}],
        corp_id=corp_id,
    )


def _add_to_location_inventory(
    conn: sqlite3.Connection,
    location_id: str,
    item_id: str,
    name: str,
    item_type: str,
    quantity: float,
    mass_kg: float,
    now: float,
    *,
    corp_id: str = "",
) -> None:
    """Upsert an item into location inventory.

    Delegates to the canonical helpers in main.py so that stack_key
    generation is consistent across all code paths (previously this
    used item_id as the stack_key while main.py used a SHA1 hash,
    creating duplicate non-mergeable stacks for the same item).
    """
    import main as _main

    if item_type == "resource":
        _main.add_resource_to_location_inventory(
            conn, location_id, item_id, mass_kg, corp_id=corp_id,
        )
    else:
        # Build a part dict with enough fields for proper normalization
        part = _resolve_part_dict_for_inventory(item_id, name, item_type, mass_kg)
        _main.add_part_to_location_inventory(
            conn, location_id, part, count=quantity, corp_id=corp_id,
        )


def _resolve_part_dict_for_inventory(item_id: str, name: str, item_type: str, mass_kg: float) -> Dict[str, Any]:
    """Build a part dict suitable for add_part_to_location_inventory."""
    loader_map = {
        "thruster": catalog_service.load_thruster_main_catalog,
        "reactor": catalog_service.load_reactor_catalog,
        "generator": catalog_service.load_generator_catalog,
        "radiator": catalog_service.load_radiator_catalog,
        "constructor": catalog_service.load_constructor_catalog,
        "refinery": catalog_service.load_refinery_catalog,
        "robonaut": catalog_service.load_robonaut_catalog,
        "storage": catalog_service.load_storage_catalog,
    }
    part_catalog = loader_map.get(item_type, lambda: {})()
    part = dict(part_catalog.get(item_id) or {})
    if not part:
        part = {
            "item_id": item_id,
            "name": name,
            "type": item_type,
            "category_id": item_type,
            "mass_kg": max(0.0, float(mass_kg or 0.0)),
        }
    else:
        part.setdefault("item_id", item_id)
        part.setdefault("name", name)
    return part


def _inventory_payload_json_for_item(item_id: str, name: str, item_type: str, mass_kg: float) -> str:
    """Build inventory payload JSON with enough metadata for UI part categorization."""
    if item_type == "resource":
        return json.dumps({"resource_id": item_id}, sort_keys=True, separators=(",", ":"))

    loader_map = {
        "thruster": catalog_service.load_thruster_main_catalog,
        "reactor": catalog_service.load_reactor_catalog,
        "generator": catalog_service.load_generator_catalog,
        "radiator": catalog_service.load_radiator_catalog,
        "constructor": catalog_service.load_constructor_catalog,
        "refinery": catalog_service.load_refinery_catalog,
        "robonaut": catalog_service.load_robonaut_catalog,
        "storage": catalog_service.load_storage_catalog,
    }

    part_catalog = loader_map.get(item_type, lambda: {})()
    part = dict(part_catalog.get(item_id) or {})

    if not part:
        part = {
            "item_id": item_id,
            "name": name,
            "type": item_type,
            "category_id": item_type,
            "mass_kg": max(0.0, float(mass_kg or 0.0)),
        }
    else:
        part.setdefault("item_id", item_id)
        part.setdefault("name", name)
        part.setdefault("type", item_type)
        part.setdefault("category_id", item_type)
        part.setdefault("mass_kg", max(0.0, float(mass_kg or 0.0)))

    return json.dumps({"part": part}, sort_keys=True, separators=(",", ":"))


def get_boost_history(conn: sqlite3.Connection, org_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Return the most recent LEO boosts for an org, newest first."""
    rows = conn.execute(
        """SELECT id, item_id, item_name, quantity, mass_kg, cost_usd,
                  boosted_at, destination_location_id
           FROM leo_boosts
           WHERE org_id = ?
           ORDER BY boosted_at DESC
           LIMIT ?""",
        (org_id, limit),
    ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "item_id": str(r["item_id"]),
            "item_name": str(r["item_name"]),
            "quantity": float(r["quantity"]),
            "mass_kg": float(r["mass_kg"]),
            "cost_usd": float(r["cost_usd"]),
            "boosted_at": float(r["boosted_at"]),
            "destination": str(r["destination_location_id"]),
        }
        for r in rows
    ]


# ── Research Unlock (KSP Tech Tree) ──────────────────────────────────────────


def get_unlocked_techs(conn: sqlite3.Connection, org_id: str) -> List[Dict[str, Any]]:
    """Get all tech IDs unlocked by an org."""
    rows = conn.execute(
        "SELECT tech_id, unlocked_at, cost_points FROM research_unlocks WHERE org_id = ?",
        (org_id,),
    ).fetchall()
    return [
        {"tech_id": str(r["tech_id"]), "unlocked_at": float(r["unlocked_at"]), "cost_points": float(r["cost_points"])}
        for r in rows
    ]


def unlock_tech(
    conn: sqlite3.Connection,
    org_id: str,
    tech_id: str,
    cost: float,
    prerequisites: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Unlock a tech node for the org. Deducts research points.
    Validates prerequisites are met.
    """
    settle_org(conn, org_id)

    # Check if already unlocked
    existing = conn.execute(
        "SELECT tech_id FROM research_unlocks WHERE org_id = ? AND tech_id = ?",
        (org_id, tech_id),
    ).fetchone()
    if existing:
        raise ValueError(f"Tech '{tech_id}' is already unlocked")

    # Check prerequisites
    if prerequisites:
        unlocked = {str(r["tech_id"]) for r in conn.execute(
            "SELECT tech_id FROM research_unlocks WHERE org_id = ?", (org_id,)
        ).fetchall()}
        missing = [p for p in prerequisites if p not in unlocked]
        if missing:
            raise ValueError(f"Prerequisites not met: {', '.join(missing)}")

    # Check research points
    org = conn.execute("SELECT research_points FROM organizations WHERE id = ?", (org_id,)).fetchone()
    if not org:
        raise ValueError("Organization not found")
    points = float(org["research_points"])
    if points < cost:
        raise ValueError(f"Need {cost:.1f} research points, have {points:.1f}")

    # Deduct and unlock
    now = game_now_s()
    conn.execute(
        "UPDATE organizations SET research_points = research_points - ? WHERE id = ?",
        (cost, org_id),
    )
    conn.execute(
        "INSERT INTO research_unlocks (org_id, tech_id, unlocked_at, cost_points) VALUES (?, ?, ?, ?)",
        (org_id, tech_id, now, cost),
    )
    conn.commit()
    return {"tech_id": tech_id, "unlocked_at": now, "cost_points": cost}


# ── Prospecting ────────────────────────────────────────────────────────────────


def _get_location_xy(conn: sqlite3.Connection, location_id: str) -> Optional[Tuple[float, float]]:
    """Return (x_km, y_km) for a location, or None if not found."""
    row = conn.execute("SELECT x, y FROM locations WHERE id = ?", (location_id,)).fetchone()
    if not row:
        return None
    return (float(row["x"]), float(row["y"]))


def _distance_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Euclidean distance between two (x_km, y_km) points."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _get_ship_robonaut_range(parts_json: str) -> float:
    """
    Return the maximum prospect_range_km from all robonauts on the ship.
    Returns 0 if no robonaut is equipped.
    """
    parts = json.loads(parts_json or "[]")
    max_range = 0.0
    for p in parts:
        if not isinstance(p, dict):
            continue
        cat = str(p.get("category") or p.get("category_id") or p.get("type") or "").lower()
        if cat in ("robonaut", "robonauts"):
            rng = float(p.get("prospect_range_km") or 0.0)
            if rng > max_range:
                max_range = rng
    return max_range


def get_sites_in_range(
    conn: sqlite3.Connection,
    org_id: str,
    ship_id: str,
) -> Dict[str, Any]:
    """
    Get all surface sites within prospecting range of a ship's robonaut.
    Returns ship info and list of sites with distance and prospected status.
    """
    ship = conn.execute(
        "SELECT id, name, location_id, parts_json FROM ships WHERE id = ?",
        (ship_id,),
    ).fetchone()
    if not ship:
        raise ValueError("Ship not found")

    parts_json = ship["parts_json"] or "[]"
    prospect_range = _get_ship_robonaut_range(parts_json)
    if prospect_range <= 0:
        raise ValueError("Ship has no robonaut equipped for prospecting")

    ship_loc_id = str(ship["location_id"] or "")
    ship_pos = _get_location_xy(conn, ship_loc_id)
    if not ship_pos:
        raise ValueError("Ship location not found")

    # Get all surface sites with their orbit_node coordinates
    sites = conn.execute(
        """
        SELECT ss.location_id, ss.body_id, ss.orbit_node_id, ss.gravity_m_s2,
               l.name AS site_name
        FROM surface_sites ss
        JOIN locations l ON l.id = ss.location_id
        ORDER BY l.sort_order, l.name
        """
    ).fetchall()

    # Get prospected site IDs for this org
    prospected_sites: set = set()
    if org_id:
        for r in conn.execute(
            "SELECT DISTINCT site_location_id FROM prospecting_results WHERE org_id = ?",
            (org_id,),
        ).fetchall():
            prospected_sites.add(str(r["site_location_id"]))

    results = []
    for site in sites:
        site_id = str(site["location_id"])
        orbit_node_id = str(site["orbit_node_id"])

        # Distance is measured from ship to the site's parent orbital node
        site_orbit_pos = _get_location_xy(conn, orbit_node_id)
        if not site_orbit_pos:
            # Fallback to site's own position
            site_orbit_pos = _get_location_xy(conn, site_id)
        if not site_orbit_pos:
            continue

        dist = _distance_km(ship_pos, site_orbit_pos)
        if dist <= prospect_range:
            results.append({
                "location_id": site_id,
                "name": str(site["site_name"]),
                "body_id": str(site["body_id"]),
                "orbit_node_id": orbit_node_id,
                "gravity_m_s2": float(site["gravity_m_s2"]),
                "distance_km": round(dist, 1),
                "is_prospected": site_id in prospected_sites,
            })

    # Sort by distance
    results.sort(key=lambda s: s["distance_km"])

    return {
        "ship_id": str(ship["id"]),
        "ship_name": str(ship["name"]),
        "ship_location": ship_loc_id,
        "prospect_range_km": prospect_range,
        "sites": results,
    }


def prospect_site(
    conn: sqlite3.Connection,
    org_id: str,
    ship_id: str,
    site_location_id: str,
) -> Dict[str, Any]:
    """
    Prospect a surface site using a ship with a robonaut.
    The ship must be within the robonaut's prospect_range_km of the site's orbit node.
    Reveals actual resource distribution to the org.
    """
    ship = conn.execute(
        "SELECT id, name, location_id, parts_json FROM ships WHERE id = ?",
        (ship_id,),
    ).fetchone()
    if not ship:
        raise ValueError("Ship not found")

    # Check ship has a robonaut and get its range
    parts_json = ship["parts_json"] or "[]"
    prospect_range = _get_ship_robonaut_range(parts_json)
    if prospect_range <= 0:
        raise ValueError("Ship must have a robonaut equipped to prospect")

    # Verify it's a surface site
    site = conn.execute(
        "SELECT location_id, body_id, orbit_node_id FROM surface_sites WHERE location_id = ?",
        (site_location_id,),
    ).fetchone()
    if not site:
        raise ValueError("Location is not a surface site")

    # Check range: distance from ship location to site's orbit node
    ship_pos = _get_location_xy(conn, str(ship["location_id"]))
    if not ship_pos:
        raise ValueError("Ship location not found")

    orbit_node_id = str(site["orbit_node_id"])
    site_orbit_pos = _get_location_xy(conn, orbit_node_id)
    if not site_orbit_pos:
        site_orbit_pos = _get_location_xy(conn, site_location_id)
    if not site_orbit_pos:
        raise ValueError("Site location coordinates not found")

    dist = _distance_km(ship_pos, site_orbit_pos)
    if dist > prospect_range:
        raise ValueError(
            f"Site is {dist:,.0f} km away but robonaut range is only {prospect_range:,.0f} km"
        )

    # Check if already prospected
    already = conn.execute(
        "SELECT COUNT(*) as cnt FROM prospecting_results WHERE org_id = ? AND site_location_id = ?",
        (org_id, site_location_id),
    ).fetchone()
    if int(already["cnt"]) > 0:
        raise ValueError("Site already prospected by your organization")

    # Get actual resource distribution
    resources = conn.execute(
        "SELECT resource_id, mass_fraction FROM surface_site_resources WHERE site_location_id = ?",
        (site_location_id,),
    ).fetchall()

    now = game_now_s()
    for res in resources:
        conn.execute(
            """INSERT INTO prospecting_results (org_id, site_location_id, resource_id, mass_fraction, prospected_at, prospected_by_ship)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (org_id, site_location_id, res["resource_id"], float(res["mass_fraction"]), now, ship_id),
        )

    conn.commit()
    return {
        "site_location_id": site_location_id,
        "ship_id": ship_id,
        "ship_name": str(ship["name"]),
        "distance_km": round(dist, 1),
        "resources_found": [
            {"resource_id": str(r["resource_id"]), "mass_fraction": float(r["mass_fraction"])}
            for r in resources
        ],
        "prospected_at": now,
    }


def get_prospected_sites(conn: sqlite3.Connection, org_id: str) -> List[Dict[str, Any]]:
    """Get all sites prospected by an org with their resource data."""
    rows = conn.execute(
        """SELECT site_location_id, resource_id, mass_fraction, prospected_at, prospected_by_ship
           FROM prospecting_results WHERE org_id = ?
           ORDER BY site_location_id, resource_id""",
        (org_id,),
    ).fetchall()

    sites: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        loc = str(r["site_location_id"])
        if loc not in sites:
            sites[loc] = {
                "site_location_id": loc,
                "prospected_at": float(r["prospected_at"]),
                "prospected_by_ship": str(r["prospected_by_ship"] or ""),
                "resources": [],
            }
        sites[loc]["resources"].append({
            "resource_id": str(r["resource_id"]),
            "mass_fraction": float(r["mass_fraction"]),
        })

    return list(sites.values())


def is_site_prospected(conn: sqlite3.Connection, org_id: str, site_location_id: str) -> bool:
    """Check if a site has been prospected by the org."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM prospecting_results WHERE org_id = ? AND site_location_id = ?",
        (org_id, site_location_id),
    ).fetchone()
    return int(row["cnt"]) > 0
