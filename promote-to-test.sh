#!/usr/bin/env bash
# ── Promote current dev source → test server ────────────────────────
# Usage:  ./promote-to-test.sh
#
# This copies the current working tree (dev) into the frozen test-server
# source directory, then rebuilds only the test container.
#
# Safety-first defaults (least-impact mode):
#   - Always creates a timestamped DB backup before any DB changes.
#   - Refuses to run if active production jobs would be cancelled by
#     pending migration 0015, unless explicitly allowed.
#   - Refuses to teleport in-transit ships unless explicitly allowed.
#
# Options:
#   --yes               Non-interactive; auto-confirm prompts.
#   --patch             Low-impact promote mode for small code changes.
#                       - Never teleports ships.
#                       - Never allows migration-0015 job cancellation.
#                       - Ignores in-transit ship blocking preflight.
#   --allow-job-cancel  Permit promotion even if migration 0015 is pending
#                       and active production jobs exist.
#   --allow-teleport    Permit teleporting in-transit ships to destination.
#
# Examples:
#   ./promote-to-test.sh
#   ./promote-to-test.sh --patch --yes
#   ./promote-to-test.sh --allow-job-cancel --yes
#   ./promote-to-test.sh --allow-teleport --yes
set -euo pipefail

DEV_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_DIR="/home/user/docker/frontier-sol-2000-test"
TEST_DB="/home/user/docker/frontier-sol-2000-data/game.db"
TEST_DB_BACKUP_DIR="/home/user/docker/frontier-sol-2000-data/backups"

AUTO_YES=false
PATCH_MODE=false
ALLOW_JOB_CANCEL=false
ALLOW_TELEPORT=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes)
      AUTO_YES=true
      ;;
    --patch)
      PATCH_MODE=true
      ;;
    --allow-job-cancel)
      ALLOW_JOB_CANCEL=true
      ;;
    --allow-teleport)
      ALLOW_TELEPORT=true
      ;;
    -h|--help)
      sed -n '1,40p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Run with --help for usage."
      exit 1
      ;;
  esac
  shift
done

# --patch enforces low-impact behavior and bypasses only transit blocking.
if [ "$PATCH_MODE" = true ]; then
  ALLOW_JOB_CANCEL=false
  ALLOW_TELEPORT=false
fi

confirm_or_abort() {
  local prompt="$1"
  if [ "$AUTO_YES" = true ]; then
    return 0
  fi
  read -r -p "$prompt [y/N] " REPLY
  case "$REPLY" in
    y|Y|yes|YES)
      return 0
      ;;
    *)
      echo "Aborted."
      exit 1
      ;;
  esac
}

get_sql_scalar() {
  local query="$1"
  sqlite3 "$TEST_DB" "$query"
}

cd "$DEV_DIR"

# ── 1. Preflight checks while server is still running ───────────────
if [ ! -f "$TEST_DB" ]; then
  echo "✖ Test DB not found at $TEST_DB"
  exit 1
fi

PENDING_0015=$(get_sql_scalar "SELECT CASE WHEN EXISTS(SELECT 1 FROM schema_migrations WHERE migration_id='0015_industry_v2') THEN 0 ELSE 1 END;")
ACTIVE_JOBS=$(get_sql_scalar "SELECT CASE WHEN EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_jobs') THEN (SELECT COUNT(*) FROM production_jobs WHERE status='active') ELSE 0 END;")
IN_TRANSIT=$(get_sql_scalar "SELECT CASE WHEN EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='ships') THEN (SELECT COUNT(*) FROM ships WHERE arrives_at IS NOT NULL OR (orbit_json IS NOT NULL AND maneuver_json IS NOT NULL AND maneuver_json != '[]' AND maneuver_json != '' AND location_id IS NULL)) ELSE 0 END;")

echo "▸ Preflight summary"
echo "  - active production jobs: $ACTIVE_JOBS"
echo "  - in-transit ships: $IN_TRANSIT"
if [ "$PENDING_0015" = "1" ]; then
  echo "  - migration 0015_industry_v2: PENDING"
else
  echo "  - migration 0015_industry_v2: already applied"
fi

if [ "$PENDING_0015" = "1" ] && [ "$ACTIVE_JOBS" -gt 0 ] && [ "$ALLOW_JOB_CANCEL" != true ]; then
  echo ""
  echo "✖ Promotion blocked to protect testers:"
  echo "  Migration 0015 will cancel active production jobs."
  if [ "$PATCH_MODE" = true ]; then
    echo "  --patch mode is low-impact and cannot bypass this safety check."
    echo "  Wait until jobs complete, or run without --patch and use --allow-job-cancel."
  else
    echo "  Re-run with --allow-job-cancel to proceed anyway."
  fi
  exit 1
