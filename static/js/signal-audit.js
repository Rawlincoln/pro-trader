/** Signal accuracy journal + daily audit UI */
const SignalAudit = (() => {
  const $ = (id) => document.getElementById(id);

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function statusClass(st) {
    if (st === "win") return "ok";
    if (st === "loss") return "bad";
    if (st === "open") return "open";
    return "mute";
  }

  function rowHtml(item) {
    const st = item.status || "open";
    const t = item.ts ? new Date(item.ts).toLocaleString() : "";
    return `<div class="sa-item ${statusClass(st)}">
      <div class="sa-item-head">
        <span class="sa-st">${esc(st).toUpperCase()}</span>
        <span class="sa-asset">${esc(item.asset || item.asset_name || "")}</span>
        <span class="sa-sig">${esc(item.signal || "")}</span>
        <span class="sa-meta">${esc(item.confidence ?? "")}% · ${esc(item.grade || "—")}</span>
      </div>
      <div class="sa-item-sub">${esc(item.reason || item.source || "")} ${t ? "· " + esc(t) : ""}</div>
    </div>`;
  }

  function list(el, items, empty) {
    if (!el) return;
    if (!items?.length) {
      el.innerHTML = `<p class="ta-empty">${empty}</p>`;
      return;
    }
    el.innerHTML = items.map(rowHtml).join("");
  }

  async function loadStatus() {
    try {
      const [status, daily] = await Promise.all([
        fetch("/api/signal-audit").then((r) => r.json()),
        fetch("/api/signal-audit/daily").then((r) => r.json()),
      ]);
      if ($("sa-winrate")) {
        $("sa-winrate").textContent =
          status.overall_win_rate != null ? `${status.overall_win_rate}%` : "—";
      }
      if ($("sa-closed")) $("sa-closed").textContent = String(status.closed ?? "—");
      if ($("sa-open")) $("sa-open").textContent = String(status.open ?? "—");

      const cal = status.calibration || {};
      if ($("sa-calib")) {
        $("sa-calib").textContent = cal.ready
          ? cal.note || `Calibration active · conf×${cal.conf_scale}`
          : cal.note || "Collecting samples — calibration starts after 5 closed signals";
      }

      list($("sa-accurate"), daily.accurate || [], "No resolved wins today yet");
      list($("sa-missed"), daily.inaccurate || [], "No resolved losses today yet");
      list($("sa-recent"), status.recent || [], "No signals logged yet — wait for BUY/SELL");

      const learn = daily.learning || {};
      const strong = (learn.strong_components || [])
        .map((c) => `${c.component}×${c.weight}`)
        .join(", ");
      const weak = (learn.weak_components || [])
        .map((c) => `${c.component}×${c.weight}`)
        .join(", ");
      if ($("sa-learn")) {
        const parts = [];
        if (learn.updates) parts.push(`Learning updates: ${learn.updates}`);
        if (strong) parts.push(`Strong: ${strong}`);
        if (weak) parts.push(`Weakened: ${weak}`);
        $("sa-learn").textContent = parts.join(" · ") || "Weights stay at 1.0 until outcomes close";
      }
    } catch (e) {
      if ($("sa-calib")) $("sa-calib").textContent = "Could not load audit";
    }
  }

  async function runNow() {
    const btn = $("sa-run");
    const st = $("sa-run-status");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Running…";
    }
    if (st) st.textContent = "";
    try {
      const res = await fetch("/api/signal-audit/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ telegram: true }),
      });
      const data = await res.json();
      if (st) {
        st.textContent = data.ok
          ? `Done — closed ${data.closed_now || 0} · Telegram ${data.telegram_sent ? "sent" : "skipped/already"}`
          : "Run failed";
      }
      await loadStatus();
    } catch {
      if (st) st.textContent = "Run failed";
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Run audit now + Telegram";
      }
    }
  }

  function init() {
    if (!$("signal-audit-panel")) return;
    $("sa-run")?.addEventListener("click", runNow);
    $("sa-refresh")?.addEventListener("click", loadStatus);
    loadStatus();
    setInterval(loadStatus, 120000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  return { loadStatus, runNow };
})();
