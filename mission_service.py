"""
Mission service — pool management, generation, completion validation, expiry.

Government-issued interplanetary objectives with rolling pool of ~5 available
missions.  See docs/missions-system.md for full design.
"""

import json
import random
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sim_service import game_now_s

# ── Constants ──────────────────────────────────────────────────────────────────

POOL_SIZE = 5
CONTRACT_LENGTH_S = 15 * 365.25 * 86400          # 15 game-years
AVAILABLE_EXPIRY_S = 5 * 365.25 * 86400          # 5 game-years unclaimed
HARD_POWER_DURATION_S = 90 * 86400               # 90 game-days consecutive

PAYOUTS = {
    "easy":   {"total": 5_000_000_000,  "upfront": 2_500_000_000,  "completion": 2_500_000_000},
    "medium": {"total": 10_000_000_000, "upfront": 5_000_000_000,  "completion": 5_000_000_000},
    "hard":   {"total": 15_000_000_000, "upfront": 7_500_000_000,  "completion": 7_500_000_000},
}

# Tier roll weights (cumulative thresholds for random.random())
TIER_WEIGHTS = [("easy", 0.40), ("medium", 0.75), ("hard", 1.0)]

# Zone weights per tier
ZONE_WEIGHTS: Dict[str, List[Tuple[str, float]]] = {
    "easy":   [("mercury_venus", 0.20), ("mars", 0.55), ("asteroid_belt", 0.90), ("jupiter", 1.0)],
    "medium": [("mercury_venus", 0.10), ("mars", 0.35), ("asteroid_belt", 0.65), ("jupiter", 0.90), ("saturn", 1.0)],
    "hard":   [("mars", 0.10), ("asteroid_belt", 0.25), ("jupiter", 0.65), ("saturn", 1.0)],
}

# ── Excluded destinations ──────────────────────────────────────────────────────

_EXCLUDED_PREFIXES = ("LEO", "HEO", "GEO", "LLO", "HLO", "LUNA_")
_EXCLUDED_EXACT = {"L1", "L2", "L3", "L4", "L5"}


def _is_excluded(loc_id: str) -> bool:
    if loc_id in _EXCLUDED_EXACT:
        return True
    for pfx in _EXCLUDED_PREFIXES:
        if loc_id.startswith(pfx):
            return True
    return False


# ── Destination registry (built once from celestial config) ────────────────────

_DEST_CACHE: Optional[Dict[str, Any]] = None


def _build_destination_registry() -> Dict[str, Any]:
    """Build zone → {orbit_nodes, surface_sites} map from celestial config."""
    global _DEST_CACHE
    if _DEST_CACHE is not None:
        return _DEST_CACHE

    from celestial_config import load_celestial_config, build_location_parent_body_map
    from transfer_planner import _resolve_heliocentric_body

    config = load_celestial_config()
    loc_body = build_location_parent_body_map(config)

    # Build body → trojan group map
    body_trojan: Dict[str, str] = {}
    for body_def in config.get("bodies", []):
        pgid = str(body_def.get("parent_group_id", "")).strip()
        if pgid == "grp_sj_l4_greeks":
            body_trojan[body_def["id"]] = "trojans_l4"
        elif pgid == "grp_sj_l5_trojans":
            body_trojan[body_def["id"]] = "trojans_l5"

    # Collect all orbit nodes and surface sites with their heliocentric zone
    orbit_node_ids = {n["id"] for n in (config.get("orbit_nodes") or [])}
    # Also include lagrange points as orbit-type destinations
    for lsys in (config.get("lagrange_systems") or []):
        for pt in (lsys.get("points") or []):
            pid = pt.get("id", "")
            if pid:
                orbit_node_ids.add(pid)

    surface_site_ids = {s["id"] for s in (config.get("surface_sites") or [])}

    # Build name map
    name_map: Dict[str, str] = {}
    for n in (config.get("orbit_nodes") or []):
        name_map[n["id"]] = n.get("name", n["id"])
    for s in (config.get("surface_sites") or []):
        name_map[s["id"]] = s.get("name", s["id"])
    for lsys in (config.get("lagrange_systems") or []):
        for pt in (lsys.get("points") or []):
            pid = pt.get("id", "")
            if pid:
                name_map[pid] = pt.get("name", pid)

    # Sub-zone mapping: mercury, venus, earth, mars, ceres, vesta, ...
    _ZONE_MAP = {
        "mercury": "mercury_venus",
        "venus": "mercury_venus",
        "zoozve": "mercury_venus",
        "earth": "earth",
        "moon": "earth",
        "mars": "mars",
        "phobos": "mars",
        "deimos": "mars",
        # Asteroid belt bodies
        "ceres": "asteroid_belt",
        "vesta": "asteroid_belt",
        "pallas": "asteroid_belt",
        "hygiea": "asteroid_belt",
        "psyche": "asteroid_belt",
        "lutetia": "asteroid_belt",
        "kalliope": "asteroid_belt",
        "hesperia": "asteroid_belt",
        "undina": "asteroid_belt",
        "antigone": "asteroid_belt",
        "kleopatra": "asteroid_belt",
        # Jupiter system
        "jupiter": "jupiter",
        "io": "jupiter",
        "europa": "jupiter",
        "ganymede": "jupiter",
        "callisto": "jupiter",
        # Trojans → jupiter zone
        "trojans_l4": "jupiter",
        "trojans_l5": "jupiter",
        # Saturn system
        "saturn": "saturn",
        "mimas": "saturn",
        "enceladus": "saturn",
        "tethys": "saturn",
        "dione": "saturn",
        "rhea": "saturn",
        "titan": "saturn",
        "iapetus": "saturn",
    }

    # For each body in the trojan map, include trojan group sub-zone lookup
    for body_id, tgroup in body_trojan.items():
        _ZONE_MAP[body_id] = _ZONE_MAP.get(tgroup, "jupiter")

    # Organize destinations by zone
    zones: Dict[str, Dict[str, List[Tuple[str, str]]]] = {}
    for zone_name in ("mercury_venus", "mars", "asteroid_belt", "jupiter", "saturn"):
        zones[zone_name] = {"orbit_nodes": [], "surface_sites": []}

    for loc_id, body_id in loc_body.items():
        if _is_excluded(loc_id):
            continue
        helio = _resolve_heliocentric_body(body_id)
        sub_zone = body_trojan.get(helio, helio)
        zone = _ZONE_MAP.get(sub_zone, _ZONE_MAP.get(helio, ""))
        if not zone or zone == "earth":
            continue

        name = name_map.get(loc_id, loc_id)
        if loc_id in orbit_node_ids:
            zones.setdefault(zone, {"orbit_nodes": [], "surface_sites": []})["orbit_nodes"].append((loc_id, name))
        elif loc_id in surface_site_ids:
            zones.setdefault(zone, {"orbit_nodes": [], "surface_sites": []})["surface_sites"].append((loc_id, name))

    _DEST_CACHE = zones
    return _DEST_CACHE


