#!/usr/bin/env python3
"""
Saturn Transfer Planner Audit
==============================
Exercises every Saturn-related transfer route and porkchop plot endpoint.
Validates delta-v values against physical expectations and reports anomalies.

Usage:
    python3 scripts/audit_saturn_transfers.py [--base-url URL] [--cookie-file FILE]

Requires an authenticated session cookie for the dev server.
"""

import argparse
import json
import math
import sys
import time
import urllib.request
import urllib.error
import http.cookiejar

# ── Physical reference values ──────────────────────────────────
# These are rough Hohmann-class Δv values (km/s) for sanity checking.
# Real Lambert values vary ±30 % depending on phase angle / departure window.
REFERENCE_DV = {
    # Interplanetary (total including local manoeuvres)
    ("LEO", "SAT_LO"):   (8.0, 30.0),   # Earth→Saturn: ~8–20+ km/s typical
    ("LEO", "SAT_HO"):   (8.0, 30.0),
    ("SAT_LO", "LEO"):   (8.0, 30.0),
    ("JUP_LO", "SAT_LO"):(3.0, 20.0),   # Jupiter→Saturn: cheaper if alignment good
    ("SAT_LO", "JUP_LO"):(3.0, 20.0),
    ("LMO", "SAT_LO"):   (5.0, 30.0),   # Mars→Saturn
    ("SAT_LO", "LMO"):   (5.0, 30.0),

    # Local Saturn system (same body, should be <10 km/s)
    ("SAT_LO", "SAT_HO"):   (3.0, 6.0),
    ("SAT_HO", "SAT_LO"):   (3.0, 6.0),
    ("SAT_LO", "TITAN_LO"): (0.5, 8.0),
    ("SAT_LO", "MIMAS_LO"): (0.5, 8.0),
    ("TITAN_LO", "TITAN_HO"):(0.1, 3.0),
    ("MIMAS_LO", "MIMAS_HO"):(0.1, 3.0),
}

# Expected TOF ranges in days
REFERENCE_TOF_DAYS = {
    ("LEO", "SAT_LO"):    (600, 4000),    # Hohmann ~6 years, Lambert can vary
    ("LEO", "SAT_HO"):    (600, 4000),
    ("SAT_LO", "LEO"):    (600, 4000),
    ("JUP_LO", "SAT_LO"): (200, 3000),
    ("SAT_LO", "JUP_LO"): (200, 3000),
    ("SAT_LO", "SAT_HO"): (300, 1000),    # Local Hohmann ~645 days
    ("SAT_HO", "SAT_LO"): (300, 1000),
}


