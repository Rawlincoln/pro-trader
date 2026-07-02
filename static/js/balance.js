const $ = (id) => document.getElementById(id);

function fmtMoney(v, cur = "USD") {
  if (v == null || Number.isNaN(v)) return "—";
  const n = Number(v);
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${cur}`;
}

function pnlClass(v) {
  if (v > 0) return "up";
  if (v < 0) return "down";
  return "";
}

function renderTable(containerId, headers, rows, emptyMsg) {
  const el = $(containerId);
  if (!el) return;
  if (!rows?.length) {
    el.innerHTML = `<p class="bs-empty">${emptyMsg}</p>`;
    return;
  }
  el.innerHTML = `<table class="bs-table"><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.join("")}</tbody></table>`;
}

function renderBalanceSheet(data) {
  const sheet = data.balance_sheet || {};
  const s = sheet.summary || {};
  const acc = sheet.account || {};
  const cur = acc.currency || "USD";

  const total = s.total_net_pnl ?? 0;
  const label = s.status_label || "—";
  $("bs-status-label").textContent = label;
  $("bs-status-label").className = "bs-hero-label " + (s.is_profitable ? "profit" : total < 0 ? "loss" : "flat");
  $("bs-total-pnl").textContent = fmtMoney(total, cur);
  $("bs-total-pnl").className = "bs-hero-pnl " + pnlClass(total);
  $("bs-hero-sub").textContent = s.is_profitable
    ? "Net profitable across closed + open trades"
    : total < 0
      ? "Net down — review losing symbols below"
      : "Break-even so far";

  $("bs-balance").textContent = fmtMoney(acc.balance, cur).replace(/^\+/, "");
  $("bs-equity").textContent = fmtMoney(acc.equity, cur).replace(/^\+/, "");
  $("bs-closed-pnl").textContent = fmtMoney(s.closed_net_pnl, cur);
  $("bs-closed-pnl").className = "value " + pnlClass(s.closed_net_pnl);
  $("bs-open-pnl").textContent = fmtMoney(s.open_floating_pnl, cur);
  $("bs-open-pnl").className = "value " + pnlClass(s.open_floating_pnl);
  $("bs-win-rate").textContent = s.win_rate_pct != null ? `${s.win_rate_pct}%` : "—";
  $("bs-wl").textContent = `${s.wins ?? 0} / ${s.losses ?? 0}`;
  $("bs-trade-count").textContent = s.closed_trades ?? 0;
  $("bs-synced").textContent = data.synced_at
    ? new Date(data.synced_at).toLocaleString()
    : "Never";

  renderChart(sheet.daily_pnl || []);

  const symRows = (sheet.by_symbol || []).map((r) => `
    <tr>
      <td>${r.symbol}</td>
      <td>${r.trades}</td>
      <td>${r.wins}/${r.losses}</td>
      <td class="${pnlClass(r.net_pnl)}">${fmtMoney(r.net_pnl, cur)}</td>
    </tr>
  `);
  renderTable("bs-by-symbol", ["Symbol", "Trades", "W/L", "Net P&L"], symRows, "No closed trades yet");

  const openRows = (sheet.open_positions || []).map((p) => `
    <tr>
      <td>${p.symbol}</td>
      <td>${p.type}</td>
      <td>${p.volume}</td>
      <td class="${pnlClass(p.profit)}">${fmtMoney(p.profit, cur)}</td>
    </tr>
  `);
  renderTable("bs-open-positions", ["Symbol", "Side", "Lots", "Floating"], openRows, "No open positions");

  const tradeRows = (sheet.recent_trades || []).map((t) => `
    <tr>
      <td>${(t.close_time || "").slice(0, 16).replace("T", " ")}</td>
      <td>${t.symbol}</td>
      <td>${t.side}</td>
      <td>${t.volume}</td>
      <td class="${pnlClass(t.net_pnl)}">${fmtMoney(t.net_pnl, cur)}</td>
    </tr>
  `);
  renderTable("bs-recent-trades", ["Closed", "Symbol", "Side", "Lots", "Net P&L"], tradeRows, "No trades yet");
}

function renderChart(daily) {
  const el = $("bs-chart");
  if (!el || typeof Plotly === "undefined") return;
  if (!daily.length) {
    el.innerHTML = "<p class='bs-empty'>No daily P&L data yet</p>";
    return;
  }
  Plotly.newPlot(el, [{
    x: daily.map((d) => d.date),
    y: daily.map((d) => d.cumulative),
    type: "scatter",
    mode: "lines",
    fill: "tozeroy",
    line: { color: "#8b5cf6", width: 2 },
    fillcolor: "rgba(139,92,246,0.15)",
  }], {
    margin: { t: 10, r: 20, b: 40, l: 50 },
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#94a3b8" },
    xaxis: { gridcolor: "#1e293b" },
    yaxis: { gridcolor: "#1e293b", title: "Cumulative P&L" },
  }, { responsive: true, displayModeBar: false });
}

function setSyncStatus(connected, msg, syncSource) {
  const dot = $("mt5-status-dot");
  const text = $("mt5-status-text");
  if (!dot || !text) return;
  const online = connected || syncSource === "myfxbook";
  dot.className = "status-dot " + (online ? "online" : "offline");
  if (syncSource === "myfxbook") {
    text.textContent = msg || "Myfxbook connected";
  } else {
    text.textContent = msg || (connected ? "Connected" : "Not connected");
  }
}

async function loadBalance() {
  try {
    const res = await fetch("/api/balance-sheet");
    const data = await res.json();
    setSyncStatus(data.mt5_connected, data.mt5_message, data.sync_source);
    if (data.balance_sheet) renderBalanceSheet(data);
  } catch {
    setSyncStatus(false, "Could not load — is the app running?");
  }
}

async function syncMyfxbook() {
  const btn = $("btn-sync-mfb");
  if (btn) { btn.disabled = true; btn.textContent = "Syncing…"; }
  try {
    const res = await fetch("/api/myfxbook/sync", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      setSyncStatus(false, data.error || "Sync failed", "myfxbook");
      return;
    }
    setSyncStatus(true, "Synced", "myfxbook");
    renderBalanceSheet({ synced_at: data.synced_at, balance_sheet: data.balance_sheet });
  } catch {
    setSyncStatus(false, "Sync failed", "myfxbook");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Sync"; }
  }
}

$("btn-sync-mfb")?.addEventListener("click", syncMyfxbook);
loadBalance();
setInterval(loadBalance, 60000);