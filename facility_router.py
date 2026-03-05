"""
Facility API routes.

  GET    /api/facilities/{location_id}         — list all facilities at a location
  POST   /api/facilities/create                — create a new facility
  PATCH  /api/facilities/{facility_id}/rename   — rename a facility
  DELETE /api/facilities/{facility_id}          — delete an empty facility
"""

import sqlite3
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth_service import require_login
from db import get_db
import facility_service

router = APIRouter(tags=["facilities"])


def _get_corp_id(user) -> str:
    if user is None:
        return ""
    if hasattr(user, "get"):
        return str(user.get("corp_id") or "")
    try:
        return str(user["corp_id"])
    except (KeyError, IndexError):
        return ""


def _get_actor_name(user) -> str:
    if user is None:
        return "system"
    username = ""
    if hasattr(user, "get"):
        username = str(user.get("username") or "")
    else:
        try:
            username = str(user["username"])
        except (KeyError, IndexError):
            pass
    if username:
        return username
    corp_name = ""
    if hasattr(user, "get"):
        corp_name = str(user.get("corp_name") or "")
    if corp_name:
        return f"corp:{corp_name}"
    cid = _get_corp_id(user)
    return f"corp:{cid}" if cid else "system"


# ── Request Models ─────────────────────────────────────────────────────────────


class CreateFacilityRequest(BaseModel):
    location_id: str
    name: str


class RenameFacilityRequest(BaseModel):
    name: str


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("/api/facilities/{location_id}")
def api_list_facilities(
    location_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """List all facilities at a location (name, corp, basic stats)."""
    user = require_login(conn, request)
    corp_id = _get_corp_id(user)

    loc = conn.execute("SELECT id, name FROM locations WHERE id = ?", (location_id,)).fetchone()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")

    facilities = facility_service.list_facilities_at_location(
        conn, location_id, viewer_corp_id=corp_id
    )

    return {
        "location_id": location_id,
        "location_name": str(loc["name"]),
        "facilities": facilities,
    }


@router.post("/api/facilities/create")
def api_create_facility(
    body: CreateFacilityRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Create a new facility at a location."""
    user = require_login(conn, request)
    corp_id = _get_corp_id(user)
    if not corp_id:
        raise HTTPException(status_code=400, detail="Corporation session required")

    loc = conn.execute("SELECT id, name FROM locations WHERE id = ?", (body.location_id,)).fetchone()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")

    name = (body.name or "").strip()
    if not name or len(name) > 50:
        raise HTTPException(status_code=400, detail="Name must be 1–50 characters")

    # Check uniqueness
    existing = conn.execute(
        "SELECT id FROM facilities WHERE location_id = ? AND corp_id = ? AND name = ?",
        (body.location_id, corp_id, name),
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="A facility with that name already exists at this location")

    actor = _get_actor_name(user)
    fid = facility_service.create_facility(conn, body.location_id, corp_id, name, actor)

    return {
        "ok": True,
        "facility_id": fid,
        "facility": {
            "id": fid,
            "location_id": body.location_id,
            "name": name,
            "corp_id": corp_id,
        },
    }


@router.patch("/api/facilities/{facility_id}/rename")
def api_rename_facility(
    facility_id: str,
    body: RenameFacilityRequest,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Rename a facility the caller owns."""
    user = require_login(conn, request)
    corp_id = _get_corp_id(user)
    fac = facility_service.require_facility_owner(conn, facility_id, corp_id)

    name = (body.name or "").strip()
    if not name or len(name) > 50:
        raise HTTPException(status_code=400, detail="Name must be 1–50 characters")

    # Uniqueness check
    dup = conn.execute(
        "SELECT id FROM facilities WHERE location_id = ? AND corp_id = ? AND name = ? AND id != ?",
        (fac["location_id"], fac["corp_id"], name, facility_id),
    ).fetchone()
    if dup:
        raise HTTPException(status_code=409, detail="A facility with that name already exists at this location")

    conn.execute("UPDATE facilities SET name = ? WHERE id = ?", (name, facility_id))
    conn.commit()

    return {"ok": True, "facility_id": facility_id, "name": name}


@router.delete("/api/facilities/{facility_id}")
def api_delete_facility(
    facility_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Delete a facility. Must be empty of facility-owned industry state."""
    user = require_login(conn, request)
    corp_id = _get_corp_id(user)
    fac = facility_service.require_facility_owner(conn, facility_id, corp_id)

    # Check emptiness
    eq = conn.execute(
        "SELECT COUNT(*) as cnt FROM deployed_equipment WHERE facility_id = ?",
        (facility_id,),
    ).fetchone()
    if eq and eq["cnt"] > 0:
        raise HTTPException(status_code=400, detail="Facility still has deployed equipment — undeploy first")

    jobs = conn.execute(
        "SELECT COUNT(*) as cnt FROM production_jobs WHERE facility_id = ? AND status = 'active'",
        (facility_id,),
    ).fetchone()
    if jobs and jobs["cnt"] > 0:
        raise HTTPException(status_code=400, detail="Facility has active jobs — cancel them first")

    conn.execute("DELETE FROM facilities WHERE id = ?", (facility_id,))
    conn.commit()

    return {"ok": True, "deleted": facility_id}
