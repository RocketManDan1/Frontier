"""
Contract router — API routes for the contract system.

Contracts allow players to create item exchange and courier missions,
accept contracts from Earth-based polities, and trade with each other.

Routes:
  GET  /api/contracts/incoming  — list accepted/incoming contracts for current user
  GET  /api/contracts/my        — list contracts created by current user (with filters)
  GET  /api/contracts/search    — search available contracts
  POST /api/contracts/create    — create a new contract
  POST /api/contracts/{id}/accept — accept a contract
  POST /api/contracts/{id}/complete — mark a contract as completed
  POST /api/contracts/{id}/reject  — reject/cancel a contract
"""

import sqlite3
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth_service import require_login
from db import get_db
from sim_service import game_now_s

router = APIRouter()


# ── Heliocentric zone helpers ──────────────────────────────────────────────────

_zone_cache: dict | None = None

# Mega-zones matching the orbital map zone jump bar.
# Each maps to one or more heliocentric sub-zones from the config.
_MEGA_ZONES = [
    {"id": "mercury",       "name": "Mercury",       "symbol": "\u263f", "sub": ["mercury"]},
    {"id": "venus",         "name": "Venus",         "symbol": "\u2640", "sub": ["venus", "zoozve"]},
    {"id": "earth",         "name": "Earth",         "symbol": "\u2295", "sub": ["earth"]},
    {"id": "mars",          "name": "Mars",          "symbol": "\u2642", "sub": ["mars"]},
    {"id": "asteroid_belt", "name": "Asteroid Belt", "symbol": "\u25cc", "sub": ["ceres", "vesta", "pallas", "hygiea"]},
    {"id": "jupiter",       "name": "Jupiter",       "symbol": "\u2643", "sub": ["jupiter", "trojans_l4", "trojans_l5"]},
    {"id": "saturn",        "name": "Saturn",        "symbol": "\u2644", "sub": ["saturn"]},
]


def _build_zone_data() -> dict:
    """Build heliocentric zone lookup matching the orbital-map zone bar.

    Returns::
        {
          "zones": [{"id": "earth", "name": "Earth", "symbol": "⊕", ...}, ...],
          "zone_locs": {"earth": [...], "jupiter": [...], ...}
        }

    Locations are grouped into 7 mega-zones: Mercury, Venus (+ Zoozve),
    Earth, Mars, Asteroid Belt (Ceres/Vesta/Pallas/Hygiea),
    Jupiter (+ Trojans L4/L5), Saturn.
    """
    global _zone_cache
    if _zone_cache is not None:
        return _zone_cache

    from celestial_config import load_celestial_config, build_location_parent_body_map
    from transfer_planner import _resolve_heliocentric_body, _get_body

    config = load_celestial_config()
    loc_body = build_location_parent_body_map(config)

    # Build body -> trojan group map
    body_trojan: dict[str, str] = {}
    for body_def in config.get("bodies", []):
        pgid = str(body_def.get("parent_group_id", "")).strip()
        if pgid == "grp_sj_l4_greeks":
            body_trojan[body_def["id"]] = "trojans_l4"
        elif pgid == "grp_sj_l5_trojans":
            body_trojan[body_def["id"]] = "trojans_l5"

    # Resolve each location to its fine-grained sub-zone
    sub_zone_locs: dict[str, set] = {}
    for loc_id, body_id in loc_body.items():
        helio = _resolve_heliocentric_body(body_id)
        sub_zone = body_trojan.get(helio, helio)
        sub_zone_locs.setdefault(sub_zone, set()).add(loc_id)

    # Build mega-zone → list of location ids (merging sub-zones)
    mega_zone_locs: dict[str, list] = {}
    zones_list = []
    for mz in _MEGA_ZONES:
        locs: set = set()
        for sub in mz["sub"]:
            locs.update(sub_zone_locs.get(sub, set()))
        mega_zone_locs[mz["id"]] = list(locs)
        zones_list.append({
            "id": mz["id"],
            "name": mz["name"],
            "symbol": mz["symbol"],
            "location_count": len(locs),
        })

    _zone_cache = {
        "zones": zones_list,
        "zone_locs": mega_zone_locs,
    }
    return _zone_cache


# ── Request / response models ──────────────────────────────────────────────────

class BidRequest(BaseModel):
    bid_amount: float