def eligible_destinations(tier: str) -> List[Tuple[str, str, str]]:
    """Return list of (location_id, name, zone) eligible for the given tier."""
    zones = _build_destination_registry()
    zone_weights = ZONE_WEIGHTS.get(tier, ZONE_WEIGHTS["easy"])
    results: List[Tuple[str, str, str]] = []

    for zone_name, _w in zone_weights:
        zdata = zones.get(zone_name, {"orbit_nodes": [], "surface_sites": []})
        if tier == "easy":
            for loc_id, name in zdata["orbit_nodes"]:
                results.append((loc_id, name, zone_name))
        else:
            # Medium and Hard: surface sites only
            for loc_id, name in zdata["surface_sites"]:
                results.append((loc_id, name, zone_name))

    return results


# ── Mission generation ─────────────────────────────────────────────────────────

def _roll_tier() -> str:
    r = random.random()
    for tier, threshold in TIER_WEIGHTS:
        if r <= threshold:
            return tier
    return "easy"


def _roll_zone(tier: str) -> str:
    r = random.random()
    weights = ZONE_WEIGHTS.get(tier, ZONE_WEIGHTS["easy"])
    for zone, threshold in weights:
        if r <= threshold:
            return zone
    return weights[-1][0]


def _pick_destination(tier: str, zone: str) -> Optional[Tuple[str, str]]:
    """Pick a random destination (id, name) from the zone appropriate for the tier."""
    zones = _build_destination_registry()
    zdata = zones.get(zone, {"orbit_nodes": [], "surface_sites": []})

    if tier == "easy":
        candidates = zdata["orbit_nodes"]
    else:
        candidates = zdata["surface_sites"]

    if not candidates:
        zones = _build_destination_registry()
        if tier == "easy":
            candidates = [pair for z in zones.values() for pair in z.get("orbit_nodes", [])]
        else:
            candidates = [pair for z in zones.values() for pair in z.get("surface_sites", [])]
    if not candidates:
        return None
    return random.choice(candidates)


def _generate_title(tier: str, dest_name: str) -> str:
    templates = {
        "easy": "Orbital Survey — {}",
        "medium": "Surface Expedition — {}",
        "hard": "Deep Space Research — {}",
    }
    return templates.get(tier, "Mission — {}").format(dest_name)


def _generate_description(tier: str, dest_name: str, dest_id: str) -> str:
    if tier == "easy":
        return (
            f"Deliver a Mission Materials Module to {dest_name} ({dest_id}). "
            f"The module must be present in orbit at the destination."
        )
    elif tier == "medium":
        return (
            f"Transport a Mission Materials Module to {dest_name} ({dest_id}). "
            f"The module must be delivered to the surface site. "
            f"Prospecting and landing at the destination are required."
        )
    else:
        return (
            f"Establish a research presence at {dest_name} ({dest_id}). "
            f"Deliver the Mission Materials Module to the surface, power it for "
            f"90 consecutive game-days, then return it to Earth orbit (LEO, HEO, or GEO)."
        )


