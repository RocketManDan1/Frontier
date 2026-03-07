"""
Facility service — helpers for resolving, creating, and validating facilities.

Central place for facility ownership checks so every endpoint that accepts
``facility_id`` uses the same logic.
"""

import json
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from sim_service import game_now_s
import industry_service


# ── Resolution & Validation ───────────────────────────────────────────────────


def resolve_facility(
    conn: sqlite3.Connection,
    facility_id: str,
) -> Dict[str, Any]:
    """Look up a facility, raising HTTPException(404) if not found."""
    row = conn.execute(
        "SELECT id, location_id, corp_id, name, created_at, created_by FROM facilities WHERE id = ?",
        (facility_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Facility not found")
    return dict(row)


def require_facility_owner(
    conn: sqlite3.Connection,
    facility_id: str,
    corp_id: str,
) -> Dict[str, Any]:
    """Resolve and verify the caller owns the facility. Returns the facility dict."""
    fac = resolve_facility(conn, facility_id)
    if corp_id and fac["corp_id"] and corp_id != fac["corp_id"]:
        raise HTTPException(status_code=403, detail="You do not own this facility")
    return fac


def auto_resolve_facility(
    conn: sqlite3.Connection,
    location_id: str,
    corp_id: str,
    *,
    create_name: str = "Facility",
    created_by: str = "auto",
) -> str:
    """Return the single facility for (location, corp) or auto-create one.

    Used by legacy location-scoped endpoints to transparently resolve a
    facility when the caller hasn't migrated to facility-aware calls yet.
    """
    rows = conn.execute(
        "SELECT id FROM facilities WHERE location_id = ? AND corp_id = ?",
        (location_id, corp_id),
    ).fetchall()
    if len(rows) == 1:
        return str(rows[0]["id"])
    if len(rows) > 1:
        # Multiple facilities — can't auto-resolve; return the first one
        # (the legacy path should still work for the oldest facility)
        return str(rows[0]["id"])
    # None exists — create a default facility
    return create_facility(conn, location_id, corp_id, create_name, created_by)


def create_facility(
    conn: sqlite3.Connection,
    location_id: str,
    corp_id: str,
    name: str,
    created_by: str,
) -> str:
    """Insert a new facility. Returns its id."""
    fid = str(uuid.uuid4())
    now = game_now_s()
    conn.execute(
        """INSERT INTO facilities (id, location_id, corp_id, name, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (fid, location_id, corp_id, name, now, created_by),
    )
    conn.commit()
    return fid


# ── Query Helpers ─────────────────────────────────────────────────────────────


def list_facilities_at_location(
    conn: sqlite3.Connection,
    location_id: str,
    *,
    viewer_corp_id: str = "",
    viewer_is_admin: bool = False,
) -> List[Dict[str, Any]]:
    """List all facilities at a location with summary stats."""
    rows = conn.execute(
        """SELECT f.id, f.location_id, f.corp_id, f.name, f.created_at, f.created_by
           FROM facilities f
           WHERE f.location_id = ?
           ORDER BY f.created_at""",
        (location_id,),
    ).fetchall()

    result = []
    for r in rows:
        fid = r["id"]
        fcorp = str(r["corp_id"] or "")

        # Equipment count
        eq_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM deployed_equipment WHERE facility_id = ?",
            (fid,),
        ).fetchone()
        eq_count = eq_row["cnt"] if eq_row else 0

        # Active job count
        job_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM production_jobs WHERE facility_id = ? AND status = 'active'",
            (fid,),
        ).fetchone()
        active_jobs = job_row["cnt"] if job_row else 0

        # Inventory stack count (cargo is location-scoped per corp)
        inv_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM location_inventory_stacks WHERE location_id = ? AND corp_id = ?",
            (r["location_id"], fcorp),
        ).fetchone()
        inventory_stack_count = inv_row["cnt"] if inv_row else 0

        # Power balance (only compute for own facilities to limit cost)
        power_mwe = 0.0
        power_used_mwe = 0.0
        if viewer_is_admin or (viewer_corp_id and fcorp == viewer_corp_id):
            equipment = conn.execute(
                """SELECT id, location_id, item_id, name, category, status, config_json, mode, corp_id
                   FROM deployed_equipment WHERE facility_id = ?""",
                (fid,),
            ).fetchall()
            eq_dicts = []
            for e in equipment:
                cfg = json.loads(e["config_json"] or "{}")
                eq_dicts.append({
                    "id": e["id"], "name": e["name"], "category": e["category"],
                    "status": e["status"], "mode": str(e["mode"] or "idle"),
                    "config": cfg, "corp_id": str(e["corp_id"] or ""),
                })
            if eq_dicts:
                pb = industry_service.compute_site_power_balance(eq_dicts)
                power_mwe = pb.get("electric_mw_supply", 0.0)
                power_used_mwe = pb.get("electric_mw_demand", 0.0)

        # Corp name lookup
        corp_row = conn.execute(
            "SELECT name FROM corporations WHERE id = ?",
            (fcorp,),
        ).fetchone()
        corp_name = str(corp_row["name"]) if corp_row else fcorp

        result.append({
            "id": fid,
            "name": r["name"],
            "corp_id": fcorp,
            "corp_name": corp_name,
            "is_mine": bool(viewer_is_admin or (viewer_corp_id and fcorp == viewer_corp_id)),
            "stats": {
                "equipment_count": eq_count,
                "power_mwe": round(power_mwe, 2),
                "power_used_mwe": round(power_used_mwe, 2),
                "active_jobs": active_jobs,
                "inventory_stack_count": inventory_stack_count,
            },
        })

    return result


def get_facility_count_by_location(conn: sqlite3.Connection) -> Dict[str, int]:
    """Return {location_id: facility_count} for all locations with facilities."""
    rows = conn.execute(
        "SELECT location_id, COUNT(*) as cnt FROM facilities GROUP BY location_id"
    ).fetchall()
    return {str(r["location_id"]): r["cnt"] for r in rows}
