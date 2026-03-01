/**
 * orbit_renderer.js — Client-side 2D orbital mechanics for Frontier: Sol 2000
 *
 * Provides:
 *   - solveKepler(M, e)            : Kepler equation solver
 *   - orbitPosition(orbit, mu, t)  : ship position from elements at game time
 *   - orbitVelocity(orbit, mu, t)  : ship velocity from elements at game time
 *   - drawOrbitEllipse(gfx, orbit, mu, bodyPos, toWorld, opts)
 *   - drawBurnMarker(gfx, pos, burn, toWorld, opts)
 *
 * All positions are in km (body-centric). The caller is responsible for
 * converting km → world coordinates via the toWorld() projection function.
 */

// ── Constants ──────────────────────────────────────────────
const DEG2RAD = Math.PI / 180;
const RAD2DEG = 180 / Math.PI;
const TWO_PI = 2 * Math.PI;
const KEPLER_MAX_ITER = 30;
const KEPLER_TOL = 1e-12;

// ── Kepler Equation Solver ────────────────────────────────

/**
 * Solve Kepler's equation M = E - e*sin(E) for elliptic orbits.
 * @param {number} M - Mean anomaly (radians)
 * @param {number} e - Eccentricity (0 <= e < 1)
 * @returns {number} Eccentric anomaly E (radians)
 */
function solveKeplerElliptic(M, e) {
  // Normalize M to [0, 2π)
  let Mn = M % TWO_PI;
  if (Mn < 0) Mn += TWO_PI;

  // Initial guess (Markley-style starter)
  let E = Mn + e * Math.sin(Mn) * (1 + e * Math.cos(Mn));

  // Newton-Raphson iteration
  for (let i = 0; i < KEPLER_MAX_ITER; i++) {
    const sinE = Math.sin(E);
    const cosE = Math.cos(E);
    const f = E - e * sinE - Mn;
    const fp = 1 - e * cosE;
    if (Math.abs(fp) < 1e-30) break;
    const dE = f / fp;
    E -= dE;
    if (Math.abs(dE) < KEPLER_TOL) break;
  }
  return E;
}

/**
 * Solve Kepler's equation M = e*sinh(H) - H for hyperbolic orbits.
 * @param {number} M - Mean anomaly (radians, can be any value)
 * @param {number} e - Eccentricity (e > 1)
 * @returns {number} Hyperbolic anomaly H
 */
function solveKeplerHyperbolic(M, e) {
  // Initial guess
  let H = M > 0 ? Math.log(2 * M / e + 1.8) : -Math.log(-2 * M / e + 1.8);

  for (let i = 0; i < KEPLER_MAX_ITER; i++) {
    const sinhH = Math.sinh(H);
    const coshH = Math.cosh(H);
    const f = e * sinhH - H - M;
    const fp = e * coshH - 1;
    if (Math.abs(fp) < 1e-30) break;
    const dH = f / fp;
    H -= dH;
    if (Math.abs(dH) < KEPLER_TOL) break;
  }
  return H;
}

/**
 * Unified Kepler solver — dispatches to elliptic or hyperbolic.
 * @param {number} M - Mean anomaly (radians)
 * @param {number} e - Eccentricity
 * @returns {number} Eccentric anomaly (E for elliptic, H for hyperbolic)
 */
function solveKepler(M, e) {
  if (e < 1.0) return solveKeplerElliptic(M, e);
  return solveKeplerHyperbolic(M, e);
}

// ── Orbital Mechanics ─────────────────────────────────────

/**
 * Compute mean motion (rad/s) from semi-major axis and mu.
 * @param {number} a_km - Semi-major axis (km). Must be positive for elliptic.
 * @param {number} mu   - Gravitational parameter (km³/s²)
 * @returns {number} Mean motion (rad/s)
 */
function meanMotion(a_km, mu) {
  const a = Math.abs(a_km);
  if (a < 1e-10) return 0;
  return Math.sqrt(mu / (a * a * a));
}

/**
 * Compute orbital period (s) for an elliptic orbit.
 */
function orbitalPeriod(a_km, mu) {
  const n = meanMotion(a_km, mu);
  return n > 0 ? TWO_PI / n : Infinity;
}

/**
 * Compute the true anomaly from eccentric anomaly.
 * @param {number} E - Eccentric (or hyperbolic) anomaly
 * @param {number} e - Eccentricity
 * @returns {number} True anomaly (radians)
 */
