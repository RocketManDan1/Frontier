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

  function fuelPct(fuel, cap) {
    if (cap <= 0) return 0;
    return Math.max(0, Math.min(100, (fuel / cap) * 100));
  }

  function deltaVPanelHtml(ship) {
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
      <div class="powerBalancePanel powerBalanceFleet">
        <div class="pbTitle">Delta-v &amp; Propulsion</div>
        <div class="pbSection">
          <div class="pbSectionHead">Mass Budget</div>
          <div class="pbRow"><span class="pbLabel">Dry mass</span><span class="pbVal">${dryMass.toFixed(0)} kg</span></div>
          <div class="pbRow"><span class="pbLabel">Fuel</span><span class="pbVal">${fuel.toFixed(0)} / ${fuelCap.toFixed(0)} kg</span></div>
          <div class="pbRow"><span class="pbLabel">Fuel level</span><span class="pbVal"><span class="pbBarWrap"><span class="pbBar" style="width:${fPct.toFixed(1)}%"></span></span> ${fPct.toFixed(0)}%</span></div>
          <div class="pbRow pbDivider"><span class="pbLabel"><b>Wet mass</b></span><span class="pbVal"><b>${wetMass.toFixed(0)} kg</b></span></div>
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
    `;
  }

  function moveOrderText(ship) {
    if (ship.status !== "transit") return "Docked (no active move order)";
    return (ship.transfer_path || []).length
      ? (ship.transfer_path || []).join(" → ")
      : `${ship.from_location_id || "?"} → ${ship.to_location_id || "?"}`;
  }

  function fmtMw(v) {
    return `${Math.max(0, Number(v) || 0).toFixed(1)} MW`;
  }

  function powerBalanceHtml(ship) {
    const pb = ship.power_balance;
    if (!pb) return "";
    const reactorMw = Number(pb.reactor_thermal_mw || 0);
    const thrusterMw = Number(pb.thruster_thermal_mw || 0);
    const genInputMw = Number(pb.generator_thermal_mw_input || 0);
    const thermalSurplus = Number(pb.thermal_surplus_mw || 0);
    const electricMw = Number(pb.generator_electric_mw || 0);
    const genWaste = Number(pb.generator_waste_heat_mw || 0);
    const radRejection = Number(pb.radiator_heat_rejection_mw || 0);
    const wasteSurplus = Number(pb.waste_heat_surplus_mw || 0);
    const maxThrottle = Number(pb.max_throttle || 0);
    const hasAny = reactorMw > 0 || thrusterMw > 0 || genInputMw > 0 || radRejection > 0;
    if (!hasAny) return "";

    const thermalCls = thermalSurplus >= 0 ? "pbPositive" : "pbNegative";
    const wasteCls = wasteSurplus > 0 ? "pbNegative" : "pbPositive";
    const throttleCls = maxThrottle < 1 ? "pbNegative" : "pbPositive";

    return `
      <div class="powerBalancePanel powerBalanceFleet">
        <div class="pbTitle">Power &amp; Thermal Balance</div>
        <div class="pbSection">
          <div class="pbSectionHead">Thermal Budget</div>
          <div class="pbRow"><span class="pbLabel">Reactor output</span><span class="pbVal">${fmtMw(reactorMw)}</span></div>
          <div class="pbRow"><span class="pbLabel">Thruster demand</span><span class="pbVal">−${fmtMw(thrusterMw)}</span></div>
          <div class="pbRow"><span class="pbLabel">Generator input</span><span class="pbVal">−${fmtMw(genInputMw)}</span></div>
          <div class="pbRow pbDivider"><span class="pbLabel"><b>Surplus</b></span><span class="pbVal ${thermalCls}"><b>${thermalSurplus >= 0 ? "+" : ""}${thermalSurplus.toFixed(1)} MW</b></span></div>
          ${thrusterMw > 0 ? `<div class="pbRow"><span class="pbLabel">Max throttle</span><span class="pbVal ${throttleCls}">${(maxThrottle * 100).toFixed(0)}%</span></div>` : ""}
        </div>
        <div class="pbSection">
          <div class="pbSectionHead">Electric</div>
          <div class="pbRow"><span class="pbLabel">Generator output</span><span class="pbVal">${fmtMw(electricMw)}</span></div>
        </div>
        <div class="pbSection">
          <div class="pbSectionHead">Waste Heat</div>
          <div class="pbRow"><span class="pbLabel">Generator waste</span><span class="pbVal">${fmtMw(genWaste)}</span></div>
          <div class="pbRow"><span class="pbLabel">Radiator rejection</span><span class="pbVal">−${fmtMw(radRejection)}</span></div>
          <div class="pbRow pbDivider"><span class="pbLabel"><b>Unradiated</b></span><span class="pbVal ${wasteCls}"><b>${wasteSurplus >= 0 ? "+" : ""}${wasteSurplus.toFixed(1)} MW</b></span></div>
        </div>
      </div>
    `;
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
            ${deltaVPanelHtml(s)}
            ${powerBalanceHtml(s)}
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

    const newHtml = ships
      .slice()
      .sort((a, b) => String(a.name).localeCompare(String(b.name)))
      .map(rowHtml)
      .join("");
    if (tbody._lastHtml !== newHtml) {
      tbody.innerHTML = newHtml;
      tbody._lastHtml = newHtml;
    }
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
