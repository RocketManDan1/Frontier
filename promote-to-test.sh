#!/usr/bin/env bash
# ── Promote current dev source → test server ────────────────────────
# Usage:  ./promote-to-test.sh
#
# This copies the current working tree (dev) into the frozen test-server
# source directory, then rebuilds only the test container.
#
# Pre-rebuild steps:
#   1. Stop the test container so the DB is safe to modify.
#   2. Teleport all in-transit ships to their destinations to avoid
#      bugs from path/orbit changes between versions.
#   3. Auth is enforced on the test server (DEV_SKIP_AUTH=false in
#      docker-compose.yml) — no one is auto-logged-in as admin.
set -euo pipefail

DEV_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_DIR="/home/user/docker/frontier-sol-2000-test"
TEST_DB="/home/user/docker/frontier-sol-2000-data/game.db"

# ── 1. Stop test container so we can safely touch the DB ────────────
echo "▸ Stopping test container..."
cd "$DEV_DIR"
sudo docker compose stop frontier-test 2>/dev/null || true

# ── 2. Teleport all in-transit ships to their destinations ──────────
if [ -f "$TEST_DB" ]; then
  IN_TRANSIT=$(sqlite3 "$TEST_DB" "SELECT COUNT(*) FROM ships WHERE arrives_at IS NOT NULL;")
  if [ "$IN_TRANSIT" -gt 0 ] 2>/dev/null; then
    echo "▸ Teleporting $IN_TRANSIT in-transit ship(s) to their destinations..."
    sqlite3 "$TEST_DB" "
      UPDATE ships
      SET location_id       = to_location_id,
          from_location_id  = NULL,
          to_location_id    = NULL,
          departed_at        = NULL,
          arrives_at         = NULL,
          transit_from_x     = NULL,
          transit_from_y     = NULL,
          transit_to_x       = NULL,
          transit_to_y       = NULL
      WHERE arrives_at IS NOT NULL;
    "
    echo "  ✔ All ships docked at their destinations."
  else
    echo "  (no ships in transit)"
  fi
else
  echo "  (test DB not found at $TEST_DB — skipping teleport)"
fi

# ── 3. Sync dev → test source ──────────────────────────────────────
echo "▸ Syncing dev → test source..."

# Wipe old test source (except docker-compose which lives in dev dir)
rm -rf "$TEST_DIR"
mkdir -p "$TEST_DIR"

# Copy everything except transient/git dirs
cd "$DEV_DIR"
find . -mindepth 1 \
  -not -path './.git/*' -not -name '.git' \
  -not -path './__pycache__/*' -not -name '__pycache__' \
  -not -path './data/*' -not -name 'data' \
  -not -path './tests/__pycache__/*' \
  | while IFS= read -r f; do
    if [ -d "$f" ]; then
      mkdir -p "$TEST_DIR/$f"
    else
      cp "$f" "$TEST_DIR/$f"
    fi
  done

# ── 4. Rebuild & start ─────────────────────────────────────────────
echo "▸ Rebuilding test container..."
sudo docker compose up -d --build frontier-test

echo "✔ Test server updated and running on :8000"
echo "  Auth: ON (DEV_SKIP_AUTH=false) — users must log in"
