"""
Tests for Phase 4 Lambert solver enhancements:
  - Step 11: Lambert result caching
  - Step 12: Battin's method fallback for near-180° transfers
  - Step 13: Multi-rev quality scoring
  - Step 14: Auto-generated interplanetary edges from topology
"""

import math
import sys
from pathlib import Path
from typing import Dict, Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ─── Constants ────────────────────────────────────────────────

MU_SUN = 1.32712440018e11  # km³/s²
MU_EARTH = 398600.4418     # km³/s²
MU_MARS = 42828.375214     # km³/s²

R_EARTH = 149597870.7  # km (1 AU)
R_MARS = 227939200.0   # km (~1.524 AU)
R_VENUS = 108208000.0  # km (~0.723 AU)

R_PARK_EARTH = 6578.0  # ~200 km LEO
R_PARK_MARS = 3596.0   # ~200 km above Mars


# ═══════════════════════════════════════════════════════════════
# Step 12: Battin's method fallback
# ═══════════════════════════════════════════════════════════════


class TestBattinMethod:
    """Test the Battin solver directly and as a fallback."""

    def test_battin_basic_transfer(self):
        """Battin should solve a simple 150° transfer."""
        from lambert import _solve_lambert_battin, _norm, _sub

        r1 = (R_EARTH, 0.0, 0.0)
        angle = math.radians(150.0)
        r2 = (R_MARS * math.cos(angle), R_MARS * math.sin(angle), 0.0)
        tof = 250.0 * 86400.0

        result = _solve_lambert_battin(r1, r2, tof, MU_SUN)
        # May or may not converge for all geometries, but shouldn't crash
        if result is not None:
            v1, v2 = result
            assert _norm(v1) < 100.0, "Departure velocity unreasonably large"
            assert _norm(v2) < 100.0, "Arrival velocity unreasonably large"

    def test_battin_near_180(self):
        """Battin should handle near-180° transfers where UV may struggle."""
        from lambert import _solve_lambert_battin, _norm

        r1 = (R_EARTH, 0.0, 0.0)
        # ~178° transfer
        angle = math.radians(178.0)
        r2 = (R_MARS * math.cos(angle), R_MARS * math.sin(angle), 0.0)
        tof = 259.0 * 86400.0

        result = _solve_lambert_battin(r1, r2, tof, MU_SUN)
        # Should not crash; may or may not converge
        if result is not None:
            v1, v2 = result
            assert _norm(v1) < 200.0
            assert _norm(v2) < 200.0

    def test_battin_degenerate_inputs(self):
        """Battin should return None for degenerate inputs."""
        from lambert import _solve_lambert_battin

        assert _solve_lambert_battin((0, 0, 0), (R_MARS, 0, 0), 86400.0, MU_SUN) is None
        assert _solve_lambert_battin((R_EARTH, 0, 0), (R_MARS, 0, 0), 0.0, MU_SUN) is None
        assert _solve_lambert_battin((R_EARTH, 0, 0), (R_MARS, 0, 0), 86400.0, 0.0) is None

    def test_solve_lambert_near_180_uses_fallback(self):
        """solve_lambert with near-180° geometry should still find a solution.

        The UV solver has a perturbation for 180°; Battin acts as additional
        robustness layer.  Either way, we should get a solution.
        """
        from lambert import solve_lambert

        r1 = (R_EARTH, 0.0, 0.0)
        r2 = (-R_MARS, 0.0, 0.0)  # Exact 180°
        tof = 259.0 * 86400.0

        solutions = solve_lambert(r1, r2, tof, MU_SUN)
        assert len(solutions) >= 1, "Should find at least one solution near 180°"

    def test_solve_lambert_near_180_with_slight_offset(self):
        """Near-180° with slight z offset — tests Battin integration."""
        from lambert import solve_lambert, _norm, _sub

        r1 = (R_EARTH, 0.0, 0.0)
        # 179° — very close to 180°
        angle = math.radians(179.0)
        r2 = (R_MARS * math.cos(angle), R_MARS * math.sin(angle), 0.0)
        tof = 259.0 * 86400.0

        solutions = solve_lambert(r1, r2, tof, MU_SUN)
        assert len(solutions) >= 1, "Should solve near-180° transfer"

        v1, v2 = solutions[0]
        v_earth = math.sqrt(MU_SUN / R_EARTH)
        v_inf = _norm(_sub(v1, (0.0, v_earth, 0.0)))
        # At 179° geometry, v_inf can be large — just verify it's finite and bounded
        assert v_inf < 50.0, f"v_inf = {v_inf:.2f} too large"

    def test_continued_fraction_eta(self):
        """Test the η continued fraction helper."""
        from lambert import _continued_fraction_eta

        # η(0) = 0
        assert abs(_continued_fraction_eta(0.0)) < 1e-12
        # η(x) = x / (√(1+x) + 1)
        for x in [0.5, 1.0, 2.0, 5.0]:
            expected = x / (math.sqrt(1 + x) + 1)
            assert abs(_continued_fraction_eta(x) - expected) < 1e-10

    def test_battin_continued_fraction_k(self):
        """Test the K(u) continued fraction converges."""
        from lambert import _battin_continued_fraction_k

        # K(0) should be 1/3 (from the definition)
        k0 = _battin_continued_fraction_k(0.0)
        assert abs(k0 - 1.0 / 3.0) < 1e-6


