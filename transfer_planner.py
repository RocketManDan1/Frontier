"""
Transfer planner — patched-conic integration layer.

Combines the Lambert solver (lambert.py) with body state vectors
from celestial_config to compute real interplanetary transfer costs.

Also provides:
 - Departure-window scanning (replaces phase-angle heuristic)
 - Synodic period calculation from config
 - Location → body resolution from config
"""

import math
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import celestial_config
from lambert import (
    Vec3,
    solve_lambert,
    compute_transfer_dv,
    compute_hohmann_dv_tof,
    _norm,
    _sub,
)


# ── Config accessors (cached) ──────────────────────────────

_CONFIG_CACHE: Dict[str, Any] = {}


def _get_config() -> Dict[str, Any]:
    if not _CONFIG_CACHE:
        _CONFIG_CACHE["cfg"] = celestial_config.load_celestial_config()
    return _CONFIG_CACHE["cfg"]


def _get_location_body_map() -> Dict[str, str]:
    return celestial_config.build_location_parent_body_map(_get_config())


def _get_body(body_id: str) -> Optional[Dict[str, Any]]:
    cfg = _get_config()
    for body in cfg.get("bodies", []):
        if body.get("id") == body_id:
            return body
    return None


def _body_parent_id(body_id: str) -> str:
    """Return the heliocentric parent: planet-level bodies orbit 'sun',
    moons orbit their planet.  Used to decide what μ to use for Lambert.
    """
    body = _get_body(body_id)
    if not body:
        return "sun"
    pos = body.get("position", {})
    center_id = str(pos.get("center_body_id", "")).strip()
    return center_id or "sun"


def _resolve_heliocentric_body(body_id: str) -> str:
    """Walk up the parent chain to find the heliocentric body.

    For locations around moons (e.g. IO_LO → io → jupiter), the
    interplanetary transfer is between the parent planets (e.g.
    jupiter ↔ earth), not between the moon-level bodies.

    Returns the body_id whose parent is 'sun'.
    """
    visited = set()
    current = body_id
    while current and current != "sun" and current not in visited:
        visited.add(current)
        parent = _body_parent_id(current)
        if parent == "sun" or parent == "":
            return current
        current = parent
    return body_id


def location_parent_body(location_id: str) -> str:
    """Resolve location_id → parent body_id from config (replaces _LOCATION_PARENT_BODY dict)."""
    return _get_location_body_map().get(location_id, "")


def get_synodic_period_s(body_a: str, body_b: str) -> Optional[float]:
    """Compute synodic period between two heliocentric bodies from config."""
    a_body = _get_body(body_a)
    b_body = _get_body(body_b)
    if not a_body or not b_body:
        return None
    pos_a = a_body.get("position", {})
    pos_b = b_body.get("position", {})
    p1 = pos_a.get("period_s")
    p2 = pos_b.get("period_s")
    if not p1 or not p2:
        return None
    p1, p2 = float(p1), float(p2)
    if p1 <= 0 or p2 <= 0:
        return None
    denom = abs(1.0 / p1 - 1.0 / p2)
    if denom < 1e-12:
        return None
    return abs(1.0 / denom)


def _parking_orbit_radius_km(body_id: str, location_id: Optional[str] = None) -> float:
    """Get parking orbit radius (km from body center).

    If location_id is an orbit_node with a radius_km, use that.
    Otherwise fall back to body radius + default altitude.
    """
    cfg = _get_config()

    # Try orbit_node radius first
    if location_id:
        r = celestial_config.get_orbit_node_radius(cfg, location_id)
        if r is not None and r > 0:
            return r

    # Fall back to body radius + default parking altitude
    body = _get_body(body_id)
    if not body:
        return 6578.0  # default LEO fallback
    radius_km = float(body.get("radius_km", 0.0))
    # Use a default parking altitude proportional to the body
    if radius_km > 10000:
        # Gas giant — use a larger default
        default_alt = 1000.0
    elif radius_km > 1000:
        default_alt = 250.0
    else:
        default_alt = 80.0
    return radius_km + default_alt


# ── Core: Lambert-based interplanetary leg ──────────────────

