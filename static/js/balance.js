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

function renderSetup(setup) {
  const el = $("setup-checklist");
  if (!el || !setup?.length) return;
  el.innerHTML = setup.map((s) => `
    <li class="${s.done ? "done" : "todo"}">
      <span class="setup-icon">${s.done ? "✓" : "○"}</span>
      <span>${s.label}</span>
      ${s.hint ? `<span class="setup-hint">${s.hint}</span>` : ""}
    </li>
  `).join("");
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
    ? "You are net profitable across closed + open trades"
    : total < 0
      ? "You are net down — review losing symbols below"
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
  renderTable("bs-recent-trades", ["Closed", "Symbol", "Side", "Lots", "Net P&L"], tradeRows, "Sync from MT5 to load trade history");
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

function setMt5Status(connected, msg, syncSource) {
  const dot = $("mt5-status-dot");
  const text = $("mt5-status-text");
  if (!dot || !text) return;
  const mfb = syncSource === "myfxbook";
  dot.className = "status-dot " + (connected || mfb ? "online" : "offline");
  if (mfb) {
    text.textContent = msg || "Myfxbook cloud connected";
  } else {
    text.textContent = msg || (connected ? "MT5 connected" : "MT5 not connected");
  }
}

function renderMfbAccounts(mfb) {
  const el = $("mfb-accounts");
  const status = $("mfb-status");
  if (!el) return;
  if (!mfb?.accounts?.length) {
    if (status && mfb?.message) status.textContent = mfb.message;
    return;
  }
  if (status) status.textContent = mfb.message || "";
  const rows = mfb.accounts.map((a) => `
    <tr>
      <td><code>${a.id}</code></td>
      <td>${a.name || "—"}</td>
      <td>${a.account_id ?? "—"}</td>
      <td>${a.server || "—"}</td>
      <td>${a.balance != null ? Number(a.balance).toFixed(2) : "—"}</td>
    </tr>
  `);
  renderTable("mfb-accounts", ["Myfxbook ID", "Name", "MT5 login", "Server", "Balance"], rows, "");
  if (status) {
    status.textContent = "Copy the Myfxbook ID into config.json as myfxbook_account_id";
  }
}

async function loadBalance() {
  try {
    const res = await fetch("/api/balance-sheet");
    const data = await res.json();
    const connected = data.mt5_connected || data.sync_source === "myfxbook";
    setMt5Status(data.mt5_connected, data.mt5_message, data.sync_source);
    if (data.myfxbook) renderMfbAccounts(data.myfxbook);
    if (data.setup) renderSetup(data.setup);
    if (data.balance_sheet) renderBalanceSheet(data);
  } catch (e) {
    setMt5Status(false, "Could not load — is the app running?");
  }
}

async function syncMt5() {
  const btn = $("btn-sync");
  if (btn) { btn.disabled = true; btn.textContent = "Syncing…"; }
  try {
    const res = await fetch("/api/mt5/sync", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      setMt5Status(false, data.error || "Sync failed");
      if (data.setup) renderSetup(data.setup);
      alert(data.error || "Could not sync from MT5");
      return;
    }
    setMt5Status(true, data.message, "mt5");
    renderBalanceSheet({ synced_at: data.synced_at, balance_sheet: data.balance_sheet });
  } catch {
    alert("Sync failed — open XM MT5, log in, enable Algo Trading");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Sync MT5"; }
  }
}

function mfbFormPayload() {
  return {
    email: ($("mfb-email")?.value || "").trim(),
    password: $("mfb-password")?.value || "",
    account_id: ($("mfb-account-id")?.value || "").trim(),
  };
}

async function loadMfbConfig() {
  try {
    const res = await fetch("/api/myfxbook/config");
    const data = await res.json();
    if ($("mfb-email") && data.email) $("mfb-email").value = data.email;
    if ($("mfb-account-id") && data.account_id) $("mfb-account-id").value = String(data.account_id);
    const status = $("mfb-status");
    if (status && data.config_path) {
      status.textContent = data.has_password
        ? `Credentials loaded from ${data.config_path}`
        : `No password saved yet — enter Myfxbook password below`;
    }
  } catch {
    /* ignore */
  }
}

async function saveMfbConfig(syncAfter = false) {
  const payload = mfbFormPayload();
  const status = $("mfb-status");
  if (!payload.email || !payload.password) {
    if (status) status.textContent = "Enter Myfxbook email and password first";
    return null;
  }
  const btn = syncAfter ? $("btn-sync-mfb-inline") : $("btn-save-mfb");
  if (btn) { btn.disabled = true; btn.textContent = syncAfter ? "Syncing…" : "Saving…"; }
  try {
    const res = await fetch("/api/myfxbook/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.ok) {
      if (status) status.textContent = data.error || "Save failed";
      return null;
    }
    if (status) status.textContent = data.message || "Saved";
    if (syncAfter) return syncMyfxbook(payload);
    return data;
  } catch {
    if (status) status.textContent = "Save failed";
    return null;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = syncAfter ? "Save & sync" : "Save credentials";
    }
  }
}