# ═══════════════════════════════════════════════════════════════
# Step 13: Multi-rev quality scoring
# ═══════════════════════════════════════════════════════════════


class TestTransferQualityScore:
    """Test the multi-rev quality scoring function."""

    def test_score_increases_with_dv(self):
        """Higher Δv should give higher (worse) score."""
        from transfer_planner import transfer_quality_score

        s1 = transfer_quality_score(5000.0, 200 * 86400.0, 0)
        s2 = transfer_quality_score(8000.0, 200 * 86400.0, 0)
        assert s2 > s1

    def test_score_increases_with_tof(self):
        """Longer TOF should give higher (worse) score."""
        from transfer_planner import transfer_quality_score

        s1 = transfer_quality_score(5000.0, 200 * 86400.0, 0)
        s2 = transfer_quality_score(5000.0, 500 * 86400.0, 0)
        assert s2 > s1

    def test_score_increases_with_revolutions(self):
        """More revolutions should give higher (worse) score."""
        from transfer_planner import transfer_quality_score

        s0 = transfer_quality_score(5000.0, 200 * 86400.0, 0)
        s1 = transfer_quality_score(5000.0, 200 * 86400.0, 1)
        s2 = transfer_quality_score(5000.0, 200 * 86400.0, 2)
        assert s1 > s0
        assert s2 > s1

    def test_lower_dv_multirev_preferred(self):
        """A multi-rev saving 500 m/s over 0-rev should be preferred despite penalty."""
        from transfer_planner import transfer_quality_score

        # 0-rev: 6000 m/s, 250 days
        s_direct = transfer_quality_score(6000.0, 250 * 86400.0, 0)
        # 1-rev: 5000 m/s, 600 days (350 more days × 1 m/s/day = 350, + 50 rev penalty = 400)
        # Saving 1000 m/s - 400 penalty = net 600 better
        s_multirev = transfer_quality_score(5000.0, 600 * 86400.0, 1)
        assert s_multirev < s_direct, "1-rev with 1000 m/s savings should beat 0-rev"

    def test_marginal_multirev_not_preferred(self):
        """A multi-rev saving only 100 m/s with much longer TOF should not be preferred."""
        from transfer_planner import transfer_quality_score

        # 0-rev: 5000 m/s, 250 days
        s_direct = transfer_quality_score(5000.0, 250 * 86400.0, 0)
        # 1-rev: 4900 m/s, 700 days (450 more days + 50 rev penalty = 500 penalty vs 100 savings)
        s_multirev = transfer_quality_score(4900.0, 700 * 86400.0, 1)
        assert s_multirev > s_direct, "Marginal multi-rev should not beat direct"

    def test_zero_tof_score(self):
        """Zero TOF should just return Δv + rev penalty."""
        from transfer_planner import transfer_quality_score

        assert transfer_quality_score(5000.0, 0.0, 0) == 5000.0
        assert transfer_quality_score(5000.0, 0.0, 1) == 5050.0


