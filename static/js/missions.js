(function () {
  "use strict";

  /* ─── DOM refs ───────────────────────────────────────────────────────── */
  const tabBtnAvailable = document.getElementById("missionsTabBtnAvailable");
  const tabBtnActive = document.getElementById("missionsTabBtnActive");
  const tabBtnHistory = document.getElementById("missionsTabBtnHistory");
  const tabAvailable = document.getElementById("missionsTabAvailable");
  const tabActive = document.getElementById("missionsTabActive");
  const tabHistory = document.getElementById("missionsTabHistory");

  const missionsList = document.getElementById("missionsList");
  const missionsEmpty = document.getElementById("missionsEmpty");
  const missionsDetail = document.getElementById("missionsDetail");
  const missionsActiveDetail = document.getElementById("missionsActiveDetail");
  const missionsHistoryList = document.getElementById("missionsHistoryList");

  const activeBar = document.getElementById("missionsActiveBar");
  const activeTier = document.getElementById("activeTier");
  const activeTitle = document.getElementById("activeTitle");
  const activeStatus = document.getElementById("activeStatus");
  const activeModule = document.getElementById("activeModule");
  const activeExpiry = document.getElementById("activeExpiry");
  const btnComplete = document.getElementById("btnCompleteMission");
  const btnAbandon = document.getElementById("btnAbandonMission");
  const itemDisplay = window.ItemDisplay || null;

  /* ─── State ──────────────────────────────────────────────────────────── */
  let missions = [];
  let activeMission = null;
  let gameTime = 0;
  let selectedId = null;

  /* ─── Tab switching ──────────────────────────────────────────────────── */
  function setTab(name) {
    if (tabAvailable) tabAvailable.style.display = name === "available" ? "block" : "none";
    if (tabActive) tabActive.style.display = name === "active" ? "block" : "none";
    if (tabHistory) tabHistory.style.display = name === "history" ? "block" : "none";
    tabBtnAvailable?.classList.toggle("active", name === "available");
    tabBtnActive?.classList.toggle("active", name === "active");
    tabBtnHistory?.classList.toggle("active", name === "history");
    tabBtnAvailable?.setAttribute("aria-selected", String(name === "available"));
    tabBtnActive?.setAttribute("aria-selected", String(name === "active"));
    tabBtnHistory?.setAttribute("aria-selected", String(name === "history"));
    if (name === "active") renderActiveDetail();
    if (name === "history") loadHistory();
  }
  tabBtnAvailable?.addEventListener("click", () => setTab("available"));
  tabBtnActive?.addEventListener("click", () => setTab("active"));
  tabBtnHistory?.addEventListener("click", () => setTab("history"));

  /* ─── Tier display helpers ───────────────────────────────────────────── */
  function tierStars(tier) {
    if (tier === "easy") return "★";
    if (tier === "medium") return "★★";
    if (tier === "hard") return "★★★";
    return tier;
  }

  function tierClass(tier) {
    return "missionsTier missionsTier--" + (tier || "easy");
  }

  function formatMoney(n) {
    if (n == null) return "$0";
    if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(0) + "M";
    return "$" + Number(n).toLocaleString();
  }

  function formatGameTime(seconds) {
    if (!seconds || seconds <= 0) return "—";
    var days = seconds / 86400;
    if (days >= 365.25) {
      var y = Math.floor(days / 365.25);
      var d = Math.floor(days - y * 365.25);
      return y + "y " + d + "d";
    }
    return Math.floor(days) + "d";
  }

  function statusLabel(status) {
    var map = {
      available: "Available",
      accepted: "Accepted",
      delivered: "Delivered",
      powered: "Powering…",
      completed: "Completed",
      failed: "Failed",
      abandoned: "Abandoned"
    };
    return map[status] || status;
  }

  function statusClass(status) {
    return "missionsStatus missionsStatus--" + (status || "available");
  }

  function renderMissionModuleCell(containerEl, locationText, powerText) {
    if (!containerEl) return;
    containerEl.innerHTML = "";

    var wrap = document.createElement("div");
    wrap.style.display = "flex";
    wrap.style.alignItems = "center";
    wrap.style.gap = "8px";

    if (itemDisplay && typeof itemDisplay.createGridCell === "function") {
      var cell = itemDisplay.createGridCell({
        item_id: "mission_materials_module",
        itemId: "mission_materials_module",
        label: "Mission Materials Module",
        category: "mission",
        quantity: 1,
        mass_kg: 25000,
        volume_m3: 40,
        subtitle: "Contract payload"
      });
      wrap.appendChild(cell);
    }

    var txt = document.createElement("span");
    txt.textContent = "Module: " + locationText + (powerText ? " | " + powerText : "");
    wrap.appendChild(txt);
    containerEl.appendChild(wrap);
  }

  /* ─── Fetch missions ─────────────────────────────────────────────────── */
  function loadMissions() {
    fetch("/api/missions", { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("Failed to load missions");
        return r.json();
      })
      .then(function (data) {
        missions = data.missions || [];
        activeMission = data.active_mission || null;
        gameTime = data.game_time_s || 0;
        renderMissionList();
        renderActiveMission();
        renderActiveDetail();
        if (selectedId) renderDetail(selectedId);
      })
      .catch(function (err) {
        console.error("loadMissions:", err);
        if (missionsEmpty) missionsEmpty.textContent = "Error loading missions";
      });
  }

  /* ─── Render mission list ────────────────────────────────────────────── */
  function renderMissionList() {
    if (!missionsList) return;
    missionsList.innerHTML = "";

    if (missions.length === 0) {
      missionsList.innerHTML = '<div class="missionsEmptyMsg">No missions available</div>';
      return;
    }

    missions.forEach(function (m) {
      var row = document.createElement("div");
      row.className = "missionsListRow" + (m.id === selectedId ? " selected" : "");
      row.dataset.id = m.id;

      row.innerHTML =
        '<span class="' + tierClass(m.tier) + '">' + tierStars(m.tier) + '</span>' +
        '<span class="missionsListDest">' + esc(m.destination_name) + '</span>' +
        '<span class="missionsListPayout">' + formatMoney(m.payout_total) + '</span>';

      row.addEventListener("click", function () {
        selectedId = m.id;
        renderMissionList();
        renderDetail(m.id);
      });
      missionsList.appendChild(row);
    });
  }

  /* ─── Render detail panel ────────────────────────────────────────────── */
  function renderDetail(id) {
    if (!missionsDetail) return;
    var m = missions.find(function (x) { return x.id === id; });
    if (!m) {
      missionsDetail.innerHTML = '<div class="missionsDetailEmpty">Select a mission to view details</div>';
      return;
    }

    var expiresIn = m.available_expires_at ? (m.available_expires_at - gameTime) : 0;
    var canAccept = !activeMission && m.status === "available";

    missionsDetail.innerHTML =
      '<div class="missionsDetailCard">' +
        '<div class="missionsDetailTier"><span class="' + tierClass(m.tier) + '">' + tierStars(m.tier) + ' ' + cap(m.tier) + '</span></div>' +
        '<h2 class="missionsDetailTitle">' + esc(m.title) + '</h2>' +
        '<div class="missionsDetailMeta">' +
          '<div class="missionsDetailRow"><span class="missionsDetailLabel">Destination</span><span>' + esc(m.destination_name) + ' <code>' + esc(m.destination_id) + '</code></span></div>' +
          '<div class="missionsDetailRow"><span class="missionsDetailLabel">Total Payout</span><span class="missionsDetailMoney">' + formatMoney(m.payout_total) + '</span></div>' +
          '<div class="missionsDetailRow"><span class="missionsDetailLabel">Upfront (50%)</span><span>' + formatMoney(m.payout_upfront) + '</span></div>' +
          '<div class="missionsDetailRow"><span class="missionsDetailLabel">Completion (50%)</span><span>' + formatMoney(m.payout_completion) + '</span></div>' +
          '<div class="missionsDetailRow"><span class="missionsDetailLabel">Contract Length</span><span>15 years</span></div>' +
          (expiresIn > 0 ? '<div class="missionsDetailRow"><span class="missionsDetailLabel">Available for</span><span>' + formatGameTime(expiresIn) + '</span></div>' : '') +
        '</div>' +
        '<div class="missionsDetailDesc">' + esc(m.description) + '</div>' +
        '<div class="missionsDetailActions">' +
          '<button class="btnOutline missionsBtn missionsAcceptBtn" ' +
            (canAccept ? '' : 'disabled title="' + (activeMission ? 'You already have an active mission' : 'Not available') + '"') +
            ' data-id="' + esc(m.id) + '">Accept Mission</button>' +
          (activeMission ? '<div class="missionsDetailWarn">You already have an active mission</div>' : '') +
        '</div>' +
      '</div>';

    var btn = missionsDetail.querySelector(".missionsAcceptBtn");
    if (btn) {
      btn.addEventListener("click", function () {
        acceptMission(m.id);
      });
    }
  }

  /* ─── Render active mission bar ──────────────────────────────────────── */
  function renderActiveMission() {
    if (!activeBar) return;
    if (!activeMission) {
      activeBar.style.display = "none";
      return;
    }
    activeBar.style.display = "flex";
    var m = activeMission;

    if (activeTier) activeTier.innerHTML = '<span class="' + tierClass(m.tier) + '">' + tierStars(m.tier) + '</span>';
    if (activeTitle) activeTitle.textContent = m.title;
    if (activeStatus) {
      activeStatus.innerHTML = '<span class="' + statusClass(m.status) + '">' + statusLabel(m.status) + '</span>';
    }

    // Module location
    if (activeModule) {
      var ml = m.module_location;
      var powerText = "";
      if (m.tier === "hard" && m.status === "powered" && m.power_started_at) {
        var powerElapsed = gameTime - m.power_started_at;
        var powerReq = m.power_required_s || (90 * 86400);
        var pct = Math.min(100, (powerElapsed / powerReq) * 100);
        powerText = "Power: " + pct.toFixed(1) + "%";
      }

      if (ml) {
        var locText = ml.location_id || "Unknown";
        if (ml.ship_name) {
          locText = "loaded on " + ml.ship_name + (ml.location_id ? " @ " + ml.location_id : "");
        } else if (ml.found_in && String(ml.found_in).indexOf("ship:") === 0) {
          var shipId = String(ml.found_in).slice(5);
          locText = "loaded on " + shipId + (ml.location_id ? " @ " + ml.location_id : "");
        }
        else if (ml.found_in === "location_inventory") locText = ml.location_id + " (station)";
        renderMissionModuleCell(activeModule, locText, powerText);
      } else {
        renderMissionModuleCell(activeModule, "not found", powerText);
      }
    }

    // Time remaining
    if (activeExpiry && m.expires_at) {
      var remain = m.expires_at - gameTime;
      activeExpiry.textContent = "Time left: " + formatGameTime(remain);
    }

  }

  /* ─── Render active mission detail tab ────────────────────────────────── */
  function renderActiveDetail() {
    if (!missionsActiveDetail) return;
    if (!activeMission) {
      missionsActiveDetail.innerHTML = '<div class="missionsDetailEmpty">No active mission</div>';
      return;
    }
    var m = activeMission;
    var expiresIn = m.expires_at ? (m.expires_at - gameTime) : 0;

    // Module location text
    var moduleHtml = "";
    var ml = m.module_location;
    if (ml) {
      var locText = ml.location_id || "Unknown";
      if (ml.ship_name) {
        locText = "loaded on " + esc(ml.ship_name) + (ml.location_id ? " @ " + esc(ml.location_id) : "");
      } else if (ml.found_in && String(ml.found_in).indexOf("ship:") === 0) {
        var shipId = String(ml.found_in).slice(5);
        locText = "loaded on " + esc(shipId) + (ml.location_id ? " @ " + esc(ml.location_id) : "");
      } else if (ml.found_in === "location_inventory") {
        locText = esc(ml.location_id) + " (station)";
      }
      moduleHtml = '<div class="missionsDetailRow"><span class="missionsDetailLabel">Module Location</span><span>' + locText + '</span></div>';
    }

    // Power progress for hard missions
    var powerHtml = "";
    if (m.tier === "hard" && m.status === "powered" && m.power_started_at) {
      var powerElapsed = gameTime - m.power_started_at;
      var powerReq = m.power_required_s || (90 * 86400);
      var pct = Math.min(100, (powerElapsed / powerReq) * 100);
      powerHtml = '<div class="missionsDetailRow"><span class="missionsDetailLabel">Power Progress</span><span>' + pct.toFixed(1) + '%</span></div>';
    }

    missionsActiveDetail.innerHTML =
      '<div class="missionsDetailCard">' +
        '<div class="missionsDetailTier"><span class="' + tierClass(m.tier) + '">' + tierStars(m.tier) + ' ' + cap(m.tier) + '</span>' +
          ' <span class="' + statusClass(m.status) + '">' + statusLabel(m.status) + '</span></div>' +
        '<h2 class="missionsDetailTitle">' + esc(m.title) + '</h2>' +
        '<div class="missionsDetailMeta">' +
          '<div class="missionsDetailRow"><span class="missionsDetailLabel">Destination</span><span>' + esc(m.destination_name) + ' <code>' + esc(m.destination_id) + '</code></span></div>' +
          '<div class="missionsDetailRow"><span class="missionsDetailLabel">Total Payout</span><span class="missionsDetailMoney">' + formatMoney(m.payout_total) + '</span></div>' +
          '<div class="missionsDetailRow"><span class="missionsDetailLabel">Upfront (50%)</span><span>' + formatMoney(m.payout_upfront) + '</span></div>' +
          '<div class="missionsDetailRow"><span class="missionsDetailLabel">Completion (50%)</span><span>' + formatMoney(m.payout_completion) + '</span></div>' +
          (expiresIn > 0 ? '<div class="missionsDetailRow"><span class="missionsDetailLabel">Time Remaining</span><span>' + formatGameTime(expiresIn) + '</span></div>' : '') +
          moduleHtml +
          powerHtml +
        '</div>' +
        '<div class="missionsDetailDesc">' + esc(m.description) + '</div>' +
        '<div class="missionsDetailActions">' +
          '<button class="btnOutline missionsBtn missionsCompleteBtn" id="activeDetailComplete" type="button">Complete</button>' +
          '<button class="btnOutline missionsBtn missionsAbandonBtn" id="activeDetailAbandon" type="button">Abandon</button>' +
        '</div>' +
      '</div>';

    var completeBtn = document.getElementById("activeDetailComplete");
    var abandonBtn = document.getElementById("activeDetailAbandon");
    if (completeBtn) completeBtn.addEventListener("click", function () { btnComplete?.click(); });
    if (abandonBtn) abandonBtn.addEventListener("click", function () { btnAbandon?.click(); });
  }

  /* ─── Actions ────────────────────────────────────────────────────────── */
  function acceptMission(id) {
    if (!confirm("Accept this mission? You will receive 50% upfront and a Mission Materials Module in LEO.")) return;
    fetch("/api/missions/" + encodeURIComponent(id) + "/accept", {
      method: "POST",
      credentials: "same-origin",
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          alert(res.data.detail || "Failed to accept mission");
          return;
        }
        showToast(res.data.message || "Mission accepted!");
        loadMissions();
      })
      .catch(function (err) {
        alert("Error: " + err.message);
      });
  }

  btnComplete?.addEventListener("click", function () {
    if (!activeMission) return;
    fetch("/api/missions/" + encodeURIComponent(activeMission.id) + "/complete", {
      method: "POST",
      credentials: "same-origin",
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          alert(res.data.detail || "Cannot complete mission yet");
          return;
        }
        showToast(res.data.message || "Mission updated!");
        loadMissions();
      })
      .catch(function (err) {
        alert("Error: " + err.message);
      });
  });

  btnAbandon?.addEventListener("click", function () {
    if (!activeMission) return;
    if (!confirm("Abandon this mission? The upfront payment will be clawed back.")) return;
    fetch("/api/missions/" + encodeURIComponent(activeMission.id) + "/abandon", {
      method: "POST",
      credentials: "same-origin",
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          alert(res.data.detail || "Failed to abandon");
          return;
        }
        showToast(res.data.message || "Mission abandoned");
        loadMissions();
      })
      .catch(function (err) {
        alert("Error: " + err.message);
      });
  });

  /* ─── History ────────────────────────────────────────────────────────── */
  function loadHistory() {
    fetch("/api/missions/history", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderHistory(data.missions || []);
      })
      .catch(function () {
        if (missionsHistoryList) missionsHistoryList.innerHTML = '<div class="missionsEmptyMsg">Error loading history</div>';
      });
  }

  function renderHistory(list) {
    if (!missionsHistoryList) return;
    if (list.length === 0) {
      missionsHistoryList.innerHTML = '<div class="missionsEmptyMsg">No mission history yet</div>';
      return;
    }
    var html =
      '<table class="missionsHistoryTable">' +
      '<thead><tr>' +
        '<th>Tier</th><th>Title</th><th>Destination</th><th>Status</th><th>Payout</th>' +
      '</tr></thead><tbody>';

    list.forEach(function (m) {
      html +=
        '<tr class="missionsHistoryRow">' +
        '<td><span class="' + tierClass(m.tier) + '">' + tierStars(m.tier) + '</span></td>' +
        '<td>' + esc(m.title) + '</td>' +
        '<td>' + esc(m.destination_name) + '</td>' +
        '<td><span class="' + statusClass(m.status) + '">' + statusLabel(m.status) + '</span></td>' +
        '<td>' + formatMoney(m.payout_total) + '</td>' +
        '</tr>';
    });

    html += '</tbody></table>';
    missionsHistoryList.innerHTML = html;
  }

  /* ─── Toast ──────────────────────────────────────────────────────────── */
  function showToast(msg) {
    var el = document.createElement("div");
    el.className = "missionsToast";
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.classList.add("show"); }, 10);
    setTimeout(function () {
      el.classList.remove("show");
      setTimeout(function () { el.remove(); }, 300);
    }, 4000);
  }

  /* ─── Utility ────────────────────────────────────────────────────────── */
  function esc(s) {
    if (!s) return "";
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function cap(s) {
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
  }

  /* ─── Init ───────────────────────────────────────────────────────────── */
  loadMissions();
  // Refresh every 30 seconds
  setInterval(loadMissions, 30000);

})();
