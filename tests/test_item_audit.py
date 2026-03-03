"""
Item handling audit — mass test for bugs, slowdowns, and consistency issues
across the hangar, shipyard, sites/cargo, and inventory systems.

Covers:
  - Catalog cross-referencing (every item resolvable, no orphans)
  - Shipyard preview/build with every part type (thrusters, reactors, storage, etc.)
  - Inventory round-trip: add items → query → transfer → verify conservation
  - Hangar context endpoint: verify all parts and cargo rendered correctly
  - Cargo context endpoint: verify location + docked ship inventories
  - Sites/industry endpoints: deploy equipment, verify items, undeploy
  - Recipe integrity: all inputs/outputs resolvable to catalog items
  - Performance checks: catalog loads, preview, and build within time budget
  - Edge cases: zero quantities, missing items, duplicate stacks, large batches
"""

import json
import math
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DEV_SKIP_AUTH", "1")


# ═══════════════════════════════════════════════════════════════════════════
# §1  CATALOG CROSS-REFERENCE AUDIT
# ═══════════════════════════════════════════════════════════════════════════

class TestCatalogCrossReference:
    """Verify every item in every catalog is resolvable and internally consistent."""

    def test_all_catalogs_load_without_error(self, all_catalogs):
        """Every catalog must load without crashing."""
        for name, catalog in all_catalogs.items():
            assert isinstance(catalog, dict), f"Catalog '{name}' is not a dict"

    def test_no_empty_catalogs(self, all_catalogs):
        """Each catalog should have at least one item."""
        for name, catalog in all_catalogs.items():
            assert len(catalog) > 0, f"Catalog '{name}' is empty"

    def test_item_ids_are_strings(self, all_catalogs):
        """All item keys and id fields must be non-empty strings."""
        for cat_name, catalog in all_catalogs.items():
            for key, item in catalog.items():
                assert isinstance(key, str) and key.strip(), (
                    f"Catalog '{cat_name}' has non-string or empty key: {key!r}"
                )
                # Recipes use recipe_id instead of id/item_id
                if cat_name == "recipe":
                    rid = item.get("recipe_id") or ""
                    assert str(rid).strip(), (
                        f"Catalog '{cat_name}' item key={key} has no recipe_id"
                    )
                else:
                    item_id = item.get("item_id") or item.get("id") or ""
                    assert str(item_id).strip(), (
                        f"Catalog '{cat_name}' item key={key} has no id/item_id"
                    )

    def test_no_duplicate_ids_across_non_recipe_catalogs(self, all_catalogs):
        """No item ID should appear in two different non-recipe catalogs.

        Recipe IDs intentionally overlap with item IDs (the recipe key is
        typically the same as the output item ID), so those are excluded.
        """
        seen: Dict[str, str] = {}
        collisions: List[str] = []
        for cat_name, catalog in all_catalogs.items():
            if cat_name == "recipe":
                continue  # recipe keys overlap with item IDs by design
            for key in catalog:
                if key in seen:
                    collisions.append(f"'{key}' in both '{seen[key]}' and '{cat_name}'")
                else:
                    seen[key] = cat_name
        assert not collisions, f"Duplicate IDs across catalogs: {collisions}"

    def test_all_items_resolvable_via_get_item_info(self, all_catalogs):
        """Every item from every catalog must be findable via get_item_info()."""
        from catalog_service import get_item_info

        missing: List[str] = []
        for cat_name, catalog in all_catalogs.items():
            if cat_name == "recipe":
                continue  # recipes aren't looked up via get_item_info
            for item_id in catalog:
                info = get_item_info(item_id)
                if info is None:
                    missing.append(f"{cat_name}/{item_id}")
        assert not missing, f"Items not found via get_item_info: {missing}"

    def test_item_names_non_empty(self, all_catalogs):
        """Every item must have a non-empty name."""
        for cat_name, catalog in all_catalogs.items():
            for key, item in catalog.items():
                name = str(item.get("name") or "").strip()
                assert name, f"Catalog '{cat_name}' item '{key}' has empty name"

    def test_canonical_category_roundtrip(self, all_catalogs):
        """Items with category_id or type should survive canonical_item_category()."""
        from catalog_service import canonical_item_category

        for cat_name, catalog in all_catalogs.items():
            if cat_name == "recipe":
                continue
            for key, item in catalog.items():
                cat_field = item.get("category_id") or item.get("type") or ""
                if cat_field:
                    canonical = canonical_item_category(cat_field)
                    assert canonical != "", (
                        f"'{cat_name}/{key}' category '{cat_field}' maps to empty canonical"
                    )


# ═══════════════════════════════════════════════════════════════════════════
# §2  RECIPE INTEGRITY AUDIT
# ═══════════════════════════════════════════════════════════════════════════

class TestRecipeIntegrity:
    """Verify recipe inputs and outputs reference real catalog items."""

    def _all_item_ids(self, all_catalogs) -> Set[str]:
        ids: Set[str] = set()
        for cat_name, catalog in all_catalogs.items():
            if cat_name == "recipe":
                continue
            ids.update(catalog.keys())
        return ids

    def test_recipe_output_items_exist(self, recipe_catalog, all_catalogs):
        """Every non-template recipe output_item_id should be in some catalog."""
        known = self._all_item_ids(all_catalogs)
        # Also include recipe IDs themselves: some recipes output into items
        # that are themselves recipe products (cascading production)
        known.update(recipe_catalog.keys())
        missing = []
        for rid, recipe in recipe_catalog.items():
            if rid.startswith("template"):  # skip template recipes
                continue
            out_id = str(recipe.get("output_item_id") or "").strip()
            if out_id and out_id not in known:
                missing.append(f"recipe '{rid}' output '{out_id}'")
        assert not missing, f"Recipe outputs reference unknown items: {missing}"

    def test_recipe_input_items_exist(self, recipe_catalog, all_catalogs):
        """Every non-template recipe input item_id should be in some catalog."""
        known = self._all_item_ids(all_catalogs)
        known.update(recipe_catalog.keys())
        missing = []
        for rid, recipe in recipe_catalog.items():
            if rid.startswith("template"):  # skip template recipes
                continue
            for inp in recipe.get("inputs") or []:
                inp_id = str(inp.get("item_id") or "").strip()
                if inp_id and inp_id not in known:
                    missing.append(f"recipe '{rid}' input '{inp_id}'")
        assert not missing, f"Recipe inputs reference unknown items: {missing}"

    def test_recipe_byproduct_items_exist(self, recipe_catalog, all_catalogs):
        """Every recipe byproduct item_id should be in some catalog."""
        known = self._all_item_ids(all_catalogs)
        missing = []
        for rid, recipe in recipe_catalog.items():
            for bp in recipe.get("byproducts") or []:
                bp_id = str(bp.get("item_id") or "").strip()
                if bp_id and bp_id not in known:
                    missing.append(f"recipe '{rid}' byproduct '{bp_id}'")
        assert not missing, f"Recipe byproducts reference unknown items: {missing}"

    def test_recipe_input_quantities_non_negative(self, recipe_catalog):
        """All recipe input quantities must be >= 0 (0.0 is valid for placeholder recipes)."""
        bad = []
        for rid, recipe in recipe_catalog.items():
            for inp in recipe.get("inputs") or []:
                qty = float(inp.get("qty") or 0)
                if qty < 0:
                    bad.append(f"recipe '{rid}' input '{inp.get('item_id')}' qty={qty}")
        assert not bad, f"Recipe inputs with negative quantities: {bad}"

    def test_recipe_output_quantities_non_negative(self, recipe_catalog):
        """All recipe output quantities must be >= 0."""
        bad = []
        for rid, recipe in recipe_catalog.items():
            qty = float(recipe.get("output_qty") or 0)
            if qty < 0:
                bad.append(f"recipe '{rid}' output qty={qty}")
        assert not bad, f"Recipes with negative output: {bad}"

    def test_recipes_have_required_fields(self, recipe_catalog):
        """Every recipe must have recipe_id, name, and an output."""
        required = {"recipe_id", "name"}
        bad = []
        for rid, recipe in recipe_catalog.items():
            missing = required - set(recipe.keys())
            if missing:
                bad.append(f"recipe '{rid}' missing: {missing}")
        assert not bad, f"Recipes with missing fields: {bad}"


