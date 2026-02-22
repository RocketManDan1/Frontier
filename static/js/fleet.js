(function () {
  "use strict";

  /* ── DOM refs ─────────────────────────────────────────── */
  const summaryEl = document.getElementById("fleetSummary");
  const shipListEl = document.getElementById("fleetShipList");
  const detailEl = document.getElementById("fleetDetail");
  const itemDisplay = window.ItemDisplay || null;

  /* ── Clock sync ───────────────────────────────────────── */
  let serverSyncGameS = Date.now() / 1000;
  let clientSyncRealS = Date.now() / 1000;
  let timeScale = 1;

  function serverNow() {
    const realNow = Date.now() / 1000;
    return serverSyncGameS + (realNow - clientSyncRealS) * timeScale;
  }

  /* ── State ────────────────────────────────────────────── */
  let allShips = [];
  let selectedShipId = null;
  let cachedLocations = null;

  /* ── Context menu ─────────────────────────────────────── */
  let contextMenuEl = null;

  function ensureContextMenu() {
    if (contextMenuEl) return contextMenuEl;
    const el = document.createElement("div");
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

  function showContextMenu(title, items, clientX, clientY) {
    const options = Array.isArray(items) ? items.filter(Boolean) : [];
    if (!options.length) { hideContextMenu(); return; }

    const menu = ensureContextMenu();
    menu.innerHTML = "";

    if (title) {
      const titleEl = document.createElement("div");
      titleEl.className = "mapContextMenuTitle";
      titleEl.textContent = title;
      menu.appendChild(titleEl);
    }

    for (const item of options) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "mapContextMenuItem";
      btn.textContent = item.label || "Action";
      btn.disabled = !!item.disabled;
      btn.addEventListener("click", async () => {
        hideContextMenu();
        if (!item.disabled && typeof item.onClick === "function") await item.onClick();
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

  document.addEventListener("mousedown", (e) => {
    if (!contextMenuEl || contextMenuEl.style.display === "none") return;
    if (contextMenuEl.contains(e.target)) return;
    hideContextMenu();
  });
  window.addEventListener("blur", hideContextMenu);
  window.addEventListener("resize", hideContextMenu);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideContextMenu(); });

  /* ── Formatters ───────────────────────────────────────── */
  function fmtKg(v) {
    const val = Math.max(0, Number(v) || 0);
    if (val >= 5000) return `${(val / 1000).toFixed(1)} t`;
    return `${val.toFixed(0)} kg`;
  }

  function fmtEta(ship) {
    if (!ship.arrives_at) return "—";
    const s = Math.max(0, ship.arrives_at - serverNow());
    const hours = s / 3600;
    if (hours >= 24) {
      const days = hours / 24;
      return `${days.toFixed(1)}d (${hours.toFixed(1)}h)`;
    }
    return `${hours.toFixed(1)}h`;
  }

  function transitProgressPct(ship) {
    if (!ship.departed_at || !ship.arrives_at) return 0;
    const span = ship.arrives_at - ship.departed_at;
    if (!(span > 0)) return 0;
    const elapsed = serverNow() - ship.departed_at;
    return Math.max(0, Math.min(100, (elapsed / span) * 100));
  }

  function escapeHtml(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function fuelPct(fuel, cap) {
    if (cap <= 0) return 0;
    return Math.max(0, Math.min(100, (fuel / cap) * 100));
  }

  function fmtMwTh(v) { return `${Math.max(0, Number(v) || 0).toFixed(1)}<span class="pbUnit">MWth</span>`; }
  function fmtMwE(v) { return `${Math.max(0, Number(v) || 0).toFixed(1)}<span class="pbUnit">MWe</span>`; }

  /* ── Ship list (sidebar) ──────────────────────────────── */
  function renderShipList(ships) {
    if (!shipListEl) return;
    shipListEl.innerHTML = "";

    const sorted = ships.slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));

    for (const ship of sorted) {
      const row = document.createElement("div");
      row.className = "fleetShipRow" + (ship.id === selectedShipId ? " isActive" : "");
      row.dataset.shipId = ship.id;

      const status = ship.status === "transit" ? "In Transit" : "Docked";
      const loc = ship.status === "docked"
        ? (ship.location_id || "—")
        : `${ship.from_location_id || "?"} → ${ship.to_location_id || "?"}`;
      const dv = Number(ship.delta_v_remaining_m_s || 0);
      const partCount = Array.isArray(ship.parts) ? ship.parts.length : 0;

      row.innerHTML = `
        <div class="fleetShipRowName">${escapeHtml(ship.name)}</div>
        <div class="fleetShipRowMeta">
          <span class="badge ${ship.status === "transit" ? "badgeMove" : ""}">${status}</span>
          <span class="fleetShipRowLoc">${escapeHtml(loc)}</span>
        </div>
        <div class="fleetShipRowStats">
          <span>${partCount} parts</span>
          <span>Δv ${Math.max(0, dv).toFixed(0)} m/s</span>
        </div>
      `;

      // Select on click
      row.addEventListener("click", () => {
        selectedShipId = ship.id;
        renderShipList(allShips);
        renderDetail(ship);
      });

      // Context menu on right-click
      row.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        selectedShipId = ship.id;
        renderShipList(allShips);
        renderDetail(ship);
        showShipContextMenu(ship, e.clientX, e.clientY);
      });

      shipListEl.appendChild(row);
    }
  }

  /* ── Ship context menu actions ────────────────────────── */
  function shipContextMenuItems(ship) {
    const isDocked = ship.status !== "transit";
    const actions = [];

    // Select ship
    actions.push({
      label: ship.id === selectedShipId ? "Ship selected" : "Select ship",
      disabled: ship.id === selectedShipId,
      onClick: () => {
        selectedShipId = ship.id;
        renderShipList(allShips);
        renderDetail(ship);
      },
    });

    // View ship details
    actions.push({
      label: "View ship details",
      onClick: () => {
        selectedShipId = ship.id;
        renderShipList(allShips);
        renderDetail(ship);
      },
    });

    // Open hangar (navigates to map with hangar context)
    actions.push({
      label: "Open hangar",
      onClick: () => {
        const loc = ship.location_id || ship.to_location_id || "";
        window.location.href = `/?focus=${encodeURIComponent(loc)}&ship=${encodeURIComponent(ship.id)}&hangar=1`;
      },
    });

    // Plan transfer (docked only)
    if (isDocked) {
      actions.push({
        label: "Plan transfer\u2026",
        onClick: () => openTransferDialog(ship),
      });
    }

    // Prospect (docked + has robonaut)
    const shipParts = Array.isArray(ship.parts) ? ship.parts : [];
    const hasRobonaut = shipParts.some((p) => {
      if (!p || typeof p !== "object") return false;
      const cat = String(p.category_id || p.type || p.category || "").toLowerCase();
      return cat === "robonaut" || cat === "robonauts";
    });
    if (hasRobonaut && isDocked) {
      actions.push({
        label: "Prospect\u2026",
        onClick: () => openProspectDialog(ship),
      });
    }

    return actions;
  }

  function showShipContextMenu(ship, clientX, clientY) {
    showContextMenu(ship.name, shipContextMenuItems(ship), clientX, clientY);
  }

  /* ── Transfer dialog ──────────────────────────────────── */
  async function loadLocations() {
    if (cachedLocations) return cachedLocations;
    try {
      const resp = await fetch("/api/locations", { cache: "no-store" });
      const data = await resp.json();
      cachedLocations = Array.isArray(data) ? data : (data.locations || []);
      return cachedLocations;
    } catch {
      return [];
    }
  }

  async function openTransferDialog(ship) {
    const locations = await loadLocations();
    const currentLoc = ship.location_id;

    // Create modal
    const overlay = document.createElement("div");
    overlay.className = "modal";
    overlay.innerHTML = `
      <div class="modalOverlay"></div>
      <div class="modalBox" style="max-width:420px;">
        <div class="modalHeader">
          <div class="modalTitle">Set Destination — ${escapeHtml(ship.name)}</div>
          <button class="modalClose" title="Close">✕</button>
        </div>
        <div class="modalBody" style="padding:12px;">
          <div style="margin-bottom:8px;" class="muted">Current: <b>${escapeHtml(currentLoc || "Unknown")}</b></div>
          <label style="display:block; margin-bottom:8px; font-size:12px;">
            Destination:
            <select id="transferDestSelect" style="width:100%; margin-top:4px; padding:6px; background:var(--panel-solid); color:var(--text); border:1px solid var(--border); border-radius:4px;">
              <option value="">— Select —</option>
            </select>
          </label>
          <div id="transferQuoteInfo" class="muted" style="font-size:12px; min-height:24px;"></div>
          <div style="display:flex; gap:8px; margin-top:12px; justify-content:flex-end;">
            <button class="btn btnSecondary" id="transferCancel">Cancel</button>
            <button class="btn btnPrimary" id="transferConfirm" disabled>Transfer</button>
          </div>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);

    const select = overlay.querySelector("#transferDestSelect");
    const quoteInfo = overlay.querySelector("#transferQuoteInfo");
    const confirmBtn = overlay.querySelector("#transferConfirm");
    const cancelBtn = overlay.querySelector("#transferCancel");
    const closeBtn = overlay.querySelector(".modalClose");
    const overlayBg = overlay.querySelector(".modalOverlay");

    // Populate destinations
    const sortedLocs = locations
      .map(l => ({ id: l.id || l.location_id, name: l.name || l.id || l.location_id }))
      .filter(l => l.id && l.id !== currentLoc)
      .sort((a, b) => String(a.name).localeCompare(String(b.name)));

    for (const loc of sortedLocs) {
      const opt = document.createElement("option");
      opt.value = loc.id;
      opt.textContent = `${loc.name} (${loc.id})`;
      select.appendChild(opt);
    }

    let currentQuote = null;

    select.addEventListener("change", async () => {
      const destId = select.value;
      confirmBtn.disabled = true;
      currentQuote = null;
      if (!destId) { quoteInfo.textContent = ""; return; }
      quoteInfo.textContent = "Loading quote…";
      try {
        const resp = await fetch(`/api/transfer_quote?from_id=${encodeURIComponent(currentLoc)}&to_id=${encodeURIComponent(destId)}`);
        const data = await resp.json();
        if (!resp.ok) { quoteInfo.textContent = data.detail || "No route"; return; }
        const dvReq = Number(data.dv_m_s || 0);
        const tofH = (Number(data.tof_s || 0) / 3600).toFixed(1);
        const dvShip = Number(ship.delta_v_remaining_m_s || 0);
        const enough = dvShip >= dvReq;
        quoteInfo.innerHTML = `Δv required: <b>${dvReq.toFixed(0)} m/s</b> · Time: <b>${tofH}h</b> · Ship Δv: <b style="color:${enough ? "var(--text)" : "#f66"}">${dvShip.toFixed(0)} m/s</b>`;
        confirmBtn.disabled = !enough;
        currentQuote = data;
      } catch (err) {
        quoteInfo.textContent = String(err.message || "Failed to load quote");
      }
    });

    function closeModal() { overlay.remove(); }

    cancelBtn.addEventListener("click", closeModal);
    closeBtn.addEventListener("click", closeModal);
    overlayBg.addEventListener("click", closeModal);

    confirmBtn.addEventListener("click", async () => {
      const destId = select.value;
      if (!destId) return;
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Transferring…";
      try {
        const resp = await fetch(`/api/ships/${encodeURIComponent(ship.id)}/transfer`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to_location_id: destId }),
        });
        const data = await resp.json();
        if (!resp.ok) { window.alert(data.detail || "Transfer failed"); confirmBtn.disabled = false; confirmBtn.textContent = "Transfer"; return; }
        closeModal();
        await sync();
      } catch (err) {
        window.alert(String(err.message || "Transfer failed"));
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Transfer";
      }
    });
  }

  /* ── Prospect dialog ───────────────────────────────────── */
  async function openProspectDialog(ship) {
    if (!ship) return;
    hideContextMenu();

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

    function fmtDist(km) {
      km = Math.max(0, Number(km) || 0);
      if (km >= 1e6) return `${(km / 1e6).toFixed(1)}M km`;
      if (km >= 1e3) return `${(km / 1e3).toFixed(0)}k km`;
      return `${km.toFixed(0)} km`;
    }
    function fmtPct(v) { return `${(Number(v || 0) * 100).toFixed(1)}%`; }

    const overlay = document.createElement("div");
    overlay.className = "modal";
    overlay.innerHTML = `
      <div class="modalOverlay"></div>
      <div class="prospectModal">
        <div class="prospectHeader">
          <div class="prospectHeaderLeft">
            <div class="prospectTitle">Prospecting</div>
            <div class="prospectSubtitle">${escapeHtml(ship.name)} &bull; ${escapeHtml(bestRobonaut.name || "Robonaut")} &bull; Range ${fmtDist(rangeKm)}</div>
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
    overlay.querySelector("#prospectClose").onclick = closeModal;
    overlay.addEventListener("pointerdown", (e) => {
      if (e.target === overlay || e.target.classList.contains("modalOverlay")) closeModal();
    });
    document.addEventListener("keydown", escClose);

    const bodyEl = overlay.querySelector(".prospectBody");

    try {
      const resp = await fetch(`/api/org/prospecting/in_range/${encodeURIComponent(ship.id)}`, { cache: "no-store" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data?.detail || "Failed to load prospecting data");

      const sites = Array.isArray(data.sites) ? data.sites : [];
      if (!sites.length) {
        bodyEl.innerHTML = '<div class="prospectEmpty">No surface sites within range of this ship\'s robonaut.</div>';
        return;
      }

      renderProspectSites(bodyEl, sites, ship, closeModal);
    } catch (err) {
      bodyEl.innerHTML = `<div class="prospectError">${escapeHtml(err?.message || "Failed to load")}</div>`;
    }
  }

  function renderProspectSites(container, sites, ship, closeModal) {
    function fmtDist(km) {
      km = Math.max(0, Number(km) || 0);
      if (km >= 1e6) return `${(km / 1e6).toFixed(1)}M km`;
      if (km >= 1e3) return `${(km / 1e3).toFixed(0)}k km`;
      return `${km.toFixed(0)} km`;
    }
    function fmtPct(v) { return `${(Number(v || 0) * 100).toFixed(1)}%`; }

    container.innerHTML = "";

    const totalSites = sites.length;
    const prospected = sites.filter((s) => s.is_prospected).length;
    const unprospected = totalSites - prospected;

    const summary = document.createElement("div");
    summary.className = "prospectSummary";
    summary.innerHTML = `
      <span class="prospectSummaryCount">${totalSites} site${totalSites !== 1 ? "s" : ""} in range</span>
      <span class="prospectSummaryDetail">${prospected} prospected &bull; ${unprospected} uncharted</span>
    `;
    container.appendChild(summary);

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
        metaEl.innerHTML = `${fmtDist(site.distance_km)} &bull; ${Number(site.gravity_m_s2 || 0).toFixed(2)} m/s²`;
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
              site.is_prospected = true;
              site.resources_found = result.resources_found || [];
              renderProspectSites(container, sites, ship, closeModal);
              await sync();
            } catch (err) {
              btn.disabled = false;
              btn.textContent = "Prospect";
              window.alert(err?.message || "Prospecting failed");
            }
          });
          actionCol.appendChild(btn);
        }

        row.appendChild(actionCol);

        if (site.is_prospected && Array.isArray(site.resources_found) && site.resources_found.length) {
          const resWrap = document.createElement("div");
          resWrap.className = "prospectResourceList";
          for (const res of site.resources_found) {
            const resRow = document.createElement("div");
            resRow.className = "prospectResourceRow";
            resRow.innerHTML = `<span class="prospectResName">${escapeHtml(res.resource_id)}</span><span class="prospectResFraction">${fmtPct(res.mass_fraction)}</span>`;
            resWrap.appendChild(resRow);
          }
          row.appendChild(resWrap);
        }

        group.appendChild(row);
      }
      container.appendChild(group);
    }
  }

  /* ── Deconstruct dialog ───────────────────────────────── */
  async function confirmDeconstruct(ship) {
    const ok = window.confirm(
      `Deconstruct "${ship.name}"?\n\nAll parts and cargo will be deposited at ${ship.location_id || "the current location"}. The ship record will be deleted.`
    );
    if (!ok) return;
    try {
      const resp = await fetch(`/api/ships/${encodeURIComponent(ship.id)}/deconstruct`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keep_ship_record: false }),
      });
      const data = await resp.json();
      if (!resp.ok) { window.alert(data.detail || "Deconstruct failed"); return; }
      selectedShipId = null;
      await sync();
    } catch (err) {
      window.alert(String(err.message || "Deconstruct failed"));
    }
  }

  /* ── Detail pane ──────────────────────────────────────── */
  function renderDetail(ship) {
    if (!detailEl) return;
    if (!ship) {
      detailEl.innerHTML = '<div class="fleetDetailEmpty muted">Select a ship from the list</div>';
      return;
    }

    const isDocked = ship.status !== "transit";
    const loc = ship.status === "docked"
      ? (ship.location_id || "—")
      : `${ship.from_location_id || "?"} → ${ship.to_location_id || "?"}`;

    const dv = Number(ship.delta_v_remaining_m_s || 0);
    const partCount = Array.isArray(ship.parts) ? ship.parts.length : 0;

    // Build the detail HTML
    let html = `
      <div class="fleetDetailHead">
        <div class="fleetDetailHeadInfo">
          <div class="fleetDetailName">${escapeHtml(ship.name)}</div>
          <div class="fleetDetailSub">
            <span class="badge ${ship.status === "transit" ? "badgeMove" : ""}">${isDocked ? "Docked" : "In Transit"}</span>
            <span>${escapeHtml(loc)}</span>
            <span>${partCount} parts · Δv ${Math.max(0, dv).toFixed(0)} m/s</span>
          </div>
        </div>
        <div class="fleetDetailActions">
          <button class="btn btnSmall" data-action="hangar" title="Open Hangar">Open Hangar</button>
          <button class="btn btnSmall" data-action="transfer" ${!isDocked ? "disabled" : ""} title="Plan Transfer">Plan Transfer</button>
          <button class="btn btnSmall" data-action="deconstruct" ${!isDocked ? "disabled" : ""} title="Deconstruct">Deconstruct</button>
        </div>
      </div>
    `;

    // Transit progress
    if (ship.status === "transit") {
      const pct = transitProgressPct(ship);
      const path = (ship.transfer_path || []).join(" → ") || `${ship.from_location_id} → ${ship.to_location_id}`;
      html += `
        <div class="fleetSection">
          <div class="fleetSectionTitle">Transfer Progress</div>
          <div class="fleetTransitInfo">
            <div class="muted" style="margin-bottom:4px;">${escapeHtml(path)}</div>
            <div class="bar" style="width:100%; max-width:400px;">
              <div class="barFill" style="width:${pct.toFixed(1)}%;"></div>
            </div>
            <div class="muted" style="margin-top:4px;">${pct.toFixed(0)}% complete · ETA ${fmtEta(ship)}</div>
          </div>
        </div>
      `;
    }

    // Parts synopsis
    html += `
      <div class="fleetSection">
        <div class="fleetSectionTitle">Parts</div>
        <div class="fleetPartsGrid" id="fleetDetailParts"></div>
      </div>
    `;

    // Cargo / Inventory synopsis
    html += `
      <div class="fleetSection">
        <div class="fleetSectionTitle">Cargo</div>
        <div id="fleetDetailCargo"></div>
      </div>
    `;

    // Delta-v & propulsion
    html += renderDeltaVPanel(ship);

    // Power balance
    html += renderPowerPanel(ship);

    detailEl.innerHTML = html;

    // Wire action buttons
    detailEl.querySelector("[data-action='hangar']")?.addEventListener("click", () => {
      const loc = ship.location_id || ship.to_location_id || "";
      window.location.href = `/?focus=${encodeURIComponent(loc)}&ship=${encodeURIComponent(ship.id)}&hangar=1`;
    });
    detailEl.querySelector("[data-action='transfer']")?.addEventListener("click", () => openTransferDialog(ship));
    detailEl.querySelector("[data-action='deconstruct']")?.addEventListener("click", () => confirmDeconstruct(ship));

    // Context menu on detail head
    const headEl = detailEl.querySelector(".fleetDetailHead");
    if (headEl) {
      headEl.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        showShipContextMenu(ship, e.clientX, e.clientY);
      });
    }

    // Render parts grid with ItemDisplay
    renderPartsGrid(document.getElementById("fleetDetailParts"), ship);

    // Render cargo
    renderCargoSection(document.getElementById("fleetDetailCargo"), ship);
  }

  /* ── Parts grid rendering ─────────────────────────────── */
  function renderPartsGrid(container, ship) {
    if (!container) return;
    const parts = Array.isArray(ship.parts) ? ship.parts.filter(Boolean) : [];
    container.innerHTML = "";
    if (!parts.length) { container.innerHTML = '<span class="muted">No parts</span>'; return; }

    if (!itemDisplay) {
      container.textContent = parts.map(p => p.name || p.type || "Part").join(", ");
      return;
    }

    // Sort: non-thrusters first, then thrusters
    const isThruster = (p) => {
      const type = String(p?.type || "").toLowerCase();
      const name = String(p?.name || "").toLowerCase();
      return type === "thruster" || name.includes("thruster");
    };
    const sorted = parts.filter(p => !isThruster(p)).concat(parts.filter(p => isThruster(p)));

    for (const part of sorted) {
      const p = typeof part === "object" && part ? part : {};
      const name = String(p.name || p.type || "Part");
      const category = String(p.type || p.category_id || "module").toLowerCase();
      const tooltipLines = [];
      if (Number(p.thrust_kn) > 0) tooltipLines.push(["Thrust", `${Number(p.thrust_kn).toFixed(0)} kN`]);
      if (Number(p.isp_s) > 0) tooltipLines.push(["ISP", `${Number(p.isp_s).toFixed(0)} s`]);
      if (Number(p.capacity_m3) > 0) tooltipLines.push(["Capacity", `${Number(p.capacity_m3).toFixed(2)} m³`]);
      if (Number(p.thermal_mw) > 0) tooltipLines.push(["Power", `${Number(p.thermal_mw).toFixed(1)} MWth`]);
      if (Number(p.water_kg) > 0) tooltipLines.push(["Water", fmtKg(Number(p.water_kg))]);
      if (Number(p.mass_kg) > 0) tooltipLines.push(["Mass", fmtKg(Number(p.mass_kg))]);
      if (Number(p.electric_mw) > 0) tooltipLines.push(["Electric", `${Number(p.electric_mw).toFixed(1)} MWe`]);
      if (Number(p.heat_rejection_mw) > 0) tooltipLines.push(["Rad. rejection", `${Number(p.heat_rejection_mw).toFixed(1)} MWth`]);

      const cell = itemDisplay.createGridCell({
        label: name,
        iconSeed: p.item_id || name,
        category: category,
        mass_kg: Number(p.mass_kg) || 0,
        subtitle: category,
        branch: p.branch || "",
        family: p.thruster_family || "",
        techLevel: p.tech_level || "",
        tooltipLines: tooltipLines.length ? tooltipLines : undefined,
      });

      // Right-click context menu on part cells
      cell.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        showContextMenu(name, [
          { label: "View Part Details", disabled: true },
        ], e.clientX, e.clientY);
      });

      container.appendChild(cell);
    }
  }

  /* ── Cargo section rendering ──────────────────────────── */
  function renderCargoSection(container, ship) {
    if (!container) return;
    const containers = Array.isArray(ship.inventory_containers) ? ship.inventory_containers : [];
    const items = Array.isArray(ship.inventory_items) ? ship.inventory_items : [];
    const summary = ship.inventory_capacity_summary || {};

    if (!containers.length && !items.length) {
      container.innerHTML = '<span class="muted">No cargo</span>';
      return;
    }

    let html = "";

    // Capacity summary
    const totalCap = Number(summary.total_capacity_m3 || 0);
    const totalUsed = Number(summary.total_used_m3 || 0);
    const usedPct = totalCap > 0 ? Math.min(100, (totalUsed / totalCap) * 100) : 0;
    if (totalCap > 0) {
      html += `
        <div class="fleetCargoSummary">
          <span>Capacity: ${totalUsed.toFixed(2)} / ${totalCap.toFixed(2)} m³ (${usedPct.toFixed(0)}%)</span>
          <div class="bar" style="width:160px; display:inline-block; vertical-align:middle; margin-left:8px;">
            <div class="barFill" style="width:${usedPct.toFixed(1)}%;"></div>
          </div>
        </div>
      `;
    }

    container.innerHTML = html;

    // Render resource items as grid cells
    if (items.length && itemDisplay) {
      const grid = document.createElement("div");
      grid.className = "fleetPartsGrid";

      for (const item of items) {
        const name = String(item.resource_name || item.resource_id || "Resource");
        const massKg = Number(item.mass_kg || item.cargo_mass_kg || 0);
        const volM3 = Number(item.volume_m3 || item.used_m3 || 0);
        const amount = Number(item.amount || item.quantity || 0);

        const tooltipLines = [];
        if (massKg > 0) tooltipLines.push(["Mass", fmtKg(massKg)]);
        if (volM3 > 0) tooltipLines.push(["Volume", `${volM3.toFixed(2)} m³`]);
        if (amount > 0) tooltipLines.push(["Amount", amount.toFixed(1)]);

        const cell = itemDisplay.createGridCell({
          label: name,
          iconSeed: item.resource_id || name,
          category: "resource",
          mass_kg: massKg,
          subtitle: "resource",
          tooltipLines: tooltipLines.length ? tooltipLines : undefined,
        });
        grid.appendChild(cell);
      }
      container.appendChild(grid);
    } else if (items.length) {
      const list = document.createElement("div");
      list.className = "muted";
      list.textContent = items.map(i => `${i.resource_name || i.resource_id} (${fmtKg(i.mass_kg || i.cargo_mass_kg || 0)})`).join(", ");
      container.appendChild(list);
    }

    // Container breakdown
    if (containers.length) {
      const breakdown = document.createElement("div");
      breakdown.className = "fleetContainerBreakdown";
      for (const c of containers) {
        const cName = String(c.container_name || c.part_name || `Container #${c.container_index}`);
        const capM3 = Number(c.capacity_m3 || 0);
        const usedM3 = Number(c.used_m3 || c.cargo_used_m3 || 0);
        const resName = String(c.resource_name || c.resource_id || "Empty");
        const cMassKg = Number(c.cargo_mass_kg || 0);
        const pct = capM3 > 0 ? Math.min(100, (usedM3 / capM3) * 100) : 0;

        const row = document.createElement("div");
        row.className = "fleetContainerRow";
        row.innerHTML = `
          <span class="fleetContainerName">${escapeHtml(cName)}</span>
          <span class="fleetContainerContent">${cMassKg > 0 ? `${escapeHtml(resName)}: ${fmtKg(cMassKg)}` : '<span class="muted">Empty</span>'}</span>
          <span class="fleetContainerBar">
            <span class="bar" style="width:80px; display:inline-block; vertical-align:middle;">
              <span class="barFill" style="width:${pct.toFixed(1)}%;"></span>
            </span>
            <span class="muted">${usedM3.toFixed(2)}/${capM3.toFixed(2)} m³</span>
          </span>
        `;
        breakdown.appendChild(row);
      }
      container.appendChild(breakdown);
    }
  }

  /* ── Delta-v panel ────────────────────────────────────── */
  function renderDeltaVPanel(ship) {
    const dryMass = Number(ship.dry_mass_kg || 0);
    const fuel = Number(ship.fuel_kg || 0);
    const fuelCap = Number(ship.fuel_capacity_kg || 0);
    const wetMass = dryMass + fuel;
    const isp = Number(ship.isp_s || 0);
    const thrust = Number(ship.thrust_kn || 0);
    const dv = Number(ship.delta_v_remaining_m_s || 0);
    const accelG = wetMass > 0 ? (thrust * 1000) / (wetMass * 9.80665) : 0;
    const fPct = fuelPct(fuel, fuelCap);
    const dvCls = dv > 0 ? "pbPositive" : "pbNeutral";

    return `
      <div class="fleetSection">
        <div class="powerBalancePanel powerBalanceFleet">
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
      </div>
    `;
  }

  /* ── Power balance panel ──────────────────────────────── */
  function renderPowerPanel(ship) {
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
      ? `<div class="pbOverheatBanner"><span class="pbOverheatIcon">⚠</span><span class="pbOverheatText">OVERHEATING — ${wasteSurplus.toFixed(1)} MWth unradiated waste heat.</span></div>`
      : "";
    const genThrottled = genThrottle < 1 && electricRated > 0;

    return `
      <div class="fleetSection">
        <div class="powerBalancePanel powerBalanceFleet${isOverheating ? ' pbOverheating' : ''}">
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
      </div>
    `;
  }

  /* ── Data sync ────────────────────────────────────────── */
  async function sync() {
    const tClient = Date.now() / 1000;
    try {
      const resp = await fetch("/api/state", { cache: "no-store" });
      const data = await resp.json();
      serverSyncGameS = data.server_time || tClient;
      clientSyncRealS = tClient;
      const parsedScale = Number(data.time_scale);
      timeScale = Number.isFinite(parsedScale) && parsedScale >= 0 ? parsedScale : 1;

      allShips = data.ships || [];
      const moving = allShips.filter((s) => s.status === "transit").length;
      summaryEl.textContent = `${allShips.length} ships · ${moving} in transit`;

      renderShipList(allShips);

      // Update detail pane if a ship is selected
      if (selectedShipId) {
        const selected = allShips.find((s) => s.id === selectedShipId);
        if (selected) {
          renderDetail(selected);
        } else {
          // Ship was deleted
          selectedShipId = null;
          detailEl.innerHTML = '<div class="fleetDetailEmpty muted">Ship no longer exists. Select another.</div>';
        }
      }
    } catch (err) {
      summaryEl.textContent = "Failed to load fleet data";
    }
  }

  sync();
  setInterval(sync, 2000);
})();
