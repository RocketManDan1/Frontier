"""
Comprehensive contract-system tests — stress-tests every phase of the
contract lifecycle with full escrow verification.

Sets up two rival corporations (Alpha Corp, Beta Corp) each with inventory
at LEO, runs them through item-exchange, courier, and auction contracts,
and verifies that money and items move correctly at every stage.

Covers:
  - Item exchange: create → escrow items → accept → money+items transfer
  - Item exchange: create → cancel → escrow return
  - Courier: create → escrow items+reward → accept → complete → delivery
  - Courier: create → accept → issuer cancel → escrow return
  - Auction: create → bid → outbid → buyout → settlement
  - Auction: create → bid → issuer cancel → refund bidder
  - Edge cases: insufficient funds, insufficient inventory, self-accept,
    double-accept, wrong status transitions
"""

import json
import os
import re
import secrets
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

# ── Path & env setup ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture()
def app_client():
    """TestClient with DEV_SKIP_AUTH disabled — requires real auth cookies."""
    import auth_service
    old = auth_service.DEV_SKIP_AUTH
    auth_service.DEV_SKIP_AUTH = False

    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as c:
        yield c
    # Restore so other test files are not affected
    auth_service.DEV_SKIP_AUTH = old


def _register_corp(client, name: str, password: str = "test123", color: str = "#ff0000"):
    """Register a corp via the API and return (corp_id, org_id, session_cookie_value)."""
    r = client.post("/api/auth/corp/register", json={
        "corp_name": name,
        "password": password,
        "color": color,
    })
    assert r.status_code == 200, f"Failed to register {name}: {r.text}"
    data = r.json()
    corp_id = data["corp"]["id"]
    # Extract session cookie
    cookie = r.cookies.get("session_token")
    assert cookie, f"No session cookie returned for {name}"
    return corp_id, cookie


def _get_org_id_for_corp(client, cookie: str) -> str:
    """Fetch the org_id for a logged-in corp."""
    r = client.get("/api/org", cookies={"session_token": cookie})
    assert r.status_code == 200, f"Failed to get org: {r.text}"
    return r.json()["org"]["id"]


def _get_balance(client, cookie: str) -> float:
    """Get the org's current balance."""
    r = client.get("/api/org", cookies={"session_token": cookie})
    assert r.status_code == 200
    return float(r.json()["org"]["balance_usd"])


def _seed_resource(client, cookie: str, location_id: str, resource_id: str, mass_kg: float):
    """Seed resource inventory for a corp via the admin debug endpoint or direct SQL.
    Uses the inventory-add endpoint if available, otherwise manipulates DB directly."""
    # Use admin to add inventory — we need an admin session for that.
    # Since we disabled DEV_SKIP_AUTH, we need to log in as admin.
    # Instead, we'll directly inject via the app's DB connection.
    from db import connect_db
    from main import add_resource_to_location_inventory

    conn = connect_db()
    try:
        # Resolve corp_id from the cookie
        row = conn.execute(
            "SELECT corp_id FROM corp_sessions WHERE token = ?", (cookie,)
        ).fetchone()
        corp_id = str(row["corp_id"]) if row else ""
        add_resource_to_location_inventory(conn, location_id, resource_id, mass_kg, corp_id=corp_id)
        conn.commit()
    finally:
        conn.close()


def _seed_part(client, cookie: str, location_id: str, part: dict, count: int = 1):
    """Seed part inventory for a corp."""
    from db import connect_db
    from main import add_part_to_location_inventory

    conn = connect_db()
    try:
        row = conn.execute(
            "SELECT corp_id FROM corp_sessions WHERE token = ?", (cookie,)
        ).fetchone()
        corp_id = str(row["corp_id"]) if row else ""
        add_part_to_location_inventory(conn, location_id, part, count=count, corp_id=corp_id)
        conn.commit()
    finally:
        conn.close()


def _get_resource_qty(client, cookie: str, location_id: str, resource_id: str) -> float:
    """Get the quantity (mass_kg) of a resource in a corp's inventory."""
    r = client.get(f"/api/inventory/location/{location_id}", cookies={"session_token": cookie})
    assert r.status_code == 200, f"Inventory fetch failed: {r.text}"
    data = r.json()
    for res in data.get("resources", []):
        if res.get("stack_key") == resource_id or res.get("item_id") == resource_id:
            return float(res.get("quantity", 0))
    return 0.0


def _get_part_qty(client, cookie: str, location_id: str, stack_key: str) -> float:
    """Get the quantity of a part stack in a corp's inventory."""
    r = client.get(f"/api/inventory/location/{location_id}", cookies={"session_token": cookie})
    assert r.status_code == 200, f"Inventory fetch failed: {r.text}"
    data = r.json()
    for part in data.get("parts", []):
        if part.get("stack_key") == stack_key:
            return float(part.get("quantity", 0))
    return 0.0


def _move_container_to_location(container_stack_key: str, destination_id: str):
    """Directly move a courier cargo container to a destination in the DB.
    Used in tests to simulate the courier physically transporting the crate."""
    from db import connect_db
    conn = connect_db()
    try:
        conn.execute(
            "UPDATE location_inventory_stacks SET location_id = ? WHERE stack_key = ?",
            (destination_id, container_stack_key),
        )
        conn.commit()
    finally:
        conn.close()


def _load_container_onto_ship(container_stack_key: str, location_id: str, corp_id: str) -> str:
    """Create a minimal ship at *location_id* and move the courier container
    from location_inventory_stacks into the ship's parts_json.

    Returns the ship_id.
    """
    from db import connect_db
    conn = connect_db()
    try:
        # Grab the container row so we can build the part dict
        row = conn.execute(
            "SELECT * FROM location_inventory_stacks WHERE stack_key = ?",
            (container_stack_key,),
        ).fetchone()
        assert row is not None, f"Container {container_stack_key} not found in location_inventory_stacks"

        payload = json.loads(row["payload_json"] or "{}")
        part = payload.get("part", {
            "item_id": "courier_cargo_container",
            "name": "Courier Cargo Container",
            "mass_kg": float(row["mass_kg"] or 0),
            "stack_key": container_stack_key,
            "sealed": True,
        })

        # Remove from location inventory
        conn.execute(
            "DELETE FROM location_inventory_stacks WHERE stack_key = ?",
            (container_stack_key,),
        )

        # Create a ship with the container as its sole non-engine part
        ship_id = f"test_courier_ship_{uuid.uuid4().hex[:8]}"
        parts = [
            {"item_id": "test_thruster_mk1", "name": "Test Thruster Mk1", "mass_kg": 50.0},
            part,
        ]
        total_dry_mass = 50.0 + float(part.get("mass_kg", 0))

        # Gracefully handle missing columns (e.g. 'owner' may not exist)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(ships)").fetchall()}
        base = {
            "id": ship_id,
            "name": "Courier Vessel",
            "location_id": location_id,
            "parts_json": json.dumps(parts),
            "fuel_kg": 0,
            "fuel_capacity_kg": 0,
            "dry_mass_kg": total_dry_mass,
            "isp_s": 0,
            "color": "#ffffff",
            "size_px": 12,
        }
        if "owner" in cols:
            base["owner"] = corp_id
        base = {k: v for k, v in base.items() if k in cols or k == "id"}
        # Always keep id, name, location_id, parts_json
        for required_key in ("id", "name", "location_id", "parts_json"):
            base[required_key] = base.get(required_key) or {"id": ship_id, "name": "Courier Vessel", "location_id": location_id, "parts_json": json.dumps(parts)}[required_key]
        col_names = ", ".join(base.keys())
        placeholders = ", ".join("?" for _ in base)
        conn.execute(f"INSERT INTO ships ({col_names}) VALUES ({placeholders})", tuple(base.values()))
        conn.commit()
        return ship_id
    finally:
        conn.close()


