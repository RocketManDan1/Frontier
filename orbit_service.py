"""
2D Orbital Mechanics Service — Physics-Based Ships (Phase 1)

Pure-math utilities for representing ships as Keplerian orbits in 2D.
No database or config dependencies — all functions take explicit parameters.

Provides:
  - elements_to_state()   : orbital elements → (r, v) state vector
  - state_to_elements()   : (r, v) → orbital elements
  - propagate_position()  : position on orbit at a given time (fast path)
  - propagate_state()     : full (r, v) at a given time
  - apply_burn()          : Δv impulse → new orbital elements
  - compute_soi_exit_time()  : time of SOI boundary crossing
  - orbit_matches_location()  : check if orbit matches a location (orbit node)
  - hohmann_burn_sequence()  : 2-burn Hohmann transfer plan
  - decompose_dv_to_burn()   : Δv vector → (prograde, radial) components

Orbit element dict schema:
    {
        "body_id": str,       # central body (patched-conic SOI parent)
        "a_km": float,        # semi-major axis (positive=elliptic, negative=hyperbolic)
        "e": float,           # eccentricity (0≤e<1 elliptic, e≥1 hyperbolic)
        "omega_deg": float,   # argument of periapsis in 2D plane (degrees)
        "M0_deg": float,      # mean anomaly at epoch (degrees)
        "epoch_s": float,     # game time when these elements are valid
        "direction": int,     # +1 prograde (CCW), -1 retrograde (CW)
    }
"""

import math
from typing import Any, Dict, List, Optional, Tuple

# ── Type aliases ────────────────────────────────────────────

Vec2 = Tuple[float, float]
OrbitElements = Dict[str, Any]

TWO_PI = 2.0 * math.pi


# ── Vector helpers (2D) ────────────────────────────────────

def _v2_add(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] + b[0], a[1] + b[1])


def _v2_sub(a: Vec2, b: Vec2) -> Vec2:
    return (a[0] - b[0], a[1] - b[1])


def _v2_scale(s: float, v: Vec2) -> Vec2:
    return (s * v[0], s * v[1])


def _v2_dot(a: Vec2, b: Vec2) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _v2_norm(v: Vec2) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1])


def _v2_unit(v: Vec2) -> Vec2:
    n = _v2_norm(v)
    if n < 1e-30:
        return (1.0, 0.0)
    return (v[0] / n, v[1] / n)


def _v2_cross_z(a: Vec2, b: Vec2) -> float:
    """Z-component of a×b (scalar in 2D)."""
    return a[0] * b[1] - a[1] * b[0]


def _v2_rotate(v: Vec2, angle_rad: float) -> Vec2:
    """Rotate vector v by angle_rad (CCW positive)."""
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


# ── Kepler equation solvers ────────────────────────────────

def _solve_kepler_elliptic(M: float, e: float, tol: float = 1e-12, max_iter: int = 30) -> float:
    """Solve M = E - e·sin(E) for E using Newton-Raphson."""
    # Normalize M to [0, 2π)
    M = M % TWO_PI
    if M < 0:
        M += TWO_PI

    e = min(e, 0.999999999)

    # Initial guess
    E = M if e < 0.8 else math.pi
    for _ in range(max_iter):
        f = E - e * math.sin(E) - M
        fp = 1.0 - e * math.cos(E)
        if abs(fp) < 1e-30:
            break
        dE = f / fp
        E -= dE
        if abs(dE) < tol:
            break
    return E


def _solve_kepler_hyperbolic(M: float, e: float, tol: float = 1e-12, max_iter: int = 50) -> float:
    """Solve M = e·sinh(H) - H for H using Newton-Raphson.

    M is the mean hyperbolic anomaly, e > 1.
    """
    # Initial guess
    if abs(M) < 1e-30:
        return 0.0
    H = M / (e - 1.0) if abs(M) < 2.0 else math.copysign(math.log(abs(2.0 * M / e)), M)
    for _ in range(max_iter):
        f = e * math.sinh(H) - H - M
        fp = e * math.cosh(H) - 1.0
        if abs(fp) < 1e-30:
            break
        dH = f / fp
        H -= dH
        if abs(dH) < tol:
            break
    return H


# ── Mean motion ─────────────────────────────────────────────

