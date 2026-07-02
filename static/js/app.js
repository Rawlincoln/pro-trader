const ASSET = window.ASSET || { id: "eurusd", decimals: 5, chartTickFormat: ".5f", showAgent: true };
const socket = typeof io !== "undefined"
  ? io({ query: { asset: ASSET.id } })
  : null;
window.__proTraderSocket = socket;
let lastData = null;
const chartRenderState = {};

if (socket) {
  socket.on("connect", () => {
    document.getElementById("connection-status").className = "status-dot online";
  });

  socket.on("disconnect", () => {
    document.getElementById("connection-status").className = "status-dot offline";
  });
}

// Poll fallback for cloud hosting when WebSocket is unavailable
setInterval(() => {
  if (socket?.connected) return;
  fetch(`/api/analysis/${ASSET.id}`)
    .then(r => r.json())
    .then(data => { if (!data.error) renderDashboard(data); })
    .catch(() => {});
}, 45000);

socket?.on("market_update", (data) => {
  if (data.asset_id && data.asset_id !== ASSET.id) return;
  if (data.error) {
    document.getElementById("signal-summary").textContent = "Error: " + data.error;
    return;
  }
  lastData = data;
  renderDashboard(data);
});

socket?.on("news_alert", (alert) => {
  showNewsAlertPopup(alert);
  if (Notification.permission === "granted") {
    new Notification(`NEWS ${alert.signal}: ${alert.event?.slice(0, 60)}`, {
      body: alert.message,
      tag: alert.id,
    });
  }
  playAlertSound(alert.urgency === "immediate");
});

document.getElementById("refresh-btn").addEventListener("click", async () => {
  const btn = document.getElementById("refresh-btn");
  btn.textContent = "Refreshing...";
  btn.disabled = true;
  try {
    const res = await fetch(`/api/refresh/${ASSET.id}`);
    const data = await res.json();
    if (!data.error) renderDashboard(data);
  } finally {
    btn.textContent = "Refresh Now";
    btn.disabled = false;
  }
});

function fmtPrice(v, decimals) {
  if (v == null) return "—";
  return Number(v).toFixed(decimals ?? ASSET.decimals);
}

function renderDashboard(data) {
  const decimals = data.decimals ?? ASSET.decimals;
  renderQuote(data.quote, decimals);
  renderSignal(data);
  renderNewsTrading(data);
  renderTradePlan(data.trade_plan, data.exit_check, decimals);
  const tickFmt = data.chart_tick_format || ASSET.chartTickFormat;
  const plan = data.trade_plan;
  renderChart("chart-1h", data.charts["1h"], plan, tickFmt);
  renderChart("chart-4h", data.charts["4h"], plan, tickFmt);
  if (ChartTools.isFullscreen("chart-1h")) {
    renderChart("chart-fullscreen-plot", data.charts["1h"], plan, tickFmt);
  } else if (ChartTools.isFullscreen("chart-4h")) {
    renderChart("chart-fullscreen-plot", data.charts["4h"], plan, tickFmt);
  }
  renderAnalysis("analysis-1h", data.analysis_1h, decimals);
  renderAnalysis("analysis-4h", data.analysis_4h, decimals);
  renderNews(data.news);
  renderCalendar(data.calendar, data.calendar_risk);
  renderFxbookStats(data.fxbook_stats, data.calendar_risk, data.news_sentiment);
  if (ASSET.id === "bitcoin") renderAttentionLiquidity(data.attention_liquidity, data);

  const ts = new Date(data.updated_at * 1000).toLocaleTimeString();
  document.getElementById("last-update").textContent = "Updated " + ts;
}

function renderQuote(quote, decimals) {
  document.getElementById("live-price").textContent = fmtPrice(quote.price, decimals);
  const changeEl = document.getElementById("price-change");
  if (quote.change !== undefined) {
    const sign = quote.change >= 0 ? "+" : "";
    changeEl.textContent = `${sign}${fmtPrice(quote.change, decimals)} (${sign}${quote.change_pct?.toFixed(2)}%)`;
    changeEl.className = "change " + (quote.change >= 0 ? "up" : "down");
  }
}

