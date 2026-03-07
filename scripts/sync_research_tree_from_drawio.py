#!/usr/bin/env python3
"""Sync config/research_tree.json node layout (and optionally edges) from a draw.io file.

Usage:
  python3 scripts/sync_research_tree_from_drawio.py \
    --drawio /path/to/tree.drawio \
    --write

By default this script:
- matches draw.io boxes to research nodes by normalized title text,
- applies alias fallbacks for known draw.io spelling/title variants,
- updates each matched node's x/y in config/research_tree.json,
- rebuilds edges from draw.io connectors where both endpoints match nodes,
- prints a summary of matched/unmatched titles and edge coverage.
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Tuple

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TREE_PATH = APP_DIR / "config" / "research_tree.json"

# Known title variants in the provided draw.io file.
TITLE_ALIASES_TO_NODE_ID: Dict[str, str] = {
    "advance solid core ii": "advanced_solid_core_ii",
    "advance solid core iii": "advanced_solid_core_iii",
    "advance microgravity mining": "advanced_microgravity_mining",
    "early industrial pringing": "early_industrial_printing",
    "vacuum metelllurgy": "vacuum_metallurgy",
    "electron beam metellurgy": "electron_beam_metallurgy",
    "zero g plasma metellurgy": "plasma_metallurgy",
    "cryogenic seperation": "cryogenic_separation",
    "plasma isotope seperation": "plasma_isotope_separation",
    "advanced cryovolitile mining": "advanced_cryovolatile_mining",
    "laser cryovolitile mining": "laser_cryovolatile_mining",
    "plasma microgravuty mining": "plasma_microgravity_mining",
    "laser aerospace printing": "laser_ship_printing",
    "aerospace plasma printing": "industrial_plasma_ship_printing",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_text(value: str) -> str:
    text = value.strip().lower()
    text = text.replace("&", " and ")
    text = _NON_ALNUM_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def extract_title(raw_value: str) -> str:
    decoded = html.unescape(raw_value or "")
    decoded = re.sub(r"<br\s*/?>", "\n", decoded, flags=re.IGNORECASE)
    decoded = re.sub(r"</(div|p|li)>", "\n", decoded, flags=re.IGNORECASE)
    decoded = _TAG_RE.sub("", decoded)
    decoded = decoded.replace("\xa0", " ")
    lines = [ln.strip() for ln in decoded.splitlines() if ln.strip()]
    return lines[0] if lines else ""


def load_tree(tree_path: Path) -> Dict[str, Any]:
    with tree_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_drawio(drawio_path: Path) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    xml_root = ET.parse(drawio_path).getroot()
    vertices: List[Dict[str, Any]] = []
    edges: List[Tuple[str, str]] = []

    for cell in xml_root.findall(".//mxCell"):
        cell_id = str(cell.get("id") or "").strip()
        if not cell_id:
            continue

        if cell.get("vertex") == "1":
            geom = cell.find("mxGeometry")
            if geom is None:
                continue

            try:
                x = float(geom.get("x") or "0")
                y = float(geom.get("y") or "0")
            except ValueError:
                continue

            title = extract_title(str(cell.get("value") or ""))
            if not title:
                continue

            vertices.append({
                "cell_id": cell_id,
                "title": title,
                "x": x,
                "y": y,
            })

        if cell.get("edge") == "1":
            source = str(cell.get("source") or "").strip()
            target = str(cell.get("target") or "").strip()
            if source and target:
                edges.append((source, target))

    return vertices, edges


def remap_positions(
    vertices: List[Dict[str, Any]],
    node_by_id: Dict[str, Dict[str, Any]],
    x_scale: float,
    y_scale: float,
    padding: float,
) -> Tuple[Dict[str, Tuple[int, int]], List[str]]:
    name_to_node_id: Dict[str, str] = {}
    for node_id, node in node_by_id.items():
        name_to_node_id[normalize_text(str(node.get("name") or ""))] = node_id

    draw_xs = [v["x"] for v in vertices]
    draw_ys = [v["y"] for v in vertices]
    min_x = min(draw_xs) if draw_xs else 0.0
    min_y = min(draw_ys) if draw_ys else 0.0

    positions: Dict[str, Tuple[int, int]] = {}
    unmatched_titles: List[str] = []

    for vertex in vertices:
        title_norm = normalize_text(vertex["title"])
        node_id = None

        if title_norm in name_to_node_id:
            node_id = name_to_node_id[title_norm]
        elif title_norm in TITLE_ALIASES_TO_NODE_ID:
            alias_id = TITLE_ALIASES_TO_NODE_ID[title_norm]
            if alias_id in node_by_id:
                node_id = alias_id

        if not node_id:
            unmatched_titles.append(vertex["title"])
            continue

        x = int(round((vertex["x"] - min_x) * x_scale + padding))
        y = int(round((vertex["y"] - min_y) * y_scale + padding))
        positions[node_id] = (x, y)

    return positions, unmatched_titles


def remap_edges(
    drawio_edges: List[Tuple[str, str]],
    vertices: List[Dict[str, Any]],
    node_by_id: Dict[str, Dict[str, Any]],
) -> Tuple[List[List[str]], int]:
    name_to_node_id: Dict[str, str] = {}
    for node_id, node in node_by_id.items():
        name_to_node_id[normalize_text(str(node.get("name") or ""))] = node_id

    cell_to_node_id: Dict[str, str] = {}
    for vertex in vertices:
        title_norm = normalize_text(vertex["title"])
        node_id = name_to_node_id.get(title_norm)
        if not node_id:
            node_id = TITLE_ALIASES_TO_NODE_ID.get(title_norm)
        if node_id and node_id in node_by_id:
            cell_to_node_id[vertex["cell_id"]] = node_id

    remapped: List[List[str]] = []
    seen = set()
    skipped = 0

    for source_cell, target_cell in drawio_edges:
        source_node = cell_to_node_id.get(source_cell)
        target_node = cell_to_node_id.get(target_cell)
        if not source_node or not target_node:
            skipped += 1
            continue
        if source_node == target_node:
            skipped += 1
            continue

        edge = (source_node, target_node)
        if edge in seen:
            continue
        seen.add(edge)
        remapped.append([source_node, target_node])

    return remapped, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync research_tree.json positions from draw.io")
    parser.add_argument("--drawio", required=True, help="Path to draw.io file")
    parser.add_argument("--tree", default=str(DEFAULT_TREE_PATH), help="Path to research_tree.json")
    parser.add_argument("--x-scale", type=float, default=2.0, help="Scale multiplier for x coordinates")
    parser.add_argument("--y-scale", type=float, default=0.9, help="Scale multiplier for y coordinates")
    parser.add_argument("--padding", type=float, default=200.0, help="Canvas padding in output coordinates")
    parser.add_argument(
        "--keep-existing-edges",
        action="store_true",
        help="Do not overwrite edges from draw.io",
    )
    parser.add_argument("--write", action="store_true", help="Write changes back to file")
    args = parser.parse_args()

    drawio_path = Path(args.drawio).expanduser()
    tree_path = Path(args.tree).expanduser()

    if not drawio_path.exists():
        print(f"ERROR: draw.io file not found: {drawio_path}", file=sys.stderr)
        return 1
    if not tree_path.exists():
        print(f"ERROR: tree file not found: {tree_path}", file=sys.stderr)
        return 1

    tree = load_tree(tree_path)
    original_tree = copy.deepcopy(tree)

    vertices, drawio_edges = parse_drawio(drawio_path)
    if not vertices:
        print("ERROR: no draw.io vertex nodes found", file=sys.stderr)
        return 1

    node_by_id = {str(n["id"]): n for n in tree.get("nodes", [])}
    if not node_by_id:
        print("ERROR: research tree has no nodes", file=sys.stderr)
        return 1

    positions, unmatched_titles = remap_positions(
        vertices=vertices,
        node_by_id=node_by_id,
        x_scale=args.x_scale,
        y_scale=args.y_scale,
        padding=args.padding,
    )

    changed_positions = 0
    for node in tree.get("nodes", []):
        node_id = str(node.get("id"))
        if node_id not in positions:
            continue
        new_x, new_y = positions[node_id]
        old_x = int(node.get("x") or 0)
        old_y = int(node.get("y") or 0)
        if old_x != new_x or old_y != new_y:
            changed_positions += 1
        node["x"] = new_x
        node["y"] = new_y

    edge_count = len(tree.get("edges", []))
    skipped_edges = 0
    if not args.keep_existing_edges:
        remapped_edges, skipped_edges = remap_edges(
            drawio_edges=drawio_edges,
            vertices=vertices,
            node_by_id=node_by_id,
        )
        if remapped_edges:
            tree["edges"] = remapped_edges
            edge_count = len(remapped_edges)

    if args.write:
        with tree_path.open("w", encoding="utf-8") as f:
            json.dump(tree, f, indent=2)
            f.write("\n")

    matched_nodes = len(positions)
    total_nodes = len(tree.get("nodes", []))
    unmatched_nodes = total_nodes - matched_nodes

    print("Research tree draw.io sync summary")
    print(f"- matched nodes: {matched_nodes}/{total_nodes}")
    print(f"- unmatched research nodes: {unmatched_nodes}")
    print(f"- changed node positions: {changed_positions}")
    print(f"- output edge count: {edge_count}")
    if not args.keep_existing_edges:
        print(f"- skipped draw.io edges (unmatched endpoints/self): {skipped_edges}")

    if unmatched_titles:
        unique_unmatched_titles = sorted(set(unmatched_titles))
        print("- unmatched draw.io titles:")
        for title in unique_unmatched_titles:
            print(f"  * {title}")

    if not args.write:
        print("\nDry run only. Re-run with --write to save.")

    if tree == original_tree:
        print("No changes detected.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
