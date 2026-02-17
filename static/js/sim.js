window.Sim = (function () {
  const { trueAnomalyAndR, rotateXY } = window.Util;

  // CR3BP helpers (compute normalized L points once per (primary,secondary) pair)
  function dOmega_dx(x, mu) {
    const r1 = Math.abs(x + mu);
    const r2 = Math.abs(x - (1 - mu));
    return x - (1 - mu) * (x + mu) / (r1*r1*r1) - mu * (x - (1 - mu)) / (r2*r2*r2);
  }

  function bisectRoot(f, a, b, iters=250, tol=1e-14) {
    let fa = f(a), fb = f(b);
    if (fa === 0) return a;
    if (fb === 0) return b;
    if (fa * fb > 0) throw new Error("Bisection interval does not bracket root");

    let lo=a, hi=b, flo=fa, fhi=fb;
    for (let i=0;i<iters;i++){
      const mid = (lo+hi)/2;
      const fmid = f(mid);
      if (Math.abs(fmid) < tol || Math.abs(hi-lo) < tol) return mid;
      if (flo * fmid <= 0) { hi=mid; fhi=fmid; }
      else { lo=mid; flo=fmid; }
    }
    return (lo+hi)/2;
  }

  // returns Earth-centered normalized coords for L points when secondary is at (1,0)
  function computeLNorm(mu, point) {
    const X_E = -mu;
    const X_M = 1 - mu;
    const f = (x)=>dOmega_dx(x, mu);
    const eps = 1e-6;

    let xb=0, yb=0;
    if (point === "L4") { xb = 0.5 - mu; yb = Math.sqrt(3)/2; }
    else if (point === "L5") { xb = 0.5 - mu; yb = -Math.sqrt(3)/2; }
    else if (point === "L1") xb = bisectRoot(f, X_E+eps, X_M-eps);
    else if (point === "L2") xb = bisectRoot(f, X_M+eps, 2.0);
    else if (point === "L3") xb = bisectRoot(f, -2.0, X_E-eps);
    else throw new Error("Unknown L point: " + point);

    // Earth-centered normalized: shift by Earth barycentric position
    const x_ec = xb - X_E;
    const y_ec = yb;
    return { x: x_ec, y: y_ec };
  }

  function buildStateEngine(config) {
    const bodies = new Map(config.bodies.map(b => [b.id, b]));
    const orbitByBody = new Map(config.orbits.map(o => [o.body_id, o]));
    const derived = config.derived_nodes || [];
    const routes = config.routes || [];
    const settings = config.settings || {};

    const kmToPx = settings.km_to_px ?? 0.001;
    const simRate = settings.sim_rate ?? 1.0;

    // Precompute L-norm cache for (primary,secondary,point)
    const lCache = new Map();
    function getLNorm(primaryId, secondaryId, point) {
      const key = `${primaryId}|${secondaryId}|${point}`;
      if (lCache.has(key)) return lCache.get(key);

      const m1 = bodies.get(primaryId)?.mass_kg;
      const m2 = bodies.get(secondaryId)?.mass_kg;
      if (!m1 || !m2) throw new Error("Missing masses for CR3BP pair");

      const mu = m2 / (m1 + m2);
      const norm = computeLNorm(mu, point);
      lCache.set(key, norm);
      return norm;
    }

    // positions: id -> {x_km,y_km, extra?}
    const pos = new Map();
    const ring = new Map(); // id -> {cx_km,cy_km,r_km,label}

    // topo-ish evaluation: do a few passes (good enough for small graphs)
    function computePositions(realNowS) {
      pos.clear();
      ring.clear();

      // 1) resolve orbit-model bodies
      for (let pass=0; pass<6; pass++) {
        for (const [id, b] of bodies.entries()) {
          const o = orbitByBody.get(id);
          if (!o) continue;

          const parentId = o.parent_body_id;
          const parent = parentId ? pos.get(parentId) : { x_km: 0, y_km: 0 };
          if (parentId && !pos.has(parentId)) continue;

          if (o.model === "fixed") {
            const x = o.params?.x_km ?? 0;
            const y = o.params?.y_km ?? 0;
            pos.set(id, { x_km: parent.x_km + x, y_km: parent.y_km + y });
          }

          if (o.model === "keplerian_2d") {
            const p = o.params || {};
            const a = p.a_km;
            const e = p.e ?? 0;
            const period = p.period_s;
            const epoch = p.epoch_s ?? 0;
            const M0 = p.M0 ?? 0;

            const tSim = (realNowS - epoch) * simRate;
            const n = 2*Math.PI / period;
            const M = (M0 + n * tSim) % (2*Math.PI);

            const { nu, r } = trueAnomalyAndR(M, e, a);
            const x = r * Math.cos(nu);
            const y = r * Math.sin(nu);

            pos.set(id, { x_km: parent.x_km + x, y_km: parent.y_km + y });
          }

          if (o.model === "ring_marker") {
            const p = o.params || {};
            const r_km = p.radius_km;
            const label = p.label ?? id;
            ring.set(id, { cx_km: parent.x_km, cy_km: parent.y_km, r_km, label });
            // Anchor point used for routes/labels (to the +X side)
            pos.set(id, { x_km: parent.x_km + r_km, y_km: parent.y_km });
          }
        }
      }

      // 2) derived nodes (L points)
      for (const d of derived) {
        if (d.model !== "lagrange_cr3bp") continue;
        const p = d.params || {};
        const primaryId = p.primary;
        const secondaryId = p.secondary;
        const point = p.point;

        const P = pos.get(primaryId);
        const S = pos.get(secondaryId);
        if (!P || !S) continue;

        const vx = S.x_km - P.x_km;
        const vy = S.y_km - P.y_km;
        const r = Math.sqrt(vx*vx + vy*vy);
        const theta = Math.atan2(vy, vx);

        const norm = getLNorm(primaryId, secondaryId, point);
        const local = { x: norm.x * r, y: norm.y * r };
        const rot = rotateXY(local.x, local.y, theta);

        pos.set(d.body_id, { x_km: P.x_km + rot.x, y_km: P.y_km + rot.y });
      }

      return { bodies, routes, pos, ring, kmToPx };
    }

    return { computePositions };
  }

  return { buildStateEngine };
})();
