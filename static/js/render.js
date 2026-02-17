window.Render = (function () {
  const { hexToInt, distToSegment } = window.Util;

  function createUI() {
   const infoTitle = document.getElementById("infoTitle");
   let infoCoords = document.getElementById("infoCoords");
   const infoList = document.getElementById("infoList");

  // If index.html doesn't include #infoCoords, create it so render.js never crashes.
  if (!infoCoords && infoList && infoList.parentElement) {
    infoCoords = document.createElement("div");
    infoCoords.id = "infoCoords";
    infoCoords.className = "muted small";
    infoList.parentElement.insertBefore(infoCoords, infoList);
  }

  function setInfo(title, coordsText, bullets) {
    if (infoTitle) infoTitle.textContent = title;
    if (infoCoords) infoCoords.textContent = coordsText || "";

    if (infoList) {
      infoList.innerHTML = "";
      (bullets || []).forEach((b) => {
        const li = document.createElement("li");
        li.textContent = b;
        infoList.appendChild(li);
      });
    }
  }

  return { setInfo };
}


  function createPixi(stageEl) {
    const app = new PIXI.Application({
      resizeTo: stageEl,
      backgroundAlpha: 0,
      antialias: true,
      autoDensity: true,
      resolution: window.devicePixelRatio || 1,
    });
    stageEl.appendChild(app.view);

    const world = new PIXI.Container();
    app.stage.addChild(world);

    // center world
    world.position.set(app.renderer.width / 2, app.renderer.height / 2);

    const ringsGfx = new PIXI.Graphics();
    const routesGfx = new PIXI.Graphics();
    const nodesLayer = new PIXI.Container();
    const labelsLayer = new PIXI.Container();

    world.addChild(ringsGfx);
    world.addChild(routesGfx);
    world.addChild(nodesLayer);
    world.addChild(labelsLayer);

    return { app, world, ringsGfx, routesGfx, nodesLayer, labelsLayer };
  }

  function buildNodes(engineState, pixi, ui) {
    const nodes = new Map(); // id -> {container, body}
    pixi.nodesLayer.removeChildren();

    for (const [id, b] of engineState.bodies.entries()) {
      if (b.render_kind !== "node") continue;

      const c = new PIXI.Container();
      c.interactive = true;
      c.buttonMode = true;

      const dot = new PIXI.Graphics();
      const r = b.radius_px ?? 6;

      dot.lineStyle(1, 0xffffff, 0.25);
      dot.drawCircle(0, 0, r + 1);

      dot.beginFill(hexToInt(b.color || "#7aa2ff"), 0.85);
      dot.drawCircle(0, 0, r);
      dot.endFill();

      const label = new PIXI.Text(id, {
        fontFamily: "system-ui, sans-serif",
        fontSize: b.label_size ?? 12,
        fill: 0xe8e8e8,
        stroke: 0x000000,
        strokeThickness: 3,
      });
      label.anchor.set(0.5, 0);
      label.position.set(0, r + (b.label_offset ?? 4));

      c.addChild(dot, label);

      c.on("pointertap", () => {
        const p = engineState.pos.get(id) || { x_km: 0, y_km: 0 };
        ui.setInfo(b.title, `x=${p.x_km.toFixed(0)} km, y=${p.y_km.toFixed(0)} km`, b.notes || []);
      });

      pixi.nodesLayer.addChild(c);
      nodes.set(id, { container: c, body: b });
    }

    return nodes;
  }

  function drawFrame(engineState, pixi, nodes, ui) {
    const kmToPx = engineState.kmToPx;

    // Update node positions
    for (const [id, obj] of nodes.entries()) {
      const p = engineState.pos.get(id);
      if (!p) continue;
      obj.container.position.set(p.x_km * kmToPx, p.y_km * kmToPx);
    }

    // Draw rings (LEO/GEO)
    pixi.ringsGfx.clear();
    pixi.labelsLayer.removeChildren();

    // ring hit & label helpers
    const ringHitTargets = [];

    for (const [id, ring] of engineState.ring.entries()) {
      const b = engineState.bodies.get(id);
      const cx = ring.cx_km * kmToPx;
      const cy = ring.cy_km * kmToPx;

      const color = b?.color || "#e8e8e8";
      pixi.ringsGfx.lineStyle(2, hexToInt(color), 0.16);
      pixi.ringsGfx.drawCircle(cx, cy, ring.r_km * kmToPx);

      // label at top
      const t = new PIXI.Text(ring.label || id, {
        fontFamily: "system-ui, sans-serif",
        fontSize: 12,
        fill: hexToInt(color),
        stroke: 0x000000,
        strokeThickness: 3,
      });
      t.anchor.set(0.5, 1);
      t.position.set(cx, cy - ring.r_km * kmToPx - 6);
      pixi.labelsLayer.addChild(t);

      ringHitTargets.push({ id, cx, cy, r: ring.r_km * kmToPx, body: b });
    }

    // Draw routes
    pixi.routesGfx.clear();
    for (const r of engineState.routes) {
      const A = engineState.pos.get(r.a);
      const B = engineState.pos.get(r.b);
      if (!A || !B) continue;

      pixi.routesGfx.lineStyle(r.width_px || 3, hexToInt(r.color || "#89b4ff"), r.alpha ?? 0.45);
      pixi.routesGfx.moveTo(A.x_km * kmToPx, A.y_km * kmToPx);
      pixi.routesGfx.lineTo(B.x_km * kmToPx, B.y_km * kmToPx);
    }

    // Click handling for routes/rings on empty space
    pixi.app.stage.off("pointertap");
    pixi.app.stage.interactive = true;
    pixi.app.stage.hitArea = pixi.app.screen;

    pixi.app.stage.on("pointertap", (e) => {
      // If you clicked a node, node handler got it. Only handle stage clicks.
      if (e.target !== pixi.app.stage) return;

      const worldPoint = pixi.world.toLocal(e.data.global);
      const tol = 10 / pixi.world.scale.x;

      // route hit
      let best = null;
      for (const r of engineState.routes) {
        const A = engineState.pos.get(r.a);
        const B = engineState.pos.get(r.b);
        if (!A || !B) continue;

        const aPx = new PIXI.Point(A.x_km * kmToPx, A.y_km * kmToPx);
        const bPx = new PIXI.Point(B.x_km * kmToPx, B.y_km * kmToPx);

        const d = distToSegment(worldPoint, aPx, bPx);
        if (d <= tol && (!best || d < best.d)) best = { r, d };
      }
      if (best) {
        ui.setInfo(`Route: ${best.r.id}`, `Endpoints: ${best.r.a} ↔ ${best.r.b}`, best.r.notes || []);
        return;
      }

      // ring hit
      for (const rt of ringHitTargets) {
        const dx = worldPoint.x - rt.cx;
        const dy = worldPoint.y - rt.cy;
        const rr = Math.sqrt(dx*dx + dy*dy);
        if (Math.abs(rr - rt.r) <= tol) {
          ui.setInfo(rt.body?.title || rt.id, "", rt.body?.notes || []);
          return;
        }
      }
    });
  }

  return { createUI, createPixi, buildNodes, drawFrame };
})();
