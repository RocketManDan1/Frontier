"""
Tests for the 2D orbital mechanics service (orbit_service.py).

Validates:
  - Circular orbit elements ↔ state vector round-trip
  - Eccentric orbit elements ↔ state vector round-trip
  - Kepler equation solvers (elliptic and hyperbolic)
  - Burn application (prograde, retrograde, radial)
  - SOI exit time computation
  - Station matching / auto-docking
  - Hohmann transfer burn sequence
  - Δv decomposition
  - Frame transforms (SOI entry/exit)
  - Hyperbolic orbit handling
  - Edge cases (near-circular, near-parabolic)
"""

import math
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orbit_service import (
    Vec2,
    OrbitElements,
    elements_to_state,
    state_to_elements,
    propagate_position,
    propagate_state,
    apply_burn,
    compute_apoapsis_km,
    compute_periapsis_km,
    orbit_can_escape,
    compute_soi_exit_time,
    transform_to_child_frame,
    transform_to_parent_frame,
    orbit_matches_location,
    hohmann_burn_sequence,
    decompose_dv_to_burn,
    circular_orbit,
    mean_motion,
    orbital_period,
    _solve_kepler_elliptic,
    _solve_kepler_hyperbolic,
    _v2_norm,
    _v2_sub,
    _v2_dot,
)

# ── Constants ──────────────────────────────────────────────

MU_SUN = 1.32712440018e11    # km³/s²
MU_EARTH = 398600.4418       # km³/s²
MU_MARS = 42828.375214       # km³/s²
MU_MOON = 4902.8             # km³/s²

R_LEO = 6578.0               # LEO radius (km) — 200 km altitude
R_GEO = 42164.0              # GEO radius (km)
R_EARTH_ORBIT = 149597870.7  # Earth heliocentric (km)
R_MARS_ORBIT = 227939200.0   # Mars heliocentric (km)

SOI_EARTH = 924000.0         # Earth SOI radius (km)
SOI_MARS = 577000.0          # Mars SOI radius (km)

EPOCH = 946684800.0           # J2000 as unix timestamp (approx)


# ── Helper ─────────────────────────────────────────────────

def _assert_vec2_close(a: Vec2, b: Vec2, tol: float = 1e-6, rel: bool = False):
    """Assert two Vec2s are close. If rel=True, use relative tolerance."""
    for i in range(2):
        if rel:
            denom = max(abs(a[i]), abs(b[i]), 1e-10)
            assert abs(a[i] - b[i]) / denom < tol, f"Component {i}: {a[i]} vs {b[i]}, rel diff {abs(a[i]-b[i])/denom}"
        else:
            assert abs(a[i] - b[i]) < tol, f"Component {i}: {a[i]} vs {b[i]}, diff {abs(a[i]-b[i])}"


# ── Kepler Equation Solvers ────────────────────────────────

class TestKeplerSolvers:
    def test_elliptic_circular(self):
        """For e=0, E = M."""
        for M in [0.0, 0.5, 1.0, math.pi, 5.0]:
            E = _solve_kepler_elliptic(M, 0.0)
            assert abs(E - (M % (2 * math.pi))) < 1e-10

    def test_elliptic_moderate_e(self):
        """Known solution: e=0.5, M=1.0 → verify M = E - e·sin(E)."""
        e = 0.5
        M = 1.0
        E = _solve_kepler_elliptic(M, e)
        M_check = E - e * math.sin(E)
        assert abs(M_check - M) < 1e-10

    def test_elliptic_high_e(self):
        """High eccentricity e=0.99."""
        e = 0.99
        M = 2.5
        E = _solve_kepler_elliptic(M, e)
        M_check = E - e * math.sin(E)
        assert abs(M_check - (M % (2 * math.pi))) < 1e-10

    def test_hyperbolic_zero(self):
        """M=0 → H=0."""
        H = _solve_kepler_hyperbolic(0.0, 2.0)
        assert abs(H) < 1e-10

    def test_hyperbolic_moderate(self):
        """e=2.0, M=1.5 → verify M = e·sinh(H) - H."""
        e = 2.0
        M = 1.5
        H = _solve_kepler_hyperbolic(M, e)
        M_check = e * math.sinh(H) - H
        assert abs(M_check - M) < 1e-10

    def test_hyperbolic_negative_M(self):
        """Negative mean anomaly."""
        e = 1.5
        M = -3.0
        H = _solve_kepler_hyperbolic(M, e)
        M_check = e * math.sinh(H) - H
        assert abs(M_check - M) < 1e-10


