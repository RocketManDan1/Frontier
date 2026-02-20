"""
Shipyard API routes.

Extracted from main.py — handles:
  /api/shipyard/preview
  /api/shipyard/build
"""

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth_service import require_login
import catalog_service
from db import get_db

router = APIRouter(tags=["shipyard"])


def _main():
    """Lazy import to avoid circular dependency with main.py."""
    import main
    return main


# ── Helpers ────────────────────────────────────────────────

def _slugify_ship_id(raw: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", raw.strip().lower()).strip("_")
    return text or "ship"


def _next_available_ship_id(conn: sqlite3.Connection, preferred: str) -> str:
    base = _slugify_ship_id(preferred)
    candidate = base
    suffix = 2
    while conn.execute("SELECT 1 FROM ships WHERE id=?", (candidate,)).fetchone():
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


# ── Pydantic models ────────────────────────────────────────

class ShipyardPreviewReq(BaseModel):
    parts: List[Any] = Field(default_factory=list)
    source_location_id: Optional[str] = None


class ShipyardBuildReq(BaseModel):
    name: str
    ship_id: Optional[str] = None
    notes: List[str] = Field(default_factory=list)
    parts: List[Any] = Field(default_factory=list)
    source_location_id: Optional[str] = None


# ── Routes ─────────────────────────────────────────────────

@router.post("/api/shipyard/preview")
def api_shipyard_preview(req: ShipyardPreviewReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    m = _main()
    source_location_id = (req.source_location_id or "").strip() or "LEO"

    require_login(conn, request)
    loc = conn.execute("SELECT id,is_group FROM locations WHERE id=?", (source_location_id,)).fetchone()
    if not loc or int(loc["is_group"]):
        raise HTTPException(status_code=400, detail="source_location_id must be a valid non-group location")

    item_ids = m.normalize_shipyard_item_ids(req.parts)
    parts = m.shipyard_parts_from_item_ids(item_ids)
    stats = m.build_ship_stats_payload(parts)
    power_balance = catalog_service.compute_power_balance(parts)
    return {
        "build_location_id": source_location_id,
        "parts": parts,
        "stats": stats,
        "power_balance": power_balance,
    }


@router.post("/api/shipyard/build")
def api_shipyard_build(req: ShipyardBuildReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    m = _main()
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    source_location_id = (req.source_location_id or "").strip() or "LEO"

    item_ids = m.normalize_shipyard_item_ids(req.parts)
    if not item_ids:
        raise HTTPException(status_code=400, detail="At least one part is required")

    require_login(conn, request)

    loc = conn.execute(
        "SELECT id,is_group FROM locations WHERE id=?",
        (source_location_id,),
    ).fetchone()
    if not loc or int(loc["is_group"]):
        raise HTTPException(status_code=400, detail="source_location_id must be a valid non-group location")

    using_inventory_source = source_location_id != "LEO"
    if using_inventory_source:
        parts = m.consume_parts_from_location_inventory(conn, source_location_id, item_ids)
    else:
        parts = m.shipyard_parts_from_item_ids(item_ids)

    if not parts:
        raise HTTPException(status_code=400, detail="No valid parts found for build")

    stats = m.build_ship_stats_payload(parts)

    preferred_id = (req.ship_id or name).strip()
    ship_id = _next_available_ship_id(conn, preferred_id)
    notes = [str(n) for n in (req.notes or []) if str(n).strip()]

    conn.execute(
        """
        INSERT INTO ships (
          id,name,shape,color,size_px,notes_json,
          location_id,from_location_id,to_location_id,departed_at,arrives_at,
          transfer_path_json,dv_planned_m_s,dock_slot,
          parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ship_id,
            name,
            "triangle",
            "#ffffff",
            12.0,
            json.dumps(notes),
            source_location_id,
            None,
            None,
            None,
            None,
            "[]",
            None,
            None,
            json.dumps(parts),
            stats["fuel_kg"],
            stats["fuel_capacity_kg"],
            stats["dry_mass_kg"],
            stats["isp_s"],
        ),
    )
    conn.commit()

    return {
        "ok": True,
        "ship": {
            "id": ship_id,
            "name": name,
            "location_id": source_location_id,
            "parts": parts,
            "notes": notes,
            "source_location_id": source_location_id,
            **stats,
            "status": "docked",
        },
    }
