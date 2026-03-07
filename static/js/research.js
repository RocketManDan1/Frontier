(function () {
  var treeEl = document.getElementById("researchTree");
  var viewportEl = document.querySelector(".researchViewport");
  var infoTitleEl = document.getElementById("researchInfoTitle");
  var infoTreeEl = document.getElementById("researchInfoTree");
  var infoListEl = document.getElementById("researchInfoList");
  var unlockSummaryEl = document.getElementById("researchUnlockSummary");
  var unlockRowsEl = document.getElementById("researchUnlockRows");
  var rpIndicatorEl = document.getElementById("researchRpIndicator");

  var NODE_WIDTH = 280;
  var NODE_HEIGHT = 90;

  var treeData = null;
  var unlockedIds = new Set();
  var selectedNodeId = null;
  var orgResearchPoints = 0;

  /* ── Zoom / pan state ─────────────────────────────────────────── */
  var _zoom = 1;
  var _panX = 0;
  var _panY = 0;
  var MIN_ZOOM = 0.10;
  var MAX_ZOOM = 2.5;
  var WHEEL_SENSITIVITY = 0.0015;
  var _isPanning = false;
  var _lastPtr = { x: 0, y: 0 };
  var _didDrag = false;
  var DRAG_THRESHOLD = 5;
  var _treeBounds = { w: 0, h: 0 };
  var _initialFitDone = false;

  function applyTransform() {
    treeEl.style.transform =
      "translate(" + _panX + "px," + _panY + "px) scale(" + _zoom + ")";
  }

  function fitToView() {
    if (!_treeBounds.w || !_treeBounds.h) return;
    var rect = viewportEl.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    var scX = rect.width / _treeBounds.w;
    var scY = rect.height / _treeBounds.h;
    _zoom = Math.min(scX, scY) * 0.92;
    _zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, _zoom));
    _panX = (rect.width - _treeBounds.w * _zoom) / 2;
    _panY = (rect.height - _treeBounds.h * _zoom) / 2;
    applyTransform();
  }

  /* ── Zoom-toward-cursor (scroll wheel) ──────────────────────── */
  viewportEl.addEventListener("wheel", function (e) {
    e.preventDefault();
    var dy = Number(e.deltaY) || 0;
    if (dy === 0) return;

    var oldZ = _zoom;
    var factor = Math.exp(-dy * WHEEL_SENSITIVITY);
    var newZ = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, oldZ * factor));
    if (Math.abs(newZ - oldZ) < 1e-9) return;

    var rect = viewportEl.getBoundingClientRect();
    var mx = e.clientX - rect.left;
    var my = e.clientY - rect.top;

    var wx = (mx - _panX) / oldZ;
    var wy = (my - _panY) / oldZ;

    _zoom = newZ;
    _panX = mx - wx * newZ;
    _panY = my - wy * newZ;
    applyTransform();
  }, { passive: false });

  /* ── Pointer-drag panning ─────────────────────────────────────── */
  viewportEl.addEventListener("pointerdown", function (e) {
    if (e.button !== 0) return;
    if (e.target && e.target.closest && e.target.closest(".kspTechNode")) return;
    _isPanning = true;
    _didDrag = false;
    _lastPtr = { x: e.clientX, y: e.clientY };
    viewportEl.setPointerCapture(e.pointerId);
    viewportEl.style.cursor = "grabbing";
  });

  viewportEl.addEventListener("pointermove", function (e) {
    if (!_isPanning) return;
    var dx = e.clientX - _lastPtr.x;
    var dy = e.clientY - _lastPtr.y;
    if (!_didDrag && Math.abs(dx) + Math.abs(dy) > DRAG_THRESHOLD) _didDrag = true;
    if (_didDrag) { _panX += dx; _panY += dy; applyTransform(); }
    _lastPtr = { x: e.clientX, y: e.clientY };
  });

  viewportEl.addEventListener("pointerup", function () {
    _isPanning = false;
    viewportEl.style.cursor = "";
    /* Reset _didDrag after a short delay so the click handler
       on the node still sees it for THIS event cycle, but future
       clicks are not blocked. */
    setTimeout(function () { _didDrag = false; }, 0);
  });

  viewportEl.addEventListener("lostpointercapture", function () {
    _isPanning = false;
    viewportEl.style.cursor = "";
    setTimeout(function () { _didDrag = false; }, 0);
  });

  /* ── Fit button (bottom-right of viewport) ──────────────────── */
  var fitBtn = document.createElement("button");
  fitBtn.type = "button";
  fitBtn.className = "researchFitBtn";
  fitBtn.textContent = "Fit";
  fitBtn.title = "Fit entire tree in view";
  fitBtn.addEventListener("click", function (ev) { ev.stopPropagation(); fitToView(); });
  viewportEl.appendChild(fitBtn);

  // ── Helpers ──────────────────────────────────────────────────────
  function fmtMass(kg) {
    if (kg == null || kg === 0) return "";
    if (kg >= 1000) return (kg / 1000).toFixed(2) + " t";
    return kg.toFixed(1) + " kg";
  }
  function fmtNum(n, unit) {
    if (n == null || n === 0) return "";
    return n.toLocaleString() + (unit ? " " + unit : "");
  }
  function itemSubtitle(item) {
    return String((item && (item.category_id || item.type || item.category)) || "module")
      .trim()
      .toLowerCase() || "module";
  }
  function redirectToLogin() {
    try { if (window.top && window.top !== window) { window.top.location.href = "/login"; return; } } catch (e) { /* noop */ }
    window.location.href = "/login";
  }

  // ── Load data ────────────────────────────────────────────────────
  async function loadTree() {
    var resp = await fetch("/api/research/tree", { cache: "no-store" });
    if (!resp.ok) {
      if (resp.status === 401) { redirectToLogin(); return; }
      treeEl.textContent = "Failed to load research tree.";
      return;
    }
    var data = await resp.json();
    treeData = data;
    unlockedIds = new Set(data.unlocked || []);

    try {
      var orgResp = await fetch("/api/org", { cache: "no-store" });
      if (orgResp.ok) {
        var orgData = await orgResp.json();
        orgResearchPoints = (orgData.org && orgData.org.research_points) || 0;
      }
    } catch (e) { /* ignore */ }

    if (!selectedNodeId && data.nodes && data.nodes.length) {
      var starter = data.nodes.find(function (n) { return n.auto_unlock; });
      selectedNodeId = starter ? starter.id : data.nodes[0].id;
    }

    renderRpIndicator();
    renderTree();
    renderInfo();
  }

  function renderRpIndicator() {
    if (rpIndicatorEl) {
      rpIndicatorEl.textContent = orgResearchPoints.toFixed(1) + " RP available";
    }
  }

  // ── Prerequisite helpers ──────────────────────────────────────────
  function getPrereqs(nodeId) {
    var edges = (treeData && treeData.edges) || [];
    var prereqs = [];
    for (var i = 0; i < edges.length; i++) {
      if (edges[i][1] === nodeId) prereqs.push(edges[i][0]);
    }
    return prereqs;
  }

  function canUnlock(nodeId) {
    var prereqs = getPrereqs(nodeId);
    if (prereqs.length === 0) return true;
    for (var j = 0; j < prereqs.length; j++) {
      if (!unlockedIds.has(prereqs[j])) return false;
    }
    return true;
  }

  // ── Render tree ──────────────────────────────────────────────────
  function renderTree() {
    treeEl.innerHTML = "";
    if (!treeData || !treeData.nodes || !treeData.nodes.length) {
      treeEl.innerHTML = '<div class="muted" style="padding:20px;">No tech nodes loaded.</div>';
      return;
    }

    var nodes = treeData.nodes;
    var edges = treeData.edges || [];

    // Build node lookup and bounding box
    var nodeMap = {};
    var minX = Infinity, minY = Infinity, maxX = 0, maxY = 0;
    for (var i = 0; i < nodes.length; i++) {
      var nx = Number(nodes[i].x) || 0;
      var ny = Number(nodes[i].y) || 0;
      nodeMap[nodes[i].id] = nodes[i];
      if (nx < minX) minX = nx;
      if (ny < minY) minY = ny;
      if (nx + NODE_WIDTH > maxX) maxX = nx + NODE_WIDTH;
      if (ny + NODE_HEIGHT > maxY) maxY = ny + NODE_HEIGHT;
    }

    if (!isFinite(minX)) minX = 0;
    if (!isFinite(minY)) minY = 0;
    var treePad = 40;
    var offsetX = treePad - minX;
    var offsetY = treePad - minY;

    _treeBounds = {
      w: (maxX - minX) + treePad * 2,
      h: (maxY - minY) + treePad * 2,
    };
    treeEl.style.width = _treeBounds.w + "px";
    treeEl.style.height = _treeBounds.h + "px";

    // Draw edges as SVG
    var svgNS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("class", "researchEdgeSvg");
    svg.setAttribute("width", _treeBounds.w);
    svg.setAttribute("height", _treeBounds.h);
    svg.style.position = "absolute";
    svg.style.top = "0";
    svg.style.left = "0";
    svg.style.pointerEvents = "none";
    svg.style.zIndex = "0";

    for (var e = 0; e < edges.length; e++) {
      var fromId = edges[e][0], toId = edges[e][1];
      var fromNode = nodeMap[fromId], toNode = nodeMap[toId];
      if (!fromNode || !toNode) continue;

      var fx = (Number(fromNode.x) || 0) + offsetX;
      var fy = (Number(fromNode.y) || 0) + offsetY;
      var tx = (Number(toNode.x) || 0) + offsetX;
      var ty = (Number(toNode.y) || 0) + offsetY;

      var fromCy = fy + NODE_HEIGHT / 2;
      var toCy = ty + NODE_HEIGHT / 2;

      /* ── Orthogonal (right-angle) edge routing ────────────────
         Always connect side-to-side for a cleaner tree look that
         better matches draw.io layouts. */
      var pts = [];
      var fromCenterX = fx + NODE_WIDTH / 2;
      var toCenterX = tx + NODE_WIDTH / 2;
      var toRight = toCenterX >= fromCenterX;

      var exitX = toRight ? (fx + NODE_WIDTH) : fx;
      var enterX = toRight ? tx : (tx + NODE_WIDTH);
      var midX = (exitX + enterX) / 2;

      pts = [
        exitX, fromCy,
        midX, fromCy,
        midX, toCy,
        enterX, toCy
      ];

      var polyline = document.createElementNS(svgNS, "polyline");
      var pointStr = "";
      for (var pi = 0; pi < pts.length; pi += 2) {
        if (pi > 0) pointStr += " ";
        pointStr += pts[pi] + "," + pts[pi + 1];
      }
      polyline.setAttribute("points", pointStr);
      polyline.setAttribute("fill", "none");
      polyline.setAttribute("stroke", unlockedIds.has(fromId) ? "#40c860" : "#334");
      polyline.setAttribute("stroke-width", "2");
      svg.appendChild(polyline);
    }
    treeEl.appendChild(svg);

    // Draw nodes
    for (var ni = 0; ni < nodes.length; ni++) {
      var node = nodes[ni];
      var isUnlocked = unlockedIds.has(node.id);
      var prerequisitesMet = canUnlock(node.id);
      var isSelected = selectedNodeId === node.id;

      var card = document.createElement("button");
      card.type = "button";
      card.className = "kspTechNode";
      if (isUnlocked) card.classList.add("isUnlocked");
      if (isSelected) card.classList.add("isSelected");
      if (!isUnlocked && prerequisitesMet) card.classList.add("isAvailable");
      if (!isUnlocked && !prerequisitesMet) card.classList.add("isLocked");

      card.style.left = ((Number(node.x) || 0) + offsetX) + "px";
      card.style.top = ((Number(node.y) || 0) + offsetY) + "px";
      card.style.width = NODE_WIDTH + "px";

      (function (n) {
        card.addEventListener("click", function () {
          if (_didDrag) return;
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
      } else if (node.auto_unlock) {
        costLine.textContent = "Free";
      } else {
        costLine.textContent = node.cost_rp + " RP";
      }
      card.appendChild(costLine);

      var itemCount = document.createElement("div");
      itemCount.className = "kspNodeItemCount";
      var ic = (node.items || []).length;
      itemCount.textContent = ic > 0 ? ic + " item" + (ic !== 1 ? "s" : "") : "";
      card.appendChild(itemCount);

      treeEl.appendChild(card);
    }

    /* Fit on first paint, preserve camera on re-paints */
    if (!_initialFitDone) {
      _initialFitDone = true;
      fitToView();
    } else {
      applyTransform();
    }
  }

  // ── Render sidebar info ───────────────────────────────────────────
  function renderInfo() {
    if (!treeData || !treeData.nodes) {
      infoTitleEl.textContent = "Loading...";
      infoTreeEl.textContent = "";
      infoListEl.innerHTML = "";
      unlockRowsEl.innerHTML = "";
      unlockSummaryEl.textContent = "";
      return;
    }

    var node = null;
    if (selectedNodeId) {
      node = treeData.nodes.find(function (n) { return n.id === selectedNodeId; });
    }
    if (!node && treeData.nodes.length) node = treeData.nodes[0];
    if (!node) {
      infoTitleEl.textContent = "No node selected";
      infoTreeEl.textContent = "";
      infoListEl.innerHTML = "";
      unlockRowsEl.innerHTML = "";
      return;
    }

    selectedNodeId = node.id;
    var isUnlocked = unlockedIds.has(node.id);
    var prerequisitesMet = canUnlock(node.id);

    infoTitleEl.textContent = node.name;
    infoTreeEl.textContent = "Cost: " + (node.auto_unlock ? "Free" : node.cost_rp + " RP");

    infoListEl.innerHTML = "";
    var infos = [
      "Status: " + (isUnlocked ? "Unlocked \u2713" : prerequisitesMet ? "Available" : "Locked (prerequisites needed)"),
      "Your RP: " + orgResearchPoints.toFixed(1),
    ];

    var prereqs = getPrereqs(node.id);
    if (prereqs.length > 0) {
      var prereqNames = prereqs.map(function (pid) {
        var pn = treeData.nodes.find(function (n) { return n.id === pid; });
        var name = pn ? pn.name : pid;
        var met = unlockedIds.has(pid);
        return name + (met ? " \u2713" : " \u2717");
      });
      infos.push("Prerequisites: " + prereqNames.join(", "));
    }

    for (var ii = 0; ii < infos.length; ii++) {
      var li = document.createElement("li");
      li.textContent = infos[ii];
      infoListEl.appendChild(li);
    }

    unlockSummaryEl.innerHTML = "";
    if (!isUnlocked && !node.auto_unlock) {
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
      (function (n) {
        btn.addEventListener("click", function () { doUnlock(n); });
      })(node);
      unlockSummaryEl.appendChild(btn);
    } else {
      unlockSummaryEl.textContent = "Technology unlocked \u2713";
    }

    unlockRowsEl.innerHTML = "";
    var items = node.items || [];
    if (items.length === 0) {
      unlockRowsEl.innerHTML = '<div class="muted small">No items on this node.</div>';
      return;
    }

    if (window.ItemDisplay) {
      var grid = document.createElement("div");
      grid.className = "researchUnlockGrid";
      for (var ix = 0; ix < items.length; ix++) {
        var item = items[ix];
        var catName = item.category_id || item.category || "";
        if (catName.startsWith("refineries_")) catName = "refinery";
        var subtitle = itemSubtitle(item);

        var tooltipLines = [];
        if (item.thrust_kn) tooltipLines.push(["Thrust", fmtNum(item.thrust_kn, "kN")]);
        if (item.isp_s) tooltipLines.push(["ISP", fmtNum(item.isp_s, "s")]);
        if (item.thermal_mw) tooltipLines.push(["Power", fmtNum(item.thermal_mw, "MWth")]);
        if (item.core_temp_k) tooltipLines.push(["Core Temp", fmtNum(item.core_temp_k, "K")]);
        if (item.rated_temp_k) tooltipLines.push(["Core Temp Req", fmtNum(item.rated_temp_k, "K")]);
        if (item.electric_mw) tooltipLines.push(["Electric", fmtNum(item.electric_mw, "MWe")]);
        if (item.heat_rejection_mw) tooltipLines.push(["Rejection", fmtNum(item.heat_rejection_mw, "MWth")]);
        if (item.scan_rate_km2_per_hr) tooltipLines.push(["Scan Rate", fmtNum(item.scan_rate_km2_per_hr, "km²/hr")]);
        if (item.mining_rate_kg_per_hr) tooltipLines.push(["Mining Rate", fmtNum(item.mining_rate_kg_per_hr, "kg/hr")]);
        if (item.construction_rate_kg_per_hr) tooltipLines.push(["Build Rate", fmtNum(item.construction_rate_kg_per_hr, "kg/hr")]);

        var cell = window.ItemDisplay.createGridCell({
          label: item.name,
          iconSeed: item.item_id || item.name,
          itemId: item.item_id,
          category: catName,
          mass_kg: item.mass_kg || 0,
          subtitle: subtitle,
          branch: item.branch || "",
          family: item.family || "",
          techLevel: String(item.tech_level || ""),
          water_extraction_kg_per_hr: item.water_extraction_kg_per_hr,
          min_water_ice_fraction: item.min_water_ice_fraction,
          max_water_ice_fraction: item.max_water_ice_fraction,
          tooltipLines: tooltipLines,
        });

        if (!isUnlocked) cell.classList.add("isLocked");
        grid.appendChild(cell);
      }
      unlockRowsEl.appendChild(grid);
    } else {
      for (var ix2 = 0; ix2 < items.length; ix2++) {
        var item2 = items[ix2];
        var row = document.createElement("div");
        row.className = "kspUnlockRow" + (isUnlocked ? " isUnlocked" : "");

        var nameSpan = document.createElement("div");
        nameSpan.className = "kspUnlockName";
        nameSpan.textContent = item2.name;
        row.appendChild(nameSpan);

        var statsSpan = document.createElement("div");
        statsSpan.className = "kspUnlockStats muted small";
        var stats2 = [];
        if (item2.mass_kg) stats2.push(fmtMass(item2.mass_kg));
        if (item2.thrust_kn) stats2.push(fmtNum(item2.thrust_kn, "kN"));
        if (item2.isp_s) stats2.push(fmtNum(item2.isp_s, "s ISP"));
        if (item2.thermal_mw) stats2.push(fmtNum(item2.thermal_mw, "MWth"));
        if (item2.electric_mw) stats2.push(fmtNum(item2.electric_mw, "MWe"));
        statsSpan.textContent = stats2.join(" \u00b7 ") || "\u2014";
        row.appendChild(statsSpan);

        unlockRowsEl.appendChild(row);
      }
    }
  }

  // ── Unlock action ─────────────────────────────────────────────────
  async function doUnlock(node) {
    var prereqs = getPrereqs(node.id);

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
    } catch (er) {
      alert("Error: " + er.message);
    }
  }

  loadTree();
})();
