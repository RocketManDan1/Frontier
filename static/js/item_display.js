window.ItemDisplay = (function () {
  const iconCache = new Map();

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function hashCode(text) {
    let hash = 0;
    const source = String(text || "");
    for (let i = 0; i < source.length; i += 1) {
      hash = ((hash << 5) - hash + source.charCodeAt(i)) | 0;
    }
    return Math.abs(hash);
  }

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

  function iconDataUri(seed, label) {
    const cacheKey = `${String(seed || "")}::${String(label || "")}`;
    const cached = iconCache.get(cacheKey);
    if (cached) return cached;

    const hash = hashCode(seed || label || "item");
    const hue = hash % 360;
    const hue2 = (hue + 52) % 360;
    const glyph = itemGlyph(label);
    const svg = `
      <svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>
        <defs>
          <linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
            <stop offset='0%' stop-color='hsl(${hue} 72% 46%)'/>
            <stop offset='100%' stop-color='hsl(${hue2} 72% 28%)'/>
          </linearGradient>
        </defs>
        <rect x='2' y='2' width='60' height='60' rx='10' fill='url(#g)'/>
        <rect x='2' y='2' width='60' height='60' rx='10' fill='none' stroke='rgba(220,238,255,0.26)'/>
        <text x='32' y='38' text-anchor='middle' font-family='Segoe UI,Roboto,sans-serif' font-size='20' fill='rgba(243,250,255,0.98)' font-weight='700'>${escapeHtml(glyph)}</text>
      </svg>
    `;
    const uri = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
    iconCache.set(cacheKey, uri);
    return uri;
  }

  function fmtKg(value) {
    return `${Math.max(0, Number(value) || 0).toFixed(0)} kg`;
  }

  function fmtM3(value) {
    return `${Math.max(0, Number(value) || 0).toFixed(2)} m³`;
  }

  function createCard(options = {}) {
    const card = document.createElement("article");
    card.className = String(options.className || "inventoryItemCard").trim() || "inventoryItemCard";
    card.setAttribute("role", options.role || "listitem");

    if (options.draggable) {
      card.draggable = true;
      card.classList.add("isDraggable");
    }

    const icon = document.createElement("img");
    icon.className = "inventoryItemIcon";
    icon.alt = `${String(options.label || "Item")} icon`;
    icon.src = iconDataUri(options.iconSeed, options.label);

    const body = document.createElement("div");
    body.className = "inventoryItemBody";

    const title = document.createElement("div");
    title.className = "inventoryItemTitle";
    title.textContent = String(options.label || "Item");

    const sub = document.createElement("div");
    sub.className = "inventoryItemSub";
    sub.textContent = String(options.subtitle || "");

    const stats = document.createElement("div");
    stats.className = "inventoryItemStats";
    stats.textContent = String(options.stats || "");

    body.append(title, sub, stats);
    card.append(icon, body);

    return card;
  }

  return {
    escapeHtml,
    fmtKg,
    fmtM3,
    iconDataUri,
    createCard,
  };
})();