def mean_motion(a_km: float, mu: float) -> float:
    """Mean motion n (rad/s) for semi-major axis a (km) and μ (km³/s²).

    For elliptic orbits: n = √(μ/a³)
    For hyperbolic orbits (a < 0): n = √(μ/|a|³)  (mean hyperbolic motion)
    """
    a_abs = abs(a_km)
    if a_abs < 1e-6 or mu < 1e-30:
        return 0.0
    return math.sqrt(mu / (a_abs * a_abs * a_abs))


def orbital_period(a_km: float, mu: float) -> float:
    """Orbital period (seconds) for an elliptic orbit. Returns inf for hyperbolic."""
    if a_km <= 0:
        return float("inf")
    n = mean_motion(a_km, mu)
    if n < 1e-30:
        return float("inf")
    return TWO_PI / n


# ── Elements → State Vector ────────────────────────────────

def elements_to_state(elements: OrbitElements, mu: float, game_time_s: float) -> Tuple[Vec2, Vec2]:
    """Convert 2D Keplerian elements to position and velocity vectors.

    Returns ((x, y), (vx, vy)) in the central body's frame (km, km/s).
    """
    a = float(elements["a_km"])
    e = float(elements["e"])
    omega = math.radians(float(elements["omega_deg"]))
    M0 = math.radians(float(elements["M0_deg"]))
    epoch = float(elements["epoch_s"])
    direction = int(elements.get("direction", 1))

    n = mean_motion(a, mu)
    dt = game_time_s - epoch
    M = M0 + n * dt * direction

    if e < 1.0:
        # Elliptic orbit
        E = _solve_kepler_elliptic(M, e)
        cos_E = math.cos(E)
        sin_E = math.sin(E)
        sqrt_1me2 = math.sqrt(max(0.0, 1.0 - e * e))

        # Position in perifocal frame
        x_pf = a * (cos_E - e)
        y_pf = a * sqrt_1me2 * sin_E

        # Velocity in perifocal frame
        denom = 1.0 - e * cos_E
        if abs(denom) < 1e-30:
            denom = 1e-30
        vx_pf = -n * a * sin_E / denom
        vy_pf = n * a * sqrt_1me2 * cos_E / denom
    else:
        # Hyperbolic orbit (e >= 1.0)
        a_abs = abs(a)
        H = _solve_kepler_hyperbolic(M, e)
        cosh_H = math.cosh(H)
        sinh_H = math.sinh(H)
        sqrt_e2m1 = math.sqrt(max(0.0, e * e - 1.0))

        # Position in perifocal frame
        x_pf = a_abs * (e - cosh_H)  # Note: a is negative, so x_pf = |a|*(e - cosh(H))
        y_pf = a_abs * sqrt_e2m1 * sinh_H

        # For hyperbolic: a < 0, so use |a|
        denom = e * cosh_H - 1.0
        if abs(denom) < 1e-30:
            denom = 1e-30
        vx_pf = -n * a_abs * sinh_H / denom
        vy_pf = n * a_abs * sqrt_e2m1 * cosh_H / denom

    # Apply direction (retrograde orbits reverse the velocity tangent)
    if direction < 0:
        vx_pf = -vx_pf
        vy_pf = -vy_pf

    # Rotate from perifocal to body-centered inertial by ω
    r_body = _v2_rotate((x_pf, y_pf), omega)
    v_body = _v2_rotate((vx_pf, vy_pf), omega)

    return r_body, v_body


# ── State Vector → Elements ────────────────────────────────

