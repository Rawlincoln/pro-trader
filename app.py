"""Multi-asset Pro Trader - Real-time analysis dashboard."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, join_room

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from analysis.attention_liquidity import blend_attention_into_signal, build_attention_liquidity
from analysis.indicators import add_all_indicators, indicators_to_series
from analysis.patterns import pick_primary_pattern
from analysis.signals import build_full_analysis
from data.assets import ASSETS, DEFAULT_ASSET, get_asset, list_assets
from data.calendar import calendar_risk_assessment, fetch_calendar
from data.fetcher import fetch_live_quote, fetch_ohlc_bundle, ohlc_to_chart
from data.fxbook import build_fxbook_stats
from data.news import fetch_news, news_sentiment_summary
from data.news_trader import build_news_trading_snapshot, run_news_monitor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IS_CLOUD = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))
ASYNC_MODE = "threading" if IS_CLOUD else "eventlet"

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "pro-trader-secret")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=ASYNC_MODE)

PORT = int(os.environ.get("PORT", 5000))
HOST = os.environ.get("HOST", "0.0.0.0")
_bg_started = False

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()
_refreshing: set[str] = set()
REFRESH_INTERVAL = 30


def _fetch_market_bundle(asset_id: str) -> tuple:
    df_1h, df_4h = fetch_ohlc_bundle(asset_id)
    quote = fetch_live_quote(asset_id, df_1h=df_1h)
    return df_1h, df_4h, quote


def run_analysis(asset_id: str = DEFAULT_ASSET) -> dict:
    asset = get_asset(asset_id)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            f_market = pool.submit(_fetch_market_bundle, asset_id)
            news_limit = 28 if asset_id == "bitcoin" else 12
            f_news = pool.submit(fetch_news, news_limit, asset_id)
            f_calendar = pool.submit(fetch_calendar, 7, asset_id)
            f_fxbook = pool.submit(build_fxbook_stats, asset_id)

            df_1h, df_4h, quote = f_market.result()
            news = f_news.result()
            calendar = f_calendar.result()
            fxbook_stats = f_fxbook.result()

        news_sent = news_sentiment_summary(news)
        cal_risk = calendar_risk_assessment(calendar)

        full = build_full_analysis(df_1h, df_4h, news_sent, cal_risk, asset_id)
        news_trading = build_news_trading_snapshot(
            asset_id, news=news, calendar=calendar, quote=quote,
        )

        df_1h_ind = add_all_indicators(df_1h)
        df_4h_ind = add_all_indicators(df_4h)

        tech_signal = full["technical"]["signal"]
        tech_conf = full["technical"]["confidence"]
        news_signal = news_trading.get("combined_signal", "WAIT")
        news_conf = news_trading.get("combined_confidence", 40)

        final_signal = tech_signal
        final_conf = tech_conf
        signal_source = "technical"
        immediate = [a for a in news_trading.get("active_alerts", [])
                     if a.get("urgency") == "immediate" and a.get("signal") in ("BUY", "SELL")]
        if immediate and immediate[0].get("confidence", 0) >= 65:
            final_signal = immediate[0]["signal"]
            final_conf = round((tech_conf * 0.3 + immediate[0]["confidence"] * 0.7), 1)
            signal_source = "news_release"
        elif news_conf >= 70 and news_signal in ("BUY", "SELL"):
            final_signal = news_signal
            final_conf = round((tech_conf * 0.4 + news_conf * 0.6), 1)
            signal_source = "news"

        attention_liquidity = None
        attention_notes: list[str] = []
        if asset_id == "bitcoin":
            attention_liquidity = build_attention_liquidity(
                news=news, news_sent=news_sent, quote=quote,
            )
            final_signal, final_conf, signal_source, attention_notes = blend_attention_into_signal(
                tech_signal, tech_conf, final_signal, final_conf, attention_liquidity,
            )
            if signal_source == "attention" and immediate:
                signal_source = "attention+news"

        fundamental_notes = list(full["technical"].get("fundamental_notes", []))
        fundamental_notes.extend(attention_notes)

        return {
            "asset_id": asset_id,
            "asset_name": asset["name"],
            "quote": quote,
            "signal": final_signal,
            "confidence": final_conf,
            "technical_signal": tech_signal,
            "technical_confidence": tech_conf,
            "news_signal": news_signal,
            "news_confidence": news_conf,
            "signal_source": signal_source,
            "combined_score": full["technical"]["combined_score"],
            "confluence": full["technical"].get("confluence"),
            "adjusted_score": full["technical"].get("adjusted_score"),
            "timeframes_aligned": full["technical"]["timeframes_aligned"],
            "primary_trend": full["technical"]["primary_trend"],
            "fundamental_notes": fundamental_notes,
            "attention_liquidity": attention_liquidity,
            "analysis_1h": _serialize_analysis(full["analysis_1h"], final_signal),
            "analysis_4h": _serialize_analysis(full["analysis_4h"], final_signal),
            "trade_plan": full["trade_plan"],
            "exit_check": full["exit_check"],
            "news": news,
            "news_sentiment": news_sent,
            "calendar": calendar,
            "calendar_risk": cal_risk,
            "news_trading": news_trading,
            "fxbook_stats": fxbook_stats,
            "charts": {
                "1h": {
                    "candles": ohlc_to_chart(df_1h_ind, 80, asset_id),
                    "indicators": indicators_to_series(df_1h_ind, 80),
                    "levels": full["analysis_1h"]["levels"],
                    "patterns": _chart_patterns(full["analysis_1h"], final_signal),
                },
                "4h": {
                    "candles": ohlc_to_chart(df_4h_ind, 80, asset_id),
                    "indicators": indicators_to_series(df_4h_ind, 80),
                    "levels": full["analysis_4h"]["levels"],
                    "patterns": _chart_patterns(full["analysis_4h"], final_signal),
                },
            },
            "decimals": asset["decimals"],
            "chart_tick_format": asset["chart_tick_format"],
            "updated_at": time.time(),
        }
    except Exception as exc:
        logger.exception("Analysis failed for %s: %s", asset_id, exc)
        return {"asset_id": asset_id, "error": str(exc), "updated_at": time.time()}


def _chart_patterns(analysis: dict, signal: str) -> list[dict]:
    primary = pick_primary_pattern(
        analysis.get("patterns", []),
        analysis.get("bias", "neutral"),
        signal,
    )
    return [primary] if primary else []


def _serialize_analysis(analysis: dict, signal: str = "WAIT") -> dict:
    primary = pick_primary_pattern(
        analysis.get("patterns", []),
        analysis.get("bias", "neutral"),
        signal,
    )
    return {
        "timeframe": analysis["timeframe"],
        "bias": analysis["bias"],
        "score": analysis["score"],
        "trend": analysis["trend"],
        "confluence_count": analysis.get("confluence_count", 0),
        "indicators": analysis["indicators"],
        "levels": analysis["levels"],
        "patterns": [primary] if primary else [],
        "primary_pattern": primary,
        "breakdown": analysis["breakdown"],
    }


def _store_analysis(asset_id: str, data: dict) -> None:
    with _cache_lock:
        _cache[asset_id] = {"data": data, "updated_at": time.time()}


def _get_cache_entry(asset_id: str) -> dict:
    with _cache_lock:
        return _cache.get(asset_id, {"data": None, "updated_at": 0}).copy()


def _refresh_asset(asset_id: str, emit: bool = True, force: bool = False) -> dict:
    with _cache_lock:
        if asset_id in _refreshing and not force:
            entry = _cache.get(asset_id, {"data": None})
            return entry.get("data") or {"asset_id": asset_id, "error": "Refresh in progress"}
        _refreshing.add(asset_id)

    try:
        data = run_analysis(asset_id)
        _store_analysis(asset_id, data)
        if emit and "error" not in data:
            socketio.emit("market_update", data, room=asset_id)
        return data
    finally:
        with _cache_lock:
            _refreshing.discard(asset_id)


def get_cached_analysis(asset_id: str = DEFAULT_ASSET, force: bool = False) -> dict:
    entry = _get_cache_entry(asset_id)
    data = entry["data"]
    stale = data is None or time.time() - entry["updated_at"] > REFRESH_INTERVAL

    if force:
        return _refresh_asset(asset_id, emit=True, force=True)

    if not stale:
        return data

    if data is not None:
        socketio.start_background_task(_refresh_asset, asset_id, True, False)
        return data

    return _refresh_asset(asset_id, emit=False, force=True)


def _bg_sleep(seconds: float) -> None:
    if ASYNC_MODE == "eventlet":
        socketio.sleep(seconds)
    else:
        time.sleep(seconds)


def background_refresh():
    asset_ids = list(ASSETS.keys())
    idx = 0
    while True:
        asset_id = asset_ids[idx % len(asset_ids)]
        idx += 1
        try:
            _refresh_asset(asset_id, emit=True)
        except Exception as exc:
            logger.error("Background refresh error for %s: %s", asset_id, exc)
        _bg_sleep(REFRESH_INTERVAL)


def background_news_monitor():
    """Fast loop for pre-event and live news alerts."""
    while True:
        try:
            alerts = run_news_monitor()
            for alert in alerts:
                asset_id = alert.get("asset_id", DEFAULT_ASSET)
                socketio.emit("news_alert", alert, room=asset_id)
                socketio.emit("news_alert", alert)
        except Exception as exc:
            logger.error("News monitor error: %s", exc)
        _bg_sleep(45 if IS_CLOUD else 30)


def prewarm_cache():
    targets = [DEFAULT_ASSET] if IS_CLOUD else list(ASSETS.keys())
    for asset_id in targets:
        try:
            _refresh_asset(asset_id, emit=True)
        except Exception as exc:
            logger.error("Prewarm failed for %s: %s", asset_id, exc)


def _spawn_background(target) -> None:
    if ASYNC_MODE == "eventlet":
        socketio.start_background_task(target)
    else:
        threading.Thread(target=target, daemon=True).start()


def start_background_tasks() -> None:
    global _bg_started
    if _bg_started:
        return
    _bg_started = True
    if IS_CLOUD:
        threading.Timer(3.0, prewarm_cache).start()
    else:
        _spawn_background(prewarm_cache)
    _spawn_background(background_refresh)
    _spawn_background(background_news_monitor)
    logger.info("Background tasks started (cloud=%s)", IS_CLOUD)


def _render_dashboard(asset_id: str):
    asset = get_asset(asset_id)
    return render_template(
        "dashboard.html",
        asset=asset,
        assets=list_assets(),
        active_asset=asset_id,
    )


@app.before_request
def _lazy_start_workers():
    if request.path != "/health":
        start_background_tasks()


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "pro-trader", "cloud": IS_CLOUD})


@app.route("/")
def index():
    return _render_dashboard("eurusd")


@app.route("/gold")
def gold():
    return _render_dashboard("gold")


@app.route("/bitcoin")
def bitcoin():
    return _render_dashboard("bitcoin")


@app.route("/api/analysis")
@app.route("/api/analysis/<asset_id>")
def api_analysis(asset_id: str = DEFAULT_ASSET):
    return jsonify(get_cached_analysis(asset_id))


@app.route("/api/refresh")
@app.route("/api/refresh/<asset_id>")
def api_refresh(asset_id: str = DEFAULT_ASSET):
    return jsonify(get_cached_analysis(asset_id, force=True))


@app.route("/api/news-trading")
@app.route("/api/news-trading/<asset_id>")
def api_news_trading(asset_id: str = DEFAULT_ASSET):
    return jsonify(build_news_trading_snapshot(asset_id))


@app.route("/api/bitcoin/attention")
def api_bitcoin_attention():
    news = fetch_news(28, "bitcoin")
    news_sent = news_sentiment_summary(news)
    quote = fetch_live_quote("bitcoin")
    return jsonify(build_attention_liquidity(news=news, news_sent=news_sent, quote=quote))


@app.route("/api/agent")
def api_agent():
    state_path = ROOT / "agent_state.json"
    if state_path.exists():
        try:
            with open(state_path, encoding="utf-8") as f:
                return jsonify(json.load(f))
        except Exception as exc:
            return jsonify({"status": "error", "message": str(exc)})
    return jsonify({"status": "not_running", "message": "Agent not started. Run run_agent.bat"})


@socketio.on("connect")
def on_connect():
    start_background_tasks()
    asset_id = request.args.get("asset", DEFAULT_ASSET)
    if asset_id not in ASSETS:
        asset_id = DEFAULT_ASSET
    join_room(asset_id)

    entry = _get_cache_entry(asset_id)
    if entry["data"]:
        socketio.emit("market_update", entry["data"])
    else:
        socketio.start_background_task(_refresh_asset, asset_id, True, False)


if __name__ == "__main__":
    start_background_tasks()
    print("\n" + "=" * 60)
    print("  Pro Trader Dashboard")
    print(f"  Local:    http://127.0.0.1:{PORT}/")
    print(f"  Network:  http://{HOST}:{PORT}/")
    print("  EUR/USD · Gold · Bitcoin")
    print("=" * 60 + "\n")
    socketio.run(app, host=HOST, port=PORT, debug=False)