"""
Orbit Bridge — connects orbit_service.py (pure math) to game config/DB.

Provides:
  - orbit_for_location()       : construct orbit elements for a docked ship
  - compute_transfer_burn_plan(): plan a transfer as a burn sequence
  - plan_chain_mission()       : LCA-based hierarchy walker for multi-leg missions
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
                sma = pos.get("semi_major_axis_km") or pos.get("a_km")
                if sma and float(sma) > 0:
                    return {"body_id": primary, "radius_km": float(sma)}

        # For collinear points (L1/L2/L3): use the configured distance
        distance_km = lp_info.get("distance_km")
        if distance_km and distance_km > 0:
            return {"body_id": primary, "radius_km": distance_km}

    return None


# ── Transfer burn plan computation ─────────────────────────


# ── Chain mission system (hierarchy walker) ────────────────


def _resolve_location_body(location_id: str) -> str:
    """Return the body that *location_id* is attached to."""
    return _get_location_body_map().get(location_id, "")


def _resolve_gateway_for_body(body_id: str) -> Optional[str]:
    """Return the standard gateway orbit location for *body_id*."""
    cfg = _get_config()
    return celestial_config.get_gateway_location(cfg, body_id)


def _get_surface_site_orbit_node(location_id: str) -> Optional[str]:
    """If *location_id* is a surface site, return its orbit_node location.

    Returns ``None`` if the location is not a surface site.
    """
    cfg = _get_config()
    site_info = celestial_config.get_surface_site_info(cfg, location_id)
    if site_info:
        return site_info.get("orbit_node_id")
    return None


def _classify_leg(
    from_body: str,
    to_body: str,
) -> str:
    """Classify a single leg: ``"local"``, ``"soi"``, or ``"interplanetary"``."""
    if from_body == to_body:
        return "local"
    parent = transfer_planner._find_soi_parent(from_body, to_body)
    if parent:
        return "soi"
    return "interplanetary"


def _build_chain_legs(
    from_location_id: str,
    to_location_id: str,
) -> Optional[List[Dict[str, str]]]:
    """Decompose *from* → *to* into an ordered list of transfer legs.

    Uses the celestial body hierarchy (LCA) to decide which gateway orbits
    to pass through.  Each element is ``{"from": loc_id, "to": loc_id}``.

    Returns ``None`` if either location is unknown.
    """
    loc_map = _get_location_body_map()
    from_body = loc_map.get(from_location_id, "")
    to_body = loc_map.get(to_location_id, "")
    if not from_body or not to_body:
        return None

    # Trivial case — same body or direct SOI/interplanetary
    leg_type = _classify_leg(from_body, to_body)

    # Resolve orbit params to check if a direct plan is possible
    cfg = _get_config()
    from_resolved = _resolve_location_orbit_params(cfg, from_location_id)
    to_resolved = _resolve_location_orbit_params(cfg, to_location_id)
    if not from_resolved or not to_resolved:
        return None

    from_node_body = from_resolved["body_id"]
    to_node_body = to_resolved["body_id"]

    if from_node_body == to_node_body:
        # Same orbital parent body — single local leg
        return [{"from": from_location_id, "to": to_location_id}]

    parent = transfer_planner._find_soi_parent(from_node_body, to_node_body)
    if parent:
        # SOI transfer — single leg
        return [{"from": from_location_id, "to": to_location_id}]

    # ── Multi-leg: use hierarchy walker ──
    # Build ancestry chains for both location bodies (not helio, actual bodies)
    from_helio = transfer_planner._resolve_heliocentric_body(from_node_body)
    to_helio = transfer_planner._resolve_heliocentric_body(to_node_body)

    if from_helio == to_helio:
        # Same heliocentric body but different local bodies with no direct
        # SOI parent.  Rare case — need to go via the helio body's gateway.
        gw = _resolve_gateway_for_body(from_helio)
        if not gw:
            return None
        legs: List[Dict[str, str]] = []
        if from_location_id != gw:
            legs.append({"from": from_location_id, "to": gw})
        if gw != to_location_id:
            legs.append({"from": gw, "to": to_location_id})
        return legs if legs else [{"from": from_location_id, "to": to_location_id}]

    # Different heliocentric bodies — full chain:
    #   1. Ascend from source to its gateway (if not already there)
    #   2. Interplanetary: gateway → gateway
    #   3. Descend from destination gateway to target (if not already there)

    legs = []

    # Ascend: source → departure gateway
    dep_gw = _resolve_gateway_for_body(from_helio)
    if not dep_gw:
        return None
    if from_location_id != dep_gw:
        # Check if there is a sub-body to escape from first
        # e.g. Lunar Surface → LLO → LEO
        _build_ascent_legs(from_location_id, from_node_body, from_helio, dep_gw, legs)
    else:
        pass  # already at departure gateway

    # Interplanetary leg
    arr_gw = _resolve_gateway_for_body(to_helio)
    if not arr_gw:
        return None
    legs.append({"from": dep_gw, "to": arr_gw})

    # Descend: arrival gateway → destination
    if to_location_id != arr_gw:
        _build_descent_legs(arr_gw, to_location_id, to_node_body, to_helio, legs)

    return legs


def _build_ascent_legs(
    from_location_id: str,
    from_node_body: str,
    helio_body: str,
    dep_gw: str,
    legs: List[Dict[str, str]],
) -> None:
    """Build ascent legs from *from_location_id* up to *dep_gw*.

    Handles cases like:
      - Lunar Surface → LLO → LEO (Moon body → Earth gateway)
      - LLO → LEO (Moon orbit → Earth gateway)
      - some_body_orbit → helio_gateway

    Surface sites that resolve to the same orbit as their orbit_node
    (e.g. LUNA_SHACKLETON → LLO) are NOT split into a separate local
    leg — the SOI planner handles them as a single departure.
    """
    if from_node_body != helio_body:
        sub_gw = _resolve_gateway_for_body(from_node_body)

        # Check if the from_location is a surface site whose orbit_node
        # IS the sub-body gateway.  If so, skip the degenerate local leg
        # (surface resolves to same orbit as orbit_node, so dv=0).
        orbit_node = _get_surface_site_orbit_node(from_location_id)
        if orbit_node and orbit_node == sub_gw:
            # Surface site is already "at" the sub_gw orbit —
            # just add the SOI leg directly from the surface.
            if from_location_id != dep_gw:
                legs.append({"from": from_location_id, "to": dep_gw})
            return

        if sub_gw and sub_gw != from_location_id:
            legs.append({"from": from_location_id, "to": sub_gw})
        # SOI from sub-body gateway to helio gateway
        source = sub_gw or from_location_id
        if source != dep_gw:
            legs.append({"from": source, "to": dep_gw})
    else:
        # Source is on the helio body itself — direct local hop to gateway
        legs.append({"from": from_location_id, "to": dep_gw})


def _build_descent_legs(
    arr_gw: str,
    to_location_id: str,
    to_node_body: str,
    helio_body: str,
    legs: List[Dict[str, str]],
) -> None:
    """Build descent legs from *arr_gw* down to *to_location_id*.

    Mirror of ``_build_ascent_legs``.

    Surface sites that resolve to the same orbit as their orbit_node
    are NOT split into a separate local leg — the SOI planner handles
    arrival at the surface directly.
    """
    if to_node_body != helio_body:
        sub_gw = _resolve_gateway_for_body(to_node_body)

        # Check if the destination is a surface site whose orbit_node
        # IS the sub-body gateway — skip degenerate local descent.
        orbit_node = _get_surface_site_orbit_node(to_location_id)
        if orbit_node and orbit_node == sub_gw:
            # Destination surface is "at" the sub_gw orbit —
            # just add the SOI leg directly to the surface.
            if arr_gw != to_location_id:
                legs.append({"from": arr_gw, "to": to_location_id})
            return

        # SOI from helio gateway to sub-body gateway
        target = sub_gw or to_location_id
        if arr_gw != target:
            legs.append({"from": arr_gw, "to": target})
        # Local descent from sub-body gateway to destination
        if sub_gw and sub_gw != to_location_id:
            legs.append({"from": sub_gw, "to": to_location_id})
    else:
        # Destination is on the helio body itself — direct local descent
        legs.append({"from": arr_gw, "to": to_location_id})


def orbit_summary(elements: Dict[str, Any]) -> Dict[str, Any]:
    """Return a human-readable summary of an orbit.

    Keys: ``body``, ``type`` ("circular"|"elliptical"|"hyperbolic"),
    ``altitude_km``, ``pe_km``, ``ap_km``, ``period_s``, ``ecc``.
    """
    body_id = str(elements.get("body_id", ""))
    a_km = float(elements.get("a_km", 0))
    e = float(elements.get("e", 0))

    try:
        mu = _get_mu_for_body(body_id)
    except Exception:
        mu = 0.0

    cfg = _get_config()
    body_radius = celestial_config.get_body_radius(cfg, body_id) or 0.0

    pe_km = a_km * (1 - e)
    if e < 1.0:
        ap_km = a_km * (1 + e)
    else:
        ap_km = float("inf")

    pe_alt = pe_km - body_radius
    ap_alt = ap_km - body_radius if ap_km < float("inf") else float("inf")

    if e < 0.01:
        orbit_type = "circular"
    elif e < 1.0:
        orbit_type = "elliptical"
    else:
        orbit_type = "hyperbolic"

    period_s = 0.0
    if e < 1.0 and a_km > 0 and mu > 0:
        period_s = orbit_service.orbital_period(a_km, mu)

    return {
        "body": body_id,
        "type": orbit_type,
        "altitude_km": round(pe_alt, 1) if orbit_type == "circular" else None,
        "pe_km": round(pe_alt, 1),
        "ap_km": round(ap_alt, 1) if ap_alt < float("inf") else None,
        "period_s": round(period_s, 1) if period_s > 0 else None,
        "ecc": round(e, 6),
    }


def plan_chain_mission(
    conn: sqlite3.Connection,
    from_location_id: str,
    to_location_id: str,
    departure_time_s: float,
    current_orbit: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Plan a full chain mission from *from_location_id* to *to_location_id*.

    Uses the body hierarchy to decompose the route into legs, plans each
    leg with the appropriate planner (local Hohmann, SOI, or interplanetary
    Lambert), and returns a unified burn plan.

    Returns ``None`` if no route can be computed.

    Return dict:
        legs           : list of per-leg plan dicts
        burns          : concatenated list of all burns
        total_dv_m_s   : sum of all leg Δv
        total_tof_s    : total mission time
        initial_orbit  : orbit elements at departure
        orbit_body_id  : body for the initial orbit
        orbit_predictions : concatenated prediction segments
        from_location_id : original source
        to_location_id   : final destination
        transfer_type  : "chain_mission" | "local_hohmann" | "soi_hohmann" | "interplanetary_lambert"
    """
    if from_location_id == to_location_id:
        return None

    chain = _build_chain_legs(from_location_id, to_location_id)
    if not chain:
        return None

    # Single leg — delegate to compute_transfer_burn_plan and wrap with legs key
    if len(chain) == 1:
        result = compute_transfer_burn_plan(
            conn, chain[0]["from"], chain[0]["to"], departure_time_s,
            current_orbit=current_orbit,
        )
        if result:
            from_summary = orbit_summary(result["initial_orbit"])
            preds = result.get("orbit_predictions", [])
            to_summary = orbit_summary(preds[-1]["elements"]) if preds else from_summary
            result["legs"] = [{
                "index": 0,
                "from_id": chain[0]["from"],
                "to_id": chain[0]["to"],
                "dv_m_s": float(result["total_dv_m_s"]),
                "tof_s": float(result["total_tof_s"]),
                "transfer_type": result.get("transfer_type", ""),
                "burn_count": len(result.get("burns", [])),
                "from_orbit": from_summary,
                "to_orbit": to_summary,
            }]
            result["leg_count"] = 1
        return result

    # ── Multi-leg: identify the "anchor" interplanetary leg (if any) ──
    # Interplanetary legs dictate overall timing; other legs are planned
    # backwards (ascent) or forwards (descent) from the anchor.
    cfg = _get_config()
    anchor_idx: Optional[int] = None
    for i, leg in enumerate(chain):
        fb = _resolve_location_body(leg["from"])
        tb = _resolve_location_body(leg["to"])
        fb_r = _resolve_location_orbit_params(cfg, leg["from"])
        tb_r = _resolve_location_orbit_params(cfg, leg["to"])
        if fb_r and tb_r:
            fb_node = fb_r["body_id"]
            tb_node = tb_r["body_id"]
            fh = transfer_planner._resolve_heliocentric_body(fb_node)
            th = transfer_planner._resolve_heliocentric_body(tb_node)
            if fh != th:
                anchor_idx = i
                break

    # ── Forward planning: plan each leg sequentially ──
    # (For Phase 1, we plan forward from departure time.  Backward planning
    # from an interplanetary anchor will be added when we implement departure
    # window selection — for now, the Lambert planner already picks the best
    # TOF at whatever departure time it receives.)
    all_legs: List[Dict[str, Any]] = []
    all_burns: List[Dict[str, Any]] = []
    all_predictions: List[Dict[str, Any]] = []
    worst_intercept: Dict[str, Any] = {"ok": True}
    cursor_time = departure_time_s
    total_dv = 0.0
    initial_orbit = None
    orbit_body_id = None

    for i, leg in enumerate(chain):
        leg_plan = compute_transfer_burn_plan(
            conn, leg["from"], leg["to"], cursor_time,
            current_orbit=current_orbit if i == 0 else None,
        )
        if not leg_plan:
            logger.warning(
                "Chain mission leg %d (%s → %s) failed to plan",
                i + 1, leg["from"], leg["to"],
            )
            return None

        # Number burns sequentially across the whole chain
        burn_offset = len(all_burns)
        leg_burns = []
        for b in leg_plan["burns"]:
            numbered = dict(b)
            numbered["leg_index"] = i
            leg_burns.append(numbered)

        all_burns.extend(leg_burns)
        all_predictions.extend(leg_plan.get("orbit_predictions", []))
        total_dv += float(leg_plan["total_dv_m_s"])

        # Track worst intercept check across all legs
        leg_ic = leg_plan.get("intercept_check", {"ok": True})
        if not leg_ic.get("ok", True):
            if worst_intercept.get("ok", True) or leg_ic.get("miss_ratio", 0) > worst_intercept.get("miss_ratio", 0):
                worst_intercept = leg_ic

        from_summary = orbit_summary(leg_plan["initial_orbit"])
        # Build arrival orbit summary from the last prediction
        preds = leg_plan.get("orbit_predictions", [])
        to_summary = orbit_summary(preds[-1]["elements"]) if preds else from_summary

        all_legs.append({
            "index": i,
            "from_id": leg["from"],
            "to_id": leg["to"],
            "dv_m_s": float(leg_plan["total_dv_m_s"]),
            "tof_s": float(leg_plan["total_tof_s"]),
            "transfer_type": leg_plan.get("transfer_type", ""),
            "burn_count": len(leg_burns),
            "from_orbit": from_summary,
            "to_orbit": to_summary,
        })

        # Track initial orbit from the first leg
        if i == 0:
            initial_orbit = leg_plan["initial_orbit"]
            orbit_body_id = leg_plan.get("orbit_body_id", "")

        cursor_time += float(leg_plan["total_tof_s"])

    if not initial_orbit:
        return None

    total_tof = cursor_time - departure_time_s

    return {
        "legs": all_legs,
        "burns": all_burns,
        "total_dv_m_s": total_dv,
        "total_tof_s": total_tof,
        "initial_orbit": initial_orbit,
        "orbit_body_id": orbit_body_id,
        "from_location_id": from_location_id,
        "to_location_id": to_location_id,
        "orbit_predictions": all_predictions,
        "transfer_type": "chain_mission",
        "leg_count": len(all_legs),
        "intercept_check": worst_intercept,
    }


