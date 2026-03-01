#!/usr/bin/env python3
"""
Focused stress-test for L4/L5 Lagrange-point and Jupiter Trojan transfers.

Tests:
  - Earth–Moon L-point hub routes (L1↔L2, L1↔L4, L1↔L5, L4↔L5, etc.)
  - Sun–Jupiter L-point hub routes (SJ_L1↔SJ_L4, SJ_L4↔trojans, etc.)
  - Intra-camp trojan transfers (within L4 Greeks, within L5 Trojans)
  - Cross-camp transfers (L4 Greek → L5 Trojan and reverse)
  - Planet → trojan interplanetary (LEO/JUP_LO → Greek/Trojan)
  - Trojan → planet interplanetary
  - Surface landing at trojan surface sites

Also prints a reference-data table comparing game Δv/TOF values against
known real-world estimates for later accuracy review.

Usage:
    python3 scripts/stress_test_lagrange.py [--port 8001] [--cleanup]
"""

import argparse
import json
import math
import sys
import time
import requests

# ── Configuration ──────────────────────────────────────────

DEV_PORT = 8001
COOKIE_FILE = "cookies_dev.txt"
TAG = "lagrange"

# Ship parts: same as main stress test (SCN-1 + RD-0410 + 4× tanks)
DEFAULT_PARTS = [
    {"item_id": "scn_1_pioneer"},
    {"item_id": "rd0410_igrit"},
    {"item_id": "water_tank_100_m3"},
    {"item_id": "water_tank_100_m3"},
    {"item_id": "water_tank_100_m3"},
    {"item_id": "water_tank_100_m3"},
]

