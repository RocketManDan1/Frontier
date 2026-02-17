-- Settings (JSON in value_json)
INSERT OR REPLACE INTO settings(key, value_json) VALUES
  ('km_to_px', '0.001'),
  ('sim_rate', '20000');  -- simulation seconds per real second (~2 min Luna orbit)

-- Bodies
INSERT OR REPLACE INTO bodies(id, title, kind, render_kind, mass_kg, color, radius_px, notes_json) VALUES
  ('Earth', 'Earth', 'primary', 'node', 5.972e24, '#4f8bff', 16, '["Earth (fixed origin)."]'),
  ('Luna', 'Luna', 'moon', 'node', 7.342e22, '#cfd8e3', 10, '["Luna (Kepler-ish 2D orbit; accelerated)."]'),

  ('LEO', 'Low Earth Orbit', 'marker', 'ring', NULL, '#e8e8e8', 0, '["Representative LEO ring (marker)."]'),
  ('GEO', 'Geostationary Orbit', 'marker', 'ring', NULL, '#ffd166', 0, '["Geostationary ring (marker)."]'),

  ('L1', 'Earth–Luna L1', 'lagrange', 'node', NULL, '#a78bfa', 5, '["Derived (CR3BP)."]'),
  ('L2', 'Earth–Luna L2', 'lagrange', 'node', NULL, '#a78bfa', 5, '["Derived (CR3BP)."]'),
  ('L3', 'Earth–Luna L3', 'lagrange', 'node', NULL, '#a78bfa', 5, '["Derived (CR3BP)."]'),
  ('L4', 'Earth–Luna L4', 'lagrange', 'node', NULL, '#34d399', 5, '["Derived (CR3BP)."]'),
  ('L5', 'Earth–Luna L5', 'lagrange', 'node', NULL, '#34d399', 5, '["Derived (CR3BP)."]');

-- Orbits
-- Earth fixed at origin
INSERT OR REPLACE INTO orbits(body_id, parent_body_id, model, params_json) VALUES
  ('Earth', NULL, 'fixed', '{"x_km":0,"y_km":0}');

-- Luna Kepler 2D around Earth
-- period_s ~ 27.321661 days (sidereal) = 2360591.5 s
INSERT OR REPLACE INTO orbits(body_id, parent_body_id, model, params_json) VALUES
  ('Luna', 'Earth', 'keplerian_2d', '{"a_km":384400,"e":0.0549,"period_s":2360591.5,"epoch_s":0,"M0":0}');

-- Ring markers around Earth
-- Earth radius 6378.137 km, LEO ~ +400 km, GEO +35786 km
INSERT OR REPLACE INTO orbits(body_id, parent_body_id, model, params_json) VALUES
  ('LEO', 'Earth', 'ring_marker', '{"radius_km":6778.137,"label":"LEO"}'),
  ('GEO', 'Earth', 'ring_marker', '{"radius_km":42164.137,"label":"GEO"}');

-- Derived nodes (CR3BP L points)
INSERT OR REPLACE INTO derived_nodes(body_id, model, params_json) VALUES
  ('L1', 'lagrange_cr3bp', '{"primary":"Earth","secondary":"Luna","point":"L1"}'),
  ('L2', 'lagrange_cr3bp', '{"primary":"Earth","secondary":"Luna","point":"L2"}'),
  ('L3', 'lagrange_cr3bp', '{"primary":"Earth","secondary":"Luna","point":"L3"}'),
  ('L4', 'lagrange_cr3bp', '{"primary":"Earth","secondary":"Luna","point":"L4"}'),
  ('L5', 'lagrange_cr3bp', '{"primary":"Earth","secondary":"Luna","point":"L5"}');

-- Routes (you can add/remove freely)
INSERT OR REPLACE INTO routes(id, a_body_id, b_body_id, notes_json) VALUES
  ('Earth↔Luna', 'Earth', 'Luna', '["Conceptual corridor."]'),
  ('Earth↔L1', 'Earth', 'L1', '[]'),
  ('Earth↔L2', 'Earth', 'L2', '[]'),
  ('Luna↔L1', 'Luna', 'L1', '[]'),
  ('Luna↔L2', 'Luna', 'L2', '[]'),
  ('Earth↔LEO', 'Earth', 'LEO', '["Ring endpoints use a default anchor point."]'),
  ('LEO↔GEO', 'LEO', 'GEO', '[]'),
  ('GEO↔L1', 'GEO', 'L1', '[]');