def compute_interplanetary_leg(
    from_location: str,
    to_location: str,
    departure_time_s: float,
    extra_dv_fraction: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Compute an interplanetary transfer leg using the Lambert solver.

    Sweeps several TOFs around the Hohmann estimate and picks the lowest-Δv
    solution for the given departure time.  Phase-angle information is returned
    for display but does NOT modify the Δv — the Lambert solver already
    accounts for actual body geometry at departure.

    Returns a dict compatible with the existing orbital data format, or None
    if the transfer cannot be computed (same body, unknown body, etc.).
    """
    cfg = _get_config()
    loc_map = _get_location_body_map()

    from_body = loc_map.get(from_location, "")
    to_body = loc_map.get(to_location, "")
    if not from_body or not to_body or from_body == to_body:
        return None
    if from_body == "sun" or to_body == "sun":
        return None

    # Resolve to heliocentric bodies for the Lambert arc
    from_helio = _resolve_heliocentric_body(from_body)
    to_helio = _resolve_heliocentric_body(to_body)
    if from_helio == to_helio:
        return None  # Same parent planet — not interplanetary

    # Get body states at departure time
    try:
        r1_vec, v1_body = celestial_config.compute_body_state(cfg, from_helio, departure_time_s)
        r2_vec, v2_body_dep = celestial_config.compute_body_state(cfg, to_helio, departure_time_s)
    except Exception:
        return None

    r1_km = _norm(r1_vec)
    r2_km = _norm(r2_vec)
    if r1_km < 1e-6 or r2_km < 1e-6:
        return None

    # Hohmann TOF as baseline estimate
    mu_sun = celestial_config.get_body_mu(cfg, "sun")
    hohmann_tof_s = math.pi * math.sqrt(((r1_km + r2_km) / 2.0) ** 3 / mu_sun)

    # Get body parameters for patched-conic burns
    try:
        mu_from = celestial_config.get_body_mu(cfg, from_helio)
        mu_to = celestial_config.get_body_mu(cfg, to_helio)
    except Exception:
        mu_from = 0.0
        mu_to = 0.0

    r_park_from = _parking_orbit_radius_km(from_helio, from_location)
    r_park_to = _parking_orbit_radius_km(to_helio, to_location)

    # Sweep TOFs around the Hohmann estimate to find the best Lambert Δv
    # for this departure time.  The porkchop sweeps departure × TOF; here
    # we fix the departure and sweep TOF only.
    tof_factors = [1.0, 0.9, 1.1, 0.8, 1.2, 0.7, 1.3, 0.5, 1.5, 0.4, 1.8, 2.0, 2.5, 0.3]
    best_dv_total = float("inf")
    best_v1: Optional[Vec3] = None
    best_v2: Optional[Vec3] = None
    best_tof_s = hohmann_tof_s
    best_v2_body_arr: Optional[Vec3] = None

    for factor in tof_factors:
        tof_try = hohmann_tof_s * factor
        if tof_try < 86400.0:  # Skip < 1 day
            continue
        arr_time = departure_time_s + tof_try
        try:
            r2_arr, v2_arr = celestial_config.compute_body_state(cfg, to_helio, arr_time)
        except Exception:
            continue

        solutions = solve_lambert(r1_vec, r2_arr, tof_try, mu_sun, max_revs=0)
        if not solutions:
            continue

        for v1_sol, v2_sol in solutions:
            dv_dep, dv_arr, dv_tot = compute_transfer_dv(
                v1_departure=v1_sol,
                v1_body=v1_body,
                v2_arrival=v2_sol,
                v2_body=v2_arr,
                mu_departure=mu_from,
                r_park_departure=r_park_from,
                mu_arrival=mu_to,
                r_park_arrival=r_park_to,
            )
            if dv_tot < best_dv_total:
                best_dv_total = dv_tot
                best_v1 = v1_sol
                best_v2 = v2_sol
                best_tof_s = tof_try
                best_v2_body_arr = v2_arr

    if best_v1 is None or best_v2 is None or best_v2_body_arr is None:
        return None

    # Final dv computation with the best solution
    dv_dep, dv_arr, base_dv_m_s = compute_transfer_dv(
        v1_departure=best_v1,
        v1_body=v1_body,
        v2_arrival=best_v2,
        v2_body=best_v2_body_arr,
        mu_departure=mu_from,
        r_park_departure=r_park_from,
        mu_arrival=mu_to,
        r_park_arrival=r_park_to,
    )
    base_tof_s = best_tof_s
    arrival_time_s = departure_time_s + best_tof_s

    # Compute v_inf values for display
    v_inf_depart = _norm(_sub(best_v1, v1_body))
    v_inf_arrive = _norm(_sub(best_v2, best_v2_body_arr))

    # Compute phase angle info for display (informational only — NOT applied to Δv)
    # Phase angle = angle between departure and arrival body positions at departure
    phase_rad = _angle_between_2d(r1_vec, r2_vec)
    phase_deg = math.degrees(phase_rad)

    # Optimal phase angle (Hohmann approximation)
    optimal_phase = math.pi * (1.0 - (1.0 / (2.0 ** (2.0 / 3.0))) * ((r1_km + r2_km) / r2_km) ** (2.0 / 3.0))
    if r2_km < r1_km:
        optimal_phase = 2.0 * math.pi - abs(optimal_phase)
    optimal_phase %= (2.0 * math.pi)
    optimal_phase_deg = math.degrees(optimal_phase)

    delta = phase_rad - optimal_phase
    alignment = (1.0 - math.cos(delta)) / 2.0
    alignment_pct = alignment * 100.0

    # Lambert already accounts for geometry — NO phase multiplier applied.
    # Phase multiplier is kept at 1.0 for backward compat; alignment_pct
    # serves as the quality indicator.
    phase_multiplier = 1.0
    phase_adjusted_dv = base_dv_m_s
    final_dv = base_dv_m_s * (1.0 + max(0.0, float(extra_dv_fraction)))
    final_tof = _excess_dv_time_reduction(base_tof_s, base_dv_m_s, max(0.0, float(extra_dv_fraction)))

    return {
        "base_dv_m_s": float(base_dv_m_s),
        "base_tof_s": float(base_tof_s),
        "phase_multiplier": float(phase_multiplier),
        "phase_adjusted_dv_m_s": float(phase_adjusted_dv),
        "dv_m_s": float(final_dv),
        "tof_s": float(final_tof),
        "phase_angle_deg": float(phase_deg),
        "optimal_phase_deg": float(optimal_phase_deg),
        "alignment_pct": float(alignment_pct),
        "from_body": from_body,
        "to_body": to_body,
        "v_inf_depart_km_s": float(v_inf_depart),
        "v_inf_arrive_km_s": float(v_inf_arrive),
        "dv_depart_m_s": float(dv_dep),
        "dv_arrive_m_s": float(dv_arr),
        "arrival_time": float(arrival_time_s),
        "solver": "lambert",
    }


def _angle_between_2d(a: Vec3, b: Vec3) -> float:
    """Angle between two 3D vectors projected to the ecliptic (x, y) plane."""
    theta_a = math.atan2(a[1], a[0])
    theta_b = math.atan2(b[1], b[0])
    return (theta_b - theta_a) % (2.0 * math.pi)


def _excess_dv_time_reduction(base_tof_s: float, base_dv_m_s: float, extra_dv_fraction: float) -> float:
    """Given extra delta-v fraction, compute reduced TOF.

    Same formula as fleet_router._excess_dv_time_reduction for compatibility.
    """
    if base_tof_s <= 0 or extra_dv_fraction <= 0:
        return base_tof_s
    reduction = 1.0 / ((1.0 + extra_dv_fraction) ** 0.6)
    return max(3600.0, base_tof_s * reduction)


# ── Departure-window scanning (Lambert-based) ──────────────

def scan_departure_windows(
    from_location: str,
    to_location: str,
    departure_time_s: float,
    current_phase_multiplier: float,
    synodic_period_s: Optional[float],
    max_candidates: int = 3,
) -> List[Dict[str, Any]]:
    """Scan future departure times for better transfer windows.

    Similar to the old _scan_departure_windows but uses Lambert internally
    to compute actual Δv at each candidate time.
    """
    if synodic_period_s is None or synodic_period_s <= 0:
        return []

    horizon_s = max(86400.0, min(float(synodic_period_s), 240.0 * 86400.0))
    step_s = 86400.0  # 1-day steps
    candidates: List[Dict[str, Any]] = []
    samples = int(horizon_s / step_s)

    for idx in range(1, samples + 1):
        t = float(departure_time_s) + idx * step_s
        result = compute_interplanetary_leg(from_location, to_location, t, extra_dv_fraction=0.0)
        if not result:
            continue

        multiplier = float(result["phase_multiplier"])
        savings_pct = 0.0
        if current_phase_multiplier > 1e-9:
            savings_pct = max(0.0, (1.0 - multiplier / current_phase_multiplier) * 100.0)

        candidates.append({
            "departure_time": t,
            "wait_s": float(t - departure_time_s),
            "phase_multiplier": multiplier,
            "phase_angle_deg": float(result["phase_angle_deg"]),
            "optimal_phase_deg": float(result["optimal_phase_deg"]),
            "alignment_pct": float(result["alignment_pct"]),
            "dv_savings_pct": float(savings_pct),
            "lambert_dv_m_s": float(result["base_dv_m_s"]),
        })

    candidates.sort(key=lambda item: (item["phase_multiplier"], item["wait_s"]))
    return candidates[:max_candidates]


def estimate_next_window_s(
    from_location: str,
    to_location: str,
    departure_time_s: float,
    current_phase_multiplier: float,
    synodic_period_s: Optional[float],
) -> Optional[float]:
    """Estimate seconds until the next better departure window."""
    windows = scan_departure_windows(
        from_location=from_location,
        to_location=to_location,
        departure_time_s=departure_time_s,
        current_phase_multiplier=current_phase_multiplier,
        synodic_period_s=synodic_period_s,
    )
    if not windows:
        return None
    best_wait = float(windows[0]["wait_s"])
    if float(windows[0]["phase_multiplier"]) >= current_phase_multiplier - 1e-6:
        return None
    return best_wait


def is_interplanetary(from_location: str, to_location: str) -> bool:
    """True if the transfer crosses between different heliocentric bodies."""
    loc_map = _get_location_body_map()
    a = loc_map.get(from_location, "")
    b = loc_map.get(to_location, "")
    if not a or not b or a == "sun" or b == "sun":
        return False
    return _resolve_heliocentric_body(a) != _resolve_heliocentric_body(b)


# ── Porkchop plot computation ──────────────────────────────

def compute_porkchop(
    from_location: str,
    to_location: str,
    departure_start_s: float,
    departure_end_s: float,
    tof_min_s: float,
    tof_max_s: float,
    grid_size: int = 40,
    max_revs: int = 0,
) -> Optional[Dict[str, Any]]:
    """Compute a porkchop plot grid of Δv values.

    Scans a 2D grid of (departure_time × time_of_flight) and runs
    a Lambert solve at each point, returning the total patched-conic
    Δv for departure + arrival burns.

    Returns None if locations are not interplanetary.
    """
    cfg = _get_config()
    loc_map = _get_location_body_map()

    from_body = loc_map.get(from_location, "")
    to_body = loc_map.get(to_location, "")
    if not from_body or not to_body or from_body == to_body:
        return None

    from_helio = _resolve_heliocentric_body(from_body)
    to_helio = _resolve_heliocentric_body(to_body)
    if from_helio == to_helio:
        return None

    mu_sun = celestial_config.get_body_mu(cfg, "sun")
    try:
        mu_from = celestial_config.get_body_mu(cfg, from_helio)
        mu_to = celestial_config.get_body_mu(cfg, to_helio)
    except Exception:
        mu_from = 0.0
        mu_to = 0.0

    r_park_from = _parking_orbit_radius_km(from_helio, from_location)
    r_park_to = _parking_orbit_radius_km(to_helio, to_location)

    grid_size = max(5, min(grid_size, 100))
    dep_step = (departure_end_s - departure_start_s) / max(1, grid_size - 1)
    tof_step = (tof_max_s - tof_min_s) / max(1, grid_size - 1)

    departure_times: List[float] = []
    tof_values: List[float] = []
    for i in range(grid_size):
        departure_times.append(departure_start_s + i * dep_step)
        tof_values.append(tof_min_s + i * tof_step)

    # Pre-compute departure body states (one per departure time)
    dep_states: List[Optional[Tuple[Vec3, Vec3]]] = []
    for dep_t in departure_times:
        try:
            r1, v1 = celestial_config.compute_body_state(cfg, from_helio, dep_t)
            dep_states.append((r1, v1))
        except Exception:
            dep_states.append(None)

    # Sentinel for failed solves
    FAIL_DV = float("inf")

    # Build the grid: dv_grid[dep_idx][tof_idx]
    dv_grid: List[List[Optional[float]]] = []
    best_solutions: List[Dict[str, Any]] = []
    global_best_dv = FAIL_DV

    for dep_idx, dep_t in enumerate(departure_times):
        row: List[Optional[float]] = []
        dep_state = dep_states[dep_idx]

        for tof_idx, tof in enumerate(tof_values):
            if dep_state is None or tof <= 0:
                row.append(None)
                continue

            r1_vec, v1_body = dep_state
            arr_t = dep_t + tof

            try:
                r2_vec, v2_body = celestial_config.compute_body_state(cfg, to_helio, arr_t)
            except Exception:
                row.append(None)
                continue

            solutions = solve_lambert(r1_vec, r2_vec, tof, mu_sun, max_revs=max_revs)
            if not solutions:
                row.append(None)
                continue

            # Find best solution across all revolutions
            best_dv_this = FAIL_DV
            best_v1_this = None
            best_v2_this = None
            best_rev = 0

            for sol_idx, (v1_sol, v2_sol) in enumerate(solutions):
                dv_dep, dv_arr, dv_tot = compute_transfer_dv(
                    v1_departure=v1_sol,
                    v1_body=v1_body,
                    v2_arrival=v2_sol,
                    v2_body=v2_body,
                    mu_departure=mu_from,
                    r_park_departure=r_park_from,
                    mu_arrival=mu_to,
                    r_park_arrival=r_park_to,
                )
                if dv_tot < best_dv_this:
                    best_dv_this = dv_tot
                    best_v1_this = v1_sol
                    best_v2_this = v2_sol
                    # Revolution count: sol_idx 0 = 0-rev, 1+ = multi-rev
                    best_rev = sol_idx // 2 + (1 if sol_idx > 0 else 0)

            if best_dv_this < FAIL_DV:
                row.append(round(best_dv_this, 1))

                if best_dv_this < global_best_dv:
                    global_best_dv = best_dv_this
            else:
                row.append(None)

            continue

        dv_grid.append(row)

    # Find top-N best solutions from the grid
    candidates: List[Tuple[float, int, int]] = []
    for di in range(grid_size):
        for ti in range(grid_size):
            val = dv_grid[di][ti]
            if val is not None:
                candidates.append((val, di, ti))

    candidates.sort(key=lambda x: x[0])

    # De-duplicate: keep solutions that are spread apart in the grid
    seen_cells: set = set()
    for dv_val, di, ti in candidates[:50]:
        # Skip if too close to an already-picked solution
        close = False
        for sdi, sti in seen_cells:
            if abs(di - sdi) <= 2 and abs(ti - sti) <= 2:
                close = True
                break
        if close:
            continue

        dep_t = departure_times[di]
        tof = tof_values[ti]
        dep_state = dep_states[di]
        if dep_state is None:
            continue

        r1_vec, v1_body = dep_state
        arr_t = dep_t + tof

        try:
            r2_vec, v2_body = celestial_config.compute_body_state(cfg, to_helio, arr_t)
        except Exception:
            continue

        solutions = solve_lambert(r1_vec, r2_vec, tof, mu_sun, max_revs=max_revs)
        if not solutions:
            continue

        # Re-evaluate for detailed output
        for v1_sol, v2_sol in solutions:
            dv_dep, dv_arr, dv_tot = compute_transfer_dv(
                v1_departure=v1_sol, v1_body=v1_body,
                v2_arrival=v2_sol, v2_body=v2_body,
                mu_departure=mu_from, r_park_departure=r_park_from,
                mu_arrival=mu_to, r_park_arrival=r_park_to,
            )
            if abs(dv_tot - dv_val) < 1.0:
                v_inf_dep = _norm(_sub(v1_sol, v1_body))
                v_inf_arr = _norm(_sub(v2_sol, v2_body))
                best_solutions.append({
                    "departure_time": round(dep_t, 1),
                    "arrival_time": round(arr_t, 1),
                    "tof_s": round(tof, 1),
                    "dv_m_s": round(dv_tot, 1),
                    "dv_depart_m_s": round(dv_dep, 1),
                    "dv_arrive_m_s": round(dv_arr, 1),
                    "v_inf_depart_km_s": round(v_inf_dep, 3),
                    "v_inf_arrive_km_s": round(v_inf_arr, 3),
                    "type": "short",
                })
                seen_cells.add((di, ti))
                break

        if len(best_solutions) >= 5:
            break

    return {
        "from_body": from_helio,
        "to_body": to_helio,
        "from_location": from_location,
        "to_location": to_location,
        "departure_times": [round(t, 1) for t in departure_times],
        "tof_values": [round(t, 1) for t in tof_values],
        "dv_grid": dv_grid,
        "grid_size": grid_size,
        "best_solutions": best_solutions,
    }


def invalidate_config_cache() -> None:
    """Clear the cached config (call after config reload)."""
    _CONFIG_CACHE.clear()
