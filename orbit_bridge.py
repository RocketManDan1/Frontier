"""
Orbit Bridge — connects orbit_service.py (pure math) to game config/DB.

Provides:
  - orbit_for_location()       : construct orbit elements for a docked ship
  - compute_transfer_burn_plan(): plan a transfer as a burn sequence
  - settle_ship_events()       : execute pending burns + SOI transitions + auto-dock
  - backfill_docked_orbits()   : initialize orbit_json for ships that lack one
"""

import json
import logging
import math
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import celestial_config
import orbit_service
import transfer_planner

logger = logging.getLogger(__name__)


# ── Config accessors (re-use transfer_planner's cached config) ─────────

def _get_config() -> Dict[str, Any]:
    return transfer_planner._get_config()


def _get_location_body_map() -> Dict[str, str]:
    return transfer_planner._get_location_body_map()


# ── Orbit construction for locations ───────────────────────

def orbit_for_location(
    location_id: str,
    game_time_s: float,
    angle_deg: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Build orbital elements for a ship docked at a given location.

    Works for:
    - Orbit-node locations (LEO, GEO, LLO, etc.) → circular orbit at node radius
    - Surface sites → circular orbit at body surface radius (represents landed)
    - Lagrange points → circular orbit around primary body at L-point distance

    Returns None only for truly unrecognised locations.
    """
    cfg = _get_config()

    # Case 1: Standard orbit node (LEO, GEO, LLO, HMO, etc.)
    body_id = celestial_config.get_orbit_node_body_id(cfg, location_id)
    radius_km = celestial_config.get_orbit_node_radius(cfg, location_id)
    if body_id and radius_km and radius_km > 0:
        try:
            mu = celestial_config.get_body_mu(cfg, body_id)
        except Exception:
            return None
        return orbit_service.circular_orbit(body_id, radius_km, mu, game_time_s, angle_deg=angle_deg)

    # Case 2: Surface site → orbit at body surface radius
    site_info = celestial_config.get_surface_site_info(cfg, location_id)
    if site_info:
        body_id = site_info["body_id"]
        try:
            body_radius = celestial_config.get_body_radius(cfg, body_id)
            mu = celestial_config.get_body_mu(cfg, body_id)
        except Exception:
            return None
        if body_radius <= 0:
            return None
        return orbit_service.circular_orbit(body_id, body_radius, mu, game_time_s, angle_deg=angle_deg)

    # Case 3: Lagrange point → circular orbit around primary body
    lp_info = celestial_config.get_lagrange_point_info(cfg, location_id)
    if lp_info:
        primary = lp_info["primary_body_id"]
        secondary = lp_info["secondary_body_id"]
        model = lp_info["model"]
        try:
            mu_primary = celestial_config.get_body_mu(cfg, primary)
        except Exception:
            return None

        # For L4/L5 (triangle points): use the secondary body's orbital radius
        # (they orbit the primary at the same SMA as the secondary)
        if model in ("triangle_plus", "triangle_minus"):
            try:
                body_state = celestial_config.compute_body_state(cfg, secondary, game_time_s)
                r_secondary = math.sqrt(body_state[0]**2 + body_state[1]**2)
                # L4 leads by 60°, L5 trails by 60° relative to secondary
                sec_angle = math.degrees(math.atan2(body_state[1], body_state[0]))
                offset = 60.0 if model == "triangle_plus" else -60.0
                lp_angle = sec_angle + offset
            except Exception:
                return None
            return orbit_service.circular_orbit(primary, r_secondary, mu_primary, game_time_s, angle_deg=lp_angle)

        # For L1/L2/L3 (collinear points): use distance_km from primary
        distance_km = lp_info.get("distance_km")
        if distance_km and distance_km > 0:
            try:
                body_state = celestial_config.compute_body_state(cfg, secondary, game_time_s)
                sec_angle = math.degrees(math.atan2(body_state[1], body_state[0]))
                if model == "line_primary_minus":
                    lp_angle = sec_angle + 180.0
                else:
                    lp_angle = sec_angle
            except Exception:
                lp_angle = angle_deg
            return orbit_service.circular_orbit(primary, distance_km, mu_primary, game_time_s, angle_deg=lp_angle)

    return None


def _get_mu_for_body(body_id: str) -> float:
    """Get gravitational parameter for a body, raising on failure."""
    cfg = _get_config()
    return celestial_config.get_body_mu(cfg, body_id)


def _get_soi_for_body(body_id: str) -> Optional[float]:
    """Get SOI radius for a body (None for sun)."""
    cfg = _get_config()
    return celestial_config.get_body_soi(cfg, body_id)


# ── Location orbit parameter resolution ────────────────────

def _resolve_location_orbit_params(
    cfg: Dict[str, Any],
    location_id: str,
) -> Optional[Dict[str, str]]:
    """Resolve a location to its effective orbital parameters.

    Returns { body_id, radius_km } for orbit nodes, surface sites, and
    Lagrange points. Returns None for unrecognised locations.
    """
    # Case 1: orbit node
    body_id = celestial_config.get_orbit_node_body_id(cfg, location_id)
    radius_km = celestial_config.get_orbit_node_radius(cfg, location_id)
    if body_id and radius_km and radius_km > 0:
        return {"body_id": body_id, "radius_km": radius_km}

    # Case 2: surface site → use orbit_node (the orbital anchor)
    # Surface transfers go surface ↔ orbit_node, but for the burn planner
    # we treat the surface site as being at the orbit_node's orbit.
    # The extra landing/ascent Δv is added by the route quote system.
    site_info = celestial_config.get_surface_site_info(cfg, location_id)
    if site_info:
        orbit_node_id = site_info["orbit_node_id"]
        node_body = celestial_config.get_orbit_node_body_id(cfg, orbit_node_id)
        node_radius = celestial_config.get_orbit_node_radius(cfg, orbit_node_id)
        if node_body and node_radius and node_radius > 0:
            return {"body_id": node_body, "radius_km": node_radius}

    # Case 3: Lagrange point
    lp_info = celestial_config.get_lagrange_point_info(cfg, location_id)
    if lp_info:
        primary = lp_info["primary_body_id"]
        model = lp_info["model"]
        secondary = lp_info["secondary_body_id"]

        # For triangle points (L4/L5): same SMA as secondary body
        if model in ("triangle_plus", "triangle_minus"):
            # Get secondary body's orbital SMA from config
            bodies_by_id = celestial_config._build_bodies_by_id(cfg)
            sec_body = bodies_by_id.get(secondary)
            if sec_body:
                pos = sec_body.get("position", {})
                sma = pos.get("semi_major_axis_km")
                if sma and float(sma) > 0:
                    return {"body_id": primary, "radius_km": float(sma)}

        # For collinear points (L1/L2/L3): use the configured distance
        distance_km = lp_info.get("distance_km")
        if distance_km and distance_km > 0:
            return {"body_id": primary, "radius_km": distance_km}

    return None


# ── Transfer burn plan computation ─────────────────────────

def compute_transfer_burn_plan(
    conn: sqlite3.Connection,
    from_location_id: str,
    to_location_id: str,
    departure_time_s: float,
) -> Optional[Dict[str, Any]]:
    """Compute a physics-based transfer as a sequence of burns.

    Returns a dict with:
        burns          : list of burn events for maneuver_json
        total_dv_m_s   : total delta-v (m/s)
        total_tof_s    : total time of flight (s)
        initial_orbit  : orbit elements at departure (for orbit_json)
        orbit_predictions : list of predicted orbit segments

    Returns None if the transfer cannot be computed as a burn plan
    for unrecognised locations.
    """
    cfg = _get_config()
    loc_map = _get_location_body_map()

    from_body = loc_map.get(from_location_id, "")
    to_body = loc_map.get(to_location_id, "")
    if not from_body or not to_body:
        return None

    # Resolve effective orbit parameters for each endpoint.
    # This handles orbit nodes, surface sites, and Lagrange points.
    from_resolved = _resolve_location_orbit_params(cfg, from_location_id)
    to_resolved = _resolve_location_orbit_params(cfg, to_location_id)
    if not from_resolved or not to_resolved:
        return None

    from_node_body = from_resolved["body_id"]
    to_node_body = to_resolved["body_id"]
    from_radius = from_resolved["radius_km"]
    to_radius = to_resolved["radius_km"]

    if from_radius <= 0 or to_radius <= 0:
        return None

    # Case 1: Same body — Hohmann transfer
    if from_node_body == to_node_body:
        return _plan_local_transfer(
            from_location_id, to_location_id,
            from_node_body, from_radius, to_radius,
            departure_time_s,
        )

    # Case 2: SOI transfer — different sub-bodies with a common parent
    # (e.g. Moon orbit ↔ Earth orbit, Io orbit ↔ Europa orbit)
    parent_body = transfer_planner._find_soi_parent(from_node_body, to_node_body)
    if parent_body:
        return _plan_soi_transfer(
            from_location_id, to_location_id,
            from_node_body, to_node_body,
            from_radius, to_radius,
            parent_body,
            departure_time_s,
        )

    # Case 3: Interplanetary — Lambert-based with patched conics
    return _plan_interplanetary_transfer(
        conn,
        from_location_id, to_location_id,
        from_node_body, to_node_body,
        from_radius, to_radius,
        departure_time_s,
    )


def _plan_local_transfer(
    from_location_id: str,
    to_location_id: str,
    body_id: str,
    from_radius_km: float,
    to_radius_km: float,
    departure_time_s: float,
) -> Optional[Dict[str, Any]]:
    """Plan a same-body Hohmann transfer (e.g. LEO → GEO)."""
    try:
        mu = _get_mu_for_body(body_id)
    except Exception:
        return None

    if abs(from_radius_km - to_radius_km) / max(from_radius_km, to_radius_km) < 0.001:
        # Same orbit — no transfer needed
        return None

    plan = orbit_service.hohmann_burn_sequence(
        mu, from_radius_km, to_radius_km, departure_time_s, body_id=body_id,
    )

    # Build initial orbit (circular at departure location)
    initial_orbit = orbit_service.circular_orbit(body_id, from_radius_km, mu, departure_time_s)

    # Build orbit predictions: departure orbit → transfer orbit → arrival orbit
    predictions = []

    # Departure orbit (until first burn)
    predictions.append({
        "from_s": departure_time_s,
        "to_s": plan["burns"][0]["time_s"],
        "body_id": body_id,
        "elements": dict(initial_orbit),
    })

    # Transfer orbit (between burns) — simulate burn 1
    transfer_orbit = orbit_service.apply_burn(
        initial_orbit, mu, plan["burns"][0]["time_s"],
        plan["burns"][0]["prograde_m_s"], plan["burns"][0]["radial_m_s"],
    )
    predictions.append({
        "from_s": plan["burns"][0]["time_s"],
        "to_s": plan["burns"][1]["time_s"],
        "body_id": body_id,
        "elements": dict(transfer_orbit),
    })

    # Arrival orbit (after burn 2) — simulate burn 2
    arrival_orbit = orbit_service.apply_burn(
        transfer_orbit, mu, plan["burns"][1]["time_s"],
        plan["burns"][1]["prograde_m_s"], plan["burns"][1]["radial_m_s"],
    )
    predictions.append({
        "from_s": plan["burns"][1]["time_s"],
        "to_s": None,
        "body_id": body_id,
        "elements": dict(arrival_orbit),
    })

    # Build maneuver entries
    burns = []
    for b in plan["burns"]:
        burns.append({
            "time_s": b["time_s"],
            "prograde_m_s": b["prograde_m_s"],
            "radial_m_s": b["radial_m_s"],
            "label": b.get("label", ""),
        })

    return {
        "burns": burns,
        "total_dv_m_s": plan["total_dv_m_s"],
        "total_tof_s": plan["total_tof_s"],
        "initial_orbit": initial_orbit,
        "orbit_body_id": body_id,
        "from_location_id": from_location_id,
        "to_location_id": to_location_id,
        "orbit_predictions": predictions,
        "transfer_type": "local_hohmann",
    }


def _plan_soi_transfer(
    from_location_id: str,
    to_location_id: str,
    from_node_body: str,
    to_node_body: str,
    from_radius_km: float,
    to_radius_km: float,
    parent_body: str,
    departure_time_s: float,
) -> Optional[Dict[str, Any]]:
    """Plan an SOI transfer between sub-bodies sharing a common parent.

    E.g. LLO (moon, 1837 km) → LEO (earth, 6778 km) with parent = earth.

    Models as a Hohmann transfer in the parent body's gravitational field:
      - Departure body distance from parent → one apse
      - Arrival body distance from parent → other apse

    For the departure parking orbit: circular around the departure body.
    For the transfer orbit: elliptical around the parent body.
    For the arrival parking orbit: circular around the arrival body.
    """
    cfg = _get_config()

    try:
        mu_parent = _get_mu_for_body(parent_body)
        mu_from = _get_mu_for_body(from_node_body)
    except Exception:
        return None

    # Get departure body distance from parent (in parent frame)
    # The "transfer distance" at the departure end is the sub-body's orbit radius.
    def _body_distance_from_parent(body_id: str, time_s: float) -> float:
        if body_id == parent_body:
            return 0.0
        body_r, _ = celestial_config.compute_body_state(cfg, body_id, time_s)
        parent_r, _ = celestial_config.compute_body_state(cfg, parent_body, time_s)
        return math.sqrt(
            (body_r[0] - parent_r[0]) ** 2
            + (body_r[1] - parent_r[1]) ** 2
            + (body_r[2] - parent_r[2]) ** 2
        )

    # Determine radii in the parent body's frame
    if from_node_body == parent_body:
        # Departing from the parent itself (e.g., LEO → LLO)
        r_depart_parent = from_radius_km
    else:
        r_depart_parent = _body_distance_from_parent(from_node_body, departure_time_s)

    if to_node_body == parent_body:
        r_arrive_parent = to_radius_km
    else:
        r_arrive_parent = _body_distance_from_parent(to_node_body, departure_time_s)

    if r_depart_parent <= 0 or r_arrive_parent <= 0:
        return None
    if abs(r_depart_parent - r_arrive_parent) / max(r_depart_parent, r_arrive_parent) < 0.001:
        return None

    # Hohmann transfer in the parent body's frame
    plan = orbit_service.hohmann_burn_sequence(
        mu_parent, r_depart_parent, r_arrive_parent, departure_time_s,
        body_id=parent_body,
    )

    arrival_time_s = departure_time_s + plan["total_tof_s"]

    # Initial orbit: circular parking orbit around the departure body
    initial_orbit = orbit_service.circular_orbit(
        from_node_body, from_radius_km, mu_from, departure_time_s,
    )

    # Burns
    burns = []
    for b in plan["burns"]:
        burns.append({
            "time_s": b["time_s"],
            "prograde_m_s": b["prograde_m_s"],
            "radial_m_s": b["radial_m_s"],
            "label": b.get("label", ""),
        })

    # Orbit predictions: departure orbit → transfer in parent frame → arrival orbit
    predictions = []

    # Phase 1: Departure parking orbit (until burn 1)
    predictions.append({
        "from_s": departure_time_s,
        "to_s": plan["burns"][0]["time_s"],
        "body_id": from_node_body,
        "elements": dict(initial_orbit),
    })

    # Phase 2: Transfer orbit in parent body's frame
    transfer_orbit = orbit_service.circular_orbit(
        parent_body, r_depart_parent, mu_parent, departure_time_s,
    )
    # Apply departure burn to get the transfer ellipse
    transfer_orbit = orbit_service.apply_burn(
        transfer_orbit, mu_parent, plan["burns"][0]["time_s"],
        plan["burns"][0]["prograde_m_s"], plan["burns"][0]["radial_m_s"],
    )
    predictions.append({
        "from_s": plan["burns"][0]["time_s"],
        "to_s": plan["burns"][1]["time_s"],
        "body_id": parent_body,
        "elements": dict(transfer_orbit),
    })

    # Phase 3: Arrival parking orbit (after burn 2)
    try:
        mu_to = _get_mu_for_body(to_node_body)
        arrival_orbit = orbit_service.circular_orbit(
            to_node_body, to_radius_km, mu_to, arrival_time_s,
        )
        predictions.append({
            "from_s": arrival_time_s,
            "to_s": None,
            "body_id": to_node_body,
            "elements": dict(arrival_orbit),
        })
    except Exception:
        pass

    return {
        "burns": burns,
        "total_dv_m_s": plan["total_dv_m_s"],
        "total_tof_s": plan["total_tof_s"],
        "initial_orbit": initial_orbit,
        "orbit_body_id": from_node_body,
        "from_location_id": from_location_id,
        "to_location_id": to_location_id,
        "orbit_predictions": predictions,
        "transfer_type": "soi_hohmann",
    }


def _plan_interplanetary_transfer(
    conn: sqlite3.Connection,
    from_location_id: str,
    to_location_id: str,
    from_node_body: str,
    to_node_body: str,
    from_radius_km: float,
    to_radius_km: float,
    departure_time_s: float,
) -> Optional[Dict[str, Any]]:
    """Plan an interplanetary transfer with departure burn + heliocentric coast + arrival burn.

    Uses the existing Lambert solver for the interplanetary leg, then
    decomposes into body-centric departure burn, heliocentric orbit, and
    body-centric arrival burn.
    """
    cfg = _get_config()

    # Resolve to heliocentric bodies
    from_helio = transfer_planner._resolve_heliocentric_body(from_node_body)
    to_helio = transfer_planner._resolve_heliocentric_body(to_node_body)
    if from_helio == to_helio:
        # Not actually interplanetary — fall back to local
        # This can happen for moon↔planet transfers
        return None

    # Get Lambert solution from existing planner
    orbital = transfer_planner.compute_interplanetary_leg(
        from_location=from_location_id,
        to_location=to_location_id,
        departure_time_s=departure_time_s,
    )
    if not orbital:
        return None

    # Extract key values
    dv_depart_m_s = float(orbital.get("dv_depart_m_s", 0.0))
    dv_arrive_m_s = float(orbital.get("dv_arrive_m_s", 0.0))
    base_tof_s = float(orbital.get("base_tof_s", 0.0))
    total_dv_m_s = float(orbital.get("base_dv_m_s", 0.0))
    arrival_time_s = float(orbital.get("arrival_time", departure_time_s + base_tof_s))

    # Build burn 1: departure burn (at from_node_body, in parking orbit)
    try:
        mu_from = _get_mu_for_body(from_node_body)
    except Exception:
        mu_from = _get_mu_for_body(from_helio)

    initial_orbit = orbit_service.circular_orbit(
        from_node_body, from_radius_km, mu_from, departure_time_s,
    )

    # Build burn 2: arrival burn (at to_node_body, circularization)
    burns = [
        {
            "time_s": departure_time_s,
            "prograde_m_s": dv_depart_m_s,
            "radial_m_s": 0.0,
            "label": f"Departure burn ({from_location_id})",
        },
        {
            "time_s": arrival_time_s,
            "prograde_m_s": -dv_arrive_m_s,  # Arrival burn is retrograde (capture)
            "radial_m_s": 0.0,
            "label": f"Arrival burn ({to_location_id})",
        },
    ]

    # Orbit predictions: we store departure orbit + heliocentric arc + arrival orbit
    # For the heliocentric leg, convert the Lambert solution to orbital elements
    helio_r1 = orbital.get("helio_r1")
    helio_v1 = orbital.get("helio_v1")
    helio_mu = float(orbital.get("helio_mu", 0.0))

    predictions = []
    predictions.append({
        "from_s": departure_time_s,
        "to_s": departure_time_s,
        "body_id": from_node_body,
        "elements": dict(initial_orbit),
    })

    if helio_r1 and helio_v1 and helio_mu > 0:
        r1_2d = (float(helio_r1[0]), float(helio_r1[1]))
        v1_2d = (float(helio_v1[0]), float(helio_v1[1]))
        helio_elems = orbit_service.state_to_elements(
            r1_2d, v1_2d, helio_mu, departure_time_s, body_id="sun",
        )
        predictions.append({
            "from_s": departure_time_s,
            "to_s": arrival_time_s,
            "body_id": "sun",
            "elements": helio_elems,
        })

    # Predicted arrival orbit
    try:
        mu_to = _get_mu_for_body(to_node_body)
        arrival_orbit = orbit_service.circular_orbit(
            to_node_body, to_radius_km, mu_to, arrival_time_s,
        )
        predictions.append({
            "from_s": arrival_time_s,
            "to_s": None,
            "body_id": to_node_body,
            "elements": dict(arrival_orbit),
        })
    except Exception:
        pass

    return {
        "burns": burns,
        "total_dv_m_s": total_dv_m_s,
        "total_tof_s": base_tof_s,
        "initial_orbit": initial_orbit,
        "orbit_body_id": from_node_body,
        "from_location_id": from_location_id,
        "to_location_id": to_location_id,
        "orbit_predictions": predictions,
        "transfer_type": "interplanetary_lambert",
    }


# ── Settle ship events (replaces settle_arrivals for orbit ships) ──────

def settle_ship_events(conn: sqlite3.Connection, now_s: float) -> None:
    """Process all pending orbital events (burns, SOI transitions) up to now_s.

    This is the orbit-model replacement for settle_arrivals().
    Only affects ships that have orbit_json set.
    Ships still using the legacy timer model (orbit_json IS NULL) are
    handled by the existing settle_arrivals().
    """
    rows = conn.execute(
        """SELECT id, orbit_json, maneuver_json, orbit_body_id
           FROM ships
           WHERE orbit_json IS NOT NULL
             AND maneuver_json IS NOT NULL
             AND maneuver_json != '[]'
             AND maneuver_json != ''"""
    ).fetchall()

    for row in rows:
        ship_id = row["id"]
        try:
            orbit = json.loads(row["orbit_json"])
            maneuvers = json.loads(row["maneuver_json"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("Ship %s has invalid orbit/maneuver JSON, skipping", ship_id)
            continue

        if not isinstance(maneuvers, list) or not maneuvers:
            continue

        changed = False
        while maneuvers and float(maneuvers[0].get("time_s", float("inf"))) <= now_s:
            m = maneuvers.pop(0)
            changed = True

            m_type = m.get("type", "burn")
            if m_type in ("soi_exit", "soi_enter"):
                orbit = _execute_soi_transition(orbit, m, now_s)
            else:
                orbit = _execute_burn(orbit, m)

        if changed:
            orbit_body_id = orbit.get("body_id", row["orbit_body_id"] or "")
            conn.execute(
                """UPDATE ships
                   SET orbit_json = ?,
                       maneuver_json = ?,
                       orbit_body_id = ?
                   WHERE id = ?""",
                (json.dumps(orbit), json.dumps(maneuvers), orbit_body_id, ship_id),
            )

    # Auto-dock: check free-flying ships with no remaining maneuvers
    _check_auto_docking(conn, now_s)


def _execute_burn(
    orbit: Dict[str, Any],
    maneuver: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a single burn maneuver and return the new orbit."""
    body_id = orbit.get("body_id", "")
    try:
        mu = _get_mu_for_body(body_id)
    except Exception:
        logger.warning("Cannot get mu for body %s, skipping burn", body_id)
        return orbit

    burn_time = float(maneuver.get("time_s", orbit.get("epoch_s", 0.0)))
    prograde = float(maneuver.get("prograde_m_s", 0.0))
    radial = float(maneuver.get("radial_m_s", 0.0))

    try:
        new_orbit = orbit_service.apply_burn(orbit, mu, burn_time, prograde, radial)
        logger.info(
            "Burn executed: body=%s dv=(%.1f, %.1f) m/s → a=%.1f km, e=%.4f",
            body_id, prograde, radial, new_orbit["a_km"], new_orbit["e"],
        )
        return new_orbit
    except Exception:
        logger.exception("Failed to execute burn for body %s", body_id)
        return orbit


def _execute_soi_transition(
    orbit: Dict[str, Any],
    maneuver: Dict[str, Any],
    now_s: float,
) -> Dict[str, Any]:
    """Execute an SOI transition (enter or exit)."""
    m_type = maneuver.get("type", "")
    to_body_id = maneuver.get("to_body_id", "")

    if not to_body_id:
        logger.warning("SOI transition has no to_body_id, skipping")
        return orbit

    transition_time = float(maneuver.get("time_s", now_s))
    current_body = orbit.get("body_id", "")

    cfg = _get_config()
    try:
        mu_current = celestial_config.get_body_mu(cfg, current_body)
    except Exception:
        logger.warning("Cannot get mu for %s in SOI transition", current_body)
        return orbit

    # Get ship state at transition time in current body frame
    r_local, v_local = orbit_service.elements_to_state(orbit, mu_current, transition_time)

    if m_type == "soi_exit":
        # Exiting current body → entering parent body
        try:
            body_r, body_v = _body_state_2d(current_body, transition_time)
            mu_parent = celestial_config.get_body_mu(cfg, to_body_id)
        except Exception:
            logger.warning("SOI exit failed: cannot resolve body states")
            return orbit

        new_orbit = orbit_service.transform_to_parent_frame(
            r_local, v_local, body_r, body_v, mu_parent, transition_time, to_body_id,
        )
        logger.info("SOI exit: %s → %s", current_body, to_body_id)
        return new_orbit

    elif m_type == "soi_enter":
        # Entering child body from parent frame
        try:
            child_r, child_v = _body_state_2d(to_body_id, transition_time)
            mu_child = celestial_config.get_body_mu(cfg, to_body_id)
        except Exception:
            logger.warning("SOI enter failed: cannot resolve body states")
            return orbit

        new_orbit = orbit_service.transform_to_child_frame(
            r_local, v_local, child_r, child_v, mu_child, transition_time, to_body_id,
        )
        logger.info("SOI enter: %s → %s", current_body, to_body_id)
        return new_orbit

    return orbit


def _body_state_2d(body_id: str, game_time_s: float) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Get body position and velocity in parent frame as 2D tuples."""
    cfg = _get_config()
    r3, v3 = celestial_config.compute_body_state(cfg, body_id, game_time_s)
    return (r3[0], r3[1]), (v3[0], v3[1])


# ── Auto-docking ──────────────────────────────────────────

def _check_auto_docking(conn: sqlite3.Connection, now_s: float) -> None:
    """Dock free-flying ships whose orbit matches a location.

    Only considers ships with:
    - location_id IS NULL (not already docked)
    - orbit_json IS NOT NULL (has orbit data)
    - maneuver_json IS NULL or '[]' (no pending maneuvers)

    Checks: (1) intended destination first (to_location_id), then
    (2) all orbit nodes at the same body as a fallback.
    """
    rows = conn.execute(
        """SELECT id, orbit_json, orbit_body_id, to_location_id
           FROM ships
           WHERE location_id IS NULL
             AND orbit_json IS NOT NULL
             AND (maneuver_json IS NULL OR maneuver_json = '[]' OR maneuver_json = '')"""
    ).fetchall()

    if not rows:
        return

    cfg = _get_config()

    # Build a lookup: body_id → [(location_id, radius_km), ...]
    orbit_nodes_by_body: Dict[str, List[Tuple[str, float]]] = {}
    for node in (cfg.get("orbit_nodes") or []):
        if not isinstance(node, dict):
            continue
        bid = str(node.get("body_id", "")).strip()
        nid = str(node.get("id", "")).strip()
        r = node.get("radius_km")
        if bid and nid and r is not None and float(r) > 0:
            orbit_nodes_by_body.setdefault(bid, []).append((nid, float(r)))

    def _dock_ship(ship_id: str, loc_id: str) -> None:
        conn.execute(
            """UPDATE ships
               SET location_id = ?,
                   from_location_id = NULL,
                   to_location_id = NULL,
                   departed_at = NULL,
                   arrives_at = NULL,
                   transit_from_x = NULL,
                   transit_from_y = NULL,
                   transit_to_x = NULL,
                   transit_to_y = NULL,
                   trajectory_json = NULL,
                   orbit_predictions_json = NULL
               WHERE id = ?""",
            (loc_id, ship_id),
        )
        logger.info("Auto-docked ship %s at %s", ship_id, loc_id)

    for row in rows:
        ship_id = row["id"]
        body_id = row["orbit_body_id"] or ""

        try:
            orbit = json.loads(row["orbit_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        # Priority 1: check intended destination (handles surface sites + L-points)
        to_loc = row["to_location_id"]
        if to_loc:
            resolved = _resolve_location_orbit_params(cfg, to_loc)
            if resolved:
                r_body = resolved["body_id"]
                r_radius = resolved["radius_km"]
                if r_body == body_id and orbit_service.orbit_matches_location(orbit, body_id, r_radius):
                    _dock_ship(ship_id, to_loc)
                    continue

        # Priority 2: check all orbit nodes at the same body
        nodes = orbit_nodes_by_body.get(body_id, [])
        for loc_id, radius_km in nodes:
            if orbit_service.orbit_matches_location(orbit, body_id, radius_km):
                _dock_ship(ship_id, loc_id)
                break


# ── Backfill: initialize orbit_json for docked ships ──────

def backfill_docked_orbits(conn: sqlite3.Connection, game_time_s: float) -> int:
    """Set orbit_json for docked ships that don't have one yet.

    Called during startup or migration to ensure all docked ships have
    orbital elements matching their location.

    Returns the number of ships updated.
    """
    rows = conn.execute(
        """SELECT id, location_id
           FROM ships
           WHERE location_id IS NOT NULL
             AND orbit_json IS NULL"""
    ).fetchall()

    count = 0
    for row in rows:
        ship_id = row["id"]
        location_id = row["location_id"]

        orbit = orbit_for_location(location_id, game_time_s)
        if orbit is None:
            # Location type not yet supported by the orbit model
            continue

        body_id = orbit.get("body_id", "")
        conn.execute(
            """UPDATE ships
               SET orbit_json = ?,
                   orbit_body_id = ?
               WHERE id = ?""",
            (json.dumps(orbit), body_id, ship_id),
        )
        count += 1
        logger.debug("Backfilled orbit for ship %s at %s", ship_id, location_id)

    if count:
        conn.commit()
        logger.info("Backfilled orbit_json for %d docked ships", count)

    return count