# ── Circular Orbit Round-Trip ──────────────────────────────

class TestCircularOrbitRoundTrip:
    def test_leo_circular_position(self):
        """Ship in LEO circular orbit: position at t=epoch should be at M0 angle."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH, angle_deg=0.0)
        r, v = elements_to_state(elems, MU_EARTH, EPOCH)

        # At M0=0, position should be along +x axis at r = R_LEO
        assert abs(_v2_norm(r) - R_LEO) < 1.0
        assert abs(r[0] - R_LEO) < 1.0
        assert abs(r[1]) < 1.0

    def test_leo_circular_velocity(self):
        """Velocity should be v_circular = √(μ/r), perpendicular to r."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH, angle_deg=0.0)
        r, v = elements_to_state(elems, MU_EARTH, EPOCH)

        v_expected = math.sqrt(MU_EARTH / R_LEO)
        assert abs(_v2_norm(v) - v_expected) / v_expected < 1e-6
        # Velocity should be perpendicular to position (dot product ≈ 0)
        assert abs(_v2_dot(r, v)) / (_v2_norm(r) * _v2_norm(v)) < 1e-6

    def test_circular_roundtrip(self):
        """elements → state → elements should preserve all parameters."""
        elems_orig = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH, angle_deg=45.0)
        r, v = elements_to_state(elems_orig, MU_EARTH, EPOCH)
        elems_back = state_to_elements(r, v, MU_EARTH, EPOCH, body_id="earth")

        # Semi-major axis should match
        assert abs(elems_back["a_km"] - R_LEO) / R_LEO < 1e-6
        # Eccentricity should be near zero
        assert elems_back["e"] < 0.001
        # Body ID preserved
        assert elems_back["body_id"] == "earth"

    def test_circular_at_90_deg(self):
        """Position at M0=90° should be along +y axis."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH, angle_deg=90.0)
        r, v = elements_to_state(elems, MU_EARTH, EPOCH)

        assert abs(r[0]) < 1.0
        assert abs(r[1] - R_LEO) < 1.0

    def test_propagate_quarter_period(self):
        """After 1/4 orbital period, ship should move ~90°."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH, angle_deg=0.0)
        period = orbital_period(R_LEO, MU_EARTH)
        t_quarter = EPOCH + period / 4.0

        r = propagate_position(elems, MU_EARTH, t_quarter)
        # Should be near +y axis
        assert abs(r[0]) / R_LEO < 0.02
        assert abs(r[1] - R_LEO) / R_LEO < 0.02

    def test_propagate_full_period(self):
        """After one full period, ship returns to starting position."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH, angle_deg=30.0)
        period = orbital_period(R_LEO, MU_EARTH)

        r0 = propagate_position(elems, MU_EARTH, EPOCH)
        r1 = propagate_position(elems, MU_EARTH, EPOCH + period)

        _assert_vec2_close(r0, r1, tol=0.1)


# ── Eccentric Orbit Round-Trip ─────────────────────────────

class TestEccentricOrbitRoundTrip:
    def test_earth_orbit_roundtrip(self):
        """Eccentric orbit (e=0.5) round-trip preserves elements."""
        elems = {
            "body_id": "earth",
            "a_km": 20000.0,
            "e": 0.5,
            "omega_deg": 45.0,
            "M0_deg": 30.0,
            "epoch_s": EPOCH,
            "direction": 1,
        }
        r, v = elements_to_state(elems, MU_EARTH, EPOCH)
        elems_back = state_to_elements(r, v, MU_EARTH, EPOCH, body_id="earth")

        assert abs(elems_back["a_km"] - 20000.0) / 20000.0 < 1e-6
        assert abs(elems_back["e"] - 0.5) < 1e-6
        # omega might differ by 360° — normalize
        omega_diff = abs(elems_back["omega_deg"] - 45.0) % 360.0
        omega_diff = min(omega_diff, 360.0 - omega_diff)
        assert omega_diff < 0.01

    def test_high_eccentricity_roundtrip(self):
        """e=0.9 orbit round-trip."""
        elems = {
            "body_id": "sun",
            "a_km": 200000000.0,
            "e": 0.9,
            "omega_deg": 120.0,
            "M0_deg": 60.0,
            "epoch_s": EPOCH,
            "direction": 1,
        }
        r, v = elements_to_state(elems, MU_SUN, EPOCH)
        elems_back = state_to_elements(r, v, MU_SUN, EPOCH, body_id="sun")

        assert abs(elems_back["a_km"] - 200000000.0) / 200000000.0 < 1e-5
        assert abs(elems_back["e"] - 0.9) < 1e-5

    def test_periapsis_position(self):
        """At M0=0, ship should be at periapsis distance from body."""
        a = 20000.0
        e = 0.5
        elems = {
            "body_id": "earth",
            "a_km": a,
            "e": e,
            "omega_deg": 0.0,
            "M0_deg": 0.0,
            "epoch_s": EPOCH,
            "direction": 1,
        }
        r, v = elements_to_state(elems, MU_EARTH, EPOCH)
        r_periapsis = a * (1.0 - e)  # 10000 km

        assert abs(_v2_norm(r) - r_periapsis) < 1.0

    def test_apoapsis_position(self):
        """At M0=180°, ship should be at apoapsis distance."""
        a = 20000.0
        e = 0.5
        elems = {
            "body_id": "earth",
            "a_km": a,
            "e": e,
            "omega_deg": 0.0,
            "M0_deg": 180.0,
            "epoch_s": EPOCH,
            "direction": 1,
        }
        r, v = elements_to_state(elems, MU_EARTH, EPOCH)
        r_apoapsis = a * (1.0 + e)  # 30000 km

        assert abs(_v2_norm(r) - r_apoapsis) < 1.0

    def test_conservation_of_energy(self):
        """Orbital energy should be constant at different times."""
        elems = {
            "body_id": "earth",
            "a_km": 15000.0,
            "e": 0.4,
            "omega_deg": 30.0,
            "M0_deg": 0.0,
            "epoch_s": EPOCH,
            "direction": 1,
        }
        expected_energy = -MU_EARTH / (2.0 * 15000.0)

        for dt in [0, 1000, 5000, 10000]:
            r, v = elements_to_state(elems, MU_EARTH, EPOCH + dt)
            energy = 0.5 * _v2_norm(v) ** 2 - MU_EARTH / _v2_norm(r)
            assert abs(energy - expected_energy) / abs(expected_energy) < 1e-8, \
                f"Energy not conserved at dt={dt}: {energy} vs {expected_energy}"

    def test_conservation_of_angular_momentum(self):
        """Angular momentum h = r × v should be constant."""
        elems = {
            "body_id": "earth",
            "a_km": 15000.0,
            "e": 0.4,
            "omega_deg": 30.0,
            "M0_deg": 0.0,
            "epoch_s": EPOCH,
            "direction": 1,
        }
        from orbit_service import _v2_cross_z
        h_values = []
        for dt in [0, 1000, 5000, 10000]:
            r, v = elements_to_state(elems, MU_EARTH, EPOCH + dt)
            h_values.append(_v2_cross_z(r, v))

        for h in h_values:
            assert abs(h - h_values[0]) / abs(h_values[0]) < 1e-8


# ── Burn Application ──────────────────────────────────────

class TestBurnApplication:
    def test_prograde_burn_raises_orbit(self):
        """Prograde burn should increase semi-major axis."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)

        new_elems = apply_burn(elems, MU_EARTH, EPOCH, prograde_m_s=500.0, radial_m_s=0.0)
        assert new_elems["a_km"] > R_LEO
        assert new_elems["e"] > 0.01  # No longer circular

    def test_retrograde_burn_lowers_orbit(self):
        """Retrograde burn should decrease semi-major axis."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)

        new_elems = apply_burn(elems, MU_EARTH, EPOCH, prograde_m_s=-500.0, radial_m_s=0.0)
        assert new_elems["a_km"] < R_LEO
        assert new_elems["e"] > 0.01

    def test_radial_burn_preserves_energy(self):
        """Pure radial burn changes direction but not speed, so a stays same within rounding."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)

        # Small radial to avoid too-large perturbation
        new_elems = apply_burn(elems, MU_EARTH, EPOCH, prograde_m_s=0.0, radial_m_s=100.0)
        # Energy ∝ -1/(2a), so a should be approximately unchanged
        # Radial burns do change speed (they add perpendicular component), but
        # for small burns the change in |v| is second-order
        # For a 100 m/s radial burn against ~7.7 km/s circular: Δ|v|/|v| ≈ (0.1/7.7)² ≈ tiny
        r_rel = abs(new_elems["a_km"] - R_LEO) / R_LEO
        assert r_rel < 0.01  # within 1%

    def test_hohmann_injection_burn(self):
        """Prograde burn for LEO→GEO Hohmann should give expected transfer orbit."""
        v_circ_leo = math.sqrt(MU_EARTH / R_LEO) * 1000.0  # m/s
        a_transfer = 0.5 * (R_LEO + R_GEO)
        v_transfer_peri = math.sqrt(MU_EARTH * (2.0 / R_LEO - 1.0 / a_transfer)) * 1000.0
        dv = v_transfer_peri - v_circ_leo  # ~2440 m/s

        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)
        new_elems = apply_burn(elems, MU_EARTH, EPOCH, prograde_m_s=dv, radial_m_s=0.0)

        # Transfer orbit: a should be (R_LEO + R_GEO) / 2
        assert abs(new_elems["a_km"] - a_transfer) / a_transfer < 0.01
        # Eccentricity: e = (r_apo - r_peri) / (r_apo + r_peri)
        e_expected = (R_GEO - R_LEO) / (R_GEO + R_LEO)
        assert abs(new_elems["e"] - e_expected) < 0.02

    def test_burn_preserves_body_id(self):
        """Body ID should carry through burns."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)
        new_elems = apply_burn(elems, MU_EARTH, EPOCH, prograde_m_s=100.0, radial_m_s=0.0)
        assert new_elems["body_id"] == "earth"

    def test_escape_burn(self):
        """Large prograde burn should create a hyperbolic orbit (e >= 1)."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)
        v_escape = math.sqrt(2.0 * MU_EARTH / R_LEO) * 1000.0  # m/s
        v_circ = math.sqrt(MU_EARTH / R_LEO) * 1000.0
        dv = v_escape - v_circ + 500.0  # 500 m/s beyond escape

        new_elems = apply_burn(elems, MU_EARTH, EPOCH, prograde_m_s=dv, radial_m_s=0.0)
        assert new_elems["e"] >= 1.0
        assert new_elems["a_km"] < 0  # Negative semi-major axis for hyperbolic


