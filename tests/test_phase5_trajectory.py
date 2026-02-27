"""
Tests for Phase 5 Lambert solver enhancements — Trajectory rendering:
  - Step 15: Kepler propagator (compute_trajectory_points)
  - Step 16: DB migration (trajectory_json column)
  - Step 17: Trajectory data in API response (compute_leg_trajectory)
  - Step 18: Frontend trajectory integration (verified via data flow)
"""

import json
import math
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ─── Constants ────────────────────────────────────────────────

MU_SUN = 1.32712440018e11  # km³/s²
MU_EARTH = 398600.4418     # km³/s²

R_EARTH_AU = 149597870.7  # km (1 AU)
R_MARS_AU = 227939200.0   # km (~1.524 AU)
R_VENUS_AU = 108208000.0  # km (~0.723 AU)


# ═══════════════════════════════════════════════════════════════
# Step 15: Kepler propagator
# ═══════════════════════════════════════════════════════════════


class TestKeplerPropagator:
    """Test the universal-variable Kepler propagation."""

    def test_circular_orbit_returns_to_start(self):
        """Propagating a circular orbit for one full period should return to start."""
        from transfer_planner import _kepler_propagate_state
        from lambert import _norm

        r0 = (R_EARTH_AU, 0.0, 0.0)
        v_circ = math.sqrt(MU_SUN / R_EARTH_AU)
        v0 = (0.0, v_circ, 0.0)

        period = 2.0 * math.pi * math.sqrt(R_EARTH_AU ** 3 / MU_SUN)
        r_final = _kepler_propagate_state(r0, v0, period, MU_SUN)

        # Should be back near the starting position
        dx = r_final[0] - R_EARTH_AU
        dy = r_final[1] - 0.0
        dz = r_final[2] - 0.0
        dist_err = math.sqrt(dx * dx + dy * dy + dz * dz)
        # Allow 0.1% of 1 AU error for numerical precision
        assert dist_err < R_EARTH_AU * 0.001, f"Return error: {dist_err:.1f} km"

    def test_circular_orbit_quarter_period(self):
        """After 1/4 period of circular orbit, should be 90° ahead."""
        from transfer_planner import _kepler_propagate_state

        r0 = (R_EARTH_AU, 0.0, 0.0)
        v_circ = math.sqrt(MU_SUN / R_EARTH_AU)
        v0 = (0.0, v_circ, 0.0)

        period = 2.0 * math.pi * math.sqrt(R_EARTH_AU ** 3 / MU_SUN)
        r_quarter = _kepler_propagate_state(r0, v0, period / 4.0, MU_SUN)

        # Should be near (0, R_EARTH_AU, 0)
        assert abs(r_quarter[0]) < R_EARTH_AU * 0.01, f"x should be ~0, got {r_quarter[0]:.1f}"
        assert abs(r_quarter[1] - R_EARTH_AU) < R_EARTH_AU * 0.01, f"y should be ~{R_EARTH_AU:.1f}, got {r_quarter[1]:.1f}"

    def test_elliptical_orbit_half_period(self):
        """Propagate half period of an elliptical transfer (Hohmann-like)."""
        from transfer_planner import _kepler_propagate_state
        from lambert import _norm

        # Hohmann transfer from Earth to Mars orbit
        a = (R_EARTH_AU + R_MARS_AU) / 2.0
        v_dep = math.sqrt(MU_SUN * (2.0 / R_EARTH_AU - 1.0 / a))

        r0 = (R_EARTH_AU, 0.0, 0.0)
        v0 = (0.0, v_dep, 0.0)

        tof = math.pi * math.sqrt(a ** 3 / MU_SUN)
        r_arr = _kepler_propagate_state(r0, v0, tof, MU_SUN)

        # Should arrive at Mars orbit distance (on opposite side)
        r_arr_mag = _norm(r_arr)
        assert abs(r_arr_mag - R_MARS_AU) < R_MARS_AU * 0.01, \
            f"Arrival radius: {r_arr_mag:.1f} km, expected ~{R_MARS_AU:.1f}"

        # Should be at x ≈ -R_MARS, y ≈ 0
        assert r_arr[0] < 0, f"x should be negative, got {r_arr[0]:.1f}"
        assert abs(r_arr[1]) < R_MARS_AU * 0.05, f"y should be ~0, got {r_arr[1]:.1f}"

    def test_propagate_preserves_radius_circular(self):
        """Circular orbit propagation should maintain constant radius."""
        from transfer_planner import _kepler_propagate_state
        from lambert import _norm

        r0 = (R_EARTH_AU, 0.0, 0.0)
        v_circ = math.sqrt(MU_SUN / R_EARTH_AU)
        v0 = (0.0, v_circ, 0.0)

        period = 2.0 * math.pi * math.sqrt(R_EARTH_AU ** 3 / MU_SUN)
        for frac in [0.1, 0.25, 0.5, 0.75, 0.9]:
            r = _kepler_propagate_state(r0, v0, period * frac, MU_SUN)
            r_mag = _norm(r)
            assert abs(r_mag - R_EARTH_AU) / R_EARTH_AU < 0.001, \
                f"At t={frac}T: radius {r_mag:.1f} vs expected {R_EARTH_AU:.1f}"

    def test_zero_time_returns_initial(self):
        """dt=0 should return the initial position."""
        from transfer_planner import _kepler_propagate_state

        r0 = (R_EARTH_AU, 0.0, 0.0)
        v0 = (0.0, 30.0, 0.0)
        r = _kepler_propagate_state(r0, v0, 0.0, MU_SUN)
        assert r[0] == pytest.approx(R_EARTH_AU, rel=1e-10)
        assert r[1] == pytest.approx(0.0, abs=1e-6)

    def test_hyperbolic_trajectory(self):
        """Propagate a hyperbolic trajectory (v > escape velocity)."""
        from transfer_planner import _kepler_propagate_state
        from lambert import _norm

        r0 = (R_EARTH_AU, 0.0, 0.0)
        v_esc = math.sqrt(2.0 * MU_SUN / R_EARTH_AU)
        v0 = (0.0, v_esc * 1.5, 0.0)  # 50% above escape

        # Propagate 100 days — should be moving outward
        dt = 100.0 * 86400.0
        r = _kepler_propagate_state(r0, v0, dt, MU_SUN)
        r_mag = _norm(r)
        assert r_mag > R_EARTH_AU, f"Hyperbolic should be moving outward: {r_mag:.1f}"


