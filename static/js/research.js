(function () {
  const subtabsEl = document.getElementById("researchSubtabs");
  const viewportEl = document.getElementById("researchViewport");
  const treeEl = document.getElementById("researchTree");
  const infoTitleEl = document.getElementById("researchInfoTitle");
  const infoCategoryEl = document.getElementById("researchInfoCategory");
  const infoListEl = document.getElementById("researchInfoList");

  let researchPayload = { categories: [], trees: {} };
  let activeCategory = "thrusters";
  let selectedTechId = null;

  const pan = { x: 60, y: 60, dragging: false, lastX: 0, lastY: 0 };

  function nodeMetrics(tech) {
    const kind = String(tech?.kind || "").toLowerCase();
    if (kind === "upgrade") return { width: 164, height: 42 };
    return { width: 216, height: 56 };
  }

  function setTreeSummary(tree) {
    const categoryLabel = (researchPayload.categories || []).find((c) => c.id === activeCategory)?.label || "Research";
    const meta = tree && typeof tree === "object" ? (tree.meta || {}) : {};
    const connectivity = meta.connectivity && typeof meta.connectivity === "object" ? meta.connectivity : {};
    const disconnected = Array.isArray(connectivity.disconnected_nodes) ? connectivity.disconnected_nodes : [];

    infoTitleEl.textContent = "Select a technology";
    infoCategoryEl.textContent = categoryLabel;
    infoListEl.innerHTML = "";

    const bullets = [
      `Layout: ${meta.layout || "custom"}`,
      `Connected: ${connectivity.connected === false ? "No" : "Yes"}`,
      `Disconnected nodes: ${disconnected.length ? disconnected.join(", ") : "none"}`,
    ];

    bullets.forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      infoListEl.appendChild(li);
    });
  }

  function setInfo(tech) {
    if (!tech) {
      infoTitleEl.textContent = "Select a technology";
      infoCategoryEl.textContent = "";
      infoListEl.innerHTML = "";
      return;
    }

    const categoryLabel = (researchPayload.categories || []).find((c) => c.id === activeCategory)?.label || "Research";
    infoTitleEl.textContent = tech.name;
    infoCategoryEl.textContent = categoryLabel;
    infoListEl.innerHTML = "";

    const bullets = [];
    bullets.push(`Tech ID: ${tech.id}`);
    if (tech.kind) bullets.push(`Type: ${tech.kind}`);
    if (typeof tech.tier === "number" && Number.isFinite(tech.tier)) bullets.push(`Tier: ${tech.tier}`);
    const between = Array.isArray(tech.tier_between_main)
      ? tech.tier_between_main
      : (Array.isArray(tech.tier_between_engines) ? tech.tier_between_engines : []);
    if (between.length === 2) {
      bullets.push(`Between tiers: ${between[0]} → ${between[1]}`);
    }

    const requires = Array.isArray(tech.requires) ? tech.requires : [];
    bullets.push(`Requires: ${requires.length ? requires.join(", ") : "none"}`);

    (Array.isArray(tech.effects) ? tech.effects : []).forEach((effect) => bullets.push(`Effect: ${effect}`));
    (Array.isArray(tech.tradeoffs) ? tech.tradeoffs : []).forEach((tradeoff) => bullets.push(`Tradeoff: ${tradeoff}`));

    const details = tech.details && typeof tech.details === "object" ? tech.details : {};
    Object.entries(details).forEach(([key, value]) => {
      if (value == null || value === "") return;
      if (Array.isArray(value)) {
        if (!value.length) return;
        bullets.push(`${key}: ${value.join(", ")}`);
      } else if (typeof value === "object") {
        bullets.push(`${key}: ${JSON.stringify(value)}`);
      } else {
        bullets.push(`${key}: ${value}`);
      }
    });

    bullets.forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      infoListEl.appendChild(li);
    });
  }

  function updatePanTransform() {
    treeEl.style.transform = `translate(${pan.x}px, ${pan.y}px)`;
  }

  function renderSubtabs() {
    subtabsEl.innerHTML = "";
    (researchPayload.categories || []).forEach((cat) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `tab researchSubtab ${cat.id === activeCategory ? "active" : ""}`;
      btn.setAttribute("role", "tab");
      btn.textContent = cat.label;
      btn.addEventListener("click", () => {
        activeCategory = cat.id;
        selectedTechId = null;
        renderSubtabs();
        renderTree();
      });
      subtabsEl.appendChild(btn);
    });
  }

  function renderTree() {
    const tree = (researchPayload.trees || {})[activeCategory] || { nodes: [], edges: [] };
    const techs = Array.isArray(tree.nodes) ? tree.nodes : [];
    const edges = Array.isArray(tree.edges) ? tree.edges : [];
    const lanes = Array.isArray(tree?.meta?.lanes) ? tree.meta.lanes : [];
    const byId = new Map(techs.map((t) => [t.id, t]));

    treeEl.innerHTML = "";

    lanes.forEach((lane) => {
      const laneEl = document.createElement("div");
      laneEl.className = `researchLane ${lane?.reserved ? "reserved" : ""}`;
      laneEl.style.left = `${Number(lane?.x || 0)}px`;
      laneEl.style.width = `${Math.max(1, Number(lane?.width || 420))}px`;

      const labelEl = document.createElement("div");
      labelEl.className = "researchLaneLabel";
      labelEl.textContent = String(lane?.label || lane?.id || "Lane");
      laneEl.appendChild(labelEl);

      treeEl.appendChild(laneEl);
    });

    edges.forEach((edge) => {
      const from = byId.get(edge.from);
      const to = byId.get(edge.to);
      if (!from || !to) return;
      const fromMetrics = nodeMetrics(from);
      const toMetrics = nodeMetrics(to);

      const line = document.createElement("div");
      line.className = "researchEdge";

      const x1 = Number(from.x || 0) + Math.round(fromMetrics.width * 0.5);
      const y1 = Number(from.y || 0) + Math.round(fromMetrics.height * 0.5);
      const x2 = Number(to.x || 0) + Math.round(toMetrics.width * 0.5);
      const y2 = Number(to.y || 0) + Math.round(toMetrics.height * 0.5);
      const dx = x2 - x1;
      const dy = y2 - y1;
      const length = Math.max(1, Math.hypot(dx, dy));
      const angle = (Math.atan2(dy, dx) * 180) / Math.PI;

      line.style.left = `${x1}px`;
      line.style.top = `${y1}px`;
      line.style.width = `${length}px`;
      line.style.transform = `rotate(${angle}deg)`;
      treeEl.appendChild(line);
    });

    if (!techs.length) {
      setInfo(null);
      infoTitleEl.textContent = "No technology entries yet";
      infoCategoryEl.textContent = "This category has no loaded JSON data.";
      return;
    }

    const maxX = Math.max(...techs.map((t) => Number(t.x || 0)), 0);
    const maxY = Math.max(...techs.map((t) => Number(t.y || 0)), 0);
    const laneRight = Math.max(...lanes.map((lane) => Number(lane?.x || 0) + Number(lane?.width || 0)), 0);
    treeEl.style.width = `${Math.max(2200, maxX + 420, laneRight + 120)}px`;
    treeEl.style.height = `${Math.max(1400, maxY + 420)}px`;

    techs.forEach((tech) => {
      const node = document.createElement("button");
      node.type = "button";
      const kind = String(tech.kind || "").toLowerCase();
      const kindClass = kind === "upgrade" ? "researchNodeUpgrade" : "researchNodeMain";
      node.className = `researchNode ${kindClass} ${selectedTechId === tech.id ? "active" : ""}`;
      node.style.left = `${tech.x}px`;
      node.style.top = `${tech.y}px`;
      node.textContent = tech.name;
      node.addEventListener("click", (event) => {
        event.stopPropagation();
        selectedTechId = tech.id;
        setInfo(tech);
        renderTree();
      });
      treeEl.appendChild(node);
    });

    const selected = byId.get(selectedTechId) || null;
    if (selected) setInfo(selected);
    else setTreeSummary(tree);
    updatePanTransform();
  }

  async function loadResearch() {
    const resp = await fetch("/api/research/tree", { cache: "no-store" });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || "Failed to load research tree.");
    }

    researchPayload = {
      categories: Array.isArray(data.categories) ? data.categories : [],
      trees: data.trees && typeof data.trees === "object" ? data.trees : {},
    };

    if (!researchPayload.categories.length) {
      researchPayload.categories = [
        { id: "thrusters", label: "Thrusters" },
        { id: "reactors", label: "Reactors" },
        { id: "generators", label: "Generators" },
        { id: "robonauts", label: "Robonauts" },
        { id: "refineries", label: "Refineries" },
        { id: "radiators", label: "Radiators" },
      ];
    }

    if (!researchPayload.categories.some((c) => c.id === activeCategory)) {
      activeCategory = researchPayload.categories[0].id;
    }

    renderSubtabs();
    renderTree();
  }

  viewportEl.addEventListener("pointerdown", (event) => {
    const clickedNode = event.target && event.target.closest && event.target.closest(".researchNode");
    if (clickedNode) {
      return;
    }
    pan.dragging = true;
    pan.lastX = event.clientX;
    pan.lastY = event.clientY;
    viewportEl.setPointerCapture?.(event.pointerId);
    viewportEl.classList.add("isPanning");
  });

  viewportEl.addEventListener("pointermove", (event) => {
    if (!pan.dragging) return;
    const dx = event.clientX - pan.lastX;
    const dy = event.clientY - pan.lastY;
    pan.x += dx;
    pan.y += dy;
    pan.lastX = event.clientX;
    pan.lastY = event.clientY;
    updatePanTransform();
  });

  function endPan(event) {
    if (!pan.dragging) return;
    pan.dragging = false;
    viewportEl.releasePointerCapture?.(event.pointerId);
    viewportEl.classList.remove("isPanning");
  }

  viewportEl.addEventListener("pointerup", endPan);
  viewportEl.addEventListener("pointercancel", endPan);
  viewportEl.addEventListener("pointerleave", endPan);

  loadResearch().catch((err) => {
    infoTitleEl.textContent = "Research load error";
    infoCategoryEl.textContent = err?.message || "Unknown error";
    infoListEl.innerHTML = "";
  });
})();