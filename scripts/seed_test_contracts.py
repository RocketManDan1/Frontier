#!/usr/bin/env python3
"""
Seed script: generate sample corporations and a spread of Water contracts.

Usage (from host):
  sudo docker compose exec frontier-dev python /app/scripts/seed_test_contracts.py

Or from repo root:
  sudo docker compose exec frontier-dev python scripts/seed_test_contracts.py

What it creates:
  - 5 test corporations (Helios Mining, Artemis Logistics, etc.) with orgs + $1B balance
  - ~15 contracts across all 3 types (auction, courier, item_exchange)
  - Uses Water as the commodity for all contracts
  - Contracts are spread across LEO, GEO, LLO, L1
"""

import json
import os
import sys
import time
import uuid

# Ensure the app root is on the path so we can import project modules
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from db import connect_db
from auth_service import hash_password
import org_service
from sim_service import game_now_s

# ── Configuration ──────────────────────────────────────────────────────────────

CORPS = [
    {"name": "Helios Mining",       "color": "#e8a735", "password": "test123"},
    {"name": "Artemis Logistics",   "color": "#3a9edb", "password": "test123"},
    {"name": "Orbital Dynamics",    "color": "#59e860", "password": "test123"},
    {"name": "Nova Industries",     "color": "#d94444", "password": "test123"},
    {"name": "Lunar Transit Corp",  "color": "#c57edb", "password": "test123"},
]

LOCATIONS = ["LEO", "GEO", "LLO", "L1"]
WATER_ITEM = {"id": "water", "name": "Water", "category": "fuel"}

DAY = 86400  # seconds