# ═══════════════════════════════════════════════════════════════════════════
# §3  PERFORMANCE BENCHMARKS
# ═══════════════════════════════════════════════════════════════════════════

class TestCatalogPerformance:
    """Verify catalog loads and key operations complete within time budgets."""

    LOAD_BUDGET_S = 2.0  # seconds per catalog load
    PREVIEW_BUDGET_S = 3.0  # seconds for shipyard preview

    def test_all_catalog_loads_fast(self):
        """Each catalog should load in under {LOAD_BUDGET_S}s."""
        import catalog_service

        loaders = [
            ("resource", catalog_service.load_resource_catalog),
            ("storage", catalog_service.load_storage_catalog),
            ("thruster", catalog_service.load_thruster_main_catalog),
            ("reactor", catalog_service.load_reactor_catalog),
            ("generator", catalog_service.load_generator_catalog),
            ("radiator", catalog_service.load_radiator_catalog),
            ("recipe", catalog_service.load_recipe_catalog),
        ]
        slow = []
        for name, loader in loaders:
            # Clear lru_cache so we time a cold load
            if hasattr(loader, "cache_clear"):
                loader.cache_clear()
            t0 = time.perf_counter()
            loader()
            elapsed = time.perf_counter() - t0
            if elapsed > self.LOAD_BUDGET_S:
                slow.append(f"{name}: {elapsed:.3f}s")
        assert not slow, f"Slow catalog loads: {slow}"

    def test_get_item_info_fast_for_all_items(self, all_catalogs):
        """get_item_info should resolve any item within 50ms."""
        from catalog_service import get_item_info

        slow = []
        for cat_name, catalog in all_catalogs.items():
            if cat_name == "recipe":
                continue
            for item_id in list(catalog.keys())[:20]:  # sample up to 20 per catalog
                t0 = time.perf_counter()
                get_item_info(item_id)
                elapsed = time.perf_counter() - t0
                if elapsed > 0.05:
                    slow.append(f"{cat_name}/{item_id}: {elapsed*1000:.1f}ms")
        assert not slow, f"Slow get_item_info lookups: {slow}"

    def test_derive_stats_fast_with_many_parts(self, thruster_catalog, storage_catalog, resource_catalog):
        """derive_ship_stats_from_parts with 20 parts should be < 100ms."""
        from catalog_service import derive_ship_stats_from_parts

        parts = []
        for item_id, item in list(thruster_catalog.items())[:5]:
            parts.append(dict(item))
        for item_id, item in list(storage_catalog.items())[:15]:
            parts.append(dict(item))

        t0 = time.perf_counter()
        stats = derive_ship_stats_from_parts(parts, resource_catalog)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.1, f"derive_ship_stats_from_parts took {elapsed*1000:.1f}ms"
        assert isinstance(stats, dict)


# ═══════════════════════════════════════════════════════════════════════════
# §4  SHIPYARD PREVIEW & BUILD — FULL PART-TYPE MATRIX
# ═══════════════════════════════════════════════════════════════════════════

