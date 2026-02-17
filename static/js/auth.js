(function () {
  if (window.__authBootstrapLoaded) return;
  window.__authBootstrapLoaded = true;

  async function loadCurrentUser() {
    const resp = await fetch("/api/auth/me", { cache: "no-store" });
    if (!resp.ok) {
      if (resp.status === 401) {
        window.location.href = "/login";
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
      return user;
    },
  };

  window.gameAuth.ensure().catch(() => {
    window.location.href = "/login";
  });
})();
