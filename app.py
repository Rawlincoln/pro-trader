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
from analysis.coach import build_coach_card
from analysis.indicators import add_all_indicators, indicators_to_series
from analysis.patterns import pick_primary_pattern
from analysis.signals import build_full_analysis, check_exit_conditions, generate_trade_plan
from data.assets import ASSETS, DEFAULT_ASSET, get_asset, list_assets
from data.calendar import calendar_risk_assessment, fetch_calendar
from data.fetcher import fetch_live_quote, fetch_ohlc_bundle, ohlc_to_chart
from data.fxbook import build_fxbook_stats
from data.news import fetch_news, news_sentiment_summary
from data.news_trader import build_news_trading_snapshot, run_news_monitor
from agent.config import load_config as load_agent_config
from agent.config import myfxbook_config_public, save_myfxbook_config
from data.trade_ledger import (
    _sync_ledger_auto,
    get_balance_sheet,
    get_myfxbook_status,
    get_mt5_status,
    import_csv_deals,
    sync_from_mt5,
    sync_from_myfxbook,
)
from data.trade_alerts import (
    detect_price_alerts,
    detect_trade_alerts,
    discover_telegram_chats,
    dispatch_alerts,
    get_alert_history,
    get_alerts_status,
    load_config as load_alert_config,
    merge_test_config,
    save_config as save_alert_config,
    test_telegram as test_alert_telegram,
    _safe_config as safe_alert_config,
)
from data.user_levels import build_user_sr_snapshot, get_drawings, save_drawings
from data.signal_audit import (
    apply_accuracy_to_analysis,
    build_daily_audit,
    evaluate_open_signals,
    get_audit_status,
    maybe_log_signal,
    run_daily_audit_cycle,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IS_CLOUD = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))
ASYNC_MODE = "threading" if IS_CLOUD else "eventlet"

app = Flask(__name__, static_folder="static", template_folder="templates")
# Never ship a known default secret in production
_secret = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
if not _secret:
    _secret = "pro-trader-dev-only"
    if IS_CLOUD:
        logging.getLogger(__name__).warning(
            "SECRET_KEY not set — set it in Render env for production"
        )
app.config["SECRET_KEY"] = _secret
# Lock down CORS in cloud; allow all only for local/dev
_cors = os.environ.get("CORS_ORIGINS", "*")
socketio = SocketIO(
    app,
    cors_allowed_origins=[o.strip() for o in _cors.split(",")] if _cors != "*" else "*",
    async_mode=ASYNC_MODE,
)

