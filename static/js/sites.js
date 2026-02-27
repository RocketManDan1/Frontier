/**
 * Sites & Industry — Overview + Industrial tabs.
 *
 * Overview: All locations with inventory/equipment summaries, click for detail.
 * Industrial: Deploy equipment, start/cancel production & mining jobs,
 *             production chain flow visualization.
 */
(function () {
  "use strict";

  const itemDisplay = window.ItemDisplay;

  /* ── State ───────────────────────────────────────────────── */

  let allSites = [];
  let selectedSiteId = null;
  let currentTab = "overview";       // "overview" | "industrial"
  let currentFilter = "all";
  let overviewGroupByBody = false;
  let cargoGroupByBody = false;
  let cargoOnlyWithCargo = false;
  let industryLocationId = null;
  let industryData = null;
  let pollTimer = null;


  /* ── Game-time sync (mirrors clock.js) ───────────────────── */

  let serverSyncGameS = Date.now() / 1000;
  let clientSyncRealS = Date.now() / 1000;
  let timeScale = 1;

  function serverNow() {
    const realNow = Date.now() / 1000;
    return serverSyncGameS + (realNow - clientSyncRealS) * timeScale;
  }

  async function syncClock() {
    try {
      const t = Date.now() / 1000;
      const r = await fetch("/api/time", { cache: "no-store" });
      if (!r.ok) return;
      const d = await r.json();
      serverSyncGameS = Number(d.server_time) || t;
      clientSyncRealS = t;
      timeScale = Number.isFinite(Number(d.time_scale)) ? Number(d.time_scale) : 1;
    } catch (_) {}
  }

  /* ── Utilities ───────────────────────────────────────────── */

  function esc(v) { return itemDisplay ? itemDisplay.escapeHtml(v) : String(v || ""); }
  function fmtKg(v) { return itemDisplay ? itemDisplay.fmtKg(v) : (function(){ var val = Math.max(0, Number(v||0)); return val >= 5000 ? (val/1000).toFixed(1)+' t' : val.toFixed(0)+' kg'; })(); }
  function fmtM3(v) { return Math.max(0, Number(v) || 0).toFixed(2) + ' m³'; }
  function fmtPct(v) { return `${(Number(v||0) * 100).toFixed(1)}%`; }
  function fmtDuration(s) {
    s = Math.max(0, Math.round(Number(s) || 0));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s/60)}m ${s%60}s`;
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}h ${m}m`;
  }

  /* ── API Helpers ─────────────────────────────────────────── */

  async function fetchJSON(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
    return r.json();
  }

  async function postJSON(url, body) {
    return fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }



  /* ══════════════════════════════════════════════════════════
     TAB SWITCH
     ══════════════════════════════════════════════════════════ */

  function initTabSwitching() {
    document.querySelectorAll(".siteSubTab").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".siteSubTab").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        currentTab = btn.dataset.tab;
        document.getElementById("tabOverview").style.display = currentTab === "overview" ? "" : "none";
        document.getElementById("tabIndustrial").style.display = currentTab === "industrial" ? "" : "none";
        document.getElementById("tabCargo").style.display = currentTab === "cargo" ? "" : "none";
        if (currentTab === "industrial") loadIndustryContent();
        if (currentTab === "cargo") renderCargoSitesTable();
      });
    });
  }

  /* ── Body group ordering (inner → outer) ──────────────── */

  const BODY_ORDER = ["Sun", "Mercury", "Venus", "Earth", "Luna", "Mars", "Ceres", "Vesta", "Pallas", "Hygiea"];
  function bodySort(a, b) {
    const ai = BODY_ORDER.indexOf(a);
    const bi = BODY_ORDER.indexOf(b);
    return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi) || a.localeCompare(b);
  }

  /* ══════════════════════════════════════════════════════════
     OVERVIEW TAB
     ══════════════════════════════════════════════════════════ */

  async function loadSites() {
    try {
      const data = await fetchJSON("/api/sites");
      allSites = data.sites || [];
      renderSitesTable();
      populateIndustryLocationSelect();
    } catch (e) {
      console.error("Failed to load sites:", e);
    }
  }

  function renderSitesTable() {
    const tbody = document.getElementById("sitesTableBody");
    const search = (document.getElementById("sitesSearchInput").value || "").toLowerCase();
    const filtered = allSites.filter(s => {
      if (search && !s.name.toLowerCase().includes(search) && !s.id.toLowerCase().includes(search)) return false;
      if (currentFilter === "surface") return s.is_surface_site;
      if (currentFilter === "orbital") return !s.is_surface_site;
      if (currentFilter === "active") {
        const eq = s.equipment || {};
        return (eq.refinery || {}).total > 0 || (eq.constructor || {}).total > 0 || (eq.robonaut || {}).total > 0 || s.active_jobs > 0;
      }
      return true;
    });

    if (!filtered.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="muted" style="text-align:center;padding:24px">No locations match filter</td></tr>`;
      return;
    }

    function siteRow(s) {
      const eq = s.equipment || {};
      const refCount = (eq.refinery || {}).total || 0;
      const conCount = (eq.constructor || {}).total || 0;
      const roboCount = (eq.robonaut || {}).total || 0;
      const equipStr = (refCount || conCount || roboCount)
        ? `<span class="eqBadge ref">${refCount}R</span><span class="eqBadge con">${conCount}C</span><span class="eqBadge con">${roboCount}B</span>`
        : '<span class="muted">—</span>';

      const invStr = s.inventory.stack_count > 0
        ? `${s.inventory.stack_count} <span class="muted">(${fmtKg(s.inventory.total_mass_kg)})</span>`
        : '<span class="muted">—</span>';

      const jobStr = s.active_jobs > 0
        ? `<span class="badge badgeActive">${s.active_jobs}</span>`
        : '<span class="muted">—</span>';

      const typeStr = s.is_surface_site
        ? '<span class="badge badgeSurface">Surface</span>'
        : '<span class="badge badgeOrbital">Orbital</span>';

      const sel = selectedSiteId === s.id ? ' class="selected"' : '';

      return `<tr data-site-id="${esc(s.id)}"${sel}>
        <td class="siteName">${esc(s.name)}</td>
        <td>${typeStr}</td>
        <td>${s.ships_docked || '<span class="muted">—</span>'}</td>
        <td>${invStr}</td>
        <td>${equipStr}</td>
        <td>${jobStr}</td>
      </tr>`;
    }

    if (overviewGroupByBody) {
      const groups = {};
      filtered.forEach(s => {
        const body = s.body_name || "Other";
        if (!groups[body]) groups[body] = [];
        groups[body].push(s);
      });
      const order = Object.keys(groups).sort(bodySort);
      tbody.innerHTML = order.map(body =>
        `<tr class="bodyGroupHeader"><td colspan="6">${esc(body)}</td></tr>` +
        groups[body].map(siteRow).join("")
      ).join("");
    } else {
      tbody.innerHTML = filtered.map(siteRow).join("");
    }

    // Click handler
    tbody.querySelectorAll("tr[data-site-id]").forEach(tr => {
      tr.addEventListener("click", () => selectSite(tr.dataset.siteId));
    });
  }

  async function selectSite(siteId) {
    selectedSiteId = siteId;
    renderSitesTable();

    const placeholder = document.getElementById("siteDetailPlaceholder");
    const content = document.getElementById("siteDetailContent");
    placeholder.style.display = "none";
    content.style.display = "";

    try {
      const data = await fetchJSON(`/api/sites/${encodeURIComponent(siteId)}`);
      renderSiteDetail(data);
    } catch (e) {
      content.innerHTML = `<div class="muted">Failed to load: ${esc(e.message)}</div>`;
    }
  }

  function renderSiteDetail(site) {
    document.getElementById("siteDetailName").textContent = site.name;
    const typeEl = document.getElementById("siteDetailType");
    if (site.is_surface_site) {
      typeEl.textContent = "Surface Site";
      typeEl.className = "badge badgeSurface";
    } else {
      typeEl.textContent = "Orbital";
      typeEl.className = "badge badgeOrbital";
    }

    // Info grid
    const infoGrid = document.getElementById("siteInfoGrid");
    let infoHtml = `<div class="infoRow"><span class="infoLabel">ID</span><span class="infoValue">${esc(site.id)}</span></div>`;
    if (site.body_id) infoHtml += `<div class="infoRow"><span class="infoLabel">Body</span><span class="infoValue">${esc(site.body_id)}</span></div>`;
    if (site.surface) {
      infoHtml += `<div class="infoRow"><span class="infoLabel">Gravity</span><span class="infoValue">${site.surface.gravity_m_s2.toFixed(2)} m/s²</span></div>`;
      infoHtml += `<div class="infoRow"><span class="infoLabel">Orbit Node</span><span class="infoValue">${esc(site.surface.orbit_node_id)}</span></div>`;
    }
    infoHtml += `<div class="infoRow"><span class="infoLabel">Ships</span><span class="infoValue">${(site.ships || []).length}</span></div>`;
    infoGrid.innerHTML = infoHtml;

    // Resource deposits
    const depSection = document.getElementById("siteDepositsSection");
    const depGrid = document.getElementById("siteDepositsGrid");
    if (site.minable_resources && site.minable_resources.length) {
      depSection.style.display = "";
      depGrid.innerHTML = site.minable_resources.map(r => `
        <div class="depositRow">
          <span class="depositName">${esc(r.name)}</span>
          <div class="depositBar">
            <div class="depositBarFill" style="width:${Math.min(100, r.mass_fraction_pct * 2)}%"></div>
          </div>
          <span class="depositPct">${r.mass_fraction_pct}%</span>
        </div>
      `).join("");
    } else {
      depSection.style.display = "none";
    }

    // Inventory
    const invGrid = document.getElementById("siteInventoryGrid");
    const invEmpty = document.getElementById("siteInventoryEmpty");
    const inv = site.inventory || {};
    const resources = inv.resources || [];
    const parts = inv.parts || [];
    if (resources.length || parts.length) {
      invEmpty.style.display = "none";
      invGrid.innerHTML = "";
      resources.forEach(r => {
        const cell = itemDisplay.createGridCell({
          label: r.name, iconSeed: r.item_id, itemId: r.item_id,
          category: r.category_id || "resource",
          phase: r.phase || "",
          icon: r.icon || "",
          mass_kg: r.mass_kg, quantity: r.quantity, subtitle: fmtKg(r.mass_kg),
        });
        invGrid.appendChild(cell);
      });
      parts.forEach(p => {
        const part = p.part || {};
        const cat = part.category_id || part.type || "generic";
        const cell = itemDisplay.createGridCell({
          label: p.name, iconSeed: p.item_id, itemId: p.item_id,
          category: cat,
          mass_kg: p.mass_kg, quantity: p.quantity,
        });
        invGrid.appendChild(cell);
      });
    } else {
      invEmpty.style.display = "";
      invGrid.innerHTML = "";
    }

    // Equipment
    const eqGrid = document.getElementById("siteEquipmentGrid");
    const eqEmpty = document.getElementById("siteEquipmentEmpty");
    const equipment = site.equipment || [];
    if (equipment.length) {
      eqEmpty.style.display = "none";
      eqGrid.innerHTML = "";
      equipment.forEach(eq => {
        const cell = itemDisplay.createGridCell({
          label: eq.name, iconSeed: eq.item_id, itemId: eq.item_id,
          category: eq.category,
          subtitle: eq.status === "active" ? "⚡ Active" : "Idle",
          className: eq.status === "active" ? "eqActive" : "",
        });
        eqGrid.appendChild(cell);
      });
    } else {
      eqEmpty.style.display = "";
      eqGrid.innerHTML = "";
    }

    // Ships
    const shipsList = document.getElementById("siteShipsList");
    const shipsEmpty = document.getElementById("siteShipsEmpty");
    const ships = site.ships || [];
    if (ships.length) {
      shipsEmpty.style.display = "none";
      shipsList.innerHTML = ships.map(s =>
        `<div class="siteShipRow">${esc(s.name)}</div>`
      ).join("");
    } else {
      shipsEmpty.style.display = "";
      shipsList.innerHTML = "";
    }
  }

  /* ══════════════════════════════════════════════════════════
     INDUSTRIAL TAB
     ══════════════════════════════════════════════════════════ */

  let constructorsCollapsed = false;

  function populateIndustryLocationSelect() {
    const sel = document.getElementById("industryLocationSelect");
    const current = sel.value;

    const industrial = [];      // deployed constructors or refineries
    const otherPresence = [];   // ships docked / robonauts / active jobs but no constructor/refinery
    const prospectedOnly = [];  // prospected but no presence
    allSites.forEach(s => {
      const eq = s.equipment || {};
      const hasConstructorOrRefinery = ((eq.refinery || {}).total || 0) + ((eq.constructor || {}).total || 0) > 0;
      const hasRobonaut = ((eq.robonaut || {}).total || 0) > 0;
      const hasShipLanded = Number(s.ships_docked || 0) > 0;
      const hasActivity = Number(s.active_jobs || 0) > 0;
      const hasPresence = hasConstructorOrRefinery || hasRobonaut || hasShipLanded || hasActivity;
      const isProspected = !!s.is_prospected;

      if (!hasPresence && !isProspected) return;
      if (hasConstructorOrRefinery) industrial.push(s);
      else if (hasPresence) otherPresence.push(s);
      else prospectedOnly.push(s);
    });

    let html = '<option value="">— Select a location —</option>';
    if (industrial.length) {
      html += '<optgroup label="Industrial Sites">';
      industrial.forEach(s => html += `<option value="${esc(s.id)}">${esc(s.name)}</option>`);
      html += '</optgroup>';
    }
    if (otherPresence.length) {
      html += '<optgroup label="Occupied">';
      otherPresence.forEach(s => html += `<option value="${esc(s.id)}">${esc(s.name)}</option>`);
      html += '</optgroup>';
    }
    if (prospectedOnly.length) {
      html += '<optgroup label="Prospected Sites">';
      prospectedOnly.forEach(s => html += `<option value="${esc(s.id)}">${esc(s.name)}</option>`);
      html += '</optgroup>';
    }
    if (!industrial.length && !otherPresence.length && !prospectedOnly.length) {
      html += '<option value="" disabled>No prospected or occupied sites</option>';
    }

    sel.innerHTML = html;
    if (current) sel.value = current;

    if (sel.value !== current) {
      industryLocationId = sel.value || null;
      if (!industryLocationId) {
        document.getElementById("industryContent").style.display = "none";
      }
    }
  }

  function initIndustryLocationSelect() {
    document.getElementById("industryLocationSelect").addEventListener("change", e => {
      industryLocationId = e.target.value || null;
      if (industryLocationId) {
        loadIndustryContent();
      } else {
        document.getElementById("industryContent").style.display = "none";
      }
    });
  }

  async function loadIndustryContent() {
    if (!industryLocationId) return;
    document.getElementById("industryContent").style.display = "";

    try {
      industryData = await fetchJSON(`/api/industry/${encodeURIComponent(industryLocationId)}`);
      renderIndustry();
    } catch (e) {
      console.error("Failed to load industry data:", e);
    }
  }

  function renderIndustry() {
    if (!industryData) return;
    const renderers = [
      ["PowerBalance", renderPowerBalance],
      ["Constructors", renderConstructors],
      ["Mining", renderMiningSummary],
      ["RefinerySlots", renderRefinerySlots],
      ["ConstructionQueue", renderConstructionQueue],
      ["ProductionChain", renderProductionChain],
      ["JobHistory", renderJobHistory],
    ];
    for (const [name, fn] of renderers) {
      try { fn(); } catch (e) { console.error(`renderIndustry: ${name} failed:`, e); }
    }
  }

  /* ── Constructors (collapsible, toggle switches) ─────── */

  function renderConstructors() {
    const list = document.getElementById("constructorsList");
    const badge = document.getElementById("constructorCountBadge");
    const summary = document.getElementById("constructorSummary");
    const equipment = industryData.equipment || [];
    const constructors = equipment.filter(e => e.category === "constructor" || e.category === "robonaut");

    badge.textContent = constructors.length;

    if (!constructors.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No constructors deployed</div>';
      summary.textContent = "";
      return;
    }

    // Summary stats
    const miningCount = constructors.filter(c => c.mode === "mine").length;
    const constructCount = constructors.filter(c => c.mode === "construct").length;
    const idleCount = constructors.filter(c => c.mode === "idle").length;
    const totalMiningRate = constructors
      .filter(c => c.mode === "mine")
      .reduce((sum, c) => sum + (c.config?.mining_rate_kg_per_hr || 0), 0);
    const totalBuildRate = constructors
      .filter(c => c.mode === "construct")
      .reduce((sum, c) => sum + (c.config?.construction_rate_kg_per_hr || 0), 0);

    let sumParts = [];
    if (miningCount) sumParts.push(`${miningCount} mining (${totalMiningRate} kg/hr)`);
    if (constructCount) sumParts.push(`${constructCount} building (${totalBuildRate} kg/hr)`);
    if (idleCount) sumParts.push(`${idleCount} idle`);
    summary.textContent = sumParts.join(" · ");

    list.innerHTML = constructors.map(eq => {
      const cfg = eq.config || {};
      const mode = eq.mode || "idle";
      const isRobonaut = eq.category === "robonaut";
      const mineChecked = mode === "mine" ? "checked" : "";
      const constructChecked = mode === "construct" ? "checked" : "";
      const idleChecked = mode === "idle" ? "checked" : "";
      const miningRate = cfg.mining_rate_kg_per_hr || 0;
      const buildRate = cfg.construction_rate_kg_per_hr || 0;

      let modeHtml;
      if (isRobonaut) {
        // Robonauts: mine/idle toggle only
        modeHtml = `
          <div class="constructorModeSwitch">
            <label class="modeOption ${mode === 'mine' ? 'active' : ''}">
              <input type="radio" name="mode_${eq.id}" value="mine" ${mineChecked}> ⛏ Mine
            </label>
            <label class="modeOption ${mode === 'idle' ? 'active' : ''}">
              <input type="radio" name="mode_${eq.id}" value="idle" ${idleChecked}> ⏸ Idle
            </label>
          </div>`;
      } else {
        modeHtml = `
          <div class="constructorModeSwitch">
            <label class="modeOption ${mode === 'mine' ? 'active' : ''}">
              <input type="radio" name="mode_${eq.id}" value="mine" ${mineChecked}> ⛏ Mine
            </label>
            <label class="modeOption ${mode === 'construct' ? 'active' : ''}">
              <input type="radio" name="mode_${eq.id}" value="construct" ${constructChecked}> 🔧 Build
            </label>
            <label class="modeOption ${mode === 'idle' ? 'active' : ''}">
              <input type="radio" name="mode_${eq.id}" value="idle" ${idleChecked}> ⏸ Idle
            </label>
          </div>`;
      }

      const statsHtml = `<span class="eqDetail">${miningRate} kg/hr mine</span>` +
        (!isRobonaut ? `<span class="eqDetail">${buildRate} kg/hr build</span>` : '') +
        `<span class="eqDetail">${cfg.electric_mw || 0} MWe</span>`;

      const totalMinedHtml = eq.mining_total_kg > 0 ? `<span class="muted">Total mined: ${fmtKg(eq.mining_total_kg)}</span>` : '';

      return `<div class="constructorRow" data-equip-id="${eq.id}">
        <div class="constructorInfo">
          <span class="constructorIcon">${isRobonaut ? '🤖' : '⛏'}</span>
          <div class="constructorDetails">
            <div class="constructorName">${esc(eq.name)}</div>
            <div class="constructorStats">${statsHtml}</div>
            ${totalMinedHtml}
          </div>
        </div>
        ${modeHtml}
        <button class="btnSmall btnUndeploy" data-equip-id="${eq.id}" ${mode !== 'idle' ? 'disabled title="Set to idle first"' : ''}>Undeploy</button>
      </div>`;
    }).join("");

    // Wire mode switches
    list.querySelectorAll('input[type="radio"]').forEach(radio => {
      radio.addEventListener("change", async () => {
        const row = radio.closest(".constructorRow");
        const equipId = row.dataset.equipId;
        try {
          await postJSON("/api/industry/constructor/mode", {
            equipment_id: equipId,
            mode: radio.value,
          });
          loadIndustryContent();
          loadSites();
        } catch (e) {
          alert("Failed: " + e.message);
          loadIndustryContent();
        }
      });
    });

    // Wire undeploy buttons
    list.querySelectorAll(".btnUndeploy").forEach(btn => {
      btn.addEventListener("click", () => undeployEquipment(btn.dataset.equipId));
    });

    // Collapsible header
    const header = document.getElementById("constructorsSectionHeader");
    const body = document.getElementById("constructorsSectionBody");
    header.onclick = () => {
      constructorsCollapsed = !constructorsCollapsed;
      body.style.display = constructorsCollapsed ? "none" : "";
      header.querySelector(".collapseToggle").textContent = constructorsCollapsed ? "▸" : "▾";
    };
  }

  /* ── Mining Summary ──────────────────────────────────────── */

  function renderMiningSummary() {
    const panel = document.getElementById("industryMiningPanel");
    const content = document.getElementById("industryMiningContent");

    if (!industryData.is_surface_site) {
      panel.style.display = "none";
      return;
    }
    panel.style.display = "";

    const equipment = industryData.equipment || [];
    const miners = equipment.filter(e => e.mode === "mine" && (e.category === "constructor" || e.category === "robonaut"));
    const minable = industryData.minable_resources || [];

    if (!miners.length) {
      if (!industryData.is_prospected) {
        content.innerHTML = '<div class="muted" style="padding:8px">⚠ Site not yet prospected</div>';
      } else {
        content.innerHTML = '<div class="muted" style="padding:8px">No constructors set to mine mode</div>';
      }
      return;
    }

    const totalRate = miners.reduce((s, m) => s + (m.config?.mining_rate_kg_per_hr || 0), 0);

    let html = `<div class="miningSummaryHeader">Total mining rate: <strong>${totalRate} kg/hr</strong> (${miners.length} units)</div>`;
    html += '<div class="miningDistribution">';
    minable.forEach(r => {
      const rateForResource = totalRate * (r.mass_fraction || 0);
      html += `<div class="miningDistRow">
        <span class="miningDistName">${esc(r.name)}</span>
        <div class="depositBar"><div class="depositBarFill" style="width:${Math.min(100, r.mass_fraction_pct * 2)}%"></div></div>
        <span class="miningDistRate">${rateForResource.toFixed(1)} kg/hr</span>
        <span class="depositPct">${r.mass_fraction_pct}%</span>
      </div>`;
    });
    html += '</div>';

    content.innerHTML = html;
  }

  /* ── Refinery Slots ──────────────────────────────────────── */

  function renderRefinerySlots() {
    const list = document.getElementById("refinerySlotsList");
    const badge = document.getElementById("refinerySlotCountBadge");
    const slots = industryData.refinery_slots || [];

    badge.textContent = slots.length;

    if (!slots.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No refineries deployed. Deploy refineries to get refining slots.</div>';
      return;
    }

    list.innerHTML = slots.map((slot, idx) => {
      const hasRecipe = !!slot.recipe_id;
      const isActive = slot.status === "active";

      let statsHtml = "";
      if (hasRecipe) {
        const produced = slot.cumulative_output_qty || 0;
        const batches = slot.batches_available || 0;
        const prodLabel = produced >= 1000 ? `${(produced / 1000).toFixed(1)} t` : `${produced.toFixed(0)} kg`;
        statsHtml = `<div class="slotStats">`
          + `<span class="slotProduced" title="Total produced since recipe assigned">${prodLabel} produced</span>`
          + `<span class="slotBatches ${batches === 0 ? 'noBatches' : ''}" title="Jobs worth of raw materials in storage">${batches} batch${batches !== 1 ? 'es' : ''} avail</span>`
          + `</div>`;
      }

      const recipeName = hasRecipe ? esc(slot.recipe_name || slot.recipe_id) : '<span class="muted">Empty — click to assign recipe</span>';

      return `<div class="refinerySlotRow ${isActive ? 'slotActive' : ''}" data-slot-id="${slot.id}" data-slot-idx="${idx}" draggable="true">
        <div class="slotDragHandle" title="Drag to reorder priority">⠿</div>
        <div class="slotPriority">#${idx + 1}</div>
        <div class="slotEquipName">${esc(slot.equipment_name)}</div>
        <div class="slotRecipe ${hasRecipe ? '' : 'slotEmpty'}" data-slot-id="${slot.id}">${recipeName}</div>
        ${statsHtml}
        ${hasRecipe ? `<button class="btnSmall btnClearSlot" data-slot-id="${slot.id}">${isActive ? 'Clear Next' : 'Clear'}</button>` : ''}
      </div>`;
    }).join("");

    // Wire click-to-assign on any slot (recipe change takes effect after current job)
    list.querySelectorAll(".slotRecipe").forEach(el => {
      el.addEventListener("click", () => {
        const slotId = el.dataset.slotId;
        const slot = slots.find(s => s.id === slotId);
        if (slot) {
          openRecipeSelectModal("refinery", slotId, slot.specialization);
        }
      });
    });

    // Wire clear buttons
    list.querySelectorAll(".btnClearSlot").forEach(btn => {
      btn.addEventListener("click", async () => {
        try {
          await postJSON("/api/industry/refinery/assign", { slot_id: btn.dataset.slotId, recipe_id: "" });
          loadIndustryContent();
        } catch (e) {
          alert("Failed: " + e.message);
        }
      });
    });

    // Drag-and-drop reordering
    initSlotDragDrop(list, slots);
  }

  function initSlotDragDrop(container, slots) {
    let dragEl = null;
    container.querySelectorAll(".refinerySlotRow").forEach(row => {
      row.addEventListener("dragstart", e => {
        dragEl = row;
        row.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
      });
      row.addEventListener("dragend", () => {
        if (dragEl) dragEl.classList.remove("dragging");
        dragEl = null;
      });
      row.addEventListener("dragover", e => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        const rect = row.getBoundingClientRect();
        const mid = rect.top + rect.height / 2;
        if (e.clientY < mid) {
          row.style.borderTop = "2px solid var(--accent)";
          row.style.borderBottom = "";
        } else {
          row.style.borderBottom = "2px solid var(--accent)";
          row.style.borderTop = "";
        }
      });
      row.addEventListener("dragleave", () => {
        row.style.borderTop = "";
        row.style.borderBottom = "";
      });
      row.addEventListener("drop", async e => {
        e.preventDefault();
        row.style.borderTop = "";
        row.style.borderBottom = "";
        if (!dragEl || dragEl === row) return;

        // Compute new order
        const rows = [...container.querySelectorAll(".refinerySlotRow")];
        const fromIdx = rows.indexOf(dragEl);
        const toIdx = rows.indexOf(row);
        if (fromIdx < 0 || toIdx < 0) return;

        const ids = rows.map(r => r.dataset.slotId);
        const [moved] = ids.splice(fromIdx, 1);
        ids.splice(toIdx, 0, moved);

        try {
          await postJSON("/api/industry/refinery/reorder", {
            location_id: industryLocationId,
            slot_ids: ids,
          });
          loadIndustryContent();
        } catch (e) {
          alert("Reorder failed: " + e.message);
        }
      });
    });
  }

  /* ── Construction Queue ──────────────────────────────────── */

  function renderConstructionQueue() {
    const list = document.getElementById("constructionQueueList");
    const poolInfo = document.getElementById("constructionPoolInfo");
    const queue = industryData.construction_queue || [];
    const poolSpeed = industryData.construction_pool_speed || 0;
    const poolMult = industryData.construction_pool_mult || 0;

    poolInfo.textContent = poolSpeed > 0
      ? `Pool: ${poolSpeed} kg/hr (${poolMult.toFixed(2)}× speed)`
      : "No constructors in build mode";

    // Wire add-to-queue button (must be before early return)
    const addBtn = document.getElementById("btnAddConstruction");
    addBtn.onclick = () => openRecipeSelectModal("construction", null, null);

    if (!queue.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">Construction queue empty. Add recipes to build.</div>';
      return;
    }

    list.innerHTML = queue.map((item, idx) => {
      const isActive = item.status === "active";
      let progressHtml = "";
      if (isActive && item.progress != null) {
        const pct = (item.progress * 100).toFixed(0);
        const remaining = fmtDuration(item.remaining_s || 0);
        progressHtml = `
          <div class="queueProgress">
            <div class="bar"><div class="barFill" style="width:${pct}%"></div></div>
            <span class="queuePct">${pct}% · ${remaining}</span>
          </div>`;
      }

      const outputLabel = (item.output_item_id || "?").replace(/_/g, " ");

      return `<div class="constructionQueueRow ${isActive ? 'queueActive' : ''}" data-queue-id="${item.id}" draggable="${isActive ? 'false' : 'true'}">
        <div class="queueOrder">${isActive ? '▶' : `#${idx + 1}`}</div>
        <div class="queueInfo">
          <div class="queueName">${esc(item.recipe_name || item.recipe_id)}</div>
          <div class="queueOutput">→ ${esc(outputLabel)} ×${item.output_qty || 1}</div>
        </div>
        ${progressHtml}
        <button class="btnSmall btnDequeue" data-queue-id="${item.id}">${isActive ? 'Cancel' : 'Remove'}</button>
      </div>`;
    }).join("");

    // Wire dequeue buttons
    list.querySelectorAll(".btnDequeue").forEach(btn => {
      btn.addEventListener("click", async () => {
        const msg = "Remove this item from the queue?";
        if (!confirm(msg)) return;
        try {
          await postJSON("/api/industry/construction/dequeue", { queue_id: btn.dataset.queueId });
          loadIndustryContent();
          loadSites();
        } catch (e) {
          alert("Failed: " + e.message);
        }
      });
    });

    // Drag-and-drop reordering for queued (non-active) items
    initQueueDragDrop(list, queue);
  }

  function initQueueDragDrop(container, queue) {
    let dragEl = null;
    container.querySelectorAll(".constructionQueueRow[draggable='true']").forEach(row => {
      row.addEventListener("dragstart", e => {
        dragEl = row;
        row.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
      });
      row.addEventListener("dragend", () => {
        if (dragEl) dragEl.classList.remove("dragging");
        dragEl = null;
      });
      row.addEventListener("dragover", e => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
      });
      row.addEventListener("drop", async e => {
        e.preventDefault();
        if (!dragEl || dragEl === row) return;
        const rows = [...container.querySelectorAll(".constructionQueueRow")];
        const ids = rows.map(r => r.dataset.queueId);
        const fromIdx = rows.indexOf(dragEl);
        const toIdx = rows.indexOf(row);
        if (fromIdx < 0 || toIdx < 0) return;
        const [moved] = ids.splice(fromIdx, 1);
        ids.splice(toIdx, 0, moved);
        try {
          await postJSON("/api/industry/construction/reorder", {
            location_id: industryLocationId,
            queue_ids: ids,
          });
          loadIndustryContent();
        } catch (e) {
          alert("Reorder failed: " + e.message);
        }
      });
    });
  }

  /* ── Recipe Select Modal ─────────────────────────────────── */

  function openRecipeSelectModal(mode, slotId, specialization) {
    const modal = document.getElementById("recipeSelectModal");
    const title = document.getElementById("recipeSelectTitle");
    const content = document.getElementById("recipeSelectContent");
    modal.style.display = "";

    const recipes = industryData.available_recipes || [];

    if (mode === "refinery") {
      title.textContent = "Assign Refining Recipe";
      const filtered = recipes.filter(r => {
        if (r.facility_type === "shipyard") return false;
        if (specialization) {
          const cat = String(r.refinery_category || "").trim();
          if (cat && cat !== specialization && !["all_refineries"].includes(cat)) return false;
        }
        return true;
      });

      if (!filtered.length) {
        content.innerHTML = '<div class="muted" style="padding:12px">No compatible recipes</div>';
        return;
      }

      content.innerHTML = filtered.map(r => {
        const inputsHtml = (r.inputs_status || []).map(inp =>
          `<span class="recipeInput ${inp.sufficient ? "sufficient" : "insufficient"}">${esc(inp.name)}: ${inp.qty_available.toFixed(1)}/${inp.qty_needed.toFixed(1)}</span>`
        ).join(" ");
        const outName = (r.output_item_id || "?").replace(/_/g, " ");
        return `<div class="recipeSelectRow" data-recipe-id="${r.recipe_id}">
          <div class="recipeInfo">
            <div class="recipeName">${esc(r.name)}</div>
            <div class="recipeInputs">${inputsHtml}</div>
            <div class="recipeMeta"><span>Time: ${fmtDuration(r.build_time_s)}</span> <span>→ ${esc(outName)} ×${r.output_qty}</span></div>
          </div>
          <button class="btnSmall btnSelectRecipe" data-recipe-id="${r.recipe_id}" data-slot-id="${slotId}">Assign</button>
        </div>`;
      }).join("");

      content.querySelectorAll(".btnSelectRecipe").forEach(btn => {
        btn.addEventListener("click", async () => {
          try {
            await postJSON("/api/industry/refinery/assign", {
              slot_id: btn.dataset.slotId,
              recipe_id: btn.dataset.recipeId,
            });
            modal.style.display = "none";
            loadIndustryContent();
          } catch (e) {
            alert("Failed: " + e.message);
          }
        });
      });

    } else if (mode === "construction") {
      title.textContent = "Add to Construction Queue";
      const filtered = recipes.filter(r => r.facility_type === "shipyard");

      if (!filtered.length) {
        content.innerHTML = '<div class="muted" style="padding:12px">No construction recipes available</div>';
        return;
      }

      // Group by output category
      const groups = Object.create(null);
      filtered.forEach(r => {
        const cat = r.output_category || "other";
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(r);
      });

      const categoryLabels = {
        thruster: "Thrusters", reactor: "Reactors", generator: "Generators",
        radiator: "Radiators", refinery: "Refineries", constructor: "Constructors",
        robonaut: "Robonauts", other: "Other",
      };
      const catOrder = ["thruster", "reactor", "generator", "radiator", "refinery", "constructor", "robonaut", "other"];
      const sortedCats = catOrder.filter(c => groups[c]);
      Object.keys(groups).forEach(c => { if (!sortedCats.includes(c)) sortedCats.push(c); });

      let html = "";
      for (const catId of sortedCats) {
        const catRecipes = groups[catId];
        html += `<div class="recipeGroupHeader">${esc(categoryLabels[catId] || catId)}</div>`;
        catRecipes.forEach(r => {
          const inputsHtml = (r.inputs_status || []).map(inp =>
            `<span class="recipeInput ${inp.sufficient ? "sufficient" : "insufficient"}">${esc(inp.name)}: ${inp.qty_available.toFixed(1)}/${inp.qty_needed.toFixed(1)}</span>`
          ).join(" ");
          const outName = (r.output_item_id || "?").replace(/_/g, " ");
          html += `<div class="recipeSelectRow ${r.can_start ? 'canStart' : 'cantStart'}">
            <div class="recipeInfo">
              <div class="recipeName">${esc(r.name)}</div>
              <div class="recipeInputs">${inputsHtml}</div>
              <div class="recipeMeta"><span>Time: ${fmtDuration(r.build_time_s)}</span> <span>→ ${esc(outName)} ×${r.output_qty}</span></div>
            </div>
            <button class="btnSmall btnQueueRecipe" data-recipe-id="${r.recipe_id}">Queue</button>
          </div>`;
        });
      }
      content.innerHTML = html;

      content.querySelectorAll(".btnQueueRecipe").forEach(btn => {
        btn.addEventListener("click", async () => {
          try {
            await postJSON("/api/industry/construction/queue", {
              location_id: industryLocationId,
              recipe_id: btn.dataset.recipeId,
            });
            modal.style.display = "none";
            loadIndustryContent();
            loadSites();
          } catch (e) {
            alert("Failed: " + e.message);
          }
        });
      });
    }
  }

  function initRecipeSelectModal() {
    document.getElementById("recipeSelectModalClose").addEventListener("click", () => {
      document.getElementById("recipeSelectModal").style.display = "none";
    });
    document.querySelector("#recipeSelectModal .modalOverlay").addEventListener("click", () => {
      document.getElementById("recipeSelectModal").style.display = "none";
    });
  }

  /* ── Power & Thermal Balance panel ──────────────────────── */

  function renderPowerBalance() {
    const el = document.getElementById("industryPowerContent");
    const pb = industryData.power_balance;
    if (!pb) {
      el.innerHTML = '<div class="muted" style="padding:12px">No power data</div>';
      return;
    }

    const noEquip = pb.reactors.length === 0 && pb.generators.length === 0 &&
                    pb.radiators.length === 0 && pb.consumers.length === 0;
    if (noEquip) {
      el.innerHTML = '<div class="muted" style="padding:12px">Deploy reactors &amp; generators to power equipment.</div>';
      return;
    }

    // Bars
    function bar(label, value, max, unit, colorClass) {
      const pct = max > 0 ? Math.min(100, (Math.abs(value) / max) * 100) : 0;
      return `<div class="pwrBarRow">
        <span class="pwrBarLabel">${label}</span>
        <div class="pwrBarTrack">
          <div class="pwrBarFill ${colorClass}" style="width:${pct.toFixed(1)}%"></div>
        </div>
        <span class="pwrBarValue">${value.toFixed(1)} ${unit}</span>
      </div>`;
    }

    // Thermal section
    const thMax = Math.max(pb.thermal_mw_supply, pb.thermal_mw_consumed, 1);
    let html = '<div class="pwrSection">';
    html += '<div class="pwrSectionTitle">☢ Thermal</div>';
    html += bar("Reactor Output", pb.thermal_mw_supply, thMax, "MWth", "pwrFillThermal");
    html += bar("Generator Demand", pb.thermal_mw_consumed, thMax, "MWth", "pwrFillDemand");
    if (pb.thermal_mw_surplus > 0) {
      html += `<div class="pwrNote ok">+${pb.thermal_mw_surplus.toFixed(1)} MWth surplus (absorbed by surface)</div>`;
    }
    if (pb.gen_throttle < 1) {
      html += `<div class="pwrNote warn">Generators throttled to ${(pb.gen_throttle * 100).toFixed(0)}% — insufficient thermal</div>`;
    }
    html += '</div>';

    // Electric section
    const elMax = Math.max(pb.electric_mw_supply, pb.electric_mw_demand, 1);
    html += '<div class="pwrSection">';
    html += '<div class="pwrSectionTitle">⚡ Electric</div>';
    html += bar("Generator Output", pb.electric_mw_supply, elMax, "MWe", "pwrFillElectric");
    html += bar("Equipment Demand", pb.electric_mw_demand, elMax, "MWe", "pwrFillDemand");
    if (pb.electric_mw_surplus >= 0) {
      html += `<div class="pwrNote ok">+${pb.electric_mw_surplus.toFixed(1)} MWe surplus</div>`;
    } else {
      html += `<div class="pwrNote crit">⚠ ${pb.electric_mw_surplus.toFixed(1)} MWe — POWER DEFICIT</div>`;
    }
    html += '</div>';

    // Waste heat section
    const whMax = Math.max(pb.waste_heat_mw, pb.heat_rejection_mw, 1);
    html += '<div class="pwrSection">';
    html += '<div class="pwrSectionTitle">🌡 Waste Heat</div>';
    html += bar("Generated", pb.waste_heat_mw, whMax, "MWth", "pwrFillWaste");
    html += bar("Radiator Capacity", pb.heat_rejection_mw, whMax, "MWth", "pwrFillRadiator");
    if (pb.waste_heat_surplus_mw > 0) {
      html += `<div class="pwrNote ok">${pb.waste_heat_surplus_mw.toFixed(1)} MWth excess — absorbed by surface</div>`;
    } else {
      html += `<div class="pwrNote ok">Heat balanced</div>`;
    }
    html += '</div>';

    el.innerHTML = html;
  }

  /* ── Production chain flow ───────────────────────────────── */

  function renderProductionChain() {
    const rawCol = document.getElementById("chainRawMaterials");
    const refinedCol = document.getElementById("chainRefined");
    const constructCol = document.getElementById("chainConstructable");

    const inv = industryData.inventory || {};
    const resources = inv.resources || [];
    const recipes = industryData.available_recipes || [];

    // Categorize resources: raw materials vs refined/finished
    const resourceCatalog = {};
    resources.forEach(r => { resourceCatalog[r.item_id || r.resource_id] = r; });

    // Two-pass classification: first collect all outputs, then classify inputs
    const refinedIds = new Set();
    const constructableIds = new Set();

    // Pass 1: classify all recipe outputs
    recipes.forEach(recipe => {
      const outId = recipe.output_item_id;
      if (recipe.facility_type === "shipyard") {
        constructableIds.add(outId);
      } else {
        refinedIds.add(outId);
      }
    });

    // Pass 2: raw = recipe inputs that are NOT themselves recipe outputs
    const rawInputIds = new Set();
    recipes.forEach(recipe => {
      (recipe.inputs || []).forEach(inp => {
        if (!refinedIds.has(inp.item_id) && !constructableIds.has(inp.item_id)) {
          rawInputIds.add(inp.item_id);
        }
      });
    });

    // Raw materials: inventory items that are raw inputs or not a recipe output
    const rawItems = [];
    const seenRaw = new Set();
    resources.forEach(r => {
      const rid = r.item_id || r.resource_id;
      if (rawInputIds.has(rid) || (!refinedIds.has(rid) && !constructableIds.has(rid))) {
        rawItems.push(r);
        seenRaw.add(rid);
      }
    });
    // Add recipe inputs not in inventory
    rawInputIds.forEach(id => {
      if (!seenRaw.has(id)) {
        rawItems.push({ item_id: id, name: id.replace(/_/g, " "), quantity: 0, mass_kg: 0 });
      }
    });

    // Refined: outputs of refinery recipes
    const refinedItems = [];
    const seenRefined = new Set();
    refinedIds.forEach(id => {
      const inStock = resourceCatalog[id];
      refinedItems.push({
        item_id: id,
        name: inStock ? inStock.name : id.replace(/_/g, " "),
        quantity: inStock ? inStock.quantity : 0,
        mass_kg: inStock ? inStock.mass_kg : 0,
        available: !!inStock,
      });
      seenRefined.add(id);
    });

    // Constructable: outputs of shipyard/constructor recipes
    const constructItems = [];
    constructableIds.forEach(id => {
      const inStock = resourceCatalog[id];
      constructItems.push({
        item_id: id,
        name: inStock ? inStock.name : id.replace(/_/g, " "),
        quantity: inStock ? inStock.quantity : 0,
        available: !!inStock,
      });
    });

    rawCol.innerHTML = rawItems.length
      ? rawItems.map(r => chainItemHtml(r, "raw_material")).join("")
      : '<div class="chainEmpty muted">No raw materials</div>';

    refinedCol.innerHTML = refinedItems.length
      ? refinedItems.map(r => chainItemHtml(r, "finished_material")).join("")
      : '<div class="chainEmpty muted">No refined outputs available</div>';

    constructCol.innerHTML = constructItems.length
      ? constructItems.map(r => chainItemHtml(r, "constructor")).join("")
      : '<div class="chainEmpty muted">No constructable items</div>';
  }

  function chainItemHtml(item, category) {
    const qty = Number(item.quantity || 0);
    const qtyStr = qty > 0 ? fmtKg(qty) : "0";
    const emptyClass = qty <= 0 ? " chainItemEmpty" : "";
    return `<div class="chainItem${emptyClass}">
      <div class="chainItemIcon">${getItemEmoji(category)}</div>
      <div class="chainItemInfo">
        <div class="chainItemName">${esc(item.name)}</div>
        <div class="chainItemQty">${qtyStr}</div>
      </div>
    </div>`;
  }

  function getItemEmoji(cat) {
    switch (cat) {
      case "raw_material": return "◆";
      case "finished_material": return "◇";
      case "resource": return "●";
      case "constructor": return "⚙";
      case "refinery": return "⚗";
      default: return "■";
    }
  }

  /* ── Job history ─────────────────────────────────────────── */

  function renderJobHistory() {
    const list = document.getElementById("industryHistoryList");
    if (!list) return;
    try {
      const history = industryData.job_history || [];

      if (!history.length) {
        list.innerHTML = '<div class="muted" style="padding:12px">No job history</div>';
        return;
      }

      let html = '<table class="historyTable">';
      html += '<thead><tr><th>Type</th><th>Recipe/Resource</th><th>Equipment</th><th>Status</th></tr></thead>';
      html += '<tbody>';
      for (let i = 0; i < history.length; i++) {
        const h = history[i];
        const statusCls = h.status === "completed" ? "badgeOk" : "badgeWarn";
        html += '<tr>';
        html += '<td>' + esc(h.job_type) + '</td>';
        html += '<td>' + esc(h.recipe_name || h.resource_name || "\u2014") + '</td>';
        html += '<td>' + esc(h.equipment_name) + '</td>';
        html += '<td><span class="badge ' + statusCls + '">' + esc(h.status) + '</span></td>';
        html += '</tr>';
      }
      html += '</tbody></table>';
      list.innerHTML = html;
    } catch (err) {
      list.innerHTML = '<div style="padding:12px;color:#ff6b6b">Job history error: ' + String(err.message || err) + '</div>';
      console.error("renderJobHistory error:", err);
    }
  }

  /* ══════════════════════════════════════════════════════════
     ACTIONS
     ══════════════════════════════════════════════════════════ */

  /* Deploy equipment modal */
  function initDeployModal() {
    document.getElementById("btnDeployEquip").addEventListener("click", openDeployModal);
    document.getElementById("deployModalClose").addEventListener("click", () => {
      document.getElementById("deployModal").style.display = "none";
    });
    document.querySelector("#deployModal .modalOverlay").addEventListener("click", () => {
      document.getElementById("deployModal").style.display = "none";
    });
  }

  async function openDeployModal() {
    if (!industryLocationId) return;
    document.getElementById("deployModal").style.display = "";

    const listEl = document.getElementById("deployableItemsList");
    const empty = document.getElementById("deployableEmpty");
    const pwrSummary = document.getElementById("deployPowerSummary");
    listEl.innerHTML = "";
    pwrSummary.innerHTML = "";

    try {
      const normalizeDeployCategory = (raw) => {
        const value = String(raw || "").trim().toLowerCase();
        if (!value) return "";
        // Use Object.create(null) to avoid prototype keys like 'constructor'
        const aliases = Object.create(null);
        Object.assign(aliases, {
          robonauts: "robonaut", robots: "robonaut", robot: "robonaut",
          constructors: "constructor", builders: "constructor", builder: "constructor",
          refineries: "refinery", reactors: "reactor", generators: "generator", radiators: "radiator",
        });
        return aliases[value] || value;
      };
      const deployableCategories = new Set(["refinery", "constructor", "robonaut", "reactor", "generator", "radiator"]);
      const catOrder = ["reactor", "generator", "radiator", "refinery", "constructor", "robonaut"];
      const catLabels = Object.create(null);
      Object.assign(catLabels, { reactor: "Reactors", generator: "Generators", radiator: "Radiators",
        refinery: "Refineries", constructor: "Constructors", robonaut: "Robonauts" });

      // Fetch cargo context and industry data in parallel
      const [context, indData] = await Promise.all([
        fetchJSON(`/api/cargo/context/${encodeURIComponent(industryLocationId)}`),
        fetchJSON(`/api/industry/${encodeURIComponent(industryLocationId)}`).catch(() => null),
      ]);
      const entities = Array.isArray(context.entities) ? context.entities : [];

      // Render power summary from industry data
      const pb = indData && indData.power_balance;
      if (pb) {
        const eSup = Number(pb.electric_mw_supply || 0);
        const eDem = Number(pb.electric_mw_demand || 0);
        const eSur = Number(pb.electric_mw_surplus || 0);
        const tSup = Number(pb.thermal_mw_supply || 0);
        const eSurClass = eSur >= 0 ? "pwrOk" : "pwrBad";

        pwrSummary.innerHTML = `
          <div class="deployPwrStat"><span class="deployPwrLabel">Thermal Supply:</span><span class="deployPwrVal">${tSup.toFixed(1)} MWth</span></div>
          <div class="deployPwrStat"><span class="deployPwrLabel">Electric Supply:</span><span class="deployPwrVal">${eSup.toFixed(1)} MWe</span></div>
          <div class="deployPwrStat"><span class="deployPwrLabel">Demand:</span><span class="deployPwrVal">${eDem.toFixed(1)} MWe</span></div>
          <div class="deployPwrStat"><span class="deployPwrLabel">Surplus:</span><span class="deployPwrVal ${eSurClass}">${eSur >= 0 ? "+" : ""}${eSur.toFixed(1)} MWe</span></div>
        `;
      }

      // Collect deployable items
      const deployableItems = [];
      entities.forEach(entity => {
        const isShip = String(entity.entity_kind || "") === "ship";
        const sourceLabel = isShip
          ? `Ship: ${String(entity.name || entity.id || "Unknown")}`
          : "Site Inventory";

        (entity.stack_items || []).forEach(item => {
          const cat = normalizeDeployCategory(item.category_id || item.category || item.type || "");
          if (!deployableCategories.has(cat)) return;

          const transfer = item.transfer || {};
          let sourceKind = String(transfer.source_kind || "");
          let sourceId = String(transfer.source_id || "");
          const sourceKey = String(transfer.source_key || "");
          if (!sourceKind) sourceKind = isShip ? "ship_part" : "location_part";
          if (!sourceId) sourceId = String(entity.id || "");

          deployableItems.push({
            itemId: String(item.item_id || ""),
            name: String(item.label || item.name || item.item_id || "Part"),
            category: cat,
            quantity: Number(item.quantity || 1),
            massKg: Number(item.mass_kg || 0),
            electricMw: Number(item.electric_mw || 0),
            thermalMw: Number(item.thermal_mw || 0),
            thermalMwInput: Number(item.thermal_mw_input || 0),
            wasteHeatMw: Number(item.waste_heat_mw || 0),
            heatRejectionMw: Number(item.heat_rejection_mw || 0),
            miningRate: Number(item.mining_rate_kg_per_hr || 0),
            constructionRate: Number(item.construction_rate_kg_per_hr || 0),
            conversionEff: Number(item.conversion_efficiency || 0),
            excavationType: String(item.excavation_type || ""),
            specialization: String(item.specialization || ""),
            maxRecipeTier: Number(item.max_recipe_tier || 0),
            throughputMult: Number(item.throughput_mult || 0),
            minGravity: Number(item.min_surface_gravity_ms2 || 0),
            operatingTempK: Number(item.operating_temp_k || 0),
            branch: String(item.branch || ""),
            techLevel: Number(item.tech_level || 0),
            iconSeed: item.icon_seed || item.item_uid || item.item_id,
            sourceKind, sourceId, sourceKey, sourceLabel,
          });
        });
      });

      if (!deployableItems.length) {
        empty.style.display = "";
        return;
      }
      empty.style.display = "none";

      // Group by category (Object.create(null) avoids prototype keys like 'constructor')
      const grouped = Object.create(null);
      deployableItems.forEach(item => {
        if (!grouped[item.category]) grouped[item.category] = [];
        grouped[item.category].push(item);
      });

      // Build detail chips for each item
      function buildDetails(item) {
        const chips = [];
        const cat = item.category;
        if (cat === "reactor") {
          if (item.thermalMw) chips.push(`<b>${item.thermalMw}</b> MWth`);
        } else if (cat === "generator") {
          if (item.thermalMwInput) chips.push(`<b>${item.thermalMwInput}</b> MWth in`);
          if (item.electricMw) chips.push(`<b>${item.electricMw}</b> MWe out`);
          if (item.conversionEff) chips.push(`η ${(item.conversionEff * 100).toFixed(0)}%`);
          if (item.wasteHeatMw) chips.push(`<b>${item.wasteHeatMw}</b> MWth waste`);
        } else if (cat === "radiator") {
          if (item.heatRejectionMw) chips.push(`<b>${item.heatRejectionMw}</b> MWth rejection`);
          if (item.operatingTempK) chips.push(`${item.operatingTempK} K`);
        } else if (cat === "refinery") {
          if (item.specialization) chips.push(item.specialization.replace(/_/g, " "));
          if (item.maxRecipeTier) chips.push(`Tier ${item.maxRecipeTier}`);
          if (item.electricMw) chips.push(`<b>${item.electricMw}</b> MWe`);
        } else if (cat === "constructor") {
          if (item.miningRate) chips.push(`<b>${item.miningRate}</b> kg/hr mine`);
          if (item.constructionRate) chips.push(`<b>${item.constructionRate}</b> kg/hr build`);
          if (item.electricMw) chips.push(`<b>${item.electricMw}</b> MWe`);
          if (item.minGravity) chips.push(`≥${item.minGravity} m/s²`);
        } else if (cat === "robonaut") {
          if (item.miningRate) chips.push(`<b>${item.miningRate}</b> kg/hr ISRU`);
          if (item.electricMw) chips.push(`<b>${item.electricMw}</b> MWe`);
        }
        if (item.massKg > 0) chips.push(`${(item.massKg / 1000).toFixed(1)}t`);
        if (item.branch) chips.push(item.branch.replace(/_/g, " "));
        if (item.techLevel > 0) chips.push(`T${item.techLevel}`);
        return chips.map(c => `<span class="deployItemDetail">${c}</span>`).join("");
      }

      // Render grouped items
      catOrder.forEach(cat => {
        const items = grouped[cat];
        if (!items || !items.length) return;

        // Category header
        const header = document.createElement("div");
        header.className = "deployCatHeader";
        header.textContent = catLabels[cat] || cat;
        listEl.appendChild(header);

        // Item rows
        items.forEach(item => {
          const row = document.createElement("div");
          row.className = "deployItemRow";
          row.innerHTML = `
            <div class="deployItemInfo">
              <div class="deployItemName">${esc(item.name)}</div>
              <div class="deployItemDetails">${buildDetails(item)}</div>
            </div>
            <div class="deployItemMeta">
              <span class="deployItemQty">×${item.quantity}</span>
              <span class="deployItemSource">${esc(item.sourceLabel)}</span>
            </div>
          `;

          row.addEventListener("click", async () => {
            row.style.pointerEvents = "none";
            row.style.opacity = "0.5";
            try {
              if (item.sourceKind === "ship_part") {
                await postJSON("/api/stack/transfer", {
                  source_kind: "ship_part",
                  source_id: item.sourceId,
                  source_key: item.sourceKey,
                  target_kind: "location",
                  target_id: industryLocationId,
                });
              }
              await postJSON("/api/industry/deploy", {
                location_id: industryLocationId,
                item_id: item.itemId,
              });
              document.getElementById("deployModal").style.display = "none";
              loadIndustryContent();
              loadSites();
              if (cargoLocationId === industryLocationId) loadCargoContext();
            } catch (e) {
              row.style.pointerEvents = "";
              row.style.opacity = "";
              alert("Deploy failed: " + e.message);
            }
          });
          listEl.appendChild(row);
        });
      });
    } catch (e) {
      listEl.innerHTML = `<div class="muted" style="padding:12px">Failed to load inventory: ${esc(e.message)}</div>`;
    }
  }

  /* Action helpers */
  async function undeployEquipment(equipId) {
    if (!confirm("Undeploy this equipment?")) return;
    try {
      await postJSON("/api/industry/undeploy", { equipment_id: equipId });
      loadIndustryContent();
      loadSites();
    } catch (e) {
      alert("Undeploy failed: " + e.message);
    }
  }

  async function cancelJob(jobId) {
    if (!confirm("Cancel this job? Consumed resources will be partially refunded.")) return;
    try {
      await postJSON("/api/industry/jobs/cancel", { job_id: jobId });
      loadIndustryContent();
      loadSites();
    } catch (e) {
      alert("Cancel failed: " + e.message);
    }
  }

  /* ══════════════════════════════════════════════════════════
     CARGO TRANSFER TAB
     ══════════════════════════════════════════════════════════ */

  let cargoLocationId = null;
  let cargoContext = null;      // latest /api/cargo/context response
  let cargoSourceKey = null;    // "location:<id>" or "ship:<id>"
  let cargoDestKey = null;
  let cargoStaged = [];         // [{item, sourceKey, amount}]

  function entityKey(kind, id) { return `${kind}:${id}`; }
  function parseEntityKey(k) {
    const i = (k || "").indexOf(":");
    return i < 0 ? { kind: "", id: "" } : { kind: k.slice(0, i), id: k.slice(i + 1) };
  }

  function renderCargoSitesTable() {
    const tbody = document.getElementById("cargoSitesTableBody");
    if (!tbody) return;
    const search = (document.getElementById("cargoSearchInput")?.value || "").toLowerCase();
    const filtered = allSites.filter(s => {
      if (search && !(s.name || "").toLowerCase().includes(search) && !(s.id || "").toLowerCase().includes(search)) return false;
      if (cargoOnlyWithCargo) {
        const invCount = Number(s.inventory?.stack_count || 0) + Number(s.inventory?.resource_count || 0);
        if (invCount <= 0) return false;
      }
      return true;
    });

    function siteRow(s) {
      const shipCount = Number(s.ship_count || s.ships_docked || 0);
      const invCount = Number(s.inventory?.stack_count || 0) + Number(s.inventory?.resource_count || 0);
      const sel = cargoLocationId === s.id ? ' class="selected"' : '';
      return `<tr${sel} data-id="${esc(s.id)}"><td>${esc(s.name)}</td><td>${shipCount}</td><td>${invCount}</td></tr>`;
    }

    if (cargoGroupByBody) {
      const groups = {};
      filtered.forEach(s => {
        const body = s.body_name || "Other";
        if (!groups[body]) groups[body] = [];
        groups[body].push(s);
      });
      const order = Object.keys(groups).sort(bodySort);
      tbody.innerHTML = order.map(body =>
        `<tr class="bodyGroupHeader"><td colspan="3">${esc(body)}</td></tr>` +
        groups[body].map(siteRow).join("")
      ).join("");
    } else {
      tbody.innerHTML = filtered.map(siteRow).join("");
    }

    tbody.querySelectorAll("tr[data-id]").forEach(row => {
      row.style.cursor = "pointer";
      row.addEventListener("click", () => selectCargoLocation(row.dataset.id));
    });
  }

  async function selectCargoLocation(locationId) {
    cargoLocationId = locationId;
    renderCargoSitesTable();
    await loadCargoContext();
  }

  async function loadCargoContext() {
    if (!cargoLocationId) return;
    const placeholder = document.getElementById("cargoPlaceholder");
    const workspace = document.getElementById("cargoWorkspace");
    try {
      cargoContext = await fetchJSON(`/api/cargo/context/${encodeURIComponent(cargoLocationId)}`);
      if (placeholder) placeholder.style.display = "none";
      if (workspace) workspace.style.display = "";
      renderCargoWorkspace();
    } catch (e) {
      console.error("Failed to load cargo context:", e);
      if (placeholder) { placeholder.style.display = ""; placeholder.innerHTML = `<div class="muted">Error loading cargo data</div>`; }
      if (workspace) workspace.style.display = "none";
    }
  }

  function renderCargoWorkspace() {
    if (!cargoContext) return;

    // Update header
    const nameEl = document.getElementById("cargoLocationName");
    if (nameEl) nameEl.textContent = cargoContext.location?.name || cargoLocationId;

    // Build entity options
    const entities = cargoContext.entities || [];
    const sourceSelect = document.getElementById("cargoSourceSelect");
    const destSelect = document.getElementById("cargoDestSelect");

    // Auto-select defaults if nothing selected
    if (!cargoSourceKey || !entities.some(e => entityKey(e.entity_kind, e.id) === cargoSourceKey)) {
      const loc = entities.find(e => e.entity_kind === "location");
      cargoSourceKey = loc ? entityKey(loc.entity_kind, loc.id) : (entities[0] ? entityKey(entities[0].entity_kind, entities[0].id) : null);
    }
    if (!cargoDestKey || !entities.some(e => entityKey(e.entity_kind, e.id) === cargoDestKey)) {
      const firstShip = entities.find(e => e.entity_kind === "ship");
      cargoDestKey = firstShip ? entityKey(firstShip.entity_kind, firstShip.id) : null;
    }

    populateEntitySelect(sourceSelect, entities, cargoSourceKey);
    populateEntitySelect(destSelect, entities, cargoDestKey);

    renderCargoSource();
    renderCargoDest();
    renderStagingList();
  }

  function populateEntitySelect(selectEl, entities, selectedKey) {
    if (!selectEl) return;
    selectEl.innerHTML = "";
    entities.forEach(e => {
      const key = entityKey(e.entity_kind, e.id);
      const opt = document.createElement("option");
      opt.value = key;
      const prefix = e.entity_kind === "ship" ? "🚀 " : "📍 ";
      opt.textContent = prefix + (e.name || e.id);
      if (key === selectedKey) opt.selected = true;
      selectEl.appendChild(opt);
    });
  }

  function findEntity(key) {
    if (!key || !cargoContext) return null;
    const { kind, id } = parseEntityKey(key);
    return (cargoContext.entities || []).find(e => e.entity_kind === kind && e.id === id) || null;
  }

  function renderEntityInventory(containerEl, entity, side) {
    if (!containerEl) return;
    containerEl.innerHTML = "";
    if (!entity) {
      containerEl.innerHTML = '<div class="muted">No entity selected</div>';
      return;
    }

    const isShip = entity.entity_kind === "ship";
    const eKey = entityKey(entity.entity_kind, entity.id);

    // Summary line
    const summary = document.createElement("div");
    summary.className = "cargoEntitySummary";
    if (isShip) {
      const stats = entity.stats || {};
      let text = `MODULES: ${(entity.stack_items || []).length}`;
      if (stats.delta_v_remaining_m_s != null) text += ` · ΔV ${Math.max(0, stats.delta_v_remaining_m_s).toFixed(0)} M/S`;
      summary.textContent = text;
    } else {
      const rCount = (entity.inventory_items || []).length;
      const pCount = (entity.stack_items || []).length;
      summary.textContent = `Resources: ${rCount} · Parts: ${pCount}`;
    }
    containerEl.appendChild(summary);

    // Capacity summary bar (ships)
    if (isShip && entity.capacity_summary) {
      const capEl = renderCargoCapacitySummary(entity.capacity_summary);
      if (capEl) containerEl.appendChild(capEl);
    }

    // Container groups (ships) — hangar style
    const groups = entity.container_groups || [];
    if (groups.length) {
      const groupList = document.createElement("div");
      groupList.className = "inventoryContainerGroupList";

      groups.forEach((cg) => {
        const phase = String(cg.phase || "solid").toLowerCase();
        const section = document.createElement("section");
        section.className = `inventoryContainerGroup containerPhase-${phase}`;

        // Head row
        const head = document.createElement("div");
        head.className = "inventoryContainerGroupHead";

        const phaseBadge = document.createElement("span");
        phaseBadge.className = `containerPhaseBadge containerPhaseBadge-${phase}`;
        const phaseIcons = { solid: "◆", liquid: "💧", gas: "☁" };
        phaseBadge.textContent = phaseIcons[phase] || "◆";

        const title = document.createElement("div");
        title.className = "inventoryContainerGroupTitle";
        title.textContent = String(cg.name || "Container");

        const used = Math.max(0, Number(cg.used_m3) || 0);
        const cap = Math.max(0, Number(cg.capacity_m3) || 0);
        const sub = document.createElement("div");
        sub.className = "inventoryContainerGroupSub";
        sub.textContent = `${phase[0].toUpperCase()}${phase.slice(1)} · ${fmtM3(used)} / ${fmtM3(cap)}`;

        const headLeft = document.createElement("div");
        headLeft.className = "containerGroupHeadLeft";
        headLeft.append(phaseBadge, title);

        head.append(headLeft, sub);

        // Items grid
        const itemsWrap = document.createElement("div");
        itemsWrap.className = "inventoryContainerItems";
        const items = Array.isArray(cg.items) ? cg.items : [];
        if (!items.length) {
          const emptySlot = document.createElement("div");
          emptySlot.className = `containerEmptySlot containerEmptySlot-${phase}`;
          emptySlot.textContent = `Empty ${phase} container`;
          itemsWrap.appendChild(emptySlot);
        } else {
          items.forEach(item => {
            const cell = buildCargoCell(item, eKey, side);
            itemsWrap.appendChild(cell);
          });
        }

        section.append(head, itemsWrap);
        groupList.appendChild(section);
      });

      containerEl.appendChild(groupList);
    }

    // Loose inventory items (resources not in containers)
    const looseItems = (entity.inventory_items || []).filter(item => {
      return !item.container_index && item.container_index !== 0;
    });
    if (looseItems.length) {
      const looseHead = document.createElement("div");
      looseHead.className = "cargoGroupLabel";
      looseHead.textContent = isShip ? "LOOSE RESOURCES" : "RESOURCES";
      containerEl.appendChild(looseHead);

      const looseGrid = document.createElement("div");
      looseGrid.className = "invGrid cargoItemGrid";
      looseItems.forEach(item => {
        const cell = buildCargoCell(item, eKey, side);
        looseGrid.appendChild(cell);
      });
      containerEl.appendChild(looseGrid);
    }

    // Stack items (parts/modules)
    const stackItems = entity.stack_items || [];
    if (stackItems.length) {
      const stackHead = document.createElement("div");
      stackHead.className = "cargoGroupLabel";
      stackHead.textContent = isShip ? "INSTALLED MODULES" : "PARTS";
      containerEl.appendChild(stackHead);

      const stackGrid = document.createElement("div");
      stackGrid.className = "invGrid cargoItemGrid";
      stackItems.forEach(item => {
        const cell = buildCargoCell(item, eKey, side);
        stackGrid.appendChild(cell);
      });
      containerEl.appendChild(stackGrid);
    }

    if (!groups.length && !looseItems.length && !stackItems.length) {
      containerEl.innerHTML = '<div class="muted">No cargo or parts</div>';
    }
  }

  /** Capacity summary bar — matches hangar style */
  function renderCargoCapacitySummary(capSummary) {
    if (!capSummary) return null;
    const used = Math.max(0, Number(capSummary.used_m3) || 0);
    const cap = Math.max(0, Number(capSummary.capacity_m3) || 0);
    const pct = cap > 0 ? Math.max(0, Math.min(100, (used / cap) * 100)) : 0;
    const byPhase = capSummary.by_phase && typeof capSummary.by_phase === "object" ? capSummary.by_phase : {};

    const wrap = document.createElement("div");
    wrap.className = "inventoryCapSummary";

    const line = document.createElement("div");
    line.className = "inventoryCapSummaryLine";
    line.textContent = `${fmtM3(used)} / ${fmtM3(cap)} used`;

    const bar = document.createElement("div");
    bar.className = "inventoryCapBar";
    const fill = document.createElement("div");
    fill.className = "inventoryCapBarFill";
    fill.style.width = `${pct.toFixed(2)}%`;
    bar.appendChild(fill);

    const phases = document.createElement("div");
    phases.className = "inventoryCapSummaryPhases";
    phases.textContent = ["solid", "liquid", "gas"].map(ph => {
      const row = byPhase[ph] || {};
      const pUsed = Math.max(0, Number(row.used_m3) || 0);
      const pCap = Math.max(0, Number(row.capacity_m3) || 0);
      return `${ph[0].toUpperCase()}${ph.slice(1)} ${fmtM3(pUsed)} / ${fmtM3(pCap)}`;
    }).join(" · ");

    wrap.append(line, bar, phases);
    return wrap;
  }

  function buildCargoCell(item, entityKey, side) {
    const isStackItem = !!(item.item_kind === "ship_part" || item.item_kind === "location_part" || item.source_kind === "ship_part" || item.source_kind === "location_part");
    const transfer = item.transfer || {};
    const sourceKind = String(transfer.source_kind || item.source_kind || "");
    const sourceId = String(transfer.source_id || item.source_id || "");
    const sourceKeyStr = String(transfer.source_key || item.source_key || "");

    const cell = itemDisplay.createGridCell({
      label: item.label || item.name || "Item",
      iconSeed: item.icon_seed || item.item_uid || item.item_id,
      itemId: item.item_id || "",
      category: item.category_id || item.category || item.type,
      quantity: item.quantity || item.amount,
      mass_kg: item.mass_kg,
      volume_m3: item.volume_m3,
      subtitle: item.subtitle || item.category_id,
      tooltipLines: item.tooltip_lines,
    });

    // Make clickable to stage
    if (side === "source") {
      cell.style.cursor = "pointer";
      cell.addEventListener("click", () => {
        stageItem(item, entityKey);
      });
    }

    return cell;
  }

  function stageItem(item, sourceEntityKey) {
    const transfer = item.transfer || {};
    const sourceKind = String(transfer.source_kind || item.source_kind || "");
    const sourceId = String(transfer.source_id || item.source_id || "");
    const sourceKeyStr = String(transfer.source_key || item.source_key || "");
    const maxAmount = Number(transfer.amount || item.amount || item.quantity || 1);
    const isStack = sourceKind === "ship_part" || sourceKind === "location_part";

    const resourceId = String(transfer.resource_id || item.resource_id || "");

    // Check if already staged
    const existingIdx = cargoStaged.findIndex(s =>
      s.sourceKind === sourceKind && s.sourceId === sourceId && s.sourceKeyStr === sourceKeyStr && s.resourceId === resourceId
    );
    if (existingIdx >= 0) return; // already staged

    let amount = maxAmount;
    if (!isStack && maxAmount > 1) {
      const input = window.prompt(`Transfer how much? (max ${maxAmount})`, String(maxAmount));
      if (input == null) return;
      amount = Math.max(1, Math.min(maxAmount, Math.round(Number(input) || 0)));
      if (amount <= 0) return;
    }

    cargoStaged.push({
      item,
      sourceEntityKey,
      sourceKind,
      sourceId,
      sourceKeyStr,
      resourceId: String(transfer.resource_id || item.resource_id || ""),
      amount,
      maxAmount,
      isStack,
      label: item.label || item.name || "Item",
    });

    renderStagingList();
  }

  function removeStagedItem(idx) {
    cargoStaged.splice(idx, 1);
    renderStagingList();
  }

  function renderStagingList() {
    const list = document.getElementById("cargoStagingList");
    const empty = document.getElementById("cargoStagingEmpty");
    const count = document.getElementById("cargoStagingCount");
    const transferBtn = document.getElementById("cargoTransferBtn");

    if (count) count.textContent = `${cargoStaged.length} item${cargoStaged.length !== 1 ? "s" : ""}`;
    if (empty) empty.style.display = cargoStaged.length ? "none" : "";
    if (transferBtn) transferBtn.disabled = !cargoStaged.length || !cargoDestKey;

    if (!list) return;
    list.innerHTML = "";

    cargoStaged.forEach((staged, idx) => {
      const row = document.createElement("div");
      row.className = "cargoStagedRow";

      const info = document.createElement("div");
      info.className = "cargoStagedInfo";

      const name = document.createElement("span");
      name.className = "cargoStagedName";
      name.textContent = staged.label;
      info.appendChild(name);

      if (!staged.isStack && staged.amount > 0) {
        const qty = document.createElement("span");
        qty.className = "cargoStagedQty muted";
        qty.textContent = ` ×${staged.amount}`;
        info.appendChild(qty);
      }

      const removeBtn = document.createElement("button");
      removeBtn.className = "btnSmall cargoStagedRemove";
      removeBtn.textContent = "✕";
      removeBtn.addEventListener("click", () => removeStagedItem(idx));

      row.append(info, removeBtn);
      list.appendChild(row);
    });
  }

  function renderCargoSource() {
    const body = document.getElementById("cargoSourceBody");
    const entity = findEntity(cargoSourceKey);
    renderEntityInventory(body, entity, "source");
  }

  function renderCargoDest() {
    const body = document.getElementById("cargoDestBody");
    const entity = findEntity(cargoDestKey);
    renderEntityInventory(body, entity, "dest");
  }

  async function executeCargoTransfers() {
    if (!cargoStaged.length || !cargoDestKey) return;

    const { kind: destKind, id: destId } = parseEntityKey(cargoDestKey);
    const transferBtn = document.getElementById("cargoTransferBtn");
    if (transferBtn) transferBtn.disabled = true;

    let errors = [];
    for (const staged of cargoStaged) {
      try {
        if (staged.isStack) {
          await postJSON("/api/stack/transfer", {
            source_kind: staged.sourceKind,
            source_id: staged.sourceId,
            source_key: staged.sourceKeyStr,
            target_kind: destKind,
            target_id: destId,
          });
        } else {
          const transferBody = {
            source_kind: staged.sourceKind,
            source_id: staged.sourceId,
            source_key: staged.sourceKeyStr,
            target_kind: destKind,
            target_id: destId,
            amount: staged.amount,
          };
          if (staged.resourceId) transferBody.resource_id = staged.resourceId;
          await postJSON("/api/inventory/transfer", transferBody);
        }
      } catch (e) {
        errors.push(`${staged.label}: ${e.message}`);
      }
    }

    cargoStaged = [];
    renderStagingList();
    await loadCargoContext();
    await loadSites();

    if (errors.length) {
      alert("Some transfers failed:\n" + errors.join("\n"));
    }
  }

  function initCargoTab() {
    // Search filter
    const searchInput = document.getElementById("cargoSearchInput");
    if (searchInput) searchInput.addEventListener("input", () => renderCargoSitesTable());

    // Source/dest selectors
    const sourceSelect = document.getElementById("cargoSourceSelect");
    if (sourceSelect) sourceSelect.addEventListener("change", () => {
      cargoSourceKey = sourceSelect.value || null;
      renderCargoSource();
    });
    const destSelect = document.getElementById("cargoDestSelect");
    if (destSelect) destSelect.addEventListener("change", () => {
      cargoDestKey = destSelect.value || null;
      renderCargoDest();
      renderStagingList(); // update transfer button state
    });

    // Transfer button
    const transferBtn = document.getElementById("cargoTransferBtn");
    if (transferBtn) transferBtn.addEventListener("click", () => executeCargoTransfers());

    // Clear button
    const clearBtn = document.getElementById("cargoClearBtn");
    if (clearBtn) clearBtn.addEventListener("click", () => {
      cargoStaged = [];
      renderStagingList();
    });

    // Refresh button
    const refreshBtn = document.getElementById("cargoRefreshBtn");
    if (refreshBtn) refreshBtn.addEventListener("click", () => loadCargoContext());

    // Group by body toggle (Cargo)
    const cargoGroupBtn = document.getElementById("cargoGroupByBody");
    if (cargoGroupBtn) {
      cargoGroupBtn.addEventListener("click", () => {
        cargoGroupByBody = !cargoGroupByBody;
        cargoGroupBtn.classList.toggle("active", cargoGroupByBody);
        renderCargoSitesTable();
      });
    }

    // Filter to only sites with cargo
    const cargoOnlyBtn = document.getElementById("cargoOnlyWithCargo");
    if (cargoOnlyBtn) {
      cargoOnlyBtn.addEventListener("click", () => {
        cargoOnlyWithCargo = !cargoOnlyWithCargo;
        cargoOnlyBtn.classList.toggle("active", cargoOnlyWithCargo);
        renderCargoSitesTable();
      });
    }
  }

  /* ══════════════════════════════════════════════════════════
     FILTER & SEARCH
     ══════════════════════════════════════════════════════════ */

  function initFilters() {
    document.querySelectorAll(".siteFilterBtn").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".siteFilterBtn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        currentFilter = btn.dataset.filter;
        renderSitesTable();
      });
    });
    document.getElementById("sitesSearchInput").addEventListener("input", () => renderSitesTable());

    // Group by body toggle (Overview)
    const overviewBtn = document.getElementById("overviewGroupByBody");
    if (overviewBtn) {
      overviewBtn.addEventListener("click", () => {
        overviewGroupByBody = !overviewGroupByBody;
        overviewBtn.classList.toggle("active", overviewGroupByBody);
        renderSitesTable();
      });
    }
  }

  /* ══════════════════════════════════════════════════════════
     POLLING & INIT
     ══════════════════════════════════════════════════════════ */

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => {
      loadSites();
      if (currentTab === "industrial" && industryLocationId) loadIndustryContent();
      if (currentTab === "cargo" && cargoLocationId) loadCargoContext();
      if (selectedSiteId) selectSite(selectedSiteId);
    }, 10000);

    // Also refresh job progress bars every second
    setInterval(() => {
      if (currentTab === "industrial" && industryData) {
        renderRefinerySlots();
        renderConstructionQueue();
      }
    }, 1000);
  }

  async function init() {
    await syncClock();
    initTabSwitching();
    initFilters();
    initIndustryLocationSelect();
    initDeployModal();
    initRecipeSelectModal();
    initCargoTab();
    await loadSites();
    startPolling();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