def compute_transfer_burn_plan(
    conn: sqlite3.Connection,
    from_location_id: str,
    to_location_id: str,
    departure_time_s: float,
    current_orbit: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Compute a physics-based transfer as a sequence of burns.

    Parameters
    ----------
    current_orbit : The ship's existing orbital elements (from orbit_json).
        When provided, the burn plan preserves the ship's angular position
        so the visual transition from docked → transit is seamless.

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
            current_orbit=current_orbit,
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
            current_orbit=current_orbit,
        )

    # Case 3: Interplanetary — Lambert-based with patched conics
    return _plan_interplanetary_transfer(
        conn,
        from_location_id, to_location_id,
        from_node_body, to_node_body,
        from_radius, to_radius,
        departure_time_s,
        current_orbit=current_orbit,
    )


# ── Intercept validation ────────────────────────────────────

def _compute_intercept_check(
    transfer_orbit: Dict[str, Any],
    parent_body: str,
    dest_sub_body: str,
    arrival_time_s: float,
) -> Dict[str, Any]:
    """Validate that a transfer orbit actually intercepts the destination body.

    Computes the ship's predicted position and the destination body's
    position at arrival time, both in the parent body frame.
    Returns a dict with miss distance, SOI radius, and pass/fail.
    """
    cfg = _get_config()

    try:
        mu_parent = celestial_config.get_body_mu(cfg, parent_body)
    except Exception:
        return {"ok": True, "reason": "cannot_resolve_mu"}

    # Ship position at arrival time in parent frame (km)
    try:
        ship_pos = orbit_service.propagate_position(transfer_orbit, mu_parent, arrival_time_s)
    except Exception:
        return {"ok": True, "reason": "propagation_failed"}

    # Destination body position at arrival time relative to parent (km)
    try:
        body_r, _ = celestial_config.compute_body_state(cfg, dest_sub_body, arrival_time_s)
        parent_r, _ = celestial_config.compute_body_state(cfg, parent_body, arrival_time_s)
        dest_dx = body_r[0] - parent_r[0]
        dest_dy = body_r[1] - parent_r[1]
    except Exception:
        return {"ok": True, "reason": "body_state_failed"}

    miss_km = math.sqrt(
        (ship_pos[0] - dest_dx) ** 2 + (ship_pos[1] - dest_dy) ** 2
    )

    soi_km = celestial_config.get_body_soi(cfg, dest_sub_body)
    if soi_km is None:
        # No SOI defined — use a generous fallback (10% of body distance)
        body_dist = math.sqrt(dest_dx ** 2 + dest_dy ** 2)
        soi_km = body_dist * 0.1

    ok = miss_km <= soi_km
    return {
        "ok": ok,
        "miss_km": round(miss_km, 1),
        "soi_radius_km": round(soi_km, 1),
        "miss_ratio": round(miss_km / max(1.0, soi_km), 2),
        "dest_body": dest_sub_body,
    }


