/**
 * Chart fullscreen + drawing tools (trend lines, H/V lines, rectangles).
 */
const ChartTools = (() => {
  const CHART_IDS = ["chart-1h", "chart-4h"];
  const DRAW_COLOR = "#fbbf24";
  const STORAGE_PREFIX = "protrader-drawings";

  const state = {
    modes: {},
    pending: {},
    drawings: {},
    fullscreenSourceId: null,
    onRerender: null,
  };

  const TOOLS = [
    { id: "select", label: "Pan", title: "Pan & zoom", icon: "✥" },
    { id: "trend", label: "Trend", title: "Trend line (2 clicks)", icon: "╱" },
    { id: "extended", label: "Ray", title: "Extended trend line (2 clicks)", icon: "↗" },
    { id: "hline", label: "H", title: "Horizontal line (1 click)", icon: "─" },
    { id: "vline", label: "V", title: "Vertical line (1 click)", icon: "│" },
    { id: "rect", label: "Zone", title: "Rectangle zone (2 clicks)", icon: "▢" },
    { id: "undo", label: "Undo", title: "Undo last drawing", icon: "↩", action: true },
    { id: "clear", label: "Clear", title: "Clear all drawings", icon: "✕", action: true },
    { id: "fullscreen", label: "Full", title: "Fullscreen chart", icon: "⛶", action: true },
  ];

  function storageKey(chartId) {
    const assetId = (window.ASSET && window.ASSET.id) || "default";
    return `${STORAGE_PREFIX}-${assetId}-${chartId}`;
  }

  function loadDrawings(chartId) {
    if (state.drawings[chartId]) return;
    try {
      const raw = localStorage.getItem(storageKey(chartId));
      state.drawings[chartId] = raw ? JSON.parse(raw) : [];
    } catch (_) {
      state.drawings[chartId] = [];
    }
  }

  function saveDrawings(chartId) {
    try {
      localStorage.setItem(storageKey(chartId), JSON.stringify(state.drawings[chartId] || []));
    } catch (_) {}
  }

  function resolveDrawingKey(plotId) {
    if (plotId === "chart-fullscreen-plot" && state.fullscreenSourceId) {
      return state.fullscreenSourceId;
    }
    return plotId;
  }

  function getMode(chartId) {
    return state.modes[chartId] || "select";
  }

  function setMode(chartId, mode) {
    state.modes[chartId] = mode;
    state.pending[chartId] = null;
    updateToolbarActive(chartId);
    updateHint(chartId);
    const plotIds = plotIdsForChart(chartId);
    plotIds.forEach(pid => {
      if (document.getElementById(pid)) {
        Plotly.relayout(pid, { dragmode: mode === "select" ? "zoom" : false });
      }
    });
  }

  function plotIdsForChart(chartId) {
    const ids = [chartId];
    if (state.fullscreenSourceId === chartId) ids.push("chart-fullscreen-plot");
    return ids;
  }

  function updateToolbarActive(chartId) {
    const mode = getMode(chartId);
    document.querySelectorAll(`.chart-toolbar[data-chart="${chartId}"] .chart-tool-btn[data-tool]`).forEach(btn => {
      const tool = btn.dataset.tool;
      btn.classList.toggle("active", !btn.dataset.action && tool === mode);
    });
  }

  function hintEl(chartId) {
    return document.getElementById(chartId === "chart-1h" ? "chart-hint-1h" : "chart-hint-4h");
  }

  function updateHint(chartId) {
    const el = hintEl(chartId);
    if (!el) return;
    const mode = getMode(chartId);
    const pending = state.pending[chartId];
    const hints = {
      select: "Drag to zoom · double-click to reset",
      trend: pending ? "Click second point to finish trend line" : "Click first point for trend line",
      extended: pending ? "Click second point to finish extended line" : "Click first point for extended line",
      hline: "Click on chart to place horizontal line",
      vline: "Click on chart to place vertical line",
      rect: pending ? "Click opposite corner for rectangle" : "Click first corner of zone",
    };
    if (mode === "select") {
      el.classList.add("hidden");
      el.textContent = "";
    } else {
      el.classList.remove("hidden");
      el.textContent = hints[mode] || "";
    }
  }

  function toolbarHtml(chartId) {
    return TOOLS.map(t => {
      const cls = t.action ? "chart-tool-btn action" : "chart-tool-btn";
      return `<button type="button" class="${cls}" data-chart="${chartId}" data-tool="${t.id}" title="${t.title}">
        <span class="tool-icon">${t.icon}</span><span class="tool-label">${t.label}</span>
      </button>`;
    }).join("");
  }

  function initToolbar(chartId) {
    const el = document.querySelector(`.chart-toolbar[data-chart="${chartId}"]`);
    if (!el || el.dataset.ready) return;
    el.innerHTML = toolbarHtml(chartId);
    el.dataset.ready = "1";
    loadDrawings(chartId);
    setMode(chartId, "select");
  }

  function initFullscreenToolbar() {
    const el = document.getElementById("chart-fullscreen-toolbar");
    if (!el) return;
    el.addEventListener("click", onToolbarClick);
  }

  function onToolbarClick(e) {
    const btn = e.target.closest(".chart-tool-btn");
    if (!btn) return;
    const chartId = btn.dataset.chart;
    const tool = btn.dataset.tool;
    if (!chartId || !tool) return;

    if (tool === "fullscreen") {
      openFullscreen(chartId);
      return;
    }
    if (tool === "clear") {
      clearDrawings(chartId);
      return;
    }
    if (tool === "undo") {
      undoDrawing(chartId);
      return;
    }
    setMode(chartId, tool);
  }

  function addDrawing(chartId, drawing) {
    loadDrawings(chartId);
    state.drawings[chartId].push({ id: Date.now().toString(36), ...drawing });
    saveDrawings(chartId);
    requestRerender(chartId);
  }

  function clearDrawings(chartId) {
    state.drawings[chartId] = [];
    saveDrawings(chartId);
    state.pending[chartId] = null;
    requestRerender(chartId);
    updateHint(chartId);
  }

  function undoDrawing(chartId) {
    loadDrawings(chartId);
    if (state.drawings[chartId].length) {
      state.drawings[chartId].pop();
      saveDrawings(chartId);
    }
    state.pending[chartId] = null;
    requestRerender(chartId);
    updateHint(chartId);
  }

  function requestRerender(chartId) {
    if (typeof state.onRerender === "function") {
      state.onRerender(chartId);
    }
  }

  function pixelToData(plotId, clientX, clientY) {
    const gd = document.getElementById(plotId);
    const fl = gd?._fullLayout;
    if (!fl) return null;
    const bbox = gd.getBoundingClientRect();
    const xpx = clientX - bbox.left;
    const ypx = clientY - bbox.top;
    const xaxis = fl.xaxis;
    const yaxis = fl.yaxis;
    if (!xaxis || !yaxis) return null;

    let x = xaxis.p2d(xpx);
    if (xaxis.type === "date" && typeof x === "number") {
      x = new Date(x).toISOString();
    }
    const y = yaxis.p2d(ypx);
    if (!Number.isFinite(y)) return null;
    return { x, y };
  }

  function applyDrawPoint(chartId, pt) {
    const mode = getMode(chartId);

    if (mode === "hline") {
      addDrawing(chartId, { type: "hline", y: pt.y, color: DRAW_COLOR });
      return;
    }
    if (mode === "vline") {
      addDrawing(chartId, { type: "vline", x: pt.x, color: DRAW_COLOR });
      return;
    }

    const pending = state.pending[chartId];
    if (!pending) {
      state.pending[chartId] = pt;
      updateHint(chartId);
      return;
    }

    const drawing = {
      type: mode,
      x0: pending.x,
      y0: pending.y,
      x1: pt.x,
      y1: pt.y,
      color: DRAW_COLOR,
    };
    state.pending[chartId] = null;
    addDrawing(chartId, drawing);
    updateHint(chartId);
  }

  function onPlotMouseDown(plotId, e) {
    const chartId = resolveDrawingKey(plotId);
    const mode = getMode(chartId);
    if (mode === "select" || e.button !== 0) return;
    const pt = pixelToData(plotId, e.clientX, e.clientY);
    if (!pt) return;
    e.preventDefault();
    applyDrawPoint(chartId, pt);
  }

  function extendTrend(x0, y0, x1, y1, xMin, xMax) {
    const t0 = new Date(x0).getTime();
    const t1 = new Date(x1).getTime();
    if (!Number.isFinite(t0) || !Number.isFinite(t1) || t0 === t1) {
      return { x0: xMin, y0, x1: xMax, y1: y0 };
    }
    const slope = (y1 - y0) / (t1 - t0);
    const tMin = new Date(xMin).getTime();
    const tMax = new Date(xMax).getTime();
    return {
      x0: xMin,
      y0: y0 + slope * (tMin - t0),
      x1: xMax,
      y1: y0 + slope * (tMax - t0),
    };
  }

  function drawingToShapes(drawing, times) {
    const line = { color: drawing.color || DRAW_COLOR, width: 2 };
    const xStart = times[0];
    const xEnd = times[times.length - 1];

    switch (drawing.type) {
      case "hline":
        return [{
          type: "line", xref: "x", yref: "y",
          x0: xStart, x1: xEnd, y0: drawing.y, y1: drawing.y,
          line: { ...line, dash: "solid" },
        }];
      case "vline":
        return [{
          type: "line", xref: "x", yref: "paper",
          x0: drawing.x, x1: drawing.x, y0: 0, y1: 1,
          line: { ...line, dash: "dot" },
        }];
      case "trend":
        return [{
          type: "line", xref: "x", yref: "y",
          x0: drawing.x0, y0: drawing.y0, x1: drawing.x1, y1: drawing.y1,
          line,
        }];
      case "extended": {
        const ext = extendTrend(drawing.x0, drawing.y0, drawing.x1, drawing.y1, xStart, xEnd);
        return [{
          type: "line", xref: "x", yref: "y",
          x0: ext.x0, y0: ext.y0, x1: ext.x1, y1: ext.y1,
          line: { ...line, dash: "dash" },
        }];
      }
      case "rect":
        return [{
          type: "rect", xref: "x", yref: "y",
          x0: drawing.x0, y0: drawing.y0, x1: drawing.x1, y1: drawing.y1,
          fillcolor: "rgba(251,191,36,0.12)",
          line: { color: drawing.color || DRAW_COLOR, width: 1.5 },
        }];
      default:
        return [];
    }
  }

  function getDrawShapes(plotId, times) {
    const chartId = resolveDrawingKey(plotId);
    loadDrawings(chartId);
    return (state.drawings[chartId] || []).flatMap(d => drawingToShapes(d, times));
  }

  function bindPlotEvents(plotId) {
    const el = document.getElementById(plotId);
    if (!el || el._chartToolsBound) return;
    el._chartToolsBound = true;
    el.addEventListener("mousedown", e => onPlotMouseDown(plotId, e));
    el.on("plotly_doubleclick", () => {
      Plotly.relayout(plotId, { "xaxis.autorange": true, "yaxis.autorange": true });
    });
  }

  function openFullscreen(chartId) {
    const overlay = document.getElementById("chart-fullscreen");
    if (!overlay) return;

    state.fullscreenSourceId = chartId;
    const title = chartId === "chart-1h" ? "1H Chart" : "4H Chart";
    document.getElementById("chart-fullscreen-title").textContent = title;

    const fsToolbar = document.getElementById("chart-fullscreen-toolbar");
    if (fsToolbar) {
      fsToolbar.dataset.chart = chartId;
      fsToolbar.innerHTML = toolbarHtml(chartId);
      updateToolbarActive(chartId);
    }

    overlay.classList.remove("hidden");
    document.body.classList.add("chart-fs-open");
    requestRerender(chartId);

    setTimeout(() => {
      const plot = document.getElementById("chart-fullscreen-plot");
      if (plot) Plotly.Plots.resize(plot);
    }, 80);
  }

  function closeFullscreen() {
    const overlay = document.getElementById("chart-fullscreen");
    if (!overlay) return;
    overlay.classList.add("hidden");
    document.body.classList.remove("chart-fs-open");
    const sourceId = state.fullscreenSourceId;
    state.fullscreenSourceId = null;
    if (sourceId) {
      setTimeout(() => {
        const plot = document.getElementById(sourceId);
        if (plot) Plotly.Plots.resize(plot);
      }, 80);
    }
  }

  function isFullscreen(chartId) {
    return state.fullscreenSourceId === chartId;
  }

  function init(onRerender) {
    state.onRerender = onRerender;
    CHART_IDS.forEach(initToolbar);
    initFullscreenToolbar();

    document.querySelectorAll(".chart-toolbar[data-chart]").forEach(tb => {
      if (!tb.id || tb.id !== "chart-fullscreen-toolbar") {
        tb.addEventListener("click", onToolbarClick);
      }
    });

    document.getElementById("chart-fullscreen-close")?.addEventListener("click", closeFullscreen);
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && state.fullscreenSourceId) closeFullscreen();
    });
  }

  return {
    init,
    getDrawShapes,
    bindPlotEvents,
    resolveDrawingKey,
    openFullscreen,
    closeFullscreen,
    isFullscreen,
    getMode,
  };
})();