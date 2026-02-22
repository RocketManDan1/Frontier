"""
Catalog & research API routes.

Extracted from main.py — handles:
  /api/catalog/items
  /api/catalog/recipes
  /api/catalog/recipes/by-category
  /api/research/tree
  /api/health
  /api/shipyard/catalog
"""

import sqlite3
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from auth_service import require_login
import catalog_service
from constants import ITEM_CATEGORIES
from db import get_db

router = APIRouter(tags=["catalog"])


def _main():
    """Lazy import to avoid circular dependency with main.py."""
    import main
    return main


@router.get("/api/catalog/items")
def api_catalog_items(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_login(conn, request)
    return {
        "item_categories": ITEM_CATEGORIES,
    }


@router.get("/api/health")
def api_health(conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    conn.execute("SELECT 1")
    return {
        "ok": True,
        "service": "earthmoon-db",
    }


@router.get("/api/catalog/recipes")
def api_catalog_recipes(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_login(conn, request)
    return {
        "recipes": sorted(
            _main().load_recipe_catalog().values(),
            key=lambda r: str(r.get("name") or "").lower(),
        ),
    }


@router.get("/api/catalog/recipes/by-category")
def api_catalog_recipes_by_category(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_login(conn, request)
    return catalog_service.build_recipe_categories_payload(_main().load_recipe_catalog())


@router.get("/api/research/tree")
def api_research_tree(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    user = require_login(conn, request)
    tree = catalog_service.build_ksp_tech_tree()

    # Include org's unlock state
    import org_service
    org_id = org_service.get_org_id_for_user(conn, user["username"])
    unlocked_ids: list = []
    if org_id:
        unlocks = org_service.get_unlocked_techs(conn, org_id)
        unlocked_ids = [u["tech_id"] for u in unlocks]
    tree["unlocked"] = unlocked_ids
    return tree


@router.get("/api/shipyard/catalog")
def api_shipyard_catalog(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_login(conn, request)
    loc_rows = conn.execute(
        "SELECT id,name FROM locations WHERE is_group=0 ORDER BY sort_order, name"
    ).fetchall()
    summary_rows = conn.execute(
        """
        SELECT location_id,
               SUM(CASE WHEN stack_type='part' THEN quantity ELSE 0 END) AS part_qty,
               SUM(CASE WHEN stack_type='resource' THEN mass_kg ELSE 0 END) AS resource_mass_kg
        FROM location_inventory_stacks
        GROUP BY location_id
        """
    ).fetchall()
    inv_summary = {
        str(r["location_id"]): {
            "part_qty": float(r["part_qty"] or 0.0),
            "resource_mass_kg": float(r["resource_mass_kg"] or 0.0),
        }
        for r in summary_rows
    }

    payload = _main().build_shipyard_catalog_payload()
    payload["build_source_locations"] = [
        {
            "id": str(loc["id"]),
            "name": str(loc["name"]),
            "inventory_part_qty": inv_summary.get(str(loc["id"]), {}).get("part_qty", 0.0),
            "inventory_resource_mass_kg": inv_summary.get(str(loc["id"]), {}).get("resource_mass_kg", 0.0),
        }
        for loc in loc_rows
        if str(loc["id"]) == "LEO"
        or inv_summary.get(str(loc["id"]), {}).get("part_qty", 0.0) > 0.0
        or inv_summary.get(str(loc["id"]), {}).get("resource_mass_kg", 0.0) > 0.0
    ]
    return payload
