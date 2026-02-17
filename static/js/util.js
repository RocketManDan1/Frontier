window.Util = (function () {
  function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }
  function hexToInt(hex) { return parseInt(hex.replace("#", "0x"), 16); }

  function distToSegment(p, a, b) {
    const abx = b.x - a.x, aby = b.y - a.y;
    const apx = p.x - a.x, apy = p.y - a.y;
    const ab2 = abx*abx + aby*aby;
    let t = ab2 === 0 ? 0 : (apx*abx + apy*aby)/ab2;
    t = clamp(t, 0, 1);
    const cx = a.x + t*abx, cy = a.y + t*aby;
    const dx = p.x - cx, dy = p.y - cy;
    return Math.sqrt(dx*dx + dy*dy);
  }

  // Kepler solver (E from M,e)
  function keplerSolveE(M, e) {
    let E = M + e * Math.sin(M);
    for (let i = 0; i < 7; i++) {
      const f = E - e*Math.sin(E) - M;
      const fp = 1 - e*Math.cos(E);
      E -= f / fp;
    }
    return E;
  }

  function trueAnomalyAndR(M, e, aKm) {
    const Ean = keplerSolveE(M, e);
    const sinv = Math.sqrt(1 + e) * Math.sin(Ean / 2);
    const cosv = Math.sqrt(1 - e) * Math.cos(Ean / 2);
    const nu = 2 * Math.atan2(sinv, cosv);
    const r = aKm * (1 - e * Math.cos(Ean));
    return { nu, r };
  }

  function rotateXY(x, y, theta) {
    const c = Math.cos(theta), s = Math.sin(theta);
    return { x: c*x - s*y, y: s*x + c*y };
  }

  return { clamp, hexToInt, distToSegment, trueAnomalyAndR, rotateXY };
})();