class CreateContractRequest(BaseModel):
    contract_type: str = "item_exchange"  # item_exchange | courier | auction
    title: str = ""
    description: str = ""
    price: float = 0.0                       # starting bid for auctions
    buyout_price: float = 0.0                # optional instant-buy price
    location_id: Optional[str] = None
    destination_id: Optional[str] = None     # for courier contracts
    assignee_org_id: Optional[str] = None    # private contract target
    availability: str = "public"             # public | private
    expiry_hours: float = 168.0              # default 1 week game-time
    expiry_days: Optional[int] = None        # auction duration (180 / 360 / 1825)
    items: list[dict] = []                   # [{item_id, quantity, ...}, ...]
    reward: float = 0.0                      # courier reward / collateral


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_org_id(conn: sqlite3.Connection, user) -> str:
    """Resolve org_id for the logged-in user."""
    from org_service import ensure_org_for_corp, ensure_org_for_user
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    if corp_id:
        return ensure_org_for_corp(conn, corp_id)
    username = user.get("username") if hasattr(user, "get") else user["username"]
    return ensure_org_for_user(conn, username)


def _get_corp_id_for_org(conn: sqlite3.Connection, org_id: str) -> str:
    """Resolve org_id → corp_id (the corporation that owns the org).
    Returns empty string if no corp found (admin/user orgs use corp_id='')."""
    row = conn.execute(
        "SELECT id FROM corporations WHERE org_id = ?", (org_id,)
    ).fetchone()
    return str(row["id"]) if row else ""


def _get_user_corp_id(user) -> str:
    """Extract corp_id from the user auth dict."""
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    return str(corp_id) if corp_id else ""


def _org_balance(conn: sqlite3.Connection, org_id: str) -> float:
    """Get current balance for an organization."""
    row = conn.execute("SELECT balance_usd FROM organizations WHERE id = ?", (org_id,)).fetchone()
    return float(row["balance_usd"]) if row else 0.0


def _deduct_money(conn: sqlite3.Connection, org_id: str, amount: float) -> None:
    """Deduct money from an org's balance. Raises 400 if insufficient."""
    bal = _org_balance(conn, org_id)
    if bal < amount:
        raise HTTPException(status_code=400, detail=f"Insufficient funds. Need ${amount:,.2f}, have ${bal:,.2f}")
    conn.execute(
        "UPDATE organizations SET balance_usd = balance_usd - ? WHERE id = ?",
        (amount, org_id),
    )


def _credit_money(conn: sqlite3.Connection, org_id: str, amount: float) -> None:
    """Add money to an org's balance."""
    if amount <= 0:
        return
    conn.execute(
        "UPDATE organizations SET balance_usd = balance_usd + ? WHERE id = ?",
        (amount, org_id),
    )


def _escrow_items_from_inventory(conn: sqlite3.Connection, items: list[dict],
                                  location_id: str, corp_id: str) -> None:
    """Remove items from a corp's inventory at the given location for escrow.
    Validates quantities are available before removing anything."""
    import json as _json
    from main import (
        _resource_stack_row, _consume_location_resource_mass,
        _part_stack_row, _consume_location_part_unit,
    )

    # First pass: validate everything is available
    for it in items:
        stack_key = it.get("stack_key") or it.get("item_id", "")
        it_type = it.get("type", "resource")
        qty = float(it.get("quantity", 0))

        if it_type == "part":
            try:
                row = _part_stack_row(conn, location_id, stack_key, corp_id=corp_id)
            except HTTPException:
                raise HTTPException(status_code=400,
                    detail=f"Item '{it.get('name', stack_key)}' not found in your inventory at this location.")
            avail = float(row["quantity"] or 0)
            if avail < qty:
                raise HTTPException(status_code=400,
                    detail=f"Insufficient '{it.get('name', stack_key)}': have {avail:.0f}, need {qty:.0f}")
        else:
            # Resource — quantity is mass in kg
            try:
                row = _resource_stack_row(conn, location_id, stack_key, corp_id=corp_id)
            except HTTPException:
                raise HTTPException(status_code=400,
                    detail=f"Resource '{it.get('name', stack_key)}' not found in your inventory at this location.")
            avail = float(row["mass_kg"] or 0)
            if avail < qty - 0.01:
                raise HTTPException(status_code=400,
                    detail=f"Insufficient '{it.get('name', stack_key)}': have {avail:.1f} kg, need {qty:.1f} kg")

    # Second pass: actually consume
    for it in items:
        stack_key = it.get("stack_key") or it.get("item_id", "")
        it_type = it.get("type", "resource")
        qty = float(it.get("quantity", 0))

        if it_type == "part":
            for _ in range(int(qty)):
                row = _part_stack_row(conn, location_id, stack_key, corp_id=corp_id)
                _consume_location_part_unit(conn, row)
        else:
            row = _resource_stack_row(conn, location_id, stack_key, corp_id=corp_id)
            _consume_location_resource_mass(conn, row, qty)


