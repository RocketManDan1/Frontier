(function () {
  const form = document.getElementById("spawnForm");
  const locationSelect = document.getElementById("locationId");
  const msgEl = document.getElementById("adminMsg");
  const recentShipsEl = document.getElementById("recentShips");
  const pauseSimBtn = document.getElementById("pauseSimBtn");
  const resetGameBtn = document.getElementById("resetGameBtn");
  const simStatusEl = document.getElementById("simStatus");
  const accountUsernameEl = document.getElementById("accountUsername");
  const accountPasswordEl = document.getElementById("accountPassword");
  const createAccountBtn = document.getElementById("createAccountBtn");
  const changePasswordBtn = document.getElementById("changePasswordBtn");
  const deleteAccountBtn = document.getElementById("deleteAccountBtn");
  const accountsMsgEl = document.getElementById("accountsMsg");
  const accountsListEl = document.getElementById("accountsList");
  const grantMoneyUsdEl = document.getElementById("grantMoneyUsd");
  const grantResearchPointsEl = document.getElementById("grantResearchPoints");
  const grantOrgBtn = document.getElementById("grantOrgBtn");
  const grantMsgEl = document.getElementById("grantMsg");

  function setMessage(text, isError) {
    msgEl.textContent = isError ? `Error: ${text || "Unknown error"}` : (text || "");
  }

  function setSimulationUi(paused) {
    if (simStatusEl) {
      simStatusEl.textContent = `Status: ${paused ? "Paused" : "Running"}`;
    }
    if (pauseSimBtn) {
      pauseSimBtn.textContent = paused ? "Resume Simulation" : "Pause Simulation";
    }
  }

  function setAccountsMessage(text, isError) {
    if (!accountsMsgEl) return;
    accountsMsgEl.textContent = isError ? `Error: ${text || "Unknown error"}` : (text || "");
  }

  function selectedAccountUsername() {
    return String(accountUsernameEl?.value || "").trim().toLowerCase();
  }

  function setGrantMessage(text, isError) {
    if (!grantMsgEl) return;
    grantMsgEl.textContent = isError ? `Error: ${text || "Unknown error"}` : (text || "");
  }

  async function loadSimulationStatus() {
    if (!pauseSimBtn || !simStatusEl) return;
    const resp = await fetch("/api/time", { cache: "no-store" });
    const data = await resp.json();
    setSimulationUi(!!data.paused || Number(data.time_scale) === 0);
  }

  async function loadAccounts() {
    if (!accountsListEl) return;
    const resp = await fetch("/api/admin/accounts", { cache: "no-store" });
    const data = await resp.json();
    if (!resp.ok) {
      setAccountsMessage(data.detail || "Failed to load accounts.", true);
      return;
    }

    const accounts = Array.isArray(data.accounts) ? data.accounts : [];
    accountsListEl.innerHTML = "";
    accounts.forEach((account) => {
      const li = document.createElement("li");
      li.className = "accountListItem";

      const userBtn = document.createElement("button");
      userBtn.type = "button";
      userBtn.className = "btnSecondary";
      userBtn.textContent = `${account.username}${account.is_admin ? " (admin)" : ""}`;
      userBtn.addEventListener("click", () => {
        if (accountUsernameEl) accountUsernameEl.value = account.username;
      });

      li.appendChild(userBtn);
      accountsListEl.appendChild(li);
    });
  }

  async function loadLocations() {
    const resp = await fetch("/api/locations", { cache: "no-store" });
    const data = await resp.json();
    const leaves = (data.locations || [])
      .filter((l) => !l.is_group)
      .sort((a, b) => String(a.name).localeCompare(String(b.name)));

    locationSelect.innerHTML = leaves
      .map((loc) => `<option value="${loc.id}">${loc.name} (${loc.id})</option>`)
      .join("");

    if (!leaves.length) {
      setMessage("No valid spawn locations found.", true);
    }
  }

  async function loadRecentShips() {
    const resp = await fetch("/api/state", { cache: "no-store" });
    const data = await resp.json();
    const ships = (data.ships || [])
      .slice()
      .sort((a, b) => String(a.name).localeCompare(String(b.name)))
      .slice(0, 10);

    recentShipsEl.innerHTML = "";
    ships.forEach((s) => {
      const li = document.createElement("li");
      li.className = "adminShipItem";

      const meta = document.createElement("div");
      meta.className = "adminShipMeta";

      const main = document.createElement("div");
      main.textContent = `${s.name} (${s.id})`;

      const loc = document.createElement("div");
      loc.className = "muted small";
      loc.textContent = `@ ${s.location_id || `${s.from_location_id} → ${s.to_location_id}`}`;

      meta.appendChild(main);
      meta.appendChild(loc);

      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "btnSecondary adminDeleteBtn";
      delBtn.textContent = "Delete";
      delBtn.setAttribute("data-delete-ship-id", s.id);
      delBtn.setAttribute("data-delete-ship-name", s.name);

      const refuelBtn = document.createElement("button");
      refuelBtn.type = "button";
      refuelBtn.className = "btnSecondary adminRefuelBtn";
      refuelBtn.textContent = "Refuel";
      refuelBtn.setAttribute("data-refuel-ship-id", s.id);
      refuelBtn.setAttribute("data-refuel-ship-name", s.name);

      const actions = document.createElement("div");
      actions.className = "adminShipActions";
      actions.appendChild(refuelBtn);
      actions.appendChild(delBtn);

      li.appendChild(meta);
      li.appendChild(actions);
      recentShipsEl.appendChild(li);
    });
  }

  recentShipsEl.addEventListener("click", async (e) => {
    const refuelBtn = e.target.closest("button[data-refuel-ship-id]");
    if (refuelBtn) {
      const shipId = refuelBtn.getAttribute("data-refuel-ship-id") || "";
      const shipName = refuelBtn.getAttribute("data-refuel-ship-name") || shipId;
      if (!shipId) return;

      refuelBtn.disabled = true;
      setMessage(`Refueling ${shipName}…`, false);

      try {
        const resp = await fetch(`/api/admin/ships/${encodeURIComponent(shipId)}/refuel`, {
          method: "POST",
        });
        const data = await resp.json();
        if (!resp.ok) {
          setMessage(data.detail || "Failed to refuel ship.", true);
          refuelBtn.disabled = false;
          return;
        }
        const fuelVal = Number(data.ship.fuel_kg || 0);
        const fuelStr = fuelVal >= 5000 ? `${(fuelVal / 1000).toFixed(1)} t` : `${fuelVal.toFixed(0)} kg`;
        setMessage(`Refueled ${data.ship.name} (${data.ship.id}) to ${fuelStr}.`, false);
        await loadRecentShips();
      } catch (err) {
        setMessage(`Refuel failed: ${err.message || err}`, true);
        refuelBtn.disabled = false;
      }
      return;
    }

    const btn = e.target.closest("button[data-delete-ship-id]");
    if (!btn) return;

    const shipId = btn.getAttribute("data-delete-ship-id") || "";
    const shipName = btn.getAttribute("data-delete-ship-name") || shipId;
    if (!shipId) return;

    const ok = window.confirm(`Delete ${shipName} (${shipId})?`);
    if (!ok) return;

    btn.disabled = true;
    setMessage(`Deleting ${shipName}…`, false);

    try {
      const resp = await fetch(`/api/admin/ships/${encodeURIComponent(shipId)}`, {
        method: "DELETE",
      });
      const data = await resp.json();
      if (!resp.ok) {
        setMessage(data.detail || "Failed to delete ship.", true);
        btn.disabled = false;
        return;
      }

      setMessage(`Deleted ${data.deleted.name} (${data.deleted.id}).`, false);
      await loadRecentShips();
    } catch (err) {
      setMessage(`Delete failed: ${err.message || err}`, true);
      btn.disabled = false;
    }
  });

  pauseSimBtn?.addEventListener("click", async () => {
    pauseSimBtn.disabled = true;
    setMessage("Updating simulation state…", false);
    try {
      const resp = await fetch("/api/admin/simulation/toggle_pause", { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) {
        setMessage(data.detail || "Failed to update simulation state.", true);
        return;
      }
      const paused = !!data.paused || Number(data.time_scale) === 0;
      setSimulationUi(paused);
      setMessage(paused ? "Simulation paused." : "Simulation resumed.", false);
    } catch (err) {
      setMessage(`Simulation update failed: ${err.message || err}`, true);
    } finally {
      pauseSimBtn.disabled = false;
    }
  });

  resetGameBtn?.addEventListener("click", async () => {
    const confirmed = window.confirm(
      "Reset game to Jan 1, 2000 and delete all ship/player data? This cannot be undone."
    );
    if (!confirmed) return;

    resetGameBtn.disabled = true;
    if (pauseSimBtn) pauseSimBtn.disabled = true;
    setMessage("Resetting game…", false);

    try {
      const resp = await fetch("/api/admin/reset_game", { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) {
        setMessage(data.detail || "Failed to reset game.", true);
        return;
      }

      setSimulationUi(!!data.paused || Number(data.time_scale) === 0);
      setMessage(
        `Game reset to ${data.reset_to}. Deleted ${Number(data.deleted_ships || 0)} ships and ${Number(data.deleted_accounts || 0)} accounts.`,
        false
      );
      await loadRecentShips();
      await loadAccounts();
    } catch (err) {
      setMessage(`Reset failed: ${err.message || err}`, true);
    } finally {
      resetGameBtn.disabled = false;
      if (pauseSimBtn) pauseSimBtn.disabled = false;
    }
  });

  createAccountBtn?.addEventListener("click", async () => {
    const username = selectedAccountUsername();
    const password = String(accountPasswordEl?.value || "");
    if (!username || !password) {
      setAccountsMessage("Username and password are required.", true);
      return;
    }

    createAccountBtn.disabled = true;
    try {
      const resp = await fetch("/api/admin/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setAccountsMessage(data.detail || "Failed to create account.", true);
        return;
      }
      setAccountsMessage(`Created account ${data.username}.`, false);
      if (accountPasswordEl) accountPasswordEl.value = "";
      await loadAccounts();
    } catch (err) {
      setAccountsMessage(`Create failed: ${err.message || err}`, true);
    } finally {
      createAccountBtn.disabled = false;
    }
  });

  changePasswordBtn?.addEventListener("click", async () => {
    const username = selectedAccountUsername();
    const password = String(accountPasswordEl?.value || "");
    if (!username || !password) {
      setAccountsMessage("Username and password are required.", true);
      return;
    }

    changePasswordBtn.disabled = true;
    try {
      const resp = await fetch(`/api/admin/accounts/${encodeURIComponent(username)}/password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setAccountsMessage(data.detail || "Failed to change password.", true);
        return;
      }
      setAccountsMessage(`Password updated for ${data.username}.`, false);
      if (accountPasswordEl) accountPasswordEl.value = "";
    } catch (err) {
      setAccountsMessage(`Password update failed: ${err.message || err}`, true);
    } finally {
      changePasswordBtn.disabled = false;
    }
  });

  deleteAccountBtn?.addEventListener("click", async () => {
    const username = selectedAccountUsername();
    if (!username) {
      setAccountsMessage("Username is required.", true);
      return;
    }
    const confirmed = window.confirm(`Delete account ${username}?`);
    if (!confirmed) return;

    deleteAccountBtn.disabled = true;
    try {
      const resp = await fetch(`/api/admin/accounts/${encodeURIComponent(username)}`, {
        method: "DELETE",
      });
      const data = await resp.json();
      if (!resp.ok) {
        setAccountsMessage(data.detail || "Failed to delete account.", true);
        return;
      }
      setAccountsMessage(`Deleted account ${data.username}.`, false);
      if (accountUsernameEl) accountUsernameEl.value = "";
      if (accountPasswordEl) accountPasswordEl.value = "";
      await loadAccounts();
    } catch (err) {
      setAccountsMessage(`Delete failed: ${err.message || err}`, true);
    } finally {
      deleteAccountBtn.disabled = false;
    }
  });

  grantOrgBtn?.addEventListener("click", async () => {
    const username = selectedAccountUsername();
    const moneyUsd = Number(grantMoneyUsdEl?.value || 0);
    const researchPoints = Number(grantResearchPointsEl?.value || 0);

    if (!username) {
      setGrantMessage("Select or enter an account username first.", true);
      return;
    }
    if (!Number.isFinite(moneyUsd) || moneyUsd < 0 || !Number.isFinite(researchPoints) || researchPoints < 0) {
      setGrantMessage("Give Money and Give Research Points must be non-negative numbers.", true);
      return;
    }
    if (moneyUsd <= 0 && researchPoints <= 0) {
      setGrantMessage("Provide a positive amount for money or research points.", true);
      return;
    }

    grantOrgBtn.disabled = true;
    setGrantMessage("Applying grant…", false);

    try {
      const resp = await fetch("/api/admin/org/grant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username,
          money_usd: moneyUsd,
          research_points: researchPoints,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setGrantMessage(data.detail || "Failed to apply grant.", true);
        return;
      }

      const balance = Number(data?.org?.balance_usd || 0);
      const rp = Number(data?.org?.research_points || 0);
      setGrantMessage(
        `Granted to ${username}. New balance: $${Math.round(balance).toLocaleString()} • RP: ${rp.toFixed(1)}`,
        false
      );
    } catch (err) {
      setGrantMessage(`Grant failed: ${err.message || err}`, true);
    } finally {
      grantOrgBtn.disabled = false;
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    setMessage("Spawning ship…", false);

    const payload = {
      name: form.shipName.value,
      ship_id: form.shipId.value || null,
      location_id: form.locationId.value,
      color: form.shipColor.value,
      size_px: Number(form.shipSize.value || 12),
      notes: String(form.shipNotes.value || "")
        .split("\n")
        .map((n) => n.trim())
        .filter(Boolean),
    };

    try {
      const resp = await fetch("/api/admin/spawn_ship", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setMessage(data.detail || "Failed to spawn ship.", true);
        return;
      }

      form.shipName.value = "";
      form.shipId.value = "";
      form.shipNotes.value = "";
      setMessage(`Spawned ${data.ship.name} (${data.ship.id}) at ${data.ship.location_id}.`, false);
      await loadRecentShips();
    } catch (err) {
      setMessage(`Spawn failed: ${err.message || err}`, true);
    }
  });

  Promise.all([loadLocations(), loadRecentShips(), loadSimulationStatus(), loadAccounts()]).catch((err) => {
    setMessage(`Init error: ${err.message || err}`, true);
  });
})();
