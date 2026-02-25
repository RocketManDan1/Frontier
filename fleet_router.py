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
import math
import sqlite3
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from auth_service import require_login
import catalog_service
import celestial_config
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
        "path": list(quote["path"]),
        "departure_time": float(quote["departure_time"]),
        "is_interplanetary": bool(quote["is_interplanetary"]),
    }

    # Check if any locations on the path are surface sites
    path_ids = [from_id, to_id]
    hops = quote["path"]
    if isinstance(hops, list):
        path_ids.extend(str(h) for h in hops if isinstance(h, str))
    path_ids_unique = list(dict.fromkeys(path_ids))
    if path_ids_unique:
        placeholders = ",".join("?" for _ in path_ids_unique)
        site_rows = conn.execute(
            f"SELECT location_id, body_id, gravity_m_s2 FROM surface_sites WHERE location_id IN ({placeholders})",
            path_ids_unique,
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


# ── Orbital mechanics helpers for advanced quotes ──────────

# Simplified Keplerian orbital elements (J2000 epoch: 2000-01-01T12:00 TT)
# Each entry: (a_km, e, i_deg, Omega_deg, w_deg, M0_deg, period_s)
_BODY_ORBITS: Dict[str, Dict[str, float]] = {
    "mercury": {"a_km": 57_909_227.0, "period_s": 7_600_521.6},  # 87.969 d
    "venus":   {"a_km": 108_209_475.0, "period_s": 19_414_166.4},  # 224.701 d
    "earth":   {"a_km": 149_597_870.7, "period_s": 31_558_149.8},  # 365.256 d
    "mars":    {"a_km": 227_943_824.0, "period_s": 59_355_072.0},  # 686.971 d
    "ceres":   {"a_km": 413_767_000.0, "period_s": 145_166_000.0},
    "vesta":   {"a_km": 353_340_000.0, "period_s": 114_500_000.0},
    "pallas":  {"a_km": 414_500_000.0, "period_s": 145_700_000.0},
    "hygiea":  {"a_km": 470_300_000.0, "period_s": 175_400_000.0},
}

# Reference epoch for mean anomaly: game epoch 0 = 2040-01-01T00:00 UTC
_EPOCH_MEAN_ANOMALY_DEG: Dict[str, float] = {
    "mercury": 174.796,
    "venus":   50.115,
    "earth":   357.529,
    "mars":    19.373,
    "ceres":   95.989,
    "vesta":   149.84,
    "pallas":  33.2,
    "hygiea":  98.0,
}

# Which parent body does a location orbit?
_LOCATION_PARENT_BODY: Dict[str, str] = {
    "LEO": "earth", "HEO": "earth", "GEO": "earth",
    "L1": "earth", "L2": "earth", "L3": "earth", "L4": "earth", "L5": "earth",
    "LLO": "earth", "HLO": "earth",
    "LUNA_SHACKLETON": "earth", "LUNA_PEARY": "earth", "LUNA_TRANQUILLITATIS": "earth",
    "LUNA_IMBRIUM": "earth", "LUNA_ANORTHOSITE": "earth", "LUNA_KREEP": "earth",
    "MERC_ORB": "mercury", "MERC_HEO": "mercury", "MERC_GEO": "mercury",
    "VEN_ORB": "venus", "VEN_HEO": "venus", "VEN_GEO": "venus", "ZOOZVE": "venus",
    "LMO": "mars", "HMO": "mars", "MGO": "mars",
    "PHOBOS": "mars", "DEIMOS": "mars",
    "CERES_LO": "ceres", "CERES_HO": "ceres",
    "VESTA_LO": "vesta", "VESTA_HO": "vesta",
    "PALLAS_LO": "pallas", "PALLAS_HO": "pallas",
    "HYGIEA_LO": "hygiea", "HYGIEA_HO": "hygiea",
    "CERES_OCCATOR": "ceres", "CERES_AHUNA": "ceres", "CERES_KERWAN": "ceres",
    "VESTA_RHEASILVIA": "vesta", "VESTA_MARCIANOVA": "vesta", "VESTA_OPPIA": "vesta",
    "PALLAS_DIPOLE": "pallas", "PALLAS_EQUATORIAL": "pallas", "PALLAS_PELION": "pallas",
    "HYGIEA_CENTRAL": "hygiea", "HYGIEA_EASTERN": "hygiea", "HYGIEA_SOUTH": "hygiea",
    "SUN": "sun",
}

_SUN_MU_KM3_S2 = 1.32712440018e11
_BODY_CONSTANTS: Dict[str, Dict[str, float]] = {
    "mercury": {"mu_km3_s2": 22031.86855, "radius_km": 2439.7, "parking_alt_km": 200.0},
    "venus": {"mu_km3_s2": 324858.592, "radius_km": 6051.8, "parking_alt_km": 250.0},
    "earth": {"mu_km3_s2": 398600.4418, "radius_km": 6378.137, "parking_alt_km": 400.0},
    "mars": {"mu_km3_s2": 42828.375214, "radius_km": 3389.5, "parking_alt_km": 250.0},
    "ceres": {"mu_km3_s2": 62.63, "radius_km": 473.0, "parking_alt_km": 100.0},
    "vesta": {"mu_km3_s2": 17.29, "radius_km": 262.7, "parking_alt_km": 80.0},
    "pallas": {"mu_km3_s2": 13.61, "radius_km": 256.0, "parking_alt_km": 80.0},
    "hygiea": {"mu_km3_s2": 5.56, "radius_km": 217.0, "parking_alt_km": 70.0},
}

_ROUTE_CACHE_BUCKET_S = 6.0 * 3600.0
_ROUTE_CACHE_MAX = 512
_ROUTE_QUOTE_CACHE: Dict[Tuple[str, str, str, int, int], Dict[str, Any]] = {}
_TRANSFER_GRAPH_CACHE: Dict[str, Dict[str, List[Tuple[str, float, float]]]] = {}


# Cache position lookups bucketed to 5-minute intervals to keep
# the LRU effective at 48× game speed while staying accurate enough
# for phase-angle and Hohmann calculations.
_DYN_LOC_BUCKET_S = 300  # 5 minutes


@lru_cache(maxsize=64)
def _dynamic_locations_by_id(game_time_bucket: int) -> Dict[str, Tuple[float, float]]:
    cfg = celestial_config.load_celestial_config()
    rows, _ = celestial_config.build_locations_and_edges(
        cfg, game_time_s=float(game_time_bucket) * _DYN_LOC_BUCKET_S,
    )
    return {str(row[0]): (float(row[5]), float(row[6])) for row in rows}


def _body_heliocentric_state(body_id: str, game_time_s: float) -> Optional[Dict[str, float]]:
    if str(body_id or "").strip().lower() == "sun":
        return {"r_km": 0.0, "theta_rad": 0.0}

    bucket = int(float(game_time_s) // _DYN_LOC_BUCKET_S)
    locs = _dynamic_locations_by_id(bucket)
    sun = locs.get("grp_sun")
    body = locs.get(f"grp_{body_id}")
    if not sun or not body:
        return None

    dx = body[0] - sun[0]
    dy = body[1] - sun[1]
    radius_km = max(1e-9, math.hypot(dx, dy))
    theta_rad = math.atan2(dy, dx) % (2.0 * math.pi)
    return {"r_km": radius_km, "theta_rad": theta_rad}


def _body_phase_solution(from_body: str, to_body: str, game_time_s: float) -> Optional[Dict[str, float]]:
    from_state = _body_heliocentric_state(from_body, game_time_s)
    to_state = _body_heliocentric_state(to_body, game_time_s)
    if not from_state or not to_state:
        return None

    phase = (to_state["theta_rad"] - from_state["theta_rad"]) % (2.0 * math.pi)
    r1 = max(1e-9, from_state["r_km"])
    r2 = max(1e-9, to_state["r_km"])
    optimal_phase = math.pi * (1.0 - (1.0 / (2.0 ** (2.0 / 3.0))) * ((r1 + r2) / r2) ** (2.0 / 3.0))
    if r2 < r1:
        optimal_phase = 2.0 * math.pi - abs(optimal_phase)
    optimal_phase %= (2.0 * math.pi)

    delta = phase - optimal_phase
    alignment = (1.0 - math.cos(delta)) / 2.0
    phase_multiplier = 1.0 + 0.4 * alignment

    return {
        "r1_km": r1,
        "r2_km": r2,
        "phase_angle_deg": float(math.degrees(phase)),
        "optimal_phase_deg": float(math.degrees(optimal_phase)),
        "alignment_pct": float(alignment * 100.0),
        "phase_multiplier": float(phase_multiplier),
    }


def _scan_departure_windows(
    from_body: str,
    to_body: str,
    departure_time_s: float,
    current_phase_multiplier: float,
    synodic_period_s: Optional[float],
) -> List[Dict[str, float]]:
    if synodic_period_s is None or synodic_period_s <= 0:
        return []

    horizon_s = max(86400.0, min(float(synodic_period_s), 240.0 * 86400.0))
    step_s = 86400.0
    candidates: List[Dict[str, float]] = []
    samples = int(horizon_s / step_s)

    for idx in range(1, samples + 1):
        t = float(departure_time_s) + idx * step_s
        solution = _body_phase_solution(from_body, to_body, t)
        if not solution:
            continue
        multiplier = float(solution["phase_multiplier"])
        savings_pct = 0.0
        if current_phase_multiplier > 1e-9:
            savings_pct = max(0.0, (1.0 - multiplier / current_phase_multiplier) * 100.0)
        candidates.append(
            {
                "departure_time": t,
                "wait_s": float(t - departure_time_s),
                "phase_multiplier": multiplier,
                "phase_angle_deg": float(solution["phase_angle_deg"]),
                "optimal_phase_deg": float(solution["optimal_phase_deg"]),
                "alignment_pct": float(solution["alignment_pct"]),
                "dv_savings_pct": float(savings_pct),
            }
        )

    candidates.sort(key=lambda item: (item["phase_multiplier"], item["wait_s"]))
    return candidates[:3]


def _estimate_next_window_s(
    from_body: str,
    to_body: str,
    departure_time_s: float,
    current_phase_multiplier: float,
    synodic_period_s: Optional[float],
) -> Optional[float]:
    windows = _scan_departure_windows(
        from_body=from_body,
        to_body=to_body,
        departure_time_s=departure_time_s,
        current_phase_multiplier=current_phase_multiplier,
        synodic_period_s=synodic_period_s,
    )
    if not windows:
        return None
    best_wait = float(windows[0]["wait_s"])
    if float(windows[0]["phase_multiplier"]) >= current_phase_multiplier - 1e-6:
        return None
    return best_wait


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


def _get_transfer_graph(conn: sqlite3.Connection, edge_hash: str) -> Dict[str, List[Tuple[str, float, float]]]:
    cached = _TRANSFER_GRAPH_CACHE.get(edge_hash)
    if cached is not None:
        return cached

    rows = conn.execute("SELECT from_id,to_id,dv_m_s,tof_s FROM transfer_edges").fetchall()
    graph: Dict[str, List[Tuple[str, float, float]]] = {}
    for row in rows:
        src = str(row["from_id"])
        dst = str(row["to_id"])
        graph.setdefault(src, []).append((dst, float(row["dv_m_s"]), float(row["tof_s"])))
        graph.setdefault(dst, graph.get(dst, []))

    _TRANSFER_GRAPH_CACHE.clear()
    _TRANSFER_GRAPH_CACHE[edge_hash] = graph
    return graph


def _compute_leg_at_departure(
    leg_from: str,
    leg_to: str,
    base_dv: float,
    base_tof: float,
    departure_time_s: float,
    extra_dv_fraction: float,
) -> Dict[str, Any]:
    leg_base_dv = float(base_dv)
    leg_base_tof = float(base_tof)
    leg_phase_multiplier = 1.0
    leg_phase_adjusted_dv = leg_base_dv
    leg_final_dv = leg_base_dv * (1.0 + max(0.0, float(extra_dv_fraction)))
    leg_final_tof = _excess_dv_time_reduction(leg_base_tof, leg_base_dv, max(0.0, float(extra_dv_fraction)))
    orbital: Optional[Dict[str, float]] = None

    if _is_interplanetary(leg_from, leg_to):
        orbital = _compute_interplanetary_leg_quote(leg_from, leg_to, departure_time_s, extra_dv_fraction)
        if orbital:
            leg_base_dv = float(orbital["base_dv_m_s"])
            leg_base_tof = float(orbital["base_tof_s"])
            leg_phase_multiplier = float(orbital["phase_multiplier"])
            leg_phase_adjusted_dv = float(orbital["phase_adjusted_dv_m_s"])
            leg_final_dv = float(orbital["dv_m_s"])
            leg_final_tof = float(orbital["tof_s"])

    return {
        "from_id": leg_from,
        "to_id": leg_to,
        "is_interplanetary": bool(orbital),
        "base_dv_m_s": leg_base_dv,
        "base_tof_s": leg_base_tof,
        "phase_multiplier": leg_phase_multiplier,
        "phase_adjusted_dv_m_s": leg_phase_adjusted_dv,
        "dv_m_s": leg_final_dv,
        "tof_s": leg_final_tof,
        "departure_time": float(departure_time_s),
        "orbital": orbital,
    }


def _solve_dynamic_route(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    departure_time_s: float,
    extra_dv_fraction: float,
) -> Optional[Dict[str, Any]]:
    if from_id == to_id:
        return {
            "from_id": from_id,
            "to_id": to_id,
            "path": [from_id],
            "base_dv_m_s": 0.0,
            "base_tof_s": 0.0,
            "phase_adjusted_dv_m_s": 0.0,
            "phase_multiplier": 1.0,
            "dv_m_s": 0.0,
            "tof_s": 0.0,
            "extra_dv_fraction": float(extra_dv_fraction),
            "departure_time": float(departure_time_s),
            "is_interplanetary": False,
            "legs": [],
            "route_mode": "dynamic-dijkstra",
        }

    edge_hash = _edge_hash(conn)
    graph = _get_transfer_graph(conn, edge_hash)
    if from_id not in graph or to_id not in graph:
        return None

    best_cost: Dict[str, Tuple[float, float]] = {from_id: (0.0, 0.0)}
    prev: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    pq: List[Tuple[float, float, str]] = [(0.0, 0.0, from_id)]

    while pq:
        current_dv, elapsed_s, node = heapq.heappop(pq)
        known = best_cost.get(node)
        if known is None:
            continue
        if current_dv > known[0] + 1e-9:
            continue
        if node == to_id:
            break

        for nxt, edge_dv, edge_tof in graph.get(node, []):
            leg_departure = float(departure_time_s) + float(elapsed_s)
            leg = _compute_leg_at_departure(node, nxt, edge_dv, edge_tof, leg_departure, extra_dv_fraction)

            ndv = float(current_dv + float(leg["dv_m_s"]))
            nelapsed = float(elapsed_s + float(leg["tof_s"]))
            existing = best_cost.get(nxt)
            better = (
                existing is None
                or ndv < existing[0] - 1e-9
                or (abs(ndv - existing[0]) <= 1e-9 and nelapsed < existing[1] - 1e-6)
            )
            if not better:
                continue

            best_cost[nxt] = (ndv, nelapsed)
            prev[nxt] = (node, leg)
            heapq.heappush(pq, (ndv, nelapsed, nxt))

    if to_id not in best_cost:
        return None

    reversed_legs: List[Dict[str, Any]] = []
    path_nodes = [to_id]
    cursor = to_id
    while cursor != from_id:
        step = prev.get(cursor)
        if not step:
            return None
        parent, leg = step
        reversed_legs.append(leg)
        path_nodes.append(parent)
        cursor = parent

    legs = list(reversed(reversed_legs))
    path = list(reversed(path_nodes))

    base_total_dv = sum(float(leg["base_dv_m_s"]) for leg in legs)
    base_total_tof = sum(float(leg["base_tof_s"]) for leg in legs)
    phase_adjusted_total_dv = sum(float(leg["phase_adjusted_dv_m_s"]) for leg in legs)
    total_dv = sum(float(leg["dv_m_s"]) for leg in legs)
    total_tof = sum(float(leg["tof_s"]) for leg in legs)
    phase_multiplier = (phase_adjusted_total_dv / base_total_dv) if base_total_dv > 0 else 1.0

    return {
        "from_id": from_id,
        "to_id": to_id,
        "path": path,
        "base_dv_m_s": base_total_dv,
        "base_tof_s": base_total_tof,
        "phase_adjusted_dv_m_s": phase_adjusted_total_dv,
        "phase_multiplier": phase_multiplier,
        "dv_m_s": total_dv,
        "tof_s": total_tof,
        "extra_dv_fraction": float(extra_dv_fraction),
        "departure_time": float(departure_time_s),
        "is_interplanetary": any(bool(leg.get("is_interplanetary")) for leg in legs),
        "legs": legs,
        "route_mode": "dynamic-dijkstra",
    }


def _compute_route_quote_from_path(
    conn: sqlite3.Connection,
    path: List[str],
    departure_time_s: float,
    extra_dv_fraction: float,
) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    if len(path) == 1:
        node = str(path[0])
        return {
            "from_id": node,
            "to_id": node,
            "path": [node],
            "base_dv_m_s": 0.0,
            "base_tof_s": 0.0,
            "phase_adjusted_dv_m_s": 0.0,
            "phase_multiplier": 1.0,
            "dv_m_s": 0.0,
            "tof_s": 0.0,
            "extra_dv_fraction": float(extra_dv_fraction),
            "departure_time": float(departure_time_s),
            "is_interplanetary": False,
            "legs": [],
            "route_mode": "fixed-path",
        }

    base_total_dv = 0.0
    base_total_tof = 0.0
    phase_adjusted_total_dv = 0.0
    total_dv = 0.0
    total_tof = 0.0
    legs: List[Dict[str, Any]] = []

    leg_departure = float(departure_time_s)
    for idx in range(len(path) - 1):
        leg_from = str(path[idx])
        leg_to = str(path[idx + 1])
        edge = conn.execute(
            "SELECT dv_m_s,tof_s FROM transfer_edges WHERE from_id=? AND to_id=?",
            (leg_from, leg_to),
        ).fetchone()
        if not edge:
            return None

        leg = _compute_leg_at_departure(
            leg_from,
            leg_to,
            float(edge["dv_m_s"]),
            float(edge["tof_s"]),
            leg_departure,
            extra_dv_fraction,
        )
        legs.append(leg)

        base_total_dv += float(leg["base_dv_m_s"])
        base_total_tof += float(leg["base_tof_s"])
        phase_adjusted_total_dv += float(leg["phase_adjusted_dv_m_s"])
        total_dv += float(leg["dv_m_s"])
        total_tof += float(leg["tof_s"])
        leg_departure += float(leg["tof_s"])

    phase_multiplier = (phase_adjusted_total_dv / base_total_dv) if base_total_dv > 0 else 1.0
    return {
        "from_id": str(path[0]),
        "to_id": str(path[-1]),
        "path": [str(n) for n in path],
        "base_dv_m_s": base_total_dv,
        "base_tof_s": base_total_tof,
        "phase_adjusted_dv_m_s": phase_adjusted_total_dv,
        "phase_multiplier": phase_multiplier,
        "dv_m_s": total_dv,
        "tof_s": total_tof,
        "extra_dv_fraction": float(extra_dv_fraction),
        "departure_time": float(departure_time_s),
        "is_interplanetary": any(bool(leg.get("is_interplanetary")) for leg in legs),
        "legs": legs,
        "route_mode": "fixed-path",
    }


def _route_legs_timeline(legs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    timeline: List[Dict[str, Any]] = []
    for leg in (legs or []):
        dep = float(leg.get("departure_time") or 0.0)
        tof = max(0.0, float(leg.get("tof_s") or 0.0))
        timeline.append(
            {
                "from_id": str(leg.get("from_id") or ""),
                "to_id": str(leg.get("to_id") or ""),
                "departure_time": dep,
                "arrival_time": dep + tof,
                "tof_s": tof,
                "dv_m_s": float(leg.get("dv_m_s") or 0.0),
                "is_interplanetary": bool(leg.get("is_interplanetary")),
            }
        )
    return timeline


def _load_matrix_path(path_json: str, from_id: str, to_id: str) -> List[str]:
    try:
        raw = json.loads(path_json or "[]")
    except (json.JSONDecodeError, TypeError):
        raw = []

    path = [str(node) for node in raw if isinstance(node, str)] if isinstance(raw, list) else []
    if not path:
        return [from_id, to_id]
    if path[0] != from_id:
        path = [from_id] + path
    if path[-1] != to_id:
        path = path + [to_id]
    return path


def _compute_interplanetary_leg_quote(
    from_id: str,
    to_id: str,
    departure_time_s: float,
    extra_dv_fraction: float,
) -> Optional[Dict[str, float]]:
    from_body = _LOCATION_PARENT_BODY.get(from_id, "")
    to_body = _LOCATION_PARENT_BODY.get(to_id, "")
    if not from_body or not to_body or from_body == to_body:
        return None
    if from_body == "sun" or to_body == "sun":
        return None

    origin = _BODY_CONSTANTS.get(from_body)
    destination = _BODY_CONSTANTS.get(to_body)
    if not origin or not destination:
        return None

    phase_solution = _body_phase_solution(from_body, to_body, departure_time_s)
    if not phase_solution:
        return None
    from_state = _body_heliocentric_state(from_body, departure_time_s)
    to_state = _body_heliocentric_state(to_body, departure_time_s)
    if not from_state or not to_state:
        return None

    base_dv_m_s, base_tof_s = _main()._hohmann_interplanetary_dv_tof(
        from_state["r_km"],
        to_state["r_km"],
        _SUN_MU_KM3_S2,
        origin["mu_km3_s2"],
        origin["radius_km"] + origin["parking_alt_km"],
        destination["mu_km3_s2"],
        destination["radius_km"] + destination["parking_alt_km"],
    )

    phase_multiplier = float(phase_solution["phase_multiplier"])

    phase_adjusted_dv = base_dv_m_s * phase_multiplier
    dv_m_s = phase_adjusted_dv * (1.0 + max(0.0, float(extra_dv_fraction)))
    tof_s = _excess_dv_time_reduction(base_tof_s, phase_adjusted_dv, max(0.0, float(extra_dv_fraction)))

    return {
        "base_dv_m_s": float(base_dv_m_s),
        "base_tof_s": float(base_tof_s),
        "phase_multiplier": float(phase_multiplier),
        "phase_adjusted_dv_m_s": float(phase_adjusted_dv),
        "dv_m_s": float(dv_m_s),
        "tof_s": float(tof_s),
        "phase_angle_deg": float(phase_solution["phase_angle_deg"]),
        "optimal_phase_deg": float(phase_solution["optimal_phase_deg"]),
        "alignment_pct": float(phase_solution["alignment_pct"]),
        "from_body": from_body,
        "to_body": to_body,
    }


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

    solved = _solve_dynamic_route(
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
    a = _LOCATION_PARENT_BODY.get(from_id, "")
    b = _LOCATION_PARENT_BODY.get(to_id, "")
    if not a or not b or a == "sun" or b == "sun":
        return False
    return a != b


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
    path = list(quote["path"])
    phase_multiplier = float(quote["phase_multiplier"])
    is_interplanetary = bool(quote["is_interplanetary"])

    interplanetary_legs = [leg for leg in quote["legs"] if bool(leg.get("is_interplanetary"))]
    orbital_seed = (interplanetary_legs[0].get("orbital") if interplanetary_legs else None) or {}
    from_body = str(orbital_seed.get("from_body") or "")
    to_body = str(orbital_seed.get("to_body") or "")
    phase_angle_deg = orbital_seed.get("phase_angle_deg")
    optimal_phase_deg = orbital_seed.get("optimal_phase_deg")
    alignment_pct = orbital_seed.get("alignment_pct")
    synodic_period_s = None
    next_window_s = None

    if from_body in _BODY_ORBITS and to_body in _BODY_ORBITS:
        p1 = _BODY_ORBITS[from_body]["period_s"]
        p2 = _BODY_ORBITS[to_body]["period_s"]
        if abs((1.0 / p1) - (1.0 / p2)) > 1e-12:
            synodic_period_s = round(abs(1.0 / (1.0 / p1 - 1.0 / p2)), 0)
            next_wait = _estimate_next_window_s(
                from_body=from_body,
                to_body=to_body,
                departure_time_s=dep_time,
                current_phase_multiplier=phase_multiplier,
                synodic_period_s=synodic_period_s,
            )
            if next_wait is not None:
                next_window_s = round(float(next_wait), 0)

    window_suggestions: List[Dict[str, float]] = []
    if from_body and to_body and synodic_period_s:
        window_suggestions = _scan_departure_windows(
            from_body=from_body,
            to_body=to_body,
            departure_time_s=dep_time,
            current_phase_multiplier=phase_multiplier,
            synodic_period_s=float(synodic_period_s),
        )

    result: Dict[str, Any] = {
        "from_id": from_id,
        "to_id": to_id,
        "path": path,
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

    # Surface sites
    path_ids = [from_id, to_id]
    hops = path
    if isinstance(hops, list):
        path_ids.extend(str(h) for h in hops if isinstance(h, str))
    path_ids_unique = list(dict.fromkeys(path_ids))
    if path_ids_unique:
        placeholders = ",".join("?" for _ in path_ids_unique)
        site_rows = conn.execute(
            f"SELECT location_id, body_id, gravity_m_s2 FROM surface_sites WHERE location_id IN ({placeholders})",
            path_ids_unique,
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
                 transfer_path_json,dv_planned_m_s,dock_slot,
                 parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s,
                 corp_id,
                 transit_from_x,transit_from_y,transit_to_x,transit_to_y
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
            "transfer_path": json.loads(r["transfer_path_json"] or "[]"),
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

        if r["arrives_at"] and r["from_location_id"] and r["to_location_id"] and r["departed_at"]:
            raw_path = json.loads(r["transfer_path_json"] or "[]")
            fixed_path = [str(node) for node in raw_path if isinstance(node, str)] if isinstance(raw_path, list) else []
            if not fixed_path:
                fixed_path = [str(r["from_location_id"]), str(r["to_location_id"])]
            else:
                if fixed_path[0] != str(r["from_location_id"]):
                    fixed_path = [str(r["from_location_id"])] + fixed_path
                if fixed_path[-1] != str(r["to_location_id"]):
                    fixed_path = fixed_path + [str(r["to_location_id"])]

            route_at_departure = _compute_route_quote_from_path(
                conn,
                path=fixed_path,
                departure_time_s=float(r["departed_at"]),
                extra_dv_fraction=0.0,
            )
            if route_at_departure:
                ship_data["transfer_legs"] = _route_legs_timeline(route_at_departure.get("legs") or [])

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
    path_json = json.dumps(route_quote["path"])

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

    # ── TWR gate: check all surface sites on the path ──────────
    # Collect all location IDs involved (origin, destination, and hops)
    path_ids = [from_id, to_id]
    try:
        hops = json.loads(path_json)
        if isinstance(hops, list):
            path_ids.extend(str(h) for h in hops if isinstance(h, str))
    except (json.JSONDecodeError, TypeError):
        pass
    # Deduplicate
    path_ids_unique = list(dict.fromkeys(path_ids))

    if path_ids_unique:
        placeholders = ",".join("?" for _ in path_ids_unique)
        site_rows = conn.execute(
            f"SELECT location_id, gravity_m_s2 FROM surface_sites WHERE location_id IN ({placeholders})",
            path_ids_unique,
        ).fetchall()
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

    conn.execute(
        """
        UPDATE ships
        SET
          location_id=NULL,
          from_location_id=?,
          to_location_id=?,
          departed_at=?,
          arrives_at=?,
          transfer_path_json=?,
          dv_planned_m_s=?,
          fuel_kg=?,
          transit_from_x=?,
          transit_from_y=?,
          transit_to_x=?,
          transit_to_y=?
        WHERE id=?
        """,
        (from_id, to_id, dep, arr, path_json, dv, fuel_remaining_kg,
         from_xy[0], from_xy[1], to_xy[0], to_xy[1], ship_id),
    )
    conn.commit()

    return {
        "ok": True,
        "ship_id": ship_id,
        "from": from_id,
        "to": to_id,
        "dv_m_s": dv,
        "tof_s": tof,
        "transfer_legs": _route_legs_timeline(route_quote.get("legs") or []),
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
