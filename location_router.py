"""
Location API routes.

Extracted from main.py — handles:
  /api/locations
  /api/locations/tree
  /api/surface_sites
  /api/surface_sites/{site_id}
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_service import require_login
from db import get_db

router = APIRouter(tags=["locations"])


def _main():
    """Lazy import to avoid circular dependency with main.py."""
    import main
    return main


# ── Helpers ────────────────────────────────────────────────

def build_tree(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    nodes: Dict[str, Dict[str, Any]] = {}
    children_by_parent: Dict[Optional[str], List[str]] = {}

    for r in rows:
        nodes[r["id"]] = {
            "id": r["id"],
            "name": r["name"],
            "is_group": bool(r["is_group"]),
            "sort_order": int(r["sort_order"]),
            "children": [],
        }
        children_by_parent.setdefault(r["parent_id"], []).append(r["id"])

    def sort_key(nid: str) -> Tuple[int, str]:
        n = nodes[nid]
        return (0 if n["is_group"] else 1, n["sort_order"], n["name"].lower())

    def attach(parent_id: Optional[str]) -> List[Dict[str, Any]]:
        kids = children_by_parent.get(parent_id, [])
        kids.sort(key=sort_key)
        out = []
        for kid in kids:
            n = nodes[kid]
            n["children"] = attach(kid)
            out.append(n)
        return out

    return attach(None)


# ── Routes ─────────────────────────────────────────────────

@router.get("/api/locations")
def api_locations(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_login(conn, request)
    rows = conn.execute(
        "SELECT id,name,parent_id,is_group,sort_order,x,y FROM locations ORDER BY sort_order, name"
    ).fetchall()
    metadata_by_id = _main()._location_metadata_by_id()
    locations = []
    for row in rows:
        item = dict(row)
        extra = metadata_by_id.get(str(item.get("id") or ""), {})
        if extra:
            item.update(extra)
        locations.append(item)
    return {"locations": locations}


@router.get("/api/locations/tree")
def api_locations_tree(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_login(conn, request)
    rows = conn.execute(
        "SELECT id,name,parent_id,is_group,sort_order FROM locations"
    ).fetchall()
    return {"tree": build_tree(rows)}
