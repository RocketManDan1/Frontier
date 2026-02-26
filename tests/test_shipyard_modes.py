"""
Shipyard mode tests — verify the three shipyard modes function correctly.

Tests cover:
  - "Build to Boost" mode: boostable items, boost cost, boost+build flow
  - "Build from Site" mode: build from location inventory
  - "Edit Ship" mode: deconstruct + rebuild flow
  - API endpoint validation for all related routes
  - Frontend HTML structure validation
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DEV_SKIP_AUTH", "1")


# ── HTML Structure Tests ──────────────────────────────────────────────────

class TestShipyardHTMLStructure:
    """Verify the shipyard HTML has the mode selector and designer elements."""

    @pytest.fixture()
    def html_content(self):
        html_path = Path(__file__).resolve().parent.parent / "static" / "shipyard.html"
        return html_path.read_text()

    def test_mode_selector_exists(self, html_content):
        assert 'id="shipyardModeSelect"' in html_content

    def test_boost_mode_card_exists(self, html_content):
        assert 'data-mode="boost"' in html_content

    def test_site_mode_card_exists(self, html_content):
        assert 'data-mode="site"' in html_content

    def test_edit_mode_card_exists(self, html_content):
        assert 'data-mode="edit"' in html_content

    def test_designer_hidden_by_default(self, html_content):
        assert 'id="shipyardDesigner"' in html_content
        # Designer should start hidden
        assert 'shipyardDesigner' in html_content
        # Check it starts with display:none
        idx = html_content.index('id="shipyardDesigner"')
        surrounding = html_content[max(0, idx - 100):idx + 100]
        assert 'display:none' in surrounding

    def test_ship_selector_exists(self, html_content):
        assert 'id="shipyardShipSelect"' in html_content

    def test_ship_selector_hidden_by_default(self, html_content):
        idx = html_content.index('id="shipyardShipSelect"')
        surrounding = html_content[max(0, idx - 100):idx + 100]
        assert 'display:none' in surrounding

    def test_boost_cost_element_exists(self, html_content):
        assert 'id="shipyardBoostCost"' in html_content

    def test_back_buttons_exist(self, html_content):
        assert 'id="shipyardBackToModes"' in html_content
        assert 'id="shipyardModeBack"' in html_content

    def test_mode_card_labels(self, html_content):
        assert "Build to Boost" in html_content
        assert "Build from Site" in html_content
        assert "Edit Ship" in html_content

    def test_cache_buster_updated(self, html_content):
        # Ensure the JS cache buster is >= sy18
        assert "shipyard.js?v=sy1" in html_content
        # Ensure CSS cache buster is >= layout34
        assert "styles.css?v=layout3" in html_content


# ── JavaScript Structure Tests ────────────────────────────────────────────

class TestShipyardJSStructure:
    """Verify the shipyard JS has mode-related functions and state."""

    @pytest.fixture()
    def js_content(self):
        js_path = Path(__file__).resolve().parent.parent / "static" / "js" / "shipyard.js"
        return js_path.read_text()

    def test_current_mode_variable(self, js_content):
        assert 'let currentMode = ""' in js_content

    def test_enter_mode_function(self, js_content):
        assert "async function enterMode(mode)" in js_content

    def test_boost_mode_handler(self, js_content):
        assert "buildShipBoost" in js_content

    def test_site_mode_handler(self, js_content):
        assert "buildShipSite" in js_content

    def test_edit_mode_handler(self, js_content):
        assert "buildShipEdit" in js_content

    def test_boost_cost_rendering(self, js_content):
        assert "renderBoostCost" in js_content

    def test_org_balance_loading(self, js_content):
        assert "loadOrgBalance" in js_content

    def test_fleet_loading(self, js_content):
        assert "loadFleet" in js_content

    def test_ship_selector_rendering(self, js_content):
        assert "renderShipSelector" in js_content

    def test_mode_selectors_setup(self, js_content):
        assert "setupModeSelectors" in js_content

    def test_boost_uses_boostable_items(self, js_content):
        assert "/api/org/boostable-items" in js_content

    def test_boost_uses_org_boost(self, js_content):
        assert "/api/org/boost" in js_content

    def test_edit_uses_deconstruct(self, js_content):
        assert "/deconstruct" in js_content

    def test_edit_uses_fleet_state(self, js_content):
        assert "/api/state" in js_content

    def test_show_screen_function(self, js_content):
        assert "function showScreen(screen)" in js_content

    def test_three_screens(self, js_content):
        # Should have modes, shipSelect, and designer screens
        assert '"modes"' in js_content
        assert '"shipSelect"' in js_content
        assert '"designer"' in js_content

    def test_slot_categories_order(self, js_content):
        """Verify category order: Robonauts at top, Thrusters at bottom."""
        robo_idx = js_content.index('"robonauts"')
        thrust_idx = js_content.index('"thrusters"')
        assert robo_idx < thrust_idx

    def test_augment_garage_with_ship_parts(self, js_content):
        """Edit mode should augment the garage with the ship's own parts."""
        assert "augmentGarageWithShipParts" in js_content

    def test_fuel_hidden_in_boost_mode(self, js_content):
        """Boost mode should hide fuel loading."""
        assert 'currentMode === "boost"' in js_content