PORT = int(os.environ.get("PORT", 5000))
HOST = os.environ.get("HOST", "0.0.0.0")
_bg_started = False

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()
_refreshing: set[str] = set()
REFRESH_INTERVAL = 30
PRICE_WATCH_INTERVAL = 15


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

        # User-drawn 4H/1H levels (from chart tools) drive S/R for trading
        px_hint = float((quote or {}).get("price") or 0)
        user_sr = build_user_sr_snapshot(
            asset_id,
            px_hint,
            decimals=asset["decimals"],
            near_dist=asset["near_level_distance"],
        )
        full = build_full_analysis(
            df_1h, df_4h, news_sent, cal_risk, asset_id, user_sr=user_sr,
        )
        # Refresh user_sr with accurate price from analysis if available
        price_live = full["analysis_1h"]["indicators"].get("price") or px_hint
        user_sr = build_user_sr_snapshot(
            asset_id,
            float(price_live or 0),
            decimals=asset["decimals"],
            near_dist=asset["near_level_distance"],
        )
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
        # News may only override when technical is not strongly opposite
        immediate = [a for a in news_trading.get("active_alerts", [])
                     if a.get("urgency") == "immediate" and a.get("signal") in ("BUY", "SELL")]
        tech_blocks_news = (
            tech_signal in ("BUY", "SELL")
            and news_signal in ("BUY", "SELL")
            and tech_signal != news_signal
            and tech_conf >= 65
        )
        if (
            immediate
            and immediate[0].get("confidence", 0) >= 72
            and not tech_blocks_news
        ):
            final_signal = immediate[0]["signal"]
            final_conf = round((tech_conf * 0.35 + immediate[0]["confidence"] * 0.65), 1)
            signal_source = "news_release"
        elif (
            news_conf >= 75
            and news_signal in ("BUY", "SELL")
            and not tech_blocks_news
            and (tech_signal == "WAIT" or tech_signal == news_signal or tech_conf < 60)
        ):
            final_signal = news_signal
            final_conf = round((tech_conf * 0.45 + news_conf * 0.55), 1)
            signal_source = "news"
            if final_conf < 58:
                final_signal = "WAIT"
                signal_source = "technical"

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

        # Final risk gate: high-impact calendar demotes late overrides
        if cal_risk.get("risk_level") == "high" and final_signal in ("BUY", "SELL"):
            if final_conf < 75:
                fundamental_notes.append(
                    "High-impact calendar — final signal demoted to WAIT"
                )
                final_signal = "WAIT"
                final_conf = max(25.0, float(final_conf) - 12)
                signal_source = "technical"

        # Rebuild plan from final signal (news/attention may have changed it)
        price = full["analysis_1h"]["indicators"].get("price") or quote.get("price") or 0
        atr_val = full["analysis_1h"]["indicators"].get("atr")
        trade_plan = generate_trade_plan(
            signal=final_signal,
            price=float(price or 0),
            atr=atr_val,
            levels_1h=full["analysis_1h"]["levels"],
            levels_4h=full["analysis_4h"]["levels"],
            confidence=float(final_conf or 0),
            asset_id=asset_id,
            user_sr=user_sr,
        )
        exit_check = check_exit_conditions(
            float(price or 0), trade_plan, full["analysis_1h"]["indicators"]
        )

        # Account equity for position sizing (cached ledger snapshot — no live MT5 call)
        equity = 1000.0
        risk_pct = 1.0
        try:
            cfg = load_agent_config()
            risk_pct = float(cfg.get("risk_percent") or 1.0)
            ledger_path = ROOT / "trade_ledger.json"
            if ledger_path.exists():
                with open(ledger_path, encoding="utf-8") as lf:
                    led = json.load(lf)
                acc = led.get("account_snapshot") or {}
                if acc.get("equity"):
                    equity = float(acc["equity"]) or equity
                elif acc.get("balance"):
                    equity = float(acc["balance"]) or equity
        except Exception:
            pass

        tech_for_coach = {
            **(full.get("technical") or {}),
            "timeframes_aligned": full["technical"].get("timeframes_aligned"),
            "confluence": full["technical"].get("confluence"),
        }
        coach = build_coach_card(
            asset_id=asset_id,
            signal=final_signal,
            confidence=float(final_conf or 0),
            trade_plan=trade_plan,
            exit_check=exit_check,
            technical=tech_for_coach,
            calendar_risk=cal_risk,
            user_sr=user_sr,
            news_sentiment=news_sent,
            signal_source=signal_source,
            equity=equity,
            risk_percent=risk_pct,
        )
        # Coach may demote weak / thin-session setups
        if coach.get("signal") == "WAIT" and final_signal in ("BUY", "SELL"):
            final_signal = "WAIT"
            final_conf = min(float(final_conf or 0), float(coach.get("confidence") or 50))
            fundamental_notes.append(
                f"Coach demoted to WAIT (grade {coach.get('grade', {}).get('letter', '?')})"
            )
            trade_plan = generate_trade_plan(
                signal="WAIT",
                price=float(price or 0),
                atr=atr_val,
                levels_1h=full["analysis_1h"]["levels"],
                levels_4h=full["analysis_4h"]["levels"],
                confidence=float(final_conf or 0),
                asset_id=asset_id,
                user_sr=user_sr,
            )
            coach = build_coach_card(
                asset_id=asset_id,
                signal="WAIT",
                confidence=float(final_conf or 0),
                trade_plan=trade_plan,
                exit_check=exit_check,
                technical=tech_for_coach,
                calendar_risk=cal_risk,
                user_sr=user_sr,
                news_sentiment=news_sent,
                signal_source=signal_source,
                equity=equity,
                risk_percent=risk_pct,
            )

        # Accuracy feedback loop: scale conf / demote weak sources from journal history
        final_signal, final_conf, acc_notes = apply_accuracy_to_analysis(
            final_signal, float(final_conf or 0), signal_source or "technical",
        )
        if acc_notes:
            fundamental_notes.extend(acc_notes)
            if final_signal == "WAIT" and trade_plan.get("action") in ("BUY", "SELL"):
                trade_plan = generate_trade_plan(
                    signal="WAIT",
                    price=float(price or 0),
                    atr=atr_val,
                    levels_1h=full["analysis_1h"]["levels"],
                    levels_4h=full["analysis_4h"]["levels"],
                    confidence=float(final_conf or 0),
                    asset_id=asset_id,
                    user_sr=user_sr,
                )
                coach = build_coach_card(
                    asset_id=asset_id,
                    signal="WAIT",
                    confidence=float(final_conf or 0),
                    trade_plan=trade_plan,
                    exit_check=exit_check,
                    technical=tech_for_coach,
                    calendar_risk=cal_risk,
                    user_sr=user_sr,
                    news_sentiment=news_sent,
                    signal_source=signal_source,
                    equity=equity,
                    risk_percent=risk_pct,
                )

        result = {
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
            "trade_plan": trade_plan,
            "exit_check": exit_check,
            "coach": coach,
            "user_sr": user_sr,
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
        # Journal actionable signals for daily accuracy audit + learning
        try:
            maybe_log_signal(result)
        except Exception as audit_exc:
            logger.debug("Signal audit log skip: %s", audit_exc)
        return result
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


def _emit_trade_alerts(alerts: list[dict]) -> None:
    for alert in alerts:
        asset_id = alert.get("asset_id", DEFAULT_ASSET)
        socketio.emit("trade_alert", alert, room=asset_id)
        socketio.emit("trade_alert", alert, room="all_alerts")


def _process_trade_alerts(asset_id: str, data: dict) -> None:
    if data.get("error"):
        return
    try:
        alerts, _ = detect_trade_alerts(asset_id, data)
        if alerts:
            dispatch_alerts(alerts)
            _emit_trade_alerts(alerts)
    except Exception as exc:
        logger.error("Trade alert error for %s: %s", asset_id, exc)


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
            _process_trade_alerts(asset_id, data)
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
    """Fast loop for pre-event and live news alerts (+ Telegram when configured)."""
    while True:
        try:
            alerts = run_news_monitor()
            cfg = load_alert_config()
            precision = cfg.get("precision_mode", True)
            for alert in alerts:
                asset_id = alert.get("asset_id", DEFAULT_ASSET)
                socketio.emit("news_alert", alert, room=asset_id)
                socketio.emit("news_alert", alert, room="all_alerts")
                # Precision: only high-urgency news to Telegram
                if not cfg.get("alert_news", True):
                    continue
                if precision and alert.get("urgency") not in ("immediate", "high"):
                    continue
                conf = float(alert.get("confidence") or 0)
                if precision and conf < float(cfg.get("min_confidence_signal", 72)):
                    continue
                # Map news alert into telegram dispatcher shape
                tg_alert = {
                    "type": alert.get("type") or "news",
                    "signal": alert.get("signal") or "NEWS",
                    "asset_name": get_asset(asset_id)["name"],
                    "message": alert.get("message") or alert.get("event") or "",
                    "confidence": conf,
                    "price": alert.get("price"),
                    "reason": alert.get("reason") or "News / calendar",
                    "urgency": alert.get("urgency"),
                }
                try:
                    dispatch_alerts([tg_alert], cfg)
                except Exception as exc:
                    logger.debug("News telegram skip: %s", exc)
        except Exception as exc:
            logger.error("News monitor error: %s", exc)
        _bg_sleep(45 if IS_CLOUD else 30)


def background_price_watch():
    """Fast price loop for entry/exit level hits on all symbols."""
    while True:
        try:
            config = load_alert_config()
            if not config.get("enabled"):
                _bg_sleep(PRICE_WATCH_INTERVAL)
                continue
            for asset_id in ASSETS:
                if not config.get("symbols", {}).get(asset_id, True):
                    continue
                entry = _get_cache_entry(asset_id)
                cached = entry.get("data")
                if not cached or cached.get("error"):
                    continue
                try:
                    quote = fetch_live_quote(asset_id)
                    live_price = float(quote.get("price", 0))
                    if not live_price:
                        continue
                    alerts, _ = detect_price_alerts(asset_id, cached, live_price, config=config)
                    if alerts:
                        dispatch_alerts(alerts, config)
                        _emit_trade_alerts(alerts)
                    # Score open signal-audit journal entries against live price
                    try:
                        evaluate_open_signals(prices={asset_id: live_price})
                    except Exception as exc:
                        logger.debug("Signal audit eval %s: %s", asset_id, exc)
                except Exception as exc:
                    logger.debug("Price watch %s: %s", asset_id, exc)
        except Exception as exc:
            logger.error("Price watch error: %s", exc)
        _bg_sleep(PRICE_WATCH_INTERVAL)


def background_signal_audit():
    """Re-score journal, and send daily accuracy audit on Telegram after 18:00 UTC."""
    while True:
        try:
            prices: dict[str, float] = {}
            for asset_id in ASSETS:
                entry = _get_cache_entry(asset_id)
                data = entry.get("data") or {}
                q = data.get("quote") or {}
                p = q.get("price")
                if p:
                    prices[asset_id] = float(p)
                else:
                    try:
                        quote = fetch_live_quote(asset_id)
                        if quote.get("price"):
                            prices[asset_id] = float(quote["price"])
                    except Exception:
                        pass
            if prices:
                evaluate_open_signals(prices=prices)
            run_daily_audit_cycle(force=False, send_telegram=True)
        except Exception as exc:
            logger.error("Signal audit background error: %s", exc)
        _bg_sleep(300 if IS_CLOUD else 180)


def prewarm_cache():
    targets = list(ASSETS.keys())
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
    _spawn_background(background_price_watch)
    _spawn_background(background_signal_audit)
    # Always keep Myfxbook ledger fresh (phone trades) while app is running
    _spawn_background(background_ledger_sync)
    logger.info("Background tasks started (cloud=%s, myfxbook auto-sync + signal audit on)", IS_CLOUD)


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
    # Include /health so Render keepalive wakes scanners 24/7
    start_background_tasks()


@app.route("/health")
def health():
    start_background_tasks()
    status = get_alerts_status(scanner_running=_bg_started)
    audit = get_audit_status()
    return jsonify({
        "status": "ok",
        "service": "pro-trader",
        "cloud": IS_CLOUD,
        "trade_alerts": status,
        "telegram_24_7": bool(status.get("server_push_ready")),
        "signal_audit": {
            "total_logged": audit.get("total_logged"),
            "open": audit.get("open"),
            "win_rate": audit.get("overall_win_rate"),
            "calibration_ready": (audit.get("calibration") or {}).get("ready"),
        },
    })


@app.route("/api/signal-audit")
def api_signal_audit():
    return jsonify(get_audit_status())


@app.route("/api/signal-audit/daily")
def api_signal_audit_daily():
    day = request.args.get("date")
    if day:
        try:
            from datetime import date as date_cls
            y, m, d = (int(x) for x in day.split("-"))
            audit = build_daily_audit(date_cls(y, m, d))
        except (ValueError, TypeError):
            audit = build_daily_audit()
    else:
        audit = build_daily_audit()
    return jsonify(audit)


@app.route("/api/signal-audit/run", methods=["POST"])
def api_signal_audit_run():
    """Force evaluate opens + rebuild daily audit; optional Telegram."""
    body = request.get_json(silent=True) or {}
    send_tg = body.get("telegram", True)
    prices: dict[str, float] = {}
    for asset_id in ASSETS:
        entry = _get_cache_entry(asset_id)
        data = entry.get("data") or {}
        p = (data.get("quote") or {}).get("price")
        if p:
            prices[asset_id] = float(p)
    closed = evaluate_open_signals(prices=prices) if prices else []
    audit = run_daily_audit_cycle(force=True, send_telegram=bool(send_tg))
    return jsonify({
        "ok": True,
        "closed_now": len(closed),
        "telegram_sent": audit.get("telegram_sent"),
        "audit": audit,
        "status": get_audit_status(),
    })


@app.route("/api/trade-alerts/status")
def api_trade_alerts_status():
    return jsonify(get_alerts_status(scanner_running=_bg_started))


@app.route("/api/trade-alerts/history")
def api_trade_alerts_history():
    return jsonify({"alerts": get_alert_history(50)})


@app.route("/api/trade-alerts/config", methods=["GET", "POST"])
def api_trade_alerts_config():
    if request.method == "GET":
        return jsonify(safe_alert_config(load_alert_config()))
    body = request.get_json(silent=True) or {}
    cfg = save_alert_config(body)
    return jsonify({"ok": True, "config": safe_alert_config(cfg)})


@app.route("/api/trade-alerts/test", methods=["POST"])
def api_trade_alerts_test():
    body = request.get_json(silent=True) or {}
    return jsonify(test_alert_telegram(merge_test_config(body)))


@app.route("/api/trade-alerts/telegram/discover", methods=["POST"])
def api_trade_alerts_discover():
    body = request.get_json(silent=True) or {}
    cfg = merge_test_config(body)
    token = body.get("telegram_bot_token") or cfg.get("telegram_bot_token", "")
    return jsonify(discover_telegram_chats(token))


@app.route("/")
def index():
    return _render_dashboard("eurusd")


@app.route("/gold")
def gold():
    return _render_dashboard("gold")


@app.route("/bitcoin")
def bitcoin():
    return _render_dashboard("bitcoin")


@app.route("/balance")
def balance_page():
    resp = app.make_response(render_template("balance.html", assets=list_assets()))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/api/mt5/status")
def api_mt5_status():
    return jsonify(get_mt5_status())


@app.route("/api/mt5/sync", methods=["POST"])
def api_mt5_sync():
    days = int(request.args.get("days", 365))
    return jsonify(sync_from_mt5(days=days))


def _myfxbook_request_payload() -> dict:
    data = request.get_json(silent=True) or {}
    return {
        "email": (data.get("email") or request.args.get("email") or "").strip() or None,
        "password": data.get("password") or request.args.get("password") or None,
        "account_id": data.get("account_id") or request.args.get("account_id"),
    }


@app.route("/api/user-levels/<asset_id>", methods=["GET"])
def api_user_levels_get(asset_id: str):
    """Return saved chart drawings used as S/R for trading."""
    if asset_id not in ASSETS:
        return jsonify({"error": "unknown asset"}), 404
    return jsonify(get_drawings(asset_id))


@app.route("/api/user-levels/<asset_id>", methods=["POST"])
def api_user_levels_save(asset_id: str):
    """Save chart drawings (H-lines, zones, trend lines) for S/R trading."""
    if asset_id not in ASSETS:
        return jsonify({"error": "unknown asset"}), 404
    body = request.get_json(silent=True) or {}
    saved = save_drawings(
        asset_id,
        chart_1h=body.get("chart-1h"),
        chart_4h=body.get("chart-4h"),
        drawings=body.get("drawings"),
    )
    # Invalidate analysis cache so next poll uses new levels
    with _cache_lock:
        _cache.pop(asset_id, None)
    return jsonify({"ok": True, "message": "Levels saved — used for entries & stops", **saved})


@app.route("/api/myfxbook/config")
def api_myfxbook_config_get():
    return jsonify(myfxbook_config_public())


@app.route("/api/myfxbook/config", methods=["POST"])
def api_myfxbook_config_save():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    account_id = data.get("account_id") or 0
    existing = load_agent_config()
    email = email or (existing.get("myfxbook_email") or "").strip()
    if not email:
        return jsonify({"ok": False, "error": "email is required on first save only"}), 400
    # Blank password = keep saved (never force re-entry)
    if not password and not existing.get("myfxbook_password"):
        return jsonify({"ok": False, "error": "password is required on first save only"}), 400
    save_myfxbook_config(
        email,
        password or existing.get("myfxbook_password", ""),
        account_id or existing.get("myfxbook_account_id") or 0,
    )
    pub = myfxbook_config_public()
    msg = (
        "Locked in — you will not need to re-enter credentials."
        if pub.get("saved_permanently")
        else "Saved — auto-sync uses these credentials."
    )
    return jsonify({
        "ok": True,
        "message": msg,
        "config": pub,
    })


@app.route("/api/myfxbook/status")
def api_myfxbook_status():
    payload = _myfxbook_request_payload()
    return jsonify(get_myfxbook_status(**payload))


@app.route("/api/myfxbook/accounts", methods=["GET", "POST"])
def api_myfxbook_accounts():
    payload = _myfxbook_request_payload()
    return jsonify(get_myfxbook_status(**payload))


@app.route("/api/myfxbook/sync", methods=["POST"])
def api_myfxbook_sync():
    payload = _myfxbook_request_payload()
    save_credentials = bool(request.get_json(silent=True))
    return jsonify(sync_from_myfxbook(**payload, save_credentials=save_credentials))


@app.route("/api/balance-sheet")
def api_balance_sheet():
    """Return balance sheet; kick a background Myfxbook sync if data is stale."""
    data = get_balance_sheet()
    cfg = load_agent_config()
    if cfg.get("myfxbook_auto_sync", True):
        try:
            minutes = max(1, int(cfg.get("ledger_sync_minutes", 2)))
            synced_at = data.get("synced_at")
            stale = True
            if synced_at:
                from datetime import datetime, timezone
                try:
                    ts = datetime.fromisoformat(str(synced_at).replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - ts).total_seconds()
                    stale = age > minutes * 60
                except Exception:
                    stale = True
            if stale:
                def _kick():
                    try:
                        r = _sync_ledger_auto()
                        if r.get("ok"):
                            logger.info("Balance-page stale sync: %s", r.get("message"))
                    except Exception as exc:
                        logger.debug("Stale sync kick failed: %s", exc)
                threading.Thread(target=_kick, daemon=True).start()
                data["auto_sync_triggered"] = True
        except Exception:
            pass
    return jsonify(data)


@app.route("/api/balance-sheet/import", methods=["POST"])
def api_balance_sheet_import():
    csv_text = request.get_data(as_text=True) or ""
    return jsonify(import_csv_deals(csv_text))


def background_ledger_sync():
    """Always auto-sync Myfxbook (phone trades) on a timer while the app runs.

    - Syncs immediately on start
    - Repeats every ledger_sync_minutes (default 2)
    - Works local and cloud when Myfxbook credentials are set
    """
    # First pass ASAP so balance is fresh after launch
    first = True
    while True:
        try:
            cfg = load_agent_config()
            auto = cfg.get("myfxbook_auto_sync", True)
            minutes = max(1, int(cfg.get("ledger_sync_minutes", 2)))
            if auto:
                result = _sync_ledger_auto()
                if result.get("ok"):
                    logger.info("Auto Myfxbook sync: %s", result.get("message"))
                elif result.get("error"):
                    logger.warning("Auto Myfxbook sync: %s", result.get("error"))
            else:
                logger.debug("myfxbook_auto_sync is false — skipping")
            # After first sync, wait full interval; first run had no prior wait
            if first:
                first = False
            _bg_sleep(minutes * 60)
        except Exception as exc:
            logger.warning("Ledger sync error: %s", exc)
            _bg_sleep(60)


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
    join_room("all_alerts")

    entry = _get_cache_entry(asset_id)
    if entry["data"]:
        socketio.emit("market_update", entry["data"])
    else:
        socketio.start_background_task(_refresh_asset, asset_id, True, False)


# Gunicorn (Render) never hits __main__ — start scanners on import after a short delay
if IS_CLOUD or os.environ.get("PRO_TRADER_BG", "1") == "1":
    threading.Timer(2.0, start_background_tasks).start()


if __name__ == "__main__":
    start_background_tasks()
    print("\n" + "=" * 60)
    print("  Pro Trader Dashboard")
    print(f"  Local:    http://127.0.0.1:{PORT}/")
    print(f"  Network:  http://{HOST}:{PORT}/")
    print("  EUR/USD · Gold · Bitcoin · Telegram 24/7 on Render")
    print("=" * 60 + "\n")
    socketio.run(app, host=HOST, port=PORT, debug=False)