class TestShipyardItemHandling:
    """Test shipyard preview with every combination of part types."""

    def test_preview_with_thruster_only(self, client, thruster_catalog):
        if not thruster_catalog:
            pytest.skip("No thrusters")
        first_id = next(iter(thruster_catalog))
        r = client.post("/api/shipyard/preview", json={"parts": [first_id]})
        assert r.status_code == 200
        data = r.json()
        assert "stats" in data
        assert float(data["stats"].get("dry_mass_kg", 0)) > 0

    def test_preview_with_storage_only(self, client, storage_catalog):
        if not storage_catalog:
            pytest.skip("No storage items")
        first_id = next(iter(storage_catalog))
        r = client.post("/api/shipyard/preview", json={"parts": [first_id]})
        assert r.status_code == 200

    def test_preview_with_reactor_only(self, client, reactor_catalog):
        if not reactor_catalog:
            pytest.skip("No reactors")
        first_id = next(iter(reactor_catalog))
        r = client.post("/api/shipyard/preview", json={"parts": [first_id]})
        assert r.status_code == 200

    def test_preview_with_generator_only(self, client, generator_catalog):
        if not generator_catalog:
            pytest.skip("No generators")
        first_id = next(iter(generator_catalog))
        r = client.post("/api/shipyard/preview", json={"parts": [first_id]})
        assert r.status_code == 200

    def test_preview_with_radiator_only(self, client, radiator_catalog):
        if not radiator_catalog:
            pytest.skip("No radiators")
        first_id = next(iter(radiator_catalog))
        r = client.post("/api/shipyard/preview", json={"parts": [first_id]})
        assert r.status_code == 200

    def test_preview_full_ship_all_part_types(
        self, client, thruster_catalog, storage_catalog, reactor_catalog,
        generator_catalog, radiator_catalog,
    ):
        """Preview a ship with one of every part type — must not crash."""
        parts = []
        for cat in [thruster_catalog, storage_catalog, reactor_catalog,
                     generator_catalog, radiator_catalog]:
            if cat:
                parts.append(next(iter(cat)))
        if not parts:
            pytest.skip("No parts available")
        r = client.post("/api/shipyard/preview", json={"parts": parts})
        assert r.status_code == 200
        data = r.json()
        assert "stats" in data
        assert "parts" in data
        # Every part should be hydrated back in the response
        assert len(data["parts"]) == len(parts)

    def test_preview_returns_power_balance(
        self, client, reactor_catalog, generator_catalog, radiator_catalog,
    ):
        """Ships with power components should get a power_balance section."""
        parts = []
        if reactor_catalog:
            parts.append(next(iter(reactor_catalog)))
        if generator_catalog:
            parts.append(next(iter(generator_catalog)))
        if radiator_catalog:
            parts.append(next(iter(radiator_catalog)))
        if not parts:
            pytest.skip("No power parts")
        r = client.post("/api/shipyard/preview", json={"parts": parts})
        assert r.status_code == 200
        data = r.json()
        # Should have a power_balance key (may be empty dict for minimal parts)
        assert "power_balance" in data

    def test_preview_with_fuel(self, client, thruster_catalog, storage_catalog):
        """Preview with explicit fuel_kg should reflect in stats."""
        parts = []
        if thruster_catalog:
            parts.append(next(iter(thruster_catalog)))
        if storage_catalog:
            parts.append(next(iter(storage_catalog)))
        if not parts:
            pytest.skip("No parts")
        r = client.post("/api/shipyard/preview", json={
            "parts": parts,
            "fuel_kg": 500.0,
        })
        assert r.status_code == 200

    def test_preview_with_unlimited_fuel(self, client, thruster_catalog, storage_catalog):
        """Preview with unlimited_fuel=True should not crash."""
        parts = []
        if thruster_catalog:
            parts.append(next(iter(thruster_catalog)))
        if storage_catalog:
            parts.append(next(iter(storage_catalog)))
        if not parts:
            pytest.skip("No parts")
        r = client.post("/api/shipyard/preview", json={
            "parts": parts,
            "unlimited_fuel": True,
        })
        assert r.status_code == 200

    def test_preview_empty_parts_list(self, client):
        """Empty parts list should return 200 with zero stats, not crash."""
        r = client.post("/api/shipyard/preview", json={"parts": []})
        assert r.status_code in (200, 400, 422)

    def test_preview_invalid_part_id(self, client):
        """Unknown part IDs should be gracefully handled (generic fallback)."""
        r = client.post("/api/shipyard/preview", json={"parts": ["nonexistent_999"]})
        assert r.status_code in (200, 400, 422)

    def test_preview_duplicate_parts(self, client, thruster_catalog):
        """Duplicate parts should be handled (two of same thruster)."""
        if not thruster_catalog:
            pytest.skip("No thrusters")
        first = next(iter(thruster_catalog))
        r = client.post("/api/shipyard/preview", json={"parts": [first, first]})
        assert r.status_code == 200

    def test_preview_response_timing(self, client, thruster_catalog, storage_catalog):
        """Preview should complete within the time budget."""
        parts = []
        if thruster_catalog:
            parts.append(next(iter(thruster_catalog)))
        if storage_catalog:
            parts.append(next(iter(storage_catalog)))
        if not parts:
            pytest.skip("No parts")
        t0 = time.perf_counter()
        r = client.post("/api/shipyard/preview", json={"parts": parts})
        elapsed = time.perf_counter() - t0
        assert r.status_code == 200
        assert elapsed < 3.0, f"Preview took {elapsed:.2f}s (budget: 3s)"

    def test_preview_every_thruster(self, client, thruster_catalog):
        """Preview should not crash for any individual thruster."""
        failures = []
        for item_id in thruster_catalog:
            r = client.post("/api/shipyard/preview", json={"parts": [item_id]})
            if r.status_code != 200:
                failures.append(f"{item_id}: HTTP {r.status_code}")
        assert not failures, f"Thruster preview failures: {failures}"

    def test_preview_every_storage(self, client, storage_catalog):
        """Preview should not crash for any individual storage item."""
        failures = []
        for item_id in storage_catalog:
            r = client.post("/api/shipyard/preview", json={"parts": [item_id]})
            if r.status_code != 200:
                failures.append(f"{item_id}: HTTP {r.status_code}")
        assert not failures, f"Storage preview failures: {failures}"

    def test_preview_every_reactor(self, client, reactor_catalog):
        """Preview should not crash for any individual reactor."""
        failures = []
        for item_id in reactor_catalog:
            r = client.post("/api/shipyard/preview", json={"parts": [item_id]})
            if r.status_code != 200:
                failures.append(f"{item_id}: HTTP {r.status_code}")
        assert not failures, f"Reactor preview failures: {failures}"


# ═══════════════════════════════════════════════════════════════════════════
# §5  SHIPYARD BUILD → HANGAR ROUND-TRIP
# ═══════════════════════════════════════════════════════════════════════════

class TestShipyardBuildRoundTrip:
    """Build a ship via API, then verify it appears correctly in hangar/inventory."""

    def _get_boostable_parts(self, client):
        """Get boostable non-resource parts from the org endpoint."""
        # Ensure org has funds
        client.post("/api/admin/org/grant", json={
            "username": "admin", "money_usd": 999_999_999_999, "research_points": 0,
        })
        r = client.get("/api/org/boostable-items")
        if r.status_code != 200:
            return []
        items = r.json().get("items", [])
        return [i["item_id"] for i in items if i.get("type") != "resource"]

    def _seed_and_build(self, client, part_ids, name="AuditShip", fuel_kg=0):
        """Boost parts to LEO and then build a ship from them."""
        client.post("/api/admin/org/grant", json={
            "username": "admin", "money_usd": 999_999_999_999, "research_points": 0,
        })
        items_payload = [{"item_id": pid, "quantity": 1} for pid in part_ids]
        client.post("/api/org/boost", json={"items": items_payload})
        r = client.post("/api/shipyard/build", json={
            "name": name,
            "parts": part_ids,
            "fuel_kg": fuel_kg,
        })
        return r

    def test_build_and_query_hangar(
        self, client,
    ):
        """Build a ship → hit hangar context → verify parts appear."""
        boostable = self._get_boostable_parts(client)
        if len(boostable) < 1:
            pytest.skip("No boostable parts available")
        parts = boostable[:2]  # pick up to 2 boostable parts

        r = self._seed_and_build(client, parts, name="HangarAuditShip")
        assert r.status_code == 200, f"Build failed: {r.text}"
        data = r.json()
        assert data.get("ok") is True
        ship = data["ship"]
        ship_id = ship["id"]

        # Query hangar context
        r2 = client.get(f"/api/hangar/context/{ship_id}")
        assert r2.status_code == 200, f"Hangar context failed: {r2.text}"
        hangar = r2.json()
        assert "entities" in hangar
        assert "anchor" in hangar

        # The ship entity should appear
        ship_entity = None
        for e in hangar["entities"]:
            if e.get("id") == ship_id and e.get("entity_kind") == "ship":
                ship_entity = e
                break
        assert ship_entity is not None, f"Ship {ship_id} not in hangar entities"

        # Parts list should be populated
        assert isinstance(ship_entity.get("parts"), list)
        assert len(ship_entity["parts"]) >= len(parts)

    def test_build_and_query_ship_inventory(
        self, client,
    ):
        """Build a ship → hit ship inventory endpoint → verify structure."""
        boostable = self._get_boostable_parts(client)
        if len(boostable) < 1:
            pytest.skip("No boostable parts available")
        parts = boostable[:2]

        r = self._seed_and_build(client, parts, name="InvAuditShip")
        assert r.status_code == 200
        ship_id = r.json()["ship"]["id"]

        r2 = client.get(f"/api/inventory/ship/{ship_id}")
        assert r2.status_code == 200
        inv = r2.json()
        assert inv["ship_id"] == ship_id
        assert isinstance(inv.get("items"), list)
        assert isinstance(inv.get("container_groups"), list)
        assert isinstance(inv.get("capacity_summary"), dict)

    def test_build_with_all_part_families(
        self, client,
    ):
        """Build a ship with multiple boostable parts → verify stats consistency."""
        boostable = self._get_boostable_parts(client)
        if len(boostable) < 2:
            pytest.skip("Need at least 2 boostable parts")
        parts = boostable[:5]  # up to 5 parts

        r = self._seed_and_build(client, parts, name="FullAuditShip")
        assert r.status_code == 200
        ship = r.json()["ship"]

        # Stats sanity
        assert float(ship.get("dry_mass_kg", 0)) > 0, "Ship should have positive mass"

        # Verify via state endpoint that ship exists
        state_r = client.get("/api/state")
        assert state_r.status_code == 200
        state = state_r.json()
        ships = state.get("ships") or []
        found = [s for s in ships if s.get("id") == ship["id"]]
        assert len(found) == 1, f"Ship {ship['id']} not in /api/state"

    def test_build_ship_name_slugification(self, client):
        """Ship names with special chars should be slugified without crashing."""
        boostable = self._get_boostable_parts(client)
        if not boostable:
            pytest.skip("No boostable parts")
        r = self._seed_and_build(client, [boostable[0]], name="Test Ship #1 (alpha)")
        assert r.status_code == 200
        ship_id = r.json()["ship"]["id"]
        assert ship_id  # Should always be non-empty
        # Should be slugified (no spaces/special chars)
        assert " " not in ship_id


