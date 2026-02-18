(function () {
  const root = document.querySelector(".mapPage .content");
  const dock = document.querySelector(".mapPage .mapDock");
  const layer = document.getElementById("appWindowLayer");
  if (!root || !dock || !layer) return;

  const STORAGE_KEY = "earthmoon.appWindows.v1";
  const MIN_WIDTH = 380;
  const MIN_HEIGHT = 300;
  const TOP_OFFSET = 12;

  const APP_CONFIG = {
    fleet: { title: "Fleet", url: "/fleet", icon: "/static/img/dock/fleet.png" },
    shipyard: { title: "Shipyard", url: "/shipyard", icon: "/static/img/dock/shipyard.png" },
    research: { title: "Research", url: "/research", icon: "/static/img/dock/research.png" },
    profile: { title: "Profile", url: "/profile?embed=1", icon: "/static/img/dock/profile.png" },
    admin: { title: "Admin", url: "/admin", icon: "" },
  };

  const state = loadState();
  const windows = new Map();
  let zIndex = 80;

  function bootstrapDockButton(btn, iconSrc) {
    if (!btn) return;
    if (!btn.querySelector(".mapDockBtnLabel")) {
      const labelText = (btn.textContent || "").trim();
      btn.textContent = "";
      const iconEl = document.createElement("span");
      iconEl.className = "mapDockBtnIcon";
      iconEl.setAttribute("aria-hidden", "true");
      const labelEl = document.createElement("span");
      labelEl.className = "mapDockBtnLabel";
      labelEl.textContent = labelText;
      btn.append(iconEl, labelEl);
    }
    if (iconSrc) {
      btn.classList.add("hasIcon");
      btn.style.setProperty("--dock-icon", `url(\"${iconSrc}\")`);
    }
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function loadState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  function saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // Ignore storage failures.
    }
  }

  function dockButtonFor(id) {
    return dock.querySelector(`[data-app-window='${id}']`);
  }

  function setDockOpen(id, open) {
    const btn = dockButtonFor(id);
    if (!btn) return;
    btn.classList.toggle("isOpen", !!open);
    btn.setAttribute("aria-pressed", open ? "true" : "false");
  }

  function contentLimits() {
    const rect = root.getBoundingClientRect();
    return {
      maxWidth: Math.max(MIN_WIDTH, rect.width - 10),
      maxHeight: Math.max(MIN_HEIGHT, rect.height - TOP_OFFSET - 8),
      minLeft: 0,
      maxLeft: rect.width,
      minTop: TOP_OFFSET,
      maxTop: rect.height,
    };
  }

  function clampRect(rect) {
    const limits = contentLimits();
    const width = clamp(rect.width, MIN_WIDTH, limits.maxWidth);
    const height = clamp(rect.height, MIN_HEIGHT, limits.maxHeight);
    const left = clamp(rect.left, limits.minLeft, limits.maxLeft - width);
    const top = clamp(rect.top, limits.minTop, limits.maxTop - height);
    return { left, top, width, height };
  }

  function applyRect(el, rect) {
    el.style.left = `${Math.round(rect.left)}px`;
    el.style.top = `${Math.round(rect.top)}px`;
    el.style.width = `${Math.round(rect.width)}px`;
    el.style.height = `${Math.round(rect.height)}px`;
  }

  function bringToFront(el) {
    zIndex += 1;
    el.style.zIndex = String(zIndex);
    root.querySelectorAll(".mapWindow.isSelected").forEach((panelEl) => {
      panelEl.classList.remove("isSelected");
    });
    el.classList.add("isSelected");
  }

  function persistWindow(id, record) {
    state[id] = {
      ...(state[id] || {}),
      ...record,
    };
    saveState();
  }

  function attachFrameEmbedMode(frame) {
    frame.addEventListener("load", () => {
      try {
        const doc = frame.contentDocument;
        if (!doc || !doc.body) return;
        doc.body.classList.add("embedMode");
      } catch {
        // Ignore cross-context issues.
      }
    });
  }

  function ensureResizeHandles(panel) {
    panel.querySelectorAll("[data-resize-handle='true']").forEach((el) => el.remove());
    const dirs = ["n", "e", "s", "w", "ne", "nw", "se", "sw"];
    return dirs.map((dir) => {
      const handle = document.createElement("div");
      handle.className = `mapWindowResize mapWindowResize--${dir}`;
      handle.setAttribute("data-resize-handle", "true");
      handle.setAttribute("data-resize-dir", dir);
      handle.setAttribute("aria-hidden", "true");
      panel.appendChild(handle);
      return handle;
    });
  }

  function createWindow(appId, config) {
    const panel = document.createElement("section");
    panel.className = "panel mapWindow appWindow";
    panel.dataset.appWindow = appId;

    const saved = state[appId] || {};
    const baseRect = {
      left: Number.isFinite(saved.left) ? saved.left : 208 + windows.size * 24,
      top: Number.isFinite(saved.top) ? saved.top : 24 + windows.size * 20,
      width: Number.isFinite(saved.width) ? saved.width : 760,
      height: Number.isFinite(saved.height) ? saved.height : 520,
    };
    const initial = clampRect(baseRect);

    panel.innerHTML = `
      <div class="mapWindowHeader" data-drag-handle="true">
        <div class="appWindowHeaderRow">
          <div class="titleSmall">${config.title}</div>
          <div class="appWindowActions">
            <button class="appWindowAction" type="button" data-window-action="minimize" aria-label="Minimize">—</button>
            <button class="appWindowAction" type="button" data-window-action="close" aria-label="Close">×</button>
          </div>
        </div>
      </div>
      <div class="appWindowBody">
        <iframe class="appWindowFrame" src="${config.url}" loading="lazy" title="${config.title} window"></iframe>
      </div>
    `;

    const dragHandle = panel.querySelector("[data-drag-handle='true']");
    const resizeHandles = ensureResizeHandles(panel);
    const bodyEl = panel.querySelector(".appWindowBody");
    const frameEl = panel.querySelector(".appWindowFrame");

    if (frameEl) attachFrameEmbedMode(frameEl);

    function syncRect() {
      const panelRect = panel.getBoundingClientRect();
      const rootRect = root.getBoundingClientRect();
      persistWindow(appId, {
        left: panelRect.left - rootRect.left,
        top: panelRect.top - rootRect.top,
        width: panelRect.width,
        height: panelRect.height,
      });
    }

    function stopPointerListeners(move, stop) {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    }

    function startDrag(event) {
      if (event.button !== 0) return;
      if (event.target.closest("button,a,input,select,textarea")) return;
      event.preventDefault();
      bringToFront(panel);
      panel.classList.add("isDragging");

      const pRect = panel.getBoundingClientRect();
      const rRect = root.getBoundingClientRect();
      const origin = {
        x: event.clientX,
        y: event.clientY,
        left: pRect.left - rRect.left,
        top: pRect.top - rRect.top,
        width: pRect.width,
        height: pRect.height,
      };

      const onMove = (moveEvent) => {
        const next = clampRect({
          left: origin.left + (moveEvent.clientX - origin.x),
          top: origin.top + (moveEvent.clientY - origin.y),
          width: origin.width,
          height: origin.height,
        });
        applyRect(panel, next);
      };

      const stop = () => {
        panel.classList.remove("isDragging");
        syncRect();
        stopPointerListeners(onMove, stop);
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", stop);
      window.addEventListener("pointercancel", stop);
    }

    function startResize(event) {
      if (event.button !== 0) return;
      event.preventDefault();
      bringToFront(panel);
      panel.classList.add("isResizing");
      const dir = String(event.currentTarget?.dataset?.resizeDir || "se").toLowerCase();

      const pRect = panel.getBoundingClientRect();
      const rRect = root.getBoundingClientRect();
      const origin = {
        x: event.clientX,
        y: event.clientY,
        left: pRect.left - rRect.left,
        top: pRect.top - rRect.top,
        width: pRect.width,
        height: pRect.height,
      };

      const onMove = (moveEvent) => {
        const dx = moveEvent.clientX - origin.x;
        const dy = moveEvent.clientY - origin.y;
        let nextLeft = origin.left;
        let nextTop = origin.top;
        let nextWidth = origin.width;
        let nextHeight = origin.height;

        if (dir.includes("e")) nextWidth = origin.width + dx;
        if (dir.includes("s")) nextHeight = origin.height + dy;
        if (dir.includes("w")) {
          nextLeft = origin.left + dx;
          nextWidth = origin.width - dx;
        }
        if (dir.includes("n")) {
          nextTop = origin.top + dy;
          nextHeight = origin.height - dy;
        }

        const next = clampRect({
          left: nextLeft,
          top: nextTop,
          width: nextWidth,
          height: nextHeight,
        });
        applyRect(panel, next);
      };

      const stop = () => {
        panel.classList.remove("isResizing");
        syncRect();
        stopPointerListeners(onMove, stop);
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", stop);
      window.addEventListener("pointercancel", stop);
    }

    function setMinimized(minimized) {
      panel.dataset.minimized = minimized ? "true" : "false";
      panel.classList.toggle("isMinimized", !!minimized);
      if (bodyEl) bodyEl.style.display = minimized ? "none" : "block";
      resizeHandles.forEach((handle) => {
        handle.style.display = minimized ? "none" : "";
      });
      if (minimized) {
        panel.style.height = "30px";
      } else {
        const restore = state[appId] || {};
        const next = clampRect({
          left: Number.isFinite(restore.left) ? restore.left : initial.left,
          top: Number.isFinite(restore.top) ? restore.top : initial.top,
          width: Number.isFinite(restore.width) ? restore.width : initial.width,
          height: Number.isFinite(restore.height) ? restore.height : initial.height,
        });
        applyRect(panel, next);
      }
      persistWindow(appId, { minimized: !!minimized });
    }

    panel.addEventListener("pointerdown", () => bringToFront(panel));
    if (dragHandle) dragHandle.addEventListener("pointerdown", startDrag);
    resizeHandles.forEach((handle) => {
      handle.addEventListener("pointerdown", startResize);
    });

    panel.addEventListener("click", (event) => {
      const actionBtn = event.target.closest("[data-window-action]");
      if (!actionBtn) return;
      const action = actionBtn.getAttribute("data-window-action");
      if (action === "close") {
        hideWindow(appId);
      } else if (action === "minimize") {
        setMinimized(panel.dataset.minimized !== "true");
      }
    });

    applyRect(panel, initial);
    layer.appendChild(panel);
    bringToFront(panel);

    if (saved.minimized) setMinimized(true);

    windows.set(appId, panel);
    persistWindow(appId, {
      left: initial.left,
      top: initial.top,
      width: initial.width,
      height: initial.height,
      open: true,
    });

    return panel;
  }

  function showWindow(appId) {
    const config = APP_CONFIG[appId];
    if (!config) return;

    const existing = windows.get(appId);
    if (existing) {
      existing.style.display = "block";
      bringToFront(existing);
      setDockOpen(appId, true);
      persistWindow(appId, { open: true });
      return;
    }

    createWindow(appId, config);
    setDockOpen(appId, true);
  }

  function hideWindow(appId) {
    const panel = windows.get(appId);
    if (!panel) return;
    panel.style.display = "none";
    setDockOpen(appId, false);
    persistWindow(appId, { open: false });
  }

  function toggleWindow(appId) {
    const panel = windows.get(appId);
    if (panel && panel.style.display !== "none") {
      hideWindow(appId);
      return;
    }
    showWindow(appId);
  }

  dock.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-app-window]");
    if (!btn) return;
    const appId = btn.getAttribute("data-app-window");
    if (!appId) return;
    toggleWindow(appId);
  });

  window.addEventListener("resize", () => {
    windows.forEach((panel, appId) => {
      if (panel.style.display === "none") return;
      if (panel.dataset.minimized === "true") return;
      const pRect = panel.getBoundingClientRect();
      const rRect = root.getBoundingClientRect();
      const next = clampRect({
        left: pRect.left - rRect.left,
        top: pRect.top - rRect.top,
        width: pRect.width,
        height: pRect.height,
      });
      applyRect(panel, next);
      persistWindow(appId, next);
    });
  });

  Object.keys(APP_CONFIG).forEach((appId) => {
    const btn = dockButtonFor(appId);
    bootstrapDockButton(btn, APP_CONFIG[appId]?.icon || "");
    const saved = state[appId];
    if (saved && saved.open) showWindow(appId);
    else setDockOpen(appId, false);
  });
})();