# ── Apoapsis / Periapsis ──────────────────────────────────

class TestApsides:
    def test_circular_apsides(self):
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)
        assert abs(compute_apoapsis_km(elems) - R_LEO) < 1.0
        assert abs(compute_periapsis_km(elems) - R_LEO) < 1.0

    def test_eccentric_apsides(self):
        elems = {"a_km": 20000.0, "e": 0.5, "body_id": "earth",
                 "omega_deg": 0.0, "M0_deg": 0.0, "epoch_s": EPOCH, "direction": 1}
        assert abs(compute_apoapsis_km(elems) - 30000.0) < 1.0
        assert abs(compute_periapsis_km(elems) - 10000.0) < 1.0

    def test_hyperbolic_apoapsis_infinite(self):
        elems = {"a_km": -20000.0, "e": 1.5, "body_id": "earth",
                 "omega_deg": 0.0, "M0_deg": 0.0, "epoch_s": EPOCH, "direction": 1}
        assert compute_apoapsis_km(elems) == float("inf")

    def test_hyperbolic_periapsis(self):
        elems = {"a_km": -20000.0, "e": 1.5, "body_id": "earth",
                 "omega_deg": 0.0, "M0_deg": 0.0, "epoch_s": EPOCH, "direction": 1}
        expected = 20000.0 * (1.5 - 1.0)  # |a| * (e - 1)
        assert abs(compute_periapsis_km(elems) - expected) < 1.0