def _compute_helio_intercept_check(
    helio_orbit: Dict[str, Any],
    dest_helio_body: str,
    arrival_time_s: float,
) -> Dict[str, Any]:
    """Validate intercept for a heliocentric (interplanetary) transfer.

    Computes ship position and destination body position at arrival,
    both in heliocentric frame.
    """
    cfg = _get_config()

    try:
        mu_sun = celestial_config.get_body_mu(cfg, "sun")
    except Exception:
        return {"ok": True, "reason": "cannot_resolve_sun_mu"}

    try:
        ship_pos = orbit_service.propagate_position(helio_orbit, mu_sun, arrival_time_s)
    except Exception:
        return {"ok": True, "reason": "propagation_failed"}

    try:
        body_r, _ = celestial_config.compute_body_state(cfg, dest_helio_body, arrival_time_s)
    except Exception:
        return {"ok": True, "reason": "body_state_failed"}

    miss_km = math.sqrt(
        (ship_pos[0] - body_r[0]) ** 2 + (ship_pos[1] - body_r[1]) ** 2
    )

    soi_km = celestial_config.get_body_soi(cfg, dest_helio_body)
    if soi_km is None:
        soi_km = 1e6  # generous fallback for unknown SOI

    ok = miss_km <= soi_km
    return {
        "ok": ok,
        "miss_km": round(miss_km, 1),
        "soi_radius_km": round(soi_km, 1),
        "miss_ratio": round(miss_km / max(1.0, soi_km), 2),
        "dest_body": dest_helio_body,
    }


