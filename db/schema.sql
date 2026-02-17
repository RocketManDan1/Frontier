PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bodies (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'body',          -- informational
  render_kind TEXT NOT NULL DEFAULT 'node',   -- 'node' or 'ring'
  mass_kg REAL,                               -- needed for CR3BP derived nodes
  color TEXT NOT NULL DEFAULT '#7aa2ff',
  radius_px REAL NOT NULL DEFAULT 6,
  label_size INTEGER NOT NULL DEFAULT 12,
  label_offset REAL NOT NULL DEFAULT 4,
  notes_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS orbits (
  body_id TEXT PRIMARY KEY REFERENCES bodies(id) ON DELETE CASCADE,
  parent_body_id TEXT REFERENCES bodies(id) ON DELETE SET NULL,
  model TEXT NOT NULL,                        -- 'fixed', 'keplerian_2d', 'ring_marker'
  params_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS derived_nodes (
  body_id TEXT PRIMARY KEY REFERENCES bodies(id) ON DELETE CASCADE,
  model TEXT NOT NULL,                        -- 'lagrange_cr3bp'
  params_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS routes (
  id TEXT PRIMARY KEY,
  a_body_id TEXT NOT NULL REFERENCES bodies(id) ON DELETE CASCADE,
  b_body_id TEXT NOT NULL REFERENCES bodies(id) ON DELETE CASCADE,
  color TEXT NOT NULL DEFAULT '#89b4ff',
  width_px REAL NOT NULL DEFAULT 3,
  alpha REAL NOT NULL DEFAULT 0.45,
  notes_json TEXT NOT NULL DEFAULT '[]'
);
