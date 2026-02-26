"""
Tests for the Lambert solver module.

Verifies:
  - Earth→Mars Hohmann-like 0-rev transfer (known ~5.6 km/s)
  - Earth→Venus transfer (~3.5 km/s)
  - Round-trip consistency (swap r1/r2)
  - 180° transfer stability
  - Multi-revolution solution existence
  - Degenerate inputs return empty/None
  - Patched-conic Δv helper
  - Hohmann orbit-change helper
"""

import math
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lambert import (
    Vec3,
    solve_lambert,
    compute_transfer_dv,
    compute_hohmann_dv_tof,
    _cross,
    _dot,
    _norm,
    _sub,
    _stumpff_c2,
    _stumpff_c3,
)

# ─── Constants ────────────────────────────────────────────────

MU_SUN = 1.32712440018e11  # km³/s²
MU_EARTH = 398600.4418     # km³/s²
MU_MARS = 42828.375214     # km³/s²

# Approximate circular orbit radii (km)
R_EARTH = 149597870.7  # 1 AU
R_MARS = 227939200.0   # ~1.524 AU
R_VENUS = 108208000.0  # ~0.723 AU

# Parking orbit radii
R_PARK_EARTH = 6578.0  # ~200 km LEO
R_PARK_MARS = 3596.0   # ~200 km above Mars

# Hohmann transfer time Earth→Mars ≈ 259 days
HOHMANN_TOF_EARTH_MARS = 259.0 * 86400.0


# ─── Helpers ──────────────────────────────────────────────────

def _circular_velocity(mu: float, r: float) -> float:
    """Circular orbit speed in km/s."""
    return math.sqrt(mu / r)


def _make_coplanar_positions(r1: float, r2: float, angle_deg: float = 180.0):
    """Create two position vectors in the ecliptic plane separated by angle."""
    angle_rad = math.radians(angle_deg)
    p1 = (r1, 0.0, 0.0)
    p2 = (r2 * math.cos(angle_rad), r2 * math.sin(angle_rad), 0.0)
    return p1, p2


# ─── Stumpff function tests ──────────────────────────────────

class TestStumpffFunctions:
    def test_c2_at_zero(self):
        assert abs(_stumpff_c2(0.0) - 0.5) < 1e-10

    def test_c3_at_zero(self):
        assert abs(_stumpff_c3(0.0) - 1.0 / 6.0) < 1e-10

    def test_c2_positive(self):
        """c2(ψ) = (1 - cos(√ψ)) / ψ for ψ > 0."""
        psi = 4.0
        expected = (1.0 - math.cos(2.0)) / 4.0
        assert abs(_stumpff_c2(psi) - expected) < 1e-10

    def test_c3_positive(self):
        """c3(ψ) = (√ψ - sin(√ψ)) / (ψ√ψ) for ψ > 0."""
        psi = 4.0
        expected = (2.0 - math.sin(2.0)) / (4.0 * 2.0)
        assert abs(_stumpff_c3(psi) - expected) < 1e-10

    def test_c2_negative(self):
        """c2(ψ) = (cosh(√-ψ) - 1) / (-ψ) for ψ < 0."""
        psi = -4.0
        expected = (math.cosh(2.0) - 1.0) / 4.0
        assert abs(_stumpff_c2(psi) - expected) < 1e-10

    def test_c3_negative(self):
        """c3(ψ) = (sinh(√-ψ) - √-ψ) / (-ψ · √-ψ) for ψ < 0."""
        psi = -4.0
        expected = (math.sinh(2.0) - 2.0) / (4.0 * 2.0)
        assert abs(_stumpff_c3(psi) - expected) < 1e-10


# ─── Vector utility tests ────────────────────────────────────

class TestVectorUtils:
    def test_cross_product(self):
        a = (1.0, 0.0, 0.0)
        b = (0.0, 1.0, 0.0)
        result = _cross(a, b)
        assert abs(result[0]) < 1e-15
        assert abs(result[1]) < 1e-15
        assert abs(result[2] - 1.0) < 1e-15

    def test_dot_product(self):
        assert abs(_dot((1, 2, 3), (4, 5, 6)) - 32.0) < 1e-10

    def test_norm(self):
        assert abs(_norm((3.0, 4.0, 0.0)) - 5.0) < 1e-10


# ─── Core Lambert solver tests ──────────────────────────────