def _release_items_to_inventory(conn: sqlite3.Connection, items: list[dict],
                                 location_id: str, corp_id: str) -> None:
    """Add escrowed items back into a corp's inventory at the given location."""
    from main import add_resource_to_location_inventory, add_part_to_location_inventory

    for it in items:
        item_id = it.get("item_id", "")
        it_type = it.get("type", "resource")
        qty = float(it.get("quantity", 0))

        if it_type == "part":
            # Reconstruct the part dict for add_part_to_location_inventory
            part = {
                "item_id": item_id,
                "name": it.get("name", item_id),
                "mass_kg": float(it.get("mass_kg", 0)) / max(qty, 1),
            }
            # Attach payload fields if present
            if it.get("payload_json"):
                import json as _json
                try:
                    payload = _json.loads(it["payload_json"]) if isinstance(it["payload_json"], str) else it["payload_json"]
                    if isinstance(payload, dict) and "part" in payload:
                        part = payload["part"]
                except Exception:
                    pass
            add_part_to_location_inventory(conn, location_id, part, count=qty, corp_id=corp_id)
        else:
            # Resource — quantity is mass in kg
            add_resource_to_location_inventory(conn, location_id, item_id, qty, corp_id=corp_id)


# ── Courier cargo container helpers ──────────────────────────────────────────

def _courier_container_stack_key(contract_id: str) -> str:
    """Deterministic stack_key for a courier cargo container."""
    return f"courier_crate_{contract_id}"


def _create_courier_container(
    conn: sqlite3.Connection,
    contract_id: str,
    items: list[dict],
    location_id: str,
    courier_corp_id: str,
) -> str:
    """Create a sealed Courier Cargo Container as a part in the courier's inventory.

    The container holds the total mass of the contracted items but is opaque
    — the courier cannot open it or use its contents as fuel.

    Returns the container stack_key.
    """
    import json as _json
    from main import _upsert_inventory_stack

    total_mass = sum(float(it.get("mass_kg", 0)) for it in items)
    total_volume = sum(float(it.get("volume_m3", 0)) for it in items)
    stack_key = _courier_container_stack_key(contract_id)

    payload = {
        "part": {
            "item_id": "courier_cargo_container",
            "name": "Courier Cargo Container",
            "mass_kg": total_mass,
            "stack_key": stack_key,
            "sealed": True,
            "contract_id": contract_id,
        }
    }

    _upsert_inventory_stack(
        conn,
        location_id=location_id,
        stack_type="part",
        stack_key=stack_key,
        item_id="courier_cargo_container",
        name="Courier Cargo Container",
        quantity_delta=1.0,
        mass_delta_kg=total_mass,
        volume_delta_m3=total_volume,
        payload_json=_json.dumps(payload, sort_keys=True),
        corp_id=courier_corp_id,
    )
    return stack_key


def _find_courier_container(
    conn: sqlite3.Connection,
    container_id: str,
) -> dict | None:
    """Locate a courier cargo container anywhere in the game world.

    Searches location_inventory_stacks (location or ship cargo) and returns
    a dict with location_id/ship_id and the row, or None if not found.
    """
    # Check location inventory first
    row = conn.execute(
        """SELECT location_id, corp_id, stack_key, mass_kg
           FROM location_inventory_stacks
           WHERE stack_type = 'part' AND stack_key = ?""",
        (container_id,),
    ).fetchone()
    if row:
        return {
            "found_in": "location",
            "location_id": str(row["location_id"]),
            "corp_id": str(row["corp_id"]),
        }

    # Check ship parts (parts stored as JSON in ships.parts_json)
    ships = conn.execute(
        "SELECT id, location_id, parts_json FROM ships WHERE parts_json LIKE ?",
        (f"%{container_id}%",),
    ).fetchall()
    for ship in ships:
        import json as _json
        try:
            parts = _json.loads(ship["parts_json"] or "[]")
            for part in parts:
                if isinstance(part, dict) and part.get("stack_key") == container_id:
                    return {
                        "found_in": "ship",
                        "ship_id": str(ship["id"]),
                        "location_id": str(ship["location_id"]),
                        "corp_id": "",
                    }
        except Exception:
            pass

    return None


def _remove_courier_container(
    conn: sqlite3.Connection,
    container_id: str,
) -> bool:
    """Remove the courier cargo container from wherever it is.

    Returns True if removed, False if not found.
    """
    # Try location inventory first
    row = conn.execute(
        """SELECT location_id, corp_id
           FROM location_inventory_stacks
           WHERE stack_type = 'part' AND stack_key = ?""",
        (container_id,),
    ).fetchone()
    if row:
        conn.execute(
            """DELETE FROM location_inventory_stacks
               WHERE stack_type = 'part' AND stack_key = ?""",
            (container_id,),
        )
        return True

    # Try ship parts
    ships = conn.execute(
        "SELECT id, parts_json, fuel_kg FROM ships WHERE parts_json LIKE ?",
        (f"%{container_id}%",),
    ).fetchall()
    for ship in ships:
        import json as _json
        try:
            parts = _json.loads(ship["parts_json"] or "[]")
            new_parts = [
                p for p in parts
                if not (isinstance(p, dict) and p.get("stack_key") == container_id)
            ]
            if len(new_parts) != len(parts):
                from main import _persist_ship_inventory_state
                fuel_kg = max(0.0, float(ship["fuel_kg"] or 0))
                _persist_ship_inventory_state(
                    conn, ship_id=str(ship["id"]),
                    parts=new_parts, fuel_kg=fuel_kg,
                )
                return True
        except Exception:
            pass

    return False





