(function () {
  "use strict";

  /* ═══════════════════════════════════════════════════════════
   *  DOM refs
   * ═══════════════════════════════════════════════════════════ */
  var steps = [
    document.getElementById("ccStep1"),
    document.getElementById("ccStep2"),
    document.getElementById("ccStep3"),
    document.getElementById("ccStep4"),
  ];
  var btnCancel  = document.getElementById("ccBtnCancel");
  var btnPrev    = document.getElementById("ccBtnPrev");
  var btnNext    = document.getElementById("ccBtnNext");
  var btnFinish  = document.getElementById("ccBtnFinish");
  var btnCreate  = document.getElementById("btnCreateContract");

  // Step 2 — inventory picker
  var ccLocationSelect = document.getElementById("ccLocationSelect");
  var ccItemList       = document.getElementById("ccItemList");
  var ccSelectedCount  = document.getElementById("ccSelectedCount");
  var ccSelectedVolume = document.getElementById("ccSelectedVolume");


  // Step 2 — ship picker
  var ccShipList = document.getElementById("ccShipList");

  // Step 2 — catalog picker (buy orders)
  var ccCatalogList        = document.getElementById("ccCatalogList");
  var ccCatalogSearchInput = document.getElementById("ccCatalogSearchInput");
  var ccCatalogFilter      = document.getElementById("ccCatalogFilter");

  // Step 3 — barter picker
  var ccBarterList        = document.getElementById("ccBarterList");
  var ccBarterSearchInput = document.getElementById("ccBarterSearchInput");
  var ccBarterFilter      = document.getElementById("ccBarterFilter");
  var ccBarterCount       = document.getElementById("ccBarterCount");

  // Step 3 — courier destination
  var ccCourierDest = document.getElementById("ccCourierDest");

  /* ═══════════════════════════════════════════════════════════
   *  State
   * ═══════════════════════════════════════════════════════════ */
  var currentStep   = 0;
  var locations     = [];     // from /api/contracts/my-locations
  var items         = [];     // inventory items for current location
  var selected      = new Map(); // stack_key → { item, qty }
  var selectedShips = new Map(); // ship.id → ship object
  var catalogAll    = [];     // flat list from /api/catalog/browse
  var catalogSelected = new Map(); // item_id → { item, qty }
  var barterSelected  = new Map(); // item_id → { item, qty }
  var allShips      = [];     // from /api/state (own, parked)
  var allLocations  = [];     // flat list from /api/locations/tree

  var locationsLoaded = false;
  var catalogLoaded   = false;
  var shipsLoaded     = false;
  var destinationsLoaded = false;

  /* ═══════════════════════════════════════════════════════════
   *  Helpers
   * ═══════════════════════════════════════════════════════════ */
  function getType() {
    var el = document.querySelector('input[name="ccType"]:checked');
    return el ? el.value : "auction";
  }
  function getExDir() {
    var el = document.querySelector('input[name="ccExDir"]:checked');
    return el ? el.value : "sell";
  }
  function getAvail() {
    var el = document.querySelector('input[name="ccAvail"]:checked');
    return el ? el.value : "public";
  }
  function getPriceMethod() {
    var el = document.querySelector('input[name="ccPriceMethod"]:checked');
    return el ? el.value : "isk";
  }
  function isBuyOrder() {
    return getType() === "item_exchange" && getExDir() === "buy";
  }
  /** Step 2 uses inventory picker (auction + courier + sell) or catalog picker (buy order) */
  function step2UsesInventory() {
    return !isBuyOrder();
  }

  function $id(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function fmtNum(v) { var n = Number(v) || 0; return n === Math.floor(n) ? String(n) : n.toFixed(1); }
  function fmtVol(v) { return (Number(v) || 0).toFixed(1); }
  function fmtMass(v) {
    var n = Number(v) || 0;
    if (n >= 1000) return (n / 1000).toFixed(1) + " t";
    return n.toFixed(0) + " kg";
  }
  function fmtMoney(v) {
    var n = Number(v) || 0;
    if (n >= 1e9) return (n / 1e9).toFixed(2) + " Billion";
    if (n >= 1e6) return (n / 1e6).toFixed(2) + " Million";
    return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function show(el)  { if (el) el.style.display = ""; }
  function hide(el)  { if (el) el.style.display = "none"; }
  function showId(id) { show($id(id)); }
  function hideId(id) { hide($id(id)); }

  /* ═══════════════════════════════════════════════════════════
   *  Init
   * ═══════════════════════════════════════════════════════════ */
  loadContractInfo();
  showStep(0);

  async function loadContractInfo() {
    try {
      var resp = await fetch("/api/contracts/incoming");
      if (!resp.ok) return;
      var data = await resp.json();

    } catch (e) { /* ignore */ }
  }

  /* ═══════════════════════════════════════════════════════════
   *  Step 1 — Type / availability toggles
   * ═══════════════════════════════════════════════════════════ */

  // Show/hide exchange direction when "Item Exchange" is selected
  document.querySelectorAll('input[name="ccType"]').forEach(function (r) {
    r.addEventListener("change", function () {
      var exDir = $id("ccExchangeDir");
      if (exDir) exDir.style.display = getType() === "item_exchange" ? "block" : "none";
    });
  });

  // Show/hide private fields
  document.querySelectorAll('input[name="ccAvail"]').forEach(function (r) {
    r.addEventListener("change", function () {
      var f = $id("ccPrivateFields");
      if (f) f.style.display = r.value === "private" && r.checked ? "block" : "none";
    });
  });

  /* ═══════════════════════════════════════════════════════════
   *  Step navigation
   * ═══════════════════════════════════════════════════════════ */
  function showStep(n) {
    currentStep = n;
    steps.forEach(function (el, i) { if (el) el.style.display = i === n ? "block" : "none"; });
    if (btnPrev)   btnPrev.style.display   = n > 0          ? "inline-block" : "none";
    if (btnNext)   btnNext.style.display   = n > 0 && n < 3 ? "inline-block" : "none";
    if (btnFinish) btnFinish.style.display = n === 3         ? "inline-block" : "none";
    if (btnCreate) btnCreate.style.display = n === 0         ? "inline-block" : "none";

    // Step-specific entry actions
    if (n === 1) onEnterStep2();
    if (n === 2) onEnterStep3();
    if (n === 3) populateConfirm();
  }

  if (btnCreate) btnCreate.addEventListener("click", function () { showStep(1); });
  if (btnNext)   btnNext.addEventListener("click", function () {
    if (currentStep === 1 && !validateStep2()) return;
    if (currentStep < 3) showStep(currentStep + 1);
  });
  if (btnPrev) btnPrev.addEventListener("click", function () {
    if (currentStep > 0) showStep(currentStep - 1);
  });
  if (btnFinish) btnFinish.addEventListener("click", submitContract);
  if (btnCancel) btnCancel.addEventListener("click", closeWindow);
  var closeBtn = document.querySelector(".ccWizardClose");
  if (closeBtn) closeBtn.addEventListener("click", closeWindow);

  function closeWindow() { window.location.href = "/contracts"; }

  function validateStep2() {
    if (isBuyOrder()) {
      if (catalogSelected.size === 0) { window.alert("Please select at least one item to buy."); return false; }
    } else {
      // Check if mode is ships or items
      var shipMode = $id("ccPickerShips") && $id("ccPickerShips").style.display !== "none";
      if (shipMode) {
        if (selectedShips.size === 0) { window.alert("Please select at least one ship."); return false; }
      } else {
        if (selected.size === 0) { window.alert("Please select at least one item."); return false; }
      }
    }
    return true;
  }

  /* ═══════════════════════════════════════════════════════════
   *  Step 2 — entry logic (show correct picker for type)
   * ═══════════════════════════════════════════════════════════ */
  function onEnterStep2() {
    var modeBar    = $id("ccItemModeBar");
    var invPicker  = $id("ccPickerInventory");
    var shipPicker = $id("ccPickerShips");
    var catPicker  = $id("ccPickerCatalog");

    if (isBuyOrder()) {
      // Buy order → catalog browser
      hide(modeBar); hide(invPicker); hide(shipPicker); show(catPicker);
      if (!catalogLoaded) loadCatalog();
    } else {
      // Auction / Courier / Sell → inventory picker with optional ship toggle
      show(modeBar); show(invPicker); hide(catPicker);
      // Default to cargo mode
      setItemMode("items");
      if (!locationsLoaded) loadLocations();
    }
  }

  /* ── Item mode toggle (Cargo vs Ships) ─────────────────── */
  var btnModeItems = $id("ccModeItems");
  var btnModeShips = $id("ccModeShips");
  if (btnModeItems) btnModeItems.addEventListener("click", function () { setItemMode("items"); });
  if (btnModeShips) btnModeShips.addEventListener("click", function () { setItemMode("ships"); });

  function setItemMode(mode) {
    var invPicker  = $id("ccPickerInventory");
    var shipPicker = $id("ccPickerShips");
    if (btnModeItems) btnModeItems.classList.toggle("active", mode === "items");
    if (btnModeShips) btnModeShips.classList.toggle("active", mode === "ships");
    if (mode === "items") {
      show(invPicker); hide(shipPicker);
      if (!locationsLoaded) loadLocations();
    } else {
      hide(invPicker); show(shipPicker);
      if (!shipsLoaded) loadShips();
    }
  }

  /* ═══════════════════════════════════════════════════════════
   *  Step 2 — Inventory picker (locations + items)
   * ═══════════════════════════════════════════════════════════ */
  async function loadLocations() {
    locationsLoaded = true;
    try {
      var resp = await fetch("/api/contracts/my-locations");
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      locations = data.locations || [];
      if (ccLocationSelect) {
        ccLocationSelect.innerHTML = "";
        if (locations.length === 0) {
          ccLocationSelect.innerHTML = '<option value="">No locations with cargo</option>';
        } else {
          locations.forEach(function (loc) {
            var opt = document.createElement("option");
            opt.value = loc.id;
            opt.textContent = loc.name + " (" + loc.item_count + " items)";
            ccLocationSelect.appendChild(opt);
          });
          loadItems(locations[0].id);
        }
      }
    } catch (err) {
      console.warn("loadLocations:", err);
      if (ccLocationSelect) ccLocationSelect.innerHTML = '<option value="">Failed to load</option>';
    }
  }

  if (ccLocationSelect) ccLocationSelect.addEventListener("change", function () {
    if (ccLocationSelect.value) loadItems(ccLocationSelect.value);
  });

  async function loadItems(locationId) {
    if (!ccItemList) return;
    ccItemList.innerHTML = '<div class="ccItemEmpty">Loading\u2026</div>';
    try {
      var resp = await fetch("/api/inventory/location/" + encodeURIComponent(locationId));
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      items = [];
      (data.resources || []).forEach(function (r) {
        items.push({ stack_key: r.stack_key || r.item_id, item_id: r.item_id || r.resource_id,
          name: r.name, quantity: r.quantity || 1, volume_m3: r.volume_m3 || 0, mass_kg: r.mass_kg || 0,
          type: "resource", is_stackable: true });
      });
      (data.parts || []).forEach(function (p) {
        items.push({ stack_key: p.stack_key || p.item_id, item_id: p.item_id,
          name: p.name || (p.part && p.part.name) || p.item_id, quantity: p.quantity || 1,
          volume_m3: p.volume_m3 || 0, mass_kg: p.mass_kg || 0, type: "part", is_stackable: false });
      });
      renderItems();
    } catch (err) {
      console.warn("loadItems:", err);
      if (ccItemList) ccItemList.innerHTML = '<div class="ccItemEmpty">Failed to load items</div>';
    }
  }

  function renderItems() {
    if (!ccItemList) return;
    ccItemList.innerHTML = "";
    if (items.length === 0) {
      ccItemList.innerHTML = '<div class="ccItemEmpty">No items at this location</div>';
      return;
    }
    items.forEach(function (item) {
      var row = document.createElement("div");
      row.className = "ccItemRow";
      var sel = selected.get(item.stack_key);
      var isChecked = !!sel;
      var selQty = sel ? sel.qty : item.quantity;
      var qtyCell = (item.is_stackable && isChecked)
        ? '<input type="number" class="ccQtyInput" data-key="' + esc(item.stack_key) + '" min="1" max="' + item.quantity + '" value="' + selQty + '" />'
        : fmtNum(item.quantity);
      row.innerHTML =
        '<span class="ccItemCheck"><input type="checkbox" class="ccItemCb" data-key="' + esc(item.stack_key) + '" ' + (isChecked ? "checked" : "") + " /></span>" +
        '<span class="ccItemColType"><span class="ccItemIcon ccItemIcon--' + (item.type === "resource" ? "resource" : "part") + '"></span>' + esc(item.name) + "</span>" +
        '<span class="ccItemColQty">' + qtyCell + "</span>" +
        '<span class="ccItemColVol">' + fmtVol(item.volume_m3) + "</span>" +
        '<span class="ccItemColDetail"></span>';
      ccItemList.appendChild(row);

      row.querySelector(".ccItemCb").addEventListener("change", function (e) {
        if (e.target.checked) { selected.set(item.stack_key, { item: item, qty: item.quantity }); }
        else { selected.delete(item.stack_key); }
        renderItems(); updateItemSummary();
      });
      var qi = row.querySelector(".ccQtyInput");
      if (qi) qi.addEventListener("change", function () {
        var v = Math.max(1, Math.min(item.quantity, parseInt(qi.value) || 1));
        qi.value = v;
        var e = selected.get(item.stack_key);
        if (e) e.qty = v;
        updateItemSummary();
      });
    });
    updateItemSummary();
  }

  function updateItemSummary() {
    var count = 0, volume = 0;
    selected.forEach(function (e) {
      count += e.qty;
      var per = e.item.quantity > 0 ? e.item.volume_m3 / e.item.quantity : 0;
      volume += per * e.qty;
    });
    if (ccSelectedCount) ccSelectedCount.textContent = String(count);
    if (ccSelectedVolume) ccSelectedVolume.textContent = volume.toFixed(1);
  }

  /* ═══════════════════════════════════════════════════════════
   *  Step 2 — Ship picker
   * ═══════════════════════════════════════════════════════════ */
  async function loadShips() {
    shipsLoaded = true;
    if (!ccShipList) return;
    ccShipList.innerHTML = '<div class="ccItemEmpty">Loading ships\u2026</div>';
    try {
      var resp = await fetch("/api/state");
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      allShips = (data.ships || []).filter(function (s) {
        return s.is_own && s.status === "idle";
      });
      renderShips();
    } catch (err) {
      console.warn("loadShips:", err);
      ccShipList.innerHTML = '<div class="ccItemEmpty">Failed to load ships</div>';
    }
  }

  function renderShips() {
    if (!ccShipList) return;
    ccShipList.innerHTML = "";
    if (allShips.length === 0) {
      ccShipList.innerHTML = '<div class="ccItemEmpty">No idle ships available</div>';
      return;
    }
    allShips.forEach(function (ship) {
      var row = document.createElement("div");
      row.className = "ccItemRow";
      var isChecked = selectedShips.has(ship.id);
      row.innerHTML =
        '<span class="ccItemCheck"><input type="checkbox" class="ccShipCb" data-id="' + esc(ship.id) + '" ' + (isChecked ? "checked" : "") + " /></span>" +
        '<span class="ccItemColType">' + esc(ship.name) + "</span>" +
        '<span class="ccItemColQty">' + fmtMass(ship.dry_mass_kg || 0) + "</span>" +
        '<span class="ccItemColVol">' + esc(ship.location_id || "Unknown") + "</span>" +
        '<span class="ccItemColDetail">' + (ship.part_count || 0) + " parts</span>";
      ccShipList.appendChild(row);

      row.querySelector(".ccShipCb").addEventListener("change", function (e) {
        if (e.target.checked) selectedShips.set(ship.id, ship);
        else selectedShips.delete(ship.id);
      });
    });
  }

  /* ═══════════════════════════════════════════════════════════
   *  Step 2 — Catalog picker (buy orders)
   * ═══════════════════════════════════════════════════════════ */
  async function loadCatalog() {
    catalogLoaded = true;
    if (!ccCatalogList) return;
    ccCatalogList.innerHTML = '<div class="ccItemEmpty">Loading catalog\u2026</div>';
    try {
      var resp = await fetch("/api/catalog/browse");
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      catalogAll = [];
      ["raw_materials", "finished_goods", "modules"].forEach(function (cat) {
        (data[cat] || []).forEach(function (it) {
          catalogAll.push({ id: it.id, name: it.name, category: it.category, _cat: cat });
        });
      });
      renderCatalog();
    } catch (err) {
      console.warn("loadCatalog:", err);
      ccCatalogList.innerHTML = '<div class="ccItemEmpty">Failed to load catalog</div>';
    }
  }

  function filterCatalog(allItems, searchInput, filterSelect) {
    var q = (searchInput ? searchInput.value : "").toLowerCase().trim();
    var cat = filterSelect ? filterSelect.value : "all";
    return allItems.filter(function (it) {
      if (cat !== "all" && it._cat !== cat) return false;
      if (q && it.name.toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
  }

  function renderCatalog() {
    if (!ccCatalogList) return;
    var filtered = filterCatalog(catalogAll, ccCatalogSearchInput, ccCatalogFilter);
    ccCatalogList.innerHTML = "";
    if (filtered.length === 0) {
      ccCatalogList.innerHTML = '<div class="ccItemEmpty">No matching items</div>';
      return;
    }
    filtered.forEach(function (item) {
      var row = document.createElement("div");
      row.className = "ccItemRow";
      var sel = catalogSelected.get(item.id);
      var isChecked = !!sel;
      var qty = sel ? sel.qty : 1;
      var qtyCell = isChecked
        ? '<input type="number" class="ccQtyInput ccCatQty" data-id="' + esc(item.id) + '" min="1" value="' + qty + '" />'
        : "—";
      row.innerHTML =
        '<span class="ccItemCheck"><input type="checkbox" class="ccCatCb" data-id="' + esc(item.id) + '" ' + (isChecked ? "checked" : "") + " /></span>" +
        '<span class="ccItemColType">' + esc(item.name) + "</span>" +
        '<span class="ccItemColQty">' + qtyCell + "</span>" +
        '<span class="ccItemColVol ccCatCat">' + esc(item.category) + "</span>";
      ccCatalogList.appendChild(row);

      row.querySelector(".ccCatCb").addEventListener("change", function (e) {
        if (e.target.checked) catalogSelected.set(item.id, { item: item, qty: 1 });
        else catalogSelected.delete(item.id);
        renderCatalog(); updateCatalogSummary();
      });
      var qi = row.querySelector(".ccCatQty");
      if (qi) qi.addEventListener("change", function () {
        var v = Math.max(1, parseInt(qi.value) || 1);
        qi.value = v;
        var e = catalogSelected.get(item.id);
        if (e) e.qty = v;
        updateCatalogSummary();
      });
    });
    updateCatalogSummary();
  }

  function updateCatalogSummary() {
    var count = 0;
    catalogSelected.forEach(function (e) { count += e.qty; });
    if (ccSelectedCount) ccSelectedCount.textContent = String(count);
    if (ccSelectedVolume) ccSelectedVolume.textContent = "0.0";
  }

  if (ccCatalogSearchInput) ccCatalogSearchInput.addEventListener("input", renderCatalog);
  if (ccCatalogFilter)      ccCatalogFilter.addEventListener("change", renderCatalog);

  /* ═══════════════════════════════════════════════════════════
   *  Step 3 — entry logic (show correct options block)
   * ═══════════════════════════════════════════════════════════ */
  function onEnterStep3() {
    var ctype = getType();
    hideId("ccOpts_auction");
    hideId("ccOpts_courier");
    hideId("ccOpts_exchange");

    if (ctype === "auction") {
      showId("ccOpts_auction");
    } else if (ctype === "courier") {
      showId("ccOpts_courier");
      if (!destinationsLoaded) loadDestinations();
    } else {
      showId("ccOpts_exchange");
    }
  }

  /* ── Step 3 — Courier destination picker ─────────────── */
  async function loadDestinations() {
    destinationsLoaded = true;
    if (!ccCourierDest) return;
    ccCourierDest.innerHTML = '<option value="">Loading\u2026</option>';
    try {
      var resp = await fetch("/api/locations/tree");
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      allLocations = [];
      flattenTree(data.tree || [], 0);
      ccCourierDest.innerHTML = '<option value="">Select destination\u2026</option>';
      allLocations.forEach(function (loc) {
        var opt = document.createElement("option");
        opt.value = loc.id;
        opt.textContent = loc.prefix + loc.name;
        if (loc.is_group) opt.disabled = true;
        ccCourierDest.appendChild(opt);
      });
    } catch (err) {
      console.warn("loadDestinations:", err);
      ccCourierDest.innerHTML = '<option value="">Failed to load</option>';
    }
  }

  function flattenTree(nodes, depth) {
    var prefix = "";
    for (var i = 0; i < depth; i++) prefix += "\u00A0\u00A0\u00A0";
    nodes.forEach(function (n) {
      allLocations.push({ id: n.id, name: n.name, is_group: n.is_group, prefix: prefix });
      if (n.children && n.children.length) flattenTree(n.children, depth + 1);
    });
  }

  /* ── Step 3 — Exchange: price method toggle ──────────── */
  document.querySelectorAll('input[name="ccPriceMethod"]').forEach(function (r) {
    r.addEventListener("change", function () {
      var method = getPriceMethod();
      var pf = $id("ccExchangePriceFields");
      var bf = $id("ccExchangeBarterFields");
      if (pf) pf.style.display = method === "isk" ? "" : "none";
      if (bf) bf.style.display = method === "barter" ? "" : "none";
    });
  });

  /* ── Step 3 — Barter catalog picker ──────────────────── */
  function renderBarter() {
    if (!ccBarterList) return;
    // Reuse catalogAll (load if needed)
    if (catalogAll.length === 0 && !catalogLoaded) {
      loadCatalog().then(renderBarter);
      return;
    }
    var filtered = filterCatalog(catalogAll, ccBarterSearchInput, ccBarterFilter);
    ccBarterList.innerHTML = "";
    if (filtered.length === 0) {
      ccBarterList.innerHTML = '<div class="ccItemEmpty">No matching items</div>';
      return;
    }
    filtered.forEach(function (item) {
      var row = document.createElement("div");
      row.className = "ccItemRow";
      var sel = barterSelected.get(item.id);
      var isChecked = !!sel;
      var qty = sel ? sel.qty : 1;
      var qtyCell = isChecked
        ? '<input type="number" class="ccQtyInput ccBrtQty" data-id="' + esc(item.id) + '" min="1" value="' + qty + '" />'
        : "—";
      row.innerHTML =
        '<span class="ccItemCheck"><input type="checkbox" class="ccBrtCb" data-id="' + esc(item.id) + '" ' + (isChecked ? "checked" : "") + " /></span>" +
        '<span class="ccItemColType">' + esc(item.name) + "</span>" +
        '<span class="ccItemColQty">' + qtyCell + "</span>" +
        '<span class="ccItemColVol ccCatCat">' + esc(item.category) + "</span>";
      ccBarterList.appendChild(row);

      row.querySelector(".ccBrtCb").addEventListener("change", function (e) {
        if (e.target.checked) barterSelected.set(item.id, { item: item, qty: 1 });
        else barterSelected.delete(item.id);
        renderBarter();
      });
      var qi = row.querySelector(".ccBrtQty");
      if (qi) qi.addEventListener("change", function () {
        var v = Math.max(1, parseInt(qi.value) || 1);
        qi.value = v;
        var e = barterSelected.get(item.id);
        if (e) e.qty = v;
        updateBarterCount();
      });
    });
    updateBarterCount();
  }

  function updateBarterCount() {
    var c = 0;
    barterSelected.forEach(function (e) { c += e.qty; });
    if (ccBarterCount) ccBarterCount.textContent = String(c);
  }

  if (ccBarterSearchInput) ccBarterSearchInput.addEventListener("input", renderBarter);
  if (ccBarterFilter)      ccBarterFilter.addEventListener("change", renderBarter);

  /* ═══════════════════════════════════════════════════════════
   *  Step 4 — Populate confirmation
   * ═══════════════════════════════════════════════════════════ */
  function populateConfirm() {
    var ctype = getType();
    var avail = getAvail();
    var typeNames = { auction: "Auction", courier: "Courier", item_exchange: "Item Exchange" };

    // Type
    if ($id("ccConfType")) $id("ccConfType").textContent = typeNames[ctype] || ctype;

    // Direction (only for item_exchange)
    var dirRow = $id("ccConfDirRow");
    if (ctype === "item_exchange") {
      if (dirRow) dirRow.style.display = "";
      if ($id("ccConfDir")) $id("ccConfDir").textContent = getExDir() === "buy" ? "Want to Buy" : "Want to Sell";
    } else {
      if (dirRow) dirRow.style.display = "none";
    }

    // Availability
    if ($id("ccConfAvail")) $id("ccConfAvail").textContent = avail === "public" ? "Public" : "Private";

    // Location
    var locOpt = ccLocationSelect ? ccLocationSelect.selectedOptions[0] : null;
    var locName = locOpt && ccLocationSelect.value ? locOpt.textContent : "(Any)";
    if ($id("ccConfLocation")) $id("ccConfLocation").textContent = locName;

    // Destination (courier only)
    var destRow = $id("ccConfDestRow");
    if (ctype === "courier") {
      if (destRow) destRow.style.display = "";
      var destOpt = ccCourierDest ? ccCourierDest.selectedOptions[0] : null;
      if ($id("ccConfDest")) $id("ccConfDest").textContent = destOpt && ccCourierDest.value ? destOpt.textContent.trim() : "(Not set)";
    } else {
      if (destRow) destRow.style.display = "none";
    }

    // Expiration
    var days = 0;
    if (ctype === "auction") days = parseInt(($id("ccAuctionTime") || {}).value) || 180;
    else if (ctype === "courier") days = parseInt(($id("ccCourierExpiry") || {}).value) || 30;
    else days = parseInt(($id("ccExchangeExpiry") || {}).value) || 14;
    var daysLabel = days >= 365 ? (days / 365).toFixed(0) + " year(s)" : days + " days";
    if ($id("ccConfExpiry")) $id("ccConfExpiry").textContent = daysLabel;

    // Price rows — show/hide per type
    var bidRow = $id("ccConfBidRow"), buyoutRow = $id("ccConfBuyoutRow");
    var priceRow = $id("ccConfPriceRow"), rewardRow = $id("ccConfRewardRow"), collateralRow = $id("ccConfCollateralRow");
    [bidRow, buyoutRow, priceRow, rewardRow, collateralRow].forEach(function (r) { if (r) r.style.display = "none"; });

    if (ctype === "auction") {
      var bid = parseFloat(($id("ccStartingBid") || {}).value) || 0;
      var buyout = parseFloat(($id("ccBuyoutPrice") || {}).value) || 0;
      if (bidRow) { bidRow.style.display = ""; $id("ccConfBid").textContent = "$" + fmtMoney(bid); }
      if (buyoutRow) { buyoutRow.style.display = ""; $id("ccConfBuyout").textContent = buyout > 0 ? "$" + fmtMoney(buyout) : "(None)"; }
    } else if (ctype === "courier") {
      var reward = parseFloat(($id("ccCourierReward") || {}).value) || 0;
      var collateral = parseFloat(($id("ccCourierCollateral") || {}).value) || 0;
      if (rewardRow) { rewardRow.style.display = ""; $id("ccConfReward").textContent = "$" + fmtMoney(reward); }
      if (collateralRow) { collateralRow.style.display = ""; $id("ccConfCollateral").textContent = "$" + fmtMoney(collateral); }
    } else {
      var method = getPriceMethod();
      if (method === "isk") {
        var price = parseFloat(($id("ccExchangePrice") || {}).value) || 0;
        if (priceRow) { priceRow.style.display = ""; $id("ccConfPrice").textContent = "$" + fmtMoney(price); }
      }
    }

    // Items
    var itemLines = [];
    if (isBuyOrder()) {
      catalogSelected.forEach(function (e) { itemLines.push(esc(e.item.name) + " x " + e.qty); });
    } else if ($id("ccPickerShips") && $id("ccPickerShips").style.display !== "none") {
      selectedShips.forEach(function (ship) { itemLines.push(esc(ship.name)); });
    } else {
      selected.forEach(function (e) { itemLines.push(esc(e.item.name) + " x " + e.qty); });
    }
    if ($id("ccConfItems")) $id("ccConfItems").innerHTML = itemLines.join("<br>") || "(None)";

    // Barter items
    var barterRow = $id("ccConfBarterRow");
    if (ctype === "item_exchange" && getPriceMethod() === "barter" && barterSelected.size > 0) {
      if (barterRow) barterRow.style.display = "";
      var bl = [];
      barterSelected.forEach(function (e) { bl.push(esc(e.item.name) + " x " + e.qty); });
      if ($id("ccConfBarter")) $id("ccConfBarter").innerHTML = bl.join("<br>");
    } else {
      if (barterRow) barterRow.style.display = "none";
    }

    // Description
    var desc = "";
    if (ctype === "auction") desc = ($id("ccDescAuction") || {}).value || "";
    else if (ctype === "courier") desc = ($id("ccDescCourier") || {}).value || "";
    else desc = ($id("ccDescExchange") || {}).value || "";
    var descEl = $id("ccConfDesc");
    if (descEl) {
      desc = desc.trim();
      descEl.textContent = desc || "";
      descEl.style.display = desc ? "" : "none";
    }
  }

  /* ═══════════════════════════════════════════════════════════
   *  Submit contract
   * ═══════════════════════════════════════════════════════════ */
  async function submitContract() {
    var ctype = getType();
    var avail = getAvail();
    var locId = ccLocationSelect ? ccLocationSelect.value : "";

    // Build items payload
    var itemsPayload = [];
    if (isBuyOrder()) {
      catalogSelected.forEach(function (e) {
        itemsPayload.push({ item_id: e.item.id, name: e.item.name, quantity: e.qty });
      });
    } else if ($id("ccPickerShips") && $id("ccPickerShips").style.display !== "none") {
      selectedShips.forEach(function (ship) {
        itemsPayload.push({ item_id: ship.id, name: ship.name, quantity: 1, type: "ship" });
        if (!locId) locId = ship.location_id;
      });
    } else {
      selected.forEach(function (e) {
        itemsPayload.push({
          item_id: e.item.item_id, stack_key: e.item.stack_key, name: e.item.name,
          quantity: e.qty, volume_m3: e.item.volume_m3, mass_kg: e.item.mass_kg,
        });
      });
    }

    var title = itemsPayload.length > 0
      ? itemsPayload.map(function (i) { return i.name + (i.quantity > 1 ? " x" + i.quantity : ""); }).join(", ")
      : "Contract";

    var body = {
      contract_type: ctype,
      title: title,
      availability: avail,
      location_id: locId || null,
      assignee_org_id: null,
      items: itemsPayload,
      description: "",
      price: 0,
      buyout_price: 0,
      reward: 0,
      expiry_days: 14,
      destination_id: null,
    };

    // Type-specific fields
    if (ctype === "auction") {
      body.price = parseFloat(($id("ccStartingBid") || {}).value) || 0;
      body.buyout_price = parseFloat(($id("ccBuyoutPrice") || {}).value) || 0;
      body.expiry_days = parseInt(($id("ccAuctionTime") || {}).value) || 180;
      body.description = (($id("ccDescAuction") || {}).value || "").trim();
    } else if (ctype === "courier") {
      body.destination_id = ccCourierDest ? ccCourierDest.value || null : null;
      body.reward = parseFloat(($id("ccCourierReward") || {}).value) || 0;
      body.price = parseFloat(($id("ccCourierCollateral") || {}).value) || 0; // collateral stored in price
      body.expiry_days = parseInt(($id("ccCourierExpiry") || {}).value) || 30;
      body.description = (($id("ccDescCourier") || {}).value || "").trim();
    } else {
      // item_exchange
      var method = getPriceMethod();
      if (method === "isk") {
        body.price = parseFloat(($id("ccExchangePrice") || {}).value) || 0;
      } else {
        // barter — pack wanted items into description or a special field
        var barterItems = [];
        barterSelected.forEach(function (e) {
          barterItems.push({ item_id: e.item.id, name: e.item.name, quantity: e.qty });
        });
        body.buyout_price = 0;
        body.description = (($id("ccDescExchange") || {}).value || "").trim();
        // Encode barter items in the items_json alongside the offered items
        if (barterItems.length > 0) {
          body.description = "[Barter] Wants: " + barterItems.map(function (b) { return b.name + " x" + b.quantity; }).join(", ") +
            (body.description ? "\n" + body.description : "");
        }
      }
      if (method === "isk") {
        body.description = (($id("ccDescExchange") || {}).value || "").trim();
      }
      body.expiry_days = parseInt(($id("ccExchangeExpiry") || {}).value) || 14;
    }

    try {
      btnFinish.disabled = true;
      btnFinish.textContent = "Submitting\u2026";
      var resp = await fetch("/api/contracts/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        var errData = {};
        try { errData = await resp.json(); } catch (e) {}
        throw new Error(errData.detail || "HTTP " + resp.status);
      }
      closeWindow();
    } catch (err) {
      window.alert("Failed to create contract: " + err.message);
    } finally {
      if (btnFinish) { btnFinish.disabled = false; btnFinish.textContent = "Finish"; }
    }
  }
})();