def _move_ship_to_location(ship_id: str, destination_id: str):
    """Teleport a ship to a new location in the DB."""
    from db import connect_db
    conn = connect_db()
    try:
        conn.execute(
            "UPDATE ships SET location_id = ? WHERE id = ?",
            (destination_id, ship_id),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def corps(app_client):
    """Register Alpha Corp and Beta Corp (with unique suffix). Seed each with resources at LEO.

    Returns a dict with:
      alpha: {corp_id, cookie, org_id}
      beta:  {corp_id, cookie, org_id}
    """
    suffix = secrets.token_hex(4)
    alpha_id, alpha_cookie = _register_corp(app_client, f"Alpha-{suffix}", color="#ff0000")
    beta_id, beta_cookie = _register_corp(app_client, f"Beta-{suffix}", color="#0000ff")

    alpha_org = _get_org_id_for_corp(app_client, alpha_cookie)
    beta_org = _get_org_id_for_corp(app_client, beta_cookie)

    # Seed inventory: each corp gets 10,000 kg water and 5,000 kg advanced_ceramics at LEO
    _seed_resource(app_client, alpha_cookie, "LEO", "water", 10_000)
    _seed_resource(app_client, alpha_cookie, "LEO", "advanced_ceramics", 5_000)
    _seed_resource(app_client, beta_cookie, "LEO", "water", 10_000)
    _seed_resource(app_client, beta_cookie, "LEO", "advanced_ceramics", 5_000)

    # Seed parts: 10 "Test Thruster" parts each
    test_part = {"item_id": "test_thruster_mk1", "name": "Test Thruster Mk1", "mass_kg": 50.0}
    _seed_part(app_client, alpha_cookie, "LEO", test_part, count=10)
    _seed_part(app_client, beta_cookie, "LEO", test_part, count=10)

    return {
        "alpha": {"corp_id": alpha_id, "cookie": alpha_cookie, "org_id": alpha_org},
        "beta":  {"corp_id": beta_id,  "cookie": beta_cookie,  "org_id": beta_org},
    }


# ======================== ITEM EXCHANGE TESTS ==============================


class TestItemExchangeLifecycle:
    """Full lifecycle for item_exchange contracts."""

    def test_create_escrows_resource(self, app_client, corps):
        """Creating an item_exchange contract removes items from seller inventory."""
        alpha = corps["alpha"]
        before = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Selling 500kg Water",
            "description": "Test",
            "price": 25_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 500, "volume_m3": 0.5, "mass_kg": 500, "type": "resource",
            }],
        })
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

        after = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        assert after == pytest.approx(before - 500, abs=0.1), \
            f"Expected ~{before - 500}, got {after}"

    def test_create_escrows_parts(self, app_client, corps):
        """Creating an item_exchange contract escrows parts from seller."""
        alpha = corps["alpha"]
        # Get part stack_key — need to look it up from inventory
        r = app_client.get("/api/inventory/location/LEO", cookies={"session_token": alpha["cookie"]})
        parts = r.json().get("parts", [])
        thruster_stack = [p for p in parts if "Test Thruster" in p.get("name", "")]
        assert thruster_stack, "No thruster parts found in Alpha inventory"
        sk = thruster_stack[0]["stack_key"]
        before = float(thruster_stack[0]["quantity"])

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Selling 3 Test Thrusters",
            "description": "Part escrow test",
            "price": 150_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": sk, "item_id": "test_thruster_mk1", "name": "Test Thruster Mk1",
                "quantity": 3, "volume_m3": 0, "mass_kg": 150, "type": "part",
            }],
        })
        assert r.status_code == 200, r.text

        after = _get_part_qty(app_client, alpha["cookie"], "LEO", sk)
        assert after == pytest.approx(before - 3, abs=0.1)

    def test_accept_transfers_items_and_money(self, app_client, corps):
        """Accepting an item_exchange: buyer pays → seller gets money, buyer gets items."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        alpha_bal_before = _get_balance(app_client, alpha["cookie"])
        beta_bal_before = _get_balance(app_client, beta["cookie"])
        beta_water_before = _get_resource_qty(app_client, beta["cookie"], "LEO", "water")

        # Alpha creates contract to sell 200 kg water for $10,000
        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "200kg Water for 10k",
            "description": "",
            "price": 10_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 200, "volume_m3": 0.2, "mass_kg": 200, "type": "resource",
            }],
        })
        assert r.status_code == 200
        cid = r.json()["contract_id"]

        # Beta accepts
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200, r.text

        # Verify contract is completed
        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": beta["cookie"]})
        assert r.json()["contract"]["status"] == "completed"

        # Verify money: Alpha gained $10,000, Beta lost $10,000
        alpha_bal_after = _get_balance(app_client, alpha["cookie"])
        beta_bal_after = _get_balance(app_client, beta["cookie"])
        assert alpha_bal_after == pytest.approx(alpha_bal_before + 10_000, rel=1e-4)
        assert beta_bal_after == pytest.approx(beta_bal_before - 10_000, rel=1e-4)

        # Verify items: Beta now has 200 kg more water
        beta_water_after = _get_resource_qty(app_client, beta["cookie"], "LEO", "water")
        assert beta_water_after == pytest.approx(beta_water_before + 200, abs=0.1)

    def test_cancel_returns_escrowed_items(self, app_client, corps):
        """Cancelling an item_exchange returns escrowed items to the issuer."""
        alpha = corps["alpha"]
        before = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Cancel Test",
            "description": "",
            "price": 5_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 300, "volume_m3": 0.3, "mass_kg": 300, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]
        mid = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        assert mid == pytest.approx(before - 300, abs=0.1)

        # Cancel
        r = app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200
        assert r.json()["new_status"] == "cancelled"

        after = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        assert after == pytest.approx(before, abs=0.1)

    def test_cannot_accept_own_contract(self, app_client, corps):
        """Alpha cannot accept their own contract."""
        alpha = corps["alpha"]
        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Self-Accept Test",
            "description": "",
            "price": 1_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 50, "volume_m3": 0.05, "mass_kg": 50, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 400
        assert "own contract" in r.json()["detail"].lower()

        # Clean up — cancel
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_insufficient_inventory_rejects(self, app_client, corps):
        """Cannot create contract for more resources than you own."""
        alpha = corps["alpha"]
        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Too Much Water",
            "description": "",
            "price": 1_000_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 999_999_999, "volume_m3": 999999, "mass_kg": 999_999_999, "type": "resource",
            }],
        })
        assert r.status_code == 400
        assert "insufficient" in r.json()["detail"].lower()

    def test_buyer_insufficient_funds(self, app_client, corps):
        """Buyer without enough money can't accept."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        # Create a contract with a huge price
        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Expensive Water",
            "description": "",
            "price": 999_999_999_999_999,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 10, "volume_m3": 0.01, "mass_kg": 10, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 400
        assert "insufficient funds" in r.json()["detail"].lower()

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})


# ======================== COURIER TESTS ====================================