def state_to_elements(
    r_vec: Vec2,
    v_vec: Vec2,
    mu: float,
    game_time_s: float,
    body_id: str = "",
) -> OrbitElements:
    """Convert 2D state vector to Keplerian orbital elements.

    Parameters
    ----------
    r_vec : (x, y) position in km (body-centered)
    v_vec : (vx, vy) velocity in km/s (body-centered)
    mu    : gravitational parameter (km³/s²)
    game_time_s : game time for epoch
    body_id : central body identifier

    Returns
    -------
    OrbitElements dict with keys: body_id, a_km, e, omega_deg, M0_deg, epoch_s, direction
    """
    r = _v2_norm(r_vec)
    v = _v2_norm(v_vec)

    if r < 1e-10:
        raise ValueError("Zero position vector — cannot determine orbit")
    if mu < 1e-30:
        raise ValueError("Zero gravitational parameter")

    # Specific orbital energy → semi-major axis
    energy = 0.5 * v * v - mu / r
    if abs(energy) < 1e-20:
        # Near-parabolic — treat as very high eccentricity ellipse
        a = 1e12  # Very large semi-major axis
    else:
        a = -mu / (2.0 * energy)

    # Specific angular momentum (z-component in 2D)
    h = _v2_cross_z(r_vec, v_vec)

    # Direction: h > 0 = CCW (prograde), h < 0 = CW (retrograde)
    direction = 1 if h >= 0 else -1

    # Eccentricity vector: e_vec = ((v² - μ/r)·r - (r·v)·v) / μ
    rv_dot = _v2_dot(r_vec, v_vec)
    coeff1 = (v * v - mu / r) / mu
    coeff2 = rv_dot / mu
    e_vec = (
        coeff1 * r_vec[0] - coeff2 * v_vec[0],
        coeff1 * r_vec[1] - coeff2 * v_vec[1],
    )
    e = _v2_norm(e_vec)

    # Argument of periapsis: angle of eccentricity vector
    if e > 1e-10:
        omega = math.atan2(e_vec[1], e_vec[0])
    else:
        # Near-circular: set ω = 0, absorb position angle into M0
        omega = 0.0

    # True anomaly: angle from periapsis to position
    # ν = angle(r_vec) - ω, accounting for direction
    pos_angle = math.atan2(r_vec[1], r_vec[0])
    nu = pos_angle - omega
    if direction < 0:
        nu = -nu  # Retrograde: reverse angle convention

    # Normalize ν to [-π, π)
    nu = (nu + math.pi) % TWO_PI - math.pi

    # True anomaly → mean anomaly at epoch
    if e < 1.0:
        # Elliptic: ν → E → M
        sin_nu = math.sin(nu)
        cos_nu = math.cos(nu)
        sqrt_1me2 = math.sqrt(max(0.0, 1.0 - e * e))
        E = math.atan2(sqrt_1me2 * sin_nu, e + cos_nu)
        M0 = E - e * math.sin(E)
    elif e > 1.0:
        # Hyperbolic: ν → H → M
        sin_nu = math.sin(nu)
        cos_nu = math.cos(nu)
        sqrt_e2m1 = math.sqrt(max(0.0, e * e - 1.0))
        # tanh(H/2) = √((e-1)/(e+1)) · tan(ν/2)
        tan_half_nu = math.tan(nu / 2.0)
        tanh_half_H = math.sqrt((e - 1.0) / (e + 1.0)) * tan_half_nu
        # Clamp to avoid atanh domain error
        tanh_half_H = max(-0.99999999, min(0.99999999, tanh_half_H))
        H = 2.0 * math.atanh(tanh_half_H)
        M0 = e * math.sinh(H) - H
    else:
        # Parabolic (e ≈ 1): use Barker's equation
        D = math.tan(nu / 2.0)
        M0 = D + D * D * D / 3.0

    # Apply direction to M0
    M0 = M0 * direction

    return {
        "body_id": body_id,
        "a_km": float(a),
        "e": float(max(0.0, e)),
        "omega_deg": float(math.degrees(omega)),
        "M0_deg": float(math.degrees(M0)),
        "epoch_s": float(game_time_s),
        "direction": direction,
    }


# ── Fast position propagation ──────────────────────────────

def propagate_position(elements: OrbitElements, mu: float, game_time_s: float) -> Vec2:
    """Compute position on orbit at game_time_s. Fast path: no velocity."""
    a = float(elements["a_km"])
    e = float(elements["e"])
    omega = math.radians(float(elements["omega_deg"]))
    M0 = math.radians(float(elements["M0_deg"]))
    epoch = float(elements["epoch_s"])
    direction = int(elements.get("direction", 1))

    n = mean_motion(a, mu)
    dt = game_time_s - epoch
    M = M0 + n * dt * direction

    if e < 1.0:
        E = _solve_kepler_elliptic(M, e)
        cos_E = math.cos(E)
        sin_E = math.sin(E)
        sqrt_1me2 = math.sqrt(max(0.0, 1.0 - e * e))
        x_pf = a * (cos_E - e)
        y_pf = a * sqrt_1me2 * sin_E
    else:
        a_abs = abs(a)
        H = _solve_kepler_hyperbolic(M, e)
        cosh_H = math.cosh(H)
        sinh_H = math.sinh(H)
        sqrt_e2m1 = math.sqrt(max(0.0, e * e - 1.0))
        x_pf = a_abs * (e - cosh_H)
        y_pf = a_abs * sqrt_e2m1 * sinh_H

    return _v2_rotate((x_pf, y_pf), omega)