def generate_mission(now_s: float) -> Optional[Dict[str, Any]]:
    """Generate a single random mission. Returns dict or None if no valid destination."""
    tier = _roll_tier()
    zone = _roll_zone(tier)
    dest = _pick_destination(tier, zone)
    if not dest:
        return None

    dest_id, dest_name = dest
    payout = PAYOUTS[tier]

    return {
        "id": f"msn_{uuid.uuid4().hex[:12]}",
        "tier": tier,
        "title": _generate_title(tier, dest_name),
        "description": _generate_description(tier, dest_name, dest_id),
        "destination_id": dest_id,
        "destination_name": dest_name,
        "status": "available",
        "payout_total": payout["total"],
        "payout_upfront": payout["upfront"],
        "payout_completion": payout["completion"],
        "org_id": None,
        "accepted_at": None,
        "expires_at": None,
        "delivered_at": None,
        "power_started_at": None,
        "power_required_s": HARD_POWER_DURATION_S if tier == "hard" else 0,
        "completed_at": None,
        "created_at": now_s,
        "available_expires_at": now_s + AVAILABLE_EXPIRY_S,
    }


def _insert_mission(conn: sqlite3.Connection, m: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO missions
           (id, tier, title, description, destination_id, destination_name,
            status, payout_total, payout_upfront, payout_completion,
            org_id, accepted_at, expires_at, delivered_at,
            power_started_at, power_required_s, completed_at,
            created_at, available_expires_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            m["id"], m["tier"], m["title"], m["description"],
            m["destination_id"], m["destination_name"],
            m["status"], m["payout_total"], m["payout_upfront"], m["payout_completion"],
            m["org_id"], m["accepted_at"], m["expires_at"], m["delivered_at"],
            m["power_started_at"], m["power_required_s"], m["completed_at"],
            m["created_at"], m["available_expires_at"],
        ),
    )


# ── Settle-on-access: expiry + pool refill ─────────────────────────────────────

def settle_missions(conn: sqlite3.Connection) -> None:
    """Expire overdue missions and refill the available pool to POOL_SIZE.
    Called on every missions list fetch (settle-on-access pattern)."""
    now = game_now_s()

    # 1. Expire available missions past their unclaimed window
    conn.execute(
        "UPDATE missions SET status='failed' WHERE status='available' AND available_expires_at < ?",
        (now,),
    )

    # 2. Auto-fail accepted/delivered/powered missions past contract expiry.
    #    Anti-exploit policy: claw back upfront and remove mission module.
    overdue_active = conn.execute(
        """SELECT id, org_id, payout_upfront FROM missions
           WHERE status IN ('accepted','delivered','powered') AND expires_at < ?""",
        (now,),
    ).fetchall()
    for row in overdue_active:
        mission_id = str(row["id"])
        org_id = str(row["org_id"] or "")
        upfront = float(row["payout_upfront"] or 0)

        if org_id:
            conn.execute(
                "UPDATE organizations SET balance_usd = balance_usd - ? WHERE id = ?",
                (upfront, org_id),
            )
        remove_mission_module(conn, mission_id)
        conn.execute(
            "UPDATE missions SET status='failed', completed_at=? WHERE id=?",
            (now, mission_id),
        )

    # 3. Refill pool
    count_row = conn.execute(
        "SELECT COUNT(*) as cnt FROM missions WHERE status='available'"
    ).fetchone()
    available_count = int(count_row["cnt"]) if count_row else 0

    needed = POOL_SIZE - available_count
    attempts = 0
    while needed > 0 and attempts < 20:
        attempts += 1
        m = generate_mission(now)
        if m is None:
            continue
        # Avoid duplicate destinations in the current pool
        existing = conn.execute(
            "SELECT 1 FROM missions WHERE status='available' AND destination_id=?",
            (m["destination_id"],),
        ).fetchone()
        if existing:
            continue
        _insert_mission(conn, m)
        needed -= 1

    conn.commit()


# ── Mission module helpers ─────────────────────────────────────────────────────

MISSION_MODULE_ITEM_ID = "mission_materials_module"
MISSION_MODULE_MASS_KG = 25000.0
MISSION_MODULE_VOLUME_M3 = 40.0


def mission_module_stack_key(mission_id: str) -> str:
    """Per-mission unique stack key so modules are unambiguous."""
    return f"mission_module_{mission_id}"