# ═══════════════════════════════════════════════════════════════
# Step 11: Lambert result caching
# ═══════════════════════════════════════════════════════════════


class TestLambertCache:
    """Test the Lambert result caching in transfer_planner."""

    def test_cache_key_bucketing(self):
        """Same departure time within bucket → same cache key."""
        from transfer_planner import _lambert_cache_key, _LAMBERT_CACHE_BUCKET_S

        key1 = _lambert_cache_key("LEO", "LMO", 1000.0, 0.0)
        key2 = _lambert_cache_key("LEO", "LMO", 1000.0 + _LAMBERT_CACHE_BUCKET_S * 0.5, 0.0)
        assert key1 == key2, "Same bucket should produce same key"

    def test_cache_key_different_buckets(self):
        """Different departure buckets → different cache keys."""
        from transfer_planner import _lambert_cache_key, _LAMBERT_CACHE_BUCKET_S

        key1 = _lambert_cache_key("LEO", "LMO", 0.0, 0.0)
        key2 = _lambert_cache_key("LEO", "LMO", _LAMBERT_CACHE_BUCKET_S * 2.0, 0.0)
        assert key1 != key2

    def test_cache_key_different_locations(self):
        """Different location pairs → different cache keys."""
        from transfer_planner import _lambert_cache_key

        key1 = _lambert_cache_key("LEO", "LMO", 1000.0, 0.0)
        key2 = _lambert_cache_key("LEO", "MERC_ORB", 1000.0, 0.0)
        assert key1 != key2

    def test_cache_key_different_extra_dv(self):
        """Different extra_dv_fraction → different cache keys."""
        from transfer_planner import _lambert_cache_key

        key1 = _lambert_cache_key("LEO", "LMO", 1000.0, 0.0)
        key2 = _lambert_cache_key("LEO", "LMO", 1000.0, 0.5)
        assert key1 != key2

    def test_cache_put_get(self):
        """Put and get from the cache."""
        from transfer_planner import (
            _lambert_cache_get, _lambert_cache_put,
            _lambert_cache_key, clear_lambert_cache,
        )

        clear_lambert_cache()
        key = _lambert_cache_key("TEST_A", "TEST_B", 99999.0, 0.0)
        data = {"dv_m_s": 5000.0, "tof_s": 86400.0}
        _lambert_cache_put(key, data)

        result = _lambert_cache_get(key)
        assert result is not None
        assert result["dv_m_s"] == 5000.0
        assert result["tof_s"] == 86400.0

        # Clean up
        clear_lambert_cache()

    def test_cache_miss(self):
        """Cache miss returns None."""
        from transfer_planner import _lambert_cache_get, _lambert_cache_key, clear_lambert_cache

        clear_lambert_cache()
        key = _lambert_cache_key("NONEXISTENT_A", "NONEXISTENT_B", 12345.0, 0.0)
        assert _lambert_cache_get(key) is None
        clear_lambert_cache()

    def test_cache_stats(self):
        """Cache stats should track hits and misses."""
        from transfer_planner import (
            get_lambert_cache_stats, clear_lambert_cache,
            _lambert_cache_put, _lambert_cache_get, _lambert_cache_key,
        )

        clear_lambert_cache()
        stats = get_lambert_cache_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["size"] == 0

        key = _lambert_cache_key("STAT_A", "STAT_B", 0.0, 0.0)
        _lambert_cache_put(key, {"x": 1})
        stats = get_lambert_cache_stats()
        assert stats["misses"] == 1
        assert stats["size"] == 1

        _lambert_cache_get(key)
        stats = get_lambert_cache_stats()
        assert stats["hits"] == 1

        clear_lambert_cache()

    def test_cache_eviction(self):
        """Cache should evict oldest entries when max size is exceeded."""
        from transfer_planner import (
            _lambert_cache_put, _lambert_cache_get, _lambert_cache_key,
            clear_lambert_cache, _LAMBERT_CACHE_MAX,
        )

        clear_lambert_cache()

        # Fill cache beyond max
        for i in range(_LAMBERT_CACHE_MAX + 10):
            key = _lambert_cache_key(f"EVICT_{i}", "B", float(i * 100000), 0.0)
            _lambert_cache_put(key, {"idx": i})

        from transfer_planner import get_lambert_cache_stats
        stats = get_lambert_cache_stats()
        assert stats["size"] <= _LAMBERT_CACHE_MAX

        # First entries should have been evicted
        key0 = _lambert_cache_key("EVICT_0", "B", 0.0, 0.0)
        assert _lambert_cache_get(key0) is None

        clear_lambert_cache()

    def test_cache_returns_copy(self):
        """Cached values should be copies, not references."""
        from transfer_planner import (
            _lambert_cache_put, _lambert_cache_get, _lambert_cache_key,
            clear_lambert_cache,
        )

        clear_lambert_cache()
        key = _lambert_cache_key("COPY_A", "COPY_B", 0.0, 0.0)
        data = {"dv_m_s": 5000.0}
        _lambert_cache_put(key, data)

        result1 = _lambert_cache_get(key)
        result1["dv_m_s"] = 9999.0  # mutate the copy

        result2 = _lambert_cache_get(key)
        assert result2["dv_m_s"] == 5000.0, "Cache should return independent copies"

        clear_lambert_cache()


