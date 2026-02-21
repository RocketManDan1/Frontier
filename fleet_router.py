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
import math
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from auth_service import require_login
import catalog_service
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
    row = conn.execute(
        "SELECT dv_m_s,tof_s,path_json FROM transfer_matrix WHERE from_id=? AND to_id=?",
        (from_id, to_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No transfer data for that pair")

    result: Dict[str, Any] = {
        "from_id": from_id,
        "to_id": to_id,
        "dv_m_s": float(row["dv_m_s"]),
        "tof_s": float(row["tof_s"]),
        "path": json.loads(row["path_json"] or "[]"),
    }

    # Check if any locations on the path are surface sites
    path_ids = [from_id, to_id]
    try:
        hops = json.loads(row["path_json"] or "[]")
        if isinstance(hops, list):
            path_ids.extend(str(h) for h in hops if isinstance(h, str))
    except (json.JSONDecodeError, TypeError):
        pass
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
}

# Reference epoch for mean anomaly: game epoch 0 = 2040-01-01T00:00 UTC
_EPOCH_MEAN_ANOMALY_DEG: Dict[str, float] = {
    "mercury": 174.796,
    "venus":   50.115,
    "earth":   357.529,
    "mars":    19.373,
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
    "SUN": "sun",
}


def _body_angle_at_time(body_id: str, game_time_s: float) -> Optional[float]:
    """Return heliocentric longitude (radians) for a body at game_time_s."""
    orb = _BODY_ORBITS.get(body_id)
    if not orb:
        return None
    m0 = math.radians(_EPOCH_MEAN_ANOMALY_DEG.get(body_id, 0.0))
    mean_motion = 2.0 * math.pi / orb["period_s"]
    return m0 + mean_motion * game_time_s


def _is_interplanetary(from_id: str, to_id: str) -> bool:
    """True if the transfer crosses between different heliocentric bodies."""
    a = _LOCATION_PARENT_BODY.get(from_id, "")
    b = _LOCATION_PARENT_BODY.get(to_id, "")
    if not a or not b or a == "sun" or b == "sun":
        return False
    return a != b


def _phase_angle_multiplier(from_body: str, to_body: str, game_time_s: float) -> float:
    """
    Compute a delta-v multiplier based on the synodic phase angle.
    Returns 1.0 at optimal Hohmann alignment, up to ~1.4 at worst alignment.
    Uses a cosine model: multiplier = 1 + penalty * (1 - cos(phase - optimal)) / 2
    """
    theta_from = _body_angle_at_time(from_body, game_time_s)
    theta_to = _body_angle_at_time(to_body, game_time_s)
    if theta_from is None or theta_to is None:
        return 1.0

    # Current phase angle
    phase = (theta_to - theta_from) % (2.0 * math.pi)

    # Optimal Hohmann phase angle
    a_from = _BODY_ORBITS[from_body]["a_km"]
    a_to = _BODY_ORBITS[to_body]["a_km"]
    a_transfer = 0.5 * (a_from + a_to)
    optimal_phase = math.pi * (1.0 - (1.0 / (2.0 ** (2.0 / 3.0))) * ((a_from + a_to) / a_to) ** (2.0 / 3.0))
    if a_to < a_from:
        optimal_phase = 2.0 * math.pi - abs(optimal_phase)
    optimal_phase = optimal_phase % (2.0 * math.pi)

    # Delta from optimal
    delta = phase - optimal_phase
    alignment = (1.0 - math.cos(delta)) / 2.0  # 0 = optimal, 1 = worst

    # Penalty: up to 40% more delta-v at worst alignment
    return 1.0 + 0.4 * alignment


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

    row = conn.execute(
        "SELECT dv_m_s,tof_s,path_json FROM transfer_matrix WHERE from_id=? AND to_id=?",
        (from_id, to_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No transfer data for that pair")

    base_dv = float(row["dv_m_s"])
    base_tof = float(row["tof_s"])
    path = json.loads(row["path_json"] or "[]")

    dep_time = departure_time if departure_time is not None else game_now_s()

    # Phase angle adjustment for interplanetary legs
    from_body = _LOCATION_PARENT_BODY.get(from_id, "")
    to_body = _LOCATION_PARENT_BODY.get(to_id, "")
    is_interplanetary = _is_interplanetary(from_id, to_id)

    phase_multiplier = 1.0
    phase_angle_deg = None
    optimal_phase_deg = None
    alignment_pct = None
    synodic_period_s = None
    next_window_s = None

    if is_interplanetary and from_body in _BODY_ORBITS and to_body in _BODY_ORBITS:
        phase_multiplier = _phase_angle_multiplier(from_body, to_body, dep_time)

        # Compute current phase angle for display
        theta_from = _body_angle_at_time(from_body, dep_time)
        theta_to = _body_angle_at_time(to_body, dep_time)
        if theta_from is not None and theta_to is not None:
            phase_rad = (theta_to - theta_from) % (2.0 * math.pi)
            phase_angle_deg = round(math.degrees(phase_rad), 1)

            # Optimal phase
            a_from = _BODY_ORBITS[from_body]["a_km"]
            a_to = _BODY_ORBITS[to_body]["a_km"]
            opt = math.pi * (1.0 - (1.0 / (2.0 ** (2.0 / 3.0))) * ((a_from + a_to) / a_to) ** (2.0 / 3.0))
            if a_to < a_from:
                opt = 2.0 * math.pi - abs(opt)
            opt = opt % (2.0 * math.pi)
            optimal_phase_deg = round(math.degrees(opt), 1)

            delta = phase_rad - opt
            alignment_pct = round((1.0 - math.cos(delta)) / 2.0 * 100, 1)

            # Synodic period
            p1 = _BODY_ORBITS[from_body]["period_s"]
            p2 = _BODY_ORBITS[to_body]["period_s"]
            synodic_period_s = round(abs(1.0 / (1.0 / p1 - 1.0 / p2)), 0)

            # Find next optimal window (search forward in 1-day steps)
            best_time = dep_time
            best_mult = phase_multiplier
            step = 86400.0  # 1 day
            for i in range(1, int(synodic_period_s / step) + 2):
                t = dep_time + i * step
                m = _phase_angle_multiplier(from_body, to_body, t)
                if m < best_mult:
                    best_mult = m
                    best_time = t
            if best_time > dep_time:
                next_window_s = round(best_time - dep_time, 0)

    # Apply phase multiplier
    adjusted_dv = base_dv * phase_multiplier

    # Apply extra delta-v for faster transit
    total_dv = adjusted_dv * (1.0 + extra_dv_fraction)
    adjusted_tof = _excess_dv_time_reduction(base_tof, adjusted_dv, extra_dv_fraction)

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
            "phase_angle_deg": phase_angle_deg,
            "optimal_phase_deg": optimal_phase_deg,
            "alignment_pct": alignment_pct,
            "synodic_period_s": synodic_period_s,
            "next_window_s": next_window_s,
        }

    # Surface sites
    path_ids = [from_id, to_id]
    try:
        hops = json.loads(row["path_json"] or "[]")
        if isinstance(hops, list):
            path_ids.extend(str(h) for h in hops if isinstance(h, str))
    except (json.JSONDecodeError, TypeError):
        pass
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

    rows = conn.execute(
        """
        SELECT id,name,shape,color,size_px,notes_json,
               location_id,from_location_id,to_location_id,departed_at,arrives_at,
                 transfer_path_json,dv_planned_m_s,dock_slot,
                 parts_json,fuel_kg,fuel_capacity_kg,dry_mass_kg,isp_s
        FROM ships
        ORDER BY id
        """
    ).fetchall()

    ships = []
    for r in rows:
        parts = m.normalize_parts(json.loads(r["parts_json"] or "[]"))
        stats = m.derive_ship_stats_from_parts(
            parts,
            current_fuel_kg=float(r["fuel_kg"] or 0.0),
        )
        inventory_containers = m.compute_ship_inventory_containers(parts, stats["fuel_kg"])
        inventory_items = m.compute_ship_inventory_resources(str(r["id"]), inventory_containers)
        inventory_capacity_summary = m.compute_ship_capacity_summary(inventory_containers)
        ships.append(
            {
                "id": r["id"],
                "name": r["name"],
                "shape": r["shape"],
                "color": r["color"],
                "size_px": r["size_px"],
                "notes": json.loads(r["notes_json"] or "[]"),
                "location_id": r["location_id"],
                "from_location_id": r["from_location_id"],
                "to_location_id": r["to_location_id"],
                "departed_at": r["departed_at"],
                "arrives_at": r["arrives_at"],
                "transfer_path": json.loads(r["transfer_path_json"] or "[]"),
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
                "status": "transit" if r["arrives_at"] else "docked",
            }
        )

    return {
        "user": {
            "username": user["username"],
            "is_admin": bool(user["is_admin"]),
        },
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

    require_login(conn, request)
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

    row = conn.execute(
        "SELECT dv_m_s,tof_s,path_json FROM transfer_matrix WHERE from_id=? AND to_id=?",
        (from_id, to_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No transfer data for that destination")

    dv = float(row["dv_m_s"])
    tof = float(row["tof_s"])
    path_json = row["path_json"] or "[]"

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

    fuel_used_kg = m.compute_fuel_needed_for_delta_v_kg(
        stats["dry_mass_kg"],
        stats["fuel_kg"],
        stats["isp_s"],
        dv,
    )
    fuel_remaining_kg = max(0.0, stats["fuel_kg"] - fuel_used_kg)

    dep = now_s
    arr = now_s + max(1.0, tof)

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
                        fuel_kg=?
        WHERE id=?
        """,
                    (from_id, to_id, dep, arr, path_json, dv, fuel_remaining_kg, ship_id),
    )
    conn.commit()

    return {
        "ok": True,
        "ship_id": ship_id,
        "from": from_id,
        "to": to_id,
        "dv_m_s": dv,
        "tof_s": tof,
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
    target_resource = str(target.get("resource_id") or "").lower()
    target_cargo_mass = max(0.0, float(target.get("cargo_mass_kg") or 0.0))

    if target_resource == "water":
        current_fuel_kg = max(0.0, current_fuel_kg - target_cargo_mass)

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
        parts[target_idx] = part

    stats = m.derive_ship_stats_from_parts(parts, current_fuel_kg=current_fuel_kg)
    conn.execute(
        """
        UPDATE ships
        SET parts_json=?, fuel_kg=?, fuel_capacity_kg=?, dry_mass_kg=?, isp_s=?
        WHERE id=?
        """,
        (
            json.dumps(parts),
            stats["fuel_kg"],
            stats["fuel_capacity_kg"],
            stats["dry_mass_kg"],
            stats["isp_s"],
            sid,
        ),
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
            resource_id = str(cargo.get("resource_id") or "").strip()
            cargo_mass_kg = max(0.0, float(cargo.get("cargo_mass_kg") or 0.0))
            if resource_id and cargo_mass_kg > 0.0:
                m.add_resource_to_location_inventory(conn, location_id, resource_id, cargo_mass_kg)
                if resource_id.lower() == "water":
                    transferred_fuel_like_kg += cargo_mass_kg
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

        m.add_part_to_location_inventory(conn, location_id, clean_part)

    if fuel_kg > transferred_fuel_like_kg + 1e-6:
        m.add_resource_to_location_inventory(conn, location_id, "water", fuel_kg - transferred_fuel_like_kg)

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
    target_resource = str(target.get("resource_id") or "").lower()
    target_cargo_mass = max(0.0, float(target.get("cargo_mass_kg") or 0.0))

    if target_resource == "water":
        current_fuel_kg = max(0.0, current_fuel_kg - target_cargo_mass)

    if not (0 <= target_idx < len(parts)):
        raise HTTPException(status_code=404, detail="Container not found")

    deployed_part = dict(parts.pop(target_idx) or {})
    if max(0.0, float(target.get("cargo_mass_kg") or 0.0)) > 0.0:
        deployed_part["resource_id"] = str(target.get("resource_id") or deployed_part.get("resource_id") or "")
        deployed_part["cargo_mass_kg"] = max(0.0, float(target.get("cargo_mass_kg") or 0.0))
        deployed_part["cargo_used_m3"] = max(0.0, float(target.get("used_m3") or 0.0))

    m.add_part_to_location_inventory(conn, location_id, deployed_part)

    stats = m.derive_ship_stats_from_parts(parts, current_fuel_kg=current_fuel_kg)
    conn.execute(
        """
        UPDATE ships
        SET parts_json=?, fuel_kg=?, fuel_capacity_kg=?, dry_mass_kg=?, isp_s=?
        WHERE id=?
        """,
        (
            json.dumps(parts),
            stats["fuel_kg"],
            stats["fuel_capacity_kg"],
            stats["dry_mass_kg"],
            stats["isp_s"],
            sid,
        ),
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
