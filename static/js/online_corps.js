(function () {
  if (window.__onlineCorpsLoaded) return;
  window.__onlineCorpsLoaded = true;

  const POLL_INTERVAL = 30000; // 30 seconds
  const HEARTBEAT_INTERVAL = 30000; // 30 seconds

  function createWidget() {
    const el = document.createElement("div");
    el.id = "onlineCorpsWidget";
    el.innerHTML =
      '<div class="onlineCorpsHeader">' +
        '<span class="onlineCorpsDot"></span>' +
        '<span class="onlineCorpsLabel">Online</span>' +
        '<span class="onlineCorpsCount"></span>' +
      '</div>' +
      '<div class="onlineCorpsList"></div>';
    document.body.appendChild(el);
    return el;
  }

  function renderCorps(widget, corps) {
    const countEl = widget.querySelector(".onlineCorpsCount");
    const listEl = widget.querySelector(".onlineCorpsList");
    countEl.textContent = corps.length;

    listEl.innerHTML = "";
    if (corps.length === 0) {
      const empty = document.createElement("div");
      empty.className = "onlineCorpsEmpty";
      empty.textContent = "No corporations online";
      listEl.appendChild(empty);
    } else {
      corps.forEach(function (c) {
        const row = document.createElement("div");
        row.className = "onlineCorpsRow";
        const swatch = document.createElement("span");
        swatch.className = "onlineCorpsSwatch";
        swatch.style.backgroundColor = c.color || "#ffffff";
        const name = document.createElement("span");
        name.className = "onlineCorpsName";
        name.textContent = c.name;
        row.appendChild(swatch);
        row.appendChild(name);
        listEl.appendChild(row);
      });
    }
  }

  async function fetchOnlineCorps() {
    try {
      const resp = await fetch("/api/auth/online-corps", { cache: "no-store" });
      if (!resp.ok) return [];
      const data = await resp.json();
      return data.corps || [];
    } catch {
      return [];
    }
  }

  async function poll(widget) {
    const corps = await fetchOnlineCorps();
    renderCorps(widget, corps);
  }

  // Add CSS
  const style = document.createElement("style");
  style.textContent = `
    #onlineCorpsWidget {
      position: fixed;
      bottom: 12px;
      right: 12px;
      z-index: 9999;
      background: var(--panel-solid, #0d131c);
      border: 1px solid var(--border-soft, rgba(100,132,166,0.24));
      border-radius: 6px;
      padding: 6px 10px;
      min-width: 140px;
      max-width: 220px;
      font-family: inherit;
      font-size: 12px;
      color: var(--text, #d7e6f2);
      opacity: 0.85;
      transition: opacity 0.2s;
      pointer-events: auto;
    }
    #onlineCorpsWidget:hover {
      opacity: 1;
      border-color: var(--border, rgba(126,166,206,0.35));
    }
    .onlineCorpsHeader {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 4px;
      cursor: default;
      user-select: none;
    }
    .onlineCorpsDot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--ok, #74d8c0);
      flex-shrink: 0;
      box-shadow: 0 0 4px var(--ok, #74d8c0);
    }
    .onlineCorpsLabel {
      color: var(--muted, rgba(170,192,212,0.72));
      text-transform: uppercase;
      letter-spacing: 0.06em;
      font-size: 10px;
      flex: 1;
    }
    .onlineCorpsCount {
      color: var(--accent, #59b9e6);
      font-weight: 600;
      font-size: 12px;
    }
    .onlineCorpsList {
      display: flex;
      flex-direction: column;
      gap: 2px;
      max-height: 180px;
      overflow-y: auto;
    }
    .onlineCorpsRow {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 2px 0;
    }
    .onlineCorpsSwatch {
      width: 10px;
      height: 10px;
      border-radius: 2px;
      flex-shrink: 0;
      border: 1px solid rgba(255,255,255,0.12);
    }
    .onlineCorpsName {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-size: 12px;
    }
    .onlineCorpsEmpty {
      color: var(--muted, rgba(170,192,212,0.72));
      font-style: italic;
      font-size: 11px;
      padding: 2px 0;
    }
  `;
  document.head.appendChild(style);

  // Heartbeat — let the server know this corp has the game open
  async function sendHeartbeat() {
    try {
      await fetch("/api/auth/heartbeat", { method: "POST", cache: "no-store" });
    } catch { /* ignore */ }
  }

  // Boot
  const widget = createWidget();
  sendHeartbeat();
  poll(widget);
  setInterval(function () { poll(widget); }, POLL_INTERVAL);
  setInterval(sendHeartbeat, HEARTBEAT_INTERVAL);
})();