def create_corp(conn, name, color, password):
    """Create a corporation + org, return (corp_id, org_id)."""
    existing = conn.execute(
        "SELECT id, org_id FROM corporations WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if existing:
        print(f"  [skip] Corp '{name}' already exists (id={existing['id']})")
        return existing["id"], existing["org_id"]

    corp_id = str(uuid.uuid4())
    pw_hash = hash_password(name.lower(), password)
    now = time.time()

    org_id = org_service.create_org_for_corp(conn, corp_id, name)

    conn.execute(
        """INSERT INTO corporations (id, name, password_hash, color, org_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (corp_id, name, pw_hash, color, org_id, now),
    )
    print(f"  [new]  Corp '{name}' → corp={corp_id[:8]}… org={org_id[:8]}…")
    return corp_id, org_id


def create_contract(conn, **kw):
    """Insert a contract row directly. Returns contract_id."""
    contract_id = str(uuid.uuid4())
    now = game_now_s()
    expires = now + kw.get("expiry_days", 180) * DAY

    items = kw.get("items", [])
    meta = {"items": items}
    if kw.get("buyout_price"):
        meta["buyout_price"] = kw["buyout_price"]

    conn.execute(
        """
        INSERT INTO contracts
          (id, contract_type, title, description, issuer_org_id, assignee_org_id,
           location_id, destination_id, price, reward, availability, status,
           created_at, expires_at, completed_at, items_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'outstanding', ?, ?, NULL, ?)
        """,
        (
            contract_id,
            kw["contract_type"],
            kw.get("title", "Untitled"),
            kw.get("description", ""),
            kw["issuer_org_id"],
            kw.get("assignee_org_id"),
            kw.get("location_id"),
            kw.get("destination_id"),
            kw.get("price", 0.0),
            kw.get("reward", 0.0),
            kw.get("availability", "public"),
            now,
            expires,
            json.dumps(meta),
        ),
    )
    return contract_id


def water_items(qty):
    """Return a list with a single Water item entry."""
    return [{"item_id": "water", "name": "Water", "quantity": qty}]


def main():
    print("=" * 60)
    print("Seed Test Contracts — Water commodity spread")
    print("=" * 60)

    conn = connect_db()
    try:
        # ── Create corporations ────────────────────────────────
        print("\n1. Creating test corporations…")
        corp_orgs = []  # [(corp_id, org_id, name), ...]
        for c in CORPS:
            corp_id, org_id = create_corp(conn, c["name"], c["color"], c["password"])
            corp_orgs.append((corp_id, org_id, c["name"]))

        conn.commit()
        print(f"   → {len(corp_orgs)} corporations ready")

        # ── Create contracts ───────────────────────────────────
        print("\n2. Creating sample contracts…")
        created = 0

        # Grab org IDs for easy reference
        helios   = corp_orgs[0]  # Helios Mining
        artemis  = corp_orgs[1]  # Artemis Logistics
        orbital  = corp_orgs[2]  # Orbital Dynamics
        nova     = corp_orgs[3]  # Nova Industries
        lunar    = corp_orgs[4]  # Lunar Transit Corp

        # ── AUCTIONS (5 contracts) ─────────────────────────────
        print("   Auctions:")

        cid = create_contract(conn,
            contract_type="auction",
            title="Water x500 [Auction]",
            description="Bulk water lot from Helios mining operations.",
            issuer_org_id=helios[1],
            location_id="LEO",
            price=22500.0,         # starting bid
            buyout_price=45000.0,  # buyout
            expiry_days=180,
            items=water_items(500),
        )
        print(f"     Helios: 500 Water @ LEO (bid $22.5k, buyout $45k) → {cid[:8]}…")
        created += 1

        cid = create_contract(conn,
            contract_type="auction",
            title="Water x1000 [Auction]",
            description="Large water reserve. Premium quality.",
            issuer_org_id=nova[1],
            location_id="GEO",
            price=40000.0,
            buyout_price=90000.0,
            expiry_days=360,
            items=water_items(1000),
        )
        print(f"     Nova: 1000 Water @ GEO (bid $40k, buyout $90k) → {cid[:8]}…")
        created += 1

        cid = create_contract(conn,
            contract_type="auction",
            title="Water x200 [Auction]",
            description="Small lot, quick sale.",
            issuer_org_id=orbital[1],
            location_id="LLO",
            price=10000.0,
            buyout_price=0.0,
            expiry_days=180,
            items=water_items(200),
        )
        print(f"     Orbital: 200 Water @ LLO (bid $10k, no buyout) → {cid[:8]}…")
        created += 1

        cid = create_contract(conn,
            contract_type="auction",
            title="Water x5000 [Auction]",
            description="Massive water shipment. Excellent for colony supply.",
            issuer_org_id=helios[1],
            location_id="L1",
            price=200000.0,
            buyout_price=450000.0,
            expiry_days=1825,
            items=water_items(5000),
        )
        print(f"     Helios: 5000 Water @ L1 (bid $200k, buyout $450k) → {cid[:8]}…")
        created += 1

        cid = create_contract(conn,
            contract_type="auction",
            title="Water x100 [Auction, Private]",
            description="Reserved allocation for Nova Industries.",
            issuer_org_id=lunar[1],
            assignee_org_id=nova[1],
            location_id="LEO",
            availability="private",
            price=5000.0,
            buyout_price=9000.0,
            expiry_days=180,
            items=water_items(100),
        )
        print(f"     Lunar→Nova: 100 Water @ LEO (private auction) → {cid[:8]}…")
        created += 1

        # ── COURIER CONTRACTS (5 contracts) ────────────────────
        print("   Courier:")

        cid = create_contract(conn,
            contract_type="courier",
            title="Courier: 300 Water LEO→LLO",
            description="Transport water from Earth orbit to Luna. Standard rates.",
            issuer_org_id=artemis[1],
            location_id="LEO",
            destination_id="LLO",
            reward=15000.0,
            price=30000.0,   # collateral
            expiry_days=90,
            items=water_items(300),
        )
        print(f"     Artemis: 300 Water LEO→LLO (reward $15k) → {cid[:8]}…")
        created += 1

        cid = create_contract(conn,
            contract_type="courier",
            title="Courier: 1000 Water GEO→L1",
            description="Urgent: water needed at L1 station. Bonus for fast delivery.",
            issuer_org_id=nova[1],
            location_id="GEO",
            destination_id="L1",
            reward=50000.0,
            price=100000.0,
            expiry_days=30,
            items=water_items(1000),
        )
        print(f"     Nova: 1000 Water GEO→L1 (reward $50k, urgent) → {cid[:8]}…")
        created += 1

        cid = create_contract(conn,
            contract_type="courier",
            title="Courier: 500 Water L1→LLO",
            description="Lagrange point to Luna delivery.",
            issuer_org_id=orbital[1],
            location_id="L1",
            destination_id="LLO",
            reward=25000.0,
            price=50000.0,
            expiry_days=180,
            items=water_items(500),
        )
        print(f"     Orbital: 500 Water L1→LLO (reward $25k) → {cid[:8]}…")
        created += 1

        cid = create_contract(conn,
            contract_type="courier",
            title="Courier: 200 Water LLO→LEO",
            description="Return shipment of lunar water to Earth markets.",
            issuer_org_id=lunar[1],
            location_id="LLO",
            destination_id="LEO",
            reward=12000.0,
            price=20000.0,
            expiry_days=90,
            items=water_items(200),
        )
        print(f"     Lunar: 200 Water LLO→LEO (reward $12k) → {cid[:8]}…")
        created += 1

        cid = create_contract(conn,
            contract_type="courier",
            title="Courier: 2000 Water LEO→GEO [Private]",
            description="Dedicated Helios supply run.",
            issuer_org_id=helios[1],
            assignee_org_id=artemis[1],
            location_id="LEO",
            destination_id="GEO",
            availability="private",
            reward=35000.0,
            price=80000.0,
            expiry_days=180,
            items=water_items(2000),
        )
        print(f"     Helios→Artemis: 2000 Water LEO→GEO (private courier) → {cid[:8]}…")
        created += 1

        # ── ITEM EXCHANGE (5 contracts) ────────────────────────
        print("   Item Exchange:")

        # Want to Sell
        cid = create_contract(conn,
            contract_type="item_exchange",
            title="Sell: Water x400",
            description="Water available for immediate purchase.",
            issuer_org_id=helios[1],
            location_id="LEO",
            price=18000.0,
            expiry_days=30,
            items=water_items(400),
        )
        print(f"     Helios: Sell 400 Water @ LEO ($18k) → {cid[:8]}…")
        created += 1

        cid = create_contract(conn,
            contract_type="item_exchange",
            title="Sell: Water x800",
            description="Geostationary water depot clearance.",
            issuer_org_id=orbital[1],
            location_id="GEO",
            price=36000.0,
            expiry_days=90,
            items=water_items(800),
        )
        print(f"     Orbital: Sell 800 Water @ GEO ($36k) → {cid[:8]}…")
        created += 1

        # Want to Buy
        cid = create_contract(conn,
            contract_type="item_exchange",
            title="Buy: Water x600",
            description="Seeking water supply for Luna base expansion.",
            issuer_org_id=lunar[1],
            location_id="LLO",
            price=30000.0,
            expiry_days=90,
            items=water_items(600),
        )
        print(f"     Lunar: Buy 600 Water @ LLO ($30k) → {cid[:8]}…")
        created += 1

        cid = create_contract(conn,
            contract_type="item_exchange",
            title="Buy: Water x2000",
            description="Large procurement. Will pay top dollar for L1 delivery.",
            issuer_org_id=nova[1],
            location_id="L1",
            price=100000.0,
            expiry_days=180,
            items=water_items(2000),
        )
        print(f"     Nova: Buy 2000 Water @ L1 ($100k) → {cid[:8]}…")
        created += 1

        # Barter
        cid = create_contract(conn,
            contract_type="item_exchange",
            title="Barter: Water x300 for goods",
            description="[Barter] Wants: Structural Alloys x50\nWill trade water for structural materials.",
            issuer_org_id=artemis[1],
            location_id="LEO",
            price=0.0,
            expiry_days=90,
            items=water_items(300),
        )
        print(f"     Artemis: Barter 300 Water @ LEO (for alloys) → {cid[:8]}…")
        created += 1

        conn.commit()

        # ── Summary ────────────────────────────────────────────
        print(f"\n{'=' * 60}")
        print(f"Done! Created {created} contracts across {len(corp_orgs)} corporations.")
        print(f"\nCorporation logins (all password: test123):")
        for _, _, name in corp_orgs:
            print(f"  • {name}")
        print(f"\nContract breakdown:")
        print(f"  Auctions:      5  (4 public, 1 private)")
        print(f"  Courier:       5  (4 public, 1 private)")
        print(f"  Item Exchange: 5  (2 sell, 2 buy, 1 barter)")
        print(f"\nLocations used: {', '.join(LOCATIONS)}")
        print(f"{'=' * 60}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