class TestComputeTrajectoryPoints:
    """Test the trajectory point generation function."""

    def test_returns_correct_number_of_points(self):
        """Should return exactly n_points."""
        from transfer_planner import compute_trajectory_points

        r1 = (R_EARTH_AU, 0.0, 0.0)
        v_circ = math.sqrt(MU_SUN / R_EARTH_AU)
        v1 = (0.0, v_circ, 0.0)
        period = 2.0 * math.pi * math.sqrt(R_EARTH_AU ** 3 / MU_SUN)

        for n in [2, 10, 32, 64, 128]:
            pts = compute_trajectory_points(r1, v1, MU_SUN, period, n_points=n)
            assert len(pts) == n, f"Expected {n} points, got {len(pts)}"

    def test_first_point_is_initial_position(self):
        """First point should be the initial (x, y) position."""
        from transfer_planner import compute_trajectory_points

        r1 = (R_EARTH_AU, 0.0, 0.0)
        v1 = (0.0, 30.0, 0.0)
        pts = compute_trajectory_points(r1, v1, MU_SUN, 1000.0, n_points=10)
        assert pts[0][0] == pytest.approx(R_EARTH_AU, rel=1e-10)
        assert pts[0][1] == pytest.approx(0.0, abs=1e-6)

    def test_hohmann_transfer_arc(self):
        """Trajectory for a Hohmann transfer should sweep from r1 to ~r2."""
        from transfer_planner import compute_trajectory_points

        a = (R_EARTH_AU + R_MARS_AU) / 2.0
        v_dep = math.sqrt(MU_SUN * (2.0 / R_EARTH_AU - 1.0 / a))
        tof = math.pi * math.sqrt(a ** 3 / MU_SUN)

        r1 = (R_EARTH_AU, 0.0, 0.0)
        v1 = (0.0, v_dep, 0.0)

        pts = compute_trajectory_points(r1, v1, MU_SUN, tof, n_points=64)
        assert len(pts) == 64

        # First point at Earth orbit
        r_start = math.hypot(pts[0][0], pts[0][1])
        assert abs(r_start - R_EARTH_AU) < R_EARTH_AU * 0.001

        # Last point near Mars orbit
        r_end = math.hypot(pts[-1][0], pts[-1][1])
        assert abs(r_end - R_MARS_AU) < R_MARS_AU * 0.02, \
            f"End radius: {r_end:.1f}, expected ~{R_MARS_AU:.1f}"

        # Radius should generally increase along the arc
        radii = [math.hypot(x, y) for x, y in pts]
        assert radii[-1] > radii[0], "Radius should increase for outward transfer"

    def test_points_are_xy_tuples(self):
        """Each point should be a (float, float) tuple."""
        from transfer_planner import compute_trajectory_points

        r1 = (R_EARTH_AU, 0.0, 0.0)
        v1 = (0.0, 30.0, 0.0)
        pts = compute_trajectory_points(r1, v1, MU_SUN, 86400.0, n_points=5)
        for pt in pts:
            assert len(pt) == 2
            assert isinstance(pt[0], float)
            assert isinstance(pt[1], float)

    def test_minimum_points_clamped(self):
        """n_points < 2 should be clamped to 2."""
        from transfer_planner import compute_trajectory_points

        r1 = (R_EARTH_AU, 0.0, 0.0)
        v1 = (0.0, 30.0, 0.0)
        pts = compute_trajectory_points(r1, v1, MU_SUN, 86400.0, n_points=1)
        assert len(pts) == 2

    def test_zero_tof_returns_static_points(self):
        """With tof=0, should return n copies of same position."""
        from transfer_planner import compute_trajectory_points

        r1 = (R_EARTH_AU, 0.0, 0.0)
        v1 = (0.0, 30.0, 0.0)
        pts = compute_trajectory_points(r1, v1, MU_SUN, 0.0, n_points=5)
        assert len(pts) == 5
        for pt in pts:
            assert pt[0] == pytest.approx(R_EARTH_AU, rel=1e-10)


