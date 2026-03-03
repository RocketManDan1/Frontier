(function () {
  "use strict";

  /* ── DOM refs: main tabs ──────────────────────────────── */
  const tabBtnIncoming = document.getElementById("contractsTabBtnIncoming");
  const tabBtnMy = document.getElementById("contractsTabBtnMy");
  const tabBtnSearch = document.getElementById("contractsTabBtnSearch");

  const tabIncoming = document.getElementById("contractsTabIncoming");
  const tabMy = document.getElementById("contractsTabMy");
  const tabSearch = document.getElementById("contractsTabSearch");

  const incomingListEl = document.getElementById("contractsIncomingList");

  const btnGetContracts = document.getElementById("btnGetContracts");
  const myContractsList = document.getElementById("myContractsList");
  const myContractOwnerMe = document.getElementById("myContractOwnerMe");

  const btnSearchContracts = document.getElementById("btnSearchContracts");
  const searchResultsList = document.getElementById("searchResultsList");
  const searchStatus = document.getElementById("searchStatus");

  let wizOrgName = "";


  /* ══════════════════════════════════════════════════════════
     Main tab switching
     ══════════════════════════════════════════════════════════ */
  function setTab(tabName) {
    const isIncoming = tabName === "incoming";
    const isMy = tabName === "my";
    const isSearch = tabName === "search";

    if (tabIncoming) tabIncoming.style.display = isIncoming ? "block" : "none";
    if (tabMy) tabMy.style.display = isMy ? "block" : "none";
    if (tabSearch) tabSearch.style.display = isSearch ? "block" : "none";

    [tabBtnIncoming, tabBtnMy, tabBtnSearch].forEach((btn, i) => {
      if (!btn) return;
      const active = [isIncoming, isMy, isSearch][i];
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", String(active));
    });
  }

  tabBtnIncoming?.addEventListener("click", () => setTab("incoming"));
  tabBtnMy?.addEventListener("click", () => setTab("my"));
  tabBtnSearch?.addEventListener("click", () => {
    setTab("search");
  });

  /* ── Search type & view toggles ───────────────────────── */
  var buySellFilters = document.getElementById("buySellFilters");
  var courierFilters = document.getElementById("courierFilters");
  var zoneBarPickup = document.getElementById("zoneBarPickup");
  var zoneBarDropoff = document.getElementById("zoneBarDropoff");
  var _zonesLoaded = false;

  /** Get the selected zone id from a zone bar (null = Any) */
  function getSelectedZone(bar) {
    if (!bar) return null;
    var active = bar.querySelector(".czBtn.czActive");
    return active ? active.getAttribute("data-zone-id") : null;
  }

  function updateSearchTypeUI() {
    var activeBtn = document.querySelector(".contractsSearchTypeBtn.active");
    var isCourier = activeBtn && activeBtn.dataset.searchType === "courier";
    if (buySellFilters) buySellFilters.style.display = isCourier ? "none" : "block";
    if (courierFilters) courierFilters.style.display = isCourier ? "block" : "none";
    if (isCourier && !_zonesLoaded) loadZones();
  }

  /** Build planet symbol buttons into a zone bar element */
  function buildZoneBar(bar, zones) {
    if (!bar) return;
    bar.innerHTML = "";
    zones.forEach(function (z) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "czBtn";
      btn.setAttribute("data-zone-id", z.id);
      btn.setAttribute("aria-label", z.name + " Heliocentric Zone");
      btn.title = z.name + " Heliocentric Zone";
      btn.textContent = z.symbol || "\u2022";
      btn.addEventListener("click", function () {
        if (btn.classList.contains("czActive")) {
          btn.classList.remove("czActive");
        } else {
          bar.querySelectorAll(".czBtn").forEach(function (b) { b.classList.remove("czActive"); });
          btn.classList.add("czActive");
        }
      });
      bar.appendChild(btn);
    });
  }

  async function loadZones() {
    try {
      var resp = await fetch("/api/contracts/zones");
      if (!resp.ok) return;
      var data = await resp.json();
      var zones = data.zones || [];
      buildZoneBar(zoneBarPickup, zones);
      buildZoneBar(zoneBarDropoff, zones);
      _zonesLoaded = true;
    } catch (e) {
      console.warn("Failed to load zones:", e);
    }
  }

  document.querySelectorAll(".contractsSearchTypeBtn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".contractsSearchTypeBtn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      updateSearchTypeUI();
    });
  });

  // Init on load
  updateSearchTypeUI();



  /* ══════════════════════════════════════════════════════════
     Incoming Contracts (tab 1)
     ══════════════════════════════════════════════════════════ */
  async function loadIncomingContracts() {
    try {
      const resp = await fetch("/api/contracts/incoming");
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();



      if (incomingListEl) {
        incomingListEl.innerHTML = "";
        (data.contracts || []).forEach((c) => {
          const row = document.createElement("div");
          row.className = "contractsIncomingRow";
          row.setAttribute("data-contract-id", c.id);
          row.innerHTML =
            '<span class="contractRowType">' + esc(c.type || "Item Exchange") + "</span>" +
            '<span class="contractRowFrom">' + esc(c.issuer_name || "Unknown") + "</span>" +
            '<span class="contractRowStatus">' + esc(c.status || "Outstanding") + "</span>" +
            '<span class="contractRowDate">' + esc(c.created_at || "") + "</span>";
          incomingListEl.appendChild(row);
        });
      }
    } catch (err) {
      console.warn("Failed to load incoming contracts:", err);
    }
  }

  /* ══════════════════════════════════════════════════════════
     My Contracts (tab 2)
     ══════════════════════════════════════════════════════════ */

  function fmtGameDate(epoch) {
    if (!epoch) return "\u2014";
    var d = new Date(Number(epoch) * 1000);
    var y = d.getUTCFullYear();
    var m = String(d.getUTCMonth() + 1).padStart(2, "0");
    var dd = String(d.getUTCDate()).padStart(2, "0");
    var hh = String(d.getUTCHours()).padStart(2, "0");
    var mm = String(d.getUTCMinutes()).padStart(2, "0");
    return y + "." + m + "." + dd + " " + hh + ":" + mm;
  }

  function fmtTimeLeft(tl) {
    if (!tl || tl === "\u2014") return "\u2014";
    return String(tl);
  }

  function fmtContractType(t) {
    switch (t) {
      case "auction": return "Auction";
      case "courier": return "Courier";
      case "item_exchange": return "Item Exchange";
      default: return t || "\u2014";
    }
  }

  async function loadMyContracts() {
    const type = document.getElementById("myContractType")?.value || "auction";
    const action = document.getElementById("myContractAction")?.value || "issued_to_by";
    const status = document.getElementById("myContractStatus")?.value || "outstanding";
    const params = new URLSearchParams({ type: type, action: action, status: status });

    try {
      const resp = await fetch("/api/contracts/my?" + params);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();

      if (myContractsList) {
        myContractsList.innerHTML = "";
        const contracts = data.contracts || [];
        if (contracts.length === 0) {
          myContractsList.innerHTML = '<div class="contractsEmptyMsg">No Contracts Found</div>';
        } else {
          contracts.forEach(function (c) {
            var row = document.createElement("div");
            row.className = "contractsMyRow";
            row.setAttribute("data-contract-id", c.id);
            row.innerHTML =
              '<span class="ctCol ctColContract">' + esc(c.title || "Untitled") + '</span>' +
              '<span class="ctCol ctColType">' + esc(fmtContractType(c.contract_type)) + '</span>' +
              '<span class="ctCol ctColFrom">' + esc(c.issuer_name || "\u2014") + '</span>' +
              '<span class="ctCol ctColTo">' + esc(c.assignee_name || "(None)") + '</span>' +
              '<span class="ctCol ctColDate">' + esc(fmtGameDate(c.created_at)) + '</span>' +
              '<span class="ctCol ctColTime">' + esc(fmtTimeLeft(c.time_left)) + '</span>' +
              '<span class="ctCol ctColInfo">' + esc(c.description || "") + '</span>';
            myContractsList.appendChild(row);
          });
        }
      }
    } catch (err) {
      console.warn("Failed to load my contracts:", err);
      if (myContractsList) myContractsList.innerHTML = '<div class="contractsEmptyMsg">No Contracts Found</div>';
    }
  }

  btnGetContracts?.addEventListener("click", loadMyContracts);

  /* ══════════════════════════════════════════════════════════
     Contract Search (tab 3) — Eve Online spreadsheet style
     ══════════════════════════════════════════════════════════ */

  const searchFoundLabel = document.getElementById("searchFoundLabel");
  const searchFoundCount = document.getElementById("searchFoundCount");

  /* Item category lookup cache */
  let _itemCatalogCache = null;
  async function getItemCatalog() {
    if (_itemCatalogCache) return _itemCatalogCache;
    try {
      const resp = await fetch("/api/catalog/browse");
      if (!resp.ok) return {};
      const data = await resp.json();
      const map = {};
      (data.raw_materials || []).forEach(function (it) { map[it.id] = it; });
      (data.finished_goods || []).forEach(function (it) { map[it.id] = it; });
      (data.modules || []).forEach(function (it) { map[it.id] = it; });
      _itemCatalogCache = map;
      return map;
    } catch (e) { return {}; }
  }

  /**
   * Derive the display label, icon URI, and category for a contract.
   * Returns { label, iconUri, isMultiple }
   */
  function contractItemDisplay(contract, catalog) {
    var items = contract.items || [];
    if (items.length === 0) {
      // Fallback to title parsing
      return { label: contract.title || "Unknown", iconUri: null, isMultiple: false };
    }

    if (items.length > 1) {
      return { label: "[Multiple Items]", iconUri: null, isMultiple: true };
    }

    var it = items[0];
    var name = it.name || it.item_id || "Unknown";
    var qty = it.quantity != null ? Math.round(Number(it.quantity)) : 1;
    var label = name + " x" + qty.toLocaleString();

    // Determine category from catalog if possible
    var catInfo = catalog[it.item_id] || {};
    var category = catInfo.category || it.category || "resource";

    var iconUri = null;
    if (typeof ItemDisplay !== "undefined" && ItemDisplay.iconDataUri) {
      iconUri = ItemDisplay.iconDataUri(it.item_id || name, name, category);
    }

    return { label: label, iconUri: iconUri, isMultiple: false };
  }

  function fmtPrice(c) {
    var price = Number(c.price) || 0;
    var reward = Number(c.reward) || 0;
    var val = price || reward;
    if (!val) return "\u2014";
    return fmtMoney(val);
  }

  async function searchContracts() {
    if (searchStatus) searchStatus.textContent = "Searching\u2026";
    var params = new URLSearchParams();
    var sType = document.querySelector(".contractsSearchTypeBtn.active");
    var searchType = (sType && sType.dataset.searchType) || "buy_sell";
    params.set("search_type", searchType);

    if (searchType === "courier") {
      // Courier mode: send zone filters from icon bar
      var pz = getSelectedZone(zoneBarPickup);
      var dz = getSelectedZone(zoneBarDropoff);
      if (pz) params.set("pickup_zone", pz);
      if (dz) params.set("dropoff_zone", dz);
    } else {
      var ct = document.getElementById("searchContractType");
      if (ct && ct.value && ct.value !== "all") params.set("contract_type", ct.value);
    }

    var sortBy = document.getElementById("searchSortBy");
    params.set("sort", (sortBy && sortBy.value) || "price_asc");

    var catalog = await getItemCatalog();

    try {
      var resp = await fetch("/api/contracts/search?" + params);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      var contracts = data.contracts || [];

      if (searchResultsList) {
        searchResultsList.innerHTML = "";
        searchResultsList.classList.remove("contractsResultArea");
        searchResultsList.classList.add("csTableBody");

        if (contracts.length === 0) {
          searchResultsList.innerHTML = '<div class="contractsSearchPrompt">No contracts found matching your criteria</div>';
        } else {
          contracts.forEach(function (c) {
            var display = contractItemDisplay(c, catalog);
            var row = document.createElement("div");
            row.className = "csRow";
            row.setAttribute("data-contract-id", c.id);

            // Contract column: icon + label
            var contractCell = '<span class="csCol csColContract">';
            if (display.isMultiple) {
              contractCell += '<span class="csItemIcon csItemIconMulti" title="Multiple Items">'
                + '<svg viewBox="0 0 24 24" width="24" height="24"><rect x="2" y="2" width="9" height="9" rx="1.5" fill="#6a7a8c"/><rect x="13" y="2" width="9" height="9" rx="1.5" fill="#6a7a8c"/><rect x="2" y="13" width="9" height="9" rx="1.5" fill="#6a7a8c"/><rect x="13" y="13" width="9" height="9" rx="1.5" fill="#6a7a8c" opacity="0.4"/></svg>'
                + '</span>';
              contractCell += '<span class="csContractLabel csMultiLabel">[Multiple Items]</span>';
            } else if (display.iconUri) {
              contractCell += '<img class="csItemIcon" src="' + display.iconUri + '" alt="" />';
              contractCell += '<span class="csContractLabel">' + esc(display.label) + '</span>';
            } else {
              contractCell += '<span class="csItemIcon csItemIconBlank"></span>';
              contractCell += '<span class="csContractLabel">' + esc(display.label) + '</span>';
            }
            contractCell += '</span>';

            // Location column
            var locText = esc(c.location_name || c.location_id || "\u2014");
            if (c.contract_type === "courier" && c.destination_name) {
              locText = esc(c.location_name || c.location_id || "?") + " &rarr; " + esc(c.destination_name);
            }

            // Price column
            var priceText = fmtPrice(c);

            // Time left
            var timeText = fmtTimeLeft(c.time_left);

            // Created date
            var createdText = fmtGameDate(c.created_at);

            // Info by issuer
            var issuerInfo = esc(c.issuer_name || "\u2014");

            row.innerHTML = contractCell
              + '<span class="csCol csColLocation">' + locText + '</span>'
              + '<span class="csCol csColPrice">' + esc(priceText) + '</span>'
              + '<span class="csCol csColTimeLeft">' + esc(timeText) + '</span>'
              + '<span class="csCol csColCreated">' + esc(createdText) + '</span>'
              + '<span class="csCol csColIssuer">' + issuerInfo + '</span>';

            searchResultsList.appendChild(row);
          });
        }
      }

      var countText = "Found " + contracts.length + " contract" + (contracts.length !== 1 ? "s" : "");
      if (searchStatus) searchStatus.textContent = countText;
      if (searchFoundLabel) searchFoundLabel.textContent = countText;
      if (searchFoundCount) searchFoundCount.textContent = countText + " (0 filtered out)";
    } catch (err) {
      console.warn("Contract search failed:", err);
      if (searchResultsList) searchResultsList.innerHTML = '<div class="contractsSearchPrompt">Search failed</div>';
      if (searchStatus) searchStatus.textContent = "Search failed";
    }
  }

  btnSearchContracts?.addEventListener("click", searchContracts);

  /* ══════════════════════════════════════════════════════════
     Contract Detail Modal — Eve Online-style detail window
     ══════════════════════════════════════════════════════════ */

  var cdOverlay     = document.getElementById("contractDetailOverlay");
  var cdHeaderIcon  = document.getElementById("cdHeaderIcon");
  var cdHeaderText  = document.getElementById("cdHeaderText");
  var cdInfoSection = document.getElementById("cdInfoSection");
  var cdDetailsSection = document.getElementById("cdDetailsSection");
  var cdItemsSection = document.getElementById("cdItemsSection");
  var cdItemsHeader  = document.getElementById("cdItemsHeader");
  var cdItemsTableBody = document.getElementById("cdItemsTableBody");
  var cdBidSection   = document.getElementById("cdBidSection");
  var cdBidInput     = document.getElementById("cdBidInput");
  var cdBidBuyout    = document.getElementById("cdBidBuyout");
  var cdBtnAccept    = document.getElementById("cdBtnAccept");
  var cdBtnBid       = document.getElementById("cdBtnBid");
  var cdBtnComplete  = document.getElementById("cdBtnComplete");
  var cdBtnClose     = document.getElementById("cdBtnClose");
  var cdCloseTop     = document.getElementById("cdCloseTop");

  var _activeContract = null;

  function closeContractDetail() {
    if (cdOverlay) cdOverlay.style.display = "none";
    _activeContract = null;
  }

  if (cdBtnClose) cdBtnClose.addEventListener("click", closeContractDetail);
  if (cdCloseTop) cdCloseTop.addEventListener("click", closeContractDetail);
  if (cdOverlay) cdOverlay.addEventListener("click", function (e) {
    if (e.target === cdOverlay) closeContractDetail();
  });

  /** Build an info row: label  value */
  function cdInfoRow(label, value, cls) {
    return '<div class="cdInfoRow">'
      + '<span class="cdInfoLabel">' + esc(label) + '</span>'
      + '<span class="cdInfoValue' + (cls ? " " + cls : "") + '">' + value + '</span>'
      + '</div>';
  }

  /** Compute total volume & mass from items */
  function cdItemTotals(items) {
    var vol = 0, mass = 0;
    (items || []).forEach(function (it) {
      vol  += Number(it.volume_m3) || 0;
      mass += Number(it.mass_kg) || 0;
    });
    return { volume_m3: vol, mass_kg: mass };
  }

  /** Open the detail modal for a contract */
  async function openContractDetail(contractId) {
    if (!cdOverlay) return;
    try {
      var resp = await fetch("/api/contracts/" + contractId);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      var c = data.contract;
      _activeContract = c;

      var catalog = await getItemCatalog();
      var display = contractItemDisplay(c, catalog);
      var items = c.items || [];
      var totals = cdItemTotals(items);
      var ctype = c.contract_type;

      /* ── Header icon + summary ── */
      if (display.iconUri && !display.isMultiple) {
        cdHeaderIcon.innerHTML = '<img class="cdHeaderImg" src="' + display.iconUri + '" alt="" />';
      } else if (display.isMultiple) {
        cdHeaderIcon.innerHTML = '<svg viewBox="0 0 48 48" width="42" height="42"><rect x="4" y="4" width="18" height="18" rx="2.5" fill="#8a9bb0"/><rect x="26" y="4" width="18" height="18" rx="2.5" fill="#8a9bb0"/><rect x="4" y="26" width="18" height="18" rx="2.5" fill="#8a9bb0"/><rect x="26" y="26" width="18" height="18" rx="2.5" fill="#8a9bb0" opacity="0.4"/></svg>';
      } else {
        cdHeaderIcon.innerHTML = '<div class="cdHeaderIconBlank"></div>';
      }

      // Header summary line varies by type
      var headerSummary = "";
      if (ctype === "courier") {
        headerSummary = esc(c.location_name || "?") + " &raquo; " + esc(c.destination_name || "?");
        if (totals.volume_m3 > 0) headerSummary += " (" + fmtVol(totals.volume_m3) + " m&sup3;)";
        headerSummary += " (Courier)";
      } else if (ctype === "auction") {
        headerSummary = esc(display.label) + " (Auction)";
      } else {
        headerSummary = esc(display.label) + " (Item Exchange)";
      }
      cdHeaderText.innerHTML = headerSummary;

      /* ── Info fields section ── */
      var infoHtml = "";
      infoHtml += cdInfoRow("Info by Issuer", esc(c.description || "(None)"));
      infoHtml += cdInfoRow("Type", esc(fmtContractType(ctype)));
      infoHtml += cdInfoRow("Issued By", esc(c.issuer_name || "\u2014"));
      infoHtml += cdInfoRow("Availability", esc(c.availability === "private" ? "Private" : "Public"));
      infoHtml += cdInfoRow("Status", esc(c.status ? c.status.charAt(0).toUpperCase() + c.status.slice(1).replace("_", " ") : "Outstanding"));
      infoHtml += cdInfoRow("Location", esc(c.location_name || c.location_id || "\u2014"));
      infoHtml += cdInfoRow("Date Issued", esc(fmtGameDate(c.created_at)));
      infoHtml += cdInfoRow("Expiration Date", esc(fmtGameDate(c.expires_at)) + " (" + esc(c.time_left) + ")");
      cdInfoSection.innerHTML = infoHtml;

      /* ── Type-specific details ── */
      var detailsHtml = "";
      if (ctype === "courier") {
        detailsHtml += cdInfoRow("Complete In", esc(c.time_left));
        detailsHtml += cdInfoRow("Volume", fmtVol(totals.volume_m3) + " m&sup3;");
        detailsHtml += cdInfoRow("Mass", fmtKg(totals.mass_kg));
        detailsHtml += cdInfoRow("Reward", '<span class="cdValGreen">$' + esc(fmtMoney(c.reward)) + '</span>');
        if (c.destination_name) {
          detailsHtml += cdInfoRow("Destination", esc(c.destination_name));
        }
        if (c.courier_container_id && c.status === "in_progress") {
          detailsHtml += cdInfoRow("Cargo Container", '<span style="color:#e0a030;">Sealed \u{1F4E6}</span>');
        }
      } else if (ctype === "auction") {
        var startBid = Number(c.price) || 0;
        var buyout = Number(c.buyout_price) || 0;
        var currentBid = Number(c.current_bid) || 0;
        detailsHtml += cdInfoRow("Starting Bid", "$" + esc(fmtMoney(startBid)));
        if (buyout > 0) detailsHtml += cdInfoRow("Buyout Price", "$" + esc(fmtMoney(buyout)));
        detailsHtml += cdInfoRow("Current Bid", currentBid > 0 ? "$" + esc(fmtMoney(currentBid)) : "No Bids");
        detailsHtml += cdInfoRow("Time Left", esc(c.time_left));
      } else {
        // Item exchange
        var price = Number(c.price) || 0;
        if (price > 0) {
          detailsHtml += cdInfoRow("Price", '<span class="cdValGreen">$' + esc(fmtMoney(price)) + '</span>');
        } else {
          detailsHtml += cdInfoRow("Price", "Barter / Trade");
        }
        detailsHtml += cdInfoRow("Volume", fmtVol(totals.volume_m3) + " m&sup3;");
        detailsHtml += cdInfoRow("Mass", fmtKg(totals.mass_kg));
      }
      cdDetailsSection.innerHTML = detailsHtml;

      /* ── Items table ── */
      if (items.length > 0) {
        cdItemsSection.style.display = "block";
        cdItemsHeader.textContent = ctype === "courier" ? "Items to Transport" : "You Will Get";
        cdItemsTableBody.innerHTML = "";
        items.forEach(function (it) {
          var name = it.name || it.item_id || "Unknown";
          var qty = it.quantity != null ? Math.round(Number(it.quantity)).toLocaleString() : "1";
          var catInfo = catalog[it.item_id] || {};
          var category = catInfo.category || it.category || "resource";
          var catLabel = category.replace(/_/g, " ");
          catLabel = catLabel.charAt(0).toUpperCase() + catLabel.slice(1);

          var iconUri = "";
          if (typeof ItemDisplay !== "undefined" && ItemDisplay.iconDataUri) {
            iconUri = ItemDisplay.iconDataUri(it.item_id || name, name, category);
          }

          var tr = document.createElement("div");
          tr.className = "cdItemRow";
          tr.innerHTML = '<span class="cdItemCol cdItemName">'
            + (iconUri ? '<img class="cdItemRowIcon" src="' + iconUri + '" alt="" /> ' : '')
            + esc(name) + '</span>'
            + '<span class="cdItemCol cdItemQty">' + esc(qty) + '</span>'
            + '<span class="cdItemCol cdItemType">' + esc(catLabel) + '</span>';
          cdItemsTableBody.appendChild(tr);
        });
      } else {
        cdItemsSection.style.display = "none";
      }

      /* ── Bid section (auctions only) ── */
      if (ctype === "auction" && c.status === "outstanding") {
        cdBidSection.style.display = "block";
        cdBtnBid.style.display = "inline-block";
        cdBtnAccept.style.display = "none";
        var minBid = (Number(c.current_bid) || 0) > 0 ? Number(c.current_bid) : Number(c.price);
        cdBidInput.value = minBid;
        cdBidInput.min = minBid;
        if (cdBidBuyout) cdBidBuyout.checked = false;
        // Wire buyout checkbox
        cdBidBuyout.onchange = function () {
          if (cdBidBuyout.checked && Number(c.buyout_price) > 0) {
            cdBidInput.value = c.buyout_price;
            cdBidInput.disabled = true;
          } else {
            cdBidInput.disabled = false;
            cdBidInput.value = minBid;
          }
        };
      } else {
        cdBidSection.style.display = "none";
        cdBtnBid.style.display = "none";
      }

      /* ── Accept button (item exchange + courier) ── */
      if ((ctype === "item_exchange" || ctype === "courier") && c.status === "outstanding") {
        cdBtnAccept.style.display = "inline-block";
      } else {
        cdBtnAccept.style.display = "none";
      }

      /* ── Complete Delivery button (in-progress courier contracts) ── */
      if (ctype === "courier" && c.status === "in_progress") {
        cdBtnComplete.style.display = "inline-block";
      } else {
        cdBtnComplete.style.display = "none";
      }

      cdOverlay.style.display = "flex";
    } catch (err) {
      console.warn("Failed to open contract detail:", err);
    }
  }

  /* ── Accept handler ── */
  if (cdBtnAccept) cdBtnAccept.addEventListener("click", async function () {
    if (!_activeContract) return;
    try {
      var resp = await fetch("/api/contracts/" + _activeContract.id + "/accept", { method: "POST" });
      if (!resp.ok) {
        var err = await resp.json().catch(function () { return {}; });
        alert(err.detail || "Failed to accept contract.");
        return;
      }
      closeContractDetail();
      searchContracts(); // refresh
      loadIncomingContracts();
    } catch (e) {
      alert("Error: " + e.message);
    }
  });

  /* ── Complete Delivery handler (courier contracts) ── */
  if (cdBtnComplete) cdBtnComplete.addEventListener("click", async function () {
    if (!_activeContract) return;
    try {
      var resp = await fetch("/api/contracts/" + _activeContract.id + "/complete", { method: "POST" });
      if (!resp.ok) {
        var err = await resp.json().catch(function () { return {}; });
        alert(err.detail || "Failed to complete delivery.");
        return;
      }
      alert("Delivery completed! Reward has been paid.");
      closeContractDetail();
      searchContracts();
      loadIncomingContracts();
    } catch (e) {
      alert("Error: " + e.message);
    }
  });

  /* ── Bid handler ── */
  if (cdBtnBid) cdBtnBid.addEventListener("click", async function () {
    if (!_activeContract) return;
    var bidVal = Number(cdBidInput.value);
    if (!bidVal || bidVal <= 0) { alert("Enter a valid bid amount."); return; }
    try {
      var resp = await fetch("/api/contracts/" + _activeContract.id + "/bid", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bid_amount: bidVal }),
      });
      if (!resp.ok) {
        var err = await resp.json().catch(function () { return {}; });
        alert(err.detail || "Failed to place bid.");
        return;
      }
      var result = await resp.json();
      if (result.is_buyout) {
        alert("Buyout successful!");
      } else {
        alert("Bid placed: $" + fmtMoney(result.new_bid));
      }
      closeContractDetail();
      searchContracts(); // refresh
    } catch (e) {
      alert("Error: " + e.message);
    }
  });

  /* ── Double-click handler on search rows ── */
  if (searchResultsList) {
    searchResultsList.addEventListener("dblclick", function (e) {
      var row = e.target.closest(".csRow");
      if (!row) return;
      var cid = row.getAttribute("data-contract-id");
      if (cid) openContractDetail(cid);
    });
  }

  /* ── Double-click on Incoming Contracts rows ── */
  if (incomingListEl) {
    incomingListEl.addEventListener("dblclick", function (e) {
      var row = e.target.closest(".contractsIncomingRow");
      if (!row) return;
      var cid = row.getAttribute("data-contract-id");
      if (cid) openContractDetail(cid);
    });
  }

  /* ── Double-click on My Contracts rows ── */
  if (myContractsList) {
    myContractsList.addEventListener("dblclick", function (e) {
      var row = e.target.closest(".contractsMyRow");
      if (!row) return;
      var cid = row.getAttribute("data-contract-id");
      if (cid) openContractDetail(cid);
    });
  }

  /* ══════════════════════════════════════════════════════════
     CREATE CONTRACT — navigates to dedicated page
     ══════════════════════════════════════════════════════════ */

  function openCreateContractPage() {
    window.location.href = "/contracts/create";
  }

  /* ── Create Contract button handlers ──────────────────── */
  var btnCreate1 = document.getElementById("btnCreateContractIncoming");
  if (btnCreate1) btnCreate1.addEventListener("click", openCreateContractPage);
  var btnCreate2 = document.getElementById("btnCreateContractMy");
  if (btnCreate2) btnCreate2.addEventListener("click", openCreateContractPage);

  /* ══════════════════════════════════════════════════════════
     Helpers
     ══════════════════════════════════════════════════════════ */
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtNum(v) {
    var n = Number(v) || 0;
    return n === Math.floor(n) ? String(n) : n.toFixed(1);
  }

  function fmtVol(v) {
    var n = Number(v) || 0;
    return n.toFixed(1);
  }

  function fmtKg(v) {
    var n = Number(v) || 0;
    if (n >= 1000) return (n / 1000).toFixed(2) + " t";
    return n.toFixed(1) + " kg";
  }

  function fmtMoney(v) {
    var n = Number(v) || 0;
    if (n >= 1e9) return (n / 1e9).toFixed(2) + " Billion";
    if (n >= 1e6) return (n / 1e6).toFixed(2) + " Million";
    return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  /* ── Populate org name ────────────────────────────────── */
  async function loadUserInfo() {
    try {
      var resp = await fetch("/api/auth/me");
      if (!resp.ok) return;
      var me = await resp.json();
      if (myContractOwnerMe && me.org_name) {
        myContractOwnerMe.textContent = me.org_name;
      }
      wizOrgName = me.org_name || me.username || "";
    } catch (e) {
      // ignore
    }
  }

  /* ── Init ─────────────────────────────────────────────── */
  loadUserInfo();
  loadIncomingContracts();
})();
