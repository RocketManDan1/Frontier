(function () {
  const typeTabsEl = document.getElementById("researchTypeTabs");
  const treeTabsEl = document.getElementById("researchTreeTabs");
  const treeEl = document.getElementById("researchTree");
  const infoTitleEl = document.getElementById("researchInfoTitle");
  const infoTreeEl = document.getElementById("researchInfoTree");
  const infoListEl = document.getElementById("researchInfoList");
  const unlockSummaryEl = document.getElementById("researchUnlockSummary");
  const unlockRowsEl = document.getElementById("researchUnlockRows");

  const NODE_WIDTH = 280;
  const NODE_HEIGHT = 90;
  const SUBTREE_COLUMN_WIDTH = 340;

  let techTree = null;
  let unlockedIds = new Set();
  let activeCategoryId = null;
  let selectedNodeId = null;
  let orgResearchPoints = 0;

  function fmtMass(kg) {
    if (kg == null || kg === 0) return "";
    if (kg >= 1000) return (kg / 1000).toFixed(2) + " t";
    return kg.toFixed(1) + " kg";
  }

  function fmtNum(n, unit) {
    if (n == null || n === 0) return "";
    return n.toLocaleString() + (unit ? " " + unit : "");
  }

  function redirectToLogin() {
    try {
      if (window.top && window.top !== window) { window.top.location.href = "/login"; return; }
    } catch { /* noop */ }
    window.location.href = "/login";
  }

  async function loadTree() {
    const resp = await fetch("/api/research/tree", { cache: "no-store" });
    if (!resp.ok) {
      if (resp.status === 401) { redirectToLogin(); return; }
      treeEl.textContent = "Failed to load research tree.";
      return;
    }
    const data = await resp.json();
    techTree = data;
    unlockedIds = new Set(data.unlocked || []);

    try {
      const orgResp = await fetch("/api/org", { cache: "no-store" });
      if (orgResp.ok) {
        const orgData = await orgResp.json();
        orgResearchPoints = (orgData.org && orgData.org.research_points) || 0;
      }
    } catch { /* ignore */ }

    if (!activeCategoryId && data.categories && data.categories.length) {
      activeCategoryId = data.categories[0].id;
    }

    renderCategoryTabs();
    renderTree();
    renderInfo();
  }

  function renderCategoryTabs() {
    typeTabsEl.innerHTML = "";
    treeTabsEl.innerHTML = "";
    treeTabsEl.style.display = "none";

    if (!techTree || !techTree.categories) return;

    for (const cat of techTree.categories) {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "tab researchSubtab" + (cat.id === activeCategoryId ? " active" : "");
      tab.textContent = cat.label;
      tab.setAttribute("role", "tab");

      const total = cat.nodes.length;
      const unlocked = cat.nodes.filter(n => unlockedIds.has(n.id)).length;
      if (total > 0) {
        const badge = document.createElement("span");
        badge.className = "researchBadge";
        badge.textContent = " " + unlocked + "/" + total;
        tab.appendChild(badge);
      }

      tab.addEventListener("click", function () {
        activeCategoryId = cat.id;
        selectedNodeId = null;
        renderCategoryTabs();
        renderTree();
        renderInfo();
      });
      typeTabsEl.appendChild(tab);
    }

    var rpEl = document.createElement("div");
    rpEl.className = "researchRpIndicator";
    rpEl.textContent = orgResearchPoints.toFixed(1) + " RP available";
    typeTabsEl.appendChild(rpEl);
  }

  function getActiveCategory() {
    if (!techTree) return null;
    return techTree.categories.find(function (c) { return c.id === activeCategoryId; }) || techTree.categories[0] || null;
  }

  function renderTree() {
    treeEl.innerHTML = "";
    var cat = getActiveCategory();
    if (!cat || !cat.nodes || !cat.nodes.length) {
      treeEl.innerHTML = '<div class="muted" style="padding:20px;">No tech nodes defined for this category yet.</div>';
      return;
    }

    var nodes = cat.nodes;
    var edges = cat.edges || [];
    var subtrees = cat.subtrees || null;

    // Calculate bounding box
    var maxY = 0;
    var maxX = 0;
    for (var i = 0; i < nodes.length; i++) {
      if ((nodes[i].y || 0) > maxY) maxY = nodes[i].y || 0;
      if ((nodes[i].x || 0) > maxX) maxX = nodes[i].x || 0;
    }
    treeEl.style.width = (maxX + NODE_WIDTH + 100) + "px";
    treeEl.style.height = (maxY + NODE_HEIGHT + 80) + "px";

    // Render subtree column headers if present
    if (subtrees && subtrees.length > 1) {
      for (var si = 0; si < subtrees.length; si++) {
        var sub = subtrees[si];
        var headerEl = document.createElement("div");
        headerEl.className = "kspSubtreeHeader";
        headerEl.textContent = sub.label;
        headerEl.style.left = (sub.x_offset || (60 + si * SUBTREE_COLUMN_WIDTH)) + "px";
        headerEl.style.top = "-30px";
        headerEl.style.width = NODE_WIDTH + "px";
        treeEl.appendChild(headerEl);
      }
      // Shift the tree container down to make room for headers
      treeEl.style.paddingTop = "40px";
    } else {
      treeEl.style.paddingTop = "0";
    }

    // Draw edges
    for (var e = 0; e < edges.length; e++) {
      var fromId = edges[e][0], toId = edges[e][1];
      var fromNode = nodes.find(function (n) { return n.id === fromId; });
      var toNode = nodes.find(function (n) { return n.id === toId; });
      if (!fromNode || !toNode) continue;

      var edgeLine = document.createElement("div");
      edgeLine.className = "kspTreeEdge";
      var x = (fromNode.x || 60) + NODE_WIDTH / 2;
      var y1 = (fromNode.y || 0) + NODE_HEIGHT;
      var y2 = (toNode.y || 0);
      if (unlockedIds.has(fromId)) edgeLine.classList.add("isUnlocked");
      edgeLine.style.left = (x - 1) + "px";
      edgeLine.style.top = y1 + "px";
      edgeLine.style.height = Math.max(0, y2 - y1) + "px";
      treeEl.appendChild(edgeLine);
    }

    // Draw nodes
    for (var ni = 0; ni < nodes.length; ni++) {
      var node = nodes[ni];
      var isUnlocked = unlockedIds.has(node.id);
      var prerequisitesMet = canUnlock(cat, node);
      var isSelected = selectedNodeId === node.id;

      var card = document.createElement("button");
      card.type = "button";
      card.className = "kspTechNode";
      if (isUnlocked) card.classList.add("isUnlocked");
      if (isSelected) card.classList.add("isSelected");
      if (!isUnlocked && prerequisitesMet) card.classList.add("isAvailable");
      if (!isUnlocked && !prerequisitesMet) card.classList.add("isLocked");

      card.style.left = (node.x || 60) + "px";
      card.style.top = (node.y || 0) + "px";
      card.style.width = NODE_WIDTH + "px";

      (function (n) {
        card.addEventListener("click", function () {
          selectedNodeId = n.id;
          renderTree();
          renderInfo();
        });
      })(node);

      var nameEl = document.createElement("div");
      nameEl.className = "kspNodeName";
      nameEl.textContent = node.name;
      card.appendChild(nameEl);

      var costLine = document.createElement("div");
      costLine.className = "kspNodeCost";
      if (isUnlocked) {
        costLine.textContent = "\u2713 Unlocked";
        costLine.classList.add("unlocked");
      } else {
        costLine.textContent = node.cost_rp + " RP";
      }
      card.appendChild(costLine);

      var itemCount = document.createElement("div");
      itemCount.className = "kspNodeItemCount";
      var ic = (node.items || []).length;
      itemCount.textContent = ic > 0 ? ic + " item" + (ic !== 1 ? "s" : "") : "No items yet";
      card.appendChild(itemCount);

      treeEl.appendChild(card);
    }
  }

  function canUnlock(cat, node) {
    var edges = cat.edges || [];
    var prereqs = [];
    for (var i = 0; i < edges.length; i++) {
      if (edges[i][1] === node.id) prereqs.push(edges[i][0]);
    }
    if (prereqs.length === 0) return true;
    for (var j = 0; j < prereqs.length; j++) {
      if (!unlockedIds.has(prereqs[j])) return false;
    }
    return true;
  }

  function getSubtreeLabel(cat, node) {
    if (!cat.subtrees || cat.subtrees.length <= 1) return "";
    for (var s = 0; s < cat.subtrees.length; s++) {
      for (var n = 0; n < cat.subtrees[s].nodes.length; n++) {
        if (cat.subtrees[s].nodes[n].id === node.id) return cat.subtrees[s].label;
      }
    }
    return "";
  }

  function renderInfo() {
    var cat = getActiveCategory();
    if (!cat) {
      infoTitleEl.textContent = "Select a category";
      infoTreeEl.textContent = "";
      infoListEl.innerHTML = "";
      unlockRowsEl.innerHTML = "";
      unlockSummaryEl.textContent = "";
      return;
    }

    var node = null;
    if (selectedNodeId) {
      node = cat.nodes.find(function (n) { return n.id === selectedNodeId; });
    }
    if (!node && cat.nodes.length) node = cat.nodes[0];
    if (!node) {
      infoTitleEl.textContent = "No node selected";
      infoTreeEl.textContent = cat.label;
      infoListEl.innerHTML = "";
      unlockRowsEl.innerHTML = "";
      return;
    }

    selectedNodeId = node.id;
    var isUnlocked = unlockedIds.has(node.id);
    var prerequisitesMet = canUnlock(cat, node);

    infoTitleEl.textContent = node.name;
    var subLabel = getSubtreeLabel(cat, node);
    var breadcrumb = cat.label;
    if (subLabel) breadcrumb += " \u203a " + subLabel;
    infoTreeEl.textContent = breadcrumb + " \u00b7 Tech Level " + node.tech_level;

    infoListEl.innerHTML = "";
    var infos = [
      "Cost: " + node.cost_rp + " Research Points",
      "Status: " + (isUnlocked ? "Unlocked \u2713" : prerequisitesMet ? "Available" : "Locked (prerequisites needed)"),
      "Your RP: " + orgResearchPoints.toFixed(1),
    ];
    for (var ii = 0; ii < infos.length; ii++) {
      var li = document.createElement("li");
      li.textContent = infos[ii];
      infoListEl.appendChild(li);
    }

    unlockSummaryEl.innerHTML = "";
    if (!isUnlocked) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btnPrimary";
      var canAfford = orgResearchPoints >= node.cost_rp;
      btn.disabled = !prerequisitesMet || !canAfford;
      if (canAfford && prerequisitesMet) {
        btn.textContent = "Unlock for " + node.cost_rp + " RP";
      } else if (!prerequisitesMet) {
        btn.textContent = "Prerequisites not met";
      } else {
        btn.textContent = "Need " + node.cost_rp + " RP (have " + orgResearchPoints.toFixed(1) + ")";
      }
      (function (c, n) {
        btn.addEventListener("click", function () { doUnlock(c, n); });
      })(cat, node);
      unlockSummaryEl.appendChild(btn);
    } else {
      unlockSummaryEl.textContent = "Technology unlocked \u2713";
    }

    unlockRowsEl.innerHTML = "";
    var items = node.items || [];
    if (items.length === 0) {
      unlockRowsEl.innerHTML = '<div class="muted small">No items at this tier yet (placeholder).</div>';
      return;
    }

    // Render as item grid cells if ItemDisplay is available
    if (window.ItemDisplay) {
      var grid = document.createElement("div");
      grid.className = "researchUnlockGrid";
      for (var ix = 0; ix < items.length; ix++) {
        var item = items[ix];
        var catName = item.category || "";
        // Map research category to item display category
        if (catName.startsWith("refineries_")) catName = "refinery";

        var tooltipLines = [];
        if (item.thrust_kn) tooltipLines.push(["Thrust", fmtNum(item.thrust_kn, "kN")]);
        if (item.isp_s) tooltipLines.push(["ISP", fmtNum(item.isp_s, "s")]);
        if (item.thermal_mw) tooltipLines.push(["Thermal", fmtNum(item.thermal_mw, "MW")]);
        if (item.electric_mw) tooltipLines.push(["Electric", fmtNum(item.electric_mw, "MW")]);
        if (item.heat_rejection_mw) tooltipLines.push(["Heat Reject", fmtNum(item.heat_rejection_mw, "MW")]);

        var cell = window.ItemDisplay.createGridCell({
          label: item.name,
          iconSeed: item.item_id || item.name,
          itemId: item.item_id,
          category: catName,
          mass_kg: item.mass_kg || 0,
          subtitle: catName,
          branch: item.branch || "",
          techLevel: String(item.tech_level || ""),
          tooltipLines: tooltipLines,
        });

        if (!isUnlocked) cell.classList.add("isLocked");
        grid.appendChild(cell);
      }
      unlockRowsEl.appendChild(grid);
    } else {
      // Fallback: plain text rows
      for (var ix = 0; ix < items.length; ix++) {
        var item = items[ix];
        var row = document.createElement("div");
        row.className = "kspUnlockRow" + (isUnlocked ? " isUnlocked" : "");

        var nameSpan = document.createElement("div");
        nameSpan.className = "kspUnlockName";
        nameSpan.textContent = item.name;
        row.appendChild(nameSpan);

        var statsSpan = document.createElement("div");
        statsSpan.className = "kspUnlockStats muted small";
        var stats = [];
        if (item.mass_kg) stats.push(fmtMass(item.mass_kg));
        if (item.thrust_kn) stats.push(fmtNum(item.thrust_kn, "kN"));
        if (item.isp_s) stats.push(fmtNum(item.isp_s, "s ISP"));
        if (item.thermal_mw) stats.push(fmtNum(item.thermal_mw, "MWth"));
        if (item.electric_mw) stats.push(fmtNum(item.electric_mw, "MWe"));
        if (item.heat_rejection_mw) stats.push(fmtNum(item.heat_rejection_mw, "MW reject"));
        statsSpan.textContent = stats.join(" \u00b7 ") || "\u2014";
        row.appendChild(statsSpan);

        unlockRowsEl.appendChild(row);
      }
    }
  }

  async function doUnlock(cat, node) {
    var edges = cat.edges || [];
    var prereqs = [];
    for (var i = 0; i < edges.length; i++) {
      if (edges[i][1] === node.id) prereqs.push(edges[i][0]);
    }

    try {
      var resp = await fetch("/api/org/research/unlock", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tech_id: node.id,
          cost: node.cost_rp,
          prerequisites: prereqs,
        }),
      });
      var data = await resp.json();
      if (!resp.ok) {
        alert(data.detail || "Failed to unlock tech.");
        return;
      }
      await loadTree();
    } catch (e) {
      alert("Error: " + e.message);
    }
  }

  loadTree();
})();