# ── SOI Detection ─────────────────────────────────────────

class TestSOIDetection:
    def test_circular_leo_cannot_escape(self):
        """LEO circular orbit should not escape Earth's SOI."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)
        assert not orbit_can_escape(elems, SOI_EARTH)

    def test_hyperbolic_can_escape(self):
        """Hyperbolic orbit should always escape."""
        elems = {"a_km": -20000.0, "e": 1.5, "body_id": "earth",
                 "omega_deg": 0.0, "M0_deg": 0.0, "epoch_s": EPOCH, "direction": 1}
        assert orbit_can_escape(elems, SOI_EARTH)

    def test_high_elliptic_can_escape(self):
        """Elliptic orbit with apoapsis > SOI should escape."""
        # a*(1+e) > SOI_EARTH → choose a=500000, e=0.9 → apo=950000 > 924000
        elems = {"a_km": 500000.0, "e": 0.9, "body_id": "earth",
                 "omega_deg": 0.0, "M0_deg": 0.0, "epoch_s": EPOCH, "direction": 1}
        assert orbit_can_escape(elems, SOI_EARTH)

    def test_soi_exit_time_hyperbolic(self):
        """Hyperbolic orbit should have a computable SOI exit time."""
        # Create escape orbit via burn
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)
        v_escape = math.sqrt(2.0 * MU_EARTH / R_LEO) * 1000.0
        v_circ = math.sqrt(MU_EARTH / R_LEO) * 1000.0
        escape_elems = apply_burn(elems, MU_EARTH, EPOCH, prograde_m_s=v_escape - v_circ + 1000.0, radial_m_s=0.0)

        t_exit = compute_soi_exit_time(escape_elems, MU_EARTH, SOI_EARTH)
        assert t_exit is not None
        assert t_exit > EPOCH

        # Verify: position at exit time should be ≈ SOI radius
        r_exit = propagate_position(escape_elems, MU_EARTH, t_exit)
        assert abs(_v2_norm(r_exit) - SOI_EARTH) / SOI_EARTH < 0.05

    def test_soi_exit_time_bound_orbit(self):
        """Bound circular orbit should return None for SOI exit."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)
        assert compute_soi_exit_time(elems, MU_EARTH, SOI_EARTH) is None