function renderSignal(data) {
  const signal = data.signal || "WAIT";
  document.getElementById("signal-panel").className = "signal-panel card " + signal.toLowerCase();
  const badge = document.getElementById("signal-badge");
  badge.className = "signal-badge " + signal.toLowerCase();
  badge.textContent = signal;

  const conf = data.confidence || 0;
  document.getElementById("confidence-fill").style.width = conf + "%";
  document.getElementById("confidence-text").textContent = conf.toFixed(1) + "%";

  let summary = `${signal} signal with ${conf.toFixed(0)}% confidence`;
  if (data.signal_source === "news_release") summary += " [NEWS RELEASE OVERRIDE]";
  else if (data.signal_source === "news") summary += " [NEWS-DRIVEN]";
  else if (data.signal_source === "attention") summary += " [ATTENTION LIQUIDITY]";
  else if (data.signal_source === "attention+technical") summary += " [TECH + ATTENTION]";
  else if (data.signal_source === "attention+news") summary += " [ATTENTION + NEWS]";
  summary += `. Confluence: ${data.confluence ?? "—"}/9. `;
  summary += `4H trend: ${formatTrend(data.primary_trend)}. `;
  if (data.timeframes_aligned) summary += "1H and 4H aligned. ";

  const vol = data.analysis_1h?.indicators;
  if (vol?.volume_ratio) summary += `Volume ${vol.volume_ratio}x avg. `;
  if (data.fundamental_notes?.length) summary += data.fundamental_notes.join(" ");

  document.getElementById("signal-summary").textContent = summary;
  document.getElementById("combined-score").textContent = data.adjusted_score ?? data.combined_score ?? "—";
  document.getElementById("confluence").textContent = data.confluence != null ? data.confluence + "/9" : "—";
  document.getElementById("trend-4h").textContent = formatTrend(data.primary_trend);
  document.getElementById("tf-aligned").textContent = data.timeframes_aligned ? "Yes" : "No";
  const ns = data.news_sentiment || {};
  const sentText = ns.overall
    ? `${ns.overall} (${ns.bullish_pct || 0}% bull / ${ns.bearish_pct || 0}% bear)`
    : "—";
  document.getElementById("news-sentiment").textContent = sentText;

  const volEl = document.getElementById("volume-signal");
  if (volEl && vol) {
    volEl.textContent = `${vol.volume_signal || "—"} (${vol.volume_ratio || "?"}x)`;
  }
}

