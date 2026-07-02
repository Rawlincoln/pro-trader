/** Real-time trade alerts — all symbols (BUY/SELL/ENTRY/EXIT). */
const TradeAlerts = (() => {
  const $ = (id) => document.getElementById(id);
  const feed = [];
  const MAX_FEED = 30;

  function toast(msg) {
    const banner = $("trade-alert-banner");
    if (!banner) return;
    banner.className = "trade-alert-banner show";
    banner.textContent = msg;
    setTimeout(() => banner.classList.add("hidden"), 6000);
  }

  function typeLabel(type) {
    const map = {
      buy: "BUY",
      sell: "SELL",
      entry: "ENTRY",
      exit: "EXIT",
      exit_partial: "PARTIAL EXIT",
    };
    return map[type] || type?.toUpperCase() || "ALERT";
  }

  function typeClass(type) {
    if (type === "buy" || type === "entry") return "buy";
    if (type === "sell") return "sell";
    if (type === "exit" || type === "exit_partial") return "exit";
    return "wait";
  }

  function renderFeed() {
    const el = $("trade-alert-feed");
    if (!el) return;
    if (!feed.length) {
      el.innerHTML = "<p class='ta-empty'>No trade alerts yet — server is monitoring all symbols…</p>";
      return;
    }
    el.innerHTML = feed.map((a) => {
      const t = a.timestamp ? new Date(a.timestamp).toLocaleTimeString() : "";
      const cls = typeClass(a.type);
      const route = a.asset_route || "/";
      return `<div class="trade-alert-item ${cls} ${a.urgency === "immediate" ? "urgent" : ""}">
        <div class="ta-item-head">
          <span class="ta-type">${typeLabel(a.type)}</span>
          <a href="${route}" class="ta-asset">${a.asset_name || a.asset_id}</a>
          <span class="ta-time">${t}</span>
        </div>
        <div class="ta-item-msg">${a.message || ""}</div>
        ${a.entry ? `<div class="ta-levels">Entry ${a.entry} · SL ${a.stop_loss || "—"} · TP1 ${a.take_profit_1 || "—"}</div>` : ""}
      </div>`;
    }).join("");
  }

  function pushAlert(alert) {
    feed.unshift(alert);
    if (feed.length > MAX_FEED) feed.length = MAX_FEED;
    renderFeed();
  }

  function showBrowserAlert(alert) {
    if (!$("taBrowser")?.checked) return;
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const title = `${typeLabel(alert.type)} · ${alert.asset_name || alert.asset_id}`;
    new Notification(title, {
      body: alert.message || "",
      tag: alert.id || `${alert.asset_id}-${alert.type}`,
      requireInteraction: alert.urgency === "immediate",
    });
  }

  function playSound(urgent) {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = urgent ? 880 : 520;
      gain.gain.value = 0.12;
      osc.start();
      osc.stop(ctx.currentTime + (urgent ? 0.35 : 0.2));
      if (urgent) {
        setTimeout(() => {
          const o2 = ctx.createOscillator();
          const g2 = ctx.createGain();
          o2.connect(g2);
          g2.connect(ctx.destination);
          o2.frequency.value = 1100;
          g2.gain.value = 0.1;
          o2.start();
          o2.stop(ctx.currentTime + 0.25);
        }, 200);
      }
    } catch (_) {}
  }

  function onTradeAlert(alert) {
    if (!alert || !$("taEnabled")?.checked) return;
    const symKey = { eurusd: "taEurusd", gold: "taGold", bitcoin: "taBitcoin" }[alert.asset_id];
    if (symKey && !$(symKey)?.checked) return;
    const typeMap = {
      buy: "taBuy", sell: "taSell", entry: "taEntry",
      exit: "taExit", exit_partial: "taExit",
    };
    const typeEl = $(typeMap[alert.type] || "");
    if (typeEl && !typeEl.checked) return;

    pushAlert(alert);
    toast(`🔔 ${typeLabel(alert.type)} ${alert.asset_name}: ${alert.message}`);
    showBrowserAlert(alert);
    playSound(alert.urgency === "immediate");
  }

  function updateStatus(status) {
    const el = $("trade-alerts-status");
    if (!el || !status) return;
    const syms = (status.symbols_monitored || []).join(", ") || "—";
    if (status.server_push_ready) {
      el.textContent = `24/7 ACTIVE · ${syms} · Telegram ON`;
      el.className = "trade-alerts-status ready";
    } else if (status.enabled && status.scanner_running) {
      el.textContent = `Monitoring ${syms} · browser alerts`;
      el.className = "trade-alerts-status on";
    } else {
      el.textContent = "Alerts paused";
      el.className = "trade-alerts-status off";
    }
  }

  function applyConfig(cfg) {
    if (!cfg) return;
    if ($("taEnabled")) $("taEnabled").checked = cfg.enabled !== false;
    if ($("taBrowser")) $("taBrowser").checked = cfg.browser_alerts !== false;
    if ($("taBuy")) $("taBuy").checked = cfg.alert_buy !== false;
    if ($("taSell")) $("taSell").checked = cfg.alert_sell !== false;
    if ($("taEntry")) $("taEntry").checked = cfg.alert_entry !== false;
    if ($("taExit")) $("taExit").checked = cfg.alert_exit !== false;
    if ($("taTelegram")) $("taTelegram").checked = !!cfg.telegram_enabled;
    const syms = cfg.symbols || {};
    if ($("taEurusd")) $("taEurusd").checked = syms.eurusd !== false;
    if ($("taGold")) $("taGold").checked = syms.gold !== false;
    if ($("taBitcoin")) $("taBitcoin").checked = syms.bitcoin !== false;
    const locked = cfg.env_locked || {};
    if ($("taTgToken")) {
      $("taTgToken").placeholder = locked.telegram_token
        ? "Token via Render env"
        : (cfg.telegram_token_set ? "Token saved" : "Bot token");
      $("taTgToken").disabled = !!locked.telegram_token;
    }
    if ($("taTgChat")) {
      $("taTgChat").disabled = !!locked.telegram_chat;
      if (cfg.telegram_chat_id) $("taTgChat").value = cfg.telegram_chat_id;
    }
    const savedNote = $("taSavedNote");
    if (savedNote) savedNote.hidden = !cfg.saved_permanently;
    const warn = $("taChatWarn");
    if (warn) {
      if (cfg.needs_chat_id) {
        warn.hidden = false;
        warn.textContent = "Token saved but chat ID is missing — message your bot in Telegram, then Find chat ID.";
      } else if (cfg.needs_token) {
        warn.hidden = false;
        warn.textContent = "Paste your bot token from @BotFather to enable Telegram alerts.";
      } else if (cfg.telegram_enabled && !cfg.telegram_configured) {
        warn.hidden = false;
        warn.textContent = "Complete token + chat ID, then Save and Test.";
      } else {
        warn.hidden = true;
      }
    }
  }

  function buildSaveBody() {
    return {
      enabled: $("taEnabled")?.checked !== false,
      browser_alerts: $("taBrowser")?.checked !== false,
      alert_buy: $("taBuy")?.checked !== false,
      alert_sell: $("taSell")?.checked !== false,
      alert_entry: $("taEntry")?.checked !== false,
      alert_exit: $("taExit")?.checked !== false,
      telegram_enabled: $("taTelegram")?.checked || false,
      symbols: {
        eurusd: $("taEurusd")?.checked !== false,
        gold: $("taGold")?.checked !== false,
        bitcoin: $("taBitcoin")?.checked !== false,
      },
      telegram_bot_token: $("taTgToken")?.value.trim() || undefined,
      telegram_chat_id: $("taTgChat")?.value.trim() || undefined,
    };
  }

  async function loadConfig() {
    try {
      const [cfgRes, statusRes, histRes] = await Promise.all([
        fetch("/api/trade-alerts/config"),
        fetch("/api/trade-alerts/status"),
        fetch("/api/trade-alerts/history"),
      ]);
      applyConfig(await cfgRes.json());
      updateStatus(await statusRes.json());
      const hist = await histRes.json();
      (hist.alerts || []).slice(0, 15).reverse().forEach((a) => pushAlert(a));
    } catch {
      updateStatus({ enabled: true, scanner_running: true, symbols_monitored: ["eurusd", "gold", "bitcoin"] });
    }
  }

  async function saveConfig() {
    const btn = $("taSave");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch("/api/trade-alerts/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildSaveBody()),
      });
      const data = await res.json();
      if (!data.ok) throw new Error("Save failed");
      applyConfig(data.config);
      const status = await fetch("/api/trade-alerts/status").then((r) => r.json());
      updateStatus(status);
      toast(data.config?.saved_permanently ? "Saved permanently" : "Alert settings saved");
      if ($("taBrowser")?.checked) await requestNotifyPermission();
    } catch (e) {
      toast(e.message || "Could not save");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function discoverChat() {
    const btn = $("taDiscover");
    const token = $("taTgToken")?.value.trim();
    if (!token) {
      toast("Paste your bot token first, then click Find chat ID");
      return;
    }
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Looking…";
    }
    toast("Searching Telegram for your chat ID…");
    try {
      const res = await fetch("/api/trade-alerts/telegram/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildSaveBody()),
      });
      if (!res.ok) {
        throw new Error(
          res.status === 404
            ? "Trade alerts API not found — restart the Pro Trader app, then hard-refresh (Ctrl+F5)"
            : `Server returned ${res.status}`,
        );
      }
      const data = await res.json();
      const box = $("taChatPick");
      if (!data.ok || !data.chats?.length) {
        if (box) box.hidden = true;
        toast(data.error || "Message your bot in Telegram first, then try again");
        return;
      }
      if (box) {
        box.hidden = false;
        box.innerHTML = data.chats.map((c) => `
          <button type="button" class="ta-chat-btn" data-chat="${c.chat_id}">
            ${c.name || c.title || c.username || "Chat"} · ${c.chat_id}
          </button>
        `).join("");
        box.querySelectorAll(".ta-chat-btn").forEach((b) => {
          b.onclick = async () => {
            if ($("taTgChat")) $("taTgChat").value = b.dataset.chat;
            if ($("taTelegram")) $("taTelegram").checked = true;
            await saveConfig();
          };
        });
      }
      if (data.chats.length === 1 && $("taTgChat")) {
        $("taTgChat").value = data.chats[0].chat_id;
      }
      toast(`Found ${data.chats.length} chat(s) — click one, then Save → Test`);
    } catch (err) {
      toast(err?.message || "Could not reach server — restart the app and hard-refresh (Ctrl+F5)");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Find chat ID";
      }
    }
  }

  async function testTelegram() {
    const btn = $("taTest");
    if (btn) btn.disabled = true;
    try {
      if (!$("taTelegram")?.checked) {
        if ($("taTelegram")) $("taTelegram").checked = true;
      }
      const body = buildSaveBody();
      body.telegram_enabled = true;
      const saveRes = await fetch("/api/trade-alerts/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const saveData = await saveRes.json();
      if (saveData.config) applyConfig(saveData.config);

      const res = await fetch("/api/trade-alerts/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data.ok) {
        toast("Test sent — check your Telegram!");
        const status = await fetch("/api/trade-alerts/status").then((r) => r.json());
        updateStatus(status);
      } else {
        toast(data.error || "Test failed");
      }
    } catch {
      toast("Test request failed — is the server running?");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function requestNotifyPermission() {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default") {
      await Notification.requestPermission();
    }
  }

  function bindSocket(socket) {
    if (!socket) return;
    socket.on("trade_alert", onTradeAlert);
  }

  let bound = false;

  function bind() {
    if (bound) return;
    const panel = $("trade-alerts-panel");
    if (!panel) return;
    bound = true;

    panel.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      e.preventDefault();
      if (btn.id === "taSave") saveConfig();
      else if (btn.id === "taTest") testTelegram();
      else if (btn.id === "taDiscover") discoverChat();
    });

    $("taBrowser")?.addEventListener("change", requestNotifyPermission);
    ["taEnabled", "taTelegram", "taBuy", "taSell", "taEntry", "taExit", "taEurusd", "taGold", "taBitcoin"].forEach((id) => {
      $(id)?.addEventListener("change", () => saveConfig());
    });
  }

  let started = false;

  function init(socket) {
    bind();
    if (!started) {
      started = true;
      loadConfig().then(requestNotifyPermission);
      setInterval(() => {
        fetch("/api/trade-alerts/status").then((r) => r.json()).then(updateStatus).catch(() => {});
      }, 60000);
    }
    bindSocket(socket || window.__proTraderSocket || null);
  }

  function boot() {
    init(window.__proTraderSocket || null);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  return { init, onTradeAlert, requestNotifyPermission, discoverChat };
})();