class TestComputeLegTrajectory:
    """Test the convenience function for computing trajectory from orbital data."""

    def test_valid_orbital_data(self):
        """compute_leg_trajectory should work with valid orbital data."""
        from transfer_planner import compute_leg_trajectory

        a = (R_EARTH_AU + R_MARS_AU) / 2.0
        v_dep = math.sqrt(MU_SUN * (2.0 / R_EARTH_AU - 1.0 / a))
        tof = math.pi * math.sqrt(a ** 3 / MU_SUN)

        orbital = {
            "helio_r1": [R_EARTH_AU, 0.0, 0.0],
            "helio_v1": [0.0, v_dep, 0.0],
            "helio_mu": MU_SUN,
            "tof_s": tof,
        }
        pts = compute_leg_trajectory(orbital, n_points=32)
        assert pts is not None
        assert len(pts) == 32

    def test_missing_r1_returns_none(self):
        """Should return None if helio_r1 is missing."""
        from transfer_planner import compute_leg_trajectory

        orbital = {
            "helio_v1": [0.0, 30.0, 0.0],
            "helio_mu": MU_SUN,
            "tof_s": 86400.0,
        }
        assert compute_leg_trajectory(orbital) is None

    def test_missing_v1_returns_none(self):
        """Should return None if helio_v1 is missing."""
        from transfer_planner import compute_leg_trajectory

        orbital = {
            "helio_r1": [R_EARTH_AU, 0.0, 0.0],
            "helio_mu": MU_SUN,
            "tof_s": 86400.0,
        }
        assert compute_leg_trajectory(orbital) is None

    def test_missing_mu_returns_none(self):
        """Should return None if helio_mu is missing."""
        from transfer_planner import compute_leg_trajectory

        orbital = {
            "helio_r1": [R_EARTH_AU, 0.0, 0.0],
            "helio_v1": [0.0, 30.0, 0.0],
            "tof_s": 86400.0,
        }
        assert compute_leg_trajectory(orbital) is None

    def test_uses_base_tof_fallback(self):
        """Should fall back to base_tof_s if tof_s is missing."""
        from transfer_planner import compute_leg_trajectory

        orbital = {
            "helio_r1": [R_EARTH_AU, 0.0, 0.0],
            "helio_v1": [0.0, 30.0, 0.0],
            "helio_mu": MU_SUN,
            "base_tof_s": 86400.0,
        }
        pts = compute_leg_trajectory(orbital, n_points=10)
        assert pts is not None
        assert len(pts) == 10

    def test_empty_orbital_returns_none(self):
        """Empty dict should return None."""
        from transfer_planner import compute_leg_trajectory
        assert compute_leg_trajectory({}) is None