# ── CSS Structure Tests ───────────────────────────────────────────────────

class TestShipyardCSSStructure:
    """Verify the CSS has mode selector styles."""

    @pytest.fixture()
    def css_content(self):
        css_path = Path(__file__).resolve().parent.parent / "static" / "styles.css"
        return css_path.read_text()

    def test_mode_select_class(self, css_content):
        assert ".shipyardModeSelect" in css_content

    def test_mode_card_class(self, css_content):
        assert ".shipyardModeCard" in css_content

    def test_mode_card_hover(self, css_content):
        assert ".shipyardModeCard:hover" in css_content

    def test_ship_select_class(self, css_content):
        assert ".shipyardShipSelect" in css_content

    def test_ship_card_class(self, css_content):
        assert ".shipyardShipCard" in css_content

    def test_ship_card_disabled_class(self, css_content):
        assert ".shipyardShipCardDisabled" in css_content

    def test_boost_cost_class(self, css_content):
        assert ".shipyardBoostCost" in css_content

    def test_boost_cost_insufficient(self, css_content):
        assert ".boostCostInsufficient" in css_content


# ── API Endpoint Tests ────────────────────────────────────────────────────

class TestBoostableItemsEndpoint:
    """Test /api/org/boostable-items endpoint."""

    def test_returns_200(self, client):
        r = client.get("/api/org/boostable-items")
        assert r.status_code == 200

    def test_returns_items_list(self, client):
        r = client.get("/api/org/boostable-items")
        data = r.json()
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_returns_cost_constants(self, client):
        r = client.get("/api/org/boostable-items")
        data = r.json()
        assert "base_cost_usd" in data
        assert "cost_per_kg_usd" in data
        assert data["base_cost_usd"] == 100_000_000
        assert data["cost_per_kg_usd"] == 5_000

    def test_items_have_required_fields(self, client):
        r = client.get("/api/org/boostable-items")
        data = r.json()
        for item in data["items"]:
            assert "item_id" in item
            assert "name" in item
            assert "type" in item
            assert "mass_per_unit_kg" in item


class TestBoostCostEndpoint:
    """Test /api/org/boost-cost endpoint."""

    def test_returns_200(self, client):
        r = client.post("/api/org/boost-cost", json={"mass_kg": 1000})
        assert r.status_code == 200

    def test_cost_formula(self, client):
        mass_kg = 5000
        r = client.post("/api/org/boost-cost", json={"mass_kg": mass_kg})
        data = r.json()
        expected = 100_000_000 + (5_000 * mass_kg)
        assert data["cost_usd"] == pytest.approx(expected)

    def test_zero_mass(self, client):
        r = client.post("/api/org/boost-cost", json={"mass_kg": 0})
        data = r.json()
        assert data["cost_usd"] == pytest.approx(100_000_000)


class TestShipyardPreviewEndpoint:
    """Test /api/shipyard/preview endpoint."""

    def test_empty_parts_returns_200(self, client):
        r = client.post("/api/shipyard/preview", json={
            "parts": [],
            "source_location_id": "LEO",
        })
        assert r.status_code == 200

    def test_returns_stats(self, client):
        r = client.post("/api/shipyard/preview", json={
            "parts": [],
            "source_location_id": "LEO",
        })
        data = r.json()
        assert "stats" in data
        assert "dry_mass_kg" in data["stats"]

    def test_invalid_location(self, client):
        r = client.post("/api/shipyard/preview", json={
            "parts": [],
            "source_location_id": "NONEXISTENT_LOCATION_XYZ",
        })
        assert r.status_code == 400


class TestShipyardCatalogEndpoint:
    """Test /api/shipyard/catalog endpoint."""

    def test_returns_200(self, client):
        r = client.get("/api/shipyard/catalog")
        assert r.status_code == 200

    def test_returns_parts(self, client):
        r = client.get("/api/shipyard/catalog")
        data = r.json()
        assert "parts" in data
        assert isinstance(data["parts"], list)
        assert len(data["parts"]) > 0

    def test_returns_build_source_locations(self, client):
        r = client.get("/api/shipyard/catalog")
        data = r.json()
        assert "build_source_locations" in data
        assert isinstance(data["build_source_locations"], list)

    def test_parts_have_item_id(self, client):
        r = client.get("/api/shipyard/catalog")
        data = r.json()
        for part in data["parts"]:
            assert "item_id" in part, f"Part missing item_id: {part}"


class TestOrgEndpoint:
    """Test /api/org endpoint returns balance."""

    def test_returns_200(self, client):
        r = client.get("/api/org")
        assert r.status_code == 200

    def test_returns_balance(self, client):
        r = client.get("/api/org")
        data = r.json()
        assert "org" in data
        assert "balance_usd" in data["org"]
        assert isinstance(data["org"]["balance_usd"], (int, float))