# ── SOI Frame Transforms ─────────────────────────────────

class TestSOIFrameTransforms:
    def test_child_entry_roundtrip(self):
        """Enter child SOI → exit back to parent should recover original state."""
        # Ship at some position in parent frame
        r_parent = (400000.0, 0.0)
        v_parent = (0.0, 1.5)  # km/s

        # Child body (Moon-like) position
        child_r = (384400.0, 0.0)
        child_v = (0.0, 1.022)

        # Enter child frame
        child_elems = transform_to_child_frame(
            r_parent, v_parent, child_r, child_v, MU_MOON, EPOCH, "moon"
        )

        # Recover state in child frame
        r_local, v_local = elements_to_state(child_elems, MU_MOON, EPOCH)

        # Exit back to parent frame
        parent_elems = transform_to_parent_frame(
            r_local, v_local, child_r, child_v, MU_EARTH, EPOCH, "earth"
        )

        r_final, v_final = elements_to_state(parent_elems, MU_EARTH, EPOCH)

        _assert_vec2_close(r_parent, r_final, tol=1.0)
        _assert_vec2_close(v_parent, v_final, tol=0.001)

    def test_child_frame_velocity_subtraction(self):
        """Entering child SOI should subtract child's velocity."""
        r_parent = (384400.0, 100.0)
        v_parent = (0.5, 2.0)
        child_r = (384400.0, 0.0)
        child_v = (0.0, 1.022)

        child_elems = transform_to_child_frame(
            r_parent, v_parent, child_r, child_v, MU_MOON, EPOCH, "moon"
        )

        r_local, v_local = elements_to_state(child_elems, MU_MOON, EPOCH)

        # Position should be offset by child position
        assert abs(r_local[0] - 0.0) < 1.0
        assert abs(r_local[1] - 100.0) < 1.0

        # Velocity should be offset by child velocity
        assert abs(v_local[0] - 0.5) < 0.01
        assert abs(v_local[1] - (2.0 - 1.022)) < 0.01


