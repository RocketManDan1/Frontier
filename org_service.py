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

# Tech levels that can be boosted from Earth
BOOSTABLE_TECH_LEVELS = {1, 2}

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

    new_balance = float(org["balance_usd"]) + income - team_costs
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
        "income_per_month_usd": MONTHLY_INCOME_USD,
        "team_cost_per_month_usd": RESEARCH_TEAM_COST_PER_MONTH,
        "team_points_per_week": RESEARCH_TEAM_POINTS_PER_WEEK,
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

    # Parts: tech level 1 and 2 only, filtered by unlocked tech nodes
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
            # Check whether the org has unlocked the required tech node
            branch = str(item.get("branch") or "")
            node_id = _tech_node_id_for_item(loader_name, tech_lvl, branch)
            if node_id is not None and node_id not in unlocked_ids:
                continue  # tech not yet unlocked — skip
            mass = float(item.get("mass_kg") or item.get("dry_mass_kg") or 0.0)
            boostable.append({
                "item_id": iid,
                "name": item.get("name", iid),
                "type": loader_name,
                "mass_per_unit_kg": mass,
                "tech_level": tech_lvl,
            })

    return boostable


def calculate_boost_cost(mass_kg: float) -> float:
    return LEO_BOOST_BASE_COST + (LEO_BOOST_COST_PER_KG * mass_kg)


def boost_to_leo(
    conn: sqlite3.Connection,
    org_id: str,
    item_id: str,
    quantity: float,
) -> Dict[str, Any]:
    """
    Boost an item from Earth to LEO.
    Deducts cost from org balance and adds item to LEO location inventory.
    """
    settle_org(conn, org_id)

    # Find the item in boostable catalog (filtered by org's unlocked techs)
    boostable = get_boostable_items(conn, org_id)
    item = None
    for b in boostable:
        if b["item_id"] == item_id:
            item = b
            break

    if not item:
        raise ValueError(f"Item '{item_id}' is not eligible for Earth-to-LEO boost")

    total_mass_kg = item["mass_per_unit_kg"] * quantity
    cost = calculate_boost_cost(total_mass_kg)

    org = conn.execute("SELECT balance_usd FROM organizations WHERE id = ?", (org_id,)).fetchone()
    if not org:
        raise ValueError("Organization not found")
    balance = float(org["balance_usd"])
    if balance < cost:
        raise ValueError(f"Insufficient funds. Need ${cost:,.0f}, have ${balance:,.0f}")

    # Find LEO location
    leo_loc = conn.execute(
        "SELECT id FROM locations WHERE id LIKE '%LEO%' OR id LIKE '%leo%' LIMIT 1"
    ).fetchone()
    dest_location_id = str(leo_loc["id"]) if leo_loc else LEO_LOCATION_ID

    # Deduct cost
    conn.execute(
        "UPDATE organizations SET balance_usd = balance_usd - ? WHERE id = ?",
        (cost, org_id),
    )

    # Record the boost
    boost_id = str(uuid.uuid4())
    now = game_now_s()
    conn.execute(
        """INSERT INTO leo_boosts (id, org_id, item_id, item_name, quantity, mass_kg, cost_usd, boosted_at, destination_location_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (boost_id, org_id, item_id, item["name"], quantity, total_mass_kg, cost, now, dest_location_id),
    )

    # Add item to LEO location inventory (using the same pattern as industry_service)
    _add_to_location_inventory(conn, dest_location_id, item_id, item["name"], item["type"], quantity, total_mass_kg, now)

    conn.commit()
    return {
        "boost_id": boost_id,
        "item_id": item_id,
        "item_name": item["name"],
        "quantity": quantity,
        "mass_kg": total_mass_kg,
        "cost_usd": cost,
        "destination": dest_location_id,
    }


def _add_to_location_inventory(
    conn: sqlite3.Connection,
    location_id: str,
    item_id: str,
    name: str,
    item_type: str,
    quantity: float,
    mass_kg: float,
    now: float,
) -> None:
    """Upsert an item into location inventory."""
    stack_type = "resource" if item_type == "resource" else "part"
    stack_key = item_id

    existing = conn.execute(
        """SELECT quantity, mass_kg FROM location_inventory_stacks
           WHERE location_id = ? AND stack_type = ? AND stack_key = ?""",
        (location_id, stack_type, stack_key),
    ).fetchone()

    if existing:
        conn.execute(
            """UPDATE location_inventory_stacks
               SET quantity = quantity + ?, mass_kg = mass_kg + ?, updated_at = ?
               WHERE location_id = ? AND stack_type = ? AND stack_key = ?""",
            (quantity, mass_kg, now, location_id, stack_type, stack_key),
        )
    else:
        conn.execute(
            """INSERT INTO location_inventory_stacks
               (location_id, stack_type, stack_key, item_id, name, quantity, mass_kg, volume_m3, payload_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, '{}', ?)""",
            (location_id, stack_type, stack_key, item_id, name, quantity, mass_kg, now),
        )


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


def prospect_site(
    conn: sqlite3.Connection,
    org_id: str,
    ship_id: str,
    site_location_id: str,
) -> Dict[str, Any]:
    """
    Prospect a surface site using a ship with a robonaut.
    Reveals actual resource distribution to the org.
    """
    # Verify the ship exists and is at the site location
    ship = conn.execute(
        "SELECT id, name, location_id, parts_json FROM ships WHERE id = ?",
        (ship_id,),
    ).fetchone()
    if not ship:
        raise ValueError("Ship not found")
    if str(ship["location_id"]) != site_location_id:
        raise ValueError("Ship is not at the specified site location")

    # Check ship has a robonaut
    parts = json.loads(ship["parts_json"] or "[]")
    has_robonaut = any(
        str(p.get("category") or "").lower() in ("robonaut", "robonauts")
        for p in parts
    )
    if not has_robonaut:
        raise ValueError("Ship must have a robonaut equipped to prospect")

    # Verify it's a surface site
    site = conn.execute(
        "SELECT location_id, body_id FROM surface_sites WHERE location_id = ?",
        (site_location_id,),
    ).fetchone()
    if not site:
        raise ValueError("Location is not a surface site")

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