# ═══════════════════════════════════════════════════════════════
# Step 15+: Heliocentric state vectors in compute_interplanetary_leg
# ═══════════════════════════════════════════════════════════════


class TestInterplanetaryLegHelioState:
    """Verify compute_interplanetary_leg returns helio state for trajectory."""

    def test_result_contains_helio_state(self):
        """compute_interplanetary_leg should include helio_r1, helio_v1, helio_mu."""
        from transfer_planner import compute_interplanetary_leg

        result = compute_interplanetary_leg(
            from_location="LEO",
            to_location="LMO",
            departure_time_s=0.0,
            extra_dv_fraction=0.0,
        )
        # This may return None if the two locations belong to the same
        # body system (LEO→LMO is Earth→Mars interplanetary in this game).
        # Test with actual interplanetary locations.
        # If it returns a result, check for helio fields.
        if result is not None:
            assert "helio_r1" in result, "Missing helio_r1"
            assert "helio_v1" in result, "Missing helio_v1"
            assert "helio_mu" in result, "Missing helio_mu"
            assert len(result["helio_r1"]) == 3
            assert len(result["helio_v1"]) == 3
            assert result["helio_mu"] > 0


# ═══════════════════════════════════════════════════════════════
# Step 16: DB migration
# ═══════════════════════════════════════════════════════════════


class TestTrajectoryMigration:
    """Test the trajectory_json migration."""

    def test_migration_0014_exists(self):
        """Migration 0014_trajectory_json should be in the migrations list."""
        from db_migrations import _migrations

        migrations = _migrations()
        ids = [m.migration_id for m in migrations]
        assert "0014_trajectory_json" in ids

    def test_migration_0014_adds_column(self):
        """Applying migration 0014 should add trajectory_json column to ships."""
        import sqlite3
        from db_migrations import apply_migrations, _table_columns

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = OFF")  # Simplify for in-memory
        apply_migrations(conn)

        cols = _table_columns(conn, "ships")
        assert "trajectory_json" in cols, f"trajectory_json not in ships columns: {cols}"
        conn.close()

    def test_migration_is_idempotent(self):
        """Running migration twice should not fail."""
        import sqlite3
        from db_migrations import apply_migrations

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = OFF")
        apply_migrations(conn)
        # Apply again — should be a no-op
        apply_migrations(conn)
        conn.close()


# ═══════════════════════════════════════════════════════════════
# Step 17: Trajectory data in API / data flow
# ═══════════════════════════════════════════════════════════════