function trueAnomaly(E, e) {
  if (e < 1.0) {
    // Elliptic
    return 2 * Math.atan2(
      Math.sqrt(1 + e) * Math.sin(E / 2),
      Math.sqrt(1 - e) * Math.cos(E / 2)
    );
  } else {
    // Hyperbolic
    return 2 * Math.atan2(
      Math.sqrt(e + 1) * Math.sinh(E / 2),
      Math.sqrt(e - 1) * Math.cosh(E / 2)
    );
  }
}

/**
 * Compute position (km) and optionally velocity (km/s) relative to the
 * central body at a given game time.
 *
 * @param {Object} orbit - Orbital elements:
 *   { body_id, a_km, e, omega_deg, M0_deg, epoch_s, direction }
 * @param {number} mu - Gravitational parameter of the central body (km³/s²)
 * @param {number} gameTime - Current game time (seconds, same epoch as orbit.epoch_s)
 * @returns {{ x: number, y: number, vx?: number, vy?: number }}
 *   Position in km relative to central body. Velocity in km/s if orbit is valid.
 */
function orbitPosition(orbit, mu, gameTime) {
  const a = orbit.a_km;
  const e = Math.max(0, orbit.e || 0);
  const omega = (orbit.omega_deg || 0) * DEG2RAD;
  const M0 = (orbit.M0_deg || 0) * DEG2RAD;
  const epoch = orbit.epoch_s || 0;
  const dir = orbit.direction || 1;

  const n = meanMotion(a, mu);
  const dt = gameTime - epoch;
  const M = M0 + dir * n * dt;

  const E = solveKepler(M, e);
  const nu = trueAnomaly(E, e);

  // Radius
  let r;
  if (e < 1.0) {
    r = a * (1 - e * Math.cos(E));
  } else {
    const p = Math.abs(a) * (e * e - 1);
    r = p / (1 + e * Math.cos(nu));
  }

  // Position in orbital plane rotated by argument of periapsis
  const cosNuOmega = Math.cos(nu + omega);
  const sinNuOmega = Math.sin(nu + omega);

  return {
    x: r * cosNuOmega,
    y: r * sinNuOmega,
  };
}

/**
 * Compute the true anomaly (rad) at a given game time.
 * @param {Object} orbit - Orbital elements
 * @param {number} mu - Gravitational parameter (km³/s²)
 * @param {number} gameTime - Game time (seconds)
 * @returns {number} True anomaly in radians
 */
function trueAnomalyAtTime(orbit, mu, gameTime) {
  const a = orbit.a_km;
  const e = Math.max(0, orbit.e || 0);
  const M0 = (orbit.M0_deg || 0) * DEG2RAD;
  const epoch = orbit.epoch_s || 0;
  const dir = orbit.direction || 1;

  const n = meanMotion(a, mu);
  const dt = gameTime - epoch;
  const M = M0 + dir * n * dt;

  const E = solveKepler(M, e);
  return trueAnomaly(E, e);
}

/**
 * Compute the game time corresponding to a given true anomaly.
 * This is the INVERSE of trueAnomalyAtTime().
 * 
 * @param {Object} orbit - Orbital elements
 * @param {number} mu - Gravitational parameter (km³/s²)
 * @param {number} nu - True anomaly (radians)
 * @returns {number} Game time (seconds)
 */
function timeFromTrueAnomaly(orbit, mu, nu) {
  const a = orbit.a_km;
  const e = Math.max(0, orbit.e || 0);
  const M0 = (orbit.M0_deg || 0) * DEG2RAD;
  const epoch = orbit.epoch_s || 0;
  const dir = orbit.direction || 1;

  const n = meanMotion(a, mu);
  if (n < 1e-20) return epoch;

  let E;
  if (e < 1.0) {
    // Elliptic: true anomaly → eccentric anomaly
    E = Math.atan2(
      Math.sqrt(1 - e * e) * Math.sin(nu),
      e + Math.cos(nu)
    );
  } else {
    // Hyperbolic
    E = 2 * Math.atanh(
      Math.sqrt((e - 1) / (e + 1)) * Math.tan(nu / 2)
    );
  }

  let M;
  if (e < 1.0) {
    M = E - e * Math.sin(E);
  } else {
    M = e * Math.sinh(E) - E;
  }

  const dt = (M - M0) / (dir * n);
  return epoch + dt;
}