# ── Station Matching ──────────────────────────────────────

class TestLocationMatching:
    def test_matching_circular_orbit(self):
        """Circular orbit at location radius should match."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)
        assert orbit_matches_location(elems, "earth", R_LEO)

    def test_wrong_body_no_match(self):
        """Orbit around wrong body should not match."""
        elems = circular_orbit("mars", R_LEO, MU_MARS, EPOCH)
        assert not orbit_matches_location(elems, "earth", R_LEO)

    def test_eccentric_no_match(self):
        """Eccentric orbit should not match location."""
        elems = {"body_id": "earth", "a_km": R_LEO, "e": 0.2,
                 "omega_deg": 0.0, "M0_deg": 0.0, "epoch_s": EPOCH, "direction": 1}
        assert not orbit_matches_location(elems, "earth", R_LEO)

    def test_wrong_radius_no_match(self):
        """Orbit at different radius should not match."""
        elems = circular_orbit("earth", R_GEO, MU_EARTH, EPOCH)
        assert not orbit_matches_location(elems, "earth", R_LEO)

    def test_close_radius_matches(self):
        """Orbit within 2% of location radius should match."""
        radius_close = R_LEO * 1.015  # 1.5% off
        elems = circular_orbit("earth", radius_close, MU_EARTH, EPOCH)
        assert orbit_matches_location(elems, "earth", R_LEO)

    def test_tight_tolerance(self):
        """Custom tight tolerance."""
        radius_close = R_LEO * 1.005
        elems = circular_orbit("earth", radius_close, MU_EARTH, EPOCH)
        assert orbit_matches_location(elems, "earth", R_LEO, radius_tolerance=0.01)
        assert not orbit_matches_location(elems, "earth", R_LEO, radius_tolerance=0.001)


# ── Hohmann Transfer ──────────────────────────────────────

class TestHohmannTransfer:
    def test_leo_to_geo_dv(self):
        """LEO→GEO Hohmann should give well-known Δv ≈ 3.9 km/s total."""
        plan = hohmann_burn_sequence(MU_EARTH, R_LEO, R_GEO, EPOCH, body_id="earth")

        assert len(plan["burns"]) == 2
        total_dv = plan["total_dv_m_s"]
        # Known LEO→GEO Hohmann ≈ 3930 m/s
        assert 3800 < total_dv < 4100, f"Total Δv = {total_dv} m/s, expected ~3930"

    def test_leo_to_geo_tof(self):
        """Transfer time should be about 5.25 hours (half Hohmann period)."""
        plan = hohmann_burn_sequence(MU_EARTH, R_LEO, R_GEO, EPOCH, body_id="earth")

        # a_transfer = (6578 + 42164) / 2 = 24371 km
        # T = 2π√(a³/μ) → T/2 ≈ 18,900 s ≈ 5.25 hours
        assert 18000 < plan["total_tof_s"] < 20000

    def test_hohmann_burn_signs(self):
        """For raising orbit: both burns should be prograde (positive)."""
        plan = hohmann_burn_sequence(MU_EARTH, R_LEO, R_GEO, EPOCH)
        assert plan["burns"][0]["prograde_m_s"] > 0
        assert plan["burns"][1]["prograde_m_s"] > 0

    def test_hohmann_lowering_orbit(self):
        """For lowering orbit: both burns should be retrograde (negative)."""
        plan = hohmann_burn_sequence(MU_EARTH, R_GEO, R_LEO, EPOCH)
        assert plan["burns"][0]["prograde_m_s"] < 0
        assert plan["burns"][1]["prograde_m_s"] < 0

    def test_hohmann_burn2_timing(self):
        """Second burn should happen at departure_time + tof."""
        plan = hohmann_burn_sequence(MU_EARTH, R_LEO, R_GEO, EPOCH)
        assert abs(plan["burns"][1]["time_s"] - (EPOCH + plan["total_tof_s"])) < 1.0

    def test_hohmann_simulation(self):
        """Actually apply both Hohmann burns and verify arrival orbit."""
        plan = hohmann_burn_sequence(MU_EARTH, R_LEO, R_GEO, EPOCH, body_id="earth")

        # Start with circular LEO orbit
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)

        # Apply burn 1
        b1 = plan["burns"][0]
        elems = apply_burn(elems, MU_EARTH, b1["time_s"], b1["prograde_m_s"], b1["radial_m_s"])

        # Verify transfer orbit
        assert abs(elems["a_km"] - (R_LEO + R_GEO) / 2.0) / ((R_LEO + R_GEO) / 2.0) < 0.02

        # Apply burn 2
        b2 = plan["burns"][1]
        elems = apply_burn(elems, MU_EARTH, b2["time_s"], b2["prograde_m_s"], b2["radial_m_s"])

        # Verify arrival: should be near-circular at GEO radius
        assert abs(elems["a_km"] - R_GEO) / R_GEO < 0.02
        assert elems["e"] < 0.03  # Near circular


# ── Δv Decomposition ─────────────────────────────────────

class TestDvDecomposition:
    def test_pure_prograde(self):
        """Δv aligned with velocity → all prograde, no radial."""
        v = (0.0, 7.7)  # km/s, +y
        r = (6578.0, 0.0)  # +x
        dv = (0.0, 0.5)  # km/s, along v

        pro, rad = decompose_dv_to_burn(dv, v, r)
        assert abs(pro - 500.0) < 1.0  # 0.5 km/s = 500 m/s prograde
        assert abs(rad) < 1.0

    def test_pure_radial(self):
        """Δv aligned with position → all radial, no prograde."""
        v = (0.0, 7.7)
        r = (6578.0, 0.0)
        dv = (0.3, 0.0)  # Along r = radial out

        pro, rad = decompose_dv_to_burn(dv, v, r)
        assert abs(pro) < 1.0
        assert abs(rad - 300.0) < 1.0

    def test_mixed_burn(self):
        """45° Δv → equal prograde and radial."""
        v = (0.0, 7.7)
        r = (6578.0, 0.0)
        dv_mag = 0.5
        dv = (dv_mag / math.sqrt(2), dv_mag / math.sqrt(2))

        pro, rad = decompose_dv_to_burn(dv, v, r)
        expected = dv_mag / math.sqrt(2) * 1000.0
        assert abs(pro - expected) < 1.0
        assert abs(rad - expected) < 1.0


# ── Mean Motion & Period ──────────────────────────────────

class TestMeanMotionAndPeriod:
    def test_leo_period(self):
        """LEO period should be about 5400 seconds (90 minutes)."""
        T = orbital_period(R_LEO, MU_EARTH)
        assert 5200 < T < 5600

    def test_geo_period(self):
        """GEO period should be about 86164 seconds (sidereal day)."""
        T = orbital_period(R_GEO, MU_EARTH)
        assert abs(T - 86164.0) / 86164.0 < 0.001

    def test_earth_helio_period(self):
        """Earth's heliocentric period should be ~365.25 days."""
        T = orbital_period(R_EARTH_ORBIT, MU_SUN)
        assert abs(T - 365.25 * 86400) / (365.25 * 86400) < 0.001

    def test_hyperbolic_period_infinite(self):
        """Hyperbolic orbit has infinite period."""
        T = orbital_period(-20000.0, MU_EARTH)
        assert T == float("inf")


