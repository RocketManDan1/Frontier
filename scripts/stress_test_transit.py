#!/usr/bin/env python3
"""
Stress-test the transit system on the dev server.

Spawns ships at many locations, attempts transfers between a broad set of
source→destination pairs covering local, SOI, interplanetary, and chain
mission routes.  Reports successes and failures with details.

Usage:
    python3 scripts/stress_test_transit.py [--port 8001] [--cleanup]
"""

import argparse
import json
import sys
import time
import requests

# ── Configuration ──────────────────────────────────────────

DEV_PORT = 8001
BASE = f"http://localhost:{DEV_PORT}"
COOKIE_FILE = "cookies_dev.txt"
TAG = "stresstest"

# Ship parts: Clone of "Orbit Test 1" — proven working design
# SCN-1 thruster + RD-0410 reactor + 4× 100m³ water tanks
# No generator/radiator needed → no waste heat issues
DEFAULT_PARTS = [
    {"item_id": "scn_1_pioneer"},            # thruster: ISP=850s, thrust=250kN
    {"item_id": "rd0410_igrit"},             # reactor: RD-0410
    {"item_id": "water_tank_100_m3"},        # storage: 100m3 water
    {"item_id": "water_tank_100_m3"},        # storage: 100m3 water
    {"item_id": "water_tank_100_m3"},        # storage: 100m3 water
    {"item_id": "water_tank_100_m3"},        # storage: 100m3 water
]