/**
 * Find the closest point on an orbit arc to a given world position.
 * Returns { nu, dist, wx, wy, gameTime } or null if no point is close enough.
 *
 * @param {Object} orbit - Orbital elements
 * @param {number} mu - Gravitational parameter (km³/s²)
 * @param {{ rx: number, ry: number }} bodyWorldPos - Body center in world coords
 * @param {Function} kmToWorld - (dxKm, dyKm, bodyId) → { wx, wy }
 * @param {number} mouseWx - Mouse world X
 * @param {number} mouseWy - Mouse world Y
 * @param {Object} [opts]
 * @param {number} [opts.startNu] - Start true anomaly
 * @param {number} [opts.endNu]   - End true anomaly
 * @param {number} [opts.numSamples=64] - Number of sample points
 * @returns {{ nu: number, dist: number, wx: number, wy: number, gameTime: number }|null}
 */
function closestArcPoint(orbit, mu, bodyWorldPos, kmToWorld, mouseWx, mouseWy, opts = {}) {
  const a = orbit.a_km;
  const e = Math.max(0, orbit.e || 0);
  const omega = (orbit.omega_deg || 0) * DEG2RAD;
  const numSamples = opts.numSamples || 64;
  const startNu = opts.startNu !== undefined ? opts.startNu : 0;
  const endNu = opts.endNu !== undefined ? opts.endNu : TWO_PI;
  const span = endNu - startNu;
  if (Math.abs(span) < 0.001) return null;

  let bestDist = Infinity;
  let bestNu = 0;
  let bestWx = 0, bestWy = 0;

  for (let i = 0; i <= numSamples; i++) {
    const nu = startNu + (i / numSamples) * span;
    let r;
    if (e < 1.0) {
      r = a * (1 - e * e) / (1 + e * Math.cos(nu));
    } else {
      const p = Math.abs(a) * (e * e - 1);
      r = p / (1 + e * Math.cos(nu));
      if (r <= 0) continue;
    }
    const px = r * Math.cos(nu + omega);
    const py = r * Math.sin(nu + omega);
    const w = kmToWorld(px, py, orbit.body_id);
    const wx = bodyWorldPos.rx + w.wx;
    const wy = bodyWorldPos.ry + w.wy;
    const dx = wx - mouseWx;
    const dy = wy - mouseWy;
    const d = Math.sqrt(dx * dx + dy * dy);
    if (d < bestDist) {
      bestDist = d;
      bestNu = nu;
      bestWx = wx;
      bestWy = wy;
    }
  }

  const gameTime = timeFromTrueAnomaly(orbit, mu, bestNu);
  return { nu: bestNu, dist: bestDist, wx: bestWx, wy: bestWy, gameTime };
}

/**
 * Generate an array of points along an orbit for rendering.
 *
 * @param {Object} orbit - Orbital elements
 * @param {number} mu - Gravitational parameter (km³/s²)
 * @param {number} numPoints - Number of points to generate (default 128)
 * @param {Object} [opts] - Options
 * @param {number} [opts.startNu] - Start true anomaly (radians, default 0)
 * @param {number} [opts.endNu]   - End true anomaly   (radians, default 2π)
 * @returns {Array<{x: number, y: number}>} Points in km, body-centric
 */
function orbitPoints(orbit, mu, numPoints = 128, opts = {}) {
  const a = orbit.a_km;
  const e = Math.max(0, orbit.e || 0);
  const omega = (orbit.omega_deg || 0) * DEG2RAD;

  const points = [];

  if (e < 1.0) {
    // Elliptic — sweep full orbit or partial
    const startNu = opts.startNu !== undefined ? opts.startNu : 0;
    const endNu = opts.endNu !== undefined ? opts.endNu : TWO_PI;
    const span = endNu - startNu;

    for (let i = 0; i <= numPoints; i++) {
      const nu = startNu + (i / numPoints) * span;
      const r = a * (1 - e * e) / (1 + e * Math.cos(nu));
      points.push({
        x: r * Math.cos(nu + omega),
        y: r * Math.sin(nu + omega),
      });
    }
  } else {
    // Hyperbolic — only draw the near-periapsis arc
    const nuMax = Math.acos(-1 / e) * 0.95; // stay inside asymptote
    const startNu = opts.startNu !== undefined ? opts.startNu : -nuMax;
    const endNu = opts.endNu !== undefined ? opts.endNu : nuMax;
    const span = endNu - startNu;

    for (let i = 0; i <= numPoints; i++) {
      const nu = startNu + (i / numPoints) * span;
      const p = Math.abs(a) * (e * e - 1);
      const r = p / (1 + e * Math.cos(nu));
      if (r <= 0) continue;
      points.push({
        x: r * Math.cos(nu + omega),
        y: r * Math.sin(nu + omega),
      });
    }
  }

  return points;
}

