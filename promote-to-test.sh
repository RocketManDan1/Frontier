#!/usr/bin/env bash
# ── Promote current dev source → test server ────────────────────────
# Usage:  ./promote-to-test.sh
#
# This copies the current working tree (dev) into the frozen test-server
# source directory, then rebuilds only the test container.
set -euo pipefail

DEV_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_DIR="/home/user/docker/frontier-sol-2000-test"

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

echo "▸ Rebuilding test container..."
sudo docker compose up -d --build frontier-test

echo "✔ Test server updated and running on :8000"