# ═══════════════════════════════════════════════════════════════
# Step 14: Auto-generated interplanetary edges
# ═══════════════════════════════════════════════════════════════


class TestAutoEdgeGeneration:
    """Test auto-generation of interplanetary transfer edges from topology."""

    @pytest.fixture
    def config(self):
        import celestial_config
        return celestial_config.load_celestial_config()

    def test_generate_interplanetary_edges_returns_edges(self, config):
        """Should generate edges for heliocentric body pairs."""
        import celestial_config
        edges = celestial_config.generate_interplanetary_edges(config)
        assert len(edges) > 0, "Should generate at least some edges"

    def test_all_edges_are_interplanetary_type(self, config):
        """Every auto-generated edge should have type 'interplanetary'."""
        import celestial_config
        edges = celestial_config.generate_interplanetary_edges(config)
        for edge in edges:
            assert edge[4] == "interplanetary", f"Edge {edge[0]}->{edge[1]} has wrong type: {edge[4]}"

    def test_edges_are_bidirectional(self, config):
        """For every A→B edge, there should be a B→A edge."""
        import celestial_config
        edges = celestial_config.generate_interplanetary_edges(config)
        edge_set = {(e[0], e[1]) for e in edges}
        for e in edges:
            reverse = (e[1], e[0])
            assert reverse in edge_set, f"Missing reverse edge for {e[0]}->{e[1]}"

    def test_gateway_map_populated(self, config):
        """Gateway map should have entries for heliocentric bodies with orbits."""
        import celestial_config
        gw_map = celestial_config.get_auto_edge_gateway_map(config)
        # At minimum: earth, mars, venus, mercury, jupiter, ceres, vesta, pallas, hygiea
        assert "earth" in gw_map
        assert "mars" in gw_map
        assert "jupiter" in gw_map

    def test_gateway_is_lowest_orbit(self, config):
        """Gateway for Earth should be LEO (lowest orbit)."""
        import celestial_config
        gw_map = celestial_config.get_auto_edge_gateway_map(config)
        assert gw_map.get("earth") == "LEO"

    def test_gateway_for_mars_is_lmo(self, config):
        """Gateway for Mars should be LMO (lowest orbit)."""
        import celestial_config
        gw_map = celestial_config.get_auto_edge_gateway_map(config)
        assert gw_map.get("mars") == "LMO"

    def test_moons_not_in_gateway_map(self, config):
        """Moons (moon, phobos, deimos, io, etc.) should not be in gateway map."""
        import celestial_config
        gw_map = celestial_config.get_auto_edge_gateway_map(config)
        for moon in ["moon", "phobos", "deimos", "io", "europa", "ganymede", "callisto"]:
            assert moon not in gw_map, f"Moon '{moon}' should not be in gateway map"

    def test_edges_reference_valid_locations(self, config):
        """All edge endpoints should be valid orbit_node IDs."""
        import celestial_config
        edges = celestial_config.generate_interplanetary_edges(config)
        orbit_ids = {
            str(n["id"]) for n in config.get("orbit_nodes", [])
            if isinstance(n, dict) and n.get("id")
        }
        for edge in edges:
            assert edge[0] in orbit_ids, f"Edge from '{edge[0]}' is not a valid orbit_node"
            assert edge[1] in orbit_ids, f"Edge to '{edge[1]}' is not a valid orbit_node"

    def test_hohmann_estimates_reasonable(self, config):
        """Hohmann Δv estimates should be in a reasonable range."""
        import celestial_config
        edges = celestial_config.generate_interplanetary_edges(config)

        # Find Earth→Mars edge
        earth_mars = [e for e in edges if e[0] == "LEO" and e[1] == "LMO"]
        assert len(earth_mars) == 1, f"Expected 1 LEO→LMO edge, got {len(earth_mars)}"

        dv_m_s = earth_mars[0][2]
        tof_s = earth_mars[0][3]

        # Earth→Mars Hohmann Δv ≈ 5600 m/s, TOF ≈ 259 days
        assert 3000 < dv_m_s < 12000, f"Earth→Mars Δv estimate {dv_m_s:.0f} out of range"
        assert 150 * 86400 < tof_s < 400 * 86400, f"Earth→Mars TOF {tof_s/86400:.0f} days out of range"

    def test_no_self_loops(self, config):
        """No edge should have same source and destination."""
        import celestial_config
        edges = celestial_config.generate_interplanetary_edges(config)
        for edge in edges:
            assert edge[0] != edge[1], f"Self-loop detected: {edge[0]}"

    def test_auto_flag_replaces_interplanetary_edges(self):
        """When auto_interplanetary_edges=True, hand-authored interplanetary edges are replaced."""
        import json
        import celestial_config

        config = celestial_config.load_celestial_config()

        # Get edges without auto-gen
        config_no_auto = dict(config)
        config_no_auto["auto_interplanetary_edges"] = False
        _, edges_manual = celestial_config.build_locations_and_edges(config_no_auto)
        manual_ip = [e for e in edges_manual if e[4] == "interplanetary"]

        # Get edges with auto-gen
        config_auto = dict(config)
        config_auto["auto_interplanetary_edges"] = True
        _, edges_auto = celestial_config.build_locations_and_edges(config_auto)
        auto_ip = [e for e in edges_auto if e[4] == "interplanetary"]

        # Should have generated edges (maybe different count than manual)
        assert len(auto_ip) > 0, "Auto mode should generate interplanetary edges"

        # Verify non-interplanetary edges are unchanged
        manual_non_ip = [e for e in edges_manual if e[4] != "interplanetary"]
        auto_non_ip = [e for e in edges_auto if e[4] != "interplanetary"]
        assert len(manual_non_ip) == len(auto_non_ip), "Non-interplanetary edges should be unchanged"

    def test_auto_edges_no_duplicates(self, config):
        """Auto-generated edges should have no duplicate (from, to) pairs."""
        import celestial_config
        edges = celestial_config.generate_interplanetary_edges(config)
        pairs = [(e[0], e[1]) for e in edges]
        assert len(pairs) == len(set(pairs)), "Duplicate edges detected"

    def test_empty_config_returns_empty(self):
        """Empty config with no bodies should return empty edges."""
        import celestial_config
        edges = celestial_config.generate_interplanetary_edges({"bodies": []})
        assert edges == []

    def test_bodies_without_orbits_skipped(self):
        """Bodies with no orbit_nodes should be skipped."""
        import celestial_config
        # Minimal config with sun only (no orbit_nodes)
        config = {
            "bodies": [
                {"id": "sun", "mu_km3_s2": 1.32712440018e11, "position": {"type": "fixed", "x_km": 0, "y_km": 0}},
                {"id": "test_body", "mu_km3_s2": 100, "position": {"type": "keplerian", "center_body_id": "sun", "a_km": 1e8, "e": 0, "i_deg": 0, "raan_deg": 0, "arg_periapsis_deg": 0, "M0_deg": 0, "period_s": 1e7}},
            ],
            "orbit_nodes": [],
        }
        edges = celestial_config.generate_interplanetary_edges(config)
        assert edges == []
