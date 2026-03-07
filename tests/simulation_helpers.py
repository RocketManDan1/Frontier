"""
Simulation test helpers — fabricate realistic game world state for integration tests.

Provides a GameWorldBuilder that creates orgs, corporations, ships, inventory,
surface sites, facilities, and equipment in the correct order with proper
foreign key relationships.  All helpers operate directly on a sqlite3 connection
already initialised with migrations (and optionally with seed data).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sim_service import game_now_s


# ── Tiny deterministic ID generators ──────────────────────────────────────────

def _uuid() -> str:
    return str(uuid.uuid4())


def _short_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


# ── GameWorldBuilder ──────────────────────────────────────────────────────────


class GameWorldBuilder:
    """
    Fluent builder for fabricating a realistic game world in the test DB.

    Usage::

        world = GameWorldBuilder(conn)
        world.create_user("alice")
        corp_id = world.create_corporation("AstraCorp", owner="alice")
        org_id = world.get_org_id(corp_id=corp_id)
        world.ensure_location("MARS_HELLAS", "Hellas Planitia")
        world.create_surface_site("MARS_HELLAS", "mars", gravity=3.72, orbit_node_id="LMO")
        world.seed_site_resources("MARS_HELLAS", {"iron_oxides": 0.4, "water_ice": 0.1, ...})
        world.prospect_site(org_id, "MARS_HELLAS")
        fac_id = world.create_facility("MARS_HELLAS", corp_id, "Hellas Base")
        world.add_part_to_location("MARS_HELLAS", "mgm_1a_phaethon", corp_id=corp_id)
        ship_id = world.spawn_ship("ship_1", "Cargo Hauler", "LEO", owner_corp=corp_id)
        world.add_resource_to_location("LEO", "water", 5000.0, corp_id=corp_id)
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._users_created: set[str] = set()
        self._locations_created: set[str] = set()
        self._corps_created: Dict[str, str] = {}    # corp_id -> org_id
        self._orgs_created: Dict[str, str] = {}     # org_id -> name

    # ── Users ──────────────────────────────────────────────────────────────

    def create_user(self, username: str, *, is_admin: bool = False) -> str:
        """Create a test user. Returns username."""
        if username in self._users_created:
            return username
        self.conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, is_admin, created_at) VALUES (?,?,?,?)",
            (username, f"test_hash_{username}", int(is_admin), time.time()),
        )
        self.conn.commit()
        self._users_created.add(username)
        return username

    def create_session(self, username: str) -> str:
        """Create a session token for a user. Returns token."""
        if username not in self._users_created:
            self.create_user(username)
        token = secrets.token_urlsafe(32)
        self.conn.execute(
            "INSERT INTO sessions (token, username, created_at) VALUES (?,?,?)",
            (token, username, time.time()),
        )
        self.conn.commit()
        return token

    # ── Corporations & Organizations ───────────────────────────────────────

    def create_corporation(
        self,
        name: str,
        *,
        owner: str = "testuser",
        starting_balance: float = 10_000_000_000.0,
        starting_rp: float = 50.0,
    ) -> str:
        """Create a corporation with a linked organization. Returns corp_id."""
        if owner not in self._users_created:
            self.create_user(owner)

        corp_id = _uuid()
        org_id = _uuid()
        now = game_now_s()

        # Create the org first
        self.conn.execute(
            """INSERT INTO organizations (id, name, balance_usd, research_points, last_settled_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (org_id, name, starting_balance, starting_rp, now, now),
        )

        # Create the corp linked to the org
        self.conn.execute(
            """INSERT INTO corporations (id, name, password_hash, color, org_id, created_at)
               VALUES (?, ?, ?, '#00ff00', ?, ?)""",
            (corp_id, name, f"hash_{name}", org_id, now),
        )
        self.conn.commit()

        self._corps_created[corp_id] = org_id
        self._orgs_created[org_id] = name
        return corp_id

    def get_org_id(self, *, corp_id: str = "", username: str = "") -> str:
        """Resolve org_id from corp or user. Creates org if needed."""
        if corp_id:
            if corp_id in self._corps_created:
                return self._corps_created[corp_id]
            from org_service import ensure_org_for_corp
            return ensure_org_for_corp(self.conn, corp_id)
        if username:
            from org_service import ensure_org_for_user
            return ensure_org_for_user(self.conn, username)
        raise ValueError("Must provide corp_id or username")

    def get_org_balance(self, org_id: str) -> float:
        """Read current org balance."""
        row = self.conn.execute(
            "SELECT balance_usd FROM organizations WHERE id = ?", (org_id,)
        ).fetchone()
        return float(row["balance_usd"]) if row else 0.0

    def set_org_balance(self, org_id: str, balance: float) -> None:
        """Set org balance directly."""
        self.conn.execute(
            "UPDATE organizations SET balance_usd = ? WHERE id = ?",
            (balance, org_id),
        )
        self.conn.commit()

    # ── Locations ──────────────────────────────────────────────────────────

    def ensure_location(self, location_id: str, name: str = "") -> str:
        """Ensure a location exists. Returns location_id."""
        if location_id in self._locations_created:
            return location_id
        display_name = name or location_id
        self.conn.execute(
            """INSERT OR IGNORE INTO locations (id, name, parent_id, is_group, sort_order, x, y)
               VALUES (?, ?, NULL, 0, 0, 0, 0)""",
            (location_id, display_name),
        )
        self.conn.commit()
        self._locations_created.add(location_id)
        return location_id

    def ensure_standard_locations(self) -> None:
        """Create the standard set of locations used in most tests."""
        for loc_id, name in [
            ("LEO", "Low Earth Orbit"),
            ("HEO", "High Earth Orbit"),
            ("GEO", "Geosynchronous Orbit"),
            ("LMO", "Low Mars Orbit"),
            ("LLO", "Low Lunar Orbit"),
            ("MARS_HELLAS", "Hellas Planitia"),
            ("MARS_AMAZONIS", "Amazonis Planitia"),
            ("CERES_SURFACE", "Ceres Surface"),
            ("VESTA_SURFACE", "Vesta Surface"),
            ("EUROPA_SURFACE", "Europa Surface"),
        ]:
            self.ensure_location(loc_id, name)

    # ── Surface Sites ──────────────────────────────────────────────────────

    def create_surface_site(
        self,
        location_id: str,
        body_id: str,
        *,
        gravity: float = 3.72,
        orbit_node_id: str = "LMO",
    ) -> str:
        """Register a location as a surface site. Returns location_id."""
        self.ensure_location(location_id)
        self.ensure_location(orbit_node_id)

        self.conn.execute(
            """INSERT OR IGNORE INTO surface_sites (location_id, body_id, orbit_node_id, gravity_m_s2)
               VALUES (?, ?, ?, ?)""",
            (location_id, body_id, orbit_node_id, gravity),
        )
        self.conn.commit()
        return location_id

    def seed_site_resources(
        self,
        location_id: str,
        resources: Dict[str, float],
    ) -> None:
        """Seed surface_site_resources for a site. resources = {resource_id: mass_fraction}."""
        for res_id, fraction in resources.items():
            self.conn.execute(
                """INSERT OR REPLACE INTO surface_site_resources (site_location_id, resource_id, mass_fraction)
                   VALUES (?, ?, ?)""",
                (location_id, res_id, fraction),
            )
        self.conn.commit()

    def prospect_site(self, org_id: str, location_id: str) -> None:
        """Mark a site as prospected by an org (inserts prospecting results from site resources)."""
        # Copy site resource distribution into prospecting results
        resources = self.conn.execute(
            "SELECT resource_id, mass_fraction FROM surface_site_resources WHERE site_location_id = ?",
            (location_id,),
        ).fetchall()
        now = game_now_s()
        for r in resources:
            self.conn.execute(
                """INSERT OR IGNORE INTO prospecting_results
                   (org_id, site_location_id, resource_id, mass_fraction, prospected_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (org_id, location_id, r["resource_id"], float(r["mass_fraction"]), now),
            )
        # If no resources defined, insert a dummy so is_site_prospected returns True
        if not resources:
            self.conn.execute(
                """INSERT OR IGNORE INTO prospecting_results
                   (org_id, site_location_id, resource_id, mass_fraction, prospected_at)
                   VALUES (?, ?, 'silicate_rock', 0.5, ?)""",
                (org_id, location_id, now),
            )
        self.conn.commit()

    # ── Facilities ─────────────────────────────────────────────────────────

    def create_facility(
        self,
        location_id: str,
        corp_id: str,
        name: str = "Test Facility",
    ) -> str:
        """Create a facility at a location. Returns facility_id."""
        self.ensure_location(location_id)
        fac_id = _uuid()
        now = game_now_s()
        self.conn.execute(
            """INSERT INTO facilities (id, location_id, corp_id, name, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, 'test')""",
            (fac_id, location_id, corp_id, name, now),
        )
        self.conn.commit()
        return fac_id

    # ── Ships ──────────────────────────────────────────────────────────────

    def spawn_ship(
        self,
        ship_id: str,
        name: str,
        location_id: str,
        *,
        owner_corp: str = "",
        owner_user: str = "",
        parts: Optional[List[Dict[str, Any]]] = None,
        fuel_kg: float = 5000.0,
        dry_mass_kg: float = 10000.0,
        fuel_capacity_kg: float = 10000.0,
        cargo_capacity_kg: float = 50000.0,
        status: str = "docked",
    ) -> str:
        """
        Spawn a ship with realistic defaults. Returns ship_id.
        If parts is None, generates a minimal thruster part with the given capacities.
        """
        self.ensure_location(location_id)

        if parts is None:
            parts = [
                {
                    "item_id": "test_thruster",
                    "name": "Test Thruster",
                    "type": "thruster",
                    "mass_kg": dry_mass_kg,
                    "thrust_n": 50000,
                    "isp_s": 900,
                    "fuel_capacity_kg": fuel_capacity_kg,
                    "cargo_capacity_kg": cargo_capacity_kg,
                }
            ]

        parts_json = json.dumps(parts)
        now = game_now_s()

        # Get column names to handle schema variations
        cols_info = {r["name"] for r in self.conn.execute("PRAGMA table_info(ships)").fetchall()}

        # Only include columns that actually exist
        all_fields: Dict[str, Any] = {
            "id": ship_id,
            "name": name,
            "location_id": location_id,
            "parts_json": parts_json,
            "created_at": now,
            "shape": "triangle",
            "color": "#ffffff",
            "size_px": 12,
            "notes_json": "[]",
            "status": status,
            "dry_mass_kg": dry_mass_kg,
            "fuel_kg": fuel_kg,
            "fuel_capacity_kg": fuel_capacity_kg,
            "isp_s": 900.0,
            "owner": owner_user or "test",
            "corp_id": owner_corp,
        }

        # Filter to only columns that exist in the schema
        base = {k: v for k, v in all_fields.items() if k in cols_info}

        col_names = ", ".join(base.keys())
        placeholders = ", ".join("?" for _ in base)
        self.conn.execute(
            f"INSERT INTO ships ({col_names}) VALUES ({placeholders})",
            tuple(base.values()),
        )
        self.conn.commit()
        return ship_id

    # ── Inventory ──────────────────────────────────────────────────────────

    def add_resource_to_location(
        self,
        location_id: str,
        resource_id: str,
        mass_kg: float,
        *,
        corp_id: str = "",
    ) -> None:
        """Add a resource to location inventory using the real inventory system."""
        self.ensure_location(location_id)
        import main as _main
        _main.add_resource_to_location_inventory(
            self.conn, location_id, resource_id, mass_kg, corp_id=corp_id,
        )
        self.conn.commit()

    def add_part_to_location(
        self,
        location_id: str,
        item_id: str,
        *,
        corp_id: str = "",
        name: str = "",
        mass_kg: float = 0.0,
        count: int = 1,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Add a part to location inventory. Tries to use catalog data first,
        falls back to constructing a minimal part dict.
        """
        self.ensure_location(location_id)
        import main as _main

        # Try to get from any equipment catalog
        from industry_service import _resolve_deployable_catalog_entry
        cat = _resolve_deployable_catalog_entry(item_id)

        part: Dict[str, Any] = {
            "item_id": item_id,
            "name": name or (cat.get("name", item_id) if cat else item_id),
            "type": (cat.get("type", "part") if cat else "part"),
            "category_id": (cat.get("category_id", "part") if cat else "part"),
            "mass_kg": mass_kg or (cat.get("mass_kg", 1000.0) if cat else 1000.0),
        }
        if cat:
            # Add category-specific fields that deploy_equipment expects
            for key in ("miner_type", "printer_type", "specialization",
                        "mining_rate_kg_per_hr", "construction_rate_kg_per_hr",
                        "water_extraction_kg_per_hr", "electric_mw",
                        "thermal_mw", "heat_rejection_mw", "throughput_mult",
                        "efficiency", "max_concurrent_recipes", "max_recipe_tier"):
                if key in cat:
                    part[key] = cat[key]

        if extra_fields:
            part.update(extra_fields)

        for _ in range(count):
            _main.add_part_to_location_inventory(
                self.conn, location_id, part, 1.0, corp_id=corp_id,
            )
        self.conn.commit()

    def add_cargo_to_ship(
        self,
        ship_id: str,
        resource_id: str,
        mass_kg: float,
    ) -> float:
        """Add resource cargo to a ship. Returns accepted mass."""
        import main as _main
        accepted = _main.add_cargo_to_ship(self.conn, ship_id, resource_id, mass_kg)
        self.conn.commit()
        return accepted

    def get_location_inventory(self, location_id: str, *, corp_id: str = "") -> List[Dict[str, Any]]:
        """Read all inventory stacks at a location."""
        if corp_id:
            rows = self.conn.execute(
                """SELECT * FROM location_inventory_stacks
                   WHERE location_id = ? AND corp_id = ?
                   ORDER BY stack_type, item_id""",
                (location_id, corp_id),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM location_inventory_stacks WHERE location_id = ?
                   ORDER BY stack_type, item_id""",
                (location_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_resource_mass_at_location(
        self, location_id: str, resource_id: str, *, corp_id: str = ""
    ) -> float:
        """Get total mass of a specific resource at a location."""
        if corp_id:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(mass_kg), 0.0) as total
                   FROM location_inventory_stacks
                   WHERE location_id = ? AND corp_id = ? AND stack_type = 'resource' AND item_id = ?""",
                (location_id, corp_id, resource_id),
            ).fetchone()
        else:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(mass_kg), 0.0) as total
                   FROM location_inventory_stacks
                   WHERE location_id = ? AND stack_type = 'resource' AND item_id = ?""",
                (location_id, resource_id),
            ).fetchone()
        return float(row["total"]) if row else 0.0

    def get_ship_cargo(self, ship_id: str) -> Dict[str, float]:
        """Get ship cargo {resource_id: mass_kg}."""
        rows = self.conn.execute(
            "SELECT resource_id, mass_kg FROM ship_cargo_stacks WHERE ship_id = ?",
            (ship_id,),
        ).fetchall()
        return {str(r["resource_id"]): float(r["mass_kg"]) for r in rows}

    # ── Equipment helpers ──────────────────────────────────────────────────

    def deploy_equipment_directly(
        self,
        location_id: str,
        item_id: str,
        category: str,
        *,
        corp_id: str = "",
        facility_id: str = "",
        name: str = "",
        config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Insert a deployed_equipment row directly (bypassing inventory consumption).
        Useful when you just need equipment in place without caring about the
        deploy flow.  Returns equipment_id.
        """
        self.ensure_location(location_id)
        equip_id = _uuid()
        now = game_now_s()
        config_json = json.dumps(config or {})

        self.conn.execute(
            """INSERT INTO deployed_equipment
               (id, location_id, item_id, name, category, deployed_at, deployed_by,
                status, config_json, corp_id, mode, facility_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                equip_id, location_id, item_id,
                name or item_id, category,
                now, "test", "idle", config_json,
                corp_id, "idle", facility_id,
            ),
        )
        self.conn.commit()
        return equip_id

    def create_refinery_slots(
        self,
        equipment_id: str,
        location_id: str,
        num_slots: int,
        *,
        corp_id: str = "",
        facility_id: str = "",
    ) -> List[str]:
        """Create refinery slots for a deployed refinery. Returns slot IDs."""
        slot_ids = []
        for i in range(num_slots):
            slot_id = _uuid()
            self.conn.execute(
                """INSERT INTO refinery_slots
                   (id, equipment_id, location_id, slot_index, priority, corp_id, facility_id)
                   VALUES (?,?,?,?,?,?,?)""",
                (slot_id, equipment_id, location_id, i, i, corp_id, facility_id),
            )
            slot_ids.append(slot_id)
        self.conn.commit()
        return slot_ids

    # ── Missions helpers ───────────────────────────────────────────────────

    def insert_mission(
        self,
        *,
        mission_id: str = "",
        tier: str = "easy",
        destination_id: str = "LMO",
        destination_name: str = "Low Mars Orbit",
        status: str = "available",
        org_id: Optional[str] = None,
        accepted_at: Optional[float] = None,
        expires_at: Optional[float] = None,
        power_started_at: Optional[float] = None,
    ) -> str:
        """Insert a mission directly. Returns mission_id."""
        import mission_service

        mid = mission_id or _short_id("msn_")
        payout = mission_service.PAYOUTS[tier]
        now = game_now_s()

        self.conn.execute(
            """INSERT INTO missions
               (id, tier, title, description, destination_id, destination_name,
                status, payout_total, payout_upfront, payout_completion,
                org_id, accepted_at, expires_at, delivered_at,
                power_started_at, power_required_s, completed_at,
                created_at, available_expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid, tier,
                f"Test {tier.title()} Mission — {destination_name}",
                f"Test mission to {destination_name}",
                destination_id, destination_name,
                status,
                payout["total"], payout["upfront"], payout["completion"],
                org_id, accepted_at, expires_at, None,
                power_started_at,
                90 * 86400 if tier == "hard" else 0,
                None, now, now + (5 * 365.25 * 86400),
            ),
        )
        self.conn.commit()
        return mid

    # ── Assertions / queries ───────────────────────────────────────────────

    def get_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a mission row as dict."""
        row = self.conn.execute(
            "SELECT * FROM missions WHERE id = ?", (mission_id,)
        ).fetchone()
        return dict(row) if row else None

    def count_deployed_equipment(
        self, location_id: str, *, category: str = "", facility_id: str = ""
    ) -> int:
        """Count deployed equipment at a location."""
        query = "SELECT COUNT(*) as cnt FROM deployed_equipment WHERE location_id = ?"
        params: list = [location_id]
        if category:
            query += " AND category = ?"
            params.append(category)
        if facility_id:
            query += " AND facility_id = ?"
            params.append(facility_id)
        row = self.conn.execute(query, params).fetchone()
        return int(row["cnt"]) if row else 0

    def get_active_jobs(
        self, location_id: str, *, job_type: str = ""
    ) -> List[Dict[str, Any]]:
        """Get active production jobs at a location."""
        query = "SELECT * FROM production_jobs WHERE location_id = ? AND status = 'active'"
        params: list = [location_id]
        if job_type:
            query += " AND job_type = ?"
            params.append(job_type)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