def _mission_module_id_from_payload(payload_json: str) -> str:
    try:
        payload = json.loads(payload_json or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return ""
    mission_id = payload.get("mission_id")
    if mission_id:
        return str(mission_id)
    part = payload.get("part")
    if isinstance(part, dict):
        mission_id = part.get("_mission_id") or part.get("mission_id")
        if mission_id:
            return str(mission_id)
    return ""


def _module_stack_matches_mission(stack_key: str, payload_json: str, mission_id: str) -> bool:
    if str(stack_key or "") == mission_module_stack_key(mission_id):
        return True
    return _mission_module_id_from_payload(payload_json) == str(mission_id)


def _mission_owner_corp_ids(conn: sqlite3.Connection, mission_id: str) -> List[str]:
    row = conn.execute("SELECT org_id FROM missions WHERE id = ?", (mission_id,)).fetchone()
    if not row:
        return []
    org_id = str(row["org_id"] or "")
    if not org_id:
        return []
    corp_rows = conn.execute("SELECT id FROM corporations WHERE org_id = ?", (org_id,)).fetchall()
    return [str(r["id"] or "") for r in corp_rows if str(r["id"] or "")]


def mint_mission_module(conn: sqlite3.Connection, mission_id: str, location_id: str, org_id: str) -> None:
    """Place 1× Mission Materials Module into a location's inventory for the org."""
    from main import _upsert_inventory_stack
    from org_service import get_org_id_for_corp

    # Resolve corp_id from org
    corp_row = conn.execute(
        "SELECT id FROM corporations WHERE org_id = ?", (org_id,)
    ).fetchone()
    corp_id = str(corp_row["id"]) if corp_row else ""

    stack_key = mission_module_stack_key(mission_id)
    payload = json.dumps({"mission_module": True, "mission_id": mission_id})

    _upsert_inventory_stack(
        conn,
        location_id=location_id,
        stack_type="part",
        stack_key=stack_key,
        item_id=MISSION_MODULE_ITEM_ID,
        name="Mission Materials Module",
        quantity_delta=1.0,
        mass_delta_kg=MISSION_MODULE_MASS_KG,
        volume_delta_m3=MISSION_MODULE_VOLUME_M3,
        payload_json=payload,
        corp_id=corp_id,
    )


def remove_mission_module(conn: sqlite3.Connection, mission_id: str) -> None:
    """Remove the mission module from wherever it exists (location inventory, facilities, or ship cargo)."""
    stack_key = mission_module_stack_key(mission_id)

    # Remove from location inventory (includes ship cargo stacks)
    conn.execute(
        "DELETE FROM location_inventory_stacks WHERE stack_key = ? AND item_id = ?",
        (stack_key, MISSION_MODULE_ITEM_ID),
    )

    # Remove from deployed equipment at facilities
    conn.execute(
        "DELETE FROM deployed_equipment WHERE item_id = ?",
        (MISSION_MODULE_ITEM_ID,),
    )

    # Remove from ship cargo (parts_json) — search all ships
    rows = conn.execute("SELECT id, parts_json FROM ships WHERE parts_json LIKE '%mission_materials_module%'").fetchall()
    for row in rows:
        import main as m
        parts, _cargo = m.split_ship_parts_and_cargo(row["parts_json"] or "[]")
        filtered = [p for p in parts if not (
            isinstance(p, dict) and
            p.get("item_id") == MISSION_MODULE_ITEM_ID and
            p.get("_mission_id") == mission_id
        )]
        if len(filtered) != len(parts):
            conn.execute(
                "UPDATE ships SET parts_json = ? WHERE id = ?",
                (m.merge_ship_parts_and_cargo(filtered), row["id"]),
            )


def find_mission_module(conn: sqlite3.Connection, mission_id: str, target_location_id: str) -> Optional[str]:
    """Check if the mission module is at the target location.
    Searches location inventory, deployed facility equipment, and docked ships.
    Returns 'location' or 'facility:<facility_id>' or 'ship:<ship_id>' or None."""
    stack_key = mission_module_stack_key(mission_id)

    # Check location inventory (location-scoped cargo)
    stack_rows = conn.execute(
        """SELECT stack_key, payload_json FROM location_inventory_stacks
           WHERE location_id = ? AND item_id = ? AND quantity >= 1""",
        (target_location_id, MISSION_MODULE_ITEM_ID),
    ).fetchall()
    for row in stack_rows:
        if _module_stack_matches_mission(str(row["stack_key"] or ""), str(row["payload_json"] or "{}"), mission_id):
            return "location"

    # Check deployed equipment at facilities for this location
    facility_eq = conn.execute(
        """SELECT de.id, de.facility_id FROM deployed_equipment de
           JOIN facilities f ON f.id = de.facility_id
           WHERE f.location_id = ? AND de.item_id = ?""",
        (target_location_id, MISSION_MODULE_ITEM_ID),
    ).fetchone()
    if facility_eq:
        return f"facility:{facility_eq['facility_id']}"

    # Check ALL docked ships at this location (not just those with module in parts_json)
    ships = conn.execute(
        """SELECT id, parts_json FROM ships
           WHERE location_id = ? AND arrives_at IS NULL""",
        (target_location_id,),
    ).fetchall()
    for ship in ships:
        # Check ship installed parts
        import main as m
        parts, _cargo = m.split_ship_parts_and_cargo(ship["parts_json"] or "[]")
        for p in parts:
            if isinstance(p, dict) and p.get("item_id") == MISSION_MODULE_ITEM_ID:
                p_mission_id = str(p.get("_mission_id") or p.get("mission_id") or "")
                if p_mission_id == mission_id:
                    return f"ship:{ship['id']}"
        # Check ship cargo containers (inventory stacks on ship)
        cargo_rows = conn.execute(
            """SELECT stack_key, payload_json FROM location_inventory_stacks
               WHERE location_id = ? AND item_id = ? AND quantity >= 1""",
            (f"ship:{ship['id']}", MISSION_MODULE_ITEM_ID),
        ).fetchall()
        for cargo in cargo_rows:
            if _module_stack_matches_mission(str(cargo["stack_key"] or ""), str(cargo["payload_json"] or "{}"), mission_id):
                return f"ship:{ship['id']}"

    return None


def find_mission_module_anywhere(conn: sqlite3.Connection, mission_id: str) -> Optional[Dict[str, str]]:
    """Find where the mission module currently is. Returns {location_id, found_in} or None."""
    owner_corp_ids = _mission_owner_corp_ids(conn, mission_id)
    where_sql = "WHERE item_id = ? AND quantity >= 1"
    params: List[Any] = [MISSION_MODULE_ITEM_ID]
    if owner_corp_ids:
        placeholders = ",".join("?" for _ in owner_corp_ids)
        where_sql += f" AND corp_id IN ({placeholders})"
        params.extend(owner_corp_ids)

    # Check location inventory
    stack_rows = conn.execute(
        f"""SELECT location_id, stack_key, payload_json FROM location_inventory_stacks
           {where_sql}""",
        tuple(params),
    ).fetchall()
    fallback_location_candidates: List[Dict[str, str]] = []
    fallback_ship_candidates: List[Dict[str, str]] = []
    for row in stack_rows:
        loc_id = str(row["location_id"] or "")
        stack_key = str(row["stack_key"] or "")
        payload_json = str(row["payload_json"] or "{}")
        if _module_stack_matches_mission(stack_key, payload_json, mission_id):
            if loc_id.startswith("ship:"):
                ship_id = loc_id[5:]
                ship_row = conn.execute(
                    "SELECT name, location_id FROM ships WHERE id = ?", (ship_id,)
                ).fetchone()
                return {
                    "location_id": str(ship_row["location_id"] or "in_transit") if ship_row else "unknown",
                    "found_in": f"ship:{ship_id}",
                    "ship_name": str(ship_row["name"] or ship_id) if ship_row else ship_id,
                }
            return {"location_id": loc_id, "found_in": "location_inventory"}

        if loc_id.startswith("ship:"):
            ship_id = loc_id[5:]
            ship_row = conn.execute(
                "SELECT name, location_id FROM ships WHERE id = ?", (ship_id,)
            ).fetchone()
            fallback_ship_candidates.append(
                {
                    "location_id": str(ship_row["location_id"] or "in_transit") if ship_row else "unknown",
                    "found_in": f"ship:{ship_id}",
                    "ship_name": str(ship_row["name"] or ship_id) if ship_row else ship_id,
                }
            )
        elif loc_id:
            fallback_location_candidates.append(
                {"location_id": loc_id, "found_in": "location_inventory"}
            )

    # Check deployed equipment at facilities
    eq_params: List[Any] = [MISSION_MODULE_ITEM_ID]
    eq_filter = ""
    if owner_corp_ids:
        placeholders = ",".join("?" for _ in owner_corp_ids)
        eq_filter = f" AND f.corp_id IN ({placeholders})"
        eq_params.extend(owner_corp_ids)
    eq_row = conn.execute(
        """SELECT de.facility_id, f.location_id, f.name AS facility_name
           FROM deployed_equipment de
           JOIN facilities f ON f.id = de.facility_id
           WHERE de.item_id = ?""" + eq_filter + " LIMIT 1",
        tuple(eq_params),
    ).fetchone()
    if eq_row:
        return {
            "location_id": str(eq_row["location_id"]),
            "found_in": f"facility:{eq_row['facility_id']}",
            "facility_name": str(eq_row["facility_name"] or eq_row["facility_id"]),
        }

    # Check ship installed parts
    ship_filter = "WHERE parts_json LIKE '%mission_materials_module%'"
    ship_params: List[Any] = []
    if owner_corp_ids:
        placeholders = ",".join("?" for _ in owner_corp_ids)
        ship_filter += f" AND corp_id IN ({placeholders})"
        ship_params.extend(owner_corp_ids)
    ships = conn.execute(
        "SELECT id, name, location_id FROM ships " + ship_filter,
        tuple(ship_params),
    ).fetchall()
    fallback_part_candidates: List[Dict[str, str]] = []
    for ship in ships:
        import main as m
        row = conn.execute("SELECT parts_json FROM ships WHERE id=?", (ship["id"],)).fetchone()
        parts, _cargo = m.split_ship_parts_and_cargo((row["parts_json"] if row else "") or "[]")
        for p in parts:
            if not isinstance(p, dict) or p.get("item_id") != MISSION_MODULE_ITEM_ID:
                continue

            p_mission_id = str(p.get("_mission_id") or p.get("mission_id") or "")
            loc = str(ship["location_id"] or "in_transit")
            candidate = {
                "location_id": loc,
                "found_in": f"ship:{ship['id']}",
                "ship_name": str(ship["name"] or ship["id"]),
            }
            if p_mission_id == mission_id:
                return candidate
            fallback_part_candidates.append(candidate)

    def _dedupe_candidates(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
        seen = set()
        out: List[Dict[str, str]] = []
        for row in rows:
            key = (str(row.get("found_in") or ""), str(row.get("location_id") or ""), str(row.get("ship_name") or ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(row))
        return out

    fallback_ship_candidates = _dedupe_candidates(fallback_ship_candidates)
    fallback_part_candidates = _dedupe_candidates(fallback_part_candidates)
    fallback_location_candidates = _dedupe_candidates(fallback_location_candidates)

    def _select_ship_candidate(rows: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
        if not rows:
            return None
        ordered = sorted(
            rows,
            key=lambda r: (
                0 if str(r.get("location_id") or "") in {"LEO", "HEO", "GEO"} else 1,
                str(r.get("ship_name") or "").lower(),
                str(r.get("found_in") or "").lower(),
            ),
        )
        chosen = dict(ordered[0])
        if len(ordered) > 1:
            chosen["ship_name"] = f"{chosen.get('ship_name') or 'ship'} (+{len(ordered) - 1} more)"
        return chosen

    candidate = _select_ship_candidate(fallback_ship_candidates)
    if candidate:
        return candidate

    candidate = _select_ship_candidate(fallback_part_candidates)
    if candidate:
        return candidate

    if fallback_location_candidates:
        ordered_locations = sorted(fallback_location_candidates, key=lambda r: str(r.get("location_id") or "").lower())
        chosen_loc = dict(ordered_locations[0])
        if len(ordered_locations) > 1:
            chosen_loc["location_id"] = f"{chosen_loc.get('location_id') or 'unknown'} (+{len(ordered_locations) - 1} more)"
        return chosen_loc

    return None


# ── Accept mission ─────────────────────────────────────────────────────────────

def accept_mission(conn: sqlite3.Connection, mission_id: str, org_id: str) -> Dict[str, Any]:
    """Accept a mission. Validates constraints, pays upfront, mints module.
    Returns updated mission dict. Raises ValueError on failure."""
    now = game_now_s()

    try:
        conn.execute("BEGIN IMMEDIATE")

        # Check org doesn't already have an active mission
        active = conn.execute(
            """SELECT id, title FROM missions
               WHERE org_id = ? AND status IN ('accepted','delivered','powered')""",
            (org_id,),
        ).fetchone()
        if active:
            raise ValueError(f"Organization already has an active mission: {active['title']}")

        # Load and validate mission
        mission = conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
        if not mission:
            raise ValueError("Mission not found")
        if mission["status"] != "available":
            raise ValueError(f"Mission is not available (status: {mission['status']})")

        # Atomic: claim it
        upfront = float(mission["payout_upfront"])
        expires_at = now + CONTRACT_LENGTH_S

        conn.execute(
            """UPDATE missions SET
                 status='accepted', org_id=?, accepted_at=?, expires_at=?
               WHERE id=? AND status='available'""",
            (org_id, now, expires_at, mission_id),
        )

        # Verify we actually got it (race condition guard)
        updated = conn.execute("SELECT * FROM missions WHERE id = ? AND org_id = ?", (mission_id, org_id)).fetchone()
        if not updated:
            raise ValueError("Mission was claimed by another organization")

        # Pay 50% upfront
        conn.execute(
            "UPDATE organizations SET balance_usd = balance_usd + ? WHERE id = ?",
            (upfront, org_id),
        )

        # Mint module at LEO
        mint_mission_module(conn, mission_id, "LEO", org_id)

        conn.commit()
        return dict(updated)
    except Exception:
        conn.rollback()
        raise


# ── Complete mission ───────────────────────────────────────────────────────────

def complete_mission(conn: sqlite3.Connection, mission_id: str, org_id: str) -> Dict[str, Any]:
    """Attempt to complete a mission. Logic varies by tier.
    Returns updated mission dict. Raises ValueError on failure."""
    now = game_now_s()

    mission = conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
    if not mission:
        raise ValueError("Mission not found")
    if str(mission["org_id"]) != org_id:
        raise ValueError("This mission does not belong to your organization")

    tier = mission["tier"]
    status = mission["status"]
    dest_id = mission["destination_id"]

    if tier == "easy":
        if status != "accepted":
            raise ValueError(f"Cannot complete easy mission in status '{status}' (must be 'accepted')")
        found = find_mission_module(conn, mission_id, dest_id)
        if not found:
            raise ValueError(f"Mission Materials Module not found at destination {dest_id}")

        # Complete!
        remove_mission_module(conn, mission_id)
        _pay_completion(conn, mission, now)
        return _get_mission_dict(conn, mission_id)

    elif tier == "medium":
        if status != "accepted":
            raise ValueError(f"Cannot complete medium mission in status '{status}' (must be 'accepted')")

        # Medium missions require a facility at the destination surface site
        if not _org_has_facility(conn, org_id, dest_id):
            raise ValueError(f"Your organization must have a facility at {dest_id} to complete this mission")

        found = find_mission_module(conn, mission_id, dest_id)
        if not found:
            raise ValueError(f"Mission Materials Module not found at destination surface site {dest_id}")

        remove_mission_module(conn, mission_id)
        _pay_completion(conn, mission, now)
        return _get_mission_dict(conn, mission_id)

    elif tier == "hard":
        if status == "accepted":
            # Step 1: Check module at destination + facility established
            if not _org_has_facility(conn, org_id, dest_id):
                raise ValueError(f"Your organization must have a facility at {dest_id} to advance this mission")

            found = find_mission_module(conn, mission_id, dest_id)
            if not found:
                raise ValueError(f"Mission Materials Module not found at destination {dest_id}")

            conn.execute(
                "UPDATE missions SET status='delivered', delivered_at=? WHERE id=?",
                (now, mission_id),
            )
            conn.commit()
            return _get_mission_dict(conn, mission_id)

        elif status == "delivered":
            # Step 2: Start power phase — check module at site + power available
            found = find_mission_module(conn, mission_id, dest_id)
            if not found:
                raise ValueError(f"Mission Materials Module must remain at destination {dest_id}")

            # Check facility power at destination (any positive net power)
            has_power = _check_facility_power(conn, dest_id)
            if not has_power:
                raise ValueError("Destination site must have positive net electric power")

            conn.execute(
                "UPDATE missions SET status='powered', power_started_at=? WHERE id=?",
                (now, mission_id),
            )
            conn.commit()
            return _get_mission_dict(conn, mission_id)

        elif status == "powered":
            # Step 3: Require 90 consecutive powered days at destination.
            # If power drops before completion, timer resets.
            power_started = float(mission["power_started_at"] or 0)
            required = float(mission["power_required_s"] or HARD_POWER_DURATION_S)
            elapsed = now - power_started

            at_destination = bool(find_mission_module(conn, mission_id, dest_id))
            has_power = _check_facility_power(conn, dest_id)

            if elapsed < required:
                if not at_destination:
                    raise ValueError("Mission Materials Module must remain at destination until power phase completes")
                if not has_power:
                    conn.execute(
                        """UPDATE missions SET
                             power_started_at=?,
                             power_reset_count=COALESCE(power_reset_count, 0) + 1,
                             last_power_reset_at=?
                           WHERE id=?""",
                        (now, now, mission_id),
                    )
                    conn.commit()
                    raise ValueError("Power interrupted at destination; hard mission power timer reset")

                days_elapsed = (now - power_started) / 86400
                days_needed = required / 86400
                raise ValueError(
                    f"Power phase in progress: {days_elapsed:.1f}/{days_needed:.0f} game-days elapsed"
                )

            if at_destination:
                raise ValueError("Power requirement met. Return Mission Materials Module to Earth orbit (LEO, HEO, or GEO)")

            # Check module returned to Earth orbit
            earth_orbits = ["LEO", "HEO", "GEO"]
            found_at_earth = None
            for eo in earth_orbits:
                if find_mission_module(conn, mission_id, eo):
                    found_at_earth = eo
                    break

            if not found_at_earth:
                raise ValueError("Mission Materials Module must be returned to Earth orbit (LEO, HEO, or GEO)")

            remove_mission_module(conn, mission_id)
            _pay_completion(conn, mission, now)
            return _get_mission_dict(conn, mission_id)

        else:
            raise ValueError(f"Cannot advance hard mission from status '{status}'")

    raise ValueError(f"Unknown tier: {tier}")


def _pay_completion(conn: sqlite3.Connection, mission: sqlite3.Row, now: float) -> None:
    """Pay the completion payout and mark the mission completed."""
    completion = float(mission["payout_completion"])
    org_id = str(mission["org_id"])

    conn.execute(
        "UPDATE organizations SET balance_usd = balance_usd + ? WHERE id = ?",
        (completion, org_id),
    )
    conn.execute(
        "UPDATE missions SET status='completed', completed_at=? WHERE id=?",
        (now, mission["id"]),
    )
    conn.commit()


def _get_mission_dict(conn: sqlite3.Connection, mission_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
    return dict(row) if row else {}


# ── Abandon mission ────────────────────────────────────────────────────────────

def abandon_mission(conn: sqlite3.Connection, mission_id: str, org_id: str) -> Dict[str, Any]:
    """Abandon a mission. Claws back the upfront payment as debt.
    Returns updated mission dict."""
    mission = conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
    if not mission:
        raise ValueError("Mission not found")
    if str(mission["org_id"]) != org_id:
        raise ValueError("This mission does not belong to your organization")
    if mission["status"] not in ("accepted", "delivered", "powered"):
        raise ValueError(f"Cannot abandon mission in status '{mission['status']}'")

    now = game_now_s()

    # Anti-exploit: claw back the upfront payment
    upfront = float(mission["payout_upfront"])
    conn.execute(
        "UPDATE organizations SET balance_usd = balance_usd - ? WHERE id = ?",
        (upfront, org_id),
    )

    # Remove the module
    remove_mission_module(conn, mission_id)

    conn.execute(
        "UPDATE missions SET status='abandoned', completed_at=? WHERE id=?",
        (now, mission_id),
    )
    conn.commit()
    return _get_mission_dict(conn, mission_id)


# ── Facility check helpers ──────────────────────────────────────────────────────

def _org_has_facility(conn: sqlite3.Connection, org_id: str, location_id: str) -> bool:
    """Check if the org (via its corp) has at least one facility at the given location."""
    corp_row = conn.execute(
        "SELECT id FROM corporations WHERE org_id = ?", (org_id,)
    ).fetchone()
    if not corp_row:
        return False
    corp_id = str(corp_row["id"])
    fac = conn.execute(
        "SELECT 1 FROM facilities WHERE location_id = ? AND corp_id = ? LIMIT 1",
        (location_id, corp_id),
    ).fetchone()
    return fac is not None


def _check_facility_power(conn: sqlite3.Connection, location_id: str) -> bool:
    """Check if any facility at the given location has positive net electric surplus."""
    facilities = conn.execute(
        "SELECT id FROM facilities WHERE location_id = ?",
        (location_id,),
    ).fetchall()

    for fac in facilities:
        fid = str(fac["id"])
        # Sum up generator output and reactor output at this facility
        equip_rows = conn.execute(
            "SELECT item_id, payload_json FROM deployed_equipment WHERE facility_id = ?",
            (fid,),
        ).fetchall()

        net_power = 0.0
        for eq in equip_rows:
            payload = json.loads(eq["payload_json"] or "{}")
            part = payload.get("part", payload)
            # Generators produce power
            if part.get("type") == "generator" or part.get("category_id") == "generator":
                net_power += float(part.get("power_output_kw", 0) or part.get("electric_output_kw", 0) or 0)
            # Reactors produce power
            elif part.get("type") == "reactor" or part.get("category_id") == "reactor":
                net_power += float(part.get("power_output_kw", 0) or part.get("electric_output_kw", 0) or 0)
            # Equipment that consumes power
            power_draw = float(part.get("power_draw_kw", 0) or part.get("electric_draw_kw", 0) or 0)
            net_power -= power_draw

        if net_power > 0:
            return True

    return False


# ── Query helpers ──────────────────────────────────────────────────────────────

def get_available_missions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Get all available missions (triggers settle first)."""
    settle_missions(conn)
    rows = conn.execute(
        "SELECT * FROM missions WHERE status='available' ORDER BY tier, destination_name"
    ).fetchall()
    return [dict(r) for r in rows]


def get_active_mission(conn: sqlite3.Connection, org_id: str) -> Optional[Dict[str, Any]]:
    """Get the org's current active mission (if any)."""
    row = conn.execute(
        """SELECT * FROM missions
           WHERE org_id = ? AND status IN ('accepted','delivered','powered')
           ORDER BY accepted_at DESC LIMIT 1""",
        (org_id,),
    ).fetchone()
    if not row:
        return None
    m = dict(row)
    # Attach module location info
    module_loc = find_mission_module_anywhere(conn, m["id"])
    m["module_location"] = module_loc
    return m


def get_mission_history(conn: sqlite3.Connection, org_id: str) -> List[Dict[str, Any]]:
    """Get org's completed/failed/abandoned missions."""
    rows = conn.execute(
        """SELECT * FROM missions
           WHERE org_id = ? AND status IN ('completed','failed','abandoned')
           ORDER BY completed_at DESC""",
        (org_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_mission_by_id(conn: sqlite3.Connection, mission_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
    return dict(row) if row else None