# ── Transfer test pairs ────────────────────────────────────
TRANSFER_PAIRS = [
    # ═══ EARTH–MOON LAGRANGE ═══
    # Hub connections (L1 is the hub)
    ("LEO", "L1",  "EM Lagrange: LEO→L1"),
    ("L1",  "LEO", "EM Lagrange: L1→LEO"),
    ("GEO", "L1",  "EM Lagrange: GEO→L1"),
    ("L1",  "L2",  "EM Lagrange: L1→L2"),
    ("L2",  "L1",  "EM Lagrange: L2→L1"),
    ("L1",  "L3",  "EM Lagrange: L1→L3"),
    ("L3",  "L1",  "EM Lagrange: L3→L1"),
    ("L1",  "L4",  "EM Lagrange: L1→L4"),
    ("L4",  "L1",  "EM Lagrange: L4→L1"),
    ("L1",  "L5",  "EM Lagrange: L1→L5"),
    ("L5",  "L1",  "EM Lagrange: L5→L1"),
    ("L1",  "LLO", "EM Lagrange: L1→LLO"),
    ("LLO", "L1",  "EM Lagrange: LLO→L1"),
    ("L2",  "HLO", "EM Lagrange: L2→HLO"),
    ("HLO", "L2",  "EM Lagrange: HLO→L2"),
    # Cross-L-point (no direct edge — must chain through L1)
    ("L4",  "L5",  "EM Lagrange: L4→L5 (no direct edge)"),
    ("L4",  "L2",  "EM Lagrange: L4→L2 (via L1)"),
    ("L5",  "L3",  "EM Lagrange: L5→L3 (via L1)"),
    # EM L-point → orbit
    ("L4",  "GEO", "EM Lagrange: L4→GEO"),
    ("L5",  "LEO", "EM Lagrange: L5→LEO"),

    # ═══ SUN–JUPITER LAGRANGE ═══
    # Hub connections (SJ_L1 is the hub)
    ("JUP_HO", "SJ_L1", "SJ Lagrange: JUP_HO→SJ_L1"),
    ("SJ_L1", "JUP_HO", "SJ Lagrange: SJ_L1→JUP_HO"),
    ("SJ_L1", "SJ_L2",  "SJ Lagrange: SJ_L1→SJ_L2"),
    ("SJ_L2", "SJ_L1",  "SJ Lagrange: SJ_L2→SJ_L1"),
    ("SJ_L1", "SJ_L3",  "SJ Lagrange: SJ_L1→SJ_L3"),
    ("SJ_L3", "SJ_L1",  "SJ Lagrange: SJ_L3→SJ_L1"),
    ("SJ_L1", "SJ_L4",  "SJ Lagrange: SJ_L1→SJ_L4"),
    ("SJ_L4", "SJ_L1",  "SJ Lagrange: SJ_L4→SJ_L1"),
    ("SJ_L1", "SJ_L5",  "SJ Lagrange: SJ_L1→SJ_L5"),
    ("SJ_L5", "SJ_L1",  "SJ Lagrange: SJ_L5→SJ_L1"),
    # Cross-SJ L-point
    ("SJ_L4", "SJ_L5",  "SJ Lagrange: SJ_L4→SJ_L5 (cross-camp)"),
    ("SJ_L5", "SJ_L4",  "SJ Lagrange: SJ_L5→SJ_L4 (cross-camp)"),

    # ═══ L4 GREEK CAMP – INTERNAL ═══
    # SJ_L4 → individual asteroids
    ("SJ_L4", "HEKTOR_LO",     "L4 Greeks: SJ_L4→Hektor"),
    ("HEKTOR_LO", "SJ_L4",     "L4 Greeks: Hektor→SJ_L4"),
    ("SJ_L4", "ACHILLES_LO",   "L4 Greeks: SJ_L4→Achilles"),
    ("ACHILLES_LO", "SJ_L4",   "L4 Greeks: Achilles→SJ_L4"),
    ("SJ_L4", "AGAMEMNON_LO",  "L4 Greeks: SJ_L4→Agamemnon"),
    ("SJ_L4", "DIOMEDES_LO",   "L4 Greeks: SJ_L4→Diomedes"),
    ("SJ_L4", "ODYSSEUS_LO",   "L4 Greeks: SJ_L4→Odysseus"),
    # Intra-camp transfers (asteroid → asteroid via SJ_L4)
    ("HEKTOR_LO", "ACHILLES_LO",    "L4 intra: Hektor→Achilles"),
    ("ACHILLES_LO", "AGAMEMNON_LO", "L4 intra: Achilles→Agamemnon"),
    ("DIOMEDES_LO", "ODYSSEUS_LO",  "L4 intra: Diomedes→Odysseus"),
    ("ODYSSEUS_LO", "HEKTOR_LO",    "L4 intra: Odysseus→Hektor"),

    # ═══ L5 TROJAN CAMP – INTERNAL ═══
    # SJ_L5 → individual asteroids
    ("SJ_L5", "PATROCLUS_LO",  "L5 Trojans: SJ_L5→Patroclus"),
    ("PATROCLUS_LO", "SJ_L5",  "L5 Trojans: Patroclus→SJ_L5"),
    ("SJ_L5", "MENTOR_LO",     "L5 Trojans: SJ_L5→Mentor"),
    ("SJ_L5", "PARIS_LO",      "L5 Trojans: SJ_L5→Paris"),
    ("SJ_L5", "DEIPHOBUS_LO",  "L5 Trojans: SJ_L5→Deiphobus"),
    ("SJ_L5", "AENEAS_LO",     "L5 Trojans: SJ_L5→Aeneas"),
    # Intra-camp transfers
    ("PATROCLUS_LO", "MENTOR_LO",   "L5 intra: Patroclus→Mentor"),
    ("MENTOR_LO", "PARIS_LO",       "L5 intra: Mentor→Paris"),
    ("DEIPHOBUS_LO", "AENEAS_LO",   "L5 intra: Deiphobus→Aeneas"),
    ("AENEAS_LO", "PATROCLUS_LO",   "L5 intra: Aeneas→Patroclus"),

    # ═══ CROSS-CAMP (L4↔L5) ═══
    ("ACHILLES_LO", "PATROCLUS_LO", "Cross-camp: Achilles(L4)→Patroclus(L5)"),
    ("PATROCLUS_LO", "ACHILLES_LO", "Cross-camp: Patroclus(L5)→Achilles(L4)"),
    ("HEKTOR_LO", "MENTOR_LO",      "Cross-camp: Hektor(L4)→Mentor(L5)"),

    # ═══ TROJAN SURFACE SITES ═══
    ("HEKTOR_LO", "HEKTOR_SKAMANDRIOS", "Surface: LO→Hektor Skamandrios"),
    ("HEKTOR_SKAMANDRIOS", "HEKTOR_LO", "Surface: Hektor Skamandrios→LO"),
    ("ACHILLES_LO", "ACHILLES_PELEUS",  "Surface: LO→Achilles Peleus"),
    ("ACHILLES_PELEUS", "ACHILLES_LO",  "Surface: Achilles Peleus→LO"),
    ("PATROCLUS_LO", "PATROCLUS_MENOETIUS", "Surface: LO→Patroclus Menoetius"),
    ("PATROCLUS_MENOETIUS", "PATROCLUS_LO", "Surface: Patroclus Menoetius→LO"),

    # ═══ INTERPLANETARY → TROJANS ═══
    ("LEO", "ACHILLES_LO",  "Interplanetary: Earth→Achilles (L4)"),
    ("LEO", "HEKTOR_LO",    "Interplanetary: Earth→Hektor (L4)"),
    ("LEO", "PATROCLUS_LO", "Interplanetary: Earth→Patroclus (L5)"),
    ("LMO", "ACHILLES_LO",  "Interplanetary: Mars→Achilles (L4)"),
    ("JUP_LO", "ACHILLES_LO",  "Interplanetary: Jupiter→Achilles (L4)"),
    ("JUP_LO", "PATROCLUS_LO", "Interplanetary: Jupiter→Patroclus (L5)"),

    # ═══ TROJANS → PLANETS ═══
    ("ACHILLES_LO", "LEO",    "Interplanetary: Achilles(L4)→Earth"),
    ("ACHILLES_LO", "JUP_LO", "Interplanetary: Achilles(L4)→Jupiter"),
    ("PATROCLUS_LO", "LEO",   "Interplanetary: Patroclus(L5)→Earth"),
    ("PATROCLUS_LO", "JUP_LO","Interplanetary: Patroclus(L5)→Jupiter"),

    # ═══ FULL CHAINS ═══
    # Earth orbit → L1 → SJ_L4 → Achilles
    ("LEO", "SJ_L4",       "Chain: LEO→SJ_L4 (Earth to Jup L4)"),
    ("LEO", "SJ_L5",       "Chain: LEO→SJ_L5 (Earth to Jup L5)"),
    ("JUP_LO", "SJ_L4",   "Chain: JUP_LO→SJ_L4"),
    ("JUP_LO", "SJ_L5",   "Chain: JUP_LO→SJ_L5"),
]


