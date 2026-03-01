#!/usr/bin/env python3
"""
Δv / TOF Accuracy Audit
========================
Spawns test ships at key locations, queries transfer_quote for every
important route, and compares game values against real-world reference data.

Usage:
    python3 scripts/audit_deltav.py [--port 8001] [--cleanup]
"""

import argparse, json, math, requests, sys, time

# ── Real-world reference values ──────────────────────────────────────
# Sources: NASA trajectory databases, Wertz "Space Mission Engineering",
#          JPL mission design references, ESA transfer databases
# Format: (from, to, ref_dv_low, ref_dv_high, ref_tof_low_days, ref_tof_high_days, notes)
REFERENCE = [
    # ── Earth local system ──
    ("LEO", "GEO", 3800, 4300, 0.2, 0.3,
     "LEO→GTO→GEO Hohmann: ~3.9 km/s (two burns: 2.44 + 1.47). 5-6 hours."),
    ("GEO", "LEO", 1400, 1800, 0.2, 0.3,
     "GEO→LEO: ~1.5 km/s deorbit. Asymmetric due to aerobraking."),
    ("LEO", "L1", 3100, 3900, 3.0, 5.0,
     "LEO→E-M L1 via WSB/low-energy: ~3.1-3.8 km/s, 3-5 days."),
    ("L1", "LEO", 700, 900, 3.0, 5.0,
     "E-M L1→LEO: ~0.7-0.9 km/s (falls into Earth's gravity well)."),
    ("L1", "LLO", 800, 900, 3.0, 5.0,
     "E-M L1→LLO: ~0.8 km/s."),
    ("LLO", "L1", 800, 900, 3.0, 5.0,
     "LLO→E-M L1: ~0.8 km/s."),

    # ── E-M Lagrange inter-point ──
    ("L1", "L2", 100, 250, 0.5, 2.0,
     "E-M L1↔L2: very close, ~150 m/s station-keeping maneuver level."),
    ("L1", "L3", 300, 600, 10.0, 30.0,
     "E-M L1→L3: ~400-500 m/s. L3 is far side of Earth-Moon, ~10+ days transfer."),
    ("L1", "L4", 200, 400, 5.0, 15.0,
     "E-M L1→L4: ~200-350 m/s. L4 is at Moon's orbit, 60° ahead."),
    ("L1", "L5", 200, 400, 5.0, 15.0,
     "E-M L1→L5: ~200-350 m/s. L5 is at Moon's orbit, 60° behind."),

    # ── Moon surface ──
    ("LLO", "LUNA_SHACKLETON", 1600, 1900, 0.02, 0.08,
     "LLO→lunar surface: ~1.7-1.9 km/s (deorbit + powered landing). Minutes."),

    # ── Mars ──
    ("LEO", "LMO", 3500, 6000, 180, 300,
     "LEO→Mars (Hohmann): 3.6 km/s departure, ~6 months. With capture: ~5.7 km/s total."),
    ("LMO", "LEO", 3500, 6000, 180, 300,
     "Mars→LEO: similar to outbound."),

    # ── Mars surface ──
    ("LMO", "MARS_OLYMPUS", 3400, 4200, 0.02, 0.1,
     "LMO→Mars surface: ~3.4-4.0 km/s (Mars escape velocity ~5.0, orbit velocity ~3.4 km/s). Landing Δv depends on aerobraking."),

    # ── Mars local ──
    ("LMO", "DEIMOS_LO", 900, 1400, 0.1, 0.3,
     "LMO→Deimos: ~0.9-1.3 km/s (Deimos is at 23,460 km, LMO at ~300 km)."),
    ("LMO", "PHOBOS_LO", 400, 800, 0.05, 0.2,
     "LMO→Phobos: ~0.5-0.7 km/s (Phobos is at 9,376 km orbit)."),
    ("LMO", "MGO", 1400, 2000, 0.1, 0.3,
     "LMO→Mars GEO (~17,000 km): ~1.5-1.8 km/s Hohmann."),

    # ── Venus ──
    ("LEO", "VEN_ORB", 3400, 5000, 120, 200,
     "LEO→Venus orbit (Hohmann): ~3.5 km/s departure. 4-6 months."),

    # ── Mercury ──
    ("LEO", "MERC_ORB", 7500, 13000, 100, 200,
     "LEO→Mercury: extremely expensive direct, ~8-13 km/s. Usually gravity assists."),

    # ── Jupiter ──
    ("LEO", "JUP_LO", 6000, 14000, 500, 1000,
     "LEO→Jupiter orbit: ~6.3 km/s departure (Hohmann), 2-3 year transit. Capture: ~1-8 km/s."),

    # ── Jupiter local system ──
    ("JUP_LO", "JUP_HO", 4500, 7000, 100, 400,
     "JUP_LO (at ~1.1M km) → JUP_HO (~50M km, near SOI edge): huge Hohmann. ~5-6 km/s."),
    ("JUP_LO", "IO_LO", 2000, 5500, 0.5, 3.0,
     "JUP_LO→Io low orbit: Depends on JUP_LO altitude. ~2-5 km/s."),
    ("JUP_LO", "EUROPA_LO", 1000, 3500, 1.0, 5.0,
     "JUP_LO→Europa: ~1.5-3 km/s if JUP_LO is between Io and Europa."),
    ("JUP_LO", "CALLISTO_LO", 2000, 5000, 2.0, 10.0,
     "JUP_LO→Callisto: ~2-4 km/s. Large orbit change."),
    ("IO_LO", "CALLISTO_LO", 1500, 3000, 2.0, 10.0,
     "Io→Callisto: ~1.5-2.5 km/s Hohmann between 422K and 1.883M km orbits."),
    ("IO_LO", "GANYMEDE_LO", 800, 2000, 1.0, 5.0,
     "Io→Ganymede: ~1-1.8 km/s. 422K→1.07M km orbit."),

    # ── Jupiter Lagrange system ──
    ("JUP_HO", "SJ_L1", 100, 500, 100, 2000,
     "JUP high orbit → SJ_L1: L1 is near Jupiter SOI edge. Low Δv but long drift."),
    ("SJ_L1", "SJ_L4", 100, 500, 300, 5000,
     "SJ L1→L4: Phasing from L1 to 60° ahead. Low Δv but YEARS of drift at Jupiter's orbit."),
    ("SJ_L1", "SJ_L5", 100, 500, 300, 5000,
     "SJ L1→L5: Same as L4 but 60° behind."),
    ("SJ_L4", "SJ_L5", 300, 1500, 3000, 20000,
     "L4↔L5 cross-camp: 120° phase shift in Jupiter's orbit. 1-5 km/s, many years to decades."),

    # ── SJ Lagrange → trojan body ──
    ("SJ_L4", "HEKTOR_LO", 50, 300, 30, 365,
     "SJ_L4→Hektor: Hektor librates within the L4 cloud. Low Δv, weeks to months."),
    ("SJ_L5", "PATROCLUS_LO", 50, 300, 30, 365,
     "SJ_L5→Patroclus: Similar to Hektor from L4."),

    # ── Intra-camp (same trojan cloud) ──
    ("HEKTOR_LO", "ACHILLES_LO", 100, 800, 60, 730,
     "Hektor→Achilles: Both librate in L4 cloud, separated by ~5-20° true anomaly. Low Δv, months to years."),

    # ── Trojan surface sites ──
    ("HEKTOR_LO", "HEKTOR_SKAMANDRIOS", 5, 20, 0.001, 0.02,
     "Hektor LO→surface: ~5-15 m/s. Hektor escape vel: ~30 m/s."),
    ("ACHILLES_LO", "ACHILLES_PELEUS", 3, 15, 0.001, 0.02,
     "Achilles LO→surface: ~3-10 m/s."),

    # ── Asteroid belt ──
    ("LEO", "CERES_LO", 4000, 8000, 200, 500,
     "LEO→Ceres: ~5-7 km/s departure, 1-2 years."),
    ("LEO", "VESTA_LO", 4000, 7500, 200, 500,
     "LEO→Vesta: ~4.5-6 km/s departure, ~1.5 years."),

    # ── Interplanetary reference pairs ──
    ("LEO", "JUP_LO", 6000, 14000, 500, 1000,
     "~6.3 km/s departure + ~1-8 km/s Jupiter capture."),
    ("JUP_LO", "LEO", 6000, 14000, 500, 1000,
     "Return from Jupiter."),

    # ── Deep-Sun references ──
    ("LEO", "SUN", 24000, 32000, 50, 200,
     "LEO→Sun (direct): ~24-29 km/s to cancel Earth's orbital velocity. Extremely expensive."),
    ("LEO", "MERC_ORB", 7500, 13000, 100, 200,
     "LEO→Mercury orbit: ~8-13 km/s direct."),
]

