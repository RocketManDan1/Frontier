"""
Lambert solver — universal variable method with multi-revolution support.

Given two position vectors r1, r2 and a time of flight, compute the required
departure and arrival velocity vectors under two-body dynamics.

Pure math — no database, config, or framework dependencies.

Based on:
  Curtis, "Orbital Mechanics for Engineering Students", Algorithm 5.2
  Extended for multi-revolution (N > 0) solutions.
"""

import math
from typing import List, Optional, Tuple

Vec3 = Tuple[float, float, float]


# ─── Vector utilities ─────────────────────────────────────────

def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(v: Vec3) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _scale(s: float, v: Vec3) -> Vec3:
    return (s * v[0], s * v[1], s * v[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


# ─── Stumpff functions ───────────────────────────────────────

def _stumpff_c2(psi: float) -> float:
    """Stumpff function c2(ψ) = C(ψ)."""
    if abs(psi) < 1e-12:
        return 1.0 / 2.0
    if psi > 0.0:
        sp = math.sqrt(psi)
        return (1.0 - math.cos(sp)) / psi
    sp = math.sqrt(-psi)
    return (math.cosh(sp) - 1.0) / (-psi)


def _stumpff_c3(psi: float) -> float:
    """Stumpff function c3(ψ) = S(ψ)."""
    if abs(psi) < 1e-12:
        return 1.0 / 6.0
    if psi > 0.0:
        sp = math.sqrt(psi)
        return (sp - math.sin(sp)) / (psi * sp)
    sp = math.sqrt(-psi)
    return (math.sinh(sp) - sp) / ((-psi) * sp)


# ─── Battin's method — robust for near-180° transfers ────────

def _continued_fraction_eta(x: float) -> float:
    """Evaluate Battin's continued fraction η(x).

    η(x) = x / (√(1+x) + 1) ≈ series expansion for small x.
    Used in Battin's method for the relationship between the
    eccentric anomaly difference and the transfer geometry.
    """
    # Direct formula: η = x / (√(1+x) + 1)
    if abs(x) < 1e-12:
        return 0.0
    sqrt_val = math.sqrt(max(0.0, 1.0 + x))
    denom = sqrt_val + 1.0
    if abs(denom) < 1e-15:
        return 0.0
    return x / denom


def _battin_continued_fraction_k(u: float, n_terms: int = 30) -> float:
    """Evaluate Battin's continued fraction K(u) used in the h1/h2 functions.

    K(u) is defined by the continued fraction:
    K(u) = (1/3) / (1 + (4/27)u / (1 + (8/27)u / (1 + ...)))

    Evaluated bottom-up for numerical stability.
    """
    cf = 1.0
    for i in range(n_terms, 0, -1):
        c_num = (i * (i + 1.0)) / ((2.0 * i + 1.0) * (2.0 * i + 3.0))
        cf = 1.0 + c_num * u / cf
    return (1.0 / 3.0) / cf


def _solve_lambert_battin(
    r1: Vec3,
    r2: Vec3,
    tof: float,
    mu: float,
    clockwise: bool = False,
) -> Optional[Tuple[Vec3, Vec3]]:
    """Solve Lambert's problem using Battin's method.

    Battin's method is more numerically stable for near-180° transfer angles
    where the universal-variable solver may lose precision.  It uses a
    geometrically motivated parameterization that avoids the singularity.

    Reference: Battin, "An Introduction to the Mathematics and Methods of
    Astrodynamics", Chapter 6.

    Returns (v1, v2) or None if no solution found.
    """
    r1_mag = _norm(r1)
    r2_mag = _norm(r2)
    if r1_mag < 1e-10 or r2_mag < 1e-10 or tof <= 0.0 or mu <= 0.0:
        return None

    # ── Transfer angle ──────────────────────────────────────
    cos_dnu = _dot(r1, r2) / (r1_mag * r2_mag)
    cos_dnu = max(-1.0, min(1.0, cos_dnu))

    c_cross = _cross(r1, r2)
    cross_z = c_cross[2]

    if clockwise:
        if cross_z >= 0.0:
            dnu = 2.0 * math.pi - math.acos(cos_dnu)
        else:
            dnu = math.acos(cos_dnu)
    else:
        if cross_z >= 0.0:
            dnu = math.acos(cos_dnu)
        else:
            dnu = 2.0 * math.pi - math.acos(cos_dnu)

    # ── Battin geometry parameters ──────────────────────────
    # Use half-angle on transfer angle: ta = dnu / 2
    k = r1_mag * r2_mag
    l_param = (r1_mag + r2_mag) / (4.0 * math.sqrt(k))  # Battin's ℓ
    sin_dnu_2 = math.sin(dnu / 2.0)
    cos_dnu_2 = math.cos(dnu / 2.0)

    # Chord length
    c = math.sqrt(r1_mag * r1_mag + r2_mag * r2_mag - 2.0 * r1_mag * r2_mag * cos_dnu)
    s = (r1_mag + r2_mag + c) / 2.0  # semi-perimeter

    # Guard against degenerate
    if s < 1e-10 or c < 1e-10:
        return None

    lambda_param = math.sqrt(1.0 - c / s)  # Battin's λ = √(1 - c/s)
    if dnu > math.pi:
        lambda_param = -lambda_param

    # Parabolic TOF (minimum energy)
    tof_p = (2.0 / 3.0) * math.sqrt(s ** 3 / (2.0 * mu)) * (1.0 - lambda_param ** 3)

    # ── Newton iteration on x (Battin parameterization) ─────
    # x relates to the energy: x = l² for elliptic, etc.
    # Initial guess based on TOF relative to parabolic
    if abs(tof_p) < 1e-12:
        return None

    # Battin's method: iterate on x where the transfer semi-major axis a = s/(4*(1-x²))
    # Use the ratio tof/tof_p to estimate initial x
    # For TOF < TOF_p: hyperbolic (x > 1), for TOF > TOF_p: elliptic (x < 1)
    # Initial guess from Battin's heuristic
    m_ratio = tof / tof_p
    if m_ratio < 1e-12:
        return None

    # Starting value
    x = 0.0
    if m_ratio > 1.0:
        # Elliptic — x < 1
        x = (m_ratio - 1.0) / (m_ratio + 0.4)
        x = min(0.99, max(-0.99, x))
    else:
        # Hyperbolic — tends toward larger x
        x = (1.0 - m_ratio) / (m_ratio + 0.4)
        x = min(0.99, max(-0.99, x))

    lambda2 = lambda_param * lambda_param

    for _ in range(100):
        # h parameters from Battin's continued fraction
        x2 = x * x
        # Prevent division by zero
        if abs(1.0 - x2) < 1e-15:
            x = x * 0.999
            x2 = x * x

        # Compute ξ = (1 - λ²(1 - x)) / (2x)
        if abs(x) < 1e-12:
            # Limiting case near x = 0
            xi = lambda2 / 2.0
        else:
            xi = (1.0 - lambda2 * (1.0 - x)) / (2.0 * x)

        # Compute h1, h2 using continued fraction K(u)
        u = xi * xi / ((1.0 + xi) if abs(1.0 + xi) > 1e-15 else 1e-15)
        k_val = _battin_continued_fraction_k(u)

        # y = (1 + xi + h2) where h2 = k_val * (xi + 1)
        y = 1.0 + xi * (1.0 + k_val)

        if y < 1e-15:
            break

        # Semi-major axis
        a = s / (4.0 * (1.0 - x2)) if abs(1.0 - x2) > 1e-15 else s * 1e15

        # Compute TOF for current x
        if x2 < 1.0:
            # Elliptic
            beta = 2.0 * math.asin(math.sqrt(max(0.0, min(1.0, (s - c) / (2.0 * a)))))
            if dnu > math.pi:
                beta = -beta
            alpha = 2.0 * math.asin(math.sqrt(max(0.0, min(1.0, s / (2.0 * a)))))
            tof_x = math.sqrt(a ** 3 / mu) * ((alpha - math.sin(alpha)) - (beta - math.sin(beta)))
        elif abs(x2 - 1.0) < 1e-12:
            # Parabolic
            tof_x = tof_p
        else:
            # Hyperbolic
            a_hyp = abs(a)
            if s / (2.0 * a_hyp) < 1.0:
                alpha_h = 2.0 * math.asinh(math.sqrt(max(0.0, s / (2.0 * a_hyp))))
            else:
                alpha_h = 2.0 * math.acosh(s / (2.0 * a_hyp))
            sc_rat = max(0.0, (s - c) / (2.0 * a_hyp))
            if sc_rat < 1.0:
                beta_h = 2.0 * math.asinh(math.sqrt(sc_rat))
            else:
                beta_h = 2.0 * math.acosh(math.sqrt(sc_rat) + math.sqrt(max(0.0, sc_rat - 1.0)))
            if dnu > math.pi:
                beta_h = -beta_h
            tof_x = math.sqrt(a_hyp ** 3 / mu) * ((math.sinh(alpha_h) - alpha_h) - (math.sinh(beta_h) - beta_h))

        if abs(tof_x) < 1e-15:
            break

        # Newton update: dx = (tof - tof_x) / dTOF_dx
        # Numerical derivative
        dx = 0.001
        x_p = x + dx
        x_m = x - dx

        # Recompute TOF at x+dx for numerical derivative
        a_p = s / (4.0 * (1.0 - x_p * x_p)) if abs(1.0 - x_p * x_p) > 1e-15 else s * 1e15
        if x_p * x_p < 1.0 and a_p > 0:
            bp = 2.0 * math.asin(math.sqrt(max(0.0, min(1.0, (s - c) / (2.0 * a_p)))))
            if dnu > math.pi:
                bp = -bp
            ap = 2.0 * math.asin(math.sqrt(max(0.0, min(1.0, s / (2.0 * a_p)))))
            tof_p_x = math.sqrt(a_p ** 3 / mu) * ((ap - math.sin(ap)) - (bp - math.sin(bp)))
        else:
            tof_p_x = tof_x  # fallback

        dtof_dx = (tof_p_x - tof_x) / dx if abs(dx) > 1e-15 else 0.0

        if abs(dtof_dx) < 1e-20:
            break

        x_new = x + (tof - tof_x) / dtof_dx

        # Clamp
        x_new = max(-0.999, min(0.999, x_new))

        if abs(x_new - x) < 1e-10:
            x = x_new
            break
        x = x_new

    # ── Compute a from converged x ──────────────────────────
    x2 = x * x
    if abs(1.0 - x2) < 1e-15:
        return None
    a = s / (4.0 * (1.0 - x2))
    if a < 1e-10:
        return None

    # ── f, g, g_dot Lagrange coefficients ───────────────────
    # Standard formulation using a and the transfer geometry
    f = 1.0 - a / r1_mag * (1.0 - cos_dnu)
    g_denom = mu * a
    if g_denom <= 0:
        return None
    g = tof - math.sqrt(a ** 3 / mu) * (
        dnu - math.sin(dnu) if abs(dnu) < 2 * math.pi else 0.0
    )

    # The correct Lagrange coefficients for the Lambert problem:
    # v1 = (r2 - f*r1) / g
    # v2 = (g_dot*r2 - r1) / g
    if abs(g) < 1e-15:
        return None

    v1 = _scale(1.0 / g, _sub(r2, _scale(f, r1)))
    g_dot = 1.0 - a / r2_mag * (1.0 - cos_dnu)
    v2 = _scale(1.0 / g, _sub(_scale(g_dot, r2), r1))

    # Sanity check: velocities should not be absurdly large
    if _norm(v1) > 200.0 or _norm(v2) > 200.0:
        return None

    return (v1, v2)


# ─── Core: Universal variable Lambert solver ─────────────────

def _solve_lambert_uv(
    r1: Vec3,
    r2: Vec3,
    tof: float,
    mu: float,
    clockwise: bool = False,
) -> Optional[Tuple[Vec3, Vec3]]:
    """Solve Lambert's problem for zero-revolution using the universal variable.

    Based on Curtis Algorithm 5.2.  Uses Newton–Raphson with bisection fallback
    to find the universal variable *z*.

    Returns (v1, v2) or None.
    """
    r1_mag = _norm(r1)
    r2_mag = _norm(r2)
    if r1_mag < 1e-10 or r2_mag < 1e-10 or tof <= 0.0 or mu <= 0.0:
        return None

    # ── Transfer angle ──────────────────────────────────────
    cos_dnu = _dot(r1, r2) / (r1_mag * r2_mag)
    cos_dnu = max(-1.0, min(1.0, cos_dnu))

    # Full cross product for orbit normal determination
    c_cross = _cross(r1, r2)
    cross_z = c_cross[2]  # z-component

    # Handle near-180° degenerate case: orbit plane undefined
    # Perturb r2 in a direction orthogonal to r1 to define a transfer plane
    c_cross_mag = _norm(c_cross)
    if c_cross_mag < 1e-6 * r1_mag * r2_mag and cos_dnu < -0.99:
        # Near 180°: find a direction perpendicular to r1 and perturb r2
        # Use the direction that maximises cross_z (prograde bias)
        ir1 = _scale(1.0 / r1_mag, r1)
        # Pick a vector not parallel to ir1
        if abs(ir1[2]) < 0.9:
            perp = _cross(ir1, (0.0, 0.0, 1.0))
        else:
            perp = _cross(ir1, (0.0, 1.0, 0.0))
        perp_mag = _norm(perp)
        if perp_mag > 1e-15:
            perp = _scale(1.0 / perp_mag, perp)
        perturb = max(r2_mag * 1e-8, 1.0)
        r2 = _add(r2, _scale(perturb, perp))
        r2_mag = _norm(r2)
        cos_dnu = _dot(r1, r2) / (r1_mag * r2_mag)
        cos_dnu = max(-1.0, min(1.0, cos_dnu))
        c_cross = _cross(r1, r2)
        cross_z = c_cross[2]

    if clockwise:
        if cross_z >= 0.0:
            dnu = 2.0 * math.pi - math.acos(cos_dnu)
        else:
            dnu = math.acos(cos_dnu)
    else:
        if cross_z >= 0.0:
            dnu = math.acos(cos_dnu)
        else:
            dnu = 2.0 * math.pi - math.acos(cos_dnu)

    # ── A parameter ─────────────────────────────────────────
    # Use half-angle formula for numerical stability near 180°:
    # A = sin(dν) · √(r1·r2 / (1 − cos dν))
    #   = 2·sin(dν/2)·cos(dν/2) · √(r1·r2) / (√2·|sin(dν/2)|)
    #   = √2 · cos(dν/2) · √(r1·r2)   [for sin(dν/2) > 0]
    sin_dnu = math.sin(dnu)
    denom = 1.0 - cos_dnu
    if abs(denom) < 1e-14:
        # 0° or 360°: truly degenerate (same direction)
        return None

    A = sin_dnu * math.sqrt(r1_mag * r2_mag / denom)
    if abs(A) < 1e-14:
        return None

    # ── Newton–Raphson on z ─────────────────────────────────
    # F(z) = (χ³ · S(z) + A · √y) / √μ  − Δt = 0
    #
    # where  y = r1 + r2 + A·(z·S − 1)/√C
    #        χ = √(y / C)
    #        C = c2(z),  S = c3(z)

    def _y(z: float, C: float, S: float) -> float:
        return r1_mag + r2_mag + A * (z * S - 1.0) / math.sqrt(C)

    def _F(z: float) -> float:
        C = _stumpff_c2(z)
        S = _stumpff_c3(z)
        y = _y(z, C, S)
        if y < 0.0:
            return float("inf")
        chi = math.sqrt(y / C)
        return (chi ** 3 * S + A * math.sqrt(y)) / math.sqrt(mu) - tof

    def _dFdz(z: float) -> float:
        C = _stumpff_c2(z)
        S = _stumpff_c3(z)
        y = _y(z, C, S)
        if y < 0.0:
            return 1.0  # dummy positive
        chi = math.sqrt(y / C)
        sqrt_mu = math.sqrt(mu)
        if abs(z) > 1e-12:
            return (chi ** 3 * (S - 3.0 * S / (2.0 * C) * (C - 0.5)) / (2.0 * z)
                    + (A / 8.0) * (3.0 * S * math.sqrt(y) / C + A / chi)) / sqrt_mu
        else:
            # Limiting form near z = 0
            return (math.sqrt(2.0) / 40.0 * y ** 1.5
                    + (A / 8.0) * (math.sqrt(y) + A * math.sqrt(1.0 / (2.0 * y)))) / sqrt_mu

    # Find initial bracket [z_lo, z_hi] where F changes sign.
    # For 0-rev elliptic, z > 0.  For hyperbolic, z < 0.
    # Start with z = 0 and step until sign change, or use bisection.
    z = 0.0
    F0 = _F(0.0)

    # Try Newton first with z=0 initial guess
    converged = False
    for _ in range(200):
        C = _stumpff_c2(z)
        S = _stumpff_c3(z)
        y_val = _y(z, C, S)

        while y_val < 0.0:
            # Increase z to make y positive
            z += 0.1
            C = _stumpff_c2(z)
            S = _stumpff_c3(z)
            y_val = _y(z, C, S)

        chi = math.sqrt(y_val / C)
        sqrt_mu = math.sqrt(mu)
        F_val = (chi ** 3 * S + A * math.sqrt(y_val)) / sqrt_mu - tof

        if abs(F_val) < 1e-8:
            converged = True
            break

        # Analytic derivative
        if abs(z) > 1e-12:
            dFdz = (chi ** 3 * (S - 3.0 * S / (2.0 * C) * (C - 0.5)) / (2.0 * z)
                     + (A / 8.0) * (3.0 * S * math.sqrt(y_val) / C + A / chi)) / sqrt_mu
        else:
            dFdz = (math.sqrt(2.0) / 40.0 * y_val ** 1.5
                    + (A / 8.0) * (math.sqrt(y_val) + A * math.sqrt(1.0 / (2.0 * max(y_val, 1e-30))))) / sqrt_mu

        if abs(dFdz) < 1e-30:
            break

        z_new = z - F_val / dFdz

        # Damping: if step is too large, halve it
        if abs(z_new - z) > 10.0 * abs(z) + 10.0:
            z_new = z - 0.5 * F_val / dFdz

        z = z_new

    if not converged:
        return None

    # ── Compute velocities from z ───────────────────────────
    C = _stumpff_c2(z)
    S = _stumpff_c3(z)
    y_val = _y(z, C, S)
    if y_val < 0.0:
        return None

    f = 1.0 - y_val / r1_mag
    g = A * math.sqrt(y_val / mu)
    g_dot = 1.0 - y_val / r2_mag

    if abs(g) < 1e-15:
        return None

    v1 = _scale(1.0 / g, _sub(r2, _scale(f, r1)))
    v2 = _scale(1.0 / g, _sub(_scale(g_dot, r2), r1))

    return (v1, v2)


def _solve_lambert_multirev(
    r1: Vec3,
    r2: Vec3,
    tof: float,
    mu: float,
    clockwise: bool = False,
    N: int = 1,
    path_type: str = "short",
) -> Optional[Tuple[Vec3, Vec3]]:
    """Solve Lambert's problem for N complete revolutions.

    For each revolution count N ≥ 1 there can be two solutions: a "short-period"
    (low-energy) and a "long-period" (high-energy) solution, corresponding to
    z values just above and further above the minimum z = (2πN)².

    Uses bisection to robustly find z (Newton can diverge for multi-rev).
    """
    r1_mag = _norm(r1)
    r2_mag = _norm(r2)
    if r1_mag < 1e-10 or r2_mag < 1e-10 or tof <= 0.0 or mu <= 0.0:
        return None

    # Transfer angle (same logic as 0-rev, including 180° perturbation)
    cos_dnu = _dot(r1, r2) / (r1_mag * r2_mag)
    cos_dnu = max(-1.0, min(1.0, cos_dnu))

    c_cross = _cross(r1, r2)
    cross_z = c_cross[2]

    c_cross_mag = _norm(c_cross)
    if c_cross_mag < 1e-6 * r1_mag * r2_mag and cos_dnu < -0.99:
        ir1 = _scale(1.0 / r1_mag, r1)
        if abs(ir1[2]) < 0.9:
            perp = _cross(ir1, (0.0, 0.0, 1.0))
        else:
            perp = _cross(ir1, (0.0, 1.0, 0.0))
        perp_mag = _norm(perp)
        if perp_mag > 1e-15:
            perp = _scale(1.0 / perp_mag, perp)
        perturb = max(r2_mag * 1e-8, 1.0)
        r2 = _add(r2, _scale(perturb, perp))
        r2_mag = _norm(r2)
        cos_dnu = _dot(r1, r2) / (r1_mag * r2_mag)
        cos_dnu = max(-1.0, min(1.0, cos_dnu))
        c_cross = _cross(r1, r2)
        cross_z = c_cross[2]

    if clockwise:
        if cross_z >= 0.0:
            dnu = 2.0 * math.pi - math.acos(cos_dnu)
        else:
            dnu = math.acos(cos_dnu)
    else:
        if cross_z >= 0.0:
            dnu = math.acos(cos_dnu)
        else:
            dnu = 2.0 * math.pi - math.acos(cos_dnu)

    sin_dnu = math.sin(dnu)
    denom = 1.0 - cos_dnu
    if abs(denom) < 1e-14:
        return None
    A = sin_dnu * math.sqrt(r1_mag * r2_mag / denom)
    if abs(A) < 1e-14:
        return None

    sqrt_mu = math.sqrt(mu)

    def _tof_from_z(z: float) -> float:
        C = _stumpff_c2(z)
        S = _stumpff_c3(z)
        y = r1_mag + r2_mag + A * (z * S - 1.0) / math.sqrt(C)
        if y < 0.0:
            return float("inf")
        chi = math.sqrt(y / C)
        return (chi ** 3 * S + A * math.sqrt(y)) / sqrt_mu

    # For N revolutions the z must be in the elliptic region: z > (2πN)²
    z_lo = (2.0 * math.pi * N) ** 2 + 1e-4
    z_hi = (2.0 * math.pi * (N + 1)) ** 2 - 1e-4

    # Find minimum TOF in [z_lo, z_hi] via golden section
    gr = (math.sqrt(5.0) + 1.0) / 2.0
    a_gs, b_gs = z_lo, z_hi
    for _ in range(80):
        z1 = b_gs - (b_gs - a_gs) / gr
        z2 = a_gs + (b_gs - a_gs) / gr
        t1 = _tof_from_z(z1)
        t2 = _tof_from_z(z2)
        if t1 < t2:
            b_gs = z2
        else:
            a_gs = z1
        if b_gs - a_gs < 1e-8:
            break

    z_min = (a_gs + b_gs) / 2.0
    tof_min = _tof_from_z(z_min)

    if tof < tof_min * 0.999:
        return None  # TOF too short for this revolution count

    # Two solutions: one on each side of z_min
    if path_type == "short":
        # Short-period: z between z_lo and z_min
        za, zb = z_lo, z_min
    else:
        # Long-period: z between z_min and z_hi
        za, zb = z_min, z_hi

    ta = _tof_from_z(za)
    tb = _tof_from_z(zb)

    # Bisection to find z where tof_from_z(z) = tof
    for _ in range(100):
        zm = (za + zb) / 2.0
        tm = _tof_from_z(zm)
        if abs(tm - tof) < 1e-8:
            break

        # The TOF curve is U-shaped in [z_lo, z_hi] with minimum at z_min.
        # On the left branch (short-period), TOF decreases with z.
        # On the right branch (long-period), TOF increases with z.
        if path_type == "short":
            if tm > tof:
                za = zm  # Need larger z (lower TOF)
            else:
                zb = zm
        else:
            if tm > tof:
                zb = zm  # Need smaller z (lower TOF)
            else:
                za = zm

        if abs(zb - za) < 1e-12:
            break

    z = (za + zb) / 2.0

    # Verify convergence
    t_check = _tof_from_z(z)
    if abs(t_check - tof) > 1.0:
        return None

    # Compute velocities
    C = _stumpff_c2(z)
    S = _stumpff_c3(z)
    y_val = r1_mag + r2_mag + A * (z * S - 1.0) / math.sqrt(C)
    if y_val < 0.0:
        return None

    f = 1.0 - y_val / r1_mag
    g = A * math.sqrt(y_val / mu)
    g_dot = 1.0 - y_val / r2_mag

    if abs(g) < 1e-15:
        return None

    v1 = _scale(1.0 / g, _sub(r2, _scale(f, r1)))
    v2 = _scale(1.0 / g, _sub(_scale(g_dot, r2), r1))

    return (v1, v2)


# ─── Public API ──────────────────────────────────────────────

def solve_lambert(
    r1: Vec3,
    r2: Vec3,
    tof: float,
    mu: float,
    max_revs: int = 0,
    clockwise: bool = False,
) -> List[Tuple[Vec3, Vec3]]:
    """Solve Lambert's problem.

    Parameters
    ----------
    r1 : (x, y, z) departure position in km
    r2 : (x, y, z) arrival position in km
    tof : time of flight in seconds (must be > 0)
    mu : gravitational parameter in km³/s² (of central body)
    max_revs : maximum number of complete revolutions to consider
        0 = direct transfer only
        N = also find multi-rev solutions up to N revolutions
    clockwise : if True, force retrograde (clockwise) transfer direction

    Returns
    -------
    List of (v1, v2) tuples:
      v1 = departure velocity vector (km/s)
      v2 = arrival velocity vector (km/s)

    For max_revs=0: up to 1 solution.
    For max_revs=N: up to 1 + 2*N solutions (short & long path per rev).
    Empty list if no solution found.
    """
    if tof <= 0.0 or mu <= 0.0:
        return []

    r1_mag = _norm(r1)
    r2_mag = _norm(r2)
    if r1_mag < 1e-10 or r2_mag < 1e-10:
        return []

    # Check for degenerate same-position case
    if _norm(_sub(r1, r2)) < 1e-10:
        return []

    solutions: List[Tuple[Vec3, Vec3]] = []

    # Detect near-180° transfer angle for Battin fallback
    cos_dnu = _dot(r1, r2) / (r1_mag * r2_mag)
    cos_dnu = max(-1.0, min(1.0, cos_dnu))
    is_near_180 = cos_dnu < -0.95  # within ~18° of 180°

    # 0-revolution (direct) transfer
    result = _solve_lambert_uv(r1, r2, tof, mu, clockwise=clockwise)
    if result is not None:
        solutions.append(result)
    elif is_near_180:
        # Battin's method fallback for near-180° where UV solver failed
        result = _solve_lambert_battin(r1, r2, tof, mu, clockwise=clockwise)
        if result is not None:
            solutions.append(result)

    # Multi-revolution solutions
    for N in range(1, max_revs + 1):
        for path_type in ("short", "long"):
            result = _solve_lambert_multirev(
                r1, r2, tof, mu,
                clockwise=clockwise, N=N, path_type=path_type,
            )
            if result is not None:
                solutions.append(result)

    return solutions


# ─── Helper: compute total Δv for a transfer ────────────────

def compute_transfer_dv(
    v1_departure: Vec3,
    v1_body: Vec3,
    v2_arrival: Vec3,
    v2_body: Vec3,
    mu_departure: float,
    r_park_departure: float,
    mu_arrival: float,
    r_park_arrival: float,
) -> Tuple[float, float, float]:
    """Compute patched-conic Δv for an interplanetary transfer.

    Parameters
    ----------
    v1_departure : heliocentric velocity required at departure (km/s)
    v1_body : heliocentric velocity of departure body (km/s)
    v2_arrival : heliocentric velocity at arrival (km/s)
    v2_body : heliocentric velocity of arrival body (km/s)
    mu_departure : gravity parameter of departure body (km³/s²)
    r_park_departure : parking orbit radius at departure (km)
    mu_arrival : gravity parameter of arrival body (km³/s²)
    r_park_arrival : parking orbit radius at arrival (km)

    Returns
    -------
    (dv_depart_m_s, dv_arrive_m_s, total_dv_m_s) — all in m/s
    """
    # Hyperbolic excess velocities
    v_inf_depart = _norm(_sub(v1_departure, v1_body))
    v_inf_arrive = _norm(_sub(v2_arrival, v2_body))

    # Departure burn: from parking orbit to hyperbolic escape
    if mu_departure > 0.0 and r_park_departure > 0.0:
        v_park_dep = math.sqrt(mu_departure / r_park_departure)
        v_hyp_dep = math.sqrt(v_inf_depart ** 2 + 2.0 * mu_departure / r_park_departure)
        dv_depart = abs(v_hyp_dep - v_park_dep)
    else:
        dv_depart = v_inf_depart

    # Arrival burn: from hyperbolic approach to parking orbit
    if mu_arrival > 0.0 and r_park_arrival > 0.0:
        v_park_arr = math.sqrt(mu_arrival / r_park_arrival)
        v_hyp_arr = math.sqrt(v_inf_arrive ** 2 + 2.0 * mu_arrival / r_park_arrival)
        dv_arrive = abs(v_hyp_arr - v_park_arr)
    else:
        dv_arrive = v_inf_arrive

    # Convert km/s to m/s
    dv_depart_m_s = dv_depart * 1000.0
    dv_arrive_m_s = dv_arrive * 1000.0
    total_dv_m_s = dv_depart_m_s + dv_arrive_m_s

    return dv_depart_m_s, dv_arrive_m_s, total_dv_m_s


def compute_hohmann_dv_tof(
    mu: float,
    r1_km: float,
    r2_km: float,
) -> Tuple[float, float]:
    """Hohmann transfer orbit Δv (m/s) and TOF (seconds) between two circular orbits.

    Used for local orbit changes (same body, e.g. LEO → GEO).

    Parameters
    ----------
    mu : gravitational parameter of central body (km³/s²)
    r1_km : radius of departure orbit (km)
    r2_km : radius of arrival orbit (km)

    Returns
    -------
    (dv_m_s, tof_s)
    """
    if r1_km <= 0.0 or r2_km <= 0.0 or mu <= 0.0:
        return 0.0, 0.0
    a_t = 0.5 * (r1_km + r2_km)
    dv1 = math.sqrt(mu / r1_km) * (math.sqrt(2.0 * r2_km / (r1_km + r2_km)) - 1.0)
    dv2 = math.sqrt(mu / r2_km) * (1.0 - math.sqrt(2.0 * r1_km / (r1_km + r2_km)))
    tof_s = math.pi * math.sqrt(a_t ** 3 / mu)
    return (abs(dv1) + abs(dv2)) * 1000.0, tof_s
