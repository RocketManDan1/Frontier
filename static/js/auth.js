(function () {
  if (window.__authBootstrapLoaded) return;
  window.__authBootstrapLoaded = true;

  function redirectToLogin() {
    try {
      if (window.top && window.top !== window) {
        window.top.location.href = "/login";
        return;
      }
    } catch {
      // Fallback to local frame navigation.
    }
    window.location.href = "/login";
  }

  async function loadCurrentUser() {
    const resp = await fetch("/api/auth/me", { cache: "no-store" });
    if (!resp.ok) {
      if (resp.status === 401) {
        redirectToLogin();
        return null;
      }
      throw new Error("Failed to fetch auth state");
    }
    const data = await resp.json();
    return data.user || null;
  }

  function applyRoleUi(user) {
    const adminTabs = document.querySelectorAll(".authAdminTab");
    adminTabs.forEach((tab) => {
      if (!user || !user.is_admin) {
        tab.style.display = "none";
      } else {
        tab.style.display = "";
      }
    });
  }

  window.gameAuth = {
    user: null,
    async ensure() {
      const user = await loadCurrentUser();
      this.user = user;
      applyRoleUi(user);
      applyEnvBanner();          // show DEV / TEST ribbon if applicable
      return user;
    },
  };

  /* ── Environment banner (map page only) ────────────────────────────── */
  async function applyEnvBanner() {
    try {
      // Only show the banner on the map (index) page
      const path = window.location.pathname;
      if (path !== "/" && path !== "/index.html") return;

      const resp = await fetch("/api/server/info", { cache: "no-store" });
      if (!resp.ok) return;
      const info = await resp.json();
      const label = (info.env_label || "").toUpperCase();
      if (!label) return;                     // production — no banner

      // Colour map  (extend as needed)
      const colours = {
        DEV:  { bg: "#d63031", fg: "#fff" },  // red
        TEST: { bg: "#e17055", fg: "#fff" },   // orange
      };
      const c = colours[label] || { bg: "#636e72", fg: "#fff" };

      const banner = document.createElement("div");
      banner.className = "envBanner";
      banner.style.cssText = [
        `background:${c.bg}`, `color:${c.fg}`,
        "text-align:center", "font-weight:700", "font-size:12px",
        "letter-spacing:1.5px", "padding:3px 0", "position:relative",
        "z-index:9999", "user-select:none",
      ].join(";");
      banner.textContent = `▸ ${label} SERVER ◂`;

      document.body.prepend(banner);

      // Also update <title> so browser tabs are distinguishable
      document.title = `[${label}] ${document.title}`;
    } catch { /* non-critical */ }
  }

  window.gameAuth.ensure().catch(() => {
    redirectToLogin();
  });
})();
