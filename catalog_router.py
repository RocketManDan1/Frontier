"""
Catalog & research API routes.

Extracted from main.py — handles:
  /api/catalog/items
  /api/catalog/item/{item_id}
  /api/catalog/recipes
  /api/catalog/recipes/by-category
  /api/research/tree
  /api/health
  /api/shipyard/catalog
"""

import sqlite3
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

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


@router.get("/api/catalog/browse")
def api_catalog_browse(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    """Return a flat list of all game items for buy-order/barter pickers.

    Returns three groups: raw_materials, finished_goods, modules.
    Each item has: id, name, category.
    """
    require_login(conn, request)

    raw_materials = []
    finished_goods = []
    modules = []

    # Resources (raw materials + finished goods)
    for rid, r in catalog_service.load_resource_catalog().items():
        cat_id = str(r.get("category_id") or "resource").lower()
        entry = {"id": str(rid), "name": str(r.get("name") or rid), "category": cat_id}
        if cat_id in ("raw_material", "fuel"):
            raw_materials.append(entry)
        else:
            finished_goods.append(entry)

    # Equipment / modules
    module_loaders = [
        ("thruster", catalog_service.load_thruster_main_catalog),
        ("reactor", catalog_service.load_reactor_catalog),
        ("generator", catalog_service.load_generator_catalog),
        ("radiator", catalog_service.load_radiator_catalog),
        ("storage", catalog_service.load_storage_catalog),
        ("robonaut", catalog_service.load_robonaut_catalog),
        ("constructor", catalog_service.load_constructor_catalog),
        ("refinery", catalog_service.load_refinery_catalog),
    ]
    for cat_name, loader in module_loaders:
        for mid, m in loader().items():
            modules.append({
                "id": str(mid),
                "name": str(m.get("name") or mid),
                "category": cat_name,
            })

    key = lambda x: x["name"].lower()
    return {
        "raw_materials": sorted(raw_materials, key=key),
        "finished_goods": sorted(finished_goods, key=key),
        "modules": sorted(modules, key=key),
    }


@router.get("/api/health")
def api_health(conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    conn.execute("SELECT 1")
    return {
        "ok": True,
        "service": "frontier-sol-2000",
    }


@router.get("/api/catalog/item/{item_id}")
def api_catalog_item_info(item_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_login(conn, request)
    info = catalog_service.get_item_info(item_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found")
    return {"item": info}


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
    corp_id = str(user.get("corp_id") or "") if hasattr(user, "get") else ""
    if corp_id:
        org_id = org_service.get_org_id_for_corp(conn, corp_id)
    else:
        org_id = org_service.get_org_id_for_user(conn, user["username"])
    unlocked_ids: list = []
    if org_id:
        unlocks = org_service.get_unlocked_techs(conn, org_id)
        unlocked_ids = [u["tech_id"] for u in unlocks]
    tree["unlocked"] = unlocked_ids
    return tree


@router.get("/api/shipyard/catalog")
def api_shipyard_catalog(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    user = require_login(conn, request)
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    loc_rows = conn.execute(
        "SELECT id,name FROM locations WHERE is_group=0 ORDER BY sort_order, name"
    ).fetchall()

    if corp_id is not None:
        summary_rows = conn.execute(
            """
            SELECT location_id,
                   SUM(CASE WHEN stack_type='part' THEN quantity ELSE 0 END) AS part_qty,
                   SUM(CASE WHEN stack_type='resource' THEN mass_kg ELSE 0 END) AS resource_mass_kg
            FROM location_inventory_stacks
            WHERE corp_id=?
            GROUP BY location_id
            """,
            (corp_id,),
        ).fetchall()
    else:
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
        if inv_summary.get(str(loc["id"]), {}).get("part_qty", 0.0) > 0.0
        or inv_summary.get(str(loc["id"]), {}).get("resource_mass_kg", 0.0) > 0.0
    ]
    return payload
