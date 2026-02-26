(async function () {
  // ---------- DOM helpers ----------
  const stage = document.getElementById("stage");
  const infoTitle = document.getElementById("infoTitle");
  const infoSubtitle = document.getElementById("infoSubtitle");
  const infoCoords = document.getElementById("infoCoords");
  const infoList = document.getElementById("infoList");
  const shipInfoTabsHost = document.getElementById("shipInfoTabsHost");
  const realWorldRef = document.getElementById("realWorldRef");
  const actions = document.getElementById("actions");
  const mapOverviewEl = document.getElementById("mapOverview");
  const zoneJumpBarEl = document.getElementById("zoneJumpBar");
  const overviewPanelEl = document.getElementById("overviewPanel");
  const infoPanelEl = document.getElementById("infoPanel");
  const mapOrgBalanceEl = document.getElementById("mapOrgBalance");
  const mapOrgIncomeEl = document.getElementById("mapOrgIncome");
  const mapOrgResearchEl = document.getElementById("mapOrgResearch");
  const mapOrgExpensesEl = document.getElementById("mapOrgExpenses");
  const stageParallax = { x: 0, y: 0, tx: 0, ty: 0 };
  const cameraMotion = { x: 0, y: 0, energy: 0 };
  const PARALLAX_MAX_PX = 12;
  const PARALLAX_WHEEL_FACTOR = 0.006;
  const PARALLAX_PAN_FACTOR = 0.032;
  const PARALLAX_RETURN = 0.992;
  const CAMERA_MIN_SCALE = 0.001;
  const CAMERA_MAX_SCALE = 60;
  const CAMERA_WHEEL_SENSITIVITY = 0.0015;
  const DUST_BASE_ALPHA = 0.028;
  const DUST_ACTIVE_ALPHA = 0.17;
  const dustParticles = [];
  const MAP_WINDOW_LAYOUT_KEY = "earthmoon.mapWindowLayout.v1";
  let mapWindowZIndex = 20;
  let mapPanelControllers = null;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function nudgeStageParallax(dx, dy) {
    stageParallax.tx = clamp(stageParallax.tx + dx, -PARALLAX_MAX_PX, PARALLAX_MAX_PX);
    stageParallax.ty = clamp(stageParallax.ty + dy, -PARALLAX_MAX_PX, PARALLAX_MAX_PX);
  }

  function registerCameraMotion(dx, dy) {
    cameraMotion.x += dx;
    cameraMotion.y += dy;
    cameraMotion.energy = clamp(cameraMotion.energy + Math.hypot(dx, dy) * 0.012, 0, 1);
  }

  function renderStageParallax() {
    if (!stage) return;
    stageParallax.tx *= PARALLAX_RETURN;
    stageParallax.ty *= PARALLAX_RETURN;
    stageParallax.x += (stageParallax.tx - stageParallax.x) * 0.08;
    stageParallax.y += (stageParallax.ty - stageParallax.y) * 0.08;
    stage.style.backgroundPosition = `calc(50% + ${stageParallax.x.toFixed(2)}px) calc(50% + ${stageParallax.y.toFixed(2)}px)`;
  }

  function setInfo(title, subtitle, coords, bullets) {
    if (infoTitle) infoTitle.textContent = title || "";
    if (infoSubtitle) infoSubtitle.textContent = subtitle || "";
    if (infoCoords) infoCoords.textContent = coords || "";
    if (infoList) {
      infoList.innerHTML = "";
      (bullets || []).forEach((b) => {
        const li = document.createElement("li");
        li.textContent = b;
        infoList.appendChild(li);
      });
    }
  }

  function fmtOrgUsd(value) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    const amount = Number(value);
    if (Math.abs(amount) >= 1e9) return `$${(amount / 1e9).toFixed(2)}B`;
    if (Math.abs(amount) >= 1e6) return `$${(amount / 1e6).toFixed(1)}M`;
    if (Math.abs(amount) >= 1e3) return `$${(amount / 1e3).toFixed(0)}K`;
    return `$${amount.toFixed(0)}`;
  }

  function fmtOrgPoints(value) {
    if (value == null || !Number.isFinite(Number(value))) return "—";
    return `${Number(value).toFixed(1)} RP`;
  }

  function renderMapOrgSummary(org) {
    if (!mapOrgBalanceEl || !mapOrgIncomeEl || !mapOrgResearchEl || !mapOrgExpensesEl) return;

    if (!org || typeof org !== "object") {
      mapOrgBalanceEl.textContent = "—";
      mapOrgIncomeEl.textContent = "—";
      mapOrgResearchEl.textContent = "—";
      mapOrgExpensesEl.textContent = "—";
      return;
    }

    const teams = Array.isArray(org.research_teams) ? org.research_teams : [];
    const activeTeamCount = teams.filter((team) => team?.status === "active").length;
    const teamCost = Number(org.team_cost_per_month_usd) || 150000000;
    const monthlyExpenses = Number.isFinite(Number(org.monthly_expenses_usd))
      ? Number(org.monthly_expenses_usd)
      : (activeTeamCount * teamCost);

    mapOrgBalanceEl.textContent = fmtOrgUsd(org.balance_usd);
    mapOrgIncomeEl.textContent = `${fmtOrgUsd(org.income_per_month_usd)}/mo`;
    mapOrgResearchEl.textContent = fmtOrgPoints(org.research_points);
    mapOrgExpensesEl.textContent = `${fmtOrgUsd(monthlyExpenses)}/mo`;
  }

  async function syncMapOrgSummary() {
    if (!mapOrgBalanceEl) return;
    try {
      const resp = await fetch("/api/org", { cache: "no-store" });
      if (!resp.ok) return;
      const data = await resp.json();
      renderMapOrgSummary(data?.org || null);
    } catch {
      // Keep current values if summary refresh fails.
    }
  }

  function loadWindowLayoutState() {
    try {
      const raw = localStorage.getItem(MAP_WINDOW_LAYOUT_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  function saveWindowLayoutState(state) {
    try {
      localStorage.setItem(MAP_WINDOW_LAYOUT_KEY, JSON.stringify(state));
    } catch {
      // Ignore storage failures.
    }
  }

  function setupMapWindows() {
    const content = document.querySelector(".mapPage .content");
    if (!content) return;
    const appWindowLayerEl = document.getElementById("appWindowLayer");
    const mapDockEl = document.querySelector(".mapPage .mapDock");
    const mapDockWindowConfig = {
      overview: { icon: "/static/img/dock/map.png", tooltip: "Map Overview" },
      info: { icon: "/static/img/dock/info.png", tooltip: "Details" },
    };

    const layoutState = loadWindowLayoutState();
    const panelRecords = new Map();

    function bootstrapDockButton(btn, iconSrc) {
      if (!btn) return;
      const labelText = (
        btn.querySelector(".mapDockBtnLabel")?.textContent ||
        btn.getAttribute("aria-label") ||
        btn.textContent ||
        ""
      ).trim();
      if (!btn.querySelector(".mapDockBtnLabel")) {
        btn.textContent = "";
        const iconEl = document.createElement("span");
        iconEl.className = "mapDockBtnIcon";
        iconEl.setAttribute("aria-hidden", "true");
        const labelEl = document.createElement("span");
        labelEl.className = "mapDockBtnLabel";
        labelEl.textContent = labelText;
        btn.append(iconEl, labelEl);
      }
      if (labelText) {
        btn.setAttribute("aria-label", labelText);
        btn.setAttribute("data-tooltip", labelText);
        btn.removeAttribute("title");
      }
      if (iconSrc) {
        btn.classList.add("hasIcon");
        btn.style.setProperty("--dock-icon", `url(\"${iconSrc}\")`);
      }
    }

    if (mapDockEl) {
      Object.entries(mapDockWindowConfig).forEach(([panelId, cfg]) => {
        const btn = mapDockEl.querySelector(`[data-map-window='${panelId}']`);
        bootstrapDockButton(btn, cfg?.icon || "");
        if (btn && cfg?.tooltip) {
          btn.setAttribute("aria-label", cfg.tooltip);
          btn.setAttribute("data-tooltip", cfg.tooltip);
        }
      });
    }

    function bringWindowToFront(panelEl) {
      let topZ = Number.isFinite(mapWindowZIndex) ? mapWindowZIndex : 20;
      Array.from(content.children).forEach((el) => {
        if (!el?.classList?.contains("mapWindow")) return;
        if (!el) return;
        const inlineZ = Number(el.style?.zIndex);
        if (Number.isFinite(inlineZ)) {
          topZ = Math.max(topZ, inlineZ);
          return;
        }
        const computedZ = Number(window.getComputedStyle(el).zIndex);
        if (Number.isFinite(computedZ)) topZ = Math.max(topZ, computedZ);
      });
      mapWindowZIndex = topZ + 1;
      panelEl.style.zIndex = String(mapWindowZIndex);
      content.querySelectorAll(".mapWindow.isSelected").forEach((el) => {
        el.classList.remove("isSelected");
      });
      panelEl.classList.add("isSelected");
      if (appWindowLayerEl) {
        appWindowLayerEl.style.zIndex = String(Math.max(1, mapWindowZIndex - 1));
      }
    }

    function setMapDockOpen(panelId, open) {
      if (!mapDockEl) return;
      const btn = mapDockEl.querySelector(`[data-map-window='${panelId}']`);
      if (!btn) return;
      btn.classList.toggle("isOpen", !!open);
      btn.setAttribute("aria-pressed", open ? "true" : "false");
    }

    function clampPanelRect(rect, limits, minW, minH) {
      const width = clamp(rect.width, minW, Math.max(minW, limits.maxWidth));
      const height = clamp(rect.height, minH, Math.max(minH, limits.maxHeight));
      const left = clamp(rect.left, limits.minLeft, limits.maxLeft - width);
      const top = clamp(rect.top, limits.minTop, limits.maxTop - height);
      return { left, top, width, height };
    }

    function contentLimits() {
      const r = content.getBoundingClientRect();
      return {
        minLeft: 0,
        minTop: 12,
        maxLeft: Math.max(0, r.width),
        maxTop: Math.max(0, r.height),
        maxWidth: Math.max(220, r.width - 8),
        maxHeight: Math.max(180, r.height - 16),
      };
    }

    function applyRect(panelEl, rect) {
      panelEl.style.left = `${Math.round(rect.left)}px`;
      panelEl.style.top = `${Math.round(rect.top)}px`;
      panelEl.style.width = `${Math.round(rect.width)}px`;
      panelEl.style.height = `${Math.round(rect.height)}px`;
      panelEl.style.right = "auto";
      panelEl.style.maxHeight = "none";
    }

    function setupPanel(panelEl, panelId, minW, minH) {
      if (!panelEl) return;
      const dragHandle = panelEl.querySelector("[data-drag-handle='true']");
      if (!dragHandle) return;

      function ensureResizeHandles() {
        panelEl.querySelectorAll("[data-resize-handle='true']").forEach((el) => el.remove());
        const dirs = ["n", "e", "s", "w", "ne", "nw", "se", "sw"];
        return dirs.map((dir) => {
          const handle = document.createElement("div");
          handle.className = `mapWindowResize mapWindowResize--${dir}`;
          handle.setAttribute("data-resize-handle", "true");
          handle.setAttribute("data-resize-dir", dir);
          handle.setAttribute("aria-hidden", "true");
          panelEl.appendChild(handle);
          return handle;
        });
      }

      const resizeHandles = ensureResizeHandles();

      if (!dragHandle.querySelector(".mapWindowHeaderRow")) {
        const headerItems = Array.from(dragHandle.children);
        const headerRow = document.createElement("div");
        headerRow.className = "mapWindowHeaderRow";
        const titleWrap = document.createElement("div");
        headerItems.forEach((child) => titleWrap.appendChild(child));
        const actionsWrap = document.createElement("div");
        actionsWrap.className = "mapWindowActions";

        const minBtn = document.createElement("button");
        minBtn.className = "mapWindowAction";
        minBtn.type = "button";
        minBtn.setAttribute("data-map-window-action", "minimize");
        minBtn.setAttribute("aria-label", "Minimize");
        minBtn.textContent = "—";

        const closeBtn = document.createElement("button");
        closeBtn.className = "mapWindowAction";
        closeBtn.type = "button";
        closeBtn.setAttribute("data-map-window-action", "close");
        closeBtn.setAttribute("aria-label", "Close");
        closeBtn.textContent = "×";

        actionsWrap.append(minBtn, closeBtn);
        headerRow.append(titleWrap, actionsWrap);
        dragHandle.appendChild(headerRow);
      }

      const bodyChildren = Array.from(panelEl.children).filter(
        (child) => child !== dragHandle && !resizeHandles.includes(child)
      );

      const startRect = panelEl.getBoundingClientRect();
      const contentRect = content.getBoundingClientRect();
      const saved = layoutState[panelId];

      const initialRect = saved && typeof saved === "object"
        ? {
            left: Number(saved.left || 0),
            top: Number(saved.top || 0),
            width: Number(saved.width || startRect.width),
            height: Number(saved.height || startRect.height),
          }
        : {
            left: startRect.left - contentRect.left,
            top: startRect.top - contentRect.top,
            width: startRect.width,
            height: startRect.height,
          };

      const boundedInitial = clampPanelRect(initialRect, contentLimits(), minW, minH);
      applyRect(panelEl, boundedInitial);

      const persistRect = (rect) => {
        layoutState[panelId] = {
          ...(layoutState[panelId] || {}),
          left: Math.round(rect.left),
          top: Math.round(rect.top),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        };
        saveWindowLayoutState(layoutState);
      };

      const persistFlags = (flags) => {
        layoutState[panelId] = {
          ...(layoutState[panelId] || {}),
          ...flags,
        };
        saveWindowLayoutState(layoutState);
      };

      const saveCurrentRect = () => {
        const now = panelEl.getBoundingClientRect();
        const contentNow = content.getBoundingClientRect();
        persistRect({
          left: now.left - contentNow.left,
          top: now.top - contentNow.top,
          width: now.width,
          height: now.height,
        });
      };

      function setMinimized(minimized) {
        const isMin = !!minimized;
        if (isMin) {
          saveCurrentRect();
        }
        panelEl.dataset.minimized = isMin ? "true" : "false";
        panelEl.classList.toggle("isMinimized", isMin);
        bodyChildren.forEach((el) => {
          el.style.display = isMin ? "none" : "";
        });
        resizeHandles.forEach((handle) => {
          handle.style.display = isMin ? "none" : "";
        });
        if (isMin) {
          panelEl.style.height = "30px";
        } else {
          const latest = layoutState[panelId] || {};
          const panelNow = panelEl.getBoundingClientRect();
          const contentNow = content.getBoundingClientRect();
          const next = clampPanelRect(
            {
              left: Number.isFinite(latest.left) ? latest.left : panelNow.left - contentNow.left,
              top: Number.isFinite(latest.top) ? latest.top : panelNow.top - contentNow.top,
              width: Number.isFinite(latest.width) ? latest.width : panelNow.width,
              height: Number.isFinite(latest.height) ? latest.height : panelNow.height,
            },
            contentLimits(),
            minW,
            minH
          );
          applyRect(panelEl, next);
        }
        persistFlags({ minimized: isMin });
      }

      function showPanel() {
        panelEl.style.display = "block";
        bringWindowToFront(panelEl);
        setMapDockOpen(panelId, true);
        persistFlags({ open: true });
      }

      function hidePanel() {
        setMinimized(false);
        panelEl.style.display = "none";
        panelEl.classList.remove("isSelected");
        setMapDockOpen(panelId, false);
        persistFlags({ open: false, minimized: false });
      }

      function togglePanel() {
        if (panelEl.style.display === "none") {
          showPanel();
          return;
        }
        hidePanel();
      }

      function startDrag(event) {
        if (event.button !== 0) return;
        if (event.target.closest("button,a,input,select,textarea")) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(panelEl);
        panelEl.classList.add("isDragging");

        const baseRect = panelEl.getBoundingClientRect();
        const contentNow = content.getBoundingClientRect();
        const origin = {
          x: event.clientX,
          y: event.clientY,
          left: baseRect.left - contentNow.left,
          top: baseRect.top - contentNow.top,
          width: baseRect.width,
          height: baseRect.height,
        };

        const onMove = (moveEvent) => {
          const limits = contentLimits();
          const next = clampPanelRect(
            {
              left: origin.left + (moveEvent.clientX - origin.x),
              top: origin.top + (moveEvent.clientY - origin.y),
              width: origin.width,
              height: origin.height,
            },
            limits,
            minW,
            minH
          );
          applyRect(panelEl, next);
        };

        const stop = () => {
          panelEl.classList.remove("isDragging");
          const now = panelEl.getBoundingClientRect();
          const contentNow2 = content.getBoundingClientRect();
          persistRect({
            left: now.left - contentNow2.left,
            top: now.top - contentNow2.top,
            width: now.width,
            height: now.height,
          });
          window.removeEventListener("pointermove", onMove);
          window.removeEventListener("pointerup", stop);
          window.removeEventListener("pointercancel", stop);
        };

        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", stop);
        window.addEventListener("pointercancel", stop);
      }

      function startResize(event) {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(panelEl);
        panelEl.classList.add("isResizing");
        const dir = String(event.currentTarget?.dataset?.resizeDir || "se").toLowerCase();

        const baseRect = panelEl.getBoundingClientRect();
        const contentNow = content.getBoundingClientRect();
        const origin = {
          x: event.clientX,
          y: event.clientY,
          left: baseRect.left - contentNow.left,
          top: baseRect.top - contentNow.top,
          width: baseRect.width,
          height: baseRect.height,
        };

        const onMove = (moveEvent) => {
          const limits = contentLimits();
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

          const bounded = clampPanelRect(
            {
              left: nextLeft,
              top: nextTop,
              width: nextWidth,
              height: nextHeight,
            },
            limits,
            minW,
            minH
          );
          applyRect(panelEl, bounded);
        };

        const stop = () => {
          panelEl.classList.remove("isResizing");
          const now = panelEl.getBoundingClientRect();
          const contentNow2 = content.getBoundingClientRect();
          persistRect({
            left: now.left - contentNow2.left,
            top: now.top - contentNow2.top,
            width: now.width,
            height: now.height,
          });
          window.removeEventListener("pointermove", onMove);
          window.removeEventListener("pointerup", stop);
          window.removeEventListener("pointercancel", stop);
        };

        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", stop);
        window.addEventListener("pointercancel", stop);
      }

      dragHandle.addEventListener("pointerdown", startDrag);
      resizeHandles.forEach((handle) => {
        handle.addEventListener("pointerdown", startResize);
      });
      panelEl.addEventListener("pointerdown", () => bringWindowToFront(panelEl));

      panelEl.addEventListener("click", (event) => {
        const btn = event.target.closest("[data-map-window-action]");
        if (!btn) return;
        const action = btn.getAttribute("data-map-window-action");
        if (action === "close") {
          hidePanel();
          return;
        }
        if (action === "minimize") {
          setMinimized(panelEl.dataset.minimized !== "true");
        }
      });

      panelRecords.set(panelId, { panelEl, showPanel, hidePanel, togglePanel, setMinimized });

      const shouldOpen = saved?.open !== false;
      if (!shouldOpen) {
        panelEl.style.display = "none";
        setMapDockOpen(panelId, false);
      } else {
        showPanel();
        if (saved?.minimized) {
          setMinimized(true);
        } else {
          setMinimized(false);
        }
      }
    }

    setupPanel(overviewPanelEl, "overview", 240, 220);
    setupPanel(infoPanelEl, "info", 260, 280);
    mapPanelControllers = panelRecords;

    if (mapDockEl) {
      mapDockEl.addEventListener("click", (event) => {
        const btn = event.target.closest("[data-map-window]");
        if (!btn) return;
        const panelId = btn.getAttribute("data-map-window");
        if (!panelId) return;
        const record = panelRecords.get(panelId);
        if (!record) return;
        record.togglePanel();
      });
    }

    window.addEventListener("resize", () => {
      [
        { el: overviewPanelEl, id: "overview", minW: 240, minH: 220 },
        { el: infoPanelEl, id: "info", minW: 260, minH: 280 },
      ].forEach((entry) => {
        if (!entry.el) return;
        if (entry.el.style.display === "none") return;
        if (entry.el.dataset.minimized === "true") return;
        const r = entry.el.getBoundingClientRect();
        const cr = content.getBoundingClientRect();
        const next = clampPanelRect(
          {
            left: r.left - cr.left,
            top: r.top - cr.top,
            width: r.width,
            height: r.height,
          },
          contentLimits(),
          entry.minW,
          entry.minH
        );
        applyRect(entry.el, next);
        layoutState[entry.id] = {
          ...(layoutState[entry.id] || {}),
          left: Math.round(next.left),
          top: Math.round(next.top),
          width: Math.round(next.width),
          height: Math.round(next.height),
        };
      });
      saveWindowLayoutState(layoutState);
    });
  }

  function ensureInfoPanelVisible() {
    const record = mapPanelControllers?.get?.("info");
    if (record) {
      record.setMinimized(false);
      record.showPanel();
      return;
    }

    if (infoPanelEl) {
      infoPanelEl.style.display = "block";
      infoPanelEl.dataset.minimized = "false";
      infoPanelEl.classList.remove("isMinimized");
      infoPanelEl.querySelectorAll(".mapWindowBody, .mapWindowResize").forEach((el) => {
        el.style.display = "";
      });
      infoPanelEl.style.height = "";
      mapWindowZIndex += 1;
      infoPanelEl.style.zIndex = String(mapWindowZIndex);
      infoPanelEl.classList.add("isSelected");
      const appWindowLayerEl = document.getElementById("appWindowLayer");
      if (appWindowLayerEl) {
        appWindowLayerEl.style.zIndex = String(Math.max(1, mapWindowZIndex - 1));
      }
    }

    const infoDockBtn = document.querySelector(".mapPage .mapDock [data-map-window='info']");
    if (infoDockBtn) {
      infoDockBtn.classList.add("isOpen");
      infoDockBtn.setAttribute("aria-pressed", "true");
    }
  }

  const wikiSummaryCache = new Map();
  const wikiInfoboxFactsCache = new Map();
  let wikiRequestSeq = 0;
  let locationInventoryReqSeq = 0;

  function clearRealWorldReference() {
    wikiRequestSeq += 1;
    if (realWorldRef) realWorldRef.innerHTML = "";
  }

  function wikiTitleForLocation(loc) {
    if (!loc) return null;
    const configuredTitle = String(loc.wikipedia_title || "").trim();
    if (configuredTitle) return configuredTitle;
    const fallbackName = String(loc.name || "").trim();
    return fallbackName || null;
  }

  function wikiUrlForLocation(loc, summary, fallbackTitle) {
    const configuredUrl = String(loc?.wikipedia_url || "").trim();
    if (configuredUrl) return configuredUrl;
    return summary?.content_urls?.desktop?.page
      || `https://en.wikipedia.org/wiki/${encodeURIComponent(String(fallbackTitle || "").replace(/\s+/g, "_"))}`;
  }

  function buildWikiLoadingCard(title) {
    const card = document.createElement("div");
    card.className = "wikiCard";
    const heading = document.createElement("div");
    heading.className = "wikiTitle";
    heading.textContent = title;
    const loading = document.createElement("div");
    loading.className = "muted small";
    loading.textContent = "Loading real-world reference…";
    card.append(heading, loading);
    return card;
  }

  function cleanInfoboxValue(raw) {
    const text = String(raw || "")
      .replace(/\[[^\]]+\]/g, "")
      .replace(/\s+/g, " ")
      .trim();
    if (!text) return "";
    return text.length > 120 ? `${text.slice(0, 117)}…` : text;
  }

  function parseInfoboxFactsFromHtml(html) {
    if (!html || typeof DOMParser === "undefined") return null;
    const doc = new DOMParser().parseFromString(html, "text/html");
    const infobox = doc.querySelector("table.infobox");
    if (!infobox) return null;

    const rows = Array.from(infobox.querySelectorAll("tr"));
    const result = { mass: "", radius: "", gravity: "" };

    const labelMatches = {
      mass: ["mass"],
      radius: ["mean radius", "radius", "equatorial radius"],
      gravity: ["surface gravity", "gravity"],
    };

    for (const row of rows) {
      const th = row.querySelector("th");
      const td = row.querySelector("td");
      if (!th || !td) continue;

      const label = cleanInfoboxValue(th.textContent).toLowerCase();
      if (!label) continue;
      const value = cleanInfoboxValue(td.textContent);
      if (!value) continue;

      if (!result.mass && labelMatches.mass.some((k) => label.includes(k))) result.mass = value;
      if (!result.radius && labelMatches.radius.some((k) => label.includes(k))) result.radius = value;
      if (!result.gravity && labelMatches.gravity.some((k) => label.includes(k))) result.gravity = value;

      if (result.mass && result.radius && result.gravity) break;
    }

    if (!result.mass && !result.radius && !result.gravity) return null;
    return result;
  }

  async function fetchWikipediaInfoboxFacts(wikiTitle) {
    if (!wikiTitle) return null;
    const endpoint = `https://en.wikipedia.org/w/api.php?action=parse&page=${encodeURIComponent(wikiTitle)}&prop=text&format=json&formatversion=2&origin=*`;
    const resp = await fetch(endpoint, { cache: "force-cache" });
    if (!resp.ok) throw new Error(`Wikipedia infobox request failed (${resp.status})`);
    const data = await resp.json();
    return parseInfoboxFactsFromHtml(data?.parse?.text || "");
  }

  function renderWikiCard(summary, fallbackTitle, infoboxFacts = null, loc = null) {
    if (!realWorldRef) return;
    realWorldRef.innerHTML = "";

    const title = summary?.title || fallbackTitle;
    const extract = summary?.extract || "No reference summary available.";
    const articleUrl = wikiUrlForLocation(loc, summary, fallbackTitle);
    const thumbUrl = summary?.thumbnail?.source || null;

    const card = document.createElement("div");
    card.className = "wikiCard";

    const head = document.createElement("div");
    head.className = "wikiHead";

    if (thumbUrl) {
      const img = document.createElement("img");
      img.className = "wikiThumb";
      img.src = thumbUrl;
      img.alt = `${title} reference image`;
      img.loading = "lazy";
      head.appendChild(img);
    }

    const titleWrap = document.createElement("div");
    titleWrap.className = "wikiTitleWrap";

    const heading = document.createElement("div");
    heading.className = "wikiTitle";
    heading.textContent = title;

    const sub = document.createElement("div");
    sub.className = "muted small";
    sub.textContent = "Real-world reference (Wikipedia)";

    titleWrap.append(heading, sub);
    head.appendChild(titleWrap);

    const body = document.createElement("div");
    body.className = "wikiExtract";
    body.textContent = extract;

    const facts = document.createElement("div");
    facts.className = "wikiFacts";
    const factRows = [
      ["Mass", infoboxFacts?.mass],
      ["Radius", infoboxFacts?.radius],
      ["Surface gravity", infoboxFacts?.gravity],
    ].filter(([, v]) => !!v);
    if (factRows.length) {
      for (const [label, value] of factRows) {
        const row = document.createElement("div");
        row.className = "wikiFactRow";
        const k = document.createElement("span");
        k.className = "wikiFactLabel";
        k.textContent = `${label}:`;
        const v = document.createElement("span");
        v.className = "wikiFactValue";
        v.textContent = value;
        row.append(k, v);
        facts.appendChild(row);
      }
    }

    const links = document.createElement("div");
    links.className = "wikiLinks";
    const a = document.createElement("a");
    a.href = articleUrl;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = "Open full article";
    links.appendChild(a);

    card.append(head, body);
    if (factRows.length) card.appendChild(facts);
    card.appendChild(links);
    realWorldRef.appendChild(card);
  }

  async function showRealWorldReference(loc) {
    if (!realWorldRef) return;
    const wikiTitle = wikiTitleForLocation(loc);
    if (!wikiTitle) {
      clearRealWorldReference();
      return;
    }

    const reqId = ++wikiRequestSeq;
    realWorldRef.innerHTML = "";
    realWorldRef.appendChild(buildWikiLoadingCard(wikiTitle));

    try {
      let summary = wikiSummaryCache.get(wikiTitle);
      if (!summary) {
        const endpoint = `https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(wikiTitle)}`;
        const resp = await fetch(endpoint, { cache: "force-cache" });
        if (!resp.ok) throw new Error(`Wikipedia summary request failed (${resp.status})`);
        summary = await resp.json();
        wikiSummaryCache.set(wikiTitle, summary);
      }

      let infoboxFacts = wikiInfoboxFactsCache.get(wikiTitle);
      if (infoboxFacts === undefined) {
        try {
          infoboxFacts = await fetchWikipediaInfoboxFacts(wikiTitle);
        } catch (factsErr) {
          console.warn(factsErr);
          infoboxFacts = null;
        }
        wikiInfoboxFactsCache.set(wikiTitle, infoboxFacts);
      }

      if (reqId !== wikiRequestSeq) return;
      renderWikiCard(summary, wikiTitle, infoboxFacts || null, loc);
    } catch (err) {
      if (reqId !== wikiRequestSeq) return;
      console.warn(err);
      renderWikiCard(null, wikiTitle, null, loc);
    }
  }

  // ---------- Guard: PIXI must exist ----------
  if (typeof PIXI === "undefined") {
    setInfo("Map error", "PIXI failed to load", "", [
      "Check /static/js/pixi.min.js returns 200",
      "Open devtools console/network for the error",
    ]);
    throw new Error("PIXI is undefined");
  }

  // ---------- Pixi init (NO wallpaper texture; CSS handles background) ----------
  const app = new PIXI.Application({
    resizeTo: stage,
    antialias: true,
    backgroundAlpha: 0,
  });
  stage.appendChild(app.view);

  // WebGL recovery
  app.view.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    setInfo("WebGL context lost", "Try refresh / reduce GPU load", "", [
      "Wallpaper should be CSS, not a Pixi texture.",
    ]);
  });
  app.view.addEventListener("webglcontextrestored", () => window.location.reload());

  setupMapWindows();

  // ---------- Layers ----------
  const world = new PIXI.Container();
  app.stage.addChild(world);

  const orbitLayer = new PIXI.Graphics();      // rings
  const planetLayer = new PIXI.Container();    // earth/luna
  const locLayer = new PIXI.Container();       // node dots (excluding orbits)
  const transitPathLayer = new PIXI.Container(); // faint full transit paths
  const shipLayer = new PIXI.Container();      // ships
  const labelLayer = new PIXI.Container();     // labels (nodes + orbit hover labels + planet labels)
  const shipClusterLayer = new PIXI.Container(); // +N docked ship cluster markers
  const dustLayer = new PIXI.Container();      // subtle screen-space movement particles

  world.addChild(orbitLayer, planetLayer, locLayer, transitPathLayer, shipLayer, labelLayer, shipClusterLayer);
  app.stage.addChild(dustLayer);

  function ensureDustField() {
    const width = Math.max(1, app.renderer.width);
    const height = Math.max(1, app.renderer.height);
    const targetCount = Math.max(16, Math.min(42, Math.round((width * height) / 38000)));
    if (dustParticles.length === targetCount) return;

    dustLayer.removeChildren();
    dustParticles.length = 0;

    for (let i = 0; i < targetCount; i++) {
      const particle = new PIXI.Graphics();
      const radius = 0.5 + Math.random() * 1.4;
      const depth = 0.35 + Math.random() * 0.85;
      particle.beginFill(0xb7dbff, 1);
      particle.drawCircle(0, 0, radius);
      particle.endFill();
      particle.x = Math.random() * width;
      particle.y = Math.random() * height;
      particle.alpha = DUST_BASE_ALPHA * (0.6 + depth * 0.5);
      dustLayer.addChild(particle);
      dustParticles.push({
        sprite: particle,
        depth,
        vx: (Math.random() - 0.5) * 0.06,
        vy: (Math.random() - 0.5) * 0.06,
      });
    }
  }

  function wrapParticle(particle, width, height) {
    if (particle.x < -4) particle.x = width + 4;
    else if (particle.x > width + 4) particle.x = -4;
    if (particle.y < -4) particle.y = height + 4;
    else if (particle.y > height + 4) particle.y = -4;
  }

  function updateDustField() {
    ensureDustField();
    const width = Math.max(1, app.renderer.width);
    const height = Math.max(1, app.renderer.height);
    const activeAlphaBoost = cameraMotion.energy * DUST_ACTIVE_ALPHA;

    for (const p of dustParticles) {
      const motionScale = 0.012 * p.depth;
      p.sprite.x += p.vx + cameraMotion.x * motionScale;
      p.sprite.y += p.vy + cameraMotion.y * motionScale;
      wrapParticle(p.sprite, width, height);

      const targetAlpha = DUST_BASE_ALPHA + activeAlphaBoost * p.depth;
      p.sprite.alpha += (targetAlpha - p.sprite.alpha) * 0.08;
    }

    cameraMotion.x *= 0.86;
    cameraMotion.y *= 0.86;
    cameraMotion.energy *= 0.9;
  }

  // ---------- Camera (pan/zoom) ----------
  let dragging = false;
  let last = { x: 0, y: 0 };

  app.view.addEventListener("pointerdown", (e) => {
    hideContextMenu();
    if (e.button !== 0) return;
    dragging = true;
    last = { x: e.clientX, y: e.clientY };
  });

  app.view.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    hideContextMenu();
    const target = resolveContextTargetAtClientPoint(e.clientX, e.clientY);
    if (!target) return;

    if (target.kind === "docked-chip") {
      openDockedShipPickerMenu(target.locationId, e);
      return;
    }

    if (target.kind === "ship") {
      const ship = ships.find((s) => s.id === target.id);
      if (ship) openShipContextMenu(ship, e);
      return;
    }

    if (target.kind === "location") {
      const loc = locationsById.get(target.id);
      if (loc) openLocationContextMenu(loc, e);
      return;
    }

    if (target.kind === "body") {
      openBodyContextMenu(target.id, e);
    }
  });

  app.view.addEventListener("click", (e) => {
    if (Number(e.button) !== 0) return;
    const target = resolveContextTargetAtClientPoint(e.clientX, e.clientY);
    if (!target) return;
    if (target.kind === "docked-chip") {
      openDockedShipPickerMenu(target.locationId, e);
      return;
    }
    if (target.kind !== "location") return;
    if (!ORBIT_IDS.has(target.id)) return;
    const loc = locationsById.get(target.id);
    if (!loc) return;
    showLocationInfo(loc);
  });

  window.addEventListener("pointerup", () => (dragging = false));
  window.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - last.x;
    const dy = e.clientY - last.y;
    world.x += dx;
    world.y += dy;
    nudgeStageParallax(-dx * PARALLAX_PAN_FACTOR, -dy * PARALLAX_PAN_FACTOR);
    registerCameraMotion(dx, dy);
    markOrbitsDirty();
    last = { x: e.clientX, y: e.clientY };
  });

  app.view.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const wheelDeltaY = Number(e.deltaY) || 0;
      if (wheelDeltaY === 0) return;

      const oldScale = world.scale.x;
      const scaleFactor = Math.exp(-wheelDeltaY * CAMERA_WHEEL_SENSITIVITY);
      const newScale = clamp(oldScale * scaleFactor, CAMERA_MIN_SCALE, CAMERA_MAX_SCALE);
      if (Math.abs(newScale - oldScale) < 1e-9) return;

      const rect = app.view.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;

      const wx = (mx - world.x) / oldScale;
      const wy = (my - world.y) / oldScale;

      world.scale.set(newScale);
      world.x = mx - wx * newScale;
      world.y = my - wy * newScale;

      nudgeStageParallax((e.deltaX || 0) * PARALLAX_WHEEL_FACTOR, wheelDeltaY * PARALLAX_WHEEL_FACTOR);
      registerCameraMotion((e.deltaX || 0) * 0.28, wheelDeltaY * 0.28);
      markOrbitsDirty();
      refreshZoomScaledTextResolution();
      applyZoomDetailVisibility();
    },
    { passive: false }
  );

  app.ticker.add(() => {
    try {
      renderStageParallax();
      updateDustField();
    } catch (err) {
      console.error("Parallax/dust tick failed:", err);
    }
  });

  // ---------- State ----------
  let serverSyncGameS = Date.now() / 1000;
  let clientSyncRealS = Date.now() / 1000;
  let timeScale = 1;
  let locations = [];
  let locationsById = new Map();
  let leaves = [];
  let ships = [];
  let treeCache = null;
  let mapOverviewRenderKey = "";
  const mapOverviewOpenState = new Map();

  // Smooth celestial interpolation state
  const locLerp = new Map();     // id -> { fromRx, fromRy, toRx, toRy }
  let locLerpStartMs = 0;        // performance.now() when last sync arrived
  const LOC_LERP_DURATION_MS = 5000; // matches the 5 s poll interval

  // --- Performance: dirty flags & throttle counters ---
  let orbitRingsDirty = true;          // redraw orbit rings only when needed
  let lastOrbitZoom = -1;              // track zoom to detect changes
  let tickCounter = 0;                 // frame counter for throttling
  const TEXT_CULL_EVERY_N = 6;         // run text collision culling every N frames
  const OVERVIEW_EVERY_N = 10;         // run map overview rebuild every N frames
  function markOrbitsDirty() { orbitRingsDirty = true; }

  const locGfx = new Map();   // id -> {dot,label,kind,hovered}
  const shipGfx = new Map();  // id -> {ship,container,slot}
  const shipClusterLabels = new Map(); // location_id -> PIXI.Text
  const dockedChipGfx = new Map(); // location_id -> {container,bg,text,hitRadiusWorld}
  let selectedShipId = null;
  let hoveredShipId = null;
  let shipInfoTab = "details";
  let shipInfoTabShipId = null;
  let locationInfoTab = "details";
  let locationInfoTabLocationId = null;
  let locationParentById = new Map();
  let overviewVisibleZoneIds = new Set();
  let cameraTweenToken = 0;
  let contextMenuEl = null;
  let syncStatePromise = null;
  const TRANSIT_ANCHOR_BUCKET_S = 6 * 3600;
  const transitAnchorSnapshots = new Map();
  const transitAnchorSnapshotInflight = new Map();
  const HANGAR_WINDOW_EVENT = "earthmoon:open-hangar-window";

  function selectedShip() {
    if (!selectedShipId) return null;
    return ships.find((s) => s.id === selectedShipId) || null;
  }

  function contextPointerFromEvent(e) {
    const raw = e?.data?.originalEvent || e;
    if (raw && Number.isFinite(Number(raw.clientX)) && Number.isFinite(Number(raw.clientY))) {
      return { x: Number(raw.clientX), y: Number(raw.clientY) };
    }
    const rect = app.view.getBoundingClientRect();
    const gx = Number(e?.data?.global?.x) || 0;
    const gy = Number(e?.data?.global?.y) || 0;
    return { x: rect.left + gx, y: rect.top + gy };
  }

  function isSecondaryPointerEvent(e) {
    const raw = e?.data?.originalEvent || e;
    const button = Number(raw?.button);
    const which = Number(raw?.which);
    return button === 2 || which === 3;
  }

  function resolveContextTargetAtClientPoint(clientX, clientY) {
    const rect = app.view.getBoundingClientRect();
    const localX = Number(clientX) - rect.left;
    const localY = Number(clientY) - rect.top;
    if (!Number.isFinite(localX) || !Number.isFinite(localY)) return null;
    if (localX < 0 || localY < 0 || localX > rect.width || localY > rect.height) return null;

    const worldPoint = world.toLocal(new PIXI.Point(localX, localY));
    const zoom = Math.max(0.0001, Number(world.scale.x) || 1);

    let bestShip = null;
    let bestShipD2 = Number.POSITIVE_INFINITY;
    const minShipWorld = MIN_SHIP_HIT_SCREEN_PX / zoom;
    for (const [shipId, gfx] of shipGfx.entries()) {
      const c = gfx?.container;
      if (!c || c.visible === false) continue;
      const shipHitRadius = c.hitArea instanceof PIXI.Circle
        ? Math.max(minShipWorld, Number(c.hitArea.radius) || 0)
        : minShipWorld;
      const dx = worldPoint.x - Number(c.x || 0);
      const dy = worldPoint.y - Number(c.y || 0);
      const d2 = dx * dx + dy * dy;
      if (d2 <= shipHitRadius * shipHitRadius && d2 < bestShipD2) {
        bestShip = shipId;
        bestShipD2 = d2;
      }
    }
    if (bestShip) return { kind: "ship", id: bestShip };

    let bestChipLocId = null;
    let bestChipD2 = Number.POSITIVE_INFINITY;
    for (const [locationId, chip] of dockedChipGfx.entries()) {
      const c = chip?.container;
      if (!c || c.visible === false) continue;
      const chipHitRadius = Math.max(10 / zoom, Number(chip.hitRadiusWorld) || 0);
      const dx = worldPoint.x - Number(c.x || 0);
      const dy = worldPoint.y - Number(c.y || 0);
      const d2 = dx * dx + dy * dy;
      if (d2 <= chipHitRadius * chipHitRadius && d2 < bestChipD2) {
        bestChipLocId = locationId;
        bestChipD2 = d2;
      }
    }
    if (bestChipLocId) return { kind: "docked-chip", locationId: bestChipLocId };

    const orbitTolWorld = 16 / zoom;
    let bestOrbit = null;
    let bestOrbitErr = Number.POSITIVE_INFINITY;
    for (const orbitId of ORBIT_IDS) {
      const oi = orbitInfo.get(orbitId);
      if (!oi) continue;
      const d = Math.hypot(worldPoint.x - Number(oi.cx), worldPoint.y - Number(oi.cy));
      const err = Math.abs(d - Number(oi.radius));
      if (err <= orbitTolWorld && err < bestOrbitErr) {
        bestOrbit = orbitId;
        bestOrbitErr = err;
      }
    }
    if (bestOrbit) return { kind: "location", id: bestOrbit };

    const locTolWorld = MIN_LOC_HIT_SCREEN_PX / zoom;
    let bestLoc = null;
    let bestLocD2 = Number.POSITIVE_INFINITY;
    for (const loc of locations) {
      if (!loc || !!loc.is_group) continue;
      if (!Number.isFinite(Number(loc.rx)) || !Number.isFinite(Number(loc.ry))) continue;
      const dx = worldPoint.x - Number(loc.rx);
      const dy = worldPoint.y - Number(loc.ry);
      const d2 = dx * dx + dy * dy;
      if (d2 <= locTolWorld * locTolWorld && d2 < bestLocD2) {
        bestLoc = loc.id;
        bestLocD2 = d2;
      }
    }
    if (bestLoc) return { kind: "location", id: bestLoc };

    const bodyTolWorld = 24 / zoom;
    let bestBody = null;
    let bestBodyD2 = Number.POSITIVE_INFINITY;
    for (const loc of locations) {
      if (!loc || !loc.is_group || !String(loc.id || "").startsWith("grp_")) continue;
      if (!Number.isFinite(Number(loc.rx)) || !Number.isFinite(Number(loc.ry))) continue;
      const dx = worldPoint.x - Number(loc.rx);
      const dy = worldPoint.y - Number(loc.ry);
      const d2 = dx * dx + dy * dy;
      if (d2 <= bodyTolWorld * bodyTolWorld && d2 < bestBodyD2) {
        bestBody = loc.id;
        bestBodyD2 = d2;
      }
    }
    if (bestBody) return { kind: "body", id: bestBody };

    return null;
  }

  function ensureContextMenuEl() {
    if (contextMenuEl) return contextMenuEl;
    const el = document.createElement("div");
    el.id = "mapContextMenu";
    el.className = "mapContextMenu";
    el.setAttribute("role", "menu");
    el.style.display = "none";
    document.body.appendChild(el);
    contextMenuEl = el;
    return el;
  }

  function hideContextMenu() {
    if (!contextMenuEl) return;
    contextMenuEl.classList.remove("isOpen");
    contextMenuEl.style.display = "none";
    contextMenuEl.innerHTML = "";
  }

  function showContextMenu(menuTitle, items, clientX, clientY) {
    const options = Array.isArray(items) ? items.filter(Boolean) : [];
    if (!options.length) {
      hideContextMenu();
      return;
    }

    const menu = ensureContextMenuEl();
    menu.innerHTML = "";

    if (menuTitle) {
      const title = document.createElement("div");
      title.className = "mapContextMenuTitle";
      title.textContent = menuTitle;
      menu.appendChild(title);
    }

    for (const item of options) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "mapContextMenuItem";
      btn.textContent = item.label || "Action";
      btn.disabled = !!item.disabled;
      btn.addEventListener("click", () => {
        hideContextMenu();
        if (!item.disabled && typeof item.onClick === "function") item.onClick();
      });
      btn.addEventListener("contextmenu", (ev) => {
        if (item.disabled || typeof item.onRightClick !== "function") return;
        ev.preventDefault();
        ev.stopPropagation();
        item.onRightClick(ev);
      });
      menu.appendChild(btn);
    }

    menu.style.display = "block";
    menu.classList.add("isOpen");

    const vw = window.innerWidth || 0;
    const vh = window.innerHeight || 0;
    const rect = menu.getBoundingClientRect();
    const pad = 10;
    const left = Math.max(pad, Math.min(clientX, vw - rect.width - pad));
    const top = Math.max(pad, Math.min(clientY, vh - rect.height - pad));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  function requestHangarWindow(detail) {
    if (!detail) return;
    window.dispatchEvent(new CustomEvent(HANGAR_WINDOW_EVENT, { detail }));
  }

  function openShipHangarWindow(ship) {
    if (!ship) return;
    requestHangarWindow({
      kind: "ship",
      id: String(ship.id || ""),
      name: String(ship.name || ship.id || "Ship"),
    });
  }

  function openShipContextMenu(ship, e) {
    if (!ship) return;
    const pt = contextPointerFromEvent(e);
    const actionsList = [
      {
        label: ship.id === selectedShipId ? "Ship selected" : "Select ship",
        disabled: ship.id === selectedShipId,
        onClick: () => {
          selectedShipId = ship.id;
          showShipPanel();
        },
      },
      {
        label: "View ship details",
        onClick: () => {
          ensureInfoPanelVisible();
          selectedShipId = ship.id;
          showShipPanel();
        },
      },
      {
        label: "Open hangar",
        onClick: () => openShipHangarWindow(ship),
      },
    ];

    if (ship.status === "docked") {
      actionsList.push({
        label: "Plan transfer…",
        onClick: () => openTransferPlanner(ship),
      });
    }

    // Add Prospect option if ship has a robonaut
    const shipParts = Array.isArray(ship.parts) ? ship.parts : [];
    const hasRobonaut = shipParts.some((p) => {
      if (!p || typeof p !== "object") return false;
      const cat = String(p.category_id || p.type || p.category || "").toLowerCase();
      return cat === "robonaut" || cat === "robonauts";
    });
    if (hasRobonaut && ship.status === "docked") {
      actionsList.push({
        label: "Prospect…",
        onClick: () => openProspectDialog(ship),
      });
    }

    showContextMenu(ship.name || ship.id, actionsList, pt.x, pt.y);
    e?.stopPropagation?.();
  }

  function openDockedShipPickerMenu(locationId, e) {
    const anchorId = String(locationId || "");
    if (!anchorId) return;

    const dockedShips = ships
      .filter((s) => s?.status === "docked" && dockedChipAnchorIdForLocation(s.location_id) === anchorId)
      .sort((a, b) => String(a?.name || a?.id || "").localeCompare(String(b?.name || b?.id || "")));

    if (!dockedShips.length) return;

    const loc = locationsById.get(anchorId);
    const actions = dockedShips.map((ship) => ({
      label: `${ship.name || ship.id}`,
      onClick: () => {
        selectedShipId = ship.id;
        ensureInfoPanelVisible();
        showShipPanel();
      },
      onRightClick: (ev) => {
        openShipContextMenu(ship, ev);
      },
    }));

    const pt = contextPointerFromEvent(e);
    showContextMenu(`${loc?.name || anchorId} — Docked`, actions, pt.x, pt.y);
    e?.stopPropagation?.();
  }

  function openLocationContextMenu(loc, e) {
    if (!loc) return;
    const pt = contextPointerFromEvent(e);
    const activeShip = selectedShip();
    const actionsList = [];
    const isOrbit = ORBIT_IDS.has(loc.id);

    if (activeShip && activeShip.status === "docked" && !loc.is_group) {
      if (activeShip.location_id === loc.id) {
        actionsList.push({ label: `${activeShip.name} already docked here`, disabled: true });
      } else {
        actionsList.push({
          label: `Move ${activeShip.name} here…`,
          onClick: () => openTransferPlanner(activeShip, loc.id),
        });
      }
    }

    if (!isOrbit) {
      actionsList.push({
        label: "View location details",
        onClick: () => {
          ensureInfoPanelVisible();
          showLocationInfo(loc);
        },
      });
    }

    if (!loc.is_group) {
      /* location inventory placeholder — hangar window is ship-centric */
      void 0;
    }

    showContextMenu(loc.name || loc.id, actionsList, pt.x, pt.y);
    e?.stopPropagation?.();
  }

  function openBodyContextMenu(bodyId, e) {
    const loc = locationsById.get(bodyId);
    if (!loc) return;
    const pt = contextPointerFromEvent(e);
    const activeShip = selectedShip();
    const actionsList = [
      {
        label: "View body details",
        onClick: () => {
          ensureInfoPanelVisible();
          showBodyInfo(bodyId);
        },
      },
    ];

    if (activeShip && activeShip.status === "docked") {
      actionsList.unshift({
        label: "Select an orbital location to move ship",
        disabled: true,
      });
    }

    showContextMenu(loc.name || bodyId, actionsList, pt.x, pt.y);
    e?.stopPropagation?.();
  }

  document.addEventListener("pointerdown", (e) => {
    if (!contextMenuEl || contextMenuEl.style.display === "none") return;
    if (contextMenuEl.contains(e.target)) return;
    hideContextMenu();
  });
  window.addEventListener("blur", hideContextMenu);
  window.addEventListener("resize", hideContextMenu);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideContextMenu();
  });

  function centerCameraOnWorldPoint(wx, wy, animate = true, durationMs = 320) {
    const scale = Math.max(0.0001, Number(world.scale.x) || 1);
    const targetX = (stage.clientWidth / 2) - wx * scale;
    const targetY = (stage.clientHeight / 2) - wy * scale;
    const startX = Number(world.x) || 0;
    const startY = Number(world.y) || 0;

    if (!animate) {
      world.x = targetX;
      world.y = targetY;
      return;
    }

    const token = ++cameraTweenToken;
    const t0 = performance.now();
    const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

    function tick(now) {
      if (token !== cameraTweenToken) return;
      const t = Math.max(0, Math.min(1, (now - t0) / Math.max(1, durationMs)));
      const e = easeOutCubic(t);
      const nextX = startX + (targetX - startX) * e;
      const nextY = startY + (targetY - startY) * e;
      registerCameraMotion(nextX - world.x, nextY - world.y);
      world.x = nextX;
      world.y = nextY;
      markOrbitsDirty();
      if (t < 1) requestAnimationFrame(tick);
    }

    requestAnimationFrame(tick);
  }

  function setZoneGlowFromOverview(zoneIds) {
    overviewVisibleZoneIds = zoneIds instanceof Set ? new Set(zoneIds) : new Set();
    syncZoneJumpButtonStates();
  }

  function syncZoneJumpButtonStates() {
    if (!zoneJumpBarEl) return;
    const buttons = zoneJumpBarEl.querySelectorAll(".zoneJumpBtn[data-zone-id]");
    for (const btn of buttons) {
      const zoneId = String(btn.getAttribute("data-zone-id") || "");
      const isInOverview = overviewVisibleZoneIds.has(zoneId);
      btn.classList.toggle("isInOverview", isInOverview);
    }
  }

  function focusZoneLocation(zoneId) {
    const loc = locationsById.get(zoneId);
    if (!loc || !Number.isFinite(Number(loc.rx)) || !Number.isFinite(Number(loc.ry))) return;
    centerCameraOnWorldPoint(Number(loc.rx), Number(loc.ry), true, 340);
    if (String(zoneId).startsWith("grp_")) showBodyInfo(zoneId);
    else showLocationInfo(loc);
  }

  function buildZoneJumpBar() {
    if (!zoneJumpBarEl) return;
    zoneJumpBarEl.innerHTML = "";

    const zoneBodies = locations
      .filter((loc) => Number(loc?.is_group) === 1 && String(loc?.parent_id || "") === "grp_sun")
      .sort((a, b) => {
        const ao = Number(a.sort_order || 0);
        const bo = Number(b.sort_order || 0);
        if (ao !== bo) return ao - bo;
        return String(a.name || a.id).localeCompare(String(b.name || b.id));
      });

    for (const zone of zoneBodies) {
      const symbol = String(zone.symbol || "").trim() || "•";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "zoneJumpBtn";
      btn.setAttribute("data-zone-id", zone.id);
      btn.setAttribute("aria-label", `Go to ${zone.name || zone.id}`);
      btn.title = `${zone.name || zone.id} Heliocentric Zone`;
      btn.textContent = symbol;
      btn.addEventListener("click", () => focusZoneLocation(zone.id));
      zoneJumpBarEl.appendChild(btn);
    }

    syncZoneJumpButtonStates();
  }


  function serverNow() {
    const realNow = Date.now() / 1000;
    return serverSyncGameS + (realNow - clientSyncRealS) * timeScale;
  }

  function realNowS() {
    return Date.now() / 1000;
  }

  function formatEtaDaysHours(secondsRemaining) {
    const s = Math.max(0, Number(secondsRemaining) || 0);
    const hours = s / 3600;
    if (hours >= 24) {
      const days = hours / 24;
      return `${days.toFixed(1)}d (${hours.toFixed(1)}h)`;
    }
    return `${hours.toFixed(1)}h`;
  }

  function computeFuelNeededKg(dryMassKg, fuelKg, ispS, dvMs) {
    const dry = Math.max(0, Number(dryMassKg) || 0);
    const fuel = Math.max(0, Number(fuelKg) || 0);
    const isp = Math.max(0, Number(ispS) || 0);
    const dv = Math.max(0, Number(dvMs) || 0);
    if (!(dry > 0) || !(fuel > 0) || !(isp > 0) || !(dv > 0)) return 0;
    const g0 = 9.80665;
    const m0 = dry + fuel;
    const mf = m0 / Math.exp(dv / (isp * g0));
    return Math.max(0, Math.min(fuel, m0 - mf));
  }

  function buildLocationInventoryHtml(inventory) {
    const resources = Array.isArray(inventory?.resources) ? inventory.resources : [];
    const parts = Array.isArray(inventory?.parts) ? inventory.parts : [];

    if (!resources.length && !parts.length) {
      return '<li><div class="muted">No inventory at this orbital location.</div></li>';
    }

    const resourcesHtml = resources.length
      ? `<div class="shipInvSection"><div class="shipInvSectionTitle">Resources</div><div class="shipInvGrid" data-grid-type="resources"></div></div>`
      : "";

    const partsHtml = parts.length
      ? `<div class="shipInvSection"><div class="shipInvSectionTitle">Parts</div><div class="shipInvGrid" data-grid-type="parts"></div></div>`
      : "";

    return `<li><div class="shipInvRoot">${resourcesHtml}${partsHtml}</div></li>`;
  }

  function renderLocationInventoryGrids(containerEl, inventory) {
    const ID = window.ItemDisplay;
    if (!ID || !containerEl) return;
    const resources = Array.isArray(inventory?.resources) ? inventory.resources : [];
    const parts = Array.isArray(inventory?.parts) ? inventory.parts : [];

    const resGrid = containerEl.querySelector('[data-grid-type="resources"]');
    if (resGrid) {
      resources.forEach((r) => {
        const cell = ID.createGridCell({
          label: String(r?.name || r?.item_id || "Resource"),
          iconSeed: `resource::${r?.resource_id || r?.item_id || ""}`,
          itemId: r?.resource_id || r?.item_id || "",
          category: "resource",
          mass_kg: Number(r?.mass_kg) || 0,
          volume_m3: Number(r?.volume_m3) || 0,
          quantity: Number(r?.mass_kg) || 0,
          subtitle: "Resource",
        });
        resGrid.appendChild(cell);
      });
    }

    const partGrid = containerEl.querySelector('[data-grid-type="parts"]');
    if (partGrid) {
      parts.forEach((p) => {
        const partData = (p?.part && typeof p.part === "object") ? p.part : {};
        const category = String(partData.type || partData.category_id || "module").toLowerCase();
        const cell = ID.createGridCell({
          label: String(p?.name || p?.item_id || "Part"),
          iconSeed: `part::${p?.item_id || ""}`,
          itemId: p?.item_id || "",
          category: category,
          mass_kg: Number(p?.mass_kg) || 0,
          quantity: Math.max(0, Math.floor(Number(p?.quantity) || 0)),
          subtitle: category,
          branch: partData.branch || "",
          family: partData.thruster_family || "",
          techLevel: partData.tech_level || "",
        });
        partGrid.appendChild(cell);
      });
    }
  }

  async function showOrbitalLocationInventory(loc) {
    if (!loc) return;
    const reqId = ++locationInventoryReqSeq;

    clearRealWorldReference();
    setInfo(loc.name, "Orbital Inventory", `Location: ${loc.id}`, []);
    if (infoList) infoList.innerHTML = '<li><div class="muted">Loading inventory…</div></li>';

    try {
      const resp = await fetch(`/api/inventory/location/${encodeURIComponent(loc.id)}`, { cache: "no-store" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data?.detail || "Failed to load inventory");
      if (reqId !== locationInventoryReqSeq) return;
      if (infoList) {
        infoList.innerHTML = buildLocationInventoryHtml(data);
        renderLocationInventoryGrids(infoList, data);
      }
    } catch (err) {
      if (reqId !== locationInventoryReqSeq) return;
      if (infoList) infoList.innerHTML = `<li><div class="muted">${String(err?.message || "No inventory data.")}</div></li>`;
    }
  }

  function showLocationInfo(loc) {
    if (!loc) return;
    hideContextMenu();
    selectedShipId = null;
    hoveredShipId = null;
    shipInfoTabShipId = null;
    shipInfoTab = "details";
    if (actions) actions.innerHTML = "";

    const isOrbit = ORBIT_IDS.has(loc.id);
    if (locationInfoTabLocationId !== loc.id) {
      locationInfoTabLocationId = loc.id;
      locationInfoTab = "details";
    }
    renderLocationInfoTabs(loc, isOrbit);

    if (isOrbit && locationInfoTab === "inventory") {
      showOrbitalLocationInventory(loc);
      return;
    }

    locationInventoryReqSeq += 1;
    setInfo(loc.name, "", `Location: ${loc.id}`, []);
    showRealWorldReference(loc);
  }

  function bodyDisplayName(bodyId) {
    const loc = locationsById.get(bodyId);
    return loc?.name || bodyId;
  }

  function showBodyInfo(bodyId) {
    const loc = locationsById.get(bodyId);
    if (!loc) return;
    hideContextMenu();
    selectedShipId = null;
    hoveredShipId = null;
    shipInfoTabShipId = null;
    shipInfoTab = "details";
    locationInfoTabLocationId = null;
    locationInfoTab = "details";
    if (shipInfoTabsHost) shipInfoTabsHost.innerHTML = "";
    if (actions) actions.innerHTML = "";
    locationInventoryReqSeq += 1;
    setInfo(bodyDisplayName(bodyId), "Body", `Location: ${loc.id}`, []);
    showRealWorldReference(loc);
  }

  function selectOverviewLocation(locationId) {
    const loc = locationsById.get(locationId);
    if (!loc) return;
    showLocationInfo(loc);
  }

  function selectOverviewShip(shipId) {
    const ship = ships.find((s) => s.id === shipId);
    if (!ship) return;
    selectedShipId = ship.id;
    showShipPanel();
  }

  function currentViewportWorldBounds(paddingPx = 0) {
    const tl = world.toLocal(new PIXI.Point(0, 0));
    const br = world.toLocal(new PIXI.Point(app.renderer.width, app.renderer.height));
    const minX = Math.min(tl.x, br.x);
    const maxX = Math.max(tl.x, br.x);
    const minY = Math.min(tl.y, br.y);
    const maxY = Math.max(tl.y, br.y);
    const padWorld = Math.max(0, Number(paddingPx) || 0) / Math.max(0.0001, Number(world.scale.x) || 1);
    return {
      minX: minX - padWorld,
      maxX: maxX + padWorld,
      minY: minY - padWorld,
      maxY: maxY + padWorld,
    };
  }

  function pointInWorldBounds(x, y, bounds) {
    if (!bounds) return false;
    return x >= bounds.minX && x <= bounds.maxX && y >= bounds.minY && y <= bounds.maxY;
  }

  function circleIntersectsBounds(cx, cy, r, bounds) {
    const nearestX = clamp(cx, bounds.minX, bounds.maxX);
    const nearestY = clamp(cy, bounds.minY, bounds.maxY);
    const dx = cx - nearestX;
    const dy = cy - nearestY;
    return (dx * dx + dy * dy) <= (r * r);
  }

  function computeVisibleMapObjects() {
    const bounds = currentViewportWorldBounds(22);
    const visibleLocationIds = new Set();
    const visibleShipIds = new Set();

    for (const loc of locations) {
      if (!loc) continue;

      if (ORBIT_IDS.has(loc.id)) {
        const oi = orbitInfo.get(loc.id);
        if (oi && circleIntersectsBounds(oi.cx, oi.cy, oi.radius, bounds)) {
          visibleLocationIds.add(loc.id);
        }
        continue;
      }

      if (Number.isFinite(Number(loc.rx)) && Number.isFinite(Number(loc.ry)) && pointInWorldBounds(loc.rx, loc.ry, bounds)) {
        visibleLocationIds.add(loc.id);
      }
    }

    for (const id of Array.from(visibleLocationIds)) {
      let parentId = locationParentById.get(id);
      while (parentId) {
        visibleLocationIds.add(parentId);
        parentId = locationParentById.get(parentId);
      }
    }

    for (const [shipId, gfx] of shipGfx.entries()) {
      const c = gfx?.container;
      if (!c || c.visible === false) continue;
      if (pointInWorldBounds(c.x, c.y, bounds)) visibleShipIds.add(shipId);
    }

    return { visibleLocationIds, visibleShipIds };
  }

  function buildMapOverview(force = false) {
    if (!mapOverviewEl) return;

    const { visibleLocationIds, visibleShipIds } = computeVisibleMapObjects();
    const locationKey = Array.from(visibleLocationIds).sort().join("|");
    const shipKey = Array.from(visibleShipIds).sort().join("|");
    const nextRenderKey = `${locationKey}::${shipKey}`;
    if (!force && nextRenderKey === mapOverviewRenderKey) return;
    mapOverviewRenderKey = nextRenderKey;

    mapOverviewEl.innerHTML = "";

    const header = document.createElement("div");
    header.className = "ovHead";
    header.innerHTML = "<div>Name</div>";
    mapOverviewEl.appendChild(header);

    const dockedShipsByLocation = new Map();
    for (const s of ships) {
      if (!visibleShipIds.has(s.id)) continue;
      if (s.status !== "docked" || !s.location_id) continue;
      if (!dockedShipsByLocation.has(s.location_id)) dockedShipsByLocation.set(s.location_id, []);
      dockedShipsByLocation.get(s.location_id).push(s);
    }
    for (const arr of dockedShipsByLocation.values()) {
      arr.sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
    }

    const childrenByParent = new Map();
    for (const loc of locations) {
      const pid = loc.parent_id || "ROOT";
      if (!childrenByParent.has(pid)) childrenByParent.set(pid, []);
      childrenByParent.get(pid).push(loc);
    }
    for (const arr of childrenByParent.values()) {
      arr.sort((a, b) => {
        const ao = Number(a.sort_order || 0);
        const bo = Number(b.sort_order || 0);
        if (ao !== bo) return ao - bo;
        return String(a.name || a.id).localeCompare(String(b.name || b.id));
      });
    }

    const ROW_INDENT_PX = 14;
    const FOLDER_INDENT_PX = 20;

    function treePrefix(depth, branch = "├─", ancestorHasNext = []) {
      const d = Math.max(0, Number(depth) || 0);
      if (d === 0) return "";
      const guideArr = Array.isArray(ancestorHasNext) ? ancestorHasNext : [];
      if (!guideArr.length) {
        const guides = "│ ".repeat(Math.max(0, d - 1));
        return `${guides}${branch} `;
      }
      const guides = guideArr.map((hasNext) => (hasNext ? "│ " : "  ")).join("");
      return `${guides}${branch} `;
    }

    function locationClickHandler(loc) {
      if (!loc) return () => {};
      return () => {
        if (String(loc.id).startsWith("grp_")) showBodyInfo(loc.id);
        else selectOverviewLocation(loc.id);
      };
    }

    function mkGroupSummary(summary, loc, depth, treeCtx = null) {
      const folderRow = document.createElement("div");
      folderRow.className = "ovFolderRow";
      if (depth > 0) folderRow.classList.add("ovSubFolder");
      folderRow.style.paddingLeft = `${8 + depth * FOLDER_INDENT_PX}px`;

      const selectBtn = document.createElement("button");
      selectBtn.type = "button";
      selectBtn.className = "ovFolderSelectBtn";

      const prefixEl = document.createElement("span");
      prefixEl.className = "ovTreePrefix";
      const branch = treeCtx?.isLast ? "└─" : "├─";
      prefixEl.textContent = treePrefix(depth, branch, treeCtx?.ancestorHasNext || []);

      const labelEl = document.createElement("span");
      labelEl.className = "ovTreeLabel";
      labelEl.textContent = loc.name || loc.id;

      selectBtn.append(prefixEl, labelEl);
      selectBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        locationClickHandler(loc)();
      });

      folderRow.appendChild(selectBtn);
      summary.appendChild(folderRow);
    }

    function mkRow(name, depth, onClick, treeCtx = null) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ovRowBtn";

      const nameWrap = document.createElement("div");
      nameWrap.className = "ovName";

      const prefixEl = document.createElement("span");
      prefixEl.className = "ovTreePrefix";
      const branch = treeCtx?.isLast ? "└─" : "├─";
      prefixEl.textContent = treePrefix(depth, branch, treeCtx?.ancestorHasNext || []);

      const labelEl = document.createElement("span");
      labelEl.className = "ovTreeLabel";
      labelEl.textContent = name;

      nameWrap.append(prefixEl, labelEl);
      btn.appendChild(nameWrap);
      btn.style.paddingLeft = `${8 + depth * ROW_INDENT_PX}px`;
      btn.addEventListener("click", (e) => {
        if (!onClick) return;
        e.stopPropagation();
        onClick();
      });
      return btn;
    }

    function mkStaticRow(name, depth, treeCtx = null) {
      const row = document.createElement("div");
      row.className = "ovRowBtn ovRowStatic";

      const nameWrap = document.createElement("div");
      nameWrap.className = "ovName";

      const prefixEl = document.createElement("span");
      prefixEl.className = "ovTreePrefix";
      const branch = treeCtx?.isLast ? "└─" : "├─";
      prefixEl.textContent = treePrefix(depth, branch, treeCtx?.ancestorHasNext || []);

      const labelEl = document.createElement("span");
      labelEl.className = "ovTreeLabel";
      labelEl.textContent = name;

      nameWrap.append(prefixEl, labelEl);
      row.appendChild(nameWrap);
      row.style.paddingLeft = `${8 + depth * ROW_INDENT_PX}px`;
      return row;
    }

    function renderShipChildrenForLocation(locationId, depth, host, ancestorHasNext = [], hasFollowingSiblings = false) {
      const docked = dockedShipsByLocation.get(locationId) || [];
      for (let i = 0; i < docked.length; i += 1) {
        const s = docked[i];
        const isLastShip = (i === docked.length - 1) && !hasFollowingSiblings;
        host.appendChild(
          mkRow(s.name || s.id, depth, () => selectOverviewShip(s.id), {
            ancestorHasNext,
            isLast: isLastShip,
          })
        );
      }
      return docked.length;
    }

    function renderLocationNode(loc, depth, host, defaultOpen = false, treeCtx = { ancestorHasNext: [], isLast: true }) {
      if (!loc) return false;
      const isGroup = Number(loc.is_group) === 1;

      if (!isGroup) {
        const docked = dockedShipsByLocation.get(loc.id) || [];
        const isVisible = visibleLocationIds.has(loc.id);
        if (!isVisible && docked.length === 0) return false;

        const wrapper = document.createElement("div");
        wrapper.className = "ovNode";
        wrapper.appendChild(
          mkRow(loc.name || loc.id, depth, () => selectOverviewLocation(loc.id), treeCtx)
        );

        renderShipChildrenForLocation(
          loc.id,
          depth + 1,
          wrapper,
          [...(treeCtx.ancestorHasNext || []), !treeCtx.isLast],
          false
        );
        host.appendChild(wrapper);
        return true;
      }

      const groupChildren = childrenByParent.get(loc.id) || [];
      const orbitGroupId = `${loc.id}_orbits`;
      const orbitChildren = (childrenByParent.get(orbitGroupId) || []).filter((x) => ORBIT_IDS.has(x.id));

      const childrenWrap = document.createElement("div");
      childrenWrap.className = "ovChildren";
      let renderedChildren = 0;
      const childAncestor = [...(treeCtx.ancestorHasNext || []), !treeCtx.isLast];

      for (let orbitIdx = 0; orbitIdx < orbitChildren.length; orbitIdx += 1) {
        const orbit = orbitChildren[orbitIdx];
        const orbitVisible = visibleLocationIds.has(orbit.id);
        const docked = dockedShipsByLocation.get(orbit.id) || [];
        if (!orbitVisible && docked.length === 0) continue;

        const hasMorePotentialSiblings = orbitIdx < orbitChildren.length - 1 || groupChildren.length > 0;

        childrenWrap.appendChild(
          mkRow(orbit.name || orbit.id, depth + 1, () => selectOverviewLocation(orbit.id), {
            ancestorHasNext: childAncestor,
            isLast: !hasMorePotentialSiblings && docked.length === 0,
          })
        );
        renderedChildren += 1;
        renderedChildren += renderShipChildrenForLocation(
          orbit.id,
          depth + 2,
          childrenWrap,
          [...childAncestor, hasMorePotentialSiblings],
          hasMorePotentialSiblings
        );
      }

      const visibleDirectChildren = groupChildren.filter((child) => child.id !== orbitGroupId);
      for (let childIdx = 0; childIdx < visibleDirectChildren.length; childIdx += 1) {
        const child = visibleDirectChildren[childIdx];
        const hasMorePotentialSiblings = childIdx < visibleDirectChildren.length - 1;
        if (renderLocationNode(child, depth + 1, childrenWrap, false, {
          ancestorHasNext: childAncestor,
          isLast: !hasMorePotentialSiblings,
        })) renderedChildren += 1;
      }

      const groupVisible = visibleLocationIds.has(loc.id);
      if (!groupVisible && renderedChildren === 0) return false;

      const details = document.createElement("details");
      details.className = "ovGroup ovPlanetGroup";
      details.open = mapOverviewOpenState.has(loc.id) ? !!mapOverviewOpenState.get(loc.id) : defaultOpen;
      details.addEventListener("toggle", () => {
        mapOverviewOpenState.set(loc.id, !!details.open);
      });

      const summary = document.createElement("summary");
      mkGroupSummary(summary, loc, depth, treeCtx);
      details.appendChild(summary);

      if (renderedChildren === 0) {
        const empty = document.createElement("div");
        empty.className = "muted small";
        empty.style.padding = `6px 8px 8px ${22 + depth * FOLDER_INDENT_PX}px`;
        empty.textContent = "No visible objects.";
        childrenWrap.appendChild(empty);
      }

      details.appendChild(childrenWrap);

      const wrapper = document.createElement("div");
      wrapper.className = "ovNode";
      wrapper.appendChild(details);
      host.appendChild(wrapper);
      return true;
    }

    let renderedRootCount = 0;
    const renderedZoneIds = new Set();
    const sunGroup = locationsById.get("grp_sun");
    const rootCandidates = sunGroup
      ? (childrenByParent.get("grp_sun") || [])
      : (childrenByParent.get("ROOT") || []);

    for (const rootLoc of rootCandidates) {
      if (!rootLoc) continue;

      const zoneDetails = document.createElement("details");
      zoneDetails.className = "ovGroup ovZoneGroup";
      const zoneStateKey = `zone:${rootLoc.id}`;
      zoneDetails.open = mapOverviewOpenState.has(zoneStateKey)
        ? !!mapOverviewOpenState.get(zoneStateKey)
        : rootLoc.id === "grp_earth";
      zoneDetails.addEventListener("toggle", () => {
        mapOverviewOpenState.set(zoneStateKey, !!zoneDetails.open);
      });

      const zoneSummary = document.createElement("summary");
      const zoneRow = document.createElement("div");
      zoneRow.className = "ovFolderRow ovZoneRow";
      zoneRow.style.paddingLeft = "8px";
      const zoneSymbol = String(rootLoc.symbol || "").trim();
      zoneRow.innerHTML = `${zoneSymbol ? `<span class="ovAstroSymbol">${zoneSymbol}</span>` : ""}<span>${rootLoc.name || rootLoc.id} Heliocentric Zone</span>`;
      zoneSummary.appendChild(zoneRow);
      zoneDetails.appendChild(zoneSummary);

      const zoneChildren = document.createElement("div");
      zoneChildren.className = "ovChildren";
      const defaultOpen = rootLoc.id === "grp_earth";
      const rendered = renderLocationNode(rootLoc, 1, zoneChildren, defaultOpen, {
        ancestorHasNext: [],
        isLast: true,
      });
      if (!rendered) continue;
      renderedZoneIds.add(String(rootLoc.id || ""));

      zoneDetails.appendChild(zoneChildren);

      const zoneNode = document.createElement("div");
      zoneNode.className = "ovNode";
      zoneNode.appendChild(zoneDetails);
      mapOverviewEl.appendChild(zoneNode);
      renderedRootCount += 1;
    }

    setZoneGlowFromOverview(renderedZoneIds);

    const transitShips = ships.filter((s) => s.status === "transit" && visibleShipIds.has(s.id));
    if (transitShips.length) {
      const transitBlock = document.createElement("div");
      transitBlock.className = "ovNode";
      const details = document.createElement("details");
      details.className = "ovGroup";
      const transitStateKey = "ov:transit_ships";
      details.open = mapOverviewOpenState.has(transitStateKey)
        ? !!mapOverviewOpenState.get(transitStateKey)
        : true;
      details.addEventListener("toggle", () => {
        mapOverviewOpenState.set(transitStateKey, !!details.open);
      });
      const summary = document.createElement("summary");
      summary.appendChild(mkStaticRow("Transit Ships", 0, { ancestorHasNext: [], isLast: true }));
      details.appendChild(summary);
      const wrap = document.createElement("div");
      wrap.className = "ovChildren";
      transitShips
        .slice()
        .sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)))
        .forEach((s, idx, arr) => {
          const toText = s.to_location_id ? ` → ${s.to_location_id}` : "";
          wrap.appendChild(mkRow(`${s.name || s.id}${toText}`, 1, () => selectOverviewShip(s.id), {
            ancestorHasNext: [],
            isLast: idx === arr.length - 1,
          }));
        });
      details.appendChild(wrap);
      transitBlock.appendChild(details);
      mapOverviewEl.appendChild(transitBlock);
    }

    if (renderedRootCount === 0 && transitShips.length === 0) {
      const empty = document.createElement("div");
      empty.className = "muted small";
      empty.style.padding = "10px 8px";
      empty.textContent = "No map objects in view.";
      mapOverviewEl.appendChild(empty);
    }
  }

  function orderedPartsForStack(ship) {
    const parts = Array.isArray(ship?.parts) ? ship.parts.slice() : [];
    const isThruster = (part) => {
      if (typeof part === "string") return part.toLowerCase().includes("thruster");
      const type = String(part?.type || "").toLowerCase();
      const name = String(part?.name || part?.type || "").toLowerCase();
      return type === "thruster" || name.includes("thruster");
    };
    const nonThrusters = parts.filter((part) => !isThruster(part));
    const thrusters = parts.filter((part) => isThruster(part));
    return nonThrusters.concat(thrusters);
  }

  function partLabel(part) {
    if (typeof part === "string") return part;
    if (!part || typeof part !== "object") return "Part";
    const name = part.name || part.type || "Part";
    if (Number(part.water_kg) > 0) return `${name} (${(Number(part.water_kg) / 1000).toFixed(1)} t water)`;
    if (Number(part.thrust_kn) > 0 || Number(part.isp_s) > 0) {
      const thrust = Number(part.thrust_kn) > 0 ? `${Number(part.thrust_kn).toFixed(0)} kN` : null;
      const isp = Number(part.isp_s) > 0 ? `${Number(part.isp_s).toFixed(0)} s` : null;
      return `${name} (${[thrust, isp].filter(Boolean).join(", ")})`;
    }
    return name;
  }

  function buildPartsStackHtml(ship) {
    const ordered = orderedPartsForStack(ship);
    if (!ordered.length) return '<li><div class="muted">No parts installed.</div></li>';
    return '<li><div class="shipInvSection"><div class="shipInvSectionTitle">Installed Parts</div><div class="shipInvGrid" data-grid-type="ship-parts"></div></div></li>';
  }

  function renderShipPartsGrid(containerEl, ship) {
    const ID = window.ItemDisplay;
    if (!ID || !containerEl) return;
    const grid = containerEl.querySelector('[data-grid-type="ship-parts"]');
    if (!grid) return;
    const ordered = orderedPartsForStack(ship);
    ordered.forEach((part) => {
      const p = typeof part === "object" && part ? part : {};
      const name = String(p.name || p.type || "Part");
      const category = String(p.type || p.category_id || "module").toLowerCase();
      const tooltipLines = [];
      if (Number(p.thrust_kn) > 0) tooltipLines.push(["Thrust", `${Number(p.thrust_kn).toFixed(0)} kN`]);
      if (Number(p.isp_s) > 0) tooltipLines.push(["ISP", `${Number(p.isp_s).toFixed(0)} s`]);
      if (Number(p.capacity_m3) > 0) tooltipLines.push(["Capacity", `${Number(p.capacity_m3).toFixed(2)} m³`]);
      if (Number(p.thermal_mw) > 0) tooltipLines.push(["Power", `${Number(p.thermal_mw).toFixed(1)} MWth`]);
      if (Number(p.electric_mw) > 0) tooltipLines.push(["Electric", `${Number(p.electric_mw).toFixed(1)} MWe`]);
      if (Number(p.heat_rejection_mw) > 0) tooltipLines.push(["Rejection", `${Number(p.heat_rejection_mw).toFixed(1)} MW`]);
      if (Number(p.water_kg) > 0) tooltipLines.push(["Water", fmtKg(Number(p.water_kg))]);
      const cell = ID.createGridCell({
        label: name,
        iconSeed: p.item_id || name,
        itemId: p.item_id || "",
        category: category,
        mass_kg: Number(p.mass_kg) || 0,
        subtitle: category,
        branch: p.branch || "",
        family: p.thruster_family || "",
        techLevel: p.tech_level || "",
        tooltipLines: tooltipLines.length ? tooltipLines : undefined,
      });
      grid.appendChild(cell);
    });
  }

  function buildDeltaVPanelHtml(ship) {
    const dryMass = Number(ship.dry_mass_kg || 0);
    const fuel = Number(ship.fuel_kg || 0);
    const fuelCap = Number(ship.fuel_capacity_kg || 0);
    const wetMass = dryMass + fuel;
    const isp = Number(ship.isp_s || 0);
    const thrust = Number(ship.thrust_kn || 0);
    const dv = Number(ship.delta_v_remaining_m_s || 0);
    const accelG = wetMass > 0 ? (thrust * 1000) / (wetMass * 9.80665) : 0;
    const fPct = fuelCap > 0 ? Math.max(0, Math.min(100, (fuel / fuelCap) * 100)) : 0;
    const dvCls = dv > 0 ? "pbPositive" : "pbNeutral";
    return `
      <li>
        <div class="powerBalancePanel">
          <div class="pbTitle">Delta-v &amp; Propulsion</div>
          <div class="pbSection">
            <div class="pbSectionHead">Mass Budget</div>
            <div class="pbRow"><span class="pbLabel">Dry mass</span><span class="pbVal">${fmtKg(dryMass)}</span></div>
            <div class="pbRow"><span class="pbLabel">Fuel</span><span class="pbVal">${fmtKg(fuel)} / ${fmtKg(fuelCap)}</span></div>
            <div class="pbRow"><span class="pbLabel">Fuel level</span><span class="pbVal"><span class="pbBarWrap"><span class="pbBar" style="width:${fPct.toFixed(1)}%"></span></span> ${fPct.toFixed(0)}%</span></div>
            <div class="pbRow pbDivider"><span class="pbLabel"><b>Wet mass</b></span><span class="pbVal"><b>${fmtKg(wetMass)}</b></span></div>
          </div>
          <div class="pbSection">
            <div class="pbSectionHead">Propulsion</div>
            <div class="pbRow"><span class="pbLabel">Thrust</span><span class="pbVal">${thrust.toFixed(0)} kN</span></div>
            <div class="pbRow"><span class="pbLabel">Specific impulse</span><span class="pbVal">${isp.toFixed(0)} s</span></div>
            <div class="pbRow"><span class="pbLabel">Acceleration</span><span class="pbVal">${accelG.toFixed(3)} g</span></div>
          </div>
          <div class="pbSection">
            <div class="pbSectionHead">Delta-v</div>
            <div class="pbRow pbDivider"><span class="pbLabel"><b>Δv remaining</b></span><span class="pbVal ${dvCls}"><b>${Math.max(0, dv).toFixed(0)} m/s</b></span></div>
          </div>
        </div>
      </li>
    `;
  }

  function fmtMw(v) {
    return `${Math.max(0, Number(v) || 0).toFixed(1)} MW`;
  }

  function fmtMwTh(v) { return `${Math.max(0, Number(v) || 0).toFixed(1)}<span class="pbUnit">MWth</span>`; }
  function fmtMwE(v) { return `${Math.max(0, Number(v) || 0).toFixed(1)}<span class="pbUnit">MWe</span>`; }

  function buildPowerBalanceHtml(ship) {
    const pb = ship.power_balance;
    if (!pb) return "";
    const reactorMw = Number(pb.reactor_thermal_mw || 0);
    const thrusterMw = Number(pb.thruster_thermal_mw || 0);
    const genInputMw = Number(pb.generator_thermal_mw_input || 0);
    const thermalSurplus = Number(pb.thermal_surplus_mw || 0);
    const electricMw = Number(pb.generator_electric_mw || 0);
    const electricRated = Number(pb.generator_electric_mw_rated || 0);
    const genThrottle = Number(pb.gen_throttle ?? 1);
    const thrustExhaust = Number(pb.thrust_exhaust_mw || 0);
    const electricConv = Number(pb.electric_conversion_mw || 0);
    const totalWaste = Number(pb.total_waste_heat_mw || 0);
    const radRejection = Number(pb.radiator_heat_rejection_mw || 0);
    const wasteSurplus = Number(pb.waste_heat_surplus_mw || 0);
    const maxThrottle = Number(pb.max_throttle || 0);
    const hasAny = reactorMw > 0 || thrusterMw > 0 || genInputMw > 0 || radRejection > 0;
    if (!hasAny) return "";
    const thermalCls = thermalSurplus >= 0 ? "pbPositive" : "pbNegative";
    const wasteCls = wasteSurplus > 0 ? "pbNegative" : "pbPositive";
    const throttleCls = maxThrottle < 1 ? "pbNegative" : "pbPositive";
    const isOverheating = wasteSurplus > 0;
    const overheatBanner = isOverheating
      ? `<div class="pbOverheatBanner"><span class="pbOverheatIcon">⚠</span><span class="pbOverheatText">OVERHEATING — ${wasteSurplus.toFixed(1)} MWth unradiated waste heat. Ship cannot transfer and risks thermal failure.</span></div>`
      : "";
    const genThrottled = genThrottle < 1 && electricRated > 0;
    return `
      <li>
        <div class="powerBalancePanel${isOverheating ? ' pbOverheating' : ''}">
          <div class="pbTitle">Power &amp; Thermal Balance</div>
          <div class="pbSection">
            <div class="pbSectionHead">Thermal Budget (MWth)</div>
            <div class="pbRow"><span class="pbLabel">Reactor output</span><span class="pbVal">${fmtMwTh(reactorMw)}</span></div>
            <div class="pbRow"><span class="pbLabel">Thruster demand</span><span class="pbVal">−${fmtMwTh(thrusterMw)}</span></div>
            <div class="pbRow"><span class="pbLabel">Generator input</span><span class="pbVal">−${fmtMwTh(genInputMw)}</span></div>
            <div class="pbRow pbDivider"><span class="pbLabel"><b>Surplus</b></span><span class="pbVal ${thermalCls}"><b>${thermalSurplus >= 0 ? "+" : ""}${thermalSurplus.toFixed(1)}<span class="pbUnit">MWth</span></b></span></div>
            ${thrusterMw > 0 ? `<div class="pbRow"><span class="pbLabel">Max throttle</span><span class="pbVal ${throttleCls}">${(maxThrottle * 100).toFixed(0)}%</span></div>` : ""}
          </div>
          <div class="pbSection">
            <div class="pbSectionHead">Electric (MWe)</div>
            <div class="pbRow"><span class="pbLabel">Generator output${genThrottled ? ' <span class="pbNegative">(throttled)</span>' : ''}</span><span class="pbVal">${fmtMwE(electricMw)}${genThrottled ? ` <span class="muted">/ ${electricRated.toFixed(1)}</span>` : ''}</span></div>
          </div>
          <div class="pbSection">
            <div class="pbSectionHead">Waste Heat (MWth)</div>
            <div class="pbRow"><span class="pbLabel">Reactor heat produced</span><span class="pbVal">${fmtMwTh(reactorMw)}</span></div>
            ${thrustExhaust > 0 ? `<div class="pbRow"><span class="pbLabel">Thrust exhaust</span><span class="pbVal">−${fmtMwTh(thrustExhaust)}</span></div>` : ""}
            ${electricConv > 0 ? `<div class="pbRow"><span class="pbLabel">Converted to electric</span><span class="pbVal">−${fmtMwE(electricConv)}</span></div>` : ""}
            <div class="pbRow"><span class="pbLabel">Radiator rejection</span><span class="pbVal">−${fmtMwTh(radRejection)}</span></div>
            <div class="pbRow pbDivider"><span class="pbLabel"><b>Unradiated</b></span><span class="pbVal ${wasteCls}"><b>${wasteSurplus >= 0 ? "+" : ""}${wasteSurplus.toFixed(1)}<span class="pbUnit">MWth</span></b></span></div>
          </div>
          ${overheatBanner}
        </div>
      </li>
    `;
  }

  function fmtM3(v) {
    return `${(Math.max(0, Number(v) || 0)).toFixed(2)} m³`;
  }

  function fmtKg(v) {
    const val = Math.max(0, Number(v) || 0);
    if (val >= 5000) return `${(val / 1000).toFixed(1)} t`;
    return `${val.toFixed(0)} kg`;
  }

  function buildShipInfoTabsHtml(activeTab) {
    const detailsActive = activeTab === "details";
    const inventoryActive = activeTab === "inventory";
    return `
      <div class="shipInfoTabs" role="tablist" aria-label="Ship info tabs">
        <button type="button" class="shipInfoTabBtn${detailsActive ? " isActive" : ""}" data-ship-tab="details" role="tab" aria-selected="${detailsActive ? "true" : "false"}">Details</button>
        <button type="button" class="shipInfoTabBtn${inventoryActive ? " isActive" : ""}" data-ship-tab="inventory" role="tab" aria-selected="${inventoryActive ? "true" : "false"}">Inventory</button>
      </div>
    `;
  }

  function buildLocationInfoTabsHtml(activeTab) {
    const detailsActive = activeTab === "details";
    const inventoryActive = activeTab === "inventory";
    return `
      <div class="shipInfoTabs" role="tablist" aria-label="Location info tabs">
        <button type="button" class="shipInfoTabBtn${detailsActive ? " isActive" : ""}" data-location-tab="details" role="tab" aria-selected="${detailsActive ? "true" : "false"}">Details</button>
        <button type="button" class="shipInfoTabBtn${inventoryActive ? " isActive" : ""}" data-location-tab="inventory" role="tab" aria-selected="${inventoryActive ? "true" : "false"}">Inventory</button>
      </div>
    `;
  }

  function renderShipInfoTabs() {
    if (!shipInfoTabsHost) return;
    if (!selectedShipId) {
      shipInfoTabsHost.innerHTML = "";
      return;
    }
    shipInfoTabsHost.innerHTML = buildShipInfoTabsHtml(shipInfoTab);
    wireShipInfoTabs();
  }

  function wireShipInfoTabs() {
    if (!shipInfoTabsHost) return;
    const tabButtons = shipInfoTabsHost.querySelectorAll("[data-ship-tab]");
    tabButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const next = String(btn.getAttribute("data-ship-tab") || "details");
        if (!selectedShipId) return;
        if (next !== "details" && next !== "inventory") return;
        shipInfoTab = next;
        showShipPanel();
      });
    });
  }

  function renderLocationInfoTabs(loc, hasInventory) {
    if (!shipInfoTabsHost) return;
    if (!loc || !hasInventory) {
      shipInfoTabsHost.innerHTML = "";
      return;
    }

    shipInfoTabsHost.innerHTML = buildLocationInfoTabsHtml(locationInfoTab);
    const tabButtons = shipInfoTabsHost.querySelectorAll("[data-location-tab]");
    tabButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const next = String(btn.getAttribute("data-location-tab") || "details");
        if (next !== "details" && next !== "inventory") return;
        locationInfoTab = next;
        showLocationInfo(loc);
      });
    });
  }

  function buildInventoryListHtml(items, capacitySummary) {
    const rows = Array.isArray(items) ? items : [];
    const summary = (capacitySummary && typeof capacitySummary === "object") ? capacitySummary : {};
    const used = Math.max(0, Number(summary.used_m3) || 0);
    const cap = Math.max(0, Number(summary.capacity_m3) || 0);
    const pct = cap > 0 ? Math.max(0, Math.min(100, (used / cap) * 100)) : 0;

    const byPhase = (summary.by_phase && typeof summary.by_phase === "object") ? summary.by_phase : {};
    const phaseStats = ["solid", "liquid", "gas"].map((phase) => {
      const entry = byPhase[phase] || {};
      const pUsed = Math.max(0, Number(entry.used_m3) || 0);
      const pCap = Math.max(0, Number(entry.capacity_m3) || 0);
      return `${phase[0].toUpperCase()}${phase.slice(1)} ${fmtM3(pUsed)} / ${fmtM3(pCap)}`;
    }).join(" · ");

    const summaryHtml = `
      <div class="shipInvSection shipInvSummary">
        <div class="shipInvSectionTitle">Cargo Usage</div>
        <div class="shipInvSummaryBody">
          <div class="shipInvSummaryLine">${fmtM3(used)} / ${fmtM3(cap)} used</div>
          <div class="shipInvBar"><div class="shipInvBarFill" style="width:${pct.toFixed(2)}%"></div></div>
          <div class="shipInvSummaryPhases">${phaseStats}</div>
        </div>
      </div>
    `;

    if (!rows.length) {
      return `<li><div class="shipInvRoot">${summaryHtml}<div class="muted">No cargo contents found on this ship.</div></div></li>`;
    }

    return `
      <li>
        <div class="shipInvRoot">
          ${summaryHtml}
          <div class="shipInvSection">
            <div class="shipInvSectionTitle">Cargo Contents</div>
            <div class="shipInvGrid" data-grid-type="ship-cargo"></div>
          </div>
        </div>
      </li>
    `;
  }

  function renderShipInventoryGrids(containerEl, items) {
    const ID = window.ItemDisplay;
    if (!ID || !containerEl) return;
    const grid = containerEl.querySelector('[data-grid-type="ship-cargo"]');
    if (!grid) return;
    const rows = Array.isArray(items) ? items : [];
    rows.forEach((item) => {
      const cell = ID.createGridCell({
        label: String(item?.label || item?.item_id || "Cargo"),
        iconSeed: `resource::${item?.resource_id || item?.item_id || ""}`,
        itemId: item?.resource_id || item?.item_id || "",
        category: "resource",
        mass_kg: Number(item?.mass_kg) || 0,
        volume_m3: Number(item?.volume_m3) || 0,
        phase: String(item?.phase || ""),
        subtitle: `${String(item?.phase || "solid")} cargo`,
      });
      grid.appendChild(cell);
    });
  }

  async function runInventoryAction(shipId, containerIndex, action) {
    if (!shipId) return;
    const actionName = action === "deploy" ? "deploy" : "jettison";
    const resp = await fetch(`/api/ships/${encodeURIComponent(shipId)}/inventory/${actionName}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ container_index: Number(containerIndex) || 0 }),
    });

    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || "Inventory action failed.");
    }
  }

  function wireInventoryActionButtons(ship) {
    if (!ship || !infoList) return;
  }

  // ---------- Coordinate scaling ----------
  // DB coords are km-ish.
  // We apply a heliocentric log-like radial projection for deep space so inner system spacing is readable,
  // then expand local orbit groups (Earth/Luna) on a separate scale around their parent body.
  const DEEP_SCALE = 0.001; // linear fallback when Sun center is unavailable
  const ADDITIONAL_SPACING_MULT = 15;
  const GLOBAL_WORLD_SPREAD_MULT = 10;
  const HELIO_LINEAR_WORLD_PER_KM = 0.0000013 * ADDITIONAL_SPACING_MULT * GLOBAL_WORLD_SPREAD_MULT;
  const LOCAL_ORBIT_EXPANSION_MULT = 3.2;
  const EARTH_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const MOON_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const MERCURY_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const VENUS_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const MARS_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const CERES_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const VESTA_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const PALLAS_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const HYGIEA_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const PHOBOS_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const DEIMOS_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const ZOOZVE_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const JUPITER_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const IO_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const EUROPA_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const GANYMEDE_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const CALLISTO_ORBIT_SCALE = HELIO_LINEAR_WORLD_PER_KM * LOCAL_ORBIT_EXPANSION_MULT;
  const FIT_VIEW_SCALE = 0.86;
  const MAP_SCREEN_SPREAD_MULT = 10;
  const MAX_INITIAL_SCALE = CAMERA_MAX_SCALE;
  const SHIP_SIZE_SCALE = 0.78;
  const SHIP_VISUAL_SCALE = 0.5;
  const SHIP_CLICK_RADIUS_MULT = 3.2;
  const SHIP_WORLD_SCALE_COMPENSATION = 1;
  const MIN_SHIP_HIT_SCREEN_PX = 22;
  const MIN_LOC_HIT_SCREEN_PX = 20;
  const DOCKED_ROW_Y_OFFSET_PX = -26;
  const DOCKED_ROW_SLOT_SPACING_PX = 22;
  const DOCKED_CHIP_MIN_SCREEN_PX = 28;
  const DOCKED_CHIP_MAX_SCREEN_PX = 32;
  const DOCKED_CHIP_HARD_MAX_SCREEN_PX = 36;
  const DOCKED_CHIP_SCALE_MIN = 0.0002;
  const DOCKED_CHIP_SCALE_MAX = 0.5;
  const DOCKED_CHIP_LERP = 0.2;
  const SHIP_TRAIL_DISTANCE_MULT = 1.55;
  const SHIP_PATH_DASH_LEN_MULT = 1.45;
  const SHIP_PATH_GAP_LEN_MULT = 1.1;
  const SHIP_PATH_ALPHA = 0.28;
  const SHIP_PATH_TRAVELED_ALPHA = 0.06;
  const SHIP_PATH_CHEVRON_ALPHA = 0.45;
  const SHIP_PATH_DEST_MARKER_ALPHA = 0.55;
  const SHIP_IDTAG_FADE_IN_ZOOM = 0.85;
  const SHIP_IDTAG_FULL_ZOOM = 1.65;
  const SHIP_CLUSTER_ZOOM_THRESHOLD = 0.24;
  const SHIP_CLUSTER_MIN_COUNT = 2;
  const SHIP_EXTRA_SHRINK_START_ZOOM = 4;
  const SHIP_EXTRA_SHRINK_FULL_ZOOM = 6;
  const SHIP_EXTRA_SHRINK_FACTOR = 0.5;
  const SHIP_HITBOX_TIGHTEN_FACTOR = 0.56;
  const SHIP_LABEL_SCALE_MULT = 0.62;
  const SHIP_IDTAG_SCALE_MULT = 0.56;
  const SHIP_LABEL_SCREEN_CAP = 0.95;
  const SHIP_IDTAG_SCREEN_CAP = 0.75;
  const ICON_ZOOM_COMP_MAX = 5.5;
  const LABEL_ZOOM_COMP_MAX = 4.5;
  const DEEP_ZOOM_SHRINK_START = 4;
  const DEEP_ZOOM_ICON_SHRINK_RATE = 1.25;
  const DEEP_ZOOM_ICON_SHRINK_MIN = 0.2;
  const DEEP_ZOOM_LABEL_SHRINK_RATE = 0.85;
  const DEEP_ZOOM_LABEL_SHRINK_MIN = 0.26;
  const ORBIT_RING_BASE_PX = 1.15;
  const ORBIT_RING_HOVER_MULT = 1.6;
  const ORBIT_RING_MIN_PX = 0.5;
  const BODY_HOVER_SCALE_MULT = 1.16;
  const BODY_BASE_GLYPH_ALPHA = 0.9;
  const BODY_BASE_LABEL_ALPHA = 0.9;
  const CELESTIAL_BASE_LABEL_ALPHA = 0.7;
  const MOONLET_HOVER_SCALE_MULT = 1.16;
  const ASTEROID_HOVER_SCALE_MULT = 1.16;
  const SOLAR_RING_MULT = 0.72;
  const SHIP_SELECTION_STROKE_PX = 1.35;
  const UNIVERSAL_TEXT_SCALE_CAP = 2.8;
  const TEXT_COLLISION_PADDING_PX = 6;
  const PLANET_ICON_SCREEN_MULT = 1.5;
  const MOON_ICON_SCREEN_PX = 16;
  const JOVIAN_MOON_ICON_SCREEN_PX = 18;
  const SUN_ICON_SCREEN_PX = 28;
  const ASTEROID_ICON_SCREEN_PX = 16;
  const ASTEROID_HINTS = ["asteroid", "zoozve", "ceres", "vesta", "pallas", "hygiea", "io", "europa", "ganymede", "callisto", "trojan", "greek"];
  const PLANET_ICON_ZOOM_COMP_MAX = 320;
  const PLANET_LABEL_ZOOM_COMP_MAX = 42;

  // Orbits we render as rings (NOT as dots)
  const ORBIT_IDS = new Set([
    "LEO", "HEO", "GEO",
    "LLO", "HLO",
    "MERC_ORB", "MERC_HEO", "MERC_GEO",
    "VEN_ORB", "VEN_HEO", "VEN_GEO",
    "LMO", "HMO", "MGO",
    "CERES_LO", "CERES_HO",
    "VESTA_LO", "VESTA_HO",
    "PALLAS_LO", "PALLAS_HO",
    "HYGIEA_LO", "HYGIEA_HO",
    "PHOBOS_LO", "DEIMOS_LO", "ZOOZVE_LO",
    "JUP_LO", "JUP_HO",
    "IO_LO", "IO_HO",
    "EUROPA_LO", "EUROPA_HO",
    "GANYMEDE_LO", "GANYMEDE_HO",
    "CALLISTO_LO", "CALLISTO_HO",
  ]);
  const LPOINT_IDS = new Set(["L1", "L2", "L3", "L4", "L5", "SJ_L1", "SJ_L2", "SJ_L3", "SJ_L4", "SJ_L5"]);
  // Jupiter L4 (Greeks) and L5 (Trojans) stay visible at all zoom levels
  const ALWAYS_VISIBLE_LPOINTS = new Set(["SJ_L4", "SJ_L5"]);

  // orbitInfo: orbitId -> {cx,cy,radius,baseAngle,period_s}
  const orbitInfo = new Map();

  // orbit hover labels (only visible on hover)
  const orbitLabelMap = new Map();
  let hoveredOrbitId = null;
  const zoomScaledTexts = new Set();
  const BASE_TEXT_RESOLUTION = Math.max(1, window.devicePixelRatio || 1);
  const MAX_TEXT_RESOLUTION = 8;

  function registerZoomScaledText(t) {
    if (!t) return t;
    if (!Number.isFinite(Number(t.__collisionPriority))) t.__collisionPriority = 10;
    zoomScaledTexts.add(t);
    const targetRes = Math.min(MAX_TEXT_RESOLUTION, Math.max(1, BASE_TEXT_RESOLUTION * world.scale.x));
    if (t.resolution !== targetRes) {
      t.resolution = targetRes;
      t.dirty = true;
    }
    return t;
  }

  function unregisterZoomScaledText(t) {
    if (!t) return;
    zoomScaledTexts.delete(t);
  }

  function refreshZoomScaledTextResolution() {
    const targetRes = Math.min(MAX_TEXT_RESOLUTION, Math.max(1, BASE_TEXT_RESOLUTION * world.scale.x));
    for (const t of Array.from(zoomScaledTexts)) {
      if (!t || t.destroyed || t._destroyed) {
        zoomScaledTexts.delete(t);
        continue;
      }
      if (t.resolution !== targetRes) {
        t.resolution = targetRes;
        t.dirty = true;
      }
    }
  }

  function smoothstep(edge0, edge1, x) {
    const e0 = Number(edge0);
    const e1 = Number(edge1);
    if (!(e1 > e0)) return x >= e1 ? 1 : 0;
    const t = clamp((Number(x) - e0) / (e1 - e0), 0, 1);
    return t * t * (3 - 2 * t);
  }

  function zoomFade(zoom, start, end) {
    return smoothstep(start, end, zoom);
  }

  function deepZoomShrink(zoom, start, rate, minScale) {
    const z = Math.max(0.0001, Number(zoom) || 1);
    if (z <= start) return 1;
    const r = Math.max(0.0001, Number(rate) || 1);
    const minV = clamp(Number(minScale) || 0, 0.05, 1);
    const shrink = 1 / (1 + (z - start) * r);
    return clamp(shrink, minV, 1);
  }

  function highZoomShipShrinkMultiplier(zoom) {
    const t = zoomFade(zoom, SHIP_EXTRA_SHRINK_START_ZOOM, SHIP_EXTRA_SHRINK_FULL_ZOOM);
    return 1 - (SHIP_EXTRA_SHRINK_FACTOR * t);
  }

  function applyUniversalTextScaleCap() {
    const zoom = Math.max(0.0001, Number(world.scale.x) || 1);
    for (const t of Array.from(zoomScaledTexts)) {
      if (!t || t.destroyed || t._destroyed) {
        zoomScaledTexts.delete(t);
        continue;
      }
      if (!t?.scale) continue;
      const cap = Math.max(0.2, Number(t.__maxScreenScale) || Number(UNIVERSAL_TEXT_SCALE_CAP) || 1);
      const maxLocalScale = cap / zoom;
      const sx = Number(t.scale.x);
      const sy = Number(t.scale.y);
      if (!Number.isFinite(sx) || !Number.isFinite(sy)) continue;
      if (sx > maxLocalScale || sy > maxLocalScale) {
        t.scale.set(Math.min(maxLocalScale, sx), Math.min(maxLocalScale, sy));
      }
    }
  }

  function boxesOverlap(a, b, paddingPx = 0) {
    const p = Math.max(0, Number(paddingPx) || 0);
    return !(
      (a.x + a.width + p) <= b.x ||
      (b.x + b.width + p) <= a.x ||
      (a.y + a.height + p) <= b.y ||
      (b.y + b.height + p) <= a.y
    );
  }

  function applyTextCollisionCulling() {
    const candidates = [];
    for (const t of Array.from(zoomScaledTexts)) {
      if (!t || t.destroyed || t._destroyed) {
        zoomScaledTexts.delete(t);
        continue;
      }
      // reset from prior collision pass; alpha remains authoritative for intended visibility
      t.visible = true;

      const alpha = Number(t.worldAlpha ?? t.alpha ?? 1);
      if (alpha <= 0.001) continue;
      if (!t.parent) continue;

      const b = t.getBounds?.();
      if (!b) continue;
      if (!Number.isFinite(b.x) || !Number.isFinite(b.y) || !Number.isFinite(b.width) || !Number.isFinite(b.height)) continue;
      if (b.width <= 0 || b.height <= 0) continue;

      const priority = Number(t.__collisionPriority) || 0;
      const area = b.width * b.height;
      candidates.push({ t, b, priority, area });
    }

    candidates.sort((a, b) => {
      if (b.priority !== a.priority) return b.priority - a.priority;
      return a.area - b.area;
    });

    const kept = [];
    for (const c of candidates) {
      let overlaps = false;
      for (const k of kept) {
        if (boxesOverlap(c.b, k.b, TEXT_COLLISION_PADDING_PX)) {
          overlaps = true;
          break;
        }
      }
      if (overlaps) {
        c.t.visible = false;
      } else {
        kept.push(c);
      }
    }
  }

  function drawSelectionBracket(selectionBox, size, lineWidthWorld) {
    if (!selectionBox) return;
    const bracketColor = 0xffa2b2;
    const bracketHeight = Math.max(size * 1.2, 9.5);
    const bracketInset = Math.max(size * 1.05, 8.5);
    const bracketDepth = Math.max(size * 0.42, 4.5);

    selectionBox.clear();
    selectionBox.lineStyle(lineWidthWorld, bracketColor, 0.96);
    selectionBox.moveTo(-bracketInset, -bracketHeight * 0.5);
    selectionBox.lineTo(-bracketInset + bracketDepth, -bracketHeight * 0.5);
    selectionBox.moveTo(-bracketInset, -bracketHeight * 0.5);
    selectionBox.lineTo(-bracketInset, bracketHeight * 0.5);
    selectionBox.moveTo(-bracketInset, bracketHeight * 0.5);
    selectionBox.lineTo(-bracketInset + bracketDepth, bracketHeight * 0.5);
    selectionBox.moveTo(bracketInset, -bracketHeight * 0.5);
    selectionBox.lineTo(bracketInset - bracketDepth, -bracketHeight * 0.5);
    selectionBox.moveTo(bracketInset, -bracketHeight * 0.5);
    selectionBox.lineTo(bracketInset, bracketHeight * 0.5);
    selectionBox.moveTo(bracketInset, bracketHeight * 0.5);
    selectionBox.lineTo(bracketInset - bracketDepth, bracketHeight * 0.5);
  }

  function makeSunGlyph(sizePx = SUN_ICON_SCREEN_PX) {
    const s = Math.max(12, Number(sizePx) || SUN_ICON_SCREEN_PX);
    const c = new PIXI.Container();

    // Outer soft glow halo
    const glowOuter = new PIXI.Graphics();
    glowOuter.beginFill(0xffa500, 0.06);
    glowOuter.drawCircle(0, 0, s * 1.2);
    glowOuter.endFill();
    glowOuter.beginFill(0xffcc33, 0.10);
    glowOuter.drawCircle(0, 0, s * 0.85);
    glowOuter.endFill();
    glowOuter.beginFill(0xffdd55, 0.15);
    glowOuter.drawCircle(0, 0, s * 0.6);
    glowOuter.endFill();

    // Long rays (8 cardinal + diagonal)
    const rays = new PIXI.Graphics();
    const rayCount = 8;
    for (let i = 0; i < rayCount; i++) {
      const angle = (i / rayCount) * Math.PI * 2;
      const isCardinal = i % 2 === 0;
      const rayLen = isCardinal ? s * 1.1 : s * 0.7;
      const rayWidth = isCardinal ? s * 0.035 : s * 0.025;
      rays.beginFill(0xffffff, isCardinal ? 0.5 : 0.3);
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      const perpCos = Math.cos(angle + Math.PI / 2);
      const perpSin = Math.sin(angle + Math.PI / 2);
      rays.moveTo(perpCos * rayWidth, perpSin * rayWidth);
      rays.lineTo(cos * rayLen, sin * rayLen);
      rays.lineTo(-perpCos * rayWidth, -perpSin * rayWidth);
      rays.closePath();
      rays.endFill();
    }

    // Fine secondary rays (16 thin spikes)
    const fineRays = new PIXI.Graphics();
    const fineCount = 16;
    for (let i = 0; i < fineCount; i++) {
      const angle = (i / fineCount) * Math.PI * 2 + Math.PI / fineCount;
      const rayLen = s * (0.35 + (i % 3) * 0.1);
      fineRays.lineStyle(0.6, 0xffffff, 0.2);
      fineRays.moveTo(0, 0);
      fineRays.lineTo(Math.cos(angle) * rayLen, Math.sin(angle) * rayLen);
    }

    // Inner bright core
    const coreOuter = new PIXI.Graphics();
    coreOuter.beginFill(0xfff8e0, 0.7);
    coreOuter.drawCircle(0, 0, s * 0.22);
    coreOuter.endFill();

    const coreInner = new PIXI.Graphics();
    coreInner.beginFill(0xffffff, 0.95);
    coreInner.drawCircle(0, 0, s * 0.11);
    coreInner.endFill();

    c.addChild(glowOuter, fineRays, rays, coreOuter, coreInner);
    c.__baseSizePx = s;
    return c;
  }

  function makeMoonIconGlyph(sizePx = MOON_ICON_SCREEN_PX) {
    const s = Math.max(8, Number(sizePx) || MOON_ICON_SCREEN_PX);
    const r = s * 0.5;
    const c = new PIXI.Container();

    const disk = new PIXI.Graphics();
    disk.beginFill(0x0b0f16, 0.96);
    disk.lineStyle(Math.max(0.8, s * 0.07), 0xb9c6da, 0.8);
    disk.drawCircle(0, 0, r);
    disk.endFill();

    const crescent = new PIXI.Graphics();
    crescent.beginFill(0xf2f6ff, 0.95);
    crescent.drawCircle(-s * 0.07, 0, r * 0.63);
    crescent.endFill();
    crescent.beginFill(0x0b0f16, 1);
    crescent.drawCircle(s * 0.13, 0, r * 0.63);
    crescent.endFill();

    const crater = new PIXI.Graphics();
    crater.beginFill(0xd7e3f7, 0.85);
    crater.drawCircle(-s * 0.19, -s * 0.18, Math.max(0.8, s * 0.07));
    crater.endFill();

    c.addChild(disk, crescent, crater);
    c.__baseSizePx = s;
    return c;
  }

  function makeJovianMoonIconGlyph(moonId, sizePx = JOVIAN_MOON_ICON_SCREEN_PX) {
    const s = Math.max(7, Number(sizePx) || JOVIAN_MOON_ICON_SCREEN_PX);
    const r = s * 0.5;
    const id = String(moonId || "").toUpperCase();
    const paletteByMoon = {
      IO: { base: 0xf2c95f, stroke: 0xf9e2a8, accentA: 0xb85b1f, accentB: 0xe58d2e, accentC: 0x8d3f17 },
      EUROPA: { base: 0xe3d8c2, stroke: 0xf3ead8, accentA: 0x9a6742, accentB: 0xc38b61, accentC: 0x7e5539 },
      GANYMEDE: { base: 0x9c8974, stroke: 0xcfbea6, accentA: 0x6c5a49, accentB: 0xb19678, accentC: 0x4b3e34 },
      CALLISTO: { base: 0x776857, stroke: 0xa18f77, accentA: 0x4f4338, accentB: 0x8d7761, accentC: 0x352d25 },
    };
    const palette = paletteByMoon[id] || paletteByMoon.CALLISTO;

    const c = new PIXI.Container();

    const disk = new PIXI.Graphics();
    disk.beginFill(palette.base, 0.96);
    disk.lineStyle(Math.max(0.75, s * 0.07), palette.stroke, 0.86);
    disk.drawCircle(0, 0, r);
    disk.endFill();

    if (id === "IO") {
      const plumeA = new PIXI.Graphics();
      plumeA.beginFill(palette.accentA, 0.82);
      plumeA.drawCircle(-s * 0.19, -s * 0.12, Math.max(0.65, s * 0.075));
      plumeA.endFill();
      const plumeB = new PIXI.Graphics();
      plumeB.beginFill(palette.accentB, 0.74);
      plumeB.drawCircle(s * 0.2, s * 0.1, Math.max(0.6, s * 0.07));
      plumeB.endFill();
      const caldera = new PIXI.Graphics();
      caldera.beginFill(palette.accentC, 0.66);
      caldera.drawCircle(s * 0.02, -s * 0.02, Math.max(0.5, s * 0.055));
      caldera.endFill();
      c.addChild(disk, plumeA, plumeB, caldera);
    } else if (id === "EUROPA") {
      const cracks = new PIXI.Graphics();
      cracks.lineStyle(Math.max(0.45, s * 0.045), palette.accentA, 0.7);
      cracks.moveTo(-s * 0.34, -s * 0.18);
      cracks.lineTo(-s * 0.1, -s * 0.02);
      cracks.lineTo(s * 0.26, s * 0.08);
      cracks.lineStyle(Math.max(0.4, s * 0.04), palette.accentB, 0.62);
      cracks.moveTo(-s * 0.25, s * 0.26);
      cracks.lineTo(s * 0.03, s * 0.05);
      cracks.lineTo(s * 0.28, -s * 0.12);
      const pit = new PIXI.Graphics();
      pit.beginFill(palette.accentC, 0.32);
      pit.drawCircle(s * 0.14, s * 0.14, Math.max(0.45, s * 0.045));
      pit.endFill();
      c.addChild(disk, cracks, pit);
    } else if (id === "GANYMEDE") {
      const terrainA = new PIXI.Graphics();
      terrainA.beginFill(palette.accentB, 0.42);
      terrainA.drawCircle(-s * 0.17, -s * 0.08, Math.max(0.7, s * 0.09));
      terrainA.endFill();
      const terrainB = new PIXI.Graphics();
      terrainB.beginFill(palette.accentA, 0.5);
      terrainB.drawCircle(s * 0.16, s * 0.14, Math.max(0.65, s * 0.08));
      terrainB.endFill();
      const ridge = new PIXI.Graphics();
      ridge.lineStyle(Math.max(0.42, s * 0.043), palette.accentC, 0.58);
      ridge.moveTo(-s * 0.3, s * 0.02);
      ridge.lineTo(-s * 0.05, s * 0.08);
      ridge.lineTo(s * 0.24, s * 0.24);
      c.addChild(disk, terrainA, terrainB, ridge);
    } else {
      const craterA = new PIXI.Graphics();
      craterA.beginFill(palette.accentA, 0.64);
      craterA.drawCircle(-s * 0.2, -s * 0.13, Math.max(0.58, s * 0.065));
      craterA.endFill();
      const craterB = new PIXI.Graphics();
      craterB.beginFill(palette.accentB, 0.58);
      craterB.drawCircle(s * 0.07, s * 0.18, Math.max(0.6, s * 0.068));
      craterB.endFill();
      const craterC = new PIXI.Graphics();
      craterC.beginFill(palette.accentC, 0.7);
      craterC.drawCircle(s * 0.2, -s * 0.15, Math.max(0.5, s * 0.055));
      craterC.endFill();
      const ring = new PIXI.Graphics();
      ring.lineStyle(Math.max(0.36, s * 0.04), palette.accentC, 0.46);
      ring.drawCircle(-s * 0.2, -s * 0.13, Math.max(0.8, s * 0.09));
      c.addChild(disk, craterA, craterB, craterC, ring);
    }

    c.__baseSizePx = s;
    return c;
  }

  function makeAsteroidIconGlyph(sizePx = ASTEROID_ICON_SCREEN_PX) {
    const s = Math.max(8, Number(sizePx) || ASTEROID_ICON_SCREEN_PX);
    const c = new PIXI.Container();

    const rock = new PIXI.Graphics();
    rock.beginFill(0x8f97a6, 0.95);
    rock.lineStyle(Math.max(0.8, s * 0.07), 0xd5dcea, 0.72);
    rock.moveTo(-s * 0.46, -s * 0.12);
    rock.lineTo(-s * 0.18, -s * 0.44);
    rock.lineTo(s * 0.23, -s * 0.39);
    rock.lineTo(s * 0.47, -s * 0.08);
    rock.lineTo(s * 0.39, s * 0.3);
    rock.lineTo(s * 0.08, s * 0.47);
    rock.lineTo(-s * 0.33, s * 0.35);
    rock.closePath();
    rock.endFill();

    const ridge = new PIXI.Graphics();
    ridge.lineStyle(Math.max(0.65, s * 0.05), 0xffffff, 0.42);
    ridge.moveTo(-s * 0.2, -s * 0.16);
    ridge.lineTo(s * 0.18, -s * 0.08);
    ridge.lineTo(s * 0.03, s * 0.17);

    const pitA = new PIXI.Graphics();
    pitA.beginFill(0x5d6470, 0.82);
    pitA.drawCircle(-s * 0.13, s * 0.1, Math.max(0.75, s * 0.08));
    pitA.endFill();

    const pitB = new PIXI.Graphics();
    pitB.beginFill(0x6c7382, 0.72);
    pitB.drawCircle(s * 0.14, -s * 0.17, Math.max(0.65, s * 0.06));
    pitB.endFill();

    c.addChild(rock, ridge, pitA, pitB);
    c.__baseSizePx = s;
    return c;
  }

  // ---------- Utilities ----------
  function hasAncestor(id, ancestorId, parentById) {
    let cur = parentById.get(id);
    while (cur) {
      if (cur === ancestorId) return true;
      cur = parentById.get(cur);
    }
    return false;
  }

  function hasAsteroidHint(value) {
    const text = String(value || "").toLowerCase();
    if (!text) return false;
    for (const hint of ASTEROID_HINTS) {
      if (text.includes(hint)) return true;
    }
    return false;
  }

  function isAsteroidLocation(loc) {
    if (!loc) return false;
    if (hasAsteroidHint(loc.id) || hasAsteroidHint(loc.name)) return true;

    let cur = locationParentById.get(loc.id);
    while (cur) {
      if (hasAsteroidHint(cur)) return true;
      const parentLoc = locationsById.get(cur);
      if (parentLoc && (hasAsteroidHint(parentLoc.id) || hasAsteroidHint(parentLoc.name))) return true;
      cur = locationParentById.get(cur);
    }
    return false;
  }

  function stringHash01(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return ((h >>> 0) % 1000000) / 1000000;
  }

  function parseHexColor(color, fallback = 0xff2c4d) {
    if (typeof color !== "string") return fallback;
    let hex = color.trim();
    if (hex.startsWith("#")) hex = hex.slice(1);
    if (hex.length === 3) hex = hex.split("").map((c) => c + c).join("");
    if (!/^[0-9a-fA-F]{6}$/.test(hex)) return fallback;
    const parsed = Number.parseInt(hex, 16);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function mixColor(colorA, colorB, t) {
    const tt = Math.max(0, Math.min(1, Number(t) || 0));
    const aR = (colorA >> 16) & 255;
    const aG = (colorA >> 8) & 255;
    const aB = colorA & 255;
    const bR = (colorB >> 16) & 255;
    const bG = (colorB >> 8) & 255;
    const bB = colorB & 255;
    const r = Math.round(aR + (bR - aR) * tt);
    const g = Math.round(aG + (bG - aG) * tt);
    const b = Math.round(aB + (bB - aB) * tt);
    return (r << 16) | (g << 8) | b;
  }

  function normalizeVec(x, y, fx = 1, fy = 0) {
    const m = Math.hypot(x, y);
    if (m < 1e-9) return { x: fx, y: fy };
    return { x: x / m, y: y / m };
  }

  function tangentFromAnchor(point, anchor, preferredDir) {
    if (!anchor) return normalizeVec(preferredDir.x, preferredDir.y, 1, 0);
    const radial = normalizeVec(point.x - anchor.rx, point.y - anchor.ry, preferredDir.x, preferredDir.y);
    const tA = { x: -radial.y, y: radial.x };
    const tB = { x: radial.y, y: -radial.x };
    const dA = tA.x * preferredDir.x + tA.y * preferredDir.y;
    const dB = tB.x * preferredDir.x + tB.y * preferredDir.y;
    return dA >= dB ? tA : tB;
  }

  function getLocationBodyCenter(locationId) {
    // Walk up the location tree to find the nearest grp_* body ancestor.
    let cur = String(locationId || "");
    const seen = new Set();
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      if (cur.startsWith("grp_") && !cur.endsWith("_orbits") && !cur.endsWith("_sites") && !cur.endsWith("_moons")) {
        const loc = locationsById.get(cur);
        if (loc) return loc;
      }
      cur = String(locationParentById.get(cur) || "");
    }
    return null;
  }

  /**
   * Walk up from a location to find its top-level solar body group
   * (the ancestor whose parent is grp_sun). Returns the group id string.
   * For Earth and its Moon, both return "grp_earth".
   * For Mars and its moons, returns "grp_mars".
   */
  function getLocationSolarGroup(locationId) {
    let cur = String(locationId || "");
    const seen = new Set();
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      if (cur.startsWith("grp_") && !cur.endsWith("_orbits") && !cur.endsWith("_sites") && !cur.endsWith("_moons")) {
        const parent = String(locationParentById.get(cur) || "");
        if (parent === "grp_sun" || parent === "") return cur;
      }
      cur = String(locationParentById.get(cur) || "");
    }
    return null;
  }

  function dockedChipAnchorIdForLocation(locationId) {
    let cur = String(locationId || "");
    const seen = new Set();
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      if (cur.startsWith("grp_") && !cur.endsWith("_orbits")) return cur;
      cur = String(locationParentById.get(cur) || "");
    }
    return String(locationId || "");
  }

  function pickOrbitTangent(body, point, towardPoint, fallbackDir) {
    if (!body) return normalizeVec(fallbackDir.x, fallbackDir.y, 1, 0);

    const radial = normalizeVec(point.x - body.rx, point.y - body.ry, fallbackDir.x, fallbackDir.y);
    const ccw = { x: -radial.y, y: radial.x };
    const cw = { x: radial.y, y: -radial.x };
    const toTarget = normalizeVec(towardPoint.x - point.x, towardPoint.y - point.y, fallbackDir.x, fallbackDir.y);

    const ccwScore = ccw.x * toTarget.x + ccw.y * toTarget.y;
    const cwScore = cw.x * toTarget.x + cw.y * toTarget.y;
    return ccwScore >= cwScore ? ccw : cw;
  }

  /**
   * Compute a Hohmann transfer arc (half-ellipse) between two positions
   * around a central focus (the Sun). Returns a polyline curve object.
   *
   * The radial profile follows the Hohmann ellipse r(ν) equation so
   * the arc always transitions smoothly from r₁ to r₂ with the
   * characteristic outward (or inward) bulge.
   *
   * The angular sweep always takes the **shorter** path around the Sun
   * (≤ π radians), so arcs never loop more than halfway around.
   * This is decoupled from the true-anomaly sweep (which always covers
   * a full half-ellipse, 0→π or π→2π) so both endpoints hit the correct
   * radii regardless of the angular separation.
   */
  function computeHohmannArc(sunX, sunY, fromX, fromY, toX, toY) {
    const r1 = Math.max(1e-6, Math.hypot(fromX - sunX, fromY - sunY));
    const r2 = Math.max(1e-6, Math.hypot(toX - sunX, toY - sunY));
    const theta1 = Math.atan2(fromY - sunY, fromX - sunX);
    const theta2raw = Math.atan2(toY - sunY, toX - sunX);

    // CCW (prograde) angular sweep in [0, 2π)
    const ccw = ((theta2raw - theta1) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI);
    // Pick the shorter path (≤ π).  Positive = CCW, negative = CW.
    let sweep = (ccw <= Math.PI) ? ccw : -(2 * Math.PI - ccw);
    // Tiny minimum so nearly-aligned endpoints still produce a visible arc
    if (Math.abs(sweep) < 0.05) sweep = Math.sign(sweep || 1) * 0.05;

    // Hohmann ellipse parameters
    const a = (r1 + r2) / 2;                   // semi-major axis
    const e = Math.abs(r2 - r1) / (r1 + r2);   // eccentricity
    const p = a * (1 - e * e);                  // semi-latus rectum = 2·r1·r2/(r1+r2)

    // True anomaly: full half-ellipse so radius transitions from r1 → r2
    const nuStart = (r1 <= r2) ? 0 : Math.PI;
    const nuEnd   = (r1 <= r2) ? Math.PI : 2 * Math.PI;

    const numSamples = 96;
    const points = [];
    const cumDist = [0];
    for (let i = 0; i <= numSamples; i++) {
      const t = i / numSamples;
      // Radius from the Hohmann ellipse equation
      const nu = nuStart + (nuEnd - nuStart) * t;
      const r = p / (1 + e * Math.cos(nu));
      // Angle: sweep from θ₁ toward θ₂ via the shorter path
      const angle = theta1 + sweep * t;
      const x = sunX + r * Math.cos(angle);
      const y = sunY + r * Math.sin(angle);
      points.push({ x, y });
      if (i > 0) {
        const ddx = x - points[i - 1].x;
        const ddy = y - points[i - 1].y;
        cumDist.push(cumDist[i - 1] + Math.hypot(ddx, ddy));
      }
    }
    return { type: "arc", points, cumDist };
  }

  /** Interpolate position along an arc polyline at fractional t ∈ [0,1]. */
  function arcPoint(curve, t) {
    const totalDist = curve.cumDist[curve.cumDist.length - 1] || 0;
    return pointOnPolyline(curve.points, curve.cumDist, Math.max(0, Math.min(1, t)) * totalDist);
  }

  /** Approximate tangent vector along an arc polyline at fractional t ∈ [0,1]. */
  function arcTangent(curve, t) {
    const dt = 0.005;
    const t1 = Math.max(0, t - dt);
    const t2 = Math.min(1, t + dt);
    const p1 = arcPoint(curve, t1);
    const p2 = arcPoint(curve, t2);
    return { x: p2.x - p1.x, y: p2.y - p1.y };
  }

  /** Unified curve position: dispatches between Bézier, arc, and composite. */
  function curvePoint(curve, t) {
    if (curve.type === "composite") return compositePoint(curve, t);
    if (curve.type === "arc") return arcPoint(curve, t);
    return cubicPoint(curve, t);
  }

  /** Unified curve tangent: dispatches between Bézier, arc, and composite. */
  function curveTangent(curve, t) {
    if (curve.type === "composite") return compositeTangent(curve, t);
    if (curve.type === "arc") return arcTangent(curve, t);
    return cubicTangent(curve, t);
  }

  /**
   * Build a composite curve from multiple transfer legs, stitched into one
   * continuous polyline. Each leg is weighted by its time-of-flight so the
   * ship moves at a pace proportional to real transfer time.
   */
  function buildCompositeCurve(legs) {
    if (!legs || !legs.length) return null;

    const totalTof = legs.reduce((s, l) => s + Math.max(1, Number(l.tof_s) || (Number(l.arrival_time) - Number(l.departure_time))), 0);
    const allPoints = [];
    const legBounds = []; // { startFrac, endFrac } in [0,1] for each leg
    let timeSoFar = 0;
    let firstLegTrack = null;
    let lastLegTrack = null;

    for (let i = 0; i < legs.length; i++) {
      const leg = legs[i];
      const fromId = String(leg.from_id || "");
      const toId = String(leg.to_id || "");
      const depTime = Number(leg.departure_time);
      const arrTime = Number(leg.arrival_time);
      const isIP = !!(leg.is_interplanetary);
      const tof = Math.max(1, Number(leg.tof_s) || (arrTime - depTime));

      const fromAnchor = getTransitAnchorWorld(fromId, depTime);
      const fromLive = locationsById.get(fromId);
      const fromLoc = fromAnchor || fromLive;
      const toAnchor = getTransitAnchorWorld(toId, arrTime);
      const toLive = locationsById.get(toId);
      const toLoc = toAnchor || toLive;
      if (!fromLoc || !toLoc) continue;

      const legCurve = computeTransitCurve(fromId, toId, fromLoc, toLoc, isIP, depTime, arrTime);
      if (!legCurve) continue;

      // Capture tracking from first and last legs for composite warp
      if (!firstLegTrack && legCurve.trackStartId) {
        firstLegTrack = { id: legCurve.trackStartId, orig: legCurve.trackStartOrig };
      }
      if (legCurve.trackEndId) {
        lastLegTrack = { id: legCurve.trackEndId, orig: legCurve.trackEndOrig };
      }

      // Sample this leg's curve into points
      let pts;
      if (legCurve.type === "arc") {
        pts = legCurve.points;
      } else {
        pts = [];
        for (let s = 0; s <= 64; s++) pts.push(cubicPoint(legCurve, s / 64));
      }

      const startFrac = timeSoFar / totalTof;
      timeSoFar += tof;
      const endFrac = timeSoFar / totalTof;
      legBounds.push({ startFrac, endFrac });

      // Append points (skip first point of subsequent legs to avoid duplicates)
      const startIdx = (allPoints.length > 0) ? 1 : 0;
      for (let j = startIdx; j < pts.length; j++) {
        allPoints.push({ x: pts[j].x, y: pts[j].y, frac: startFrac + (endFrac - startFrac) * (j / (pts.length - 1)) });
      }
    }

    if (allPoints.length < 2) return null;

    // Build cumulative distances
    const cumDist = [0];
    for (let i = 1; i < allPoints.length; i++) {
      const dx = allPoints[i].x - allPoints[i - 1].x;
      const dy = allPoints[i].y - allPoints[i - 1].y;
      cumDist.push(cumDist[i - 1] + Math.hypot(dx, dy));
    }

    const composite = { type: "composite", points: allPoints, cumDist, legBounds };
    if (firstLegTrack) { composite.trackStartId = firstLegTrack.id; composite.trackStartOrig = firstLegTrack.orig; }
    if (lastLegTrack) { composite.trackEndId = lastLegTrack.id; composite.trackEndOrig = lastLegTrack.orig; }
    return composite;
  }

  /** Map overall t ∈ [0,1] to a distance along the composite polyline,
   *  respecting per-leg time weighting. */
  function compositeDistAtT(curve, t) {
    const tc = Math.max(0, Math.min(1, t));
    // Find the polyline point whose frac is closest
    const pts = curve.points;
    if (tc <= 0) return 0;
    if (tc >= 1) return curve.cumDist[curve.cumDist.length - 1];
    // Binary search for the segment spanning tc
    let lo = 0, hi = pts.length - 1;
    while (lo < hi - 1) {
      const mid = (lo + hi) >> 1;
      if (pts[mid].frac <= tc) lo = mid; else hi = mid;
    }
    const segFrac = pts[hi].frac - pts[lo].frac;
    const localT = segFrac > 1e-9 ? (tc - pts[lo].frac) / segFrac : 0;
    return curve.cumDist[lo] + (curve.cumDist[hi] - curve.cumDist[lo]) * localT;
  }

  function compositePoint(curve, t) {
    const d = compositeDistAtT(curve, t);
    return pointOnPolyline(curve.points, curve.cumDist, d);
  }

  function compositeTangent(curve, t) {
    const dt = 0.003;
    const p1 = compositePoint(curve, Math.max(0, t - dt));
    const p2 = compositePoint(curve, Math.min(1, t + dt));
    return { x: p2.x - p1.x, y: p2.y - p1.y };
  }

  /**
   * Compute a linear warp that adjusts a transit curve's endpoints
   * to track the current live positions of departure/arrival bodies.
   * Each point on the curve at fraction t gets shifted by:
   *   delta = (1-t)*startDelta + t*endDelta
   * Returns null if no warp is needed (positions unchanged).
   */
  function computeCurveWarp(curve) {
    if (!curve || (!curve.trackStartId && !curve.trackEndId)) return null;
    const fromLive = curve.trackStartId ? locationsById.get(curve.trackStartId) : null;
    const toLive = curve.trackEndId ? locationsById.get(curve.trackEndId) : null;
    const dxS = fromLive && curve.trackStartOrig ? fromLive.rx - curve.trackStartOrig.x : 0;
    const dyS = fromLive && curve.trackStartOrig ? fromLive.ry - curve.trackStartOrig.y : 0;
    const dxE = toLive && curve.trackEndOrig ? toLive.rx - curve.trackEndOrig.x : 0;
    const dyE = toLive && curve.trackEndOrig ? toLive.ry - curve.trackEndOrig.y : 0;
    if (Math.abs(dxS) + Math.abs(dyS) + Math.abs(dxE) + Math.abs(dyE) < 0.01) return null;
    return { dxS, dyS, dxE, dyE };
  }

  /** Apply warp displacement at fraction t ∈ [0,1] along the curve. */
  function warpXY(x, y, t, w) {
    if (!w) return { x, y };
    const u = 1 - t;
    return { x: x + u * w.dxS + t * w.dxE, y: y + u * w.dyS + t * w.dyE };
  }

  /**
   * Create warped copies of a polyline's points and cumulative distances.
   * The warp linearly interpolates a start-displacement and end-displacement
   * based on each point's fractional position along the path.
   */
  function warpPolyline(points, cumDist, warp) {
    if (!warp) return { points, cumDist };
    const total = cumDist[cumDist.length - 1] || 1;
    const wp = points.map((p, i) => {
      const frac = cumDist[i] / total;
      const u = 1 - frac;
      return { x: p.x + u * warp.dxS + frac * warp.dxE, y: p.y + u * warp.dyS + frac * warp.dyE, frac: p.frac };
    });
    const wc = [0];
    for (let i = 1; i < wp.length; i++) {
      const dx = wp[i].x - wp[i - 1].x;
      const dy = wp[i].y - wp[i - 1].y;
      wc.push(wc[i - 1] + Math.hypot(dx, dy));
    }
    return { points: wp, cumDist: wc };
  }

  function computeTransitCurve(fromLocId, toLocId, fromLoc, toLoc, isInterplanetary, legDeparture, legArrival) {
    const p0 = { x: fromLoc.rx, y: fromLoc.ry };
    const p3 = { x: toLoc.rx, y: toLoc.ry };

    // For solar interplanetary transfers, render a Hohmann transfer arc
    if (isInterplanetary) {
      const fromSolar = getLocationSolarGroup(fromLocId);
      const toSolar = getLocationSolarGroup(toLocId);
      if (fromSolar && toSolar && fromSolar !== toSolar) {
        const sun = locationsById.get("grp_sun");
        if (sun) {
          // Use parent body centers for the arc so endpoints land on the
          // planet orbital rings, not offset by local-orbit expansion.
          // For endpoints: use the arrival-time ANGLE (where the planet will
          // be along its orbit) but the CURRENT orbital ring radius, so the
          // arc visually lands on the drawn orbit ring.
          const fromBodyAnchor = legDeparture != null ? getTransitAnchorWorld(fromSolar, legDeparture) : null;
          const fromBodyLive = locationsById.get(fromSolar);
          const fromBodyFuture = fromBodyAnchor || fromBodyLive;
          const toBodyAnchor = legArrival != null ? getTransitAnchorWorld(toSolar, legArrival) : null;
          const toBodyLive = locationsById.get(toSolar);
          const toBodyFuture = toBodyAnchor || toBodyLive;

          // Snap to current ring radius but arrival-time angle
          function snapToRingRadius(futureBody, liveBody, sunPos) {
            if (!futureBody || !liveBody) return futureBody || liveBody;
            const fdx = futureBody.rx - sunPos.rx;
            const fdy = futureBody.ry - sunPos.ry;
            const futureR = Math.hypot(fdx, fdy);
            if (futureR < 1e-6) return futureBody;
            const futureAngle = Math.atan2(fdy, fdx);
            const liveR = Math.hypot(liveBody.rx - sunPos.rx, liveBody.ry - sunPos.ry);
            return {
              rx: sunPos.rx + liveR * Math.cos(futureAngle),
              ry: sunPos.ry + liveR * Math.sin(futureAngle),
            };
          }
          const arcFromBody = snapToRingRadius(fromBodyFuture, fromBodyLive, sun);
          const arcToBody = snapToRingRadius(toBodyFuture, toBodyLive, sun);
          const arcFrom = arcFromBody ? { x: arcFromBody.rx, y: arcFromBody.ry } : p0;
          const arcTo = arcToBody ? { x: arcToBody.rx, y: arcToBody.ry } : p3;

          // For orbit nodes with significant radius (e.g. JUP_HO at 40M km),
          // extend the arc endpoint outward from the planet center along the
          // Sun→planet direction so the path visually reaches the orbit ring.
          function offsetByOrbitRadius(pt, locId) {
            const oi = orbitInfo.get(locId);
            if (!oi || oi.radius <= 0) return;
            const dx = pt.x - sun.rx;
            const dy = pt.y - sun.ry;
            const d = Math.hypot(dx, dy);
            if (d > 1e-6) {
              pt.x += (dx / d) * oi.radius;
              pt.y += (dy / d) * oi.radius;
            }
          }
          offsetByOrbitRadius(arcFrom, fromLocId);
          offsetByOrbitRadius(arcTo, toLocId);

          const arc = computeHohmannArc(sun.rx, sun.ry, arcFrom.x, arcFrom.y, arcTo.x, arcTo.y);
          // Track solar body positions so the arc endpoints follow planet movement
          if (fromBodyLive) { arc.trackStartId = fromSolar; arc.trackStartOrig = { x: fromBodyLive.rx, y: fromBodyLive.ry }; }
          if (toBodyLive) { arc.trackEndId = toSolar; arc.trackEndOrig = { x: toBodyLive.rx, y: toBodyLive.ry }; }
          return arc;
        }
      }
    }

    // Non-interplanetary or Earth-Moon: use existing orbital Bézier
    const dx = p3.x - p0.x;
    const dy = p3.y - p0.y;
    const d = Math.max(1e-6, Math.hypot(dx, dy));
    const dir = normalizeVec(dx, dy, 1, 0);

    const fromBody = getLocationBodyCenter(fromLocId);
    const toBody = getLocationBodyCenter(toLocId);
    const samePrimary = !!(fromBody && toBody && fromBody.id === toBody.id);
    const primaryBody = samePrimary ? fromBody : (fromBody || toBody || null);

    const departTan = pickOrbitTangent(fromBody || primaryBody, p0, p3, dir);
    const arriveTan = pickOrbitTangent(toBody || primaryBody, p3, p0, { x: -dir.x, y: -dir.y });

    let c1Dist = Math.max(12, d * 0.36);
    let c2Dist = Math.max(12, d * 0.33);
    let bendVec = { x: 0, y: 0 };

    if (samePrimary && primaryBody) {
      const r0 = Math.hypot(p0.x - primaryBody.rx, p0.y - primaryBody.ry);
      const r1 = Math.hypot(p3.x - primaryBody.rx, p3.y - primaryBody.ry);
      const semiMajor = Math.max(1e-6, (r0 + r1) * 0.5);

      c1Dist = Math.max(12, Math.min(d * 0.62, semiMajor * 0.88));
      c2Dist = Math.max(12, Math.min(d * 0.58, semiMajor * 0.82));

      const midToBody = normalizeVec(((p0.x + p3.x) * 0.5) - primaryBody.rx, ((p0.y + p3.y) * 0.5) - primaryBody.ry, 0, 0);
      const outward = r1 >= r0 ? 1 : -1;
      const bendMag = Math.min(d * 0.18, Math.max(10, Math.abs(r1 - r0) * 0.34));
      bendVec = { x: midToBody.x * bendMag * outward, y: midToBody.y * bendMag * outward };
    }

    const c1 = {
      x: p0.x + departTan.x * c1Dist + bendVec.x * 0.55,
      y: p0.y + departTan.y * c1Dist + bendVec.y * 0.55,
    };
    const c2 = {
      x: p3.x - arriveTan.x * c2Dist + bendVec.x * 0.35,
      y: p3.y - arriveTan.y * c2Dist + bendVec.y * 0.35,
    };

    // Track solar-group body positions so Bézier endpoints follow planet movement
    const bezier = { p0, c1, c2, p3 };
    const fromTrackId = getLocationSolarGroup(fromLocId) || fromLocId;
    const toTrackId = getLocationSolarGroup(toLocId) || toLocId;
    const fromTrack = locationsById.get(fromTrackId);
    const toTrack = locationsById.get(toTrackId);
    if (fromTrack) { bezier.trackStartId = fromTrackId; bezier.trackStartOrig = { x: fromTrack.rx, y: fromTrack.ry }; }
    if (toTrack) { bezier.trackEndId = toTrackId; bezier.trackEndOrig = { x: toTrack.rx, y: toTrack.ry }; }
    return bezier;
  }

  function cubicPoint(curve, t) {
    const u = 1 - t;
    const tt = t * t;
    const uu = u * u;
    const uuu = uu * u;
    const ttt = tt * t;
    return {
      x: (curve.p0.x * uuu) + (3 * curve.c1.x * uu * t) + (3 * curve.c2.x * u * tt) + (curve.p3.x * ttt),
      y: (curve.p0.y * uuu) + (3 * curve.c1.y * uu * t) + (3 * curve.c2.y * u * tt) + (curve.p3.y * ttt),
    };
  }

  function cubicTangent(curve, t) {
    const u = 1 - t;
    const p0 = curve.p0;
    const p1 = curve.c1;
    const p2 = curve.c2;
    const p3 = curve.p3;
    return {
      x: 3 * u * u * (p1.x - p0.x) + 6 * u * t * (p2.x - p1.x) + 3 * t * t * (p3.x - p2.x),
      y: 3 * u * u * (p1.y - p0.y) + 6 * u * t * (p2.y - p1.y) + 3 * t * t * (p3.y - p2.y),
    };
  }

  function pointOnPolyline(points, cumulative, targetDist) {
    const total = cumulative[cumulative.length - 1] || 0;
    const d = Math.max(0, Math.min(total, targetDist));
    // Binary search for the segment containing targetDist
    let lo = 1, hi = cumulative.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cumulative[mid] < d) lo = mid + 1;
      else hi = mid;
    }
    const i = lo;
    if (i < cumulative.length) {
      const segStart = cumulative[i - 1];
      const segLen = Math.max(1e-9, cumulative[i] - segStart);
      const t = (d - segStart) / segLen;
      const a = points[i - 1];
      const b = points[i];
      return {
        x: a.x + (b.x - a.x) * t,
        y: a.y + (b.y - a.y) * t,
      };
    }
    return points[points.length - 1];
  }

  function drawDashedTransitPath(pathGfx, curve, size, isSelected, displayScale = 1, shipProgress = 0, warp = null) {
    if (!pathGfx || !curve) return;

    let points, cumulative;
    if (curve.type === "arc" || curve.type === "composite") {
      // Arc and composite curves already have pre-computed polyline + cumulative distances
      points = curve.points;
      cumulative = curve.cumDist;
    } else {
      // Bézier: sample into a polyline (cached on curve object)
      if (!curve._polyPoints) {
        const samples = 128;
        curve._polyPoints = [];
        for (let i = 0; i <= samples; i++) curve._polyPoints.push(cubicPoint(curve, i / samples));
        curve._polyCum = [0];
        for (let i = 1; i < curve._polyPoints.length; i++) {
          const dx = curve._polyPoints[i].x - curve._polyPoints[i - 1].x;
          const dy = curve._polyPoints[i].y - curve._polyPoints[i - 1].y;
          curve._polyCum.push(curve._polyCum[i - 1] + Math.hypot(dx, dy));
        }
      }
      points = curve._polyPoints;
      cumulative = curve._polyCum;
    }

    // Apply live-position warp so path endpoints track moving celestial bodies
    if (warp) {
      const w = warpPolyline(points, cumulative, warp);
      points = w.points;
      cumulative = w.cumDist;
    }

    const total = cumulative[cumulative.length - 1] || 0;
    if (total < 1) return;
    const scaleSafe = Math.max(0.05, Number(displayScale) || 1);
    const shipSizeFactor = clamp((Number(size) || 10) / 10, 0.42, 1.55);
    const zoom = Math.max(0.0001, world.scale.x);

    // --- Stroke widths (screen-pixel based, divided by zoom to stay constant on screen) ---
    const baseStroke = (isSelected ? 2.4 : 1.5) * shipSizeFactor;
    const lineWidth = Math.max(1.0, baseStroke) / zoom;
    const thinWidth = Math.max(0.6, baseStroke * 0.5) / zoom;
    const chevWidth = Math.max(0.8, baseStroke * 0.7) / zoom;

    // --- Colors ---
    const aheadColor = isSelected ? 0xb8d4ff : 0x8ab4e8;
    const traveledColor = isSelected ? 0x6688aa : 0x556677;
    const chevronColor = isSelected ? 0xddeeff : 0xaaccee;
    const aheadAlpha = isSelected ? 0.55 : 0.38;
    const traveledAlpha = isSelected ? 0.18 : 0.10;

    // --- Split point along the path ---
    // For composite curves, shipProgress is time-fraction — convert to distance
    const shipDist = (curve.type === "composite")
      ? compositeDistAtT(curve, clamp(shipProgress, 0, 1))
      : clamp(shipProgress, 0, 1) * total;

    pathGfx.clear();

    // Find the polyline index where the ship currently is (for splitting
    // the path into traveled vs. ahead portions using actual polyline points
    // instead of re-sampling, which produces much smoother curves).
    let shipSegIdx = 0;
    for (let i = 1; i < cumulative.length; i++) {
      if (cumulative[i] >= shipDist) { shipSegIdx = i; break; }
      shipSegIdx = i;
    }
    // Exact interpolated ship position on the polyline
    const shipPt = pointOnPolyline(points, cumulative, shipDist);

    // 1) Traveled portion (thin, dim line behind the ship)
    if (shipDist > 1) {
      pathGfx.lineStyle(thinWidth, traveledColor, traveledAlpha);
      pathGfx.moveTo(points[0].x, points[0].y);
      for (let i = 1; i < shipSegIdx; i++) {
        pathGfx.lineTo(points[i].x, points[i].y);
      }
      pathGfx.lineTo(shipPt.x, shipPt.y);
    }

    // 2) Ahead portion (smooth solid line from ship to destination)
    const remainDist = total - shipDist;
    if (remainDist > 1) {
      pathGfx.lineStyle(lineWidth, aheadColor, aheadAlpha);
      pathGfx.moveTo(shipPt.x, shipPt.y);
      for (let i = shipSegIdx; i < points.length; i++) {
        if (cumulative[i] <= shipDist) continue;
        pathGfx.lineTo(points[i].x, points[i].y);
      }
    }

    // 3) Static directional chevron dashes along the ahead portion
    const dashLen = Math.max(3, size * SHIP_PATH_DASH_LEN_MULT * 0.7);
    const gapLen = Math.max(4, size * SHIP_PATH_GAP_LEN_MULT * 1.8);

    if (remainDist > dashLen * 2) {
      const maxChevrons = isSelected ? 20 : 8;
      let chevCount = 0;
      for (let d = shipDist; d < total; d += dashLen + gapLen) {
        if (chevCount >= maxChevrons) break;
        const fadeIn = smoothstep(shipDist, shipDist + remainDist * 0.05, d);
        const fadeOut = 1 - smoothstep(total - remainDist * 0.08, total, d);
        const segAlpha = fadeIn * fadeOut * (isSelected ? 0.55 : SHIP_PATH_CHEVRON_ALPHA);
        if (segAlpha < 0.01) continue;
        pathGfx.lineStyle(chevWidth, chevronColor, segAlpha);
        const a = pointOnPolyline(points, cumulative, d);
        const b = pointOnPolyline(points, cumulative, Math.min(total, d + dashLen));
        pathGfx.moveTo(a.x, a.y);
        pathGfx.lineTo(b.x, b.y);
        chevCount++;
      }
    }

    // 4) Destination diamond marker
    const destPt = points[points.length - 1];
    const markerSize = Math.max(3, 5) / zoom;
    const destAlpha = isSelected ? 0.7 : SHIP_PATH_DEST_MARKER_ALPHA;
    pathGfx.lineStyle(Math.max(0.5, 1.0) / zoom, chevronColor, destAlpha);
    pathGfx.beginFill(aheadColor, destAlpha * 0.4);
    pathGfx.moveTo(destPt.x, destPt.y - markerSize);
    pathGfx.lineTo(destPt.x + markerSize, destPt.y);
    pathGfx.lineTo(destPt.x, destPt.y + markerSize);
    pathGfx.lineTo(destPt.x - markerSize, destPt.y);
    pathGfx.closePath();
    pathGfx.endFill();

    // 5) Origin dot
    const originPt = points[0];
    const originSize = Math.max(2, 3) / zoom;
    pathGfx.lineStyle(0);
    pathGfx.beginFill(traveledColor, traveledAlpha * 2.5);
    pathGfx.drawCircle(originPt.x, originPt.y, originSize);
    pathGfx.endFill();
  }

  /**
   * Draw faint arcs for future (not-yet-active) transfer legs so the full
   * multi-leg route is visible on the map even before the ship reaches them.
   */
  function drawFutureTransitLegs(pathGfx, ship, activeLegIndex, shipSize, isSelected) {
    if (!pathGfx || !ship) return;
    const legs = Array.isArray(ship.transfer_legs) ? ship.transfer_legs : [];
    if (activeLegIndex < 0 || activeLegIndex >= legs.length - 1) return;

    const zoom = Math.max(0.0001, world.scale.x);
    const shipSizeFactor = clamp((Number(shipSize) || 10) / 10, 0.42, 1.55);
    const lineWidth = Math.max(0.8, (isSelected ? 1.8 : 1.0) * shipSizeFactor) / zoom;
    const futureColor = isSelected ? 0x8ab4e8 : 0x7a9cc0;
    const futureAlpha = isSelected ? 0.25 : 0.15;

    for (let i = activeLegIndex + 1; i < legs.length; i++) {
      const leg = legs[i];
      if (!leg) continue;
      const fromId = String(leg.from_id || "");
      const toId = String(leg.to_id || "");
      const depTime = Number(leg.departure_time);
      const arrTime = Number(leg.arrival_time);
      const isInterplanetary = !!(leg.is_interplanetary);

      const fromAnchor = getTransitAnchorWorld(fromId, depTime);
      const fromLive = locationsById.get(fromId);
      const fromLoc = fromAnchor || fromLive;
      const toAnchor = getTransitAnchorWorld(toId, arrTime);
      const toLive = locationsById.get(toId);
      const toLoc = toAnchor || toLive;
      if (!fromLoc || !toLoc) continue;

      const curve = computeTransitCurve(fromId, toId, fromLoc, toLoc, isInterplanetary, depTime, arrTime);
      if (!curve) continue;

      // Draw as a simple faint solid line
      let points;
      if (curve.type === "arc") {
        points = curve.points;
      } else {
        points = [];
        for (let s = 0; s <= 64; s++) points.push(cubicPoint(curve, s / 64));
      }
      if (!points || points.length < 2) continue;

      pathGfx.lineStyle(lineWidth, futureColor, futureAlpha);
      pathGfx.moveTo(points[0].x, points[0].y);
      for (let j = 1; j < points.length; j++) {
        pathGfx.lineTo(points[j].x, points[j].y);
      }
    }
  }

  function pickActiveTransferLeg(ship, nowGameS) {
    const legs = Array.isArray(ship?.transfer_legs) ? ship.transfer_legs : [];
    if (!legs.length) return null;

    let active = legs.find((leg) => nowGameS >= Number(leg.departure_time) && nowGameS <= Number(leg.arrival_time));
    if (!active) {
      if (nowGameS < Number(legs[0].departure_time)) active = legs[0];
      else active = legs[legs.length - 1];
    }
    if (!active) return null;
    const idx = Math.max(0, legs.indexOf(active));
    return {
      index: idx,
      count: legs.length,
      leg: active,
    };
  }

  function drawTransitLegMarkers(pathGfx, ship, nowGameS, isSelected) {
    if (!pathGfx || !ship) return;
    const legs = Array.isArray(ship.transfer_legs) ? ship.transfer_legs : [];
    if (!legs.length) return;

    const zoom = Math.max(0.0001, world.scale.x);
    const markerR = (isSelected ? 3.0 : 2.2) / zoom;
    const doneColor = 0x627287;
    const activeColor = 0xd6e9ff;
    const activeHaloColor = 0xffffff;
    const pendingColor = 0x9cb4cf;
    const pulse = 0.5 + 0.5 * Math.sin(performance.now() * 0.006);
    const sun = locationsById.get("grp_sun");

    for (let i = 0; i < legs.length; i++) {
      const leg = legs[i] || {};
      const toId = String(leg.to_id || "");
      if (!toId) continue;

      let x, y;
      // For interplanetary legs, snap marker to body-center on current ring
      if (leg.is_interplanetary && sun) {
        const toSolar = getLocationSolarGroup(toId);
        if (toSolar) {
          const bodyAnchor = getTransitAnchorWorld(toSolar, Number(leg.arrival_time));
          const bodyLive = locationsById.get(toSolar);
          const bodyFuture = bodyAnchor || bodyLive;
          if (bodyFuture && bodyLive) {
            const fdx = bodyFuture.rx - sun.rx;
            const fdy = bodyFuture.ry - sun.ry;
            const futureR = Math.hypot(fdx, fdy);
            if (futureR > 1e-6) {
              const angle = Math.atan2(fdy, fdx);
              const liveR = Math.hypot(bodyLive.rx - sun.rx, bodyLive.ry - sun.ry);
              x = sun.rx + liveR * Math.cos(angle);
              y = sun.ry + liveR * Math.sin(angle);
            }
          }
        }
      }
      // Fallback: use orbit-location anchor or live position
      if (x == null || y == null) {
        const anchor = getTransitAnchorWorld(toId, Number(leg.arrival_time));
        const live = locationsById.get(toId);
        x = Number(anchor?.rx ?? live?.rx);
        y = Number(anchor?.ry ?? live?.ry);
      }
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;

      const arr = Number(leg.arrival_time || 0);
      let color = pendingColor;
      let alpha = isSelected ? 0.85 : 0.65;
      if (nowGameS >= arr) {
        color = doneColor;
        alpha = isSelected ? 0.68 : 0.45;
      }
      if (nowGameS >= Number(leg.departure_time || 0) && nowGameS <= arr) {
        color = activeColor;
        alpha = 0.95;

        const haloR = markerR * (1.7 + pulse * 0.75);
        const haloA = (isSelected ? 0.44 : 0.34) * (0.75 + pulse * 0.5);
        pathGfx.lineStyle(Math.max(0.85, 1.0 / zoom), activeHaloColor, haloA);
        pathGfx.beginFill(activeHaloColor, haloA * 0.2);
        pathGfx.drawCircle(x, y, haloR);
        pathGfx.endFill();

        pathGfx.lineStyle(Math.max(1.1, 1.3 / zoom), activeHaloColor, 0.95);
        pathGfx.beginFill(activeColor, 0.98);
        pathGfx.drawCircle(x, y, markerR * 1.12);
        pathGfx.endFill();
        continue;
      }

      pathGfx.lineStyle(Math.max(0.9, 1.1 / zoom), color, alpha);
      pathGfx.beginFill(color, alpha * 0.92);
      pathGfx.drawCircle(x, y, markerR);
      pathGfx.endFill();
    }
  }

  // ---------- Parking offsets ----------
  function dockedRowOffsetWorld(slotIndex, zoom, dockedCount) {
    const zoomSafe = Math.max(0.0001, Number(zoom) || 1);
    const count = Math.max(1, Number(dockedCount) || 1);
    const idxRaw = Math.max(0, Number(slotIndex) || 0);
    const idx = Math.min(count - 1, idxRaw);
    const centeredX = idx - ((count - 1) / 2);
    return {
      dxWorld: (centeredX * DOCKED_ROW_SLOT_SPACING_PX) / zoomSafe,
      dyWorld: DOCKED_ROW_Y_OFFSET_PX / zoomSafe,
    };
  }

  function assignDockSlots(shipsArr) {
    const byLoc = new Map();
    for (const s of shipsArr) {
      if (s.status === "docked" && s.location_id) {
        if (!byLoc.has(s.location_id)) byLoc.set(s.location_id, []);
        byLoc.get(s.location_id).push(s);
      }
    }
    const slotById = new Map();
    for (const [locId, arr] of byLoc.entries()) {
      if (arr.length === 1) {
        slotById.set(arr[0].id, 0);
        continue;
      }

      const explicit = [];
      const auto = [];
      for (const s of arr) {
        if (s.dock_slot != null && Number.isFinite(Number(s.dock_slot))) explicit.push(s);
        else auto.push(s);
      }
      const taken = new Set(explicit.map((s) => Number(s.dock_slot)));
      for (const s of explicit) slotById.set(s.id, Number(s.dock_slot));
      auto.sort((a, b) => String(a.id).localeCompare(String(b.id)));
      let next = 0;
      for (const s of auto) {
        while (taken.has(next)) next++;
        slotById.set(s.id, next);
        taken.add(next);
        next++;
      }
    }
    return slotById;
  }

  function applyDockSlots(shipsArr) {
    const slotById = assignDockSlots(shipsArr);
    for (const [shipId, gfx] of shipGfx.entries()) {
      if (!gfx.slot) gfx.slot = { index: 0 };
      gfx.slot.index = Number(slotById.get(shipId) ?? 0);
    }
  }

  // ---------- Planets ----------
  let sunGfx = null;
  let mercuryGfx = null;
  let venusGfx = null;
  let earthGfx = null;
  let moonGfx = null;
  let marsGfx = null;
  let ceresGfx = null;
  let vestaGfx = null;
  let pallasGfx = null;
  let hygieaGfx = null;
  let jupiterGfx = null;
  const mainPlanetGfx = [];
  let hoveredBodyId = null;
  let moonDetailAlpha = 1;
  let orbitDetailAlpha = 1;
  let mainOrbitDetailAlpha = 1;
  let lPointDetailAlpha = 1;
  let localNodeDetailAlpha = 1;

  function makePlanet(name, radiusWorld, innerColor, glowColor) {
    const c = new PIXI.Container();
    const glyph = new PIXI.Container();

    const half = Math.max(2.8, radiusWorld);
    if (String(name).toLowerCase() === "sun") {
      const sunBurst = makeSunGlyph(SUN_ICON_SCREEN_PX);
      sunBurst.__isSunIcon = true;
      glyph.addChild(sunBurst);
      c.__isSunIcon = true;
      c.__sunBaseSizePx = sunBurst.__baseSizePx || SUN_ICON_SCREEN_PX;
    } else if (["moon", "luna"].includes(String(name).toLowerCase())) {
      const moonGlyph = makeMoonIconGlyph(MOON_ICON_SCREEN_PX);
      moonGlyph.__isMoonIcon = true;
      glyph.addChild(moonGlyph);
      c.__isMoonIcon = true;
      c.__moonBaseSizePx = moonGlyph.__baseSizePx || MOON_ICON_SCREEN_PX;
    } else {
      const diamond = new PIXI.Graphics();
      diamond.lineStyle(1.2, 0xffffff, 0.72);
      diamond.beginFill(innerColor, 0.86);
      diamond.moveTo(0, -half);
      diamond.lineTo(half, 0);
      diamond.lineTo(0, half);
      diamond.lineTo(-half, 0);
      diamond.closePath();
      diamond.endFill();

      const diamondCore = new PIXI.Graphics();
      diamondCore.beginFill(0xffffff, 0.22);
      diamondCore.moveTo(0, -half * 0.42);
      diamondCore.lineTo(half * 0.42, 0);
      diamondCore.lineTo(0, half * 0.42);
      diamondCore.lineTo(-half * 0.42, 0);
      diamondCore.closePath();
      diamondCore.endFill();
      glyph.addChild(diamond, diamondCore);
    }

    const label = registerZoomScaledText(new PIXI.Text(name, {
      fontFamily: "system-ui, sans-serif",
      fontSize: 13,
      fill: 0xe8e8e8,
      stroke: 0x000000,
      strokeThickness: 3,
    }));
    label.__collisionPriority = 80;
    label.anchor.set(0.5, -0.9);
    c.__baseRadiusWorld = half;
    c.__glyph = glyph;
    c.__label = label;

    c.addChild(glyph, label);
    return c;
  }

  function buildPlanets() {
    function bindBodyHover(gfx, bodyId) {
      if (!gfx) return;
      gfx.__bodyId = bodyId;
      gfx.interactive = true;
      gfx.buttonMode = true;
      gfx.on("pointerover", () => {
        hoveredBodyId = bodyId;
      });
      gfx.on("pointerout", () => {
        if (hoveredBodyId === bodyId) hoveredBodyId = null;
      });
      gfx.on("pointertap", (e) => {
        if (isSecondaryPointerEvent(e)) return;
        showBodyInfo(bodyId);
      });
      gfx.on("rightclick", (e) => {
        openBodyContextMenu(bodyId, e);
      });
    }

    planetLayer.removeChildren();
    mainPlanetGfx.length = 0;
    sunGfx = makePlanet("Sun", 4.8, 0xf6c65b, 0xf6c65b);
    mercuryGfx = makePlanet("Mercury", 4.2, 0xc2b8a3, 0xd2c6ad);
    venusGfx = makePlanet("Venus", 4.4, 0xd9b77a, 0xf0cf8a);
    earthGfx = makePlanet("Earth", 4.6, 0x2b7cff, 0x2b7cff);
    moonGfx = makePlanet("Luna", 4.2, 0xbdbdbd, 0xffffff);
    marsGfx = makePlanet("Mars", 4.4, 0xcd6b4f, 0xe58b6c);
    ceresGfx = makePlanet("Ceres", 3.8, 0x8b7d6b, 0xa09080);
    vestaGfx = makePlanet("Vesta", 3.6, 0x9a8a7a, 0xb0a090);
    pallasGfx = makePlanet("Pallas", 3.5, 0x7a6e62, 0x908478);
    hygieaGfx = makePlanet("Hygiea", 3.4, 0x5c5550, 0x706860);
    jupiterGfx = makePlanet("Jupiter", 5.2, 0xc88b3a, 0xd4a860);

    bindBodyHover(sunGfx, "grp_sun");
    bindBodyHover(mercuryGfx, "grp_mercury");
    bindBodyHover(venusGfx, "grp_venus");
    bindBodyHover(earthGfx, "grp_earth");
    bindBodyHover(moonGfx, "grp_moon");
    bindBodyHover(marsGfx, "grp_mars");
    bindBodyHover(ceresGfx, "grp_ceres");
    bindBodyHover(vestaGfx, "grp_vesta");
    bindBodyHover(pallasGfx, "grp_pallas");
    bindBodyHover(hygieaGfx, "grp_hygiea");
    bindBodyHover(jupiterGfx, "grp_jupiter");

    mainPlanetGfx.push(sunGfx, mercuryGfx, venusGfx, earthGfx, marsGfx, ceresGfx, vestaGfx, pallasGfx, hygieaGfx, jupiterGfx);
    planetLayer.addChild(sunGfx, mercuryGfx, venusGfx, earthGfx, moonGfx, marsGfx, ceresGfx, vestaGfx, pallasGfx, hygieaGfx, jupiterGfx);
  }

  function updatePlanetVisualScale() {
    const zoom = Math.max(0.0001, Number(world.scale.x) || 1);
    const iconLockedToScreen = (1 / zoom) * PLANET_ICON_SCREEN_MULT;
    const labelLockedToScreen = 1 / zoom;

    for (const planet of mainPlanetGfx) {
      if (!planet) continue;
      if (planet.__isSunIcon) continue;
      const isHovered = hoveredBodyId && planet.__bodyId === hoveredBodyId;
      const hoverMul = isHovered ? BODY_HOVER_SCALE_MULT : 1;
      if (planet.__glyph) {
        planet.__glyph.scale.set(iconLockedToScreen * hoverMul);
        planet.__glyph.alpha = isHovered ? 1 : BODY_BASE_GLYPH_ALPHA;
      }
      if (planet.__label) {
        planet.__label.scale.set(labelLockedToScreen * hoverMul);
        planet.__label.alpha = isHovered ? 1 : BODY_BASE_LABEL_ALPHA;
      }
    }

    if (sunGfx?.__glyph) {
      const sunBase = Math.max(12, Number(sunGfx.__sunBaseSizePx) || SUN_ICON_SCREEN_PX);
      const sunLockedToScreen = (SUN_ICON_SCREEN_PX / sunBase) / zoom;
      const isSunHovered = hoveredBodyId === "grp_sun";
      const hoverMul = isSunHovered ? BODY_HOVER_SCALE_MULT : 1;
      sunGfx.__glyph.scale.set(sunLockedToScreen * hoverMul);
      sunGfx.__glyph.alpha = isSunHovered ? 1 : 0.95;
    }
    if (sunGfx?.__label) {
      const isSunHovered = hoveredBodyId === "grp_sun";
      const hoverMul = isSunHovered ? BODY_HOVER_SCALE_MULT : 1;
      sunGfx.__label.scale.set(labelLockedToScreen * hoverMul);
      sunGfx.__label.alpha = isSunHovered ? 1 : BODY_BASE_LABEL_ALPHA;
    }

    if (moonGfx?.__glyph) {
      const moonBase = Math.max(8, Number(moonGfx.__moonBaseSizePx) || MOON_ICON_SCREEN_PX);
      const moonLockedToScreen = (MOON_ICON_SCREEN_PX / moonBase) / zoom;
      const isMoonHovered = hoveredBodyId === "grp_moon";
      const hoverMul = isMoonHovered ? BODY_HOVER_SCALE_MULT : 1;
      moonGfx.__glyph.scale.set(moonLockedToScreen * hoverMul);
      moonGfx.__glyph.alpha = isMoonHovered ? 1 : BODY_BASE_GLYPH_ALPHA;
    }
    if (moonGfx?.__label) {
      const isMoonHovered = hoveredBodyId === "grp_moon";
      const hoverMul = isMoonHovered ? BODY_HOVER_SCALE_MULT : 1;
      moonGfx.__label.scale.set(labelLockedToScreen * hoverMul);
      moonGfx.__label.alpha = isMoonHovered ? 1 : BODY_BASE_LABEL_ALPHA;
    }
  }

  function positionPlanets() {
    const sun = locationsById.get("grp_sun");
    const mercury = locationsById.get("grp_mercury");
    const venus = locationsById.get("grp_venus");
    const earth = locationsById.get("grp_earth");
    const moon = locationsById.get("grp_moon");
    const mars = locationsById.get("grp_mars");
    const ceres = locationsById.get("grp_ceres");
    const vesta = locationsById.get("grp_vesta");
    const pallas = locationsById.get("grp_pallas");
    const hygiea = locationsById.get("grp_hygiea");
    const jupiter = locationsById.get("grp_jupiter");
    if (!sun || !mercury || !venus || !earth || !moon || !mars) return;
    if (!sunGfx || !mercuryGfx || !venusGfx || !earthGfx || !moonGfx || !marsGfx) return;

    sunGfx.position.set(sun.rx, sun.ry);
    mercuryGfx.position.set(mercury.rx, mercury.ry);
    venusGfx.position.set(venus.rx, venus.ry);
    earthGfx.position.set(earth.rx, earth.ry);
    moonGfx.position.set(moon.rx, moon.ry);
    marsGfx.position.set(mars.rx, mars.ry);
    if (ceres && ceresGfx) ceresGfx.position.set(ceres.rx, ceres.ry);
    if (vesta && vestaGfx) vestaGfx.position.set(vesta.rx, vesta.ry);
    if (pallas && pallasGfx) pallasGfx.position.set(pallas.rx, pallas.ry);
    if (hygiea && hygieaGfx) hygieaGfx.position.set(hygiea.rx, hygiea.ry);
    if (jupiter && jupiterGfx) jupiterGfx.position.set(jupiter.rx, jupiter.ry);
  }

  // ---------- Orbit hover labels ----------
  function ensureOrbitLabels() {
    for (const id of ORBIT_IDS) {
      if (orbitLabelMap.has(id)) continue;

      const loc = locationsById.get(id);
      const name = loc ? loc.name : id;

      const t = registerZoomScaledText(new PIXI.Text(name, {
        fontFamily: "system-ui, sans-serif",
        fontSize: 12,
        fill: 0xe8e8e8,
        stroke: 0x000000,
        strokeThickness: 3,
      }));
      t.__collisionPriority = 95;

      t.alpha = 0; // ✅ hidden until hover
      t.anchor.set(0, 0.5);
      labelLayer.addChild(t);
      orbitLabelMap.set(id, t);
    }
  }

  // ---------- Orbits (rings) ----------
  function computeOrbitInfo() {
    orbitInfo.clear();

    const orbitDefs = [
      { id: "LEO", center: "grp_earth", period_s: 180 },
      { id: "HEO", center: "grp_earth", period_s: 240 },
      { id: "GEO", center: "grp_earth", period_s: 300 },
      { id: "LLO", center: "grp_moon", period_s: 220 },
      { id: "HLO", center: "grp_moon", period_s: 280 },
      { id: "MERC_ORB", center: "grp_mercury", period_s: 260 },
      { id: "MERC_HEO", center: "grp_mercury", period_s: 320 },
      { id: "MERC_GEO", center: "grp_mercury", period_s: 380 },
      { id: "VEN_ORB", center: "grp_venus", period_s: 320 },
      { id: "VEN_HEO", center: "grp_venus", period_s: 380 },
      { id: "VEN_GEO", center: "grp_venus", period_s: 440 },
      { id: "LMO", center: "grp_mars", period_s: 360 },
      { id: "HMO", center: "grp_mars", period_s: 420 },
      { id: "MGO", center: "grp_mars", period_s: 480 },
      { id: "CERES_LO", center: "grp_ceres", period_s: 400 },
      { id: "CERES_HO", center: "grp_ceres", period_s: 460 },
      { id: "VESTA_LO", center: "grp_vesta", period_s: 380 },
      { id: "VESTA_HO", center: "grp_vesta", period_s: 440 },
      { id: "PALLAS_LO", center: "grp_pallas", period_s: 390 },
      { id: "PALLAS_HO", center: "grp_pallas", period_s: 450 },
      { id: "HYGIEA_LO", center: "grp_hygiea", period_s: 410 },
      { id: "HYGIEA_HO", center: "grp_hygiea", period_s: 470 },
      { id: "PHOBOS_LO", center: "PHOBOS", period_s: 120 },
      { id: "DEIMOS_LO", center: "DEIMOS", period_s: 140 },
      { id: "ZOOZVE_LO", center: "ZOOZVE", period_s: 100 },
      { id: "JUP_LO", center: "grp_jupiter", period_s: 500 },
      { id: "JUP_HO", center: "grp_jupiter", period_s: 600 },
      { id: "IO_LO", center: "IO", period_s: 160 },
      { id: "IO_HO", center: "IO", period_s: 200 },
      { id: "EUROPA_LO", center: "EUROPA", period_s: 180 },
      { id: "EUROPA_HO", center: "EUROPA", period_s: 220 },
      { id: "GANYMEDE_LO", center: "GANYMEDE", period_s: 200 },
      { id: "GANYMEDE_HO", center: "GANYMEDE", period_s: 240 },
      { id: "CALLISTO_LO", center: "CALLISTO", period_s: 220 },
      { id: "CALLISTO_HO", center: "CALLISTO", period_s: 260 },
    ];

    for (const od of orbitDefs) {
      const loc = locationsById.get(od.id);
      const ctr = locationsById.get(od.center);
      if (!loc || !ctr) continue;

      const dx = loc.rx - ctr.rx;
      const dy = loc.ry - ctr.ry;
      const r = Math.hypot(dx, dy);
      const baseAngle = Math.atan2(dy, dx);

      orbitInfo.set(od.id, {
        cx: ctr.rx,
        cy: ctr.ry,
        radius: r,
        baseAngle,
        period_s: od.period_s,
      });
    }

    ensureOrbitLabels();
  }

  // Orbit IDs that should remain visible at solar-system zoom levels
  const SOLAR_SCALE_ORBIT_IDS = new Set(["JUP_HO"]);

  function renderOrbitRings() {
    // Detect zoom changes and mark dirty
    const currentZoom = world.scale.x;
    if (currentZoom !== lastOrbitZoom) {
      orbitRingsDirty = true;
      lastOrbitZoom = currentZoom;
    }
    if (!orbitRingsDirty) return;
    orbitRingsDirty = false;

    orbitLayer.clear();
    if (orbitDetailAlpha <= 0.001 && mainOrbitDetailAlpha <= 0.001) return;

    const zoom = Math.max(0.0001, Number(world.scale.x) || 1);
    const deepOrbitShrink = deepZoomShrink(zoom, DEEP_ZOOM_SHRINK_START, 0.45, ORBIT_RING_MIN_PX / ORBIT_RING_BASE_PX);
    const ringScreenPx = Math.max(ORBIT_RING_MIN_PX, ORBIT_RING_BASE_PX * deepOrbitShrink);
    const baseLW = ringScreenPx / zoom;

    const sun = locationsById.get("grp_sun");
    if (sun) {
      const solarIds = ["grp_mercury", "grp_venus", "grp_earth", "grp_mars", "grp_jupiter"];
      orbitLayer.lineStyle((ringScreenPx * SOLAR_RING_MULT) / zoom, 0xf3d9a6, 0.18 * mainOrbitDetailAlpha);
      for (const pid of solarIds) {
        const body = locationsById.get(pid);
        if (!body) continue;
        const rr = Math.hypot(body.rx - sun.rx, body.ry - sun.ry);
        if (rr > 1e-6) orbitLayer.drawCircle(sun.rx, sun.ry, rr);
      }

      // Zoozve quasi-satellite orbit ring around the Sun
      const zoozveBody = locationsById.get("ZOOZVE") || locationsById.get("grp_zoozve");
      if (zoozveBody) {
        const rrZ = Math.hypot(zoozveBody.rx - sun.rx, zoozveBody.ry - sun.ry);
        if (rrZ > 1e-6) {
          orbitLayer.lineStyle((ringScreenPx * SOLAR_RING_MULT) / zoom, 0xa0a0a0, 0.10 * mainOrbitDetailAlpha);
          orbitLayer.drawCircle(sun.rx, sun.ry, rrZ);
        }
      }

      // Asteroid belt dust cloud — wide overlapping bands for a smooth diffuse look
      const beltBodies = ["grp_vesta", "grp_ceres", "grp_pallas", "grp_hygiea"];
      const beltRadii = beltBodies.map(id => {
        const b = locationsById.get(id);
        return b ? Math.hypot(b.rx - sun.rx, b.ry - sun.ry) : 0;
      }).filter(r => r > 1e-6);
      if (beltRadii.length > 0) {
        const innerR = Math.min(...beltRadii) * 0.85;
        const outerR = Math.max(...beltRadii) * 1.15;
        const bandSpan = outerR - innerR;
        const bandCount = 14;
        const bandWidth = bandSpan / bandCount * 2.8; // overlap ≈ 2.8×
        const beltAlpha = 0.04 * mainOrbitDetailAlpha;
        for (let i = 0; i < bandCount; i++) {
          const t = i / (bandCount - 1);
          const r = innerR + t * bandSpan;
          const edgeFade = 1 - Math.pow(2 * t - 1, 2);
          const a = beltAlpha * (0.25 + 0.75 * edgeFade);
          orbitLayer.lineStyle(bandWidth, 0x8b4513, a);
          orbitLayer.drawCircle(sun.rx, sun.ry, r);
        }
        // scatter particles — deterministic positions so they don't flicker
        const scatterCount = 32;
        for (let i = 0; i < scatterCount; i++) {
          const angle = (i / scatterCount) * Math.PI * 2 + 0.37;
          const rFrac = ((i * 7 + 3) % scatterCount) / scatterCount;
          const rScatter = innerR + rFrac * bandSpan;
          const px = sun.rx + Math.cos(angle) * rScatter;
          const py = sun.ry + Math.sin(angle) * rScatter;
          const dotR = (1.8 + ((i * 13) % 10) * 0.25) / zoom;
          orbitLayer.lineStyle(0);
          orbitLayer.beginFill(0xa0522d, 0.12 * mainOrbitDetailAlpha);
          orbitLayer.drawCircle(px, py, dotR);
          orbitLayer.endFill();
        }
      }
    }

    function drawRing(id) {
      const oi = orbitInfo.get(id);
      if (!oi) return;

      const isSolarScale = SOLAR_SCALE_ORBIT_IDS.has(id);
      const alpha = isSolarScale ? mainOrbitDetailAlpha : orbitDetailAlpha;
      const isHover = hoveredOrbitId === id;
      const lw = isHover ? baseLW * ORBIT_RING_HOVER_MULT : baseLW;
      const a = (isHover ? 0.26 : 0.10) * alpha;

      orbitLayer.lineStyle(lw, 0xffffff, a);
      orbitLayer.drawCircle(oi.cx, oi.cy, oi.radius);
    }

    [
      "LEO", "HEO", "GEO",
      "LLO", "HLO",
      "MERC_ORB", "MERC_HEO", "MERC_GEO",
      "VEN_ORB", "VEN_HEO", "VEN_GEO",
      "LMO", "HMO", "MGO",
      "CERES_LO", "CERES_HO",
      "VESTA_LO", "VESTA_HO",
      "PALLAS_LO", "PALLAS_HO",
      "HYGIEA_LO", "HYGIEA_HO",
      "PHOBOS_LO", "DEIMOS_LO", "ZOOZVE_LO",
      "JUP_LO", "JUP_HO",
      "IO_LO", "IO_HO",
      "EUROPA_LO", "EUROPA_HO",
      "GANYMEDE_LO", "GANYMEDE_HO",
      "CALLISTO_LO", "CALLISTO_HO",
    ].forEach(drawRing);

    // Phobos and Deimos orbital path rings around Mars
    const marsGrp = locationsById.get("grp_mars");
    if (marsGrp) {
      const moonletOrbitIds = ["PHOBOS", "DEIMOS"];
      orbitLayer.lineStyle(baseLW, 0xaaaaaa, 0.12 * orbitDetailAlpha);
      for (const mid of moonletOrbitIds) {
        const marker = locationsById.get(mid);
        if (!marker) continue;
        const rr = Math.hypot(marker.rx - marsGrp.rx, marker.ry - marsGrp.ry);
        if (rr > 1e-6) orbitLayer.drawCircle(marsGrp.rx, marsGrp.ry, rr);
      }
    }

    // Jupiter's Galilean moon orbital path rings
    const jupGrp = locationsById.get("grp_jupiter");
    if (jupGrp) {
      const jupMoonIds = ["IO", "EUROPA", "GANYMEDE", "CALLISTO"];
      orbitLayer.lineStyle(baseLW, 0xaaaaaa, 0.12 * orbitDetailAlpha);
      for (const mid of jupMoonIds) {
        const marker = locationsById.get(mid);
        if (!marker) continue;
        const rr = Math.hypot(marker.rx - jupGrp.rx, marker.ry - jupGrp.ry);
        if (rr > 1e-6) orbitLayer.drawCircle(jupGrp.rx, jupGrp.ry, rr);
      }
    }
  }

  // ---------- Ring hover detection (Step 4) ----------
  // NOTE: doesn't interfere with drag-pan (guard at top)
  app.view.addEventListener("pointermove", (e) => {
    if (dragging) return;

    const rect = app.view.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const p = world.toLocal(new PIXI.Point(mx, my));
    const tol = 10 / world.scale.x; // hover tolerance band

    let best = null;
    let bestErr = Infinity;

    for (const [id, oi] of orbitInfo.entries()) {
      const d = Math.hypot(p.x - oi.cx, p.y - oi.cy);
      const err = Math.abs(d - oi.radius);
      if (err < tol && err < bestErr) {
        best = id;
        bestErr = err;
      }
    }

    if (hoveredOrbitId !== best) {
      hoveredOrbitId = best;
      markOrbitsDirty();
    }

    // show only hovered orbit label near cursor
    for (const [id, t] of orbitLabelMap.entries()) {
      if (id === hoveredOrbitId) {
        const a = SOLAR_SCALE_ORBIT_IDS.has(id) ? mainOrbitDetailAlpha : orbitDetailAlpha;
        t.alpha = a;
        t.position.set(p.x + 14 / world.scale.x, p.y);
      } else {
        t.alpha = 0;
      }
    }
  });

  app.view.addEventListener("pointerleave", () => {
    if (hoveredOrbitId !== null) markOrbitsDirty();
    hoveredOrbitId = null;
    hoveredBodyId = null;
    for (const t of orbitLabelMap.values()) t.alpha = 0;
  });

  // ---------- Locations ----------
  function ensureLocationsGfx() {
    function makeMoonMarker(moonId = "") {
      const id = String(moonId || "").toUpperCase();
      const isJovianMoon = id === "IO" || id === "EUROPA" || id === "GANYMEDE" || id === "CALLISTO";
      const targetScreenPx = isJovianMoon ? JOVIAN_MOON_ICON_SCREEN_PX : MOON_ICON_SCREEN_PX;
      const marker = isJovianMoon
        ? makeJovianMoonIconGlyph(id, targetScreenPx)
        : makeMoonIconGlyph(targetScreenPx);
      marker.__isMoonIcon = true;
      marker.__moonBaseSizePx = marker.__baseSizePx || MOON_ICON_SCREEN_PX;
      marker.__targetScreenPx = targetScreenPx;
      return marker;
    }

    function makeAsteroidMarker() {
      const marker = makeAsteroidIconGlyph(ASTEROID_ICON_SCREEN_PX);
      marker.__isAsteroidIcon = true;
      marker.__asteroidBaseSizePx = marker.__baseSizePx || ASTEROID_ICON_SCREEN_PX;
      return marker;
    }

    for (const loc of leaves) {
      // ✅ Orbits are rings only; don't draw dots/labels for them
      if (ORBIT_IDS.has(loc.id)) continue;
      if (loc.id === "SUN") continue;
      // Surface sites live in the Sites page, not on the orbital map
      if (loc.is_surface_site) continue;

      if (locGfx.has(loc.id)) continue;

      const inEarthLocal = hasAncestor(loc.id, "grp_earth_orbits", locationParentById);
      const inMoonLocal = hasAncestor(loc.id, "grp_moon_orbits", locationParentById);
      const isLPoint = LPOINT_IDS.has(loc.id);
      const isMoonlet = loc.id === "PHOBOS" || loc.id === "DEIMOS" || loc.id === "IO" || loc.id === "EUROPA" || loc.id === "GANYMEDE" || loc.id === "CALLISTO";
      const isAsteroid = isAsteroidLocation(loc);

      let kind = "deep-node";
      if (isLPoint) kind = "lagrange";
      else if (inEarthLocal || inMoonLocal) kind = "orbit-node";
      else if (isMoonlet) kind = "moonlet";
      else if (isAsteroid) kind = "asteroid";

      let dot;
      if (LPOINT_IDS.has(loc.id)) {
        dot = new PIXI.Container();
        // Soft outer glow
        const glow = new PIXI.Graphics();
        glow.beginFill(0x88bbff, 0.06);
        glow.drawCircle(0, 0, 6);
        glow.endFill();
        glow.beginFill(0x88bbff, 0.04);
        glow.drawCircle(0, 0, 9);
        glow.endFill();
        dot.addChild(glow);
        // Diamond shape
        const diamond = new PIXI.Graphics();
        diamond.lineStyle(0.8, 0xaaccff, 0.7);
        diamond.beginFill(0x6699cc, 0.25);
        diamond.moveTo(0, -2.5);
        diamond.lineTo(2.5, 0);
        diamond.lineTo(0, 2.5);
        diamond.lineTo(-2.5, 0);
        diamond.closePath();
        diamond.endFill();
        dot.addChild(diamond);
        // Inner bright dot
        const core = new PIXI.Graphics();
        core.beginFill(0xddeeff, 0.6);
        core.drawCircle(0, 0, 0.75);
        core.endFill();
        dot.addChild(core);
        // Crosshair lines
        const cross = new PIXI.Graphics();
        cross.lineStyle(0.5, 0xaaccff, 0.3);
        cross.moveTo(0, -4.5); cross.lineTo(0, -3);
        cross.moveTo(0, 3);    cross.lineTo(0, 4.5);
        cross.moveTo(-4.5, 0); cross.lineTo(-3, 0);
        cross.moveTo(3, 0);    cross.lineTo(4.5, 0);
        dot.addChild(cross);
      } else if (loc.id === "PHOBOS") {
        dot = makeMoonMarker("PHOBOS");
      } else if (loc.id === "DEIMOS") {
        dot = makeMoonMarker("DEIMOS");
      } else if (loc.id === "IO" || loc.id === "EUROPA" || loc.id === "GANYMEDE" || loc.id === "CALLISTO") {
        dot = makeMoonMarker(loc.id);
      } else if (isAsteroid) {
        dot = makeAsteroidMarker();
      } else {
        // Invisible marker — no visual circle, but still interactive
        dot = new PIXI.Graphics();
        dot.beginFill(0xffffff, 0.0);
        dot.drawCircle(0, 0, 9);
        dot.endFill();
      }

      const label = registerZoomScaledText(new PIXI.Text(loc.name, {
        fontFamily: "system-ui, sans-serif",
        fontSize: 12,
        fill: 0xe8e8e8,
        stroke: 0x000000,
        strokeThickness: 3,
      }));
      label.__collisionPriority = 70;
      label.anchor.set(0.5, -0.8);
      label.alpha = 0;

      const entry = { dot, label, kind, hovered: false };

      dot.interactive = true;
      dot.buttonMode = true;
      dot.on("pointerover", () => {
        entry.hovered = true;
      });
      dot.on("pointerout", () => {
        entry.hovered = false;
      });
      dot.on("pointertap", (e) => {
        if (isSecondaryPointerEvent(e)) return;
        const clickCount = Number(e?.data?.originalEvent?.detail || 1);
        if (clickCount >= 2) ensureInfoPanelVisible();
        showLocationInfo(loc);
      });
      dot.on("rightclick", (e) => {
        openLocationContextMenu(loc, e);
      });

      locLayer.addChild(dot);
      labelLayer.addChild(label);
      locGfx.set(loc.id, entry);
    }
  }

  function applyZoomDetailVisibility() {
    const z = world.scale.x;
    const iconGrowOnZoomOut = clamp(1 / Math.max(0.0001, z), 1, ICON_ZOOM_COMP_MAX);
    const labelGrowOnZoomOut = clamp(1 / Math.max(0.0001, z), 1, LABEL_ZOOM_COMP_MAX);
    const deepIconShrink = deepZoomShrink(z, DEEP_ZOOM_SHRINK_START, DEEP_ZOOM_ICON_SHRINK_RATE, DEEP_ZOOM_ICON_SHRINK_MIN);
    const deepLabelShrink = deepZoomShrink(z, DEEP_ZOOM_SHRINK_START, DEEP_ZOOM_LABEL_SHRINK_RATE, DEEP_ZOOM_LABEL_SHRINK_MIN);

    // Moons, local orbit rings, and orbit-node dots fully visible at scale 0.30,
    // fading in from 0.10.
    moonDetailAlpha = zoomFade(z, 0.10, 0.30);
    orbitDetailAlpha = zoomFade(z, 0.10, 0.30);
    mainOrbitDetailAlpha = 1 - zoomFade(z, 1.2, 3.2);
    lPointDetailAlpha = zoomFade(z, 1.2, 2.4);
    localNodeDetailAlpha = zoomFade(z, 0.10, 0.30);

    if (moonGfx) moonGfx.alpha = moonDetailAlpha;
    updatePlanetVisualScale();

    for (const [locId, entry] of locGfx.entries()) {
      const detailAlpha = (entry.kind === "lagrange" && ALWAYS_VISIBLE_LPOINTS.has(locId))
        ? 1
        : (entry.kind === "lagrange"
          ? lPointDetailAlpha
          : (entry.kind === "orbit-node"
            ? localNodeDetailAlpha
            : (entry.kind === "moonlet" ? moonDetailAlpha : 1)));
      if (entry.kind === "moonlet" && entry.dot?.__isMoonIcon) {
        const moonBase = Math.max(8, Number(entry.dot.__moonBaseSizePx) || MOON_ICON_SCREEN_PX);
        const targetMoonPx = Math.max(7, Number(entry.dot.__targetScreenPx) || MOON_ICON_SCREEN_PX);
        const moonletHoverMul = entry.hovered ? MOONLET_HOVER_SCALE_MULT : 1;
        entry.dot.scale.set(((targetMoonPx / moonBase) / Math.max(0.0001, z)) * moonletHoverMul);
      } else if (entry.kind === "asteroid" && entry.dot?.__isAsteroidIcon) {
        const asteroidBase = Math.max(8, Number(entry.dot.__asteroidBaseSizePx) || ASTEROID_ICON_SCREEN_PX);
        const asteroidHoverMul = entry.hovered ? ASTEROID_HOVER_SCALE_MULT : 1;
        entry.dot.scale.set(((ASTEROID_ICON_SCREEN_PX / asteroidBase) / Math.max(0.0001, z)) * asteroidHoverMul);
      } else if (entry.kind === "lagrange" && ALWAYS_VISIBLE_LPOINTS.has(locId)) {
        // Solar-scale L-points: lock to screen size like planets
        entry.dot.scale.set((1 / Math.max(0.0001, z)) * PLANET_ICON_SCREEN_MULT);
      } else {
        entry.dot.scale.set(iconGrowOnZoomOut * deepIconShrink);
      }
      entry.label.scale.set(
        (entry.kind === "lagrange" && ALWAYS_VISIBLE_LPOINTS.has(locId))
          ? (1 / Math.max(0.0001, z))
          : labelGrowOnZoomOut * deepLabelShrink
      );
      // Ensure location dots have a minimum screen-pixel hit area regardless of zoom
      const dotScale = Math.max(0.0001, entry.dot.scale.x);
      const locHitLocal = Math.max(9, (MIN_LOC_HIT_SCREEN_PX / Math.max(0.0001, z)) / dotScale);
      if (entry.dot.hitArea instanceof PIXI.Circle) {
        entry.dot.hitArea.radius = locHitLocal;
      } else {
        entry.dot.hitArea = new PIXI.Circle(0, 0, locHitLocal);
      }
      entry.dot.alpha = detailAlpha * (entry.hovered ? 1 : 0.9);
      entry.dot.visible = detailAlpha > 0.001;
      // Moonlets, asteroids, and solar-scale L-points always show their name label when visible
      const alwaysLabel = entry.kind === "moonlet" || entry.kind === "asteroid"
        || (entry.kind === "lagrange" && ALWAYS_VISIBLE_LPOINTS.has(locId));
      if (alwaysLabel) {
        entry.label.alpha = entry.hovered ? detailAlpha : detailAlpha * CELESTIAL_BASE_LABEL_ALPHA;
        entry.label.visible = detailAlpha > 0.001;
      } else {
        entry.label.alpha = entry.hovered ? detailAlpha : 0;
        entry.label.visible = entry.hovered && detailAlpha > 0.001;
      }
    }

    for (const [id, t] of orbitLabelMap.entries()) {
      const a = SOLAR_SCALE_ORBIT_IDS.has(id) ? mainOrbitDetailAlpha : orbitDetailAlpha;
      t.alpha = (id === hoveredOrbitId && a > 0.001) ? a : 0;
    }

    applyUniversalTextScaleCap();
  }

  function updateLocationPositions() {
    for (const loc of leaves) {
      const g = locGfx.get(loc.id);
      if (!g) continue; // orbit nodes won't exist here
      g.dot.position.set(loc.rx, loc.ry);
      g.label.position.set(loc.rx, loc.ry);
    }
  }

  function fitToLocations() {
    if (leaves.length === 0) return;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const l of leaves) {
      minX = Math.min(minX, l.rx);
      minY = Math.min(minY, l.ry);
      maxX = Math.max(maxX, l.rx);
      maxY = Math.max(maxY, l.ry);
    }
    const w = Math.max(1e-6, maxX - minX);
    const h = Math.max(1e-6, maxY - minY);

    const pad = 140;
    const vw = Math.max(200, stage.clientWidth - pad);
    const vh = Math.max(200, stage.clientHeight - pad);

    const sx = vw / w;
    const sy = vh / h;
    const fitScale = Math.min(sx, sy);
    const s = Math.max(CAMERA_MIN_SCALE, Math.min(MAX_INITIAL_SCALE, fitScale * FIT_VIEW_SCALE * MAP_SCREEN_SPREAD_MULT));

    world.scale.set(s);
    world.x = stage.clientWidth / 2 - ((minX + maxX) / 2) * s;
    world.y = stage.clientHeight / 2 - ((minY + maxY) / 2) * s;
    markOrbitsDirty();
    refreshZoomScaledTextResolution();
    applyZoomDetailVisibility();
  }

  // ---------- Ships ----------
  const DEFAULT_SHIP_ICON_URL = "/static/img/mining-barge.png";
  const SHIP_PNG_FORWARD_OFFSET_RAD = Math.PI / 2;
  const SHIP_GLOW_ALPHA = 0.12;
  const SHIP_GLOW_RADIUS_MULT = 0.9;
  const SHIP_ICON_LOCK_ZOOM_START = 2.4;
  const SHIP_ICON_LOCK_SCREEN_PX = 16;

  function slugifyIconName(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }

  function resolveShipIconUrl(ship) {
    const shape = String(ship?.shape || "").trim();
    if (!shape || shape === "triangle") return DEFAULT_SHIP_ICON_URL;

    if (/^(https?:|data:|blob:|\/)/i.test(shape) && /\.(png|jpg|jpeg|webp|gif|svg)(\?|#|$)/i.test(shape)) {
      return shape;
    }

    if (/\.(png|jpg|jpeg|webp|gif|svg)(\?|#|$)/i.test(shape)) {
      return `/static/img/${shape.replace(/^\/+/, "")}`;
    }

    const slug = slugifyIconName(shape);
    if (!slug) return DEFAULT_SHIP_ICON_URL;
    return `/static/img/${slug}.png`;
  }

  function buildFallbackShipGlyph(size, colorInt, alpha = 0.95) {
    const gfx = new PIXI.Graphics();
    const len = Math.max(8, size * 1.95);
    const half = len * 0.34;
    const stroke = mixColor(colorInt, 0xffffff, 0.42);

    gfx.lineStyle(1.2, stroke, Math.min(1, alpha + 0.08));
    gfx.beginFill(colorInt, Math.max(0.12, alpha * 0.22));
    gfx.moveTo(len * 0.88, 0);
    gfx.lineTo(-len * 0.62, -half);
    gfx.lineTo(-len * 0.62, half);
    gfx.lineTo(len * 0.88, 0);
    gfx.endFill();
    gfx.alpha = alpha;
    return gfx;
  }

  function buildShipUnderGlow(size, colorInt, alpha = 0.95) {
    const glow = new PIXI.Graphics();
    const r = Math.max(7, size * SHIP_GLOW_RADIUS_MULT);
    glow.__glowRadiusPx = r;
    const core = mixColor(colorInt, 0xffffff, 0.78);
    glow.beginFill(core, SHIP_GLOW_ALPHA * alpha);
    glow.drawCircle(0, 0, r);
    glow.endFill();
    glow.beginFill(0xffffff, SHIP_GLOW_ALPHA * 0.36 * alpha);
    glow.drawCircle(0, 0, r * 0.46);
    glow.endFill();
    return glow;
  }

  function buildShipIconSprite(ship, size, colorInt, alpha = 0.95) {
    const iconContainer = new PIXI.Container();
    const iconUrl = resolveShipIconUrl(ship);
    const targetH = Math.max(8, size * 1.2);
    iconContainer.__iconBasePx = targetH;
    iconContainer.__hitRadiusPx = Math.max(3.2, targetH * 0.34);

    const glow = buildShipUnderGlow(size, colorInt, alpha);
    iconContainer.__glowRadiusPx = Math.max(3.2, Number(glow.__glowRadiusPx) || 0);
    iconContainer.addChild(glow);

    const fallback = buildFallbackShipGlyph(size, colorInt, alpha);
    iconContainer.addChild(fallback);

    const texture = PIXI.Texture.from(iconUrl);
    const sprite = new PIXI.Sprite(texture);
    sprite.anchor.set(0.5);
    sprite.rotation = SHIP_PNG_FORWARD_OFFSET_RAD;
    sprite.alpha = alpha;
    sprite.visible = false;
    iconContainer.addChild(sprite);

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

    return iconContainer;
  }

  function getOrCreateShipClusterLabel(locationId) {
    let label = shipClusterLabels.get(locationId);
    if (label) return label;

    label = registerZoomScaledText(new PIXI.Text("+0", {
      fontFamily: "system-ui, sans-serif",
      fontSize: 13,
      fill: 0xfff3f6,
      stroke: 0x000000,
      strokeThickness: 3,
    }));
    label.__collisionPriority = 90;
    label.anchor.set(0.5, 1);
    label.alpha = 0;
    label.visible = false;
    shipClusterLayer.addChild(label);
    shipClusterLabels.set(locationId, label);
    return label;
  }

  function updateShipClusterLabels(hiddenByLocation, clusterMode) {
    if (!clusterMode) {
      for (const label of shipClusterLabels.values()) {
        label.visible = false;
        label.alpha = 0;
      }
      return;
    }

    const zoom = Math.max(0.0001, world.scale.x);
    const activeLocationIds = new Set();

    for (const [locationId, hiddenCount] of hiddenByLocation.entries()) {
      if (!(hiddenCount > 0)) continue;
      const loc = locationsById.get(locationId);
      if (!loc) continue;

      activeLocationIds.add(locationId);
      const label = getOrCreateShipClusterLabel(locationId);
      label.text = `+${hiddenCount}`;
      label.visible = true;
      label.alpha = 0.92;
      label.position.set(loc.rx, loc.ry - (24 / zoom));
      label.scale.set(clamp(1 / zoom, 1, 6));
    }

    for (const [locationId, label] of shipClusterLabels.entries()) {
      if (activeLocationIds.has(locationId)) continue;
      label.visible = false;
      label.alpha = 0;
    }
  }

  function getOrCreateDockedShipChip(locationId) {
    let chip = dockedChipGfx.get(locationId);
    if (chip) return chip;

    const container = new PIXI.Container();
    const bg = new PIXI.Graphics();
    const text = registerZoomScaledText(new PIXI.Text("0", {
      fontFamily: "Orbitron, Rajdhani, Roboto Condensed, Arial Narrow, sans-serif",
      fontSize: 26,
      fontWeight: 700,
      letterSpacing: 0.5,
      fill: 0xd8eaff,
      stroke: 0x000000,
      strokeThickness: 2,
    }));
    text.__collisionPriority = 85;

    text.anchor.set(0.5, 0.5);

    container.visible = false;
    container.alpha = 0;
    container.addChild(bg, text);
    shipClusterLayer.addChild(container);

    chip = {
      container,
      bg,
      text,
      hitRadiusWorld: 0,
      targetX: 0,
      targetY: 0,
      currentX: 0,
      currentY: 0,
      targetScale: 0.1,
      currentScale: 0.1,
      targetAlpha: 0,
      currentAlpha: 0,
      initialized: false,
      active: false,
    };
    dockedChipGfx.set(locationId, chip);
    return chip;
  }

  function updateDockedShipChips(dockedShipsByLocation) {
    const zoom = Math.max(0.0001, Number(world.scale.x) || 1);

    for (const chip of dockedChipGfx.values()) {
      if (chip) chip.active = false;
    }

    for (const [locationId, dockedShips] of dockedShipsByLocation.entries()) {
      const count = Array.isArray(dockedShips) ? dockedShips.length : 0;
      if (count <= 0) continue;
      const loc = locationsById.get(locationId);
      if (!loc || !Number.isFinite(Number(loc.rx)) || !Number.isFinite(Number(loc.ry))) continue;

      const chip = getOrCreateDockedShipChip(locationId);
      chip.active = true;
      const labelText = `${count}`;
      if (chip.text.text !== labelText) chip.text.text = labelText;

      const innerR = Math.max(chip.text.width, chip.text.height) * 0.5;
      const circleR = innerR + 1.5;
      chip.bg.clear();
      chip.bg.beginFill(0x0d1520, 0.88);
      chip.bg.lineStyle(1, 0x6b92c8, 0.78);
      chip.bg.drawCircle(0, 0, circleR);
      chip.bg.endFill();

      const baseDiameterPx = circleR * 2;
      const zoomNearT = smoothstep(1.2, 3.8, zoom);
      const targetDiameterPx =
        DOCKED_CHIP_MAX_SCREEN_PX
        - (DOCKED_CHIP_MAX_SCREEN_PX - DOCKED_CHIP_MIN_SCREEN_PX) * zoomNearT;
      const boundedDiameterPx = Math.min(DOCKED_CHIP_HARD_MAX_SCREEN_PX, targetDiameterPx);
      // Divide by zoom so badge stays a fixed screen-pixel size regardless of world zoom
      const targetScale = clamp(
        boundedDiameterPx / (Math.max(1, baseDiameterPx) * zoom),
        DOCKED_CHIP_SCALE_MIN,
        DOCKED_CHIP_SCALE_MAX
      );

      chip.targetX = Number(loc.rx);
      chip.targetY = Number(loc.ry) + ((DOCKED_ROW_Y_OFFSET_PX - 14) / zoom);
      chip.targetScale = targetScale;
      chip.targetAlpha = 0.96;

      // Runtime guard to keep badges from outgrowing nearby map glyphs
      const finalDiameterPx = baseDiameterPx * targetScale * zoom;
      if (finalDiameterPx > DOCKED_CHIP_HARD_MAX_SCREEN_PX + 0.01) {
        console.warn("Docked chip size exceeded cap", {
          locationId,
          finalDiameterPx,
          capPx: DOCKED_CHIP_HARD_MAX_SCREEN_PX,
          zoom,
        });
      }
    }

    for (const [locationId, chip] of dockedChipGfx.entries()) {
      if (!chip || !chip.container) continue;

      if (!chip.active) {
        chip.targetAlpha = 0;
      }

      if (!chip.initialized) {
        chip.currentX = Number(chip.targetX) || 0;
        chip.currentY = Number(chip.targetY) || 0;
        chip.currentScale = Number(chip.targetScale) || 0.1;
        chip.currentAlpha = Number(chip.targetAlpha) || 0;
        chip.initialized = true;
      } else {
        chip.currentX += (chip.targetX - chip.currentX) * DOCKED_CHIP_LERP;
        chip.currentY += (chip.targetY - chip.currentY) * DOCKED_CHIP_LERP;
        chip.currentScale += (chip.targetScale - chip.currentScale) * DOCKED_CHIP_LERP;
        chip.currentAlpha += (chip.targetAlpha - chip.currentAlpha) * DOCKED_CHIP_LERP;
      }

      chip.container.position.set(chip.currentX, chip.currentY);
      chip.container.scale.set(chip.currentScale);
      chip.container.alpha = chip.currentAlpha;
      chip.container.visible = chip.currentAlpha > 0.02;

      const textW = Number(chip.text?.width) || 10;
      const textH = Number(chip.text?.height) || 10;
      const chipRadiusPx = Math.max(textW, textH) * 0.6 * chip.currentScale;
      chip.hitRadiusWorld = Math.max(3 / zoom, chipRadiusPx);

      // Reset inactive targets to avoid stale interpolation on reactivation
      if (!chip.active && chip.currentAlpha <= 0.02) {
        chip.targetScale = chip.currentScale;
        chip.targetX = chip.currentX;
        chip.targetY = chip.currentY;
      }
    }
  }

  function buildShipSprite(ship) {
    const c = new PIXI.Container();
    c.interactive = true;
    c.buttonMode = true;
    c.scale.set(SHIP_WORLD_SCALE_COMPENSATION);

    const size = (Number(ship.size_px) || 12) * SHIP_SIZE_SCALE * SHIP_VISUAL_SCALE;
    const colorInt = parseHexColor(ship.color, 0xff2c4d);
    const hitRadius = Math.max(14, size * SHIP_CLICK_RADIUS_MULT);
    c.hitArea = new PIXI.Circle(0, 0, hitRadius);
    const shipIcon = buildShipIconSprite(ship, size, colorInt, 0.95);

    const headingLine = null;

    const selectionBox = new PIXI.Graphics();
    drawSelectionBracket(selectionBox, size, SHIP_SELECTION_STROKE_PX / Math.max(0.0001, Number(world.scale.x) || 1));
    selectionBox.alpha = 0;

    const label = registerZoomScaledText(new PIXI.Text(ship.name, {
      fontFamily: "Orbitron, Rajdhani, Roboto Condensed, Arial Narrow, sans-serif",
      fontSize: 13,
      fontWeight: 700,
      letterSpacing: 1.2,
      fill: 0xe8e8e8,
      stroke: 0x000000,
      strokeThickness: 2,
    }));
    label.__collisionPriority = 110;
    label.__maxScreenScale = SHIP_LABEL_SCREEN_CAP;
    const idTag = registerZoomScaledText(new PIXI.Text(String(ship.id || "").slice(0, 3), {
      fontFamily: "Orbitron, Rajdhani, Roboto Condensed, Arial Narrow, sans-serif",
      fontSize: 11,
      fontWeight: 700,
      letterSpacing: 1.1,
      fill: colorInt,
      stroke: 0x000000,
      strokeThickness: 2,
    }));
    idTag.__collisionPriority = 108;
    idTag.__maxScreenScale = SHIP_IDTAG_SCREEN_CAP;
    const idOffsetY = size + 6;
    const labelOffsetY = idOffsetY + 11;
    idTag.anchor.set(0.5, 0);
    idTag.position.set(0, idOffsetY);
    idTag.alpha = 0.9;
    label.anchor.set(0.5, 0);
    label.position.set(0, labelOffsetY);
    label.alpha = 0;

    c.on("pointerover", () => {
      hoveredShipId = ship.id;
    });
    c.on("pointerout", () => {
      if (hoveredShipId === ship.id) hoveredShipId = null;
    });

    c.addChild(selectionBox, shipIcon, idTag, label);

    c.on("pointertap", (e) => {
      if (isSecondaryPointerEvent(e)) return;
      const clickCount = Number(e?.data?.originalEvent?.detail || 1);
      if (clickCount >= 2) ensureInfoPanelVisible();
      hideContextMenu();
      selectedShipId = ship.id;
      showShipPanel();
      e?.stopPropagation?.();
    });
    c.on("rightclick", (e) => {
      openShipContextMenu(ship, e);
    });

    return { container: c, shipIcon, headingLine, selectionBox, label, idTag, idOffsetY, labelOffsetY, size, colorInt, hitRadius };
  }

  function upsertShips(shipsArr) {
    const nextIds = new Set();

    for (const s of shipsArr) {
      nextIds.add(s.id);
      const existing = shipGfx.get(s.id);
      if (existing) {
        existing.ship = s;
        if (existing.label && existing.label.text !== s.name) existing.label.text = s.name;
        const shortId = String(s.id || "").slice(0, 3);
        if (existing.idTag && existing.idTag.text !== shortId) existing.idTag.text = shortId;
        continue;
      }

      const { container, shipIcon, headingLine, selectionBox, label, idTag, idOffsetY, labelOffsetY, size, colorInt, hitRadius } = buildShipSprite(s);
      const pathGfx = new PIXI.Graphics();

      const slot = { index: 0 };

      transitPathLayer.addChild(pathGfx);
      shipLayer.addChild(container);
      shipGfx.set(s.id, {
        ship: s,
        container,
        shipIcon,
        headingLine,
        selectionBox,
        pathGfx,
        label,
        idTag,
        idOffsetY,
        labelOffsetY,
        size,
        colorInt,
        hitRadius,
        slot,
        transitKey: null,
        curve: null,
      });
    }

    for (const [shipId, gfx] of shipGfx.entries()) {
      if (nextIds.has(shipId)) continue;
      unregisterZoomScaledText(gfx.label);
      unregisterZoomScaledText(gfx.idTag);
      if (gfx.pathGfx?.parent) gfx.pathGfx.parent.removeChild(gfx.pathGfx);
      gfx.pathGfx?.destroy?.();
      if (gfx.container?.parent) gfx.container.parent.removeChild(gfx.container);
      gfx.container?.destroy?.({ children: true });
      shipGfx.delete(shipId);
    }
  }

  function updateShipPositions() {
    const now = serverNow();
    const zoom = Math.max(0.0001, world.scale.x);
    const deepShipShrink = deepZoomShrink(zoom, DEEP_ZOOM_SHRINK_START, DEEP_ZOOM_ICON_SHRINK_RATE, DEEP_ZOOM_ICON_SHRINK_MIN);
    const effectiveShipScale = deepShipShrink * highZoomShipShrinkMultiplier(zoom);
    const shipTextLockedToScreen = 1 / zoom;
    const deepLabelShrink = deepZoomShrink(zoom, DEEP_ZOOM_SHRINK_START, DEEP_ZOOM_LABEL_SHRINK_RATE, DEEP_ZOOM_LABEL_SHRINK_MIN);
    const idTagZoomAlpha = zoomFade(zoom, SHIP_IDTAG_FADE_IN_ZOOM, SHIP_IDTAG_FULL_ZOOM);
    const clusterMode = zoom <= SHIP_CLUSTER_ZOOM_THRESHOLD;
    const dockedCountByLocation = new Map();
    const dockedShipsByLocation = new Map();
    const hiddenClusterCountByLocation = new Map();

    for (const gfx of shipGfx.values()) {
      const s = gfx.ship;
      if (s?.status !== "docked" || !s.location_id) continue;
      dockedCountByLocation.set(s.location_id, (dockedCountByLocation.get(s.location_id) || 0) + 1);
      // Only count landed ships (not in orbits or L-points) for the badge chip
      if (!ORBIT_IDS.has(s.location_id) && !LPOINT_IDS.has(s.location_id)) {
        const chipAnchorId = dockedChipAnchorIdForLocation(s.location_id);
        if (!dockedShipsByLocation.has(chipAnchorId)) dockedShipsByLocation.set(chipAnchorId, []);
        dockedShipsByLocation.get(chipAnchorId).push(s);
      }
    }

    for (const gfx of shipGfx.values()) {
      const { ship, container, shipIcon, headingLine, selectionBox, pathGfx, label, idTag, idOffsetY, labelOffsetY, size, hitRadius, slot } = gfx;
      let facingAngle = 0;
      let headingAngle = 0;
      let px = 0;
      let py = 0;
      if (ship.status === "docked") {
        const loc = locationsById.get(ship.location_id);
        if (!loc) continue;

        if (pathGfx) pathGfx.clear();

        const dockedCount = dockedCountByLocation.get(ship.location_id) || 1;
        const isSelectedOrHovered = (ship.id === selectedShipId || ship.id === hoveredShipId);
        const isOrbitLocation = ORBIT_IDS.has(ship.location_id);
        const isLPointLocation = LPOINT_IDS.has(ship.location_id);
        // Always hide landed ships behind the badge (non-orbit, non-L-point)
        const isLandedLocation = !isOrbitLocation && !isLPointLocation;
        if (isLandedLocation && !isSelectedOrHovered) {
          container.visible = false;
          hiddenClusterCountByLocation.set(ship.location_id, (hiddenClusterCountByLocation.get(ship.location_id) || 0) + 1);
          continue;
        }

        container.visible = true;

        // Orbit ships: position along the orbit ring with animated rotation
        const oi = isOrbitLocation ? orbitInfo.get(ship.location_id) : null;
        if (oi && oi.period_s > 0) {
          const slotIndex = Number(slot?.index) || 0;
          const slotSpacing = (2 * Math.PI) / Math.max(1, dockedCount);
          const elapsed = (performance.now() / 1000);
          const orbitAngle = oi.baseAngle + (elapsed / oi.period_s) * 2 * Math.PI + slotIndex * slotSpacing;
          px = oi.cx + oi.radius * Math.cos(orbitAngle);
          py = oi.cy + oi.radius * Math.sin(orbitAngle);
          facingAngle = orbitAngle + Math.PI / 2;
          headingAngle = facingAngle;
        } else {
          const slotIndex = Number(slot?.index) || 0;
          const rowOffset = dockedRowOffsetWorld(slotIndex, zoom, dockedCount);
          px = loc.rx + rowOffset.dxWorld;
          py = loc.ry + rowOffset.dyWorld;
          facingAngle = 0;
          headingAngle = 0;
        }
      } else {
        if (!ship.departed_at || !ship.arrives_at) continue;

        // Build a single composite curve from all transfer legs
        const legs = Array.isArray(ship.transfer_legs) ? ship.transfer_legs : [];
        const overallDepart = Number(ship.departed_at);
        const overallArrive = Number(ship.arrives_at);

        // Build cache key from all leg endpoints + anchors
        let compositeSig = `${ship.from_location_id}->${ship.to_location_id}|${legs.length}`;
        for (const leg of legs) {
          const fa = getTransitAnchorWorld(String(leg.from_id || ""), Number(leg.departure_time));
          const ta = getTransitAnchorWorld(String(leg.to_id || ""), Number(leg.arrival_time));
          compositeSig += `|${fa ? fa.rx.toFixed(1) : "~"},${ta ? ta.rx.toFixed(1) : "~"}`;
        }

        if (!gfx.curve || gfx.transitKey !== compositeSig) {
          if (legs.length > 0) {
            gfx.curve = buildCompositeCurve(legs);
          }
          // Fallback: single-leg from overall from/to
          if (!gfx.curve) {
            const fromId = String(ship.from_location_id || "");
            const toId = String(ship.to_location_id || "");
            const A = locationsById.get(fromId);
            const B = locationsById.get(toId);
            const fa = getTransitAnchorWorld(fromId, overallDepart) || A;
            const ta = getTransitAnchorWorld(toId, overallArrive) || B;
            if (fa && ta) {
              const fs = getLocationSolarGroup(fromId);
              const ts = getLocationSolarGroup(toId);
              gfx.curve = computeTransitCurve(fromId, toId, fa, ta, !!(fs && ts && fs !== ts), overallDepart, overallArrive);
            }
          }
          gfx.transitKey = compositeSig;
        }
        if (!gfx.curve) continue;

        const denom = Math.max(1e-6, overallArrive - overallDepart);
        const t = (now - overallDepart) / denom;
        const tt = Math.max(0, Math.min(1, t));

        // Warp: adjust curve for live planet positions so endpoints track moving bodies
        const warp = computeCurveWarp(gfx.curve);
        const p = curvePoint(gfx.curve, tt);
        const wp = warpXY(p.x, p.y, tt, warp);
        const tan = curveTangent(gfx.curve, tt);
        const travelAngle = Math.atan2(tan.y, tan.x);
        const decel = tt >= 0.5;
        facingAngle = decel ? travelAngle + Math.PI : travelAngle;
        headingAngle = travelAngle;
        px = wp.x;
        py = wp.y;

        if (pathGfx) {
          const isSelected = ship.id === selectedShipId;
          // Quantize inputs to skip redundant full redraws
          const qProg = (tt * 500) | 0;
          const qZoom = (zoom * 100) | 0;
          const qWarp = warp ? `${(warp.dxS|0)},${(warp.dyS|0)},${(warp.dxE|0)},${(warp.dyE|0)}` : "";
          const cacheKey = `${qProg}|${qZoom}|${isSelected ? 1 : 0}|${qWarp}`;
          if (pathGfx.__transitCacheKey !== cacheKey) {
            pathGfx.__transitCacheKey = cacheKey;
            drawDashedTransitPath(pathGfx, gfx.curve, size || 10, isSelected, effectiveShipScale, tt, warp);
          }
        }

        container.visible = true;
      }

      const displayPose = { x: px, y: py, facingAngle, headingAngle };
      container.position.set(displayPose.x, displayPose.y);
      container.rotation = displayPose.facingAngle;
      const baseIconPx = Math.max(6, Number(shipIcon?.__iconBasePx) || Math.max(8, (size || 10) * 1.2));
      const lockedScale = (SHIP_ICON_LOCK_SCREEN_PX / baseIconPx) / zoom;
      const iconDisplayScale = zoom >= SHIP_ICON_LOCK_ZOOM_START ? lockedScale : effectiveShipScale;

      if (shipIcon) shipIcon.scale.set(iconDisplayScale);
      if (container?.hitArea) {
        const glowBase = Math.max(2.4, Number(shipIcon?.__glowRadiusPx) || Number(shipIcon?.__hitRadiusPx) || (Number(hitRadius) || 8) * 0.3);
        const minWorldRadius = MIN_SHIP_HIT_SCREEN_PX / zoom;
        const scaledHitRadius = Math.max(minWorldRadius, glowBase * iconDisplayScale);
        if (container.hitArea instanceof PIXI.Circle) {
          container.hitArea.radius = scaledHitRadius;
        } else {
          container.hitArea = new PIXI.Circle(0, 0, scaledHitRadius);
        }
      }

      if (headingLine) {
        headingLine.rotation = displayPose.headingAngle - displayPose.facingAngle;
        headingLine.alpha = ship.status === "transit" ? 0.84 : 0.5;
      }

      if (selectionBox) {
        selectionBox.scale.set(iconDisplayScale);
        const selectionLineWorld = SHIP_SELECTION_STROKE_PX / (zoom * Math.max(0.0001, iconDisplayScale));
        drawSelectionBracket(selectionBox, size, selectionLineWorld);
        selectionBox.alpha = (ship.id === selectedShipId || ship.id === hoveredShipId) ? 1 : 0;
      }

      if (label) {
        const theta = container.rotation || 0;
        const idDy = (baseIconPx * iconDisplayScale * 0.56) + (2 / zoom);
        const dy = idDy + (6 / zoom);
        label.position.set(Math.sin(theta) * dy, Math.cos(theta) * dy);
        label.rotation = -theta;
        label.scale.set(shipTextLockedToScreen);
        label.alpha = (ship.id === selectedShipId || ship.id === hoveredShipId) ? 1 : 0;
        label.visible = label.alpha > 0.001;
      }

      if (idTag) {
        const theta = container.rotation || 0;
        const dy = (baseIconPx * iconDisplayScale * 0.56) + (1.5 / zoom);
        idTag.position.set(Math.sin(theta) * dy, Math.cos(theta) * dy);
        idTag.rotation = -theta;
        idTag.scale.set(shipTextLockedToScreen);
        const selectedBoost = (ship.id === selectedShipId || ship.id === hoveredShipId) ? 1 : 0.82;
        idTag.alpha = idTagZoomAlpha * selectedBoost;
        idTag.visible = idTag.alpha > 0.01;
      }
    }

    updateDockedShipChips(dockedShipsByLocation);
    updateShipClusterLabels(hiddenClusterCountByLocation, false);

    applyUniversalTextScaleCap();
  }

  function projectLocationsForMap(rawLocations) {
    const projectedLocations = Array.isArray(rawLocations)
      ? rawLocations.map((loc) => ({ ...loc }))
      : [];

    const parentById = new Map(projectedLocations.map((l) => [l.id, l.parent_id || null]));

    const sun = projectedLocations.find((l) => l.id === "grp_sun");
    const mercury = projectedLocations.find((l) => l.id === "grp_mercury");
    const venus = projectedLocations.find((l) => l.id === "grp_venus");
    const earth = projectedLocations.find((l) => l.id === "grp_earth");
    const moon = projectedLocations.find((l) => l.id === "grp_moon");
    const mars = projectedLocations.find((l) => l.id === "grp_mars");
    const ceres = projectedLocations.find((l) => l.id === "grp_ceres");
    const vesta = projectedLocations.find((l) => l.id === "grp_vesta");
    const pallas = projectedLocations.find((l) => l.id === "grp_pallas");
    const hygiea = projectedLocations.find((l) => l.id === "grp_hygiea");
    const jupiter = projectedLocations.find((l) => l.id === "grp_jupiter");

    const sunX = sun ? Number(sun.x) : 0;
    const sunY = sun ? Number(sun.y) : 0;
    const sunRx = sunX * DEEP_SCALE;
    const sunRy = sunY * DEEP_SCALE;

    function projectDeepPosition(xKm, yKm) {
      const xx = Number(xKm);
      const yy = Number(yKm);
      if (!sun) return { rx: xx * DEEP_SCALE, ry: yy * DEEP_SCALE };

      const dx = xx - sunX;
      const dy = yy - sunY;
      const rKm = Math.hypot(dx, dy);
      if (rKm <= 1e-9) return { rx: sunRx, ry: sunRy };

      const unitX = dx / rKm;
      const unitY = dy / rKm;
      const rVisual = Math.max(0, rKm) * HELIO_LINEAR_WORLD_PER_KM;
      return {
        rx: sunRx + unitX * rVisual,
        ry: sunRy + unitY * rVisual,
      };
    }

    const mercuryProjected = mercury ? projectDeepPosition(mercury.x, mercury.y) : { rx: 0, ry: 0 };
    const venusProjected = venus ? projectDeepPosition(venus.x, venus.y) : { rx: 0, ry: 0 };
    const earthProjected = earth ? projectDeepPosition(earth.x, earth.y) : { rx: 0, ry: 0 };
    const moonProjected = moon ? projectDeepPosition(moon.x, moon.y) : projectDeepPosition(384400, 0);
    const marsProjected = mars ? projectDeepPosition(mars.x, mars.y) : { rx: 0, ry: 0 };
    const ceresProjected = ceres ? projectDeepPosition(ceres.x, ceres.y) : { rx: 0, ry: 0 };
    const vestaProjected = vesta ? projectDeepPosition(vesta.x, vesta.y) : { rx: 0, ry: 0 };
    const pallasProjected = pallas ? projectDeepPosition(pallas.x, pallas.y) : { rx: 0, ry: 0 };
    const hygieaProjected = hygiea ? projectDeepPosition(hygiea.x, hygiea.y) : { rx: 0, ry: 0 };
    const jupiterProjected = jupiter ? projectDeepPosition(jupiter.x, jupiter.y) : { rx: 0, ry: 0 };
    const mercuryRx = mercuryProjected.rx;
    const mercuryRy = mercuryProjected.ry;
    const venusRx = venusProjected.rx;
    const venusRy = venusProjected.ry;
    const earthRx = earthProjected.rx;
    const earthRy = earthProjected.ry;
    const moonRx = moonProjected.rx;
    const moonRy = moonProjected.ry;
    const marsRx = marsProjected.rx;
    const marsRy = marsProjected.ry;
    const ceresRx = ceresProjected.rx;
    const ceresRy = ceresProjected.ry;
    const vestaRx = vestaProjected.rx;
    const vestaRy = vestaProjected.ry;
    const pallasRx = pallasProjected.rx;
    const pallasRy = pallasProjected.ry;
    const hygieaRx = hygieaProjected.rx;
    const hygieaRy = hygieaProjected.ry;
    const jupiterRx = jupiterProjected.rx;
    const jupiterRy = jupiterProjected.ry;

    // Resolve Phobos, Deimos, Zoozve marker positions (they are projected as mars_moons / zoozve group descendants)
    const phobosLoc = projectedLocations.find((l) => l.id === "PHOBOS");
    const deimosLoc = projectedLocations.find((l) => l.id === "DEIMOS");
    const zoozve = projectedLocations.find((l) => l.id === "grp_zoozve") || projectedLocations.find((l) => l.id === "ZOOZVE");
    // Phobos/Deimos projected positions (relative to mars, using MARS_ORBIT_SCALE)
    const phobosProjected = phobosLoc && mars
      ? { rx: marsRx + (Number(phobosLoc.x) - Number(mars.x)) * MARS_ORBIT_SCALE,
          ry: marsRy + (Number(phobosLoc.y) - Number(mars.y)) * MARS_ORBIT_SCALE }
      : { rx: marsRx, ry: marsRy };
    const deimosProjected = deimosLoc && mars
      ? { rx: marsRx + (Number(deimosLoc.x) - Number(mars.x)) * MARS_ORBIT_SCALE,
          ry: marsRy + (Number(deimosLoc.y) - Number(mars.y)) * MARS_ORBIT_SCALE }
      : { rx: marsRx, ry: marsRy };
    const zoozveProjected = zoozve ? projectDeepPosition(zoozve.x, zoozve.y) : { rx: 0, ry: 0 };
    const phobosRx = phobosProjected.rx;
    const phobosRy = phobosProjected.ry;
    const deimosRx = deimosProjected.rx;
    const deimosRy = deimosProjected.ry;
    const zoozveRx = zoozveProjected.rx;
    const zoozveRy = zoozveProjected.ry;

    // Resolve Jupiter's Galilean moon marker positions
    const ioLoc = projectedLocations.find((l) => l.id === "IO");
    const europaLoc = projectedLocations.find((l) => l.id === "EUROPA");
    const ganymedeLoc = projectedLocations.find((l) => l.id === "GANYMEDE");
    const callistoLoc = projectedLocations.find((l) => l.id === "CALLISTO");
    const ioProjected = ioLoc && jupiter
      ? { rx: jupiterRx + (Number(ioLoc.x) - Number(jupiter.x)) * JUPITER_ORBIT_SCALE,
          ry: jupiterRy + (Number(ioLoc.y) - Number(jupiter.y)) * JUPITER_ORBIT_SCALE }
      : { rx: jupiterRx, ry: jupiterRy };
    const europaProjected = europaLoc && jupiter
      ? { rx: jupiterRx + (Number(europaLoc.x) - Number(jupiter.x)) * JUPITER_ORBIT_SCALE,
          ry: jupiterRy + (Number(europaLoc.y) - Number(jupiter.y)) * JUPITER_ORBIT_SCALE }
      : { rx: jupiterRx, ry: jupiterRy };
    const ganymProjected = ganymedeLoc && jupiter
      ? { rx: jupiterRx + (Number(ganymedeLoc.x) - Number(jupiter.x)) * JUPITER_ORBIT_SCALE,
          ry: jupiterRy + (Number(ganymedeLoc.y) - Number(jupiter.y)) * JUPITER_ORBIT_SCALE }
      : { rx: jupiterRx, ry: jupiterRy };
    const callistoProjected = callistoLoc && jupiter
      ? { rx: jupiterRx + (Number(callistoLoc.x) - Number(jupiter.x)) * JUPITER_ORBIT_SCALE,
          ry: jupiterRy + (Number(callistoLoc.y) - Number(jupiter.y)) * JUPITER_ORBIT_SCALE }
      : { rx: jupiterRx, ry: jupiterRy };
    const ioRx = ioProjected.rx;
    const ioRy = ioProjected.ry;
    const europaRx = europaProjected.rx;
    const europaRy = europaProjected.ry;
    const ganymRx = ganymProjected.rx;
    const ganymRy = ganymProjected.ry;
    const callistoRx = callistoProjected.rx;
    const callistoRy = callistoProjected.ry;

    for (const l of projectedLocations) {
      l.is_group = !!Number(l.is_group);

      const deep = projectDeepPosition(l.x, l.y);
      let rx = deep.rx;
      let ry = deep.ry;

      if (!l.is_group && hasAncestor(l.id, "grp_earth_orbits", parentById) && earth) {
        rx = earthRx + (Number(l.x) - Number(earth.x)) * EARTH_ORBIT_SCALE;
        ry = earthRy + (Number(l.y) - Number(earth.y)) * EARTH_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_moon_orbits", parentById) && moon) {
        rx = moonRx + (Number(l.x) - Number(moon.x)) * MOON_ORBIT_SCALE;
        ry = moonRy + (Number(l.y) - Number(moon.y)) * MOON_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_mercury_orbits", parentById) && mercury) {
        rx = mercuryRx + (Number(l.x) - Number(mercury.x)) * MERCURY_ORBIT_SCALE;
        ry = mercuryRy + (Number(l.y) - Number(mercury.y)) * MERCURY_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_venus_orbits", parentById) && venus) {
        rx = venusRx + (Number(l.x) - Number(venus.x)) * VENUS_ORBIT_SCALE;
        ry = venusRy + (Number(l.y) - Number(venus.y)) * VENUS_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_mars_orbits", parentById) && mars) {
        rx = marsRx + (Number(l.x) - Number(mars.x)) * MARS_ORBIT_SCALE;
        ry = marsRy + (Number(l.y) - Number(mars.y)) * MARS_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_mars_moons", parentById) && mars) {
        rx = marsRx + (Number(l.x) - Number(mars.x)) * MARS_ORBIT_SCALE;
        ry = marsRy + (Number(l.y) - Number(mars.y)) * MARS_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_ceres_orbits", parentById) && ceres) {
        rx = ceresRx + (Number(l.x) - Number(ceres.x)) * CERES_ORBIT_SCALE;
        ry = ceresRy + (Number(l.y) - Number(ceres.y)) * CERES_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_vesta_orbits", parentById) && vesta) {
        rx = vestaRx + (Number(l.x) - Number(vesta.x)) * VESTA_ORBIT_SCALE;
        ry = vestaRy + (Number(l.y) - Number(vesta.y)) * VESTA_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_pallas_orbits", parentById) && pallas) {
        rx = pallasRx + (Number(l.x) - Number(pallas.x)) * PALLAS_ORBIT_SCALE;
        ry = pallasRy + (Number(l.y) - Number(pallas.y)) * PALLAS_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_hygiea_orbits", parentById) && hygiea) {
        rx = hygieaRx + (Number(l.x) - Number(hygiea.x)) * HYGIEA_ORBIT_SCALE;
        ry = hygieaRy + (Number(l.y) - Number(hygiea.y)) * HYGIEA_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_phobos_orbits", parentById) && phobosLoc) {
        rx = phobosRx + (Number(l.x) - Number(phobosLoc.x)) * PHOBOS_ORBIT_SCALE;
        ry = phobosRy + (Number(l.y) - Number(phobosLoc.y)) * PHOBOS_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_deimos_orbits", parentById) && deimosLoc) {
        rx = deimosRx + (Number(l.x) - Number(deimosLoc.x)) * DEIMOS_ORBIT_SCALE;
        ry = deimosRy + (Number(l.y) - Number(deimosLoc.y)) * DEIMOS_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_zoozve_orbits", parentById) && zoozve) {
        rx = zoozveRx + (Number(l.x) - Number(zoozve.x)) * ZOOZVE_ORBIT_SCALE;
        ry = zoozveRy + (Number(l.y) - Number(zoozve.y)) * ZOOZVE_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_jupiter_orbits", parentById) && jupiter) {
        rx = jupiterRx + (Number(l.x) - Number(jupiter.x)) * JUPITER_ORBIT_SCALE;
        ry = jupiterRy + (Number(l.y) - Number(jupiter.y)) * JUPITER_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_jupiter_moons", parentById) && jupiter) {
        rx = jupiterRx + (Number(l.x) - Number(jupiter.x)) * JUPITER_ORBIT_SCALE;
        ry = jupiterRy + (Number(l.y) - Number(jupiter.y)) * JUPITER_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_jupiter_lpoints", parentById) && jupiter) {
        // L4/L5 are heliocentric (60° from Jupiter on its orbit) — use solar projection
        // L1/L2/L3 are near Jupiter — use local orbit scale
        const sjId = String(l.id);
        if (sjId === "SJ_L4" || sjId === "SJ_L5") {
          const proj = projectDeepPosition(l.x, l.y);
          rx = proj.rx;
          ry = proj.ry;
        } else {
          rx = jupiterRx + (Number(l.x) - Number(jupiter.x)) * JUPITER_ORBIT_SCALE;
          ry = jupiterRy + (Number(l.y) - Number(jupiter.y)) * JUPITER_ORBIT_SCALE;
        }
      } else if (!l.is_group && hasAncestor(l.id, "grp_io_orbits", parentById) && ioLoc) {
        rx = ioRx + (Number(l.x) - Number(ioLoc.x)) * IO_ORBIT_SCALE;
        ry = ioRy + (Number(l.y) - Number(ioLoc.y)) * IO_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_europa_orbits", parentById) && europaLoc) {
        rx = europaRx + (Number(l.x) - Number(europaLoc.x)) * EUROPA_ORBIT_SCALE;
        ry = europaRy + (Number(l.y) - Number(europaLoc.y)) * EUROPA_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_ganymede_orbits", parentById) && ganymedeLoc) {
        rx = ganymRx + (Number(l.x) - Number(ganymedeLoc.x)) * GANYMEDE_ORBIT_SCALE;
        ry = ganymRy + (Number(l.y) - Number(ganymedeLoc.y)) * GANYMEDE_ORBIT_SCALE;
      } else if (!l.is_group && hasAncestor(l.id, "grp_callisto_orbits", parentById) && callistoLoc) {
        rx = callistoRx + (Number(l.x) - Number(callistoLoc.x)) * CALLISTO_ORBIT_SCALE;
        ry = callistoRy + (Number(l.y) - Number(callistoLoc.y)) * CALLISTO_ORBIT_SCALE;
      } else if (l.id === "grp_mercury") {
        rx = mercuryRx; ry = mercuryRy;
      } else if (l.id === "grp_venus") {
        rx = venusRx; ry = venusRy;
      } else if (l.id === "grp_earth") {
        rx = earthRx; ry = earthRy;
      } else if (l.id === "grp_moon") {
        rx = moonRx; ry = moonRy;
      } else if (l.id === "grp_mars") {
        rx = marsRx; ry = marsRy;
      } else if (l.id === "grp_ceres") {
        rx = ceresRx; ry = ceresRy;
      } else if (l.id === "grp_vesta") {
        rx = vestaRx; ry = vestaRy;
      } else if (l.id === "grp_pallas") {
        rx = pallasRx; ry = pallasRy;
      } else if (l.id === "grp_hygiea") {
        rx = hygieaRx; ry = hygieaRy;
      } else if (l.id === "grp_jupiter") {
        rx = jupiterRx; ry = jupiterRy;
      }

      l.rx = rx;
      l.ry = ry;
    }

    return {
      locations: projectedLocations,
      parentById,
    };
  }

  function transitAnchorBucket(gameTimeS) {
    const t = Number(gameTimeS);
    if (!Number.isFinite(t)) return null;
    return Math.floor(t / TRANSIT_ANCHOR_BUCKET_S);
  }

  function getTransitAnchorWorld(locationId, gameTimeS) {
    const bucket = transitAnchorBucket(gameTimeS);
    if (bucket == null) return null;
    const byLocation = transitAnchorSnapshots.get(String(bucket));
    if (!byLocation) return null;
    return byLocation.get(String(locationId)) || null;
  }

  async function ensureTransitAnchorSnapshot(bucket) {
    const key = String(bucket);
    if (transitAnchorSnapshots.has(key)) return;
    if (transitAnchorSnapshotInflight.has(key)) {
      await transitAnchorSnapshotInflight.get(key);
      return;
    }

    const bucketTime = (Number(bucket) + 0.5) * TRANSIT_ANCHOR_BUCKET_S;
    const promise = (async () => {
      const resp = await fetch(`/api/locations?dynamic=1&t=${encodeURIComponent(String(bucketTime))}`, { cache: "no-store" });
      const data = await resp.json();
      const projected = projectLocationsForMap(data.locations || []);
      const byLocation = new Map();
      for (const loc of projected.locations) {
        byLocation.set(String(loc.id), {
          rx: Number(loc.rx),
          ry: Number(loc.ry),
        });
      }
      transitAnchorSnapshots.set(key, byLocation);
      while (transitAnchorSnapshots.size > 16) {
        const first = transitAnchorSnapshots.keys().next().value;
        if (first == null) break;
        transitAnchorSnapshots.delete(first);
      }
    })();

    transitAnchorSnapshotInflight.set(key, promise);
    try {
      await promise;
    } finally {
      transitAnchorSnapshotInflight.delete(key);
    }
  }

  async function ensureTransitAnchorsForShips(shipList) {
    const buckets = new Set();
    for (const ship of (shipList || [])) {
      if (!ship || ship.status !== "transit") continue;
      const fromBucket = transitAnchorBucket(ship.departed_at);
      const toBucket = transitAnchorBucket(ship.arrives_at);
      if (fromBucket != null) buckets.add(fromBucket);
      if (toBucket != null) buckets.add(toBucket);
      // Also collect buckets for every individual transfer leg so that
      // intermediate interplanetary legs resolve correct future positions.
      const legs = Array.isArray(ship.transfer_legs) ? ship.transfer_legs : [];
      for (const leg of legs) {
        const legDepBucket = transitAnchorBucket(leg.departure_time);
        const legArrBucket = transitAnchorBucket(leg.arrival_time);
        if (legDepBucket != null) buckets.add(legDepBucket);
        if (legArrBucket != null) buckets.add(legArrBucket);
      }
    }
    await Promise.all(Array.from(buckets).map((bucket) => ensureTransitAnchorSnapshot(bucket)));
  }

  // ---------- Move Planner modal ----------
  function closeModal() {
    const el = document.getElementById("tpModalRoot");
    if (el) el.remove();
    document.removeEventListener("keydown", escClose);
  }
  function escClose(e) {
    if (e.key === "Escape") closeModal();
  }

  function transferTreePrefix(ancestorHasNext, isLast) {
    const chain = Array.isArray(ancestorHasNext) ? ancestorHasNext : [];
    const guides = chain.map((hasNext) => (hasNext ? "│ " : "  ")).join("");
    return `${guides}${isLast ? "└─" : "├─"} `;
  }

  function transferNodeSymbol(node) {
    const nodeSymbol = String(node?.symbol || "").trim();
    if (nodeSymbol) return nodeSymbol;
    const locSymbol = String(locationsById.get(String(node?.id || ""))?.symbol || "").trim();
    return locSymbol || "";
  }

  function renderTreeNode(node, containerEl, onSelectLeaf, treeCtx, selectedDestId, openStateById) {
    const ctx = treeCtx || { ancestorHasNext: [], isLast: true, depth: 0 };
    const prefixText = transferTreePrefix(ctx.ancestorHasNext, ctx.isLast);

    if (node.is_group) {
      const details = document.createElement("details");
      details.className = "transferTreeGroup";
      const openDefault = (ctx.depth || 0) < 2;
      details.open = openStateById.has(node.id) ? !!openStateById.get(node.id) : openDefault;
      details.addEventListener("toggle", () => {
        openStateById.set(node.id, !!details.open);
      });

      const summary = document.createElement("summary");
      summary.className = "transferTreeSummary";
      const row = document.createElement("div");
      row.className = "transferTreeRow transferTreeFolderRow";

      const prefix = document.createElement("span");
      prefix.className = "transferTreePrefix";
      prefix.textContent = prefixText;

      const symbol = transferNodeSymbol(node);
      const symbolEl = document.createElement("span");
      symbolEl.className = "transferTreeSymbol";
      symbolEl.textContent = symbol;
      if (!symbol) symbolEl.style.display = "none";

      const label = document.createElement("span");
      label.className = "transferTreeLabel";
      label.textContent = node.name;

      row.append(prefix, symbolEl, label);
      summary.appendChild(row);
      details.appendChild(summary);

      const childrenWrap = document.createElement("div");
      childrenWrap.className = "transferTreeChildren";
      const kids = Array.isArray(node.children) ? node.children : [];
      const nextAncestor = [...ctx.ancestorHasNext, !ctx.isLast];
      for (let i = 0; i < kids.length; i += 1) {
        renderTreeNode(
          kids[i],
          childrenWrap,
          onSelectLeaf,
          { ancestorHasNext: nextAncestor, isLast: i === kids.length - 1, depth: (ctx.depth || 0) + 1 },
          selectedDestId,
          openStateById
        );
      }

      details.appendChild(childrenWrap);
      containerEl.appendChild(details);
      return;
    }

    const btn = document.createElement("button");
    btn.className = `transferTreeLeaf${selectedDestId === node.id ? " isSelected" : ""}`;
    btn.type = "button";

    const row = document.createElement("div");
    row.className = "transferTreeRow";

    const prefix = document.createElement("span");
    prefix.className = "transferTreePrefix";
    prefix.textContent = prefixText;

    const symbol = transferNodeSymbol(node);
    const symbolEl = document.createElement("span");
    symbolEl.className = "transferTreeSymbol";
    symbolEl.textContent = symbol;
    if (!symbol) symbolEl.style.display = "none";

    const label = document.createElement("span");
    label.className = "transferTreeLabel";
    label.textContent = node.name;

    row.append(prefix, symbolEl, label);
    btn.appendChild(row);
    btn.onclick = () => onSelectLeaf(node);
    containerEl.appendChild(btn);
  }

  // ── Prospecting Dialog ──────────────────────────────────────────────────────

  async function openProspectDialog(ship) {
    if (!ship) return;
    hideContextMenu();

    function _esc(s) { return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

    function fmtDist(km) {
      km = Math.max(0, Number(km) || 0);
      if (km >= 1e6) return `${(km / 1e6).toFixed(1)}M km`;
      if (km >= 1e3) return `${(km / 1e3).toFixed(0)}k km`;
      return `${km.toFixed(0)} km`;
    }

    function fmtPct(v) { return `${(Number(v || 0) * 100).toFixed(1)}%`; }

    // Get robonaut info from parts
    const shipParts = Array.isArray(ship.parts) ? ship.parts : [];
    const robonauts = shipParts.filter((p) => {
      if (!p || typeof p !== "object") return false;
      const cat = String(p.category_id || p.type || p.category || "").toLowerCase();
      return cat === "robonaut" || cat === "robonauts";
    });
    const bestRobonaut = robonauts.reduce((best, r) => {
      const range = Number(r.prospect_range_km || 0);
      return range > (Number(best?.prospect_range_km) || 0) ? r : best;
    }, robonauts[0] || {});
    const rangeKm = Number(bestRobonaut.prospect_range_km || 0);

    // Build overlay
    const overlay = document.createElement("div");
    overlay.id = "prospectModalRoot";
    overlay.className = "modal";

    overlay.innerHTML = `
      <div class="modalOverlay"></div>
      <div class="prospectModal">
        <div class="prospectHeader">
          <div class="prospectHeaderLeft">
            <div class="prospectTitle">Prospecting</div>
            <div class="prospectSubtitle">${_esc(ship.name)} &bull; ${_esc(bestRobonaut.name || "Robonaut")} &bull; Range ${fmtDist(rangeKm)}</div>
          </div>
          <button class="iconBtn btnSecondary" id="prospectClose">✕</button>
        </div>
        <div class="prospectBody">
          <div class="prospectLoading">Loading sites in range…</div>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    function closeModal() {
      overlay.remove();
      document.removeEventListener("keydown", escClose);
    }
    function escClose(e) { if (e.key === "Escape") closeModal(); }
    document.getElementById("prospectClose").onclick = closeModal;
    overlay.addEventListener("pointerdown", (e) => {
      if (e.target === overlay || e.target.classList.contains("modalOverlay")) closeModal();
    });
    document.addEventListener("keydown", escClose);

    const bodyEl = overlay.querySelector(".prospectBody");

    // Fetch sites in range
    try {
      const resp = await fetch(`/api/org/prospecting/in_range/${encodeURIComponent(ship.id)}`, { cache: "no-store" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data?.detail || "Failed to load prospecting data");

      const sites = Array.isArray(data.sites) ? data.sites : [];
      if (!sites.length) {
        bodyEl.innerHTML = '<div class="prospectEmpty">No surface sites within range of this ship\'s robonaut.</div>';
        return;
      }

      renderProspectSiteList(bodyEl, sites, ship, data, closeModal);
    } catch (err) {
      bodyEl.innerHTML = `<div class="prospectError">${_esc(err?.message || "Failed to load")}</div>`;
    }
  }

  function renderProspectSiteList(container, sites, ship, rangeData, closeModal) {
    function _esc(s) { return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

    function fmtDist(km) {
      km = Math.max(0, Number(km) || 0);
      if (km >= 1e6) return `${(km / 1e6).toFixed(1)}M km`;
      if (km >= 1e3) return `${(km / 1e3).toFixed(0)}k km`;
      return `${km.toFixed(0)} km`;
    }

    function fmtPct(v) { return `${(Number(v || 0) * 100).toFixed(1)}%`; }

    container.innerHTML = "";

    // Summary bar
    const summary = document.createElement("div");
    summary.className = "prospectSummary";
    const totalSites = sites.length;
    const prospected = sites.filter((s) => s.is_prospected).length;
    const unprospected = totalSites - prospected;
    summary.innerHTML = `
      <span class="prospectSummaryCount">${totalSites} site${totalSites !== 1 ? "s" : ""} in range</span>
      <span class="prospectSummaryDetail">${prospected} prospected &bull; ${unprospected} uncharted</span>
    `;
    container.appendChild(summary);

    // Group sites by body
    const byBody = new Map();
    for (const site of sites) {
      const body = site.body_id || "Unknown";
      if (!byBody.has(body)) byBody.set(body, []);
      byBody.get(body).push(site);
    }

    for (const [bodyId, bodySites] of byBody) {
      const group = document.createElement("div");
      group.className = "prospectBodyGroup";

      const groupHeader = document.createElement("div");
      groupHeader.className = "prospectBodyHeader";
      groupHeader.textContent = bodyId;
      group.appendChild(groupHeader);

      for (const site of bodySites) {
        const row = document.createElement("div");
        row.className = `prospectSiteRow ${site.is_prospected ? "isProspected" : "isUncharted"}`;

        const infoCol = document.createElement("div");
        infoCol.className = "prospectSiteInfo";

        const nameEl = document.createElement("div");
        nameEl.className = "prospectSiteName";
        nameEl.textContent = site.name || site.location_id;
        infoCol.appendChild(nameEl);

        const metaEl = document.createElement("div");
        metaEl.className = "prospectSiteMeta";
        metaEl.innerHTML = `${fmtDist(site.distance_km)} &bull; ${site.gravity_m_s2.toFixed(2)} m/s²`;
        infoCol.appendChild(metaEl);

        row.appendChild(infoCol);

        const actionCol = document.createElement("div");
        actionCol.className = "prospectSiteAction";

        if (site.is_prospected) {
          const badge = document.createElement("span");
          badge.className = "prospectBadge prospectBadgeGreen";
          badge.textContent = "Prospected ✓";
          actionCol.appendChild(badge);
        } else {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btnPrimary prospectBtn";
          btn.textContent = "Prospect";
          btn.addEventListener("click", async () => {
            btn.disabled = true;
            btn.textContent = "Prospecting…";
            try {
              const resp = await fetch("/api/org/prospecting/prospect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ship_id: ship.id, site_location_id: site.location_id }),
              });
              const result = await resp.json().catch(() => ({}));
              if (!resp.ok) throw new Error(result?.detail || "Prospecting failed");

              // Update the site in our list to show results
              site.is_prospected = true;
              site.resources_found = result.resources_found || [];

              // Re-render the list with updated data
              renderProspectSiteList(container, sites, ship, rangeData, closeModal);

              // Refresh map state
              if (typeof syncState === "function") syncState();
            } catch (err) {
              btn.disabled = false;
              btn.textContent = "Prospect";
              alert(err?.message || "Prospecting failed");
            }
          });
          actionCol.appendChild(btn);
        }

        row.appendChild(actionCol);

        // If prospected, show resource results below
        if (site.is_prospected && Array.isArray(site.resources_found) && site.resources_found.length) {
          const resWrap = document.createElement("div");
          resWrap.className = "prospectResourceList";
          for (const res of site.resources_found) {
            const resRow = document.createElement("div");
            resRow.className = "prospectResourceRow";
            resRow.innerHTML = `<span class="prospectResName">${_esc(res.resource_id)}</span><span class="prospectResFraction">${fmtPct(res.mass_fraction)}</span>`;
            resWrap.appendChild(resRow);
          }
          row.appendChild(resWrap);
        }

        group.appendChild(row);
      }

      container.appendChild(group);
    }
  }

  async function openTransferPlanner(ship, initialDestId = null) {
    if (!ship || ship.status !== "docked") return;

    hideContextMenu();

    if (!treeCache) {
      const t = await (await fetch("/api/locations/tree", { cache: "no-store" })).json();
      treeCache = t.tree || [];
    }

    // ── State ──────────────────────────────────────────────
    let selectedDest = null;
    let currentExtraDvFraction = 0;
    let departureTimeOverride = null; // null = "now"
    let lastQuote = null;
    let lastPorkchopData = null;       // stored porkchop grid data
    let lastPorkchopDepTime = null;    // departure time used for the porkchop
    let porkchopRedrawCrosshair = null; // function to redraw crosshair at a TOF
    const transferTreeOpenState = new Map();

    function _esc(s) { return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

    // ── Build overlay ──────────────────────────────────────
    const overlay = document.createElement("div");
    overlay.id = "tpModalRoot";
    overlay.className = "modal";

    const shipDv = Number(ship.delta_v_remaining_m_s || 0);
    const shipFuel = Number(ship.fuel_kg || 0);
    const shipFuelCap = Number(ship.fuel_capacity_kg || 0);
    const shipFuelPct = shipFuelCap > 0 ? Math.round((shipFuel / shipFuelCap) * 100) : 0;

    overlay.innerHTML = `
      <div class="modalOverlay"></div>
      <div class="tpModal">
        <div class="tpHeader">
          <div class="tpHeaderLeft">
            <div class="tpTitle">Transfer Planner</div>
            <div class="tpSubtitle">${_esc(ship.name)} &bull; ${_esc(ship.location_id)} &bull; Δv ${Math.round(shipDv)} m/s &bull; Fuel ${shipFuelPct}%</div>
          </div>
          <button class="iconBtn btnSecondary" id="tpClose">✕</button>
        </div>

        <div class="tpBody">
          <div class="tpDestPanel">
            <div class="tpDestLabel">Destination</div>
            <div id="tpTreeRoot"></div>
          </div>

          <div class="tpDetailPanel" id="tpDetailPanel">
            <div class="tpSection">
              <div class="muted" style="text-align:center; padding:20px 0;">Select a destination to view transfer details</div>
            </div>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    document.getElementById("tpClose").onclick = closeModal;
    overlay.addEventListener("pointerdown", (e) => { if (e.target === overlay || e.target.classList.contains("modalOverlay")) closeModal(); });
    document.addEventListener("keydown", escClose);

    const treeRoot = document.getElementById("tpTreeRoot");
    const detailPanel = document.getElementById("tpDetailPanel");

    // ── Helpers ────────────────────────────────────────────
    function fmtDuration(s) {
      s = Math.max(0, Math.round(Number(s) || 0));
      if (s < 3600) return `${Math.floor(s / 60)}m`;
      const h = s / 3600;
      if (h < 24) return `${h.toFixed(1)}h`;
      const d = h / 24;
      return `${d.toFixed(1)}d (${h.toFixed(0)}h)`;
    }

    function fmtGameDate(gameTimeS) {
      // Game time is a Unix timestamp (epoch = 1970-01-01, game starts ~2000-01-01)
      const date = new Date(gameTimeS * 1000);
      return date.toISOString().slice(0, 16).replace("T", " ") + " UTC";
    }

    function gameTimeToISOInput(gameTimeS) {
      const date = new Date(gameTimeS * 1000);
      return date.toISOString().slice(0, 16);
    }

    function isoInputToGameTime(isoStr) {
      const d = new Date(isoStr + "Z");
      if (isNaN(d.getTime())) return null;
      return d.getTime() / 1000;
    }

    function alignmentClass(pct) {
      if (pct <= 25) return "tpAlignGood";
      if (pct <= 60) return "tpAlignFair";
      return "tpAlignPoor";
    }

    function alignmentLabel(pct) {
      if (pct <= 15) return "Optimal";
      if (pct <= 25) return "Good";
      if (pct <= 45) return "Fair";
      if (pct <= 70) return "Poor";
      return "Bad";
    }

    // ── Destination tree ── segmented accordion ──────────
    function buildDestinationTree() {
      treeRoot.innerHTML = "";
      const wrap = document.createElement("div");
      wrap.className = "transferTreeRoot";

      // treeCache usually has one root (grp_sun) — flatten to its children
      let zoneNodes = treeCache;
      if (zoneNodes.length === 1 && zoneNodes[0].is_group && Array.isArray(zoneNodes[0].children)) {
        zoneNodes = zoneNodes[0].children;
      }

      const zoneDetails = []; // track <details> for exclusive-open

      for (const zoneNode of zoneNodes) {
        if (!zoneNode.is_group) {
          // Top-level leaf (e.g. "SUN") — render directly
          const btn = makeLeafButton(zoneNode);
          wrap.appendChild(btn);
          continue;
        }

        const zone = document.createElement("details");
        zone.className = "transferTreeZone";
        const stateKey = `zone:${zoneNode.id}`;
        const savedOpen = transferTreeOpenState.get(stateKey);
        zone.open = savedOpen != null ? !!savedOpen : (zoneNode.id === "grp_earth");

        zone.addEventListener("toggle", () => {
          transferTreeOpenState.set(stateKey, !!zone.open);
          // Accordion: close other zones when one opens
          if (zone.open) {
            for (const other of zoneDetails) {
              if (other !== zone && other.open) {
                other.open = false;
                const otherKey = other.dataset.stateKey;
                if (otherKey) transferTreeOpenState.set(otherKey, false);
              }
            }
          }
        });
        zone.dataset.stateKey = stateKey;

        const summary = document.createElement("summary");
        const sym = locationsById.get(zoneNode.id)?.symbol || "";
        if (sym) {
          const symSpan = document.createElement("span");
          symSpan.className = "transferTreeZoneSymbol";
          symSpan.textContent = sym;
          summary.appendChild(symSpan);
        }
        summary.appendChild(document.createTextNode(zoneNode.name));
        zone.appendChild(summary);

        const body = document.createElement("div");
        body.className = "transferTreeZoneBody";
        renderZoneChildren(zoneNode.children || [], body);
        zone.appendChild(body);

        wrap.appendChild(zone);
        zoneDetails.push(zone);
      }

      treeRoot.appendChild(wrap);
    }

    function renderZoneChildren(nodes, container) {
      for (const node of nodes) {
        if (!node.is_group) {
          container.appendChild(makeLeafButton(node));
          continue;
        }
        // Sub-group (Orbits, Moons, Lagrange, Surface Sites, sub-bodies like Luna, Ceres…)
        const kids = Array.isArray(node.children) ? node.children : [];
        const hasLeaves = kids.some((k) => !k.is_group);
        const hasGroups = kids.some((k) => k.is_group);

        // If this is a body group (Luna, Deimos, Ceres, etc.) that contains both
        // sub-groups and leaves, render it as a mini zone header
        if (hasGroups) {
          const details = document.createElement("details");
          details.className = "transferTreeGroup transferTreeBody";
          details.open = transferTreeOpenState.has(node.id) ? !!transferTreeOpenState.get(node.id) : true;
          details.addEventListener("toggle", () => {
            transferTreeOpenState.set(node.id, !!details.open);
          });

          const summary = document.createElement("summary");
          summary.className = "transferTreeSummary transferTreeBodySummary";
          const sym = locationsById.get(node.id)?.symbol || "";
          if (sym) {
            const symSpan = document.createElement("span");
            symSpan.className = "transferTreeBodySymbol";
            symSpan.textContent = sym;
            summary.appendChild(symSpan);
          }
          summary.appendChild(document.createTextNode(node.name));
          details.appendChild(summary);

          const inner = document.createElement("div");
          inner.className = "transferTreeChildren";
          renderZoneChildren(kids, inner);
          details.appendChild(inner);
          container.appendChild(details);
          continue;
        }

        // Pure leaf group (e.g. "Orbits" with only orbit leaves)
        if (hasLeaves) {
          const details = document.createElement("details");
          details.className = "transferTreeGroup transferTreeCategory";
          details.open = transferTreeOpenState.has(node.id) ? !!transferTreeOpenState.get(node.id) : true;
          details.addEventListener("toggle", () => {
            transferTreeOpenState.set(node.id, !!details.open);
          });

          const summary = document.createElement("summary");
          summary.className = "transferTreeSummary transferTreeCatSummary";
          summary.appendChild(document.createTextNode(node.name));
          details.appendChild(summary);

          const inner = document.createElement("div");
          inner.className = "transferTreeChildren";
          for (const kid of kids) {
            if (!kid.is_group) {
              inner.appendChild(makeLeafButton(kid));
            } else {
              renderZoneChildren([kid], inner);
            }
          }
          details.appendChild(inner);
          container.appendChild(details);
          continue;
        }

        // Empty group — skip
      }
    }

    function makeLeafButton(node) {
      const btn = document.createElement("button");
      btn.className = `transferTreeLeaf${selectedDest === node.id ? " isSelected" : ""}`;
      btn.type = "button";
      btn.textContent = node.name;
      btn.onclick = () => selectLeaf(node);
      return btn;
    }

    // ── Fetch & render quote ───────────────────────────────
    async function fetchAndRenderQuote() {
      if (!selectedDest) return;

      const depTime = departureTimeOverride != null ? departureTimeOverride : serverNow();
      const params = new URLSearchParams({
        from_id: ship.location_id,
        to_id: selectedDest,
        departure_time: String(depTime),
        extra_dv_fraction: String(currentExtraDvFraction),
      });

      detailPanel.innerHTML = `<div class="tpSection"><div class="muted" style="text-align:center; padding:12px;">Loading transfer data…</div></div>`;

      try {
        const resp = await fetch(`/api/transfer_quote_advanced?${params}`, { cache: "no-store" });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          detailPanel.innerHTML = `<div class="tpSection"><div class="muted">${_esc(err.detail || "No transfer data available.")}</div></div>`;
          return;
        }
        const q = await resp.json();
        lastQuote = q;
        renderQuoteDetails(q, depTime);
      } catch (err) {
        console.error(err);
        detailPanel.innerHTML = `<div class="tpSection"><div class="muted">Failed to load transfer data.</div></div>`;
      }
    }

    function renderQuoteDetails(q, depTime) {
      const destName = locationsById.get(q.to_id)?.name || q.to_id;
      const path = q.path || [];
      const fuelNeedKg = computeFuelNeededKg(ship.dry_mass_kg, ship.fuel_kg, ship.isp_s, q.dv_m_s);
      const fuelAfterKg = Math.max(0, shipFuel - fuelNeedKg);
      const fuelAfterPct = shipFuelCap > 0 ? Math.round((fuelAfterKg / shipFuelCap) * 100) : 0;
      const hasFuel = fuelNeedKg <= shipFuel + 0.1;
      const hasDv = q.dv_m_s <= shipDv + 0.1;

      const evaluateSurfaceTwr = () => {
        const surfaceSites = Array.isArray(q.surface_sites) ? q.surface_sites : [];
        if (!surfaceSites.length) return { ok: true };
        const thrustKn = Number(ship.thrust_kn || 0);
        const wetMassKg = Number(ship.dry_mass_kg || 0) + Number(ship.fuel_kg || 0);
        const thrustN = thrustKn * 1000;

        if (!(wetMassKg > 0) || !(thrustN > 0)) {
          const site = surfaceSites.find((s) => Number(s?.gravity_m_s2 || 0) > 0) || surfaceSites[0] || null;
          return {
            ok: false,
            siteId: String(site?.location_id || "surface site"),
            gravity: Number(site?.gravity_m_s2 || 0),
            twr: 0,
          };
        }

        for (const site of surfaceSites) {
          const gravity = Number(site?.gravity_m_s2 || 0);
          if (!(gravity > 0)) continue;
          const twr = thrustN / (wetMassKg * gravity);
          if (twr < 1.0) {
            return {
              ok: false,
              siteId: String(site.location_id || "surface site"),
              gravity,
              twr,
            };
          }
        }
        return { ok: true };
      };
      const twrCheck = evaluateSurfaceTwr();
      const hasSurfaceTwr = twrCheck.ok;

      // Overheating check
      const pb = ship.power_balance;
      const wasteSurplus = pb ? Number(pb.waste_heat_surplus_mw || 0) : 0;
      const isOverheating = wasteSurplus > 0;

      // Build path display
      let pathHtml = "";
      for (let i = 0; i < path.length; i++) {
        const nid = path[i];
        const nname = locationsById.get(nid)?.name || nid;
        let cls = "tpPathNode";
        if (i === 0) cls += " tpPathOrigin";
        else if (i === path.length - 1) cls += " tpPathDest";
        pathHtml += `<span class="${cls}">${_esc(nname)}</span>`;
        if (i < path.length - 1) pathHtml += `<span class="tpPathArrow">▸</span>`;
      }

      // Orbital alignment section
      let orbitalHtml = "";
      if (q.is_interplanetary && q.orbital) {
        const orb = q.orbital;
        const alignCls = alignmentClass(orb.alignment_pct);
        const alignLbl = alignmentLabel(orb.alignment_pct);
        const synodicDays = orb.synodic_period_s ? (orb.synodic_period_s / 86400).toFixed(0) : "—";
        const nextWindowDays = orb.next_window_s ? (orb.next_window_s / 86400).toFixed(0) : "—";

        orbitalHtml = `
          <div class="tpSection">
            <div class="tpSectionTitle">Orbital Alignment</div>
            <div class="tpRow">
              <span class="tpLabel">Window quality</span>
              <span class="tpVal"><span class="tpAlignBadge ${alignCls}">${alignLbl}</span></span>
            </div>
            <div class="tpRow">
              <span class="tpLabel">Phase angle</span>
              <span class="tpVal">${orb.phase_angle_deg}° <span class="muted">(optimal ${orb.optimal_phase_deg}°)</span></span>
            </div>
            <div class="tpRow">
              <span class="tpLabel">Synodic period</span>
              <span class="tpVal">${synodicDays} days</span>
            </div>
            <div class="tpRow">
              <span class="tpLabel">Next optimal window</span>
              <span class="tpVal">${nextWindowDays !== "—" ? nextWindowDays + " days" : "—"}</span>
            </div>
          </div>
          <div id="tpPorkchopContainer" class="tpSection">
            <div class="tpSectionTitle">Porkchop Plot — Departure Window Map</div>
            <div id="tpPorkchopContent" style="position:relative;min-height:60px;">
              <div class="muted" style="text-align:center;padding:12px;">Loading porkchop plot…</div>
            </div>
          </div>
        `;
      }

      // TOF slider section — replaces the old burn profile.
      // For interplanetary transfers, the actual Δv shown comes from the
      // porkchop grid at the selected TOF.  For local transfers we just
      // show the Lambert/Hohmann result.
      const tofDays = Math.round(q.base_tof_s / 86400);

      const html = `
        <!-- Departure Date -->
        <div class="tpSection" style="display:none">
          <div class="tpSectionTitle">Departure</div>
          <div class="tpDateRow">
            <button class="tpDateBtn ${departureTimeOverride == null ? 'active' : ''}" id="tpDateNowBtn">Now</button>
            <input type="datetime-local" class="tpDateInput" id="tpDateInput"
                   value="${gameTimeToISOInput(depTime)}"
                   title="Set departure date (game time)">
            <span class="muted" style="font-size:11px;">${fmtGameDate(depTime)}</span>
          </div>
        </div>

        <!-- Route Overview -->
        <div class="tpSection" style="display:none">
          <div class="tpSectionTitle">Route — ${_esc(ship.location_id)} → ${_esc(destName)}</div>
          <div class="tpPathWrap">${pathHtml}</div>
          <div style="margin-top:8px;">
            <div class="tpRow">
              <span class="tpLabel">Lambert Δv</span>
              <span class="tpVal">${Math.round(q.base_dv_m_s)} m/s</span>
            </div>
            ${q.is_interplanetary ? `<div class="tpRow">
              <span class="tpLabel">Transfer Δv</span>
              <span class="tpVal">${Math.round(q.phase_adjusted_dv_m_s)} m/s</span>
            </div>` : ""}
            <div class="tpRow">
              <span class="tpLabel">Hohmann transit time</span>
              <span class="tpVal">${fmtDuration(q.base_tof_s)}</span>
            </div>
          </div>
        </div>

        ${orbitalHtml}

        <!-- Transfer — TOF slider + live Δv readout -->
        <div class="tpSection">
          <div class="tpSectionTitle">Transfer</div>
          ${q.is_interplanetary ? `
          <div class="tpSliderWrap">
            <div class="tpRow">
              <span class="tpLabel">Time of flight</span>
              <span class="tpVal tpAccent" id="tpTofReadout">${tofDays} days</span>
            </div>
            <div class="tpSliderRow">
              <span class="muted" style="font-size:10px;" id="tpTofMinLabel">—</span>
              <input type="range" class="tpSlider" id="tpTofSlider"
                     min="0" max="100" step="1" value="50" disabled
                     title="Adjust after porkchop loads">
              <span class="muted" style="font-size:10px;" id="tpTofMaxLabel">—</span>
            </div>
          </div>` : ""}
          <div style="margin-top:10px;">
            <div class="tpRow tpHighlight">
              <span class="tpLabel"><b>Total Δv</b></span>
              <span class="tpVal ${hasDv ? 'tpPositive' : 'tpNegative'}" id="tpTotalDvReadout"><b>${Math.round(q.dv_m_s)} m/s</b></span>
            </div>
            <div class="tpRow">
              <span class="tpLabel">Transit time</span>
              <span class="tpVal" id="tpTransitTimeReadout">${fmtDuration(q.tof_s)}</span>
            </div>
          </div>
        </div>

        <!-- Ship Cost -->
        <div class="tpSection">
          <div class="tpSectionTitle">Ship Cost</div>
          <div class="tpRow">
            <span class="tpLabel">Fuel required</span>
            <span class="tpVal ${hasFuel ? '' : 'tpNegative'}" id="tpFuelRequired">${fmtKg(fuelNeedKg)}</span>
          </div>
          <div class="tpRow">
            <span class="tpLabel">Fuel remaining after</span>
            <span class="tpVal ${fuelAfterPct > 20 ? '' : fuelAfterPct > 0 ? 'tpWarn' : 'tpNegative'}" id="tpFuelRemaining">${fmtKg(fuelAfterKg)} (${fuelAfterPct}%)</span>
          </div>
          <div class="tpRow">
            <span class="tpLabel">Ship Δv remaining</span>
            <span class="tpVal" id="tpShipDvRemaining">${Math.round(shipDv)} m/s</span>
          </div>
          <div id="tpCostStatus">
          ${!hasDv ? `<div class="tpRow"><span class="tpLabel"></span><span class="tpVal tpNegative">Insufficient Δv (need ${Math.round(q.dv_m_s)}, have ${Math.round(shipDv)})</span></div>` : ""}
          ${!hasFuel && hasDv ? `<div class="tpRow"><span class="tpLabel"></span><span class="tpVal tpNegative">Insufficient fuel</span></div>` : ""}
          ${!hasSurfaceTwr ? `<div class="tpRow"><span class="tpLabel"></span><span class="tpVal tpNegative">Insufficient surface TWR for ${_esc(twrCheck.siteId || "surface site")} (TWR ${Number(twrCheck.twr || 0).toFixed(2)} &lt; 1.00 at ${Number(twrCheck.gravity || 0).toFixed(2)} m/s²)</span></div>` : ""}
          </div>
        </div>

        ${isOverheating ? `
        <div class="tpSection">
          <div class="pbOverheatBanner" style="margin-top:0;">
            <span class="pbOverheatIcon">⚠</span>
            <span class="pbOverheatText">OVERHEATING — ${wasteSurplus.toFixed(1)} MWth of unradiated waste heat. Ship cannot transfer until thermal balance is resolved. Add radiators or remove generators.</span>
          </div>
        </div>` : ""}

        <!-- Actions -->
        <div class="tpActions">
          <button id="tpCancelBtn" class="btnSecondary">Cancel</button>
          ${window.gameAuth && window.gameAuth.user && window.gameAuth.user.is_admin ? `<button id="tpTeleportBtn" class="btnSecondary" style="background:rgba(255,140,0,0.15);border-color:rgba(255,140,0,0.5);color:#ffa500;">⚡ Teleport</button>` : ""}
          <button id="tpConfirmBtn" class="btnPrimary" ${hasDv && hasFuel && hasSurfaceTwr && !isOverheating ? "" : "disabled"}>${isOverheating ? "Overheating" : "Confirm Transfer"}</button>
        </div>
      `;

      detailPanel.innerHTML = html;

      // Wire up controls
      document.getElementById("tpCancelBtn").onclick = closeModal;

      // Teleport button (admin only)
      const tpTeleportBtn = document.getElementById("tpTeleportBtn");
      if (tpTeleportBtn) {
        tpTeleportBtn.onclick = async () => {
          if (!selectedDest) return;
          tpTeleportBtn.disabled = true;
          tpTeleportBtn.textContent = "Teleporting…";
          try {
            const resp = await fetch(`/api/admin/ships/${encodeURIComponent(ship.id)}/teleport`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ to_location_id: selectedDest }),
            });
            if (!resp.ok) {
              const data = await resp.json().catch(() => ({}));
              tpTeleportBtn.textContent = _esc(data.detail || "Teleport failed");
              tpTeleportBtn.disabled = false;
              return;
            }
            closeModal();
            await syncState();
            showShipPanel();
          } catch (err) {
            tpTeleportBtn.textContent = "Teleport failed";
            tpTeleportBtn.disabled = false;
          }
        };
      }

      // Date controls
      document.getElementById("tpDateNowBtn").onclick = () => {
        departureTimeOverride = null;
        fetchAndRenderQuote();
      };

      document.getElementById("tpDateInput").onchange = (e) => {
        const gt = isoInputToGameTime(e.target.value);
        if (gt != null) {
          departureTimeOverride = gt;
          fetchAndRenderQuote();
        }
      };

      // Auto-fetch porkchop for interplanetary transfers
      if (q.is_interplanetary && q.orbital) {
        fetchAndRenderPorkchop(q, depTime);
      }

      // Confirm button
      document.getElementById("tpConfirmBtn").onclick = async () => {
        if (!selectedDest) return;
        const btn = document.getElementById("tpConfirmBtn");
        btn.disabled = true;
        btn.textContent = "Executing…";
        try {
          const resp = await fetch(`/api/ships/${encodeURIComponent(ship.id)}/transfer`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ to_location_id: selectedDest }),
          });
          if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            btn.textContent = _esc(data.detail || "Transfer failed.");
            btn.disabled = false;
            return;
          }
          closeModal();
          await syncState();
          showShipPanel();
        } catch (err) {
          btn.textContent = "Transfer failed";
          btn.disabled = false;
        }
      };
    }

    // ── Porkchop plot ──────────────────────────────────────
    async function fetchAndRenderPorkchop(q, depTime) {
      const container = document.getElementById("tpPorkchopContainer");
      const content = document.getElementById("tpPorkchopContent");
      if (!container || !content) return;

      container.style.display = "";
      content.innerHTML = `<div class="muted" style="text-align:center;padding:16px;">Computing porkchop plot…</div>`;

      try {
        const params = new URLSearchParams({
          from_id: ship.location_id,
          to_id: selectedDest,
          departure_start: String(depTime),
          grid_size: "50",
        });
        const resp = await fetch(`/api/transfer/porkchop?${params}`, { cache: "no-store" });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          content.innerHTML = `<div class="muted" style="text-align:center;padding:12px;">${_esc(err.detail || "Failed to compute porkchop plot.")}</div>`;
          return;
        }
        const data = await resp.json();
        lastPorkchopData = data;
        lastPorkchopDepTime = depTime;
        renderPorkchopPlot(content, data, depTime);
        wireTofSlider(data, depTime);
      } catch (err) {
        console.error("Porkchop error:", err);
        content.innerHTML = `<div class="muted" style="text-align:center;padding:12px;">Failed to load porkchop data.</div>`;
      }
    }

    function renderPorkchopPlot(container, data, currentDep) {
      const depTimes = data.departure_times || [];
      const tofs = data.tof_values || [];
      const grid = data.dv_grid || [];
      const best = data.best_solutions || [];
      const gs = grid.length;
      if (gs === 0 || tofs.length === 0) {
        container.innerHTML = `<div class="muted" style="text-align:center;padding:12px;">No data returned.</div>`;
        return;
      }

      // Find dv range for color mapping
      let dvMin = Infinity, dvMax = 0;
      for (let di = 0; di < gs; di++) {
        for (let ti = 0; ti < (grid[di] || []).length; ti++) {
          const v = grid[di][ti];
          if (v != null && isFinite(v)) {
            if (v < dvMin) dvMin = v;
            if (v > dvMax) dvMax = v;
          }
        }
      }
      if (dvMin >= dvMax) dvMax = dvMin + 1000;

      // Cap the color range at 3× minimum to avoid washing out
      const dvColorMax = Math.min(dvMax, dvMin * 3.0);

      // Canvas dimensions
      const MARGIN_L = 70, MARGIN_B = 65, MARGIN_T = 10, MARGIN_R = 80;
      const cellW = 6, cellH = 6;
      const plotW = gs * cellW, plotH = tofs.length * cellH;
      const canvasW = MARGIN_L + plotW + MARGIN_R;
      const canvasH = MARGIN_T + plotH + MARGIN_B;

      container.innerHTML = `
        <canvas id="tpPorkchopCanvas" width="${canvasW}" height="${canvasH}" style="width:100%;max-width:${canvasW}px;image-rendering:pixelated;cursor:crosshair;"></canvas>
        <div id="tpPorkchopTooltip" class="tpPorkchopTip" style="display:none;"></div>
        <div id="tpPorkchopBest" style="margin-top:8px;"></div>
      `;

      const canvas = document.getElementById("tpPorkchopCanvas");
      const ctx = canvas.getContext("2d");

      // ── Draw the static base image ──
      function drawBase() {
        // Background
        ctx.fillStyle = "rgba(4, 8, 14, 0.95)";
        ctx.fillRect(0, 0, canvasW, canvasH);

        // Heatmap cells
        for (let di = 0; di < gs; di++) {
          for (let ti = 0; ti < (grid[di] || []).length; ti++) {
            const v = grid[di][ti];
            const x = MARGIN_L + di * cellW;
            const y = MARGIN_T + (tofs.length - 1 - ti) * cellH;
            if (v == null) {
              ctx.fillStyle = "rgba(20, 20, 30, 0.8)";
            } else {
              const t = Math.max(0, Math.min(1, (v - dvMin) / (dvColorMax - dvMin)));
              ctx.fillStyle = porkchopColor(t);
            }
            ctx.fillRect(x, y, cellW, cellH);
          }
        }

        // Best solution markers
        for (const sol of best) {
          const di = depTimes.findIndex((t, i) => i === depTimes.length - 1 || depTimes[i + 1] > sol.departure_time);
          const ti = tofs.findIndex((t, i) => i === tofs.length - 1 || tofs[i + 1] > sol.tof_s);
          if (di >= 0 && ti >= 0) {
            const x = MARGIN_L + di * cellW + cellW / 2;
            const y = MARGIN_T + (tofs.length - 1 - ti) * cellH + cellH / 2;
            ctx.beginPath();
            ctx.arc(x, y, 5, 0, 2 * Math.PI);
            ctx.strokeStyle = "#fff";
            ctx.lineWidth = 1.5;
            ctx.stroke();
            ctx.beginPath();
            ctx.arc(x, y, 2, 0, 2 * Math.PI);
            ctx.fillStyle = "#fff";
            ctx.fill();
          }
        }

        // Current departure time vertical line (clamped so it always appears)
        if (depTimes.length >= 2) {
          const frac = Math.max(0, Math.min(1,
            (currentDep - depTimes[0]) / (depTimes[depTimes.length - 1] - depTimes[0])
          ));
          const cx = MARGIN_L + frac * plotW;
          ctx.strokeStyle = "rgba(89,185,230,0.6)";
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(cx, MARGIN_T);
          ctx.lineTo(cx, MARGIN_T + plotH);
          ctx.stroke();
          ctx.setLineDash([]);
        }

        // Axes
        ctx.strokeStyle = "rgba(109,182,255,0.3)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(MARGIN_L, MARGIN_T);
        ctx.lineTo(MARGIN_L, MARGIN_T + plotH);
        ctx.lineTo(MARGIN_L + plotW, MARGIN_T + plotH);
        ctx.stroke();

        // X axis labels
        ctx.fillStyle = "rgba(163,184,203,0.7)";
        ctx.font = "9px sans-serif";
        ctx.textAlign = "center";
        const xLabels = 5;
        for (let i = 0; i <= xLabels; i++) {
          const idx = Math.round((i / xLabels) * (gs - 1));
          const t = depTimes[idx];
          const x = MARGIN_L + idx * cellW + cellW / 2;
          const dateStr = fmtGameDate(t).slice(0, 10);
          ctx.fillText(dateStr, x, MARGIN_T + plotH + 14);
        }
        ctx.save();
        ctx.translate(MARGIN_L + plotW / 2, MARGIN_T + plotH + 40);
        ctx.textAlign = "center";
        ctx.font = "10px sans-serif";
        ctx.fillStyle = "rgba(163,184,203,0.6)";
        ctx.fillText("Departure Date", 0, 0);
        ctx.restore();

        // Y axis labels
        ctx.textAlign = "right";
        ctx.font = "9px sans-serif";
        ctx.fillStyle = "rgba(163,184,203,0.7)";
        const yLabels = 5;
        for (let i = 0; i <= yLabels; i++) {
          const idx = Math.round((i / yLabels) * (tofs.length - 1));
          const tofDays = (tofs[idx] / 86400).toFixed(0);
          const y = MARGIN_T + (tofs.length - 1 - idx) * cellH + cellH / 2;
          ctx.fillText(`${tofDays}d`, MARGIN_L - 6, y + 3);
        }
        ctx.save();
        ctx.translate(12, MARGIN_T + plotH / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.textAlign = "center";
        ctx.font = "10px sans-serif";
        ctx.fillStyle = "rgba(163,184,203,0.6)";
        ctx.fillText("Time of Flight", 0, 0);
        ctx.restore();

        // Color bar
        const barX = MARGIN_L + plotW + 12, barW = 14, barH = plotH;
        for (let i = 0; i < barH; i++) {
          const t = i / barH;
          ctx.fillStyle = porkchopColor(t);
          ctx.fillRect(barX, MARGIN_T + i, barW, 1);
        }
        ctx.strokeStyle = "rgba(109,182,255,0.3)";
        ctx.strokeRect(barX, MARGIN_T, barW, barH);
        ctx.fillStyle = "rgba(163,184,203,0.7)";
        ctx.font = "9px sans-serif";
        ctx.textAlign = "left";
        ctx.fillText(`${(dvMin / 1000).toFixed(1)}`, barX + barW + 4, MARGIN_T + barH + 3);
        ctx.fillText(`${(dvColorMax / 1000).toFixed(1)}`, barX + barW + 4, MARGIN_T + 9);
        ctx.fillText("km/s", barX + barW + 4, MARGIN_T + barH / 2 + 3);
      }

      drawBase();

      // Save the base image so we can redraw crosshairs without re-rendering
      const baseImageData = ctx.getImageData(0, 0, canvasW, canvasH);

      // ── Crosshair drawing (for TOF slider) ──
      porkchopRedrawCrosshair = function(tofS) {
        ctx.putImageData(baseImageData, 0, 0);
        if (tofS == null) return;
        const tofMin = tofs[0], tofMax = tofs[tofs.length - 1];
        if (tofS < tofMin || tofS > tofMax) return;
        const frac = (tofS - tofMin) / (tofMax - tofMin);
        const cy = MARGIN_T + (1 - frac) * plotH; // TOF increases upward
        ctx.strokeStyle = "rgba(68, 224, 255, 0.7)";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(MARGIN_L, cy);
        ctx.lineTo(MARGIN_L + plotW, cy);
        ctx.stroke();
        ctx.setLineDash([]);

        // Draw a diamond at the intersection with the departure line
        // Clamp to range so it always appears even if currentDep is slightly outside
        if (depTimes.length >= 2) {
          const depFrac = Math.max(0, Math.min(1,
            (currentDep - depTimes[0]) / (depTimes[depTimes.length - 1] - depTimes[0])
          ));
          const cx = MARGIN_L + depFrac * plotW;
          const sz = 6;
          // Dark outline for contrast against any heatmap color
          ctx.lineWidth = 2.5;
          ctx.strokeStyle = "rgba(0, 0, 0, 0.8)";
          ctx.beginPath();
          ctx.moveTo(cx, cy - sz);
          ctx.lineTo(cx + sz, cy);
          ctx.lineTo(cx, cy + sz);
          ctx.lineTo(cx - sz, cy);
          ctx.closePath();
          ctx.stroke();
          // Bright fill
          ctx.fillStyle = "#44e0ff";
          ctx.fill();
          // Thin bright border on top
          ctx.lineWidth = 1;
          ctx.strokeStyle = "rgba(255, 255, 255, 0.7)";
          ctx.stroke();
        }
      };

      // ── Canvas hover tooltip ──
      const tooltip = document.getElementById("tpPorkchopTooltip");
      canvas.onmousemove = (e) => {
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const mx = (e.clientX - rect.left) * scaleX;
        const my = (e.clientY - rect.top) * scaleX;
        const di = Math.floor((mx - MARGIN_L) / cellW);
        const ti = tofs.length - 1 - Math.floor((my - MARGIN_T) / cellH);
        if (di < 0 || di >= gs || ti < 0 || ti >= tofs.length) {
          tooltip.style.display = "none";
          return;
        }
        const v = (grid[di] || [])[ti];
        const depDate = fmtGameDate(depTimes[di]).slice(0, 10);
        const tofDays = (tofs[ti] / 86400).toFixed(0);
        const dvStr = v != null ? `${(v / 1000).toFixed(2)} km/s` : "N/A";
        tooltip.style.display = "";
        tooltip.style.left = (e.clientX - canvas.closest(".tpSection").getBoundingClientRect().left + 14) + "px";
        tooltip.style.top = (e.clientY - canvas.closest(".tpSection").getBoundingClientRect().top - 10) + "px";
        tooltip.innerHTML = `<b>Δv ${dvStr}</b><br>Depart: ${depDate}<br>TOF: ${tofDays} days`;
      };
      canvas.onmouseleave = () => { tooltip.style.display = "none"; };

      // Best solutions table
      const bestEl = document.getElementById("tpPorkchopBest");
      if (best.length > 0) {
        let rows = best.map((s, i) => `
          <tr>
            <td>${i + 1}</td>
            <td>${fmtGameDate(s.departure_time).slice(0, 10)}</td>
            <td>${(s.tof_s / 86400).toFixed(0)}d</td>
            <td><b>${(s.dv_m_s / 1000).toFixed(2)}</b></td>
            <td>${(s.dv_depart_m_s / 1000).toFixed(2)}</td>
            <td>${(s.dv_arrive_m_s / 1000).toFixed(2)}</td>
            <td>${s.v_inf_depart_km_s.toFixed(2)}</td>
          </tr>
        `).join("");
        bestEl.innerHTML = `
          <div class="tpSectionTitle" style="margin-top:4px;">Best Transfer Windows</div>
          <table class="tpPorkchopTable">
            <thead><tr>
              <th>#</th><th>Depart</th><th>TOF</th><th>Δv (km/s)</th><th>Dep Δv</th><th>Arr Δv</th><th>V∞ Dep</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        `;
      }
    }

    // ── Wire TOF slider to porkchop data ───────────────────
    function wireTofSlider(data, depTime) {
      const slider = document.getElementById("tpTofSlider");
      const readout = document.getElementById("tpTofReadout");
      const dvReadout = document.getElementById("tpTotalDvReadout");
      const transitReadout = document.getElementById("tpTransitTimeReadout");
      const tofMinLabel = document.getElementById("tpTofMinLabel");
      const tofMaxLabel = document.getElementById("tpTofMaxLabel");
      if (!slider || !readout) return;

      const tofs = data.tof_values || [];
      const depTimes = data.departure_times || [];
      const grid = data.dv_grid || [];
      if (tofs.length === 0 || depTimes.length === 0) return;

      const tofMinS = tofs[0];
      const tofMaxS = tofs[tofs.length - 1];

      // Set slider range (use index = 0..tofs.length-1)
      slider.min = "0";
      slider.max = String(tofs.length - 1);
      slider.step = "1";
      slider.disabled = false;

      if (tofMinLabel) tofMinLabel.textContent = `${Math.round(tofMinS / 86400)}d`;
      if (tofMaxLabel) tofMaxLabel.textContent = `${Math.round(tofMaxS / 86400)}d`;

      // Find nearest departure column to current dep time
      let bestDepIdx = 0;
      let bestDepDist = Infinity;
      for (let i = 0; i < depTimes.length; i++) {
        const dist = Math.abs(depTimes[i] - depTime);
        if (dist < bestDepDist) { bestDepDist = dist; bestDepIdx = i; }
      }

      // Find the best-Δv TOF index at this departure column
      let bestTofIdx = Math.floor(tofs.length / 2);
      let bestDv = Infinity;
      const depCol = grid[bestDepIdx] || [];
      for (let ti = 0; ti < depCol.length; ti++) {
        const v = depCol[ti];
        if (v != null && isFinite(v) && v < bestDv) {
          bestDv = v;
          bestTofIdx = ti;
        }
      }

      slider.value = String(bestTofIdx);

      function updateFromSlider() {
        const ti = Number(slider.value);
        const tofS = tofs[ti];
        const tofDays = Math.round(tofS / 86400);
        readout.textContent = `${tofDays} days`;

        // Look up Δv at (bestDepIdx, ti) from the grid
        const dv = (grid[bestDepIdx] || [])[ti];
        if (dv != null && isFinite(dv)) {
          const dvM = Math.round(dv);
          if (dvReadout) dvReadout.innerHTML = `<b>${dvM} m/s</b>`;
          // Update fuel/feasibility class
          if (dvReadout) {
            dvReadout.className = dvM <= shipDv + 0.1 ? "tpVal tpPositive" : "tpVal tpNegative";
          }
        } else {
          if (dvReadout) { dvReadout.innerHTML = `<b>N/A</b>`; dvReadout.className = "tpVal muted"; }
        }

        if (transitReadout) transitReadout.textContent = fmtDuration(tofS);

        // Update ship cost section with new Δv
        if (dv != null && isFinite(dv)) updateShipCostFromDv(dv);

        // Redraw crosshair on porkchop
        if (porkchopRedrawCrosshair) porkchopRedrawCrosshair(tofS);
      }

      slider.oninput = updateFromSlider;
      // Trigger initial update
      updateFromSlider();
    }

    // ── Update ship cost section from a given Δv ──────────
    function updateShipCostFromDv(dvMs) {
      const fuelReqEl = document.getElementById("tpFuelRequired");
      const fuelRemEl = document.getElementById("tpFuelRemaining");
      const statusEl = document.getElementById("tpCostStatus");
      const confirmBtn = document.getElementById("tpConfirmBtn");
      if (!fuelReqEl || !fuelRemEl) return;

      const fuelNeed = computeFuelNeededKg(ship.dry_mass_kg, ship.fuel_kg, ship.isp_s, dvMs);
      const fuelAfter = Math.max(0, shipFuel - fuelNeed);
      const fuelAfterP = shipFuelCap > 0 ? Math.round((fuelAfter / shipFuelCap) * 100) : 0;
      const okFuel = fuelNeed <= shipFuel + 0.1;
      const okDv = dvMs <= shipDv + 0.1;

      fuelReqEl.textContent = fmtKg(fuelNeed);
      fuelReqEl.className = "tpVal" + (okFuel ? "" : " tpNegative");

      fuelRemEl.textContent = `${fmtKg(fuelAfter)} (${fuelAfterP}%)`;
      fuelRemEl.className = "tpVal" + (fuelAfterP > 20 ? "" : fuelAfterP > 0 ? " tpWarn" : " tpNegative");

      // Rebuild status messages
      if (statusEl) {
        let msgs = "";
        if (!okDv) msgs += `<div class="tpRow"><span class="tpLabel"></span><span class="tpVal tpNegative">Insufficient Δv (need ${Math.round(dvMs)}, have ${Math.round(shipDv)})</span></div>`;
        if (!okFuel && okDv) msgs += `<div class="tpRow"><span class="tpLabel"></span><span class="tpVal tpNegative">Insufficient fuel</span></div>`;
        statusEl.innerHTML = msgs;
      }

      // Enable/disable confirm button
      if (confirmBtn) {
        const canTransfer = okDv && okFuel && !confirmBtn.textContent.includes("Overheating");
        confirmBtn.disabled = !canTransfer;
      }
    }

    function porkchopColor(t) {
      // t: 0 = best (low dv), 1 = worst (high dv)
      // Color scale: dark blue → cyan → green → yellow → red
      t = Math.max(0, Math.min(1, t));
      let r, g, b;
      if (t < 0.25) {
        const f = t / 0.25;
        r = 0; g = Math.round(40 + f * 120); b = Math.round(80 + f * 100);
      } else if (t < 0.5) {
        const f = (t - 0.25) / 0.25;
        r = 0; g = Math.round(160 + f * 80); b = Math.round(180 - f * 130);
      } else if (t < 0.75) {
        const f = (t - 0.5) / 0.25;
        r = Math.round(f * 240); g = Math.round(240 - f * 60); b = Math.round(50 - f * 50);
      } else {
        const f = (t - 0.75) / 0.25;
        r = Math.round(240 + f * 15); g = Math.round(180 - f * 150); b = 0;
      }
      return `rgb(${r},${g},${b})`;
    }

    // ── Select destination leaf ─────────────────────────────
    async function selectLeaf(node) {
      selectedDest = node.id;
      currentExtraDvFraction = 0;
      buildDestinationTree();
      await fetchAndRenderQuote();
    }

    buildDestinationTree();

    if (initialDestId && initialDestId !== ship.location_id) {
      const initialLoc = locationsById.get(initialDestId);
      if (initialLoc && !initialLoc.is_group) {
        selectLeaf({ id: initialLoc.id, name: initialLoc.name || initialLoc.id });
      }
    }
  }

  // ---------- Side panel ----------
  function showShipPanel() {
    const ship = ships.find((s) => s.id === selectedShipId);
    if (!ship) {
      setInfo("Select a ship", "", "", ["Click a ship, then Move to plan a transfer."]);
      actions.innerHTML = "";
      clearRealWorldReference();
      shipInfoTabShipId = null;
      shipInfoTab = "details";
      locationInfoTabLocationId = null;
      locationInfoTab = "details";
      renderShipInfoTabs();
      return;
    }

    locationInfoTabLocationId = null;
    locationInfoTab = "details";

    if (shipInfoTabShipId !== ship.id) {
      shipInfoTabShipId = ship.id;
      shipInfoTab = "details";
    }

    clearRealWorldReference();

    renderShipInfoTabs();

    if (shipInfoTab === "inventory") {
      const inventoryItems = Array.isArray(ship.inventory_items) ? ship.inventory_items : [];
      const capacitySummary = ship.inventory_capacity_summary && typeof ship.inventory_capacity_summary === "object"
        ? ship.inventory_capacity_summary
        : null;

      const subtitle = ship.status === "docked"
        ? `Docked: ${locationsById.get(ship.location_id)?.name || ship.location_id}`
        : `In transit: ${locationsById.get(ship.from_location_id)?.name || ship.from_location_id} → ${locationsById.get(ship.to_location_id)?.name || ship.to_location_id}`;

      setInfo(ship.name, subtitle, `Cargo resources: ${inventoryItems.length}`, []);

      if (infoList) {
        infoList.innerHTML += buildInventoryListHtml(inventoryItems, capacitySummary);
        infoList.innerHTML += buildPartsStackHtml(ship);
        infoList.innerHTML += buildDeltaVPanelHtml(ship);
        infoList.innerHTML += buildPowerBalanceHtml(ship);
        renderShipInventoryGrids(infoList, inventoryItems);
        renderShipPartsGrid(infoList, ship);
        wireInventoryActionButtons(ship);
      }

      actions.innerHTML = "";
      return;
    }

    if (ship.status === "docked") {
      const loc = locationsById.get(ship.location_id);
      setInfo(
        ship.name,
        `Docked: ${loc?.name || ship.location_id}`,
        `Location: ${ship.location_id}`,
        [
          ...(ship.notes || []),
        ]
      );
      if (infoList) {
        infoList.innerHTML += buildPartsStackHtml(ship);
        infoList.innerHTML += buildDeltaVPanelHtml(ship);
        infoList.innerHTML += buildPowerBalanceHtml(ship);
        renderShipPartsGrid(infoList, ship);
      }
      actions.innerHTML = `<button id="moveBtn" class="btnPrimary">Move</button> <button id="deconstructBtn" class="btnSecondary">Deconstruct</button>`;
      document.getElementById("moveBtn").onclick = () => openTransferPlanner(ship);
      document.getElementById("deconstructBtn").onclick = async () => {
        const ok = window.confirm(`Deconstruct ${ship.name} at ${loc?.name || ship.location_id}? This removes the ship and moves parts/resources into local inventory.`);
        if (!ok) return;
        const deconstructBtn = document.getElementById("deconstructBtn");
        if (deconstructBtn) deconstructBtn.disabled = true;
        try {
          const resp = await fetch(`/api/ships/${encodeURIComponent(ship.id)}/deconstruct`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ keep_ship_record: false }),
          });
          if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data?.detail || "Deconstruct failed.");
          }

          selectedShipId = null;
          await syncState();
          setInfo("Select a ship", "", "", ["Click a ship, then Move to plan a transfer."]);
          actions.innerHTML = "";
        } catch (err) {
          console.error(err);
          alert(err?.message || "Deconstruct failed.");
        } finally {
          if (deconstructBtn) deconstructBtn.disabled = false;
          if (!app.ticker.started) app.ticker.start();
        }
      };
      return;
    }

    const A = locationsById.get(ship.from_location_id);
    const B = locationsById.get(ship.to_location_id);
    const eta = ship.arrives_at ? Math.max(0, ship.arrives_at - serverNow()) : null;
    const activeLegInfo = pickActiveTransferLeg(ship, serverNow());
    const activeLeg = activeLegInfo?.leg || null;
    const legFrom = activeLeg ? (locationsById.get(String(activeLeg.from_id))?.name || String(activeLeg.from_id || "")) : "";
    const legTo = activeLeg ? (locationsById.get(String(activeLeg.to_id))?.name || String(activeLeg.to_id || "")) : "";
    const legEta = activeLeg ? Math.max(0, Number(activeLeg.arrival_time || 0) - serverNow()) : null;

    setInfo(
      ship.name,
      `In transit: ${A?.name || ship.from_location_id} → ${B?.name || ship.to_location_id}`,
      eta !== null ? `ETA: ${formatEtaDaysHours(eta)}` : "",
      [
        ship.dv_planned_m_s != null ? `Δv planned: ${Math.round(ship.dv_planned_m_s)} m/s` : "",
        (ship.transfer_path || []).length ? `Path: ${(ship.transfer_path || []).join(" → ")}` : "",
        activeLeg ? `Current leg ${Number(activeLegInfo.index) + 1}/${Number(activeLegInfo.count)}: ${legFrom} → ${legTo}${legEta != null ? ` • ETA ${formatEtaDaysHours(legEta)}` : ""}` : "",
      ].filter(Boolean)
    );
    if (infoList) {
      infoList.innerHTML += buildPartsStackHtml(ship);
      infoList.innerHTML += buildDeltaVPanelHtml(ship);
      infoList.innerHTML += buildPowerBalanceHtml(ship);
      renderShipPartsGrid(infoList, ship);
    }
    actions.innerHTML = `<div class="muted small">(No mid-course changes in alpha.)</div>`;
  }

  // ---------- Sync ----------
  async function syncLocationsOnce(options = {}) {
    const shouldRefit = options.refit !== false;
    const data = await (await fetch("/api/locations?dynamic=1", { cache: "no-store" })).json();
    const projected = projectLocationsForMap(data.locations || []);

    // --- Capture previous positions for smooth interpolation ---
    const prevById = locationsById;          // old map (empty on first call)
    const isFirstLoad = prevById.size === 0;

    locations = projected.locations;
    locationParentById = projected.parentById;
    locationsById = new Map(locations.map((l) => [l.id, l]));
    leaves = locations.filter((l) => !l.is_group);

    // Build lerp entries: from old position → new position
    locLerp.clear();
    if (!isFirstLoad) {
      for (const l of locations) {
        const prev = prevById.get(l.id);
        if (prev && (prev.rx !== l.rx || prev.ry !== l.ry)) {
          locLerp.set(l.id, {
            fromRx: prev.rx, fromRy: prev.ry,
            toRx:   l.rx,   toRy:   l.ry,
          });
          // Start from old position so first frame isn't a jump
          l.rx = prev.rx;
          l.ry = prev.ry;
        }
      }
      locLerpStartMs = performance.now();
    }

    buildPlanets();
    positionPlanets();

    computeOrbitInfo();
    markOrbitsDirty();

    ensureLocationsGfx();
    updateLocationPositions();
    if (shouldRefit) {
      fitToLocations();
    }
    applyZoomDetailVisibility();
    buildZoneJumpBar();
    buildMapOverview(true);
  }

  async function syncState() {
    if (syncStatePromise) return syncStatePromise;

    syncStatePromise = (async () => {
    const tClient = Date.now() / 1000;
    const data = await (await fetch("/api/state", { cache: "no-store" })).json();
    serverSyncGameS = data.server_time || tClient;
    clientSyncRealS = tClient;
    const parsedScale = Number(data.time_scale);
    timeScale = Number.isFinite(parsedScale) && parsedScale >= 0 ? parsedScale : 1;

    ships = data.ships || [];
    ensureTransitAnchorsForShips(ships).catch((err) => console.error("Transit anchor prefetch failed:", err));
    upsertShips(ships);
    applyDockSlots(ships);
    buildMapOverview(true);

    if (infoCoords) {
      infoCoords.textContent = `Loaded: ${leaves.length} locations, ${ships.length} ships • scale=${world.scale.x.toFixed(3)}`;
    }

    if (selectedShipId) showShipPanel();
    })();

    try {
      return await syncStatePromise;
    } finally {
      syncStatePromise = null;
    }
  }

  // ---------- Boot ----------
  await syncLocationsOnce({ refit: true });
  await syncState();
  await syncMapOrgSummary();
  setInterval(() => {
    syncLocationsOnce({ refit: false }).catch((err) => console.error(err));
  }, 5000);
  setInterval(() => {
    syncState().catch((err) => console.error(err));
  }, 1000);
  setInterval(() => {
    syncMapOrgSummary().catch((err) => console.error(err));
  }, 30000);

  setInfo("Select a ship", "", "", ["Click a ship, then Move to plan a transfer."]);
  actions.innerHTML = "";

  // Orbit-center lookup: orbitId → center body id (mirrors computeOrbitInfo defs)
  const ORBIT_CENTER_MAP = new Map([
    ["LEO","grp_earth"],["HEO","grp_earth"],["GEO","grp_earth"],
    ["LLO","grp_moon"],["HLO","grp_moon"],
    ["MERC_ORB","grp_mercury"],["MERC_HEO","grp_mercury"],["MERC_GEO","grp_mercury"],
    ["VEN_ORB","grp_venus"],["VEN_HEO","grp_venus"],["VEN_GEO","grp_venus"],
    ["LMO","grp_mars"],["HMO","grp_mars"],["MGO","grp_mars"],
    ["CERES_LO","grp_ceres"],["CERES_HO","grp_ceres"],
    ["VESTA_LO","grp_vesta"],["VESTA_HO","grp_vesta"],
    ["PALLAS_LO","grp_pallas"],["PALLAS_HO","grp_pallas"],
    ["HYGIEA_LO","grp_hygiea"],["HYGIEA_HO","grp_hygiea"],
    ["JUP_LO","grp_jupiter"],["JUP_HO","grp_jupiter"],
    ["IO_LO","IO"],["IO_HO","IO"],
    ["EUROPA_LO","EUROPA"],["EUROPA_HO","EUROPA"],
    ["GANYMEDE_LO","GANYMEDE"],["GANYMEDE_HO","GANYMEDE"],
    ["CALLISTO_LO","CALLISTO"],["CALLISTO_HO","CALLISTO"],
  ]);

  // --- Smooth celestial lerp (called every frame) ---
  // Linear interpolation with extrapolation past t=1 so movement
  // never visibly pauses between poll intervals.
  function lerpCelestialPositions() {
    if (locLerp.size === 0) return;
    const elapsed = performance.now() - locLerpStartMs;
    // Linear progress — intentionally NOT clamped to 1 so the bodies
    // keep drifting at the same velocity until the next poll overwrites.
    const t = elapsed / LOC_LERP_DURATION_MS;
    for (const [id, lp] of locLerp) {
      const loc = locationsById.get(id);
      if (!loc) continue;
      loc.rx = lp.fromRx + (lp.toRx - lp.fromRx) * t;
      loc.ry = lp.fromRy + (lp.toRy - lp.fromRy) * t;
    }
    // Keep orbit-ring centers in sync with interpolated planet positions
    for (const [orbitId, centerId] of ORBIT_CENTER_MAP) {
      const oi = orbitInfo.get(orbitId);
      const ctr = locationsById.get(centerId);
      if (oi && ctr) { oi.cx = ctr.rx; oi.cy = ctr.ry; }
    }
    // Don't clear — the next syncLocationsOnce() call resets locLerp
  }

  // Main render loop
  app.ticker.add(() => {
    try {
      tickCounter++;
      const hadLerp = locLerp.size > 0;
      lerpCelestialPositions();
      if (hadLerp) markOrbitsDirty();  // celestials moved
      positionPlanets();
      updateLocationPositions();
      renderOrbitRings();              // skips internally when not dirty
      applyZoomDetailVisibility();
      updateShipPositions();
      if (tickCounter % OVERVIEW_EVERY_N === 0) buildMapOverview();
      if (tickCounter % TEXT_CULL_EVERY_N === 0) applyTextCollisionCulling();
    } catch (err) {
      console.error("Main map tick failed:", err);
      if (!app.ticker.started) app.ticker.start();
    }
  });
})();
