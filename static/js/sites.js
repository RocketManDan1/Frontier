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
  function fmtKg(v) { return itemDisplay ? itemDisplay.fmtKg(v) : `${Number(v||0).toFixed(0)} kg`; }
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
        if (currentTab === "industrial") loadIndustryContent();
      });
    });
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

    tbody.innerHTML = filtered.map(s => {
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
    }).join("");

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
    renderIndustryEquipment();
    renderIndustryJobs();
    renderProductionChain();
    renderAvailableRecipes();
    renderMiningSection();
    renderJobHistory();
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
    list.querySelectorAll(".btnMine").forEach(btn => {
      btn.addEventListener("click", () => openMiningModal(btn.dataset.equipId));
    });
    list.querySelectorAll(".btnUndeploy").forEach(btn => {
      btn.addEventListener("click", () => undeployEquipment(btn.dataset.equipId));
    });
  }

  /* ── Active jobs panel ───────────────────────────────────── */

  function renderIndustryJobs() {
    const list = document.getElementById("industryJobsList");
    const jobs = industryData.active_jobs || [];

    if (!jobs.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No active jobs</div>';
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

      // Refine job
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

    // Determine which item_ids are raw vs refined using recipe outputs
    const refinedIds = new Set();
    const rawInputIds = new Set();
    const constructableIds = new Set();

    const allRecipes = recipes;
    allRecipes.forEach(recipe => {
      const outId = recipe.output_item_id;
      if (recipe.facility_type === "shipyard") {
        constructableIds.add(outId);
      } else {
        refinedIds.add(outId);
      }
      (recipe.inputs || []).forEach(inp => {
        if (!refinedIds.has(inp.item_id)) {
          rawInputIds.add(inp.item_id);
        }
      });
    });

    // Raw materials: in stock + needed as inputs
    const rawItems = [];
    const seenRaw = new Set();
    resources.forEach(r => {
      const rid = r.item_id || r.resource_id;
      if (!refinedIds.has(rid) || rawInputIds.has(rid)) {
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

  /* ── Available recipes ───────────────────────────────────── */

  function renderAvailableRecipes() {
    const list = document.getElementById("industryRecipesList");
    const recipes = industryData.available_recipes || [];

    if (!recipes.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No recipes available. Deploy refineries to unlock recipes.</div>';
      return;
    }

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
        const idleRefs = recipe.idle_refineries || [];

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
            ${canStart && idleRefs.length ?
              `<button class="btnSmall btnStartRecipe" data-recipe-id="${recipe.recipe_id}"
                data-equip-id="${idleRefs[0].id}">Produce</button>` :
              `<span class="muted">${!idleRefs.length ? "No idle refinery" : "Missing inputs"}</span>`
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
    const history = industryData.job_history || [];

    if (!history.length) {
      list.innerHTML = '<div class="muted" style="padding:12px">No job history</div>';
      return;
    }

    list.innerHTML = `<table class="historyTable">
      <thead><tr><th>Type</th><th>Recipe/Resource</th><th>Equipment</th><th>Status</th></tr></thead>
      <tbody>${history.map(h => `
        <tr>
          <td>${esc(h.job_type)}</td>
          <td>${esc(h.recipe_name || h.resource_name || "—")}</td>
          <td>${esc(h.equipment_name)}</td>
          <td><span class="badge badge${h.status === "completed" ? "Ok" : "Warn"}">${esc(h.status)}</span></td>
        </tr>
      `).join("")}</tbody>
    </table>`;
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
  }

  /* ══════════════════════════════════════════════════════════
     POLLING & INIT
     ══════════════════════════════════════════════════════════ */

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => {
      loadSites();
      if (currentTab === "industrial" && industryLocationId) loadIndustryContent();
      if (selectedSiteId) selectSite(selectedSiteId);
    }, 10000);

    // Also refresh job progress bars every second
    setInterval(() => {
      if (currentTab === "industrial" && industryData) renderIndustryJobs();
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
    await loadSites();
    startPolling();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