/**
 * Draw an orbital ellipse/hyperbola onto a PIXI.Graphics object.
 *
 * @param {PIXI.Graphics} gfx - Graphics to draw on (should be pre-cleared)
 * @param {Object} orbit      - Orbital elements
 * @param {number} mu         - Gravitational parameter (km³/s²)
 * @param {{ rx: number, ry: number }} bodyWorldPos - Body center in world coords
 * @param {Function} kmToWorld - (dxKm, dyKm, bodyId) → { wx, wy } world offset
 * @param {Object} [opts]     - Drawing options
 * @param {number} [opts.color=0xffffff] - Line color
 * @param {number} [opts.alpha=0.3]      - Line alpha
 * @param {number} [opts.lineWidth=1]    - Line width in world units
 * @param {boolean} [opts.dashed=false]  - Draw dashed line
 * @param {number} [opts.numPoints=128]  - Point count
 */
function drawOrbitPath(gfx, orbit, mu, bodyWorldPos, kmToWorld, opts = {}) {
  const color = opts.color !== undefined ? opts.color : 0xffffff;
  const alpha = opts.alpha !== undefined ? opts.alpha : 0.3;
  const lineWidth = opts.lineWidth !== undefined ? opts.lineWidth : 1;
  const numPoints = opts.numPoints || 128;

  const points = orbitPoints(orbit, mu, numPoints, opts);
  if (points.length < 2) return;

  gfx.lineStyle(lineWidth, color, alpha);

  let started = false;
  for (let i = 0; i < points.length; i++) {
    const w = kmToWorld(points[i].x, points[i].y, orbit.body_id);
    const wx = bodyWorldPos.rx + w.wx;
    const wy = bodyWorldPos.ry + w.wy;

    if (opts.dashed && i % 4 >= 2) {
      started = false;
      continue;
    }

    if (!started) {
      gfx.moveTo(wx, wy);
      started = true;
    } else {
      gfx.lineTo(wx, wy);
    }
  }
}

/**
 * Compute the world-coordinate position of a ship from its orbital elements.
 *
 * @param {Object} orbit          - Orbital elements from the server
 * @param {number} mu             - Gravitational parameter (km³/s²)
 * @param {number} gameTime       - Current game time (s)
 * @param {{ rx: number, ry: number }} bodyWorldPos - Body center in world coords
 * @param {Function} kmToWorld    - (dxKm, dyKm, bodyId) → { wx, wy }
 * @returns {{ wx: number, wy: number, angle: number }}
 */
function shipWorldPosition(orbit, mu, gameTime, bodyWorldPos, kmToWorld) {
  const pos = orbitPosition(orbit, mu, gameTime);
  const w = kmToWorld(pos.x, pos.y, orbit.body_id);

  // Compute heading from a slightly future position
  const dtSmall = 0.5; // half second
  const pos2 = orbitPosition(orbit, mu, gameTime + dtSmall);
  const w2 = kmToWorld(pos2.x, pos2.y, orbit.body_id);

  const dx = w2.wx - w.wx;
  const dy = w2.wy - w.wy;
  const angle = Math.atan2(dy, dx);

  return {
    wx: bodyWorldPos.rx + w.wx,
    wy: bodyWorldPos.ry + w.wy,
    angle,
  };
}

// ── Orbital Parameter Utilities ────────────────────────────

/**
 * Compute apoapsis distance (km) from orbital elements.
 * Returns Infinity for hyperbolic orbits (e >= 1).
 */
function computeApoapsis(orbit) {
  if (!orbit || orbit.e >= 1.0) return Infinity;
  return orbit.a_km * (1 + orbit.e);
}

/**
 * Compute periapsis distance (km) from orbital elements.
 */