async function syncMyfxbook(inlinePayload) {
  const btn = $("btn-sync-mfb");
  if (btn) { btn.disabled = true; btn.textContent = "Syncing…"; }
  const payload = inlinePayload || mfbFormPayload();
  const hasInline = Boolean(payload.email && payload.password);
  try {
    const res = await fetch("/api/myfxbook/sync", {
      method: "POST",
      headers: hasInline ? { "Content-Type": "application/json" } : undefined,
      body: hasInline ? JSON.stringify(payload) : undefined,
    });
    const data = await res.json();
    if (!data.ok) {
      setMt5Status(false, data.error || "Myfxbook sync failed", "myfxbook");
      if (data.setup) renderSetup(data.setup);
      alert(data.error || "Could not sync from Myfxbook — check config.json");
      return;
    }
    setMt5Status(true, data.message, "myfxbook");
    renderBalanceSheet({ synced_at: data.synced_at, balance_sheet: data.balance_sheet });
  } catch {
    alert("Myfxbook sync failed — check email/password in config.json");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Sync Myfxbook"; }
  }
}

async function listMyfxbookAccounts() {
  const btn = $("btn-list-mfb");
  const status = $("mfb-status");
  const payload = mfbFormPayload();
  if (!payload.email || !payload.password) {
    if (status) status.textContent = "Enter email and password in the form first";
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = "Loading…"; }
  try {
    const res = await fetch("/api/myfxbook/accounts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    renderMfbAccounts(data);
    if (!data.ok && status) status.textContent = data.error || "Could not connect";
  } catch {
    if (status) status.textContent = "Request failed — is the app running?";
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "List Myfxbook accounts"; }
  }
}

async function importCsv() {
  const input = $("csv-file");
  const status = $("csv-import-status");
  const btn = $("btn-import-csv");
  const file = input?.files?.[0];
  if (!file) {
    if (status) status.textContent = "Choose a CSV file first";
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = "Importing…"; }
  try {
    const text = await file.text();
    const res = await fetch("/api/balance-sheet/import", {
      method: "POST",
      headers: { "Content-Type": "text/plain; charset=utf-8" },
      body: text,
    });
    const data = await res.json();
    if (!data.ok) {
      if (status) status.textContent = data.error || "Import failed";
      return;
    }
    if (status) status.textContent = data.message || "Imported";
    renderBalanceSheet({ synced_at: data.synced_at, balance_sheet: data.balance_sheet });
  } catch {
    if (status) status.textContent = "Import failed — check file format";
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Import CSV"; }
  }
}

$("btn-sync")?.addEventListener("click", syncMt5);
$("btn-sync-mfb")?.addEventListener("click", () => syncMyfxbook());
$("btn-sync-mfb-inline")?.addEventListener("click", () => saveMfbConfig(true));
$("btn-save-mfb")?.addEventListener("click", () => saveMfbConfig(false));
$("btn-list-mfb")?.addEventListener("click", listMyfxbookAccounts);
$("btn-import-csv")?.addEventListener("click", importCsv);
loadMfbConfig();
loadBalance();
setInterval(loadBalance, 60000);