# ── Transfer test pairs ────────────────────────────────────
# (from_location, to_location, description)
TRANSFER_PAIRS = [
    # --- Local Hohmann (same body) ---
    ("LEO", "GEO", "local: LEO→GEO"),
    ("GEO", "LEO", "local: GEO→LEO"),
    ("LEO", "HEO", "local: LEO→HEO"),
    ("HEO", "GEO", "local: HEO→GEO"),
    ("LLO", "HLO", "local: LLO→HLO"),
    ("HLO", "LLO", "local: HLO→LLO"),
    ("LMO", "HMO", "local: LMO→HMO"),
    ("HMO", "MGO", "local: HMO→MGO"),
    ("LMO", "MGO", "local: LMO→MGO"),
    ("JUP_LO", "JUP_HO", "local: JUP_LO→JUP_HO"),
    ("IO_LO", "IO_HO", "local: IO_LO→IO_HO"),
    ("EUROPA_LO", "EUROPA_HO", "local: EUROPA_LO→EUROPA_HO"),
    ("GANYMEDE_LO", "GANYMEDE_HO", "local: GANYMEDE_LO→GANYMEDE_HO"),
    ("CALLISTO_LO", "CALLISTO_HO", "local: CALLISTO_LO→CALLISTO_HO"),
    ("CERES_LO", "CERES_HO", "local: CERES_LO→CERES_HO"),
    ("VESTA_LO", "VESTA_HO", "local: VESTA_LO→VESTA_HO"),
    ("MERC_ORB", "MERC_HEO", "local: MERC_ORB→MERC_HEO"),
    ("VEN_ORB", "VEN_HEO", "local: VEN_ORB→VEN_HEO"),

    # --- SOI transfers (moon ↔ planet orbit) ---
    ("LEO", "LLO", "SOI: LEO→LLO (Earth→Moon)"),
    ("LLO", "LEO", "SOI: LLO→LEO (Moon→Earth)"),
    ("LEO", "HLO", "SOI: LEO→HLO"),
    ("GEO", "LLO", "SOI: GEO→LLO"),
    ("LMO", "PHOBOS_LO", "SOI: LMO→PHOBOS_LO"),
    ("PHOBOS_LO", "DEIMOS_LO", "SOI: PHOBOS_LO→DEIMOS_LO"),
    ("JUP_LO", "IO_LO", "SOI: JUP→IO"),
    ("JUP_LO", "EUROPA_LO", "SOI: JUP→EUROPA"),
    ("IO_LO", "EUROPA_LO", "SOI: IO→EUROPA"),
    ("EUROPA_LO", "GANYMEDE_LO", "SOI: EUROPA→GANYMEDE"),
    ("GANYMEDE_LO", "CALLISTO_LO", "SOI: GANYMEDE→CALLISTO"),

    # --- Lagrange point transfers ---
    ("LEO", "L1", "L-point: LEO→L1"),
    ("LEO", "L2", "L-point: LEO→L2"),
    ("LEO", "L4", "L-point: LEO→L4"),
    ("LEO", "L5", "L-point: LEO→L5"),
    ("L1", "LEO", "L-point: L1→LEO"),
    ("L4", "L5", "L-point: L4→L5"),

    # --- Surface site transfers ---
    ("LLO", "LUNA_SHACKLETON", "surface: LLO→Luna Shackleton"),
    ("LUNA_SHACKLETON", "LLO", "surface: Luna Shackleton→LLO"),
    ("LUNA_PEARY", "LLO", "surface: Luna Peary→LLO"),
    ("LMO", "MARS_OLYMPUS", "surface: LMO→Mars Olympus"),
    ("MARS_OLYMPUS", "LMO", "surface: Mars Olympus→LMO"),

    # --- Interplanetary ---
    ("LEO", "LMO", "interplanetary: Earth→Mars"),
    ("LEO", "VEN_ORB", "interplanetary: Earth→Venus"),
    ("LEO", "MERC_ORB", "interplanetary: Earth→Mercury"),
    ("LEO", "CERES_LO", "interplanetary: Earth→Ceres"),
    ("LEO", "VESTA_LO", "interplanetary: Earth→Vesta"),
    ("LEO", "JUP_LO", "interplanetary: Earth→Jupiter"),
    ("LMO", "LEO", "interplanetary: Mars→Earth"),
    ("VEN_ORB", "LEO", "interplanetary: Venus→Earth"),
    ("VEN_ORB", "LMO", "interplanetary: Venus→Mars"),
    ("MERC_ORB", "VEN_ORB", "interplanetary: Mercury→Venus"),
    ("CERES_LO", "LMO", "interplanetary: Ceres→Mars"),
    ("VESTA_LO", "CERES_LO", "interplanetary: Vesta→Ceres"),

    # --- Chain missions (multi-leg) ---
    ("LLO", "LMO", "chain: Moon orbit→Mars orbit"),
    ("LUNA_SHACKLETON", "LMO", "chain: Moon surface→Mars orbit"),
    ("LLO", "VEN_ORB", "chain: Moon→Venus"),
    ("LLO", "CERES_LO", "chain: Moon→Ceres"),

    # --- Trojan asteroids ---
    ("LEO", "ACHILLES_LO", "interplanetary: Earth→Achilles (trojan)"),
    ("LEO", "HEKTOR_LO", "interplanetary: Earth→Hektor (trojan)"),
    ("JUP_LO", "ACHILLES_LO", "SOI-ish: Jupiter→Achilles"),
]


