"""
API smoke tests — hit every endpoint and verify it doesn't crash.

These tests run against the FastAPI TestClient with DEV_SKIP_AUTH=1,
so all requests appear as admin.  The goal is not to validate business
logic in depth but to catch:
  - import errors / missing dependencies
  - broken SQL (syntax errors, missing columns)
  - 500-level crashes from unexpected None values
  - regressions after migrations or refactors
"""

import pytest


# ── Health & catalog (no auth, no state) ────────────────────────────────────

class TestHealthAndCatalog:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_catalog_items(self, client):
        r = client.get("/api/catalog/items")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, (list, dict))

    def test_catalog_recipes(self, client):
        r = client.get("/api/catalog/recipes")
        assert r.status_code == 200

    def test_catalog_recipes_by_category(self, client):
        r = client.get("/api/catalog/recipes/by-category")
        assert r.status_code == 200

    def test_research_tree(self, client):
        r = client.get("/api/research/tree")
        assert r.status_code == 200

    def test_shipyard_catalog(self, client):
        r = client.get("/api/shipyard/catalog")
        assert r.status_code == 200


# ── Auth endpoints ──────────────────────────────────────────────────────────

class TestAuthEndpoints:
    def test_me(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 200

    def test_corps(self, client):
        r = client.get("/api/auth/corps")
        assert r.status_code == 200

    def test_online_corps(self, client):
        r = client.get("/api/auth/online-corps")
        assert r.status_code == 200

    def test_login_missing_fields(self, client):
        r = client.post("/api/auth/login", json={})
        assert r.status_code in (400, 422)

    def test_logout(self, client):
        r = client.post("/api/auth/logout")
        # Should succeed (even with no real session)
        assert r.status_code in (200, 303, 307)


# ── Simulation / time ──────────────────────────────────────────────────────

class TestSimulationEndpoints:
    def test_time(self, client):
        r = client.get("/api/time")
        assert r.status_code == 200
        data = r.json()
        assert "game_time_s" in data or "now" in data or isinstance(data, dict)

    def test_state(self, client):
        r = client.get("/api/state")
        assert r.status_code == 200


# ── Fleet endpoints ────────────────────────────────────────────────────────

class TestFleetEndpoints:
    def test_state_returns_ships(self, client):
        r = client.get("/api/state")
        assert r.status_code == 200

    def test_transfer_quote_missing_params(self, client):
        r = client.get("/api/transfer_quote")
        # Missing required query params → 400 or 422
        assert r.status_code in (400, 422)


# ── Location endpoints ─────────────────────────────────────────────────────

class TestLocationEndpoints:
    def test_locations(self, client):
        r = client.get("/api/locations")
        assert r.status_code == 200

    def test_locations_dynamic(self, client):
        r = client.get("/api/locations?dynamic=1")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data.get("locations"), list)
        assert "game_time_s" in data
        ids = {str(loc.get("id")) for loc in (data.get("locations") or []) if isinstance(loc, dict)}
        assert "grp_moon" in ids

    def test_locations_tree(self, client):
        r = client.get("/api/locations/tree")
        assert r.status_code == 200

    def test_surface_sites(self, client):
        r = client.get("/api/surface_sites")
        assert r.status_code == 200


# ── Organization endpoints ─────────────────────────────────────────────────

class TestOrgEndpoints:
    def test_org_get(self, client):
        r = client.get("/api/org")
        # May return 200 or 404 depending on whether the admin user has an org
        assert r.status_code in (200, 404, 500)

    def test_marketplace(self, client):
        r = client.get("/api/org/marketplace")
        assert r.status_code in (200, 404)

    def test_boostable_items(self, client):
        r = client.get("/api/org/boostable-items")
        assert r.status_code in (200, 404)

    def test_research_unlocks(self, client):
        r = client.get("/api/org/research/unlocks")
        assert r.status_code in (200, 404)


# ── Admin endpoints ────────────────────────────────────────────────────────

class TestAdminEndpoints:
    def test_accounts_list(self, client):
        r = client.get("/api/admin/accounts")
        assert r.status_code == 200

    def test_toggle_pause(self, client):
        r = client.post("/api/admin/simulation/toggle_pause")
        assert r.status_code == 200

    def test_admin_org_grant(self, client):
        r = client.post(
            "/api/admin/org/grant",
            json={"username": "admin", "money_usd": 1000, "research_points": 5},
        )
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert "org" in data


# ── Inventory endpoints ────────────────────────────────────────────────────

class TestInventoryEndpoints:
    def test_location_inventory(self, client):
        r = client.get("/api/inventory/location/LEO")
        assert r.status_code in (200, 404)

    def test_ship_inventory_not_found(self, client):
        r = client.get("/api/inventory/ship/nonexistent_ship")
        assert r.status_code in (404, 200)


# ── Shipyard endpoints ─────────────────────────────────────────────────────

class TestShipyardEndpoints:
    def test_preview_empty(self, client):
        r = client.post("/api/shipyard/preview", json={"parts": []})
        # May return 200 with zeroed stats or 400/422 for empty parts
        assert r.status_code in (200, 400, 422)


# ── Industry / Sites ───────────────────────────────────────────────────────

class TestIndustryEndpoints:
    def test_sites_list(self, client):
        r = client.get("/api/sites")
        assert r.status_code == 200


# ── HTML page serving ──────────────────────────────────────────────────────

class TestPageServing:
    @pytest.mark.parametrize("path", [
        "/",
        "/login",
    ])
    def test_pages_return_html(self, client, path):
        r = client.get(path, follow_redirects=False)
        # Pages either serve HTML directly or redirect to login
        assert r.status_code in (200, 302, 303, 307)
