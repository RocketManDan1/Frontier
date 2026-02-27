import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from db import APP_DIR

CONFIG_PATH = APP_DIR / "config" / "celestial_config.json"

LocationRow = Tuple[str, str, Optional[str], int, int, float, float]
EdgeRow = Tuple[str, str, float, float, str]


class CelestialConfigError(ValueError):
    pass


UNIX_EPOCH_JD = 2440587.5


def _as_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise CelestialConfigError(f"{field} must be numeric")


def _as_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise CelestialConfigError(f"{field} must be an integer")


def _require_str(obj: Dict[str, Any], key: str, ctx: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CelestialConfigError(f"{ctx}.{key} must be a non-empty string")
    return value.strip()


def _optional_str(obj: Dict[str, Any], key: str) -> Optional[str]:
    value = obj.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _get_angle_deg(entry: Dict[str, Any], key: str = "angle_deg") -> float:
    return _as_float(entry.get(key, 0.0), key)


def _unix_s_to_julian_day(unix_s: float) -> float:
    return (float(unix_s) / 86400.0) + UNIX_EPOCH_JD


def _solve_eccentric_anomaly(mean_anomaly_rad: float, eccentricity: float) -> float:
    m = float(mean_anomaly_rad)
    e = max(0.0, min(0.999999999, float(eccentricity)))

    estimate = m if e < 0.8 else math.pi
    for _ in range(25):
        f = estimate - e * math.sin(estimate) - m
        fp = 1.0 - e * math.cos(estimate)
        if abs(fp) < 1e-12:
            break
        step = f / fp
        estimate -= step
        if abs(step) < 1e-12:
            break
    return estimate


def _compute_keplerian_position(pos: Dict[str, Any], field_prefix: str, game_time_s: float) -> Tuple[float, float]:
    a_km = _as_float(pos.get("a_km"), f"{field_prefix}.a_km")
    if a_km <= 0.0:
        raise CelestialConfigError(f"{field_prefix}.a_km must be > 0")

    e = _as_float(pos.get("e", 0.0), f"{field_prefix}.e")
    if e < 0.0 or e >= 1.0:
        raise CelestialConfigError(f"{field_prefix}.e must be in [0, 1)")

    i_rad = math.radians(_as_float(pos.get("i_deg", 0.0), f"{field_prefix}.i_deg"))
    omega_big_rad = math.radians(_as_float(pos.get("Omega_deg", 0.0), f"{field_prefix}.Omega_deg"))
    omega_small_rad = math.radians(_as_float(pos.get("omega_deg", 0.0), f"{field_prefix}.omega_deg"))
    m0_rad = math.radians(_as_float(pos.get("M0_deg", 0.0), f"{field_prefix}.M0_deg"))
    epoch_jd = _as_float(pos.get("epoch_jd"), f"{field_prefix}.epoch_jd")
    period_s = _as_float(pos.get("period_s"), f"{field_prefix}.period_s")
    if period_s <= 0.0:
        raise CelestialConfigError(f"{field_prefix}.period_s must be > 0")

    current_jd = _unix_s_to_julian_day(game_time_s)
    dt_s = (current_jd - epoch_jd) * 86400.0
    n_rad_s = (2.0 * math.pi) / period_s
    m = m0_rad + n_rad_s * dt_s
    m = math.fmod(m, 2.0 * math.pi)
    if m < 0.0:
        m += 2.0 * math.pi

    eccentric_anomaly = _solve_eccentric_anomaly(m, e)
    cos_e = math.cos(eccentric_anomaly)
    sin_e = math.sin(eccentric_anomaly)
    x_orb = a_km * (cos_e - e)
    y_orb = a_km * math.sqrt(max(0.0, 1.0 - e * e)) * sin_e

    cos_omega_big = math.cos(omega_big_rad)
    sin_omega_big = math.sin(omega_big_rad)
    cos_omega_small = math.cos(omega_small_rad)
    sin_omega_small = math.sin(omega_small_rad)
    cos_i = math.cos(i_rad)

    r11 = cos_omega_big * cos_omega_small - sin_omega_big * sin_omega_small * cos_i
    r12 = -cos_omega_big * sin_omega_small - sin_omega_big * cos_omega_small * cos_i
    r21 = sin_omega_big * cos_omega_small + cos_omega_big * sin_omega_small * cos_i
    r22 = -sin_omega_big * sin_omega_small + cos_omega_big * cos_omega_small * cos_i

    x = r11 * x_orb + r12 * y_orb
    y = r21 * x_orb + r22 * y_orb
    return x, y


Vec3 = Tuple[float, float, float]


def _compute_keplerian_state_3d(
    pos: Dict[str, Any], field_prefix: str, game_time_s: float,
) -> Tuple[Vec3, Vec3]:
    """Compute 3D position (km) and velocity (km/s) from Keplerian elements.

    Returns ((x,y,z), (vx,vy,vz)) in the parent body's reference frame.
    """
    a_km = _as_float(pos.get("a_km"), f"{field_prefix}.a_km")
    if a_km <= 0.0:
        raise CelestialConfigError(f"{field_prefix}.a_km must be > 0")

    e = _as_float(pos.get("e", 0.0), f"{field_prefix}.e")
    if e < 0.0 or e >= 1.0:
        raise CelestialConfigError(f"{field_prefix}.e must be in [0, 1)")

    i_rad = math.radians(_as_float(pos.get("i_deg", 0.0), f"{field_prefix}.i_deg"))
    omega_big_rad = math.radians(_as_float(pos.get("Omega_deg", 0.0), f"{field_prefix}.Omega_deg"))
    omega_small_rad = math.radians(_as_float(pos.get("omega_deg", 0.0), f"{field_prefix}.omega_deg"))
    m0_rad = math.radians(_as_float(pos.get("M0_deg", 0.0), f"{field_prefix}.M0_deg"))
    epoch_jd = _as_float(pos.get("epoch_jd"), f"{field_prefix}.epoch_jd")
    period_s = _as_float(pos.get("period_s"), f"{field_prefix}.period_s")
    if period_s <= 0.0:
        raise CelestialConfigError(f"{field_prefix}.period_s must be > 0")

    current_jd = _unix_s_to_julian_day(game_time_s)
    dt_s = (current_jd - epoch_jd) * 86400.0
    n = (2.0 * math.pi) / period_s          # mean motion (rad/s)
    mean_anom = m0_rad + n * dt_s
    mean_anom = math.fmod(mean_anom, 2.0 * math.pi)
    if mean_anom < 0.0:
        mean_anom += 2.0 * math.pi

    E = _solve_eccentric_anomaly(mean_anom, e)
    cos_E = math.cos(E)
    sin_E = math.sin(E)
    sqrt_1me2 = math.sqrt(max(0.0, 1.0 - e * e))

    # Position in perifocal (orbital) frame
    x_orb = a_km * (cos_E - e)
    y_orb = a_km * sqrt_1me2 * sin_E

    # Velocity in perifocal frame  (km/s)
    denom = 1.0 - e * cos_E
    if abs(denom) < 1e-15:
        denom = 1e-15
    vx_orb = -n * a_km * sin_E / denom
    vy_orb = n * a_km * sqrt_1me2 * cos_E / denom

    # 3D rotation matrix (perifocal → inertial)
    cos_O = math.cos(omega_big_rad)
    sin_O = math.sin(omega_big_rad)
    cos_w = math.cos(omega_small_rad)
    sin_w = math.sin(omega_small_rad)
    cos_i = math.cos(i_rad)
    sin_i = math.sin(i_rad)

    r11 = cos_O * cos_w - sin_O * sin_w * cos_i
    r12 = -cos_O * sin_w - sin_O * cos_w * cos_i
    r13 = sin_O * sin_i
    r21 = sin_O * cos_w + cos_O * sin_w * cos_i
    r22 = -sin_O * sin_w + cos_O * cos_w * cos_i
    r23 = -cos_O * sin_i
    r31 = sin_w * sin_i
    r32 = cos_w * sin_i
    r33 = cos_i

    rx = r11 * x_orb + r12 * y_orb
    ry = r21 * x_orb + r22 * y_orb
    rz = r31 * x_orb + r32 * y_orb

    vx = r11 * vx_orb + r12 * vy_orb
    vy = r21 * vx_orb + r22 * vy_orb
    vz = r31 * vx_orb + r32 * vy_orb

    return (rx, ry, rz), (vx, vy, vz)


def load_celestial_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    if not path.exists():
        raise CelestialConfigError(f"Config not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CelestialConfigError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CelestialConfigError("Root config must be an object")
    return raw


def _compute_body_positions(bodies: Sequence[Dict[str, Any]], game_time_s: float = 0.0) -> Dict[str, Tuple[float, float]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for body in bodies:
        bid = _require_str(body, "id", "bodies[]")
        if bid in by_id:
            raise CelestialConfigError(f"Duplicate body id: {bid}")
        by_id[bid] = body

    unresolved = set(by_id.keys())
    positions: Dict[str, Tuple[float, float]] = {}

    while unresolved:
        progressed = False
        for bid in list(unresolved):
            body = by_id[bid]
            pos = body.get("position")
            if not isinstance(pos, dict):
                raise CelestialConfigError(f"Body {bid} must define a position object")

            pos_type = str(pos.get("type") or "").strip().lower()
            if pos_type == "fixed":
                x = _as_float(pos.get("x_km"), f"bodies[{bid}].position.x_km")
                y = _as_float(pos.get("y_km"), f"bodies[{bid}].position.y_km")
                positions[bid] = (x, y)
                unresolved.remove(bid)
                progressed = True
                continue

            if pos_type == "polar_from_body":
                center_body_id = str(pos.get("center_body_id") or "").strip()
                if not center_body_id:
                    raise CelestialConfigError(f"Body {bid} polar position needs center_body_id")
                if center_body_id not in positions:
                    continue
                radius_km = _as_float(pos.get("radius_km"), f"bodies[{bid}].position.radius_km")
                angle_deg = _get_angle_deg(pos)
                a = math.radians(angle_deg)
                cx, cy = positions[center_body_id]
                positions[bid] = (cx + radius_km * math.cos(a), cy + radius_km * math.sin(a))
                unresolved.remove(bid)
                progressed = True
                continue

            if pos_type == "keplerian":
                center_body_id = str(pos.get("center_body_id") or "").strip()
                if center_body_id and center_body_id not in positions:
                    continue
                x_local, y_local = _compute_keplerian_position(pos, f"bodies[{bid}].position", game_time_s)
                if center_body_id:
                    cx, cy = positions[center_body_id]
                    positions[bid] = (cx + x_local, cy + y_local)
                else:
                    positions[bid] = (x_local, y_local)
                unresolved.remove(bid)
                progressed = True
                continue

            raise CelestialConfigError(f"Body {bid} has unsupported position.type: {pos_type}")

        if not progressed:
            raise CelestialConfigError(
                "Unable to resolve body positions (check center_body_id references and cycles): "
                + ", ".join(sorted(unresolved))
            )

    return positions


def _line_point(primary: Tuple[float, float], unit: Tuple[float, float], distance_km: float, sign: float) -> Tuple[float, float]:
    return (primary[0] + unit[0] * distance_km * sign, primary[1] + unit[1] * distance_km * sign)


def _triangle_point(primary: Tuple[float, float], unit: Tuple[float, float], tangent: Tuple[float, float], distance_km: float, sign: float) -> Tuple[float, float]:
    c = 0.5
    s = (math.sqrt(3.0) / 2.0) * sign
    return (
        primary[0] + (c * unit[0] + s * tangent[0]) * distance_km,
        primary[1] + (c * unit[1] + s * tangent[1]) * distance_km,
    )


def _body_group_row(body: Dict[str, Any], body_pos: Dict[str, Tuple[float, float]]) -> Optional[LocationRow]:
    if body.get("emit_group", True) is False:
        return None
    body_id = _require_str(body, "id", "bodies[]")
    group_id = _require_str(body, "group_id", f"bodies[{body_id}]")
    name = _require_str(body, "name", f"bodies[{body_id}]")
    parent_group_id_raw = body.get("parent_group_id")
    parent_group_id = str(parent_group_id_raw).strip() if isinstance(parent_group_id_raw, str) and parent_group_id_raw.strip() else None
    sort_order = _as_int(body.get("sort_order", 100), f"bodies[{body_id}].sort_order")
    x, y = body_pos[body_id]
    return (group_id, name, parent_group_id, 1, sort_order, float(x), float(y))


def build_locations_and_edges(config: Dict[str, Any], game_time_s: Optional[float] = None) -> Tuple[List[LocationRow], List[EdgeRow]]:
    bodies = config.get("bodies")
    if not isinstance(bodies, list) or not bodies:
        raise CelestialConfigError("bodies must be a non-empty array")

    resolved_game_time_s = float(game_time_s) if game_time_s is not None else 0.0
    body_pos = _compute_body_positions(bodies, game_time_s=resolved_game_time_s)

    location_rows: List[LocationRow] = []
    location_ids: set[str] = set()
    leaf_ids: set[str] = set()

    def add_row(row: LocationRow) -> None:
        loc_id = row[0]
        if loc_id in location_ids:
            raise CelestialConfigError(f"Duplicate location id generated: {loc_id}")
        location_rows.append(row)
        location_ids.add(loc_id)
        if row[3] == 0:
            leaf_ids.add(loc_id)

    for body in bodies:
        row = _body_group_row(body, body_pos)
        if row is not None:
            add_row(row)

    groups = config.get("groups", [])
    if not isinstance(groups, list):
        raise CelestialConfigError("groups must be an array")
    for g in groups:
        if not isinstance(g, dict):
            raise CelestialConfigError("groups[] entries must be objects")
        gid = _require_str(g, "id", "groups[]")
        name = _require_str(g, "name", f"groups[{gid}]")
        parent_id_raw = g.get("parent_id")
        parent_id = str(parent_id_raw).strip() if isinstance(parent_id_raw, str) and parent_id_raw.strip() else None
        sort_order = _as_int(g.get("sort_order", 100), f"groups[{gid}].sort_order")

        anchor_body_id = str(g.get("anchor_body_id") or "").strip()
        offset_x = _as_float(g.get("offset_x_km", 0.0), f"groups[{gid}].offset_x_km")
        offset_y = _as_float(g.get("offset_y_km", 0.0), f"groups[{gid}].offset_y_km")
        if anchor_body_id:
            if anchor_body_id not in body_pos:
                raise CelestialConfigError(f"groups[{gid}] anchor_body_id references unknown body: {anchor_body_id}")
            bx, by = body_pos[anchor_body_id]
            x, y = bx + offset_x, by + offset_y
        else:
            x = _as_float(g.get("x_km", 0.0), f"groups[{gid}].x_km")
            y = _as_float(g.get("y_km", 0.0), f"groups[{gid}].y_km")

        add_row((gid, name, parent_id, 1, sort_order, float(x), float(y)))

    orbit_nodes = config.get("orbit_nodes", [])
    if not isinstance(orbit_nodes, list):
        raise CelestialConfigError("orbit_nodes must be an array")
    for node in orbit_nodes:
        if not isinstance(node, dict):
            raise CelestialConfigError("orbit_nodes[] entries must be objects")
        nid = _require_str(node, "id", "orbit_nodes[]")
        name = _require_str(node, "name", f"orbit_nodes[{nid}]")
        parent_id = _require_str(node, "parent_id", f"orbit_nodes[{nid}]")
        body_id = _require_str(node, "body_id", f"orbit_nodes[{nid}]")
        sort_order = _as_int(node.get("sort_order", 100), f"orbit_nodes[{nid}].sort_order")
        angle_deg = _get_angle_deg(node)

        if body_id not in body_pos:
            raise CelestialConfigError(f"orbit_nodes[{nid}] references unknown body_id: {body_id}")
        bx, by = body_pos[body_id]

        radius_km_raw = node.get("radius_km")
        altitude_km_raw = node.get("altitude_km")
        if radius_km_raw is not None:
            radius_km = _as_float(radius_km_raw, f"orbit_nodes[{nid}].radius_km")
        elif altitude_km_raw is not None:
            radius_body_km = _as_float(next((b.get("radius_km") for b in bodies if b.get("id") == body_id), None), f"bodies[{body_id}].radius_km")
            radius_km = radius_body_km + _as_float(altitude_km_raw, f"orbit_nodes[{nid}].altitude_km")
        else:
            raise CelestialConfigError(f"orbit_nodes[{nid}] requires radius_km or altitude_km")

        a = math.radians(angle_deg)
        x = bx + radius_km * math.cos(a)
        y = by + radius_km * math.sin(a)
        add_row((nid, name, parent_id, 0, sort_order, float(x), float(y)))

    lagrange_systems = config.get("lagrange_systems", [])
    if not isinstance(lagrange_systems, list):
        raise CelestialConfigError("lagrange_systems must be an array")
    for system in lagrange_systems:
        if not isinstance(system, dict):
            raise CelestialConfigError("lagrange_systems[] entries must be objects")
        sid = _require_str(system, "id", "lagrange_systems[]")
        primary_body_id = _require_str(system, "primary_body_id", f"lagrange_systems[{sid}]")
        secondary_body_id = _require_str(system, "secondary_body_id", f"lagrange_systems[{sid}]")
        parent_group_id = _require_str(system, "parent_group_id", f"lagrange_systems[{sid}]")

        if primary_body_id not in body_pos or secondary_body_id not in body_pos:
            raise CelestialConfigError(f"lagrange_systems[{sid}] references unknown bodies")

        primary = body_pos[primary_body_id]
        secondary = body_pos[secondary_body_id]
        dx = secondary[0] - primary[0]
        dy = secondary[1] - primary[1]
        distance = max(1e-9, math.hypot(dx, dy))
        unit = (dx / distance, dy / distance)
        tangent = (-unit[1], unit[0])

        points = system.get("points", [])
        if not isinstance(points, list) or not points:
            raise CelestialConfigError(f"lagrange_systems[{sid}].points must be a non-empty array")

        for point in points:
            if not isinstance(point, dict):
                raise CelestialConfigError(f"lagrange_systems[{sid}].points[] entries must be objects")
            pid = _require_str(point, "id", f"lagrange_systems[{sid}].points[]")
            name = _require_str(point, "name", f"lagrange_systems[{sid}].points[{pid}]")
            sort_order = _as_int(point.get("sort_order", 100), f"lagrange_systems[{sid}].points[{pid}].sort_order")
            model = str(point.get("model") or "").strip().lower()

            if model in ("line_primary_plus", "line_primary_minus"):
                distance_km = _as_float(point.get("distance_km"), f"lagrange_systems[{sid}].points[{pid}].distance_km")
                sign = 1.0 if model.endswith("plus") else -1.0
                x, y = _line_point(primary, unit, distance_km, sign)
            elif model in ("triangle_plus", "triangle_minus"):
                sign = 1.0 if model.endswith("plus") else -1.0
                x, y = _triangle_point(primary, unit, tangent, distance, sign)
            else:
                raise CelestialConfigError(
                    f"lagrange_systems[{sid}].points[{pid}] has unsupported model: {model}"
                )

            add_row((pid, name, parent_group_id, 0, sort_order, float(x), float(y)))

    markers = config.get("markers", [])
    if not isinstance(markers, list):
        raise CelestialConfigError("markers must be an array")
    for marker in markers:
        if not isinstance(marker, dict):
            raise CelestialConfigError("markers[] entries must be objects")
        mid = _require_str(marker, "id", "markers[]")
        name = _require_str(marker, "name", f"markers[{mid}]")
        parent_id = _require_str(marker, "parent_id", f"markers[{mid}]")
        sort_order = _as_int(marker.get("sort_order", 100), f"markers[{mid}].sort_order")

        body_id = str(marker.get("body_id") or "").strip()
        if body_id:
            if body_id not in body_pos:
                raise CelestialConfigError(f"markers[{mid}] references unknown body_id: {body_id}")
            bx, by = body_pos[body_id]
            x = bx + _as_float(marker.get("offset_x_km", 0.0), f"markers[{mid}].offset_x_km")
            y = by + _as_float(marker.get("offset_y_km", 0.0), f"markers[{mid}].offset_y_km")
        else:
            x = _as_float(marker.get("x_km"), f"markers[{mid}].x_km")
            y = _as_float(marker.get("y_km"), f"markers[{mid}].y_km")

        add_row((mid, name, parent_id, 0, sort_order, float(x), float(y)))

    # ── Surface Sites ──────────────────────────────────────
    surface_sites = config.get("surface_sites", [])
    if not isinstance(surface_sites, list):
        raise CelestialConfigError("surface_sites must be an array")

    body_by_id: Dict[str, Dict[str, Any]] = {}
    for body in bodies:
        bid = str(body.get("id") or "").strip()
        if bid:
            body_by_id[bid] = body

    surface_edge_rows: List[EdgeRow] = []
    for site in surface_sites:
        if not isinstance(site, dict):
            raise CelestialConfigError("surface_sites[] entries must be objects")
        sid = _require_str(site, "id", "surface_sites[]")
        name = _require_str(site, "name", f"surface_sites[{sid}]")
        body_id = _require_str(site, "body_id", f"surface_sites[{sid}]")
        parent_id = _require_str(site, "parent_group_id", f"surface_sites[{sid}]")
        orbit_node_id = _require_str(site, "orbit_node_id", f"surface_sites[{sid}]")
        sort_order = _as_int(site.get("sort_order", 100), f"surface_sites[{sid}].sort_order")
        angle_deg = _get_angle_deg(site)

        if body_id not in body_pos:
            raise CelestialConfigError(f"surface_sites[{sid}] references unknown body_id: {body_id}")
        if orbit_node_id not in leaf_ids:
            raise CelestialConfigError(f"surface_sites[{sid}] references unknown orbit_node_id: {orbit_node_id}")

        body_def = body_by_id[body_id]
        radius_km = _as_float(body_def.get("radius_km", 0.0), f"bodies[{body_id}].radius_km")
        bx, by = body_pos[body_id]
        a = math.radians(angle_deg)
        x = bx + radius_km * math.cos(a)
        y = by + radius_km * math.sin(a)

        add_row((sid, name, parent_id, 0, sort_order, float(x), float(y)))

        # Generate bidirectional transfer edges for landing/ascent
        landing_dv = _as_float(site.get("landing_dv_m_s", 1870), f"surface_sites[{sid}].landing_dv_m_s")
        landing_tof = _as_float(site.get("landing_tof_s", 3600), f"surface_sites[{sid}].landing_tof_s")
        surface_edge_rows.append((orbit_node_id, sid, float(landing_dv), float(landing_tof), "landing"))
        surface_edge_rows.append((sid, orbit_node_id, float(landing_dv), float(landing_tof), "landing"))

    for loc_id, _, parent_id, _, _, _, _ in location_rows:
        if parent_id and parent_id not in location_ids:
            raise CelestialConfigError(f"Location {loc_id} references unknown parent_id: {parent_id}")

    transfer_edges = config.get("transfer_edges", [])
    if not isinstance(transfer_edges, list):
        raise CelestialConfigError("transfer_edges must be an array")

    edge_rows: List[EdgeRow] = []
    edge_ids: set[Tuple[str, str]] = set()
    for edge in transfer_edges:
        if not isinstance(edge, dict):
            raise CelestialConfigError("transfer_edges[] entries must be objects")
        src = _require_str(edge, "from_id", "transfer_edges[]")
        dst = _require_str(edge, "to_id", "transfer_edges[]")
        key = (src, dst)
        if key in edge_ids:
            raise CelestialConfigError(f"Duplicate transfer edge: {src}->{dst}")
        edge_ids.add(key)
        if src not in leaf_ids or dst not in leaf_ids:
            raise CelestialConfigError(
                f"transfer edge {src}->{dst} references missing non-group locations"
            )
        dv_m_s = _as_float(edge.get("dv_m_s"), f"transfer_edges[{src}->{dst}].dv_m_s")
        tof_s = _as_float(edge.get("tof_s"), f"transfer_edges[{src}->{dst}].tof_s")
        edge_type = edge.get("type", "local")
        if edge_type not in ("local", "interplanetary", "lagrange"):
            raise CelestialConfigError(
                f"transfer_edges[{src}->{dst}].type must be 'local', 'interplanetary', or 'lagrange'; got '{edge_type}'"
            )
        edge_rows.append((src, dst, float(dv_m_s), float(tof_s), str(edge_type)))

    # ── Auto-generate interplanetary edges if enabled ───────
    auto_ip = config.get("auto_interplanetary_edges", False)
    if auto_ip:
        # Remove hand-authored interplanetary edges (keep local/lagrange)
        edge_rows = [e for e in edge_rows if e[4] != "interplanetary"]
        edge_ids = {(e[0], e[1]) for e in edge_rows}

        # Generate from topology
        auto_edges = generate_interplanetary_edges(config, hohmann_estimate=True)
        for ae in auto_edges:
            key = (ae[0], ae[1])
            if key not in edge_ids:
                # Verify both endpoints exist as leaf locations
                if ae[0] in leaf_ids and ae[1] in leaf_ids:
                    edge_rows.append(ae)
                    edge_ids.add(key)

    # Append surface site landing/ascent edges
    edge_rows.extend(surface_edge_rows)

    return location_rows, edge_rows


def load_locations_and_edges(path: Path = CONFIG_PATH, game_time_s: Optional[float] = None) -> Tuple[List[LocationRow], List[EdgeRow]]:
    config = load_celestial_config(path)
    return build_locations_and_edges(config, game_time_s=game_time_s)


# ── Surface Site data types ────────────────────────────────

SurfaceSiteRow = Tuple[str, str, str, float]  # (location_id, body_id, orbit_node_id, gravity_m_s2)
SurfaceSiteResourceRow = Tuple[str, str, float]  # (site_location_id, resource_id, mass_fraction)


def build_surface_site_data(config: Dict[str, Any]) -> Tuple[List[SurfaceSiteRow], List[SurfaceSiteResourceRow]]:
    """Parse surface_sites from config and return rows for DB tables."""
    bodies = config.get("bodies", [])
    body_by_id: Dict[str, Dict[str, Any]] = {}
    for body in (bodies if isinstance(bodies, list) else []):
        bid = str(body.get("id") or "").strip()
        if bid:
            body_by_id[bid] = body

    surface_sites = config.get("surface_sites", [])
    if not isinstance(surface_sites, list):
        return [], []

    site_rows: List[SurfaceSiteRow] = []
    resource_rows: List[SurfaceSiteResourceRow] = []

    for site in surface_sites:
        if not isinstance(site, dict):
            continue
        sid = _require_str(site, "id", "surface_sites[]")
        body_id = _require_str(site, "body_id", f"surface_sites[{sid}]")
        orbit_node_id = _require_str(site, "orbit_node_id", f"surface_sites[{sid}]")

        body_def = body_by_id.get(body_id)
        if not body_def:
            raise CelestialConfigError(f"surface_sites[{sid}] references unknown body_id: {body_id}")
        gravity = _as_float(body_def.get("gravity_m_s2", 0.0), f"bodies[{body_id}].gravity_m_s2")

        site_rows.append((sid, body_id, orbit_node_id, gravity))

        resource_dist = site.get("resource_distribution", {})
        if isinstance(resource_dist, dict):
            for resource_id, fraction in resource_dist.items():
                frac = float(fraction)
                if frac > 0:
                    resource_rows.append((sid, str(resource_id).strip(), frac))

    return site_rows, resource_rows


def load_surface_site_data(path: Path = CONFIG_PATH) -> Tuple[List[SurfaceSiteRow], List[SurfaceSiteResourceRow]]:
    config = load_celestial_config(path)
    return build_surface_site_data(config)


def build_location_metadata(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    bodies = config.get("bodies", [])
    groups = config.get("groups", [])
    orbit_nodes = config.get("orbit_nodes", [])
    lagrange_systems = config.get("lagrange_systems", [])
    markers = config.get("markers", [])

    metadata_by_location_id: Dict[str, Dict[str, Any]] = {}
    body_metadata_by_body_id: Dict[str, Dict[str, Any]] = {}

    def extract_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        symbol = _optional_str(entry, "symbol")
        wikipedia_title = _optional_str(entry, "wikipedia_title")
        wikipedia_url = _optional_str(entry, "wikipedia_url")
        if symbol:
            out["symbol"] = symbol
        if wikipedia_title:
            out["wikipedia_title"] = wikipedia_title
        if wikipedia_url:
            out["wikipedia_url"] = wikipedia_url
        return out

    for body in bodies:
        if not isinstance(body, dict):
            continue
        body_id = _optional_str(body, "id")
        body_meta = extract_metadata(body)
        if body_id:
            body_metadata_by_body_id[body_id] = body_meta

        emit_group = body.get("emit_group", True) is not False
        group_id = _optional_str(body, "group_id")
        if emit_group and group_id and body_meta:
            metadata_by_location_id[group_id] = dict(body_meta)

    for group in groups:
        if not isinstance(group, dict):
            continue
        gid = _optional_str(group, "id")
        if not gid:
            continue
        group_meta = extract_metadata(group)
        if group_meta:
            metadata_by_location_id[gid] = {
                **metadata_by_location_id.get(gid, {}),
                **group_meta,
            }

    for orbit_node in orbit_nodes:
        if not isinstance(orbit_node, dict):
            continue
        nid = _optional_str(orbit_node, "id")
        if not nid:
            continue
        orbit_meta = extract_metadata(orbit_node)
        if orbit_meta:
            metadata_by_location_id[nid] = {
                **metadata_by_location_id.get(nid, {}),
                **orbit_meta,
            }

    for system in lagrange_systems:
        if not isinstance(system, dict):
            continue
        points = system.get("points", [])
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict):
                continue
            pid = _optional_str(point, "id")
            if not pid:
                continue
            point_meta = extract_metadata(point)
            if point_meta:
                metadata_by_location_id[pid] = {
                    **metadata_by_location_id.get(pid, {}),
                    **point_meta,
                }

    for marker in markers:
        if not isinstance(marker, dict):
            continue
        mid = _optional_str(marker, "id")
        if not mid:
            continue
        marker_meta = extract_metadata(marker)
        body_id = _optional_str(marker, "body_id")
        inherited_meta = body_metadata_by_body_id.get(body_id or "", {})
        merged_meta = {
            **inherited_meta,
            **marker_meta,
        }
        if merged_meta:
            metadata_by_location_id[mid] = {
                **metadata_by_location_id.get(mid, {}),
                **merged_meta,
            }

    # Surface sites
    surface_sites = config.get("surface_sites", [])
    if isinstance(surface_sites, list):
        for site in surface_sites:
            if not isinstance(site, dict):
                continue
            sid = _optional_str(site, "id")
            if not sid:
                continue
            site_meta = extract_metadata(site)
            body_id = _optional_str(site, "body_id")
            inherited_meta = body_metadata_by_body_id.get(body_id or "", {})
            merged_meta = {
                **inherited_meta,
                **site_meta,
                "is_surface_site": True,
                "body_id": body_id,
            }
            metadata_by_location_id[sid] = {
                **metadata_by_location_id.get(sid, {}),
                **merged_meta,
            }

    return metadata_by_location_id


def load_location_metadata(path: Path = CONFIG_PATH) -> Dict[str, Dict[str, Any]]:
    config = load_celestial_config(path)
    return build_location_metadata(config)


# ── Body state vector API ──────────────────────────────────────

def _build_bodies_by_id(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Index config bodies by id."""
    bodies = config.get("bodies")
    if not isinstance(bodies, list):
        return {}
    return {str(b.get("id", "")).strip(): b for b in bodies if isinstance(b, dict) and b.get("id")}


def compute_body_state(
    config: Dict[str, Any],
    body_id: str,
    game_time_s: float,
) -> Tuple[Vec3, Vec3]:
    """Compute heliocentric 3D position (km) and velocity (km/s) for a body.

    Resolves the parent chain (e.g. Moon → Earth → Sun) by accumulating
    positions and velocities up the hierarchy.

    Returns ((x, y, z), (vx, vy, vz)) in heliocentric frame.
    """
    bodies_by_id = _build_bodies_by_id(config)
    return _compute_body_state_recursive(bodies_by_id, body_id, game_time_s)


def _compute_body_state_recursive(
    bodies_by_id: Dict[str, Dict[str, Any]],
    body_id: str,
    game_time_s: float,
) -> Tuple[Vec3, Vec3]:
    body = bodies_by_id.get(body_id)
    if not body:
        raise CelestialConfigError(f"Unknown body_id: {body_id}")

    pos = body.get("position")
    if not isinstance(pos, dict):
        raise CelestialConfigError(f"Body {body_id} must define a position object")

    pos_type = str(pos.get("type") or "").strip().lower()

    if pos_type == "fixed":
        x = _as_float(pos.get("x_km", 0.0), f"bodies[{body_id}].position.x_km")
        y = _as_float(pos.get("y_km", 0.0), f"bodies[{body_id}].position.y_km")
        return (x, y, 0.0), (0.0, 0.0, 0.0)

    if pos_type == "polar_from_body":
        # Static position, zero velocity (belt marker etc.)
        center_id = str(pos.get("center_body_id") or "").strip()
        radius_km = _as_float(pos.get("radius_km", 0.0), f"bodies[{body_id}].position.radius_km")
        angle_rad = math.radians(_as_float(pos.get("angle_deg", 0.0), f"bodies[{body_id}].position.angle_deg"))
        parent_r, parent_v = _compute_body_state_recursive(bodies_by_id, center_id, game_time_s) if center_id else ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        return (
            (parent_r[0] + radius_km * math.cos(angle_rad), parent_r[1] + radius_km * math.sin(angle_rad), parent_r[2]),
            parent_v,
        )

    if pos_type == "keplerian":
        center_id = str(pos.get("center_body_id") or "").strip()
        local_r, local_v = _compute_keplerian_state_3d(pos, f"bodies[{body_id}].position", game_time_s)

        if center_id:
            parent_r, parent_v = _compute_body_state_recursive(bodies_by_id, center_id, game_time_s)
            return (
                (parent_r[0] + local_r[0], parent_r[1] + local_r[1], parent_r[2] + local_r[2]),
                (parent_v[0] + local_v[0], parent_v[1] + local_v[1], parent_v[2] + local_v[2]),
            )
        return local_r, local_v

    raise CelestialConfigError(f"Body {body_id} has unsupported position.type: {pos_type}")


def get_body_mu(config: Dict[str, Any], body_id: str) -> float:
    """Get gravitational parameter μ (km³/s²) for a body from config."""
    bodies_by_id = _build_bodies_by_id(config)
    body = bodies_by_id.get(body_id)
    if not body:
        raise CelestialConfigError(f"Unknown body_id: {body_id}")
    mu = body.get("mu_km3_s2")
    if mu is None:
        raise CelestialConfigError(f"Body {body_id} has no mu_km3_s2")
    return float(mu)


def get_body_radius(config: Dict[str, Any], body_id: str) -> float:
    """Get body radius (km) from config."""
    bodies_by_id = _build_bodies_by_id(config)
    body = bodies_by_id.get(body_id)
    if not body:
        raise CelestialConfigError(f"Unknown body_id: {body_id}")
    return float(body.get("radius_km", 0.0))


def get_body_soi(config: Dict[str, Any], body_id: str) -> Optional[float]:
    """Get SOI radius (km) for a body, or None if not defined."""
    bodies_by_id = _build_bodies_by_id(config)
    body = bodies_by_id.get(body_id)
    if not body:
        return None
    soi = body.get("soi_radius_km")
    return float(soi) if soi is not None else None


def build_location_parent_body_map(config: Dict[str, Any]) -> Dict[str, str]:
    """Build location_id → parent_body_id mapping from orbit_nodes, markers, and surface_sites."""
    result: Dict[str, str] = {}

    for node in (config.get("orbit_nodes") or []):
        if isinstance(node, dict):
            nid = node.get("id")
            bid = node.get("body_id")
            if nid and bid:
                result[str(nid)] = str(bid)

    for marker in (config.get("markers") or []):
        if isinstance(marker, dict):
            mid = marker.get("id")
            bid = marker.get("body_id")
            if mid and bid:
                result[str(mid)] = str(bid)

    for site in (config.get("surface_sites") or []):
        if isinstance(site, dict):
            sid = site.get("id")
            bid = site.get("body_id")
            if sid and bid:
                result[str(sid)] = str(bid)

    # Lagrange points → map to primary body
    for lsys in (config.get("lagrange_systems") or []):
        if isinstance(lsys, dict):
            primary = lsys.get("primary_body_id")
            for pt in (lsys.get("points") or []):
                if isinstance(pt, dict) and pt.get("id") and primary:
                    result[str(pt["id"])] = str(primary)

    return result


def get_orbit_node_radius(config: Dict[str, Any], location_id: str) -> Optional[float]:
    """Get the orbital radius (km from body center) for an orbit_node location."""
    for node in (config.get("orbit_nodes") or []):
        if isinstance(node, dict) and str(node.get("id", "")) == location_id:
            r = node.get("radius_km")
            if r is not None:
                return float(r)
    return None


def get_orbit_node_body_id(config: Dict[str, Any], location_id: str) -> Optional[str]:
    """Get the body_id that an orbit_node location orbits."""
    for node in (config.get("orbit_nodes") or []):
        if isinstance(node, dict) and str(node.get("id", "")) == location_id:
            bid = str(node.get("body_id", "")).strip()
            return bid if bid else None
    return None


# ── Auto-generation of interplanetary transfer edges ────────


def _get_body_parent_id(body: Dict[str, Any]) -> str:
    """Return the center_body_id for a body (empty string if none/sun)."""
    pos = body.get("position", {})
    center = str(pos.get("center_body_id", "")).strip()
    return center


def _resolve_helio_body_id(bodies_by_id: Dict[str, Dict[str, Any]], body_id: str) -> str:
    """Walk up the parent chain to find the heliocentric body (parent is sun/empty)."""
    visited: set = set()
    current = body_id
    while current and current != "sun" and current not in visited:
        visited.add(current)
        body = bodies_by_id.get(current)
        if not body:
            return body_id
        parent = _get_body_parent_id(body)
        if not parent or parent == "sun":
            return current
        current = parent
    return body_id


def _find_gateway_location(
    config: Dict[str, Any],
    body_id: str,
    bodies_by_id: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    """Find the gateway orbit location for a body.

    Priority:
    1. Body's explicit ``gateway_location_id`` field (if set in config)
    2. The orbit_node with the smallest radius_km around that body
    3. None if no orbit_nodes exist for the body
    """
    body = bodies_by_id.get(body_id)
    if body:
        explicit = str(body.get("gateway_location_id", "")).strip()
        if explicit:
            return explicit

    # Find lowest orbit
    best_id: Optional[str] = None
    best_radius = float("inf")
    for node in (config.get("orbit_nodes") or []):
        if not isinstance(node, dict):
            continue
        if str(node.get("body_id", "")) != body_id:
            continue
        r = node.get("radius_km")
        if r is not None and float(r) < best_radius:
            best_radius = float(r)
            best_id = str(node["id"])

    return best_id


def generate_interplanetary_edges(
    config: Dict[str, Any],
    hohmann_estimate: bool = True,
) -> List[EdgeRow]:
    """Auto-generate bidirectional interplanetary transfer edges from topology.

    Rules:
    1. Identify all heliocentric bodies (bodies whose parent is sun or empty)
       that have at least one orbit_node (gateway location).
    2. Generate a bidirectional interplanetary edge between each pair of
       heliocentric bodies that both have SOI radii defined.
    3. Use Hohmann-estimate Δv/TOF as placeholder values (the Lambert solver
       will override these dynamically at query time).
    4. Moons are not directly connected — they are reached via local edges
       from their parent planet's gateway.

    Parameters
    ----------
    config : loaded celestial_config dict
    hohmann_estimate : if True, compute Hohmann Δv/TOF estimates for the
        placeholder edge values; if False, use zero (Lambert overrides anyway).

    Returns
    -------
    List of EdgeRow tuples: (from_id, to_id, dv_m_s, tof_s, "interplanetary")
    """
    bodies_by_id = _build_bodies_by_id(config)
    mu_sun = 0.0
    sun_body = bodies_by_id.get("sun")
    if sun_body:
        mu_sun = float(sun_body.get("mu_km3_s2", 0.0))

    # 1. Identify heliocentric bodies with gateways and SOI
    helio_bodies: List[Dict[str, Any]] = []
    for body in (config.get("bodies") or []):
        if not isinstance(body, dict):
            continue
        bid = str(body.get("id", "")).strip()
        if not bid or bid == "sun":
            continue
        parent = _get_body_parent_id(body)
        if parent and parent != "sun":
            continue  # Moon or sub-satellite — skip

        gateway = _find_gateway_location(config, bid, bodies_by_id)
        if not gateway:
            continue  # No orbit node on this body

        # SOI is optional — bodies without SOI still get edges
        soi = body.get("soi_radius_km")

        helio_bodies.append({
            "id": bid,
            "gateway": gateway,
            "soi": float(soi) if soi is not None else None,
        })

    if not helio_bodies or mu_sun <= 0:
        return []

    # 2. For each pair, generate bidirectional edges
    edges: List[EdgeRow] = []
    n = len(helio_bodies)
    for i in range(n):
        for j in range(i + 1, n):
            a = helio_bodies[i]
            b = helio_bodies[j]

            dv_estimate = 0.0
            tof_estimate = 86400.0  # 1-day placeholder

            if hohmann_estimate and mu_sun > 0:
                # Get approximate orbital radii for Hohmann estimate
                body_a = bodies_by_id.get(a["id"])
                body_b = bodies_by_id.get(b["id"])
                if body_a and body_b:
                    pos_a = body_a.get("position", {})
                    pos_b = body_b.get("position", {})
                    r_a = float(pos_a.get("a_km", 0.0))
                    r_b = float(pos_b.get("a_km", 0.0))
                    if r_a > 0 and r_b > 0:
                        # Hohmann estimate: Δv ≈ |v_circ_dep - v_transfer_dep| + |v_circ_arr - v_transfer_arr|
                        a_t = 0.5 * (r_a + r_b)
                        if a_t > 0:
                            v1_circ = math.sqrt(mu_sun / r_a)
                            v2_circ = math.sqrt(mu_sun / r_b)
                            v1_trans = math.sqrt(mu_sun * (2.0 / r_a - 1.0 / a_t))
                            v2_trans = math.sqrt(mu_sun * (2.0 / r_b - 1.0 / a_t))
                            # v_inf at each end
                            v_inf_dep = abs(v1_trans - v1_circ)
                            v_inf_arr = abs(v2_trans - v2_circ)
                            # Convert to patched-conic Δv from parking orbit
                            mu_a = float(body_a.get("mu_km3_s2", 0.0))
                            mu_b = float(body_b.get("mu_km3_s2", 0.0))
                            r_gw_a = get_orbit_node_radius(config, a["gateway"])
                            r_gw_b = get_orbit_node_radius(config, b["gateway"])

                            dv_dep = v_inf_dep
                            if mu_a > 0 and r_gw_a and r_gw_a > 0:
                                v_park = math.sqrt(mu_a / r_gw_a)
                                v_hyp = math.sqrt(v_inf_dep**2 + 2.0 * mu_a / r_gw_a)
                                dv_dep = abs(v_hyp - v_park)

                            dv_arr = v_inf_arr
                            if mu_b > 0 and r_gw_b and r_gw_b > 0:
                                v_park = math.sqrt(mu_b / r_gw_b)
                                v_hyp = math.sqrt(v_inf_arr**2 + 2.0 * mu_b / r_gw_b)
                                dv_arr = abs(v_hyp - v_park)

                            dv_estimate = (dv_dep + dv_arr) * 1000.0  # km/s → m/s
                            tof_estimate = math.pi * math.sqrt(a_t**3 / mu_sun)

            edges.append((a["gateway"], b["gateway"], dv_estimate, tof_estimate, "interplanetary"))
            edges.append((b["gateway"], a["gateway"], dv_estimate, tof_estimate, "interplanetary"))

    return edges


def get_auto_edge_gateway_map(config: Dict[str, Any]) -> Dict[str, str]:
    """Return a body_id → gateway_location_id mapping (for diagnostics/tests)."""
    bodies_by_id = _build_bodies_by_id(config)
    result: Dict[str, str] = {}
    for body in (config.get("bodies") or []):
        if not isinstance(body, dict):
            continue
        bid = str(body.get("id", "")).strip()
        if not bid or bid == "sun":
            continue
        parent = _get_body_parent_id(body)
        if parent and parent != "sun":
            continue
        gw = _find_gateway_location(config, bid, bodies_by_id)
        if gw:
            result[bid] = gw
    return result
