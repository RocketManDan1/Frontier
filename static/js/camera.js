window.Camera = (function () {
  const { clamp } = window.Util;
  const MIN_ZOOM = 0.001;
  const MAX_ZOOM = 60;
  const WHEEL_SENSITIVITY = 0.0015;

  function attachPanZoom(app, stageEl, world) {
    let dragging = false;
    let last = { x: 0, y: 0 };

    app.stage.interactive = true;
    app.stage.hitArea = app.screen;

    app.stage.on("pointerdown", (e) => {
      dragging = true;
      last = { x: e.data.global.x, y: e.data.global.y };
    });
    app.stage.on("pointerup", () => (dragging = false));
    app.stage.on("pointerupoutside", () => (dragging = false));
    app.stage.on("pointermove", (e) => {
      if (!dragging) return;
      const g = e.data.global;
      world.position.x += (g.x - last.x);
      world.position.y += (g.y - last.y);
      last = { x: g.x, y: g.y };
    });

    stageEl.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const wheelDeltaY = Number(ev.deltaY) || 0;
      if (wheelDeltaY === 0) return;
      const zoomFactor = Math.exp(-wheelDeltaY * WHEEL_SENSITIVITY);

      const oldScale = world.scale.x;
      const newScale = clamp(oldScale * zoomFactor, MIN_ZOOM, MAX_ZOOM);
      if (Math.abs(newScale - oldScale) < 1e-9) return;

      const rect = stageEl.getBoundingClientRect();
      const mouse = new PIXI.Point(ev.clientX - rect.left, ev.clientY - rect.top);

      const before = world.toLocal(mouse);
      world.scale.set(newScale);
      const after = world.toLocal(mouse);

      world.position.x += (after.x - before.x) * newScale;
      world.position.y += (after.y - before.y) * newScale;
    }, { passive: false });
  }

  return { attachPanZoom };
})();