def _plan_local_transfer(
    from_location_id: str,
    to_location_id: str,
    body_id: str,
    from_radius_km: float,
    to_radius_km: float,
    departure_time_s: float,
    current_orbit: Optional[Dict[str, Any]] = None,
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

    # Build initial orbit — reuse the ship's actual orbit when available so
    # that the angular position is continuous (no visual teleport).
    if (
        current_orbit
        and current_orbit.get("body_id") == body_id
        and abs(float(current_orbit.get("a_km", 0)) - from_radius_km)
            / max(from_radius_km, 1.0) < 0.01
    ):
        initial_orbit = dict(current_orbit)
    else:
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
    current_orbit: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Plan an SOI transfer between sub-bodies sharing a common parent.

    E.g. LEO (earth, 6778 km) → LLO (moon, 1837 km) with parent = earth.

    Models as a Hohmann transfer in the parent body's gravitational field,
    oriented so the transfer ellipse aims at the target sub-body's position.

    The ship coasts in the parking orbit until it reaches the optimal
    departure angle, then fires the injection burn.  This produces a
    realistic "wait for departure window" phase before the transfer.

    The initial orbit and transfer orbit are always in the parent body's frame
    so that settle_ship_events can apply burns using the correct μ.
    """
    cfg = _get_config()

    try:
        mu_parent = _get_mu_for_body(parent_body)
    except Exception:
        return None

    # ── Helpers ──
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

    def _body_angle_from_parent(body_id: str, time_s: float) -> float:
        """Angular position (rad) of body relative to parent (helio 2D)."""
        body_r, _ = celestial_config.compute_body_state(cfg, body_id, time_s)
        parent_r, _ = celestial_config.compute_body_state(cfg, parent_body, time_s)
        return math.atan2(body_r[1] - parent_r[1], body_r[0] - parent_r[0])

    # ── Determine radii in the parent body's frame ──
    if from_node_body == parent_body:
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

    # ── Hohmann parameters ──
    a_transfer = 0.5 * (r_depart_parent + r_arrive_parent)
    hohmann_tof_s = math.pi * math.sqrt(a_transfer ** 3 / mu_parent)
    going_outward = r_depart_parent < r_arrive_parent

    # Identify the sub-body whose position determines orientation
    if to_node_body != parent_body:
        sub_body = to_node_body
    elif from_node_body != parent_body:
        sub_body = from_node_body
    else:
        sub_body = None

    # ── Compute coast phase (departure window) ──
    # The ship coasts in a circular parking orbit until it reaches
    # the correct departure angle for the Hohmann injection burn.
    #
    # For outward transfers: ship departs from periapsis, arrives at
    #   apoapsis.  We orient the ellipse so apoapsis aims at the target
    #   body's position at arrival time.
    # For inward transfers: ship is at the sub-body's angular position
    #   and fires retrograde immediately.

    n_park = orbit_service.mean_motion(r_depart_parent, mu_parent)
    park_period_s = orbit_service.orbital_period(r_depart_parent, mu_parent)

    # Determine the ship's actual angular position in the parent frame so
    # the coast starts from the right place (avoids visual teleport).
    ship_start_angle_rad = 0.0
    if (
        current_orbit
        and current_orbit.get("body_id") == parent_body
        and n_park > 0
    ):
        # Ship is directly orbiting the parent body — use its real angle
        try:
            ship_start_angle_rad = math.radians(
                orbit_service.angular_position_deg(current_orbit, mu_parent, departure_time_s)
            )
        except Exception:
            ship_start_angle_rad = 0.0
    elif current_orbit and from_node_body != parent_body:
        # Ship orbits a sub-body (e.g. LLO around Moon); in the parent
        # frame its position is ~the sub-body's angular position.
        try:
            ship_start_angle_rad = _body_angle_from_parent(from_node_body, departure_time_s)
        except Exception:
            ship_start_angle_rad = 0.0

    if sub_body and sub_body == to_node_body and n_park > 0:
        # ── Synodic phase targeting ──
        # Hohmann transfers traverse exactly 180°: the ship departs at
        # angle θ_d and arrives at θ_d + π.  We need:
        #   ship_start + n_park·coast + π = body_angle(sub_body, t0 + coast + TOF)
        #
        # Linearising the target body's motion as θ_target ≈ θ0 + n_target·t
        # gives an analytical first estimate; Newton refinement (using the
        # real ephemeris) converges to <1 km in 3-4 iterations.
        n_target = orbit_service.mean_motion(r_arrive_parent, mu_parent)
        synodic_n = n_park - n_target
        if abs(synodic_n) < 1e-20:
            synodic_n = 1e-20  # avoid division by zero for co-orbital case
        synodic_period_s = 2 * math.pi / abs(synodic_n)

        # Analytical first estimate (linear body motion)
        theta_target_0 = _body_angle_from_parent(sub_body, departure_time_s)
        needed = theta_target_0 + n_target * hohmann_tof_s - ship_start_angle_rad - math.pi
        coast_s = needed / synodic_n
        coast_s = coast_s % synodic_period_s
        if coast_s < 60:
            coast_s += synodic_period_s

        # Newton refinement (3 iterations — converges to <1 km)
        for _ in range(3):
            t_arrive = departure_time_s + coast_s + hohmann_tof_s
            actual_target_angle = _body_angle_from_parent(sub_body, t_arrive)
            ship_at_arrive = ship_start_angle_rad + n_park * coast_s + math.pi
            error = (actual_target_angle - ship_at_arrive + math.pi) % (2 * math.pi) - math.pi
            coast_s += error / synodic_n

        # Normalise to a positive coast ≥ 60s
        coast_s = coast_s % synodic_period_s
        if coast_s < 60:
            coast_s += synodic_period_s

        departure_angle_rad = ship_start_angle_rad + n_park * coast_s
        departure_angle_deg = math.degrees(departure_angle_rad)
        burn1_time = departure_time_s + coast_s

    elif sub_body and sub_body == from_node_body:
        # Transferring away from a sub-body toward the parent's parking
        # orbit — no phase constraint (destination is a circular orbit
        # with no specific angular position to hit).  Depart immediately
        # from the sub-body's current angular position.
        departure_angle_rad = _body_angle_from_parent(sub_body, departure_time_s)
        departure_angle_deg = math.degrees(departure_angle_rad)
        ship_start_angle_rad = departure_angle_rad
        burn1_time = departure_time_s
        coast_s = 0.0

    else:
        departure_angle_rad = ship_start_angle_rad
        departure_angle_deg = math.degrees(ship_start_angle_rad)
        burn1_time = departure_time_s
        coast_s = 0.0

    # ── Hohmann burn sequence (anchored at burn1_time) ──
    plan = orbit_service.hohmann_burn_sequence(
        mu_parent, r_depart_parent, r_arrive_parent, burn1_time,
        body_id=parent_body,
    )

    burn2_time = burn1_time + hohmann_tof_s
    total_tof_s = coast_s + hohmann_tof_s

    # ── Build initial orbit in parent body's frame ──
    # Use the ship's actual starting angle so there is no visual jump.
    start_angle_deg = math.degrees(ship_start_angle_rad)
    initial_orbit = orbit_service.circular_orbit(
        parent_body, r_depart_parent, mu_parent, departure_time_s,
        angle_deg=start_angle_deg,
    )

    # Burns: only the injection burn goes into the parent-frame maneuver list.
    # The circularization burn happens in the destination body's frame
    # after the SOI transition.
    burns = [{
        "time_s": plan["burns"][0]["time_s"],
        "prograde_m_s": plan["burns"][0]["prograde_m_s"],
        "radial_m_s": plan["burns"][0]["radial_m_s"],
        "label": plan["burns"][0].get("label", "Injection burn"),
    }]

    # ── SOI transition: compute approach hyperbola in destination frame ──
    # The Hohmann apoapsis coincides (approximately) with the destination
    # body's position.  At that exact point the relative position is ~0
    # which makes state_to_elements degenerate.  Instead, we step backwards
    # from apoapsis to find when the ship crosses the Moon's SOI boundary,
    # then compute the approach orbit from that point.
    #
    # IMPORTANT: The transfer orbit is in the *parent body frame* (Earth-cen-
    # tered).  We must get the child body (Moon) position in the same frame.
    # `_body_state_2d` returns heliocentric coords, so we subtract the parent
    # body's heliocentric position to get Earth-centered Moon coords.
    approach_orbit = None
    approach_duration_s = 0.0
    soi_enter_time = burn2_time  # default: apoapsis
    circularization_time = burn2_time
    try:
        mu_to = _get_mu_for_body(to_node_body)
        cfg_loc = _get_config()
        soi_radius_km = celestial_config.get_body_soi(cfg_loc, to_node_body)

        # Compute the transfer orbit elements
        transfer_orbit_tmp = orbit_service.apply_burn(
            dict(initial_orbit), mu_parent, burn1_time,
            plan["burns"][0]["prograde_m_s"], plan["burns"][0]["radial_m_s"],
        )

        if soi_radius_km and soi_radius_km > 0:
            # Binary search for the time when ship-to-child distance = SOI radius.
            # Search window: from 50% of Hohmann TOF to apoapsis.
            t_lo = burn1_time + hohmann_tof_s * 0.5
            t_hi = burn2_time
            for _ in range(40):  # bisection converges fast
                t_mid = 0.5 * (t_lo + t_hi)
                r_ship, _ = orbit_service.elements_to_state(transfer_orbit_tmp, mu_parent, t_mid)
                r_child, _ = _body_state_in_parent_frame_2d(to_node_body, parent_body, t_mid)
                sep = math.sqrt((r_ship[0] - r_child[0]) ** 2 + (r_ship[1] - r_child[1]) ** 2)
                if sep > soi_radius_km:
                    t_lo = t_mid
                else:
                    t_hi = t_mid
                if abs(t_hi - t_lo) < 10:  # <10 s precision
                    break
            soi_enter_time = 0.5 * (t_lo + t_hi)

            # Now compute ship and Moon state at SOI entry (both in parent frame)
            r_parent_pos, v_parent_vel = orbit_service.elements_to_state(
                transfer_orbit_tmp, mu_parent, soi_enter_time,
            )
            child_r, child_v = _body_state_in_parent_frame_2d(to_node_body, parent_body, soi_enter_time)
            approach_orbit = orbit_service.transform_to_child_frame(
                r_parent_pos, v_parent_vel, child_r, child_v, mu_to,
                soi_enter_time, to_node_body,
            )
            # Estimate approach duration (SOI entry → periapsis)
            approach_duration_s = burn2_time - soi_enter_time + min(hohmann_tof_s * 0.1, 3600 * 6)
            circularization_time = soi_enter_time + approach_duration_s
        else:
            # No SOI radius defined — fall back to computing at apoapsis
            r_parent_pos, v_parent_vel = orbit_service.elements_to_state(
                transfer_orbit_tmp, mu_parent, burn2_time,
            )
            child_r, child_v = _body_state_in_parent_frame_2d(to_node_body, parent_body, burn2_time)
            approach_orbit = orbit_service.transform_to_child_frame(
                r_parent_pos, v_parent_vel, child_r, child_v, mu_to,
                burn2_time, to_node_body,
            )
            approach_duration_s = min(hohmann_tof_s * 0.25, 3600 * 12)
            circularization_time = burn2_time + approach_duration_s
    except Exception:
        logger.debug("SOI approach orbit computation failed for %s → %s",
                      from_location_id, to_location_id)

    # Insert SOI enter maneuver so settle_ship_events transitions the
    # ship's orbit state from the parent frame to the destination body frame.
    burns.append({
        "type": "soi_enter",
        "time_s": soi_enter_time,
        "to_body_id": to_node_body,
    })
    # Note: circularization in the destination body's frame is handled by
    # auto-docking — once the SOI transition puts the ship in the right
    # body frame with low relative velocity, orbit_matches_location will
    # trigger a dock at the destination.

    # ── Orbit predictions ──
    predictions = []

    # Phase 1: Coast in parking orbit until burn 1
    predictions.append({
        "from_s": departure_time_s,
        "to_s": burn1_time,
        "body_id": parent_body,
        "elements": dict(initial_orbit),
    })

    # Phase 2: Transfer orbit in parent body's frame
    # Simulate the departure burn to get the transfer ellipse elements.
    # For outward: the ship will be at departure_angle_rad at burn1_time
    # (because M0=0 and it coasts for coast_s = departure_angle/n).
    transfer_orbit = orbit_service.apply_burn(
        dict(initial_orbit), mu_parent, burn1_time,
        plan["burns"][0]["prograde_m_s"], plan["burns"][0]["radial_m_s"],
    )
    predictions.append({
        "from_s": burn1_time,
        "to_s": soi_enter_time,
        "body_id": parent_body,
        "elements": dict(transfer_orbit),
    })

    # Phase 3: Hyperbolic approach in the destination body's frame.
    # Only include if the frame transform produced valid elements.
    # At Hohmann apoapsis the ship-to-moon relative position can be
    # near-zero, making state_to_elements degenerate (huge e, tiny a).
    approach_valid = (
        approach_orbit
        and abs(float(approach_orbit.get("a_km", 0))) > 50
        and float(approach_orbit.get("e", 0)) < 100
    )
    if approach_valid:
        predictions.append({
            "from_s": soi_enter_time,
            "to_s": circularization_time,
            "body_id": to_node_body,
            "elements": dict(approach_orbit),
        })
    else:
        # Skip degenerate approach orbit; reset circularization time
        circularization_time = soi_enter_time

    # Phase 4: Arrival parking orbit around the destination body
    try:
        mu_to = _get_mu_for_body(to_node_body)
        arrival_orbit = orbit_service.circular_orbit(
            to_node_body, to_radius_km, mu_to, circularization_time,
        )
        predictions.append({
            "from_s": circularization_time,
            "to_s": None,
            "body_id": to_node_body,
            "elements": dict(arrival_orbit),
        })
    except Exception:
        pass

    # ── Intercept validation ──
    # Check that the transfer orbit actually reaches the destination body
    intercept_check = {"ok": True}
    if to_node_body != parent_body:
        intercept_check = _compute_intercept_check(
            transfer_orbit, parent_body, to_node_body, burn2_time,
        )
        if not intercept_check["ok"]:
            logger.warning(
                "SOI transfer %s → %s: intercept miss %.0f km (SOI %.0f km, ratio %.1f)",
                from_location_id, to_location_id,
                intercept_check["miss_km"], intercept_check["soi_radius_km"],
                intercept_check["miss_ratio"],
            )

    # total_tof_s includes approach duration for accurate ETA
    total_tof_s = coast_s + hohmann_tof_s + (approach_duration_s if approach_valid else 0.0)

    return {
        "burns": burns,
        "total_dv_m_s": plan["total_dv_m_s"],
        "total_tof_s": total_tof_s,
        "initial_orbit": initial_orbit,
        "orbit_body_id": parent_body,
        "from_location_id": from_location_id,
        "to_location_id": to_location_id,
        "orbit_predictions": predictions,
        "transfer_type": "soi_hohmann",
        "intercept_check": intercept_check,
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
    current_orbit: Optional[Dict[str, Any]] = None,
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

    # Reuse the ship's actual orbit when available to avoid visual teleport
    if (
        current_orbit
        and current_orbit.get("body_id") == from_node_body
        and abs(float(current_orbit.get("a_km", 0)) - from_radius_km)
            / max(from_radius_km, 1.0) < 0.01
    ):
        initial_orbit = dict(current_orbit)
    else:
        initial_orbit = orbit_service.circular_orbit(
            from_node_body, from_radius_km, mu_from, departure_time_s,
        )

    # Build maneuver sequence:
    #   1. Departure burn (prograde, at from_node_body)
    #   2. SOI exit (from_node_body → sun)
    #   3. SOI enter (sun → to_node_body)
    #   4. Arrival burn (retrograde capture, at to_node_body)
    burns = [
        {
            "time_s": departure_time_s,
            "prograde_m_s": dv_depart_m_s,
            "radial_m_s": 0.0,
            "label": f"Departure burn ({from_location_id})",
        },
        {
            "type": "soi_exit",
            "time_s": departure_time_s,
            "to_body_id": "sun",
        },
        {
            "type": "soi_enter",
            "time_s": arrival_time_s,
            "to_body_id": to_node_body,
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

    # ── Intercept validation ──
    intercept_check = {"ok": True}
    to_helio = transfer_planner._resolve_heliocentric_body(to_node_body)
    if helio_r1 and helio_v1 and helio_mu > 0 and to_helio:
        helio_check_orbit = None
        # Use the heliocentric orbit elements if we computed them
        for pred in predictions:
            if pred.get("body_id") == "sun" and pred.get("elements"):
                helio_check_orbit = pred["elements"]
                break
        if helio_check_orbit:
            intercept_check = _compute_helio_intercept_check(
                helio_check_orbit, to_helio, arrival_time_s,
            )
            if not intercept_check["ok"]:
                logger.warning(
                    "Interplanetary transfer %s → %s: intercept miss %.0f km (SOI %.0f km, ratio %.1f)",
                    from_location_id, to_location_id,
                    intercept_check["miss_km"], intercept_check["soi_radius_km"],
                    intercept_check["miss_ratio"],
                )

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
        "intercept_check": intercept_check,
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
        # Exiting current body → entering parent body (to_body_id).
        # body_r/v must be current_body's position in the parent frame.
        try:
            body_r, body_v = _body_state_in_parent_frame_2d(
                current_body, to_body_id, transition_time,
            )
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
        # Entering child body (to_body_id) from parent frame (current_body).
        # child_r/v must be child's position in the parent frame.
        try:
            child_r, child_v = _body_state_in_parent_frame_2d(
                to_body_id, current_body, transition_time,
            )
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
    """Get body heliocentric position and velocity as 2D tuples."""
    cfg = _get_config()
    r3, v3 = celestial_config.compute_body_state(cfg, body_id, game_time_s)
    return (r3[0], r3[1]), (v3[0], v3[1])


def _body_state_in_parent_frame_2d(
    body_id: str, parent_body_id: str, game_time_s: float
) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Get body position/velocity relative to its parent body as 2D tuples.

    e.g. Moon position in Earth-centered frame.
    Both bodies' heliocentric states are computed and subtracted.
    """
    r_body_h, v_body_h = _body_state_2d(body_id, game_time_s)
    r_parent_h, v_parent_h = _body_state_2d(parent_body_id, game_time_s)
    return (
        (r_body_h[0] - r_parent_h[0], r_body_h[1] - r_parent_h[1]),
        (v_body_h[0] - v_parent_h[0], v_body_h[1] - v_parent_h[1]),
    )


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
        # Update orbit to match the docked location
        loc_orbit = orbit_for_location(loc_id, now_s)
        loc_orbit_json = json.dumps(loc_orbit) if loc_orbit else None
        loc_body_id = loc_orbit.get("body_id", "") if loc_orbit else None
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
                   maneuver_json = '[]',
                   orbit_predictions_json = NULL,
                   orbit_json = COALESCE(?, orbit_json),
                   orbit_body_id = COALESCE(?, orbit_body_id)
               WHERE id = ?""",
            (loc_id, loc_orbit_json, loc_body_id, ship_id),
        )
        logger.info("Auto-docked ship %s at %s", ship_id, loc_id)

    for row in rows:
        ship_id = row["id"]
        body_id = row["orbit_body_id"] or ""

        try:
            orbit = json.loads(row["orbit_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        # Priority 1: dock at intended destination if all maneuvers are done.
        # For SOI transfers the ship's orbit_body_id may differ from the
        # destination's body (e.g. Earth-frame orbit arriving at Moon), so
        # we trust the completed transfer plan rather than requiring an
        # exact orbit match.  However, we validate proximity: the ship's
        # current orbital position must be within a reasonable distance
        # of the destination body.
        to_loc = row["to_location_id"]
        if to_loc:
            # Proximity check: verify ship is near the destination body
            dest_resolved = _resolve_location_orbit_params(cfg, to_loc)
            if dest_resolved:
                dest_body = dest_resolved["body_id"]
                if body_id and dest_body and body_id != dest_body:
                    # Ship is in a different body's frame — check distance
                    # to the destination body at the current time
                    try:
                        mu_body = celestial_config.get_body_mu(cfg, body_id)
                        ship_pos = orbit_service.propagate_position(orbit, mu_body, now_s)
                        dest_r, _ = celestial_config.compute_body_state(cfg, dest_body, now_s)
                        body_r, _ = celestial_config.compute_body_state(cfg, body_id, now_s)
                        dx = ship_pos[0] - (dest_r[0] - body_r[0])
                        dy = ship_pos[1] - (dest_r[1] - body_r[1])
                        miss_km = math.sqrt(dx * dx + dy * dy)
                        soi_km = celestial_config.get_body_soi(cfg, dest_body)
                        # Use 3× SOI as threshold for auto-dock (generous)
                        threshold_km = (soi_km * 3.0) if soi_km else 500000.0
                        if miss_km > threshold_km:
                            logger.warning(
                                "Auto-dock blocked for ship %s: %.0f km from %s (threshold %.0f km)",
                                ship_id, miss_km, dest_body, threshold_km,
                            )
                            # Fall through to orbit-node matching instead
                            pass
                        else:
                            _dock_ship(ship_id, to_loc)
                            continue
                    except Exception:
                        logger.debug("Proximity check failed for ship %s, docking anyway", ship_id)
                        _dock_ship(ship_id, to_loc)
                        continue
                else:
                    # Same body — dock normally
                    _dock_ship(ship_id, to_loc)
                    continue
            else:
                # Cannot resolve destination — dock anyway (legacy behavior)
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