def make_opener(cookie_file):
    cj = http.cookiejar.MozillaCookieJar(cookie_file)
    cj.load(ignore_discard=True, ignore_expires=True)
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def api_get(opener, base_url, path, params=None):
    """GET request, return parsed JSON or raise."""
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{base_url}{path}?{qs}"
    else:
        url = f"{base_url}{path}"
    req = urllib.request.Request(url)
    try:
        with opener.open(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if hasattr(e, "read") else ""
        return {"_error": e.code, "_detail": body[:300]}
    except Exception as e:
        return {"_error": str(e)}


def format_dv(dv_m_s):
    return f"{dv_m_s/1000:.2f} km/s" if dv_m_s else "N/A"


def format_tof(tof_s):
    if not tof_s:
        return "N/A"
    days = tof_s / 86400
    if days > 365:
        return f"{days:.0f}d ({days/365.25:.1f}y)"
    return f"{days:.0f}d"


def section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def run_audit(base_url, cookie_file):
    opener = make_opener(cookie_file)
    issues = []
    results = []

    # ── Saturn system locations ────────────────────────────────
    saturn_orbit_nodes = [
        "SAT_LO", "SAT_HO",
        "MIMAS_LO", "MIMAS_HO",
        "ENCELADUS_LO", "ENCELADUS_HO",
        "TETHYS_LO", "TETHYS_HO",
        "DIONE_LO", "DIONE_HO",
        "RHEA_LO", "RHEA_HO",
        "TITAN_LO", "TITAN_HO",
        "IAPETUS_LO", "IAPETUS_HO",
    ]

    outer_system_locs = ["LEO", "HEO", "GEO", "LLO", "LMO", "JUP_LO", "JUP_HO",
                         "IO_LO", "EUROPA_LO", "GANYMEDE_LO", "CALLISTO_LO"]

    # ── PART 1: Transfer Quotes ────────────────────────────────
    section("PART 1: Transfer Quotes — Saturn Intra-System")

    # All intra-Saturn transfers (orbit nodes within Saturn system)
    intra_saturn_pairs = []
    for i, a in enumerate(saturn_orbit_nodes):
        for b in saturn_orbit_nodes[i+1:]:
            intra_saturn_pairs.append((a, b))
            intra_saturn_pairs.append((b, a))

    print(f"\nTesting {len(intra_saturn_pairs)} intra-Saturn transfer pairs...")
    print(f"{'From':<18} {'To':<18} {'Δv':>12} {'TOF':>14} {'Mode':<18} {'Status'}")
    print("-" * 100)

    intra_ok = 0
    intra_fail = 0
    for from_id, to_id in intra_saturn_pairs:
        data = api_get(opener, base_url, "/api/transfer_quote",
                       {"from_id": from_id, "to_id": to_id})
        if "_error" in data:
            status = f"ERROR: {data['_error']}"
            intra_fail += 1
            issues.append(f"INTRA {from_id}→{to_id}: {status}")
            print(f"{from_id:<18} {to_id:<18} {'---':>12} {'---':>14} {'---':<18} {status}")
        else:
            dv = data.get("dv_m_s", 0)
            tof = data.get("tof_s", 0)
            mode = data.get("route_mode", "?")
            is_ip = data.get("is_interplanetary", False)

            # Sanity checks
            flags = []
            ref = REFERENCE_DV.get((from_id, to_id))
            if ref:
                lo, hi = ref[0] * 1000, ref[1] * 1000
                if dv < lo:
                    flags.append(f"Δv LOW (<{ref[0]}km/s)")
                elif dv > hi:
                    flags.append(f"Δv HIGH (>{ref[1]}km/s)")
            if dv > 20000 and not is_ip:
                flags.append("Δv>20km/s for local?!")
            if tof <= 0:
                flags.append("TOF=0")

            ref_tof = REFERENCE_TOF_DAYS.get((from_id, to_id))
            if ref_tof:
                tof_d = tof / 86400
                if tof_d < ref_tof[0]:
                    flags.append(f"TOF SHORT (<{ref_tof[0]}d)")
                elif tof_d > ref_tof[1]:
                    flags.append(f"TOF LONG (>{ref_tof[1]}d)")

            status = ", ".join(flags) if flags else "OK"
            if flags:
                issues.append(f"INTRA {from_id}→{to_id}: {status} (dv={format_dv(dv)}, tof={format_tof(tof)})")
            intra_ok += 1

            results.append({
                "type": "intra",
                "from": from_id, "to": to_id,
                "dv_m_s": dv, "tof_s": tof,
                "mode": mode, "is_ip": is_ip,
                "flags": flags,
            })
            print(f"{from_id:<18} {to_id:<18} {format_dv(dv):>12} {format_tof(tof):>14} {mode:<18} {status}")

    print(f"\nIntra-Saturn: {intra_ok} OK, {intra_fail} errors")

    # ── PART 2: Interplanetary to/from Saturn ──────────────────
    section("PART 2: Transfer Quotes — Interplanetary To/From Saturn")

    interplanetary_pairs = []
    for sat_loc in ["SAT_LO", "SAT_HO", "TITAN_LO", "MIMAS_LO", "ENCELADUS_LO"]:
        for other in outer_system_locs:
            interplanetary_pairs.append((other, sat_loc))
            interplanetary_pairs.append((sat_loc, other))

    print(f"\nTesting {len(interplanetary_pairs)} interplanetary transfer pairs...")
    print(f"{'From':<18} {'To':<18} {'Δv':>12} {'TOF':>14} {'Mode':<18} {'Status'}")
    print("-" * 100)

    ip_ok = 0
    ip_fail = 0
    for from_id, to_id in interplanetary_pairs:
        data = api_get(opener, base_url, "/api/transfer_quote",
                       {"from_id": from_id, "to_id": to_id})
        if "_error" in data:
            status = f"ERROR: {data['_error']}"
            ip_fail += 1
            issues.append(f"IP {from_id}→{to_id}: {status}")
            print(f"{from_id:<18} {to_id:<18} {'---':>12} {'---':>14} {'---':<18} {status}")
        else:
            dv = data.get("dv_m_s", 0)
            tof = data.get("tof_s", 0)
            mode = data.get("route_mode", "?")
            is_ip = data.get("is_interplanetary", False)
            gw_dep = data.get("gateway_departure", "")
            gw_arr = data.get("gateway_arrival", "")

            flags = []
            if not is_ip:
                flags.append("NOT INTERPLANETARY?!")

            ref = REFERENCE_DV.get((from_id, to_id))
            if ref:
                lo, hi = ref[0] * 1000, ref[1] * 1000
                if dv < lo:
                    flags.append(f"Δv LOW (<{ref[0]}km/s)")
                elif dv > hi:
                    flags.append(f"Δv HIGH (>{ref[1]}km/s)")

            # Generic sanity: interplanetary to Saturn should generally be 7-35 km/s
            if dv < 3000:
                flags.append("Δv suspiciously low (<3 km/s)")
            elif dv > 50000:
                flags.append("Δv suspiciously high (>50 km/s)")

            ref_tof = REFERENCE_TOF_DAYS.get((from_id, to_id))
            if ref_tof:
                tof_d = tof / 86400
                if tof_d < ref_tof[0]:
                    flags.append(f"TOF SHORT (<{ref_tof[0]}d)")
                elif tof_d > ref_tof[1]:
                    flags.append(f"TOF LONG (>{ref_tof[1]}d)")

            status_str = ", ".join(flags) if flags else "OK"
            if flags:
                issues.append(f"IP {from_id}→{to_id}: {status_str} (dv={format_dv(dv)}, tof={format_tof(tof)}, gw={gw_dep}→{gw_arr})")

            ip_ok += 1
            gw_info = f" [{gw_dep}→{gw_arr}]" if gw_dep else ""
            results.append({
                "type": "interplanetary",
                "from": from_id, "to": to_id,
                "dv_m_s": dv, "tof_s": tof,
                "mode": mode, "is_ip": is_ip,
                "gw_dep": gw_dep, "gw_arr": gw_arr,
                "flags": flags,
            })
            print(f"{from_id:<18} {to_id:<18} {format_dv(dv):>12} {format_tof(tof):>14} {mode:<18} {status_str}{gw_info}")

    print(f"\nInterplanetary: {ip_ok} OK, {ip_fail} errors")

    # ── PART 3: Advanced Quotes (phase angle effects) ──────────
    section("PART 3: Advanced Transfer Quotes — Phase Angle & Extra Δv")

    adv_pairs = [
        ("LEO", "SAT_LO", 0.0),
        ("LEO", "SAT_LO", 0.5),
        ("LEO", "SAT_LO", 1.0),
        ("SAT_LO", "LEO", 0.0),
        ("SAT_LO", "JUP_LO", 0.0),
        ("JUP_LO", "SAT_LO", 0.0),
        ("SAT_LO", "TITAN_LO", 0.0),
        ("TITAN_LO", "SAT_LO", 0.0),
        ("LEO", "TITAN_LO", 0.0),
        ("LEO", "TITAN_LO", 1.0),
        ("LMO", "SAT_LO", 0.0),
    ]

    print(f"\n{'From':<14} {'To':<14} {'ExDv':>5} {'Base Δv':>10} {'Phase Δv':>10} {'Final Δv':>10} {'Base TOF':>10} {'Final TOF':>10} {'Phase×':>7}")
    print("-" * 105)

    for from_id, to_id, extra_dv in adv_pairs:
        data = api_get(opener, base_url, "/api/transfer_quote_advanced",
                       {"from_id": from_id, "to_id": to_id, "extra_dv_fraction": str(extra_dv)})
        if "_error" in data:
            print(f"{from_id:<14} {to_id:<14} {extra_dv:>5.1f} ERROR: {data['_error']}")
            issues.append(f"ADV {from_id}→{to_id} x{extra_dv}: ERROR {data['_error']}")
        else:
            base_dv = data.get("base_dv_m_s", 0)
            phase_dv = data.get("phase_adjusted_dv_m_s", 0)
            final_dv = data.get("dv_m_s", 0)
            base_tof = data.get("base_tof_s", 0)
            final_tof = data.get("tof_s", 0)
            phase_mult = data.get("phase_multiplier", 1.0)

            flags = []
            if extra_dv > 0 and final_tof >= base_tof:
                flags.append("Extra Δv didn't reduce TOF")
            if extra_dv > 0 and final_dv <= base_dv:
                flags.append("Extra Δv didn't increase cost")
            if phase_mult < 0.5 or phase_mult > 5.0:
                flags.append(f"Phase mult unusual: {phase_mult:.2f}")

            status_str = ", ".join(flags) if flags else ""
            if flags:
                issues.append(f"ADV {from_id}→{to_id} x{extra_dv}: {status_str}")

            print(f"{from_id:<14} {to_id:<14} {extra_dv:>5.1f} {format_dv(base_dv):>10} {format_dv(phase_dv):>10} {format_dv(final_dv):>10} {format_tof(base_tof):>10} {format_tof(final_tof):>10} {phase_mult:>7.2f}  {status_str}")

    # ── PART 4: Porkchop Plots ─────────────────────────────────
    section("PART 4: Porkchop Plots")

    porkchop_pairs = [
        ("LEO", "SAT_LO", "Earth → Saturn"),
        ("SAT_LO", "LEO", "Saturn → Earth"),
        ("JUP_LO", "SAT_LO", "Jupiter → Saturn"),
        ("SAT_LO", "JUP_LO", "Saturn → Jupiter"),
        ("LMO", "SAT_LO", "Mars → Saturn"),
        ("SAT_LO", "LMO", "Saturn → Mars"),
        ("LEO", "TITAN_LO", "Earth → Titan"),
        ("TITAN_LO", "LEO", "Titan → Earth"),
    ]

    print(f"\n{'Transfer':<24} {'Grid':>5} {'Valid':>6} {'Fill%':>6} {'Best Δv':>12} {'Best TOF':>14} {'Dep Δv':>10} {'Arr Δv':>10} {'Status'}")
    print("-" * 110)

    for from_id, to_id, label in porkchop_pairs:
        data = api_get(opener, base_url, "/api/transfer/porkchop",
                       {"from_id": from_id, "to_id": to_id, "grid_size": "25"})
        if "_error" in data:
            print(f"{label:<24} {'---':>5} {'---':>6} {'---':>6} {'---':>12} {'---':>14} {'---':>10} {'---':>10} ERROR: {data['_error']}")
            issues.append(f"PORKCHOP {from_id}→{to_id}: ERROR {data['_error']}")
            continue

        grid = data.get("dv_grid", [])
        grid_sz = data.get("grid_size", 0)
        total_cells = sum(len(r) for r in grid)
        valid_cells = sum(1 for r in grid for v in r if v is not None)
        fill_pct = (valid_cells / total_cells * 100) if total_cells > 0 else 0

        best = data.get("best_solutions", [])
        flags = []
        if not best:
            flags.append("NO BEST SOLUTIONS")
        if fill_pct < 5:
            flags.append(f"Very sparse grid ({fill_pct:.0f}%)")
        elif fill_pct < 20:
            flags.append(f"Sparse grid ({fill_pct:.0f}%)")

        if best:
            b = best[0]
            best_dv = b.get("dv_m_s", 0)
            best_tof = b.get("tof_s", 0)
            dep_dv = b.get("dv_depart_m_s", 0)
            arr_dv = b.get("dv_arrive_m_s", 0)

            # Sanity: best Δv should be within physical range
            ref = REFERENCE_DV.get((from_id, to_id))
            if ref:
                lo, hi = ref[0] * 1000, ref[1] * 1000
                if best_dv < lo * 0.5:
                    flags.append(f"Best Δv suspiciously low")
                elif best_dv > hi * 2:
                    flags.append(f"Best Δv suspiciously high")
        else:
            best_dv = 0
            best_tof = 0
            dep_dv = 0
            arr_dv = 0

        status_str = ", ".join(flags) if flags else "OK"
        if flags:
            issues.append(f"PORKCHOP {from_id}→{to_id}: {status_str}")

        print(f"{label:<24} {grid_sz:>5} {valid_cells:>6} {fill_pct:>5.1f}% {format_dv(best_dv):>12} {format_tof(best_tof):>14} {format_dv(dep_dv):>10} {format_dv(arr_dv):>10} {status_str}")

        # Print top 3 solutions
        for i, sol in enumerate(best[:3]):
            dep_time = sol.get("departure_time", 0)
            dep_days = dep_time / 86400
            print(f"    #{i+1}: Δv={format_dv(sol.get('dv_m_s',0))}  "
                  f"dep={format_dv(sol.get('dv_depart_m_s',0))}  "
                  f"arr={format_dv(sol.get('dv_arrive_m_s',0))}  "
                  f"TOF={format_tof(sol.get('tof_s',0))}  "
                  f"depart=day {dep_days:.0f}")

    # ── PART 5: Symmetry Check ─────────────────────────────────
    section("PART 5: Transfer Symmetry (A→B vs B→A)")

    symmetry_pairs = [
        ("LEO", "SAT_LO"),
        ("SAT_LO", "JUP_LO"),
        ("SAT_LO", "SAT_HO"),
        ("SAT_LO", "TITAN_LO"),
        ("SAT_LO", "MIMAS_LO"),
        ("LEO", "TITAN_LO"),
    ]

    print(f"\n{'Pair':<30} {'A→B Δv':>12} {'B→A Δv':>12} {'Diff %':>8} {'Status'}")
    print("-" * 80)

    for a, b in symmetry_pairs:
        ab = api_get(opener, base_url, "/api/transfer_quote", {"from_id": a, "to_id": b})
        ba = api_get(opener, base_url, "/api/transfer_quote", {"from_id": b, "to_id": a})

        if "_error" in ab or "_error" in ba:
            print(f"{a}↔{b:<26} ERROR")
            continue

        dv_ab = ab.get("dv_m_s", 0)
        dv_ba = ba.get("dv_m_s", 0)

        avg = (dv_ab + dv_ba) / 2 if (dv_ab + dv_ba) > 0 else 1
        diff_pct = abs(dv_ab - dv_ba) / avg * 100

        flags = []
        # Local transfers should be nearly symmetric; interplanetary can differ by phase angle
        is_ip = ab.get("is_interplanetary", False)
        if not is_ip and diff_pct > 5:
            flags.append("Local asymmetry >5%")
        elif is_ip and diff_pct > 60:
            flags.append("Large asymmetry")

        status_str = ", ".join(flags) if flags else "OK"
        if flags:
            issues.append(f"SYMMETRY {a}↔{b}: {status_str} ({diff_pct:.1f}%)")

        print(f"{a}↔{b:<26} {format_dv(dv_ab):>12} {format_dv(dv_ba):>12} {diff_pct:>7.1f}% {status_str}")

    # ── PART 6: Moon-to-Moon Transfers ─────────────────────────
    section("PART 6: Saturn Moon-to-Moon Transfers")

    moon_los = ["MIMAS_LO", "ENCELADUS_LO", "TETHYS_LO", "DIONE_LO", "RHEA_LO", "TITAN_LO", "IAPETUS_LO"]

    print(f"\n{'From':<16} {'To':<16} {'Δv':>12} {'TOF':>14} {'Mode':<18} {'Status'}")
    print("-" * 90)

    moon_ok = 0
    moon_fail = 0
    for i, a in enumerate(moon_los):
        for b in moon_los[i+1:]:
            for from_id, to_id in [(a, b), (b, a)]:
                data = api_get(opener, base_url, "/api/transfer_quote",
                               {"from_id": from_id, "to_id": to_id})
                if "_error" in data:
                    moon_fail += 1
                    print(f"{from_id:<16} {to_id:<16} {'---':>12} {'---':>14} {'---':<18} ERROR: {data['_error']}")
                    issues.append(f"MOON {from_id}→{to_id}: ERROR")
                else:
                    dv = data.get("dv_m_s", 0)
                    tof = data.get("tof_s", 0)
                    mode = data.get("route_mode", "?")
                    is_ip = data.get("is_interplanetary", False)

                    flags = []
                    if is_ip:
                        flags.append("Flagged interplanetary?!")
                    if dv > 15000:
                        flags.append("Δv >15 km/s for moon-to-moon")
                    if dv < 100:
                        flags.append("Δv <0.1 km/s suspiciously low")

                    status_str = ", ".join(flags) if flags else "OK"
                    if flags:
                        issues.append(f"MOON {from_id}→{to_id}: {status_str} (dv={format_dv(dv)})")
                    moon_ok += 1
                    print(f"{from_id:<16} {to_id:<16} {format_dv(dv):>12} {format_tof(tof):>14} {mode:<18} {status_str}")

    print(f"\nMoon-to-Moon: {moon_ok} OK, {moon_fail} errors")

    # ── Summary ────────────────────────────────────────────────
    section("AUDIT SUMMARY")

    total = len(intra_saturn_pairs) + len(interplanetary_pairs) + len(adv_pairs) + len(porkchop_pairs) + len(symmetry_pairs) * 2 + len(moon_los) * (len(moon_los)-1)
    print(f"\nTotal transfers tested: ~{total}")
    print(f"Issues found: {len(issues)}")

    if issues:
        print("\n--- All Issues ---")
        for i, issue in enumerate(issues, 1):
            print(f"  {i:>3}. {issue}")
    else:
        print("\n  ✓ All transfers passed sanity checks!")

    return len(issues)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Saturn Transfer Planner Audit")
    parser.add_argument("--base-url", default="http://localhost:8001",
                        help="Base URL of the dev server")
    parser.add_argument("--cookie-file", default=None,
                        help="Path to cookies file (Netscape format)")
    args = parser.parse_args()

    if args.cookie_file is None:
        import os
        script_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.dirname(script_dir)
        args.cookie_file = os.path.join(repo_root, "cookies_dev.txt")

    print(f"Saturn Transfer Planner Audit")
    print(f"Server: {args.base_url}")
    print(f"Cookies: {args.cookie_file}")

    issues = run_audit(args.base_url, args.cookie_file)
    sys.exit(1 if issues > 0 else 0)