# ── Hyperbolic Orbit Handling ─────────────────────────────

class TestHyperbolicOrbits:
    def test_hyperbolic_state_roundtrip(self):
        """Hyperbolic orbit elements ↔ state round-trip."""
        elems = {
            "body_id": "earth",
            "a_km": -20000.0,
            "e": 1.5,
            "omega_deg": 30.0,
            "M0_deg": 10.0,
            "epoch_s": EPOCH,
            "direction": 1,
        }
        r, v = elements_to_state(elems, MU_EARTH, EPOCH)

        # Verify position is at finite distance
        assert _v2_norm(r) > 0
        assert _v2_norm(r) < 1e8

        # Round-trip
        elems_back = state_to_elements(r, v, MU_EARTH, EPOCH, body_id="earth")
        assert abs(elems_back["a_km"] - (-20000.0)) / 20000.0 < 0.01
        assert abs(elems_back["e"] - 1.5) < 0.01

    def test_hyperbolic_propagation_moves_outward(self):
        """Hyperbolic orbit with positive M0 should move outward over time."""
        elems = {
            "body_id": "earth",
            "a_km": -20000.0,
            "e": 2.0,
            "omega_deg": 0.0,
            "M0_deg": 5.0,
            "epoch_s": EPOCH,
            "direction": 1,
        }
        r0 = propagate_position(elems, MU_EARTH, EPOCH)
        r1 = propagate_position(elems, MU_EARTH, EPOCH + 10000)

        assert _v2_norm(r1) > _v2_norm(r0)

    def test_hyperbolic_energy_positive(self):
        """Hyperbolic orbit should have positive specific energy."""
        elems = {
            "body_id": "earth",
            "a_km": -20000.0,
            "e": 1.5,
            "omega_deg": 0.0,
            "M0_deg": 5.0,
            "epoch_s": EPOCH,
            "direction": 1,
        }
        r, v = elements_to_state(elems, MU_EARTH, EPOCH)
        energy = 0.5 * _v2_norm(v) ** 2 - MU_EARTH / _v2_norm(r)
        assert energy > 0


