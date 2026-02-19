(function () {
  const garageEl = document.getElementById("shipyardGarage");
  const slotsEl = document.getElementById("shipyardSlots");
  const statsEl = document.getElementById("shipyardStats");
  const msgEl = document.getElementById("shipyardMsg");
  const buildBtn = document.getElementById("shipyardBuildBtn");
  const shipNameEl = document.getElementById("shipyardName");
  const sourceLocationEl = document.getElementById("shipyardSourceLocation");
  const sourceHintEl = document.getElementById("shipyardSourceHint");
  const itemDisplay = window.ItemDisplay || null;

  const SHIPYARD_DRAG_MIME = "application/x-earthmoon-shipyard-item";

  let buildLocationId = "LEO";
  let catalogParts = [];
  let garageParts = [];
  let selectedItemIds = [];
  let buildSourceLocations = [];
  let sourceRefreshTimer = null;

  const GARAGE_FOLDERS = [
    { id: "thrusters", label: "Thrusters" },
    { id: "storage", label: "Storage" },
    { id: "generator", label: "Generator" },
  ];

  function setMsg(text, isError) {
    msgEl.textContent = text || "";
    msgEl.style.color = isError ? "#ff9aa3" : "";
  }

  async function fetchJson(url, options) {
    const resp = await fetch(url, options);
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data?.detail || `Request failed: ${resp.status}`);
    }
    return data;
  }

  function fmtMassKg(v) {
    return `${Math.max(0, Number(v) || 0).toFixed(0)} kg`;
  }

  function fmtMs(v) {
    return `${Math.max(0, Number(v) || 0).toFixed(0)} m/s`;
  }

  function fmtGs(v) {
    return `${Math.max(0, Number(v) || 0).toFixed(3)} g`;
  }

  function sourceOptionLabel(loc) {
    const id = String(loc?.id || "");
    const name = String(loc?.name || id);
    const partQty = Math.max(0, Number(loc?.inventory_part_qty || 0));
    const resourceMass = Math.max(0, Number(loc?.inventory_resource_mass_kg || 0));
    if (id === "LEO") return `${name} (${id}) · catalog`;
    return `${name} (${id}) · ${partQty.toFixed(0)} parts · ${resourceMass.toFixed(0)} kg resources`;
  }

  function renderSourceLocations() {
    if (!sourceLocationEl) return;
    sourceLocationEl.innerHTML = "";

    const options = (Array.isArray(buildSourceLocations) ? buildSourceLocations : [])
      .slice()
      .sort((a, b) => {
        const aId = String(a?.id || "");
        const bId = String(b?.id || "");
        if (aId === "LEO" && bId !== "LEO") return -1;
        if (bId === "LEO" && aId !== "LEO") return 1;
        return String(a?.name || aId).localeCompare(String(b?.name || bId));
      });
    for (const loc of options) {
      const opt = document.createElement("option");
      opt.value = String(loc?.id || "");
      opt.textContent = sourceOptionLabel(loc);
      sourceLocationEl.appendChild(opt);
    }

    if (!options.length) {
      const opt = document.createElement("option");
      opt.value = "LEO";
      opt.textContent = "LEO";
      sourceLocationEl.appendChild(opt);
    }

    sourceLocationEl.value = buildLocationId;
    if (!sourceLocationEl.value) {
      buildLocationId = String(sourceLocationEl.options[0]?.value || "LEO");
      sourceLocationEl.value = buildLocationId;
    }
    updateSourceHint();
  }

  function updateSourceHint() {
    if (!sourceHintEl) return;
    const selected = buildSourceLocations.find((x) => String(x?.id || "") === String(buildLocationId)) || null;
    const sourceCount = Array.isArray(buildSourceLocations) ? buildSourceLocations.length : 0;
    if (!selected) {
      sourceHintEl.innerHTML = `Part source: <b>${buildLocationId}</b> · ${sourceCount} available`;
      return;
    }
    if (buildLocationId === "LEO") {
      sourceHintEl.innerHTML = `Part source: <b>${selected.name || selected.id}</b> (catalog supply) · ${sourceCount} available`;
      return;
    }
    sourceHintEl.innerHTML = `Part source: <b>${selected.name || selected.id}</b> · inventory consumed · ${sourceCount} available`;
  }

  function applyCatalogData(data, preserveSelection = true) {
    const prev = String(buildLocationId || "LEO");
    buildLocationId = String(data?.build_location_id || prev || "LEO");
    catalogParts = Array.isArray(data?.parts) ? data.parts : [];
    buildSourceLocations = Array.isArray(data?.build_source_locations) ? data.build_source_locations : [];

    if (preserveSelection && buildSourceLocations.some((loc) => String(loc?.id || "") === prev)) {
      buildLocationId = prev;
    }
  }

  async function refreshSourcesOnly() {
    const before = String(buildLocationId || "LEO");
    const data = await fetchJson("/api/shipyard/catalog", { cache: "no-store" });
    applyCatalogData(data, true);
    renderSourceLocations();
    const after = String(buildLocationId || "LEO");
    if (after !== before) {
      selectedItemIds = [];
    }
    await loadGarageForCurrentSource();
    renderGarage();
    renderSlots();
  }

  function selectedCountByItemId(itemId) {
    const target = String(itemId || "");
    let count = 0;
    for (const id of selectedItemIds) {
      if (String(id || "") === target) count += 1;
    }
    return count;
  }

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

  async function loadGarageForCurrentSource() {
    if (buildLocationId === "LEO") {
      garageParts = catalogParts.map((part) => ({
        ...part,
        available_qty: Number.POSITIVE_INFINITY,
        source_kind: "catalog",
      }));
      return;
    }

    const inv = await fetchJson(`/api/inventory/location/${encodeURIComponent(buildLocationId)}`, { cache: "no-store" });
    const invParts = Array.isArray(inv?.parts) ? inv.parts : [];
    garageParts = invParts
      .map(mapInventoryPartToGaragePart)
      .filter((part) => !!String(part.item_id || "").trim() && Number(part.available_qty || 0) > 0);
  }

  function partDetailText(part) {
    const bits = [];
    if (Number(part.thrust_kn) > 0) bits.push(`${Number(part.thrust_kn).toFixed(0)} kN`);
    if (Number(part.isp_s) > 0) bits.push(`${Number(part.isp_s).toFixed(0)} s`);
    if (Number(part.capacity_m3) > 0) bits.push(`${Number(part.capacity_m3).toFixed(0)} m3`);
    if (Number(part.fuel_capacity_kg) > 0) bits.push(`${Number(part.fuel_capacity_kg).toFixed(0)} kg fuel`);
    if (Number(part.mass_kg) > 0) bits.push(`${Number(part.mass_kg).toFixed(0)} kg dry`);
    return bits.join(" • ") || "—";
  }

  function partVolumeM3(part) {
    const capacity = Number(part?.capacity_m3 || 0);
    if (capacity > 0) return capacity;
    const vol = Number(part?.volume_m3 || 0);
    if (vol > 0) return vol;
    return 0;
  }

  function partStatsText(part, remaining = null) {
    const fmtKg = itemDisplay && typeof itemDisplay.fmtKg === "function"
      ? itemDisplay.fmtKg
      : (v) => `${Math.max(0, Number(v) || 0).toFixed(0)} kg`;
    const fmtM3 = itemDisplay && typeof itemDisplay.fmtM3 === "function"
      ? itemDisplay.fmtM3
      : (v) => `${Math.max(0, Number(v) || 0).toFixed(2)} m³`;
    const base = `${fmtKg(part?.mass_kg)} · ${fmtM3(partVolumeM3(part))}`;
    if (Number.isFinite(remaining)) return `${base} · ${Math.max(0, Math.floor(remaining))} available`;
    return base;
  }

  function partSubtitle(part) {
    const raw = String(part?.category_id || part?.type || "module").trim().toLowerCase();
    return raw || "module";
  }

  function parseShipyardDragPayload(event) {
    const dt = event?.dataTransfer;
    if (!dt) return null;
    const raw = dt.getData(SHIPYARD_DRAG_MIME) || dt.getData("text/plain");
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return null;
      return parsed;
    } catch {
      return null;
    }
  }

  function bindStackDropZone(dropEl) {
    if (!dropEl) return;
    if (dropEl.dataset.shipyardDropBound === "1") return;
    dropEl.dataset.shipyardDropBound = "1";
    dropEl.addEventListener("dragover", (event) => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    });
    dropEl.addEventListener("drop", (event) => {
      const payload = parseShipyardDragPayload(event);
      if (!payload) return;
      event.preventDefault();
      if (String(payload.source || "") === "garage") {
        addSelectedPart(payload.item_id);
        return;
      }
      if (String(payload.source || "") === "slot") {
        const fromIndex = Number(payload.index);
        if (Number.isInteger(fromIndex) && fromIndex >= 0 && fromIndex < selectedItemIds.length) {
          const [moved] = selectedItemIds.splice(fromIndex, 1);
          selectedItemIds.push(moved);
          renderSlots();
          refreshPreview();
        }
      }
    });
  }

  function removeSelectedPartAt(index) {
    if (index < 0 || index >= selectedItemIds.length) return;
    selectedItemIds.splice(index, 1);
    renderSlots();
    renderGarage();
    refreshPreview();
  }

  function addSelectedPart(itemId, insertAt = null) {
    const id = String(itemId || "").trim();
    if (!id) return;
    if (insertAt == null || insertAt < 0 || insertAt > selectedItemIds.length) {
      selectedItemIds.push(id);
    } else {
      selectedItemIds.splice(insertAt, 0, id);
    }
    renderSlots();
    renderGarage();
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
    renderSlots();
    refreshPreview();
  }

  function createItemCard(part, options = {}) {
    const label = String(part?.name || part?.type || part?.item_id || "Part").trim() || "Part";
    const card = itemDisplay && typeof itemDisplay.createCard === "function"
      ? itemDisplay.createCard({
        label,
        subtitle: String(options.subtitle || partSubtitle(part)),
        stats: String(options.stats || partStatsText(part)),
        iconSeed: part?.item_id || part?.name || label,
        className: "inventoryItemCard stackItemCard shipyardItemCard",
        role: "listitem",
        draggable: !!options.draggable,
      })
      : (() => {
        const fallback = document.createElement("article");
        fallback.className = "inventoryItemCard stackItemCard shipyardItemCard";
        fallback.setAttribute("role", "listitem");
        if (options.draggable) {
          fallback.draggable = true;
          fallback.classList.add("isDraggable");
        }
        fallback.textContent = `${label} · ${partDetailText(part)}`;
        return fallback;
      })();

    if (options.disabled) {
      card.classList.add("isDisabled");
    }
    return card;
  }

  function partFolderId(part) {
    const rawCategory = String(part?.category_id || part?.type || "").toLowerCase();
    const name = String(part?.name || "").toLowerCase();

    if (
      rawCategory.includes("thruster") ||
      rawCategory.includes("engine") ||
      Number(part?.thrust_kn) > 0 ||
      Number(part?.isp_s) > 0 ||
      name.includes("thruster") ||
      name.includes("engine")
    ) {
      return "thrusters";
    }

    if (
      rawCategory.includes("storage") ||
      rawCategory.includes("tank") ||
      rawCategory.includes("cargo") ||
      Number(part?.capacity_m3) > 0 ||
      Number(part?.fuel_capacity_kg) > 0 ||
      Number(part?.water_kg) > 0 ||
      name.includes("storage") ||
      name.includes("tank")
    ) {
      return "storage";
    }

    return "generator";
  }

  function groupedGarageParts(parts) {
    const byGroup = new Map(GARAGE_FOLDERS.map((folder) => [folder.id, []]));
    for (const part of parts) {
      const groupId = partFolderId(part);
      byGroup.get(groupId).push(part);
    }
    return GARAGE_FOLDERS.map((folder) => [folder.label, byGroup.get(folder.id) || []]);
  }

  function renderGarage() {
    const groups = groupedGarageParts(garageParts);
    garageEl.innerHTML = "";

    if (!groups.length) {
      garageEl.innerHTML = '<div class="muted small">No parts available at this source.</div>';
      return;
    }

    groups.forEach(([groupName, parts]) => {
      const group = document.createElement("div");
      group.className = "shipyardGarageGroup";

      const heading = document.createElement("div");
      heading.className = "shipyardGarageHeading";
      heading.textContent = groupName;
      group.appendChild(heading);

      const strip = document.createElement("div");
      strip.className = "stackItemStrip shipyardCardStrip";
      const sortedParts = parts
        .slice()
        .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));

      if (!sortedParts.length) {
        const empty = document.createElement("div");
        empty.className = "muted small";
        empty.textContent = "—";
        strip.appendChild(empty);
      } else {
        sortedParts.forEach((part) => {
          const selectedCount = selectedCountByItemId(part.item_id);
          const availableQty = Number(part.available_qty);
          const isLimited = Number.isFinite(availableQty);
          const remaining = isLimited ? Math.max(0, Math.floor(availableQty - selectedCount)) : Infinity;

          const card = createItemCard(part, {
            draggable: !(isLimited && remaining <= 0),
            disabled: isLimited && remaining <= 0,
            subtitle: partSubtitle(part),
            stats: partStatsText(part, isLimited ? remaining : null),
          });

          card.addEventListener("click", () => {
            if (isLimited && remaining <= 0) return;
            addSelectedPart(part.item_id);
          });

          if (!(isLimited && remaining <= 0)) {
            card.addEventListener("dragstart", (event) => {
              if (!event.dataTransfer) return;
              const payload = JSON.stringify({ source: "garage", item_id: part.item_id });
              event.dataTransfer.effectAllowed = "copyMove";
              event.dataTransfer.setData(SHIPYARD_DRAG_MIME, payload);
              event.dataTransfer.setData("text/plain", payload);
              card.classList.add("isDragging");
            });
            card.addEventListener("dragend", () => card.classList.remove("isDragging"));
          }

          strip.appendChild(card);
        });
      }

      group.appendChild(strip);

      garageEl.appendChild(group);
    });
  }

  function renderSlots() {
    slotsEl.innerHTML = "";

    if (!selectedItemIds.length) {
      const emptyDrop = document.createElement("div");
      emptyDrop.className = "partsStackEmpty shipyardSlotsDropZone";
      emptyDrop.textContent = "No parts added yet. Click or drag parts from the garage.";
      bindStackDropZone(emptyDrop);
      slotsEl.appendChild(emptyDrop);
      return;
    }

    const strip = document.createElement("div");
    strip.className = "stackItemStrip shipyardCardStrip shipyardSlotsDropZone";
    bindStackDropZone(strip);

    selectedItemIds.forEach((itemId, index) => {
      const part = garageParts.find((p) => String(p.item_id || "") === String(itemId || ""));

      const card = createItemCard(part || { item_id: itemId, name: itemId }, {
        draggable: true,
        subtitle: part ? partSubtitle(part) : "module",
        stats: part ? partStatsText(part) : "0 kg · 0.00 m³",
      });
      card.dataset.slotIndex = String(index);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "btnSecondary shipyardCardRemoveBtn";
      removeBtn.textContent = "Remove";
      removeBtn.addEventListener("click", (event) => {
        event.stopPropagation();
        removeSelectedPartAt(index);
      });
      card.appendChild(removeBtn);

      card.addEventListener("click", () => removeSelectedPartAt(index));
      card.addEventListener("dragstart", (event) => {
        if (!event.dataTransfer) return;
        const payload = JSON.stringify({ source: "slot", index });
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData(SHIPYARD_DRAG_MIME, payload);
        event.dataTransfer.setData("text/plain", payload);
        card.classList.add("isDragging");
      });
      card.addEventListener("dragend", () => card.classList.remove("isDragging"));
      card.addEventListener("dragover", (event) => {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      });
      card.addEventListener("drop", (event) => {
        const payload = parseShipyardDragPayload(event);
        if (!payload) return;
        event.preventDefault();
        const toIndex = Number(card.dataset.slotIndex);
        if (!Number.isInteger(toIndex)) return;
        if (String(payload.source || "") === "garage") {
          addSelectedPart(payload.item_id, toIndex);
          return;
        }
        if (String(payload.source || "") === "slot") {
          moveSelectedPart(Number(payload.index), toIndex);
        }
      });

      strip.appendChild(card);
    });

    slotsEl.appendChild(strip);
  }

  function renderStats(stats) {
    const dryMass = Number(stats?.dry_mass_kg || 0);
    const fuelMass = Number(stats?.fuel_kg || 0);
    const fuelCap = Number(stats?.fuel_capacity_kg || 0);
    const wetMass = Number(stats?.wet_mass_kg || 0);
    const isp = Number(stats?.isp_s || 0);
    const thrust = Number(stats?.thrust_kn || 0);
    const dv = Number(stats?.delta_v_remaining_m_s || 0);
    const accelG = Number(stats?.accel_g || 0);

    const lines = [
      `Dry mass: ${fmtMassKg(dryMass)}`,
      `Fuel: ${fmtMassKg(fuelMass)} / ${fmtMassKg(fuelCap)}`,
      `Wet mass: ${fmtMassKg(wetMass)}`,
      `Thrust: ${thrust.toFixed(0)} kN`,
      `Isp: ${isp.toFixed(0)} s`,
      `Delta-v: ${fmtMs(dv)}`,
      `Acceleration: ${fmtGs(accelG)}`,
    ];

    statsEl.innerHTML = lines.map((line) => `<li>${line}</li>`).join("");
  }

  async function refreshPreview() {
    try {
      const data = await fetchJson("/api/shipyard/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parts: selectedItemIds, source_location_id: buildLocationId }),
      });
      renderStats(data.stats || {});
    } catch (err) {
      renderStats({});
      setMsg(err?.message || "Failed to refresh preview", true);
    }
  }

  async function buildShip() {
    const name = String(shipNameEl.value || "").trim();
    if (!name) {
      setMsg("Ship name is required.", true);
      return;
    }
    if (!selectedItemIds.length) {
      setMsg("Add at least one part before building.", true);
      return;
    }

    buildBtn.disabled = true;
    try {
      const data = await fetchJson("/api/shipyard/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, parts: selectedItemIds, source_location_id: buildLocationId }),
      });
      const ship = data.ship || {};
      setMsg(`Built ${ship.name || "ship"} at ${ship.location_id || buildLocationId}.`, false);
      selectedItemIds = [];
      shipNameEl.value = "";
      await loadGarageForCurrentSource();
      renderGarage();
      renderSlots();
      await refreshPreview();
    } catch (err) {
      setMsg(err?.message || "Build failed.", true);
    } finally {
      buildBtn.disabled = false;
    }
  }

  async function init() {
    try {
      bindStackDropZone(slotsEl);
      const data = await fetchJson("/api/shipyard/catalog", { cache: "no-store" });
      applyCatalogData(data, false);
      renderSourceLocations();
      await loadGarageForCurrentSource();
      renderGarage();
      renderSlots();
      await refreshPreview();

      if (sourceRefreshTimer) clearInterval(sourceRefreshTimer);
      sourceRefreshTimer = setInterval(() => {
        refreshSourcesOnly().catch(() => {});
      }, 5000);
    } catch (err) {
      setMsg(err?.message || "Failed to load shipyard catalog.", true);
      renderGarage();
      renderSlots();
      renderStats({});
    }
  }

  sourceLocationEl?.addEventListener("change", async () => {
    buildLocationId = String(sourceLocationEl.value || "LEO");
    selectedItemIds = [];
    updateSourceHint();
    await loadGarageForCurrentSource();
    renderGarage();
    renderSlots();
    await refreshPreview();
  });

  buildBtn?.addEventListener("click", buildShip);
  init();
})();
