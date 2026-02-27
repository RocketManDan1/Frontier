import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable, List


@dataclass(frozen=True)
class Migration:
    migration_id: str
    description: str
    apply: Callable[[sqlite3.Connection], None]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return {str(r["name"]) for r in rows}


def _safe_add_column(conn: sqlite3.Connection, table: str, name: str, coltype: str) -> None:
    if name in _table_columns(conn, table):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype};")


def _migration_0001_initial(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS locations (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          parent_id TEXT REFERENCES locations(id) ON DELETE CASCADE,
          is_group INTEGER NOT NULL DEFAULT 0,
          sort_order INTEGER NOT NULL DEFAULT 0,
          x REAL NOT NULL DEFAULT 0,
          y REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_locations_parent ON locations(parent_id);

        CREATE TABLE IF NOT EXISTS transfer_edges (
          from_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
          to_id   TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
          dv_m_s  REAL NOT NULL,
          tof_s   REAL NOT NULL,
          PRIMARY KEY (from_id, to_id)
        );
        CREATE INDEX IF NOT EXISTS idx_edges_from ON transfer_edges(from_id);

        CREATE TABLE IF NOT EXISTS transfer_matrix (
          from_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
          to_id   TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
          dv_m_s  REAL NOT NULL,
          tof_s   REAL NOT NULL,
          path_json TEXT NOT NULL DEFAULT '[]',
          PRIMARY KEY (from_id, to_id)
        );
        CREATE INDEX IF NOT EXISTS idx_matrix_from ON transfer_matrix(from_id);

        CREATE TABLE IF NOT EXISTS transfer_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
          username TEXT PRIMARY KEY,
          password_hash TEXT NOT NULL,
          is_admin INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
          token TEXT PRIMARY KEY,
          username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
          created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_username ON sessions(username);

        CREATE TABLE IF NOT EXISTS ships (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          shape TEXT NOT NULL DEFAULT 'triangle',
          color TEXT NOT NULL DEFAULT '#ffffff',
          size_px REAL NOT NULL DEFAULT 12,
          notes_json TEXT NOT NULL DEFAULT '[]'
        );
        """
    )


def _migration_0002_ships_runtime_columns(conn: sqlite3.Connection) -> None:
    _safe_add_column(conn, "ships", "location_id", "TEXT")
    _safe_add_column(conn, "ships", "from_location_id", "TEXT")
    _safe_add_column(conn, "ships", "to_location_id", "TEXT")
    _safe_add_column(conn, "ships", "departed_at", "REAL")
    _safe_add_column(conn, "ships", "arrives_at", "REAL")
    _safe_add_column(conn, "ships", "transfer_path_json", "TEXT NOT NULL DEFAULT '[]'")
    _safe_add_column(conn, "ships", "dv_planned_m_s", "REAL")
    _safe_add_column(conn, "ships", "dock_slot", "INTEGER")
    _safe_add_column(conn, "ships", "parts_json", "TEXT NOT NULL DEFAULT '[]'")
    _safe_add_column(conn, "ships", "fuel_kg", "REAL NOT NULL DEFAULT 0")
    _safe_add_column(conn, "ships", "fuel_capacity_kg", "REAL NOT NULL DEFAULT 0")
    _safe_add_column(conn, "ships", "dry_mass_kg", "REAL NOT NULL DEFAULT 0")
    _safe_add_column(conn, "ships", "isp_s", "REAL NOT NULL DEFAULT 0")


def _migration_0003_location_inventory(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS location_inventory_stacks (
          location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
          stack_type TEXT NOT NULL,
          stack_key TEXT NOT NULL,
          item_id TEXT NOT NULL,
          name TEXT NOT NULL,
          quantity REAL NOT NULL DEFAULT 0,
          mass_kg REAL NOT NULL DEFAULT 0,
          volume_m3 REAL NOT NULL DEFAULT 0,
          payload_json TEXT NOT NULL DEFAULT '{}',
          updated_at REAL NOT NULL,
          PRIMARY KEY (location_id, stack_type, stack_key)
        );

        CREATE INDEX IF NOT EXISTS idx_location_inventory_lookup
          ON location_inventory_stacks(location_id, stack_type, item_id);
        """
    )


def _migration_0004_surface_sites(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS surface_sites (
          location_id TEXT PRIMARY KEY REFERENCES locations(id) ON DELETE CASCADE,
          body_id TEXT NOT NULL,
          orbit_node_id TEXT NOT NULL,
          gravity_m_s2 REAL NOT NULL DEFAULT 0.0
        );
        CREATE INDEX IF NOT EXISTS idx_surface_sites_body ON surface_sites(body_id);

        CREATE TABLE IF NOT EXISTS surface_site_resources (
          site_location_id TEXT NOT NULL REFERENCES surface_sites(location_id) ON DELETE CASCADE,
          resource_id TEXT NOT NULL,
          mass_fraction REAL NOT NULL DEFAULT 0.0,
          PRIMARY KEY (site_location_id, resource_id)
        );
        CREATE INDEX IF NOT EXISTS idx_site_resources_lookup
          ON surface_site_resources(site_location_id);
        """
    )


def _migration_0005_industry(conn: sqlite3.Connection) -> None:
    """Add deployed equipment and production/mining job tables."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS deployed_equipment (
          id TEXT PRIMARY KEY,
          location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
          item_id TEXT NOT NULL,
          name TEXT NOT NULL,
          category TEXT NOT NULL,
          deployed_at REAL NOT NULL,
          deployed_by TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'idle',
          config_json TEXT NOT NULL DEFAULT '{}',
          FOREIGN KEY (deployed_by) REFERENCES users(username) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_deployed_equip_location
          ON deployed_equipment(location_id);
        CREATE INDEX IF NOT EXISTS idx_deployed_equip_category
          ON deployed_equipment(location_id, category);

        CREATE TABLE IF NOT EXISTS production_jobs (
          id TEXT PRIMARY KEY,
          location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
          equipment_id TEXT NOT NULL REFERENCES deployed_equipment(id) ON DELETE CASCADE,
          job_type TEXT NOT NULL,
          recipe_id TEXT,
          resource_id TEXT,
          status TEXT NOT NULL DEFAULT 'active',
          started_at REAL NOT NULL,
          completes_at REAL NOT NULL,
          inputs_json TEXT NOT NULL DEFAULT '[]',
          outputs_json TEXT NOT NULL DEFAULT '[]',
          created_by TEXT NOT NULL,
          completed_at REAL,
          FOREIGN KEY (created_by) REFERENCES users(username) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_production_jobs_location
          ON production_jobs(location_id, status);
        CREATE INDEX IF NOT EXISTS idx_production_jobs_equipment
          ON production_jobs(equipment_id, status);
        """
    )


def _migration_0006_organizations(conn: sqlite3.Connection) -> None:
    """Organizations, research teams, research points, LEO boosts, and prospecting."""
    conn.executescript(
        """
        -- ── Organizations ──────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS organizations (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL UNIQUE,
          balance_usd REAL NOT NULL DEFAULT 1000000000.0,
          research_points REAL NOT NULL DEFAULT 0.0,
          last_settled_at REAL NOT NULL DEFAULT 0.0,
          created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS org_members (
          username TEXT PRIMARY KEY REFERENCES users(username) ON DELETE CASCADE,
          org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_org_members_org ON org_members(org_id);

        CREATE TABLE IF NOT EXISTS research_teams (
          id TEXT PRIMARY KEY,
          org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          hired_at REAL NOT NULL,
          cost_per_month_usd REAL NOT NULL DEFAULT 150000000.0,
          points_per_week REAL NOT NULL DEFAULT 5.0,
          status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE INDEX IF NOT EXISTS idx_research_teams_org ON research_teams(org_id);

        -- ── LEO Boost Ledger ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS leo_boosts (
          id TEXT PRIMARY KEY,
          org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          item_id TEXT NOT NULL,
          item_name TEXT NOT NULL,
          quantity REAL NOT NULL DEFAULT 1.0,
          mass_kg REAL NOT NULL DEFAULT 0.0,
          cost_usd REAL NOT NULL DEFAULT 0.0,
          boosted_at REAL NOT NULL,
          destination_location_id TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_leo_boosts_org ON leo_boosts(org_id);

        -- ── Research Unlocks (KSP tech tree) ─────────────────────────────
        CREATE TABLE IF NOT EXISTS research_unlocks (
          org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          tech_id TEXT NOT NULL,
          unlocked_at REAL NOT NULL,
          cost_points REAL NOT NULL DEFAULT 0.0,
          PRIMARY KEY (org_id, tech_id)
        );
        CREATE INDEX IF NOT EXISTS idx_research_unlocks_org ON research_unlocks(org_id);

        -- ── Prospecting (per-org site visibility) ────────────────────────
        CREATE TABLE IF NOT EXISTS prospecting_results (
          org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          site_location_id TEXT NOT NULL,
          resource_id TEXT NOT NULL,
          mass_fraction REAL NOT NULL DEFAULT 0.0,
          prospected_at REAL NOT NULL,
          prospected_by_ship TEXT,
          PRIMARY KEY (org_id, site_location_id, resource_id)
        );
        CREATE INDEX IF NOT EXISTS idx_prospecting_org_site
          ON prospecting_results(org_id, site_location_id);
        """
    )


def _migration_0007_corporations(conn: sqlite3.Connection) -> None:
    """Add corporation-based auth and ownership.

    Corporations replace individual user accounts for gameplay.
    Admin login remains separate via the users table.
    """
    conn.executescript(
        """
        -- ── Corporations ──────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS corporations (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL,
          color TEXT NOT NULL DEFAULT '#ffffff',
          org_id TEXT REFERENCES organizations(id),
          created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS corp_sessions (
          token TEXT PRIMARY KEY,
          corp_id TEXT NOT NULL REFERENCES corporations(id) ON DELETE CASCADE,
          created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_corp_sessions_corp ON corp_sessions(corp_id);
        """
    )

    # Add corp_id ownership columns to game tables
    _safe_add_column(conn, "ships", "corp_id", "TEXT")
    _safe_add_column(conn, "deployed_equipment", "corp_id", "TEXT")
    _safe_add_column(conn, "production_jobs", "corp_id", "TEXT")

    # Recreate location_inventory_stacks with corp_id in primary key
    # (old PK was location_id, stack_type, stack_key — now includes corp_id)
    conn.executescript(
        """
        DROP TABLE IF EXISTS location_inventory_stacks;
        CREATE TABLE location_inventory_stacks (
          location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
          corp_id TEXT NOT NULL DEFAULT '',
          stack_type TEXT NOT NULL,
          stack_key TEXT NOT NULL,
          item_id TEXT NOT NULL,
          name TEXT NOT NULL,
          quantity REAL NOT NULL DEFAULT 0,
          mass_kg REAL NOT NULL DEFAULT 0,
          volume_m3 REAL NOT NULL DEFAULT 0,
          payload_json TEXT NOT NULL DEFAULT '{}',
          updated_at REAL NOT NULL,
          PRIMARY KEY (location_id, corp_id, stack_type, stack_key)
        );
        CREATE INDEX IF NOT EXISTS idx_location_inventory_lookup
          ON location_inventory_stacks(location_id, corp_id, stack_type, item_id);
        """
    )

    # Wipe game data — clean slate for corp ownership model
    conn.executescript(
        """
        DELETE FROM ships;
        DELETE FROM deployed_equipment;
        DELETE FROM production_jobs;
        DELETE FROM organizations;
        DELETE FROM org_members;
        DELETE FROM research_teams;
        DELETE FROM leo_boosts;
        DELETE FROM research_unlocks;
        DELETE FROM prospecting_results;
        DELETE FROM transfer_meta WHERE key LIKE 'sim_%';
        """
    )


def _migration_0008_corp_session_heartbeat(conn: sqlite3.Connection) -> None:
    """Add last_seen column so we can track which corps actually have the game open."""
    _safe_add_column(conn, "corp_sessions", "last_seen", "REAL")
    # Back-fill existing rows with created_at so they aren't NULL
    conn.execute("UPDATE corp_sessions SET last_seen = created_at WHERE last_seen IS NULL")


def _migration_0009_industry_actor_identity(conn: sqlite3.Connection) -> None:
    """Decouple industry actor identity from users(username) for corp sessions."""
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.executescript(
            """
            ALTER TABLE deployed_equipment RENAME TO deployed_equipment_old;

            CREATE TABLE deployed_equipment (
              id TEXT PRIMARY KEY,
              location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
              item_id TEXT NOT NULL,
              name TEXT NOT NULL,
              category TEXT NOT NULL,
              deployed_at REAL NOT NULL,
              deployed_by TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'idle',
              config_json TEXT NOT NULL DEFAULT '{}',
              corp_id TEXT
            );

            INSERT INTO deployed_equipment
              (id, location_id, item_id, name, category, deployed_at, deployed_by, status, config_json, corp_id)
            SELECT
              id, location_id, item_id, name, category, deployed_at,
              COALESCE(NULLIF(deployed_by, ''), 'system'),
              status, config_json, corp_id
            FROM deployed_equipment_old;

            DROP TABLE deployed_equipment_old;

            CREATE INDEX IF NOT EXISTS idx_deployed_equip_location
              ON deployed_equipment(location_id);
            CREATE INDEX IF NOT EXISTS idx_deployed_equip_category
              ON deployed_equipment(location_id, category);

            ALTER TABLE production_jobs RENAME TO production_jobs_old;

            CREATE TABLE production_jobs (
              id TEXT PRIMARY KEY,
              location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
              equipment_id TEXT NOT NULL REFERENCES deployed_equipment(id) ON DELETE CASCADE,
              job_type TEXT NOT NULL,
              recipe_id TEXT,
              resource_id TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              started_at REAL NOT NULL,
              completes_at REAL NOT NULL,
              inputs_json TEXT NOT NULL DEFAULT '[]',
              outputs_json TEXT NOT NULL DEFAULT '[]',
              created_by TEXT NOT NULL,
              completed_at REAL,
              corp_id TEXT
            );

            INSERT INTO production_jobs
              (id, location_id, equipment_id, job_type, recipe_id, resource_id, status, started_at, completes_at, inputs_json, outputs_json, created_by, completed_at, corp_id)
            SELECT
              id, location_id, equipment_id, job_type, recipe_id, resource_id, status, started_at, completes_at, inputs_json, outputs_json,
              COALESCE(NULLIF(created_by, ''), 'system'),
              completed_at, corp_id
            FROM production_jobs_old;

            DROP TABLE production_jobs_old;

            CREATE INDEX IF NOT EXISTS idx_production_jobs_location
              ON production_jobs(location_id, status);
            CREATE INDEX IF NOT EXISTS idx_production_jobs_equipment
              ON production_jobs(equipment_id, status);
            """
        )
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _migration_0010_org_loans(conn: sqlite3.Connection) -> None:
    """Add per-organization loan ledger and repayment tracking."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS org_loans (
          id TEXT PRIMARY KEY,
          org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
          loan_code TEXT NOT NULL,
          principal_usd REAL NOT NULL,
          annual_interest_rate REAL NOT NULL,
          term_months INTEGER NOT NULL,
          total_payable_usd REAL NOT NULL,
          monthly_payment_usd REAL NOT NULL,
          remaining_balance_usd REAL NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          started_at REAL NOT NULL,
          paid_off_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_org_loans_org ON org_loans(org_id);
        CREATE INDEX IF NOT EXISTS idx_org_loans_org_code_status ON org_loans(org_id, loan_code, status);
        """
    )


def _migration_0011_rekey_inventory_stacks(conn: sqlite3.Connection) -> None:
    """Re-key legacy part stacks that used item_id as the stack_key.

    Some code paths (org_service LEO boosts, older admin tools) stored
    parts with ``stack_key = item_id`` and minimal/empty ``payload_json``.
    The canonical path uses a SHA1 hash of the normalized part payload.
    This migration regenerates stack keys for all 'part' stacks so they
    are consistent and stack correctly regardless of provenance.
    """
    import catalog_service

    # Gather all part catalog loaders
    part_catalogs = {}
    for loader in (
        catalog_service.load_thruster_main_catalog,
        catalog_service.load_reactor_catalog,
        catalog_service.load_generator_catalog,
        catalog_service.load_radiator_catalog,
        catalog_service.load_constructor_catalog,
        catalog_service.load_refinery_catalog,
        catalog_service.load_robonaut_catalog,
        catalog_service.load_storage_catalog,
    ):
        part_catalogs.update(loader())

    rows = conn.execute(
        """
        SELECT rowid, location_id, corp_id, stack_type, stack_key, item_id, name,
               quantity, mass_kg, volume_m3, payload_json, updated_at
        FROM location_inventory_stacks
        WHERE stack_type = 'part'
        """
    ).fetchall()

    if not rows:
        return

    def _json_dumps_stable(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    # Container runtime keys to strip before hashing
    container_runtime_keys = frozenset({
        "cargo_manifest", "container_uid",
        "cargo_used_m3", "used_m3", "fill_m3", "stored_m3", "current_m3",
        "cargo_mass_kg", "contents_mass_kg", "stored_mass_kg", "current_mass_kg",
        "water_kg", "fuel_kg", "resource_id",
    })

    # Group rows that will merge into the same new stack_key
    merge_groups = {}  # (location_id, corp_id, new_stack_key) -> [rows]

    for r in rows:
        payload = json.loads(r["payload_json"] or "{}")
        part = payload.get("part") if isinstance(payload, dict) else None
        item_id = str(r["item_id"] or "").strip()

        # Resolve the part dict from catalog if the payload doesn't have it
        if not isinstance(part, dict) or not part:
            catalog_entry = part_catalogs.get(item_id)
            if catalog_entry:
                part = dict(catalog_entry)
                part.setdefault("item_id", item_id)
                part.setdefault("name", str(r["name"] or item_id))
            else:
                qty = max(0.0, float(r["quantity"] or 0.0))
                per_unit_mass = max(0.0, float(r["mass_kg"] or 0.0)) / qty if qty > 0 else 0.0
                part = {
                    "item_id": item_id,
                    "name": str(r["name"] or item_id),
                    "type": "generic",
                    "category_id": "generic",
                    "mass_kg": per_unit_mass,
                }

        # Strip container runtime keys
        clean_part = {k: v for k, v in part.items() if k not in container_runtime_keys}

        new_payload_json = _json_dumps_stable({"part": clean_part})
        new_stack_key = hashlib.sha1(new_payload_json.encode("utf-8")).hexdigest()

        group_key = (str(r["location_id"]), str(r["corp_id"] or ""), new_stack_key)
        merge_groups.setdefault(group_key, []).append({
            "rowid": r["rowid"],
            "old_stack_key": str(r["stack_key"]),
            "item_id": item_id,
            "name": str(r["name"] or item_id),
            "quantity": float(r["quantity"] or 0.0),
            "mass_kg": float(r["mass_kg"] or 0.0),
            "volume_m3": float(r["volume_m3"] or 0.0),
            "payload_json": new_payload_json,
            "updated_at": float(r["updated_at"] or 0.0),
        })

    now = time.time()
    for (loc_id, corp_id, new_key), group in merge_groups.items():
        # Delete all old rows in this group
        rowids = [g["rowid"] for g in group]
        placeholders = ",".join("?" * len(rowids))
        conn.execute(f"DELETE FROM location_inventory_stacks WHERE rowid IN ({placeholders})", rowids)

        # Merge quantities
        total_qty = sum(g["quantity"] for g in group)
        total_mass = sum(g["mass_kg"] for g in group)
        total_vol = sum(g["volume_m3"] for g in group)
        if total_qty <= 0 and total_mass <= 0:
            continue

        # Use the first row's metadata
        first = group[0]

        # Check if a row with this new_key already exists (from a previous correct insert)
        existing = conn.execute(
            """
            SELECT quantity, mass_kg, volume_m3 FROM location_inventory_stacks
            WHERE location_id=? AND corp_id=? AND stack_type='part' AND stack_key=?
            """,
            (loc_id, corp_id, new_key),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE location_inventory_stacks
                SET quantity = quantity + ?, mass_kg = mass_kg + ?,
                    volume_m3 = volume_m3 + ?, payload_json = ?, updated_at = ?
                WHERE location_id=? AND corp_id=? AND stack_type='part' AND stack_key=?
                """,
                (total_qty, total_mass, total_vol, first["payload_json"], now,
                 loc_id, corp_id, new_key),
            )
        else:
            conn.execute(
                """
                INSERT INTO location_inventory_stacks
                  (location_id, corp_id, stack_type, stack_key, item_id, name,
                   quantity, mass_kg, volume_m3, payload_json, updated_at)
                VALUES (?, ?, 'part', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (loc_id, corp_id, new_key, first["item_id"], first["name"],
                 total_qty, total_mass, total_vol, first["payload_json"], now),
            )

    conn.commit()


def _migration_0012_transit_coord_snapshot(conn: sqlite3.Connection) -> None:
    """Add columns to snapshot departure/arrival coordinates for in-transit ships."""
    _safe_add_column(conn, "ships", "transit_from_x", "REAL")
    _safe_add_column(conn, "ships", "transit_from_y", "REAL")
    _safe_add_column(conn, "ships", "transit_to_x", "REAL")
    _safe_add_column(conn, "ships", "transit_to_y", "REAL")


def _migration_0013_edge_type(conn: sqlite3.Connection) -> None:
    """Add edge_type column to transfer_edges (local, interplanetary, lagrange, landing)."""
    _safe_add_column(conn, "transfer_edges", "edge_type", "TEXT DEFAULT 'local'")


def _migration_0014_trajectory_json(conn: sqlite3.Connection) -> None:
    """Add trajectory_json column to ships for storing rendered trajectory polylines."""
    _safe_add_column(conn, "ships", "trajectory_json", "TEXT")


def _migrations() -> List[Migration]:
    return [
        Migration("0001_initial", "Create core gameplay/auth tables", _migration_0001_initial),
        Migration("0002_ships_runtime_columns", "Add ships runtime/stat columns", _migration_0002_ships_runtime_columns),
    Migration("0003_location_inventory", "Add scalable location inventory stack table", _migration_0003_location_inventory),
    Migration("0004_surface_sites", "Add surface sites and resource distribution tables", _migration_0004_surface_sites),
    Migration("0005_industry", "Add deployed equipment and production/mining job tables", _migration_0005_industry),
    Migration("0006_organizations", "Organizations, research, LEO boosts, prospecting", _migration_0006_organizations),
    Migration("0007_corporations", "Corporation auth, ownership columns, data wipe", _migration_0007_corporations),
    Migration("0008_corp_session_heartbeat", "Add last_seen heartbeat column to corp_sessions", _migration_0008_corp_session_heartbeat),
    Migration("0009_industry_actor_identity", "Decouple industry actor identity from users table", _migration_0009_industry_actor_identity),
    Migration("0010_org_loans", "Add organization loans and repayment tracking", _migration_0010_org_loans),
    Migration("0011_rekey_inventory_stacks", "Re-key legacy inventory stacks to SHA1-based stack keys", _migration_0011_rekey_inventory_stacks),
    Migration("0012_transit_coord_snapshot", "Add transit coordinate snapshot columns to ships", _migration_0012_transit_coord_snapshot),
    Migration("0013_edge_type", "Add edge_type column to transfer_edges", _migration_0013_edge_type),
    Migration("0014_trajectory_json", "Add trajectory_json column to ships", _migration_0014_trajectory_json),
    ]


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          migration_id TEXT PRIMARY KEY,
          description TEXT NOT NULL,
          applied_at REAL NOT NULL
        );
        """
    )

    applied = {
        str(r["migration_id"])
        for r in conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
    }

    for migration in _migrations():
        if migration.migration_id in applied:
            continue
        migration.apply(conn)
        conn.execute(
            "INSERT INTO schema_migrations (migration_id,description,applied_at) VALUES (?,?,?)",
            (migration.migration_id, migration.description, time.time()),
        )
