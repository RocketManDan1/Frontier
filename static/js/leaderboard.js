(function () {
  if (window.__leaderboardLoaded) return;
  window.__leaderboardLoaded = true;

  var POLL_INTERVAL = 30000; // 30 seconds
  var container = null;

  function createWidget() {
    container = document.getElementById("scenarioLeaderboard");
    if (!container) return null;
    container.innerHTML =
      '<div class="lbHeader">' +
        '<div class="lbTitle">SCENARIO 1: FUSION DAWN</div>' +
        '<div class="lbObjective">Construct the Helion He3 Tokamak Fusion Reactor</div>' +
      '</div>' +
      '<div class="lbBody"></div>';
    return container;
  }

  function renderLeaderboard(data) {
    if (!container) return;
    var body = container.querySelector(".lbBody");
    if (!body) return;

    var entries = (data && data.leaderboard) || [];
    if (entries.length === 0) {
      body.innerHTML = '<div class="lbEmpty">No corporations registered</div>';
      return;
    }

    var html = '<table class="lbTable">' +
      '<thead><tr>' +
        '<th class="lbRank">#</th>' +
        '<th class="lbCorp">Corporation</th>' +
        '<th class="lbRes">Research</th>' +
        '<th class="lbHe3">He\u00B3</th>' +
        '<th class="lbBuild">Build</th>' +
        '<th class="lbTotal">Total</th>' +
      '</tr></thead><tbody>';

    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      var isWinner = e.winner;
      var rowClass = isWinner ? "lbRow lbWinner" : "lbRow";
      html += '<tr class="' + rowClass + '">' +
        '<td class="lbRank">' + (i + 1) + '</td>' +
        '<td class="lbCorp">' +
          '<span class="lbSwatch" style="background:' + escapeAttr(e.color) + '"></span>' +
          '<span class="lbCorpName">' + escapeHtml(e.corp_name) + '</span>' +
          (isWinner ? ' <span class="lbWinBadge">★ WINNER</span>' : '') +
        '</td>' +
        '<td class="lbRes">' + formatPct(e.research_pct) + '</td>' +
        '<td class="lbHe3">' + formatPct(e.he3_pct) + '</td>' +
        '<td class="lbBuild">' + formatPct(e.build_pct) + '</td>' +
        '<td class="lbTotal">' +
          '<div class="lbBarOuter">' +
            '<div class="lbBarFill" style="width:' + Math.min(100, e.total_pct) + '%"></div>' +
          '</div>' +
          '<span class="lbTotalNum">' + formatPct(e.total_pct) + '</span>' +
        '</td>' +
      '</tr>';
    }

    html += '</tbody></table>';
    body.innerHTML = html;
  }

  function formatPct(v) {
    if (v == null) return "0%";
    return v.toFixed(1) + "%";
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function escapeAttr(s) {
    return String(s).replace(/[&"'<>]/g, function (c) {
      return { "&": "&amp;", '"': "&quot;", "'": "&#39;", "<": "&lt;", ">": "&gt;" }[c];
    });
  }

  async function fetchLeaderboard() {
    try {
      var resp = await fetch("/api/org/leaderboard", { cache: "no-store" });
      if (!resp.ok) return null;
      return await resp.json();
    } catch (e) {
      return null;
    }
  }

  async function poll() {
    var data = await fetchLeaderboard();
    if (data) renderLeaderboard(data);
  }

  // ── CSS ────────────────────────────────────────────────────────────
  var style = document.createElement("style");
  style.textContent = [
    ".scenarioLeaderboard {",
    "  position: absolute;",
    "  top: 50%;",
    "  right: 14px;",
    "  transform: translateY(-50%);",
    "  z-index: 8;",
    "  background: rgba(10, 16, 26, 0.88);",
    "  border: 1px solid rgba(100,132,166,0.28);",
    "  border-radius: 6px;",
    "  padding: 10px 12px;",
    "  min-width: 340px;",
    "  max-width: 420px;",
    "  max-height: 60vh;",
    "  overflow-y: auto;",
    "  font-family: inherit;",
    "  font-size: 11px;",
    "  color: var(--text, #d7e6f2);",
    "  pointer-events: auto;",
    "  opacity: 0.80;",
    "  transition: opacity 0.2s;",
    "}",
    ".scenarioLeaderboard:hover {",
    "  opacity: 1;",
    "  border-color: rgba(126,166,206,0.45);",
    "}",
    ".lbHeader {",
    "  margin-bottom: 8px;",
    "  text-align: center;",
    "}",
    ".lbTitle {",
    "  font-size: 11px;",
    "  font-weight: 700;",
    "  letter-spacing: 0.10em;",
    "  text-transform: uppercase;",
    "  color: rgba(220, 210, 160, 0.9);",
    "}",
    ".lbObjective {",
    "  font-size: 10px;",
    "  color: rgba(170,192,212,0.64);",
    "  margin-top: 2px;",
    "  font-style: italic;",
    "}",
    ".lbTable {",
    "  width: 100%;",
    "  border-collapse: collapse;",
    "  font-size: 11px;",
    "}",
    ".lbTable th {",
    "  text-align: left;",
    "  font-size: 9px;",
    "  text-transform: uppercase;",
    "  letter-spacing: 0.06em;",
    "  color: rgba(170,192,212,0.5);",
    "  padding: 2px 4px 4px;",
    "  border-bottom: 1px solid rgba(100,132,166,0.2);",
    "  font-weight: 600;",
    "}",
    ".lbTable td {",
    "  padding: 3px 4px;",
    "  vertical-align: middle;",
    "  white-space: nowrap;",
    "}",
    ".lbRank {",
    "  width: 20px;",
    "  text-align: center;",
    "  color: rgba(170,192,212,0.5);",
    "  font-weight: 600;",
    "}",
    ".lbCorp {",
    "  max-width: 120px;",
    "  overflow: hidden;",
    "  text-overflow: ellipsis;",
    "}",
    ".lbSwatch {",
    "  display: inline-block;",
    "  width: 8px;",
    "  height: 8px;",
    "  border-radius: 2px;",
    "  margin-right: 5px;",
    "  vertical-align: middle;",
    "  border: 1px solid rgba(255,255,255,0.12);",
    "}",
    ".lbCorpName {",
    "  vertical-align: middle;",
    "}",
    ".lbRes, .lbHe3, .lbBuild {",
    "  text-align: right;",
    "  font-variant-numeric: tabular-nums;",
    "  color: rgba(170,192,212,0.72);",
    "  font-size: 10px;",
    "}",
    ".lbTotal {",
    "  text-align: right;",
    "  min-width: 80px;",
    "}",
    ".lbBarOuter {",
    "  display: inline-block;",
    "  width: 48px;",
    "  height: 6px;",
    "  background: rgba(100,132,166,0.15);",
    "  border-radius: 3px;",
    "  overflow: hidden;",
    "  vertical-align: middle;",
    "  margin-right: 4px;",
    "}",
    ".lbBarFill {",
    "  height: 100%;",
    "  border-radius: 3px;",
    "  background: linear-gradient(90deg, #3a7bd5, #59b9e6);",
    "  transition: width 0.5s ease;",
    "}",
    ".lbWinner .lbBarFill {",
    "  background: linear-gradient(90deg, #d4a017, #ffd700);",
    "}",
    ".lbTotalNum {",
    "  font-weight: 600;",
    "  font-variant-numeric: tabular-nums;",
    "  vertical-align: middle;",
    "  font-size: 11px;",
    "}",
    ".lbWinner .lbTotalNum {",
    "  color: #ffd700;",
    "}",
    ".lbWinBadge {",
    "  font-size: 9px;",
    "  color: #ffd700;",
    "  font-weight: 700;",
    "  vertical-align: middle;",
    "}",
    ".lbRow:hover {",
    "  background: rgba(100,132,166,0.08);",
    "}",
    ".lbWinner {",
    "  background: rgba(255,215,0,0.06);",
    "}",
    ".lbEmpty {",
    "  color: rgba(170,192,212,0.5);",
    "  font-style: italic;",
    "  text-align: center;",
    "  padding: 8px 0;",
    "}",
    "@media (max-width: 900px) {",
    "  .scenarioLeaderboard {",
    "    min-width: 260px;",
    "    max-width: 300px;",
    "    font-size: 10px;",
    "  }",
    "  .lbRes, .lbHe3, .lbBuild { display: none; }",
    "}",
  ].join("\n");
  document.head.appendChild(style);

  // ── Boot ───────────────────────────────────────────────────────────
  var w = createWidget();
  if (w) {
    poll();
    setInterval(poll, POLL_INTERVAL);
  }
})();
