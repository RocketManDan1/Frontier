(function () {
  const path = String(window.location.pathname || "");
  if (path !== "/") return;

  let serverSyncGameS = Date.now() / 1000;
  let clientSyncRealS = Date.now() / 1000;
  let timeScale = 1;
  let paused = false;

  const root = document.createElement("div");
  root.className = "simClock";
  root.setAttribute("aria-live", "polite");
  root.textContent = "Game Time: loading…";
  document.body.appendChild(root);

  function serverNow() {
    const realNow = Date.now() / 1000;
    return serverSyncGameS + (realNow - clientSyncRealS) * timeScale;
  }

  function fmtGameDate(epochS) {
    const d = new Date(Math.max(0, Number(epochS) || 0) * 1000);
    const yyyy = d.getUTCFullYear();
    const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
    const dd = String(d.getUTCDate()).padStart(2, "0");
    const hh = String(d.getUTCHours()).padStart(2, "0");
    const mi = String(d.getUTCMinutes()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd} ${hh}:${mi} UTC`;
  }

  function renderClock() {
    const status = paused ? "Paused" : "Running";
    root.innerHTML = `Game Time: ${fmtGameDate(serverNow())}<span class="muted">${status}</span>`;
  }

  function scheduleMinuteRender() {
    const nowMs = Date.now();
    const msUntilNextMinute = 60000 - (nowMs % 60000);
    setTimeout(() => {
      renderClock();
      scheduleMinuteRender();
    }, msUntilNextMinute + 10);
  }

  async function syncClock() {
    try {
      const tClient = Date.now() / 1000;
      const resp = await fetch("/api/time", { cache: "no-store" });
      if (!resp.ok) throw new Error("Clock sync failed");
      const data = await resp.json();
      serverSyncGameS = Number(data.server_time) || tClient;
      clientSyncRealS = tClient;
      const parsedScale = Number(data.time_scale);
      timeScale = Number.isFinite(parsedScale) && parsedScale >= 0 ? parsedScale : 1;
      paused = !!data.paused || timeScale === 0;
      renderClock();
    } catch (_err) {
      root.innerHTML = `Game Time: ${fmtGameDate(serverNow())}<span class="muted">Sync error</span>`;
    }
  }

  renderClock();
  syncClock();
  scheduleMinuteRender();
  setInterval(syncClock, 10000);
})();