def propagate_state(elements: OrbitElements, mu: float, game_time_s: float) -> Tuple[Vec2, Vec2]:
    """Compute full (position, velocity) at game_time_s. Alias for elements_to_state."""
    return elements_to_state(elements, mu, game_time_s)


# ── Burn application ───────────────────────────────────────

def apply_burn(
    elements: OrbitElements,
    mu: float,
    burn_time_s: float,
    prograde_m_s: float,
    radial_m_s: float,
) -> OrbitElements:
    """Apply an impulsive Δv burn to a ship's orbit and return new elements.

    Parameters
    ----------
    elements     : current orbital elements
    mu           : gravitational parameter of central body (km³/s²)
    burn_time_s  : game time when the burn occurs
    prograde_m_s : Δv along velocity direction (m/s, positive = speed up)
    radial_m_s   : Δv along radial-out direction (m/s, positive = away from body)

    Returns
    -------
    New OrbitElements dict reflecting the post-burn orbit.
    """
    # Get state at burn time
    r_vec, v_vec = elements_to_state(elements, mu, burn_time_s)

    # Convert Δv from m/s to km/s
    dv_prograde_kms = prograde_m_s / 1000.0
    dv_radial_kms = radial_m_s / 1000.0

    # Compute prograde and radial unit vectors
    v_hat = _v2_unit(v_vec)  # prograde direction
    r_hat = _v2_unit(r_vec)  # radial-out direction

    # Apply Δv
    v_new = (
        v_vec[0] + dv_prograde_kms * v_hat[0] + dv_radial_kms * r_hat[0],
        v_vec[1] + dv_prograde_kms * v_hat[1] + dv_radial_kms * r_hat[1],
    )

    # Convert back to elements
    body_id = elements.get("body_id", "")
    return state_to_elements(r_vec, v_new, mu, burn_time_s, body_id=body_id)


# ── SOI boundary detection ─────────────────────────────────

def compute_apoapsis_km(elements: OrbitElements) -> float:
    """Compute apoapsis radius (km from body center). Returns inf for hyperbolic."""
    a = float(elements["a_km"])
    e = float(elements["e"])
    if e >= 1.0 or a <= 0:
        return float("inf")
    return a * (1.0 + e)


def compute_periapsis_km(elements: OrbitElements) -> float:
    """Compute periapsis radius (km from body center)."""
    a = float(elements["a_km"])
    e = float(elements["e"])
    if a <= 0:
        return abs(a) * (e - 1.0)  # hyperbolic periapsis
    return a * (1.0 - e)


def orbit_can_escape(elements: OrbitElements, soi_radius_km: float) -> bool:
    """Check if this orbit reaches beyond the SOI boundary.

    True for hyperbolic orbits (e >= 1) or highly elliptic orbits
    whose apoapsis exceeds the SOI radius.
    """
    return compute_apoapsis_km(elements) > soi_radius_km