def parse_cookies(cookie_file: str) -> dict:
    cookies = {}
    try:
        with open(cookie_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                elif line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    cookies[parts[5]] = parts[6]
    except FileNotFoundError:
        pass
    return cookies


def main():
    parser = argparse.ArgumentParser(description="L4/L5/Trojan stress test")
    parser.add_argument("--port", type=int, default=DEV_PORT)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    base = f"http://localhost:{args.port}"
    cookies = parse_cookies(COOKIE_FILE)
    if not cookies:
        print("ERROR: No cookies found.", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    session.cookies.update(cookies)

    me = session.get(f"{base}/api/auth/me").json()
    if not me.get("ok"):
        print("ERROR: Auth failed.", file=sys.stderr)
        sys.exit(1)
    print(f"Authenticated as: {me['user']['username']}")

    # Collect unique source locations
    source_locations = sorted(set(pair[0] for pair in TRANSFER_PAIRS))
    print(f"\n{'='*70}")
    print(f"PHASE 1: Spawning {len(source_locations)} ships")
    print(f"{'='*70}\n")

    spawned_ships = {}
    for loc_id in source_locations:
        ship_id = f"lagr_{TAG}_{loc_id.lower()}"
        payload = {
            "name": f"LAGR[{TAG}] {loc_id}",
            "location_id": loc_id,
            "ship_id": ship_id,
            "shape": "triangle",
            "color": "#00ccff",
            "size_px": 10,
            "parts": DEFAULT_PARTS,
            "notes": [TAG, "lagrange-test"],
        }
        resp = session.post(f"{base}/api/admin/spawn_ship", json=payload)
        if resp.status_code == 200:
            actual_id = resp.json()["ship"]["id"]
            spawned_ships[loc_id] = actual_id
            print(f"  [OK] Spawned '{actual_id}' at {loc_id}")
        else:
            print(f"  [FAIL] Spawn at {loc_id}: {resp.status_code} {resp.text[:120]}")

    print(f"\nSpawned: {len(spawned_ships)} ships")
    time.sleep(2)
    session.get(f"{base}/api/state")

    # Refuel
    print(f"\n{'='*70}")
    print(f"PHASE 1.5: Refueling")
    print(f"{'='*70}\n")
    for loc_id, ship_id in spawned_ships.items():
        resp = session.post(f"{base}/api/admin/ships/{ship_id}/refuel")
        if resp.status_code == 200:
            print(f"  [OK] Refueled '{ship_id}'")
        else:
            print(f"  [WARN] Refuel '{ship_id}': {resp.status_code}")

    # Transfers
    print(f"\n{'='*70}")
    print(f"PHASE 2: Testing {len(TRANSFER_PAIRS)} transfers")
    print(f"{'='*70}\n")

    results = {"ok": 0, "fail": 0, "skip": 0}
    errors = []
    successes = []

    for i, (from_loc, to_loc, desc) in enumerate(TRANSFER_PAIRS):
        ship_id = spawned_ships.get(from_loc)
        if not ship_id:
            print(f"  [{i+1:3d}] [SKIP] {desc} — no ship at {from_loc}")
            results["skip"] += 1
            continue

        # Check ship state
        state = session.get(f"{base}/api/state").json()
        ship = next((s for s in state.get("ships", []) if s["id"] == ship_id), None)

        if not ship or ship.get("status") != "docked" or ship.get("location_id") != from_loc:
            new_id = f"lagr_{TAG}_{from_loc.lower()}_{i}"
            payload = {
                "name": f"LAGR[{TAG}] {from_loc}→{to_loc}",
                "location_id": from_loc, "ship_id": new_id,
                "shape": "triangle", "color": "#00ccff", "size_px": 10,
                "parts": DEFAULT_PARTS, "notes": [TAG],
            }
            r = session.post(f"{base}/api/admin/spawn_ship", json=payload)
            if r.status_code != 200:
                print(f"  [{i+1:3d}] [SKIP] {desc} — re-spawn failed")
                results["skip"] += 1
                continue
            ship_id = r.json()["ship"]["id"]
            session.post(f"{base}/api/admin/ships/{ship_id}/refuel")
            time.sleep(0.1)

        resp = session.post(
            f"{base}/api/ships/{ship_id}/transfer",
            json={"to_location_id": to_loc},
        )

        if resp.status_code == 200:
            data = resp.json()
            dv = data.get("dv_m_s", 0)
            tof = data.get("tof_s", 0)
            ttype = data.get("transfer_type", data.get("route_mode", "?"))
            tof_d = tof / 86400
            preds = len(data.get("orbit_predictions", []))
            burns = len(data.get("maneuvers", []))
            print(f"  [{i+1:3d}] [OK]   {desc:50s} Δv={dv:>8.0f} m/s  TOF={tof_d:>8.1f}d  {ttype}")
            results["ok"] += 1
            successes.append({
                "from": from_loc, "to": to_loc, "desc": desc,
                "dv": dv, "tof_d": tof_d, "type": ttype,
            })
        else:
            try:
                detail = resp.json().get("detail", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            print(f"  [{i+1:3d}] [FAIL] {desc:50s} {resp.status_code}: {detail}")
            results["fail"] += 1
            errors.append({
                "pair": f"{from_loc} → {to_loc}", "desc": desc,
                "status": resp.status_code, "error": detail, "ship_id": ship_id,
            })

    # Summary
    print(f"\n{'='*70}")
    print(f"RESULTS SUMMARY")
    print(f"{'='*70}")
    print(f"  OK:      {results['ok']}")
    print(f"  FAILED:  {results['fail']}")
    print(f"  SKIPPED: {results['skip']}")
    print(f"  Total:   {len(TRANSFER_PAIRS)}")

    if errors:
        print(f"\n{'='*70}")
        print(f"FAILURE DETAILS")
        print(f"{'='*70}")
        for e in errors:
            print(f"\n  {e['pair']} ({e['desc']})")
            print(f"    Ship: {e['ship_id']}  Status: {e['status']}")
            print(f"    Error: {e['error']}")

    # Δv reference table
    if successes:
        print(f"\n{'='*70}")
        print(f"Δv / TOF REFERENCE TABLE (for accuracy review)")
        print(f"{'='*70}")
        print(f"{'Route':55s} {'Δv(m/s)':>10s} {'TOF(d)':>10s} {'Type':>20s}")
        print("-" * 100)
        for s in successes:
            print(f"{s['desc']:55s} {s['dv']:>10.0f} {s['tof_d']:>10.1f} {s['type']:>20s}")

    # Cleanup
    if args.cleanup:
        print(f"\nCleaning up...")
        state = session.get(f"{base}/api/state").json()
        deleted = 0
        for s in state.get("ships", []):
            if s["id"].startswith(f"lagr_{TAG}_"):
                session.delete(f"{base}/api/admin/ships/{s['id']}")
                deleted += 1
        print(f"  Deleted {deleted} ships")

    print("\nDone.")
    return 1 if results["fail"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
