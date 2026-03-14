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
  let industryFacilityId = null;
  let industryFacilityName = "";
  let industryFacilities = [];       // facilities at current location
  let industryData = null;
  let currentIndustrySubtab = "overview"; // "overview" | "deployments" | "mining" | "printing"
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
  function formatPrinterTypeLabel(printerType, withSuffix) {
    const key = String(printerType || "").trim().toLowerCase();
    if (key === "industrial") return withSuffix ? "Industrial Printer" : "Industrial";
    if (key === "ship" || key === "aerospace") return withSuffix ? "Aerospace Printer" : "Aerospace";
    return withSuffix ? "Printer" : "";
  }
  function fmtDuration(s) {
    s = Math.max(0, Math.round(Number(s) || 0));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s/60)}m ${s%60}s`;
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}h ${m}m`;
  }

  function mergeTooltipLines(baseLines, generatedLines) {
    const merged = [];
    const labels = new Set();

    function pushLines(lines) {
      if (!Array.isArray(lines)) return;
      lines.forEach((line) => {
        if (Array.isArray(line) && line.length >= 2) {
          const label = String(line[0] || "").trim();
          const value = String(line[1] || "");
          const key = label.toLowerCase();
          if (label && labels.has(key)) return;
          if (label) labels.add(key);
          merged.push([label, value]);
        }
      });
    }

    pushLines(baseLines);
    pushLines(generatedLines);
    return merged;
  }

  function buildModuleTooltipLines(item) {
    const p = item && typeof item === "object" ? item : {};
    const lines = [];
    if (Number(p.thrust_kn) > 0) lines.push(["Thrust", `${Number(p.thrust_kn).toFixed(0)} kN`]);
    if (Number(p.isp_s) > 0) lines.push(["ISP", `${Number(p.isp_s).toFixed(0)} s`]);
    if (Number(p.thermal_mw) > 0) lines.push(["Power", `${Number(p.thermal_mw).toFixed(1)} MWth`]);
    if (Number(p.core_temp_k) > 0) lines.push(["Core Temp", `${Number(p.core_temp_k).toFixed(0)} K`]);
    if (Number(p.rated_temp_k) > 0) lines.push(["Core Temp Req", `${Number(p.rated_temp_k).toFixed(0)} K`]);
    if (Number(p.electric_mw) > 0) lines.push(["Electric", `${Number(p.electric_mw).toFixed(1)} MWe`]);
    if (Number(p.heat_rejection_mw) > 0) lines.push(["Rejection", `${Number(p.heat_rejection_mw).toFixed(1)} MWth`]);
    if (Number(p.capacity_m3) > 0) lines.push(["Capacity", `${Number(p.capacity_m3).toFixed(0)} m³`]);
    if (Number(p.fuel_capacity_kg) > 0) lines.push(["Fuel Cap", fmtKg(Number(p.fuel_capacity_kg))]);
    if (Number(p.scan_rate_km2_per_hr) > 0) lines.push(["Scan Rate", `${Number(p.scan_rate_km2_per_hr).toFixed(0)} km²/hr`]);
    if (Number(p.mining_rate_kg_per_hr) > 0) lines.push(["Mining Rate", `${Number(p.mining_rate_kg_per_hr).toFixed(0)} kg/hr`]);
    if (Number(p.construction_rate_kg_per_hr) > 0) lines.push(["Build Rate", `${Number(p.construction_rate_kg_per_hr).toFixed(0)} kg/hr`]);
    return lines;
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
        if (currentTab === "industrial") {
          if (industryLocationId && industryFacilityId) loadIndustryContent();
          else if (industryLocationId) loadFacilityGrid();
        }
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
        return s.facility_count > 0 || (eq.refinery || {}).total > 0 || (eq.constructor || {}).total > 0 || (eq.miner || {}).total > 0 || (eq.printer || {}).total > 0 || (eq.prospector || {}).total > 0 || (eq.robonaut || {}).total > 0 || s.active_jobs > 0;
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
      const conCount = (eq.constructor || {}).total || 0 || (eq.miner || {}).total || 0 || (eq.printer || {}).total || 0;
      const prospectorCount = ((eq.prospector || {}).total || 0) + ((eq.robonaut || {}).total || 0);
      const equipStr = (refCount || conCount || prospectorCount)
        ? `<span class="eqBadge ref">${refCount}R</span><span class="eqBadge con">${conCount}C</span><span class="eqBadge con">${prospectorCount}P</span>`
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

    // Eligible Equipment (shown only for prospected surface sites)
    const eligSection = document.getElementById("siteEligibleSection");
    const eligGrid = document.getElementById("siteEligibleGrid");
    const eligible = site.eligible_equipment;
    if (eligible && (eligible.eligible_miners?.length || eligible.eligible_isru?.length)) {
      eligSection.style.display = "";
      let eligHtml = "";
      if (eligible.eligible_miners && eligible.eligible_miners.length) {
        eligHtml += `<div class="eligibleGroupLabel">Miners</div>`;
        eligible.eligible_miners.forEach(m => {
          eligHtml += `<div class="eligibleRow"><span class="eligibleIcon">⛏</span><span class="eligibleLabel">${esc(m.label)}</span><span class="eligibleReason muted">${esc(m.reason)}</span></div>`;
        });
      }
      if (eligible.eligible_isru && eligible.eligible_isru.length) {
        eligHtml += `<div class="eligibleGroupLabel">ISRU Modules</div>`;
        eligible.eligible_isru.forEach(m => {
          eligHtml += `<div class="eligibleRow"><span class="eligibleIcon">🏭</span><span class="eligibleLabel">${esc(m.label)}</span><span class="eligibleReason muted">${esc(m.reason)}</span></div>`;
        });
      }
      eligGrid.innerHTML = eligHtml;
    } else {
      eligSection.style.display = "none";
      eligGrid.innerHTML = "";
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
        const tooltipLines = mergeTooltipLines([], buildModuleTooltipLines(part));
        const cell = itemDisplay.createGridCell({
          label: p.name, iconSeed: p.item_id, itemId: p.item_id,
          category: cat,
          mass_kg: p.mass_kg, quantity: p.quantity,
          branch: part.branch || "",
          family: part.family || part.thruster_family || "",
          techLevel: part.tech_level || "",
          core_temp_k: part.core_temp_k,
          rated_temp_k: part.rated_temp_k,
          thermal_mw_input: part.thermal_mw_input,
          electric_mw: part.electric_mw,
          conversion_efficiency: part.conversion_efficiency,
          max_concurrent_recipes: part.max_concurrent_recipes,
          recipe_slots: part.recipe_slots,
          supported_recipe_names: part.supported_recipe_names,
          water_extraction_kg_per_hr: part.water_extraction_kg_per_hr,
          min_water_ice_fraction: part.min_water_ice_fraction,
          max_water_ice_fraction: part.max_water_ice_fraction,
          tooltipLines: tooltipLines.length ? tooltipLines : undefined,
        });
        invGrid.appendChild(cell);
      });
    } else {
      invEmpty.style.display = "";
      invGrid.innerHTML = "";
    }

    // Facilities
    const facilitiesList = document.getElementById("siteFacilitiesList");
    const facilitiesEmpty = document.getElementById("siteFacilitiesEmpty");
    const facilities = site.facilities || [];
    if (facilities.length) {
      facilitiesEmpty.style.display = "none";
      facilitiesList.innerHTML = facilities.map(f => {
        const st = f.stats || {};
        const owner = f.is_mine ? "Mine" : esc(f.corp_name || f.corp_id || "Unknown Corp");
        return `<div class="siteShipRow">
          <span>${esc(f.name)}</span>
          <span class="muted">${owner} · ⚡ ${Number(st.power_mwe || 0).toFixed(1)} MWe · 🏭 ${st.equipment_count || 0} · ⚙ ${st.active_jobs || 0}</span>
        </div>`;
      }).join("");
    } else {
      facilitiesEmpty.style.display = "";
      facilitiesList.innerHTML = "";
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

  function populateIndustryLocationSelect() {
    const sel = document.getElementById("industryLocationSelect");
    const current = sel.value;

    const industrial = [];      // deployed constructors or refineries
    const otherPresence = [];   // ships docked / prospectors / active jobs but no constructor/refinery
    const prospectedOnly = [];  // prospected but no presence
    allSites.forEach(s => {
      const eq = s.equipment || {};
      const hasConstructorOrRefinery = ((eq.refinery || {}).total || 0) + ((eq.constructor || {}).total || 0) + ((eq.miner || {}).total || 0) + ((eq.printer || {}).total || 0) > 0;
      const hasProspector = (((eq.prospector || {}).total || 0) + ((eq.robonaut || {}).total || 0)) > 0;
      const hasShipLanded = Number(s.ships_docked || 0) > 0;
      const hasActivity = Number(s.active_jobs || 0) > 0;
      const hasPresence = hasConstructorOrRefinery || hasProspector || hasShipLanded || hasActivity;
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
      industryFacilityId = null;
      industryFacilityName = "";
      if (industryLocationId) {
        loadFacilityGrid();
      } else {
        document.getElementById("industryContent").style.display = "none";
      }
    });

    // Back button from facility industry view → facility grid
    document.getElementById("btnBackToFacilities").addEventListener("click", () => {
      industryFacilityId = null;
      industryFacilityName = "";
      showFacilityGrid();
    });

    // Facility creation modal
    document.getElementById("facilityCreateModalClose").addEventListener("click", closeFacilityCreateModal);
    document.getElementById("facilityCreateCancel").addEventListener("click", closeFacilityCreateModal);
    document.getElementById("facilityCreateConfirm").addEventListener("click", submitFacilityCreate);
    document.getElementById("facilityCreateNameInput").addEventListener("keydown", e => {
      if (e.key === "Enter") submitFacilityCreate();
    });
  }

  /* ── Facility Grid ─────────────────────────────────────── */

  async function loadFacilityGrid() {
    if (!industryLocationId) return;
    document.getElementById("industryContent").style.display = "";
    try {
      const data = await fetchJSON(`/api/facilities/${encodeURIComponent(industryLocationId)}`);
      industryFacilities = data.facilities || [];
      showFacilityGrid();
    } catch (e) {
      console.error("Failed to load facilities:", e);
      // Fallback: show empty grid
      industryFacilities = [];
      showFacilityGrid();
    }
  }

  function showFacilityGrid() {
    document.getElementById("facilityGridView").style.display = "";
    document.getElementById("facilityIndustryView").style.display = "none";
    document.getElementById("facilityBreadcrumb").style.display = "none";
    renderFacilityGrid();
  }

  function renderFacilityGrid() {
    const container = document.getElementById("facilityGridCards");
    let html = "";

    // Own facilities (clickable)
    const own = industryFacilities.filter(f => f.is_mine);
    const others = industryFacilities.filter(f => !f.is_mine);

    for (const f of own) {
      const stats = f.stats || {};
      const pwrMwe = Number(stats.power_mwe || 0);
      const pwrUsed = Number(stats.power_used_mwe || 0);
      const pwrOk = pwrUsed <= 0 || pwrMwe >= pwrUsed;
      const pwrClass = pwrOk ? "" : " pwrBadBadge";
      const pwrLabel = pwrOk ? `⚡ ${pwrMwe.toFixed(1)} MWe` : `⚡ ${pwrMwe.toFixed(1)}/${pwrUsed.toFixed(1)} MWe ⚠`;
      html += `<div class="facilityCard facilityOwn" data-facility-id="${esc(f.id)}" data-facility-name="${esc(f.name)}">
        <div class="facilityCardName">${esc(f.name)}</div>
        <div class="facilityCardStats">
          <span class="${pwrClass}">${pwrLabel}</span>
          <span>🏭 ${stats.equipment_count || 0} equipment</span>
          <span>⚙ ${stats.active_jobs || 0} active</span>
        </div>
        <div class="facilityCardEnter">ENTER ▸</div>
      </div>`;
    }

    // Create card
    html += `<div class="facilityCard facilityCreate" id="facilityCreateCard">
      <div class="facilityCreatePlus">+</div>
      <div class="facilityCreateLabel">Create New Facility</div>
    </div>`;

    // Other corps' facilities (not clickable)
    for (const f of others) {
      const stats = f.stats || {};
      html += `<div class="facilityCard facilityOther">
        <div class="facilityCardName">${esc(f.name)}</div>
        <div class="facilityCardCorp">${esc(f.corp_name || "Unknown")}</div>
        <div class="facilityCardStats">
          <span>🏭 ${stats.equipment_count || 0} equipment</span>
          <span>⚡ ${Number(stats.power_mwe || 0).toFixed(1)} MWe</span>
        </div>
      </div>`;
    }

    container.innerHTML = html;

    // Click handlers
    container.querySelectorAll(".facilityOwn").forEach(card => {
      card.addEventListener("click", () => {
        enterFacility(card.dataset.facilityId, card.dataset.facilityName);
      });
    });

    document.getElementById("facilityCreateCard").addEventListener("click", openFacilityCreateModal);
  }

  function enterFacility(facilityId, facilityName) {
    try {
      industryFacilityId = facilityId;
      industryFacilityName = facilityName || "Facility";
      industryData = null; // clear stale data from previous facility
      document.getElementById("facilityGridView").style.display = "none";
      document.getElementById("facilityIndustryView").style.display = "";

      // Show breadcrumb
      const bc = document.getElementById("facilityBreadcrumb");
      bc.style.display = "";
      const siteName = document.getElementById("industryLocationSelect").selectedOptions[0]?.text || industryLocationId;
      document.getElementById("facilityBreadcrumbText").textContent =
        `${siteName}  ▸  ${industryFacilityName}`;

      switchIndustrySubtab(currentIndustrySubtab);

      loadIndustryContent();
    } catch (e) {
      console.error("enterFacility failed:", e);
      industryFacilityId = null;
      industryFacilityName = "";
      showFacilityGrid();
    }
  }

  async function loadIndustryContent() {
    if (!industryFacilityId) return;
    document.getElementById("facilityIndustryView").style.display = "";

    try {
      industryData = await fetchJSON(`/api/industry/facility/${encodeURIComponent(industryFacilityId)}`);
      renderIndustry();
    } catch (e) {
      console.error("Failed to load industry data:", e);
      industryFacilityId = null;
      industryFacilityName = "";
      showFacilityGrid();
    }
  }

  /* ── Facility Creation Modal ─────────────────────────── */

  function openFacilityCreateModal() {
    const siteName = document.getElementById("industryLocationSelect").selectedOptions[0]?.text || industryLocationId;
    document.getElementById("facilityCreateLocationLabel").textContent = `Location: ${siteName}`;
    document.getElementById("facilityCreateNameInput").value = "";
    document.getElementById("facilityCreateError").style.display = "none";
    document.getElementById("facilityCreateModal").style.display = "";
    document.getElementById("facilityCreateNameInput").focus();
  }

  function closeFacilityCreateModal() {
    document.getElementById("facilityCreateModal").style.display = "none";
  }

  async function submitFacilityCreate() {
    const name = (document.getElementById("facilityCreateNameInput").value || "").trim();
    if (!name) {
      const errEl = document.getElementById("facilityCreateError");
      errEl.textContent = "Name is required";
      errEl.style.display = "";
      return;
    }
    try {
      const result = await postJSON("/api/facilities/create", {
        location_id: industryLocationId,
        name: name,
      });
      closeFacilityCreateModal();
      // Enter the newly created facility
      if (result.facility_id) {
        enterFacility(result.facility_id, name);
      } else {
        loadFacilityGrid();
      }
    } catch (e) {
      const errEl = document.getElementById("facilityCreateError");
      errEl.textContent = e.message || "Failed to create facility";
      errEl.style.display = "";
    }
  }

  /* ── Industry Subtab Switching ────────────────────────────── */

  function switchIndustrySubtab(tab) {
    currentIndustrySubtab = tab;
    // Update tab bar buttons
    document.querySelectorAll("#industrySubTabs .indSubTab").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.indtab === tab);
    });
    // Show/hide panels
    const panels = {
      overview: "indTabOverview",
      deployments: "indTabDeployments",
      mining: "indTabMining",
      printing: "indTabPrinting",
    };
    for (const [key, id] of Object.entries(panels)) {
      const el = document.getElementById(id);
      if (el) el.style.display = key === tab ? "" : "none";
    }
    // Re-render active subtab if data is loaded
    if (industryData) renderIndustry();
  }

  function initIndustrySubtabs() {
    document.querySelectorAll("#industrySubTabs .indSubTab").forEach(btn => {
      btn.addEventListener("click", () => switchIndustrySubtab(btn.dataset.indtab));
    });
  }

  function updateSubtabBadges() {
    if (!industryData) return;
    const equipment = industryData.equipment || [];
    const queue = industryData.construction_queue || [];

    // Mining badge: count of active miners/ISRU
    const minerCats = new Set(["miner", "isru", "constructor"]);
    const activeMiners = equipment.filter(e => minerCats.has(e.category) && e.mode === "mine").length;
    const refSlots = (industryData.refinery_slots || []).filter(s => s.status === "active").length;

    // Printing badge: count waiting_materials
    const waitingCount = queue.filter(q => q.status === "waiting_materials").length;
    const printingBadge = document.querySelector('#industrySubTabs .indSubTab[data-indtab="printing"]');
    if (printingBadge) {
      printingBadge.textContent = waitingCount > 0 ? `Printing (${waitingCount} waiting)` : "Printing";
    }
  }

  function renderIndustry() {
    if (!industryData) return;
    const tab = currentIndustrySubtab;
    try {
      updateSubtabBadges();
      if (tab === "overview") {
        renderPowerBalance();
        renderAggregateRates();
        renderMiningSummary();
        renderJobHistory();
      } else if (tab === "deployments") {
        renderDeployments();
        renderPowerBreakdown();
      } else if (tab === "mining") {
        renderMinerEquipment();
        renderRefineryEquipment();
        renderRefinerySlots();
      } else if (tab === "printing") {
        renderPrinterEquipment();
        renderConstructionQueue();
      }
    } catch (e) {
      console.error(`renderIndustry: subtab ${tab} failed:`, e);
    }
  }

  /* ── Aggregate Rates (Overview) ──────────────────────────── */

  function renderAggregateRates() {
    const el = document.getElementById("industryAggContent");
    if (!el) return;
    const equipment = industryData.equipment || [];

    const minerCats = new Set(["miner", "isru", "constructor"]);
    const activeMiners = equipment.filter(e => minerCats.has(e.category) && e.mode === "mine");
    const activePrinters = equipment.filter(e => (e.category === "printer" || e.category === "constructor") && e.mode === "construct");
    const activeRefineries = (industryData.refinery_slots || []).filter(s => s.status === "active");

    const totalMiningRate = activeMiners.reduce((s, m) => s + (m.config?.mining_rate_kg_per_hr || m.config?.water_extraction_kg_per_hr || 0), 0);
    const totalBuildRate = activePrinters.reduce((s, p) => s + (p.config?.construction_rate_kg_per_hr || 0), 0);

    el.innerHTML = `<div class="aggRatesGrid">
      <div class="aggRateItem"><span class="aggRateVal">${activeMiners.length}</span><span class="aggRateLabel">Miners Active</span></div>
      <div class="aggRateItem"><span class="aggRateVal">${totalMiningRate.toFixed(0)}</span><span class="aggRateLabel">kg/hr mining</span></div>
      <div class="aggRateItem"><span class="aggRateVal">${activeRefineries.length}</span><span class="aggRateLabel">Refineries Active</span></div>
      <div class="aggRateItem"><span class="aggRateVal">${activePrinters.length}</span><span class="aggRateLabel">Printers Active</span></div>
      <div class="aggRateItem"><span class="aggRateVal">${totalBuildRate.toFixed(0)}</span><span class="aggRateLabel">kg/hr printing</span></div>
    </div>`;
  }

  /* ── Deployments Subtab ──────────────────────────────────── */

  function renderDeployments() {
    const container = document.getElementById("deploymentsEquipList");
    if (!container) return;
    const equipment = industryData.equipment || [];

    if (!equipment.length) {
      container.innerHTML = '<div class="muted" style="padding:12px">No equipment deployed. Use "Deploy Equipment" to add modules.</div>';
      return;
    }

    // Group into categories
    const groups = Object.create(null);
    const groupOrder = [
      { key: "power", label: "Power", cats: new Set(["reactor", "generator", "radiator"]) },
      { key: "production", label: "Production", cats: new Set(["refinery", "printer"]) },
      { key: "extraction", label: "Extraction", cats: new Set(["miner", "isru", "constructor", "prospector", "robonaut"]) },
    ];

    for (const g of groupOrder) {
      groups[g.key] = equipment.filter(e => g.cats.has(e.category));
    }

    let html = "";
    for (const g of groupOrder) {
      const items = groups[g.key];
      if (!items.length) continue;

      html += `<div class="panel deployGroupPanel">`;
      html += `<div class="panelHeader"><span class="panelTitle">${g.label} (${items.length})</span></div>`;
      html += `<div class="deployGroupBody">`;

      for (const eq of items) {
        const cfg = eq.config || {};
        const mode = eq.mode || "idle";

        let statusBadge;
        if (mode === "mine") statusBadge = '<span class="badge badgeOk">Mining</span>';
        else if (mode === "construct") statusBadge = '<span class="badge badgeOk">Printing</span>';
        else statusBadge = '<span class="badge badgeIdle">Idle</span>';

        // Power check
        const pb = industryData.power_balance;
        if (pb && pb.electric_mw_surplus < 0 && mode !== "idle" && !["reactor", "generator", "radiator"].includes(eq.category)) {
          statusBadge = '<span class="badge badgeCrit">Unpowered</span>';
        }

        let statsChips = [];
        if (cfg.electric_mw) statsChips.push(`${cfg.electric_mw} MWe`);
        if (cfg.thermal_mw) statsChips.push(`${cfg.thermal_mw} MWth`);
        if (cfg.waste_heat_mw) statsChips.push(`${cfg.waste_heat_mw} MWth waste`);
        if (cfg.heat_rejection_mw) statsChips.push(`${cfg.heat_rejection_mw} MWth rejection`);
        if (cfg.mining_rate_kg_per_hr) statsChips.push(`${cfg.mining_rate_kg_per_hr} kg/hr mine`);
        if (cfg.water_extraction_kg_per_hr) statsChips.push(`${cfg.water_extraction_kg_per_hr} kg/hr extract`);
        if (cfg.construction_rate_kg_per_hr) statsChips.push(`${cfg.construction_rate_kg_per_hr} kg/hr build`);

        const techLabel = cfg.tech_level ? `T${cfg.tech_level}` : "";

        html += `<div class="deployEquipRow" data-equip-id="${eq.id}">
          <div class="deployEquipInfo">
            <div class="deployEquipName">${esc(eq.name)} ${techLabel ? `<span class="muted">${techLabel}</span>` : ""}</div>
            <div class="deployEquipStats">${statsChips.map(c => `<span class="eqDetail">${c}</span>`).join("")}</div>
          </div>
          <div class="deployEquipStatus">${statusBadge}</div>
          <button class="btnSmall btnUndeploy" data-equip-id="${eq.id}" ${mode !== 'idle' ? 'disabled title="Set to idle first"' : ''}>Undeploy</button>
        </div>`;
      }

      html += `</div></div>`;
    }

    container.innerHTML = html;

    // Wire undeploy buttons
    container.querySelectorAll(".btnUndeploy").forEach(btn => {
      btn.addEventListener("click", () => undeployEquipment(btn.dataset.equipId));
    });
  }

  function renderPowerBreakdown() {
    const el = document.getElementById("powerBreakdownContent");
    if (!el) return;
    const pb = industryData.power_balance;
    if (!pb) {
      el.innerHTML = '<div class="muted" style="padding:12px">No power data</div>';
      return;
    }

    let html = '<table class="pwrBreakdownTable"><thead><tr><th>Equipment</th><th>Type</th><th>Electric</th><th>Thermal</th><th>Waste Heat</th></tr></thead><tbody>';

    const addRows = (items, type) => {
      if (!items) return;
      for (const item of items) {
        html += `<tr>
          <td>${esc(item.name)}</td>
          <td>${esc(type)}</td>
          <td>${item.electric_mw ? item.electric_mw.toFixed(1) + " MWe" : "—"}</td>
          <td>${item.thermal_mw ? item.thermal_mw.toFixed(1) + " MWth" : "—"}</td>
          <td>${item.waste_heat_mw ? item.waste_heat_mw.toFixed(1) + " MWth" : "—"}</td>
        </tr>`;
      }
    };

    addRows(pb.reactors, "Reactor");
    addRows(pb.generators, "Generator");
    addRows(pb.radiators, "Radiator");
    addRows(pb.consumers, "Consumer");

    // Totals
    html += `<tr class="pwrBreakdownTotals">
      <td><strong>Totals</strong></td><td></td>
      <td>${pb.electric_mw_supply.toFixed(1)} / ${pb.electric_mw_demand.toFixed(1)} MWe</td>
      <td>${pb.thermal_mw_supply.toFixed(1)} / ${pb.thermal_mw_consumed.toFixed(1)} MWth</td>
      <td>${pb.waste_heat_mw.toFixed(1)} / ${pb.heat_rejection_mw.toFixed(1)} MWth</td>
    </tr>`;

    html += '</tbody></table>';
    el.innerHTML = html;
  }

  /* ── Miner Equipment (Mining & Refining subtab) ──────────── */

  function renderMinerEquipment() {
    const list = document.getElementById("minersList");
    const badge = document.getElementById("minerCountBadge");
    const equipment = industryData.equipment || [];
    const miners = equipment.filter(
      e => e.category === "miner" || e.category === "isru" || e.category === "constructor" || e.category === "prospector" || e.category === "robonaut"
    );

    badge.textContent = miners.length;

    if (!miners.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No miners or ISRU deployed</div>';
      return;
    }

    list.innerHTML = miners.map(eq => renderEquipmentRow(eq)).join("");
    wireEquipmentControls(list);
  }

  /* ── Refinery Equipment (Mining & Refining subtab) ───────── */

  function renderRefineryEquipment() {
    const list = document.getElementById("refineriesList");
    const badge = document.getElementById("refineryCountBadge");
    const equipment = industryData.equipment || [];
    const refineries = equipment.filter(e => e.category === "refinery");

    badge.textContent = refineries.length;

    if (!refineries.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No refineries deployed</div>';
      return;
    }

    list.innerHTML = refineries.map(eq => {
      const cfg = eq.config || {};
      const spec = cfg.specialization ? cfg.specialization.replace(/_/g, " ") : "";
      const tier = cfg.max_recipe_tier ? `Tier ${cfg.max_recipe_tier}` : "";
      const slots = cfg.max_concurrent_recipes || cfg.recipe_slots || "?";
      return `<div class="constructorRow" data-equip-id="${eq.id}">
        <div class="constructorInfo">
          <span class="constructorIcon">⚗</span>
          <div class="constructorDetails">
            <div class="constructorName">${esc(eq.name)}</div>
            <div class="constructorStats">
              ${spec ? `<span class="eqDetail">${spec}</span>` : ""}
              ${tier ? `<span class="eqDetail">${tier}</span>` : ""}
              <span class="eqDetail">${slots} slot${slots !== 1 ? "s" : ""}</span>
              <span class="eqDetail">${cfg.electric_mw || 0} MWe</span>
            </div>
          </div>
        </div>
        <button class="btnSmall btnUndeploy" data-equip-id="${eq.id}">Undeploy</button>
      </div>`;
    }).join("");

    list.querySelectorAll(".btnUndeploy").forEach(btn => {
      btn.addEventListener("click", () => undeployEquipment(btn.dataset.equipId));
    });
  }

  /* ── Printer Equipment (Printing subtab) ─────────────────── */

  function renderPrinterEquipment() {
    const list = document.getElementById("printersList");
    const badge = document.getElementById("printerCountBadge");
    const equipment = industryData.equipment || [];
    const printers = equipment.filter(e => e.category === "printer" || (e.category === "constructor" && e.mode === "construct"));

    badge.textContent = printers.length;

    if (!printers.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No printers deployed</div>';
      return;
    }

    list.innerHTML = printers.map(eq => renderEquipmentRow(eq)).join("");
    wireEquipmentControls(list);
  }

  /* ── Shared equipment row rendering ──────────────────────── */

  function renderEquipmentRow(eq) {
    const cfg = eq.config || {};
    const mode = eq.mode || "idle";
    const isMiner = eq.category === "miner";
    const isIsru = eq.category === "isru";
    const isPrinter = eq.category === "printer";
    const isProspector = eq.category === "prospector" || eq.category === "robonaut";
    const mineChecked = mode === "mine" ? "checked" : "";
    const constructChecked = mode === "construct" ? "checked" : "";
    const idleChecked = mode === "idle" ? "checked" : "";
    const miningRate = cfg.mining_rate_kg_per_hr || 0;
    const buildRate = cfg.construction_rate_kg_per_hr || 0;

    let modeHtml;
    if (isProspector) {
      modeHtml = `<div class="constructorModeSwitch">
        <label class="modeOption ${mode === 'idle' ? 'active' : ''}">
          <input type="radio" name="mode_${eq.id}" value="idle" ${idleChecked}> ⏸ Idle
        </label>
      </div>`;
    } else if (isIsru) {
      modeHtml = `<div class="constructorModeSwitch">
        <label class="modeOption ${mode === 'mine' ? 'active' : ''}">
          <input type="radio" name="mode_${eq.id}" value="mine" ${mineChecked}> 💧 Extract
        </label>
        <label class="modeOption ${mode === 'idle' ? 'active' : ''}">
          <input type="radio" name="mode_${eq.id}" value="idle" ${idleChecked}> ⏸ Idle
        </label>
      </div>`;
    } else if (isMiner) {
      modeHtml = `<div class="constructorModeSwitch">
        <label class="modeOption ${mode === 'mine' ? 'active' : ''}">
          <input type="radio" name="mode_${eq.id}" value="mine" ${mineChecked}> ⛏ Mine
        </label>
        <label class="modeOption ${mode === 'idle' ? 'active' : ''}">
          <input type="radio" name="mode_${eq.id}" value="idle" ${idleChecked}> ⏸ Idle
        </label>
      </div>`;
    } else if (isPrinter) {
      const printerType = cfg.printer_type || "";
      const printerLabel = printerType === "industrial"
        ? "🏭 Build Industrial"
        : (printerType === "ship" || printerType === "aerospace")
          ? "🚀 Build Aerospace"
          : "🔧 Build";
      modeHtml = `<div class="constructorModeSwitch">
        <label class="modeOption ${mode === 'construct' ? 'active' : ''}">
          <input type="radio" name="mode_${eq.id}" value="construct" ${constructChecked}> ${printerLabel}
        </label>
        <label class="modeOption ${mode === 'idle' ? 'active' : ''}">
          <input type="radio" name="mode_${eq.id}" value="idle" ${idleChecked}> ⏸ Idle
        </label>
      </div>`;
    } else {
      // Legacy constructor
      modeHtml = `<div class="constructorModeSwitch">
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

    let statsHtml = "";
    if (isIsru) {
      const extractRate = cfg.water_extraction_kg_per_hr || 0;
      statsHtml = `<span class="eqDetail">${extractRate} kg/hr extract</span>` +
        `<span class="eqDetail">${cfg.electric_mw || 0} MWe</span>`;
      if (cfg.branch) statsHtml += `<span class="eqDetail">${cfg.branch.replace(/_/g, " ")}</span>`;
    } else if (isMiner) {
      statsHtml = `<span class="eqDetail">${miningRate} kg/hr mine</span>` +
        `<span class="eqDetail">${cfg.electric_mw || 0} MWe</span>`;
      if (cfg.miner_type) statsHtml += `<span class="eqDetail">${cfg.miner_type.replace("_", "-")}</span>`;
    } else if (isPrinter) {
      statsHtml = `<span class="eqDetail">${buildRate} kg/hr build</span>` +
        `<span class="eqDetail">${cfg.electric_mw || 0} MWe</span>`;
      if (cfg.printer_type) statsHtml += `<span class="eqDetail">${formatPrinterTypeLabel(cfg.printer_type, true)}</span>`;
    } else if (!isProspector) {
      statsHtml = `<span class="eqDetail">${miningRate} kg/hr mine</span>` +
        `<span class="eqDetail">${buildRate} kg/hr build</span>` +
        `<span class="eqDetail">${cfg.electric_mw || 0} MWe</span>`;
    } else {
      statsHtml = `<span class="eqDetail">${cfg.electric_mw || 0} MWe</span>`;
    }

    const totalMinedHtml = eq.mining_total_kg > 0 ? `<span class="muted">Total mined: ${fmtKg(eq.mining_total_kg)}</span>` : '';
    const icon = isProspector ? '📡' : isIsru ? '💧' : isMiner ? '⛏' : isPrinter ? '🖨' : '⛏';

    return `<div class="constructorRow" data-equip-id="${eq.id}">
      <div class="constructorInfo">
        <span class="constructorIcon">${icon}</span>
        <div class="constructorDetails">
          <div class="constructorName">${esc(eq.name)}</div>
          <div class="constructorStats">${statsHtml}</div>
          ${totalMinedHtml}
        </div>
      </div>
      ${modeHtml}
      <button class="btnSmall btnUndeploy" data-equip-id="${eq.id}" ${mode !== 'idle' ? 'disabled title="Set to idle first"' : ''}>Undeploy</button>
    </div>`;
  }

  function wireEquipmentControls(container) {
    container.querySelectorAll('input[type="radio"]').forEach(radio => {
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
    container.querySelectorAll(".btnUndeploy").forEach(btn => {
      btn.addEventListener("click", () => undeployEquipment(btn.dataset.equipId));
    });
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
    const miners = equipment.filter(e => e.mode === "mine" && (e.category === "miner" || e.category === "constructor" || e.category === "isru"));
    const minable = industryData.minable_resources || [];

    if (!miners.length) {
      if (!industryData.is_prospected) {
        content.innerHTML = '<div class="muted" style="padding:8px">⚠ Site not yet prospected</div>';
      } else {
        content.innerHTML = '<div class="muted" style="padding:8px">No miners set to mine mode</div>';
      }
      return;
    }

    const totalRate = miners.reduce(
      (s, m) => s + (m.config?.mining_rate_kg_per_hr || m.config?.water_extraction_kg_per_hr || 0),
      0
    );

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
      let progressHtml = "";
      if (hasRecipe) {
        const produced = slot.cumulative_output_qty || 0;
        const batches = slot.batches_available || 0;
        const prodLabel = produced >= 1000 ? `${(produced / 1000).toFixed(1)} t` : `${produced.toFixed(0)} kg`;
        statsHtml = `<div class="slotStats">`
          + `<span class="slotProduced" title="Total produced since recipe assigned">${prodLabel} produced</span>`
          + `<span class="slotBatches ${batches === 0 ? 'noBatches' : ''}" title="Jobs worth of raw materials in storage">${batches} batch${batches !== 1 ? 'es' : ''} avail</span>`
          + `</div>`;

        // Progress bar for active jobs
        if (isActive && slot.job_started_at && slot.job_completes_at) {
          const now = serverNow();
          const total = slot.job_completes_at - slot.job_started_at;
          const elapsed = now - slot.job_started_at;
          const pct = total > 0 ? Math.min(100, Math.max(0, (elapsed / total) * 100)) : 0;
          const remain = Math.max(0, slot.job_completes_at - now);
          const remainLabel = fmtDuration(remain);
          progressHtml = `<div class="slotProgressWrap" data-job-start="${slot.job_started_at}" data-job-end="${slot.job_completes_at}">`
            + `<div class="slotProgressBar" style="width:${pct.toFixed(1)}%"></div>`
            + `<div class="slotProgressLabel">${remainLabel} remaining</div>`
            + `</div>`;
        }
      }

      const recipeName = hasRecipe ? esc(slot.recipe_name || slot.recipe_id) : '<span class="muted">Empty — click to assign recipe</span>';

      let actionsHtml = "";
      if (hasRecipe) {
        actionsHtml += `<button class="btnSmall btnChangeSlot" data-slot-id="${slot.id}" title="Change recipe">Change</button>`;
        actionsHtml += `<button class="btnSmall btnClearSlot" data-slot-id="${slot.id}" title="Clear recipe assignment">Clear</button>`;
      }

      return `<div class="refinerySlotRow ${isActive ? 'slotActive' : ''}" data-slot-id="${slot.id}" data-slot-idx="${idx}" draggable="true">
        <div class="slotDragHandle" title="Drag to reorder priority">⠿</div>
        <div class="slotPriority">#${idx + 1}</div>
        <div class="slotEquipName">${esc(slot.equipment_name)}</div>
        <div class="slotRecipe ${hasRecipe ? '' : 'slotEmpty'}" data-slot-id="${slot.id}">${recipeName}</div>
        ${statsHtml}
        <div class="slotActions">${actionsHtml}</div>
        ${progressHtml}
      </div>`;
    }).join("");

    // Wire click-to-assign on empty slots
    list.querySelectorAll(".slotRecipe.slotEmpty").forEach(el => {
      el.addEventListener("click", () => {
        const slotId = el.dataset.slotId;
        const slot = slots.find(s => s.id === slotId);
        if (slot) {
          openRecipeSelectModal("refinery", slotId, slot.specialization);
        }
      });
    });

    // Wire change buttons (opens recipe select modal)
    list.querySelectorAll(".btnChangeSlot").forEach(btn => {
      btn.addEventListener("click", () => {
        const slotId = btn.dataset.slotId;
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

    // Start live progress-bar ticker
    startRefineryProgressTicker();
  }

  let _refineryTickInterval = null;
  function startRefineryProgressTicker() {
    if (_refineryTickInterval) clearInterval(_refineryTickInterval);
    _refineryTickInterval = setInterval(() => {
      document.querySelectorAll(".slotProgressWrap").forEach(wrap => {
        const start = Number(wrap.dataset.jobStart);
        const end = Number(wrap.dataset.jobEnd);
        if (!start || !end || end <= start) return;
        const now = serverNow();
        const total = end - start;
        const elapsed = now - start;
        const pct = Math.min(100, Math.max(0, (elapsed / total) * 100));
        const remain = Math.max(0, end - now);
        const bar = wrap.querySelector(".slotProgressBar");
        const label = wrap.querySelector(".slotProgressLabel");
        if (bar) bar.style.width = pct.toFixed(1) + "%";
        if (label) label.textContent = fmtDuration(remain) + " remaining";
        // If batch is done, trigger a refresh
        if (remain <= 0) {
          clearInterval(_refineryTickInterval);
          _refineryTickInterval = null;
          loadIndustryContent();
        }
      });
    }, 1000);
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
            facility_id: industryFacilityId || "",
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
      : "No printers in build mode";

    // Wire add-to-queue button (must be before early return)
    const addBtn = document.getElementById("btnAddConstruction");
    addBtn.onclick = () => openRecipeSelectModal("construction", null, null);

    if (!queue.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">Construction queue empty. Add recipes to build.</div>';
      return;
    }

    list.innerHTML = queue.map((item, idx) => {
      const isActive = item.status === "active";
      const isWaiting = item.status === "waiting_materials";
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

      let statusHtml = "";
      if (isWaiting) {
        // Show missing materials
        const missing = item.missing_materials || [];
        const missingHtml = missing.length
          ? missing.map(m => `<span class="queueMissingItem">${esc(m.name || m.item_id)} (need ${fmtKg(m.required_kg)} — have ${fmtKg(m.available_kg)})</span>`).join(", ")
          : "materials";
        statusHtml = `<div class="queueWaitingMaterials"><span class="badge badgeWarn">Waiting for materials</span><div class="queueMissingList">${missingHtml}</div></div>`;
      } else if (!isActive && (item.missing_materials || []).length) {
        // Queued but missing some materials — show warning
        const missing = item.missing_materials;
        const missingHtml = missing.map(m => `<span class="queueMissingItem">${esc(m.name || m.item_id)} (need ${fmtKg(m.required_kg)} — have ${fmtKg(m.available_kg)})</span>`).join(", ");
        statusHtml = `<div class="queueWaitingMaterials"><span class="badge badgeWarn">Missing materials</span><div class="queueMissingList">${missingHtml}</div></div>`;
      }

      const outputLabel = (item.output_item_id || "?").replace(/_/g, " ");
      const rowClass = isActive ? "queueActive" : isWaiting ? "queueWaiting" : "";

      return `<div class="constructionQueueRow ${rowClass}" data-queue-id="${item.id}" draggable="${isActive ? 'false' : 'true'}">
        <div class="queueOrder">${isActive ? '▶' : `#${idx + 1}`}</div>
        <div class="queueInfo">
          <div class="queueName">${esc(item.recipe_name || item.recipe_id)}</div>
          <div class="queueOutput">→ ${esc(outputLabel)} ×${item.output_qty || 1}</div>
          ${statusHtml}
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
            facility_id: industryFacilityId || "",
          });
          loadIndustryContent();
        } catch (e) {
          alert("Reorder failed: " + e.message);
        }
      });
    });
  }

  /* ── Recipe Select Modal ─────────────────────────────────── */

  let constructionStaging = []; // [{recipe_id, recipe_name, quantity, inputs_status, build_time_s, output_item_id, output_qty}]

  function openRecipeSelectModal(mode, slotId, specialization) {
    const modal = document.getElementById("recipeSelectModal");
    const title = document.getElementById("recipeSelectTitle");
    const singlePane = document.getElementById("recipeSelectSinglePane");
    const dualPane = document.getElementById("recipeSelectDualPane");
    const content = document.getElementById("recipeSelectContent");
    modal.style.display = "";

    const recipes = industryData.available_recipes || [];

    if (mode === "refinery") {
      singlePane.style.display = "";
      dualPane.style.display = "none";
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
      singlePane.style.display = "none";
      dualPane.style.display = "";
      title.textContent = "Add to Construction Queue";

      constructionStaging = [];

      const filtered = recipes.filter(r => r.facility_type === "shipyard");
      const catalog = document.getElementById("recipeConstructionCatalog");

      if (!filtered.length) {
        catalog.innerHTML = '<div class="muted" style="padding:12px">No construction recipes available</div>';
        renderConstructionStaging();
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
        radiator: "Radiators", refinery: "Refineries", constructor: "Legacy Constructors",
        miner: "Miners", printer: "Printers",
        prospector: "Prospectors", robonaut: "Prospectors", other: "Other",
      };
      const catOrder = ["thruster", "reactor", "generator", "radiator", "refinery", "miner", "printer", "constructor", "prospector", "robonaut", "other"];
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
          html += `<div class="recipeSelectRow">
            <div class="recipeInfo">
              <div class="recipeName">${esc(r.name)}</div>
              <div class="recipeInputs">${inputsHtml}</div>
              <div class="recipeMeta"><span>Time: ${fmtDuration(r.build_time_s)}</span> <span>→ ${esc(outName)} ×${r.output_qty}</span></div>
            </div>
            <button class="btnSmall btnAddToStaging" data-recipe-id="${r.recipe_id}">+</button>
          </div>`;
        });
      }
      catalog.innerHTML = html;

      // Wire catalog "+" buttons → add to staging
      catalog.querySelectorAll(".btnAddToStaging").forEach(btn => {
        btn.addEventListener("click", () => {
          const rid = btn.dataset.recipeId;
          const existing = constructionStaging.find(s => s.recipe_id === rid);
          if (existing) {
            existing.quantity++;
          } else {
            const r = filtered.find(x => x.recipe_id === rid);
            constructionStaging.push({
              recipe_id: rid,
              recipe_name: r ? r.name : rid,
              quantity: 1,
              build_time_s: r ? r.build_time_s : 0,
              output_item_id: r ? r.output_item_id : "",
              output_qty: r ? r.output_qty : 0,
            });
          }
          renderConstructionStaging();
        });
      });

      renderConstructionStaging();

      // Wire confirm button
      document.getElementById("recipeStagingConfirm").onclick = async () => {
        if (!constructionStaging.length) return;
        const btn = document.getElementById("recipeStagingConfirm");
        btn.disabled = true;
        btn.textContent = "Adding…";
        try {
          for (const item of constructionStaging) {
            await postJSON("/api/industry/construction/queue", {
              location_id: industryLocationId,
              recipe_id: item.recipe_id,
              facility_id: industryFacilityId || "",
              quantity: item.quantity,
            });
          }
          constructionStaging = [];
          modal.style.display = "none";
          loadIndustryContent();
          loadSites();
        } catch (e) {
          alert("Failed: " + e.message);
        } finally {
          btn.disabled = false;
          btn.textContent = "Add to Queue";
        }
      };
    }
  }

  function renderConstructionStaging() {
    const list = document.getElementById("recipeStagingList");
    const confirmBtn = document.getElementById("recipeStagingConfirm");
    if (!list) return;

    if (!constructionStaging.length) {
      list.innerHTML = '<div class="muted" style="padding:12px;text-align:center">Click + on a recipe to add it</div>';
      confirmBtn.disabled = true;
      return;
    }
    confirmBtn.disabled = false;

    list.innerHTML = constructionStaging.map((item, idx) => {
      const outName = (item.output_item_id || "?").replace(/_/g, " ");
      return `<div class="stagingRow" data-idx="${idx}">
        <div class="stagingInfo">
          <div class="stagingName">${esc(item.recipe_name)}</div>
          <div class="stagingMeta">→ ${esc(outName)} ×${item.output_qty}</div>
        </div>
        <div class="stagingControls">
          <button class="btnStagingMinus" data-idx="${idx}">−</button>
          <span class="stagingQty">${item.quantity}</span>
          <button class="btnStagingPlus" data-idx="${idx}">+</button>
          <button class="btnStagingRemove" data-idx="${idx}">✕</button>
        </div>
      </div>`;
    }).join("");

    list.querySelectorAll(".btnStagingMinus").forEach(btn => {
      btn.addEventListener("click", () => {
        const idx = parseInt(btn.dataset.idx);
        if (constructionStaging[idx].quantity > 1) {
          constructionStaging[idx].quantity--;
        } else {
          constructionStaging.splice(idx, 1);
        }
        renderConstructionStaging();
      });
    });
    list.querySelectorAll(".btnStagingPlus").forEach(btn => {
      btn.addEventListener("click", () => {
        const idx = parseInt(btn.dataset.idx);
        constructionStaging[idx].quantity++;
        renderConstructionStaging();
      });
    });
    list.querySelectorAll(".btnStagingRemove").forEach(btn => {
      btn.addEventListener("click", () => {
        const idx = parseInt(btn.dataset.idx);
        constructionStaging.splice(idx, 1);
        renderConstructionStaging();
      });
    });
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

    // ── Thermal Balance block ──
    let thermalHtml = '<div class="pwrPanelBlock">';
    thermalHtml += '<div class="pwrPanelTitle">Thermal Balance</div>';

    // Thermal section
    const thMax = Math.max(pb.thermal_mw_supply, pb.thermal_mw_consumed, 1);
    thermalHtml += '<div class="pwrSection">';
    thermalHtml += '<div class="pwrSectionTitle">☢ Thermal</div>';
    thermalHtml += bar("Reactor Output", pb.thermal_mw_supply, thMax, "MWth", "pwrFillThermal");
    thermalHtml += bar("Generator Demand", pb.thermal_mw_consumed, thMax, "MWth", "pwrFillDemand");
    if (pb.thermal_mw_surplus > 0) {
      thermalHtml += `<div class="pwrNote ok">+${pb.thermal_mw_surplus.toFixed(1)} MWth surplus (absorbed by surface)</div>`;
    }
    if (pb.gen_throttle < 1) {
      thermalHtml += `<div class="pwrNote warn">Generators throttled to ${(pb.gen_throttle * 100).toFixed(0)}% — insufficient thermal</div>`;
    }
    thermalHtml += '</div>';

    // Waste heat section
    const whMax = Math.max(pb.waste_heat_mw, pb.heat_rejection_mw, 1);
    thermalHtml += '<div class="pwrSection">';
    thermalHtml += '<div class="pwrSectionTitle">🌡 Waste Heat</div>';
    thermalHtml += bar("Generated", pb.waste_heat_mw, whMax, "MWth", "pwrFillWaste");
    thermalHtml += bar("Radiator Capacity", pb.heat_rejection_mw, whMax, "MWth", "pwrFillRadiator");
    if (pb.waste_heat_surplus_mw > 0) {
      thermalHtml += `<div class="pwrNote ok">${pb.waste_heat_surplus_mw.toFixed(1)} MWth excess — absorbed by surface</div>`;
    } else {
      thermalHtml += `<div class="pwrNote ok">Heat balanced</div>`;
    }
    thermalHtml += '</div>';
    thermalHtml += '</div>';

    // ── Power Balance block ──
    let powerHtml = '<div class="pwrPanelBlock">';
    powerHtml += '<div class="pwrPanelTitle">Power Balance</div>';

    // Electric section
    const elMax = Math.max(pb.electric_mw_supply, pb.electric_mw_demand, 1);
    powerHtml += '<div class="pwrSection">';
    powerHtml += '<div class="pwrSectionTitle">⚡ Electric</div>';
    powerHtml += bar("Generator Output", pb.electric_mw_supply, elMax, "MWe", "pwrFillElectric");
    powerHtml += bar("Equipment Demand", pb.electric_mw_demand, elMax, "MWe", "pwrFillDemand");

    // Per-consumer breakdown
    if (pb.consumers && pb.consumers.length > 0) {
      const activeConsumers = pb.consumers.filter(c => c.active && c.electric_mw > 0);
      if (activeConsumers.length > 0) {
        powerHtml += '<div class="pwrConsumerList">';
        for (const c of activeConsumers) {
          powerHtml += `<div class="pwrConsumerRow"><span class="pwrConsumerName">${c.name}</span><span class="pwrConsumerVal">${Number(c.electric_mw).toFixed(1)} MWe</span></div>`;
        }
        powerHtml += '</div>';
      }
    }

    if (pb.electric_mw_surplus >= 0) {
      powerHtml += `<div class="pwrNote ok">+${pb.electric_mw_surplus.toFixed(1)} MWe surplus</div>`;
    } else {
      powerHtml += `<div class="pwrNote crit">⚠ ${pb.electric_mw_surplus.toFixed(1)} MWe — POWER DEFICIT</div>`;
      powerHtml += `<div class="pwrNote crit">Mining, refining, and construction are <b>halted</b> until power is restored.</div>`;
    }
    powerHtml += '</div>';
    powerHtml += '</div>';

    el.innerHTML = thermalHtml + powerHtml;
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
          prospectors: "prospector", prospector: "prospector",
          robonauts: "robonaut", robots: "robonaut", robot: "robonaut",
          constructors: "constructor", builders: "constructor", builder: "constructor",
          miners: "miner", miner: "miner",
          printers: "printer", printer: "printer",
          isru_modules: "isru", isru_module: "isru", isru_units: "isru", isru_unit: "isru",
          water_extractor: "isru", water_extraction: "isru", sifting: "isru", heat_drill: "isru",
          refineries: "refinery", reactors: "reactor", generators: "generator", radiators: "radiator",
        });
        return aliases[value] || value;
      };
      const deployableCategories = new Set(["refinery", "miner", "printer", "constructor", "prospector", "robonaut", "isru", "reactor", "generator", "radiator"]);
      const catOrder = ["reactor", "generator", "radiator", "refinery", "miner", "isru", "printer", "constructor", "prospector", "robonaut"];
      const catLabels = Object.create(null);
      Object.assign(catLabels, { reactor: "Reactors", generator: "Generators", radiator: "Radiators",
        refinery: "Refineries", miner: "Miners", isru: "ISRU", printer: "Printers", constructor: "Legacy Constructors", prospector: "Prospectors", robonaut: "Prospectors" });

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
            waterExtractionRate: Number(item.water_extraction_kg_per_hr || 0),
            constructionRate: Number(item.construction_rate_kg_per_hr || 0),
            minWaterIceFrac: Number(item.min_water_ice_fraction || 0),
            maxWaterIceFrac: Number(item.max_water_ice_fraction || 0),
            prospectRangeKm: Number(item.prospect_range_km || 0),
            scanRateKm2PerHr: Number(item.scan_rate_km2_per_hr || 0),
            conversionEff: Number(item.conversion_efficiency || 0),
            excavationType: String(item.excavation_type || ""),
            minerType: String(item.miner_type || ""),
            printerType: String(item.printer_type || ""),
            fabricationType: String(item.fabrication_type || ""),
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
        } else if (cat === "miner") {
          if (item.miningRate) chips.push(`<b>${item.miningRate}</b> kg/hr mine`);
          if (item.electricMw) chips.push(`<b>${item.electricMw}</b> MWe`);
          if (item.minerType) chips.push(item.minerType.replace(/_/g, " "));
          if (item.excavationType) chips.push(item.excavationType.replace(/_/g, " "));
          if (item.minGravity) chips.push(`≥${item.minGravity} m/s²`);
        } else if (cat === "isru") {
          if (item.waterExtractionRate) chips.push(`<b>${item.waterExtractionRate}</b> kg/hr extract`);
          if (item.electricMw) chips.push(`<b>${item.electricMw}</b> MWe`);
          if (item.minWaterIceFrac > 0) chips.push(`Ice ≥ ${(item.minWaterIceFrac * 100).toFixed(0)}%`);
          if (item.maxWaterIceFrac > 0 && item.maxWaterIceFrac < 1) chips.push(`Ice ≤ ${(item.maxWaterIceFrac * 100).toFixed(0)}%`);
          if (item.excavationType) chips.push(item.excavationType.replace(/_/g, " "));
        } else if (cat === "printer") {
          if (item.constructionRate) chips.push(`<b>${item.constructionRate}</b> kg/hr build`);
          if (item.electricMw) chips.push(`<b>${item.electricMw}</b> MWe`);
          if (item.printerType) chips.push(item.printerType.replace(/_/g, " "));
          if (item.fabricationType) chips.push(item.fabricationType.replace(/_/g, " "));
        } else if (cat === "constructor") {
          if (item.miningRate) chips.push(`<b>${item.miningRate}</b> kg/hr mine`);
          if (item.constructionRate) chips.push(`<b>${item.constructionRate}</b> kg/hr build`);
          if (item.electricMw) chips.push(`<b>${item.electricMw}</b> MWe`);
          if (item.minGravity) chips.push(`≥${item.minGravity} m/s²`);
        } else if (cat === "prospector" || cat === "robonaut") {
          if (item.prospectRangeKm) chips.push(`Range <b>${item.prospectRangeKm}</b> km`);
          if (item.scanRateKm2PerHr) chips.push(`<b>${item.scanRateKm2PerHr}</b> km²/hr scan`);
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
                facility_id: industryFacilityId || "",
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
  let cargoFacilityId = null;
  let cargoFacilitiesByLocation = Object.create(null);
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
      let html = `<tr${sel} data-id="${esc(s.id)}"><td>${esc(s.name)}</td><td>${shipCount}</td><td>${invCount}</td></tr>`;
      if (cargoLocationId === s.id) {
        const facilities = cargoFacilitiesByLocation[s.id] || [];
        html += facilities.map(f => {
          const st = f.stats || {};
          const selectedClass = cargoFacilityId === f.id ? " selected" : "";
          return `<tr class="cargoFacilityRow${selectedClass}" data-location-id="${esc(s.id)}" data-facility-id="${esc(f.id)}">
            <td colspan="3">└─ ${esc(f.name)} · ⚡ ${Number(st.power_mwe || 0).toFixed(1)} MWe · 📦 ${Number(st.inventory_stack_count || 0)} stacks</td>
          </tr>`;
        }).join("");
      }
      return html;
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
    tbody.querySelectorAll("tr[data-facility-id]").forEach(row => {
      row.style.cursor = "pointer";
      row.addEventListener("click", (e) => {
        e.stopPropagation();
        selectCargoFacility(row.dataset.locationId, row.dataset.facilityId);
      });
    });
  }

  async function selectCargoLocation(locationId) {
    cargoLocationId = locationId;
    await loadCargoFacilities(locationId);
    renderCargoSitesTable();

    const mine = cargoFacilitiesByLocation[locationId] || [];
    if (!mine.length) {
      cargoFacilityId = null;
      await loadCargoContext();
      return;
    }

    if (cargoFacilityId && mine.some(f => f.id === cargoFacilityId)) {
      await loadCargoContext();
      return;
    }

    cargoFacilityId = null;
    const placeholder = document.getElementById("cargoPlaceholder");
    const workspace = document.getElementById("cargoWorkspace");
    if (workspace) workspace.style.display = "none";
    if (placeholder) {
      placeholder.style.display = "";
      placeholder.innerHTML = `<div class="muted">Select a facility under ${esc(locationId)} to manage cargo</div>`;
    }
  }

  async function loadCargoFacilities(locationId) {
    if (!locationId) return;
    try {
      const data = await fetchJSON(`/api/facilities/${encodeURIComponent(locationId)}`);
      const mine = (data.facilities || []).filter(f => f.is_mine);
      cargoFacilitiesByLocation[locationId] = mine;
    } catch (_) {
      cargoFacilitiesByLocation[locationId] = [];
    }
  }

  async function selectCargoFacility(locationId, facilityId) {
    cargoLocationId = locationId;
    cargoFacilityId = facilityId;
    renderCargoSitesTable();
    await loadCargoContext();
  }

  async function loadCargoContext() {
    if (!cargoLocationId) return;
    const placeholder = document.getElementById("cargoPlaceholder");
    const workspace = document.getElementById("cargoWorkspace");
    try {
      let url = `/api/cargo/context/${encodeURIComponent(cargoLocationId)}`;
      if (cargoFacilityId) url += `?facility_id=${encodeURIComponent(cargoFacilityId)}`;
      cargoContext = await fetchJSON(url);
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
    if (nameEl) nameEl.textContent = cargoContext.location_name || cargoContext.location?.name || cargoLocationId;
    const bc = document.getElementById("cargoContextBreadcrumb");
    if (bc) {
      const body = cargoContext.body_name || "—";
      const site = cargoContext.location_name || cargoContext.location?.name || cargoLocationId || "—";
      const facility = cargoContext.facility_name || "—";
      bc.textContent = `Body: ${body}  →  Site: ${site}  →  Facility: ${facility}`;
    }

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

    const category = item.category_id || item.category || item.type;
    let subtitle = item.subtitle || item.category_id;
    if (String(category || "").toLowerCase() === "printer" && item.printer_type) {
      subtitle = formatPrinterTypeLabel(item.printer_type, true);
    }

    const tooltipLines = mergeTooltipLines(item.tooltip_lines, buildModuleTooltipLines(item));

    const cell = itemDisplay.createGridCell({
      label: item.label || item.name || "Item",
      iconSeed: item.icon_seed || item.item_uid || item.item_id,
      itemId: item.item_id || "",
      category: category,
      quantity: item.quantity || item.amount,
      mass_kg: item.mass_kg,
      volume_m3: item.volume_m3,
      subtitle: subtitle,
      branch: item.branch || "",
      family: item.family || item.thruster_family || "",
      techLevel: item.tech_level || "",
      core_temp_k: item.core_temp_k,
      rated_temp_k: item.rated_temp_k,
      water_extraction_kg_per_hr: item.water_extraction_kg_per_hr,
      min_water_ice_fraction: item.min_water_ice_fraction,
      max_water_ice_fraction: item.max_water_ice_fraction,
      miner_type: item.miner_type,
      operational_environment: item.operational_environment,
      min_surface_gravity_ms2: item.min_surface_gravity_ms2,
      max_surface_gravity_ms2: item.max_surface_gravity_ms2,
      min_volatile_mass_fraction: item.min_volatile_mass_fraction,
      thermal_mw_input: item.thermal_mw_input,
      electric_mw: item.electric_mw,
      conversion_efficiency: item.conversion_efficiency,
      max_concurrent_recipes: item.max_concurrent_recipes,
      recipe_slots: item.recipe_slots,
      supported_recipe_names: item.supported_recipe_names,
      tooltipLines: tooltipLines.length ? tooltipLines : undefined,
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
          const stackBody = {
            source_kind: staged.sourceKind,
            source_id: staged.sourceId,
            source_key: staged.sourceKeyStr,
            target_kind: destKind,
            target_id: destId,
          };
          if (destKind === "location" && cargoFacilityId) stackBody.facility_id = cargoFacilityId;
          await postJSON("/api/stack/transfer", stackBody);
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
          if (destKind === "location" && cargoFacilityId) transferBody.facility_id = cargoFacilityId;
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
      if (currentTab === "industrial" && industryLocationId && industryFacilityId) loadIndustryContent();
      if (currentTab === "cargo" && cargoLocationId) loadCargoContext();
      if (selectedSiteId) selectSite(selectedSiteId);
    }, 10000);

    // Also refresh job progress bars every second
    setInterval(() => {
      if (currentTab === "industrial" && industryData) {
        if (currentIndustrySubtab === "mining") renderRefinerySlots();
        if (currentIndustrySubtab === "printing") renderConstructionQueue();
      }
    }, 1000);
  }

  async function init() {
    await syncClock();
    initTabSwitching();
    initFilters();
    initIndustryLocationSelect();
    initIndustrySubtabs();
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