def compute_soi_exit_time(
    elements: OrbitElements,
    mu: float,
    soi_radius_km: float,
    search_start_s: Optional[float] = None,
    max_search_s: float = 1e9,
) -> Optional[float]:
    """Find the game time when the ship crosses the SOI boundary (r = soi_radius).

    Uses Newton iteration on r(t) - soi_radius = 0.

    Returns the game time of crossing, or None if the orbit doesn't reach the SOI.
    """
    a = float(elements["a_km"])
    e = float(elements["e"])
    epoch = float(elements["epoch_s"])

    if not orbit_can_escape(elements, soi_radius_km):
        return None

    # Find the true anomaly ν where r = soi_radius
    # r = p / (1 + e·cos(ν))  →  cos(ν) = (p/r - 1) / e
    if a > 0:
        p = a * (1.0 - e * e)
    else:
        p = abs(a) * (e * e - 1.0)

    if p < 1e-6:
        return None

    cos_nu = (p / soi_radius_km - 1.0) / e if abs(e) > 1e-10 else -1.0
    if abs(cos_nu) > 1.0:
        return None  # orbit doesn't reach SOI radius

    nu_exit = math.acos(max(-1.0, min(1.0, cos_nu)))

    # Convert ν to time
    direction = int(elements.get("direction", 1))
    n = mean_motion(a, mu)
    if n < 1e-30:
        return None

    if e < 1.0:
        # Elliptic: ν → E → M → t
        sqrt_1me2 = math.sqrt(max(0.0, 1.0 - e * e))
        E_exit = math.atan2(sqrt_1me2 * math.sin(nu_exit), e + math.cos(nu_exit))
        M_exit = E_exit - e * math.sin(E_exit)
    else:
        # Hyperbolic: ν → H → M → t
        tan_half_nu = math.tan(nu_exit / 2.0)
        tanh_half_H = math.sqrt((e - 1.0) / (e + 1.0)) * tan_half_nu
        tanh_half_H = max(-0.99999999, min(0.99999999, tanh_half_H))
        H_exit = 2.0 * math.atanh(tanh_half_H)
        M_exit = e * math.sinh(H_exit) - H_exit

    M0 = math.radians(float(elements["M0_deg"]))
    dM = M_exit - M0 * direction

    # For elliptic: ensure we get the NEXT crossing (dM > 0)
    if e < 1.0:
        dM = dM % TWO_PI
        if dM < 0:
            dM += TWO_PI

    dt = dM / (n * abs(direction)) if abs(direction) > 0 else 0.0
    if dt < 0:
        dt = 0.0

    t_exit = epoch + dt
    start = search_start_s if search_start_s is not None else epoch
    if t_exit < start:
        # For elliptic, try next period
        if e < 1.0:
            period = TWO_PI / n
            while t_exit < start:
                t_exit += period
            if t_exit - epoch > max_search_s:
                return None
        else:
            return None  # Hyperbolic: only one exit

    if t_exit - epoch > max_search_s:
        return None

    return t_exit


# ── SOI frame transforms ──────────────────────────────────

def transform_to_child_frame(
    r_parent: Vec2,
    v_parent: Vec2,
    child_r_parent: Vec2,
    child_v_parent: Vec2,
    mu_child: float,
    game_time_s: float,
    child_body_id: str,
) -> OrbitElements:
    """Transform ship state from parent frame to child body frame.

    Used when entering a child body's SOI.
    """
    r_local = _v2_sub(r_parent, child_r_parent)
    v_local = _v2_sub(v_parent, child_v_parent)
    return state_to_elements(r_local, v_local, mu_child, game_time_s, body_id=child_body_id)


def transform_to_parent_frame(
    r_local: Vec2,
    v_local: Vec2,
    body_r_parent: Vec2,
    body_v_parent: Vec2,
    mu_parent: float,
    game_time_s: float,
    parent_body_id: str,
) -> OrbitElements:
    """Transform ship state from body frame to parent frame.

    Used when exiting a body's SOI.
    """
    r_parent = _v2_add(r_local, body_r_parent)
    v_parent = _v2_add(v_local, body_v_parent)
    return state_to_elements(r_parent, v_parent, mu_parent, game_time_s, body_id=parent_body_id)


# ── Location matching / auto-docking ───────────────────────

def orbit_matches_location(
    elements: OrbitElements,
    location_body_id: str,
    location_radius_km: float,
    radius_tolerance: float = 0.02,
    eccentricity_limit: float = 0.05,
) -> bool:
    """Check if a ship's orbit is close enough to an orbit-node location to dock.

    Locations are defined in celestial_config as orbit nodes (LEO, GEO,
    lunar orbit, L-points, etc.) — not physical station objects.  A ship
    docks at a location when its orbit is near-circular at the same body
    and radius.

    Criteria:
    - Same central body
    - Eccentricity below limit (near-circular)
    - Semi-major axis within tolerance of location radius
    """
    if elements.get("body_id", "") != location_body_id:
        return False
    e = float(elements.get("e", 1.0))
    if e > eccentricity_limit:
        return False
    a = float(elements.get("a_km", 0.0))
    if location_radius_km <= 0:
        return False
    r_diff = abs(a - location_radius_km) / location_radius_km
    return r_diff <= radius_tolerance


# Backward-compat alias
orbit_matches_station = orbit_matches_location


# ── Hohmann transfer planning ──────────────────────────────

