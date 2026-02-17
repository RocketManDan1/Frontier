(function () {
  const summaryEl = document.getElementById("fleetSummary");
  const tbody = document.querySelector("#fleetTable tbody");
  const expandedShipIds = new Set();

  let serverSyncGameS = Date.now() / 1000;
  let clientSyncRealS = Date.now() / 1000;
  let timeScale = 1;

  function serverNow() {
    const realNow = Date.now() / 1000;
    return serverSyncGameS + (realNow - clientSyncRealS) * timeScale;
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
    const t = elapsed / span;
    return Math.max(0, Math.min(100, t * 100));
  }

  function cargoText(ship) {
    const notes = Array.isArray(ship.notes) ? ship.notes : [];
    const cargoNote = notes.find((n) => String(n).toLowerCase().startsWith("cargo:"));
    if (cargoNote) return String(cargoNote).slice(6).trim() || "—";
    return "—";
  }

  function partsText(ship) {
    const parts = Array.isArray(ship.parts) ? ship.parts.filter(Boolean) : [];
    if (!parts.length) return "—";
    return parts
      .map((part) => {
        if (typeof part === "string") return part;
        if (!part || typeof part !== "object") return "Part";
        const name = part.name || part.type || "Part";
        if (Number(part.water_kg) > 0) {
          return `${name} (${(Number(part.water_kg) / 1000).toFixed(1)} t water)`;
        }
        if (Number(part.thrust_kn) > 0 || Number(part.isp_s) > 0) {
          const thrust = Number(part.thrust_kn) > 0 ? `${Number(part.thrust_kn).toFixed(0)} kN` : null;
          const isp = Number(part.isp_s) > 0 ? `${Number(part.isp_s).toFixed(0)} s` : null;
          return `${name} (${[thrust, isp].filter(Boolean).join(", ")})`;
        }
        return name;
      })
      .join(", ");
  }

  function orderedPartsForStack(ship) {
    const parts = Array.isArray(ship.parts) ? ship.parts.slice() : [];
    const toName = (part) => {
      if (typeof part === "string") return part;
      if (!part || typeof part !== "object") return "Part";
      return String(part.name || part.type || "Part");
    };
    const isThruster = (part) => {
      if (typeof part === "string") return part.toLowerCase().includes("thruster");
      const type = String(part?.type || "").toLowerCase();
      const name = toName(part).toLowerCase();
      return type === "thruster" || name.includes("thruster");
    };

    const nonThrusters = parts.filter((part) => !isThruster(part));
    const thrusters = parts.filter((part) => isThruster(part));
    return nonThrusters.concat(thrusters);
  }

  function partsStackHtml(ship) {
    const ordered = orderedPartsForStack(ship);
    if (!ordered.length) return '<div class="partsStackEmpty">—</div>';

    const rowHtml = ordered.map((part, index) => {
      const rowNum = index + 1;
      return `<div class="partsStackRow"><span class="partsStackCell partsStackIndex">${rowNum}</span><span class="partsStackCell">${partsText({ parts: [part] })}</span></div>`;
    }).join("");

    return `<div class="partsStack"><div class="partsStackHead"><span class="partsStackCell partsStackIndex">#</span><span class="partsStackCell">Part</span></div>${rowHtml}</div>`;
  }

  function fuelText(ship) {
    const fuel = Number(ship.fuel_kg || 0);
    const cap = Number(ship.fuel_capacity_kg || 0);
    if (cap <= 0) return `${fuel.toFixed(0)} kg`;
    return `${fuel.toFixed(0)} / ${cap.toFixed(0)} kg`;
  }

  function deltaVText(ship) {
    const dv = Number(ship.delta_v_remaining_m_s || 0);
    return `${Math.max(0, dv).toFixed(0)} m/s`;
  }

  function moveOrderText(ship) {
    if (ship.status !== "transit") return "Docked (no active move order)";
    return (ship.transfer_path || []).length
      ? (ship.transfer_path || []).join(" → ")
      : `${ship.from_location_id || "?"} → ${ship.to_location_id || "?"}`;
  }

  function rowHtml(s) {
    const isExpanded = expandedShipIds.has(s.id);
    const status = s.status === "transit" ? "In Transit" : "Docked";
    const loc = s.status === "docked" ? (s.location_id || "—") : `${s.from_location_id || "?"} → ${s.to_location_id || "?"}`;
    const dv = s.dv_planned_m_s != null ? `${Math.round(s.dv_planned_m_s)} m/s` : "—";
    const path = (s.transfer_path || []).length ? (s.transfer_path || []).join(" → ") : "—";
    const progressPct = transitProgressPct(s);
    const etaCell = s.status === "transit" ? fmtEta(s) : "—";
    const progressDetails = s.status === "transit"
      ? `<div class="bar"><div class="barFill" style="width:${progressPct.toFixed(1)}%;"></div></div><div class="muted small" style="margin-top:4px;">${progressPct.toFixed(0)}% complete • ETA ${fmtEta(s)}</div>`
      : `<div class="muted small">No active transfer.</div>`;

    return `
      <tr class="fleetRow ${isExpanded ? "isExpanded" : ""}" data-ship-id="${s.id}">
        <td>
          <div class="fleetShipCell">
            <span class="fleetChevron" aria-hidden="true">▸</span>
            <div class="shipName">${s.name}</div>
          </div>
          <div class="muted small">${s.id}</div>
        </td>
        <td><span class="badge ${s.status === "transit" ? "badgeMove" : ""}">${status}</span></td>
        <td>${loc}</td>
        <td>${etaCell}</td>
        <td>${dv}</td>
        <td style="max-width:340px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${path}</td>
      </tr>
      <tr class="fleetDetailsRow ${isExpanded ? "isOpen" : ""}">
        <td colspan="6">
          <div class="fleetDetails">
            <div><b>Parts:</b> ${partsStackHtml(s)}</div>
            <div><b>Fuel remaining:</b> ${fuelText(s)}</div>
            <div><b>Δv remaining:</b> ${deltaVText(s)}</div>
            <div><b>Cargo:</b> ${cargoText(s)}</div>
            <div><b>Move order:</b> ${moveOrderText(s)}</div>
            <div class="fleetProgress">
              <div><b>Progress:</b></div>
              ${progressDetails}
            </div>
          </div>
        </td>
      </tr>
    `;
  }

  async function sync() {
    const tClient = Date.now() / 1000;
    const resp = await fetch("/api/state", { cache: "no-store" });
    const data = await resp.json();
    serverSyncGameS = data.server_time || tClient;
    clientSyncRealS = tClient;
    const parsedScale = Number(data.time_scale);
    timeScale = Number.isFinite(parsedScale) && parsedScale >= 0 ? parsedScale : 1;

    const ships = data.ships || [];
    const moving = ships.filter((s) => s.status === "transit").length;
    summaryEl.textContent = `${ships.length} ships • ${moving} in transit`;

    tbody.innerHTML = ships
      .slice()
      .sort((a, b) => String(a.name).localeCompare(String(b.name)))
      .map(rowHtml)
      .join("");
  }

  tbody?.addEventListener("click", (event) => {
    const row = event.target.closest("tr.fleetRow");
    if (!row) return;
    const shipId = row.getAttribute("data-ship-id");
    if (!shipId) return;
    if (expandedShipIds.has(shipId)) expandedShipIds.delete(shipId);
    else expandedShipIds.add(shipId);
    sync();
  });

  sync();
  setInterval(sync, 1000);
})();
