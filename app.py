"""Multi-asset Pro Trader - Real-time analysis dashboard."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, join_room

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from analysis.indicators import add_all_indicators, indicators_to_series
from analysis.signals import build_full_analysis
from data.assets import ASSETS, DEFAULT_ASSET, get_asset, list_assets
from data.calendar import calendar_risk_assessment, fetch_calendar
from data.fetcher import fetch_live_quote, fetch_ohlc, ohlc_to_chart
from data.news import fetch_news, news_sentiment_summary
from data.news_trader import build_news_trading_snapshot, run_news_monitor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "pro-trader-secret")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

PORT = int(os.environ.get("PORT", 5000))
HOST = os.environ.get("HOST", "0.0.0.0")
_bg_started = False

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()
REFRESH_INTERVAL = 30


def run_analysis(asset_id: str = DEFAULT_ASSET) -> dict:
    asset = get_asset(asset_id)
    try:
        df_1h = fetch_ohlc("1h", asset_id)
        df_4h = fetch_ohlc("4h", asset_id)
        quote = fetch_live_quote(asset_id)
        news = fetch_news(12, asset_id)
        news_sent = news_sentiment_summary(news)
        calendar = fetch_calendar(7, asset_id)
        cal_risk = calendar_risk_assessment(calendar)

        full = build_full_analysis(df_1h, df_4h, news_sent, cal_risk, asset_id)
        news_trading = build_news_trading_snapshot(asset_id)

        df_1h_ind = add_all_indicators(df_1h)
        df_4h_ind = add_all_indicators(df_4h)

        tech_signal = full["technical"]["signal"]
        tech_conf = full["technical"]["confidence"]
        news_signal = news_trading.get("combined_signal", "WAIT")
        news_conf = news_trading.get("combined_confidence", 40)

        # Blend: immediate news release overrides when high confidence
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
            "fundamental_notes": full["technical"].get("fundamental_notes", []),
            "analysis_1h": _serialize_analysis(full["analysis_1h"]),
            "analysis_4h": _serialize_analysis(full["analysis_4h"]),
            "trade_plan": full["trade_plan"],
            "exit_check": full["exit_check"],
            "news": news,
            "news_sentiment": news_sent,
            "calendar": calendar,
            "calendar_risk": cal_risk,
            "news_trading": news_trading,
            "charts": {
                "1h": {
                    "candles": ohlc_to_chart(df_1h_ind, 80, asset_id),
                    "indicators": indicators_to_series(df_1h_ind, 80),
                    "levels": full["analysis_1h"]["levels"],
                },
                "4h": {
                    "candles": ohlc_to_chart(df_4h_ind, 80, asset_id),
                    "indicators": indicators_to_series(df_4h_ind, 80),
                    "levels": full["analysis_4h"]["levels"],
                },
            },
            "decimals": asset["decimals"],
            "chart_tick_format": asset["chart_tick_format"],
            "updated_at": time.time(),
        }
    except Exception as exc:
        logger.exception("Analysis failed for %s: %s", asset_id, exc)
        return {"asset_id": asset_id, "error": str(exc), "updated_at": time.time()}


def _serialize_analysis(analysis: dict) -> dict:
    return {
        "timeframe": analysis["timeframe"],
        "bias": analysis["bias"],
        "score": analysis["score"],
        "trend": analysis["trend"],
        "confluence_count": analysis.get("confluence_count", 0),
        "indicators": analysis["indicators"],
        "levels": analysis["levels"],
        "patterns": analysis["patterns"],
        "breakdown": analysis["breakdown"],
    }


def get_cached_analysis(asset_id: str = DEFAULT_ASSET, force: bool = False) -> dict:
    with _cache_lock:
        entry = _cache.get(asset_id, {"data": None, "updated_at": 0})
        if force or entry["data"] is None or time.time() - entry["updated_at"] > REFRESH_INTERVAL:
            entry["data"] = run_analysis(asset_id)
            entry["updated_at"] = time.time()
            _cache[asset_id] = entry
        return entry["data"]


def background_refresh():
    while True:
        for asset_id in ASSETS:
            try:
                data = run_analysis(asset_id)
                with _cache_lock:
                    _cache[asset_id] = {"data": data, "updated_at": time.time()}
                if "error" not in data:
                    socketio.emit("market_update", data, room=asset_id)
            except Exception as exc:
                logger.error("Background refresh error for %s: %s", asset_id, exc)
        socketio.sleep(REFRESH_INTERVAL)


def background_news_monitor():
    """Fast loop for pre-event and live news alerts."""
    while True:
        try:
            alerts = run_news_monitor()
            for alert in alerts:
                asset_id = alert.get("asset_id", DEFAULT_ASSET)
                socketio.emit("news_alert", alert, room=asset_id)
                socketio.emit("news_alert", alert)  # global feed
        except Exception as exc:
            logger.error("News monitor error: %s", exc)
        socketio.sleep(20)


def _render_dashboard(asset_id: str):
    asset = get_asset(asset_id)
    return render_template(
        "dashboard.html",
        asset=asset,
        assets=list_assets(),
        active_asset=asset_id,
    )


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


def start_background_tasks() -> None:
    global _bg_started
    if _bg_started:
        return
    _bg_started = True
    socketio.start_background_task(background_refresh)
    socketio.start_background_task(background_news_monitor)
    logger.info("Background tasks started")


@socketio.on("connect")
def on_connect():
    start_background_tasks()
    asset_id = request.args.get("asset", DEFAULT_ASSET)
    if asset_id not in ASSETS:
        asset_id = DEFAULT_ASSET
    join_room(asset_id)
    data = get_cached_analysis(asset_id)
    socketio.emit("market_update", data)


# Start workers for gunicorn/cloud (no __main__ block)
if os.environ.get("PORT"):
    start_background_tasks()


if __name__ == "__main__":
    start_background_tasks()
    print("\n" + "=" * 60)
    print("  Pro Trader Dashboard")
    print(f"  Local:    http://127.0.0.1:{PORT}/")
    print(f"  Network:  http://{HOST}:{PORT}/")
    print("  EUR/USD · Gold · Bitcoin")
    print("=" * 60 + "\n")
    socketio.run(app, host=HOST, port=PORT, debug=False)