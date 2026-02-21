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
  let industryLocationId = null;
  let industryData = null;
  let pollTimer = null;
  const collapsedConstructCats = new Set();  // persists collapsed state across re-renders

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
        return (eq.refinery || eq.constructor || {}).total > 0 || s.active_jobs > 0;
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
      const equipStr = (refCount || conCount)
        ? `<span class="eqBadge ref">${refCount}R</span><span class="eqBadge con">${conCount}C</span>`
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
          label: r.name, iconSeed: r.item_id, category: "resource",
          mass_kg: r.mass_kg, quantity: r.quantity, subtitle: fmtKg(r.mass_kg),
        });
        invGrid.appendChild(cell);
      });
      parts.forEach(p => {
        const part = p.part || {};
        const cat = part.category_id || part.type || "generic";
        const cell = itemDisplay.createGridCell({
          label: p.name, iconSeed: p.item_id, category: cat,
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
          label: eq.name, iconSeed: eq.item_id, category: eq.category,
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

  function populateIndustryLocationSelect() {
    const sel = document.getElementById("industryLocationSelect");
    const current = sel.value;

    // Group by parent
    const withIndustry = [];
    const others = [];
    allSites.forEach(s => {
      const eq = s.equipment || {};
      const hasInd = (eq.refinery || {}).total || (eq.constructor || {}).total || s.active_jobs > 0;
      if (hasInd) withIndustry.push(s); else others.push(s);
    });

    let html = '<option value="">— Select a location —</option>';
    if (withIndustry.length) {
      html += '<optgroup label="Industrial Sites">';
      withIndustry.forEach(s => html += `<option value="${esc(s.id)}">${esc(s.name)}</option>`);
      html += '</optgroup>';
    }
    html += '<optgroup label="All Locations">';
    others.forEach(s => html += `<option value="${esc(s.id)}">${esc(s.name)}</option>`);
    html += '</optgroup>';

    sel.innerHTML = html;
    if (current) sel.value = current;
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
      ["Equipment", renderIndustryEquipment],
      ["RefineJobs", renderRefineJobs],
      ["ConstructJobs", renderConstructJobs],
      ["ProductionChain", renderProductionChain],
      ["RefineRecipes", renderRefineRecipes],
      ["ConstructRecipes", renderConstructRecipes],
      ["Mining", renderMiningSection],
      ["JobHistory", renderJobHistory],
    ];
    for (const [name, fn] of renderers) {
      try { fn(); } catch (e) { console.error(`renderIndustry: ${name} failed:`, e); }
    }
  }

  /* ── Equipment panel ─────────────────────────────────────── */

  function renderIndustryEquipment() {
    const list = document.getElementById("industryEquipList");
    const equipment = industryData.equipment || [];

    if (!equipment.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No equipment deployed. Deploy refineries or constructors from inventory.</div>';
      return;
    }

    list.innerHTML = equipment.map(eq => {
      const cfg = eq.config || {};
      const statusClass = eq.status === "active" ? "eqStatusActive" : "eqStatusIdle";
      let details = "";
      if (eq.category === "refinery") {
        details = `<span class="eqDetail">${esc(cfg.specialization || "general")}</span>
          <span class="eqDetail">Tier ${cfg.max_recipe_tier || 1}</span>
          <span class="eqDetail">${cfg.electric_mw || 0} MW</span>`;
      } else {
        details = `<span class="eqDetail">${cfg.mining_rate_kg_per_hr || 0} kg/hr mining</span>
          <span class="eqDetail">${cfg.construction_rate_kg_per_hr || 0} kg/hr build</span>
          <span class="eqDetail">${cfg.electric_mw || 0} MW</span>`;
      }

      return `<div class="industryEquipRow">
        <div class="eqIcon">${eq.category === "refinery" ? "⚗" : "⛏"}</div>
        <div class="eqInfo">
          <div class="eqName">${esc(eq.name)}</div>
          <div class="eqDetails">${details}</div>
        </div>
        <div class="eqStatus ${statusClass}">${eq.status}</div>
        <div class="eqActions">
          ${eq.status === "idle" && eq.category === "refinery" ?
            `<button class="btnSmall btnStart" data-equip-id="${eq.id}">Start Job</button>` : ""}
          ${eq.status === "idle" && eq.category === "constructor" ?
            `<button class="btnSmall btnBuild" data-equip-id="${eq.id}">Start Build</button>` : ""}
          ${eq.status === "idle" && eq.category === "constructor" ?
            `<button class="btnSmall btnMine" data-equip-id="${eq.id}">Start Mining</button>` : ""}
          ${eq.status === "idle" ?
            `<button class="btnSmall btnUndeploy" data-equip-id="${eq.id}">Undeploy</button>` : ""}
        </div>
      </div>`;
    }).join("");

    // Wire actions
    list.querySelectorAll(".btnStart").forEach(btn => {
      btn.addEventListener("click", () => openStartJobModal(btn.dataset.equipId));
    });
    list.querySelectorAll(".btnBuild").forEach(btn => {
      btn.addEventListener("click", () => openStartBuildModal(btn.dataset.equipId));
    });
    list.querySelectorAll(".btnMine").forEach(btn => {
      btn.addEventListener("click", () => openMiningModal(btn.dataset.equipId));
    });
    list.querySelectorAll(".btnUndeploy").forEach(btn => {
      btn.addEventListener("click", () => undeployEquipment(btn.dataset.equipId));
    });
  }

  /* ── Refinery jobs panel ───────────────────────────────── */

  function renderRefineJobs() {
    const list = document.getElementById("industryRefineJobsList");
    const jobs = (industryData.active_jobs || []).filter(j => j.job_type === "refine");

    if (!jobs.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No active refinery jobs</div>';
      return;
    }

    const now = serverNow();
    list.innerHTML = jobs.map(job => {
      const started = job.started_at;
      const completes = job.completes_at;
      const totalDur = Math.max(1, completes - started);
      const elapsed = Math.min(now - started, totalDur);
      const pct = Math.min(100, Math.max(0, (elapsed / totalDur) * 100));
      const remaining = Math.max(0, completes - now);

      return `<div class="industryJobRow refine">
        <div class="jobIcon">⚗</div>
        <div class="jobInfo">
          <div class="jobName">${esc(job.recipe_name || job.recipe_id)}</div>
          <div class="jobMeta">${esc(job.equipment_name)} · ETA: ${fmtDuration(remaining)}</div>
        </div>
        <div class="jobProgress">
          <div class="bar"><div class="barFill" style="width:${pct.toFixed(1)}%"></div></div>
          <span class="jobPct">${pct.toFixed(0)}%</span>
        </div>
        <button class="btnSmall btnCancel" data-job-id="${job.id}" data-job-type="refine">Cancel</button>
      </div>`;
    }).join("");

    list.querySelectorAll(".btnCancel").forEach(btn => {
      btn.addEventListener("click", () => cancelJob(btn.dataset.jobId));
    });
  }

  /* ── Construction jobs panel ─────────────────────────────── */

  function renderConstructJobs() {
    const list = document.getElementById("industryConstructJobsList");
    const jobs = (industryData.active_jobs || []).filter(j => j.job_type === "construct" || j.job_type === "mine");

    if (!jobs.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No active construction or mining jobs</div>';
      return;
    }

    const now = serverNow();
    list.innerHTML = jobs.map(job => {
      if (job.job_type === "mine") {
        return `<div class="industryJobRow mining">
          <div class="jobIcon">⛏</div>
          <div class="jobInfo">
            <div class="jobName">Mining: ${esc(job.resource_name || job.resource_id)}</div>
            <div class="jobMeta">${esc(job.equipment_name)} · ${job.rate_kg_per_hr} kg/hr</div>
            <div class="jobMeta muted">Total mined: ${fmtKg(job.total_mined_kg)}</div>
          </div>
          <div class="jobProgress"><span class="badge badgeActive">Mining</span></div>
          <button class="btnSmall btnCancel" data-job-id="${job.id}" data-job-type="mine">Stop</button>
        </div>`;
      }

      // Construct job
      const started = job.started_at;
      const completes = job.completes_at;
      const totalDur = Math.max(1, completes - started);
      const elapsed = Math.min(now - started, totalDur);
      const pct = Math.min(100, Math.max(0, (elapsed / totalDur) * 100));
      const remaining = Math.max(0, completes - now);

      return `<div class="industryJobRow construct">
        <div class="jobIcon">🔧</div>
        <div class="jobInfo">
          <div class="jobName">${esc(job.recipe_name || job.recipe_id)}</div>
          <div class="jobMeta">${esc(job.equipment_name)} · ETA: ${fmtDuration(remaining)}</div>
        </div>
        <div class="jobProgress">
          <div class="bar"><div class="barFill" style="width:${pct.toFixed(1)}%"></div></div>
          <span class="jobPct">${pct.toFixed(0)}%</span>
        </div>
        <button class="btnSmall btnCancel" data-job-id="${job.id}" data-job-type="construct">Cancel</button>
      </div>`;
    }).join("");

    list.querySelectorAll(".btnCancel").forEach(btn => {
      btn.addEventListener("click", () => {
        const jtype = btn.dataset.jobType;
        if (jtype === "mine") stopMining(btn.dataset.jobId);
        else cancelJob(btn.dataset.jobId);
      });
    });
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

  /* ── Refining recipes ──────────────────────────────────── */

  function renderRefineRecipes() {
    const list = document.getElementById("industryRefineRecipesList");
    const recipes = (industryData.available_recipes || []).filter(r => r.facility_type !== "shipyard");

    if (!recipes.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No refining recipes available. Deploy refineries to unlock recipes.</div>';
      return;
    }

    renderRecipeList(list, recipes, "refinery");
  }

  /* ── Construction recipes ────────────────────────────────── */

  function renderConstructRecipes() {
    const list = document.getElementById("industryConstructRecipesList");
    if (!list) return;
    try {
      const recipes = (industryData.available_recipes || []).filter(r => r.facility_type === "shipyard");

      if (!recipes.length) {
        list.innerHTML = '<div class="muted" style="padding:12px">No construction recipes available. Deploy constructors to unlock recipes.</div>';
        return;
      }

      // Group by output_category (part type)
      // Use Object.create(null) to avoid prototype collisions (e.g. "constructor")
      const groups = Object.create(null);
      recipes.forEach(r => {
        const cat = r.output_category || "other";
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(r);
      });

      const categoryOrder = ["thruster", "reactor", "generator", "radiator", "refinery", "constructor", "robonaut", "other"];
      const categoryLabels = {
        thruster: "Thrusters", reactor: "Reactors", generator: "Generators",
        radiator: "Radiators", refinery: "Refineries", constructor: "Constructors",
        robonaut: "Robonauts", other: "Other",
      };

      const sortedCats = categoryOrder.filter(c => groups[c]);
      Object.keys(groups).forEach(c => { if (!sortedCats.includes(c)) sortedCats.push(c); });

      let html = "";
      for (const catId of sortedCats) {
        const catRecipes = groups[catId];
        const label = categoryLabels[catId] || catId;
        const count = catRecipes.length;

        var isCollapsed = collapsedConstructCats.has(catId);
        html += '<div class="recipeCategoryGroup collapsibleGroup">';
        html += '<div class="recipeCategoryHeader collapsibleHeader" data-cat-id="' + catId + '" data-collapsed="' + isCollapsed + '">';
        html += '<span class="collapseToggle">' + (isCollapsed ? '&#9656;' : '&#9662;') + '</span> ';
        html += '<span>' + esc(label) + '</span> ';
        html += '<span class="recipeCategoryCount">' + count + '</span>';
        html += '</div>';
        html += '<div class="collapsibleBody"' + (isCollapsed ? ' style="display:none"' : '') + '>';

        for (let i = 0; i < catRecipes.length; i++) {
          const recipe = catRecipes[i];
          const inputs = recipe.inputs_status || [];
          let inputsHtml = "";
          for (let j = 0; j < inputs.length; j++) {
            const inp = inputs[j];
            const ok = inp.sufficient;
            const qa = Number(inp.qty_available || 0).toFixed(2);
            const qn = Number(inp.qty_needed || 0).toFixed(2);
            inputsHtml += '<span class="recipeInput ' + (ok ? "sufficient" : "insufficient") + '">';
            inputsHtml += esc(inp.name) + ": " + qa + "/" + qn;
            inputsHtml += '</span> ';
          }

          const canStart = recipe.can_start;
          const idleEquip = recipe.idle_constructors || [];
          const outName = (recipe.output_item_id || "?").replace(/_/g, " ");

          html += '<div class="recipeRow ' + (canStart ? "canStart" : "cantStart") + '">';
          html += '<div class="recipeInfo">';
          html += '<div class="recipeName">' + esc(recipe.name) + '</div>';
          html += '<div class="recipeInputs">' + inputsHtml + '</div>';
          html += '<div class="recipeMeta">';
          html += '<span>Time: ' + fmtDuration(recipe.build_time_s) + '</span> ';
          html += '<span>Tier ' + (recipe.min_tech_tier || 1) + '</span> ';
          html += '<span>' + (recipe.power_kw || 0) + ' kW</span>';
          html += '</div></div>';
          html += '<div class="recipeOutput">';
          html += '<span class="recipeOutputLabel">\u2192 ' + esc(outName) + ' \u00d7' + (recipe.output_qty || 1) + '</span>';
          html += '</div>';
          html += '<div class="recipeAction">';
          if (canStart && idleEquip.length) {
            html += '<button class="btnSmall btnStartRecipe" data-recipe-id="' + recipe.recipe_id + '" data-equip-id="' + idleEquip[0].id + '">Build</button>';
          } else {
            html += '<span class="muted">' + (!idleEquip.length ? "No idle constructor" : "Missing inputs") + '</span>';
          }
          html += '</div></div>';
        }

        html += '</div></div>';
      }

      list.innerHTML = html;

      // Wire collapsible headers
      list.querySelectorAll(".collapsibleHeader").forEach(function(header) {
        header.addEventListener("click", function() {
          var collapsed = header.dataset.collapsed === "true";
          header.dataset.collapsed = collapsed ? "false" : "true";
          header.querySelector(".collapseToggle").textContent = collapsed ? "\u25BE" : "\u25B8";
          var body = header.nextElementSibling;
          body.style.display = collapsed ? "" : "none";
          // Persist state
          var cid = header.dataset.catId;
          if (collapsed) collapsedConstructCats.delete(cid);
          else collapsedConstructCats.add(cid);
        });
      });

      // Wire start buttons
      list.querySelectorAll(".btnStartRecipe").forEach(function(btn) {
        btn.addEventListener("click", async function() {
          try {
            await postJSON("/api/industry/jobs/start", {
              equipment_id: btn.dataset.equipId,
              recipe_id: btn.dataset.recipeId,
            });
            loadIndustryContent();
            loadSites();
          } catch (e) {
            alert("Failed: " + e.message);
          }
        });
      });
    } catch (err) {
      list.innerHTML = '<div style="padding:12px;color:#ff6b6b">Construction recipes error: ' + String(err.message || err) + '</div>';
      console.error("renderConstructRecipes error:", err);
    }
  }

  function renderRecipeList(list, recipes, equipType) {
    // Group by refinery_category
    const groups = {};
    recipes.forEach(r => {
      const cat = r.refinery_category || "unassigned";
      (groups[cat] = groups[cat] || []).push(r);
    });

    const categoryLabels = {
      lithic_processing: "Lithic Processing",
      metallurgy: "Metallurgy",
      volatiles_cryogenics: "Volatiles & Cryogenics",
      nuclear_exotic: "Nuclear & Exotic",
      unassigned: "Other",
    };

    let html = "";
    for (const [catId, catRecipes] of Object.entries(groups)) {
      html += `<div class="recipeCategoryGroup">
        <div class="recipeCategoryHeader">${esc(categoryLabels[catId] || catId)}</div>`;

      catRecipes.forEach(recipe => {
        const inputsHtml = (recipe.inputs_status || []).map(inp => {
          const ok = inp.sufficient;
          return `<span class="recipeInput ${ok ? "sufficient" : "insufficient"}">
            ${esc(inp.name)}: ${inp.qty_available.toFixed(2)}/${inp.qty_needed.toFixed(2)}
          </span>`;
        }).join("");

        const canStart = recipe.can_start;
        const idleEquip = equipType === "constructor"
          ? (recipe.idle_constructors || [])
          : (recipe.idle_refineries || []);
        const noEquipMsg = equipType === "constructor" ? "No idle constructor" : "No idle refinery";

        html += `<div class="recipeRow ${canStart ? "canStart" : "cantStart"}">
          <div class="recipeInfo">
            <div class="recipeName">${esc(recipe.name)}</div>
            <div class="recipeInputs">${inputsHtml}</div>
            <div class="recipeMeta">
              <span>Time: ${fmtDuration(recipe.build_time_s)}</span>
              <span>Tier ${recipe.min_tech_tier}</span>
              <span>${recipe.power_kw} kW</span>
            </div>
          </div>
          <div class="recipeOutput">
            <span class="recipeOutputLabel">→ ${esc(recipe.output_item_id?.replace(/_/g, " ") || "?")} ×${recipe.output_qty}</span>
          </div>
          <div class="recipeAction">
            ${canStart && idleEquip.length ?
              `<button class="btnSmall btnStartRecipe" data-recipe-id="${recipe.recipe_id}"
                data-equip-id="${idleEquip[0].id}">${equipType === "constructor" ? "Build" : "Produce"}</button>` :
              `<span class="muted">${!idleEquip.length ? noEquipMsg : "Missing inputs"}</span>`
            }
          </div>
        </div>`;
      });

      html += `</div>`;
    }

    list.innerHTML = html;

    list.querySelectorAll(".btnStartRecipe").forEach(btn => {
      btn.addEventListener("click", async () => {
        try {
          await postJSON("/api/industry/jobs/start", {
            equipment_id: btn.dataset.equipId,
            recipe_id: btn.dataset.recipeId,
          });
          loadIndustryContent();
          loadSites();
        } catch (e) {
          alert("Failed: " + e.message);
        }
      });
    });
  }

  /* ── Mining section ──────────────────────────────────────── */

  function renderMiningSection() {
    const panel = document.getElementById("industryMiningPanel");
    const content = document.getElementById("industryMiningContent");

    if (!industryData.is_surface_site) {
      panel.style.display = "none";
      return;
    }
    panel.style.display = "";

    const minable = industryData.minable_resources || [];
    const activeJobs = (industryData.active_jobs || []).filter(j => j.job_type === "mine");
    const idleConstructors = industryData.idle_constructors || [];

    let html = "";

    // Active mining
    if (activeJobs.length) {
      html += '<div class="miningSectionTitle">Active Mining</div>';
      activeJobs.forEach(job => {
        html += `<div class="miningActiveRow">
          <span class="miningIcon">⛏</span>
          <span class="miningResource">${esc(job.resource_name)}</span>
          <span class="miningRate">${job.rate_kg_per_hr} kg/hr</span>
          <span class="miningTotal">${fmtKg(job.total_mined_kg)} mined</span>
        </div>`;
      });
    }

    // Available resources
    if (minable.length) {
      html += '<div class="miningSectionTitle">Available Deposits</div>';
      minable.forEach(r => {
        html += `<div class="miningDepositRow">
          <span class="depositName">${esc(r.name)}</span>
          <div class="depositBar"><div class="depositBarFill" style="width:${Math.min(100, r.mass_fraction_pct * 2)}%"></div></div>
          <span class="depositPct">${r.mass_fraction_pct}%</span>
        </div>`;
      });
    }

    if (!html) html = '<div class="muted" style="padding:12px">No mining data available</div>';
    content.innerHTML = html;
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

    const grid = document.getElementById("deployableItemsGrid");
    const empty = document.getElementById("deployableEmpty");
    grid.innerHTML = "";

    try {
      const inv = await fetchJSON(`/api/inventory/location/${encodeURIComponent(industryLocationId)}`);
      const parts = (inv.parts || []).filter(p => {
        const cat = (p.part || {}).category_id || (p.part || {}).type || "";
        return cat === "refinery" || cat === "constructor";
      });

      if (!parts.length) {
        empty.style.display = "";
        return;
      }
      empty.style.display = "none";

      parts.forEach(p => {
        const part = p.part || {};
        const cat = part.category_id || part.type || "generic";
        const cell = itemDisplay.createGridCell({
          label: p.name, iconSeed: p.item_id, category: cat,
          quantity: p.quantity, subtitle: cat,
        });
        cell.style.cursor = "pointer";
        cell.addEventListener("click", async () => {
          try {
            await postJSON("/api/industry/deploy", {
              location_id: industryLocationId,
              item_id: p.item_id,
            });
            document.getElementById("deployModal").style.display = "none";
            loadIndustryContent();
            loadSites();
          } catch (e) {
            alert("Deploy failed: " + e.message);
          }
        });
        grid.appendChild(cell);
      });
    } catch (e) {
      grid.innerHTML = `<div class="muted">Failed to load inventory: ${esc(e.message)}</div>`;
    }
  }

  /* Start job modal */
  function initStartJobModal() {
    document.getElementById("startJobModalClose").addEventListener("click", () => {
      document.getElementById("startJobModal").style.display = "none";
    });
    document.querySelector("#startJobModal .modalOverlay").addEventListener("click", () => {
      document.getElementById("startJobModal").style.display = "none";
    });
  }

  function openStartJobModal(equipId) {
    const modal = document.getElementById("startJobModal");
    const content = document.getElementById("startJobContent");
    modal.style.display = "";

    const equip = (industryData.equipment || []).find(e => e.id === equipId);
    if (!equip) { content.innerHTML = '<div class="muted">Equipment not found</div>'; return; }

    const cfg = equip.config || {};
    const spec = cfg.specialization || "";
    const maxTier = cfg.max_recipe_tier || 1;
    const recipes = (industryData.available_recipes || []).filter(r => {
      if (r.facility_type === "shipyard") return false; // Only refinery recipes
      const matchSpec = !spec || r.refinery_category === spec;
      const matchTier = (r.min_tech_tier || 0) <= maxTier;
      return matchSpec && matchTier;
    });

    if (!recipes.length) {
      content.innerHTML = `<div class="muted">No compatible recipes for ${esc(equip.name)}</div>`;
      return;
    }

    content.innerHTML = `<div class="startJobEquipName">${esc(equip.name)} (${esc(spec)})</div>
      <div class="startJobRecipes">${recipes.map(r => {
        const inputStr = (r.inputs_status || []).map(i =>
          `<span class="${i.sufficient ? "sufficient" : "insufficient"}">${esc(i.name)}: ${i.qty_available.toFixed(2)}/${i.qty_needed.toFixed(2)}</span>`
        ).join(", ");
        return `<div class="startJobRecipeRow ${r.can_start ? "canStart" : "cantStart"}">
          <div class="recipeName">${esc(r.name)}</div>
          <div class="recipeInputs">${inputStr}</div>
          <div class="recipeMeta">→ ${esc(r.output_item_id?.replace(/_/g," "))} ×${r.output_qty} · ${fmtDuration(r.build_time_s)}</div>
          ${r.can_start ? `<button class="btnSmall btnConfirmJob" data-recipe-id="${r.recipe_id}" data-equip-id="${equipId}">Start</button>` : '<span class="muted">Missing inputs</span>'}
        </div>`;
      }).join("")}</div>`;

    content.querySelectorAll(".btnConfirmJob").forEach(btn => {
      btn.addEventListener("click", async () => {
        try {
          await postJSON("/api/industry/jobs/start", {
            equipment_id: btn.dataset.equipId,
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

  /* Start build modal (constructor → shipyard recipes) */
  function openStartBuildModal(equipId) {
    const modal = document.getElementById("startJobModal");
    const content = document.getElementById("startJobContent");
    modal.style.display = "";

    const equip = (industryData.equipment || []).find(e => e.id === equipId);
    if (!equip) { content.innerHTML = '<div class="muted">Equipment not found</div>'; return; }

    // Constructors can run any shipyard recipe
    const recipes = (industryData.available_recipes || []).filter(r => {
      return r.facility_type === "shipyard";
    });

    if (!recipes.length) {
      content.innerHTML = `<div class="muted">No construction recipes available for ${esc(equip.name)}</div>`;
      return;
    }

    content.innerHTML = `<div class="startJobEquipName">${esc(equip.name)} (Constructor)</div>
      <div class="startJobRecipes">${recipes.map(r => {
        const inputStr = (r.inputs_status || []).map(i =>
          `<span class="${i.sufficient ? "sufficient" : "insufficient"}">${esc(i.name)}: ${i.qty_available.toFixed(2)}/${i.qty_needed.toFixed(2)}</span>`
        ).join(", ");
        return `<div class="startJobRecipeRow ${r.can_start ? "canStart" : "cantStart"}">
          <div class="recipeName">${esc(r.name)}</div>
          <div class="recipeInputs">${inputStr}</div>
          <div class="recipeMeta">→ ${esc(r.output_item_id?.replace(/_/g," "))} ×${r.output_qty} · ${fmtDuration(r.build_time_s)}</div>
          ${r.can_start ? `<button class="btnSmall btnConfirmJob" data-recipe-id="${r.recipe_id}" data-equip-id="${equipId}">Build</button>` : '<span class="muted">Missing inputs</span>'}
        </div>`;
      }).join("")}</div>`;

    content.querySelectorAll(".btnConfirmJob").forEach(btn => {
      btn.addEventListener("click", async () => {
        try {
          await postJSON("/api/industry/jobs/start", {
            equipment_id: btn.dataset.equipId,
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

  /* Mining modal */
  function initMiningModal() {
    document.getElementById("miningModalClose").addEventListener("click", () => {
      document.getElementById("miningModal").style.display = "none";
    });
    document.querySelector("#miningModal .modalOverlay").addEventListener("click", () => {
      document.getElementById("miningModal").style.display = "none";
    });
  }

  function openMiningModal(equipId) {
    const modal = document.getElementById("miningModal");
    const content = document.getElementById("miningModalContent");
    modal.style.display = "";

    const minable = industryData.minable_resources || [];
    if (!minable.length) {
      content.innerHTML = '<div class="muted">No minable resources at this site</div>';
      return;
    }

    content.innerHTML = `<div class="miningSelectList">${minable.map(r => `
      <div class="miningSelectRow">
        <span class="miningSelectName">${esc(r.name)}</span>
        <span class="miningSelectPct">${r.mass_fraction_pct}%</span>
        <button class="btnSmall btnConfirmMine" data-equip-id="${equipId}" data-resource-id="${r.resource_id}">Mine</button>
      </div>
    `).join("")}</div>`;

    content.querySelectorAll(".btnConfirmMine").forEach(btn => {
      btn.addEventListener("click", async () => {
        try {
          await postJSON("/api/industry/mining/start", {
            equipment_id: btn.dataset.equipId,
            resource_id: btn.dataset.resourceId,
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

  async function stopMining(jobId) {
    try {
      await postJSON("/api/industry/mining/stop", { job_id: jobId });
      loadIndustryContent();
      loadSites();
    } catch (e) {
      alert("Stop failed: " + e.message);
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
        renderRefineJobs();
        renderConstructJobs();
      }
    }, 1000);
  }

  async function init() {
    await syncClock();
    initTabSwitching();
    initFilters();
    initIndustryLocationSelect();
    initDeployModal();
    initStartJobModal();
    initMiningModal();
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
