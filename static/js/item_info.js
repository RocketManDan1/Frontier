/**
 * Item Info Modal — Eve Online-inspired item information window.
 *
 * Shows a compact modal with 3 tabs:
 *   Description — item description text
 *   Attributes  — all stats / properties from item data
 *   Recipe      — recipes that produce or consume this item
 *
 * All item references inside the modal are hyperlinked so clicking
 * them opens the info page for that referenced item.
 *
 * Usage:
 *   ItemInfo.open("nerva2_aegis")   — fetch + show info for item ID
 *   ItemInfo.close()                — dismiss the modal
 *
 * Depends on: ItemDisplay (for icon generation + helpers)
 */
window.ItemInfo = (function () {
  "use strict";

  let modalEl = null;
  let currentItemId = null;
  let cache = new Map();

  /* ── Helpers ───────────────────────────────────────────── */

  const esc = (v) => (window.ItemDisplay ? window.ItemDisplay.escapeHtml(v) : String(v ?? ""));
  const fmtKg = (v) => (window.ItemDisplay ? window.ItemDisplay.fmtKg(v) : `${v} kg`);
  const fmtM3 = (v) => (window.ItemDisplay ? window.ItemDisplay.fmtM3(v) : `${v} m³`);

  function fmtNum(n, unit) {
    if (n == null || n === 0) return "";
    return Number(n).toLocaleString() + (unit ? " " + unit : "");
  }

  function fmtTime(seconds) {
    if (!seconds || seconds <= 0) return "—";
    if (seconds < 60) return seconds + "s";
    if (seconds < 3600) return (seconds / 60).toFixed(1) + " min";
    return (seconds / 3600).toFixed(1) + " hr";
  }

  function titleCase(s) {
    return String(s || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  /* ── Modal DOM ─────────────────────────────────────────── */

  function ensureModal() {
    if (modalEl) return modalEl;
    const overlay = document.createElement("div");
    overlay.className = "iiOverlay";
    overlay.style.display = "none";
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) close();
    });

    const modal = document.createElement("div");
    modal.className = "iiModal";

    // Header
    const header = document.createElement("div");
    header.className = "iiHeader";

    const headerIcon = document.createElement("img");
    headerIcon.className = "iiHeaderIcon";
    headerIcon.alt = "";
    header.appendChild(headerIcon);

    const headerInfo = document.createElement("div");
    headerInfo.className = "iiHeaderInfo";
    const headerTitle = document.createElement("div");
    headerTitle.className = "iiHeaderTitle";
    const headerSub = document.createElement("div");
    headerSub.className = "iiHeaderSub";
    headerInfo.appendChild(headerTitle);
    headerInfo.appendChild(headerSub);
    header.appendChild(headerInfo);

    const closeBtn = document.createElement("button");
    closeBtn.className = "iiCloseBtn";
    closeBtn.type = "button";
    closeBtn.innerHTML = "&times;";
    closeBtn.title = "Close";
    closeBtn.addEventListener("click", close);
    header.appendChild(closeBtn);

    modal.appendChild(header);

    // Tabs
    const tabBar = document.createElement("div");
    tabBar.className = "iiTabBar";
    const tabs = ["Description", "Attributes", "Recipe"];
    tabs.forEach((name, idx) => {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "iiTab" + (idx === 0 ? " active" : "");
      tab.textContent = name;
      tab.dataset.tab = name.toLowerCase();
      tab.addEventListener("click", () => switchTab(name.toLowerCase()));
      tabBar.appendChild(tab);
    });
    modal.appendChild(tabBar);

    // Body (tab panels)
    const body = document.createElement("div");
    body.className = "iiBody";
    const descPanel = document.createElement("div");
    descPanel.className = "iiPanel iiPanelDesc active";
    descPanel.dataset.panel = "description";
    const attrPanel = document.createElement("div");
    attrPanel.className = "iiPanel iiPanelAttr";
    attrPanel.dataset.panel = "attributes";
    const recipePanel = document.createElement("div");
    recipePanel.className = "iiPanel iiPanelRecipe";
    recipePanel.dataset.panel = "recipe";
    body.appendChild(descPanel);
    body.appendChild(attrPanel);
    body.appendChild(recipePanel);
    modal.appendChild(body);

    overlay.appendChild(modal);
    document.body.appendChild(overlay);
    modalEl = overlay;

    // Close on Escape
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && modalEl && modalEl.style.display !== "none") {
        close();
      }
    });

    return overlay;
  }

  function switchTab(tabName) {
    if (!modalEl) return;
    modalEl.querySelectorAll(".iiTab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === tabName);
    });
    modalEl.querySelectorAll(".iiPanel").forEach((p) => {
      p.classList.toggle("active", p.dataset.panel === tabName);
    });
  }

  /* ── Open / Close ──────────────────────────────────────── */

  async function open(itemId) {
    if (!itemId) return;
    const overlay = ensureModal();
    currentItemId = itemId;
    overlay.style.display = "flex";

    // Show loading state
    const descPanel = overlay.querySelector(".iiPanelDesc");
    const attrPanel = overlay.querySelector(".iiPanelAttr");
    const recipePanel = overlay.querySelector(".iiPanelRecipe");
    descPanel.innerHTML = '<div class="iiLoading">Loading…</div>';
    attrPanel.innerHTML = "";
    recipePanel.innerHTML = "";
    switchTab("description");

    // Set temp header
    const title = overlay.querySelector(".iiHeaderTitle");
    const sub = overlay.querySelector(".iiHeaderSub");
    const icon = overlay.querySelector(".iiHeaderIcon");
    title.textContent = titleCase(itemId);
    sub.textContent = "";
    icon.src = window.ItemDisplay
      ? window.ItemDisplay.iconDataUri(itemId, titleCase(itemId), "")
      : "";

    // Fetch item data
    let data;
    if (cache.has(itemId)) {
      data = cache.get(itemId);
    } else {
      try {
        const resp = await fetch(`/api/catalog/item/${encodeURIComponent(itemId)}`);
        if (!resp.ok) {
          descPanel.innerHTML = `<div class="iiEmpty">Item not found.</div>`;
          return;
        }
        const json = await resp.json();
        data = json.item;
        cache.set(itemId, data);
      } catch (err) {
        descPanel.innerHTML = `<div class="iiEmpty">Failed to load item info.</div>`;
        return;
      }
    }

    // Double-check we're still showing this item (user might have clicked another)
    if (currentItemId !== itemId) return;

    renderItem(data);
  }

  function close() {
    if (!modalEl) return;
    modalEl.style.display = "none";
    currentItemId = null;
  }

  /* ── Render ────────────────────────────────────────────── */

  function renderItem(item) {
    const overlay = modalEl;
    const title = overlay.querySelector(".iiHeaderTitle");
    const sub = overlay.querySelector(".iiHeaderSub");
    const icon = overlay.querySelector(".iiHeaderIcon");

    const itemName = item.name || titleCase(item.item_id || item.id || "");
    const category = item.category || item.category_id || item.type || "";

    title.textContent = itemName;
    sub.textContent = titleCase(category);
    icon.src = window.ItemDisplay
      ? window.ItemDisplay.iconDataUri(item.item_id || item.id, itemName, category)
      : "";

    renderDescription(item);
    renderAttributes(item);
    renderRecipes(item);
  }

  function renderDescription(item) {
    const panel = modalEl.querySelector(".iiPanelDesc");
    const desc = item.description || "";
    const tags = item.ui_tags || [];

    let html = "";
    if (desc) {
      html += `<div class="iiDescText">${esc(desc)}</div>`;
    } else {
      html += `<div class="iiEmpty">No description available.</div>`;
    }

    if (tags.length) {
      html += `<div class="iiTagRow">`;
      tags.forEach((tag) => {
        html += `<span class="iiTag">${esc(titleCase(tag))}</span>`;
      });
      html += `</div>`;
    }
    panel.innerHTML = html;
  }

  function renderAttributes(item) {
    const panel = modalEl.querySelector(".iiPanelAttr");
    const category = (item.category || item.category_id || item.type || "").toLowerCase();

    // Build attribute rows from known fields
    const rows = [];

    // Common fields
    if (item.name) rows.push(["Name", item.name]);
    if (item.item_id || item.id) rows.push(["Item ID", item.item_id || item.id]);
    if (category) rows.push(["Category", titleCase(category)]);

    // Mass
    const massKg = Number(item.mass_kg) || 0;
    if (massKg > 0) rows.push(["Mass", fmtKg(massKg)]);

    // Volume
    const volM3 = Number(item.volume_m3) || 0;
    if (volM3 > 0) rows.push(["Volume", fmtM3(volM3)]);

    // Phase
    if (item.phase) rows.push(["Phase", titleCase(item.phase)]);

    // Resource-specific
    const massPerM3 = Number(item.mass_per_m3_kg) || 0;
    if (massPerM3 > 0) rows.push(["Density", fmtNum(massPerM3, "kg/m³")]);
    const pricePerKg = Number(item.price_per_kg) || 0;
    if (pricePerKg > 0) rows.push(["Price", fmtNum(pricePerKg, "¢/kg")]);

    // Thruster-specific
    const thrustKn = Number(item.thrust_kn) || 0;
    if (thrustKn > 0) rows.push(["Thrust", fmtNum(thrustKn, "kN")]);
    const ispS = Number(item.isp_s) || 0;
    if (ispS > 0) rows.push(["Specific Impulse", fmtNum(ispS, "s")]);
    const thermalMw = Number(item.thermal_mw) || 0;
    if (thermalMw > 0) rows.push(["Thermal Power", fmtNum(thermalMw, "MW")]);
    if (item.reaction_mass) rows.push(["Reaction Mass", titleCase(item.reaction_mass)]);
    if (item.thruster_family) rows.push(["Family", titleCase(item.thruster_family)]);

    // Reactor-specific (thermal_mw already handled above)

    // Generator-specific
    const thermalInput = Number(item.thermal_mw_input) || 0;
    if (thermalInput > 0) rows.push(["Thermal Input", fmtNum(thermalInput, "MW")]);
    const electricMw = Number(item.electric_mw) || 0;
    if (electricMw > 0) rows.push(["Electric Output", fmtNum(electricMw, "MW")]);
    const convEff = Number(item.conversion_efficiency) || 0;
    if (convEff > 0) rows.push(["Efficiency", (convEff * 100).toFixed(1) + "%"]);
    const wasteHeat = Number(item.waste_heat_mw) || 0;
    if (wasteHeat > 0) rows.push(["Waste Heat", fmtNum(wasteHeat, "MW")]);

    // Radiator-specific
    const heatReject = Number(item.heat_rejection_mw) || 0;
    if (heatReject > 0) rows.push(["Heat Rejection", fmtNum(heatReject, "MW")]);
    const opTemp = Number(item.operating_temp_k) || 0;
    if (opTemp > 0) rows.push(["Operating Temp", fmtNum(opTemp, "K")]);

    // Storage-specific
    const capM3 = Number(item.capacity_m3) || 0;
    if (capM3 > 0) rows.push(["Capacity", fmtNum(capM3, "m³")]);
    if (item.resource_id) rows.push(["Stores", itemLink(item.resource_id, titleCase(item.resource_id))]);

    // Robonaut-specific
    const prospectRange = Number(item.prospect_range_km) || 0;
    if (prospectRange > 0) rows.push(["Prospect Range", fmtNum(prospectRange, "km")]);
    const scanRate = Number(item.scan_rate_km2_per_hr) || 0;
    if (scanRate > 0) rows.push(["Scan Rate", fmtNum(scanRate, "km²/hr")]);
    const meltRate = Number(item.melt_rate_t_per_hr) || 0;
    if (meltRate > 0) rows.push(["Melt Rate", fmtNum(meltRate, "t/hr")]);
    const miningRate = Number(item.mining_rate_kg_per_hr) || 0;
    if (miningRate > 0) rows.push(["Mining Rate", fmtNum(miningRate, "kg/hr")]);
    if (item.emission_type) rows.push(["Emission Type", titleCase(item.emission_type)]);

    // Constructor-specific
    const constructRate = Number(item.construction_rate_kg_per_hr) || 0;
    if (constructRate > 0) rows.push(["Construction Rate", fmtNum(constructRate, "kg/hr")]);
    if (item.excavation_type) rows.push(["Excavation Type", titleCase(item.excavation_type)]);
    if (item.operational_environment) rows.push(["Environment", titleCase(item.operational_environment)]);
    const minGrav = Number(item.min_surface_gravity_ms2) || 0;
    if (minGrav > 0) rows.push(["Min Gravity", fmtNum(minGrav, "m/s²")]);

    // Refinery-specific
    const throughput = Number(item.throughput_mult) || 0;
    if (throughput > 0 && throughput !== 1) rows.push(["Throughput", throughput.toFixed(2) + "×"]);
    const efficiency = Number(item.efficiency) || 0;
    if (efficiency > 0 && efficiency !== 1) rows.push(["Efficiency", (efficiency * 100).toFixed(1) + "%"]);
    const maxTier = Number(item.max_recipe_tier) || 0;
    if (maxTier > 0) rows.push(["Max Recipe Tier", String(maxTier)]);
    const maxConcurrent = Number(item.max_concurrent_recipes) || 0;
    if (maxConcurrent > 0) rows.push(["Concurrent Recipes", String(maxConcurrent)]);
    if (item.specialization) rows.push(["Specialization", titleCase(item.specialization)]);

    // Tech level / branch
    if (item.branch) rows.push(["Branch", titleCase(item.branch)]);
    const techLevel = Number(item.tech_level) || 0;
    if (techLevel > 0) rows.push(["Tech Level", String(techLevel)]);
    if (item.research_node) rows.push(["Research Node", titleCase(item.research_node)]);

    let html = '<div class="iiAttrTable">';
    for (const [label, value] of rows) {
      const isHtml = typeof value === "string" && value.includes("<");
      html += `<div class="iiAttrRow">
        <span class="iiAttrLabel">${esc(label)}</span>
        <span class="iiAttrValue">${isHtml ? value : esc(value)}</span>
      </div>`;
    }
    html += "</div>";

    if (rows.length === 0) {
      html = '<div class="iiEmpty">No attributes available.</div>';
    }

    panel.innerHTML = html;
  }

  function renderRecipes(item) {
    const panel = modalEl.querySelector(".iiPanelRecipe");
    const itemId = item.item_id || item.id;
    const recipes = (item.related_recipes || []).slice().sort((a, b) => {
      const aProduces = (a.output_item_id || "") === itemId ? 0 : 1;
      const bProduces = (b.output_item_id || "") === itemId ? 0 : 1;
      return aProduces - bProduces;
    });

    if (!recipes.length) {
      panel.innerHTML = '<div class="iiEmpty">No recipes associated with this item.</div>';
      return;
    }

    let html = "";
    for (const recipe of recipes) {
      const outputId = recipe.output_item_id || "";
      const outputName = recipe.output_item_name || titleCase(outputId);
      const outputQty = Number(recipe.output_qty) || 1;
      const isProducer = outputId === itemId;

      html += `<div class="iiRecipeCard">`;
      html += `<div class="iiRecipeHeader">`;
      html += `<span class="iiRecipeName">${esc(recipe.name || "Recipe")}</span>`;
      html += `<span class="iiRecipeBadge ${isProducer ? "iiProduces" : "iiConsumes"}">${isProducer ? "Produces" : "Consumes"}</span>`;
      html += `</div>`;

      // Output
      html += `<div class="iiRecipeSection">`;
      html += `<div class="iiRecipeSectionLabel">Output</div>`;
      html += `<div class="iiRecipeItem">${itemLink(outputId, outputName)} × ${fmtNum(outputQty, "")}</div>`;
      html += `</div>`;

      // Inputs
      const inputs = recipe.inputs || [];
      if (inputs.length) {
        html += `<div class="iiRecipeSection">`;
        html += `<div class="iiRecipeSectionLabel">Inputs</div>`;
        for (const inp of inputs) {
          const inpId = inp.item_id || "";
          const inpName = inp.name || titleCase(inpId);
          const inpQty = Number(inp.qty) || 0;
          html += `<div class="iiRecipeItem">${itemLink(inpId, inpName)} × ${fmtNum(inpQty, "kg")}</div>`;
        }
        html += `</div>`;
      }

      // Byproducts
      const byproducts = recipe.byproducts || [];
      if (byproducts.length) {
        html += `<div class="iiRecipeSection">`;
        html += `<div class="iiRecipeSectionLabel">Byproducts</div>`;
        for (const bp of byproducts) {
          const bpId = bp.item_id || "";
          const bpName = bp.name || titleCase(bpId);
          const bpQty = Number(bp.qty) || 0;
          html += `<div class="iiRecipeItem">${itemLink(bpId, bpName)} × ${fmtNum(bpQty, "kg")}</div>`;
        }
        html += `</div>`;
      }

      // Meta
      const buildTime = Number(recipe.build_time_s) || 0;
      const powerKw = Number(recipe.power_kw) || 0;
      const minTier = Number(recipe.min_tech_tier) || 0;
      const facility = recipe.facility_type || "";
      const refCat = recipe.refinery_category || "";

      const metaParts = [];
      if (buildTime > 0) metaParts.push(`<span>Build: ${fmtTime(buildTime)}</span>`);
      if (powerKw > 0) metaParts.push(`<span>Power: ${fmtNum(powerKw, "kW")}</span>`);
      if (minTier > 0) metaParts.push(`<span>Min Tier: ${minTier}</span>`);
      if (facility) metaParts.push(`<span>Facility: ${esc(titleCase(facility))}</span>`);
      if (refCat && refCat !== "unassigned") metaParts.push(`<span>Category: ${esc(titleCase(refCat))}</span>`);

      if (metaParts.length) {
        html += `<div class="iiRecipeMeta">${metaParts.join('<span class="iiMetaSep">·</span>')}</div>`;
      }

      html += `</div>`; // .iiRecipeCard
    }

    panel.innerHTML = html;

    // Wire up all item links
    panel.querySelectorAll(".iiItemLink").forEach((link) => {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        const id = link.dataset.itemId;
        if (id) open(id);
      });
    });
  }

  function itemLink(itemId, displayName) {
    if (!itemId) return esc(displayName || "—");
    return `<a class="iiItemLink" href="#" data-item-id="${esc(itemId)}" title="View ${esc(displayName)}">${esc(displayName)}</a>`;
  }

  /* ── Public API ────────────────────────────────────────── */

  return {
    open,
    close,
  };
})();
