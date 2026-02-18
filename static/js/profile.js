(function () {
  const infoEl = document.getElementById("profileInfo");
  const logoutBtn = document.getElementById("logoutBtn");

  function redirectToLogin() {
    try {
      if (window.top && window.top !== window) {
        window.top.location.href = "/login";
        return;
      }
    } catch {
      // Fallback to same-window redirect.
    }
    window.location.href = "/login";
  }

  function formatCreated(epochS) {
    const d = new Date((Number(epochS) || 0) * 1000);
    if (Number.isNaN(d.getTime())) return "unknown";
    return d.toISOString().replace("T", " ").replace(".000Z", " UTC");
  }

  async function loadProfile() {
    const resp = await fetch("/api/auth/me", { cache: "no-store" });
    if (!resp.ok) {
      if (resp.status === 401) {
        redirectToLogin();
        return;
      }
      infoEl.textContent = "Failed to load profile.";
      return;
    }

    const data = await resp.json();
    const user = data.user || {};

    infoEl.innerHTML = [
      `<div><b>User:</b> ${user.username || "unknown"}</div>`,
      `<div><b>Role:</b> ${user.is_admin ? "Administrator" : "Player"}</div>`,
      `<div class="muted small">Connected to COMNET simulation grid.</div>`,
    ].join("");
  }

  logoutBtn?.addEventListener("click", async () => {
    logoutBtn.disabled = true;
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      redirectToLogin();
    }
  });

  loadProfile();
})();