# ── Retrograde (direction = -1) ───────────────────────────

class TestRetrogradeOrbits:
    def test_retrograde_circular_velocity(self):
        """Retrograde circular orbit should have CW (negative angular momentum)."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)
        elems["direction"] = -1

        r, v = elements_to_state(elems, MU_EARTH, EPOCH)
        from orbit_service import _v2_cross_z
        h = _v2_cross_z(r, v)
        assert h < 0  # CW = negative angular momentum

    def test_retrograde_roundtrip(self):
        """Retrograde orbit round-trip should preserve direction."""
        elems = circular_orbit("earth", R_LEO, MU_EARTH, EPOCH)
        elems["direction"] = -1

        r, v = elements_to_state(elems, MU_EARTH, EPOCH)
        elems_back = state_to_elements(r, v, MU_EARTH, EPOCH, body_id="earth")

        assert elems_back["direction"] == -1


# ── Migration Test ────────────────────────────────────────

class TestOrbitMigration:
    def test_migration_adds_columns(self, db_conn):
        """Migration 0018 should add orbit_json, maneuver_json, orbit_body_id columns."""
        # The db_conn fixture applies all migrations including 0018
        # Insert a ship and verify the new columns are accessible
        db_conn.execute(
            """INSERT INTO ships (id, name, shape, color, size_px, location_id, parts_json,
               fuel_kg, fuel_capacity_kg, dry_mass_kg, isp_s)
               VALUES ('test_orbit_ship', 'Orbit Test', 'triangle', '#ff0000', 12,
                       'LEO', '[]', 100.0, 200.0, 500.0, 350.0)"""
        )

        # Should be able to read and write orbit_json
        db_conn.execute(
            "UPDATE ships SET orbit_json = ?, maneuver_json = ?, orbit_body_id = ? WHERE id = ?",
            ('{"body_id":"earth","a_km":6778}', '[{"time_s":1000}]', 'earth', 'test_orbit_ship'),
        )
        row = db_conn.execute(
            "SELECT orbit_json, maneuver_json, orbit_body_id FROM ships WHERE id = ?",
            ('test_orbit_ship',),
        ).fetchone()

        assert row["orbit_json"] == '{"body_id":"earth","a_km":6778}'
        assert row["maneuver_json"] == '[{"time_s":1000}]'
        assert row["orbit_body_id"] == "earth"

    def test_existing_ships_orbit_null(self, db_conn):
        """Existing ships should have NULL orbit columns (not breaking anything)."""
        db_conn.execute(
            """INSERT INTO ships (id, name, shape, color, size_px, location_id, parts_json,
               fuel_kg, fuel_capacity_kg, dry_mass_kg, isp_s)
               VALUES ('legacy_ship', 'Legacy', 'triangle', '#ffffff', 12,
                       'LEO', '[]', 50.0, 100.0, 300.0, 300.0)"""
        )
        row = db_conn.execute(
            "SELECT orbit_json, maneuver_json, orbit_body_id FROM ships WHERE id = ?",
            ('legacy_ship',),
        ).fetchone()

        assert row["orbit_json"] is None
        assert row["maneuver_json"] is None
        assert row["orbit_body_id"] is None
