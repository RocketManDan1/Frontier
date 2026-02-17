window.Ships = (function () {
  const { hexToInt } = window.Util;
  const SHIP_ICON_URL = "/static/img/mining-barge.png";
  const SHIP_PNG_FORWARD_OFFSET_RAD = Math.PI / 2;

  function makeFallbackGlyph(sizePx, colorInt) {
    const g = new PIXI.Graphics();
    const s = sizePx || 12;
    g.lineStyle(1, 0xffffff, 0.28);
    g.beginFill(colorInt, 0.22);
    g.moveTo(s * 1.1, 0);
    g.lineTo(-s * 0.75, -s * 0.55);
    g.lineTo(-s * 0.75, s * 0.55);
    g.lineTo(s * 1.1, 0);
    g.endFill();
    return g;
  }

  function makeShipGlyph(sizePx, colorHex) {
    const s = sizePx || 12;
    const colorInt = hexToInt(colorHex || "#ffffff");
    const targetH = Math.max(8, s * 1.2);
    const container = new PIXI.Container();
    const fallback = makeFallbackGlyph(s, colorInt);
    container.addChild(fallback);

    const texture = PIXI.Texture.from(SHIP_ICON_URL);
    const sprite = new PIXI.Sprite(texture);
    sprite.anchor.set(0.5);
    sprite.rotation = SHIP_PNG_FORWARD_OFFSET_RAD;
    sprite.alpha = 0.95;
    sprite.visible = false;

    const applySpriteSize = () => {
      const rawW = Number(texture.orig?.width) || Number(texture.width) || Number(texture.baseTexture?.realWidth) || 1;
      const rawH = Number(texture.orig?.height) || Number(texture.height) || Number(texture.baseTexture?.realHeight) || 1;
      const ratio = Math.max(0.05, rawW / Math.max(1, rawH));
      sprite.height = targetH;
      sprite.width = targetH * ratio;
    };

    const showSprite = () => {
      applySpriteSize();
      sprite.visible = true;
      fallback.visible = false;
    };
    const showFallback = () => {
      sprite.visible = false;
      fallback.visible = true;
    };

    if (texture.baseTexture?.valid) {
      showSprite();
    } else {
      texture.baseTexture?.once?.("loaded", showSprite);
      texture.baseTexture?.once?.("error", showFallback);
    }

    container.addChild(sprite);
    return container;
  }

  async function loadShips() {
    const resp = await fetch("/api/ships");
    const data = await resp.json();
    return data.ships || [];
  }

  function buildShipsLayer(pixi) {
    const layer = new PIXI.Container();
    pixi.world.addChild(layer);
    return layer;
  }

  function buildShipSprites(ships, layer, ui) {
    const map = new Map();
    for (const s of ships) {
      const c = new PIXI.Container();
      c.interactive = true;
      c.buttonMode = true;

      const shipGlyph = makeShipGlyph(s.size_px, s.color);
      c.addChild(shipGlyph);

      const label = new PIXI.Text(s.name, {
        fontFamily: "system-ui, sans-serif",
        fontSize: 11,
        fill: 0xe8e8e8,
        stroke: 0x000000,
        strokeThickness: 3,
      });
      label.anchor.set(0.5, 0);
      label.position.set(0, (s.size_px || 12) + 4);
      c.addChild(label);

      c.on("pointertap", () => {
        ui.setInfo(s.name, `Parked at: ${s.node_id || "?"}`, s.notes || []);
      });

      layer.addChild(c);
      map.set(s.id, { ship: s, container: c });
    }
    return map;
  }

  function updateShips(engineState, shipMap) {
    const kmToPx = engineState.kmToPx;
    for (const { ship, container } of shipMap.values()) {
      if (ship.location_type !== "node") continue; // PoC: node parking only
      const p = engineState.pos.get(ship.node_id);
      if (!p) continue;
      container.position.set(p.x_km * kmToPx, p.y_km * kmToPx);
    }
  }

  return { loadShips, buildShipsLayer, buildShipSprites, updateShips };
})();
