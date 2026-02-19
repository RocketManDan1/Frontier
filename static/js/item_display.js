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
    if (v >= 1e6) return `${(v / 1000).toFixed(0)} t`;
    return `${v.toFixed(0)} kg`;
  }

  function fmtM3(value) {
    return `${Math.max(0, Number(value) || 0).toFixed(2)} m³`;
  }

  /* ── Category → visual mapping ─────────────────────────── */

  const CATEGORY_VISUALS = {
    thruster:           { hueBase: 18,  shape: "chevron" },
    reactor:            { hueBase: 55,  shape: "hexagon" },
    generator:          { hueBase: 145, shape: "diamond" },
    radiator:           { hueBase: 200, shape: "circle"  },
    storage:            { hueBase: 260, shape: "square"  },
    fuel:               { hueBase: 30,  shape: "drop"    },
    raw_material:       { hueBase: 95,  shape: "ore"     },
    finished_material:  { hueBase: 175, shape: "cube"    },
    resource:           { hueBase: 130, shape: "drop"    },
    container:          { hueBase: 260, shape: "square"  },
    robonaut:           { hueBase: 310, shape: "gear"    },
    refinery:           { hueBase: 340, shape: "gear"    },
    recipe:             { hueBase: 45,  shape: "cube"    },
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
      default: // "square"
        return `<rect x='6' y='6' width='52' height='52' rx='9' fill='${fill}' stroke='rgba(220,238,255,0.22)' stroke-width='1.5'/>`;
    }
  }

  /* ── Icon data-URI generator ───────────────────────────── */

  function iconDataUri(seed, label, category) {
    const cacheKey = `${String(seed || "")}::${String(label || "")}::${String(category || "")}`;
    const cached = iconCache.get(cacheKey);
    if (cached) return cached;

    const vis = categoryVisual(category);
    const hash = hashCode(seed || label || "item");
    const hue = vis ? (vis.hueBase + (hash % 40) - 20) : hash % 360;
    const hue2 = (hue + 52) % 360;
    const shape = vis ? vis.shape : "square";
    const glyph = itemGlyph(label);
    const fillId = `g${hash % 10000}`;

    const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>
  <defs><linearGradient id='${fillId}' x1='0' y1='0' x2='1' y2='1'>
    <stop offset='0%' stop-color='hsl(${hue} 72% 46%)'/>
    <stop offset='100%' stop-color='hsl(${hue2} 72% 28%)'/>
  </linearGradient></defs>
  ${shapeSvg(shape, `url(#${fillId})`)}
  <text x='32' y='38' text-anchor='middle' font-family='Segoe UI,Roboto,sans-serif' font-size='18' fill='rgba(243,250,255,0.96)' font-weight='700'>${escapeHtml(glyph)}</text>
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

    // Icon
    const icon = document.createElement("img");
    icon.className = "invCellIcon";
    icon.alt = String(o.label || "Item");
    icon.src = iconDataUri(o.iconSeed, o.label, o.category);
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

    html += `<div class="invTooltipDivider"></div>`;

    const lines = [];
    const massKg = Number(d.tooltipMassKg) || 0;
    const volM3 = Number(d.tooltipVolumeM3) || 0;
    const qtyVal = Number(d.tooltipQuantity) || 0;

    if (qtyVal > 1) lines.push(["Quantity", String(Math.round(qtyVal))]);
    if (massKg > 0) lines.push(["Mass", fmtKg(massKg)]);
    if (volM3 > 0) lines.push(["Volume", fmtM3(volM3)]);
    if (d.tooltipPhase) lines.push(["Phase", d.tooltipPhase.charAt(0).toUpperCase() + d.tooltipPhase.slice(1)]);
    if (d.tooltipStats) lines.push(["Info", d.tooltipStats]);

    // Extra lines from data
    try {
      const extra = d.tooltipExtra ? JSON.parse(d.tooltipExtra) : [];
      if (Array.isArray(extra)) {
        extra.forEach((line) => {
          if (Array.isArray(line) && line.length >= 2) {
            lines.push([String(line[0]), String(line[1])]);
          } else if (typeof line === "string") {
            lines.push([line, ""]);
          }
        });
      }
    } catch { /* ignore */ }

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
    icon.src = iconDataUri(o.iconSeed, o.label, o.category);

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