def parse_cookies(cookie_file: str) -> dict:
    """Parse Netscape cookie file into a cookie dict for requests."""
    cookies = {}
    try:
        with open(cookie_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Handle #HttpOnly_ prefix (curl marks httponly cookies this way)
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
    parser = argparse.ArgumentParser(description="Stress test transit system")
    parser.add_argument("--port", type=int, default=DEV_PORT)
    parser.add_argument("--cleanup", action="store_true", help="Delete stress-test ships after")
    parser.add_argument("--dry-run", action="store_true", help="Only spawn ships, don't transfer")
    args = parser.parse_args()

    base = f"http://localhost:{args.port}"
    cookies = parse_cookies(COOKIE_FILE)
    if not cookies:
        print("ERROR: No cookies found. Log in to the dev server first.", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    session.cookies.update(cookies)

    # Verify auth
    me = session.get(f"{base}/api/auth/me").json()
    if not me.get("ok"):
        print("ERROR: Auth failed. Re-login to dev server.", file=sys.stderr)
        sys.exit(1)
    print(f"Authenticated as: {me['user']['username']} (admin={me['user']['is_admin']})")

    # Collect all unique source locations from our test pairs
    source_locations = sorted(set(pair[0] for pair in TRANSFER_PAIRS))
    print(f"\n{'='*70}")
    print(f"PHASE 1: Spawning {len(source_locations)} ships at unique source locations")
    print(f"{'='*70}\n")

    spawned_ships = {}  # location_id → ship_id
    spawn_ok = 0
    spawn_fail = 0

    for loc_id in source_locations:
        ship_name = f"STRESS[{TAG}] {loc_id}"
        ship_id = f"stress_{TAG}_{loc_id.lower()}"

        payload = {
            "name": ship_name,
            "location_id": loc_id,
            "ship_id": ship_id,
            "shape": "triangle",
            "color": "#ff8800",
            "size_px": 10,
            "parts": DEFAULT_PARTS,
            "notes": [TAG, "stress-test"],
        }

        resp = session.post(f"{base}/api/admin/spawn_ship", json=payload)
        if resp.status_code == 200:
            data = resp.json()
            actual_id = data["ship"]["id"]
            dv = data["ship"].get("delta_v_remaining_m_s", 0)
            spawned_ships[loc_id] = actual_id
            spawn_ok += 1
            print(f"  [OK] Spawned '{actual_id}' at {loc_id} (Δv={dv:.0f} m/s)")
        else:
            spawn_fail += 1
            print(f"  [FAIL] Spawn at {loc_id}: {resp.status_code} {resp.text[:120]}")

    print(f"\nSpawned: {spawn_ok} OK, {spawn_fail} FAIL")

    # Give the server a moment to backfill orbits
    print("\nWaiting 2s for orbit backfill...")
    time.sleep(2)

    # Trigger a state sync so orbits are populated
    session.get(f"{base}/api/state")

    if args.dry_run:
        print("\n[DRY RUN] Skipping transfers.")
        return

    # Refuel all ships before transfers
    print(f"\n{'='*70}")
    print(f"PHASE 1.5: Refueling all ships")
    print(f"{'='*70}\n")
    for loc_id, ship_id in spawned_ships.items():
        resp = session.post(f"{base}/api/admin/ships/{ship_id}/refuel")
        if resp.status_code == 200:
            data = resp.json()
            print(f"  [OK] Refueled '{ship_id}' → fuel={data.get('fuel_kg',0):.0f} kg")
        else:
            print(f"  [WARN] Refuel '{ship_id}': {resp.status_code} {resp.text[:80]}")

    print(f"\n{'='*70}")
    print(f"PHASE 2: Testing {len(TRANSFER_PAIRS)} transfers")
    print(f"{'='*70}\n")

    transfer_ok = 0
    transfer_fail = 0
    transfer_errors = []
    transfer_skipped = 0

    for i, (from_loc, to_loc, desc) in enumerate(TRANSFER_PAIRS):
        ship_id = spawned_ships.get(from_loc)
        if not ship_id:
            print(f"  [{i+1:3d}] [SKIP] {desc} — no ship at {from_loc}")
            transfer_skipped += 1
            continue

        # Check the ship is still at the source (may have been used in a previous transfer)
        state_resp = session.get(f"{base}/api/state")
        state_data = state_resp.json()
        ship_data = next((s for s in state_data.get("ships", []) if s["id"] == ship_id), None)
        if not ship_data:
            print(f"  [{i+1:3d}] [SKIP] {desc} — ship '{ship_id}' not found in state")
            transfer_skipped += 1
            continue

        if ship_data.get("status") != "docked" or ship_data.get("location_id") != from_loc:
            # Ship already in transit or moved — need to spawn a new one
            new_ship_id = f"stress_{TAG}_{from_loc.lower()}_{i}"
            payload = {
                "name": f"STRESS[{TAG}] {from_loc}→{to_loc}",
                "location_id": from_loc,
                "ship_id": new_ship_id,
                "shape": "triangle",
                "color": "#ff8800",
                "size_px": 10,
                "parts": DEFAULT_PARTS,
                "notes": [TAG, "stress-test"],
            }
            spawn_resp = session.post(f"{base}/api/admin/spawn_ship", json=payload)
            if spawn_resp.status_code != 200:
                print(f"  [{i+1:3d}] [SKIP] {desc} — couldn't re-spawn at {from_loc}")
                transfer_skipped += 1
                continue
            ship_id = spawn_resp.json()["ship"]["id"]
            # Refuel the new ship
            session.post(f"{base}/api/admin/ships/{ship_id}/refuel")
            time.sleep(0.1)

        # Attempt the transfer
        transfer_payload = {"to_location_id": to_loc}
        resp = session.post(
            f"{base}/api/ships/{ship_id}/transfer",
            json=transfer_payload,
        )

        if resp.status_code == 200:
            data = resp.json()
            dv = data.get("dv_m_s", 0)
            tof = data.get("tof_s", 0)
            ttype = data.get("transfer_type", data.get("route_mode", "?"))
            has_orbit = "orbit" in data
            has_preds = "orbit_predictions" in data and len(data.get("orbit_predictions", [])) > 0
            burns = len(data.get("maneuvers", []))
            tof_days = tof / 86400
            flags = []
            if has_orbit: flags.append("orbit")
            if has_preds: flags.append(f"preds={len(data.get('orbit_predictions',[]))}")
            if burns: flags.append(f"burns={burns}")
            print(f"  [{i+1:3d}] [OK]   {desc:45s}  Δv={dv:>8.0f} m/s  TOF={tof_days:>7.1f}d  {ttype:20s} [{','.join(flags)}]")
            transfer_ok += 1
        else:
            err_text = resp.text[:200]
            try:
                err_detail = resp.json().get("detail", err_text)
            except Exception:
                err_detail = err_text
            print(f"  [{i+1:3d}] [FAIL] {desc:45s}  {resp.status_code}: {err_detail}")
            transfer_fail += 1
            transfer_errors.append({
                "pair": f"{from_loc} → {to_loc}",
                "desc": desc,
                "status": resp.status_code,
                "error": err_detail,
                "ship_id": ship_id,
            })

    # ── Summary ──
    print(f"\n{'='*70}")
    print(f"RESULTS SUMMARY")
    print(f"{'='*70}")
    print(f"  Transfers OK:      {transfer_ok}")
    print(f"  Transfers FAILED:  {transfer_fail}")
    print(f"  Transfers SKIPPED: {transfer_skipped}")
    print(f"  Total pairs:       {len(TRANSFER_PAIRS)}")

    if transfer_errors:
        print(f"\n{'='*70}")
        print(f"FAILURE DETAILS")
        print(f"{'='*70}")
        for err in transfer_errors:
            print(f"\n  {err['pair']} ({err['desc']})")
            print(f"    Ship:   {err['ship_id']}")
            print(f"    Status: {err['status']}")
            print(f"    Error:  {err['error']}")

    # Cleanup
    if args.cleanup:
        print(f"\n{'='*70}")
        print(f"CLEANUP: Deleting stress test ships")
        print(f"{'='*70}")
        state_data = session.get(f"{base}/api/state").json()
        deleted = 0
        for s in state_data.get("ships", []):
            if s["id"].startswith(f"stress_{TAG}_"):
                resp = session.delete(f"{base}/api/admin/ships/{s['id']}")
                if resp.status_code == 200:
                    deleted += 1
        print(f"  Deleted {deleted} ships")

    print(f"\nDone.")
    return 1 if transfer_fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
