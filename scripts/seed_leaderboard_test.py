#!/usr/bin/env python3
"""
Seed fake corporations with varying research progress for leaderboard testing.
Run against the dev server (default port 8001).

Usage:
    python scripts/seed_leaderboard_test.py [--port 8001]
"""
import json
import sys
import urllib.request
import urllib.error

PORT = 8001
for i, a in enumerate(sys.argv):
    if a == "--port" and i + 1 < len(sys.argv):
        PORT = int(sys.argv[i + 1])

BASE = f"http://localhost:{PORT}"

# Fake corps with colors and how many nuclear nodes they should have unlocked
FAKE_CORPS = [
    {"name": "Helios Industries",  "color": "#e8a735", "password": "test", "research_count": 15},  # all nodes = 90%
    {"name": "Nova Dynamics",      "color": "#3a7bd5", "password": "test", "research_count": 10},  # ~60%
    {"name": "Vostok Mining Co",   "color": "#d53a3a", "password": "test", "research_count": 6},   # ~36%
    {"name": "Lunar Express",      "color": "#74d8c0", "password": "test", "research_count": 3},   # ~18%
    {"name": "Deep Space Corp",    "color": "#b05ad5", "password": "test", "research_count": 1},   # ~6%
]

# Nuclear research tree nodes in dependency order
NUCLEAR_NODES = [
    "starter_corp",
    "nuclear_fission",
    "pebble_bed",
    "advanced_pebble_bed",
    "advanced_solid_core_ii",
    "advanced_solid_core_iii",
    "liquid_core",
    "vapor_core",
    "vapor_core_ii",
    "high_power_solid_core",
    "high_power_solid_core_ii",
    "closed_cycle_gas_core",
    "open_cycle_gas_core",
    "advanced_liquid_core",
    "early_fusion_reactors",
]

# Prerequisites for each node
PREREQS = {
    "starter_corp": [],
    "nuclear_fission": ["starter_corp"],
    "pebble_bed": ["nuclear_fission"],
    "advanced_pebble_bed": ["pebble_bed"],
    "advanced_solid_core_ii": ["nuclear_fission"],
    "advanced_solid_core_iii": ["advanced_solid_core_ii"],
    "liquid_core": ["nuclear_fission"],
    "vapor_core": ["liquid_core"],
    "vapor_core_ii": ["vapor_core"],
    "high_power_solid_core": ["advanced_solid_core_iii"],
    "high_power_solid_core_ii": ["high_power_solid_core"],
    "closed_cycle_gas_core": ["high_power_solid_core_ii", "vapor_core_ii"],
    "open_cycle_gas_core": ["closed_cycle_gas_core"],
    "advanced_liquid_core": ["liquid_core"],
    "early_fusion_reactors": ["open_cycle_gas_core"],
}


def api_post(path, data, cookie=None):
    """POST JSON to the server."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            # Extract Set-Cookie
            cookie_header = resp.getheader("Set-Cookie")
            return result, cookie_header
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        return {"error": err_body, "status": e.code}, None


def api_get(path, cookie=None):
    """GET from the server."""
    req = urllib.request.Request(f"{BASE}{path}")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode(), "status": e.code}


def main():
    print(f"Seeding leaderboard test corps on {BASE}...")

    for corp_info in FAKE_CORPS:
        name = corp_info["name"]
        print(f"\n--- Creating corp: {name} ---")

        # Register the corp
        result, cookie_header = api_post("/api/auth/corp/register", {
            "corp_name": name,
            "password": corp_info["password"],
            "color": corp_info["color"],
        })

        if "error" in result:
            if "already taken" in str(result.get("error", "")):
                # Login instead
                print(f"  Already exists, logging in...")
                result, cookie_header = api_post("/api/auth/corp/login", {
                    "corp_name": name,
                    "password": corp_info["password"],
                })
                if "error" in result:
                    print(f"  Login failed: {result}")
                    continue
            else:
                print(f"  Registration failed: {result}")
                continue

        # Extract session cookie
        if not cookie_header:
            print(f"  No session cookie returned!")
            continue
        # Parse the cookie value
        cookie_str = cookie_header.split(";")[0]
        print(f"  Session: {cookie_str[:40]}...")

        # Give the org enough research points to unlock nodes
        # First get org state
        org_data = api_get("/api/org", cookie=cookie_str)
        if "error" in org_data:
            print(f"  Failed to get org: {org_data}")
            continue
        org = org_data.get("org", {})
        print(f"  Org: {org.get('name', '?')}, RP: {org.get('research_points', 0):.1f}")

        # Unlock research nodes up to the specified count
        nodes_to_unlock = NUCLEAR_NODES[:corp_info["research_count"]]
        already_unlocked = {u["tech_id"] for u in org.get("research_unlocks", [])}

        for node_id in nodes_to_unlock:
            if node_id in already_unlocked:
                print(f"  Already unlocked: {node_id}")
                continue
            prereqs = PREREQS.get(node_id, [])
            result, _ = api_post("/api/org/research/unlock", {
                "tech_id": node_id,
                "cost": 0,  # We'll set cost to 0 for testing
                "prerequisites": prereqs,
            }, cookie=cookie_str)
            if "error" in result:
                print(f"  Unlock {node_id} failed: {result}")
            else:
                print(f"  Unlocked: {node_id}")

    # Check the leaderboard
    print("\n\n=== LEADERBOARD ===")
    lb = api_get("/api/org/leaderboard")
    if "error" in lb:
        print(f"Failed: {lb}")
        return

    entries = lb.get("leaderboard", [])
    scenario = lb.get("scenario", {})
    print(f"Scenario: {scenario.get('name', '?')}")
    print(f"Objective: {scenario.get('objective', '?')}")
    print(f"{'#':<3} {'Corp':<22} {'Research':>9} {'He3':>6} {'Build':>6} {'Total':>7}")
    print("-" * 60)
    for i, e in enumerate(entries):
        print(f"{i+1:<3} {e['corp_name']:<22} {e['research_pct']:>8.1f}% {e['he3_pct']:>5.1f}% {e['build_pct']:>5.1f}% {e['total_pct']:>6.1f}%")

    print("\nDone!")


if __name__ == "__main__":
    main()
