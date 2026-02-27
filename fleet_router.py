"""
Fleet & ship-operation API routes.

Extracted from main.py — handles:
  /api/state
  /api/time
  /api/transfer_quote
  /api/transfer_quote_advanced
  /api/ships/{ship_id}/transfer
  /api/ships/{ship_id}/inventory/jettison
  /api/ships/{ship_id}/deconstruct
  /api/ships/{ship_id}/inventory/deploy
"""

import json
import heapq
import logging
import math
import sqlite3
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from auth_service import require_login
import catalog_service
import celestial_config
import transfer_planner
from db import get_db
from sim_service import (
    effective_time_scale,
    game_now_s,
    simulation_paused,
)

router = APIRouter(tags=["fleet"])


def _main():
    """Lazy import to avoid circular dependency with main.py."""
    import main
    return main


def _require_ship_ownership(conn, request, ship_id: str):
    """Verify the requesting user/corp owns the ship. Raises 403 if not."""
    from auth_service import get_current_user
    user = get_current_user(conn, request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    # Admin can operate on any ship
    if user.get("is_admin") if hasattr(user, "get") else user["is_admin"]:
        return
    corp_id = user.get("corp_id") if hasattr(user, "get") else None
    if not corp_id:
        return  # non-corp non-admin — shouldn't happen but be lenient
    row = conn.execute("SELECT corp_id FROM ships WHERE id=?", (ship_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ship not found")
    if str(row["corp_id"] or "") != corp_id:
        raise HTTPException(status_code=403, detail="This ship belongs to another corporation")


# ── Pydantic models ────────────────────────────────────────

class TransferReq(BaseModel):
    to_location_id: str


class InventoryContainerReq(BaseModel):
    container_index: int


class ShipDeconstructReq(BaseModel):
    keep_ship_record: bool = False


# ── Routes ─────────────────────────────────────────────────

@router.get("/api/time")
def api_time(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_login(conn, request)
    return {
        "server_time": game_now_s(),
        "time_scale": effective_time_scale(),
        "paused": simulation_paused(),
    }


@router.get("/api/transfer_quote")
def api_transfer_quote(from_id: str, to_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    require_login(conn, request)
    quote = _compute_route_quote(
        conn,
        from_id=from_id,
        to_id=to_id,
        departure_time_s=game_now_s(),
        extra_dv_fraction=0.0,
    )
    if not quote:
        raise HTTPException(status_code=404, detail="No transfer data for that pair")

    result: Dict[str, Any] = {
        "from_id": from_id,
        "to_id": to_id,
        "dv_m_s": float(quote["dv_m_s"]),
        "tof_s": float(quote["tof_s"]),
        "departure_time": float(quote["departure_time"]),
        "is_interplanetary": bool(quote["is_interplanetary"]),
        "route_mode": str(quote.get("route_mode") or "direct"),
    }

    # Include Δv breakdown for interplanetary routes
    if quote.get("is_interplanetary"):
        local_dep = float(quote.get("local_departure_dv_m_s") or 0)
        local_arr = float(quote.get("local_arrival_dv_m_s") or 0)
        result["local_departure_dv_m_s"] = local_dep
        result["interplanetary_dv_m_s"] = round(float(quote["dv_m_s"]) - local_dep - local_arr, 1)
        result["local_arrival_dv_m_s"] = local_arr
        if quote.get("gateway_departure"):
            result["gateway_departure"] = str(quote["gateway_departure"])
        if quote.get("gateway_arrival"):
            result["gateway_arrival"] = str(quote["gateway_arrival"])

    # Check if origin or destination are surface sites
    check_ids = list(dict.fromkeys([from_id, to_id]))
    placeholders = ",".join("?" for _ in check_ids)
    site_rows = conn.execute(
        f"SELECT location_id, body_id, gravity_m_s2 FROM surface_sites WHERE location_id IN ({placeholders})",
        check_ids,
    ).fetchall()
    if site_rows:
        result["surface_sites"] = [
            {
                "location_id": sr["location_id"],
                "body_id": sr["body_id"],
                "gravity_m_s2": float(sr["gravity_m_s2"]),
                "min_twr": 1.0,
            }
            for sr in site_rows
        ]

    return result


_ROUTE_CACHE_BUCKET_S = 6.0 * 3600.0
_ROUTE_CACHE_MAX = 512
_ROUTE_QUOTE_CACHE: Dict[Tuple[str, str, str, int, int], Dict[str, Any]] = {}

# Position snapshot bucketing for in-transit interpolation
_DYN_LOC_BUCKET_S = 300  # 5 minutes


@lru_cache(maxsize=64)
def _dynamic_locations_by_id(game_time_bucket: int) -> Dict[str, Tuple[float, float]]:
    cfg = celestial_config.load_celestial_config()
    rows, _ = celestial_config.build_locations_and_edges(
        cfg, game_time_s=float(game_time_bucket) * _DYN_LOC_BUCKET_S,
    )
    return {str(row[0]): (float(row[5]), float(row[6])) for row in rows}


def _clone_json_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(data))


def _evict_route_cache_if_needed() -> None:
    while len(_ROUTE_QUOTE_CACHE) > _ROUTE_CACHE_MAX:
        oldest_key = next(iter(_ROUTE_QUOTE_CACHE.keys()))
        _ROUTE_QUOTE_CACHE.pop(oldest_key, None)


def _edge_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM transfer_meta WHERE key='edges_hash'").fetchone()
    if row and str(row["value"] or "").strip():
        return str(row["value"])
    return str(_main().hash_edges(conn))


def _compute_interplanetary_leg_quote(
    from_id: str,
    to_id: str,
    departure_time_s: float,
    extra_dv_fraction: float,
) -> Optional[Dict[str, float]]:
    """Delegate to transfer_planner (Lambert-based)."""
    return transfer_planner.compute_interplanetary_leg(
        from_location=from_id,
        to_location=to_id,
        departure_time_s=departure_time_s,
        extra_dv_fraction=extra_dv_fraction,
    )


# ── Direct A→B transfer quote system ──────────────────────
# Replaces the old Dijkstra multi-hop path system. Transfers are
# always computed as a single A→B segment. For interplanetary
# transfers from non-gateway nodes, gateway costs are auto-resolved.

def _find_gateway_pair(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
) -> Optional[Tuple[str, str, float, float, float, float]]:
    """Find the best interplanetary gateway pair for a cross-body transfer.

    Searches for interplanetary edges connecting departure-body locations
    to arrival-body locations.  For each candidate gateway pair, adds the
    local edge cost from *from_id* to the departure gateway and from the
    arrival gateway to *to_id*.

    Returns ``(dep_gateway, arr_gateway,
               local_dep_dv, local_dep_tof,
               local_arr_dv, local_arr_tof)``
    or ``None`` if no interplanetary connection exists.
    """
    from_body = transfer_planner.location_parent_body(from_id)
    to_body = transfer_planner.location_parent_body(to_id)
    if not from_body or not to_body:
        return None

    from_helio = transfer_planner._resolve_heliocentric_body(from_body)
    to_helio = transfer_planner._resolve_heliocentric_body(to_body)
    if from_helio == to_helio:
        return None  # same heliocentric body — not interplanetary

    # Build a set of all location IDs that belong to each heliocentric body
    loc_map = transfer_planner._get_location_body_map()
    from_body_locs = set()
    to_body_locs = set()
    for loc_id, body_id in loc_map.items():
        helio = transfer_planner._resolve_heliocentric_body(body_id)
        if helio == from_helio:
            from_body_locs.add(loc_id)
        elif helio == to_helio:
            to_body_locs.add(loc_id)

    # Find all interplanetary edges between these two body groups
    ip_edges = conn.execute(
        "SELECT from_id, to_id, dv_m_s, tof_s FROM transfer_edges WHERE edge_type = 'interplanetary'"
    ).fetchall()

    candidates: List[Tuple[str, str]] = []
    for edge in ip_edges:
        ef = str(edge["from_id"])
        et = str(edge["to_id"])
        if ef in from_body_locs and et in to_body_locs:
            candidates.append((ef, et))

    if not candidates:
        return None

    # For each candidate, compute total local hop cost
    best: Optional[Tuple[str, str, float, float, float, float]] = None
    best_total_dv = float("inf")

    for dep_gw, arr_gw in candidates:
        # Local departure cost (from_id → dep_gw)
        if dep_gw == from_id:
            local_dep_dv, local_dep_tof = 0.0, 0.0
        else:
            dep_edge = conn.execute(
                "SELECT dv_m_s, tof_s FROM transfer_edges WHERE from_id=? AND to_id=?",
                (from_id, dep_gw),
            ).fetchone()
            if not dep_edge:
                # Try 2-hop via shared orbit node (surface site → orbit → gateway)
                dep_edge = _find_local_path_cost(conn, from_id, dep_gw)
            if not dep_edge:
                continue  # no local route to this gateway
            local_dep_dv = float(dep_edge["dv_m_s"])
            local_dep_tof = float(dep_edge["tof_s"])

        # Local arrival cost (arr_gw → to_id)
        if arr_gw == to_id:
            local_arr_dv, local_arr_tof = 0.0, 0.0
        else:
            arr_edge = conn.execute(
                "SELECT dv_m_s, tof_s FROM transfer_edges WHERE from_id=? AND to_id=?",
                (arr_gw, to_id),
            ).fetchone()
            if not arr_edge:
                arr_edge = _find_local_path_cost(conn, arr_gw, to_id)
            if not arr_edge:
                continue
            local_arr_dv = float(arr_edge["dv_m_s"])
            local_arr_tof = float(arr_edge["tof_s"])

        total_local_dv = local_dep_dv + local_arr_dv
        if total_local_dv < best_total_dv:
            best_total_dv = total_local_dv
            best = (dep_gw, arr_gw, local_dep_dv, local_dep_tof, local_arr_dv, local_arr_tof)

    return best


def _find_local_path_cost(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
) -> Optional[Dict[str, float]]:
    """Find the cheapest local (non-interplanetary) route between two locations.

    Uses Dijkstra over all non-interplanetary edges to handle multi-hop
    local transfers (e.g. LEO → GEO → L1 → LLO). Returns a dict with
    combined ``dv_m_s`` and ``tof_s``, or None if no local path exists.
    """
    # Load all non-interplanetary edges into an adjacency list
    all_edges = conn.execute(
        "SELECT from_id, to_id, dv_m_s, tof_s FROM transfer_edges WHERE edge_type IS NULL OR edge_type != 'interplanetary'"
    ).fetchall()
    adj: Dict[str, List[Tuple[str, float, float]]] = {}
    for e in all_edges:
        ef = str(e["from_id"])
        et = str(e["to_id"])
        if ef not in adj:
            adj[ef] = []
        adj[ef].append((et, float(e["dv_m_s"]), float(e["tof_s"])))

    # Dijkstra by dv
    dist: Dict[str, float] = {from_id: 0.0}
    tof_acc: Dict[str, float] = {from_id: 0.0}
    heap: List[Tuple[float, str]] = [(0.0, from_id)]
    visited: set = set()

    while heap:
        cost, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node == to_id:
            return {"dv_m_s": dist[to_id], "tof_s": tof_acc[to_id]}
        for neighbor, dv, tof in adj.get(node, []):
            new_cost = cost + dv
            if new_cost < dist.get(neighbor, float("inf")):
                dist[neighbor] = new_cost
                tof_acc[neighbor] = tof_acc[node] + tof
                heapq.heappush(heap, (new_cost, neighbor))

    return None


def _compute_direct_quote(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    departure_time_s: float,
    extra_dv_fraction: float,
) -> Optional[Dict[str, Any]]:
    """Compute a direct A→B transfer quote.

    For local transfers (same body): look up the direct edge (or 2-hop).
    For interplanetary transfers: auto-resolve gateways, sum local +
    Lambert costs, return a single-segment quote.
    """
    if from_id == to_id:
        return {
            "from_id": from_id,
            "to_id": to_id,
            "base_dv_m_s": 0.0,
            "base_tof_s": 0.0,
            "phase_adjusted_dv_m_s": 0.0,
            "phase_multiplier": 1.0,
            "dv_m_s": 0.0,
            "tof_s": 0.0,
            "extra_dv_fraction": float(extra_dv_fraction),
            "departure_time": float(departure_time_s),
            "is_interplanetary": False,
            "orbital": None,
            "route_mode": "direct",
        }

    # ── 1. Check for direct edge ──────────────────────────
    direct = conn.execute(
        "SELECT dv_m_s, tof_s, edge_type FROM transfer_edges WHERE from_id=? AND to_id=?",
        (from_id, to_id),
    ).fetchone()
    direct_edge_type = str((direct["edge_type"] if direct else "") or "")

    is_ip = _is_interplanetary(from_id, to_id)

    if direct and not is_ip and direct_edge_type != "interplanetary":
        # Local direct edge — simple case
        base_dv = float(direct["dv_m_s"])
        base_tof = float(direct["tof_s"])
        final_dv = base_dv * (1.0 + max(0.0, float(extra_dv_fraction)))
        final_tof = _excess_dv_time_reduction(base_tof, base_dv, max(0.0, float(extra_dv_fraction)))
        return {
            "from_id": from_id,
            "to_id": to_id,
            "base_dv_m_s": base_dv,
            "base_tof_s": base_tof,
            "phase_adjusted_dv_m_s": base_dv,
            "phase_multiplier": 1.0,
            "dv_m_s": final_dv,
            "tof_s": final_tof,
            "extra_dv_fraction": float(extra_dv_fraction),
            "departure_time": float(departure_time_s),
            "is_interplanetary": False,
            "orbital": None,
            "route_mode": "direct-local",
        }

    # ── 2. Interplanetary transfer ────────────────────────
    if is_ip:
        # Try direct interplanetary edge first (gateway-to-gateway)
        if direct and str(direct["edge_type"] or "") == "interplanetary":
            orbital = _compute_interplanetary_leg_quote(from_id, to_id, departure_time_s, extra_dv_fraction)
            if orbital:
                return {
                    "from_id": from_id,
                    "to_id": to_id,
                    "base_dv_m_s": float(orbital["base_dv_m_s"]),
                    "base_tof_s": float(orbital["base_tof_s"]),
                    "phase_adjusted_dv_m_s": float(orbital["phase_adjusted_dv_m_s"]),
                    "phase_multiplier": float(orbital["phase_multiplier"]),
                    "dv_m_s": float(orbital["dv_m_s"]),
                    "tof_s": float(orbital["tof_s"]),
                    "extra_dv_fraction": float(extra_dv_fraction),
                    "departure_time": float(departure_time_s),
                    "is_interplanetary": True,
                    "orbital": orbital,
                    "local_departure_dv_m_s": 0.0,
                    "local_departure_tof_s": 0.0,
                    "local_arrival_dv_m_s": 0.0,
                    "local_arrival_tof_s": 0.0,
                    "route_mode": "direct-lambert",
                }

        # Auto-resolve gateways for non-gateway interplanetary transfers
        gw = _find_gateway_pair(conn, from_id, to_id)
        if not gw:
            return None
        dep_gw, arr_gw, local_dep_dv, local_dep_tof, local_arr_dv, local_arr_tof = gw

        # Lambert solve for the interplanetary segment
        orbital = _compute_interplanetary_leg_quote(dep_gw, arr_gw, departure_time_s + local_dep_tof, extra_dv_fraction)
        if not orbital:
            return None

        # Sum: local departure + Lambert + local arrival
        ip_base_dv = float(orbital["base_dv_m_s"])
        ip_base_tof = float(orbital["base_tof_s"])
        ip_final_dv = float(orbital["dv_m_s"])
        ip_final_tof = float(orbital["tof_s"])

        total_base_dv = local_dep_dv + ip_base_dv + local_arr_dv
        total_base_tof = local_dep_tof + ip_base_tof + local_arr_tof
        total_dv = local_dep_dv + ip_final_dv + local_arr_dv
        total_tof = local_dep_tof + ip_final_tof + local_arr_tof
        # Phase-adjusted is the Lambert value plus local (local is not phase-dependent)
        total_phase_adj_dv = local_dep_dv + float(orbital["phase_adjusted_dv_m_s"]) + local_arr_dv

        return {
            "from_id": from_id,
            "to_id": to_id,
            "base_dv_m_s": total_base_dv,
            "base_tof_s": total_base_tof,
            "phase_adjusted_dv_m_s": total_phase_adj_dv,
            "phase_multiplier": float(orbital["phase_multiplier"]),
            "dv_m_s": total_dv,
            "tof_s": total_tof,
            "extra_dv_fraction": float(extra_dv_fraction),
            "departure_time": float(departure_time_s),
            "is_interplanetary": True,
            "orbital": orbital,
            "local_departure_dv_m_s": local_dep_dv,
            "local_departure_tof_s": local_dep_tof,
            "local_arrival_dv_m_s": local_arr_dv,
            "local_arrival_tof_s": local_arr_tof,
            "gateway_departure": dep_gw,
            "gateway_arrival": arr_gw,
            "route_mode": "direct-gateway",
        }

    # ── 3. Same-body but no direct edge — try local path ──
    hop2 = _find_local_path_cost(conn, from_id, to_id)
    if hop2:
        base_dv = float(hop2["dv_m_s"])
        base_tof = float(hop2["tof_s"])
        final_dv = base_dv * (1.0 + max(0.0, float(extra_dv_fraction)))
        final_tof = _excess_dv_time_reduction(base_tof, base_dv, max(0.0, float(extra_dv_fraction)))
        return {
            "from_id": from_id,
            "to_id": to_id,
            "base_dv_m_s": base_dv,
            "base_tof_s": base_tof,
            "phase_adjusted_dv_m_s": base_dv,
            "phase_multiplier": 1.0,
            "dv_m_s": final_dv,
            "tof_s": final_tof,
            "extra_dv_fraction": float(extra_dv_fraction),
            "departure_time": float(departure_time_s),
            "is_interplanetary": False,
            "orbital": None,
            "route_mode": "local-multihop",
        }

    return None  # unreachable pair


def _compute_route_quote(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    departure_time_s: float,
    extra_dv_fraction: float,
) -> Optional[Dict[str, Any]]:
    dep_bucket = int(float(departure_time_s) // _ROUTE_CACHE_BUCKET_S)
    extra_bucket = int(round(float(extra_dv_fraction) * 10000.0))
    edge_hash = _edge_hash(conn)
    cache_key = (edge_hash, str(from_id), str(to_id), dep_bucket, extra_bucket)
    cached = _ROUTE_QUOTE_CACHE.get(cache_key)
    if cached is not None:
        return _clone_json_dict(cached)

    solved = _compute_direct_quote(
        conn,
        from_id=from_id,
        to_id=to_id,
        departure_time_s=departure_time_s,
        extra_dv_fraction=extra_dv_fraction,
    )
    if solved is None:
        return None

    _ROUTE_QUOTE_CACHE[cache_key] = _clone_json_dict(solved)
    _evict_route_cache_if_needed()
    return solved


def _is_interplanetary(from_id: str, to_id: str) -> bool:
    """True if the transfer crosses between different heliocentric bodies."""
    return transfer_planner.is_interplanetary(from_id, to_id)


def _excess_dv_time_reduction(base_tof_s: float, base_dv_m_s: float, extra_dv_fraction: float) -> float:
    """
    Given extra delta-v (as fraction above base), compute reduced TOF.
    Uses energy-based approximation: t_new = t_base * (v_base / v_new)
    where v is characteristic velocity (proportional to sqrt of vis-viva energy).
    extra_dv_fraction = 0 means Hohmann, 1.0 means 2x the delta-v.
    """
    if base_tof_s <= 0 or extra_dv_fraction <= 0:
        return base_tof_s

    # Energy scales roughly with v^2; extra dv increases transfer orbit energy
    # Time reduction approximation: TOF ~ TOF_base / (1 + k*f) where f is fractional excess
    # A more physical model: doubling dv roughly halves transit time for interplanetary
    # Using: tof_new = tof_base / (1 + extra_dv_fraction)^0.6
    reduction = 1.0 / ((1.0 + extra_dv_fraction) ** 0.6)
    return max(3600.0, base_tof_s * reduction)  # Never less than 1 hour


@router.get("/api/transfer/porkchop")
def api_transfer_porkchop(
    from_id: str,
    to_id: str,
    request: Request,
    departure_start: Optional[float] = Query(None, description="Earliest departure game-time (epoch s). Defaults to now."),
    departure_end: Optional[float] = Query(None, description="Latest departure game-time. Defaults to start + synodic period."),
    tof_min_days: float = Query(30.0, ge=1.0, le=3650.0, description="Minimum time of flight in days"),
    tof_max_days: Optional[float] = Query(None, description="Maximum time of flight in days. Auto-computed if omitted."),
    grid_size: int = Query(40, ge=5, le=100, description="Grid resolution (NxN)"),
    max_revs: int = Query(0, ge=0, le=3, description="Max Lambert revolution count"),
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """Compute a porkchop plot for an interplanetary transfer.

    Returns a 2D grid of Δv values indexed by departure time × TOF,
    plus the top-5 best-Δv solutions with full details.
    """
    require_login(conn, request)

    now = game_now_s()

    # Resolve body pair for auto-defaults
    from_body = transfer_planner.location_parent_body(from_id)
    to_body = transfer_planner.location_parent_body(to_id)
    if not from_body or not to_body:
        raise HTTPException(status_code=400, detail="Unknown location(s)")

    from_helio = transfer_planner._resolve_heliocentric_body(from_body)
    to_helio = transfer_planner._resolve_heliocentric_body(to_body)
    if from_helio == to_helio:
        raise HTTPException(status_code=400, detail="Not an interplanetary transfer")

    # Auto-compute departure window from synodic period
    synodic = transfer_planner.get_synodic_period_s(from_helio, to_helio)
    if synodic is None or synodic <= 0:
        synodic = 365.25 * 86400.0  # fallback: 1 year

    dep_start = departure_start if departure_start is not None else now
    dep_end = departure_end if departure_end is not None else dep_start + synodic

    if dep_end <= dep_start:
        raise HTTPException(status_code=400, detail="departure_end must be after departure_start")

    # Auto-compute TOF range from Hohmann estimate
    cfg = transfer_planner._get_config()
    mu_sun = celestial_config.get_body_mu(cfg, "sun")
    try:
        r1, _ = celestial_config.compute_body_state(cfg, from_helio, dep_start)
        r2, _ = celestial_config.compute_body_state(cfg, to_helio, dep_start)
        import math as _math
        r1_km = _math.sqrt(r1[0]**2 + r1[1]**2 + r1[2]**2)
        r2_km = _math.sqrt(r2[0]**2 + r2[1]**2 + r2[2]**2)
        hohmann_tof_s = _math.pi * _math.sqrt(((r1_km + r2_km) / 2.0) ** 3 / mu_sun)
    except Exception:
        hohmann_tof_s = 200.0 * 86400.0

    tof_min_s = tof_min_days * 86400.0
    if tof_max_days is not None:
        tof_max_s = tof_max_days * 86400.0
    else:
        # Default: 0.3× to 2.5× Hohmann TOF, clamped
        tof_max_s = min(hohmann_tof_s * 2.5, 3650.0 * 86400.0)
        tof_min_s = max(tof_min_s, hohmann_tof_s * 0.3)

    if tof_max_s <= tof_min_s:
        tof_max_s = tof_min_s + 30.0 * 86400.0

    result = transfer_planner.compute_porkchop(
        from_location=from_id,
        to_location=to_id,
        departure_start_s=dep_start,
        departure_end_s=dep_end,
        tof_min_s=tof_min_s,
        tof_max_s=tof_max_s,
        grid_size=grid_size,
        max_revs=max_revs,
    )
    if result is None:
        raise HTTPException(status_code=400, detail="Cannot compute porkchop for this pair")

    return result


@router.get("/api/transfer_quote_advanced")
def api_transfer_quote_advanced(
    from_id: str,
    to_id: str,
    request: Request,
    departure_time: Optional[float] = Query(None, description="Game time of departure (epoch seconds). Defaults to now."),
    extra_dv_fraction: float = Query(0.0, ge=0.0, le=2.0, description="Fractional extra delta-v above Hohmann minimum (0=minimum, 1=double)"),
    conn: sqlite3.Connection = Depends(get_db),
) -> Dict[str, Any]:
    """
    Advanced transfer quote with phase-angle effects and delta-v/time tradeoff.

    Returns the base (Hohmann) costs plus adjusted values for the given
    departure time and extra-dv fraction. Interplanetary transfers get
    phase-angle modulation; intra-system transfers pass through unchanged.
    """
    require_login(conn, request)

    dep_time = departure_time if departure_time is not None else game_now_s()
    quote = _compute_route_quote(
        conn,
        from_id=from_id,
        to_id=to_id,
        departure_time_s=dep_time,
        extra_dv_fraction=extra_dv_fraction,
    )
    if not quote:
        raise HTTPException(status_code=404, detail="No transfer data for that pair")

    base_dv = float(quote["base_dv_m_s"])
    base_tof = float(quote["base_tof_s"])
    adjusted_dv = float(quote["phase_adjusted_dv_m_s"])
    total_dv = float(quote["dv_m_s"])
    adjusted_tof = float(quote["tof_s"])
    phase_multiplier = float(quote["phase_multiplier"])
    is_interplanetary = bool(quote["is_interplanetary"])

    orbital_seed = quote.get("orbital") or {}
    from_body = str(orbital_seed.get("from_body") or "")
    to_body = str(orbital_seed.get("to_body") or "")
    phase_angle_deg = orbital_seed.get("phase_angle_deg")
    optimal_phase_deg = orbital_seed.get("optimal_phase_deg")
    alignment_pct = orbital_seed.get("alignment_pct")
    synodic_period_s = None
    next_window_s = None

    if from_body and to_body:
        synodic_raw = transfer_planner.get_synodic_period_s(from_body, to_body)
        if synodic_raw and synodic_raw > 0:
            synodic_period_s = round(synodic_raw, 0)
            next_wait = transfer_planner.estimate_next_window_s(
                from_location=from_id,
                to_location=to_id,
                departure_time_s=dep_time,
                current_phase_multiplier=phase_multiplier,
                synodic_period_s=synodic_period_s,
            )
            if next_wait is not None:
                next_window_s = round(float(next_wait), 0)

    window_suggestions: List[Dict[str, float]] = []
    if from_body and to_body and synodic_period_s:
        window_suggestions = transfer_planner.scan_departure_windows(
            from_location=from_id,
            to_location=to_id,
            departure_time_s=dep_time,
            current_phase_multiplier=phase_multiplier,
            synodic_period_s=float(synodic_period_s),
        )

    result: Dict[str, Any] = {
        "from_id": from_id,
        "to_id": to_id,
        "route_mode": str(quote.get("route_mode") or "direct"),
        # Base Hohmann values (static)
        "base_dv_m_s": round(base_dv, 1),
        "base_tof_s": round(base_tof, 1),
        # Phase-adjusted minimum
        "phase_adjusted_dv_m_s": round(adjusted_dv, 1),
        "phase_multiplier": round(phase_multiplier, 4),
        # Final values with extra-dv
        "dv_m_s": round(total_dv, 1),
        "tof_s": round(adjusted_tof, 1),
        "extra_dv_fraction": round(extra_dv_fraction, 4),
        # Orbital data
        "is_interplanetary": is_interplanetary,
        "departure_time": dep_time,
    }

    # Include Δv breakdown for interplanetary routes
    if is_interplanetary:
        local_dep = float(quote.get("local_departure_dv_m_s") or 0)
        local_arr = float(quote.get("local_arrival_dv_m_s") or 0)
        result["local_departure_dv_m_s"] = round(local_dep, 1)
        result["interplanetary_dv_m_s"] = round(total_dv - local_dep - local_arr, 1)
        result["local_arrival_dv_m_s"] = round(local_arr, 1)
        if quote.get("gateway_departure"):
            result["gateway_departure"] = str(quote["gateway_departure"])
        if quote.get("gateway_arrival"):
            result["gateway_arrival"] = str(quote["gateway_arrival"])

    if is_interplanetary:
        result["orbital"] = {
            "from_body": from_body,
            "to_body": to_body,
            "phase_angle_deg": round(float(phase_angle_deg), 1) if phase_angle_deg is not None else None,
            "optimal_phase_deg": round(float(optimal_phase_deg), 1) if optimal_phase_deg is not None else None,
            "alignment_pct": round(float(alignment_pct), 1) if alignment_pct is not None else None,
            "synodic_period_s": synodic_period_s,
            "next_window_s": next_window_s,
            "window_suggestions": [
                {
                    "departure_time": round(float(entry["departure_time"]), 0),
                    "wait_s": round(float(entry["wait_s"]), 0),
                    "phase_multiplier": round(float(entry["phase_multiplier"]), 4),
                    "phase_angle_deg": round(float(entry["phase_angle_deg"]), 1),
                    "optimal_phase_deg": round(float(entry["optimal_phase_deg"]), 1),
                    "alignment_pct": round(float(entry["alignment_pct"]), 1),
                    "dv_savings_pct": round(float(entry["dv_savings_pct"]), 2),
                }
                for entry in window_suggestions
            ],
        }

    # Surface sites — check origin and destination only
    check_ids = list(dict.fromkeys([from_id, to_id]))
    placeholders = ",".join("?" for _ in check_ids)
    site_rows = conn.execute(
        f"SELECT location_id, body_id, gravity_m_s2 FROM surface_sites WHERE location_id IN ({placeholders})",
        check_ids,
    ).fetchall()
    if site_rows:
        result["surface_sites"] = [
            {
                "location_id": sr["location_id"],
                "body_id": sr["body_id"],
                "gravity_m_s2": float(sr["gravity_m_s2"]),
                "min_twr": 1.0,
            }
            for sr in site_rows
        ]

    return result


@router.get("/api/state")
def api_state(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    m = _main()
    now_s = game_now_s()
    user = require_login(conn, request)
    m.settle_arrivals(conn, now_s)
    conn.commit()

    # Determine the requesting corp (None for admin)
    my_corp_id = user.get("corp_id") if hasattr(user, "get") else None

    rows = conn.execute(
        """
        SELECT id,name,shape,color,size_px,notes_json,
               location_id,from_location_id,to_location_id,departed_at,arrives_at,
                 dv_planned_m_s,dock_slot,
                 parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s,
                 corp_id,
                 transit_from_x,transit_from_y,transit_to_x,transit_to_y,
                 trajectory_json
        FROM ships
        ORDER BY id
        """
    ).fetchall()

    ships = []
    for r in rows:
        ship_corp_id = r["corp_id"] or None
        is_admin = user.get("is_admin") if hasattr(user, "get") else user["is_admin"]
        is_own = (my_corp_id is not None and ship_corp_id == my_corp_id) or (my_corp_id is None and is_admin)

        parts = m.normalize_parts(json.loads(r["parts_json"] or "[]"))
        fuel_kg = max(0.0, float(r["fuel_kg"] or 0.0))
        parts, hardened_changed = m._harden_ship_parts(parts, fuel_kg)
        if hardened_changed:
            conn.execute(
                "UPDATE ships SET parts_json=? WHERE id=?",
                (json.dumps(parts), r["id"]),
            )
        stats = m.derive_ship_stats_from_parts(
            parts,
            current_fuel_kg=fuel_kg,
        )

        ship_data = {
            "id": r["id"],
            "name": r["name"],
            "shape": r["shape"],
            "color": r["color"],
            "size_px": r["size_px"],
            "location_id": r["location_id"],
            "from_location_id": r["from_location_id"],
            "to_location_id": r["to_location_id"],
            "departed_at": r["departed_at"],
            "arrives_at": r["arrives_at"],
            "status": "transit" if r["arrives_at"] else "docked",
            "corp_id": ship_corp_id,
            "is_own": is_own,
        }

        # Attach snapshot coordinates for in-transit ships
        if r["arrives_at"] and r["transit_from_x"] is not None:
            ship_data["transit_from_x"] = r["transit_from_x"]
            ship_data["transit_from_y"] = r["transit_from_y"]
            ship_data["transit_to_x"] = r["transit_to_x"]
            ship_data["transit_to_y"] = r["transit_to_y"]

        # Attach trajectory polyline for in-transit ships
        # New format: flat [[x,y], ...] array.  Legacy format was [{from_id, to_id, points}, ...]
        if r["arrives_at"] and r["trajectory_json"]:
            try:
                traj = json.loads(r["trajectory_json"])
                if traj:
                    # Normalise legacy leg-object format to flat point list
                    if isinstance(traj, list) and traj and isinstance(traj[0], dict):
                        flat = []
                        for seg in traj:
                            flat.extend(seg.get("points") or [])
                        ship_data["trajectory"] = flat if flat else None
                    else:
                        ship_data["trajectory"] = traj
            except (json.JSONDecodeError, TypeError):
                pass

        # Flag interplanetary transfers for frontend rendering
        if r["arrives_at"] and r["from_location_id"] and r["to_location_id"]:
            ship_data["is_interplanetary"] = _is_interplanetary(
                str(r["from_location_id"]), str(r["to_location_id"])
            )

        # Only include detailed data for own ships
        if is_own:
            inventory_containers = m.compute_ship_inventory_containers(parts, stats["fuel_kg"])
            inventory_items = m.compute_ship_inventory_resources(str(r["id"]), inventory_containers)
            inventory_capacity_summary = m.compute_ship_capacity_summary(inventory_containers)
            ship_data.update({
                "notes": json.loads(r["notes_json"] or "[]"),
                "dv_planned_m_s": r["dv_planned_m_s"],
                "dock_slot": r["dock_slot"],
                "parts": parts,
                "inventory_containers": inventory_containers,
                "inventory_items": inventory_items,
                "inventory_capacity_summary": inventory_capacity_summary,
                "fuel_kg": stats["fuel_kg"],
                "fuel_capacity_kg": stats["fuel_capacity_kg"],
                "dry_mass_kg": stats["dry_mass_kg"],
                "isp_s": stats["isp_s"],
                "thrust_kn": stats["thrust_kn"],
                "delta_v_remaining_m_s": m.compute_delta_v_remaining_m_s(
                    stats["dry_mass_kg"],
                    stats["fuel_kg"],
                    stats["isp_s"],
                ),
                "power_balance": catalog_service.compute_power_balance(parts),
            })

        ships.append(ship_data)

    user_info = {}
    if my_corp_id:
        user_info = {
            "corp_id": my_corp_id,
            "corp_name": user.get("corp_name"),
            "corp_color": user.get("corp_color"),
            "is_admin": False,
        }
    else:
        user_info = {
            "username": user["username"],
            "is_admin": bool(user["is_admin"]),
        }

    return {
        "user": user_info,
        "server_time": now_s,
        "time_scale": effective_time_scale(),
        "paused": simulation_paused(),
        "ships": ships,
    }


@router.post("/api/ships/{ship_id}/transfer")
def api_ship_transfer(ship_id: str, req: TransferReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    m = _main()
    now_s = game_now_s()
    to_id = req.to_location_id

    user = require_login(conn, request)
    _require_ship_ownership(conn, request, ship_id)
    m.settle_arrivals(conn, now_s)

    ship = conn.execute(
        """
                    SELECT
                        id,location_id,from_location_id,to_location_id,arrives_at,
                        parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s
        FROM ships WHERE id=?
        """,
        (ship_id,),
    ).fetchone()
    if not ship:
        raise HTTPException(status_code=404, detail="Ship not found")

    if ship["arrives_at"] is not None:
        raise HTTPException(status_code=400, detail="Ship is already in transit")

    from_id = ship["location_id"]
    if not from_id:
        raise HTTPException(status_code=400, detail="Ship has no current location_id")

    route_quote = _compute_route_quote(
        conn,
        from_id=from_id,
        to_id=to_id,
        departure_time_s=now_s,
        extra_dv_fraction=0.0,
    )
    if not route_quote:
        raise HTTPException(status_code=404, detail="No transfer data for that destination")

    dv = float(route_quote["dv_m_s"])
    tof = float(route_quote["tof_s"])

    parts = m.normalize_parts(json.loads(ship["parts_json"] or "[]"))
    stats = m.derive_ship_stats_from_parts(
        parts,
        current_fuel_kg=float(ship["fuel_kg"] or 0.0),
    )

    # ── Overheating gate: block transfer if waste heat surplus > 0 ──
    power_balance = catalog_service.compute_power_balance(parts)
    waste_heat_surplus = float(power_balance.get("waste_heat_surplus_mw") or 0.0)
    if waste_heat_surplus > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Ship is overheating — {waste_heat_surplus:.1f} MWth of unradiated waste heat. "
                   f"Add radiators or remove generators before transferring.",
        )

    delta_v_remaining = m.compute_delta_v_remaining_m_s(
        stats["dry_mass_kg"],
        stats["fuel_kg"],
        stats["isp_s"],
    )
    if dv > delta_v_remaining + 1e-6:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient fuel for transfer (need {int(round(dv))} m/s, have {int(round(delta_v_remaining))} m/s)",
        )

    # ── TWR gate: check origin and destination surface sites ──
    check_ids = list(dict.fromkeys([from_id, to_id]))
    placeholders = ",".join("?" for _ in check_ids)
    site_rows = conn.execute(
        f"SELECT location_id, gravity_m_s2 FROM surface_sites WHERE location_id IN ({placeholders})",
        check_ids,
    ).fetchall()
    if site_rows:
        if site_rows:
            thrust_kn = float(stats.get("thrust_kn") or 0.0)
            thrust_n = thrust_kn * 1000.0
            # Compute cargo mass from all inventory stacks on the ship's current location
            # For TWR, use wet mass (dry + fuel + cargo already accounted in dry_mass_kg from parts)
            wet_mass_kg = catalog_service.compute_wet_mass_kg(stats["dry_mass_kg"], stats["fuel_kg"])
            if wet_mass_kg <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Ship has zero mass — cannot compute TWR",
                )
            for site_row in site_rows:
                gravity = float(site_row["gravity_m_s2"])
                if gravity <= 0:
                    continue
                weight_n = wet_mass_kg * gravity
                twr = thrust_n / weight_n if weight_n > 0 else 0.0
                if twr < 1.0:
                    site_name = site_row["location_id"]
                    raise HTTPException(
                        status_code=400,
                        detail=f"Insufficient TWR for surface site {site_name} "
                               f"(TWR {twr:.2f} < 1.0, need {gravity:.2f} m/s² surface gravity, "
                               f"ship thrust {thrust_kn:.1f} kN, mass {wet_mass_kg:.0f} kg)",
                    )

    # ── Site claim gate: block landing on surface sites claimed by another corp ──
    my_corp_id = user.get("corp_id") if hasattr(user, "get") else None
    if my_corp_id:
        # Check if destination is a surface site claimed by another corp's refinery
        dest_site = conn.execute(
            "SELECT location_id FROM surface_sites WHERE location_id = ?", (to_id,)
        ).fetchone()
        if dest_site:
            claiming_corp = conn.execute(
                """SELECT corp_id FROM deployed_equipment
                   WHERE location_id = ? AND category = 'refinery' AND corp_id != ?
                   LIMIT 1""",
                (to_id, my_corp_id),
            ).fetchone()
            if claiming_corp:
                raise HTTPException(
                    status_code=403,
                    detail="This surface site is claimed by another corporation's refinery",
                )

    fuel_used_kg = m.compute_fuel_needed_for_delta_v_kg(
        stats["dry_mass_kg"],
        stats["fuel_kg"],
        stats["isp_s"],
        dv,
    )
    fuel_remaining_kg = max(0.0, stats["fuel_kg"] - fuel_used_kg)

    dep = now_s
    arr = now_s + max(1.0, tof)

    # Snapshot departure/arrival coordinates so in-transit interpolation
    # is stable even as celestial bodies move during the transfer.
    try:
        bucket = int(float(dep) // _DYN_LOC_BUCKET_S)
        snap_locs = _dynamic_locations_by_id(bucket)
        from_xy = snap_locs.get(from_id, (0.0, 0.0))
        to_bucket = int(float(arr) // _DYN_LOC_BUCKET_S)
        snap_locs_arr = _dynamic_locations_by_id(to_bucket)
        to_xy = snap_locs_arr.get(to_id, (0.0, 0.0))
    except Exception:
        from_xy = (0.0, 0.0)
        to_xy = (0.0, 0.0)

    # Compute trajectory polyline for interplanetary transfers
    # Stored as a flat [[x,y], ...] array (or null for local transfers)
    trajectory_points: Optional[List[List[float]]] = None
    trajectory_data: Any = None  # May be list or dict (for SOI body-centric)
    try:
        orbital = route_quote.get("orbital")
        if orbital and route_quote.get("is_interplanetary"):
            pts = transfer_planner.compute_leg_trajectory(orbital, n_points=64)
            if pts:
                trajectory_points = [[round(x, 1), round(y, 1)] for x, y in pts]
                trajectory_data = trajectory_points
    except Exception:
        logging.exception("Failed to compute trajectory points for ship %s transfer %s → %s", ship_id, from_id, to_id)

    # For SOI transfers (e.g. Earth orbit → Moon orbit), compute a body-centric
    # Lambert trajectory in the parent body's frame (KSP-style elliptical arc)
    if trajectory_data is None:
        try:
            soi_traj = transfer_planner.compute_soi_transfer_trajectory(
                from_id, to_id, dep, tof, n_points=64,
            )
            if soi_traj:
                trajectory_data = soi_traj
        except Exception:
            logging.exception("Failed to compute SOI trajectory for ship %s transfer %s → %s", ship_id, from_id, to_id)

    trajectory_json_str = json.dumps(trajectory_data) if trajectory_data else None

    conn.execute(
        """
        UPDATE ships
        SET
          location_id=NULL,
          from_location_id=?,
          to_location_id=?,
          departed_at=?,
          arrives_at=?,
          dv_planned_m_s=?,
          fuel_kg=?,
          transit_from_x=?,
          transit_from_y=?,
          transit_to_x=?,
          transit_to_y=?,
          trajectory_json=?
        WHERE id=?
        """,
        (from_id, to_id, dep, arr, dv, fuel_remaining_kg,
         from_xy[0], from_xy[1], to_xy[0], to_xy[1], trajectory_json_str, ship_id),
    )
    conn.commit()

    return {
        "ok": True,
        "ship_id": ship_id,
        "from": from_id,
        "to": to_id,
        "dv_m_s": dv,
        "tof_s": tof,
        "is_interplanetary": bool(route_quote.get("is_interplanetary")),
        "route_mode": str(route_quote.get("route_mode") or "direct"),
        "fuel_used_kg": fuel_used_kg,
        "fuel_remaining_kg": fuel_remaining_kg,
        "departed_at": dep,
        "arrives_at": arr,
    }


@router.post("/api/ships/{ship_id}/inventory/jettison")
def api_ship_inventory_jettison(ship_id: str, req: InventoryContainerReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    m = _main()
    sid = (ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    require_login(conn, request)
    _require_ship_ownership(conn, request, sid)

    row = conn.execute(
        """
        SELECT id,name,parts_json,fuel_kg
        FROM ships
        WHERE id=?
        """,
        (sid,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ship not found")

    parts = m.normalize_parts(json.loads(row["parts_json"] or "[]"))
    current_fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))
    inventory = m.compute_ship_inventory_containers(parts, current_fuel_kg)
    target = next((c for c in inventory if int(c["container_index"]) == int(req.container_index)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Container not found")

    target_idx = int(target["container_index"])

    # Determine water mass from cargo_manifest (container rows don't have top-level resource_id)
    manifest = target.get("cargo_manifest") or []
    water_mass_in_container = sum(
        max(0.0, float(e.get("mass_kg") or 0.0))
        for e in manifest
        if str(e.get("resource_id") or "").lower() == "water"
    )
    if water_mass_in_container > 0:
        current_fuel_kg = max(0.0, current_fuel_kg - water_mass_in_container)

    if 0 <= target_idx < len(parts):
        part = dict(parts[target_idx] or {})
        for key in (
            "cargo_used_m3",
            "used_m3",
            "fill_m3",
            "stored_m3",
            "current_m3",
            "cargo_mass_kg",
            "contents_mass_kg",
            "stored_mass_kg",
            "current_mass_kg",
            "water_kg",
            "fuel_kg",
        ):
            if key in part:
                part[key] = 0.0
        # Clear the cargo manifest on the part itself
        part["cargo_manifest"] = []
        parts[target_idx] = part

    m._persist_ship_inventory_state(
        conn,
        ship_id=sid,
        parts=parts,
        fuel_kg=current_fuel_kg,
    )
    conn.commit()

    return {
        "ok": True,
        "ship_id": sid,
        "container_index": target_idx,
        "action": "jettison",
    }


@router.post("/api/ships/{ship_id}/deconstruct")
def api_ship_deconstruct(ship_id: str, req: ShipDeconstructReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    m = _main()
    sid = (ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    require_login(conn, request)
    _require_ship_ownership(conn, request, sid)

    # Get corp_id from the ship itself
    ship_corp_row = conn.execute("SELECT corp_id FROM ships WHERE id=?", (sid,)).fetchone()
    corp_id = str(ship_corp_row["corp_id"] or "") if ship_corp_row and "corp_id" in ship_corp_row.keys() else ""

    row = conn.execute(
        """
        SELECT id,name,location_id,arrives_at,parts_json,fuel_kg
        FROM ships
        WHERE id=?
        """,
        (sid,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ship not found")

    location_id = str(row["location_id"] or "").strip()
    if not location_id or row["arrives_at"] is not None:
        raise HTTPException(status_code=400, detail="Ship must be docked at a location to deconstruct")

    parts = m.normalize_parts(json.loads(row["parts_json"] or "[]"))
    fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))
    containers = m.compute_ship_inventory_containers(parts, fuel_kg)
    by_index = {int(c["container_index"]): c for c in containers}

    transferred_fuel_like_kg = 0.0
    for idx, part in enumerate(parts):
        clean_part = dict(part)
        cargo = by_index.get(idx)
        if cargo:
            # Transfer ALL cargo manifest entries to location inventory
            manifest = cargo.get("cargo_manifest") or []
            for entry in manifest:
                resource_id = str(entry.get("resource_id") or "").strip()
                mass_kg = max(0.0, float(entry.get("mass_kg") or 0.0))
                if resource_id and mass_kg > 0.0:
                    m.add_resource_to_location_inventory(conn, location_id, resource_id, mass_kg, corp_id=corp_id)
                    if resource_id.lower() == "water":
                        transferred_fuel_like_kg += mass_kg
            for key in (
                "cargo_used_m3",
                "used_m3",
                "fill_m3",
                "stored_m3",
                "current_m3",
                "cargo_mass_kg",
                "contents_mass_kg",
                "stored_mass_kg",
                "current_mass_kg",
                "water_kg",
                "fuel_kg",
            ):
                clean_part.pop(key, None)
            # Clear manifest on the part
            clean_part.pop("cargo_manifest", None)

        m.add_part_to_location_inventory(conn, location_id, clean_part, corp_id=corp_id)

    if fuel_kg > transferred_fuel_like_kg + 1e-6:
        m.add_resource_to_location_inventory(conn, location_id, "water", fuel_kg - transferred_fuel_like_kg, corp_id=corp_id)

    if req.keep_ship_record:
        conn.execute(
            """
            UPDATE ships
            SET parts_json='[]', fuel_kg=0, fuel_capacity_kg=0, dry_mass_kg=0, isp_s=0
            WHERE id=?
            """,
            (sid,),
        )
    else:
        conn.execute("DELETE FROM ships WHERE id=?", (sid,))

    conn.commit()
    return {
        "ok": True,
        "ship_id": sid,
        "location_id": location_id,
        "parts_deconstructed": len(parts),
        "resources_transferred_kg": max(0.0, fuel_kg),
        "ship_deleted": not req.keep_ship_record,
    }


@router.post("/api/ships/{ship_id}/inventory/deploy")
def api_ship_inventory_deploy(ship_id: str, req: InventoryContainerReq, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Dict[str, Any]:
    m = _main()
    sid = (ship_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="ship_id is required")

    require_login(conn, request)
    _require_ship_ownership(conn, request, sid)

    # Get corp_id from the ship
    ship_corp_row = conn.execute("SELECT corp_id FROM ships WHERE id=?", (sid,)).fetchone()
    corp_id = str(ship_corp_row["corp_id"] or "") if ship_corp_row and "corp_id" in ship_corp_row.keys() else ""

    row = conn.execute(
        """
        SELECT id,name,location_id,arrives_at,parts_json,fuel_kg
        FROM ships
        WHERE id=?
        """,
        (sid,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ship not found")

    location_id = str(row["location_id"] or "").strip()
    if not location_id or row["arrives_at"] is not None:
        raise HTTPException(status_code=400, detail="Ship must be docked to deploy a container")

    parts = m.normalize_parts(json.loads(row["parts_json"] or "[]"))
    current_fuel_kg = max(0.0, float(row["fuel_kg"] or 0.0))
    inventory = m.compute_ship_inventory_containers(parts, current_fuel_kg)
    target = next((c for c in inventory if int(c["container_index"]) == int(req.container_index)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Container not found")

    target_idx = int(target["container_index"])

    # Determine water mass from cargo_manifest (container rows don't have top-level resource_id)
    manifest = target.get("cargo_manifest") or []
    water_mass_in_container = sum(
        max(0.0, float(e.get("mass_kg") or 0.0))
        for e in manifest
        if str(e.get("resource_id") or "").lower() == "water"
    )
    if water_mass_in_container > 0:
        current_fuel_kg = max(0.0, current_fuel_kg - water_mass_in_container)

    if not (0 <= target_idx < len(parts)):
        raise HTTPException(status_code=404, detail="Container not found")

    deployed_part = dict(parts.pop(target_idx) or {})
    # Preserve cargo manifest data on the deployed part for location inventory
    total_cargo_mass = max(0.0, float(target.get("cargo_mass_kg") or 0.0))
    total_used_m3 = max(0.0, float(target.get("used_m3") or 0.0))
    if total_cargo_mass > 0.0:
        deployed_part["cargo_mass_kg"] = total_cargo_mass
        deployed_part["cargo_used_m3"] = total_used_m3
        # Set resource_id from first manifest entry for backward compatibility
        if manifest:
            deployed_part["resource_id"] = str(manifest[0].get("resource_id") or deployed_part.get("resource_id") or "")
        # Preserve the manifest itself
        deployed_part["cargo_manifest"] = manifest

    m.add_part_to_location_inventory(conn, location_id, deployed_part, corp_id=corp_id)

    m._persist_ship_inventory_state(
        conn,
        ship_id=sid,
        parts=parts,
        fuel_kg=current_fuel_kg,
    )
    conn.commit()

    return {
        "ok": True,
        "ship_id": sid,
        "location_id": location_id,
        "container_index": target_idx,
        "action": "deploy",
        "deployed_container": {
            "name": str((deployed_part or {}).get("name") or f"Container {target_idx + 1}"),
        },
    }