TAG = "deltav_audit"

DEFAULT_PARTS = [
    "SCN-1", "RD-0410",
    "water-tank-medium", "water-tank-medium",
    "water-tank-medium", "water-tank-medium",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    base = f"http://localhost:{args.port}"
    session = requests.Session()

    # ── Auth ──
    r = session.post(f"{base}/api/auth/login", json={"username": "admin", "password": "admin"})
    if r.status_code != 200:
        print(f"Login failed: {r.status_code}")
        sys.exit(1)
    print("Authenticated as: admin\n")

    # ── Phase 0: Collect all unique locations from references ──
    all_locs = set()
    for ref in REFERENCE:
        all_locs.add(ref[0])
        all_locs.add(ref[1])
    all_locs = sorted(all_locs)

    # ── Phase 1: Dump all transfer edges ──
    # We'll also pull edge data directly for the audit
    # Use the transfer_quote endpoint (no ship needed) for game Δv/TOF

    print("=" * 100)
    print("Δv / TOF ACCURACY AUDIT")
    print("=" * 100)
    print()

    results = []
    for ref in REFERENCE:
        from_id, to_id = ref[0], ref[1]
        ref_dv_lo, ref_dv_hi = ref[2], ref[3]
        ref_tof_lo, ref_tof_hi = ref[4], ref[5]
        notes = ref[6]

        # Query transfer_quote
        r = session.get(f"{base}/api/transfer_quote", params={
            "from_id": from_id,
            "to_id": to_id,
        })

        if r.status_code == 200:
            data = r.json()
            game_dv = data.get("dv_m_s", 0)
            game_tof_d = data.get("tof_s", 0) / 86400
            route_mode = data.get("route_mode", "?")
        elif r.status_code == 404:
            game_dv = None
            game_tof_d = None
            route_mode = "NO_ROUTE"
        else:
            game_dv = None
            game_tof_d = None
            route_mode = f"ERR_{r.status_code}"

        # Assess accuracy
        if game_dv is not None:
            if game_dv < ref_dv_lo:
                dv_verdict = "LOW"
                dv_pct = (ref_dv_lo - game_dv) / ref_dv_lo * 100
            elif game_dv > ref_dv_hi:
                dv_verdict = "HIGH"
                dv_pct = (game_dv - ref_dv_hi) / ref_dv_hi * 100
            else:
                dv_verdict = "OK"
                dv_pct = 0
        else:
            dv_verdict = "N/A"
            dv_pct = 0

        if game_tof_d is not None:
            if game_tof_d < ref_tof_lo * 0.5:
                tof_verdict = "TOO_FAST"
                tof_ratio = ref_tof_lo / max(game_tof_d, 0.001)
            elif game_tof_d > ref_tof_hi * 2.0:
                tof_verdict = "TOO_SLOW"
                tof_ratio = game_tof_d / ref_tof_hi
            elif game_tof_d < ref_tof_lo:
                tof_verdict = "FAST"
                tof_ratio = ref_tof_lo / max(game_tof_d, 0.001)
            elif game_tof_d > ref_tof_hi:
                tof_verdict = "SLOW"
                tof_ratio = game_tof_d / ref_tof_hi
            else:
                tof_verdict = "OK"
                tof_ratio = 1.0
        else:
            tof_verdict = "N/A"
            tof_ratio = 0

        results.append({
            "from": from_id, "to": to_id,
            "game_dv": game_dv, "game_tof_d": game_tof_d,
            "ref_dv_lo": ref_dv_lo, "ref_dv_hi": ref_dv_hi,
            "ref_tof_lo": ref_tof_lo, "ref_tof_hi": ref_tof_hi,
            "dv_verdict": dv_verdict, "dv_pct": dv_pct,
            "tof_verdict": tof_verdict, "tof_ratio": tof_ratio,
            "route_mode": route_mode, "notes": notes,
        })

    # ── Print results table ──
    # Group by category
    categories = [
        ("EARTH LOCAL SYSTEM", ["LEO", "GEO", "L1", "L2", "L3", "L4", "L5", "LLO", "HLO"]),
        ("MOON SURFACE", ["LUNA_"]),
        ("MARS SYSTEM", ["LMO", "MGO", "MARS_", "DEIMOS", "PHOBOS"]),
        ("VENUS / MERCURY", ["VEN_", "MERC_"]),
        ("JUPITER LOCAL", ["JUP_", "IO_", "EUROPA_", "GANYMEDE_", "CALLISTO_"]),
        ("SJ LAGRANGE SYSTEM", ["SJ_L"]),
        ("TROJAN CAMPS", ["HEKTOR", "ACHILLES", "PATROCLUS", "MENTOR", "AENEAS", "AGAMEMNON", "DIOMEDES", "ODYSSEUS", "DEIPHOBUS", "PARIS"]),
        ("ASTEROID BELT", ["CERES", "VESTA", "PALLAS", "HYGIEA"]),
        ("DEEP INTERPLANETARY", ["SUN"]),
    ]

    def categorize(r):
        f, t = r["from"], r["to"]
        for cat_name, prefixes in categories:
            for p in prefixes:
                if f.startswith(p) or t.startswith(p):
                    return cat_name
        return "OTHER"

    printed_cats = set()
    for cat_name, _ in categories:
        cat_results = [r for r in results if categorize(r) == cat_name]
        if not cat_results:
            continue

        if cat_name not in printed_cats:
            print(f"\n{'─' * 100}")
            print(f"  {cat_name}")
            print(f"{'─' * 100}")
            print(f"  {'Route':<35s} {'Game Δv':>10s} {'Ref Range':>16s} {'Δv':>6s} {'Game TOF':>12s} {'Ref TOF':>18s} {'TOF':>10s} {'Mode':<20s}")
            print(f"  {'─'*35} {'─'*10} {'─'*16} {'─'*6} {'─'*12} {'─'*18} {'─'*10} {'─'*20}")
            printed_cats.add(cat_name)

        for r in cat_results:
            route = f"{r['from']}→{r['to']}"
            if r["game_dv"] is not None:
                game_dv_s = f"{r['game_dv']:,.0f}"
            else:
                game_dv_s = "---"
            ref_range = f"{r['ref_dv_lo']:,.0f}-{r['ref_dv_hi']:,.0f}"

            # Δv verdict with color symbol
            if r["dv_verdict"] == "OK":
                dv_sym = "  ✓"
            elif r["dv_verdict"] in ("LOW","HIGH"):
                dv_sym = f" {r['dv_pct']:+.0f}%"
            else:
                dv_sym = " ---"

            if r["game_tof_d"] is not None:
                if r["game_tof_d"] < 1:
                    game_tof_s = f"{r['game_tof_d']*24:.1f}h"
                else:
                    game_tof_s = f"{r['game_tof_d']:.1f}d"
            else:
                game_tof_s = "---"

            if r["ref_tof_lo"] < 1:
                ref_tof_s = f"{r['ref_tof_lo']*24:.1f}-{r['ref_tof_hi']*24:.1f}h"
            else:
                ref_tof_s = f"{r['ref_tof_lo']:.0f}-{r['ref_tof_hi']:.0f}d"

            if r["tof_verdict"] == "OK":
                tof_sym = "  ✓"
            elif r["tof_verdict"] in ("FAST", "SLOW"):
                tof_sym = f" ~{r['tof_ratio']:.1f}×"
            elif r["tof_verdict"] in ("TOO_FAST", "TOO_SLOW"):
                tof_sym = f" !!{r['tof_ratio']:.0f}×"
            else:
                tof_sym = " ---"

            print(f"  {route:<35s} {game_dv_s:>10s} {ref_range:>16s} {dv_sym:>6s} {game_tof_s:>12s} {ref_tof_s:>18s} {tof_sym:>10s} {r['route_mode']:<20s}")

    # ── Summary ──
    print(f"\n{'=' * 100}")
    print("SUMMARY")
    print(f"{'=' * 100}")

    total = len(results)
    dv_ok = sum(1 for r in results if r["dv_verdict"] == "OK")
    dv_low = sum(1 for r in results if r["dv_verdict"] == "LOW")
    dv_high = sum(1 for r in results if r["dv_verdict"] == "HIGH")
    dv_na = sum(1 for r in results if r["dv_verdict"] == "N/A")

    tof_ok = sum(1 for r in results if r["tof_verdict"] == "OK")
    tof_fast = sum(1 for r in results if r["tof_verdict"] in ("FAST", "TOO_FAST"))
    tof_slow = sum(1 for r in results if r["tof_verdict"] in ("SLOW", "TOO_SLOW"))
    tof_na = sum(1 for r in results if r["tof_verdict"] == "N/A")

    print(f"\n  Δv accuracy:  {dv_ok}/{total} within range, {dv_low} too low, {dv_high} too high, {dv_na} no route")
    print(f"  TOF accuracy: {tof_ok}/{total} within range, {tof_fast} too fast, {tof_slow} too slow, {tof_na} no route")

    # ── ISSUES (ranked by severity) ──
    print(f"\n{'─' * 100}")
    print("  ISSUES (sorted by severity)")
    print(f"{'─' * 100}")

    issues = []
    for r in results:
        severity = 0
        issue_parts = []
        if r["dv_verdict"] == "LOW":
            severity += r["dv_pct"]
            issue_parts.append(f"Δv {r['dv_pct']:.0f}% below ref")
        elif r["dv_verdict"] == "HIGH":
            severity += r["dv_pct"]
            issue_parts.append(f"Δv {r['dv_pct']:.0f}% above ref")
        if r["tof_verdict"] == "TOO_FAST":
            severity += r["tof_ratio"] * 20
            issue_parts.append(f"TOF {r['tof_ratio']:.0f}× too fast")
        elif r["tof_verdict"] == "TOO_SLOW":
            severity += r["tof_ratio"] * 20
            issue_parts.append(f"TOF {r['tof_ratio']:.0f}× too slow")
        elif r["tof_verdict"] == "FAST":
            severity += r["tof_ratio"] * 5
            issue_parts.append(f"TOF {r['tof_ratio']:.1f}× fast")
        elif r["tof_verdict"] == "SLOW":
            severity += r["tof_ratio"] * 5
            issue_parts.append(f"TOF {r['tof_ratio']:.1f}× slow")
        if r["dv_verdict"] == "N/A":
            severity += 50
            issue_parts.append("NO ROUTE")

        if issue_parts:
            issues.append((severity, f"{r['from']}→{r['to']}", ", ".join(issue_parts), r["notes"]))

    issues.sort(key=lambda x: -x[0])
    for sev, route, desc, notes in issues:
        print(f"  [{sev:6.0f}] {route:<35s} {desc}")
        if notes:
            print(f"          Ref: {notes}")
    
    if not issues:
        print("  No issues found!")

    print()

    if args.cleanup:
        print("No ships to clean up (audit uses transfer_quote, no ships spawned).")


if __name__ == "__main__":
    main()