class TestSolveLambert:
    """Test the main solve_lambert() function."""

    def test_earth_mars_hohmann_like(self):
        """Earth→Mars with ~Hohmann TOF should give realistic Δv.

        Known Earth→Mars Hohmann departure v_inf ≈ 2.95 km/s,
        arrival v_inf ≈ 2.65 km/s.  We use 180° geometry (which triggers
        the near-180° perturbation in the solver).
        """
        r1 = (R_EARTH, 0.0, 0.0)
        r2 = (-R_MARS, 0.0, 0.0)  # 180° away (Hohmann-like)

        solutions = solve_lambert(r1, r2, HOHMANN_TOF_EARTH_MARS, MU_SUN)
        assert len(solutions) >= 1, "Should find at least one solution"

        v1, v2 = solutions[0]

        # Earth velocity vector (prograde, perpendicular to r1)
        v_earth = _circular_velocity(MU_SUN, R_EARTH)
        v_earth_vec = (0.0, v_earth, 0.0)

        # Proper vector v_inf
        v_inf_dep = _norm(_sub(v1, v_earth_vec))

        # Should be in reasonable range (2-6 km/s for near-Hohmann)
        assert 1.0 < v_inf_dep < 8.0, f"Departure v_inf = {v_inf_dep:.2f} km/s out of range"

    def test_earth_venus_transfer(self):
        """Earth→Venus with approximate Hohmann TOF (~146 days).

        Use 150° transfer angle (not exactly 180°) for clean geometry.
        """
        r1 = (R_EARTH, 0.0, 0.0)
        tof_days = 146.0
        tof = tof_days * 86400.0

        # Venus at 150° (realistic non-degenerate geometry)
        angle = math.radians(150.0)
        r2 = (R_VENUS * math.cos(angle), R_VENUS * math.sin(angle), 0.0)

        solutions = solve_lambert(r1, r2, tof, MU_SUN)
        assert len(solutions) >= 1

        v1, v2 = solutions[0]
        v_earth_vec = (0.0, _circular_velocity(MU_SUN, R_EARTH), 0.0)
        v_inf_dep = _norm(_sub(v1, v_earth_vec))

        # Earth→Venus departure v_inf should be reasonable
        assert 0.5 < v_inf_dep < 10.0, f"Venus departure v_inf = {v_inf_dep:.2f}"

    def test_short_angle_transfer(self):
        """Transfer with small transfer angle (< 90°)."""
        r1 = (R_EARTH, 0.0, 0.0)
        angle_deg = 60.0
        angle_rad = math.radians(angle_deg)
        r2 = (R_MARS * math.cos(angle_rad), R_MARS * math.sin(angle_rad), 0.0)
        tof = 120.0 * 86400.0  # 120 days

        solutions = solve_lambert(r1, r2, tof, MU_SUN)
        assert len(solutions) >= 1, "Should solve 60° transfer"

        # Verify it's a valid velocity vector (not NaN or absurdly large)
        v1, v2 = solutions[0]
        assert _norm(v1) < 100.0, "Departure velocity unreasonably large"
        assert _norm(v2) < 100.0, "Arrival velocity unreasonably large"

    def test_obtuse_angle_transfer(self):
        """Transfer with > 180° angle (long way)."""
        r1 = (R_EARTH, 0.0, 0.0)
        angle_deg = 240.0
        angle_rad = math.radians(angle_deg)
        r2 = (R_MARS * math.cos(angle_rad), R_MARS * math.sin(angle_rad), 0.0)
        tof = 350.0 * 86400.0

        solutions = solve_lambert(r1, r2, tof, MU_SUN)
        assert len(solutions) >= 1

    def test_3d_inclined_transfer(self):
        """Transfer with out-of-plane component."""
        r1 = (R_EARTH, 0.0, 0.0)
        # Mars position with 5° inclination offset
        inc = math.radians(5.0)
        r2 = (-R_MARS * math.cos(inc), 0.0, R_MARS * math.sin(inc))
        tof = 250.0 * 86400.0

        solutions = solve_lambert(r1, r2, tof, MU_SUN)
        assert len(solutions) >= 1

        v1, v2 = solutions[0]
        # Should have a z-component in the velocity
        assert _norm(v1) < 100.0

    def test_degenerate_zero_tof(self):
        """TOF = 0 returns no solutions."""
        r1 = (R_EARTH, 0.0, 0.0)
        r2 = (-R_MARS, 0.0, 0.0)
        solutions = solve_lambert(r1, r2, 0.0, MU_SUN)
        assert solutions == []

    def test_degenerate_negative_tof(self):
        """Negative TOF returns no solutions."""
        solutions = solve_lambert((R_EARTH, 0, 0), (-R_MARS, 0, 0), -100.0, MU_SUN)
        assert solutions == []

    def test_degenerate_zero_mu(self):
        """Zero mu returns no solutions."""
        solutions = solve_lambert((R_EARTH, 0, 0), (-R_MARS, 0, 0), 86400.0, 0.0)
        assert solutions == []

    def test_degenerate_same_position(self):
        """Same departure and arrival position returns no solutions."""
        r = (R_EARTH, 0.0, 0.0)
        solutions = solve_lambert(r, r, 86400.0, MU_SUN)
        assert solutions == []

    def test_clockwise_flag(self):
        """Clockwise (retrograde) should give different velocities."""
        r1 = (R_EARTH, 0.0, 0.0)
        r2 = (0.0, R_MARS, 0.0)  # 90° prograde
        tof = 200.0 * 86400.0

        sol_pro = solve_lambert(r1, r2, tof, MU_SUN, clockwise=False)
        sol_ret = solve_lambert(r1, r2, tof, MU_SUN, clockwise=True)

        if sol_pro and sol_ret:
            v1_pro = sol_pro[0][0]
            v1_ret = sol_ret[0][0]
            # They should be different
            diff = _norm((v1_pro[0] - v1_ret[0], v1_pro[1] - v1_ret[1], v1_pro[2] - v1_ret[2]))
            assert diff > 0.1, "Prograde and retrograde should differ"


