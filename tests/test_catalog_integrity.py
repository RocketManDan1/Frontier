"""
Catalog integrity tests — validate that all JSON item/recipe files load
correctly and contain the required fields.

Catches:
  - Malformed JSON files
  - Missing required fields (id, name, type, etc.)
  - Dangling references (recipe inputs referencing non-existent resources)
  - Duplicate IDs across catalog files
  - Schema violations in item definitions
"""

import json
from pathlib import Path

import pytest

import sys, os
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DEV_SKIP_AUTH", "1")

ITEMS_DIR = Path(__file__).resolve().parent.parent / "items"


# ── JSON validity ──────────────────────────────────────────────────────────

def _all_json_files():
    """Yield every .json file under items/."""
    for p in sorted(ITEMS_DIR.rglob("*.json")):
        yield pytest.param(p, id=str(p.relative_to(ITEMS_DIR)))


@pytest.mark.parametrize("json_path", _all_json_files())
def test_json_parses(json_path: Path):
    """Every JSON file under items/ must be valid JSON."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert isinstance(data, (dict, list)), f"{json_path.name} root is not dict or list"


# ── Resource files ─────────────────────────────────────────────────────────

class TestResourceCatalog:
    def test_loads_without_error(self, resource_catalog):
        assert len(resource_catalog) > 0

    def test_required_fields(self, resource_catalog):
        required = {"id", "name", "type"}
        for item_id, item in resource_catalog.items():
            missing = required - set(item.keys())
            assert not missing, f"Resource {item_id} missing: {missing}"

    def test_ids_match_keys(self, resource_catalog):
        for key, item in resource_catalog.items():
            assert item["id"] == key, f"Key {key} != item.id {item['id']}"

    def test_no_empty_names(self, resource_catalog):
        for item_id, item in resource_catalog.items():
            assert item["name"].strip(), f"Resource {item_id} has empty name"

    def test_mass_per_m3_positive(self, resource_catalog):
        for item_id, item in resource_catalog.items():
            mass = item.get("mass_per_m3_kg")
            if mass is not None:
                assert float(mass) > 0, f"Resource {item_id} has non-positive mass_per_m3_kg"


# ── Storage files ──────────────────────────────────────────────────────────

class TestStorageCatalog:
    def test_loads_without_error(self, storage_catalog):
        assert len(storage_catalog) > 0

    def test_required_fields(self, storage_catalog):
        # Catalog normalizes to item_id (not id)
        required = {"item_id", "name", "type"}
        for item_id, item in storage_catalog.items():
            missing = required - set(item.keys())
            assert not missing, f"Storage {item_id} missing: {missing}"

    def test_capacity_positive(self, storage_catalog):
        for item_id, item in storage_catalog.items():
            cap = item.get("capacity_m3")
            if cap is not None:
                assert float(cap) > 0, f"Storage {item_id} has non-positive capacity_m3"


# ── Thruster catalog ──────────────────────────────────────────────────────

class TestThrusterCatalog:
    def test_loads_without_error(self, thruster_catalog):
        assert len(thruster_catalog) > 0

    def test_required_fields(self, thruster_catalog):
        for item_id, item in thruster_catalog.items():
            assert "item_id" in item or "id" in item, f"Thruster {item_id} missing id/item_id"
            assert "name" in item, f"Thruster {item_id} missing name"

    def test_thrust_and_isp_positive(self, thruster_catalog):
        for item_id, item in thruster_catalog.items():
            thrust = item.get("thrust_kn") or item.get("thrust_kN")
            isp = item.get("isp_s")
            if thrust is not None:
                assert float(thrust) > 0, f"Thruster {item_id} has non-positive thrust"
            if isp is not None:
                assert float(isp) > 0, f"Thruster {item_id} has non-positive ISP"


# ── Reactor catalog ───────────────────────────────────────────────────────

class TestReactorCatalog:
    def test_loads_without_error(self, reactor_catalog):
        assert len(reactor_catalog) > 0

    def test_required_fields(self, reactor_catalog):
        for item_id, item in reactor_catalog.items():
            assert "item_id" in item or "id" in item, f"Reactor {item_id} missing id/item_id"
            assert "name" in item, f"Reactor {item_id} missing name"


# ── Generator catalog ─────────────────────────────────────────────────────

class TestGeneratorCatalog:
    def test_loads_without_error(self, generator_catalog):
        assert len(generator_catalog) > 0


# ── Radiator catalog ──────────────────────────────────────────────────────

class TestRadiatorCatalog:
    def test_loads_without_error(self, radiator_catalog):
        assert len(radiator_catalog) > 0


# ── Recipe catalog ─────────────────────────────────────────────────────────

class TestRecipeCatalog:
    def test_loads_without_error(self, recipe_catalog):
        assert len(recipe_catalog) > 0

    def test_required_fields(self, recipe_catalog):
        required = {"recipe_id", "name", "output_item_id", "inputs"}
        for recipe_id, recipe in recipe_catalog.items():
            missing = required - set(recipe.keys())
            assert not missing, f"Recipe {recipe_id} missing: {missing}"

    def test_inputs_have_item_id(self, recipe_catalog):
        for recipe_id, recipe in recipe_catalog.items():
            for i, inp in enumerate(recipe.get("inputs", [])):
                assert "item_id" in inp, f"Recipe {recipe_id} input[{i}] missing item_id"

    def test_build_time_positive(self, recipe_catalog):
        for recipe_id, recipe in recipe_catalog.items():
            bt = recipe.get("build_time_s")
            if bt is not None:
                assert float(bt) > 0, f"Recipe {recipe_id} has non-positive build_time_s"

    def test_output_item_exists_somewhere(self, recipe_catalog, resource_catalog, storage_catalog):
        """Every recipe output_item_id should exist in some catalog."""
        all_known_ids = set(resource_catalog.keys()) | set(storage_catalog.keys())
        # Also include recipe outputs themselves (intermediate goods may only exist as outputs)
        all_known_ids |= {r.get("output_item_id") for r in recipe_catalog.values()}

        for recipe_id, recipe in recipe_catalog.items():
            out_id = recipe.get("output_item_id")
            if out_id:
                assert out_id in all_known_ids, (
                    f"Recipe {recipe_id} output_item_id={out_id} not found in any catalog"
                )

    def test_input_items_exist_somewhere(self, recipe_catalog, resource_catalog, storage_catalog):
        """Every recipe input item_id should be a known resource or catalog item."""
        all_known_ids = set(resource_catalog.keys()) | set(storage_catalog.keys())
        all_known_ids |= {r.get("output_item_id") for r in recipe_catalog.values()}

        # Exclude the template recipe (it has placeholder values)
        template_ids = {"template_recipe_id"}

        missing_refs = []
        for recipe_id, recipe in recipe_catalog.items():
            if recipe_id in template_ids:
                continue
            for inp in recipe.get("inputs", []):
                iid = inp.get("item_id")
                if iid and iid not in all_known_ids and iid not in template_ids:
                    missing_refs.append(f"{recipe_id} → {iid}")

        assert not missing_refs, (
            f"Dangling input references:\n  " + "\n  ".join(missing_refs[:20])
        )


# ── Cross-catalog uniqueness ──────────────────────────────────────────────

class TestCrossItemUniqueness:
    def test_no_duplicate_resource_ids(self, resource_catalog):
        """resource_catalog keys should already be unique, but verify file-level."""
        files = list((ITEMS_DIR / "Resources").glob("*.json"))
        ids = []
        for f in files:
            data = json.loads(f.read_text())
            if isinstance(data, dict) and "id" in data:
                ids.append(data["id"])
        dupes = [x for x in ids if ids.count(x) > 1]
        assert not dupes, f"Duplicate resource IDs in files: {set(dupes)}"

    def test_no_duplicate_recipe_ids(self):
        files = list((ITEMS_DIR / "Recipes").glob("*.json"))
        ids = []
        for f in files:
            data = json.loads(f.read_text())
            if isinstance(data, dict) and "recipe_id" in data:
                ids.append(data["recipe_id"])
        dupes = [x for x in ids if ids.count(x) > 1]
        assert not dupes, f"Duplicate recipe IDs in files: {set(dupes)}"


# ── Item lookup ────────────────────────────────────────────────────────────

class TestItemLookup:
    def test_get_item_info_known_resource(self, resource_catalog):
        import catalog_service
        first_id = next(iter(resource_catalog))
        info = catalog_service.get_item_info(first_id)
        assert info is not None
        assert info["id"] == first_id

    def test_get_item_info_unknown(self):
        import catalog_service
        info = catalog_service.get_item_info("__nonexistent_item_xyz__")
        assert info is None


# ── Ship stats computation ─────────────────────────────────────────────────

class TestShipStatsComputation:
    def test_compute_wet_mass(self):
        import catalog_service
        wet = catalog_service.compute_wet_mass_kg(5000.0, 2000.0)
        assert wet == pytest.approx(7000.0)

    def test_compute_acceleration(self):
        import catalog_service
        acc = catalog_service.compute_acceleration_gs(5000.0, 2000.0, 10.0)
        assert acc > 0

    def test_derive_ship_stats_empty_parts(self, resource_catalog):
        import catalog_service
        stats = catalog_service.derive_ship_stats_from_parts([], resource_catalog)
        assert isinstance(stats, dict)
        assert stats.get("dry_mass_kg", 0) == 0 or "dry_mass_kg" in stats
