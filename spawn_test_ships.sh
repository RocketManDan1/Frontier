#!/usr/bin/env bash
set -euo pipefail

SERVICE="${SERVICE:-frontier-sol-2000}"
DB_PATH="${DB_PATH:-/app/data.db}"

COUNT="${1:-5}"              # how many
TAG="${TAG:-sandbox}"        # safety tag used for purge
PREFIX="${PREFIX:-Artemis}"  # name prefix
LOC="${LOC:-LEO}"            # location_id
COLOR="${COLOR:-#ffffff}"    # ship color
SIZE="${SIZE:-12}"           # size_px
STATUS="${STATUS:-docked}"   # docked / transit

docker compose exec -T "$SERVICE" python - <<'PY'
import os, sqlite3, time, json, sys

db_path = os.environ.get("DB_PATH", "/app/data.db")
count   = int(os.environ.get("COUNT", "5"))
tag     = os.environ.get("TAG", "sandbox")
prefix  = os.environ.get("PREFIX", "Artemis")
loc     = os.environ.get("LOC", "LEO")
color   = os.environ.get("COLOR", "#ffffff")
size_px = int(os.environ.get("SIZE", "12"))
status  = os.environ.get("STATUS", "docked")

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# table exists?
t = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ships'").fetchone()
if not t:
  print(f"[spawn] ERROR: no 'ships' table found in {db_path}")
  sys.exit(1)

cols = cur.execute("PRAGMA table_info(ships)").fetchall()
colnames = [c["name"] for c in cols]
required = [c["name"] for c in cols if c["notnull"] == 1 and c["dflt_value"] is None and c["pk"] == 0]

now = time.time()
created = 0
ids = []

def set_if_exists(d, k, v):
  if k in colnames:
    d[k] = v

for i in range(count):
  ship_id = f"test_{tag}_{int(now)}_{i+1}"
  name = f"TEST[{tag}] {prefix} {i+1}"

  row = {}
  set_if_exists(row, "id", ship_id)
  set_if_exists(row, "name", name)
  set_if_exists(row, "status", status)
  set_if_exists(row, "location_id", loc)

  # optional cosmetics
  set_if_exists(row, "color", color)
  set_if_exists(row, "size_px", size_px)
  set_if_exists(row, "shape", "triangle")  # if you later add this column

  # leave transit columns null if they exist
  for k in ("from_location_id","to_location_id","departed_at","arrives_at","dv_planned_m_s"):
    set_if_exists(row, k, None)

  # transfer path if present
  if "transfer_path" in colnames and "transfer_path" not in row:
    row["transfer_path"] = json.dumps([])

  # notes if present
  if "notes" in colnames and "notes" not in row:
    row["notes"] = json.dumps({"tag": tag, "spawned": now})

  # timestamps if present (some schemas use ints, some floats)
  for k in ("created_at","updated_at"):
    set_if_exists(row, k, now)

  # if schema requires columns we didn’t fill, bail with a helpful message
  missing_required = [k for k in required if k not in row]
  if missing_required:
    print("[spawn] ERROR: ships table has required columns we didn't populate:")
    print("  missing:", missing_required)
    print("  existing columns:", colnames)
    sys.exit(2)

  cols_sql = ", ".join(row.keys())
  qs = ", ".join(["?"] * len(row))
  vals = list(row.values())

  cur.execute(f"INSERT OR REPLACE INTO ships ({cols_sql}) VALUES ({qs})", vals)
  created += 1
  ids.append(ship_id)

conn.commit()
print(f"[spawn] Inserted {created} ships into {db_path}")
for sid in ids:
  print(" ", sid)
PY
