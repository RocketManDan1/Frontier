(function () {
  // ── Color palette for corp selection ──────────────────────────────────────
  const CORP_COLORS = [
    "#4a9eff", "#ff4a4a", "#4aff7f", "#ffd84a", "#ff4af0",
    "#4affef", "#ff944a", "#a04aff", "#7fff4a", "#ff4a94",
    "#4a6eff", "#ffb74a", "#4affb7", "#ff6a4a", "#b74aff",
    "#4adeff",
  ];

  // ── Tab switching ─────────────────────────────────────────────────────────
  document.querySelectorAll(".corpTab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".corpTab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".tabPanel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      const panel = document.getElementById("tab-" + tab.dataset.tab);
      if (panel) panel.classList.add("active");
    });
  });

  // ── Admin toggle ──────────────────────────────────────────────────────────
  const adminToggle = document.getElementById("adminToggleLink");
  const adminPanel = document.getElementById("adminPanel");
  adminToggle?.addEventListener("click", () => {
    adminPanel?.classList.toggle("active");
  });

  // ── Color picker ──────────────────────────────────────────────────────────
  const pickerEl = document.getElementById("colorPicker");
  const colorInput = document.getElementById("regColor");
  if (pickerEl) {
    CORP_COLORS.forEach((color, i) => {
      const swatch = document.createElement("div");
      swatch.className = "colorSwatch" + (i === 0 ? " selected" : "");
      swatch.style.backgroundColor = color;
      swatch.dataset.color = color;
      swatch.addEventListener("click", () => {
        pickerEl.querySelectorAll(".colorSwatch").forEach((s) => s.classList.remove("selected"));
        swatch.classList.add("selected");
        colorInput.value = color;
      });
      pickerEl.appendChild(swatch);
    });
  }

  // ── Load corporations for dropdown ────────────────────────────────────────
  const corpSelect = document.getElementById("loginCorpSelect");
  async function loadCorps() {
    try {
      const resp = await fetch("/api/auth/corps");
      const data = await resp.json();
      corpSelect.innerHTML = "";
      if (!data.corps || data.corps.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "No corporations yet — register one";
        opt.disabled = true;
        opt.selected = true;
        corpSelect.appendChild(opt);
        return;
      }
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Select corporation…";
      placeholder.disabled = true;
      placeholder.selected = true;
      corpSelect.appendChild(placeholder);
      data.corps.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.name;
        opt.textContent = c.name;
        opt.style.color = c.color;
        corpSelect.appendChild(opt);
      });
    } catch (err) {
      corpSelect.innerHTML = '<option value="" disabled selected>Failed to load</option>';
    }
  }
  loadCorps();

  // ── Helper ────────────────────────────────────────────────────────────────
  function setMsg(el, text, isError) {
    if (!el) return;
    el.textContent = text || "";
    el.style.color = isError ? "#ff9a9a" : "";
  }

  // ── Corp Login ────────────────────────────────────────────────────────────
  const loginForm = document.getElementById("corpLoginForm");
  const loginMsg = document.getElementById("loginMsg");
  loginForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const corpName = corpSelect.value;
    const password = document.getElementById("loginPassword").value;
    if (!corpName) {
      setMsg(loginMsg, "Select a corporation", true);
      return;
    }
    setMsg(loginMsg, "Authenticating…", false);
    try {
      const resp = await fetch("/api/auth/corp/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ corp_name: corpName, password }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setMsg(loginMsg, data.detail || "Invalid credentials", true);
        return;
      }
      window.location.href = "/";
    } catch (err) {
      setMsg(loginMsg, `Login failed: ${err.message || err}`, true);
    }
  });

  // ── Corp Register ─────────────────────────────────────────────────────────
  const regForm = document.getElementById("corpRegisterForm");
  const regMsg = document.getElementById("registerMsg");
  regForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const corpName = document.getElementById("regCorpName").value.trim();
    const password = document.getElementById("regPassword").value;
    const color = colorInput.value;
    setMsg(regMsg, "Establishing corporation…", false);
    try {
      const resp = await fetch("/api/auth/corp/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ corp_name: corpName, password, color }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setMsg(regMsg, data.detail || "Registration failed", true);
        return;
      }
      window.location.href = "/";
    } catch (err) {
      setMsg(regMsg, `Registration failed: ${err.message || err}`, true);
    }
  });

  // ── Admin Login ───────────────────────────────────────────────────────────
  const adminForm = document.getElementById("adminLoginForm");
  const adminMsg = document.getElementById("adminMsg");
  adminForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = document.getElementById("adminUser").value.trim();
    const password = document.getElementById("adminPass").value;
    setMsg(adminMsg, "Authenticating…", false);
    try {
      const resp = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setMsg(adminMsg, data.detail || "Invalid credentials", true);
        return;
      }
      window.location.href = data.user?.is_admin ? "/admin" : "/";
    } catch (err) {
      setMsg(adminMsg, `Login failed: ${err.message || err}`, true);
    }
  });
})();