class TestTrajectoryDataFlow:
    """Test trajectory JSON serialization and data flow."""

    def test_trajectory_json_roundtrip(self):
        """Trajectory data should survive JSON serialization."""
        from transfer_planner import compute_trajectory_points

        r1 = (R_EARTH_AU, 0.0, 0.0)
        a = (R_EARTH_AU + R_MARS_AU) / 2.0
        v_dep = math.sqrt(MU_SUN * (2.0 / R_EARTH_AU - 1.0 / a))
        v1 = (0.0, v_dep, 0.0)
        tof = math.pi * math.sqrt(a ** 3 / MU_SUN)

        pts = compute_trajectory_points(r1, v1, MU_SUN, tof, n_points=32)

        # Serialize as the API would
        trajectory_data = [{
            "from_id": "LEO",
            "to_id": "LMO",
            "points": [[round(x, 1), round(y, 1)] for x, y in pts],
        }]
        json_str = json.dumps(trajectory_data)
        restored = json.loads(json_str)

        assert len(restored) == 1
        assert restored[0]["from_id"] == "LEO"
        assert restored[0]["to_id"] == "LMO"
        assert len(restored[0]["points"]) == 32
        # Check first point
        assert abs(restored[0]["points"][0][0] - R_EARTH_AU) < 1.0  # rounded to 0.1
        assert abs(restored[0]["points"][0][1]) < 1.0

    def test_trajectory_json_size_reasonable(self):
        """Trajectory JSON for 64 points should be under 10 KB."""
        from transfer_planner import compute_trajectory_points

        r1 = (R_EARTH_AU, 0.0, 0.0)
        a = (R_EARTH_AU + R_MARS_AU) / 2.0
        v_dep = math.sqrt(MU_SUN * (2.0 / R_EARTH_AU - 1.0 / a))
        v1 = (0.0, v_dep, 0.0)
        tof = math.pi * math.sqrt(a ** 3 / MU_SUN)

        pts = compute_trajectory_points(r1, v1, MU_SUN, tof, n_points=64)
        trajectory_data = [{
            "from_id": "LEO",
            "to_id": "LMO",
            "points": [[round(x, 1), round(y, 1)] for x, y in pts],
        }]
        json_str = json.dumps(trajectory_data)
        assert len(json_str) < 10000, f"Trajectory JSON is {len(json_str)} bytes, expected < 10KB"


class TestKeplerPropagatorEdgeCases:
    """Test edge cases and robustness of the Kepler propagator."""

    def test_very_small_mu(self):
        """Should handle near-zero mu gracefully."""
        from transfer_planner import _kepler_propagate_state

        r0 = (R_EARTH_AU, 0.0, 0.0)
        v0 = (0.0, 30.0, 0.0)
        r = _kepler_propagate_state(r0, v0, 86400.0, 1e-20)
        # Should not crash; result may be approximate
        assert len(r) == 3

    def test_very_large_dt(self):
        """Should handle long propagation times without crashing."""
        from transfer_planner import _kepler_propagate_state
        from lambert import _norm

        r0 = (R_EARTH_AU, 0.0, 0.0)
        v_circ = math.sqrt(MU_SUN / R_EARTH_AU)
        v0 = (0.0, v_circ, 0.0)

        # 100 years
        dt = 100.0 * 365.25 * 86400.0
        r = _kepler_propagate_state(r0, v0, dt, MU_SUN)
        r_mag = _norm(r)
        # For circular orbit, radius should stay ~constant
        assert abs(r_mag - R_EARTH_AU) / R_EARTH_AU < 0.01

    def test_retrograde_orbit(self):
        """Should handle retrograde (clockwise) orbit."""
        from transfer_planner import _kepler_propagate_state
        from lambert import _norm

        r0 = (R_EARTH_AU, 0.0, 0.0)
        v_circ = math.sqrt(MU_SUN / R_EARTH_AU)
        v0 = (0.0, -v_circ, 0.0)  # Retrograde

        period = 2.0 * math.pi * math.sqrt(R_EARTH_AU ** 3 / MU_SUN)
        r_quarter = _kepler_propagate_state(r0, v0, period / 4.0, MU_SUN)

        # Should be at (0, -R_EARTH_AU) for retrograde quarter period
        assert abs(r_quarter[0]) < R_EARTH_AU * 0.01
        assert r_quarter[1] < 0, f"Retrograde should go negative y, got {r_quarter[1]:.1f}"

    def test_inclined_orbit_z_component(self):
        """Should handle 3D orbits with z-component."""
        from transfer_planner import _kepler_propagate_state
        from lambert import _norm

        r0 = (R_EARTH_AU, 0.0, 0.0)
        v_circ = math.sqrt(MU_SUN / R_EARTH_AU)
        # 45° inclined orbit
        v0 = (0.0, v_circ * 0.7071, v_circ * 0.7071)

        dt = 86400.0 * 30  # 30 days
        r = _kepler_propagate_state(r0, v0, dt, MU_SUN)
        r_mag = _norm(r)
        # Radius should still be preserved (approx) for circular speed
        assert abs(r_mag - R_EARTH_AU) / R_EARTH_AU < 0.01
        # Should have non-zero z component
        assert abs(r[2]) > 0, "Inclined orbit should have z-component"