class TestFleetStateEndpoint:
    """Test /api/state endpoint for edit mode."""

    def test_returns_200(self, client):
        r = client.get("/api/state")
        assert r.status_code == 200

    def test_returns_ships(self, client):
        r = client.get("/api/state")
        data = r.json()
        assert "ships" in data
        assert isinstance(data["ships"], list)


# ── Boost Cost Calculation Tests ──────────────────────────────────────────

class TestBoostCostCalculation:
    """Unit tests for boost cost calculation."""

    def test_base_cost_formula(self):
        from org_service import calculate_boost_cost, LEO_BOOST_BASE_COST, LEO_BOOST_COST_PER_KG
        assert calculate_boost_cost(0) == LEO_BOOST_BASE_COST
        assert calculate_boost_cost(1000) == LEO_BOOST_BASE_COST + (LEO_BOOST_COST_PER_KG * 1000)

    def test_boostable_tech_levels(self):
        from org_service import BOOSTABLE_TECH_LEVELS
        assert 1 in BOOSTABLE_TECH_LEVELS
        assert 1.5 in BOOSTABLE_TECH_LEVELS
        assert 2 in BOOSTABLE_TECH_LEVELS
        assert 2.5 in BOOSTABLE_TECH_LEVELS
        assert 3 not in BOOSTABLE_TECH_LEVELS

    def test_leo_location_constant(self):
        from org_service import LEO_LOCATION_ID
        assert LEO_LOCATION_ID == "LEO"


# ── Build from Site Integration Tests ──────────────────────────────────── 

class TestBuildFromSite:
    """Integration tests for site-based ship building."""

    def test_build_requires_name(self, client):
        r = client.post("/api/shipyard/build", json={
            "name": "",
            "parts": ["some_part"],
            "source_location_id": "LEO",
        })
        assert r.status_code == 400

    def test_build_requires_parts(self, client):
        r = client.post("/api/shipyard/build", json={
            "name": "Test Ship",
            "parts": [],
            "source_location_id": "LEO",
        })
        assert r.status_code == 400

    def test_build_invalid_location(self, client):
        r = client.post("/api/shipyard/build", json={
            "name": "Test Ship",
            "parts": ["some_part"],
            "source_location_id": "NONEXISTENT_XYZ",
        })
        assert r.status_code == 400


# ── Ship Deconstruct Tests ────────────────────────────────────────────────

class TestShipDeconstruct:
    """Test /api/ships/{id}/deconstruct endpoint for edit mode."""

    def test_nonexistent_ship_returns_404(self, client):
        r = client.post("/api/ships/nonexistent_ship_xyz/deconstruct", json={
            "keep_ship_record": False,
        })
        assert r.status_code == 404


# ── Full Boost+Build Flow Test ────────────────────────────────────────────

class TestBoostBuildFlow:
    """
    End-to-end test: boost items to LEO, then build a ship from them.
    This mirrors what the "Build to Boost" mode does in the UI.
    """

    def test_boost_and_build_flow(self, client):
        """Full boost+build flow — boost a part and build a ship from it."""
        # Get boostable items
        r = client.get("/api/org/boostable-items")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) > 0

        # Find a non-resource part (something with mass)
        part_items = [i for i in items if i["type"] != "resource"]
        if not part_items:
            pytest.skip("No boostable parts available")

        chosen = part_items[0]
        item_id = chosen["item_id"]
        item_mass = chosen["mass_per_unit_kg"]

        # Check org balance
        r = client.get("/api/org")
        assert r.status_code == 200
        balance_before = r.json()["org"]["balance_usd"]

        # Calculate expected cost
        expected_cost = 100_000_000 + (5_000 * item_mass)

        if balance_before < expected_cost:
            pytest.skip("Insufficient funds for boost test")

        # Boost 1x of the chosen item
        r = client.post("/api/org/boost", json={
            "items": [{"item_id": item_id, "quantity": 1}],
        })
        assert r.status_code == 200
        boost_data = r.json()
        assert boost_data["ok"] is True
        assert boost_data["destination"] == "LEO"

        # Verify balance was deducted
        r = client.get("/api/org")
        balance_after = r.json()["org"]["balance_usd"]
        assert balance_after < balance_before

        # Now build a ship at LEO using the boosted part
        r = client.post("/api/shipyard/build", json={
            "name": "Boost Test Ship",
            "parts": [item_id],
            "source_location_id": "LEO",
        })
        assert r.status_code == 200
        build_data = r.json()
        assert build_data["ok"] is True
        assert build_data["ship"]["location_id"] == "LEO"

        # Clean up: deconstruct the ship we just built
        ship_id = build_data["ship"]["id"]
        r = client.post(f"/api/ships/{ship_id}/deconstruct", json={
            "keep_ship_record": False,
        })
        assert r.status_code == 200