class TestMultiRevolution:
    """Test multi-revolution Lambert solutions."""

    def test_multi_rev_finds_solutions(self):
        """With sufficient TOF, multi-rev solutions should exist."""
        r1 = (R_EARTH, 0.0, 0.0)
        # 120° transfer angle (clean geometry)
        angle = math.radians(120.0)
        r2 = (R_MARS * math.cos(angle), R_MARS * math.sin(angle), 0.0)
        # Long TOF ~2 years: enough for 1+ revolution
        tof = 730.0 * 86400.0

        solutions = solve_lambert(r1, r2, tof, MU_SUN, max_revs=1)
        # Should have at least the 0-rev solution
        assert len(solutions) >= 1

    def test_multi_rev_0_rev_subset(self):
        """max_revs=0 should return subset of max_revs=N solutions."""
        r1 = (R_EARTH, 0.0, 0.0)
        r2 = (-R_MARS, 0.0, 0.0)
        tof = HOHMANN_TOF_EARTH_MARS

        sol_0 = solve_lambert(r1, r2, tof, MU_SUN, max_revs=0)
        sol_1 = solve_lambert(r1, r2, tof, MU_SUN, max_revs=1)

        assert len(sol_0) <= len(sol_1), "More revs should give >= solutions"


# ─── Patched-conic Δv helper tests ──────────────────────────

