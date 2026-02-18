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
    const mapDockEl = document.querySelector(".mapPage .mapDock");
    const mapDockWindowConfig = {
      overview: { icon: "/static/img/dock/map.png" },
      info: { icon: "/static/img/dock/info.png" },
    };

    const layoutState = loadWindowLayoutState();
    const panelRecords = new Map();

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

    if (mapDockEl) {
      Object.entries(mapDockWindowConfig).forEach(([panelId, cfg]) => {
        const btn = mapDockEl.querySelector(`[data-map-window='${panelId}']`);
        bootstrapDockButton(btn, cfg?.icon || "");
      });
    }

    function bringWindowToFront(panelEl) {
      mapWindowZIndex += 1;
      panelEl.style.zIndex = String(mapWindowZIndex);
      content.querySelectorAll(".mapWindow.isSelected").forEach((el) => {
        el.classList.remove("isSelected");
      });
      panelEl.classList.add("isSelected");
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
    if (!target || target.kind !== "location") return;
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

  const locGfx = new Map();   // id -> {dot,label,kind,hovered}
  const shipGfx = new Map();  // id -> {ship,container,slot,phase}
  const shipClusterLabels = new Map(); // location_id -> PIXI.Text
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

    const shipTolWorld = 22 / zoom;
    let bestShip = null;
    let bestShipD2 = Number.POSITIVE_INFINITY;
    for (const [shipId, gfx] of shipGfx.entries()) {
      const c = gfx?.container;
      if (!c || c.visible === false) continue;
      const dx = worldPoint.x - Number(c.x || 0);
      const dy = worldPoint.y - Number(c.y || 0);
      const d2 = dx * dx + dy * dy;
      if (d2 <= shipTolWorld * shipTolWorld && d2 < bestShipD2) {
        bestShip = shipId;
        bestShipD2 = d2;
      }
    }
    if (bestShip) return { kind: "ship", id: bestShip };

    const orbitTolWorld = 12 / zoom;
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

    const locTolWorld = 14 / zoom;
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

    const bodyTolWorld = 18 / zoom;
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
          selectedShipId = ship.id;
          showShipPanel();
        },
      },
    ];

    if (ship.status === "docked") {
      actionsList.push({
        label: "Plan transfer…",
        onClick: () => openTransferPlanner(ship),
      });
    }

    showContextMenu(ship.name || ship.id, actionsList, pt.x, pt.y);
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
        onClick: () => showLocationInfo(loc),
      });
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
        onClick: () => showBodyInfo(bodyId),
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
      ? `
        <div class="shipInvSection">
          <div class="shipInvSectionTitle">Resources</div>
          <table class="shipInvTable">
            <thead><tr><th>Name</th><th>Mass</th><th>Volume</th></tr></thead>
            <tbody>
              ${resources.map((r) => `
                <tr>
                  <td>${String(r?.name || r?.item_id || "Resource")}</td>
                  <td>${Math.max(0, Number(r?.mass_kg) || 0).toFixed(0)} kg</td>
                  <td>${Math.max(0, Number(r?.volume_m3) || 0).toFixed(2)} m³</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      `
      : "";

    const partsHtml = parts.length
      ? `
        <div class="shipInvSection">
          <div class="shipInvSectionTitle">Parts</div>
          <table class="shipInvTable">
            <thead><tr><th>Name</th><th>Count</th><th>Mass</th></tr></thead>
            <tbody>
              ${parts.map((p) => `
                <tr>
                  <td>${String(p?.name || p?.item_id || "Part")}</td>
                  <td>${Math.max(0, Number(p?.quantity) || 0).toFixed(0)}</td>
                  <td>${Math.max(0, Number(p?.mass_kg) || 0).toFixed(0)} kg</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      `
      : "";

    return `<li><div class="shipInvRoot">${resourcesHtml}${partsHtml}</div></li>`;
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
      if (infoList) infoList.innerHTML = buildLocationInventoryHtml(data);
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
    if (!ordered.length) return '<li>Parts: —</li>';
    const rows = ordered
      .map((part, idx) => `<div class="partsStackRow"><span class="partsStackCell partsStackIndex">${idx + 1}</span><span class="partsStackCell">${partLabel(part)}</span></div>`)
      .join("");
    return `<li><div><b>Parts</b></div><div class="partsStack"><div class="partsStackHead"><span class="partsStackCell partsStackIndex">#</span><span class="partsStackCell">Part</span></div>${rows}</div></li>`;
  }

  function fmtM3(v) {
    return `${(Math.max(0, Number(v) || 0)).toFixed(2)} m³`;
  }

  function fmtKg(v) {
    return `${(Math.max(0, Number(v) || 0)).toFixed(0)} kg`;
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

  function buildInventoryGroups(containers) {
    const out = { solid: [], liquid: [], gas: [] };
    for (const c of containers || []) {
      const phase = String(c?.phase || "solid").toLowerCase();
      if (phase === "liquid") out.liquid.push(c);
      else if (phase === "gas") out.gas.push(c);
      else out.solid.push(c);
    }
    return out;
  }

  function buildInventoryListHtml(containers) {
    if (!Array.isArray(containers) || !containers.length) {
      return `<li><div class="muted">No cargo containers found on this ship.</div></li>`;
    }

    const groups = buildInventoryGroups(containers);
    const labels = [
      ["solid", "Solid Containers"],
      ["liquid", "Liquid Containers"],
      ["gas", "Gas Containers"],
    ];

    const sectionHtml = labels.map(([key, title]) => {
      const rows = groups[key] || [];
      if (!rows.length) return "";

      const tableRows = rows.map((c) => {
        const idx = Number(c.container_index);
        const used = Math.max(0, Number(c.used_m3) || 0);
        const cap = Math.max(0, Number(c.capacity_m3) || 0);
        const mass = Math.max(0, Number(c.total_mass_kg) || 0);
        const cargoMass = Math.max(0, Number(c.cargo_mass_kg) || 0);
        const name = String(c.name || `Container ${idx + 1}`);
        const resourceName = String(c.resource_name || c.resource_id || "Unspecified");
        return `
          <tr>
            <td>
              <div>${name} · ${resourceName}</div>
            </td>
            <td>${fmtM3(used)} / ${fmtM3(cap)}</td>
            <td>${fmtKg(mass)}</td>
            <td class="shipInvActionsCell">
              <button type="button" class="btnSecondary shipInvActionBtn" data-inv-action="jettison" data-container-index="${idx}" ${cargoMass <= 0 ? "disabled" : ""}>Jettison</button>
              <button type="button" class="btnSecondary shipInvActionBtn" data-inv-action="deploy" data-container-index="${idx}">Deploy Container</button>
            </td>
          </tr>
        `;
      }).join("");

      return `
        <div class="shipInvSection">
          <div class="shipInvSectionTitle">${title}</div>
          <table class="shipInvTable">
            <thead>
              <tr>
                <th>Container</th>
                <th>In Use</th>
                <th>Total Mass</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>${tableRows}</tbody>
          </table>
        </div>
      `;
    }).join("");

    return `<li><div class="shipInvRoot">${sectionHtml || '<div class="muted">No cargo containers found on this ship.</div>'}</div></li>`;
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
    if (!infoList || !ship) return;
    const buttons = infoList.querySelectorAll(".shipInvActionBtn[data-inv-action][data-container-index]");
    buttons.forEach((btn) => {
      btn.addEventListener("click", async () => {
        const action = String(btn.getAttribute("data-inv-action") || "").toLowerCase();
        const containerIndex = Number(btn.getAttribute("data-container-index") || 0);
        const isDeploy = action === "deploy";
        if (isDeploy) {
          const ok = window.confirm("Deploy this container with its cargo?");
          if (!ok) return;
        }

        btn.disabled = true;
        try {
          await runInventoryAction(ship.id, containerIndex, isDeploy ? "deploy" : "jettison");
          await syncState();
          showShipPanel();
        } catch (err) {
          console.error(err);
          alert(err?.message || "Inventory action failed.");
          showShipPanel();
        }
      });
    });
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
  const FIT_VIEW_SCALE = 0.86;
  const MAP_SCREEN_SPREAD_MULT = 10;
  const MAX_INITIAL_SCALE = CAMERA_MAX_SCALE;
  const SHIP_SIZE_SCALE = 0.78;
  const SHIP_VISUAL_SCALE = 0.5;
  const SHIP_CLICK_RADIUS_MULT = 3.2;
  const SHIP_WORLD_SCALE_COMPENSATION = 1;
  const PARKED_ORBIT_ROTATION_PERIOD_S = 3600;
  const PARKED_ABOVE_NODE_Y = -14;
  const SHIP_TRAIL_DISTANCE_MULT = 1.55;
  const SHIP_PATH_DASH_LEN_MULT = 1.45;
  const SHIP_PATH_GAP_LEN_MULT = 1.1;
  const SHIP_PATH_ALPHA = 0.2;
  const SHIP_DOCK_BASE_RADIUS_PX = 22;
  const SHIP_DOCK_RING_STEP_PX = 26;
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
  const MOONLET_HOVER_SCALE_MULT = 1.16;
  const ASTEROID_HOVER_SCALE_MULT = 1.16;
  const SOLAR_RING_MULT = 0.72;
  const SHIP_SELECTION_STROKE_PX = 1.35;
  const UNIVERSAL_TEXT_SCALE_CAP = 2.8;
  const TEXT_COLLISION_PADDING_PX = 6;
  const PLANET_ICON_SCREEN_MULT = 1.5;
  const MOON_ICON_SCREEN_PX = 16;
  const ASTEROID_ICON_SCREEN_PX = 16;
  const ASTEROID_HINTS = ["asteroid", "zoozve"];
  const PLANET_ICON_ZOOM_COMP_MAX = 320;
  const PLANET_LABEL_ZOOM_COMP_MAX = 42;

  // Orbits we render as rings (NOT as dots)
  const ORBIT_IDS = new Set([
    "LEO", "HEO", "GEO",
    "LLO", "HLO",
    "MERC_ORB", "MERC_HEO", "MERC_GEO",
    "VEN_ORB", "VEN_HEO", "VEN_GEO",
    "LMO", "HMO", "MGO",
  ]);
  const LPOINT_IDS = new Set(["L1", "L2", "L3", "L4", "L5"]);

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

  function refreshZoomScaledTextResolution() {
    const targetRes = Math.min(MAX_TEXT_RESOLUTION, Math.max(1, BASE_TEXT_RESOLUTION * world.scale.x));
    for (const t of zoomScaledTexts) {
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
    for (const t of zoomScaledTexts) {
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
    for (const t of zoomScaledTexts) {
      if (!t) continue;
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
    const loc = locationsById.get(locationId);
    if (!loc) return null;

    if (loc.id === "grp_earth" || hasAncestor(loc.id, "grp_earth", locationParentById)) {
      return locationsById.get("grp_earth") || null;
    }
    if (loc.id === "grp_moon" || hasAncestor(loc.id, "grp_moon", locationParentById)) {
      return locationsById.get("grp_moon") || null;
    }
    return null;
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

  function computeTransitCurve(ship, fromLoc, toLoc) {
    const p0 = { x: fromLoc.rx, y: fromLoc.ry };
    const p3 = { x: toLoc.rx, y: toLoc.ry };
    const dx = p3.x - p0.x;
    const dy = p3.y - p0.y;
    const d = Math.max(1e-6, Math.hypot(dx, dy));
    const dir = normalizeVec(dx, dy, 1, 0);

    const fromBody = getLocationBodyCenter(ship.from_location_id);
    const toBody = getLocationBodyCenter(ship.to_location_id);
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

    return { p0, c1, c2, p3 };
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
    for (let i = 1; i < cumulative.length; i++) {
      if (d <= cumulative[i]) {
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
    }
    return points[points.length - 1];
  }

  function drawDashedTransitPath(pathGfx, curve, size, isSelected, displayScale = 1) {
    if (!pathGfx || !curve) return;

    const samples = 72;
    const points = [];
    for (let i = 0; i <= samples; i++) points.push(cubicPoint(curve, i / samples));

    const cumulative = [0];
    for (let i = 1; i < points.length; i++) {
      const dx = points[i].x - points[i - 1].x;
      const dy = points[i].y - points[i - 1].y;
      cumulative.push(cumulative[i - 1] + Math.hypot(dx, dy));
    }

    const total = cumulative[cumulative.length - 1] || 0;
    const dashLen = Math.max(3, size * SHIP_PATH_DASH_LEN_MULT);
    const gapLen = Math.max(2, size * SHIP_PATH_GAP_LEN_MULT);
    const scaleSafe = Math.max(0.05, Number(displayScale) || 1);
    const shipSizeFactor = clamp((Number(size) || 10) / 10, 0.42, 1.55);
    const baseStrokePx = (isSelected ? 1.75 : 1.0) * shipSizeFactor;
    const screenWidth = clamp((baseStrokePx * scaleSafe) / world.scale.x, 0.12 / world.scale.x, 2.4 / world.scale.x);
    const color = isSelected ? 0xffffff : 0xffdfbe;
    const alpha = isSelected ? 0.46 : SHIP_PATH_ALPHA;

    pathGfx.clear();
    pathGfx.lineStyle(screenWidth, color, alpha);

    for (let d = 0; d < total; d += dashLen + gapLen) {
      const a = pointOnPolyline(points, cumulative, d);
      const b = pointOnPolyline(points, cumulative, Math.min(total, d + dashLen));
      pathGfx.moveTo(a.x, a.y);
      pathGfx.lineTo(b.x, b.y);
    }
  }

  // ---------- Parking offsets ----------
  function slotOffsetWorld(slotIndex, zoom, baseRadiusPx) {
    if ((slotIndex || 0) === 0) {
      return { dxWorld: 0, dyWorld: 0 };
    }

    const slotsPerRing = 12;

    const idx = Math.max(1, slotIndex) - 1;
    const ring = Math.floor(idx / slotsPerRing);
    const j = idx % slotsPerRing;

    const rPx = baseRadiusPx + ring * SHIP_DOCK_RING_STEP_PX;
    const angle = (-Math.PI / 2) + (2 * Math.PI * (j / slotsPerRing));
    const zoomSafe = Math.max(0.0001, Number(zoom) || 1);
    return {
      dxWorld: (Math.cos(angle) * rPx) / zoomSafe,
      dyWorld: (Math.sin(angle) * rPx) / zoomSafe,
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
        if (Number.isFinite(Number(s.dock_slot))) explicit.push(s);
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
    if (["moon", "luna"].includes(String(name).toLowerCase())) {
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

    bindBodyHover(sunGfx, "grp_sun");
    bindBodyHover(mercuryGfx, "grp_mercury");
    bindBodyHover(venusGfx, "grp_venus");
    bindBodyHover(earthGfx, "grp_earth");
    bindBodyHover(moonGfx, "grp_moon");
    bindBodyHover(marsGfx, "grp_mars");

    mainPlanetGfx.push(sunGfx, mercuryGfx, venusGfx, earthGfx, marsGfx);
    planetLayer.addChild(sunGfx, mercuryGfx, venusGfx, earthGfx, moonGfx, marsGfx);
  }

  function updatePlanetVisualScale() {
    const zoom = Math.max(0.0001, Number(world.scale.x) || 1);
    const iconLockedToScreen = (1 / zoom) * PLANET_ICON_SCREEN_MULT;
    const labelLockedToScreen = 1 / zoom;

    for (const planet of mainPlanetGfx) {
      if (!planet) continue;
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
    if (!sun || !mercury || !venus || !earth || !moon || !mars) return;
    if (!sunGfx || !mercuryGfx || !venusGfx || !earthGfx || !moonGfx || !marsGfx) return;

    sunGfx.position.set(sun.rx, sun.ry);
    mercuryGfx.position.set(mercury.rx, mercury.ry);
    venusGfx.position.set(venus.rx, venus.ry);
    earthGfx.position.set(earth.rx, earth.ry);
    moonGfx.position.set(moon.rx, moon.ry);
    marsGfx.position.set(mars.rx, mars.ry);
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

  function renderOrbitRings() {
    orbitLayer.clear();
    if (orbitDetailAlpha <= 0.001 && mainOrbitDetailAlpha <= 0.001) return;

    const zoom = Math.max(0.0001, Number(world.scale.x) || 1);
    const deepOrbitShrink = deepZoomShrink(zoom, DEEP_ZOOM_SHRINK_START, 0.45, ORBIT_RING_MIN_PX / ORBIT_RING_BASE_PX);
    const ringScreenPx = Math.max(ORBIT_RING_MIN_PX, ORBIT_RING_BASE_PX * deepOrbitShrink);
    const baseLW = ringScreenPx / zoom;

    const sun = locationsById.get("grp_sun");
    if (sun) {
      const solarIds = ["grp_mercury", "grp_venus", "grp_earth", "grp_mars"];
      orbitLayer.lineStyle((ringScreenPx * SOLAR_RING_MULT) / zoom, 0xf3d9a6, 0.18 * mainOrbitDetailAlpha);
      for (const pid of solarIds) {
        const body = locationsById.get(pid);
        if (!body) continue;
        const rr = Math.hypot(body.rx - sun.rx, body.ry - sun.ry);
        if (rr > 1e-6) orbitLayer.drawCircle(sun.rx, sun.ry, rr);
      }
    }

    function drawRing(id) {
      const oi = orbitInfo.get(id);
      if (!oi) return;

      const isHover = hoveredOrbitId === id;
      const lw = isHover ? baseLW * ORBIT_RING_HOVER_MULT : baseLW;
      const a = (isHover ? 0.26 : 0.10) * orbitDetailAlpha;

      orbitLayer.lineStyle(lw, 0xffffff, a);
      orbitLayer.drawCircle(oi.cx, oi.cy, oi.radius);
    }

    [
      "LEO", "HEO", "GEO",
      "LLO", "HLO",
      "MERC_ORB", "MERC_HEO", "MERC_GEO",
      "VEN_ORB", "VEN_HEO", "VEN_GEO",
      "LMO", "HMO", "MGO",
    ].forEach(drawRing);
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

    hoveredOrbitId = best;

    // show only hovered orbit label near cursor
    for (const [id, t] of orbitLabelMap.entries()) {
      if (id === hoveredOrbitId) {
        t.alpha = orbitDetailAlpha;
        t.position.set(p.x + 14 / world.scale.x, p.y);
      } else {
        t.alpha = 0;
      }
    }
  });

  app.view.addEventListener("pointerleave", () => {
    hoveredOrbitId = null;
    hoveredBodyId = null;
    for (const t of orbitLabelMap.values()) t.alpha = 0;
  });

  // ---------- Locations ----------
  function ensureLocationsGfx() {
    function makeMoonMarker(isPhobos) {
      const marker = makeMoonIconGlyph(MOON_ICON_SCREEN_PX);
      marker.__isMoonIcon = true;
      marker.__moonBaseSizePx = marker.__baseSizePx || MOON_ICON_SCREEN_PX;
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

      if (locGfx.has(loc.id)) continue;

      const inEarthLocal = hasAncestor(loc.id, "grp_earth_orbits", locationParentById);
      const inMoonLocal = hasAncestor(loc.id, "grp_moon_orbits", locationParentById);
      const isLPoint = LPOINT_IDS.has(loc.id);
      const isMoonlet = loc.id === "PHOBOS" || loc.id === "DEIMOS";
      const isAsteroid = isAsteroidLocation(loc);

      let kind = "deep-node";
      if (isLPoint) kind = "lagrange";
      else if (inEarthLocal || inMoonLocal) kind = "orbit-node";
      else if (isMoonlet) kind = "moonlet";
      else if (isAsteroid) kind = "asteroid";

      let dot;
      if (LPOINT_IDS.has(loc.id)) {
        dot = new PIXI.Graphics();
        dot.beginFill(0xffffff, 0.16);
        dot.lineStyle(1, 0xffffff, 0.3);
        dot.drawRect(-3, -3, 6, 6);
        dot.endFill();
      } else if (loc.id === "PHOBOS") {
        dot = makeMoonMarker(true);
      } else if (loc.id === "DEIMOS") {
        dot = makeMoonMarker(false);
      } else if (isAsteroid) {
        dot = makeAsteroidMarker();
      } else {
        dot = new PIXI.Graphics();
        dot.beginFill(0xffffff, 0.10);
        dot.lineStyle(2, 0xffffff, 0.22);
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

    // Keep moons and local orbit rings fully visible down to scale 0.7,
    // then fade as user zooms further out.
    moonDetailAlpha = zoomFade(z, 0.35, 0.7);
    orbitDetailAlpha = zoomFade(z, 0.35, 0.7);
    mainOrbitDetailAlpha = 1 - zoomFade(z, 1.2, 3.2);
    lPointDetailAlpha = zoomFade(z, 1.2, 2.4);
    localNodeDetailAlpha = zoomFade(z, 0.35, 0.7);

    if (moonGfx) moonGfx.alpha = moonDetailAlpha;
    updatePlanetVisualScale();

    for (const entry of locGfx.values()) {
      const detailAlpha = entry.kind === "lagrange"
        ? lPointDetailAlpha
        : (entry.kind === "orbit-node"
          ? localNodeDetailAlpha
          : (entry.kind === "moonlet" ? moonDetailAlpha : 1));
      if (entry.kind === "moonlet" && entry.dot?.__isMoonIcon) {
        const moonBase = Math.max(8, Number(entry.dot.__moonBaseSizePx) || MOON_ICON_SCREEN_PX);
        const moonletHoverMul = entry.hovered ? MOONLET_HOVER_SCALE_MULT : 1;
        entry.dot.scale.set(((MOON_ICON_SCREEN_PX / moonBase) / Math.max(0.0001, z)) * moonletHoverMul);
      } else if (entry.kind === "asteroid" && entry.dot?.__isAsteroidIcon) {
        const asteroidBase = Math.max(8, Number(entry.dot.__asteroidBaseSizePx) || ASTEROID_ICON_SCREEN_PX);
        const asteroidHoverMul = entry.hovered ? ASTEROID_HOVER_SCALE_MULT : 1;
        entry.dot.scale.set(((ASTEROID_ICON_SCREEN_PX / asteroidBase) / Math.max(0.0001, z)) * asteroidHoverMul);
      } else {
        entry.dot.scale.set(iconGrowOnZoomOut * deepIconShrink);
      }
      entry.label.scale.set(labelGrowOnZoomOut * deepLabelShrink);
      entry.dot.alpha = detailAlpha * (entry.hovered ? 1 : 0.9);
      entry.dot.visible = detailAlpha > 0.001;
      entry.label.alpha = entry.hovered ? detailAlpha : 0;
      entry.label.visible = entry.hovered && detailAlpha > 0.001;
    }

    for (const [id, t] of orbitLabelMap.entries()) {
      t.alpha = (id === hoveredOrbitId && orbitDetailAlpha > 0.001) ? orbitDetailAlpha : 0;
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

      const phase = stringHash01(s.id) * Math.PI * 2;

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
        phase,
        transitKey: null,
        curve: null,
      });
    }

    for (const [shipId, gfx] of shipGfx.entries()) {
      if (nextIds.has(shipId)) continue;
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
    const hiddenClusterCountByLocation = new Map();

    for (const gfx of shipGfx.values()) {
      const s = gfx.ship;
      if (s?.status !== "docked" || !s.location_id) continue;
      dockedCountByLocation.set(s.location_id, (dockedCountByLocation.get(s.location_id) || 0) + 1);
    }

    for (const gfx of shipGfx.values()) {
      const { ship, container, shipIcon, headingLine, selectionBox, pathGfx, label, idTag, idOffsetY, labelOffsetY, size, hitRadius, slot, phase } = gfx;
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
        const hideInCluster = clusterMode && dockedCount >= SHIP_CLUSTER_MIN_COUNT && !isSelectedOrHovered;
        if (hideInCluster) {
          container.visible = false;
          hiddenClusterCountByLocation.set(ship.location_id, (hiddenClusterCountByLocation.get(ship.location_id) || 0) + 1);
          continue;
        }

        container.visible = true;
        const slotIndex = Number(slot?.index) || 0;
        const slotOffset = slotOffsetWorld(slotIndex, zoom, SHIP_DOCK_BASE_RADIUS_PX);
        const ox = slotOffset.dxWorld;
        const oy = slotOffset.dyWorld + (PARKED_ABOVE_NODE_Y / zoom);

        // ✅ drift around orbit rings (visual only)
        if (ORBIT_IDS.has(ship.location_id) && orbitInfo.has(ship.location_id)) {
          const oi = orbitInfo.get(ship.location_id);
          const omega = (2 * Math.PI) / PARKED_ORBIT_ROTATION_PERIOD_S;
          const a = oi.baseAngle + omega * now + phase;

          const x = oi.cx + Math.cos(a) * oi.radius;
          const y = oi.cy + Math.sin(a) * oi.radius;

          px = x + ox;
          py = y + oy;
          facingAngle = a + Math.PI / 2;
          headingAngle = facingAngle;
        } else {
          px = loc.rx + ox;
          py = loc.ry + oy;
          facingAngle = 0;
          headingAngle = 0;
        }
      } else {
        const A = locationsById.get(ship.from_location_id);
        const B = locationsById.get(ship.to_location_id);
        if (!A || !B || !ship.departed_at || !ship.arrives_at) continue;

        const t = (now - ship.departed_at) / (ship.arrives_at - ship.departed_at);
        const tt = Math.max(0, Math.min(1, t));
        const transitKey = `${ship.from_location_id}->${ship.to_location_id}`;
        if (!gfx.curve || gfx.transitKey !== transitKey) {
          gfx.curve = computeTransitCurve(ship, A, B);
          gfx.transitKey = transitKey;
        }

        const p = cubicPoint(gfx.curve, tt);
        const tan = cubicTangent(gfx.curve, tt);
        const travelAngle = Math.atan2(tan.y, tan.x);
        const decel = tt >= 0.5;
        facingAngle = decel ? travelAngle + Math.PI : travelAngle;
        headingAngle = travelAngle;
        px = p.x;
        py = p.y;

        if (pathGfx) {
          const isSelected = ship.id === selectedShipId;
          drawDashedTransitPath(pathGfx, gfx.curve, size || 10, isSelected, effectiveShipScale);
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
        const hitBase = Math.max(2.4, Number(shipIcon?.__hitRadiusPx) || (Number(hitRadius) || 8) * 0.3);
        const scaledHitRadius = Math.max(2.4, hitBase * iconDisplayScale);
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

    updateShipClusterLabels(hiddenClusterCountByLocation, clusterMode);
    applyUniversalTextScaleCap();
  }

  // ---------- Move Planner modal ----------
  function closeModal() {
    const el = document.getElementById("modalOverlay");
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

  async function openTransferPlanner(ship, initialDestId = null) {
    if (!ship || ship.status !== "docked") return;

    hideContextMenu();

    if (!treeCache) {
      const t = await (await fetch("/api/locations/tree", { cache: "no-store" })).json();
      treeCache = t.tree || [];
    }

    const overlay = document.createElement("div");
    overlay.id = "modalOverlay";
    overlay.className = "modalOverlay";
    overlay.innerHTML = `
      <div class="modal">
        <div class="modalHeader">
          <div>
            <div class="modalTitle">Plan Transfer</div>
            <div class="muted small">${ship.name} • from ${ship.location_id}</div>
          </div>
          <button class="iconBtn btnSecondary" id="modalClose">✕</button>
        </div>

        <div class="modalBody">
          <div class="modalCol">
            <div class="muted small" style="margin-bottom:8px;">Destination</div>
            <div id="treeRoot"></div>
          </div>

          <div class="modalCol">
            <div class="muted small" style="margin-bottom:8px;">Transfer details</div>
            <div id="quoteBox" class="quoteBox">
              <div class="muted">Select a destination…</div>
            </div>
            <div style="display:flex; gap:10px; justify-content:flex-end; margin-top:12px;">
              <button id="cancelBtn" class="btnSecondary">Cancel</button>
              <button id="confirmBtn" class="btnPrimary" disabled>Confirm</button>
            </div>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    document.getElementById("modalClose").onclick = closeModal;
    document.getElementById("cancelBtn").onclick = closeModal;
    overlay.addEventListener("pointerdown", (e) => { if (e.target === overlay) closeModal(); });
    document.addEventListener("keydown", escClose);

    const treeRoot = document.getElementById("treeRoot");
    const quoteBox = document.getElementById("quoteBox");
    const confirmBtn = document.getElementById("confirmBtn");

    let selectedDest = null;
    const transferTreeOpenState = new Map();

    function buildDestinationTree() {
      treeRoot.innerHTML = "";
      const wrap = document.createElement("div");
      wrap.className = "tree transferTreeRoot";
      for (let i = 0; i < treeCache.length; i += 1) {
        renderTreeNode(
          treeCache[i],
          wrap,
          selectLeaf,
          { ancestorHasNext: [], isLast: i === treeCache.length - 1, depth: 0 },
          selectedDest,
          transferTreeOpenState
        );
      }
      treeRoot.appendChild(wrap);
    }

    function setQuoteHtml(html) { quoteBox.innerHTML = html; }

    async function selectLeaf(node) {
      selectedDest = node.id;
      buildDestinationTree();
      confirmBtn.disabled = true;
      setQuoteHtml(`<div class="muted">Loading quote…</div>`);

      try {
        const q = await (await fetch(
          `/api/transfer_quote?from_id=${encodeURIComponent(ship.location_id)}&to_id=${encodeURIComponent(node.id)}`,
          { cache: "no-store" }
        )).json();

        const dv = Math.round(q.dv_m_s);
        const tof = q.tof_s;
        const tofH = tof >= 3600 ? `${(tof / 3600).toFixed(1)} h` : `${(tof / 60).toFixed(1)} m`;
        const path = (q.path || []).join(" → ");
        const fuelNeedKg = computeFuelNeededKg(ship.dry_mass_kg, ship.fuel_kg, ship.isp_s, q.dv_m_s);
        const fuelAfterKg = Math.max(0, Number(ship.fuel_kg || 0) - fuelNeedKg);

        setQuoteHtml(`
          <div><b>To:</b> ${locationsById.get(node.id)?.name || node.name} <span class="muted small">(${node.id})</span></div>
          <div style="margin-top:8px;"><b>Δv:</b> ${dv} m/s</div>
          <div><b>Time:</b> ${tofH}</div>
          <div><b>Fuel use:</b> ${fuelNeedKg.toFixed(0)} kg</div>
          <div><b>Fuel after burn:</b> ${fuelAfterKg.toFixed(0)} kg</div>
          <div style="margin-top:8px;"><b>Path:</b></div>
          <div class="pathBox">${path}</div>
        `);

        confirmBtn.disabled = false;
      } catch (err) {
        console.error(err);
        setQuoteHtml(`<div class="muted">No transfer data.</div>`);
      }
    }

    buildDestinationTree();

    if (initialDestId && initialDestId !== ship.location_id) {
      const initialLoc = locationsById.get(initialDestId);
      if (initialLoc && !initialLoc.is_group) {
        selectLeaf({ id: initialLoc.id, name: initialLoc.name || initialLoc.id });
      }
    }

    confirmBtn.onclick = async () => {
      if (!selectedDest) return;
      confirmBtn.disabled = true;
      try {
        const resp = await fetch(`/api/ships/${encodeURIComponent(ship.id)}/transfer`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to_location_id: selectedDest }),
        });
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          setQuoteHtml(`<div class="muted">${data.detail || "Transfer failed."}</div>`);
          confirmBtn.disabled = false;
          return;
        }
      } finally {
        if (confirmBtn.disabled) {
          closeModal();
          await syncState();
          showShipPanel();
        }
      }
    };
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
      const subtitle = ship.status === "docked"
        ? `Docked: ${locationsById.get(ship.location_id)?.name || ship.location_id}`
        : `In transit: ${locationsById.get(ship.from_location_id)?.name || ship.from_location_id} → ${locationsById.get(ship.to_location_id)?.name || ship.to_location_id}`;

      setInfo(
        ship.name,
        subtitle,
        `Containers: ${(ship.inventory_containers || []).length}`,
        [
          `Fuel: ${Number(ship.fuel_kg || 0).toFixed(0)} / ${Number(ship.fuel_capacity_kg || 0).toFixed(0)} kg`,
          `Δv remaining: ${Number(ship.delta_v_remaining_m_s || 0).toFixed(0)} m/s`,
        ]
      );

      if (infoList) {
        infoList.innerHTML += buildInventoryListHtml(ship.inventory_containers || []);
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
          `Fuel: ${Number(ship.fuel_kg || 0).toFixed(0)} / ${Number(ship.fuel_capacity_kg || 0).toFixed(0)} kg`,
          `Δv remaining: ${Number(ship.delta_v_remaining_m_s || 0).toFixed(0)} m/s`,
          ...(ship.notes || []),
        ]
      );
      if (infoList) infoList.innerHTML += buildPartsStackHtml(ship);
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

    setInfo(
      ship.name,
      `In transit: ${A?.name || ship.from_location_id} → ${B?.name || ship.to_location_id}`,
      eta !== null ? `ETA: ${formatEtaDaysHours(eta)}` : "",
      [
        `Fuel: ${Number(ship.fuel_kg || 0).toFixed(0)} / ${Number(ship.fuel_capacity_kg || 0).toFixed(0)} kg`,
        `Δv remaining: ${Number(ship.delta_v_remaining_m_s || 0).toFixed(0)} m/s`,
        ship.dv_planned_m_s != null ? `Δv planned: ${Math.round(ship.dv_planned_m_s)} m/s` : "",
        (ship.transfer_path || []).length ? `Path: ${(ship.transfer_path || []).join(" → ")}` : "",
      ].filter(Boolean)
    );
    if (infoList) infoList.innerHTML += buildPartsStackHtml(ship);
    actions.innerHTML = `<div class="muted small">(No mid-course changes in alpha.)</div>`;
  }

  // ---------- Sync ----------
  async function syncLocationsOnce() {
    const data = await (await fetch("/api/locations", { cache: "no-store" })).json();
    locations = data.locations || [];

    const parentById = new Map(locations.map((l) => [l.id, l.parent_id || null]));
    locationParentById = parentById;

    const sun = locations.find((l) => l.id === "grp_sun");
    const mercury = locations.find((l) => l.id === "grp_mercury");
    const venus = locations.find((l) => l.id === "grp_venus");
    const earth = locations.find((l) => l.id === "grp_earth");
    const moon = locations.find((l) => l.id === "grp_moon");
    const mars = locations.find((l) => l.id === "grp_mars");

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

    for (const l of locations) {
      l.is_group = !!Number(l.is_group);

      // default deep space mapping (heliocentric log-like)
      const deep = projectDeepPosition(l.x, l.y);
      let rx = deep.rx;
      let ry = deep.ry;

      // expand local orbit nodes relative to their body center
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
      }

      l.rx = rx;
      l.ry = ry;
    }

    locationsById = new Map(locations.map((l) => [l.id, l]));
    leaves = locations.filter((l) => !l.is_group);

    buildPlanets();
    positionPlanets();

    computeOrbitInfo();

    ensureLocationsGfx();
    updateLocationPositions();
    fitToLocations();
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
  await syncLocationsOnce();
  await syncState();
  setInterval(() => {
    syncState().catch((err) => console.error(err));
  }, 1000);

  setInfo("Select a ship", "", "", ["Click a ship, then Move to plan a transfer."]);
  actions.innerHTML = "";

  // Main render loop
  app.ticker.add(() => {
    try {
      positionPlanets();
      renderOrbitRings();
      applyZoomDetailVisibility();
      updateShipPositions();
      buildMapOverview();
      applyTextCollisionCulling();
    } catch (err) {
      console.error("Main map tick failed:", err);
      if (!app.ticker.started) app.ticker.start();
    }
  });
})();