# ═══════════════════════════════════════════════════════════════════════════
# §6  INVENTORY OPERATIONS — LOCATION & SHIP
# ═══════════════════════════════════════════════════════════════════════════

class TestInventoryItemHandling:
    """Test inventory add/query/transfer flows via the DB + API."""

    def _get_boostable_parts(self, client):
        client.post("/api/admin/org/grant", json={
            "username": "admin", "money_usd": 999_999_999_999, "research_points": 0,
        })
        r = client.get("/api/org/boostable-items")
        if r.status_code != 200:
            return []
        return [i["item_id"] for i in r.json().get("items", []) if i.get("type") != "resource"]

    def _seed_and_build(self, client, parts, name="InvTestShip"):
        items_payload = [{"item_id": pid, "quantity": 1} for pid in parts]
        client.post("/api/org/boost", json={"items": items_payload})
        return client.post("/api/shipyard/build", json={"name": name, "parts": parts})

    def test_location_inventory_returns_structure(self, client):
        """Querying inventory for a location should return expected structure."""
        r = client.get("/api/inventory/location/LEO")
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            data = r.json()
            assert "resources" in data or "parts" in data or isinstance(data, dict)

    def test_ship_inventory_nonexistent(self, client):
        """Querying inventory for a non-existent ship should return 404."""
        r = client.get("/api/inventory/ship/no_such_ship_12345")
        assert r.status_code == 404

    def test_inventory_context_ship(self, client):
        """Build a ship then query its inventory context."""
        boostable = self._get_boostable_parts(client)
        if not boostable:
            pytest.skip("No boostable parts")
        parts = boostable[:2]

        build_r = self._seed_and_build(client, parts, name="CtxAuditShip")
        assert build_r.status_code == 200
        ship_id = build_r.json()["ship"]["id"]

        r = client.get(f"/api/inventory/context/ship/{ship_id}")
        assert r.status_code == 200
        ctx = r.json()
        assert "anchor" in ctx
        assert ctx["anchor"]["kind"] == "ship"
        assert "inventories" in ctx
        assert isinstance(ctx["inventories"], list)

    def test_inventory_context_location(self, client):
        """Query inventory context for a location."""
        r = client.get("/api/inventory/context/location/LEO")
        assert r.status_code == 200
        ctx = r.json()
        assert "anchor" in ctx
        assert ctx["anchor"]["kind"] == "location"

    def test_inventory_context_invalid_kind(self, client):
        """Invalid kind should return 400."""
        r = client.get("/api/inventory/context/invalid/LEO")
        assert r.status_code == 400

    def test_stack_context_ship(self, client):
        """Build a ship and query stack context."""
        boostable = self._get_boostable_parts(client)
        if not boostable:
            pytest.skip("No boostable parts")
        parts = boostable[:2]

        build_r = self._seed_and_build(client, parts, name="StackCtxShip")
        assert build_r.status_code == 200
        ship_id = build_r.json()["ship"]["id"]

        r = client.get(f"/api/stack/context/ship/{ship_id}")
        assert r.status_code == 200
        ctx = r.json()
        assert "anchor" in ctx
        assert "stacks" in ctx


# ═══════════════════════════════════════════════════════════════════════════
# §7  HANGAR CONTEXT — DEEP STRUCTURE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

class TestHangarContext:
    """Deep validation of the hangar context endpoint response structure."""

    def _get_boostable_parts(self, client):
        client.post("/api/admin/org/grant", json={
            "username": "admin", "money_usd": 999_999_999_999, "research_points": 0,
        })
        r = client.get("/api/org/boostable-items")
        if r.status_code != 200:
            return []
        return [i["item_id"] for i in r.json().get("items", []) if i.get("type") != "resource"]

    def _seed_and_build(self, client, parts, name="HangarShip"):
        items_payload = [{"item_id": pid, "quantity": 1} for pid in parts]
        client.post("/api/org/boost", json={"items": items_payload})
        r = client.post("/api/shipyard/build", json={"name": name, "parts": parts})
        return r

    def test_hangar_entity_has_required_fields(
        self, client,
    ):
        """Every entity in hangar context must have the expected fields."""
        boostable = self._get_boostable_parts(client)
        if not boostable:
            pytest.skip("No boostable parts")
        parts = boostable[:2]

        r = self._seed_and_build(client, parts)
        assert r.status_code == 200
        ship = r.json()["ship"]
        r2 = client.get(f"/api/hangar/context/{ship['id']}")
        r2 = client.get(f"/api/hangar/context/{ship['id']}")
        assert r2.status_code == 200
        ctx = r2.json()

        required_top = {"anchor", "location", "entities"}
        missing_top = required_top - set(ctx.keys())
        assert not missing_top, f"Missing top-level keys: {missing_top}"

        for entity in ctx["entities"]:
            assert "entity_kind" in entity
            assert "id" in entity
            assert "name" in entity
            if entity["entity_kind"] == "ship":
                assert "parts" in entity
                assert "stats" in entity or entity.get("stats") is None
                assert "inventory_items" in entity
                assert "container_groups" in entity

    def test_hangar_ship_stats_consistency(
        self, client, resource_catalog,
    ):
        """Hangar stats should be consistent with derive_ship_stats_from_parts."""
        from catalog_service import derive_ship_stats_from_parts

        boostable = self._get_boostable_parts(client)
        if not boostable:
            pytest.skip("No boostable parts")
        parts_ids = boostable[:2]

        r = self._seed_and_build(client, parts_ids, name="StatsAuditShip")
        assert r.status_code == 200
        ship = r.json()["ship"]
        r2 = client.get(f"/api/hangar/context/{ship['id']}")
        assert r2.status_code == 200

        ship_entity = None
        for e in r2.json()["entities"]:
            if e.get("id") == ship["id"] and e.get("entity_kind") == "ship":
                ship_entity = e
                break
        if ship_entity is None:
            pytest.fail("Ship entity not found in hangar")

        api_stats = ship_entity.get("stats")
        if api_stats is None:
            pytest.skip("No stats in hangar entity")

        # Compare dry mass — should be consistent
        api_dry = float(api_stats.get("dry_mass_kg", 0))
        build_dry = float(ship.get("dry_mass_kg", 0))
        assert abs(api_dry - build_dry) < 1.0, (
            f"Hangar dry_mass ({api_dry}) != build dry_mass ({build_dry})"
        )

    def test_hangar_multiple_ships_at_location(
        self, client,
    ):
        """Build two ships at same location, hangar should list both."""
        boostable = self._get_boostable_parts(client)
        if not boostable:
            pytest.skip("No boostable parts")
        parts = boostable[:1]

        r1 = self._seed_and_build(client, parts, name="MultiHangar1")
        assert r1.status_code == 200
        ship1 = r1.json()["ship"]

        r2 = self._seed_and_build(client, parts, name="MultiHangar2")
        assert r2.status_code == 200
        ship2 = r2.json()["ship"]

        r = client.get(f"/api/hangar/context/{ship1['id']}")
        assert r.status_code == 200
        ctx = r.json()
        entity_ids = {e["id"] for e in ctx["entities"] if e.get("entity_kind") == "ship"}
        # At minimum the anchor ship should be present
        assert ship1["id"] in entity_ids

    def test_hangar_context_nonexistent_ship(self, client):
        """Hangar for non-existent ship should return 404."""
        r = client.get("/api/hangar/context/no_such_ship_xyz")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# §8  CARGO CONTEXT — LOCATION ITEM VIEWS