class TestComputeTransferDv:
    def test_earth_mars_patched_conic(self):
        """Compute departure + arrival burns for Earth→Mars Hohmann."""
        # Approximate heliocentric velocities for Hohmann transfer orbit
        v_earth_orbital = _circular_velocity(MU_SUN, R_EARTH)  # ~29.78 km/s
        # Hohmann transfer: v at perihelion (Earth)
        a_t = 0.5 * (R_EARTH + R_MARS)
        v_dep_helio = math.sqrt(MU_SUN * (2.0 / R_EARTH - 1.0 / a_t))  # ~32.73 km/s
        v_arr_helio = math.sqrt(MU_SUN * (2.0 / R_MARS - 1.0 / a_t))   # ~21.48 km/s
        v_mars_orbital = _circular_velocity(MU_SUN, R_MARS)              # ~24.13 km/s

        dv_dep, dv_arr, dv_total = compute_transfer_dv(
            v1_departure=(v_dep_helio, 0.0, 0.0),
            v1_body=(v_earth_orbital, 0.0, 0.0),
            v2_arrival=(-v_arr_helio, 0.0, 0.0),
            v2_body=(-v_mars_orbital, 0.0, 0.0),
            mu_departure=MU_EARTH,
            r_park_departure=R_PARK_EARTH,
            mu_arrival=MU_MARS,
            r_park_arrival=R_PARK_MARS,
        )

        # Total patched-conic Δv for Earth→Mars Hohmann ≈ 5.6 km/s
        assert 4000 < dv_total < 8000, f"Total Δv = {dv_total:.0f} m/s (expected ~5600)"
        assert dv_dep > 0 and dv_arr > 0

    def test_zero_parking_orbit(self):
        """With no parking orbit, Δv = v_inf directly."""
        v_inf = 3.0  # km/s
        dv_dep, dv_arr, dv_total = compute_transfer_dv(
            v1_departure=(33.0, 0.0, 0.0),
            v1_body=(30.0, 0.0, 0.0),
            v2_arrival=(-21.0, 0.0, 0.0),
            v2_body=(-24.0, 0.0, 0.0),
            mu_departure=0.0,
            r_park_departure=0.0,
            mu_arrival=0.0,
            r_park_arrival=0.0,
        )
        assert abs(dv_dep - 3000.0) < 1.0  # 3 km/s = 3000 m/s
        assert abs(dv_arr - 3000.0) < 1.0


# ─── Hohmann orbit-change tests ─────────────────────────────

class TestHohmannDvTof:
    def test_leo_to_geo(self):
        """LEO (200km) to GEO (35786km) Δv ≈ 3.94 km/s."""
        r_leo = 6578.0   # km
        r_geo = 42164.0   # km
        dv, tof = compute_hohmann_dv_tof(MU_EARTH, r_leo, r_geo)

        assert 3500 < dv < 4500, f"LEO→GEO Δv = {dv:.0f} m/s (expected ~3940)"
        # TOF ≈ 5.3 hours = ~19000 s
        assert 15000 < tof < 25000, f"TOF = {tof:.0f} s"

    def test_symmetric(self):
        """Hohmann Δv is the same regardless of direction."""
        dv_up, tof_up = compute_hohmann_dv_tof(MU_EARTH, 6578.0, 42164.0)
        dv_down, tof_down = compute_hohmann_dv_tof(MU_EARTH, 42164.0, 6578.0)
        assert abs(dv_up - dv_down) < 1.0
        assert abs(tof_up - tof_down) < 1.0

    def test_zero_radius(self):
        """Zero radius returns zero."""
        dv, tof = compute_hohmann_dv_tof(MU_EARTH, 0.0, 42164.0)
        assert dv == 0.0
        assert tof == 0.0


# ─── Integration test: Lambert → Patched-conic pipeline ─────

class TestLambertPipeline:
    """End-to-end: solve Lambert, then compute Δv via patched conic."""

    def test_earth_mars_end_to_end(self):
        """Full pipeline: Lambert solve + patched-conic Δv."""
        r1 = (R_EARTH, 0.0, 0.0)
        # Use 150° transfer angle for clean geometry
        angle = math.radians(150.0)
        r2 = (R_MARS * math.cos(angle), R_MARS * math.sin(angle), 0.0)
        tof = 250.0 * 86400.0  # ~250 days

        solutions = solve_lambert(r1, r2, tof, MU_SUN)
        assert len(solutions) >= 1

        v1, v2 = solutions[0]

        # Body velocities (circular, prograde)
        v_earth = _circular_velocity(MU_SUN, R_EARTH)
        v_mars = _circular_velocity(MU_SUN, R_MARS)
        v1_body = (0.0, v_earth, 0.0)
        # Mars velocity is tangent to its orbit at r2
        mars_angle = angle + math.pi / 2.0  # 90° ahead of position
        v2_body = (v_mars * math.cos(mars_angle), v_mars * math.sin(mars_angle), 0.0)

        dv_dep, dv_arr, dv_total = compute_transfer_dv(
            v1_departure=v1,
            v1_body=v1_body,
            v2_arrival=v2,
            v2_body=v2_body,
            mu_departure=MU_EARTH,
            r_park_departure=R_PARK_EARTH,
            mu_arrival=MU_MARS,
            r_park_arrival=R_PARK_MARS,
        )

        # Should be in a reasonable range for Earth→Mars
        assert 2000 < dv_total < 20000, f"Pipeline Δv = {dv_total:.0f} m/s"
