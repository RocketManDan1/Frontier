(function () {
  const form = document.getElementById("loginForm");
  const msgEl = document.getElementById("loginMsg");

  function setMessage(text, isError) {
    msgEl.textContent = text || "";
    msgEl.style.color = isError ? "#ff9a9a" : "";
  }

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = String(form.username.value || "").trim();
    const password = String(form.password.value || "");

    setMessage("Authenticating…", false);

    try {
      const resp = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setMessage(data.detail || "Invalid credentials", true);
        return;
      }

      const isAdmin = !!data.user?.is_admin;
      window.location.href = isAdmin ? "/admin" : "/profile";
    } catch (err) {
      setMessage(`Login failed: ${err.message || err}`, true);
    }
  });
})();