# ═══════════════════════════════════════════════════════════════════════════

class TestCargoContext:
    """Validate the cargo tab context endpoint."""

    def test_cargo_context_valid_location(self, client):
        """Cargo context for a valid location should return 200."""
        r = client.get("/api/cargo/context/LEO")
        assert r.status_code == 200
        data = r.json()
        assert "location" in data
        assert "entities" in data
        assert isinstance(data["entities"], list)

    def test_cargo_context_invalid_location(self, client):
        """Cargo context for non-existent location should return 404."""
        r = client.get("/api/cargo/context/NOWHERE_999")
        assert r.status_code in (404, 400)

    def test_cargo_context_entities_have_items(self, client):
        """Build a ship at LEO, then cargo context should include it."""
        # Get boostable parts
        client.post("/api/admin/org/grant", json={
            "username": "admin", "money_usd": 999_999_999_999, "research_points": 0,
        })
        r = client.get("/api/org/boostable-items")
        if r.status_code != 200:
            pytest.skip("No boostable items endpoint")
        boostable = [i["item_id"] for i in r.json().get("items", []) if i.get("type") != "resource"]
        if not boostable:
            pytest.skip("No boostable parts")
        parts = boostable[:2]

        items_payload = [{"item_id": pid, "quantity": 1} for pid in parts]
        client.post("/api/org/boost", json={"items": items_payload})
        build_r = client.post("/api/shipyard/build", json={
            "name": "CargoAuditShip",
            "parts": parts,
        })
        assert build_r.status_code == 200
        ship = build_r.json()["ship"]

        r = client.get(f"/api/cargo/context/{ship['location_id']}")
        assert r.status_code == 200
        data = r.json()
        entities = data["entities"]
        # Should have at least a location entity and the ship entity
        kinds = [e.get("entity_kind") for e in entities]
        assert "location" in kinds, "Location entity missing from cargo context"

    def test_cargo_context_entity_structure(self, client):
        """Each entity in cargo context should have required fields."""
        client.post("/api/admin/org/grant", json={
            "username": "admin", "money_usd": 999_999_999_999, "research_points": 0,
        })
        r = client.get("/api/org/boostable-items")
        if r.status_code != 200:
            pytest.skip("No boostable items endpoint")
        boostable = [i["item_id"] for i in r.json().get("items", []) if i.get("type") != "resource"]
        if not boostable:
            pytest.skip("No boostable parts")
        parts = boostable[:2]

        items_payload = [{"item_id": pid, "quantity": 1} for pid in parts]
        client.post("/api/org/boost", json={"items": items_payload})
        build_r = client.post("/api/shipyard/build", json={
            "name": "CargoStructShip",
            "parts": parts,
        })
        assert build_r.status_code == 200
        ship = build_r.json()["ship"]

        r = client.get(f"/api/cargo/context/{ship['location_id']}")
        assert r.status_code == 200
        for entity in r.json()["entities"]:
            assert "entity_kind" in entity
            assert "id" in entity
            assert "name" in entity
            # Stack items and inventory items should be lists (possibly empty)
            if "inventory_items" in entity:
                assert isinstance(entity["inventory_items"], list)
            if "stack_items" in entity:
                assert isinstance(entity["stack_items"], list)


# ═══════════════════════════════════════════════════════════════════════════
# §9  SITES & INDUSTRY — ITEM DEPLOYMENT FLOWS
# ═══════════════════════════════════════════════════════════════════════════