function computePeriapsis(orbit) {
  if (!orbit) return 0;
  if (orbit.e >= 1.0) {
    // Hyperbolic: periapsis = |a| * (e - 1)
    return Math.abs(orbit.a_km) * (orbit.e - 1);
  }
  return orbit.a_km * (1 - orbit.e);
}

/**
 * Compute apoapsis altitude (km) = apoapsis distance - body radius.
 * @param {Object} orbit - Orbital elements
 * @param {number} bodyRadius - Body radius in km (default 0)
 */
function computeApoapsisAlt(orbit, bodyRadius = 0) {
  return computeApoapsis(orbit) - bodyRadius;
}

/**
 * Compute periapsis altitude (km) = periapsis distance - body radius.
 * @param {Object} orbit - Orbital elements
 * @param {number} bodyRadius - Body radius in km (default 0)
 */
function computePeriapsisAlt(orbit, bodyRadius = 0) {
  return computePeriapsis(orbit) - bodyRadius;
}

/**
 * Format a distance in km for display.
 * Returns e.g. "185 km", "1,234 km", "384,400 km"
 */
function formatDistanceKm(km) {
  if (km === Infinity || km === -Infinity) return "∞";
  if (km == null || isNaN(km)) return "—";
  return Math.round(km).toLocaleString() + " km";
}

/**
 * Format a time duration in seconds to human readable.
 * Returns e.g. "2h 15m", "1d 6h 30m", "45m 12s"
 */
