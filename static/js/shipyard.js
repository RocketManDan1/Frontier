(function () {
  const garageEl = document.getElementById("shipyardGarage");
  const slotsEl = document.getElementById("shipyardSlots");
  const statsEl = document.getElementById("shipyardStats");
  const msgEl = document.getElementById("shipyardMsg");
  const buildBtn = document.getElementById("shipyardBuildBtn");
  const shipNameEl = document.getElementById("shipyardName");
  const sourceLocationEl = document.getElementById("shipyardSourceLocation");
  const sourceHintEl = document.getElementById("shipyardSourceHint");

  let buildLocationId = "LEO";
  let catalogParts = [];
  let garageParts = [];
  let selectedItemIds = [];
  let buildSourceLocations = [];
  let sourceRefreshTimer = null;

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

  function groupedGarageParts(parts) {
    const byGroup = new Map();
    for (const part of parts) {
      const group = String(part.category_id || part.type || "parts").toLowerCase();
      if (!byGroup.has(group)) byGroup.set(group, []);
      byGroup.get(group).push(part);
    }
    return Array.from(byGroup.entries()).sort((a, b) => a[0].localeCompare(b[0]));
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

      parts
        .slice()
        .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")))
        .forEach((part) => {
          const selectedCount = selectedCountByItemId(part.item_id);
          const availableQty = Number(part.available_qty);
          const isLimited = Number.isFinite(availableQty);
          const remaining = isLimited ? Math.max(0, Math.floor(availableQty - selectedCount)) : Infinity;

          const row = document.createElement("div");
          row.className = "shipyardPartRow";

          const label = document.createElement("div");
          label.className = "shipyardPartLabel";
          const remainingText = isLimited ? ` • ${remaining} available` : "";
          label.innerHTML = `<b>${part.name}</b><div class="muted small">${partDetailText(part)}${remainingText}</div>`;

          const addBtn = document.createElement("button");
          addBtn.type = "button";
          addBtn.className = "btnSecondary";
          addBtn.textContent = "Add";
          addBtn.disabled = isLimited && remaining <= 0;
          addBtn.addEventListener("click", () => {
            if (isLimited && remaining <= 0) return;
            selectedItemIds.push(part.item_id);
            renderSlots();
            renderGarage();
            refreshPreview();
          });

          row.appendChild(label);
          row.appendChild(addBtn);
          group.appendChild(row);
        });

      garageEl.appendChild(group);
    });
  }

  function renderSlots() {
    slotsEl.innerHTML = "";

    if (!selectedItemIds.length) {
      slotsEl.innerHTML = '<div class="shipyardSlotEmpty muted">No parts added yet. Use the garage to add parts.</div>';
      return;
    }

    selectedItemIds.forEach((itemId, index) => {
      const part = garageParts.find((p) => String(p.item_id || "") === String(itemId || ""));
      const row = document.createElement("div");
      row.className = "shipyardSlotRow";

      const left = document.createElement("div");
      left.className = "shipyardSlotLabel";
      left.innerHTML = `<b>${index + 1}.</b> ${part ? part.name : itemId}`;

      const controls = document.createElement("div");
      controls.className = "shipyardSlotControls";

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "btnSecondary";
      removeBtn.textContent = "Remove";
      removeBtn.addEventListener("click", () => {
        selectedItemIds.splice(index, 1);
        renderSlots();
        renderGarage();
        refreshPreview();
      });

      controls.appendChild(removeBtn);
      row.appendChild(left);
      row.appendChild(controls);
      slotsEl.appendChild(row);
    });
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