function formatTrend(t) {
  if (!t) return "—";
  return t.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function renderTradePlan(plan, exitCheck, decimals) {
  const fmt = v => fmtPrice(v, decimals);
  document.getElementById("tp-entry").textContent = fmt(plan.entry);
  document.getElementById("tp-sl").textContent = fmt(plan.stop_loss);
  document.getElementById("tp-tp1").textContent = fmt(plan.take_profit_1);
  document.getElementById("tp-tp2").textContent = fmt(plan.take_profit_2);
  document.getElementById("tp-tp3").textContent = fmt(plan.take_profit_3);
  document.getElementById("tp-rr").textContent = plan.risk_reward ? "1:" + plan.risk_reward : "—";
  document.getElementById("entry-trigger").textContent = plan.entry_trigger || "—";
  document.getElementById("exit-trigger").textContent = plan.exit_trigger || "—";

  const exitAlert = document.getElementById("exit-alert");
  if (exitCheck?.reason) {
    exitAlert.className = "exit-alert " + (exitCheck.urgency === "immediate" ? "immediate" : "consider");
    exitAlert.textContent = (exitCheck.should_exit ? "EXIT NOW: " : "WATCH: ") + exitCheck.reason;
  } else {
    exitAlert.className = "exit-alert hidden";
  }

  document.getElementById("instructions").innerHTML =
    (plan.instructions || []).map(i => `<li>${i}</li>`).join("");
}

const PATTERN_COLORS = {
  bullish: "#10b981",
  bearish: "#ef4444",
  neutral: "#f59e0b",
};

function patternArrow(bias) {
  if (bias === "bullish") return "▲";
  if (bias === "bearish") return "▼";
  return "◆";
}

function patternShortLabel(name) {
  return name.replace(/^(Bullish|Bearish)\s+/, "");
}

function buildPatternMarkers(patterns, candles) {
  if (!patterns?.length || !candles?.length) return { annotations: [], markerTrace: null };

  const timeSet = new Set(candles.map(c => c.time));
  const visible = patterns.filter(p => p?.time && timeSet.has(p.time)).slice(0, 1);

  const annotations = visible.map(p => {
    const color = PATTERN_COLORS[p.bias] || PATTERN_COLORS.neutral;
    const below = p.marker_position === "below";
    return {
      x: p.time,
      y: p.price,
      xref: "x",
      yref: "y",
      text: `<b>${patternShortLabel(p.name)}</b> ${patternArrow(p.bias)}`,
      showarrow: true,
      arrowhead: 2,
      arrowsize: 0.9,
      arrowwidth: 1.5,
      arrowcolor: color,
      ax: 0,
      ay: below ? 34 : -34,
      font: { color, size: 9 },
      bgcolor: "rgba(17,24,39,0.9)",
      bordercolor: color,
      borderwidth: 1,
      borderpad: 3,
    };
  });

  const markerTrace = visible.length ? {
    type: "scatter",
    mode: "markers",
    x: visible.map(p => p.time),
    y: visible.map(p => p.price),
    name: "Patterns",
    marker: {
      symbol: visible.map(p => p.bias === "bullish" ? "triangle-up" : p.bias === "bearish" ? "triangle-down" : "diamond"),
      size: 11,
      color: visible.map(p => PATTERN_COLORS[p.bias] || PATTERN_COLORS.neutral),
      line: { color: "#111827", width: 1 },
    },
    hovertext: visible.map(p =>
      `${p.name}\n${p.bias.toUpperCase()} · ${p.type}\n${p.description}`
    ),
    hoverinfo: "text",
    xaxis: "x",
    yaxis: "y",
  } : null;

  return { annotations, markerTrace };
}

function renderPatternLegend(legendId, patterns) {
  const el = document.getElementById(legendId);
  if (!el) return;

  if (!patterns?.length) {
    el.innerHTML = '<span class="pattern-legend-empty">No patterns on chart</span>';
    return;
  }

  const primary = patterns[0];
  if (!primary) {
    el.innerHTML = '<span class="pattern-legend-empty">No active pattern</span>';
    return;
  }
  el.innerHTML = [primary].map(p => {
    const vol = p.volume_confirmed ? '<span class="pattern-vol">VOL</span>' : "";
    return `<span class="pattern-legend-item ${p.bias}" title="${p.description}">
      <span class="pattern-legend-arrow">${patternArrow(p.bias)}</span>
      <span class="pattern-legend-name">${p.name}</span>
      <span class="pattern-legend-type">${p.type}</span>${vol}
    </span>`;
  }).join("");
}

function rerenderChartBySource(sourceChartId) {
  const st = chartRenderState[sourceChartId];
  const tickFmt = (lastData?.chart_tick_format) || ASSET.chartTickFormat;
  const plan = lastData?.trade_plan;

  if (st) {
    renderChart(sourceChartId, st.chartData, st.tradePlan, st.tickFormat);
    if (ChartTools.isFullscreen(sourceChartId)) {
      renderChart("chart-fullscreen-plot", st.chartData, st.tradePlan, st.tickFormat);
    }
    return;
  }

  if (!lastData?.charts) return;
  const tf = sourceChartId === "chart-1h" ? "1h" : "4h";
  renderChart(sourceChartId, lastData.charts[tf], plan, tickFmt);
  if (ChartTools.isFullscreen(sourceChartId)) {
    renderChart("chart-fullscreen-plot", lastData.charts[tf], plan, tickFmt);
  }
}

function renderChart(containerId, chartData, tradePlan, tickFormat) {
  const candles = chartData.candles;
  if (!candles?.length) return;

  const sourceKey = ChartTools.resolveDrawingKey(containerId);
  chartRenderState[sourceKey] = { chartData, tradePlan, tickFormat };

  const times = candles.map(c => c.time);
  const hasVolume = candles.some(c => c.volume != null);
  const { annotations, markerTrace } = buildPatternMarkers(chartData.patterns, candles);

  const traces = [{
    type: "candlestick",
    x: times,
    open: candles.map(c => c.open),
    high: candles.map(c => c.high),
    low: candles.map(c => c.low),
    close: candles.map(c => c.close),
    name: "Price",
    xaxis: "x",
    yaxis: "y",
    increasing: { line: { color: "#10b981" } },
    decreasing: { line: { color: "#ef4444" } },
  }];

  const ind = chartData.indicators;
  if (ind?.ema_20) {
    traces.push({
      type: "scatter", mode: "lines", x: ind.times, y: ind.ema_20,
      name: "EMA 20", line: { color: "#3b82f6", width: 1.5 }, xaxis: "x", yaxis: "y",
    });
  }
  if (ind?.ema_50) {
    traces.push({
      type: "scatter", mode: "lines", x: ind.times, y: ind.ema_50,
      name: "EMA 50", line: { color: "#f59e0b", width: 1.5 }, xaxis: "x", yaxis: "y",
    });
  }
  if (ind?.vwap?.length) {
    traces.push({
      type: "scatter", mode: "lines", x: ind.times, y: ind.vwap,
      name: "VWAP", line: { color: "#a78bfa", width: 1, dash: "dot" }, xaxis: "x", yaxis: "y",
    });
  }

  if (hasVolume) {
    const volColors = candles.map((c, i) =>
      i > 0 && c.close >= candles[i - 1].close ? "rgba(16,185,129,0.5)" : "rgba(239,68,68,0.5)"
    );
    traces.push({
      type: "bar", x: times, y: candles.map(c => c.volume),
      name: "Volume", marker: { color: volColors }, xaxis: "x", yaxis: "y2",
    });
  }

  if (markerTrace) traces.push(markerTrace);

  const shapes = [];
  const addHLine = (price, color, dash) => {
    if (price == null) return;
    shapes.push({
      type: "line", xref: "x", yref: "y",
      x0: times[0], x1: times[times.length - 1], y0: price, y1: price,
      line: { color, width: 1, dash },
    });
  };

  addHLine(tradePlan?.entry, "#22d3ee", "dot");
  addHLine(tradePlan?.stop_loss, "#ef4444", "dash");
  addHLine(tradePlan?.take_profit_2, "#10b981", "dash");

  const levels = chartData.levels || {};
  (levels.support || []).slice(-2).forEach(s => addHLine(s, "rgba(16,185,129,0.4)", "dot"));
  (levels.resistance || []).slice(0, 2).forEach(r => addHLine(r, "rgba(239,68,68,0.4)", "dot"));
  if (levels.fibonacci?.fib_618) addHLine(levels.fibonacci.fib_618, "rgba(168,85,247,0.5)", "dashdot");
  if (levels.fibonacci?.fib_382) addHLine(levels.fibonacci.fib_382, "rgba(168,85,247,0.35)", "dashdot");

  shapes.push(...ChartTools.getDrawShapes(containerId, times));

  const isFullscreen = containerId === "chart-fullscreen-plot";
  const layout = {
    paper_bgcolor: "#111827",
    plot_bgcolor: "#111827",
    font: { color: "#94a3b8", size: 11 },
    xaxis: { gridcolor: "#1e293b", rangeslider: { visible: false }, domain: hasVolume ? [0, 1] : [0, 1] },
    yaxis: { gridcolor: "#1e293b", tickformat: tickFormat, side: "right", domain: hasVolume ? [0.32, 1] : [0, 1] },
    margin: { l: 10, r: 60, t: isFullscreen ? 20 : 10, b: 30 },
    legend: { orientation: "h", y: 1.08, font: { size: 10 } },
    shapes,
    annotations,
    dragmode: ChartTools.getMode(sourceKey) === "select" ? "zoom" : false,
  };

  if (hasVolume) {
    layout.yaxis2 = {
      domain: [0, 0.22], gridcolor: "#1e293b", showticklabels: false,
    };
  }

  Plotly.react(containerId, traces, layout, {
    responsive: true,
    displayModeBar: isFullscreen,
    scrollZoom: true,
    modeBarButtonsToRemove: ["lasso2d", "select2d"],
  });

  ChartTools.bindPlotEvents(containerId);

  if (!isFullscreen) {
    const legendId = containerId === "chart-1h" ? "pattern-legend-1h" : "pattern-legend-4h";
    renderPatternLegend(legendId, chartData.patterns);
  }
}

function renderBreakdown(bd) {
  if (!bd) return "";
  const items = Object.entries(bd).map(([k, v]) => {
    const cls = v > 0 ? "pos" : v < 0 ? "neg" : "";
    const sign = v > 0 ? "+" : "";
    return `<span class="breakdown-tag ${cls}">${k}: ${sign}${v}</span>`;
  });
  return `<div class="breakdown-grid">${items.join("")}</div>`;
}

function renderAnalysis(containerId, analysis, decimals) {
  const ind = analysis.indicators || {};
  const levels = analysis.levels || {};
  const patterns = analysis.patterns || [];
  const bd = analysis.breakdown || {};
  const fmt = v => v != null ? Number(v).toFixed(decimals) : "—";

  let html = `
    <div class="ind-grid">
      <div class="ind-item"><span class="k">Bias / Score</span><span class="v">${analysis.bias} (${analysis.score})</span></div>
      <div class="ind-item"><span class="k">Confluence</span><span class="v">${analysis.confluence_count ?? "—"}/9</span></div>
      <div class="ind-item"><span class="k">Trend</span><span class="v">${formatTrend(analysis.trend)}</span></div>
      <div class="ind-item"><span class="k">ADX</span><span class="v">${ind.adx ?? "—"} (${formatTrend(ind.adx_signal)})</span></div>
      <div class="ind-item"><span class="k">RSI</span><span class="v">${ind.rsi ?? "—"} (${ind.rsi_signal})</span></div>
      <div class="ind-item"><span class="k">MACD</span><span class="v">${ind.macd_cross || "none"}</span></div>
      <div class="ind-item"><span class="k">CCI</span><span class="v">${ind.cci ?? "—"} (${ind.cci_signal})</span></div>
      <div class="ind-item"><span class="k">Williams %R</span><span class="v">${ind.williams_r ?? "—"}</span></div>
      <div class="ind-item"><span class="k">MFI</span><span class="v">${ind.mfi ?? "—"} (${ind.mfi_signal})</span></div>
      <div class="ind-item"><span class="k">Stoch K/D</span><span class="v">${ind.stoch_k ?? "—"} / ${ind.stoch_d ?? "—"}</span></div>
      <div class="ind-item"><span class="k">Ichimoku</span><span class="v">${ind.ichimoku_signal}</span></div>
      <div class="ind-item"><span class="k">EMA Cross</span><span class="v">${ind.ema_cross || "none"}</span></div>
      <div class="ind-item"><span class="k">Volume</span><span class="v">${ind.volume_ratio ?? "—"}x (${ind.volume_signal})</span></div>
      <div class="ind-item"><span class="k">OBV Trend</span><span class="v">${ind.obv_trend}</span></div>
      <div class="ind-item"><span class="k">VWAP</span><span class="v">${fmt(ind.vwap)}</span></div>
      <div class="ind-item"><span class="k">ATR</span><span class="v">${fmt(ind.atr)}</span></div>
    </div>
    ${renderBreakdown(bd)}
  `;

  const primary = analysis.primary_pattern || patterns[0];
  if (primary) {
    html += '<div class="patterns-section"><h3 class="patterns-heading">Active Pattern (Signal)</h3><div class="patterns-list">' +
      [primary].map(p => {
        const vol = p.volume_confirmed ? '<span class="pattern-vol">Volume confirmed</span>' : "";
        const biasLabel = p.bias === "bullish" ? "BULLISH" : p.bias === "bearish" ? "BEARISH" : "NEUTRAL";
        return `<div class="pattern-card ${p.bias}">
          <div class="pattern-card-header">
            <span class="pattern-bias-badge ${p.bias}">${patternArrow(p.bias)} ${biasLabel}</span>
            <span class="pattern-type-badge">${p.type}</span>
            <span class="pattern-strength">${p.strength}</span>
          </div>
          <div class="pattern-card-name">${p.name}</div>
          <div class="pattern-card-desc">${p.description}</div>${vol}
        </div>`;
      }).join("") + "</div></div>";
  } else {
    html += '<p class="patterns-empty">No pattern aligned with current signal on latest candle</p>';
  }

  html += '<div class="levels-section">';
  if (levels.nearest_support) {
    html += `<div class="level-row">Support: <strong>${fmt(levels.nearest_support)}</strong> <span class="strength-badge">${levels.support_strength || ""}</span></div>`;
  }
  if (levels.nearest_resistance) {
    html += `<div class="level-row">Resistance: <strong>${fmt(levels.nearest_resistance)}</strong> <span class="strength-badge">${levels.resistance_strength || ""}</span></div>`;
  }
  if (levels.fibonacci?.fib_382) {
    html += `<div class="level-row">Fib 38.2%: ${fmt(levels.fibonacci.fib_382)} · 61.8%: ${fmt(levels.fibonacci.fib_618)}</div>`;
  }
  if (levels.volume_nodes?.length) {
    html += `<div class="level-row">Vol nodes: ${levels.volume_nodes.slice(0, 3).map(n => fmt(n.price)).join(", ")}</div>`;
  }
  html += `<div class="level-row">Position: ${levels.price_position || "—"}</div></div>`;

  document.getElementById(containerId).innerHTML = html;
}

function renderNews(news) {
  const container = document.getElementById("news-list");
  if (!news?.length) {
    container.innerHTML = "<p style='color:var(--muted)'>No relevant news found</p>";
    return;
  }
  container.innerHTML = news.map(n => `
    <div class="news-item">
      <div class="news-title"><a href="${n.link}" target="_blank" rel="noopener">${n.title}</a></div>
      <div class="news-meta">
        ${n.source} · <span class="sentiment-${n.sentiment}">${n.sentiment}</span> · ${new Date(n.published).toLocaleString()}
      </div>
    </div>
  `).join("");
}

function renderFxbookStats(fx, calRisk, newsSent) {
  const crowdEl = document.getElementById("fxbook-crowd");
  const lsEl = document.getElementById("fxbook-long-short");
  const newsCountEl = document.getElementById("fxbook-news-count");
  const cal24El = document.getElementById("calendar-24h");
  const panel = document.getElementById("fxbook-panel");

  if (crowdEl) {
    crowdEl.textContent = fx?.crowd_bias
      ? `${fx.crowd_bias.toUpperCase()} (${fx.crowd_signal || "WAIT"})`
      : "—";
  }
  if (lsEl) {
    lsEl.textContent = fx?.long_pct != null
      ? `${fx.long_pct}% / ${fx.short_pct}%`
      : "—";
  }
  if (newsCountEl) {
    const mfb = newsSent?.myfxbook_count ?? fx?.news_count ?? 0;
    const total = newsSent?.total ?? 0;
    newsCountEl.textContent = `${mfb} MFB / ${total} total`;
  }
  if (cal24El) {
    cal24El.textContent = calRisk?.next_24h_count != null
      ? `${calRisk.next_24h_count} events (${calRisk.high_impact || 0} high)`
      : "—";
  }
  if (!panel) return;

  const lines = [];
  if (fx?.crowd_reason) lines.push(`<div class="fxbook-line"><strong>Crowd:</strong> ${fx.crowd_reason}</div>`);
  if (fx?.popularity_pct != null) {
    lines.push(`<div class="fxbook-line"><strong>Popularity:</strong> ${fx.popularity_pct}% of MyFXBook traders active on ${fx.symbol || "symbol"}</div>`);
  }
  if (fx?.total_positions) {
    lines.push(`<div class="fxbook-line"><strong>Open Interest:</strong> ${fx.total_positions.toLocaleString()} positions · ${fx.total_volume_lots?.toLocaleString()} lots</div>`);
  }
  if (calRisk) {
    lines.push(
      `<div class="fxbook-line"><strong>Calendar:</strong> ${calRisk.total_events || 0} events · ` +
      `${calRisk.high_impact || 0} high · ${calRisk.released_count || 0} released · ` +
      `beats ${calRisk.beats || 0} / misses ${calRisk.misses || 0}</div>`
    );
  }
  if (newsSent?.sources) {
    const src = Object.entries(newsSent.sources).map(([k, v]) => `${k}: ${v}`).join(" · ");
    lines.push(`<div class="fxbook-line"><strong>Sources:</strong> ${src}</div>`);
  }
  panel.innerHTML = lines.length ? lines.join("") : "";
}

function renderCalendar(events, risk) {
  const riskEl = document.getElementById("calendar-risk");
  if (risk) {
    riskEl.className = "calendar-risk " + (risk.risk_level || "low");
    let stats = "";
    if (risk.total_events != null) {
      stats = `<br><span style="font-size:0.8rem;color:var(--muted)">` +
        `${risk.total_events} events · ${risk.high_impact || 0} high · ${risk.next_24h_count || 0} in 24h · ` +
        `${risk.released_count || 0} released · beats ${risk.beats || 0} / misses ${risk.misses || 0}` +
        `</span>`;
    }
    riskEl.innerHTML = `<strong>Event Risk: ${(risk.risk_level || "low").toUpperCase()}</strong>` +
      (risk.warning ? `<br>${risk.warning}` : "") + stats;
  }

  const container = document.getElementById("calendar-list");
  if (!events?.length) {
    container.innerHTML = `<p style='color:var(--muted)'>${ASSET.calendarEmpty || "No upcoming events"}</p>`;
    return;
  }
  container.innerHTML = events.map(e => `
    <div class="cal-item">
      <span class="cal-impact ${e.impact}">${e.impact}</span>
      <div>
        <div>${e.title}</div>
        <div style="color:var(--muted);font-size:0.75rem">${e.currency} · ${e.date} ${e.time}
          ${e.forecast ? " · Fcst: " + e.forecast : ""}${e.previous ? " · Prev: " + e.previous : ""}
        </div>
      </div>
    </div>
  `).join("");
}

function renderAgent(state) {
  if (!ASSET.showAgent) return;
  const statusEl = document.getElementById("agent-status");
  if (!statusEl) return;

  statusEl.textContent = (state.status || "unknown").toUpperCase();
  const mode = state.dry_run ? "DRY RUN" : "LIVE";
  document.getElementById("agent-mode").textContent = state.connected === false ? "OFFLINE " + mode : mode;
  document.getElementById("agent-action").textContent = state.last_action || "—";
  const pos = state.open_positions || [];
  document.getElementById("agent-positions").textContent = pos.length ? pos.length + " open" : "None";

  const log = state.trade_log || [];
  document.getElementById("agent-log").innerHTML = log.length
    ? log.slice(0, 5).map(l =>
        `<div class="log-item">${l.time?.slice(11, 19) || ""} <strong>${l.event}</strong> ${l.direction || ""} ${l.reason || l.message || ""}</div>`
      ).join("")
    : "<div class='log-item'>No trades yet. Start agent with run_agent.bat</div>";
}

function fetchAgent() {
  if (!ASSET.showAgent) return;
  fetch("/api/agent").then(r => r.json()).then(renderAgent).catch(() => {});
}

// Load cached data quickly; live updates arrive via WebSocket.
fetch(`/api/analysis/${ASSET.id}`)
  .then(r => r.json())
  .then(data => {
    if (!data.error) renderDashboard(data);
    else if (!lastData) {
      document.getElementById("signal-summary").textContent =
        "Loading market data — first load may take a few seconds...";
    }
  })
  .catch(() => {
    if (!lastData) {
      document.getElementById("signal-summary").textContent =
        "Connecting — waiting for live data...";
    }
  });

function renderNewsTrading(data) {
  const nt = data.news_trading;
  if (!nt) return;

  const sig = nt.combined_signal || "WAIT";
  const badge = document.getElementById("news-signal-badge");
  if (badge) {
    badge.className = "news-signal-badge " + sig.toLowerCase();
    badge.textContent = sig;
  }

  document.getElementById("news-combined-signal").textContent = sig;
  document.getElementById("news-combined-conf").textContent =
    nt.combined_confidence != null ? nt.combined_confidence.toFixed(1) + "%" : "—";
  document.getElementById("signal-source").textContent =
    (data.signal_source || "technical").replace("_", " ").toUpperCase();
  document.getElementById("active-alert-count").textContent = (nt.active_alerts || []).length;

  const banner = document.getElementById("news-alert-banner");
  const immediate = (nt.active_alerts || []).find(a => a.urgency === "immediate");
  const pre = (nt.active_alerts || []).find(a => a.type === "pre_event" && a.window_minutes <= 15);
  if (immediate) {
    banner.className = "news-alert-banner immediate";
    banner.textContent = `🚨 LIVE: ${immediate.event} → ${immediate.signal} (${immediate.confidence?.toFixed(0)}%) — ${immediate.message}`;
  } else if (pre) {
    banner.className = "news-alert-banner pre";
    banner.textContent = `⏰ ${pre.minutes_until}min: ${pre.event} — ${pre.message}`;
  } else {
    banner.className = "news-alert-banner hidden";
  }

  const alertsEl = document.getElementById("active-alerts");
  const alerts = nt.active_alerts || [];
  alertsEl.innerHTML = alerts.length ? alerts.map(a => `
    <div class="alert-card ${a.type}">
      <span class="alert-signal ${(a.signal || "wait").toLowerCase()}">${a.signal || "WAIT"}</span>
      <div class="alert-body">
        <div>${a.event || a.message}</div>
        <div class="alert-meta">${a.type.replace("_", " ")} · ${a.urgency} · ${a.confidence?.toFixed(0) || "?"}% conf
          ${a.actual ? " · Actual: " + a.actual : ""}${a.forecast ? " · Fcst: " + a.forecast : ""}
          ${a.price_at_alert ? " · Price: " + a.price_at_alert : ""}
        </div>
      </div>
    </div>
  `).join("") : "<p style='color:var(--muted);font-size:0.85rem'>No active alerts — monitoring calendar & news</p>";

  const upcomingEl = document.getElementById("upcoming-events");
  const upcoming = nt.upcoming_events || [];
  upcomingEl.innerHTML = upcoming.length ? upcoming.map(e => {
    const pb = e.pre_bias || {};
    return `<div class="event-item">
      <span class="cal-impact ${e.impact}">${e.impact}</span>
      <strong>${e.title}</strong> (${e.currency})
      <span class="countdown">${e.minutes_until > 60 ? Math.round(e.minutes_until / 60) + "h" : Math.round(e.minutes_until) + "min"}</span>
      ${e.forecast ? `<br>Fcst: ${e.forecast} · Prev: ${e.previous || "—"}` : ""}
      ${pb.expected_signal ? `<br>Expected: <span class="released-signal ${pb.expected_signal.toLowerCase()}">${pb.expected_signal}</span> — ${pb.expected_reason || ""}` : ""}
    </div>`;
  }).join("") : "<p style='color:var(--muted)'>No upcoming events</p>";

  const releasedEl = document.getElementById("released-events");
  const released = nt.released_events || [];
  releasedEl.innerHTML = released.length ? released.map(e => {
    const ra = e.release_analysis || {};
    return `<div class="event-item">
      <strong>${e.title}</strong>
      <br>Actual: <b>${e.actual}</b> · Fcst: ${e.forecast || "—"} · Prev: ${e.previous || "—"}
      <br><span class="released-signal ${(ra.signal || "wait").toLowerCase()}">${ra.signal || "WAIT"}</span>
      ${ra.confidence ? ` (${ra.confidence.toFixed(0)}%)` : ""} — ${ra.reason || ""}
      ${ra.surprise ? `<br>Surprise: ${ra.surprise}% · Est. ${ra.pip_estimate || "?"} pip move` : ""}
    </div>`;
  }).join("") : "<p style='color:var(--muted)'>No releases yet</p>";

  const impactsEl = document.getElementById("price-impacts");
  const impacts = nt.price_impacts || [];
  impactsEl.innerHTML = impacts.length
    ? "<strong>Price Impact Since Alert:</strong>" + impacts.map(i => `
      <div class="impact-row">${i.event?.slice(0, 40)} →
        <span class="${i.direction}">${i.change > 0 ? "+" : ""}${i.change} (${i.change_pct}%)</span>
        [${i.signal}]
      </div>`).join("")
    : "";

  const histEl = document.getElementById("alert-history");
  const hist = nt.alert_history || [];
  histEl.innerHTML = hist.slice(0, 8).map(h =>
    `<div class="hist-item">${h.timestamp?.slice(11, 19) || ""} [${h.type}] ${h.signal || ""} ${h.event?.slice(0, 50) || h.message?.slice(0, 50) || ""}</div>`
  ).join("");
}

const ATTENTION_COMPONENT_LABELS = {
  buzz_volume: "News buzz",
  marketing_influencer: "Marketing / influencers",
  news_velocity: "Headline velocity",
  fear_distribution: "Fear / exit talk",
  coingecko_trending: "CoinGecko trending",
  community_sentiment: "Community sentiment",
};

function renderAttentionLiquidity(att, data) {
  const panel = document.getElementById("attention-panel");
  if (!panel) return;
  if (!att) {
    document.getElementById("attention-reason").textContent = "Attention data loading…";
    return;
  }

  const ali = att.index ?? 0;
  const phase = (att.phase || "NEUTRAL").toLowerCase();
  document.getElementById("ali-score").textContent = ali.toFixed(0);
  const phaseEl = document.getElementById("ali-phase");
  phaseEl.textContent = att.phase || "—";
  phaseEl.className = "attention-phase phase-" + phase;

  const sig = (att.signal || "WAIT").toLowerCase();
  const badge = document.getElementById("attention-signal-badge");
  badge.className = "attention-signal-badge " + sig;
  badge.textContent = att.signal || "WAIT";
  document.getElementById("attention-confidence").textContent =
    `${(att.confidence || 0).toFixed(0)}% attention bias`;
  document.getElementById("attention-reason").textContent = att.reason || "—";

  document.getElementById("ali-gauge-fill").style.width = Math.min(100, ali) + "%";

  const comps = att.components || {};
  document.getElementById("attention-components").innerHTML = Object.entries(comps).map(([k, v]) => `
    <div class="attention-comp">
      <span class="comp-label">${ATTENTION_COMPONENT_LABELS[k] || k}</span>
      <div class="comp-bar"><div class="comp-bar-fill" style="width:${Math.min(100, v)}%"></div></div>
      <span class="comp-val">${Number(v).toFixed(0)}</span>
    </div>
  `).join("");

  const mom = att.momentum ?? 0;
  document.getElementById("ali-momentum").textContent =
    `${mom >= 0 ? "+" : ""}${mom.toFixed(1)} ${mom > 5 ? "↑ heating" : mom < -5 ? "↓ cooling" : "→ flat"}`;
  const counts = att.counts || {};
  document.getElementById("ali-buzz-1h").textContent = counts.headlines_1h ?? "—";
  document.getElementById("ali-recruit-hits").textContent = counts.recruitment_hits ?? "—";
  document.getElementById("ali-fear-hits").textContent = counts.fear_hits ?? "—";

  const gecko = att.coingecko || {};
  const rank = gecko.trending_rank;
  document.getElementById("ali-trending").textContent =
    rank ? `#${rank} trending` : (gecko.trending_score ? "On radar" : "Not trending");

  const div = att.divergence || {};
  document.getElementById("ali-divergence").textContent = div.label || "—";

  const drivers = att.drivers || [];
  document.getElementById("attention-drivers").innerHTML = drivers.length
    ? "<strong style='font-size:0.8rem;color:var(--muted)'>Top narrative drivers</strong>"
      + drivers.map(d => `
        <div class="attention-driver">
          <div class="driver-title">${d.title}</div>
          <div class="driver-meta">${d.source || ""} · net ${d.net > 0 ? "+" : ""}${d.net}</div>
          ${d.tags?.length ? `<div class="driver-tags">${d.tags.map(t => `<span class="driver-tag">${t}</span>`).join("")}</div>` : ""}
        </div>
      `).join("")
    : "<p style='color:var(--muted);font-size:0.82rem'>No strong recruitment/fear headlines in current scan</p>";

  const note = document.getElementById("attention-liquidity-note");
  let noteText = att.liquidity_label || "";
  if (data?.signal_source?.includes("attention")) {
    noteText += ` · Factored into main ${data.signal} signal (${(data.confidence || 0).toFixed(0)}% confidence).`;
  }
  note.textContent = noteText;
}

function showNewsAlertPopup(alert) {
  const banner = document.getElementById("news-alert-banner");
  if (!banner) return;
  banner.className = "news-alert-banner " + (alert.urgency === "immediate" ? "immediate" : "pre");
  banner.textContent = `🔔 ${alert.type === "pre_event" ? "UPCOMING" : "BREAKING"}: ${alert.event} → ${alert.signal} — ${alert.message}`;
}

function playAlertSound(urgent) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = urgent ? 880 : 440;
    gain.gain.value = 0.1;
    osc.start();
    osc.stop(ctx.currentTime + (urgent ? 0.3 : 0.15));
  } catch (_) {}
}

if ("Notification" in window && Notification.permission === "default") {
  Notification.requestPermission();
}

fetchAgent();
setInterval(fetchAgent, 15000);

ChartTools.init(rerenderChartBySource);

if (typeof TradeAlerts !== "undefined") {
  TradeAlerts.init(socket);
}