def _contract_to_dict(row) -> dict:
    import json as _json
    from sim_service import game_now_s

    created = row["created_at"]
    expires = row["expires_at"]
    now = game_now_s()

    # Compute human-readable time_left
    time_left = "\u2014"
    if expires:
        remaining = float(expires) - now
        if remaining <= 0:
            time_left = "Expired"
        elif remaining < 86400:
            time_left = "Less than a day"
        elif remaining < 86400 * 2:
            time_left = "1 day"
        else:
            days = int(remaining / 86400)
            time_left = f"{days} days"

    # Parse items_json for buyout_price and items list
    items_raw = row["items_json"] or "{}"
    try:
        items_meta = _json.loads(items_raw)
    except Exception:
        items_meta = {}
    buyout = items_meta.get("buyout_price", 0)
    items_list = items_meta.get("items", [])

    return {
        "id": row["id"],
        "contract_type": row["contract_type"],
        "title": row["title"],
        "description": row["description"],
        "issuer_org_id": row["issuer_org_id"],
        "issuer_name": row["issuer_name"] if "issuer_name" in row.keys() else "",
        "assignee_org_id": row["assignee_org_id"],
        "assignee_name": row["assignee_name"] if "assignee_name" in row.keys() else "",
        "location_id": row["location_id"],
        "location_name": row["location_name"] if "location_name" in row.keys() else "",
        "destination_id": row["destination_id"],
        "destination_name": row["destination_name"] if "destination_name" in row.keys() else "",
        "price": row["price"],
        "buyout_price": buyout,
        "reward": row["reward"],
        "items": items_list,
        "availability": row["availability"],
        "status": row["status"],
        "created_at": created,
        "expires_at": expires,
        "completed_at": row["completed_at"],
        "time_left": time_left,
        "type": row["contract_type"],  # alias for frontend
        "courier_container_id": row["courier_container_id"] if "courier_container_id" in row.keys() else None,
    }


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/api/contracts/incoming")
def get_incoming_contracts(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Contracts assigned to / accepted by the current user's org."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)

    rows = conn.execute(
        """
        SELECT c.*,
               oi.name AS issuer_name,
               oa.name AS assignee_name,
               COALESCE(l.name, c.location_id) AS location_name,
               COALESCE(ld.name, c.destination_id) AS destination_name
        FROM contracts c
        LEFT JOIN organizations oi ON oi.id = c.issuer_org_id
        LEFT JOIN organizations oa ON oa.id = c.assignee_org_id
        LEFT JOIN locations l ON l.id = c.location_id
        LEFT JOIN locations ld ON ld.id = c.destination_id
        WHERE c.assignee_org_id = ?
          AND c.status IN ('outstanding', 'in_progress')
        ORDER BY c.created_at DESC
        """,
        (org_id,),
    ).fetchall()

    return {
        "contracts": [_contract_to_dict(r) for r in rows],

    }


@router.get("/api/contracts/my")
def get_my_contracts(
    request: Request,
    type: str = "item_exchange",
    action: str = "issued_to_by",
    status: str = "outstanding",
    conn: sqlite3.Connection = Depends(get_db),
):
    """Contracts created by the current user's org, with optional filters."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)

    conditions = []
    params: list = []

    # Action filter
    if action == "issued_by":
        conditions.append("c.issuer_org_id = ?")
        params.append(org_id)
    elif action == "issued_to":
        conditions.append("c.assignee_org_id = ?")
        params.append(org_id)
    else:  # issued_to_by
        conditions.append("(c.issuer_org_id = ? OR c.assignee_org_id = ?)")
        params.extend([org_id, org_id])

    # Type filter
    if type and type != "all":
        conditions.append("c.contract_type = ?")
        params.append(type)

    # Status filter
    status_map = {
        "outstanding": ("outstanding",),
        "in_progress": ("in_progress",),
        "completed": ("completed",),
        "expired": ("expired",),
        "rejected": ("rejected", "cancelled"),
    }
    statuses = status_map.get(status, ("outstanding",))
    placeholders = ",".join("?" for _ in statuses)
    conditions.append(f"c.status IN ({placeholders})")
    params.extend(statuses)

    where = " AND ".join(conditions) if conditions else "1=1"

    rows = conn.execute(
        f"""
        SELECT c.*,
               oi.name AS issuer_name,
               oa.name AS assignee_name,
               COALESCE(l.name, c.location_id) AS location_name,
               COALESCE(ld.name, c.destination_id) AS destination_name
        FROM contracts c
        LEFT JOIN organizations oi ON oi.id = c.issuer_org_id
        LEFT JOIN organizations oa ON oa.id = c.assignee_org_id
        LEFT JOIN locations l ON l.id = c.location_id
        LEFT JOIN locations ld ON ld.id = c.destination_id
        WHERE {where}
        ORDER BY c.created_at DESC
        LIMIT 50
        """,
        params,
    ).fetchall()

    return {
        "contracts": [_contract_to_dict(r) for r in rows],
    }


@router.get("/api/contracts/my-locations")
def get_my_locations(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Return locations where the current user's corp has inventory (for contract item picker).
    Admins see ALL locations with cargo for debug/testing purposes."""
    user = require_login(conn, request)
    is_admin = bool(user.get("is_admin") if hasattr(user, "get") else False)

    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    if not corp_id:
        # Admin or user without corp — try org_id as fallback corp key
        corp_id = _get_org_id(conn, user)

    if is_admin:
        # Admins see all locations with any cargo (debug mode)
        rows = conn.execute(
            """
            SELECT lis.location_id,
                   COALESCE(l.name, lis.location_id) AS name,
                   COUNT(*) AS item_count
            FROM location_inventory_stacks lis
            LEFT JOIN locations l ON l.id = lis.location_id
            GROUP BY lis.location_id
            ORDER BY name
            """,
        ).fetchall()
    elif not corp_id:
        return {"locations": []}
    else:
        rows = conn.execute(
            """
            SELECT lis.location_id,
                   COALESCE(l.name, lis.location_id) AS name,
                   COUNT(*) AS item_count
            FROM location_inventory_stacks lis
            LEFT JOIN locations l ON l.id = lis.location_id
            WHERE lis.corp_id = ?
            GROUP BY lis.location_id
            ORDER BY name
            """,
            (corp_id,),
        ).fetchall()

    return {
        "locations": [
            {"id": r["location_id"], "name": r["name"], "item_count": int(r["item_count"])}
            for r in rows
        ]
    }


@router.get("/api/contracts/zones")
def get_contract_zones(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Return the heliocentric zone list for courier contract filtering."""
    require_login(conn, request)
    zdata = _build_zone_data()
    return {"zones": zdata["zones"]}


@router.get("/api/contracts/search")
def search_contracts(
    request: Request,
    search_type: str = "buy_sell",
    item_type: Optional[str] = None,
    location: Optional[str] = None,
    contract_type: Optional[str] = None,
    category: Optional[str] = None,
    exclude_multiple: Optional[str] = None,
    exact_type: Optional[str] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    availability: str = "public",
    issuer: Optional[str] = None,
    pickup_zone: Optional[str] = None,
    dropoff_zone: Optional[str] = None,
    sort: str = "date_asc",
    conn: sqlite3.Connection = Depends(get_db),
):
    """Search available contracts (public + matching filters)."""
    user = require_login(conn, request)

    conditions = ["c.status = 'outstanding'"]
    params: list = []

    if availability == "public":
        conditions.append("c.availability = 'public'")

    if search_type == "courier":
        conditions.append("c.contract_type = 'courier'")
    elif contract_type == "auction":
        conditions.append("c.contract_type = 'auction'")
    elif contract_type == "all":
        conditions.append("c.contract_type IN ('item_exchange', 'auction')")
    else:
        conditions.append("c.contract_type = 'item_exchange'")

    if contract_type == "want_to_buy":
        conditions.append("c.price > 0")

    if location and location != "current":
        conditions.append("c.location_id = ?")
        params.append(location)

    # ── Courier zone filtering ──
    if search_type == "courier":
        zdata = _build_zone_data()
        zone_locs = zdata["zone_locs"]
        if pickup_zone and pickup_zone in zone_locs:
            pz_locs = zone_locs[pickup_zone]
            placeholders = ",".join("?" for _ in pz_locs)
            conditions.append(f"c.location_id IN ({placeholders})")
            params.extend(pz_locs)
        if dropoff_zone and dropoff_zone in zone_locs:
            dz_locs = zone_locs[dropoff_zone]
            placeholders = ",".join("?" for _ in dz_locs)
            conditions.append(f"c.destination_id IN ({placeholders})")
            params.extend(dz_locs)

    if price_min is not None:
        conditions.append("c.price >= ?")
        params.append(price_min * 1_000_000)  # convert from millions
    if price_max is not None:
        conditions.append("c.price <= ?")
        params.append(price_max * 1_000_000)

    if issuer:
        conditions.append("oi.name LIKE ?")
        params.append(f"%{issuer}%")

    where = " AND ".join(conditions)

    order = {
        "date_asc": "c.created_at ASC",
        "date_desc": "c.created_at DESC",
        "price_asc": "c.price ASC",
        "price_desc": "c.price DESC",
    }.get(sort, "c.created_at ASC")

    rows = conn.execute(
        f"""
        SELECT c.*,
               oi.name AS issuer_name,
               oa.name AS assignee_name,
               COALESCE(l.name, c.location_id) AS location_name,
               COALESCE(ld.name, c.destination_id) AS destination_name
        FROM contracts c
        LEFT JOIN organizations oi ON oi.id = c.issuer_org_id
        LEFT JOIN organizations oa ON oa.id = c.assignee_org_id
        LEFT JOIN locations l ON l.id = c.location_id
        LEFT JOIN locations ld ON ld.id = c.destination_id
        WHERE {where}
        ORDER BY {order}
        LIMIT 100
        """,
        params,
    ).fetchall()

    return {
        "contracts": [_contract_to_dict(r) for r in rows],
    }


@router.post("/api/contracts/create")
def create_contract(
    body: CreateContractRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Create a new contract.  Escrows items and/or money from the issuer."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)

    if body.contract_type not in ("item_exchange", "courier", "auction"):
        raise HTTPException(status_code=400, detail="Invalid contract type.")

    now = game_now_s()
    contract_id = str(uuid.uuid4())

    # Auction uses expiry_days; everything else uses expiry_hours
    if body.expiry_days is not None:
        expires = now + body.expiry_days * 86400
    else:
        expires = now + body.expiry_hours * 3600

    import json as _json

    items_data = body.items or []
    meta = {"items": items_data}
    if body.contract_type == "auction":
        meta["buyout_price"] = body.buyout_price

    # ── Escrow ────────────────────────────────────────────────────
    corp_id = _get_user_corp_id(user)
    escrow_usd = 0.0

    # Escrow items from issuer's inventory (auction, item_exchange, courier)
    if items_data and body.location_id:
        _escrow_items_from_inventory(conn, items_data, body.location_id, corp_id)

    # Courier: also escrow the reward money from the issuer
    if body.contract_type == "courier" and (body.reward or 0) > 0:
        _deduct_money(conn, org_id, body.reward)
        escrow_usd += body.reward

    conn.execute(
        """
        INSERT INTO contracts
          (id, contract_type, title, description, issuer_org_id, assignee_org_id,
           location_id, destination_id, price, reward, availability, status,
           created_at, expires_at, completed_at, items_json, escrow_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'outstanding', ?, ?, NULL, ?, ?)
        """,
        (
            contract_id,
            body.contract_type,
            body.title.strip() or "Untitled Contract",
            body.description.strip(),
            org_id,
            body.assignee_org_id,
            body.location_id,
            body.destination_id,
            body.price,
            body.reward,
            body.availability,
            now,
            expires,
            _json.dumps(meta),
            escrow_usd,
        ),
    )
    conn.commit()

    return {"ok": True, "contract_id": contract_id}


@router.get("/api/contracts/{contract_id}")
def get_contract(
    contract_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Get a single contract by ID with full details."""
    user = require_login(conn, request)

    row = conn.execute(
        """
        SELECT c.*,
               oi.name AS issuer_name,
               oa.name AS assignee_name,
               COALESCE(l.name, c.location_id) AS location_name,
               COALESCE(ld.name, c.destination_id) AS destination_name,
               ob.name AS current_bidder_name
        FROM contracts c
        LEFT JOIN organizations oi ON oi.id = c.issuer_org_id
        LEFT JOIN organizations oa ON oa.id = c.assignee_org_id
        LEFT JOIN locations l ON l.id = c.location_id
        LEFT JOIN locations ld ON ld.id = c.destination_id
        LEFT JOIN organizations ob ON ob.id = c.current_bidder_org_id
        WHERE c.id = ?
        """,
        (contract_id,),
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Contract not found.")

    d = _contract_to_dict(row)
    # Add auction-specific fields
    d["current_bid"] = row["current_bid"] if "current_bid" in row.keys() else 0
    d["current_bidder_org_id"] = row["current_bidder_org_id"] if "current_bidder_org_id" in row.keys() else None
    d["current_bidder_name"] = row["current_bidder_name"] if "current_bidder_name" in row.keys() else None
    return {"contract": d}


@router.post("/api/contracts/{contract_id}/bid")
def bid_on_contract(
    contract_id: str,
    body: BidRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Place a bid on an auction contract.  Escrows bid money; refunds the previous bidder."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)

    row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Contract not found.")
    if row["contract_type"] != "auction":
        raise HTTPException(status_code=400, detail="Only auction contracts accept bids.")
    if row["status"] != "outstanding":
        raise HTTPException(status_code=400, detail="Auction is no longer active.")
    if row["issuer_org_id"] == org_id:
        raise HTTPException(status_code=400, detail="You cannot bid on your own auction.")

    import json as _json
    items_meta = _json.loads(row["items_json"] or "{}")
    buyout = items_meta.get("buyout_price", 0)
    starting_bid = row["price"]  # price field holds starting bid for auctions
    current_bid = row["current_bid"] if "current_bid" in row.keys() else 0
    prev_bidder = row["current_bidder_org_id"] if "current_bidder_org_id" in row.keys() else None

    bid = body.bid_amount
    min_bid = current_bid if current_bid > 0 else starting_bid
    if bid < min_bid:
        raise HTTPException(status_code=400, detail=f"Bid must be at least ${min_bid:,.2f}")

    # Check if this is a buyout
    is_buyout = buyout > 0 and bid >= buyout
    effective_bid = buyout if is_buyout else bid

    # ── Escrow bid money ──────────────────────────────────────────
    _deduct_money(conn, org_id, effective_bid)

    # Refund previous bidder if one exists
    if prev_bidder and current_bid > 0:
        _credit_money(conn, prev_bidder, current_bid)

    if is_buyout:
        # Buyout = instant completion:  money → seller, items → buyer
        issuer_org_id = row["issuer_org_id"]
        items = items_meta.get("items", [])
        buyer_corp = _get_corp_id_for_org(conn, org_id)

        # Pay the seller
        _credit_money(conn, issuer_org_id, effective_bid)

        # Release escrowed items to buyer at the contract location
        if items:
            _release_items_to_inventory(conn, items, row["location_id"], buyer_corp)

        now = game_now_s()
        conn.execute(
            """UPDATE contracts
               SET current_bid = ?, current_bidder_org_id = ?,
                   assignee_org_id = ?, status = 'completed',
                   completed_at = ?, escrow_usd = 0
               WHERE id = ?""",
            (effective_bid, org_id, org_id, now, contract_id),
        )
    else:
        # Regular bid — update escrow_usd to the new bid amount
        conn.execute(
            """UPDATE contracts
               SET current_bid = ?, current_bidder_org_id = ?,
                   escrow_usd = ?
               WHERE id = ?""",
            (bid, org_id, bid, contract_id),
        )
    conn.commit()

    return {"ok": True, "is_buyout": is_buyout, "new_bid": effective_bid}


@router.post("/api/contracts/{contract_id}/accept")
def accept_contract(
    contract_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Accept an outstanding contract.  Handles escrow settlement per type."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)

    row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Contract not found.")
    if row["status"] != "outstanding":
        raise HTTPException(status_code=400, detail="Contract is not available for acceptance.")
    if row["issuer_org_id"] == org_id:
        raise HTTPException(status_code=400, detail="You cannot accept your own contract.")
    if row["availability"] == "private" and row["assignee_org_id"] != org_id:
        raise HTTPException(status_code=403, detail="This contract is not available to you.")

    import json as _json
    items_meta = _json.loads(row["items_json"] or "{}")
    items = items_meta.get("items", [])
    ctype = row["contract_type"]
    issuer_org = row["issuer_org_id"]
    location = row["location_id"]
    price = float(row["price"] or 0)

    if ctype == "item_exchange":
        # Acceptor pays the price → issuer gets the money
        # Escrowed items → acceptor's inventory
        if price > 0:
            _deduct_money(conn, org_id, price)
            _credit_money(conn, issuer_org, price)
        if items:
            acceptor_corp = _get_corp_id_for_org(conn, org_id)
            _release_items_to_inventory(conn, items, location, acceptor_corp)

        now = game_now_s()
        conn.execute(
            """UPDATE contracts
               SET assignee_org_id = ?, status = 'completed',
                   completed_at = ?, escrow_usd = 0
               WHERE id = ?""",
            (org_id, now, contract_id),
        )

    elif ctype == "courier":
        # Courier accepts — create sealed cargo container at pickup location
        # Items stay escrowed in items_json; the container is a physical black-box
        # the courier must transport to the destination.
        courier_corp = _get_corp_id_for_org(conn, org_id)
        container_key = _create_courier_container(
            conn, contract_id, items, location, courier_corp,
        )
        conn.execute(
            """UPDATE contracts
               SET assignee_org_id = ?, status = 'in_progress',
                   courier_container_id = ?
               WHERE id = ?""",
            (org_id, container_key, contract_id),
        )

    elif ctype == "auction":
        # Direct accept on an auction = accept at starting price
        if price > 0:
            _deduct_money(conn, org_id, price)
            _credit_money(conn, issuer_org, price)
        if items:
            acceptor_corp = _get_corp_id_for_org(conn, org_id)
            _release_items_to_inventory(conn, items, location, acceptor_corp)

        now = game_now_s()
        conn.execute(
            """UPDATE contracts
               SET assignee_org_id = ?, status = 'completed',
                   completed_at = ?, escrow_usd = 0
               WHERE id = ?""",
            (org_id, now, contract_id),
        )

    conn.commit()
    return {"ok": True}


@router.post("/api/contracts/{contract_id}/complete")
def complete_contract(
    contract_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Mark a contract as completed.  For couriers: release items to destination, reward to courier."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)

    row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Contract not found.")
    if row["status"] != "in_progress":
        raise HTTPException(status_code=400, detail="Contract is not in progress.")
    # Only issuer or assignee can complete
    if row["issuer_org_id"] != org_id and row["assignee_org_id"] != org_id:
        raise HTTPException(status_code=403, detail="Not authorized.")

    import json as _json
    items_meta = _json.loads(row["items_json"] or "{}")
    items = items_meta.get("items", [])
    ctype = row["contract_type"]
    issuer_org = row["issuer_org_id"]
    assignee_org = row["assignee_org_id"]

    if ctype == "courier":
        # ── Courier completion: verify sealed container at destination ────
        destination = row["destination_id"] or row["location_id"]
        container_id = row["courier_container_id"] if "courier_container_id" in row.keys() else None

        if container_id:
            # Find the container in the game world
            loc_info = _find_courier_container(conn, container_id)
            if not loc_info:
                raise HTTPException(
                    status_code=400,
                    detail="Courier Cargo Container not found. It may have been lost.",
                )
            container_location = loc_info["location_id"]
            if container_location != destination:
                # Resolve destination name for the error message
                dest_row = conn.execute(
                    "SELECT name FROM locations WHERE id = ?", (destination,)
                ).fetchone()
                dest_name = dest_row["name"] if dest_row else destination
                raise HTTPException(
                    status_code=400,
                    detail=f"Courier Cargo Container is not at the destination ({dest_name}). "
                           f"Transport it there before completing the contract.",
                )
            # Container verified at destination — remove it
            _remove_courier_container(conn, container_id)

        # Release escrowed items to issuer at the destination
        if items:
            issuer_corp = _get_corp_id_for_org(conn, issuer_org)
            _release_items_to_inventory(conn, items, destination, issuer_corp)
        # Pay the courier (assignee) the escrowed reward
        reward = float(row["reward"] or 0)
        if reward > 0 and assignee_org:
            _credit_money(conn, assignee_org, reward)
    else:
        # Non-courier in_progress contracts (edge case): just release items back to issuer
        if items:
            issuer_corp = _get_corp_id_for_org(conn, issuer_org)
            _release_items_to_inventory(conn, items, row["location_id"], issuer_corp)
        escrow_usd = float(row["escrow_usd"] or 0) if "escrow_usd" in row.keys() else 0
        if escrow_usd > 0:
            _credit_money(conn, issuer_org, escrow_usd)

    now = game_now_s()
    conn.execute(
        "UPDATE contracts SET status = 'completed', completed_at = ?, escrow_usd = 0 WHERE id = ?",
        (now, contract_id),
    )
    conn.commit()
    return {"ok": True}


@router.post("/api/contracts/{contract_id}/reject")
def reject_contract(
    contract_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Reject or cancel a contract.  Returns all escrowed items and money."""
    user = require_login(conn, request)
    org_id = _get_org_id(conn, user)

    row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Contract not found.")

    if row["issuer_org_id"] == org_id:
        new_status = "cancelled"
    elif row["assignee_org_id"] == org_id:
        new_status = "rejected"
    else:
        raise HTTPException(status_code=403, detail="Not authorized.")

    if row["status"] not in ("outstanding", "in_progress"):
        raise HTTPException(status_code=400, detail="Contract cannot be rejected in its current state.")

    import json as _json
    items_meta = _json.loads(row["items_json"] or "{}")
    items = items_meta.get("items", [])
    issuer_org = row["issuer_org_id"]
    ctype = row["contract_type"]

    # Remove courier cargo container if one exists
    container_id = row["courier_container_id"] if "courier_container_id" in row.keys() else None
    if container_id:
        _remove_courier_container(conn, container_id)

    # Return escrowed items to issuer at the contract's origin location
    if items:
        issuer_corp = _get_corp_id_for_org(conn, issuer_org)
        _release_items_to_inventory(conn, items, row["location_id"], issuer_corp)

    # Return escrowed money to issuer (courier reward)
    escrow_usd = float(row["escrow_usd"] or 0) if "escrow_usd" in row.keys() else 0
    if escrow_usd > 0:
        # For auctions, the escrowed money belongs to the current bidder, not issuer
        if ctype == "auction":
            bidder = row["current_bidder_org_id"] if "current_bidder_org_id" in row.keys() else None
            if bidder:
                _credit_money(conn, bidder, escrow_usd)
        else:
            _credit_money(conn, issuer_org, escrow_usd)

    conn.execute(
        "UPDATE contracts SET status = ?, escrow_usd = 0 WHERE id = ?",
        (new_status, contract_id),
    )
    conn.commit()
    return {"ok": True, "new_status": new_status}
