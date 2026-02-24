(function () {
  // ── DOM refs ────────────────────────────────────────────────────────────────
  const orgInfo = document.getElementById("orgInfo");
  const orgStatGrid = document.getElementById("orgStatGrid");
  const orgBalance = document.getElementById("orgBalance");
  const orgIncome = document.getElementById("orgIncome");
  const orgResearchPoints = document.getElementById("orgResearchPoints");
  const orgExpenses = document.getElementById("orgExpenses");
  const teamSummary = document.getElementById("teamSummary");
  const teamsList = document.getElementById("teamsList");
  const hireTeamBtn = document.getElementById("hireTeamBtn");
  const hireTeamStatus = document.getElementById("hireTeamStatus");
  const accountInfo = document.getElementById("accountInfo");
  const logoutBtn = document.getElementById("logoutBtn");

  const boostItemsContainer = document.getElementById("boostItemsContainer");
  const boostAddItemBtn = document.getElementById("boostAddItemBtn");
  const boostMass = document.getElementById("boostMass");
  const boostCost = document.getElementById("boostCost");
  const boostBtn = document.getElementById("boostBtn");
  const boostStatus = document.getElementById("boostStatus");
  const boostHistory = document.getElementById("boostHistory");
  const orgTabBtnOverview = document.getElementById("orgTabBtnOverview");
  const orgTabBtnFinances = document.getElementById("orgTabBtnFinances");
  const orgTabBtnMarketplace = document.getElementById("orgTabBtnMarketplace");
  const orgTabOverview = document.getElementById("orgTabOverview");
  const orgTabFinances = document.getElementById("orgTabFinances");
  const orgTabMarketplace = document.getElementById("orgTabMarketplace");
  const loanStatus = document.getElementById("loanStatus");
  const loanOffers = document.getElementById("loanOffers");
  const marketPriceMeta = document.getElementById("marketPriceMeta");
  const marketPriceRows = document.getElementById("marketPriceRows");
  const marketSellRows = document.getElementById("marketSellRows");
  const marketSellStatus = document.getElementById("marketSellStatus");

  let currentOrg = null;
  let boostableItems = [];
  let boostSelectionRows = [];
  let marketplaceLoaded = false;

  // ── Formatting helpers ──────────────────────────────────────────────────────
  function fmtUsd(n) {
    if (n == null) return "—";
    if (Math.abs(n) >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
    if (Math.abs(n) >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
    if (Math.abs(n) >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K";
    return "$" + n.toFixed(0);
  }

  function fmtPoints(n) {
    if (n == null) return "—";
    return n.toFixed(1) + " RP";
  }

  function fmtMass(kg) {
    if (kg == null) return "—";
    if (kg >= 1000) return (kg / 1000).toFixed(2) + " t";
    return kg.toFixed(1) + " kg";
  }

  function fmtPct(v) {
    if (v == null) return "—";
    return (Number(v) * 100).toFixed(1) + "%";
  }

  function fmtSignedPct(pct) {
    const n = Number(pct) || 0;
    const sign = n >= 0 ? "+" : "";
    return sign + n.toFixed(1) + "%";
  }

  function fmtKg(kg) {
    const n = Number(kg) || 0;
    return n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 1 }) + " kg";
  }

  function redirectToLogin() {
    try {
      if (window.top && window.top !== window) {
        window.top.location.href = "/login";
        return;
      }
    } catch { /* noop */ }
    window.location.href = "/login";
  }

  // ── Load Org State ──────────────────────────────────────────────────────────
  async function loadOrg() {
    const resp = await fetch("/api/org", { cache: "no-store" });
    if (!resp.ok) {
      if (resp.status === 401) { redirectToLogin(); return; }
      orgInfo.textContent = "Failed to load organization.";
      return;
    }
    const data = await resp.json();
    currentOrg = data.org || {};
    renderOrg();
  }

  function renderOrg() {
    if (!currentOrg || !currentOrg.id) {
      orgInfo.textContent = "No organization found.";
      return;
    }
    orgInfo.style.display = "none";
    orgStatGrid.style.display = "";

    orgBalance.textContent = fmtUsd(currentOrg.balance_usd);
    orgIncome.textContent = fmtUsd(currentOrg.income_per_month_usd) + "/mo";
    orgResearchPoints.textContent = fmtPoints(currentOrg.research_points);

    const teams = currentOrg.research_teams || [];
    const activeTeams = teams.filter(t => t.status === "active");
    const monthlyExpense = Number.isFinite(Number(currentOrg.monthly_expenses_usd))
      ? Number(currentOrg.monthly_expenses_usd)
      : (activeTeams.length * (currentOrg.team_cost_per_month_usd || 150000000));
    orgExpenses.textContent = fmtUsd(monthlyExpense) + "/mo";

    // Teams
    teamSummary.textContent = `${activeTeams.length} active team${activeTeams.length !== 1 ? "s" : ""}` +
      ` · ${(currentOrg.team_points_per_week || 5)} RP/week each`;

    teamsList.innerHTML = "";
    if (activeTeams.length === 0) {
      teamsList.innerHTML = '<div class="muted small">No research teams hired yet.</div>';
    } else {
      for (const team of activeTeams) {
        const row = document.createElement("div");
        row.className = "orgTeamRow";
        row.innerHTML = `
          <span class="orgTeamName">Research Team</span>
          <span class="muted small">${fmtUsd(team.cost_per_month_usd)}/mo · ${team.points_per_week} RP/wk</span>
          <button class="btnDanger btnSmall fireTeamBtn" data-id="${team.id}" type="button">Dismiss</button>
        `;
        teamsList.appendChild(row);
      }
      teamsList.querySelectorAll(".fireTeamBtn").forEach(btn => {
        btn.addEventListener("click", () => fireTeam(btn.dataset.id));
      });
    }

    // Account
    const members = currentOrg.members || [];
    accountInfo.innerHTML = `<div><b>Organization:</b> ${currentOrg.name || "Unknown"}</div>` +
      `<div><b>Members:</b> ${members.join(", ")}</div>`;

    renderLoanOffers(currentOrg.loan_offers || []);
  }

  function setOrgTab(tabName) {
    const showOverview = tabName === "overview";
    const showFinances = tabName === "finances";
    const showMarketplace = tabName === "marketplace";
    if (orgTabOverview) orgTabOverview.style.display = showOverview ? "grid" : "none";
    if (orgTabFinances) orgTabFinances.style.display = showFinances ? "block" : "none";
    if (orgTabMarketplace) orgTabMarketplace.style.display = showMarketplace ? "block" : "none";
    if (orgTabBtnOverview) {
      orgTabBtnOverview.classList.toggle("active", showOverview);
      orgTabBtnOverview.setAttribute("aria-selected", String(showOverview));
    }
    if (orgTabBtnFinances) {
      orgTabBtnFinances.classList.toggle("active", showFinances);
      orgTabBtnFinances.setAttribute("aria-selected", String(showFinances));
    }
    if (orgTabBtnMarketplace) {
      orgTabBtnMarketplace.classList.toggle("active", showMarketplace);
      orgTabBtnMarketplace.setAttribute("aria-selected", String(showMarketplace));
    }
    if (showMarketplace && !marketplaceLoaded) {
      loadMarketplace();
    }
  }

  function renderMarketplacePrices(prices) {
    if (!marketPriceRows) return;
    marketPriceRows.innerHTML = "";
    if (!Array.isArray(prices) || prices.length === 0) {
      const empty = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.className = "muted small";
      cell.textContent = "No market prices available.";
      empty.appendChild(cell);
      marketPriceRows.appendChild(empty);
      return;
    }

    for (const price of prices) {
      const tr = document.createElement("tr");

      const resourceCell = document.createElement("td");
      resourceCell.textContent = price.resource_name || price.resource_id || "Unknown";
      tr.appendChild(resourceCell);

      const baseCell = document.createElement("td");
      baseCell.textContent = fmtUsd(Number(price.base_price_per_kg) || 0);
      tr.appendChild(baseCell);

      const modCell = document.createElement("td");
      modCell.textContent = fmtSignedPct(Number(price.modifier_pct) || 0);
      tr.appendChild(modCell);

      const marketCell = document.createElement("td");
      marketCell.textContent = fmtUsd(Number(price.market_price_per_kg) || 0);
      tr.appendChild(marketCell);

      marketPriceRows.appendChild(tr);
    }
  }

  function createSellRow(item) {
    const row = document.createElement("div");
    row.className = "orgMarketSellRow";

    const head = document.createElement("div");
    head.className = "orgMarketSellHead";

    const left = document.createElement("div");
    left.className = "orgMarketSellLeft";

    const title = document.createElement("div");
    title.className = "orgMarketSellTitle";
    title.textContent = `${item.resource_name} · ${item.source_kind === "ship" ? `Ship: ${item.source_name}` : "LEO Inventory"}`;

    const meta = document.createElement("div");
    meta.className = "muted small";
    meta.textContent = `${fmtKg(item.available_mass_kg)} available · ${fmtUsd(item.unit_price_usd_per_kg)}/kg`;

    const sellBtn = document.createElement("button");
    sellBtn.type = "button";
    sellBtn.className = "btnPrimary btnSmall";
    sellBtn.textContent = "Sell";

    left.appendChild(title);
    left.appendChild(meta);
    head.appendChild(left);
    head.appendChild(sellBtn);

    const controls = document.createElement("div");
    controls.className = "orgMarketSellControls";
    controls.style.display = "none";

    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "0";
    slider.max = String(Number(item.available_mass_kg) || 0);
    slider.value = String(Number(item.available_mass_kg) || 0);
    slider.step = String(Math.max(0.1, (Number(item.available_mass_kg) || 0) / 500));
    slider.className = "orgMarketSellSlider";

    const preview = document.createElement("div");
    preview.className = "muted small";

    const confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.className = "btnPrimary btnSmall";
    confirmBtn.textContent = "Confirm Sale";

    function updatePreview() {
      const qty = Math.max(0, Number(slider.value) || 0);
      const proceeds = qty * (Number(item.unit_price_usd_per_kg) || 0);
      preview.textContent = `${fmtKg(qty)} → ${fmtUsd(proceeds)}`;
      confirmBtn.disabled = qty <= 0;
    }

    slider.addEventListener("input", updatePreview);
    updatePreview();

    confirmBtn.addEventListener("click", async () => {
      const qty = Math.max(0, Number(slider.value) || 0);
      if (qty <= 0) return;

      confirmBtn.disabled = true;
      sellBtn.disabled = true;
      if (marketSellStatus) marketSellStatus.textContent = "Selling resource…";
      try {
        const resp = await fetch("/api/org/marketplace/sell", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            source_kind: item.source_kind,
            source_id: item.source_id,
            resource_id: item.resource_id,
            mass_kg: qty,
          }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          if (marketSellStatus) marketSellStatus.textContent = data.detail || "Sale failed.";
          return;
        }
        if (marketSellStatus) {
          marketSellStatus.textContent = `Sold ${fmtKg(data.sold_mass_kg)} of ${data.resource_name} for ${fmtUsd(data.proceeds_usd)}.`;
        }
        await loadOrg();
        marketplaceLoaded = false;
        await loadMarketplace();
      } catch (e) {
        if (marketSellStatus) marketSellStatus.textContent = "Error: " + e.message;
      } finally {
        confirmBtn.disabled = false;
        sellBtn.disabled = false;
      }
    });

    controls.appendChild(slider);
    controls.appendChild(preview);
    controls.appendChild(confirmBtn);

    sellBtn.addEventListener("click", () => {
      controls.style.display = controls.style.display === "none" ? "grid" : "none";
    });

    row.appendChild(head);
    row.appendChild(controls);
    return row;
  }

  function renderMarketplaceSellables(items) {
    if (!marketSellRows) return;
    marketSellRows.innerHTML = "";
    if (!Array.isArray(items) || items.length === 0) {
      const empty = document.createElement("div");
      empty.className = "muted small";
      empty.textContent = "No sellable resources in LEO or ships in LEO.";
      marketSellRows.appendChild(empty);
      return;
    }
    for (const item of items) {
      marketSellRows.appendChild(createSellRow(item));
    }
  }

  async function loadMarketplace() {
    if (!marketPriceRows || !marketSellRows) return;
    if (marketPriceMeta) marketPriceMeta.textContent = "Loading market prices…";
    marketPriceRows.innerHTML = '<tr><td colspan="4" class="muted small">Loading prices…</td></tr>';
    marketSellRows.innerHTML = '<div class="muted small">Loading sellable resources…</div>';
    try {
      const resp = await fetch("/api/org/marketplace", { cache: "no-store" });
      if (!resp.ok) {
        let msg = "Failed to load marketplace.";
        try {
          const body = await resp.json();
          if (body && body.detail) msg = String(body.detail);
        } catch (_) {}
        if (marketPriceMeta) marketPriceMeta.textContent = msg;
        if (marketSellRows) marketSellRows.innerHTML = `<div class="muted small">${msg}</div>`;
        return;
      }
      const data = await resp.json();
      if (marketPriceMeta) {
        marketPriceMeta.textContent = `In-game month #${Number(data.month_index) || 0} · LEO location: ${data.leo_location_id || "LEO"}`;
      }
      renderMarketplacePrices(data.prices || []);
      renderMarketplaceSellables(data.sellable || []);
      marketplaceLoaded = true;
    } catch (e) {
      const msg = "Failed to load marketplace: " + e.message;
      if (marketPriceMeta) marketPriceMeta.textContent = msg;
      if (marketSellRows) marketSellRows.innerHTML = `<div class="muted small">${msg}</div>`;
    }
  }

  function renderLoanOffers(loans) {
    if (!loanOffers) return;
    loanOffers.innerHTML = "";
    if (!Array.isArray(loans) || loans.length === 0) {
      loanOffers.innerHTML = '<div class="muted small">No loan options available.</div>';
      return;
    }

    for (const loan of loans) {
      const card = document.createElement("div");
      card.className = "orgLoanCard";

      const tracker = loan.tracker;
      const progress = tracker && Number.isFinite(Number(tracker.remaining_percent))
        ? Math.max(0, Math.min(100, Number(tracker.remaining_percent) * 100))
        : 0;

      card.innerHTML = `
        <div class="orgLoanTitleRow">
          <div><b>${fmtUsd(loan.principal_usd)}</b> · ${loan.term_years} year${loan.term_years !== 1 ? "s" : ""}</div>
          <div class="muted small">${fmtPct(loan.annual_interest_rate)} interest</div>
        </div>
        <div class="orgLoanMeta">
          <div class="orgLoanMetaItem">
            <div class="orgLoanMetaLabel">Monthly Payment</div>
            <div class="orgLoanMetaValue">${fmtUsd(loan.monthly_payment_usd)}/mo</div>
          </div>
          <div class="orgLoanMetaItem">
            <div class="orgLoanMetaLabel">Total Payable</div>
            <div class="orgLoanMetaValue">${fmtUsd(loan.total_payable_usd)}</div>
          </div>
          <div class="orgLoanMetaItem">
            <div class="orgLoanMetaLabel">Term</div>
            <div class="orgLoanMetaValue">${loan.term_months} months</div>
          </div>
        </div>
        ${tracker ? `
          <div class="orgLoanTracker">
            <div class="muted small">Remaining: <b>${fmtUsd(tracker.remaining_balance_usd)}</b></div>
            <div class="orgLoanProgress"><div class="orgLoanProgressFill" style="width:${progress.toFixed(1)}%"></div></div>
          </div>
        ` : '<div class="muted small">No active loan for this package.</div>'}
        <div>
          <button class="btnPrimary activateLoanBtn" type="button" data-loan-code="${loan.loan_code}" ${loan.can_activate ? "" : "disabled"}>
            ${loan.can_activate ? "Activate Loan" : "Active (Pay Off First)"}
          </button>
        </div>
      `;
      loanOffers.appendChild(card);
    }
  }

  async function activateLoan(loanCode) {
    if (!loanCode) return;
    if (loanStatus) loanStatus.textContent = "Activating loan…";
    try {
      const resp = await fetch("/api/org/loans/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ loan_code: loanCode }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        if (loanStatus) loanStatus.textContent = data.detail || "Failed to activate loan.";
        return;
      }
      if (loanStatus) loanStatus.textContent = `Loan activated: +${fmtUsd(data.principal_usd)} · ${fmtUsd(data.monthly_payment_usd)}/mo payment`;
      await loadOrg();
    } catch (e) {
      if (loanStatus) loanStatus.textContent = "Error: " + e.message;
    }
  }

  // ── Hire/Fire Teams ─────────────────────────────────────────────────────────
  async function hireTeam() {
    hireTeamBtn.disabled = true;
    hireTeamStatus.textContent = "Hiring…";
    try {
      const resp = await fetch("/api/org/hire-team", { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) {
        hireTeamStatus.textContent = data.detail || "Failed to hire team.";
        return;
      }
      hireTeamStatus.textContent = "Team hired!";
      await loadOrg();
    } catch (e) {
      hireTeamStatus.textContent = "Error: " + e.message;
    } finally {
      hireTeamBtn.disabled = false;
    }
  }

  async function fireTeam(teamId) {
    if (!confirm("Dismiss this research team?")) return;
    try {
      const resp = await fetch("/api/org/fire-team", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ team_id: teamId }),
      });
      if (!resp.ok) {
        const data = await resp.json();
        alert(data.detail || "Failed to dismiss team.");
        return;
      }
      await loadOrg();
    } catch (e) {
      alert("Error: " + e.message);
    }
  }

  // ── LEO Boost ───────────────────────────────────────────────────────────────
  function buildBoostItemOptions(selectEl, selectedItemId = "") {
    if (!selectEl) return;
    selectEl.innerHTML = '<option value="">Select item…</option>';

    const groups = Object.create(null);
    for (const item of boostableItems) {
      const groupName = item.type || "other";
      if (!groups[groupName]) groups[groupName] = [];
      groups[groupName].push(item);
    }

    for (const [groupName, items] of Object.entries(groups).sort((a, b) => String(a[0]).localeCompare(String(b[0])))) {
      const optGroup = document.createElement("optgroup");
      optGroup.label = String(groupName).charAt(0).toUpperCase() + String(groupName).slice(1);
      for (const item of items.sort((a, b) => String(a.name || a.item_id || "").localeCompare(String(b.name || b.item_id || "")))) {
        const opt = document.createElement("option");
        opt.value = item.item_id;
        opt.textContent = `${String(item.name || item.item_id || "Unknown")} (${fmtMass(Number(item.mass_per_unit_kg) || 0)})`;
        optGroup.appendChild(opt);
      }
      selectEl.appendChild(optGroup);
    }
    selectEl.value = selectedItemId || "";
  }

  function addBoostSelectionRow(initial = {}) {
    if (!boostItemsContainer) return;

    const rowEl = document.createElement("div");
    rowEl.className = "orgBoostLine";

    const itemSelect = document.createElement("select");
    itemSelect.className = "orgSelect";
    buildBoostItemOptions(itemSelect, String(initial.item_id || ""));

    const quantityInput = document.createElement("input");
    quantityInput.type = "number";
    quantityInput.className = "orgInput";
    quantityInput.min = "1";
    quantityInput.step = "1";
    quantityInput.value = String(Math.max(1, Number(initial.quantity) || 1));

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "btnSecondary btnSmall orgBoostRemoveBtn";
    removeBtn.textContent = "−";
    removeBtn.setAttribute("aria-label", "Remove payload item");

    itemSelect.addEventListener("change", updateBoostPreview);
    quantityInput.addEventListener("input", updateBoostPreview);
    removeBtn.addEventListener("click", () => {
      if (boostSelectionRows.length <= 1) return;
      boostSelectionRows = boostSelectionRows.filter((row) => row.rowEl !== rowEl);
      rowEl.remove();
      refreshBoostRemoveButtons();
      updateBoostPreview();
    });

    rowEl.appendChild(itemSelect);
    rowEl.appendChild(quantityInput);
    rowEl.appendChild(removeBtn);
    boostItemsContainer.appendChild(rowEl);

    boostSelectionRows.push({ rowEl, itemSelect, quantityInput, removeBtn });
    refreshBoostRemoveButtons();
    updateBoostPreview();
  }

  function refreshBoostRowsOptions() {
    for (const row of boostSelectionRows) {
      const selected = row.itemSelect.value;
      buildBoostItemOptions(row.itemSelect, selected);
    }
  }

  function refreshBoostRemoveButtons() {
    const canRemove = boostSelectionRows.length > 1;
    for (const row of boostSelectionRows) {
      row.removeBtn.disabled = !canRemove;
    }
  }

  function getSelectedBoostManifest() {
    const manifest = [];
    for (const row of boostSelectionRows) {
      const itemId = String(row.itemSelect.value || "").trim();
      const qty = Math.max(0, Number(row.quantityInput.value) || 0);
      if (!itemId || qty <= 0) continue;
      manifest.push({ item_id: itemId, quantity: qty });
    }
    return manifest;
  }

  async function loadBoostableItems() {
    try {
      const resp = await fetch("/api/org/boostable-items", { cache: "no-store" });
      if (!resp.ok) {
        let msg = "Failed to load boost options.";
        try {
          const body = await resp.json();
          if (body && body.detail) msg = String(body.detail);
        } catch (_) {}
        if (boostStatus) boostStatus.textContent = msg;
        return;
      }
      const data = await resp.json();
      boostableItems = Array.isArray(data.items) ? data.items : [];
      refreshBoostRowsOptions();
      if (boostSelectionRows.length === 0) addBoostSelectionRow();

      if (boostableItems.length === 0 && boostStatus) {
        boostStatus.textContent = "No boost-eligible items available right now.";
      }
      updateBoostPreview();
    } catch (e) {
      console.error("loadBoostableItems error:", e);
      if (boostStatus) boostStatus.textContent = "Failed to load boost options.";
    }
  }

  function updateBoostPreview() {
    const manifest = getSelectedBoostManifest();
    if (manifest.length === 0) {
      boostMass.textContent = "—";
      boostCost.textContent = "—";
      boostBtn.disabled = true;
      return;
    }

    let totalMass = 0;
    for (const line of manifest) {
      const item = boostableItems.find((b) => b.item_id === line.item_id);
      if (!item) continue;
      totalMass += (Number(item.mass_per_unit_kg) || 0) * line.quantity;
    }
    if (totalMass <= 0) {
      boostMass.textContent = "—";
      boostCost.textContent = "—";
      boostBtn.disabled = true;
      return;
    }

    const cost = 100000000 + 5000 * totalMass;
    boostMass.textContent = fmtMass(totalMass);
    boostCost.textContent = fmtUsd(cost);
    boostBtn.disabled = false;
  }

  async function loadBoostHistory() {
    try {
      const resp = await fetch("/api/org/boost-history", { cache: "no-store" });
      if (!resp.ok) return;
      const data = await resp.json();
      const history = data.history || [];
      if (history.length === 0) {
        boostHistory.innerHTML = '<span class="muted small">No launches yet.</span>';
        return;
      }
      boostHistory.innerHTML = "";
      for (const entry of history) {
        const row = document.createElement("div");
        row.className = "orgBoostHistoryRow";
        row.innerHTML =
          `<span class="orgBoostHistoryItem">${entry.item_name} x${entry.quantity}</span>` +
          `<span class="muted small">${fmtMass(entry.mass_kg)} · ${fmtUsd(entry.cost_usd)} → ${entry.destination}</span>`;
        boostHistory.appendChild(row);
      }
    } catch { /* ignore */ }
  }

  async function doBoost() {
    const manifest = getSelectedBoostManifest();
    if (manifest.length === 0) return;

    boostBtn.disabled = true;
    boostStatus.textContent = "Launching…";
    try {
      const resp = await fetch("/api/org/boost", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: manifest }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        boostStatus.textContent = data.detail || "Boost failed.";
        return;
      }
      const launchItems = Number(data.item_count) || manifest.length;
      boostStatus.textContent = `Launched ${launchItems} item${launchItems !== 1 ? "s" : ""} to ${data.destination} (${fmtUsd(Number(data.cost_usd) || 0)})`;
      await loadOrg();
      await loadBoostHistory();
    } catch (e) {
      boostStatus.textContent = "Error: " + e.message;
    } finally {
      boostBtn.disabled = false;
    }
  }

  // ── Event listeners ─────────────────────────────────────────────────────────
  hireTeamBtn?.addEventListener("click", hireTeam);
  boostAddItemBtn?.addEventListener("click", () => addBoostSelectionRow());
  boostBtn?.addEventListener("click", doBoost);
  orgTabBtnOverview?.addEventListener("click", () => setOrgTab("overview"));
  orgTabBtnFinances?.addEventListener("click", () => setOrgTab("finances"));
  orgTabBtnMarketplace?.addEventListener("click", () => setOrgTab("marketplace"));
  loanOffers?.addEventListener("click", (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLElement)) return;
    const button = target.closest(".activateLoanBtn");
    if (!button) return;
    activateLoan(button.getAttribute("data-loan-code") || "");
  });

  logoutBtn?.addEventListener("click", async () => {
    logoutBtn.disabled = true;
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      redirectToLogin();
    }
  });

  // ── Auto-refresh ────────────────────────────────────────────────────────────
  if (boostItemsContainer && boostSelectionRows.length === 0) addBoostSelectionRow();
  loadOrg();
  loadBoostableItems();
  loadBoostHistory();
  setOrgTab("overview");
  setInterval(loadOrg, 30000); // refresh every 30s
  setInterval(() => {
    const marketTabVisible = orgTabMarketplace && orgTabMarketplace.style.display !== "none";
    if (marketTabVisible) {
      marketplaceLoaded = false;
      loadMarketplace();
    }
  }, 30000);
})();
