(function () {
  const typeTabsEl = document.getElementById("researchTypeTabs");
  const treeTabsEl = document.getElementById("researchTreeTabs");
  const treeEl = document.getElementById("researchTree");
  const infoTitleEl = document.getElementById("researchInfoTitle");
  const infoTreeEl = document.getElementById("researchInfoTree");
  const infoListEl = document.getElementById("researchInfoList");
  const unlockSummaryEl = document.getElementById("researchUnlockSummary");
  const unlockRowsEl = document.getElementById("researchUnlockRows");
  const CATALOG_URL = "/static/data/research_catalog.json?v=rc1";
  const RESEARCH_NODE_WIDTH = 230;
  const RESEARCH_NODE_HEIGHT = 158;

  function slugify(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/&/g, " and ")
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "");
  }

  function makeTemplateTree(typeId, label) {
    const treeId = `${typeId}_${slugify(label)}`;
    const nodeIds = [
      `${treeId}_core`,
      `${treeId}_systems`,
      `${treeId}_integration`,
      `${treeId}_mastery`,
    ];

    return {
      id: treeId,
      label,
      description: `${label} research line. Placeholder template ready for detailed values.`,
      nodes: [
        {
          id: nodeIds[0],
          name: "Core Principles",
          passive: { stat: "Research Speed", perLevel: 1, unit: "%", sign: "+" },
          unlocks: {
            1: { name: `${label} Mk I`, massT: "TBD", thrustKN: "TBD", ispS: "TBD", reactorLevelRequired: "TBD" },
            4: { name: `${label} Mk II`, massT: "TBD", thrustKN: "TBD", ispS: "TBD", reactorLevelRequired: "TBD" },
          },
        },
        {
          id: nodeIds[1],
          name: "Advanced Systems",
          passive: { stat: "Efficiency", perLevel: 1, unit: "%", sign: "+" },
          unlocks: {
            1: { name: `${label} Variant I`, massT: "TBD", thrustKN: "TBD", ispS: "TBD", reactorLevelRequired: "TBD" },
            4: { name: `${label} Variant II`, massT: "TBD", thrustKN: "TBD", ispS: "TBD", reactorLevelRequired: "TBD" },
          },
        },
        {
          id: nodeIds[2],
          name: "Industrial Integration",
          passive: { stat: "Output", perLevel: 1, unit: "%", sign: "+" },
          unlocks: {
            1: { name: `${label} Production I`, massT: "TBD", thrustKN: "TBD", ispS: "TBD", reactorLevelRequired: "TBD" },
            4: { name: `${label} Production II`, massT: "TBD", thrustKN: "TBD", ispS: "TBD", reactorLevelRequired: "TBD" },
          },
        },
        {
          id: nodeIds[3],
          name: "Field Mastery",
          passive: { stat: "Reliability", perLevel: 1, unit: "%", sign: "+" },
          unlocks: {
            1: { name: `${label} Expert I`, massT: "TBD", thrustKN: "TBD", ispS: "TBD", reactorLevelRequired: "TBD" },
            4: { name: `${label} Expert II`, massT: "TBD", thrustKN: "TBD", ispS: "TBD", reactorLevelRequired: "TBD" },
          },
        },
      ],
      edges: [
        [nodeIds[0], nodeIds[1]],
        [nodeIds[1], nodeIds[2]],
        [nodeIds[2], nodeIds[3]],
      ],
      isTemplate: true,
    };
  }

  function buildFallbackCatalog() {
    const detailedNuclearThermal = {
      id: "thrusters_nuclear_thermal",
      label: "Nuclear Thermal",
      description: "Generation path for high-performance NTR propulsion.",
      nodes: [
        {
          id: "early_solid_core_ntr",
          name: "Early Solid Core NTR",
          passive: { stat: "ISP", perLevel: 1, unit: "%", sign: "+" },
          unlocks: {
            1: { name: "SCN-1 \"Pioneer\"", massT: 20, thrustKN: 250, ispS: 850, reactorLevelRequired: 2 },
            4: { name: "SCN-2 \"Frontier\"", massT: 28, thrustKN: 400, ispS: 900, reactorLevelRequired: 3 },
          },
        },
        {
          id: "advanced_solid_core_ntr",
          name: "Advanced Solid Core NTR",
          passive: { stat: "Thrust", perLevel: 2, unit: "%", sign: "+" },
          unlocks: {
            1: { name: "ASCN-1 \"Venture\"", massT: 24, thrustKN: 350, ispS: 950, reactorLevelRequired: 4 },
            4: { name: "ASCN-2 \"Atlas\"", massT: 35, thrustKN: 600, ispS: 1000, reactorLevelRequired: 5 },
          },
        },
        {
          id: "closed_gas_cycle_ntr",
          name: "Closed Gas Cycle NTR",
          passive: { stat: "Mass", perLevel: 2, unit: "%", sign: "-" },
          unlocks: {
            1: { name: "CGN-1 \"Helios\"", massT: 40, thrustKN: 500, ispS: 1400, reactorLevelRequired: 6 },
            4: { name: "CGN-2 \"Prometheus\"", massT: 55, thrustKN: 900, ispS: 1600, reactorLevelRequired: 7 },
          },
        },
        {
          id: "open_gas_cycle_ntr",
          name: "Open Gas Cycle NTR",
          passive: { stat: "ISP", perLevel: 1, unit: "%", sign: "+" },
          unlocks: {
            1: { name: "OGN-1 \"Icarus\"", massT: 60, thrustKN: 800, ispS: 2200, reactorLevelRequired: 8 },
            4: { name: "OGN-2 \"Daedalus\"", massT: 80, thrustKN: 1400, ispS: 2500, reactorLevelRequired: 9 },
          },
        },
      ],
      edges: [
        ["early_solid_core_ntr", "advanced_solid_core_ntr"],
        ["advanced_solid_core_ntr", "closed_gas_cycle_ntr"],
        ["closed_gas_cycle_ntr", "open_gas_cycle_ntr"],
      ],
      isTemplate: false,
    };

    return {
      techTypes: [
        {
          id: "thrusters",
          label: "Thrusters",
          trees: [
            detailedNuclearThermal,
            makeTemplateTree("thrusters", "Cryo"),
            makeTemplateTree("thrusters", "Solar"),
            makeTemplateTree("thrusters", "Nuclear Pulse"),
            makeTemplateTree("thrusters", "Electric"),
          ],
        },
        {
          id: "reactors",
          label: "Reactors",
          trees: [
            makeTemplateTree("reactors", "Fission"),
            makeTemplateTree("reactors", "Solar Concentrator"),
            makeTemplateTree("reactors", "Direct Plasma"),
            makeTemplateTree("reactors", "Z-Pinch"),
          ],
        },
        {
          id: "generators",
          label: "Generators",
          trees: [
            makeTemplateTree("generators", "Thermoelectric to Advanced Solid-State Conversion"),
            makeTemplateTree("generators", "Closed Brayton Cycle (Turbine)"),
            makeTemplateTree("generators", "Thermionic (Direct Emission)"),
            makeTemplateTree("generators", "Magnetohydrodynamic (MHD)"),
          ],
        },
        {
          id: "robonauts",
          label: "Robonauts",
          trees: [
            makeTemplateTree("robonauts", "Raygun"),
            makeTemplateTree("robonauts", "Missile"),
            makeTemplateTree("robonauts", "Rover"),
          ],
        },
        {
          id: "constructors",
          label: "Constructors",
          trees: [
            makeTemplateTree("constructors", "Gravity"),
            makeTemplateTree("constructors", "Microgravity"),
            makeTemplateTree("constructors", "Cryovolitile"),
          ],
        },
        {
          id: "refineries",
          label: "Refineries",
          trees: [
            makeTemplateTree("refineries", "Lithic Processing"),
            makeTemplateTree("refineries", "Metallurgy"),
            makeTemplateTree("refineries", "Volatiles & Cryogenics"),
            makeTemplateTree("refineries", "Nuclear & Exotic"),
          ],
        },
        {
          id: "radiators",
          label: "Radiators",
          trees: [
            makeTemplateTree("radiators", "Rigid Panels"),
            makeTemplateTree("radiators", "Liquid Sheet Radiators"),
            makeTemplateTree("radiators", "Spinning Heat Sink Radiators"),
            makeTemplateTree("radiators", "Phase Change Radiator"),
          ],
        },
      ],
    };
  }

  let researchCatalog = { techTypes: [] };

  const roman = ["I", "II", "III", "IV", "V"];
  const levelsByNode = {};

  let activeTypeId = null;
  let activeTreeId = null;
  let selectedNodeId = null;

  function withDefaultUnlocks(label) {
    return {
      1: { name: `${label} Mk I`, massT: "TBD", thrustKN: "TBD", ispS: "TBD", reactorLevelRequired: "TBD" },
      4: { name: `${label} Mk II`, massT: "TBD", thrustKN: "TBD", ispS: "TBD", reactorLevelRequired: "TBD" },
    };
  }

  function hydrateTree(typeId, tree) {
    if (!tree || typeof tree !== "object") return null;
    if (!tree.template && Array.isArray(tree.nodes) && tree.nodes.length) {
      return {
        ...tree,
        isTemplate: false,
      };
    }

    const generated = makeTemplateTree(typeId, tree.label || "Untitled Tree");
    return {
      ...generated,
      ...tree,
      id: tree.id || generated.id,
      label: tree.label || generated.label,
      description:
        tree.description || `${tree.label || "This"} research line. Placeholder template ready for detailed values.`,
      nodes: (tree.nodes || generated.nodes).map((node, index) => ({
        ...generated.nodes[index],
        ...node,
        unlocks: {
          ...withDefaultUnlocks(tree.label || "Tree"),
          ...(node.unlocks || {}),
        },
      })),
      edges: tree.edges || generated.edges,
      isTemplate: tree.template !== false,
    };
  }

  function hydrateCatalog(rawCatalog) {
    const techTypes = Array.isArray(rawCatalog?.techTypes) ? rawCatalog.techTypes : [];
    return {
      techTypes: techTypes
        .map((type) => {
          const trees = Array.isArray(type?.trees) ? type.trees : [];
          const hydratedTrees = trees
            .map((tree) => hydrateTree(type.id, tree))
            .filter(Boolean);
          if (!hydratedTrees.length) return null;
          return {
            id: type.id,
            label: type.label,
            trees: hydratedTrees,
          };
        })
        .filter(Boolean),
    };
  }

  async function loadCatalog() {
    try {
      const resp = await fetch(CATALOG_URL, { cache: "no-store" });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data?.detail || `Failed to load research catalog (${resp.status})`);
      }
      return hydrateCatalog(data);
    } catch (err) {
      console.warn("research catalog load failed, using fallback", err);
      return buildFallbackCatalog();
    }
  }

  function setInitialStateFromCatalog() {
    const firstType = researchCatalog.techTypes[0] || null;
    activeTypeId = firstType?.id || null;
    activeTreeId = firstType?.trees?.[0]?.id || null;
    selectedNodeId = firstType?.trees?.[0]?.nodes?.[0]?.id || null;
  }

  function activeType() {
    return researchCatalog.techTypes.find((type) => type.id === activeTypeId) || researchCatalog.techTypes[0] || null;
  }

  function activeTree() {
    const type = activeType();
    if (!type) return null;
    return type.trees.find((tree) => tree.id === activeTreeId) || type.trees[0] || null;
  }

  function selectedNode() {
    const tree = activeTree();
    if (!tree) return null;
    return tree.nodes.find((node) => node.id === selectedNodeId) || tree.nodes[0] || null;
  }

  function initLevels() {
    researchCatalog.techTypes.forEach((type) => {
      type.trees.forEach((tree) => {
        tree.nodes.forEach((node) => {
          if (typeof levelsByNode[node.id] !== "number") levelsByNode[node.id] = 0;
        });
      });
    });
  }

  function formatPassive(passive, level) {
    if (!passive) return "";
    const value = Math.max(0, Number(passive.perLevel || 0) * Math.max(0, Number(level || 0)));
    const sign = passive.sign === "-" ? "-" : "+";
    return `${passive.stat} ${sign}${value}${passive.unit || ""}`;
  }

  function renderTypeTabs() {
    typeTabsEl.innerHTML = "";
    researchCatalog.techTypes.forEach((type) => {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = `tab researchSubtab ${type.id === activeTypeId ? "active" : ""}`;
      tab.textContent = type.label;
      tab.setAttribute("role", "tab");
      tab.addEventListener("click", () => {
        activeTypeId = type.id;
        activeTreeId = type.trees[0]?.id || null;
        selectedNodeId = type.trees[0]?.nodes?.[0]?.id || null;
        renderTypeTabs();
        renderTreeTabs();
        renderTree();
        renderInfo();
      });
      typeTabsEl.appendChild(tab);
    });
  }

  function renderTreeTabs() {
    const type = activeType();
    treeTabsEl.innerHTML = "";
    (type?.trees || []).forEach((tree) => {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = `tab researchSubtab ${tree.id === activeTreeId ? "active" : ""}`;
      tab.textContent = tree.label;
      tab.setAttribute("role", "tab");
      tab.addEventListener("click", () => {
        activeTreeId = tree.id;
        selectedNodeId = tree.nodes[0]?.id || null;
        renderTreeTabs();
        renderTree();
        renderInfo();
      });
      treeTabsEl.appendChild(tab);
    });
  }

  function renderEdges(tree, nodeById) {
    (tree.edges || []).forEach(([fromId, toId]) => {
      const fromNode = nodeById.get(fromId);
      const toNode = nodeById.get(toId);
      if (!fromNode || !toNode) return;

      const edgeEl = document.createElement("div");
      edgeEl.className = "researchSkillEdge";

      const x1 = Number(fromNode.x) + RESEARCH_NODE_WIDTH;
      const y1 = Number(fromNode.y) + Math.round(RESEARCH_NODE_HEIGHT * 0.5);
      const x2 = Number(toNode.x);
      const y2 = Number(toNode.y) + Math.round(RESEARCH_NODE_HEIGHT * 0.5);
      const dx = x2 - x1;
      const dy = y2 - y1;
      const width = Math.max(1, Math.hypot(dx, dy));
      const angle = (Math.atan2(dy, dx) * 180) / Math.PI;

      edgeEl.style.left = `${x1}px`;
      edgeEl.style.top = `${y1}px`;
      edgeEl.style.width = `${width}px`;
      edgeEl.style.transform = `rotate(${angle}deg)`;
      treeEl.appendChild(edgeEl);
    });
  }

  function renderLevelButtons(node, nodeCardEl) {
    const row = document.createElement("div");
    row.className = "researchLevelRow";

    roman.forEach((label, index) => {
      const targetLevel = index + 1;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `researchLevelBtn ${levelsByNode[node.id] >= targetLevel ? "isReached" : ""}`;
      btn.textContent = label;
      btn.title = `Set ${node.name} to Level ${label}`;
      btn.addEventListener("click", (event) => {
        event.stopPropagation();
        levelsByNode[node.id] = targetLevel;
        selectedNodeId = node.id;
        renderTree();
        renderInfo();
      });
      row.appendChild(btn);
    });

    nodeCardEl.appendChild(row);
  }

  function layoutTree(tree) {
    return tree.nodes.map((node, index) => ({
      ...node,
      x: 40 + index * 270,
      y: 84 + (index % 2 === 0 ? 0 : 152),
    }));
  }

  function renderTree() {
    const tree = activeTree();
    if (!tree) {
      treeEl.innerHTML = "";
      return;
    }

    treeEl.innerHTML = "";
    const layoutNodes = layoutTree(tree);
    const nodeById = new Map(layoutNodes.map((node) => [node.id, node]));
    renderEdges(tree, nodeById);

    layoutNodes.forEach((node) => {
      const nodeLevel = levelsByNode[node.id] || 0;

      const card = document.createElement("button");
      card.type = "button";
      card.className = `researchSkillNode ${selectedNodeId === node.id ? "isSelected" : ""}`;
      card.style.left = `${node.x}px`;
      card.style.top = `${node.y}px`;
      card.addEventListener("click", () => {
        selectedNodeId = node.id;
        renderTree();
        renderInfo();
      });

      const name = document.createElement("div");
      name.className = "researchSkillNodeName";
      name.textContent = node.name;
      card.appendChild(name);

      const level = document.createElement("div");
      level.className = "researchSkillNodeLevel";
      level.textContent = `Level ${nodeLevel}/5`;
      card.appendChild(level);

      const passive = document.createElement("div");
      passive.className = "researchSkillPassive";
      passive.textContent = `Org Bonus: ${formatPassive(node.passive, nodeLevel) || "None"}`;
      card.appendChild(passive);

      renderLevelButtons(node, card);
      treeEl.appendChild(card);
    });
  }

  function renderUnlockRows(node, nodeLevel) {
    unlockRowsEl.innerHTML = "";

    [1, 4].forEach((unlockLevel) => {
      const unlock = node.unlocks?.[unlockLevel];
      if (!unlock) return;

      const row = document.createElement("div");
      row.className = `researchUnlockRow ${nodeLevel >= unlockLevel ? "isUnlocked" : ""}`;

      const title = document.createElement("div");
      title.className = "researchUnlockName";
      title.textContent = `Level ${roman[unlockLevel - 1]}: ${unlock.name}`;
      row.appendChild(title);

      const stats = document.createElement("div");
      stats.className = "researchUnlockStats";
      stats.textContent = `Mass ${unlock.massT} t · Thrust ${unlock.thrustKN} kN · ISP ${unlock.ispS} s · Reactor ${unlock.reactorLevelRequired}`;
      row.appendChild(stats);

      unlockRowsEl.appendChild(row);
    });
  }

  function renderInfo() {
    const tree = activeTree();
    const node = selectedNode();

    if (!tree || !node) {
      infoTitleEl.textContent = "No research data loaded";
      infoTreeEl.textContent = "";
      infoListEl.innerHTML = "";
      unlockRowsEl.innerHTML = "";
      return;
    }

    const type = activeType();
    const nodeLevel = levelsByNode[node.id] || 0;
    const passivePerLevel = `${node.passive.sign}${node.passive.perLevel}${node.passive.unit} ${node.passive.stat}`;

    infoTitleEl.textContent = node.name;
    infoTreeEl.textContent = `${type?.label || "Research"} · ${tree.label}`;
    unlockSummaryEl.textContent = `Current Level ${nodeLevel}/5 · Unlocks at Level I and IV`;
    infoListEl.innerHTML = "";

    [
      `Passive per level: ${passivePerLevel}`,
      `Total organization bonus: ${formatPassive(node.passive, nodeLevel)}`,
      "Each node has five levels and grants cumulative bonuses.",
      tree.isTemplate ? "Template tree: replace TBD values as you define this line." : "Configured tree: values are live for this line.",
      "Set node level directly from the level chips on each node card.",
    ].forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      infoListEl.appendChild(li);
    });

    renderUnlockRows(node, nodeLevel);
  }

  loadCatalog()
    .then((catalog) => {
      researchCatalog = catalog;
      setInitialStateFromCatalog();
      initLevels();
      renderTypeTabs();
      renderTreeTabs();
      renderTree();
      renderInfo();
    })
    .catch((error) => {
      infoTitleEl.textContent = "Research catalog error";
      infoTreeEl.textContent = error?.message || "Failed to load catalog data.";
      infoListEl.innerHTML = "";
      unlockSummaryEl.textContent = "";
      unlockRowsEl.innerHTML = "";
      treeEl.innerHTML = "";
      typeTabsEl.innerHTML = "";
      treeTabsEl.innerHTML = "";
    });
})();