function formatDuration(seconds) {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;

  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

/**
 * Build a concise orbital info summary object.
 * @param {Object} orbit - Orbital elements
 * @param {number} mu - Gravitational parameter (km³/s²)
 * @param {number} [bodyRadiusKm] - Body radius for altitude calc
 * @returns {{ apoapsis: string, periapsis: string, period: string, ecc: string, sma: string }}
 */
function orbitSummary(orbit, mu, bodyRadiusKm = 0) {
  if (!orbit) return null;
  const apo = computeApoapsisAlt(orbit, bodyRadiusKm);
  const peri = computePeriapsisAlt(orbit, bodyRadiusKm);
  const T = orbit.e < 1.0 ? orbitalPeriod(orbit.a_km, mu) : Infinity;
  return {
    apoapsis: formatDistanceKm(apo),
    periapsis: formatDistanceKm(peri),
    apoapsisKm: apo,
    periapsisKm: peri,
    period: formatDuration(T),
    periodS: T,
    ecc: orbit.e.toFixed(4),
    sma: formatDistanceKm(orbit.a_km),
    isHyperbolic: orbit.e >= 1.0,
  };
}

/**
 * Compute the world positions of apoapsis and periapsis points on an orbit.
 * @param {Object} orbit      - Orbital elements
 * @param {{ rx: number, ry: number }} bodyWorldPos - Body center in world coords
 * @param {Function} kmToWorld - (dxKm, dyKm, bodyId) → { wx, wy }
 * @returns {{ ap: {wx,wy}|null, pe: {wx,wy} }}  null ap if hyperbolic
 */
function apsisWorldPositions(orbit, bodyWorldPos, kmToWorld) {
  if (!orbit) return { ap: null, pe: null };
  const a = orbit.a_km;
  const e = Math.max(0, orbit.e || 0);
  const omega = (orbit.omega_deg || 0) * DEG2RAD;

  // Periapsis is at true anomaly = 0 (by definition)
  const rPe = e < 1.0 ? a * (1 - e) : Math.abs(a) * (e - 1);
  const peKm = { x: rPe * Math.cos(omega), y: rPe * Math.sin(omega) };
  const peW = kmToWorld(peKm.x, peKm.y, orbit.body_id);
  const pe = { wx: bodyWorldPos.rx + peW.wx, wy: bodyWorldPos.ry + peW.wy };

  // Apoapsis is at true anomaly = π (only exists for elliptic)
  let ap = null;
  if (e < 1.0) {
    const rAp = a * (1 + e);
    const apKm = { x: rAp * Math.cos(omega + Math.PI), y: rAp * Math.sin(omega + Math.PI) };
    const apW = kmToWorld(apKm.x, apKm.y, orbit.body_id);
    ap = { wx: bodyWorldPos.rx + apW.wx, wy: bodyWorldPos.ry + apW.wy };
  }

  return { ap, pe };
}

/**
 * Draw Ap/Pe markers with altitude labels on a PIXI.Graphics object.
 * @param {PIXI.Graphics} gfx     - Graphics to draw on
 * @param {Object} orbit           - Orbital elements
 * @param {{ rx: number, ry: number }} bodyWorldPos - Body center in world coords
 * @param {Function} kmToWorld     - (dxKm, dyKm, bodyId) → { wx, wy }
 * @param {number} bodyRadiusKm    - Body radius for altitude display
 * @param {number} zoom            - Current camera zoom level
 * @param {Object} [opts]          - Options
 * @param {number} [opts.color=0xffffff]
 */
function drawApsisMarkers(gfx, orbit, bodyWorldPos, kmToWorld, bodyRadiusKm, zoom, opts = {}) {
  if (!orbit || orbit.e < 0.005) return; // Skip for near-circular orbits
  const color = opts.color !== undefined ? opts.color : 0xffffff;
  const { ap, pe } = apsisWorldPositions(orbit, bodyWorldPos, kmToWorld);
  const dotR = 3 / zoom;
  const lineW = 1.0 / zoom;

  // Periapsis marker (Pe)
  if (pe) {
    const peAlt = computePeriapsisAlt(orbit, bodyRadiusKm);
    gfx.lineStyle(lineW, color, 0.7);
    gfx.beginFill(color, 0.5);
    gfx.drawCircle(pe.wx, pe.wy, dotR);
    gfx.endFill();
    // Label rendered as a tiny crosshair + text added by caller if needed
  }

  // Apoapsis marker (Ap)
  if (ap) {
    const apoAlt = computeApoapsisAlt(orbit, bodyRadiusKm);
    gfx.lineStyle(lineW, color, 0.7);
    gfx.beginFill(color, 0.5);
    gfx.drawCircle(ap.wx, ap.wy, dotR);
    gfx.endFill();
  }
}

// ── Direction Arrows ───────────────────────────────────────

/**
 * Draw small chevron arrows along an orbit arc to indicate direction of travel.
 *
 * @param {PIXI.Graphics} gfx - Graphics to draw on
 * @param {Object} orbit      - Orbital elements
 * @param {number} mu         - Gravitational parameter (km³/s²)
 * @param {{ rx: number, ry: number }} bodyWorldPos - Body center in world coords
 * @param {Function} kmToWorld - (dxKm, dyKm, bodyId) → { wx, wy }
 * @param {Object} [opts]     - Drawing options
 * @param {number} [opts.color=0xffffff]
 * @param {number} [opts.alpha=0.5]
 * @param {number} [opts.lineWidth=1]
 * @param {number} [opts.numArrows=3]
 * @param {number} [opts.arrowSize] - Chevron size in world units (default lineWidth*5)
 * @param {number} [opts.startNu]   - Start true anomaly (radians)
 * @param {number} [opts.endNu]     - End true anomaly (radians)
 */
function drawDirectionArrows(gfx, orbit, mu, bodyWorldPos, kmToWorld, opts = {}) {
  const color = opts.color !== undefined ? opts.color : 0xffffff;
  const alpha = opts.alpha !== undefined ? opts.alpha : 0.5;
  const lineWidth = opts.lineWidth !== undefined ? opts.lineWidth : 1;
  const numArrows = opts.numArrows || 3;
  const arrowSize = opts.arrowSize || lineWidth * 5;

  const a = orbit.a_km;
  const e = Math.max(0, orbit.e || 0);
  const omega = (orbit.omega_deg || 0) * DEG2RAD;

  const startNu = opts.startNu !== undefined ? opts.startNu : 0;
  const endNu = opts.endNu !== undefined ? opts.endNu : TWO_PI;
  const span = endNu - startNu;
  if (Math.abs(span) < 0.01) return;

  for (let i = 1; i <= numArrows; i++) {
    const frac = i / (numArrows + 1);
    const nu = startNu + frac * span;

    // Radius at this true anomaly
    let r;
    if (e < 1.0) {
      r = a * (1 - e * e) / (1 + e * Math.cos(nu));
    } else {
      const p = Math.abs(a) * (e * e - 1);
      r = p / (1 + e * Math.cos(nu));
      if (r <= 0) continue;
    }

    const px = r * Math.cos(nu + omega);
    const py = r * Math.sin(nu + omega);
    const w = kmToWorld(px, py, orbit.body_id);
    const wx = bodyWorldPos.rx + w.wx;
    const wy = bodyWorldPos.ry + w.wy;

    // Tangent direction from a nearby point along the arc
    const dnu = 0.02;
    const nu2 = nu + dnu;
    let r2;
    if (e < 1.0) {
      r2 = a * (1 - e * e) / (1 + e * Math.cos(nu2));
    } else {
      const p2 = Math.abs(a) * (e * e - 1);
      r2 = p2 / (1 + e * Math.cos(nu2));
    }
    if (r2 <= 0) continue;
    const px2 = r2 * Math.cos(nu2 + omega);
    const py2 = r2 * Math.sin(nu2 + omega);
    const w2 = kmToWorld(px2, py2, orbit.body_id);
    const wx2 = bodyWorldPos.rx + w2.wx;
    const wy2 = bodyWorldPos.ry + w2.wy;

    const dx = wx2 - wx;
    const dy = wy2 - wy;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len < 1e-10) continue;

    const nx = dx / len;
    const ny = dy / len;

    // Chevron: two lines forming a ">" shape
    const tipX = wx + nx * arrowSize * 0.5;
    const tipY = wy + ny * arrowSize * 0.5;
    const leftX = wx - nx * arrowSize * 0.3 - ny * arrowSize * 0.35;
    const leftY = wy - ny * arrowSize * 0.3 + nx * arrowSize * 0.35;
    const rightX = wx - nx * arrowSize * 0.3 + ny * arrowSize * 0.35;
    const rightY = wy - ny * arrowSize * 0.3 - nx * arrowSize * 0.35;

    gfx.lineStyle(lineWidth * 0.8, color, alpha * 0.85);
    gfx.moveTo(leftX, leftY);
    gfx.lineTo(tipX, tipY);
    gfx.lineTo(rightX, rightY);
  }
}