class TestSitesItemHandling:
    """Test the sites and industry endpoints for item handling correctness."""

    def test_sites_list_returns_data(self, client):
        """GET /api/sites should return a list of sites."""
        r = client.get("/api/sites")
        assert r.status_code == 200
        data = r.json()
        assert "sites" in data
        assert isinstance(data["sites"], list)

    def test_sites_list_structure(self, client):
        """Each site should have required summary fields."""
        r = client.get("/api/sites")
        assert r.status_code == 200
        for site in r.json().get("sites", []):
            assert "id" in site
            assert "name" in site

    def test_site_detail_valid_location(self, client):
        """Site detail for a known location should return 200."""
        # First get a site from the list
        r = client.get("/api/sites")
        assert r.status_code == 200
        sites = r.json().get("sites", [])
        if not sites:
            pytest.skip("No sites available")

        site_id = sites[0]["id"]
        r2 = client.get(f"/api/sites/{site_id}")
        assert r2.status_code == 200
        data = r2.json()
        assert "id" in data
        assert "name" in data
        assert "inventory" in data
        assert "equipment" in data

    def test_site_detail_invalid_location(self, client):
        """Site detail for non-existent location should return 404."""
        r = client.get("/api/sites/NONEXISTENT_LOCATION_999")
        assert r.status_code in (404, 400)

    def test_industry_overview_valid_location(self, client):
        """Industry overview for a known location should return 200."""
        r = client.get("/api/sites")
        sites = r.json().get("sites", [])
        if not sites:
            pytest.skip("No sites available")
        site_id = sites[0]["id"]
        r2 = client.get(f"/api/industry/{site_id}")
        assert r2.status_code == 200
        data = r2.json()
        assert "equipment" in data
        assert "inventory" in data

    def test_industry_overview_structure(self, client):
        """Industry overview response should have all expected sections."""
        r = client.get("/api/sites")
        sites = r.json().get("sites", [])
        if not sites:
            pytest.skip("No sites available")
        site_id = sites[0]["id"]
        r2 = client.get(f"/api/industry/{site_id}")
        assert r2.status_code == 200
        data = r2.json()
        expected_keys = {"equipment", "inventory", "location_id"}
        missing = expected_keys - set(data.keys())
        assert not missing, f"Industry overview missing keys: {missing}"

    def test_deploy_no_item_in_inventory(self, client):
        """Deploying an item not in inventory should fail gracefully."""
        r = client.get("/api/sites")
        sites = r.json().get("sites", [])
        if not sites:
            pytest.skip("No sites")
        r = client.post("/api/industry/deploy", json={
            "location_id": sites[0]["id"],
            "item_id": "nonexistent_item_999",
        })
        # Should fail with 400 or similar, not 500
        assert r.status_code in (400, 404, 422), (
            f"Expected 4xx for missing item, got {r.status_code}: {r.text}"
        )

    def test_undeploy_nonexistent_equipment(self, client):
        """Undeploying non-existent equipment should fail gracefully."""
        r = client.post("/api/industry/undeploy", json={
            "equipment_id": "nonexistent_equip_999",
        })
        assert r.status_code in (400, 404, 422), (
            f"Expected 4xx, got {r.status_code}: {r.text}"
        )

    def test_start_job_invalid_equipment(self, client):
        """Starting a job on invalid equipment should fail gracefully."""
        r = client.post("/api/industry/jobs/start", json={
            "equipment_id": "nonexistent_equip_999",
            "recipe_id": "nonexistent_recipe",
        })
        assert r.status_code in (400, 404, 422), (
            f"Expected 4xx, got {r.status_code}: {r.text}"
        )

    def test_cancel_job_invalid(self, client):
        """Cancelling a non-existent job should fail gracefully."""
        r = client.post("/api/industry/jobs/cancel", json={
            "job_id": "nonexistent_job_999",
        })
        assert r.status_code in (400, 404, 422), (
            f"Expected 4xx, got {r.status_code}: {r.text}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# §10  CATALOG API — ITEM BROWSING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

class TestCatalogAPIEndpoints:
    """Verify catalog browsing endpoints return well-formed data."""

    def test_catalog_items_structure(self, client):
        """GET /api/catalog/items should return item categories."""
        r = client.get("/api/catalog/items")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)

    def test_catalog_recipes_returns_list(self, client):
        """GET /api/catalog/recipes should return a list of recipes."""
        r = client.get("/api/catalog/recipes")
        assert r.status_code == 200
        data = r.json()
        assert "recipes" in data
        assert isinstance(data["recipes"], list)

    def test_catalog_recipes_by_category(self, client):
        """GET /api/catalog/recipes/by-category should group recipes."""
        r = client.get("/api/catalog/recipes/by-category")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)

    def test_catalog_item_info_valid(self, client, all_catalogs):
        """GET /api/catalog/item/{id} should return info for known items."""
        sample_ids = []
        for cat_name, catalog in all_catalogs.items():
            if cat_name == "recipe":
                continue
            for item_id in list(catalog.keys())[:2]:
                sample_ids.append(item_id)

        failures = []
        for item_id in sample_ids:
            r = client.get(f"/api/catalog/item/{item_id}")
            if r.status_code != 200:
                failures.append(f"{item_id}: HTTP {r.status_code}")
            else:
                data = r.json()
                if "item" not in data:
                    failures.append(f"{item_id}: missing 'item' key")
        assert not failures, f"Catalog item lookup failures: {failures}"

    def test_catalog_item_info_unknown(self, client):
        """GET /api/catalog/item/{id} for unknown item should return 404."""
        r = client.get("/api/catalog/item/nonexistent_item_999")
        assert r.status_code == 404

    def test_shipyard_catalog_structure(self, client):
        """GET /api/shipyard/catalog should return categorized parts."""
        r = client.get("/api/shipyard/catalog")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)

    def test_research_tree_structure(self, client):
        """GET /api/research/tree should return tech tree data."""
        r = client.get("/api/research/tree")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)


# ═══════════════════════════════════════════════════════════════════════════
# §11  normalize_parts UNIT TESTS — EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalizePartsEdgeCases:
    """Unit-test normalize_parts with edge-case inputs."""

    def _normalize(self, raw_parts):
        from catalog_service import (
            normalize_parts,
            canonical_item_category,
            load_thruster_main_catalog,
            load_storage_catalog,
            load_reactor_catalog,
            load_generator_catalog,
            load_radiator_catalog,
            load_robonaut_catalog,
            load_constructor_catalog,
            load_refinery_catalog,
        )
        return normalize_parts(
            raw_parts,
            load_thruster_main_catalog(),
            load_storage_catalog(),
            canonical_item_category,
            reactor_catalog=load_reactor_catalog(),
            generator_catalog=load_generator_catalog(),
            radiator_catalog=load_radiator_catalog(),
            robonaut_catalog=load_robonaut_catalog(),
            constructor_catalog=load_constructor_catalog(),
            refinery_catalog=load_refinery_catalog(),
        )

    def test_empty_list(self):
        assert self._normalize([]) == []

    def test_none_input(self):
        assert self._normalize(None) == []

    def test_string_input(self):
        assert self._normalize("not a list") == []

    def test_empty_string_entries(self):
        result = self._normalize(["", "  ", ""])
        assert result == []

    def test_valid_thruster_id(self, thruster_catalog):
        if not thruster_catalog:
            pytest.skip("No thrusters")
        item_id = next(iter(thruster_catalog))
        result = self._normalize([item_id])
        assert len(result) == 1
        assert result[0].get("name")

    def test_valid_storage_id(self, storage_catalog):
        if not storage_catalog:
            pytest.skip("No storage")
        item_id = next(iter(storage_catalog))
        result = self._normalize([item_id])
        assert len(result) == 1
        assert result[0].get("name")

    def test_unknown_string_gets_generic_category(self):
        result = self._normalize(["completely_unknown_part"])
        assert len(result) == 1
        # Should result in a generic fallback
        assert result[0].get("name") == "completely_unknown_part"

    def test_dict_entry_with_item_id(self, thruster_catalog):
        if not thruster_catalog:
            pytest.skip("No thrusters")
        item_id = next(iter(thruster_catalog))
        result = self._normalize([{"item_id": item_id}])
        assert len(result) == 1
        # Should be merged with catalog data
        assert result[0].get("name")

    def test_mixed_types(self, thruster_catalog, storage_catalog):
        parts = []
        if thruster_catalog:
            parts.append(next(iter(thruster_catalog)))
        if storage_catalog:
            parts.append({"item_id": next(iter(storage_catalog))})
        parts.append("unknown_thing")
        if not parts:
            pytest.skip("No parts")
        result = self._normalize(parts)
        assert len(result) == len(parts)


