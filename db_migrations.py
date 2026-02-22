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
        """
    )


def _migrations() -> List[Migration]:
    return [
        Migration("0001_initial", "Create core gameplay/auth tables", _migration_0001_initial),
        Migration("0002_ships_runtime_columns", "Add ships runtime/stat columns", _migration_0002_ships_runtime_columns),
    Migration("0003_location_inventory", "Add scalable location inventory stack table", _migration_0003_location_inventory),
    Migration("0004_surface_sites", "Add surface sites and resource distribution tables", _migration_0004_surface_sites),
    Migration("0005_industry", "Add deployed equipment and production/mining job tables", _migration_0005_industry),
    Migration("0006_organizations", "Organizations, research, LEO boosts, prospecting", _migration_0006_organizations),
    Migration("0007_corporations", "Corporation auth, ownership columns, data wipe", _migration_0007_corporations),
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
