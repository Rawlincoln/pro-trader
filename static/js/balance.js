const $ = (id) => document.getElementById(id);

let mfbConfigured = false;

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

  const meta = $("bs-account-meta");
  if (meta) {
    const parts = [];
    if (acc.name) parts.push(acc.name);
    if (acc.login) parts.push(`#${acc.login}`);
    if (acc.server) parts.push(acc.server);
    if (data.sync_source) parts.push(`via ${data.sync_source}`);
    if (data.import_source) parts.push(`ledger: ${data.import_source}`);
    meta.textContent = parts.length ? parts.join(" · ") : "Account snapshot from last sync";
  }

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
    line: { color: "#0781fe", width: 2 },
    fillcolor: "rgba(7,129,254,0.12)",
  }], {
    margin: { t: 10, r: 20, b: 40, l: 50 },
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#8b919c", family: "Inter, Segoe UI, sans-serif" },
    xaxis: { gridcolor: "#2a2d36" },
    yaxis: { gridcolor: "#2a2d36", title: "Cumulative P&L" },
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

function mfbPayload() {
  return {
    email: ($("mfb-email")?.value || "").trim(),
    password: $("mfb-password")?.value || "",
    account_id: ($("mfb-account-id")?.value || "").trim(),
  };
}

function applyMfbUi(data) {
  mfbConfigured = !!data.configured;
  if ($("mfb-email") && data.email) $("mfb-email").value = data.email;
  if ($("mfb-account-id") && data.account_id) $("mfb-account-id").value = String(data.account_id);
  if ($("mfb-password")) {
    $("mfb-password").value = "";
    $("mfb-password").placeholder = data.has_password
      ? "Saved — leave blank to keep"
      : "Myfxbook password (once only)";
  }
  const status = $("mfb-settings-status");
  const badge = $("mfb-summary-badge");
  const note = $("mfb-settings-note");
  const form = $("mfb-form");
  const saveBtn = $("btn-save-mfb");
  const changeBtn = $("btn-change-mfb");
  const settings = $("bs-settings");

  if (badge) {
    badge.textContent = data.configured
      ? (data.saved_permanently ? "· locked in" : "· ready")
      : "· setup needed";
    badge.className = "bs-mfb-badge " + (data.configured ? "ok" : "need");
  }
  if (status && data.configured) {
    status.textContent = data.status_label || "Credentials saved — no re-entry needed";
    status.className = "bs-import-status saved";
  }
  if (note) {
    note.textContent = data.configured
      ? (data.from_env
        ? "Credentials locked in on the cloud server. Auto-sync runs without you typing them again."
        : "Credentials saved. Auto-sync runs without re-entry. Only open this if you change your Myfxbook password.")
      : (data.hint || "Enter once → Save. You will not be asked again.");
  }
  // Hide the form when already permanent — user clicks Change to edit
  if (data.configured && data.saved_permanently) {
    if (form) form.hidden = true;
    if (saveBtn) saveBtn.hidden = true;
    if (changeBtn) changeBtn.hidden = false;
    if (settings) settings.removeAttribute("open");
  } else if (!data.configured) {
    if (form) form.hidden = false;
    if (saveBtn) saveBtn.hidden = false;
    if (changeBtn) changeBtn.hidden = true;
    if (settings) settings.setAttribute("open", "");
  }
}

async function loadMfbConfig() {
  try {
    const res = await fetch("/api/myfxbook/config");
    const data = await res.json();
    applyMfbUi(data);
  } catch {
    /* ignore */
  }
}

async function saveMfbConfig() {
  const status = $("mfb-settings-status");
  const payload = mfbPayload();
  if (!payload.email) {
    if (status) status.textContent = "Email required";
    return false;
  }
  const btn = $("btn-save-mfb");
  if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
  try {
    const res = await fetch("/api/myfxbook/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!data.ok) {
      if (status) {
        status.textContent = data.error || "Save failed";
        status.className = "bs-import-status error";
      }
      return false;
    }
    if (data.config) applyMfbUi(data.config);
    if (status) {
      status.textContent = data.message || "Saved permanently — no re-entry needed";
      status.className = "bs-import-status saved";
    }
    await syncMyfxbook({ silent: true });
    return true;
  } catch {
    if (status) status.textContent = "Save failed";
    return false;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Save once"; }
  }
}

async function loadBalance() {
  try {
    const res = await fetch("/api/balance-sheet");
    const data = await res.json();
    setSyncStatus(data.mt5_connected || data.sync_source === "myfxbook", data.mt5_message, data.sync_source);
    if (data.balance_sheet) renderBalanceSheet(data);
  } catch {
    setSyncStatus(false, "Could not load — is the app running?");
  }
}

async function syncMyfxbook(opts = {}) {
  const silent = opts.silent === true;
  const btn = $("btn-sync-mfb");
  if (btn) { btn.disabled = true; btn.textContent = "Syncing…"; }
  try {
    const res = await fetch("/api/myfxbook/sync", { method: "POST" });
    const data = await res.json();
    if (!data.ok) {
      setSyncStatus(false, data.error || "Sync failed", "myfxbook");
      const status = $("mfb-settings-status");
      // Only force open settings if credentials are missing — not on every transient error
      const needsSetup = /email|password|credential|not configured|login|wrong/i.test(
        String(data.error || "")
      );
      if (status && needsSetup) {
        status.textContent = data.error || "Sync failed — check connection settings";
        status.className = "bs-import-status error";
      }
      if (!silent && needsSetup && !mfbConfigured) {
        $("bs-settings")?.setAttribute("open", "");
      }
      return false;
    }
    setSyncStatus(true, "Synced", "myfxbook");
    renderBalanceSheet({ synced_at: data.synced_at, balance_sheet: data.balance_sheet });
    return true;
  } catch {
    setSyncStatus(false, "Sync failed", "myfxbook");
    return false;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Sync"; }
  }
}

async function init() {
  await loadMfbConfig();
  await loadBalance();
  // Always pull latest from Myfxbook when balance page opens
  if (mfbConfigured) await syncMyfxbook({ silent: true });
}

$("btn-sync-mfb")?.addEventListener("click", () => syncMyfxbook());
$("btn-save-mfb")?.addEventListener("click", saveMfbConfig);
$("btn-change-mfb")?.addEventListener("click", () => {
  const form = $("mfb-form");
  const saveBtn = $("btn-save-mfb");
  if (form) form.hidden = false;
  if (saveBtn) saveBtn.hidden = false;
  $("btn-change-mfb").hidden = true;
  $("bs-settings")?.setAttribute("open", "");
  if ($("mfb-password")) $("mfb-password").focus();
});
init();
// Keep UI fresh: re-load sheet every 30s; full Myfxbook sync every 2 min
setInterval(loadBalance, 30000);
setInterval(() => {
  if (mfbConfigured) syncMyfxbook({ silent: true });
}, 120000);