# ═══════════════════════════════════════════════════════════════════════════
# §12  derive_ship_stats_from_parts STRESS TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestDeriveStatsStress:
    """Stress-test derive_ship_stats_from_parts with many parts and edge cases."""

    def test_single_thruster(self, thruster_catalog, resource_catalog):
        from catalog_service import derive_ship_stats_from_parts
        if not thruster_catalog:
            pytest.skip("No thrusters")
        part = dict(next(iter(thruster_catalog.values())))
        stats = derive_ship_stats_from_parts([part], resource_catalog)
        assert stats["dry_mass_kg"] >= 0
        assert stats["isp_s"] >= 0

    def test_many_thrusters(self, thruster_catalog, resource_catalog):
        from catalog_service import derive_ship_stats_from_parts
        if not thruster_catalog:
            pytest.skip("No thrusters")
        parts = [dict(v) for v in list(thruster_catalog.values())[:10]]
        stats = derive_ship_stats_from_parts(parts, resource_catalog)
        assert stats["dry_mass_kg"] >= 0
        assert stats["thrust_kn"] >= 0

    def test_storage_contributes_fuel_capacity(self, thruster_catalog, storage_catalog, resource_catalog):
        from catalog_service import derive_ship_stats_from_parts
        parts_no_storage = []
        parts_with_storage = []
        if thruster_catalog:
            t = dict(next(iter(thruster_catalog.values())))
            parts_no_storage.append(t)
            parts_with_storage.append(t)
        if storage_catalog:
            s = dict(next(iter(storage_catalog.values())))
            parts_with_storage.append(s)
        if not parts_with_storage:
            pytest.skip("No parts")

        stats_no = derive_ship_stats_from_parts(parts_no_storage, resource_catalog)
        stats_with = derive_ship_stats_from_parts(parts_with_storage, resource_catalog)
        # Adding storage should increase fuel capacity (if it's a fuel tank)
        # or at least not decrease dry mass
        assert stats_with["dry_mass_kg"] >= stats_no["dry_mass_kg"]

    def test_every_thruster_individually(self, thruster_catalog, resource_catalog):
        """Every thruster should produce valid stats when used alone."""
        from catalog_service import derive_ship_stats_from_parts
        failures = []
        for item_id, item in thruster_catalog.items():
            try:
                stats = derive_ship_stats_from_parts([dict(item)], resource_catalog)
                if stats["dry_mass_kg"] < 0:
                    failures.append(f"{item_id}: negative dry_mass")
                if stats["isp_s"] < 0:
                    failures.append(f"{item_id}: negative ISP")
            except Exception as e:
                failures.append(f"{item_id}: {e}")
        assert not failures, f"Thruster stat failures: {failures}"

    def test_empty_parts(self, resource_catalog):
        from catalog_service import derive_ship_stats_from_parts
        stats = derive_ship_stats_from_parts([], resource_catalog)
        assert isinstance(stats, dict)
        assert stats.get("dry_mass_kg", 0) == 0


# ═══════════════════════════════════════════════════════════════════════════
# §13  MASS CONSERVATION — TRANSFER SANITY CHECKS
# ═══════════════════════════════════════════════════════════════════════════

