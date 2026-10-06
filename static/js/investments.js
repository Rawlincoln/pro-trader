const $ = (id) => document.getElementById(id);

function fmtKes(v, {sign = false, digits = 2} = {}) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  const body = n.toLocaleString("en-KE", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  const prefix = sign ? (n > 0 ? "+" : "") : "";
  return `${prefix}${body} KES`;
}

function pnlClass(v) {
  if (v > 0) return "up";
  if (v < 0) return "down";
  return "";
}

function renderHero(summary, windowInfo) {
  $("inv-status-label").textContent = summary.status_label || "—";
  $("inv-status-label").className = "bs-hero-label " + (summary.is_profitable ? "profit" : "loss");
  $("inv-total-pnl").textContent = fmtKes(summary.net_pnl, {sign: true});
  $("inv-total-pnl").className = "bs-hero-pnl " + pnlClass(summary.net_pnl);
  $("inv-hero-sub").textContent = `${summary.portfolios} books · net after 2% entry and 3% exit fees`;
  $("inv-hero-meta").textContent = windowInfo?.label || "Past 12 months";
  $("inv-hero-count").textContent = String(summary.portfolios ?? 5);

  $("inv-invested").textContent = fmtKes(summary.invested);
  $("inv-entry-fees").textContent = fmtKes(summary.entry_fees);
  $("inv-gross-returns").textContent = fmtKes(summary.gross_returns);
  $("inv-exit-fees").textContent = fmtKes(summary.exit_fees);
  $("inv-net-proceeds").textContent = fmtKes(summary.net_proceeds);
  $("inv-roi").textContent = summary.roi_pct != null ? `${summary.roi_pct}%` : "—";
  $("inv-roi").className = "value " + pnlClass(summary.net_pnl);
}

function renderChart(chart) {
  const el = $("inv-chart");
  if (!el || typeof Plotly === "undefined") return;
  const traces = (chart.series || []).map((s) => ({
    x: chart.dates,
    y: s.values,
    type: "scatter",
    mode: "lines",
    name: s.name,
    line: {color: s.color, width: 2.4, shape: "spline"},
    hovertemplate: "%{fullData.name}<br>%{x}<br>%{y:,.2f} KES<extra></extra>",
  }));
  traces.push({
    x: chart.dates,
    y: chart.total,
    type: "scatter",
    mode: "lines",
    name: "All books",
    line: {color: "#e0f7ff", width: 2.8},
    fill: "tozeroy",
    fillcolor: "rgba(125, 211, 252, 0.08)",
    hovertemplate: "All books<br>%{x}<br>%{y:,.2f} KES<extra></extra>",
  });
  Plotly.newPlot(el, traces, {
    margin: {t: 12, r: 16, b: 40, l: 64},
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: {color: "#8ba3c0", family: "DM Sans, Segoe UI, sans-serif"},
    legend: {
      orientation: "h",
      y: 1.12,
      font: {size: 11, color: "#cfe7ff"},
    },
    xaxis: {
      gridcolor: "rgba(120, 200, 255, 0.12)",
      zerolinecolor: "rgba(120, 200, 255, 0.18)",
      linecolor: "rgba(120, 200, 255, 0.18)",
    },
    yaxis: {
      gridcolor: "rgba(120, 200, 255, 0.12)",
      zerolinecolor: "rgba(120, 200, 255, 0.18)",
      title: {text: "KES", font: {size: 11, color: "#8ba3c0"}},
      tickformat: ",.0f",
    },
    hovermode: "x unified",
  }, {responsive: true, displayModeBar: false});
}

function cardHtml(p) {
  const hi = p.highlight ? " inv-card-highlight" : "";
  return `
    <article class="inv-card au-tile${hi}" data-id="${p.id}">
      <header class="inv-card-top">
        <div>
          <p class="inv-card-kicker">${p.market} · ${p.side} · ${p.period_label}</p>
          <h3>${p.name}</h3>
        </div>
        <span class="inv-lev">×${p.leverage}</span>
      </header>
      <p class="inv-card-note">${p.note}</p>
      <dl class="inv-dl">
        <div><dt>Invested</dt><dd>${fmtKes(p.gross_invest)}</dd></div>
        <div><dt>Entry fee 2%</dt><dd>−${fmtKes(p.entry_fee)}</dd></div>
        <div><dt>Net deployed</dt><dd>${fmtKes(p.net_deployed)}</dd></div>
        <div><dt>Gross returns</dt><dd>${fmtKes(p.gross_returns)}</dd></div>
        <div><dt>Exit fee 3%</dt><dd>−${fmtKes(p.exit_fee)}</dd></div>
        <div><dt>Net received</dt><dd>${fmtKes(p.net_proceeds)}</dd></div>
      </dl>
      <div class="inv-card-foot">
        <span class="inv-pnl ${pnlClass(p.net_pnl)}">${fmtKes(p.net_pnl, {sign: true})}</span>
        <span class="inv-roi">${p.roi_pct}% ROI · underlying ~${p.implied_underlying_move_pct}%</span>
      </div>
    </article>
  `;
}

function renderCards(portfolios) {
  const el = $("inv-cards");
  if (!el) return;
  el.innerHTML = portfolios.map(cardHtml).join("");
}

function renderTable(portfolios) {
  const el = $("inv-table");
  if (!el) return;
  const rows = portfolios.map((p) => `
    <tr>
      <td>${p.period_label}</td>
      <td>${p.symbol} ${p.leverage}×</td>
      <td>${fmtKes(p.gross_invest)}</td>
      <td>${fmtKes(p.entry_fee)}</td>
      <td>${fmtKes(p.net_deployed)}</td>
      <td>${fmtKes(p.gross_returns)}</td>
      <td>${fmtKes(p.exit_fee)}</td>
      <td>${fmtKes(p.net_proceeds)}</td>
      <td class="${pnlClass(p.net_pnl)}">${fmtKes(p.net_pnl, {sign: true})}</td>
    </tr>
  `).join("");
  el.innerHTML = `<table class="bs-table inv-table">
    <thead>
      <tr>
        <th>Period</th>
        <th>Book</th>
        <th>Invested</th>
        <th>Fee 2%</th>
        <th>Deployed</th>
        <th>Gross returns</th>
        <th>Fee 3%</th>
        <th>Net received</th>
        <th>Net P&amp;L</th>
      </tr>
    </thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function loadInvestments() {
  const res = await fetch("/api/investments", {cache: "no-store"});
  if (!res.ok) throw new Error("Failed to load investments");
  const data = await res.json();
  renderHero(data.summary || {}, data.window || {});
  renderChart(data.chart || {dates: [], series: [], total: []});
  renderCards(data.portfolios || []);
  renderTable(data.portfolios || []);
}

loadInvestments().catch((err) => {
  const sub = $("inv-hero-sub");
  if (sub) sub.textContent = err.message || "Could not load investment books";
});
