"""
Location API routes.

Extracted from main.py — handles:
  /api/locations
  /api/locations/tree
  /api/surface_sites
  /api/surface_sites/{site_id}
"""

import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_service import require_login
import celestial_config
from db import get_db
from sim_service import game_now_s

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
def api_locations(
    request: Request,
    dynamic: bool = True,
    t: Optional[float] = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    require_login(conn, request)

    effective_game_time_s: Optional[float] = None
    if dynamic:
        effective_game_time_s = float(t) if t is not None else float(game_now_s())
        try:
            cfg = celestial_config.load_celestial_config()
            location_rows, _ = celestial_config.build_locations_and_edges(cfg, game_time_s=effective_game_time_s)
        except celestial_config.CelestialConfigError as exc:
            raise HTTPException(status_code=500, detail=f"Dynamic location generation failed: {exc}")

        rows = [
            {
                "id": row[0],
                "name": row[1],
                "parent_id": row[2],
                "is_group": row[3],
                "sort_order": row[4],
                "x": row[5],
                "y": row[6],
            }
            for row in location_rows
        ]
        rows.sort(key=lambda item: (int(item.get("sort_order", 100)), str(item.get("name") or "")))
    else:
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
    response: Dict[str, Any] = {"locations": locations}
    if effective_game_time_s is not None:
        response["game_time_s"] = effective_game_time_s
    return response


@router.get("/api/locations/tree")
def api_locations_tree(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_login(conn, request)
    rows = conn.execute(
        "SELECT id,name,parent_id,is_group,sort_order FROM locations"
    ).fetchall()
    return {"tree": build_tree(rows)}


# ── Surface Sites ──────────────────────────────────────────

@router.get("/api/surface_sites")
def api_surface_sites(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """List all surface sites. Resource distributions are hidden until prospected by the user's org."""
    user = require_login(conn, request)

    # Get org for prospecting visibility
    import org_service
    corp_id = str(user.get("corp_id") or "") if hasattr(user, "get") else ""
    if corp_id:
        org_id = org_service.get_org_id_for_corp(conn, corp_id)
    else:
        org_id = org_service.get_org_id_for_user(conn, user["username"])

    # Get prospected site IDs for this org
    prospected_sites: set = set()
    if org_id:
        for r in conn.execute(
            "SELECT DISTINCT site_location_id FROM prospecting_results WHERE org_id = ?",
            (org_id,),
        ).fetchall():
            prospected_sites.add(str(r["site_location_id"]))

    sites = conn.execute(
        """
        SELECT ss.location_id, ss.body_id, ss.orbit_node_id, ss.gravity_m_s2,
               l.name AS site_name
        FROM surface_sites ss
        JOIN locations l ON l.id = ss.location_id
        ORDER BY l.sort_order, l.name
        """
    ).fetchall()

    # Load all resource distributions in one query
    all_resources = conn.execute(
        """
        SELECT site_location_id, resource_id, mass_fraction
        FROM surface_site_resources
        ORDER BY site_location_id, mass_fraction DESC
        """
    ).fetchall()

    # Group resources by site
    resources_by_site: Dict[str, List[Dict[str, Any]]] = {}
    for r in all_resources:
        site_id = r["site_location_id"]
        resources_by_site.setdefault(site_id, []).append({
            "resource_id": r["resource_id"],
            "mass_fraction": float(r["mass_fraction"]),
        })

    result = []
    for s in sites:
        site_id = s["location_id"]
        is_prospected = site_id in prospected_sites
        result.append({
            "location_id": site_id,
            "name": s["site_name"],
            "body_id": s["body_id"],
            "orbit_node_id": s["orbit_node_id"],
            "gravity_m_s2": float(s["gravity_m_s2"]),
            "is_prospected": is_prospected,
            "resource_distribution": resources_by_site.get(site_id, []) if is_prospected else [],
        })

    return {"surface_sites": result}


@router.get("/api/surface_sites/{site_id}")
def api_surface_site_detail(site_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """Get detailed info for a single surface site. Resources hidden until prospected."""
    user = require_login(conn, request)

    import org_service
    corp_id = str(user.get("corp_id") or "") if hasattr(user, "get") else ""
    if corp_id:
        org_id = org_service.get_org_id_for_corp(conn, corp_id)
    else:
        org_id = org_service.get_org_id_for_user(conn, user["username"])

    site = conn.execute(
        """
        SELECT ss.location_id, ss.body_id, ss.orbit_node_id, ss.gravity_m_s2,
               l.name AS site_name
        FROM surface_sites ss
        JOIN locations l ON l.id = ss.location_id
        WHERE ss.location_id = ?
        """,
        (site_id,),
    ).fetchone()

    if not site:
        raise HTTPException(status_code=404, detail="Surface site not found")

    is_prospected = False
    if org_id:
        is_prospected = org_service.is_site_prospected(conn, org_id, site_id)

    resources = []
    if is_prospected:
        rows = conn.execute(
            """
            SELECT resource_id, mass_fraction
            FROM surface_site_resources
            WHERE site_location_id = ?
            ORDER BY mass_fraction DESC
            """,
            (site_id,),
        ).fetchall()
        resources = [
            {
                "resource_id": r["resource_id"],
                "mass_fraction": float(r["mass_fraction"]),
            }
            for r in rows
        ]

    return {
        "location_id": site["location_id"],
        "name": site["site_name"],
        "body_id": site["body_id"],
        "orbit_node_id": site["orbit_node_id"],
        "gravity_m_s2": float(site["gravity_m_s2"]),
        "is_prospected": is_prospected,
        "resource_distribution": resources,
    }