class TestCourierLifecycle:
    """Full lifecycle for courier contracts."""

    def test_create_escrows_items_and_reward(self, app_client, corps):
        """Creating courier escrows both items and reward money."""
        alpha = corps["alpha"]
        water_before = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        bal_before = _get_balance(app_client, alpha["cookie"])

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Deliver 500kg Water to GEO",
            "description": "Courier test",
            "price": 0,
            "reward": 50_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 500, "volume_m3": 0.5, "mass_kg": 500, "type": "resource",
            }],
        })
        assert r.status_code == 200, r.text
        cid = r.json()["contract_id"]

        water_after = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        bal_after = _get_balance(app_client, alpha["cookie"])

        assert water_after == pytest.approx(water_before - 500, abs=0.1)
        assert bal_after == pytest.approx(bal_before - 50_000, rel=1e-4)

        # Verify contract escrow_usd stored correctly
        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_accept_does_not_release_escrow(self, app_client, corps):
        """Accepting a courier should NOT release items or reward yet.
        A sealed Courier Cargo Container should appear at the pickup location."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        # Create courier
        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Courier Accept Test",
            "description": "",
            "price": 0,
            "reward": 20_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 100, "volume_m3": 0.1, "mass_kg": 100, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]
        beta_bal_before = _get_balance(app_client, beta["cookie"])

        # Beta accepts — becomes courier
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Contract should be in_progress, not completed
        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": beta["cookie"]})
        contract = r.json()["contract"]
        assert contract["status"] == "in_progress"

        # Courier Cargo Container should exist with a container ID
        assert contract["courier_container_id"] is not None
        container_key = contract["courier_container_id"]
        assert container_key.startswith("courier_crate_")

        # The container should be in Beta's inventory at LEO (pickup location)
        container_qty = _get_part_qty(app_client, beta["cookie"], "LEO", container_key)
        assert container_qty == pytest.approx(1.0, abs=0.1), \
            f"Expected 1 courier container at LEO, got {container_qty}"

        # Beta's balance should be unchanged (reward not paid yet)
        beta_bal_after = _get_balance(app_client, beta["cookie"])
        assert beta_bal_after == pytest.approx(beta_bal_before, rel=1e-4)

        # Clean up — issuer cancels
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_complete_delivers_items_and_pays_courier(self, app_client, corps):
        """Completing courier: container must be at destination. Items → destination, reward → courier."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        alpha_bal_before = _get_balance(app_client, alpha["cookie"])
        beta_bal_before = _get_balance(app_client, beta["cookie"])

        # Alpha creates courier: 300kg water from LEO to GEO, $30k reward
        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Deliver Water LEO→GEO",
            "description": "",
            "price": 0,
            "reward": 30_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 300, "volume_m3": 0.3, "mass_kg": 300, "type": "resource",
            }],
        })
        assert r.status_code == 200
        cid = r.json()["contract_id"]

        # Beta accepts
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Get the container ID
        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": beta["cookie"]})
        container_key = r.json()["contract"]["courier_container_id"]
        assert container_key is not None

        # Simulate transporting the container to the destination (GEO)
        _move_container_to_location(container_key, "GEO")

        # Now complete the delivery
        r = app_client.post(f"/api/contracts/{cid}/complete", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Contract is completed
        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": alpha["cookie"]})
        assert r.json()["contract"]["status"] == "completed"

        # Beta (courier) got $30k reward
        beta_bal_after = _get_balance(app_client, beta["cookie"])
        assert beta_bal_after == pytest.approx(beta_bal_before + 30_000, rel=1e-4)

        # Alpha got items at GEO (destination)
        alpha_water_geo = _get_resource_qty(app_client, alpha["cookie"], "GEO", "water")
        assert alpha_water_geo >= 300 - 0.1

    def test_complete_fails_if_container_not_at_destination(self, app_client, corps):
        """Cannot complete courier if cargo container is still at the origin."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Container Location Check",
            "description": "",
            "price": 0,
            "reward": 10_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 100, "volume_m3": 0.1, "mass_kg": 100, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta accepts — container is at LEO
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Try to complete WITHOUT moving the container — should fail
        r = app_client.post(f"/api/contracts/{cid}/complete", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 400
        assert "not at the destination" in r.json()["detail"].lower()

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_cancel_courier_in_progress_returns_all(self, app_client, corps):
        """Issuer cancelling in-progress courier returns items + reward and removes container."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        alpha_water_before = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        alpha_bal_before = _get_balance(app_client, alpha["cookie"])

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Cancel Courier Test",
            "description": "",
            "price": 0,
            "reward": 15_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 200, "volume_m3": 0.2, "mass_kg": 200, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta accepts (creates container)
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Get container key
        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": beta["cookie"]})
        container_key = r.json()["contract"]["courier_container_id"]

        # Alpha cancels
        r = app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200

        # Container should be gone from Beta's inventory
        container_qty = _get_part_qty(app_client, beta["cookie"], "LEO", container_key)
        assert container_qty == 0.0, f"Container should be removed, but found qty={container_qty}"

        # Items returned to Alpha at LEO
        alpha_water_after = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        assert alpha_water_after == pytest.approx(alpha_water_before, abs=0.1)

        # Reward returned
        alpha_bal_after = _get_balance(app_client, alpha["cookie"])
        assert alpha_bal_after == pytest.approx(alpha_bal_before, rel=1e-4)

    def test_courier_reject_by_assignee(self, app_client, corps):
        """Assigned courier rejects — items+reward return to issuer, container removed."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        alpha_water_before = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        alpha_bal_before = _get_balance(app_client, alpha["cookie"])

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Reject Courier Test",
            "description": "",
            "price": 0,
            "reward": 10_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 150, "volume_m3": 0.15, "mass_kg": 150, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta accepts (creates container)
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Get container key
        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": beta["cookie"]})
        container_key = r.json()["contract"]["courier_container_id"]
        assert container_key is not None

        # Beta rejects
        r = app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200
        assert r.json()["new_status"] == "rejected"

        # Container removed
        container_qty = _get_part_qty(app_client, beta["cookie"], "LEO", container_key)
        assert container_qty == 0.0

        # Items + money back to Alpha
        alpha_water_after = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        alpha_bal_after = _get_balance(app_client, alpha["cookie"])
        assert alpha_water_after == pytest.approx(alpha_water_before, abs=0.1)
        assert alpha_bal_after == pytest.approx(alpha_bal_before, rel=1e-4)

    def test_complete_container_on_ship_at_destination(self, app_client, corps):
        """Courier loads container onto a ship, flies to destination, completes delivery."""
        alpha = corps["alpha"]
        beta = corps["beta"]
        beta_bal_before = _get_balance(app_client, beta["cookie"])

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Ship Delivery Test",
            "description": "",
            "price": 0,
            "reward": 20_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 200, "volume_m3": 0.2, "mass_kg": 200, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta accepts — container created at LEO
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": beta["cookie"]})
        container_key = r.json()["contract"]["courier_container_id"]

        # Load container onto a ship at LEO, then fly ship to GEO
        ship_id = _load_container_onto_ship(container_key, "LEO", beta["corp_id"])
        _move_ship_to_location(ship_id, "GEO")

        # Complete delivery — container is on ship at GEO
        r = app_client.post(f"/api/contracts/{cid}/complete", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Contract completed, courier paid
        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": alpha["cookie"]})
        assert r.json()["contract"]["status"] == "completed"

        beta_bal_after = _get_balance(app_client, beta["cookie"])
        assert beta_bal_after == pytest.approx(beta_bal_before + 20_000, rel=1e-4)

        # Items delivered to GEO for Alpha
        alpha_water_geo = _get_resource_qty(app_client, alpha["cookie"], "GEO", "water")
        assert alpha_water_geo >= 200 - 0.1

    def test_complete_fails_container_on_ship_wrong_location(self, app_client, corps):
        """Cannot complete when container is on a ship that hasn't reached destination."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Ship Wrong Location Test",
            "description": "",
            "price": 0,
            "reward": 5_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 50, "volume_m3": 0.05, "mass_kg": 50, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": beta["cookie"]})
        container_key = r.json()["contract"]["courier_container_id"]

        # Load onto ship at LEO — do NOT move ship to GEO
        _load_container_onto_ship(container_key, "LEO", beta["corp_id"])

        # Try to complete — should fail (container on ship at LEO, not GEO)
        r = app_client.post(f"/api/contracts/{cid}/complete", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 400
        assert "not at the destination" in r.json()["detail"].lower()

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_cancel_removes_container_from_ship(self, app_client, corps):
        """Cancelling courier removes the container even when loaded on a ship."""
        alpha = corps["alpha"]
        beta = corps["beta"]
        alpha_water_before = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        alpha_bal_before = _get_balance(app_client, alpha["cookie"])

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Cancel Ship Container Test",
            "description": "",
            "price": 0,
            "reward": 8_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 100, "volume_m3": 0.1, "mass_kg": 100, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": beta["cookie"]})
        container_key = r.json()["contract"]["courier_container_id"]

        # Load container onto a ship
        ship_id = _load_container_onto_ship(container_key, "LEO", beta["corp_id"])

        # Alpha cancels — container must be removed from the ship
        r = app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200

        # Verify container is gone from ship parts_json
        from db import connect_db
        conn = connect_db()
        try:
            ship_row = conn.execute("SELECT parts_json FROM ships WHERE id = ?", (ship_id,)).fetchone()
            if ship_row:
                parts = json.loads(ship_row["parts_json"] or "[]")
                container_parts = [p for p in parts if isinstance(p, dict) and p.get("stack_key") == container_key]
                assert len(container_parts) == 0, "Container should be removed from ship parts"
        finally:
            conn.close()

        # Items + money returned to Alpha
        alpha_water_after = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        alpha_bal_after = _get_balance(app_client, alpha["cookie"])
        assert alpha_water_after == pytest.approx(alpha_water_before, abs=0.1)
        assert alpha_bal_after == pytest.approx(alpha_bal_before, rel=1e-4)


# ======================== AUCTION TESTS ====================================


class TestAuctionLifecycle:
    """Full lifecycle for auction contracts."""

    def test_create_auction_escrows_items(self, app_client, corps):
        """Creating an auction escrows items from the seller."""
        alpha = corps["alpha"]
        ceramics_before = _get_resource_qty(app_client, alpha["cookie"], "LEO", "advanced_ceramics")

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "auction",
            "title": "Auction 500kg Ceramics",
            "description": "Starting bid $10k, buyout $50k",
            "price": 10_000,
            "buyout_price": 50_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_days": 180,
            "items": [{
                "stack_key": "advanced_ceramics", "item_id": "advanced_ceramics",
                "name": "Advanced Ceramics",
                "quantity": 500, "volume_m3": 0.18, "mass_kg": 500, "type": "resource",
            }],
        })
        assert r.status_code == 200, r.text
        cid = r.json()["contract_id"]

        ceramics_after = _get_resource_qty(app_client, alpha["cookie"], "LEO", "advanced_ceramics")
        assert ceramics_after == pytest.approx(ceramics_before - 500, abs=0.1)

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_bid_escrows_money_from_bidder(self, app_client, corps):
        """Bidding on an auction: bid money escrowed from bidder."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "auction",
            "title": "Bid Escrow Test",
            "description": "",
            "price": 5_000,
            "buyout_price": 100_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_days": 180,
            "items": [{
                "stack_key": "advanced_ceramics", "item_id": "advanced_ceramics",
                "name": "Advanced Ceramics",
                "quantity": 100, "volume_m3": 0.036, "mass_kg": 100, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        beta_bal_before = _get_balance(app_client, beta["cookie"])

        # Beta bids $8,000
        r = app_client.post(f"/api/contracts/{cid}/bid", cookies={"session_token": beta["cookie"]},
                           json={"bid_amount": 8_000})
        assert r.status_code == 200
        assert r.json()["is_buyout"] is False

        beta_bal_after = _get_balance(app_client, beta["cookie"])
        assert beta_bal_after == pytest.approx(beta_bal_before - 8_000, rel=1e-4)

        # Clean up — cancel returns items to Alpha, bid money to Beta
        r = app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200

        beta_bal_refund = _get_balance(app_client, beta["cookie"])
        assert beta_bal_refund == pytest.approx(beta_bal_before, rel=1e-4)

    def test_outbid_refunds_previous_bidder(self, app_client, corps):
        """When outbid, previous bidder gets their money back."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        # We need a third bidder — register one
        gamma_id, gamma_cookie = _register_corp(app_client, f"Gamma-{secrets.token_hex(4)}", color="#00ff00")
        _seed_resource(app_client, gamma_cookie, "LEO", "water", 1000)
        # No need for inventory, just money

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "auction",
            "title": "Outbid Test",
            "description": "",
            "price": 1_000,
            "buyout_price": 500_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_days": 180,
            "items": [{
                "stack_key": "advanced_ceramics", "item_id": "advanced_ceramics",
                "name": "Advanced Ceramics",
                "quantity": 50, "volume_m3": 0.018, "mass_kg": 50, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        beta_bal_before = _get_balance(app_client, beta["cookie"])
        gamma_bal_before = _get_balance(app_client, gamma_cookie)

        # Beta bids $5,000
        r = app_client.post(f"/api/contracts/{cid}/bid", cookies={"session_token": beta["cookie"]},
                           json={"bid_amount": 5_000})
        assert r.status_code == 200

        beta_bal_after_bid = _get_balance(app_client, beta["cookie"])
        assert beta_bal_after_bid == pytest.approx(beta_bal_before - 5_000, rel=1e-4)

        # Gamma outbids at $10,000 — Beta should get $5,000 back
        r = app_client.post(f"/api/contracts/{cid}/bid", cookies={"session_token": gamma_cookie},
                           json={"bid_amount": 10_000})
        assert r.status_code == 200

        beta_bal_after_outbid = _get_balance(app_client, beta["cookie"])
        assert beta_bal_after_outbid == pytest.approx(beta_bal_before, rel=1e-4), \
            "Previous bidder should be refunded when outbid"

        gamma_bal_after = _get_balance(app_client, gamma_cookie)
        assert gamma_bal_after == pytest.approx(gamma_bal_before - 10_000, rel=1e-4)

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_buyout_instant_completion(self, app_client, corps):
        """Buyout bid immediately completes: money → seller, items → buyer."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        alpha_bal_before = _get_balance(app_client, alpha["cookie"])
        beta_bal_before = _get_balance(app_client, beta["cookie"])
        beta_ceramics_before = _get_resource_qty(app_client, beta["cookie"], "LEO", "advanced_ceramics")

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "auction",
            "title": "Buyout Test",
            "description": "",
            "price": 5_000,
            "buyout_price": 20_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_days": 180,
            "items": [{
                "stack_key": "advanced_ceramics", "item_id": "advanced_ceramics",
                "name": "Advanced Ceramics",
                "quantity": 200, "volume_m3": 0.072, "mass_kg": 200, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta buys out at $20k
        r = app_client.post(f"/api/contracts/{cid}/bid", cookies={"session_token": beta["cookie"]},
                           json={"bid_amount": 20_000})
        assert r.status_code == 200
        data = r.json()
        assert data["is_buyout"] is True

        # Contract completed
        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": alpha["cookie"]})
        assert r.json()["contract"]["status"] == "completed"

        # Money: Alpha gained $20k, Beta lost $20k
        alpha_bal_after = _get_balance(app_client, alpha["cookie"])
        beta_bal_after = _get_balance(app_client, beta["cookie"])
        assert alpha_bal_after == pytest.approx(alpha_bal_before + 20_000, rel=1e-4)
        assert beta_bal_after == pytest.approx(beta_bal_before - 20_000, rel=1e-4)

        # Items: Beta got 200kg ceramics
        beta_ceramics_after = _get_resource_qty(app_client, beta["cookie"], "LEO", "advanced_ceramics")
        assert beta_ceramics_after == pytest.approx(beta_ceramics_before + 200, abs=0.1)

    def test_bid_below_minimum_rejected(self, app_client, corps):
        """Bids below the starting price / current bid are rejected."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "auction",
            "title": "Low Bid Test",
            "description": "",
            "price": 10_000,
            "buyout_price": 100_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_days": 180,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 100, "volume_m3": 0.1, "mass_kg": 100, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Bid below starting price
        r = app_client.post(f"/api/contracts/{cid}/bid", cookies={"session_token": beta["cookie"]},
                           json={"bid_amount": 5_000})
        assert r.status_code == 400
        assert "at least" in r.json()["detail"].lower()

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_cannot_bid_on_own_auction(self, app_client, corps):
        """Seller cannot bid on their own auction."""
        alpha = corps["alpha"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "auction",
            "title": "Self-Bid Test",
            "description": "",
            "price": 1_000,
            "buyout_price": 50_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_days": 180,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 50, "volume_m3": 0.05, "mass_kg": 50, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.post(f"/api/contracts/{cid}/bid", cookies={"session_token": alpha["cookie"]},
                           json={"bid_amount": 5_000})
        assert r.status_code == 400
        assert "own auction" in r.json()["detail"].lower()

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_cancel_auction_with_bidder_refunds_bidder(self, app_client, corps):
        """Cancelling auction with active bid: items → seller, bid money → bidder."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        alpha_ceramics_before = _get_resource_qty(app_client, alpha["cookie"], "LEO", "advanced_ceramics")
        beta_bal_before = _get_balance(app_client, beta["cookie"])

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "auction",
            "title": "Cancel Auction Refund Test",
            "description": "",
            "price": 5_000,
            "buyout_price": 200_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_days": 180,
            "items": [{
                "stack_key": "advanced_ceramics", "item_id": "advanced_ceramics",
                "name": "Advanced Ceramics",
                "quantity": 100, "volume_m3": 0.036, "mass_kg": 100, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta bids $15,000
        r = app_client.post(f"/api/contracts/{cid}/bid", cookies={"session_token": beta["cookie"]},
                           json={"bid_amount": 15_000})
        assert r.status_code == 200

        # Alpha cancels
        r = app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200

        # Alpha gets items back
        alpha_ceramics_after = _get_resource_qty(app_client, alpha["cookie"], "LEO", "advanced_ceramics")
        assert alpha_ceramics_after == pytest.approx(alpha_ceramics_before, abs=0.1)

        # Beta gets bid money back
        beta_bal_after = _get_balance(app_client, beta["cookie"])
        assert beta_bal_after == pytest.approx(beta_bal_before, rel=1e-4)


# ======================== STATUS TRANSITION TESTS ==========================

class TestStatusTransitions:
    """Verify invalid state transitions are properly rejected."""

    def test_cannot_accept_completed_contract(self, app_client, corps):
        """Once completed, contract can't be accepted again."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Double Accept Test",
            "description": "",
            "price": 1_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 10, "volume_m3": 0.01, "mass_kg": 10, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Accept
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Try to accept again (status is now 'completed')
        gamma_id, gamma_cookie = _register_corp(app_client, f"Delta-{secrets.token_hex(4)}", color="#aaaaaa")
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": gamma_cookie})
        assert r.status_code == 400

    def test_cannot_complete_outstanding_contract(self, app_client, corps):
        """Cannot mark an outstanding contract as completed (it must be in_progress)."""
        alpha = corps["alpha"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Premature Complete Test",
            "description": "",
            "price": 0,
            "reward": 5_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 10, "volume_m3": 0.01, "mass_kg": 10, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Try to complete while still outstanding  
        r = app_client.post(f"/api/contracts/{cid}/complete", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 400
        assert "not in progress" in r.json()["detail"].lower()

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_cannot_reject_completed_contract(self, app_client, corps):
        """Completed contracts can't be rejected."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Reject Completed Test",
            "description": "",
            "price": 500,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 5, "volume_m3": 0.005, "mass_kg": 5, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Accept (completes for item_exchange)
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Try to reject
        r = app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 400

    def test_third_party_cannot_reject(self, app_client, corps):
        """A corp not involved in the contract can't reject it."""
        alpha = corps["alpha"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "3rd Party Reject Test",
            "description": "",
            "price": 1_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 10, "volume_m3": 0.01, "mass_kg": 10, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        gamma_id, gamma_cookie = _register_corp(app_client, f"Rogue-{secrets.token_hex(4)}", color="#999999")
        r = app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": gamma_cookie})
        assert r.status_code == 403

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_bid_on_non_auction_rejected(self, app_client, corps):
        """Cannot bid on an item_exchange contract."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Non-Auction Bid Test",
            "description": "",
            "price": 5_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 10, "volume_m3": 0.01, "mass_kg": 10, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.post(f"/api/contracts/{cid}/bid", cookies={"session_token": beta["cookie"]},
                           json={"bid_amount": 5_000})
        assert r.status_code == 400
        assert "auction" in r.json()["detail"].lower()

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})


# ======================== SEARCH & LISTING TESTS ===========================

class TestContractSearchAndListing:
    """Verify search, my contracts, incoming endpoints work correctly."""

    def test_search_finds_outstanding_public(self, app_client, corps):
        """Public outstanding contracts appear in search."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Searchable Water",
            "description": "",
            "price": 7_777,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 50, "volume_m3": 0.05, "mass_kg": 50, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Search as Beta
        r = app_client.get("/api/contracts/search?search_type=buy_sell", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200
        contracts = r.json()["contracts"]
        found = [c for c in contracts if c["id"] == cid]
        assert len(found) == 1
        assert found[0]["price"] == 7_777

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_my_contracts_shows_issued(self, app_client, corps):
        """My Contracts tab shows contracts the corp issued."""
        alpha = corps["alpha"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "My Contracts Test",
            "description": "",
            "price": 3_333,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 10, "volume_m3": 0.01, "mass_kg": 10, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.get("/api/contracts/my?action=issued_by&status=outstanding",
                          cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()["contracts"]]
        assert cid in ids

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_get_contract_details(self, app_client, corps):
        """GET /api/contracts/{id} returns full contract details with items."""
        alpha = corps["alpha"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Detail View Test",
            "description": "A detailed description",
            "price": 42_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 100, "volume_m3": 0.1, "mass_kg": 100, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200
        c = r.json()["contract"]
        assert c["title"] == "Detail View Test"
        assert c["price"] == 42_000
        assert c["status"] == "outstanding"
        assert len(c["items"]) == 1
        assert c["items"][0]["name"] == "Water"
        assert c["items"][0]["quantity"] == 100

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_nonexistent_contract_returns_404(self, app_client, corps):
        alpha = corps["alpha"]
        r = app_client.get("/api/contracts/nonexistent-uuid", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 404


# ======================== MULTI-ITEM & MIXED TYPE TESTS ====================

class TestMultiItemContracts:
    """Contracts with multiple items and mixed types."""

    def test_multi_resource_escrow_and_release(self, app_client, corps):
        """Contract with multiple resource types escrows all, cancel returns all."""
        alpha = corps["alpha"]
        water_before = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        ceramics_before = _get_resource_qty(app_client, alpha["cookie"], "LEO", "advanced_ceramics")

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Multi Resource Bundle",
            "description": "",
            "price": 100_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [
                {
                    "stack_key": "water", "item_id": "water", "name": "Water",
                    "quantity": 250, "volume_m3": 0.25, "mass_kg": 250, "type": "resource",
                },
                {
                    "stack_key": "advanced_ceramics", "item_id": "advanced_ceramics",
                    "name": "Advanced Ceramics",
                    "quantity": 100, "volume_m3": 0.036, "mass_kg": 100, "type": "resource",
                },
            ],
        })
        assert r.status_code == 200
        cid = r.json()["contract_id"]

        # Both escrowed
        water_mid = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        ceramics_mid = _get_resource_qty(app_client, alpha["cookie"], "LEO", "advanced_ceramics")
        assert water_mid == pytest.approx(water_before - 250, abs=0.1)
        assert ceramics_mid == pytest.approx(ceramics_before - 100, abs=0.1)

        # Cancel — both returned
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})
        water_after = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        ceramics_after = _get_resource_qty(app_client, alpha["cookie"], "LEO", "advanced_ceramics")
        assert water_after == pytest.approx(water_before, abs=0.1)
        assert ceramics_after == pytest.approx(ceramics_before, abs=0.1)

    def test_mixed_resource_and_part_contract(self, app_client, corps):
        """Contract with both resources and parts works correctly."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        # Look up the part stack_key from alpha's inventory
        r = app_client.get("/api/inventory/location/LEO", cookies={"session_token": alpha["cookie"]})
        parts = r.json().get("parts", [])
        thruster_stacks = [p for p in parts if "Test Thruster" in p.get("name", "")]
        assert thruster_stacks, "Alpha needs thruster parts"
        sk = thruster_stacks[0]["stack_key"]
        parts_before = float(thruster_stacks[0]["quantity"])
        water_before = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Mixed Bundle: Water + Thrusters",
            "description": "",
            "price": 500_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [
                {
                    "stack_key": "water", "item_id": "water", "name": "Water",
                    "quantity": 100, "volume_m3": 0.1, "mass_kg": 100, "type": "resource",
                },
                {
                    "stack_key": sk, "item_id": "test_thruster_mk1",
                    "name": "Test Thruster Mk1",
                    "quantity": 2, "volume_m3": 0, "mass_kg": 100, "type": "part",
                },
            ],
        })
        assert r.status_code == 200
        cid = r.json()["contract_id"]

        # Escrowed
        water_mid = _get_resource_qty(app_client, alpha["cookie"], "LEO", "water")
        parts_mid = _get_part_qty(app_client, alpha["cookie"], "LEO", sk)
        assert water_mid == pytest.approx(water_before - 100, abs=0.1)
        assert parts_mid == pytest.approx(parts_before - 2, abs=0.1)

        # Beta accepts — gets both
        beta_water_before = _get_resource_qty(app_client, beta["cookie"], "LEO", "water")
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        beta_water_after = _get_resource_qty(app_client, beta["cookie"], "LEO", "water")
        assert beta_water_after == pytest.approx(beta_water_before + 100, abs=0.1)
        # Beta should also have received the parts
        beta_parts = _get_part_qty(app_client, beta["cookie"], "LEO", sk)
        assert beta_parts >= 2.0 - 0.1


# ======================== PRIVATE CONTRACT TESTS ===========================

class TestPrivateContracts:
    """Private contract visibility and acceptance rules."""

    def test_private_contract_only_assignee_can_accept(self, app_client, corps):
        """Private contract targeted at Beta cannot be accepted by Gamma."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Private Deal for Beta",
            "description": "",
            "price": 5_000,
            "location_id": "LEO",
            "availability": "private",
            "assignee_org_id": beta["org_id"],
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 20, "volume_m3": 0.02, "mass_kg": 20, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Gamma cannot accept
        gamma_id, gamma_cookie = _register_corp(app_client, f"Eps-{secrets.token_hex(4)}", color="#111111")
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": gamma_cookie})
        assert r.status_code == 403

        # Beta CAN accept
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200


# ======================== ZERO-PRICE & EDGE CASE TESTS =====================

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_zero_price_item_exchange(self, app_client, corps):
        """Item exchange with price=0 (gift) works correctly."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        beta_water_before = _get_resource_qty(app_client, beta["cookie"], "LEO", "water")

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Free Water",
            "description": "Gift",
            "price": 0,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 100, "volume_m3": 0.1, "mass_kg": 100, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        beta_water_after = _get_resource_qty(app_client, beta["cookie"], "LEO", "water")
        assert beta_water_after == pytest.approx(beta_water_before + 100, abs=0.1)

    def test_courier_zero_reward(self, app_client, corps):
        """Courier with zero reward still escrows items and delivers."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Free Courier",
            "description": "",
            "price": 0,
            "reward": 0,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 50, "volume_m3": 0.05, "mass_kg": 50, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Move the container to the destination
        r2 = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": beta["cookie"]})
        container_key = r2.json()["contract"]["courier_container_id"]
        _move_container_to_location(container_key, "GEO")

        r = app_client.post(f"/api/contracts/{cid}/complete", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200

        # Items arrive at GEO
        alpha_water_geo = _get_resource_qty(app_client, alpha["cookie"], "GEO", "water")
        assert alpha_water_geo >= 50 - 0.1

    def test_invalid_contract_type_rejected(self, app_client, corps):
        """Invalid contract type is rejected."""
        alpha = corps["alpha"]
        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "pirate_heist",
            "title": "Bad Type",
            "description": "",
            "price": 0,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [],
        })
        assert r.status_code == 400

    def test_no_auth_returns_401(self, app_client):
        """Endpoints without auth cookie return 401."""
        r = app_client.get("/api/contracts/search")
        assert r.status_code == 401

        r = app_client.post("/api/contracts/create", json={"contract_type": "item_exchange"})
        assert r.status_code == 401

    def test_buyout_with_prior_bid_refunds_both(self, app_client, corps):
        """Buyout after a regular bid: previous bidder refunded, seller paid."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        gamma_id, gamma_cookie = _register_corp(app_client, f"Zeta-{secrets.token_hex(4)}", color="#abcdef")

        beta_bal_before = _get_balance(app_client, beta["cookie"])
        gamma_bal_before = _get_balance(app_client, gamma_cookie)
        alpha_bal_before = _get_balance(app_client, alpha["cookie"])

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "auction",
            "title": "Prior Bid Then Buyout",
            "description": "",
            "price": 1_000,
            "buyout_price": 50_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_days": 180,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 100, "volume_m3": 0.1, "mass_kg": 100, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta bids $5,000
        r = app_client.post(f"/api/contracts/{cid}/bid", cookies={"session_token": beta["cookie"]},
                           json={"bid_amount": 5_000})
        assert r.status_code == 200

        # Gamma buys out at $50,000 — Beta gets $5,000 back
        r = app_client.post(f"/api/contracts/{cid}/bid", cookies={"session_token": gamma_cookie},
                           json={"bid_amount": 50_000})
        assert r.status_code == 200
        assert r.json()["is_buyout"] is True

        # Beta refunded
        beta_bal_after = _get_balance(app_client, beta["cookie"])
        assert beta_bal_after == pytest.approx(beta_bal_before, rel=1e-4)

        # Alpha gets $50k
        alpha_bal_after = _get_balance(app_client, alpha["cookie"])
        assert alpha_bal_after == pytest.approx(alpha_bal_before + 50_000, rel=1e-4)

        # Gamma paid $50k
        gamma_bal_after = _get_balance(app_client, gamma_cookie)
        assert gamma_bal_after == pytest.approx(gamma_bal_before - 50_000, rel=1e-4)


# ======================== INCOMING ENDPOINT TESTS ==========================

class TestIncomingEndpoint:
    """GET /api/contracts/incoming — contracts assigned to the current user."""

    def test_incoming_shows_accepted_courier(self, app_client, corps):
        """After Beta accepts a courier, it appears in Beta's incoming list."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Incoming Courier Test",
            "description": "",
            "price": 0,
            "reward": 5_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 50, "volume_m3": 0.05, "mass_kg": 50, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta accepts
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Incoming should list this contract for Beta
        r = app_client.get("/api/contracts/incoming", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()["contracts"]]
        assert cid in ids

        # Should NOT appear in Alpha's incoming
        r = app_client.get("/api/contracts/incoming", cookies={"session_token": alpha["cookie"]})
        ids_alpha = [c["id"] for c in r.json()["contracts"]]
        assert cid not in ids_alpha

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_incoming_excludes_completed(self, app_client, corps):
        """Completed contracts don't appear in incoming (only outstanding/in_progress)."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Incoming Completed Test",
            "description": "",
            "price": 1_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 10, "volume_m3": 0.01, "mass_kg": 10, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta accepts (item_exchange → immediately completed)
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Should NOT appear in Beta's incoming (it's completed, not in_progress)
        r = app_client.get("/api/contracts/incoming", cookies={"session_token": beta["cookie"]})
        ids = [c["id"] for c in r.json()["contracts"]]
        assert cid not in ids


# ======================== MY-LOCATIONS ENDPOINT TESTS ======================

class TestMyLocationsEndpoint:
    """GET /api/contracts/my-locations — locations where corp has inventory."""

    def test_my_locations_returns_seeded_location(self, app_client, corps):
        """After seeding inventory at LEO, my-locations includes LEO."""
        alpha = corps["alpha"]

        r = app_client.get("/api/contracts/my-locations", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200
        locs = r.json()["locations"]
        loc_ids = [l["id"] for l in locs]
        assert "LEO" in loc_ids

    def test_my_locations_has_item_count(self, app_client, corps):
        """Each location entry includes a non-zero item_count."""
        alpha = corps["alpha"]

        r = app_client.get("/api/contracts/my-locations", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200
        locs = r.json()["locations"]
        leo = [l for l in locs if l["id"] == "LEO"]
        assert leo, "LEO not found"
        assert leo[0]["item_count"] > 0


# ======================== ZONES ENDPOINT TESTS =============================

class TestZonesEndpoint:
    """GET /api/contracts/zones — heliocentric zone data for courier filtering."""

    def test_zones_returns_mega_zones(self, app_client, corps):
        """Zones endpoint returns the 7 mega-zones."""
        alpha = corps["alpha"]

        r = app_client.get("/api/contracts/zones", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200
        zones = r.json()["zones"]
        assert len(zones) >= 7
        zone_ids = [z["id"] for z in zones]
        assert "earth" in zone_ids
        assert "mars" in zone_ids
        assert "jupiter" in zone_ids

    def test_zones_have_required_fields(self, app_client, corps):
        """Each zone has id, name, symbol, location_count."""
        alpha = corps["alpha"]

        r = app_client.get("/api/contracts/zones", cookies={"session_token": alpha["cookie"]})
        zones = r.json()["zones"]
        for z in zones:
            assert "id" in z
            assert "name" in z
            assert "symbol" in z
            assert "location_count" in z


# ======================== AUCTION DIRECT ACCEPT TESTS ======================

class TestAuctionDirectAccept:
    """Accept an auction at starting price without placing a bid first."""

    def test_direct_accept_auction_at_starting_price(self, app_client, corps):
        """Accepting an auction directly pays starting price and transfers items."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        alpha_bal_before = _get_balance(app_client, alpha["cookie"])
        beta_bal_before = _get_balance(app_client, beta["cookie"])
        beta_ceramics_before = _get_resource_qty(app_client, beta["cookie"], "LEO", "advanced_ceramics")

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "auction",
            "title": "Direct Accept Auction",
            "description": "",
            "price": 15_000,
            "buyout_price": 100_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_days": 180,
            "items": [{
                "stack_key": "advanced_ceramics", "item_id": "advanced_ceramics",
                "name": "Advanced Ceramics",
                "quantity": 150, "volume_m3": 0.054, "mass_kg": 150, "type": "resource",
            }],
        })
        assert r.status_code == 200
        cid = r.json()["contract_id"]

        # Beta directly accepts (no bid first)
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Contract is completed
        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": beta["cookie"]})
        assert r.json()["contract"]["status"] == "completed"

        # Money: Alpha gained $15k (starting price), Beta lost $15k
        alpha_bal_after = _get_balance(app_client, alpha["cookie"])
        beta_bal_after = _get_balance(app_client, beta["cookie"])
        assert alpha_bal_after == pytest.approx(alpha_bal_before + 15_000, rel=1e-4)
        assert beta_bal_after == pytest.approx(beta_bal_before - 15_000, rel=1e-4)

        # Items: Beta received 150kg ceramics
        beta_ceramics_after = _get_resource_qty(app_client, beta["cookie"], "LEO", "advanced_ceramics")
        assert beta_ceramics_after == pytest.approx(beta_ceramics_before + 150, abs=0.1)

    def test_direct_accept_auction_zero_price(self, app_client, corps):
        """Auction with starting price 0 can be directly accepted as a free claim."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "auction",
            "title": "Free Auction",
            "description": "",
            "price": 0,
            "buyout_price": 50_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_days": 180,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 30, "volume_m3": 0.03, "mass_kg": 30, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        beta_water_before = _get_resource_qty(app_client, beta["cookie"], "LEO", "water")

        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        beta_water_after = _get_resource_qty(app_client, beta["cookie"], "LEO", "water")
        assert beta_water_after == pytest.approx(beta_water_before + 30, abs=0.1)


# ======================== COURIER COMPLETE BY ASSIGNEE =====================

class TestCourierCompleteByAssignee:
    """Assignee (courier) completing the delivery, not just the issuer."""

    def test_assignee_completes_courier(self, app_client, corps):
        """Beta (courier/assignee) can mark courier as completed after delivering container."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        beta_bal_before = _get_balance(app_client, beta["cookie"])

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Assignee Complete Test",
            "description": "",
            "price": 0,
            "reward": 25_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 200, "volume_m3": 0.2, "mass_kg": 200, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta accepts
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Move the container to GEO (simulate transport)
        r2 = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": beta["cookie"]})
        container_key = r2.json()["contract"]["courier_container_id"]
        _move_container_to_location(container_key, "GEO")

        # Beta (assignee) completes instead of Alpha (issuer)
        r = app_client.post(f"/api/contracts/{cid}/complete", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Contract completed
        r = app_client.get(f"/api/contracts/{cid}", cookies={"session_token": alpha["cookie"]})
        assert r.json()["contract"]["status"] == "completed"

        # Beta got reward
        beta_bal_after = _get_balance(app_client, beta["cookie"])
        assert beta_bal_after == pytest.approx(beta_bal_before + 25_000, rel=1e-4)

        # Items delivered to GEO for Alpha
        alpha_water_geo = _get_resource_qty(app_client, alpha["cookie"], "GEO", "water")
        assert alpha_water_geo >= 200 - 0.1

    def test_third_party_cannot_complete_courier(self, app_client, corps):
        """A corp not involved in the contract cannot complete it."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Third Party Complete Test",
            "description": "",
            "price": 0,
            "reward": 5_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 20, "volume_m3": 0.02, "mass_kg": 20, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta accepts
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Gamma (unrelated) tries to complete
        gamma_id, gamma_cookie = _register_corp(app_client, f"Rando-{secrets.token_hex(4)}", color="#777777")
        r = app_client.post(f"/api/contracts/{cid}/complete", cookies={"session_token": gamma_cookie})
        assert r.status_code == 403

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})


# ======================== SEARCH FILTER TESTS ==============================

class TestSearchFilters:
    """Verify search endpoint filters: courier type, price, sort, issuer, zones."""

    def test_search_courier_type(self, app_client, corps):
        """search_type=courier only returns courier contracts."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        # Create one item_exchange and one courier
        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "IE for filter test",
            "description": "",
            "price": 1_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 5, "volume_m3": 0.005, "mass_kg": 5, "type": "resource",
            }],
        })
        ie_cid = r.json()["contract_id"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Courier for filter test",
            "description": "",
            "price": 0,
            "reward": 5_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 5, "volume_m3": 0.005, "mass_kg": 5, "type": "resource",
            }],
        })
        cou_cid = r.json()["contract_id"]

        # Search courier only
        r = app_client.get("/api/contracts/search?search_type=courier",
                          cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()["contracts"]]
        assert cou_cid in ids
        assert ie_cid not in ids

        # Clean up
        app_client.post(f"/api/contracts/{ie_cid}/reject", cookies={"session_token": alpha["cookie"]})
        app_client.post(f"/api/contracts/{cou_cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_search_auction_type(self, app_client, corps):
        """contract_type=auction filter returns only auctions."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "auction",
            "title": "Auction filter test",
            "description": "",
            "price": 1_000,
            "buyout_price": 50_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_days": 180,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 5, "volume_m3": 0.005, "mass_kg": 5, "type": "resource",
            }],
        })
        auc_cid = r.json()["contract_id"]

        r = app_client.get("/api/contracts/search?search_type=buy_sell&contract_type=auction",
                          cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200
        types = set(c["contract_type"] for c in r.json()["contracts"])
        # All returned contracts should be auctions
        if types:
            assert types == {"auction"}
        # Our auction should be in the results
        ids = [c["id"] for c in r.json()["contracts"]]
        assert auc_cid in ids

        # Clean up
        app_client.post(f"/api/contracts/{auc_cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_search_sort_price_desc(self, app_client, corps):
        """Sort by price descending returns highest-priced first."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        # Create two contracts with different prices
        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Cheap",
            "description": "",
            "price": 100,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 1, "volume_m3": 0.001, "mass_kg": 1, "type": "resource",
            }],
        })
        cheap_cid = r.json()["contract_id"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Expensive",
            "description": "",
            "price": 999_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 1, "volume_m3": 0.001, "mass_kg": 1, "type": "resource",
            }],
        })
        exp_cid = r.json()["contract_id"]

        r = app_client.get("/api/contracts/search?search_type=buy_sell&sort=price_desc",
                          cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200
        contracts = r.json()["contracts"]
        prices = [c["price"] for c in contracts]
        # Should be non-ascending
        for i in range(len(prices) - 1):
            assert prices[i] >= prices[i + 1], \
                f"price_desc violated: {prices[i]} < {prices[i+1]}"

        # Clean up
        app_client.post(f"/api/contracts/{cheap_cid}/reject", cookies={"session_token": alpha["cookie"]})
        app_client.post(f"/api/contracts/{exp_cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_search_location_filter(self, app_client, corps):
        """Location filter only returns contracts at the specified location."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "LEO contract",
            "description": "",
            "price": 1_000,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 5, "volume_m3": 0.005, "mass_kg": 5, "type": "resource",
            }],
        })
        leo_cid = r.json()["contract_id"]

        # Search filtered to LEO
        r = app_client.get("/api/contracts/search?search_type=buy_sell&location=LEO",
                          cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()["contracts"]]
        assert leo_cid in ids
        # All returned contracts should be at LEO
        for c in r.json()["contracts"]:
            assert c["location_id"] == "LEO"

        # Search filtered to GEO should NOT include this contract
        r = app_client.get("/api/contracts/search?search_type=buy_sell&location=GEO",
                          cookies={"session_token": beta["cookie"]})
        ids_geo = [c["id"] for c in r.json()["contracts"]]
        assert leo_cid not in ids_geo

        # Clean up
        app_client.post(f"/api/contracts/{leo_cid}/reject", cookies={"session_token": alpha["cookie"]})


# ======================== MY CONTRACTS FILTER TESTS ========================

class TestMyContractsFilters:
    """/api/contracts/my with different action and status filters."""

    def test_my_issued_to_shows_accepted(self, app_client, corps):
        """action=issued_to shows contracts assigned to the current org."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        # Create courier, Beta accepts → contract assigned to Beta
        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "Issued-To Filter Test",
            "description": "",
            "price": 0,
            "reward": 3_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 10, "volume_m3": 0.01, "mass_kg": 10, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Beta queries "issued_to" + "in_progress" (courier type)
        r = app_client.get("/api/contracts/my?action=issued_to&status=in_progress&type=courier",
                          cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()["contracts"]]
        assert cid in ids

        # Alpha should NOT see it with "issued_to"
        r = app_client.get("/api/contracts/my?action=issued_to&status=in_progress&type=courier",
                          cookies={"session_token": alpha["cookie"]})
        ids_alpha = [c["id"] for c in r.json()["contracts"]]
        assert cid not in ids_alpha

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_my_issued_to_by_shows_both(self, app_client, corps):
        """action=issued_to_by shows contracts the org issued OR is assigned to."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        # Alpha creates a courier Beta accepts
        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "courier",
            "title": "To-By Filter Test",
            "description": "",
            "price": 0,
            "reward": 2_000,
            "location_id": "LEO",
            "destination_id": "GEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 10, "volume_m3": 0.01, "mass_kg": 10, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Both Alpha (issuer) and Beta (assignee) should see it with issued_to_by
        r = app_client.get("/api/contracts/my?action=issued_to_by&status=in_progress&type=courier",
                          cookies={"session_token": alpha["cookie"]})
        ids_alpha = [c["id"] for c in r.json()["contracts"]]
        assert cid in ids_alpha

        r = app_client.get("/api/contracts/my?action=issued_to_by&status=in_progress&type=courier",
                          cookies={"session_token": beta["cookie"]})
        ids_beta = [c["id"] for c in r.json()["contracts"]]
        assert cid in ids_beta

        # Clean up
        app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})

    def test_my_completed_status_filter(self, app_client, corps):
        """status=completed shows completed contracts."""
        alpha = corps["alpha"]
        beta = corps["beta"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Completed Filter Test",
            "description": "",
            "price": 500,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 168,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 5, "volume_m3": 0.005, "mass_kg": 5, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Beta accepts → completed
        r = app_client.post(f"/api/contracts/{cid}/accept", cookies={"session_token": beta["cookie"]})
        assert r.status_code == 200

        # Should appear under status=completed
        r = app_client.get("/api/contracts/my?action=issued_by&status=completed",
                          cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()["contracts"]]
        assert cid in ids

        # Should NOT appear under status=outstanding
        r = app_client.get("/api/contracts/my?action=issued_by&status=outstanding",
                          cookies={"session_token": alpha["cookie"]})
        ids_out = [c["id"] for c in r.json()["contracts"]]
        assert cid not in ids_out

    def test_my_rejected_status_includes_cancelled(self, app_client, corps):
        """status=rejected shows both rejected and cancelled contracts."""
        alpha = corps["alpha"]

        r = app_client.post("/api/contracts/create", cookies={"session_token": alpha["cookie"]}, json={
            "contract_type": "item_exchange",
            "title": "Cancel Filter Test",
            "description": "",
            "price": 500,
            "location_id": "LEO",
            "availability": "public",
            "expiry_hours": 24,
            "items": [{
                "stack_key": "water", "item_id": "water", "name": "Water",
                "quantity": 5, "volume_m3": 0.005, "mass_kg": 5, "type": "resource",
            }],
        })
        cid = r.json()["contract_id"]

        # Cancel it
        r = app_client.post(f"/api/contracts/{cid}/reject", cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200
        assert r.json()["new_status"] == "cancelled"

        # Should appear under status=rejected (which includes cancelled)
        r = app_client.get("/api/contracts/my?action=issued_by&status=rejected",
                          cookies={"session_token": alpha["cookie"]})
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()["contracts"]]
        assert cid in ids
