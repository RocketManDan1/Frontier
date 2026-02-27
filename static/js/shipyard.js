/**
 * Shipyard — 3-Mode Slot-Based Ship Designer
 *
 * Modes:
 *   "boost" — Build to Boost: purchase parts from Earth catalog, boost to LEO.
 *             No inventory needed. Costs $100M + $5K/kg. Parts limited to TL 1-2.5.
 *   "site"  — Build from Site: consume parts from a location's existing inventory.
 *   "edit"  — Edit Ship: select a docked ship, add/remove parts from local inventory.
 *             Under the hood: deconstruct old ship + rebuild new.
 *
 * Layout: 3-column
 *   Left:   Ship Blueprint — vertical category sections, each with filled slots + empty "add" slot
 *   Center: Parts Picker — filtered to the active category
 *   Right:  Stats — delta-v, power balance, mass budget, boost cost (boost mode)
 */
(function () {
  /* ── Screen refs ──────────────────────────────────────── */
  const modeSelectEl = document.getElementById("shipyardModeSelect");
  const shipSelectEl = document.getElementById("shipyardShipSelect");
  const shipListEl = document.getElementById("shipyardShipList");
  const backToModesBtn = document.getElementById("shipyardBackToModes");
  const designerEl = document.getElementById("shipyardDesigner");

  /* ── Designer DOM refs ────────────────────────────────── */
  const blueprintEl = document.getElementById("shipyardBlueprint");
  const pickerEl = document.getElementById("shipyardPicker");
  const pickerLabelEl = document.getElementById("pickerCategoryLabel");
  const statsEl = document.getElementById("shipyardStats");
  const msgEl = document.getElementById("shipyardMsg");
  const buildBtn = document.getElementById("shipyardBuildBtn");
  const shipNameEl = document.getElementById("shipyardName");
  const sourceLocationEl = document.getElementById("shipyardSourceLocation");
  const sourceHintEl = document.getElementById("shipyardSourceHint");
  const sourceFieldEl = document.getElementById("shipyardSourceField");
  const modeBackBtn = document.getElementById("shipyardModeBack");
  const boostCostEl = document.getElementById("shipyardBoostCost");
  const itemDisplay = window.ItemDisplay || null;

  // Fuel loading elements
  const fuelSectionEl = document.getElementById("shipyardFuelSection");
  const fuelSliderEl = document.getElementById("shipyardFuelSlider");
  const fuelInputEl = document.getElementById("shipyardFuelInput");
  const fuelAvailEl = document.getElementById("shipyardFuelAvail");
  const fuelBarFillEl = document.getElementById("shipyardFuelBarFill");
  const fuelLabelEl = document.getElementById("shipyardFuelLabel");
  const fuelFillBtn = document.getElementById("shipyardFuelFillBtn");
  const fuelEmptyBtn = document.getElementById("shipyardFuelEmptyBtn");
  const pickerSectionEl = fuelSectionEl ? fuelSectionEl.closest(".shipyardPicker") : document.querySelector(".shipyardPicker");

  const SHIPYARD_DRAG_MIME = "application/x-earthmoon-shipyard-item";

  /* ── State ────────────────────────────────────────────── */
  let currentMode = "";             // "boost" | "site" | "edit"
  let buildLocationId = "";
  let catalogParts = [];
  let garageParts = [];             // inventory parts at current source (site/edit) or boostable items (boost)
  let selectedItemIds = [];         // flat array of selected item_id (order preserved for API)
  let buildSourceLocations = [];
  let sourceRefreshTimer = null;
  let lastCatalogHash = "";

  // Slot UI state
  let activeCategory = "";          // which section is currently selected for the picker

  // Fuel loading state
  let fuelCapacityKg = 0;
  let availableFuelKg = 0;
  let requestedFuelKg = 0;

  // Boost mode state
  let boostBaseCost = 100000000;
  let boostCostPerKg = 5000;
  let orgBalanceUsd = 0;

  // Edit mode state
  let editShipId = "";
  let editShipOriginalParts = [];   // original part item_ids before editing
  let editShipLocationId = "";
  let allFleetShips = [];

  /* ── Category definitions ─────────────────────────────── */
  const SLOT_CATEGORIES = [
    { id: "robonauts",    label: "Robonauts",     catId: "robonaut",    hue: 310, tooltip: "Automated systems that prospect celestial sites and enable refueling." },
    { id: "refineries",   label: "Refineries",    catId: "refinery",    hue: 340, tooltip: "Processes raw feedstock into refined industrial resources." },
    { id: "constructors", label: "Constructors",  catId: "constructor", hue: 175, tooltip: "Mines celestial sites and constructs modules from refined goods." },
    { id: "storage",      label: "Storage",       catId: "storage",     hue: 260, tooltip: "Cargo and propellant capacity for logistics and mission endurance." },
    { id: "radiators",    label: "Radiators",     catId: "radiator",    hue: 200, tooltip: "Rejects excess waste heat so the ship can run at sustained power." },
    { id: "generators",   label: "Generators",    catId: "generator",   hue: 145, tooltip: "Converts MWth into electrical output (MWe)." },
    { id: "reactors",     label: "Reactors",      catId: "reactor",     hue: 55,  tooltip: "Uses reactions between fundamental forces to create MWth." },
    { id: "thrusters",    label: "Thrusters",     catId: "thruster",    hue: 18,  tooltip: "Converts onboard energy and propellant into thrust and delta-v." },
  ];

  /* ── Utilities ────────────────────────────────────────── */
  function setMsg(text, isError) {
    msgEl.textContent = text || "";
    msgEl.style.color = isError ? "#ff9aa3" : "";
  }

  async function fetchJson(url, options) {
    const resp = await fetch(url, options);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data?.detail || `Request failed: ${resp.status}`);
    return data;
  }

  function fmtMassKg(v) {
    const val = Math.max(0, Number(v) || 0);
    return val >= 5000 ? `${(val / 1000).toFixed(1)} t` : `${val.toFixed(0)} kg`;
  }

  function fmtMs(v) { return `${Math.max(0, Number(v) || 0).toFixed(0)} m/s`; }
  function fmtGs(v) { return `${Math.max(0, Number(v) || 0).toFixed(3)} g`; }
  function fmtUsd(v) { return `$${Math.round(Number(v) || 0).toLocaleString("en-US")}`; }

  /* ── Screen management ────────────────────────────────── */
  function showScreen(screen) {
    modeSelectEl.style.display = screen === "modes" ? "" : "none";
    shipSelectEl.style.display = screen === "shipSelect" ? "" : "none";
    designerEl.style.display = screen === "designer" ? "" : "none";
  }

  function resetDesignerState() {
    selectedItemIds = [];
    activeCategory = "";
    requestedFuelKg = 0;
    fuelCapacityKg = 0;
    availableFuelKg = 0;
    editShipId = "";
    editShipOriginalParts = [];
    editShipLocationId = "";
    shipNameEl.value = "";
    setMsg("", false);
    if (boostCostEl) boostCostEl.style.display = "none";
  }

  /* ── Source location management ───────────────────────── */
  function sourceOptionLabel(loc) {
    const id = String(loc?.id || "");
    const name = String(loc?.name || id);
    const partQty = Math.max(0, Number(loc?.inventory_part_qty || 0));
    const resourceMass = Math.max(0, Number(loc?.inventory_resource_mass_kg || 0));
    return `${name} (${id}) · ${partQty.toFixed(0)} parts · ${fmtMassKg(resourceMass)} resources`;
  }

  function renderSourceLocations() {
    if (!sourceLocationEl) return;
    sourceLocationEl.innerHTML = "";
    const options = (Array.isArray(buildSourceLocations) ? buildSourceLocations : [])
      .slice()
      .sort((a, b) => String(a?.name || a?.id || "").localeCompare(String(b?.name || b?.id || "")));

    for (const loc of options) {
      const opt = document.createElement("option");
      opt.value = String(loc?.id || "");
      opt.textContent = sourceOptionLabel(loc);
      sourceLocationEl.appendChild(opt);
    }

    if (!options.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No locations with inventory";
      opt.disabled = true;
      sourceLocationEl.appendChild(opt);
    }

    sourceLocationEl.value = buildLocationId;
    if (!sourceLocationEl.value) {
      buildLocationId = String(sourceLocationEl.options[0]?.value || "");
      sourceLocationEl.value = buildLocationId;
    }
    updateSourceHint();
  }

  function updateSourceHint() {
    if (!sourceHintEl) return;
    if (currentMode === "boost") {
      sourceHintEl.innerHTML = `Parts boosted from Earth to <b>LEO</b> — cost deducted from org funds`;
      return;
    }
    if (currentMode === "edit") {
      sourceHintEl.innerHTML = `Editing at <b>${editShipLocationId || buildLocationId}</b> — parts from local inventory`;
      return;
    }
    const selected = buildSourceLocations.find((x) => String(x?.id || "") === String(buildLocationId)) || null;
    const sourceCount = Array.isArray(buildSourceLocations) ? buildSourceLocations.length : 0;
    if (!selected) {
      sourceHintEl.innerHTML = `Part source: <b>${buildLocationId}</b> · ${sourceCount} available`;
      return;
    }
    sourceHintEl.innerHTML = `Part source: <b>${selected.name || selected.id}</b> · inventory consumed · ${sourceCount} available`;
  }

  function applyCatalogData(data, preserveSelection = true) {
    const prev = String(buildLocationId || "");
    buildLocationId = String(data?.build_location_id || prev || "");
    catalogParts = Array.isArray(data?.parts) ? data.parts : [];
    buildSourceLocations = Array.isArray(data?.build_source_locations) ? data.build_source_locations : [];
    if (preserveSelection && buildSourceLocations.some((loc) => String(loc?.id || "") === prev)) {
      buildLocationId = prev;
    }
  }

  function computeCatalogHash(data) {
    try {
      const ids = (data?.parts || []).map((p) => p.item_id).sort().join(",");
      const locs = (data?.build_source_locations || []).map((l) => `${l.id}:${l.inventory_part_qty}:${l.inventory_resource_mass_kg}`).sort().join(";");
      return `${ids}|${locs}`;
    } catch { return ""; }
  }

  /* ── Part classification ──────────────────────────────── */
  function partFolderId(part) {
    const rawCategory = String(part?.category_id || part?.type || "").toLowerCase();
    if (rawCategory === "reactor") return "reactors";
    if (rawCategory === "thruster") return "thrusters";
    if (rawCategory === "generator") return "generators";
    if (rawCategory === "radiator") return "radiators";
    if (rawCategory === "constructor") return "constructors";
    if (rawCategory === "refinery") return "refineries";
    if (rawCategory === "robonaut") return "robonauts";
    if (rawCategory === "storage") return "storage";

    const name = String(part?.name || "").toLowerCase();
    if (rawCategory.includes("reactor") || rawCategory === "fission" || rawCategory === "fusion") return "reactors";
    if (rawCategory.includes("thruster") || rawCategory.includes("engine") || name.includes("thruster") || name.includes("engine")) return "thrusters";
    if (rawCategory.includes("generator") || rawCategory === "power" || rawCategory === "power_generator") return "generators";
    if (rawCategory.includes("radiator") || rawCategory === "cooler" || rawCategory === "cooling") return "radiators";
    if (rawCategory.includes("constructor") || rawCategory.includes("fabricat")) return "constructors";
    if (rawCategory.includes("refiner") || rawCategory.includes("smelter") || rawCategory.includes("processing")) return "refineries";
    if (rawCategory.includes("robonaut") || rawCategory.includes("drone") || rawCategory.includes("rover")) return "robonauts";
    if (Number(part?.thrust_kn) > 0 || Number(part?.isp_s) > 0) return "thrusters";
    if (Number(part?.heat_rejection_mw) > 0) return "radiators";
    if (Number(part?.capacity_m3) > 0 || Number(part?.fuel_capacity_kg) > 0 || Number(part?.water_kg) > 0) return "storage";
    if (name.includes("storage") || name.includes("tank")) return "storage";
    return "storage";
  }

  function partSubtitle(part) {
    return String(part?.category_id || part?.type || "module").trim().toLowerCase() || "module";
  }

  function partVolumeM3(part) {
    const capacity = Number(part?.capacity_m3 || 0);
    if (capacity > 0) return capacity;
    return Number(part?.volume_m3 || 0);
  }

  /* ── Inventory / garage helpers ───────────────────────── */
  function mapInventoryPartToGaragePart(stackPart) {
    const payload = (stackPart && typeof stackPart.part === "object" && stackPart.part) ? stackPart.part : {};
    const itemId = String(payload.item_id || stackPart?.item_id || "").trim();
    const name = String(payload.name || stackPart?.name || itemId || "Part").trim();
    return {
      ...payload,
      item_id: itemId,
      name,
      available_qty: Math.max(0, Math.floor(Number(stackPart?.quantity || 0))),
      source_kind: "inventory",
    };
  }

  function mapBoostableToGaragePart(item) {
    return {
      item_id: String(item.item_id || ""),
      name: String(item.name || item.item_id || ""),
      type: String(item.type || ""),
      category_id: String(item.type || ""),
      mass_kg: Number(item.mass_per_unit_kg || 0),
      tech_level: Number(item.tech_level || 1),
      available_qty: Infinity,
      source_kind: "boost",
    };
  }

  async function loadGarageForCurrentSource() {
    if (currentMode === "boost") {
      // Load boostable items from org endpoint
      try {
        const data = await fetchJson("/api/org/boostable-items", { cache: "no-store" });
        boostBaseCost = Number(data.base_cost_usd || 100000000);
        boostCostPerKg = Number(data.cost_per_kg_usd || 5000);
        const items = Array.isArray(data.items) ? data.items : [];

        // Also load full catalog for detailed part info (thrust, ISP, etc.)
        const catData = await fetchJson("/api/shipyard/catalog", { cache: "no-store" });
        const catParts = Array.isArray(catData?.parts) ? catData.parts : [];
        const catById = new Map(catParts.map((p) => [String(p.item_id || ""), p]));

        garageParts = items
          .map((item) => {
            const base = mapBoostableToGaragePart(item);
            // Merge in full catalog data if available (for tooltip stats)
            const full = catById.get(base.item_id);
            if (full) {
              return {
                ...full,
                ...base,
                mass_kg: Number(full.mass_kg || base.mass_kg || 0),
              };
            }
            return base;
          })
          .filter((p) => !!p.item_id);
      } catch (err) {
        garageParts = [];
        setMsg("Failed to load boostable items: " + (err?.message || ""), true);
      }
      return;
    }

    // site / edit mode: load inventory from location
    if (!buildLocationId) { garageParts = []; return; }
    try {
      const inv = await fetchJson(`/api/inventory/location/${encodeURIComponent(buildLocationId)}`, { cache: "no-store" });
      const invParts = Array.isArray(inv?.parts) ? inv.parts : [];
      garageParts = invParts
        .map(mapInventoryPartToGaragePart)
        .filter((part) => !!String(part.item_id || "").trim() && Number(part.available_qty || 0) > 0);
    } catch {
      garageParts = [];
    }
  }

  function selectedCountByItemId(itemId) {
    const target = String(itemId || "");
    let count = 0;
    for (const id of selectedItemIds) {
      if (String(id || "") === target) count += 1;
    }
    return count;
  }

  function findPart(itemId) {
    return garageParts.find((p) => String(p.item_id || "") === String(itemId || "")) || null;
  }

  /* ── Item card builder ────────────────────────────────── */
  function createItemCard(part, options = {}) {
    const label = String(part?.name || part?.type || part?.item_id || "Part").trim() || "Part";
    const category = String(part?.category_id || part?.type || "module").trim().toLowerCase();

    const tooltipLines = [];
    if (Number(part?.thrust_kn) > 0) tooltipLines.push(["Thrust", `${Number(part.thrust_kn).toFixed(0)} kN`]);
    if (Number(part?.isp_s) > 0) tooltipLines.push(["ISP", `${Number(part.isp_s).toFixed(0)} s`]);
    if (Number(part?.thermal_mw) > 0) tooltipLines.push(["Power", `${Number(part.thermal_mw).toFixed(1)} MWth`]);
    if (Number(part?.electric_mw) > 0) tooltipLines.push(["Electric", `${Number(part.electric_mw).toFixed(1)} MWe`]);
    if (Number(part?.heat_rejection_mw) > 0) tooltipLines.push(["Rejection", `${Number(part.heat_rejection_mw).toFixed(1)} MWth`]);
    if (Number(part?.capacity_m3) > 0) tooltipLines.push(["Capacity", `${Number(part.capacity_m3).toFixed(0)} m³`]);
    if (Number(part?.fuel_capacity_kg) > 0) tooltipLines.push(["Fuel Cap", fmtMassKg(Number(part.fuel_capacity_kg))]);

    const cell = itemDisplay && typeof itemDisplay.createGridCell === "function"
      ? itemDisplay.createGridCell({
          label,
          subtitle: String(options.subtitle || partSubtitle(part)),
          iconSeed: part?.item_id || part?.name || label,
          itemId: part?.item_id || "",
          category: category,
          mass_kg: Number(part?.mass_kg) || 0,
          volume_m3: partVolumeM3(part),
          quantity: Number(options.quantity) || 0,
          draggable: !!options.draggable,
          className: options.className || "shipyardItemCell",
          branch: part?.branch || "",
          family: part?.family || part?.thruster_family || "",
          techLevel: part?.tech_level || "",
          tooltipLines: tooltipLines.length ? tooltipLines : undefined,
        })
      : (() => {
          const fallback = document.createElement("div");
          fallback.className = `invCell ${options.className || "shipyardItemCell"}`;
          if (options.draggable) { fallback.draggable = true; fallback.classList.add("isDraggable"); }
          fallback.textContent = label;
          return fallback;
        })();

    if (options.disabled) {
      cell.classList.add("isDisabled");
      cell.style.opacity = "0.4";
      cell.style.pointerEvents = "none";
    }
    return cell;
  }

  /* ── Ship Blueprint rendering ─────────────────────────── */

  function getSelectedByCategory() {
    const map = new Map(SLOT_CATEGORIES.map((cat) => [cat.id, []]));
    selectedItemIds.forEach((itemId, globalIdx) => {
      const part = findPart(itemId);
      const folderId = part ? partFolderId(part) : "storage";
      if (!map.has(folderId)) map.set(folderId, []);
      map.get(folderId).push({ itemId, globalIdx, part });
    });
    return map;
  }

  function renderBlueprint() {
    blueprintEl.innerHTML = "";
    const byCat = getSelectedByCategory();

    SLOT_CATEGORIES.forEach((cat) => {
      const items = byCat.get(cat.id) || [];
      const section = document.createElement("div");
      section.className = "bpSection" + (activeCategory === cat.id ? " isActive" : "");
      section.dataset.category = cat.id;

      // Header
      const header = document.createElement("div");
      header.className = "bpSectionHeader";
      header.title = cat.tooltip;

      const colorBar = document.createElement("span");
      colorBar.className = "bpSectionColorBar";
      colorBar.style.background = `hsl(${cat.hue}, 72%, 46%)`;
      header.appendChild(colorBar);

      const label = document.createElement("span");
      label.className = "bpSectionLabel";
      label.textContent = cat.label;
      header.appendChild(label);

      const count = document.createElement("span");
      count.className = "bpSectionCount";
      count.textContent = items.length > 0 ? String(items.length) : "";
      header.appendChild(count);

      header.addEventListener("click", () => {
        setActiveCategory(cat.id);
      });

      section.appendChild(header);

      // Slot row (only shown if section is active OR has items)
      if (items.length > 0 || activeCategory === cat.id) {
        const slotRow = document.createElement("div");
        slotRow.className = "bpSlotRow";

        // Filled slots
        items.forEach(({ itemId, globalIdx, part }) => {
          const slot = document.createElement("div");
          slot.className = "bpSlot bpSlotFilled";
          slot.dataset.globalIndex = String(globalIdx);

          const card = createItemCard(part || { item_id: itemId, name: itemId }, {
            subtitle: part ? partSubtitle(part) : "module",
            className: "shipyardItemCell",
          });
          slot.appendChild(card);

          // Remove overlay
          const removeOverlay = document.createElement("div");
          removeOverlay.className = "bpSlotRemove";
          slot.appendChild(removeOverlay);

          // Click to remove
          slot.addEventListener("click", (e) => {
            e.stopPropagation();
            removeSelectedPartAt(globalIdx);
          });

          // Drag from filled slot
          slot.draggable = true;
          slot.addEventListener("dragstart", (e) => {
            if (!e.dataTransfer) return;
            const payload = JSON.stringify({ source: "slot", index: globalIdx, item_id: itemId });
            e.dataTransfer.effectAllowed = "move";
            e.dataTransfer.setData(SHIPYARD_DRAG_MIME, payload);
            e.dataTransfer.setData("text/plain", payload);
            slot.classList.add("isDragging");
          });
          slot.addEventListener("dragend", () => slot.classList.remove("isDragging"));

          // Drop on filled slot (reorder)
          slot.addEventListener("dragover", (e) => {
            e.preventDefault();
            if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
            slot.classList.add("dragOver");
          });
          slot.addEventListener("dragleave", () => slot.classList.remove("dragOver"));
          slot.addEventListener("drop", (e) => {
            slot.classList.remove("dragOver");
            const payload = parseShipyardDragPayload(e);
            if (!payload) return;
            e.preventDefault();
            e.stopPropagation();
            if (payload.source === "picker") {
              addSelectedPart(payload.item_id, globalIdx);
            } else if (payload.source === "slot") {
              moveSelectedPart(Number(payload.index), globalIdx);
            }
          });

          slotRow.appendChild(slot);
        });

        // Empty "add" slot at the end
        const emptySlot = document.createElement("div");
        emptySlot.className = "bpSlot bpSlotEmpty";
        emptySlot.textContent = "+";
        emptySlot.addEventListener("click", (e) => {
          e.stopPropagation();
          setActiveCategory(cat.id);
        });

        // Drop on empty slot
        emptySlot.addEventListener("dragover", (e) => {
          e.preventDefault();
          if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
          emptySlot.classList.add("dragOver");
        });
        emptySlot.addEventListener("dragleave", () => emptySlot.classList.remove("dragOver"));
        emptySlot.addEventListener("drop", (e) => {
          emptySlot.classList.remove("dragOver");
          const payload = parseShipyardDragPayload(e);
          if (!payload) return;
          e.preventDefault();
          e.stopPropagation();
          if (payload.source === "picker" || payload.source === "garage") {
            addSelectedPart(payload.item_id);
          }
        });

        slotRow.appendChild(emptySlot);
        section.appendChild(slotRow);
      }

      // Allow drop on the whole section
      section.addEventListener("dragover", (e) => {
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
      });
      section.addEventListener("drop", (e) => {
        const payload = parseShipyardDragPayload(e);
        if (!payload) return;
        e.preventDefault();
        if (payload.source === "picker" || payload.source === "garage") {
          addSelectedPart(payload.item_id);
        }
      });

      blueprintEl.appendChild(section);
    });
  }

  /* ── Parts Picker rendering ───────────────────────────── */

  function renderPicker() {
    pickerEl.innerHTML = "";

    if (!activeCategory) {
      pickerLabelEl.textContent = "Select a slot";
      pickerLabelEl.classList.remove("hasCategory");
      const hint = document.createElement("div");
      hint.className = "pickerEmpty";
      hint.textContent = "Click a category in the Ship Blueprint to see available parts.";
      pickerEl.appendChild(hint);
      return;
    }

    const catDef = SLOT_CATEGORIES.find((c) => c.id === activeCategory);
    pickerLabelEl.textContent = catDef ? catDef.label : activeCategory;
    pickerLabelEl.classList.add("hasCategory");

    // Filter garage parts to this category
    const partsForCategory = garageParts
      .filter((p) => partFolderId(p) === activeCategory)
      .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));

    if (!partsForCategory.length) {
      const hint = document.createElement("div");
      hint.className = "pickerEmpty";
      if (currentMode === "boost") {
        hint.textContent = `No boostable ${catDef ? catDef.label.toLowerCase() : "parts"} unlocked. Research higher tech levels.`;
      } else {
        hint.textContent = `No ${catDef ? catDef.label.toLowerCase() : "parts"} available at this location.`;
      }
      pickerEl.appendChild(hint);
      return;
    }

    partsForCategory.forEach((part) => {
      const selectedCount = selectedCountByItemId(part.item_id);
      const availableQty = Number(part.available_qty);
      const isLimited = Number.isFinite(availableQty);
      const remaining = isLimited ? Math.max(0, Math.floor(availableQty - selectedCount)) : Infinity;
      const isDisabled = isLimited && remaining <= 0;

      const card = createItemCard(part, {
        draggable: !isDisabled,
        disabled: isDisabled,
        subtitle: partSubtitle(part),
        quantity: isLimited ? remaining : 0,
        className: "pickerItemCell",
      });

      // Click to add
      card.addEventListener("click", () => {
        if (isDisabled) return;
        const sc = selectedCountByItemId(part.item_id);
        const aq = Number(part.available_qty);
        const il = Number.isFinite(aq);
        if (il && Math.max(0, Math.floor(aq - sc)) <= 0) return;
        addSelectedPart(part.item_id);
      });

      // Drag from picker
      if (!isDisabled) {
        card.addEventListener("dragstart", (e) => {
          if (!e.dataTransfer) return;
          const payload = JSON.stringify({ source: "picker", item_id: part.item_id });
          e.dataTransfer.effectAllowed = "copyMove";
          e.dataTransfer.setData(SHIPYARD_DRAG_MIME, payload);
          e.dataTransfer.setData("text/plain", payload);
          card.classList.add("isDragging");
        });
        card.addEventListener("dragend", () => card.classList.remove("isDragging"));
      }

      pickerEl.appendChild(card);
    });
  }

  /* ── Selection management ─────────────────────────────── */

  function setActiveCategory(catId) {
    activeCategory = catId;
    renderBlueprint();
    renderPicker();
  }

  function addSelectedPart(itemId, insertAt = null) {
    const id = String(itemId || "").trim();
    if (!id) return;

    const part = findPart(id);
    if (part) {
      const folder = partFolderId(part);
      if (folder !== activeCategory) {
        activeCategory = folder;
      }
    }

    if (insertAt == null || insertAt < 0 || insertAt > selectedItemIds.length) {
      selectedItemIds.push(id);
    } else {
      selectedItemIds.splice(insertAt, 0, id);
    }
    renderBlueprint();
    renderPicker();
    refreshPreview();
  }

  function removeSelectedPartAt(globalIndex) {
    if (globalIndex < 0 || globalIndex >= selectedItemIds.length) return;
    selectedItemIds.splice(globalIndex, 1);
    renderBlueprint();
    renderPicker();
    refreshPreview();
  }

  function moveSelectedPart(fromIndex, toIndex) {
    const from = Number(fromIndex);
    const to = Number(toIndex);
    if (!Number.isInteger(from) || !Number.isInteger(to)) return;
    if (from < 0 || from >= selectedItemIds.length || to < 0 || to >= selectedItemIds.length) return;
    if (from === to) return;
    const [moved] = selectedItemIds.splice(from, 1);
    selectedItemIds.splice(to, 0, moved);
    renderBlueprint();
    refreshPreview();
  }

  /* ── Drag & drop helpers ──────────────────────────────── */

  function parseShipyardDragPayload(event) {
    const dt = event?.dataTransfer;
    if (!dt) return null;
    const raw = dt.getData(SHIPYARD_DRAG_MIME) || dt.getData("text/plain");
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return null;
      return parsed;
    } catch { return null; }
  }

  /* ── Stats rendering ──────────────────────────────────── */

  function fmtPct(v) { return `${(Math.max(0, Math.min(1, Number(v) || 0)) * 100).toFixed(0)}%`; }
  function balanceClass(surplus) { const v = Number(surplus) || 0; return v > 0 ? "pbPositive" : v < 0 ? "pbNegative" : "pbNeutral"; }
  function fmtMwTh(v) { return `${Math.max(0, Number(v) || 0).toFixed(1)}<span class="pbUnit">MWth</span>`; }
  function fmtMwE(v) { return `${Math.max(0, Number(v) || 0).toFixed(1)}<span class="pbUnit">MWe</span>`; }

  function renderPowerBalance(pb) {
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
    const wasteSurplus = Math.max(0, Number(pb.waste_heat_surplus_mw || 0));
    const maxThrottle = Number(pb.max_throttle || 0);

    const hasAny = reactorMw > 0 || thrusterMw > 0 || genInputMw > 0 || radRejection > 0;
    if (!hasAny) return "";

    const isOverheating = wasteSurplus > 0;
    const overheatBanner = isOverheating
      ? `<div class="pbOverheatBanner"><span class="pbOverheatIcon">⚠</span><span class="pbOverheatText">OVERHEATING — ${wasteSurplus.toFixed(1)} MWth unradiated waste heat. This design risks thermal failure.</span></div>`
      : "";
    const genThrottled = genThrottle < 1 && electricRated > 0;

    return `
      <div class="powerBalancePanel${isOverheating ? ' pbOverheating' : ''}">
        <div class="pbTitle">Power &amp; Thermal Balance</div>
        <div class="pbSection">
          <div class="pbSectionHead">Thermal Budget (MWth)</div>
          <div class="pbRow"><span class="pbLabel">Reactor output</span><span class="pbVal">${fmtMwTh(reactorMw)}</span></div>
          <div class="pbRow"><span class="pbLabel">Thruster demand</span><span class="pbVal">−${fmtMwTh(thrusterMw)}</span></div>
          <div class="pbRow"><span class="pbLabel">Generator input</span><span class="pbVal">−${fmtMwTh(genInputMw)}</span></div>
          <div class="pbRow pbDivider"><span class="pbLabel"><b>Thermal surplus</b></span><span class="pbVal ${balanceClass(thermalSurplus)}"><b>${thermalSurplus >= 0 ? "+" : ""}${thermalSurplus.toFixed(1)}<span class="pbUnit">MWth</span></b></span></div>
          ${thrusterMw > 0 ? `<div class="pbRow"><span class="pbLabel">Max throttle</span><span class="pbVal ${maxThrottle < 1 ? "pbNegative" : "pbPositive"}">${fmtPct(maxThrottle)}</span></div>` : ""}
        </div>
        <div class="pbSection">
          <div class="pbSectionHead">Electric Output (MWe)</div>
          <div class="pbRow"><span class="pbLabel">Generator output${genThrottled ? ' <span class="pbNegative">(throttled)</span>' : ''}</span><span class="pbVal">${fmtMwE(electricMw)}${genThrottled ? ` <span class="muted">/ ${electricRated.toFixed(1)}</span>` : ''}</span></div>
        </div>
        <div class="pbSection">
          <div class="pbSectionHead">Waste Heat Budget (MWth)</div>
          <div class="pbRow"><span class="pbLabel">Reactor heat produced</span><span class="pbVal">${fmtMwTh(reactorMw)}</span></div>
          ${thrustExhaust > 0 ? `<div class="pbRow"><span class="pbLabel">Thrust exhaust</span><span class="pbVal">−${fmtMwTh(thrustExhaust)}</span></div>` : ""}
          ${electricConv > 0 ? `<div class="pbRow"><span class="pbLabel">Converted to electric</span><span class="pbVal">−${fmtMwE(electricConv)}</span></div>` : ""}
          <div class="pbRow"><span class="pbLabel">Radiator rejection</span><span class="pbVal">−${fmtMwTh(radRejection)}</span></div>
          <div class="pbRow pbDivider"><span class="pbLabel"><b>Unradiated heat</b></span><span class="pbVal ${balanceClass(wasteSurplus)}"><b>${wasteSurplus >= 0 ? "+" : ""}${wasteSurplus.toFixed(1)}<span class="pbUnit">MWth</span></b></span></div>
        </div>
        ${overheatBanner}
      </div>
    `;
  }

  function fuelPct(fuel, cap) {
    if (cap <= 0) return 0;
    return Math.max(0, Math.min(100, (fuel / cap) * 100));
  }

  function renderDeltaVPanel(stats) {
    const dryMass = Number(stats?.dry_mass_kg || 0);
    const fuelMass = Number(stats?.fuel_kg || 0);
    const fuelCap = Number(stats?.fuel_capacity_kg || 0);
    const wetMass = Number(stats?.wet_mass_kg || 0);
    const isp = Number(stats?.isp_s || 0);
    const thrust = Number(stats?.thrust_kn || 0);
    const dv = Number(stats?.delta_v_remaining_m_s || 0);
    const accelG = Number(stats?.accel_g || 0);
    const fPct = fuelPct(fuelMass, fuelCap);
    const dvClass = dv > 0 ? "pbPositive" : "pbNeutral";

    return `
      <div class="powerBalancePanel">
        <div class="pbTitle">Delta-v &amp; Propulsion</div>
        <div class="pbSection">
          <div class="pbSectionHead">Mass Budget</div>
          <div class="pbRow"><span class="pbLabel">Dry mass</span><span class="pbVal">${fmtMassKg(dryMass)}</span></div>
          <div class="pbRow"><span class="pbLabel">Fuel</span><span class="pbVal">${fmtMassKg(fuelMass)} / ${fmtMassKg(fuelCap)}</span></div>
          <div class="pbRow"><span class="pbLabel">Fuel level</span><span class="pbVal"><span class="pbBarWrap"><span class="pbBar" style="width:${fPct.toFixed(1)}%"></span></span> ${fPct.toFixed(0)}%</span></div>
          <div class="pbRow pbDivider"><span class="pbLabel"><b>Wet mass</b></span><span class="pbVal"><b>${fmtMassKg(wetMass)}</b></span></div>
        </div>
        <div class="pbSection">
          <div class="pbSectionHead">Propulsion</div>
          <div class="pbRow"><span class="pbLabel">Thrust</span><span class="pbVal">${thrust.toFixed(0)} kN</span></div>
          <div class="pbRow"><span class="pbLabel">Specific impulse</span><span class="pbVal">${isp.toFixed(0)} s</span></div>
          <div class="pbRow"><span class="pbLabel">Acceleration</span><span class="pbVal">${fmtGs(accelG)}</span></div>
        </div>
        <div class="pbSection">
          <div class="pbSectionHead">Delta-v</div>
          <div class="pbRow pbDivider"><span class="pbLabel"><b>Δv remaining</b></span><span class="pbVal ${dvClass}"><b>${fmtMs(dv)}</b></span></div>
        </div>
      </div>
    `;
  }

  function renderStats(stats, powerBalance) {
    statsEl.innerHTML = renderDeltaVPanel(stats) + renderPowerBalance(powerBalance);
  }

  /* ── Boost cost rendering ─────────────────────────────── */

  function computeSelectedTotalMassKg() {
    let total = 0;
    for (const itemId of selectedItemIds) {
      const part = findPart(itemId);
      if (part) total += Number(part.mass_kg || 0);
    }
    return total;
  }

  function renderBoostCost() {
    if (currentMode !== "boost" || !boostCostEl) {
      if (boostCostEl) boostCostEl.style.display = "none";
      return;
    }

    if (selectedItemIds.length === 0) {
      boostCostEl.style.display = "none";
      return;
    }

    const partsMassKg = computeSelectedTotalMassKg();
    const fuelMassKg = requestedFuelKg;
    const totalMassKg = partsMassKg + fuelMassKg;
    const totalCost = boostBaseCost + (boostCostPerKg * totalMassKg);
    const insufficient = totalCost > orgBalanceUsd;

    boostCostEl.style.display = "";
    boostCostEl.innerHTML = `
      <div class="boostCostLine"><span>Parts count</span><span><b>${selectedItemIds.length}</b></span></div>
      <div class="boostCostLine"><span>Parts mass</span><span><b>${fmtMassKg(partsMassKg)}</b></span></div>
      ${fuelMassKg > 0 ? `<div class="boostCostLine"><span>Water (fuel)</span><span><b>${fmtMassKg(fuelMassKg)}</b></span></div>` : ''}
      <div class="boostCostLine"><span>Total payload</span><span><b>${fmtMassKg(totalMassKg)}</b></span></div>
      <div class="boostCostLine"><span>Base launch cost</span><span>${fmtUsd(boostBaseCost)}</span></div>
      <div class="boostCostLine"><span>Mass cost (${fmtUsd(boostCostPerKg)}/kg)</span><span>${fmtUsd(boostCostPerKg * totalMassKg)}</span></div>
      <div class="boostCostLine boostCostTotal${insufficient ? ' boostCostInsufficient' : ''}">
        <span><b>Total boost cost</b></span><span><b>${fmtUsd(totalCost)}</b></span>
      </div>
      <div class="boostCostLine"><span>Org balance</span><span${insufficient ? ' class="boostCostInsufficient"' : ''}>${fmtUsd(orgBalanceUsd)}</span></div>
      ${insufficient ? '<div class="boostCostLine boostCostInsufficient" style="justify-content:center;margin-top:4px;"><b>⚠ Insufficient funds</b></div>' : ''}
    `;
  }

  /* ── Fuel UI ──────────────────────────────────────────── */

  function updateFuelUI() {
    const hasFuelCapacity = fuelCapacityKg > 0;

    // Toggle hasFuel class on the picker section to split the layout
    if (pickerSectionEl) pickerSectionEl.classList.toggle("hasFuel", hasFuelCapacity);

    if (fuelSectionEl) fuelSectionEl.style.display = hasFuelCapacity ? "" : "none";
    if (!hasFuelCapacity) return;

    // In boost mode, water is unlimited (launched from Earth)
    const effectiveAvail = currentMode === "boost" ? fuelCapacityKg : availableFuelKg;
    const maxFuel = Math.min(fuelCapacityKg, effectiveAvail);

    requestedFuelKg = Math.max(0, Math.min(requestedFuelKg, maxFuel));
    if (fuelSliderEl) { fuelSliderEl.max = String(Math.floor(maxFuel)); fuelSliderEl.value = String(Math.floor(requestedFuelKg)); }
    if (fuelInputEl) { fuelInputEl.max = String(Math.floor(maxFuel)); fuelInputEl.value = String(Math.floor(requestedFuelKg)); }
    if (fuelAvailEl) fuelAvailEl.textContent = currentMode === "boost" ? "Unlimited water (boosted from Earth)" : `${fmtMassKg(availableFuelKg)} water at site`;
    if (fuelBarFillEl) { const pct = fuelCapacityKg > 0 ? Math.min(100, (requestedFuelKg / fuelCapacityKg) * 100) : 0; fuelBarFillEl.style.width = `${pct.toFixed(1)}%`; }
    if (fuelLabelEl) { const pct = fuelCapacityKg > 0 ? (requestedFuelKg / fuelCapacityKg) * 100 : 0; fuelLabelEl.textContent = `${fmtMassKg(requestedFuelKg)} / ${fmtMassKg(fuelCapacityKg)} (${pct.toFixed(0)}%)`; }
  }

  /* ── Preview ──────────────────────────────────────────── */

  let previewTimer = null;
  async function refreshPreview() {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(doRefreshPreview, 150);
  }

  async function doRefreshPreview() {
    // For boost mode, use LEO as the preview location
    const locationForPreview = currentMode === "boost" ? "LEO" : buildLocationId;

    try {
      const data = await fetchJson("/api/shipyard/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          parts: selectedItemIds,
          source_location_id: locationForPreview,
          fuel_kg: requestedFuelKg > 0 ? requestedFuelKg : null,
          unlimited_fuel: currentMode === "boost",
        }),
      });
      const stats = data.stats || {};
      fuelCapacityKg = Number(stats.fuel_capacity_kg || 0);
      availableFuelKg = Number(data.available_fuel_kg || 0);

      // In boost mode, water is unlimited (boosted from Earth)
      const effectiveAvail = currentMode === "boost" ? fuelCapacityKg : availableFuelKg;
      const maxFuel = Math.min(fuelCapacityKg, effectiveAvail);
      requestedFuelKg = Math.max(0, Math.min(requestedFuelKg, maxFuel));
      updateFuelUI();
      renderStats(stats, data.power_balance || null);
      renderBoostCost();
    } catch (err) {
      renderStats({}, null);
      renderBoostCost();
      setMsg(err?.message || "Failed to refresh preview", true);
    }
  }

  /* ── Build actions ────────────────────────────────────── */

  async function buildShipBoost() {
    const name = String(shipNameEl.value || "").trim();
    if (!name) { setMsg("Ship name is required.", true); return; }
    if (!selectedItemIds.length) { setMsg("Add at least one part before building.", true); return; }

    // Check cost client-side (parts + fuel water)
    const partsMassKg = computeSelectedTotalMassKg();
    const fuelMassKg = requestedFuelKg;
    const totalMassKg = partsMassKg + fuelMassKg;
    const totalCost = boostBaseCost + (boostCostPerKg * totalMassKg);
    if (totalCost > orgBalanceUsd) {
      setMsg(`Insufficient funds. Need ${fmtUsd(totalCost)}, have ${fmtUsd(orgBalanceUsd)}.`, true);
      return;
    }

    buildBtn.disabled = true;
    try {
      // Step 1: Boost all parts to LEO
      // Build manifest: count quantities per item_id
      const qtyMap = {};
      for (const itemId of selectedItemIds) {
        qtyMap[itemId] = (qtyMap[itemId] || 0) + 1;
      }
      const manifest = Object.entries(qtyMap).map(([item_id, quantity]) => ({ item_id, quantity }));

      await fetchJson("/api/org/boost", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: manifest, fuel_kg: fuelMassKg > 0 ? fuelMassKg : null }),
      });

      // Step 2: Build ship at LEO using newly-boosted inventory
      const data = await fetchJson("/api/shipyard/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          parts: selectedItemIds,
          source_location_id: "LEO",
          fuel_kg: fuelMassKg > 0 ? fuelMassKg : null,
        }),
      });

      const ship = data.ship || {};
      setMsg(`Built ${ship.name || "ship"} at LEO via boost. Cost: ${fmtUsd(totalCost)}.`, false);

      // Refresh org balance
      await loadOrgBalance();

      resetDesignerState();
      await loadGarageForCurrentSource();
      renderBlueprint();
      renderPicker();
      renderBoostCost();
      await refreshPreview();
    } catch (err) {
      setMsg(err?.message || "Build failed.", true);
    } finally {
      buildBtn.disabled = false;
    }
  }

  async function buildShipSite() {
    const name = String(shipNameEl.value || "").trim();
    if (!name) { setMsg("Ship name is required.", true); return; }
    if (!selectedItemIds.length) { setMsg("Add at least one part before building.", true); return; }

    buildBtn.disabled = true;
    try {
      const data = await fetchJson("/api/shipyard/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          parts: selectedItemIds,
          source_location_id: buildLocationId,
          fuel_kg: requestedFuelKg > 0 ? requestedFuelKg : null,
        }),
      });
      const ship = data.ship || {};
      const fuelNote = (Number(ship.fuel_kg) || 0) > 0 ? ` with ${fmtMassKg(ship.fuel_kg)} fuel` : "";
      setMsg(`Built ${ship.name || "ship"} at ${ship.location_id || buildLocationId}${fuelNote}.`, false);
      selectedItemIds = [];
      requestedFuelKg = 0;
      fuelCapacityKg = 0;
      availableFuelKg = 0;
      shipNameEl.value = "";
      updateFuelUI();
      await loadGarageForCurrentSource();
      renderBlueprint();
      renderPicker();
      await refreshPreview();
    } catch (err) {
      setMsg(err?.message || "Build failed.", true);
    } finally {
      buildBtn.disabled = false;
    }
  }

  async function buildShipEdit() {
    const name = String(shipNameEl.value || "").trim();
    if (!name) { setMsg("Ship name is required.", true); return; }
    if (!selectedItemIds.length) { setMsg("Add at least one part.", true); return; }
    if (!editShipId) { setMsg("No ship selected for editing.", true); return; }

    buildBtn.disabled = true;
    try {
      // Step 1: Deconstruct the old ship (parts return to location inventory)
      await fetchJson(`/api/ships/${encodeURIComponent(editShipId)}/deconstruct`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keep_ship_record: false }),
      });

      // Step 2: Build the new ship from the location inventory (which now has old + new parts)
      const data = await fetchJson("/api/shipyard/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          parts: selectedItemIds,
          source_location_id: editShipLocationId,
          fuel_kg: requestedFuelKg > 0 ? requestedFuelKg : null,
        }),
      });

      const ship = data.ship || {};
      const fuelNote = (Number(ship.fuel_kg) || 0) > 0 ? ` with ${fmtMassKg(ship.fuel_kg)} fuel` : "";
      setMsg(`Rebuilt ${ship.name || "ship"} at ${ship.location_id || editShipLocationId}${fuelNote}.`, false);

      selectedItemIds = [];
      requestedFuelKg = 0;
      fuelCapacityKg = 0;
      availableFuelKg = 0;
      editShipId = "";
      editShipOriginalParts = [];
      shipNameEl.value = "";
      updateFuelUI();
      await loadGarageForCurrentSource();
      renderBlueprint();
      renderPicker();
      await refreshPreview();
    } catch (err) {
      setMsg(err?.message || "Rebuild failed.", true);
    } finally {
      buildBtn.disabled = false;
    }
  }

  function buildShip() {
    if (currentMode === "boost") return buildShipBoost();
    if (currentMode === "edit") return buildShipEdit();
    return buildShipSite();
  }

  /* ── Org balance (for boost mode) ─────────────────────── */

  async function loadOrgBalance() {
    try {
      const data = await fetchJson("/api/org", { cache: "no-store" });
      orgBalanceUsd = Number(data?.org?.balance_usd || 0);
    } catch {
      orgBalanceUsd = 0;
    }
  }

  /* ── Source refresh ───────────────────────────────────── */

  async function refreshSourcesOnly() {
    if (currentMode === "boost") return;

    const before = String(buildLocationId || "");
    const data = await fetchJson("/api/shipyard/catalog", { cache: "no-store" });
    const hash = computeCatalogHash(data);
    if (hash && hash === lastCatalogHash) return;
    lastCatalogHash = hash;
    applyCatalogData(data, true);
    renderSourceLocations();
    const after = String(buildLocationId || "");
    if (after !== before) selectedItemIds = [];
    await loadGarageForCurrentSource();
    renderBlueprint();
    renderPicker();
  }

  /* ── Fleet loading (for edit mode) ────────────────────── */

  async function loadFleet() {
    try {
      const data = await fetchJson("/api/state", { cache: "no-store" });
      allFleetShips = Array.isArray(data?.ships) ? data.ships : [];
      return allFleetShips;
    } catch {
      allFleetShips = [];
      return [];
    }
  }

  function renderShipSelector() {
    if (!shipListEl) return;
    shipListEl.innerHTML = "";

    const docked = allFleetShips.filter((s) => s.is_own && s.status === "docked" && s.location_id);
    const inTransit = allFleetShips.filter((s) => s.is_own && s.status === "transit");

    if (!docked.length && !inTransit.length) {
      shipListEl.innerHTML = '<div class="pickerEmpty">No ships in your fleet.</div>';
      return;
    }

    // Docked ships (clickable)
    docked.forEach((ship) => {
      const card = document.createElement("div");
      card.className = "shipyardShipCard";

      const nameEl = document.createElement("span");
      nameEl.className = "shipCardName";
      nameEl.textContent = ship.name || ship.id;
      card.appendChild(nameEl);

      const detailEl = document.createElement("span");
      detailEl.className = "shipCardDetail";
      const partCount = Array.isArray(ship.parts) ? ship.parts.length : 0;
      detailEl.textContent = `${ship.location_id} · ${partCount} parts · ${fmtMassKg(ship.dry_mass_kg || 0)}`;
      card.appendChild(detailEl);

      card.addEventListener("click", () => {
        selectShipForEdit(ship);
      });

      shipListEl.appendChild(card);
    });

    // In-transit ships (disabled)
    inTransit.forEach((ship) => {
      const card = document.createElement("div");
      card.className = "shipyardShipCard shipyardShipCardDisabled";

      const nameEl = document.createElement("span");
      nameEl.className = "shipCardName";
      nameEl.textContent = ship.name || ship.id;
      card.appendChild(nameEl);

      const detailEl = document.createElement("span");
      detailEl.className = "shipCardDetail";
      detailEl.textContent = `In transit (${ship.from_location_id} → ${ship.to_location_id})`;
      card.appendChild(detailEl);

      shipListEl.appendChild(card);
    });
  }

  async function selectShipForEdit(ship) {
    editShipId = ship.id;
    editShipLocationId = ship.location_id;
    buildLocationId = ship.location_id;

    // Extract part item_ids from parts array
    const parts = Array.isArray(ship.parts) ? ship.parts : [];
    editShipOriginalParts = parts.map((p) => String(p.item_id || "")).filter(Boolean);

    // Load inventory at this location (for additional parts)
    await loadGarageForCurrentSource();

    // Pre-populate the blueprint with the ship's current parts
    // The ship's own parts count as available since deconstruct will return them
    augmentGarageWithShipParts(parts);

    selectedItemIds = [...editShipOriginalParts];
    shipNameEl.value = ship.name || "";

    // Configure designer for edit
    configureDesignerForMode();
    showScreen("designer");

    // Auto-select first category with parts
    const firstWithParts = SLOT_CATEGORIES.find((cat) =>
      selectedItemIds.some((id) => {
        const p = findPart(id);
        return p && partFolderId(p) === cat.id;
      })
    );
    activeCategory = firstWithParts ? firstWithParts.id : SLOT_CATEGORIES[0].id;

    renderBlueprint();
    renderPicker();
    await refreshPreview();
  }

  function augmentGarageWithShipParts(shipParts) {
    // Add the ship's own parts as available (since deconstruct will release them)
    const shipPartCounts = {};
    for (const part of shipParts) {
      const id = String(part.item_id || "");
      if (!id) continue;
      shipPartCounts[id] = (shipPartCounts[id] || 0) + 1;
    }

    for (const [itemId, count] of Object.entries(shipPartCounts)) {
      const existing = garageParts.find((p) => String(p.item_id || "") === itemId);
      if (existing) {
        // Add ship's count to existing garage quantity
        existing.available_qty = (existing.available_qty || 0) + count;
      } else {
        // Create new garage entry from the ship's part data
        const shipPart = shipParts.find((p) => String(p.item_id || "") === itemId);
        if (shipPart) {
          garageParts.push({
            ...shipPart,
            available_qty: count,
            source_kind: "ship_disassembly",
          });
        }
      }
    }
  }

  /* ── Mode initialization ──────────────────────────────── */

  function configureDesignerForMode() {
    // Source location selector: hide in boost and edit modes
    if (sourceFieldEl) {
      sourceFieldEl.style.display = (currentMode === "site") ? "" : "none";
    }

    // Build button label
    if (currentMode === "boost") {
      buildBtn.textContent = "Boost & Build";
    } else if (currentMode === "edit") {
      buildBtn.textContent = "Rebuild Ship";
    } else {
      buildBtn.textContent = "Build Ship";
    }

    // Source hint
    updateSourceHint();
  }

  async function enterMode(mode) {
    currentMode = mode;
    resetDesignerState();

    if (mode === "boost") {
      // Load boostable items + org balance
      await Promise.all([loadGarageForCurrentSource(), loadOrgBalance()]);

      buildLocationId = "LEO";
      configureDesignerForMode();
      showScreen("designer");

      // Auto-select first category with boostable parts
      const firstWithParts = SLOT_CATEGORIES.find((cat) =>
        garageParts.some((p) => partFolderId(p) === cat.id)
      );
      activeCategory = firstWithParts ? firstWithParts.id : SLOT_CATEGORIES[0].id;

      renderBlueprint();
      renderPicker();
      renderBoostCost();
      await refreshPreview();

    } else if (mode === "site") {
      // Load catalog with source locations
      const data = await fetchJson("/api/shipyard/catalog", { cache: "no-store" });
      applyCatalogData(data, false);
      renderSourceLocations();
      await loadGarageForCurrentSource();

      configureDesignerForMode();
      showScreen("designer");

      const firstWithParts = SLOT_CATEGORIES.find((cat) =>
        garageParts.some((p) => partFolderId(p) === cat.id)
      );
      activeCategory = firstWithParts ? firstWithParts.id : SLOT_CATEGORIES[0].id;

      renderBlueprint();
      renderPicker();
      await refreshPreview();

      // Start auto-refresh for inventory changes
      if (sourceRefreshTimer) clearInterval(sourceRefreshTimer);
      sourceRefreshTimer = setInterval(() => {
        refreshSourcesOnly().catch(() => {});
      }, 30000);

    } else if (mode === "edit") {
      // Load fleet, show ship selector
      await loadFleet();
      renderShipSelector();
      showScreen("shipSelect");
    }
  }

  /* ── Mode selector click handlers ─────────────────────── */

  function setupModeSelectors() {
    const cards = modeSelectEl?.querySelectorAll(".shipyardModeCard") || [];
    cards.forEach((card) => {
      card.addEventListener("click", () => {
        const mode = card.dataset.mode;
        if (mode) enterMode(mode);
      });
    });

    // Back buttons
    backToModesBtn?.addEventListener("click", () => {
      if (sourceRefreshTimer) clearInterval(sourceRefreshTimer);
      showScreen("modes");
    });

    modeBackBtn?.addEventListener("click", () => {
      if (sourceRefreshTimer) clearInterval(sourceRefreshTimer);
      resetDesignerState();
      showScreen("modes");
    });
  }

  /* ── Event listeners ──────────────────────────────────── */

  sourceLocationEl?.addEventListener("change", async () => {
    buildLocationId = String(sourceLocationEl.value || "");
    selectedItemIds = [];
    requestedFuelKg = 0;
    fuelCapacityKg = 0;
    availableFuelKg = 0;
    updateSourceHint();
    await loadGarageForCurrentSource();
    renderBlueprint();
    renderPicker();
    await refreshPreview();
  });

  fuelSliderEl?.addEventListener("input", () => {
    requestedFuelKg = Number(fuelSliderEl.value || 0);
    updateFuelUI();
    renderBoostCost();
    refreshPreview();
  });

  fuelInputEl?.addEventListener("change", () => {
    const effectiveAvail = currentMode === "boost" ? fuelCapacityKg : availableFuelKg;
    const maxFuel = Math.min(fuelCapacityKg, effectiveAvail);
    requestedFuelKg = Math.max(0, Math.min(Number(fuelInputEl.value || 0), maxFuel));
    updateFuelUI();
    renderBoostCost();
    refreshPreview();
  });

  fuelFillBtn?.addEventListener("click", () => {
    const effectiveAvail = currentMode === "boost" ? fuelCapacityKg : availableFuelKg;
    requestedFuelKg = Math.min(fuelCapacityKg, effectiveAvail);
    updateFuelUI();
    renderBoostCost();
    refreshPreview();
  });

  fuelEmptyBtn?.addEventListener("click", () => {
    requestedFuelKg = 0;
    updateFuelUI();
    renderBoostCost();
    refreshPreview();
  });

  buildBtn?.addEventListener("click", buildShip);

  /* ── Init ─────────────────────────────────────────────── */

  function init() {
    setupModeSelectors();
    showScreen("modes");
  }

  init();
})();
