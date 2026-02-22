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

  const boostItemSelect = document.getElementById("boostItemSelect");
  const boostQuantity = document.getElementById("boostQuantity");
  const boostMass = document.getElementById("boostMass");
  const boostCost = document.getElementById("boostCost");
  const boostBtn = document.getElementById("boostBtn");
  const boostStatus = document.getElementById("boostStatus");
  const boostHistory = document.getElementById("boostHistory");

  let currentOrg = null;
  let boostableItems = [];

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
    const monthlyExpense = activeTeams.length * (currentOrg.team_cost_per_month_usd || 150000000);
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
  async function loadBoostableItems() {
    const resp = await fetch("/api/org/boostable-items", { cache: "no-store" });
    if (!resp.ok) return;
    const data = await resp.json();
    boostableItems = data.items || [];

    boostItemSelect.innerHTML = '<option value="">Select item…</option>';
    // Group by type
    const groups = {};
    for (const item of boostableItems) {
      const g = item.type || "other";
      if (!groups[g]) groups[g] = [];
      groups[g].push(item);
    }
    for (const [gName, items] of Object.entries(groups).sort()) {
      const optGroup = document.createElement("optgroup");
      optGroup.label = gName.charAt(0).toUpperCase() + gName.slice(1);
      for (const item of items.sort((a, b) => a.name.localeCompare(b.name))) {
        const opt = document.createElement("option");
        opt.value = item.item_id;
        opt.textContent = `${item.name} (${fmtMass(item.mass_per_unit_kg)})`;
        optGroup.appendChild(opt);
      }
      boostItemSelect.appendChild(optGroup);
    }
  }

  function updateBoostPreview() {
    const itemId = boostItemSelect.value;
    const qty = parseFloat(boostQuantity.value) || 1;
    const item = boostableItems.find(b => b.item_id === itemId);

    if (!item) {
      boostMass.textContent = "—";
      boostCost.textContent = "—";
      boostBtn.disabled = true;
      return;
    }

    const totalMass = item.mass_per_unit_kg * qty;
    const cost = 100000000 + 5000 * totalMass;
    boostMass.textContent = fmtMass(totalMass);
    boostCost.textContent = fmtUsd(cost);
    boostBtn.disabled = false;
  }

  async function doBoost() {
    const itemId = boostItemSelect.value;
    const qty = parseFloat(boostQuantity.value) || 1;
    if (!itemId) return;

    boostBtn.disabled = true;
    boostStatus.textContent = "Launching…";
    try {
      const resp = await fetch("/api/org/boost", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_id: itemId, quantity: qty }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        boostStatus.textContent = data.detail || "Boost failed.";
        return;
      }
      boostStatus.textContent = `Launched ${data.item_name} x${data.quantity} to ${data.destination} (${fmtUsd(data.cost_usd)})`;
      await loadOrg();
    } catch (e) {
      boostStatus.textContent = "Error: " + e.message;
    } finally {
      boostBtn.disabled = false;
    }
  }

  // ── Event listeners ─────────────────────────────────────────────────────────
  hireTeamBtn?.addEventListener("click", hireTeam);
  boostItemSelect?.addEventListener("change", updateBoostPreview);
  boostQuantity?.addEventListener("input", updateBoostPreview);
  boostBtn?.addEventListener("click", doBoost);

  logoutBtn?.addEventListener("click", async () => {
    logoutBtn.disabled = true;
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      redirectToLogin();
    }
  });

  // ── Auto-refresh ────────────────────────────────────────────────────────────
  loadOrg();
  loadBoostableItems();
  setInterval(loadOrg, 30000); // refresh every 30s
})();
