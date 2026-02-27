# Copilot Instructions — Frontier: Sol 2000

## Project Context

A **High Frontier / Terra Invicta**-inspired space-logistics game with UI elements modeled after **Eve Online** (data-dense tables, inventory management, fitting windows, market-style catalogs). The game simulates near-future orbital mechanics, ship building, and resource logistics in the Earth–Moon system.

## Architecture Overview

Single-container **FastAPI + SQLite** space-logistics game server. All backend logic lives in flat Python files at the repo root; there is no package structure.

| File | Role |
|---|---|
| `main.py` | Startup logic, Pydantic models, game state endpoints, solar-system expansion |
| `fleet_router.py` | Ship fleet & transfer endpoints (`/api/fleet/*`, `/api/transfer/*`) |
| `shipyard_router.py` | Ship building endpoints (`/api/shipyard/*`) |
| `inventory_router.py` | Location/ship inventory transfer endpoints (`/api/inventory/*`) |
| `location_router.py` | Location listing & dynamic position endpoints (`/api/locations`) |
| `catalog_router.py` | Item catalog browsing endpoints (`/api/catalog/*`) |
| `industry_router.py` | Equipment deployment & production job endpoints (`/api/industry/*`) |
| `industry_service.py` | Production job logic, power balance, settle-on-access |
| `org_router.py` | Organization, research, marketplace endpoints (`/api/org/*`) |
| `org_service.py` | Organization settlement, income, loan logic |
| `admin_game_router.py` | Admin game-management endpoints (`/api/admin/*`) |
| `catalog_service.py` | Item/recipe/resource catalog loading from `items/` JSON trees; ship stat calculations |
| `sim_service.py` | Accelerated game clock (default 48× real-time), pause/resume, epoch reset |
| `celestial_config.py` | Parses `config/celestial_config.json` → locations, transfer edges, body state vectors, gravitational parameters |
| `lambert.py` | Pure-math Lambert solver (universal variable method) for two-point boundary value orbital transfers |
| `transfer_planner.py` | Patched-conic interplanetary transfer planner — Lambert + SOI burns, porkchop plot grid computation, departure window scanning |
| `constants.py` | Shared constants (game epoch, physics values) |
| `auth_service.py` | Session-cookie auth, password hashing, `require_login`/`require_admin` guards |
| `auth_repository.py` | Raw SQL helpers for the `users` table |
| `auth_router.py` | `/api/auth/*` and `/api/admin/accounts/*` routes (FastAPI `APIRouter`) |
| `db.py` | `connect_db()` returning `sqlite3.Connection` with `row_factory=Row`, FK enforcement |
| `db_migrations.py` | Ordered migration list applied at startup; each migration is a Python function |

## Key Conventions

### Database access
- **No ORM.** All queries are raw SQL via `sqlite3`. Connections are opened/closed per request in try/finally blocks.
- Migrations use `_safe_add_column()` for idempotent ALTER TABLE. New schema changes go in `db_migrations.py` as a new `Migration(migration_id="NNNN_name", ...)` entry appended to the `MIGRATIONS` list.
- `db/schema.sql` and `db/seed.sql` are the **rendered-bodies/routes schema** (used for the orbital map), separate from the game tables created by migrations.

### Item catalog (JSON-driven)
- Items are defined as JSON files under `items/`, organized by type:
  - `items/thrusters/<family>/` — `family.json` + `main/*.json` + `upgrades/*.json`
  - `items/reactors/<type>/` — `family.json` + individual reactor JSON files
  - `items/generators/<type>/`, `items/radiators/<type>/` — same pattern
  - `items/Resources/*.json` — one file per resource (`id`, `name`, `type`, `category_id`, `mass_per_m3_kg`)
  - `items/Recipes/*.json` — one file per crafting recipe (see `items/Recipes/README.md` for schema)
  - `items/Storage/*.json` — storage containers
- Catalogs are loaded at startup into in-memory dicts via `catalog_service.py` functions (`load_thruster_main_catalog()`, `load_resource_catalog()`, `load_storage_catalog()`).

### API patterns
- Routes follow `/api/<domain>/<action>` convention. Admin-only routes live under `/api/admin/`.
- Auth guards: call `require_login(conn, request)` or `require_admin(conn, request)` at the top of handlers.
- All API responses return `dict` (auto-serialized to JSON by FastAPI). Error cases raise `HTTPException`.

### Frontend
- Static HTML pages served from `static/` — no build step, no framework.
- Client JS in `static/js/` with per-page modules (`fleet.js`, `shipyard.js`, `research.js`, etc.).
- Rendering uses **PixiJS** (`pixi.min.js`) for the orbital map canvas.
- Pages check auth client-side via `/api/auth/me`; server also redirects unauthenticated users to `/login`.
- **Item display**: All items (parts, resources, cargo) render as Eve Online-style grid cells via the shared `ItemDisplay` library in `static/js/item_display.js`. See `docs/ui-item-display-system.md` for the full specification, including icon generation, tooltip system, CSS classes, backend data contract, and integration patterns. **All new item-bearing UI must use this system.**

## Development Workflow

```bash
# Build and run (Docker Compose)
sudo docker compose up -d --build

# Server runs at http://localhost:8000
# Default admin login: admin / admin

# Spawn test ships for development
./spawn_test_ships.sh

# SQLite DB is persisted in ./data/game.db (mounted as /data in container)
# To inspect: sqlite3 data/game.db
```

## Game-Specific Domain Knowledge

- **Simulation clock**: Game time runs at `GAME_TIME_SCALE` (default 48×). Ship transfers use game-time for departure/arrival. The clock can be paused/reset by admins.
- **Ship transfers**: Ships move between locations (LEO, GEO, Luna, L1–L5, etc.) consuming delta-v and fuel. Local orbit changes (same body) use Hohmann estimates from static `transfer_edges`. Interplanetary transfers use a **Lambert solver** (`lambert.py` + `transfer_planner.py`) that sweeps multiple time-of-flight candidates to find the best Δv at the current departure time. A **porkchop plot** endpoint (`/api/transfer/porkchop`) scans departure date × TOF grids for optimal transfer windows.
- **Ship builds**: Players select parts from the catalog → `/api/shipyard/preview` returns computed stats (dry mass, fuel capacity, ISP, thrust) → `/api/shipyard/build` creates the ship.
- **Inventory**: Location-based and ship-based inventory stacks tracked in `location_inventory_stacks`. Resources transfer between ships and locations.
- **Item categories** have aliases (e.g., `"engines"` → `"thruster"`, `"propellant"` → `"fuel"`) defined in `ITEM_CATEGORY_ALIASES` for flexible API lookups.

## Adding New Content

- **New resource**: Add a JSON file to `items/Resources/` following existing schema (`id`, `name`, `type: "resource"`, `category_id`, `mass_per_m3_kg`).
- **New recipe**: Add a JSON file to `items/Recipes/` following `recipe_template.json`.
- **New thruster family**: Create a subdirectory under `items/thrusters/` with `family.json`, `main/` and `upgrades/` dirs.
- **New migration**: Append to `MIGRATIONS` list in `db_migrations.py` with a sequential ID like `0004_description`.