fi

if [ "$IN_TRANSIT" -gt 0 ] && [ "$ALLOW_TELEPORT" != true ] && [ "$PATCH_MODE" != true ]; then
  echo ""
  echo "✖ Promotion blocked to protect testers:"
  echo "  $IN_TRANSIT ship(s) are in transit; teleport is now opt-in."
  echo "  Re-run with --allow-teleport to dock them before promote, or use --patch for low-impact code-only promote."
  exit 1
fi

if [ "$PENDING_0015" = "1" ] && [ "$ACTIVE_JOBS" -gt 0 ] && [ "$ALLOW_JOB_CANCEL" = true ]; then
  confirm_or_abort "⚠ Proceed and allow migration 0015 to cancel active jobs?"
fi

if [ "$IN_TRANSIT" -gt 0 ] && [ "$ALLOW_TELEPORT" = true ]; then
  confirm_or_abort "⚠ Proceed and teleport $IN_TRANSIT in-transit ship(s)?"
fi

if [ "$PATCH_MODE" = true ] && [ "$IN_TRANSIT" -gt 0 ]; then
  echo "▸ Patch mode: leaving $IN_TRANSIT in-transit ship(s) untouched."
fi

confirm_or_abort "Promote current dev code to test now?"

# ── 2. Stop test container so DB backup/modification is safe ────────
echo "▸ Stopping test container..."
sudo docker compose stop frontier-test 2>/dev/null || true

# ── 3. Backup test DB before touching anything ───────────────────────
mkdir -p "$TEST_DB_BACKUP_DIR"
BACKUP_PATH="$TEST_DB_BACKUP_DIR/game_$(date +%Y%m%d_%H%M%S).db"
cp "$TEST_DB" "$BACKUP_PATH"
echo "▸ Backed up test DB → $BACKUP_PATH"

# ── 4. Optional: teleport all in-transit ships to destinations ──────
if [ "$IN_TRANSIT" -gt 0 ] && [ "$ALLOW_TELEPORT" = true ]; then
  echo "▸ Teleporting $IN_TRANSIT in-transit ship(s) to their destinations..."
  # Legacy transit ships (arrives_at timer)
  sqlite3 "$TEST_DB" "
    UPDATE ships
    SET location_id       = to_location_id,
        from_location_id  = NULL,
        to_location_id    = NULL,
        departed_at       = NULL,
        arrives_at        = NULL,
        transit_from_x    = NULL,
        transit_from_y    = NULL,
        transit_to_x      = NULL,
        transit_to_y      = NULL,
        orbit_json        = NULL,
        maneuver_json     = NULL,
        orbit_body_id     = NULL,
        orbit_predictions_json = NULL,
        trajectory_json   = NULL
    WHERE arrives_at IS NOT NULL;
  "
  # Orbit-model transit ships (pending maneuvers, not yet docked)
  sqlite3 "$TEST_DB" "
    UPDATE ships
    SET location_id       = COALESCE(to_location_id, location_id),
        from_location_id  = NULL,
        to_location_id    = NULL,
        departed_at       = NULL,
        arrives_at        = NULL,
        transit_from_x    = NULL,
        transit_from_y    = NULL,
        transit_to_x      = NULL,
        transit_to_y      = NULL,
        orbit_json        = NULL,
        maneuver_json     = NULL,
        orbit_body_id     = NULL,
        orbit_predictions_json = NULL,
        trajectory_json   = NULL
    WHERE orbit_json IS NOT NULL
      AND maneuver_json IS NOT NULL
      AND maneuver_json != '[]'
      AND maneuver_json != ''
      AND location_id IS NULL;
  "
  echo "  ✔ All ships docked at their destinations."
fi

# ── 5. Sync dev → test source ──────────────────────────────────────
echo "▸ Syncing dev → test source..."

# Wipe old test source (except docker-compose which lives in dev dir)
rm -rf "$TEST_DIR"
mkdir -p "$TEST_DIR"

# Copy everything except transient/git dirs
find . -mindepth 1 \
  -not -path './.git/*' -not -name '.git' \
  -not -path './__pycache__/*' -not -name '__pycache__' \
  -not -path './data/*' -not -path './data' \
  -not -path './tests/__pycache__/*' \
  | while IFS= read -r f; do
    if [ -d "$f" ]; then
      mkdir -p "$TEST_DIR/$f"
    else
      cp "$f" "$TEST_DIR/$f"
    fi
  done

# ── 6. Rebuild & start ─────────────────────────────────────────────
echo "▸ Rebuilding test container..."
sudo docker compose up -d --build frontier-test

echo "✔ Test server updated and running on :8000"
echo "  Auth: ON (DEV_SKIP_AUTH=false) — users must log in"
