(function () {
  const root = document.querySelector(".mapPage .content");
  const dock = document.querySelector(".mapPage .mapDock");
  const layer = document.getElementById("appWindowLayer");
  if (!root || !dock || !layer) return;

  const STORAGE_KEY = "earthmoon.appWindows.v1";
  const MIN_WIDTH = 380;
  const MIN_HEIGHT = 300;
  const TOP_OFFSET = 12;
  const HANGAR_WINDOW_EVENT = "earthmoon:open-hangar-window";

  const APP_CONFIG = {
    fleet: { title: "Fleet", url: "/fleet", icon: "/static/img/dock/fleet.png" },
    shipyard: { title: "Shipyard", url: "/shipyard", icon: "/static/img/dock/shipyard.png" },
    sites: { title: "Sites", url: "/sites", icon: "/static/img/dock/sites.svg" },
    research: { title: "Research", url: "/research", icon: "/static/img/dock/research.png" },
    profile: { title: "Profile", url: "/profile?embed=1", icon: "/static/img/dock/profile.png" },
    admin: { title: "Admin", url: "/admin", icon: "" },
  };

  const state = loadState();
  let openSequence = Object.values(state || {}).reduce((max, entry) => {
    const seq = Number(entry?.openSeq || 0);
    return Number.isFinite(seq) ? Math.max(max, seq) : max;
  }, 0);
  const windows = new Map();
  let zIndex = 80;
  let hangarLoadSeq = 0;
  const hangarWindowState = new Map();
  const DRAG_TRANSFER_MIME = "application/x-earthmoon-inventory-transfer";
  const itemDisplay = window.ItemDisplay || null;
  let inventoryContextMenuEl = null;
  const inventoryIconCache = new Map();
  let inventoryRefreshInFlight = null;
  let inventoryRefreshQueued = false;

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtKg(value) {
    const v = Math.max(0, Number(value) || 0);
    if (v >= 5000) return `${(v / 1000).toFixed(1)} t`;
    return `${v.toFixed(0)} kg`;
  }

  function fmtM3(value) {
    return `${Math.max(0, Number(value) || 0).toFixed(2)} m³`;
  }

  function errorDetailText(detail, fallback = "Request failed") {
    if (typeof detail === "string" && detail.trim()) return detail;

    if (Array.isArray(detail)) {
      const parts = detail.map((entry) => {
        if (typeof entry === "string") return entry;
        if (entry && typeof entry === "object") {
          const msg = String(entry.msg || entry.message || "").trim();
          const loc = Array.isArray(entry.loc) ? entry.loc.join(".") : "";
          if (msg && loc) return `${loc}: ${msg}`;
          if (msg) return msg;
          return loc ? `Invalid field: ${loc}` : "Invalid request";
        }
        return "";
      }).filter(Boolean);
      if (parts.length) return parts.join("; ");
    }

    if (detail && typeof detail === "object") {
      const msg = String(detail.message || detail.error || "").trim();
      if (msg) return msg;
      try {
        return JSON.stringify(detail);
      } catch {
        return fallback;
      }
    }

    return fallback;
  }

  function errorToText(error, fallback = "Request failed") {
    if (typeof error === "string" && error.trim()) return error;
    if (error && typeof error === "object") {
      const msg = String(error.message || "").trim();
      if (msg && msg !== "[object Object]") return msg;
      if ("detail" in error) return errorDetailText(error.detail, fallback);
      try {
        return JSON.stringify(error);
      } catch {
        return fallback;
      }
    }
    return fallback;
  }

  function hangarWindowId(shipId) {
    return `hangar:ship:${String(shipId || "")}`;
  }

  function entityKey(kind, id) {
    return `${String(kind || "").toLowerCase()}:${String(id || "")}`;
  }

  function hashCode(text) {
    let hash = 0;
    const source = String(text || "");
    for (let i = 0; i < source.length; i += 1) {
      hash = ((hash << 5) - hash + source.charCodeAt(i)) | 0;
    }
    return Math.abs(hash);
  }

  function itemGlyph(label) {
    const words = String(label || "")
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2);
    if (!words.length) return "?";
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return `${words[0][0] || ""}${words[1][0] || ""}`.toUpperCase();
  }

  function iconDataUri(seed, label) {
    if (itemDisplay && typeof itemDisplay.iconDataUri === "function") {
      return itemDisplay.iconDataUri(seed, label);
    }

    const cacheKey = `${String(seed || "")}::${String(label || "")}`;
    const cached = inventoryIconCache.get(cacheKey);
    if (cached) return cached;

    const hash = hashCode(seed || label || "item");
    const hue = hash % 360;
    const hue2 = (hue + 52) % 360;
    const glyph = itemGlyph(label);
    const svg = `
      <svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'>
        <defs>
          <linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
            <stop offset='0%' stop-color='hsl(${hue} 72% 46%)'/>
            <stop offset='100%' stop-color='hsl(${hue2} 72% 28%)'/>
          </linearGradient>
        </defs>
        <rect x='2' y='2' width='60' height='60' rx='10' fill='url(#g)'/>
        <rect x='2' y='2' width='60' height='60' rx='10' fill='none' stroke='rgba(220,238,255,0.26)'/>
        <text x='32' y='38' text-anchor='middle' font-family='Segoe UI,Roboto,sans-serif' font-size='20' fill='rgba(243,250,255,0.98)' font-weight='700'>${escapeHtml(glyph)}</text>
      </svg>
    `;
    const uri = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
    inventoryIconCache.set(cacheKey, uri);
    return uri;
  }

  function parseTransferPayload(raw) {
    if (!raw || typeof raw !== "string") return null;
    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return null;
      return parsed;
    } catch {
      return null;
    }
  }

  function ensureInventoryContextMenuEl() {
    if (inventoryContextMenuEl) return inventoryContextMenuEl;
    const el = document.createElement("div");
    el.className = "mapContextMenu";
    el.setAttribute("role", "menu");
    el.style.display = "none";
    document.body.appendChild(el);
    inventoryContextMenuEl = el;
    return el;
  }

  function hideInventoryContextMenu() {
    if (!inventoryContextMenuEl) return;
    inventoryContextMenuEl.classList.remove("isOpen");
    inventoryContextMenuEl.style.display = "none";
    inventoryContextMenuEl.innerHTML = "";
  }

  function showInventoryContextMenu(menuTitle, items, clientX, clientY) {
    const options = Array.isArray(items) ? items.filter(Boolean) : [];
    if (!options.length) {
      hideInventoryContextMenu();
      return;
    }

    const menu = ensureInventoryContextMenuEl();
    menu.innerHTML = "";

    if (menuTitle) {
      const title = document.createElement("div");
      title.className = "mapContextMenuTitle";
      title.textContent = menuTitle;
      menu.appendChild(title);
    }

    for (const item of options) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "mapContextMenuItem";
      btn.textContent = item.label || "Action";
      btn.disabled = !!item.disabled;
      btn.addEventListener("click", async () => {
        hideInventoryContextMenu();
        if (!item.disabled && typeof item.onClick === "function") await item.onClick();
      });
      menu.appendChild(btn);
    }

    menu.style.display = "block";
    menu.classList.add("isOpen");

    const vw = window.innerWidth || 0;
    const vh = window.innerHeight || 0;
    const rect = menu.getBoundingClientRect();
    const pad = 10;
    const left = Math.max(pad, Math.min(clientX, vw - rect.width - pad));
    const top = Math.max(pad, Math.min(clientY, vh - rect.height - pad));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  async function runShipInventoryAction(shipId, containerIndex, action) {
    const actionName = action === "deploy" ? "deploy" : "jettison";
    const resp = await fetch(`/api/ships/${encodeURIComponent(String(shipId || ""))}/inventory/${actionName}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ container_index: Number(containerIndex) || 0 }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(errorDetailText(data?.detail, "Inventory action failed"));
    return data;
  }

  function transferPayloadFromDragEvent(event) {
    const dt = event?.dataTransfer;
    if (!dt) return null;
    const primary = dt.getData(DRAG_TRANSFER_MIME);
    const parsedPrimary = parseTransferPayload(primary);
    if (parsedPrimary) return parsedPrimary;

    const plain = dt.getData("text/plain");
    const prefix = "earthmoon-transfer:";
    if (plain && plain.startsWith(prefix)) {
      return parseTransferPayload(plain.slice(prefix.length));
    }
    return null;
  }

  function renderInventoryItemCard(item) {
    const transfer = item?.transfer && typeof item.transfer === "object" ? item.transfer : null;
    const mass = fmtKg(item?.mass_kg);
    const vol = fmtM3(item?.volume_m3);
    const statsText = `${mass} · ${vol}`;
    const category = String(item?.category || item?.item_kind || "").trim();
    const phase = String(item?.phase || "").trim();
    const qty = Math.max(0, Number(item?.quantity) || 0);

    // Build tooltip lines from item metadata
    const tooltipLines = [];
    if (item?.thrust_kn) tooltipLines.push(["Thrust", `${Number(item.thrust_kn).toFixed(0)} kN`]);
    if (item?.isp_s) tooltipLines.push(["ISP", `${Number(item.isp_s).toFixed(0)} s`]);
    if (item?.capacity_m3) tooltipLines.push(["Capacity", fmtM3(item.capacity_m3)]);
    if (item?.power_mw) tooltipLines.push(["Power", `${Number(item.power_mw).toFixed(1)} MW`]);
    if (item?.resource_id) tooltipLines.push(["Resource", String(item.resource_id)]);

    const cell = itemDisplay && typeof itemDisplay.createGridCell === "function"
      ? itemDisplay.createGridCell({
          label: String(item?.label || "Item"),
          subtitle: String(item?.subtitle || item?.item_kind || ""),
          stats: statsText,
          iconSeed: item?.icon_seed || item?.item_uid || item?.item_id,
          category: category,
          phase: phase,
          mass_kg: item?.mass_kg,
          volume_m3: item?.volume_m3,
          quantity: qty > 1 ? qty : 0,
          draggable: !!transfer,
          tooltipLines: tooltipLines.length ? tooltipLines : undefined,
        })
      : (() => {
          const fallback = document.createElement("div");
          fallback.className = "invCell";
          if (transfer) {
            fallback.draggable = true;
            fallback.classList.add("isDraggable");
          }
          const icon = document.createElement("img");
          icon.className = "invCellIcon";
          icon.alt = String(item?.label || "Item");
          icon.src = iconDataUri(item?.icon_seed || item?.item_uid || item?.item_id, item?.label || item?.item_id);
          const lbl = document.createElement("div");
          lbl.className = "invCellLabel";
          lbl.textContent = String(item?.label || "Item");
          fallback.append(icon, lbl);
          return fallback;
        })();

    if (transfer) {
      const payload = {
        source_kind: String(transfer.source_kind || ""),
        source_id: String(transfer.source_id || ""),
        source_key: String(transfer.source_key || ""),
        amount: Math.max(0, Number(transfer.amount) || 0),
      };
      cell.__transferPayload = payload;
      cell.dataset.transferPayload = JSON.stringify(payload);

      cell.addEventListener("dragstart", (event) => {
        if (!event.dataTransfer) return;
        const json = JSON.stringify(payload);
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData(DRAG_TRANSFER_MIME, json);
        event.dataTransfer.setData("text/plain", `earthmoon-transfer:${json}`);
        cell.classList.add("isDragging");
      });
      cell.addEventListener("dragend", () => {
        cell.classList.remove("isDragging");
        root.querySelectorAll(".inventoryDropZone.isOver").forEach((el) => el.classList.remove("isOver"));
      });
    }

    if (String(item?.item_kind || "").toLowerCase() === "container") {
      cell.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        const transferSource = item?.transfer && typeof item.transfer === "object" ? item.transfer : null;
        const shipId = String(transferSource?.source_id || "").trim();
        const rawIdx = Number(item?.container_index ?? transferSource?.source_key);
        const containerIndex = Number.isFinite(rawIdx) ? rawIdx : -1;
        const cargoMass = Math.max(0, Number(item?.mass_kg) || 0);
        const canAct = !!shipId && containerIndex >= 0;

        showInventoryContextMenu(String(item?.label || "Container"), [
          {
            label: "Deploy container",
            disabled: !canAct,
            onClick: async () => {
              const ok = window.confirm("Deploy this container with its cargo?");
              if (!ok) return;
              await runShipInventoryAction(shipId, containerIndex, "deploy");
              await refreshAllHangarWindows();
            },
          },
          {
            label: cargoMass > 0 ? "Jettison cargo" : "Jettison cargo (empty)",
            disabled: !canAct || cargoMass <= 0,
            onClick: async () => {
              await runShipInventoryAction(shipId, containerIndex, "jettison");
              await refreshAllHangarWindows();
            },
          },
        ], event.clientX, event.clientY);
      });
    }

    return cell;
  }

  function renderInventoryContainerGroups(selectedInventory, onDropToContainer = null) {
    const groups = Array.isArray(selectedInventory?.container_groups) ? selectedInventory.container_groups : [];
    const list = document.createElement("div");
    list.className = "inventoryContainerGroupList";

    if (!groups.length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "No cargo containers in this ship.";
      list.appendChild(empty);
      return list;
    }

    groups.forEach((group) => {
      const section = document.createElement("section");
      section.className = "inventoryContainerGroup inventoryDropZone";

      const head = document.createElement("div");
      head.className = "inventoryContainerGroupHead";

      const title = document.createElement("div");
      title.className = "inventoryContainerGroupTitle";
      title.textContent = String(group?.name || "Container");

      const phase = String(group?.phase || "solid");
      const used = Math.max(0, Number(group?.used_m3) || 0);
      const cap = Math.max(0, Number(group?.capacity_m3) || 0);
      const sub = document.createElement("div");
      sub.className = "inventoryContainerGroupSub";
      sub.textContent = `${phase[0].toUpperCase()}${phase.slice(1)} · ${fmtM3(used)} / ${fmtM3(cap)}`;

      head.append(title, sub);

      const itemsWrap = document.createElement("div");
      itemsWrap.className = "inventoryContainerItems";
      const items = Array.isArray(group?.items) ? group.items : [];
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "muted small";
        empty.textContent = "Empty";
        itemsWrap.appendChild(empty);
      } else {
        items.forEach((item) => {
          itemsWrap.appendChild(renderInventoryItemCard(item));
        });
      }

      section.append(head, itemsWrap);

      if (typeof onDropToContainer === "function") {
        const containerIndex = Number(group?.container_index);
        if (Number.isFinite(containerIndex) && containerIndex >= 0) {
          section.dataset.targetKind = "ship_container";
          section.dataset.targetId = String(selectedInventory?.id || "");
          section.dataset.targetKey = String(containerIndex);
          setDropZoneBehavior(section, async (payload) => {
            await onDropToContainer(payload, containerIndex);
          });
        }
      }

      list.appendChild(section);
    });

    return list;
  }

  function renderInventoryCapacitySummary(summary) {
    const payload = summary && typeof summary === "object" ? summary : null;
    if (!payload) return null;

    const used = Math.max(0, Number(payload.used_m3) || 0);
    const cap = Math.max(0, Number(payload.capacity_m3) || 0);
    const pct = cap > 0 ? Math.max(0, Math.min(100, (used / cap) * 100)) : 0;
    const byPhase = payload.by_phase && typeof payload.by_phase === "object" ? payload.by_phase : {};

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
    phases.textContent = ["solid", "liquid", "gas"].map((phase) => {
      const row = byPhase[phase] || {};
      const pUsed = Math.max(0, Number(row.used_m3) || 0);
      const pCap = Math.max(0, Number(row.capacity_m3) || 0);
      return `${phase[0].toUpperCase()}${phase.slice(1)} ${fmtM3(pUsed)} / ${fmtM3(pCap)}`;
    }).join(" · ");

    wrap.append(line, bar, phases);
    return wrap;
  }

  async function requestHangarContext(shipId) {
    const resp = await fetch(`/api/hangar/context/${encodeURIComponent(String(shipId || ""))}`, {
      cache: "no-store",
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data?.detail || "Failed to load hangar context");
    }
    return data;
  }

  async function runInventoryTransfer(payload, targetKind, targetId, targetKey = null) {
    const sourceKind = String(payload?.source_kind || "").trim();
    const sourceId = String(payload?.source_id || "").trim();
    const sourceKey = String(payload?.source_key || "").trim();
    const destKind = String(targetKind || "").trim();
    const destId = String(targetId || "").trim();
    const destKey = targetKey == null ? "" : String(targetKey).trim();

    const missing = [];
    if (!sourceKind) missing.push("source_kind");
    if (!sourceId) missing.push("source_id");
    if (!sourceKey) missing.push("source_key");
    if (!destKind) missing.push("target_kind");
    if (!destId) missing.push("target_id");
    if (destKind === "ship_container" && !destKey) missing.push("target_key");
    if (missing.length) {
      throw new Error(`Transfer payload missing: ${missing.join(", ")}`);
    }

    const resp = await fetch("/api/inventory/transfer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_kind: sourceKind,
        source_id: sourceId,
        source_key: sourceKey,
        amount: Math.max(0, Number(payload?.amount) || 0),
        target_kind: destKind,
        target_id: destId,
        target_key: destKey || undefined,
      }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(errorDetailText(data?.detail, "Inventory transfer failed"));
    return data;
  }

  function maybeWarnDestroyedTransfer(result) {
    const destroyed = Math.max(0, Number(result?.destroyed_mass_kg) || 0);
    if (destroyed <= 1e-9) return;
    const resourceId = String(result?.resource_id || "resource").trim() || "resource";
    window.alert(`${fmtKg(destroyed)} of ${resourceId} was lost in space (not containerized).`);
  }

  function transferAmountStep(maxAmount) {
    const max = Math.max(0, Number(maxAmount) || 0);
    if (max <= 1) return 0.01;
    if (max <= 10) return 0.05;
    if (max <= 100) return 0.1;
    if (max <= 1000) return 1;
    if (max <= 10000) return 10;
    return 100;
  }

  function clampTransferAmount(value, maxAmount) {
    const max = Math.max(0, Number(maxAmount) || 0);
    const n = Number(value);
    if (!Number.isFinite(n)) return max;
    return Math.max(0, Math.min(max, n));
  }

  function shouldPromptInventoryTransferAmount(payload) {
    const sourceKind = String(payload?.source_kind || "").trim().toLowerCase();
    if (!sourceKind) return false;
    if (!new Set(["ship_container", "ship_resource", "location_resource"]).has(sourceKind)) return false;
    const maxAmount = Math.max(0, Number(payload?.amount) || 0);
    return maxAmount > 1e-9;
  }

  function amountDecimals(step) {
    const s = Math.max(0.000001, Number(step) || 1);
    if (s >= 1) return 0;
    if (s >= 0.1) return 1;
    if (s >= 0.01) return 2;
    return 3;
  }

  async function requestInventoryTransferAmount(payload, targetKind, targetId) {
    if (!shouldPromptInventoryTransferAmount(payload)) {
      return Math.max(0, Number(payload?.amount) || 0);
    }

    const sourceKind = String(payload?.source_kind || "").trim().toLowerCase();
    const maxAmount = Math.max(0, Number(payload?.amount) || 0);
    const step = transferAmountStep(maxAmount);
    const decimals = amountDecimals(step);

    const sourceLabel = sourceKind === "location_resource"
      ? "Site inventory"
      : "Ship cargo";
    const targetLabel = String(targetKind || "").trim().toLowerCase() === "location"
      ? "Site inventory"
      : "Ship cargo";

    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "inventoryAmountOverlay";

      const dialog = document.createElement("div");
      dialog.className = "inventoryAmountDialog";
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-modal", "true");

      const title = document.createElement("div");
      title.className = "inventoryAmountTitle";
      title.textContent = "Transfer amount";

      const sub = document.createElement("div");
      sub.className = "inventoryAmountSub";
      sub.textContent = `${sourceLabel} → ${targetLabel} (${String(targetId || "").trim() || "target"})`;

      const status = document.createElement("div");
      status.className = "inventoryAmountStatus";

      const slider = document.createElement("input");
      slider.className = "inventoryAmountSlider";
      slider.type = "range";
      slider.min = "0";
      slider.max = String(maxAmount);
      slider.step = String(step);
      slider.value = String(maxAmount);

      const input = document.createElement("input");
      input.className = "inventoryAmountInput";
      input.type = "number";
      input.min = "0";
      input.max = String(maxAmount);
      input.step = String(step);
      input.value = maxAmount.toFixed(decimals);

      const actions = document.createElement("div");
      actions.className = "inventoryAmountActions";

      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "btn btnGhost";
      cancelBtn.textContent = "Cancel";

      const moveBtn = document.createElement("button");
      moveBtn.type = "button";
      moveBtn.className = "btn";
      moveBtn.textContent = "Transfer";

      let amount = maxAmount;
      const sync = (next, fromSlider = false) => {
        amount = clampTransferAmount(next, maxAmount);
        status.textContent = `${fmtKg(amount)} selected of ${fmtKg(maxAmount)} available`;
        if (!fromSlider) slider.value = String(amount);
        input.value = amount.toFixed(decimals);
        moveBtn.disabled = amount <= 1e-9;
      };

      const finish = (value) => {
        document.removeEventListener("keydown", onKeyDown, true);
        overlay.remove();
        resolve(value);
      };

      const onKeyDown = (event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        finish(null);
      };

      slider.addEventListener("input", (event) => {
        sync(Number(event?.target?.value), true);
      });
      input.addEventListener("input", (event) => {
        sync(Number(event?.target?.value), false);
      });
      cancelBtn.addEventListener("click", () => finish(null));
      moveBtn.addEventListener("click", () => finish(amount));
      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) finish(null);
      });

      actions.append(cancelBtn, moveBtn);
      dialog.append(title, sub, status, slider, input, actions);
      overlay.appendChild(dialog);
      document.body.appendChild(overlay);

      document.addEventListener("keydown", onKeyDown, true);
      sync(maxAmount, false);
      input.focus({ preventScroll: true });
      input.select();
    });
  }

  // ─── Hangar: unified workspace ───────────────────────────────

  function selectedEntityFromRecord(record) {
    const context = record?.context;
    const entities = Array.isArray(context?.entities) ? context.entities : [];
    if (!entities.length) return null;

    const selectedKey = String(record?.selectedEntityKey || "");
    const selected = entities.find((e) => entityKey(e?.entity_kind, e?.id) === selectedKey);
    return selected || entities[0] || null;
  }

  function fmtMw(v) {
    return `${Math.max(0, Number(v) || 0).toFixed(1)} MW`;
  }

  function fmtMs(v) {
    return `${Math.max(0, Number(v) || 0).toFixed(0)} m/s`;
  }

  function fmtGs(v) {
    return `${Math.max(0, Number(v) || 0).toFixed(3)} g`;
  }

  function fuelPctVal(fuel, cap) {
    if (cap <= 0) return 0;
    return Math.max(0, Math.min(100, (fuel / cap) * 100));
  }

  function renderModulesSection(entity) {
    const section = document.createElement("div");
    section.className = "hangarSection";

    const head = document.createElement("div");
    head.className = "hangarSectionHead";
    head.textContent = "Installed Modules";
    section.appendChild(head);

    const stackItems = Array.isArray(entity?.stack_items) ? entity.stack_items : [];
    if (!stackItems.length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = entity?.entity_kind === "location" ? "No parts in site inventory." : "No modules installed.";
      section.appendChild(empty);
      return section;
    }

    const grid = document.createElement("div");
    grid.className = "hangarModulesGrid";

    stackItems.forEach((item) => {
      const cell = renderInventoryItemCard(item);
      cell.classList.add("hangarModuleCard");
      grid.appendChild(cell);
    });

    section.appendChild(grid);
    return section;
  }

  function renderDeltaVSection(stats) {
    if (!stats) return null;
    const section = document.createElement("div");
    section.className = "hangarSection";

    const dryMass = Number(stats.dry_mass_kg || 0);
    const fuel = Number(stats.fuel_kg || 0);
    const fuelCap = Number(stats.fuel_capacity_kg || 0);
    const wetMass = Number(stats.wet_mass_kg || 0);
    const isp = Number(stats.isp_s || 0);
    const thrust = Number(stats.thrust_kn || 0);
    const dv = Number(stats.delta_v_remaining_m_s || 0);
    const accelG = Number(stats.accel_g || 0);
    const fPct = fuelPctVal(fuel, fuelCap);
    const dvCls = dv > 0 ? "pbPositive" : "pbNeutral";

    section.innerHTML = `
      <div class="powerBalancePanel">
        <div class="pbTitle">Delta-v &amp; Propulsion</div>
        <div class="pbSection">
          <div class="pbSectionHead">Mass Budget</div>
          <div class="pbRow"><span class="pbLabel">Dry mass</span><span class="pbVal">${fmtKg(dryMass)}</span></div>
          <div class="pbRow"><span class="pbLabel">Fuel</span><span class="pbVal">${fmtKg(fuel)} / ${fmtKg(fuelCap)}</span></div>
          <div class="pbRow"><span class="pbLabel">Fuel level</span><span class="pbVal"><span class="pbBarWrap"><span class="pbBar" style="width:${fPct.toFixed(1)}%"></span></span> ${fPct.toFixed(0)}%</span></div>
          <div class="pbRow pbDivider"><span class="pbLabel"><b>Wet mass</b></span><span class="pbVal"><b>${fmtKg(wetMass)}</b></span></div>
        </div>
        <div class="pbSection">
          <div class="pbSectionHead">Propulsion</div>
          <div class="pbRow"><span class="pbLabel">Thrust</span><span class="pbVal">${thrust.toFixed(0)} kN</span></div>
          <div class="pbRow"><span class="pbLabel">Specific impulse</span><span class="pbVal">${isp.toFixed(0)} s</span></div>
          <div class="pbRow"><span class="pbLabel">Acceleration</span><span class="pbVal">${fmtGs(accelG)}</span></div>
        </div>
        <div class="pbSection">
          <div class="pbSectionHead">Delta-v</div>
          <div class="pbRow pbDivider"><span class="pbLabel"><b>Δv remaining</b></span><span class="pbVal ${dvCls}"><b>${fmtMs(dv)}</b></span></div>
        </div>
      </div>
    `;
    return section;
  }

  function renderPowerBalanceSection(pb) {
    if (!pb) return null;
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
    if (!hasAny) return null;

    const thermalCls = thermalSurplus >= 0 ? "pbPositive" : "pbNegative";
    const wasteCls = wasteSurplus > 0 ? "pbNegative" : "pbPositive";
    const throttleCls = maxThrottle < 1 ? "pbNegative" : "pbPositive";

    const section = document.createElement("div");
    section.className = "hangarSection";
    section.innerHTML = `
      <div class="powerBalancePanel">
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
    return section;
  }

  function renderCargoSection(entity, onDropToContainer, onTransferClick) {
    const isShip = entity?.entity_kind === "ship";
    const containerGroups = Array.isArray(entity?.container_groups) ? entity.container_groups : [];
    const invItems = Array.isArray(entity?.inventory_items) ? entity.inventory_items : [];
    const hasContainers = isShip && containerGroups.length > 0;
    const hasItems = invItems.length > 0;

    if (!hasContainers && !hasItems) return null;

    const section = document.createElement("div");
    section.className = "hangarSection";

    const head = document.createElement("div");
    head.className = "hangarSectionHead";
    head.textContent = isShip ? "Cargo Hold" : "Site Resources";
    section.appendChild(head);

    // Capacity summary for ships
    if (isShip) {
      const capEl = renderInventoryCapacitySummary(entity?.capacity_summary);
      if (capEl) section.appendChild(capEl);
    }

    if (hasContainers) {
      const containerList = renderInventoryContainerGroups(
        { ...entity, id: entity?.id },
        onDropToContainer,
      );
      section.appendChild(containerList);

      // Add transfer buttons to cargo items
      if (typeof onTransferClick === "function") {
        containerList.querySelectorAll(".invCell").forEach((cell) => {
          const transferBtn = document.createElement("button");
          transferBtn.type = "button";
          transferBtn.className = "hangarTransferBtn";
          transferBtn.textContent = "Transfer";
          transferBtn.title = "Transfer this cargo to another ship or location";
          transferBtn.addEventListener("click", (event) => {
            event.stopPropagation();
            onTransferClick(cell);
          });
          cell.appendChild(transferBtn);
        });
      }
    } else if (hasItems) {
      const grid = document.createElement("div");
      grid.className = "inventoryItemGrid";
      invItems.forEach((item) => {
        const card = renderInventoryItemCard(item);
        if (typeof onTransferClick === "function" && item?.transfer) {
          const transferBtn = document.createElement("button");
          transferBtn.type = "button";
          transferBtn.className = "hangarTransferBtn";
          transferBtn.textContent = "Transfer";
          transferBtn.title = "Transfer this resource";
          transferBtn.addEventListener("click", (event) => {
            event.stopPropagation();
            onTransferClick(card);
          });
          card.appendChild(transferBtn);
        }
        grid.appendChild(card);
      });
      section.appendChild(grid);
    }

    return section;
  }

  function buildHangarWorkspace(panel, windowId) {
    const record = hangarWindowState.get(windowId);
    if (!record) return;
    const body = panel.querySelector("[data-window-body='true']");
    if (!body) return;
    const context = record.context;

    const selectedEntity = selectedEntityFromRecord(record);
    if (!selectedEntity) {
      body.innerHTML = '<div class="muted">No data found in this context.</div>';
      return;
    }

    const layout = document.createElement("div");
    layout.className = "hangarWorkspace";

    // ── Left sidebar: entity index ──
    const indexPane = document.createElement("aside");
    indexPane.className = "hangarIndexPane";

    const indexTitle = document.createElement("div");
    indexTitle.className = "inventoryIndexTitle";
    indexTitle.textContent = "Docked";
    indexPane.appendChild(indexTitle);

    const indexList = document.createElement("div");
    indexList.className = "inventoryIndexList";

    const entities = Array.isArray(context?.entities) ? context.entities : [];
    entities.forEach((entity) => {
      const eKind = String(entity?.entity_kind || "").toLowerCase();
      const eId = String(entity?.id || "");
      const key = entityKey(eKind, eId);

      const row = document.createElement("div");
      row.className = "inventoryIndexRow inventoryDropZone";
      row.setAttribute("role", "button");
      row.setAttribute("tabindex", "0");
      row.dataset.targetKind = eKind;
      row.dataset.targetId = eId;
      if (key === record.selectedEntityKey) row.classList.add("isActive");

      const rowTitle = document.createElement("div");
      rowTitle.className = "inventoryIndexRowTitle";
      rowTitle.textContent = String(entity?.name || eId || "Entity");

      const rowSub = document.createElement("div");
      rowSub.className = "inventoryIndexRowSub";

      if (eKind === "ship") {
        const dv = Number(entity?.stats?.delta_v_remaining_m_s || 0);
        const partCount = Array.isArray(entity?.stack_items) ? entity.stack_items.length : 0;
        rowSub.textContent = `Ship · ${partCount} modules · Δv ${Math.max(0, dv).toFixed(0)} m/s`;
      } else {
        rowSub.textContent = "Location storage";
      }

      row.append(rowTitle, rowSub);

      let rowClickTimer = null;
      row.addEventListener("click", (event) => {
        if (event.detail > 1) return;
        if (rowClickTimer) window.clearTimeout(rowClickTimer);
        rowClickTimer = window.setTimeout(() => {
          rowClickTimer = null;
          record.selectedEntityKey = key;
          buildHangarWorkspace(panel, windowId);
        }, 220);
      });
      row.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        record.selectedEntityKey = key;
        buildHangarWorkspace(panel, windowId);
      });
      row.addEventListener("dblclick", (event) => {
        event.preventDefault();
        if (rowClickTimer) {
          window.clearTimeout(rowClickTimer);
          rowClickTimer = null;
        }
        if (eKind === "ship") {
          openHangarWorkspace(eId, String(entity?.name || eId));
        }
      });

      // Drop zone for cargo transfers
      setDropZoneBehavior(row, async (payload) => {
        const sk = String(payload?.source_kind || "");
        if (sk === "ship_part" || sk === "location_part") {
          await runStackTransfer(payload, eKind, eId);
        } else {
          const chosenAmount = await requestInventoryTransferAmount(payload, eKind, eId);
          if (chosenAmount == null) return;
          const result = await runInventoryTransfer({ ...payload, amount: chosenAmount }, eKind, eId);
          maybeWarnDestroyedTransfer(result);
        }
        await refreshAllHangarWindows();
      });

      indexList.appendChild(row);
    });

    indexPane.appendChild(indexList);

    // ── Main pane: scrollable sections ──
    const mainPane = document.createElement("section");
    mainPane.className = "hangarMainPane inventoryDropZone";

    // Header
    const mainHead = document.createElement("div");
    mainHead.className = "hangarMainHead";

    const hTitle = document.createElement("div");
    hTitle.className = "inventoryMainTitle";
    hTitle.textContent = String(selectedEntity?.name || "Ship");

    const hSub = document.createElement("div");
    hSub.className = "inventoryMainSub";
    const locName = String(context?.location?.name || context?.location?.id || "Unknown");
    const isShip = String(selectedEntity?.entity_kind || "").toLowerCase() === "ship";
    const moduleCount = Array.isArray(selectedEntity?.stack_items) ? selectedEntity.stack_items.length : 0;
    if (isShip) {
      const dv = Number(selectedEntity?.stats?.delta_v_remaining_m_s || 0);
      hSub.textContent = `${locName} · ${moduleCount} modules · Δv ${Math.max(0, dv).toFixed(0)} m/s`;
    } else {
      hSub.textContent = `${locName} · ${moduleCount} parts`;
    }

    mainHead.append(hTitle, hSub);
    mainPane.appendChild(mainHead);

    // Sections container (scrollable)
    const sections = document.createElement("div");
    sections.className = "hangarSections";

    // 1. Modules section
    sections.appendChild(renderModulesSection(selectedEntity));

    // 2. Delta-v section (ships only)
    if (isShip) {
      const dvSection = renderDeltaVSection(selectedEntity?.stats);
      if (dvSection) sections.appendChild(dvSection);
    }

    // 3. Power balance section (ships only)
    if (isShip) {
      const pbSection = renderPowerBalanceSection(selectedEntity?.power_balance);
      if (pbSection) sections.appendChild(pbSection);
    }

    // 4. Cargo section
    const cargoDropHandler = isShip ? async (payload, containerIndex) => {
      const chosenAmount = await requestInventoryTransferAmount(payload, "ship_container", selectedEntity?.id);
      if (chosenAmount == null) return;
      const result = await runInventoryTransfer(
        { ...payload, amount: chosenAmount },
        "ship_container",
        selectedEntity?.id,
        String(containerIndex),
      );
      maybeWarnDestroyedTransfer(result);
      await refreshAllHangarWindows();
    } : null;

    const transferClickHandler = (cellEl) => {
      // Build transfer target menu from sibling entities
      const targets = entities.filter((e) => {
        const eKey = entityKey(e?.entity_kind, e?.id);
        const selKey = entityKey(selectedEntity?.entity_kind, selectedEntity?.id);
        return eKey !== selKey;
      });
      if (!targets.length) {
        window.alert("No other ships or locations to transfer to.");
        return;
      }

      // Extract drag payload from cell
      const dragPayloadStr = cellEl?.dataset?.transferPayload;
      let dragPayload = null;
      if (dragPayloadStr) {
        try { dragPayload = JSON.parse(dragPayloadStr); } catch {}
      }
      // Fallback: try to reconstruct from cell's drag event setup
      if (!dragPayload) {
        // Try finding transfer data from cell's item data
        const cellData = cellEl?.__transferPayload;
        if (cellData) dragPayload = cellData;
      }

      if (!dragPayload) {
        window.alert("Unable to determine transfer source. Try dragging instead.");
        return;
      }

      const menuItems = targets.map((t) => ({
        label: `${String(t.name || t.id)} (${t.entity_kind})`,
        onClick: async () => {
          const tKind = String(t.entity_kind || "").toLowerCase();
          const tId = String(t.id || "");
          const sk = String(dragPayload.source_kind || "");
          if (sk === "ship_part" || sk === "location_part") {
            await runStackTransfer(dragPayload, tKind, tId);
          } else {
            const chosenAmount = await requestInventoryTransferAmount(dragPayload, tKind, tId);
            if (chosenAmount == null) return;
            const result = await runInventoryTransfer({ ...dragPayload, amount: chosenAmount }, tKind, tId);
            maybeWarnDestroyedTransfer(result);
          }
          await refreshAllHangarWindows();
        },
      }));

      const rect = cellEl.getBoundingClientRect();
      showInventoryContextMenu("Transfer to…", menuItems, rect.right, rect.top);
    };

    const cargoSection = renderCargoSection(selectedEntity, cargoDropHandler, transferClickHandler);
    if (cargoSection) sections.appendChild(cargoSection);

    mainPane.appendChild(sections);

    // Main pane drop zone (for general drops)
    const selKind = String(selectedEntity?.entity_kind || "").toLowerCase();
    const selId = String(selectedEntity?.id || "");
    mainPane.dataset.targetKind = selKind;
    mainPane.dataset.targetId = selId;
    setDropZoneBehavior(mainPane, async (payload) => {
      const sk = String(payload?.source_kind || "");
      if (sk === "ship_part" || sk === "location_part") {
        await runStackTransfer(payload, selKind, selId);
      } else {
        const chosenAmount = await requestInventoryTransferAmount(payload, selKind, selId);
        if (chosenAmount == null) return;
        const result = await runInventoryTransfer({ ...payload, amount: chosenAmount }, selKind, selId);
        maybeWarnDestroyedTransfer(result);
      }
      await refreshAllHangarWindows();
    });

    layout.append(indexPane, mainPane);
    body.innerHTML = "";
    body.appendChild(layout);
  }

  async function refreshHangarWindow(windowId) {
    const panel = windows.get(windowId);
    const record = hangarWindowState.get(windowId);
    if (!panel || !record) return;

    const body = panel.querySelector("[data-window-body='true']");
    if (!body) return;
    const requestId = ++hangarLoadSeq;
    panel.dataset.hangarRequestId = String(requestId);
    const hasRenderedContext = !!record.context;
    if (!hasRenderedContext) {
      body.innerHTML = '<div class="muted">Loading hangar…</div>';
    }

    try {
      const context = await requestHangarContext(record.anchorId);
      if (panel.dataset.hangarRequestId !== String(requestId)) return;
      record.context = context;
      if (!record.selectedEntityKey) {
        record.selectedEntityKey = entityKey("ship", record.anchorId);
      }
      buildHangarWorkspace(panel, windowId);
    } catch (error) {
      if (panel.dataset.hangarRequestId !== String(requestId)) return;
      body.innerHTML = `<div class="muted">${escapeHtml(String(error?.message || "Failed to load hangar."))}</div>`;
    }
  }

  let hangarRefreshInFlight = null;
  let hangarRefreshQueued = false;

  async function refreshAllHangarWindows() {
    if (hangarRefreshInFlight) {
      hangarRefreshQueued = true;
      return hangarRefreshInFlight;
    }

    hangarRefreshInFlight = (async () => {
      do {
        hangarRefreshQueued = false;
        const ids = Array.from(hangarWindowState.keys());
        await Promise.all(ids.map((windowId) => refreshHangarWindow(windowId)));
      } while (hangarRefreshQueued);
    })().finally(() => {
      hangarRefreshInFlight = null;
    });

    return hangarRefreshInFlight;
  }

  async function runStackTransfer(payload, targetKind, targetId) {
    const sourceKind = String(payload?.source_kind || "").trim();
    const sourceId = String(payload?.source_id || "").trim();
    const sourceKey = String(payload?.source_key || "").trim();
    const destKind = String(targetKind || "").trim();
    const destId = String(targetId || "").trim();

    const missing = [];
    if (!sourceKind) missing.push("source_kind");
    if (!sourceId) missing.push("source_id");
    if (!sourceKey) missing.push("source_key");
    if (!destKind) missing.push("target_kind");
    if (!destId) missing.push("target_id");
    if (missing.length) {
      throw new Error(`Transfer payload missing: ${missing.join(", ")}`);
    }

    const resp = await fetch("/api/stack/transfer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_kind: sourceKind,
        source_id: sourceId,
        source_key: sourceKey,
        target_kind: destKind,
        target_id: destId,
      }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(errorDetailText(data?.detail, "Stack transfer failed"));
    return data;
  }

  function setDropZoneBehavior(zoneEl, onDrop) {
    if (!zoneEl || typeof onDrop !== "function") return;

    const markOver = () => zoneEl.classList.add("isOver");
    const clearOver = () => zoneEl.classList.remove("isOver");

    zoneEl.addEventListener("dragenter", (event) => {
      const payload = transferPayloadFromDragEvent(event);
      if (!payload) return;
      event.preventDefault();
      markOver();
    });

    zoneEl.addEventListener("dragover", (event) => {
      const payload = transferPayloadFromDragEvent(event);
      if (!payload) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      markOver();
    });

    zoneEl.addEventListener("dragleave", (event) => {
      const nextTarget = event.relatedTarget;
      if (nextTarget && zoneEl.contains(nextTarget)) return;
      clearOver();
    });

    zoneEl.addEventListener("drop", async (event) => {
      clearOver();
      const payload = transferPayloadFromDragEvent(event);
      if (!payload) return;
      event.preventDefault();
      event.stopPropagation();
      try {
        await onDrop(payload);
      } catch (error) {
        console.error(error);
        const message = errorToText(error, "Inventory transfer failed");
        window.alert(message);
      }
    });
  }


  document.addEventListener("pointerdown", (event) => {
    if (!inventoryContextMenuEl || inventoryContextMenuEl.style.display === "none") return;
    if (inventoryContextMenuEl.contains(event.target)) return;
    hideInventoryContextMenu();
  });
  window.addEventListener("blur", hideInventoryContextMenu);
  window.addEventListener("resize", hideInventoryContextMenu);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideInventoryContextMenu();
  });

  function bootstrapDockButton(btn, iconSrc) {
    if (!btn) return;
    if (!btn.querySelector(".mapDockBtnLabel")) {
      const labelText = (btn.textContent || "").trim();
      btn.textContent = "";
      const iconEl = document.createElement("span");
      iconEl.className = "mapDockBtnIcon";
      iconEl.setAttribute("aria-hidden", "true");
      const labelEl = document.createElement("span");
      labelEl.className = "mapDockBtnLabel";
      labelEl.textContent = labelText;
      btn.append(iconEl, labelEl);
    }
    if (iconSrc) {
      btn.classList.add("hasIcon");
      btn.style.setProperty("--dock-icon", `url(\"${iconSrc}\")`);
    }
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function loadState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  function saveState() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // Ignore storage failures.
    }
  }

  function dockButtonFor(id) {
    return dock.querySelector(`[data-app-window='${id}']`);
  }

  function setDockOpen(id, open) {
    const btn = dockButtonFor(id);
    if (!btn) return;
    btn.classList.toggle("isOpen", !!open);
    btn.setAttribute("aria-pressed", open ? "true" : "false");
  }

  function contentLimits() {
    const rect = root.getBoundingClientRect();
    return {
      maxWidth: Math.max(MIN_WIDTH, rect.width - 10),
      maxHeight: Math.max(MIN_HEIGHT, rect.height - TOP_OFFSET - 8),
      minLeft: 0,
      maxLeft: rect.width,
      minTop: TOP_OFFSET,
      maxTop: rect.height,
    };
  }

  function clampRect(rect) {
    const limits = contentLimits();
    const width = clamp(rect.width, MIN_WIDTH, limits.maxWidth);
    const height = clamp(rect.height, MIN_HEIGHT, limits.maxHeight);
    const left = clamp(rect.left, limits.minLeft, limits.maxLeft - width);
    const top = clamp(rect.top, limits.minTop, limits.maxTop - height);
    return { left, top, width, height };
  }

  function applyRect(el, rect) {
    el.style.left = `${Math.round(rect.left)}px`;
    el.style.top = `${Math.round(rect.top)}px`;
    el.style.width = `${Math.round(rect.width)}px`;
    el.style.height = `${Math.round(rect.height)}px`;
  }

  function bringToFront(el) {
    const contentChildren = Array.from(root.children || []);
    let topPanelZ = 20;
    contentChildren.forEach((panelEl) => {
      if (!panelEl?.classList?.contains("mapWindow")) return;
      const inlineZ = Number(panelEl.style?.zIndex);
      if (Number.isFinite(inlineZ)) {
        topPanelZ = Math.max(topPanelZ, inlineZ);
        return;
      }
      const computedZ = Number(window.getComputedStyle(panelEl).zIndex);
      if (Number.isFinite(computedZ)) topPanelZ = Math.max(topPanelZ, computedZ);
    });

    if (layer) {
      layer.style.zIndex = String(topPanelZ + 2);
    }

    let topZ = Number.isFinite(zIndex) ? zIndex : 80;
    layer?.querySelectorAll(".mapWindow").forEach((panelEl) => {
      if (!panelEl) return;
      const inlineZ = Number(panelEl.style?.zIndex);
      if (Number.isFinite(inlineZ)) {
        topZ = Math.max(topZ, inlineZ);
        return;
      }
      const computedZ = Number(window.getComputedStyle(panelEl).zIndex);
      if (Number.isFinite(computedZ)) topZ = Math.max(topZ, computedZ);
    });
    zIndex = topZ + 1;
    el.style.zIndex = String(zIndex);
    root.querySelectorAll(".mapWindow.isSelected").forEach((panelEl) => {
      panelEl.classList.remove("isSelected");
    });
    el.classList.add("isSelected");
  }

  function persistWindow(id, record) {
    state[id] = {
      ...(state[id] || {}),
      ...record,
    };
    saveState();
  }

  function recordWindowOpened(id) {
    openSequence += 1;
    persistWindow(id, { open: true, openSeq: openSequence });
  }

  function attachFrameEmbedMode(frame) {
    frame.addEventListener("load", () => {
      try {
        const doc = frame.contentDocument;
        if (!doc || !doc.body) return;
        doc.body.classList.add("embedMode");
      } catch {
        // Ignore cross-context issues.
      }
    });
  }

  function ensureResizeHandles(panel) {
    panel.querySelectorAll("[data-resize-handle='true']").forEach((el) => el.remove());
    const dirs = ["n", "e", "s", "w", "ne", "nw", "se", "sw"];
    return dirs.map((dir) => {
      const handle = document.createElement("div");
      handle.className = `mapWindowResize mapWindowResize--${dir}`;
      handle.setAttribute("data-resize-handle", "true");
      handle.setAttribute("data-resize-dir", dir);
      handle.setAttribute("aria-hidden", "true");
      panel.appendChild(handle);
      return handle;
    });
  }

  function createWindow(appId, config) {
    const panel = document.createElement("section");
    panel.className = "panel mapWindow appWindow";
    panel.dataset.appWindow = appId;

    const saved = state[appId] || {};
    const baseRect = {
      left: Number.isFinite(saved.left) ? saved.left : 208 + windows.size * 24,
      top: Number.isFinite(saved.top) ? saved.top : 24 + windows.size * 20,
      width: Number.isFinite(saved.width) ? saved.width : 760,
      height: Number.isFinite(saved.height) ? saved.height : 520,
    };
    const initial = clampRect(baseRect);

    panel.innerHTML = `
      <div class="mapWindowHeader" data-drag-handle="true">
        <div class="appWindowHeaderRow">
          <div class="titleSmall">${config.title}</div>
          <div class="appWindowActions">
            <button class="appWindowAction" type="button" data-window-action="minimize" aria-label="Minimize">—</button>
            <button class="appWindowAction" type="button" data-window-action="close" aria-label="Close">×</button>
          </div>
        </div>
      </div>
      <div class="appWindowBody">
        <iframe class="appWindowFrame" src="${config.url}" loading="lazy" title="${config.title} window"></iframe>
      </div>
    `;

    const dragHandle = panel.querySelector("[data-drag-handle='true']");
    const resizeHandles = ensureResizeHandles(panel);
    const bodyEl = panel.querySelector(".appWindowBody");
    const frameEl = panel.querySelector(".appWindowFrame");

    if (frameEl) attachFrameEmbedMode(frameEl);

    function syncRect() {
      const panelRect = panel.getBoundingClientRect();
      const rootRect = root.getBoundingClientRect();
      persistWindow(appId, {
        left: panelRect.left - rootRect.left,
        top: panelRect.top - rootRect.top,
        width: panelRect.width,
        height: panelRect.height,
      });
    }

    function stopPointerListeners(move, stop) {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    }

    function startDrag(event) {
      if (event.button !== 0) return;
      if (event.target.closest("button,a,input,select,textarea")) return;
      event.preventDefault();
      bringToFront(panel);
      panel.classList.add("isDragging");

      const pRect = panel.getBoundingClientRect();
      const rRect = root.getBoundingClientRect();
      const origin = {
        x: event.clientX,
        y: event.clientY,
        left: pRect.left - rRect.left,
        top: pRect.top - rRect.top,
        width: pRect.width,
        height: pRect.height,
      };

      const onMove = (moveEvent) => {
        const next = clampRect({
          left: origin.left + (moveEvent.clientX - origin.x),
          top: origin.top + (moveEvent.clientY - origin.y),
          width: origin.width,
          height: origin.height,
        });
        applyRect(panel, next);
      };

      const stop = () => {
        panel.classList.remove("isDragging");
        syncRect();
        stopPointerListeners(onMove, stop);
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", stop);
      window.addEventListener("pointercancel", stop);
    }

    function startResize(event) {
      if (event.button !== 0) return;
      event.preventDefault();
      bringToFront(panel);
      panel.classList.add("isResizing");
      const dir = String(event.currentTarget?.dataset?.resizeDir || "se").toLowerCase();

      const pRect = panel.getBoundingClientRect();
      const rRect = root.getBoundingClientRect();
      const origin = {
        x: event.clientX,
        y: event.clientY,
        left: pRect.left - rRect.left,
        top: pRect.top - rRect.top,
        width: pRect.width,
        height: pRect.height,
      };

      const onMove = (moveEvent) => {
        const dx = moveEvent.clientX - origin.x;
        const dy = moveEvent.clientY - origin.y;
        let nextLeft = origin.left;
        let nextTop = origin.top;
        let nextWidth = origin.width;
        let nextHeight = origin.height;

        if (dir.includes("e")) nextWidth = origin.width + dx;
        if (dir.includes("s")) nextHeight = origin.height + dy;
        if (dir.includes("w")) {
          nextLeft = origin.left + dx;
          nextWidth = origin.width - dx;
        }
        if (dir.includes("n")) {
          nextTop = origin.top + dy;
          nextHeight = origin.height - dy;
        }

        const next = clampRect({
          left: nextLeft,
          top: nextTop,
          width: nextWidth,
          height: nextHeight,
        });
        applyRect(panel, next);
      };

      const stop = () => {
        panel.classList.remove("isResizing");
        syncRect();
        stopPointerListeners(onMove, stop);
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", stop);
      window.addEventListener("pointercancel", stop);
    }

    function setMinimized(minimized) {
      panel.dataset.minimized = minimized ? "true" : "false";
      panel.classList.toggle("isMinimized", !!minimized);
      if (bodyEl) bodyEl.style.display = minimized ? "none" : "block";
      resizeHandles.forEach((handle) => {
        handle.style.display = minimized ? "none" : "";
      });
      if (minimized) {
        panel.style.height = "30px";
      } else {
        const restore = state[appId] || {};
        const next = clampRect({
          left: Number.isFinite(restore.left) ? restore.left : initial.left,
          top: Number.isFinite(restore.top) ? restore.top : initial.top,
          width: Number.isFinite(restore.width) ? restore.width : initial.width,
          height: Number.isFinite(restore.height) ? restore.height : initial.height,
        });
        applyRect(panel, next);
      }
      persistWindow(appId, { minimized: !!minimized });
    }

    panel.addEventListener("pointerdown", () => bringToFront(panel));
    if (dragHandle) dragHandle.addEventListener("pointerdown", startDrag);
    resizeHandles.forEach((handle) => {
      handle.addEventListener("pointerdown", startResize);
    });

    panel.addEventListener("click", (event) => {
      const actionBtn = event.target.closest("[data-window-action]");
      if (!actionBtn) return;
      const action = actionBtn.getAttribute("data-window-action");
      if (action === "close") {
        hideWindow(appId);
      } else if (action === "minimize") {
        setMinimized(panel.dataset.minimized !== "true");
      }
    });

    applyRect(panel, initial);
    layer.appendChild(panel);
    bringToFront(panel);

    if (saved.minimized) setMinimized(true);

    windows.set(appId, panel);
    persistWindow(appId, {
      left: initial.left,
      top: initial.top,
      width: initial.width,
      height: initial.height,
      open: true,
    });

    return panel;
  }

  function createInventoryWindow(windowId, title) {
    const panel = document.createElement("section");
    panel.className = "panel mapWindow appWindow inventoryWindow";
    panel.dataset.appWindow = windowId;

    const saved = state[windowId] || {};
    const baseRect = {
      left: Number.isFinite(saved.left) ? saved.left : 244 + windows.size * 18,
      top: Number.isFinite(saved.top) ? saved.top : 42 + windows.size * 14,
      width: Number.isFinite(saved.width) ? saved.width : 680,
      height: Number.isFinite(saved.height) ? saved.height : 480,
    };
    const initial = clampRect(baseRect);

    panel.innerHTML = `
      <div class="mapWindowHeader" data-drag-handle="true">
        <div class="appWindowHeaderRow">
          <div class="titleSmall" data-window-title="true">${escapeHtml(title)}</div>
          <div class="appWindowActions">
            <button class="appWindowAction" type="button" data-window-action="minimize" aria-label="Minimize">—</button>
            <button class="appWindowAction" type="button" data-window-action="close" aria-label="Close">×</button>
          </div>
        </div>
      </div>
      <div class="appWindowBody inventoryWindowBody" data-window-body="true"></div>
    `;

    const dragHandle = panel.querySelector("[data-drag-handle='true']");
    const resizeHandles = ensureResizeHandles(panel);
    const bodyEl = panel.querySelector(".appWindowBody");

    function syncRect() {
      const panelRect = panel.getBoundingClientRect();
      const rootRect = root.getBoundingClientRect();
      persistWindow(windowId, {
        left: panelRect.left - rootRect.left,
        top: panelRect.top - rootRect.top,
        width: panelRect.width,
        height: panelRect.height,
      });
    }

    function stopPointerListeners(move, stop) {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    }

    function startDrag(event) {
      if (event.button !== 0) return;
      if (event.target.closest("button,a,input,select,textarea")) return;
      event.preventDefault();
      bringToFront(panel);
      panel.classList.add("isDragging");

      const pRect = panel.getBoundingClientRect();
      const rRect = root.getBoundingClientRect();
      const origin = {
        x: event.clientX,
        y: event.clientY,
        left: pRect.left - rRect.left,
        top: pRect.top - rRect.top,
        width: pRect.width,
        height: pRect.height,
      };

      const onMove = (moveEvent) => {
        const next = clampRect({
          left: origin.left + (moveEvent.clientX - origin.x),
          top: origin.top + (moveEvent.clientY - origin.y),
          width: origin.width,
          height: origin.height,
        });
        applyRect(panel, next);
      };

      const stop = () => {
        panel.classList.remove("isDragging");
        syncRect();
        stopPointerListeners(onMove, stop);
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", stop);
      window.addEventListener("pointercancel", stop);
    }

    function startResize(event) {
      if (event.button !== 0) return;
      event.preventDefault();
      bringToFront(panel);
      panel.classList.add("isResizing");
      const dir = String(event.currentTarget?.dataset?.resizeDir || "se").toLowerCase();

      const pRect = panel.getBoundingClientRect();
      const rRect = root.getBoundingClientRect();
      const origin = {
        x: event.clientX,
        y: event.clientY,
        left: pRect.left - rRect.left,
        top: pRect.top - rRect.top,
        width: pRect.width,
        height: pRect.height,
      };

      const onMove = (moveEvent) => {
        const dx = moveEvent.clientX - origin.x;
        const dy = moveEvent.clientY - origin.y;
        let nextLeft = origin.left;
        let nextTop = origin.top;
        let nextWidth = origin.width;
        let nextHeight = origin.height;

        if (dir.includes("e")) nextWidth = origin.width + dx;
        if (dir.includes("s")) nextHeight = origin.height + dy;
        if (dir.includes("w")) {
          nextLeft = origin.left + dx;
          nextWidth = origin.width - dx;
        }
        if (dir.includes("n")) {
          nextTop = origin.top + dy;
          nextHeight = origin.height - dy;
        }

        const next = clampRect({
          left: nextLeft,
          top: nextTop,
          width: nextWidth,
          height: nextHeight,
        });
        applyRect(panel, next);
      };

      const stop = () => {
        panel.classList.remove("isResizing");
        syncRect();
        stopPointerListeners(onMove, stop);
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", stop);
      window.addEventListener("pointercancel", stop);
    }

    function setMinimized(minimized) {
      panel.dataset.minimized = minimized ? "true" : "false";
      panel.classList.toggle("isMinimized", !!minimized);
      if (bodyEl) bodyEl.style.display = minimized ? "none" : "block";
      resizeHandles.forEach((handle) => {
        handle.style.display = minimized ? "none" : "";
      });
      if (minimized) {
        panel.style.height = "30px";
      } else {
        const restore = state[windowId] || {};
        const next = clampRect({
          left: Number.isFinite(restore.left) ? restore.left : initial.left,
          top: Number.isFinite(restore.top) ? restore.top : initial.top,
          width: Number.isFinite(restore.width) ? restore.width : initial.width,
          height: Number.isFinite(restore.height) ? restore.height : initial.height,
        });
        applyRect(panel, next);
      }
      persistWindow(windowId, { minimized: !!minimized });
    }

    panel.addEventListener("pointerdown", () => bringToFront(panel));
    if (dragHandle) dragHandle.addEventListener("pointerdown", startDrag);
    resizeHandles.forEach((handle) => {
      handle.addEventListener("pointerdown", startResize);
    });

    panel.addEventListener("click", (event) => {
      const actionBtn = event.target.closest("[data-window-action]");
      if (!actionBtn) return;
      const action = actionBtn.getAttribute("data-window-action");
      if (action === "close") {
        hideWindow(windowId);
      } else if (action === "minimize") {
        setMinimized(panel.dataset.minimized !== "true");
      }
    });

    applyRect(panel, initial);
    layer.appendChild(panel);
    bringToFront(panel);

    if (saved.minimized) setMinimized(true);

    windows.set(windowId, panel);
    persistWindow(windowId, {
      left: initial.left,
      top: initial.top,
      width: initial.width,
      height: initial.height,
      open: true,
    });

    return panel;
  }

  function ensureHangarWindow(windowId, title) {
    const existing = windows.get(windowId);
    if (existing) {
      const titleEl = existing.querySelector("[data-window-title='true']");
      if (titleEl) titleEl.textContent = title;
      existing.style.display = "block";
      bringToFront(existing);
      persistWindow(windowId, { open: true });
      return existing;
    }

    return createInventoryWindow(windowId, title);
  }

  async function openHangarWorkspace(shipId, shipName) {
    const id = String(shipId || "").trim();
    if (!id) return;

    const title = `${String(shipName || id)} Hangar`;
    const windowId = hangarWindowId(id);
    const panel = ensureHangarWindow(windowId, title);
    panel.classList.add("hangarWindow");
    const titleEl = panel.querySelector("[data-window-title='true']");
    if (titleEl) titleEl.textContent = title;

    const existing = hangarWindowState.get(windowId) || {};
    hangarWindowState.set(windowId, {
      ...existing,
      anchorKind: "ship",
      anchorId: id,
      anchorName: String(shipName || id),
    });

    await refreshHangarWindow(windowId);
  }

  window.addEventListener(HANGAR_WINDOW_EVENT, (event) => {
    const payload = event?.detail || {};
    const kind = String(payload?.kind || "").toLowerCase();
    if (kind === "ship") {
      openHangarWorkspace(payload?.id, payload?.name);
    }
  });

  function showWindow(appId) {
    const config = APP_CONFIG[appId];
    if (!config) return;

    const existing = windows.get(appId);
    if (existing) {
      existing.style.display = "block";
      bringToFront(existing);
      setDockOpen(appId, true);
      recordWindowOpened(appId);
      return;
    }

    createWindow(appId, config);
    setDockOpen(appId, true);
    recordWindowOpened(appId);
  }

  function hideWindow(appId) {
    const panel = windows.get(appId);
    if (!panel) return;
    panel.style.display = "none";
    setDockOpen(appId, false);
    persistWindow(appId, { open: false });
  }

  function toggleWindow(appId) {
    const panel = windows.get(appId);
    if (panel && panel.style.display !== "none") {
      hideWindow(appId);
      return;
    }
    showWindow(appId);
  }

  dock.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-app-window]");
    if (!btn) return;
    const appId = btn.getAttribute("data-app-window");
    if (!appId) return;
    toggleWindow(appId);
  });

  window.addEventListener("resize", () => {
    windows.forEach((panel, appId) => {
      if (panel.style.display === "none") return;
      if (panel.dataset.minimized === "true") return;
      const pRect = panel.getBoundingClientRect();
      const rRect = root.getBoundingClientRect();
      const next = clampRect({
        left: pRect.left - rRect.left,
        top: pRect.top - rRect.top,
        width: pRect.width,
        height: pRect.height,
      });
      applyRect(panel, next);
      persistWindow(appId, next);
    });
  });

  const appIds = Object.keys(APP_CONFIG);
  appIds.forEach((appId) => {
    const btn = dockButtonFor(appId);
    bootstrapDockButton(btn, APP_CONFIG[appId]?.icon || "");
  });

  const openAppIds = appIds
    .filter((appId) => !!state[appId]?.open)
    .sort((a, b) => {
      const aSeq = Number(state[a]?.openSeq || 0);
      const bSeq = Number(state[b]?.openSeq || 0);
      if (aSeq === bSeq) return a.localeCompare(b);
      return aSeq - bSeq;
    });

  appIds
    .filter((appId) => !state[appId]?.open)
    .forEach((appId) => setDockOpen(appId, false));

  openAppIds.forEach((appId) => showWindow(appId));
})();