def hohmann_burn_sequence(
    mu: float,
    r1_km: float,
    r2_km: float,
    departure_time_s: float,
    body_id: str = "",
) -> Dict[str, Any]:
    """Compute a 2-burn Hohmann transfer between circular orbits.

    Returns a transfer plan dict with burns, predicted orbits, and timing.

    Parameters
    ----------
    mu : gravitational parameter of central body (km³/s²)
    r1_km : departure circular orbit radius (km)
    r2_km : arrival circular orbit radius (km)
    departure_time_s : game time of first burn
    body_id : central body id
    """
    if r1_km <= 0 or r2_km <= 0 or mu <= 0:
        raise ValueError("Invalid orbit parameters for Hohmann transfer")

    # Transfer orbit semi-major axis
    a_transfer = 0.5 * (r1_km + r2_km)

    # Circular velocities
    v_circ_1 = math.sqrt(mu / r1_km)  # km/s at r1
    v_circ_2 = math.sqrt(mu / r2_km)  # km/s at r2

    # Transfer orbit velocities at each radius (vis-viva)
    v_transfer_at_r1 = math.sqrt(mu * (2.0 / r1_km - 1.0 / a_transfer))
    v_transfer_at_r2 = math.sqrt(mu * (2.0 / r2_km - 1.0 / a_transfer))

    # Δv = (transfer velocity − circular velocity) at each end.
    # Raising (r2 > r1): both positive (prograde).
    # Lowering (r2 < r1): both negative (retrograde).
    dv1_kms = v_transfer_at_r1 - v_circ_1
    dv2_kms = v_circ_2 - v_transfer_at_r2

    # Transfer time = half the transfer orbit period
    tof_s = math.pi * math.sqrt(a_transfer ** 3 / mu)

    burn2_time = departure_time_s + tof_s

    # Total Δv in m/s
    total_dv_m_s = (abs(dv1_kms) + abs(dv2_kms)) * 1000.0

    # Compute transfer orbit elements
    # Transfer ellipse: periapsis at min(r1,r2), apoapsis at max(r1,r2)
    r_peri = min(r1_km, r2_km)
    r_apo = max(r1_km, r2_km)
    e_transfer = (r_apo - r_peri) / (r_apo + r_peri)

    return {
        "burns": [
            {
                "time_s": departure_time_s,
                "prograde_m_s": dv1_kms * 1000.0,
                "radial_m_s": 0.0,
                "body_id": body_id,
                "label": "Transfer injection",
            },
            {
                "time_s": burn2_time,
                "prograde_m_s": dv2_kms * 1000.0,
                "radial_m_s": 0.0,
                "body_id": body_id,
                "label": "Circularization",
            },
        ],
        "total_dv_m_s": total_dv_m_s,
        "total_tof_s": tof_s,
        "predicted_arrival_s": burn2_time,
        "transfer_orbit": {
            "a_km": a_transfer,
            "e": e_transfer,
        },
    }


# ── Δv decomposition ──────────────────────────────────────

def decompose_dv_to_burn(
    dv_vec_kms: Vec2,
    v_current_kms: Vec2,
    r_current_km: Vec2,
) -> Tuple[float, float]:
    """Decompose a Δv vector into prograde and radial components (m/s).

    Parameters
    ----------
    dv_vec_kms   : (dvx, dvy) in km/s
    v_current_kms : current velocity (km/s) — defines prograde direction
    r_current_km  : current position (km) — defines radial-out direction

    Returns
    -------
    (prograde_m_s, radial_m_s)
    """
    v_hat = _v2_unit(v_current_kms)
    r_hat = _v2_unit(r_current_km)

    prograde_kms = _v2_dot(dv_vec_kms, v_hat)
    radial_kms = _v2_dot(dv_vec_kms, r_hat)

    return prograde_kms * 1000.0, radial_kms * 1000.0


# ── Circular orbit constructor ─────────────────────────────

def circular_orbit(
    body_id: str,
    radius_km: float,
    mu: float,
    game_time_s: float,
    angle_deg: float = 0.0,
) -> OrbitElements:
    """Create orbital elements for a circular orbit at a given radius.

    Parameters
    ----------
    body_id     : central body
    radius_km   : orbital radius (km from body center)
    mu          : gravitational parameter (km³/s²)
    game_time_s : epoch time for the elements
    angle_deg   : initial position angle (degrees, measured from x-axis)
    """
    # For a circular orbit: a = r, e = 0, ω = 0
    # The "position angle" maps to mean anomaly at epoch
    return {
        "body_id": body_id,
        "a_km": float(radius_km),
        "e": 0.0,
        "omega_deg": 0.0,
        "M0_deg": float(angle_deg),
        "epoch_s": float(game_time_s),
        "direction": 1,
    }
