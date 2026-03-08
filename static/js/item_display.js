/**
 * Item Display — shared icon generation, tooltip, and grid-cell rendering.
 *
 * Provides:
 *   ItemDisplay.iconDataUri(seed, label, category)
 *   ItemDisplay.createGridCell(options)        → DOM element (square cell)
 *   ItemDisplay.showTooltip(anchorEl, item)    → show hover tooltip
 *   ItemDisplay.hideTooltip()                  → hide tooltip
 *   ItemDisplay.escapeHtml(v)
 *   ItemDisplay.fmtKg(v)
 *   ItemDisplay.fmtM3(v)
 */
window.ItemDisplay = (function () {
  "use strict";

  const iconCache = new Map();
  let tooltipEl = null;
  let tooltipRaf = 0;

  /* ── Helpers ───────────────────────────────────────────── */

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function hashCode(text) {
    let hash = 0;
    const source = String(text || "");
    for (let i = 0; i < source.length; i++) {
      hash = ((hash << 5) - hash + source.charCodeAt(i)) | 0;
    }
    return Math.abs(hash);
  }

  function fmtKg(value) {
    const v = Math.max(0, Number(value) || 0);
    if (v >= 5000) return `${(v / 1000).toFixed(1)} t`;
    return `${v.toFixed(0)} kg`;
  }

  function fmtM3(value) {
    return `${Math.max(0, Number(value) || 0).toFixed(2)} m³`;
  }

  function formatMinerType(value) {
    const key = String(value || "").trim().toLowerCase();
    if (key === "large_body" || key === "largebody") return "Largebody";
    if (key === "microgravity") return "Microgravity";
    if (key === "cryovolatile") return "Cryovolatile";
    if (!key) return "";
    return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  /* ── Category → visual mapping ─────────────────────────── */

  const CATEGORY_VISUALS = {
    thruster:           { hueBase: 5,   shape: "chevron",  badge: "T", badgeHue: 5   },
    reactor:            { hueBase: 140, shape: "hexagon",  badge: "R", badgeHue: 140 },
    generator:          { hueBase: 52,  shape: "diamond",  badge: "G", badgeHue: 52  },
    radiator:           { hueBase: 210, shape: "circle",   badge: "H", badgeHue: 210 },
    storage:            { hueBase: 275, shape: "square",   badge: "S", badgeHue: 275 },
    fuel:               { hueBase: 28,  shape: "drop",     badge: "F", badgeHue: 28  },
    raw_material:       { hueBase: 90,  shape: "stone",    badge: "O", badgeHue: 90  },
    finished_material:  { hueBase: 175, shape: "pallet",   badge: "M", badgeHue: 175 },
    resource:           { hueBase: 160, shape: "drop",     badge: "D", badgeHue: 160 },
    container:          { hueBase: 265, shape: "square",   badge: "C", badgeHue: 265 },
    prospector:         { hueBase: 320, shape: "gear",     badge: "P", badgeHue: 320 },
    robonaut:           { hueBase: 295, shape: "gear",     badge: "B", badgeHue: 295 },
    refinery:           { hueBase: 350, shape: "gear",     badge: "X", badgeHue: 350 },
    miner:              { hueBase: 38,  shape: "chevron",  badge: "M", badgeHue: 38  },
    constructor:        { hueBase: 38,  shape: "chevron",  badge: "M", badgeHue: 38  },
    printer:            { hueBase: 185, shape: "diamond",  badge: "P", badgeHue: 185 },
    isru:               { hueBase: 110, shape: "hexagon",  badge: "I", badgeHue: 110 },
    recipe:             { hueBase: 42,  shape: "cube",     badge: "W", badgeHue: 42  },
  };

  function categoryVisual(category) {
    const key = String(category || "").trim().toLowerCase();
    return CATEGORY_VISUALS[key] || null;
  }

  /* ── Item glyph (2-letter abbreviation) ────────────────── */

  function itemGlyph(label) {
    const words = String(label || "")
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2);
    if (!words.length) return "?";
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return `${words[0][0] || ""}${words[1][0] || ""}`.toUpperCase();
  }

  /* ── SVG shape paths (centered in 64×64) ───────────────── */

  function shapeSvg(shape, fill) {
    switch (shape) {
      case "hexagon":
        return `<polygon points='32,4 58,18 58,46 32,60 6,46 6,18' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/>`;
      case "diamond":
        return `<polygon points='32,4 58,32 32,60 6,32' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/>`;
      case "circle":
        return `<circle cx='32' cy='32' r='27' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/>`;
      case "chevron":
        return `<polygon points='8,14 32,4 56,14 56,44 32,60 8,44' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/>`;
      case "drop":
        return `<path d='M32 6 C32 6, 52 26, 52 38 C52 50, 42 58, 32 58 C22 58, 12 50, 12 38 C12 26, 32 6, 32 6Z' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/>`;
      case "ore":
        return `<polygon points='16,8 48,8 56,28 48,56 16,56 8,28' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/>`;
      case "cube":
        return `<rect x='8' y='8' width='48' height='48' rx='4' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/>`;
      case "gear":
        return `<circle cx='32' cy='32' r='22' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/><circle cx='32' cy='32' r='10' fill='rgba(0,0,0,0.25)' stroke='none'/>`;
      case "stone":
        return `<polygon points='6,56 14,40 20,44 26,30 34,34 40,24 48,20 54,32 58,38 60,56' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/><polygon points='18,56 24,46 32,42 40,46 36,56' fill='rgba(255,255,255,0.08)' stroke='none'/>`;
      case "steam":
        return `<path d='M14,48 C4,48 2,38 12,35 C9,24 20,16 30,22 C34,12 48,12 50,22 C58,17 64,28 56,36 C64,38 62,48 52,48 Z' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/>`;
      case "pallet":
        return `<rect x='6' y='48' width='52' height='8' rx='2' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1' opacity='0.7'/><rect x='10' y='30' width='20' height='18' rx='2' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/><rect x='34' y='30' width='20' height='18' rx='2' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/><rect x='16' y='12' width='32' height='18' rx='2' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/>`;
      default: // "square"
        return `<rect x='6' y='6' width='52' height='52' rx='9' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/>`;
    }
  }

  /* ── Per-category symbol (replaces 2-letter glyph) ────── */

  function categorySymbolSvg(category) {
    const f = "rgba(243,250,255,0.93)";
    const ns = `fill='none' stroke='${f}' stroke-linecap='round' stroke-linejoin='round'`;
    switch (String(category || "").toLowerCase()) {
      case "thruster":
        // Upward rocket flame / arrow
        return `<path d='M32 16L26 36H30V48H34V48H34V36H38Z' fill='${f}'/>`;
      case "reactor":
        // Atom — centre dot + three orbital ellipses
        return `<circle cx='32' cy='32' r='3.5' fill='${f}'/>`
          + `<ellipse cx='32' cy='32' rx='14' ry='5.5' ${ns} stroke-width='1.6' transform='rotate(0 32 32)'/>`
          + `<ellipse cx='32' cy='32' rx='14' ry='5.5' ${ns} stroke-width='1.6' transform='rotate(60 32 32)'/>`
          + `<ellipse cx='32' cy='32' rx='14' ry='5.5' ${ns} stroke-width='1.6' transform='rotate(-60 32 32)'/>`;
      case "generator":
        // Lightning bolt
        return `<polygon points='35,14 23,34 31,34 29,50 41,30 33,30' fill='${f}'/>`;
      case "radiator":
        // Three horizontal heat-wave lines
        return `<path d='M20 24Q26 19 32 24Q38 29 44 24' ${ns} stroke-width='2.5'/>`
          + `<path d='M20 32Q26 27 32 32Q38 37 44 32' ${ns} stroke-width='2.5'/>`
          + `<path d='M20 40Q26 35 32 40Q38 45 44 40' ${ns} stroke-width='2.5'/>`;
      case "storage":
        // Crate / box
        return `<rect x='20' y='22' width='24' height='20' rx='2' ${ns} stroke-width='2'/>`
          + `<line x1='20' y1='30' x2='44' y2='30' ${ns} stroke-width='1.5'/>`
          + `<line x1='32' y1='30' x2='32' y2='42' ${ns} stroke-width='1.5'/>`;
      case "fuel":
        // Flame with inner core
        return `<path d='M32 18C32 18 43 29 43 37C43 43 38 49 32 49C26 49 21 43 21 37C21 29 32 18 32 18Z' fill='none' stroke='${f}' stroke-width='2'/>`
          + `<path d='M32 30C32 30 37 34 37 38C37 41 35 43 32 43C29 43 27 41 27 38C27 34 32 30 32 30Z' fill='${f}'/>`;
      case "prospector":
        // Magnifying glass
        return `<circle cx='29' cy='29' r='9' ${ns} stroke-width='2'/>`
          + `<line x1='36' y1='36' x2='45' y2='45' ${ns} stroke-width='2.5'/>`;
      case "robonaut":
        // Simple robot silhouette (head + body + limbs)
        return `<rect x='26' y='18' width='12' height='9' rx='3' ${ns} stroke-width='2'/>`
          + `<line x1='32' y1='27' x2='32' y2='39' ${ns} stroke-width='2'/>`
          + `<line x1='24' y1='33' x2='40' y2='33' ${ns} stroke-width='2'/>`
          + `<line x1='28' y1='39' x2='28' y2='46' ${ns} stroke-width='2'/>`
          + `<line x1='36' y1='39' x2='36' y2='46' ${ns} stroke-width='2'/>`;
      case "refinery":
        // Gear / cog with centre dot
        return `<circle cx='32' cy='32' r='8' ${ns} stroke-width='2'/>`
          + `<circle cx='32' cy='32' r='3' fill='${f}'/>`
          + `<line x1='32' y1='18' x2='32' y2='24' ${ns} stroke-width='2.5'/>`
          + `<line x1='32' y1='40' x2='32' y2='46' ${ns} stroke-width='2.5'/>`
          + `<line x1='18' y1='32' x2='24' y2='32' ${ns} stroke-width='2.5'/>`
          + `<line x1='40' y1='32' x2='46' y2='32' ${ns} stroke-width='2.5'/>`
          + `<line x1='22' y1='22' x2='26' y2='26' ${ns} stroke-width='2'/>`
          + `<line x1='38' y1='38' x2='42' y2='42' ${ns} stroke-width='2'/>`
          + `<line x1='42' y1='22' x2='38' y2='26' ${ns} stroke-width='2'/>`
          + `<line x1='26' y1='38' x2='22' y2='42' ${ns} stroke-width='2'/>`;
      case "miner":
      case "constructor":
        // Pickaxe — angled head + handle
        return `<line x1='22' y1='46' x2='42' y2='18' ${ns} stroke-width='2.5'/>`
          + `<path d='M38 18L46 22L42 18L46 14Z' fill='${f}'/>`;
      case "printer":
        // 3D-printer nozzle depositing layers
        return `<path d='M26 18H38L42 24H22Z' fill='${f}'/>`
          + `<line x1='32' y1='24' x2='32' y2='36' ${ns} stroke-width='2'/>`
          + `<path d='M28 36L36 36L38 40L26 40Z' fill='${f}' opacity='0.7'/>`
          + `<rect x='22' y='42' width='20' height='3' rx='1' fill='${f}' opacity='0.5'/>`
          + `<rect x='20' y='47' width='24' height='3' rx='1' fill='${f}' opacity='0.35'/>`;
      case "isru":
        // Chemical processing — input funnel, pipe, output drop
        return `<path d='M22 18L32 18L42 18L38 28H26Z' fill='${f}'/>`
          + `<rect x='29' y='28' width='6' height='10' rx='1' fill='${f}' opacity='0.8'/>`
          + `<path d='M32 38C32 38 38 43 38 46C38 49 35 50 32 50C29 50 26 49 26 46C26 43 32 38 32 38Z' fill='${f}'/>`;
      case "recipe":
        // Flask / beaker
        return `<path d='M28 18V30L21 46H43L36 30V18Z' ${ns} stroke-width='2'/>`
          + `<line x1='26' y1='18' x2='38' y2='18' ${ns} stroke-width='2'/>`;
      default:
        return "";
    }
  }

  /* ── Icon data-URI generator ───────────────────────────── */

  function iconDataUri(seed, label, category, phase) {
    const cacheKey = `${String(seed || "")}::${String(label || "")}::${String(category || "")}::${String(phase || "")}`;
    const cached = iconCache.get(cacheKey);
    if (cached) return cached;

    const vis = categoryVisual(category);
    const hash = hashCode(seed || label || "item");
    const hue = vis ? (vis.hueBase + (hash % 40) - 20) : hash % 360;
    const hue2 = (hue + 52) % 360;
    let shape = vis ? vis.shape : "square";

    // Phase-aware shape override for raw materials
    const ph = String(phase || "").trim().toLowerCase();
    const cat = String(category || "").trim().toLowerCase();
    if (cat === "raw_material" || (cat === "resource" && ph)) {
      if (ph === "gas")         shape = "steam";
      else if (ph === "liquid") shape = "drop";
      else if (ph === "solid")  shape = cat === "raw_material" ? "stone" : shape;
    }
    const fillId = `g${hash % 10000}`;
    const symbol = categorySymbolSvg(cat);
    const glyph = symbol ? "" : itemGlyph(label);

    // Badge pip (top-right corner) — shows category letter on a bright circle
    let badgeSvg = "";
    if (vis && vis.badge) {
      const bh = vis.badgeHue ?? hue;
      badgeSvg = `<circle cx='54' cy='10' r='8' fill='hsl(${bh} 80% 42%)' stroke='rgba(0,0,0,0.45)' stroke-width='1.2'/>`
        + `<text x='54' y='13.5' text-anchor='middle' font-family='Segoe UI,Roboto,sans-serif' font-size='10' fill='rgba(255,255,255,0.95)' font-weight='700'>${escapeHtml(vis.badge)}</text>`;
    }

    const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>
  <defs><linearGradient id='${fillId}' x1='0' y1='0' x2='1' y2='1'>
    <stop offset='0%' stop-color='hsl(${hue} 72% 46%)'/>
    <stop offset='100%' stop-color='hsl(${hue2} 72% 28%)'/>
  </linearGradient></defs>
  ${shapeSvg(shape, `url(#${fillId})`)}
  ${symbol || `<text x='32' y='38' text-anchor='middle' font-family='Segoe UI,Roboto,sans-serif' font-size='18' fill='rgba(243,250,255,0.96)' font-weight='700'>${escapeHtml(glyph)}</text>`}
  ${badgeSvg}
</svg>`;

    const uri = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
    iconCache.set(cacheKey, uri);
    return uri;
  }

  /* ── Grid cell (Eve Online-style square) ───────────────── */

  function createGridCell(options) {
    const o = options || {};
    const cell = document.createElement("div");
    cell.className = "invCell";
    if (o.draggable) {
      cell.draggable = true;
      cell.classList.add("isDraggable");
    }
    if (o.className) cell.classList.add(...String(o.className).trim().split(/\s+/));

    // Quantity badge
    const qty = Number(o.quantity) || 0;
    if (qty > 1) {
      const badge = document.createElement("span");
      badge.className = "invCellQty";
      badge.textContent = qty >= 1000 ? `${(qty / 1000).toFixed(1)}k` : String(Math.round(qty));
      cell.appendChild(badge);
    }

    // Info button (top-right "i" icon) — opens the item info modal
    const infoItemId = String(o.itemId || o.item_id || o.iconSeed || "").trim();
    if (infoItemId) {
      const infoBtn = document.createElement("button");
      infoBtn.type = "button";
      infoBtn.className = "invCellInfoBtn";
      infoBtn.title = "Show Info";
      infoBtn.textContent = "ⓘ";
      infoBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        if (window.ItemInfo) window.ItemInfo.open(infoItemId);
      });
      cell.appendChild(infoBtn);
      cell.dataset.itemId = infoItemId;
    }

    // Icon
    const icon = document.createElement("img");
    icon.className = "invCellIcon";
    icon.alt = String(o.label || "Item");
    icon.src = o.icon ? `/static/img/icons/${o.icon}` : iconDataUri(o.iconSeed, o.label, o.category, o.phase);
    icon.draggable = false;
    cell.appendChild(icon);

    // Label
    const label = document.createElement("div");
    label.className = "invCellLabel";
    label.textContent = String(o.label || "Item");
    cell.appendChild(label);

    // Store tooltip data on the element
    cell.dataset.tooltipLabel = String(o.label || "");
    cell.dataset.tooltipCategory = String(o.category || o.subtitle || "");
    cell.dataset.tooltipMassKg = String(Number(o.mass_kg) || 0);
    cell.dataset.tooltipVolumeM3 = String(Number(o.volume_m3) || 0);
    cell.dataset.tooltipQuantity = String(qty);
    if (o.subtitle) cell.dataset.tooltipSubtitle = String(o.subtitle);
    if (o.stats) cell.dataset.tooltipStats = String(o.stats);
    if (o.tooltipLines) cell.dataset.tooltipExtra = JSON.stringify(o.tooltipLines);
    if (o.phase) cell.dataset.tooltipPhase = String(o.phase);
    if (o.branch) cell.dataset.tooltipBranch = String(o.branch);
    if (o.techLevel) cell.dataset.tooltipTechLevel = String(o.techLevel);
    if (o.family) cell.dataset.tooltipFamily = String(o.family);
    if (Number(o.water_extraction_kg_per_hr) > 0) {
      cell.dataset.tooltipWaterExtractionKgPerHr = String(Number(o.water_extraction_kg_per_hr));
    }
    if (!Number.isNaN(Number(o.min_water_ice_fraction))) {
      cell.dataset.tooltipMinWaterIceFraction = String(Number(o.min_water_ice_fraction));
    }
    if (!Number.isNaN(Number(o.max_water_ice_fraction))) {
      cell.dataset.tooltipMaxWaterIceFraction = String(Number(o.max_water_ice_fraction));
    }
    if (Number(o.core_temp_k) > 0) cell.dataset.tooltipCoreTempK = String(Number(o.core_temp_k));
    if (Number(o.rated_temp_k) > 0) cell.dataset.tooltipRatedTempK = String(Number(o.rated_temp_k));
    if (o.miner_type) cell.dataset.tooltipMinerType = String(o.miner_type);
    if (o.operational_environment) cell.dataset.tooltipOperationalEnvironment = String(o.operational_environment);
    if (!Number.isNaN(Number(o.min_surface_gravity_ms2))) {
      cell.dataset.tooltipMinSurfaceGravityMs2 = String(Number(o.min_surface_gravity_ms2));
    }
    if (!Number.isNaN(Number(o.max_surface_gravity_ms2))) {
      cell.dataset.tooltipMaxSurfaceGravityMs2 = String(Number(o.max_surface_gravity_ms2));
    }
    if (!Number.isNaN(Number(o.min_volatile_mass_fraction))) {
      cell.dataset.tooltipMinVolatileMassFraction = String(Number(o.min_volatile_mass_fraction));
    }
    if (Number(o.electric_mw) > 0) {
      cell.dataset.tooltipElectricMw = String(Number(o.electric_mw));
    }
    if (Number(o.thermal_mw_input) > 0) {
      cell.dataset.tooltipThermalMwInput = String(Number(o.thermal_mw_input));
    }
    if (!Number.isNaN(Number(o.conversion_efficiency))) {
      cell.dataset.tooltipConversionEfficiency = String(Number(o.conversion_efficiency));
    }
    if (!Number.isNaN(Number(o.recipe_slots))) {
      cell.dataset.tooltipRecipeSlots = String(Number(o.recipe_slots));
    }
    if (!Number.isNaN(Number(o.max_concurrent_recipes))) {
      cell.dataset.tooltipMaxConcurrentRecipes = String(Number(o.max_concurrent_recipes));
    }
    if (Array.isArray(o.supported_recipe_names)) {
      const recipeNames = o.supported_recipe_names
        .map((name) => String(name || "").trim())
        .filter(Boolean);
      if (recipeNames.length) {
        cell.dataset.tooltipSupportedRecipeNames = JSON.stringify(recipeNames);
      }
    }

    // Tooltip events
    cell.addEventListener("pointerenter", (e) => showTooltip(cell, e));
    cell.addEventListener("pointerleave", hideTooltip);
    cell.addEventListener("pointermove", (e) => repositionTooltip(e));

    return cell;
  }

  /* ── Tooltip ───────────────────────────────────────────── */

  function ensureTooltipEl() {
    if (tooltipEl) return tooltipEl;
    const el = document.createElement("div");
    el.className = "invTooltip";
    el.style.display = "none";
    document.body.appendChild(el);
    tooltipEl = el;
    return el;
  }

  function repositionTooltip(e) {
    const tooltip = ensureTooltipEl();
    if (tooltip.style.display === "none") return;
    cancelAnimationFrame(tooltipRaf);
    tooltipRaf = requestAnimationFrame(() => {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const rect = tooltip.getBoundingClientRect();
      const pad = 14;
      let left = e.clientX + pad;
      let top = e.clientY + pad;
      if (left + rect.width > vw - pad) left = e.clientX - rect.width - pad;
      if (top + rect.height > vh - pad) top = e.clientY - rect.height - pad;
      tooltip.style.left = `${Math.max(4, left)}px`;
      tooltip.style.top = `${Math.max(4, top)}px`;
    });
  }

  function showTooltip(cell, e) {
    const tooltip = ensureTooltipEl();
    const d = cell.dataset;

    let html = `<div class="invTooltipTitle">${escapeHtml(d.tooltipLabel || "Item")}</div>`;

    if (d.tooltipCategory || d.tooltipSubtitle) {
      html += `<div class="invTooltipSub">${escapeHtml(d.tooltipSubtitle || d.tooltipCategory)}</div>`;
    }

    // Tech tree / branch / level
    const branch = d.tooltipBranch || "";
    const family = d.tooltipFamily || "";
    const techLevel = d.tooltipTechLevel || "";
    if (branch || family || techLevel) {
      const treeName = (family || branch).replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
      const branchName = (family && branch) ? branch.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()) : "";
      let techHtml = '<div class="invTooltipTech">';
      if (treeName) techHtml += `<span class="invTooltipTechTree">${escapeHtml(treeName)}</span>`;
      if (branchName) techHtml += ` <span class="invTooltipTechBranch">/ ${escapeHtml(branchName)}</span>`;
      if (techLevel) techHtml += `<span class="invTooltipTechLevel">Tech ${escapeHtml(techLevel)}</span>`;
      techHtml += '</div>';
      html += techHtml;
    }

    html += `<div class="invTooltipDivider"></div>`;

    const lines = [];
    const extraLines = [];
    const extraLabels = new Set();
    try {
      const extra = d.tooltipExtra ? JSON.parse(d.tooltipExtra) : [];
      if (Array.isArray(extra)) {
        extra.forEach((line) => {
          if (Array.isArray(line) && line.length >= 2) {
            const label = String(line[0] || "").trim();
            const value = String(line[1] || "");
            extraLines.push([label, value]);
            if (label) extraLabels.add(label.toLowerCase());
          } else if (typeof line === "string") {
            const label = String(line).trim();
            extraLines.push([label, ""]);
            if (label) extraLabels.add(label.toLowerCase());
          }
        });
      }
    } catch { /* ignore */ }

    const massKg = Number(d.tooltipMassKg) || 0;
    const volM3 = Number(d.tooltipVolumeM3) || 0;
    const qtyVal = Number(d.tooltipQuantity) || 0;

    if (qtyVal > 1) lines.push(["Quantity", String(Math.round(qtyVal))]);
    if (massKg > 0) lines.push(["Mass", fmtKg(massKg)]);
    if (volM3 > 0) lines.push(["Volume", fmtM3(volM3)]);
    if (d.tooltipPhase) lines.push(["Phase", d.tooltipPhase.charAt(0).toUpperCase() + d.tooltipPhase.slice(1)]);
    const minerTypeKey = String(d.tooltipMinerType || "").trim().toLowerCase();
    if (minerTypeKey) {
      lines.push(["Type", formatMinerType(minerTypeKey)]);
      const minGravMs2 = Number(d.tooltipMinSurfaceGravityMs2);
      const maxGravMs2 = Number(d.tooltipMaxSurfaceGravityMs2);
      const minVolatile = Number(d.tooltipMinVolatileMassFraction);
      if (minerTypeKey === "microgravity") {
        if (!Number.isNaN(maxGravMs2) && maxGravMs2 > 0) {
          lines.push(["Operational Gravity", `< ${maxGravMs2.toFixed(2)} m/s²`]);
        }
      } else if (minerTypeKey === "large_body" || minerTypeKey === "largebody") {
        if (!Number.isNaN(minGravMs2) && minGravMs2 > 0) {
          lines.push(["Operational Gravity", `≥ ${minGravMs2.toFixed(2)} m/s²`]);
        }
      } else if (minerTypeKey === "cryovolatile") {
        if (!Number.isNaN(minVolatile) && minVolatile > 0) {
          const volatilePct = Math.max(0, Math.min(100, minVolatile * 100));
          lines.push(["Water Ice Required", `≥ ${volatilePct.toFixed(1)}%`]);
        }
      }
    }
    const waterExtractionKgPerHr = Number(d.tooltipWaterExtractionKgPerHr) || 0;
    if (waterExtractionKgPerHr > 0) {
      lines.push(["Water Extraction", `${waterExtractionKgPerHr.toFixed(0)} kg/hr`]);
    }
    const minWaterIce = Number(d.tooltipMinWaterIceFraction);
    const maxWaterIce = Number(d.tooltipMaxWaterIceFraction);
    if (!Number.isNaN(minWaterIce) || !Number.isNaN(maxWaterIce)) {
      const minPct = Math.max(0, Math.min(100, (Number.isNaN(minWaterIce) ? 0 : minWaterIce * 100)));
      const maxPct = Math.max(0, Math.min(100, (Number.isNaN(maxWaterIce) ? 100 : maxWaterIce * 100)));
      let waterNeeded = "";
      if (minPct > 0 && maxPct < 100) {
        waterNeeded = `${minPct.toFixed(1)}%–${maxPct.toFixed(1)}%`;
      } else if (minPct > 0) {
        waterNeeded = `≥ ${minPct.toFixed(1)}%`;
      } else if (maxPct < 100) {
        waterNeeded = `≤ ${maxPct.toFixed(1)}%`;
      }
      if (waterNeeded) lines.push(["Water Needed", waterNeeded]);
    }
    const coreTempK = Number(d.tooltipCoreTempK) || 0;
    const ratedTempK = Number(d.tooltipRatedTempK) || 0;
    if (coreTempK > 0) lines.push(["Core Temp", `${coreTempK.toFixed(0)} K`]);
    if (ratedTempK > 0) lines.push(["Core Temp Req", `${ratedTempK.toFixed(0)} K`]);

    const categoryText = `${d.tooltipCategory || ""} ${d.tooltipSubtitle || ""}`.toLowerCase();
    const isRefinery = categoryText.includes("refinery");
    if (isRefinery) {
      const electricMw = Number(d.tooltipElectricMw) || 0;
      const recipeSlots = Number(d.tooltipRecipeSlots);
      const maxConcurrentRecipes = Number(d.tooltipMaxConcurrentRecipes);
      if (electricMw > 0 && !extraLabels.has("electric") && !extraLabels.has("electric use")) {
        lines.push(["Electric Use", `${electricMw.toFixed(2)} MWe`]);
      }
      if (!Number.isNaN(recipeSlots) && recipeSlots > 0 && !extraLabels.has("recipe slots")) {
        lines.push(["Recipe Slots", String(Math.round(recipeSlots))]);
      } else if (!Number.isNaN(maxConcurrentRecipes) && maxConcurrentRecipes > 0 && !extraLabels.has("recipe slots")) {
        lines.push(["Recipe Slots", String(Math.round(maxConcurrentRecipes))]);
      }

      let supportedRecipeNames = [];
      try {
        supportedRecipeNames = d.tooltipSupportedRecipeNames ? JSON.parse(d.tooltipSupportedRecipeNames) : [];
      } catch {
        supportedRecipeNames = [];
      }
      if (Array.isArray(supportedRecipeNames)) {
        const normalizedNames = supportedRecipeNames
          .map((name) => String(name || "").trim())
          .filter(Boolean);
        if (normalizedNames.length && !extraLabels.has("recipes")) {
          lines.push(["Recipes", normalizedNames.join(", ")]);
        }
      }
    }

    const isGenerator = categoryText.includes("generator");
    if (isGenerator) {
      const thermalInputMw = Number(d.tooltipThermalMwInput) || 0;
      const electricMw = Number(d.tooltipElectricMw) || 0;
      const conversionEfficiencyRaw = Number(d.tooltipConversionEfficiency);

      if (thermalInputMw > 0 && !extraLabels.has("thermal input") && !extraLabels.has("power input")) {
        lines.push(["Thermal Input", `${thermalInputMw.toFixed(1)} MWth`]);
      }
      if (electricMw > 0 && !extraLabels.has("electric") && !extraLabels.has("electrical output")) {
        lines.push(["Electrical Output", `${electricMw.toFixed(1)} MWe`]);
      }
      if (!Number.isNaN(conversionEfficiencyRaw) && conversionEfficiencyRaw > 0 && !extraLabels.has("efficiency")) {
        const efficiencyPct = conversionEfficiencyRaw <= 1.0
          ? conversionEfficiencyRaw * 100.0
          : conversionEfficiencyRaw;
        lines.push(["Efficiency", `${efficiencyPct.toFixed(1)}%`]);
      }
    }

    if (d.tooltipStats) lines.push(["Info", d.tooltipStats]);

    // Extra lines from data
    extraLines.forEach((line) => lines.push(line));

    for (const [lbl, val] of lines) {
      if (val) {
        html += `<div class="invTooltipRow"><span class="invTooltipRowLabel">${escapeHtml(lbl)}</span><span class="invTooltipRowVal">${escapeHtml(val)}</span></div>`;
      } else {
        html += `<div class="invTooltipRow"><span class="invTooltipRowLabel" style="grid-column:1/-1">${escapeHtml(lbl)}</span></div>`;
      }
    }

    tooltip.innerHTML = html;
    tooltip.style.display = "block";
    repositionTooltip(e);
  }

  function hideTooltip() {
    if (!tooltipEl) return;
    tooltipEl.style.display = "none";
    tooltipEl.innerHTML = "";
    cancelAnimationFrame(tooltipRaf);
  }

  /* ── Legacy createCard (compatibility wrapper) ─────────── */

  function createCard(options) {
    const o = options || {};
    const card = document.createElement("article");
    card.className = String(o.className || "inventoryItemCard").trim();
    card.setAttribute("role", o.role || "listitem");
    if (o.draggable) {
      card.draggable = true;
      card.classList.add("isDraggable");
    }

    const icon = document.createElement("img");
    icon.className = "inventoryItemIcon";
    icon.alt = `${String(o.label || "Item")} icon`;
    icon.src = iconDataUri(o.iconSeed, o.label, o.category, o.phase);

    const body = document.createElement("div");
    body.className = "inventoryItemBody";

    const title = document.createElement("div");
    title.className = "inventoryItemTitle";
    title.textContent = String(o.label || "Item");

    const sub = document.createElement("div");
    sub.className = "inventoryItemSub";
    sub.textContent = String(o.subtitle || "");

    const stats = document.createElement("div");
    stats.className = "inventoryItemStats";
    stats.textContent = String(o.stats || "");

    body.append(title, sub, stats);
    card.append(icon, body);

    return card;
  }

  /* ── Public API ────────────────────────────────────────── */

  return {
    escapeHtml,
    fmtKg,
    fmtM3,
    hashCode,
    itemGlyph,
    iconDataUri,
    createCard,
    createGridCell,
    showTooltip,
    hideTooltip,
    categoryVisual,
  };
})();
