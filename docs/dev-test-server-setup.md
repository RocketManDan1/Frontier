# Dev / Test Server Setup

## Overview

The project runs two independent Docker containers from the same `docker-compose.yml`, each with its own source tree and database. This separation lets development happen freely on the **Dev** server without risking the game state that testers are actively playing on the **Test** server.

| | Test Server | Dev Server |
|---|---|---|
| **Container** | `frontier-test` | `frontier-dev` |
| **Host port** | `8000` | `8001` |
| **Source directory** | `/home/user/docker/frontier-sol-2000-test` (frozen copy) | `/home/user/docker/Frontier: Sol 2000` (working copy) |
| **Data directory** | `/home/user/docker/frontier-sol-2000-data` → `/data` | `/home/user/docker/frontier-sol-2000-data-dev` → `/data` |
| **`ENV_LABEL`** | `TEST` | `DEV` |
| **`DEV_SKIP_AUTH`** | `false` (full login required) | `true` (auth bypassed, all requests act as admin) |
| **Purpose** | Stable build for testers / players | Active development, experiments, breaking changes |

## Directory Layout on Host

```
/home/user/docker/
├── Frontier: Sol 2000/             # Dev source (working copy, git-tracked)
│   ├── docker-compose.yml          # Defines BOTH containers
│   ├── promote-to-test.sh          # Copies dev → test and rebuilds
│   └── …                           # All source files
├── frontier-sol-2000-test/         # Test source (frozen snapshot, NOT git-tracked)
├── frontier-sol-2000-data/         # Test SQLite DB + persistent data
└── frontier-sol-2000-data-dev/     # Dev SQLite DB + persistent data
```

### Key points

- **Only one git repo** exists — the working copy at `Frontier: Sol 2000/`. The test source directory is a plain file copy with no `.git`.
- **Databases are completely separate.** Changes to dev data (spawning test ships, resetting state, running migrations) never affect the test database.
- The `docker-compose.yml` that orchestrates both containers lives in the dev source directory.

## Promotion Workflow (Dev → Test)

When a dev build is ready for testers:

```bash
./promote-to-test.sh
```

This script:

1. **Wipes** the old test source at `/home/user/docker/frontier-sol-2000-test/`.
2. **Copies** the entire dev working tree into that directory (excluding `.git`, `__pycache__`, and `data/`).
3. **Rebuilds and restarts** only the `frontier-test` container via `docker compose up -d --build frontier-test`.

> **Important:** Promotion replaces the test *code* only — the test *database* is preserved. Schema migrations run automatically on container startup, so new migrations included in the promoted code will apply to the test DB on next boot.

## Environment Variables

### `ENV_LABEL` (`TEST` | `DEV` | *empty*)

Controls a coloured banner rendered at the top of every page so users can immediately distinguish which server they are on:

- **`DEV`** → red banner: `▸ DEV SERVER ◂`
- **`TEST`** → orange banner: `▸ TEST SERVER ◂`
- Empty / unset → no banner (production mode).

The label is also prepended to the browser tab title (e.g., `[DEV] Fleet`). The banner is fetched from `/api/server/info` and injected client-side by `static/js/auth.js`.

### `DEV_SKIP_AUTH` (`true` | `false`)

When `true`, all `require_login()` / `require_admin()` guards are bypassed and every request acts as a built-in admin user. This is **enabled on Dev** for convenience and **disabled on Test** so testers use real accounts.

### `EARTHMOON_PURGE_TEST_SHIPS_ON_STARTUP` (`true` | `false`)

When `true`, ships tagged as test/sandbox are purged on server startup. Set to `false` on the Dev server to preserve spawned test ships across restarts.

## Common Operations

### Rebuild both servers

```bash
cd "/home/user/docker/Frontier: Sol 2000"
sudo docker compose up -d --build
```

### Rebuild only the dev server (after code changes)

```bash
sudo docker compose up -d --build frontier-dev
```

### Promote dev to test

```bash
./promote-to-test.sh
```

### Run the test suite (inside dev container)

```bash
sudo docker compose exec frontier-dev bash -c "./run_tests.sh"
```

### Spawn test ships on dev

```bash
SERVICE=frontier-dev ./spawn_test_ships.sh
```

### Inspect databases

```bash
# Test DB
sqlite3 /home/user/docker/frontier-sol-2000-data/game.db

# Dev DB
sqlite3 /home/user/docker/frontier-sol-2000-data-dev/game.db
```

### View logs

```bash
sudo docker compose logs -f frontier-test    # Test server
sudo docker compose logs -f frontier-dev     # Dev server
```

## Copilot / AI Context Notes

When making changes to this project:

- **All code edits happen in the dev working copy** (`/home/user/docker/Frontier: Sol 2000/`). Never edit files directly in the test source directory.
- After editing, only the **Dev server** (`:8001`) reflects changes immediately after a rebuild. The Test server (`:8000`) is updated only when `promote-to-test.sh` is run.
- The dev server has **auth disabled** — API calls during development do not need session cookies or login.
- Both servers run the same Dockerfile and the same `main.py` entrypoint; only environment variables and the source snapshot differ.
- Database schema changes (new migrations in `db_migrations.py`) will apply to each server's database independently on startup.