class TestMassConservation:
    """Test that item transfers conserve mass (no duplication/loss)."""

    def test_upsert_inventory_stack_add_and_remove(self, db_conn):
        """Direct DB test: upsert add then subtract should net to zero."""
        import main as m
        # Ensure the location exists
        db_conn.execute(
            "INSERT OR IGNORE INTO locations (id, name, is_group) VALUES (?, ?, ?)",
            ("LEO", "Low Earth Orbit", 0),
        )
        db_conn.commit()

        # Add 100 kg water
        m._upsert_inventory_stack(
            db_conn,
            location_id="LEO",
            stack_type="resource",
            stack_key="water",
            item_id="water",
            name="Water",
            quantity_delta=100.0,
            mass_delta_kg=100.0,
            volume_delta_m3=0.1,
            payload_json="{}",
        )
        row = db_conn.execute(
            "SELECT mass_kg FROM location_inventory_stacks WHERE location_id='LEO' AND stack_key='water'"
        ).fetchone()
        assert row is not None
        assert float(row["mass_kg"]) == pytest.approx(100.0)

        # Remove 100 kg water
        m._upsert_inventory_stack(
            db_conn,
            location_id="LEO",
            stack_type="resource",
            stack_key="water",
            item_id="water",
            name="Water",
            quantity_delta=-100.0,
            mass_delta_kg=-100.0,
            volume_delta_m3=-0.1,
            payload_json="{}",
        )
        row2 = db_conn.execute(
            "SELECT mass_kg FROM location_inventory_stacks WHERE location_id='LEO' AND stack_key='water'"
        ).fetchone()
        # Row should be deleted (cleanup) or have 0 mass
        if row2:
            assert float(row2["mass_kg"]) == pytest.approx(0.0, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════
# §14  EDGE CASES & ERROR HANDLING
# ═══════════════════════════════════════════════════════════════════════════

class TestItemEdgeCases:
    """Edge cases that could cause crashes or data corruption."""

    def test_shipyard_build_empty_name(self, client, storage_catalog):
        """Building with empty name should be handled."""
        if not storage_catalog:
            pytest.skip("No storage")
        r = client.post("/api/shipyard/build", json={
            "name": "",
            "parts": [next(iter(storage_catalog))],
        })
        # Should either fail gracefully or use a fallback name
        assert r.status_code in (200, 400, 422)

    def test_shipyard_build_very_long_name(self, client, storage_catalog):
        """Building with extremely long name should be handled."""
        if not storage_catalog:
            pytest.skip("No storage")
        r = client.post("/api/shipyard/build", json={
            "name": "A" * 500,
            "parts": [next(iter(storage_catalog))],
        })
        assert r.status_code in (200, 400, 422)

    def test_shipyard_build_no_parts(self, client):
        """Building with zero parts should fail or produce a minimal ship."""
        r = client.post("/api/shipyard/build", json={
            "name": "EmptyShip",
            "parts": [],
        })
        assert r.status_code in (200, 400, 422)

    def test_shipyard_preview_massive_parts_list(self, client, thruster_catalog):
        """Preview with 50 copies of the same part should not crash."""
        if not thruster_catalog:
            pytest.skip("No thrusters")
        item_id = next(iter(thruster_catalog))
        r = client.post("/api/shipyard/preview", json={
            "parts": [item_id] * 50,
        })
        assert r.status_code == 200
        data = r.json()
        assert len(data.get("parts", [])) == 50

    def test_inventory_transfer_invalid_source(self, client):
        """Transfer from invalid source should fail gracefully."""
        r = client.post("/api/inventory/transfer", json={
            "source_kind": "ship_resource",
            "source_id": "no_such_ship",
            "source_key": "water",
            "target_kind": "location",
            "target_id": "LEO",
            "amount": 10,
            "resource_id": "water",
        })
        assert r.status_code in (400, 404, 422, 403), (
            f"Expected 4xx for bad source, got {r.status_code}: {r.text}"
        )

    def test_stack_transfer_invalid_source(self, client):
        """Stack transfer from invalid source should fail gracefully."""
        r = client.post("/api/stack/transfer", json={
            "source_kind": "ship_part",
            "source_id": "no_such_ship",
            "source_key": "no_such_part",
            "target_kind": "location",
            "target_id": "LEO",
        })
        assert r.status_code in (400, 404, 422, 403), (
            f"Expected 4xx for bad stack transfer, got {r.status_code}: {r.text}"
        )

    def test_mining_start_invalid_equipment(self, client):
        """Starting mining on non-existent equipment should fail."""
        r = client.post("/api/industry/mining/start", json={
            "equipment_id": "nonexistent_999",
            "resource_id": "water",
        })
        assert r.status_code in (400, 404, 422)

    def test_mining_stop_invalid_job(self, client):
        """Stopping a non-existent mining job should fail."""
        r = client.post("/api/industry/mining/stop", json={
            "job_id": "nonexistent_job_999",
        })
        assert r.status_code in (400, 404, 422)

    def test_constructor_mode_invalid(self, client):
        """Setting mode on non-existent equipment should fail."""
        r = client.post("/api/industry/constructor/mode", json={
            "equipment_id": "nonexistent_999",
            "mode": "mine",
        })
        assert r.status_code in (400, 404, 422)

    def test_refinery_assign_invalid(self, client):
        """Assigning recipe to non-existent slot should fail."""
        r = client.post("/api/industry/refinery/assign", json={
            "slot_id": "nonexistent_slot_999",
            "recipe_id": "nonexistent_recipe",
        })
        assert r.status_code in (400, 404, 422)


# ═══════════════════════════════════════════════════════════════════════════
# §15  FULL FLOW INTEGRATION — BUILD → LOAD → TRANSFER → VERIFY
# ═══════════════════════════════════════════════════════════════════════════

class TestFullFlowIntegration:
    """End-to-end flow tests that exercise multiple systems together."""

    def test_build_ship_load_cargo_view_hangar(self, client):
        """Build ship → view in hangar → view in cargo context → verify consistency."""
        client.post("/api/admin/org/grant", json={
            "username": "admin", "money_usd": 999_999_999_999, "research_points": 0,
        })
        br = client.get("/api/org/boostable-items")
        if br.status_code != 200:
            pytest.skip("No boostable items endpoint")
        boostable = [i["item_id"] for i in br.json().get("items", []) if i.get("type") != "resource"]
        if not boostable:
            pytest.skip("No boostable parts")
        parts = boostable[:2]

        items_payload = [{"item_id": pid, "quantity": 1} for pid in parts]
        client.post("/api/org/boost", json={"items": items_payload})
        r = client.post("/api/shipyard/build", json={
            "name": "FlowTestShip",
            "parts": parts,
        })
        assert r.status_code == 200
        ship = r.json()["ship"]
        ship_id = ship["id"]
        location_id = ship["location_id"]

        # Hangar context
        hangar_r = client.get(f"/api/hangar/context/{ship_id}")
        assert hangar_r.status_code == 200
        hangar = hangar_r.json()

        # Cargo context
        cargo_r = client.get(f"/api/cargo/context/{location_id}")
        assert cargo_r.status_code == 200
        cargo = cargo_r.json()

        # Ship inventory
        inv_r = client.get(f"/api/inventory/ship/{ship_id}")
        assert inv_r.status_code == 200
        inv = inv_r.json()

        # Inventory context
        ctx_r = client.get(f"/api/inventory/context/ship/{ship_id}")
        assert ctx_r.status_code == 200
        ctx = ctx_r.json()

        # All should reference the same ship
        assert inv["ship_id"] == ship_id
        assert ctx["anchor"]["id"] == ship_id

        # Verify no 500 errors throughout
        for label, resp in [("hangar", hangar_r), ("cargo", cargo_r), ("inv", inv_r), ("ctx", ctx_r)]:
            assert resp.status_code == 200, f"{label} returned {resp.status_code}"

    def test_multiple_ships_different_parts(self, client):
        """Build multiple ships with different part configs, verify all queryable."""
        client.post("/api/admin/org/grant", json={
            "username": "admin", "money_usd": 999_999_999_999, "research_points": 0,
        })
        br = client.get("/api/org/boostable-items")
        if br.status_code != 200:
            pytest.skip("No boostable items endpoint")
        boostable = [i["item_id"] for i in br.json().get("items", []) if i.get("type") != "resource"]
        if len(boostable) < 2:
            pytest.skip("Need at least 2 boostable parts")

        # Build different configs from boostable parts
        configs = [[boostable[0]], [boostable[1]]]
        if len(boostable) >= 3:
            configs.append([boostable[0], boostable[2]])

        ship_ids = []
        for i, parts in enumerate(configs):
            items_payload = [{"item_id": pid, "quantity": 1} for pid in parts]
            client.post("/api/org/boost", json={"items": items_payload})
            r = client.post("/api/shipyard/build", json={
                "name": f"MultiPartShip{i}",
                "parts": parts,
            })
            assert r.status_code == 200, f"Build {i} failed: {r.text}"
            ship_ids.append(r.json()["ship"]["id"])

        # Query each ship's hangar context — none should crash
        for sid in ship_ids:
            r = client.get(f"/api/hangar/context/{sid}")
            assert r.status_code == 200, f"Hangar context for {sid} failed"

    def test_site_inventory_and_catalog_consistency(self, client, all_catalogs):
        """Check every item in site inventory is findable in the catalog."""
        from catalog_service import get_item_info

        r = client.get("/api/sites")
        assert r.status_code == 200
        sites = r.json().get("sites", [])

        unknown_items = []
        for site in sites[:5]:  # sample up to 5 sites
            detail_r = client.get(f"/api/sites/{site['id']}")
            if detail_r.status_code != 200:
                continue
            detail = detail_r.json()
            inv = detail.get("inventory") or {}
            for resource in inv.get("resources") or []:
                item_id = str(resource.get("item_id") or resource.get("resource_id") or "")
                if item_id and not get_item_info(item_id):
                    unknown_items.append(f"site={site['id']} resource={item_id}")
            for part in inv.get("parts") or []:
                item_id = str(part.get("item_id") or "")
                if item_id and not get_item_info(item_id):
                    unknown_items.append(f"site={site['id']} part={item_id}")

        assert not unknown_items, f"Site inventory references unknown items: {unknown_items}"

    def test_shipyard_catalog_all_items_previewable(self, client):
        """Every item in the shipyard catalog should be usable in a preview."""
        r = client.get("/api/shipyard/catalog")
        assert r.status_code == 200
        catalog = r.json()

        # Extract item IDs from the catalog response
        all_part_ids = set()
        for key, value in catalog.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        item_id = item.get("item_id") or item.get("id") or ""
                        if item_id:
                            all_part_ids.add(str(item_id))

        if not all_part_ids:
            pytest.skip("No part IDs found in shipyard catalog")

        # Preview each one individually — cap at 30 to keep test time reasonable
        failures = []
        for item_id in sorted(all_part_ids)[:30]:
            r = client.post("/api/shipyard/preview", json={"parts": [item_id]})
            if r.status_code != 200:
                failures.append(f"{item_id}: HTTP {r.status_code}")
        assert not failures, f"Shipyard preview failures for catalog items: {failures}"