// ── Connecting Lines ──────────────────────────────────────

/**
 * Draw a dashed connecting line between two world-coordinate positions.
 * Used to bridge visual discontinuities when reference body changes
 * between prediction segments.
 *
 * @param {PIXI.Graphics} gfx
 * @param {number} x1 - Start X (world coords)
 * @param {number} y1 - Start Y
 * @param {number} x2 - End X
 * @param {number} y2 - End Y
 * @param {Object} [opts]
 * @param {number} [opts.color=0x888888]
 * @param {number} [opts.alpha=0.4]
 * @param {number} [opts.lineWidth=1]
 */
function drawConnectingDash(gfx, x1, y1, x2, y2, opts = {}) {
  const color = opts.color || 0x888888;
  const alpha = opts.alpha || 0.4;
  const lineWidth = opts.lineWidth || 1;
  const dashLen = lineWidth * 8;
  const gapLen = lineWidth * 5;

  const dx = x2 - x1;
  const dy = y2 - y1;
  const dist = Math.sqrt(dx * dx + dy * dy);
  if (dist < 1e-10) return;

  const nx = dx / dist;
  const ny = dy / dist;

  gfx.lineStyle(lineWidth, color, alpha);

  let d = 0;
  while (d < dist) {
    const segEnd = Math.min(d + dashLen, dist);
    gfx.moveTo(x1 + nx * d, y1 + ny * d);
    gfx.lineTo(x1 + nx * segEnd, y1 + ny * segEnd);
    d = segEnd + gapLen;
  }
}

// ── Exports ───────────────────────────────────────────────
// Attach to window for use from app.js (no module bundler)

window.OrbitRenderer = {
  // Kepler
  solveKepler,
  solveKeplerElliptic,
  solveKeplerHyperbolic,

  // Mechanics
  meanMotion,
  orbitalPeriod,
  trueAnomaly,
  orbitPosition,
  orbitPoints,

  // Orbital parameters
  computeApoapsis,
  computePeriapsis,
  computeApoapsisAlt,
  computePeriapsisAlt,
  orbitSummary,
  apsisWorldPositions,
  drawApsisMarkers,

  // Formatting
  formatDistanceKm,
  formatDuration,

  // Time-to-anomaly
  trueAnomalyAtTime,
  timeFromTrueAnomaly,
  closestArcPoint,

  // Rendering
  drawOrbitPath,
  shipWorldPosition,
  drawDirectionArrows,
  drawConnectingDash,

  // Constants
  DEG2RAD,
  RAD2DEG,
};
