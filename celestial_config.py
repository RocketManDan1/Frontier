import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from db import APP_DIR

CONFIG_PATH = APP_DIR / "config" / "celestial_config.json"

LocationRow = Tuple[str, str, Optional[str], int, int, float, float]
EdgeRow = Tuple[str, str, float, float]


class CelestialConfigError(ValueError):
    pass


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


def _compute_body_positions(bodies: Sequence[Dict[str, Any]]) -> Dict[str, Tuple[float, float]]:
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


def build_locations_and_edges(config: Dict[str, Any]) -> Tuple[List[LocationRow], List[EdgeRow]]:
    bodies = config.get("bodies")
    if not isinstance(bodies, list) or not bodies:
        raise CelestialConfigError("bodies must be a non-empty array")

    body_pos = _compute_body_positions(bodies)

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
        surface_edge_rows.append((orbit_node_id, sid, float(landing_dv), float(landing_tof)))
        surface_edge_rows.append((sid, orbit_node_id, float(landing_dv), float(landing_tof)))

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
        edge_rows.append((src, dst, float(dv_m_s), float(tof_s)))

    # Append surface site landing/ascent edges
    edge_rows.extend(surface_edge_rows)

    return location_rows, edge_rows


def load_locations_and_edges(path: Path = CONFIG_PATH) -> Tuple[List[LocationRow], List[EdgeRow]]:
    config = load_celestial_config(path)
    return build_locations_and_edges(config)


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
