"""Real-time news & calendar monitor with alerts and price impact tracking."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from analysis.news_signals import (
    analyze_calendar_release,
    analyze_headline,
    combine_news_signals,
    pre_event_bias,
)
from data.calendar import fetch_calendar, parse_event_datetime
from data.fetcher import fetch_live_quote
from data.news import fetch_news

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
STATE_FILE = ROOT / "news_alerts_state.json"

PRE_ALERT_MINUTES = (60, 15, 5, 1)
LIVE_WINDOW_MINUTES = 3  # consider "just released" within 3 min of event time


def _event_id(event: dict) -> str:
    key = f"{event.get('title')}|{event.get('date')}|{event.get('time')}|{event.get('currency')}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _parse_event_dt(event: dict) -> datetime | None:
    return parse_event_datetime(event)


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"alerted": {}, "seen_news": {}, "price_snapshots": {}, "alert_log": []}


def _save_state(state: dict) -> None:
    state["alert_log"] = state.get("alert_log", [])[:200]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


def _log_alert(state: dict, alert: dict) -> None:
    state.setdefault("alert_log", []).insert(0, alert)
    logger.info("NEWS ALERT [%s] %s: %s", alert.get("type"), alert.get("signal"), alert.get("message"))


def _has_actual(actual: str) -> bool:
    return bool(actual and str(actual).strip() not in ("", "-", "N/A", "n/a"))


def _minutes_until(event_dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    return (event_dt - now).total_seconds() / 60


def _track_price_impact(
    state: dict,
    alert_id: str,
    asset_id: str,
    price_at_alert: float,
    current_price: float | None = None,
) -> dict | None:
    try:
        current = current_price if current_price is not None else fetch_live_quote(asset_id)["price"]
        change = current - price_at_alert
        pct = (change / price_at_alert * 100) if price_at_alert else 0
        return {
            "price_at_alert": price_at_alert,
            "current_price": current,
            "change": round(change, 5),
            "change_pct": round(pct, 3),
            "direction": "up" if change > 0 else "down" if change < 0 else "flat",
        }
    except Exception:
        return None


def scan_calendar_alerts(
    asset_id: str,
    state: dict | None = None,
    events: list[dict] | None = None,
    quote_price: float | None = None,
) -> tuple[list[dict], dict]:
    """Scan calendar for pre-event and live-release alerts."""
    state = state or _load_state()
    alerts: list[dict] = []
    now = datetime.now(timezone.utc)

    events = events if events is not None else fetch_calendar(days_ahead=2, asset_id=asset_id)
    cached_price = quote_price
    for event in events:
        eid = _event_id(event)
        event_dt = _parse_event_dt(event)
        if not event_dt:
            continue

        mins = _minutes_until(event_dt)
        event["minutes_until"] = round(mins, 1)
        event["datetime"] = event_dt.isoformat()
        event["event_id"] = eid

        alerted = state.setdefault("alerted", {}).setdefault(eid, {})

        # Pre-event alerts
        for window in PRE_ALERT_MINUTES:
            key = f"pre_{window}"
            if 0 < mins <= window and not alerted.get(key):
                bias = pre_event_bias(event, asset_id)
                if cached_price is None:
                    cached_price = fetch_live_quote(asset_id).get("price")
                price = cached_price
                alert = {
                    "id": f"{eid}_{key}",
                    "type": "pre_event",
                    "urgency": "high" if window <= 5 else "medium" if window <= 15 else "low",
                    "asset_id": asset_id,
                    "event": event["title"],
                    "currency": event.get("currency"),
                    "impact": event.get("impact"),
                    "minutes_until": round(mins),
                    "window_minutes": window,
                    "signal": bias.get("expected_signal", "WAIT"),
                    "confidence": bias.get("expected_confidence", bias.get("confidence", 50)),
                    "strategy": bias.get("strategy"),
                    "message": bias.get("reason", f"Event in {round(mins)} minutes"),
                    "forecast": event.get("forecast"),
                    "previous": event.get("previous"),
                    "price_at_alert": price,
                    "timestamp": now.isoformat(),
                }
                alerts.append(alert)
                _log_alert(state, alert)
                alerted[key] = now.isoformat()
                state.setdefault("price_snapshots", {})[alert["id"]] = {
                    "price": price, "time": now.isoformat(), "asset_id": asset_id,
                }

        # Live release alert
        if _has_actual(event.get("actual", "")) and not alerted.get("released"):
            analysis = analyze_calendar_release(event, asset_id)
            if cached_price is None:
                cached_price = fetch_live_quote(asset_id).get("price")
            price = cached_price
            alert = {
                "id": f"{eid}_released",
                "type": "calendar_release",
                "urgency": "immediate",
                "asset_id": asset_id,
                "event": event["title"],
                "currency": event.get("currency"),
                "impact": event.get("impact"),
                "actual": event.get("actual"),
                "forecast": event.get("forecast"),
                "previous": event.get("previous"),
                "surprise": analysis.get("surprise"),
                "signal": analysis["signal"],
                "confidence": analysis["confidence"],
                "pip_estimate": analysis.get("pip_estimate"),
                "message": analysis["reason"],
                "price_at_alert": price,
                "timestamp": now.isoformat(),
            }
            alerts.append(alert)
            _log_alert(state, alert)
            alerted["released"] = now.isoformat()
            state.setdefault("price_snapshots", {})[alert["id"]] = {
                "price": price, "time": now.isoformat(), "asset_id": asset_id,
            }

        # Post-release window (event time passed, waiting for actual)
        elif -LIVE_WINDOW_MINUTES <= mins <= 0 and not alerted.get("released") and not alerted.get("live_pending"):
            if cached_price is None:
                cached_price = fetch_live_quote(asset_id).get("price")
            price = cached_price
            alert = {
                "id": f"{eid}_live_now",
                "type": "live_pending",
                "urgency": "immediate",
                "asset_id": asset_id,
                "event": event["title"],
                "currency": event.get("currency"),
                "impact": event.get("impact"),
                "signal": "WAIT",
                "confidence": 70.0,
                "message": f"LIVE NOW: {event['title']} — watch for spike, enter on direction confirm",
                "forecast": event.get("forecast"),
                "previous": event.get("previous"),
                "price_at_alert": price,
                "timestamp": now.isoformat(),
            }
            alerts.append(alert)
            _log_alert(state, alert)
            alerted["live_pending"] = now.isoformat()

    _save_state(state)
    return alerts, state


def scan_headline_alerts(
    asset_id: str,
    state: dict | None = None,
    limit: int = 20,
    articles: list[dict] | None = None,
    quote_price: float | None = None,
) -> tuple[list[dict], dict]:
    """Detect new breaking headlines and generate instant signals."""
    state = state or _load_state()
    alerts: list[dict] = []
    now = datetime.now(timezone.utc)
    seen = state.setdefault("seen_news", {})

    articles = articles if articles is not None else fetch_news(limit, asset_id)
    cached_price = quote_price
    for article in articles:
        link = article.get("link", "")
        if not link or seen.get(link):
            continue

        pub = article.get("published", "")
        try:
            pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            age_min = (now - pub_dt).total_seconds() / 60
        except Exception:
            age_min = 999

        if age_min > 30:
            seen[link] = pub
            continue

        analysis = analyze_headline(article["title"], article.get("summary", ""), asset_id)
        if analysis["signal"] == "WAIT" and analysis["confidence"] < 50:
            seen[link] = pub
            continue

        if cached_price is None:
            cached_price = fetch_live_quote(asset_id).get("price")
        price = cached_price
        alert = {
            "id": hashlib.md5(link.encode()).hexdigest()[:12],
            "type": "headline",
            "urgency": "immediate" if age_min < 5 else "medium",
            "asset_id": asset_id,
            "event": article["title"],
            "source": article.get("source"),
            "link": link,
            "signal": analysis["signal"],
            "confidence": analysis["confidence"],
            "sentiment": analysis.get("sentiment"),
            "message": analysis["reason"],
            "price_at_alert": price,
            "age_minutes": round(age_min, 1),
            "timestamp": now.isoformat(),
        }
        alerts.append(alert)
        _log_alert(state, alert)
        seen[link] = pub
        state.setdefault("price_snapshots", {})[alert["id"]] = {
            "price": price, "time": now.isoformat(), "asset_id": asset_id,
        }

    _save_state(state)
    return alerts, state


def build_news_trading_snapshot(
    asset_id: str,
    news: list[dict] | None = None,
    calendar: list[dict] | None = None,
    quote: dict | None = None,
) -> dict[str, Any]:
    """Full news trading state for dashboard."""
    state = _load_state()
    quote_price = quote.get("price") if quote else None
    calendar_events = calendar if calendar is not None else fetch_calendar(days_ahead=2, asset_id=asset_id)
    news_articles = news if news is not None else fetch_news(20, asset_id)

    cal_alerts, state = scan_calendar_alerts(
        asset_id, state, events=calendar_events, quote_price=quote_price,
    )
    head_alerts, state = scan_headline_alerts(
        asset_id, state, articles=news_articles, quote_price=quote_price,
    )

    events = calendar_events
    enriched_events = []
    for ev in events:
        dt = _parse_event_dt(ev)
        if dt:
            ev["datetime"] = dt.isoformat()
            ev["minutes_until"] = round(_minutes_until(dt), 1)
            ev["event_id"] = _event_id(ev)
            if ev["minutes_until"] > 0:
                ev["pre_bias"] = pre_event_bias(ev, asset_id)
            if _has_actual(ev.get("actual", "")):
                ev["release_analysis"] = analyze_calendar_release(ev, asset_id)

        enriched_events.append(ev)

    calendar_signals = [
        {**a, "type": "calendar_release"} for a in cal_alerts if a["type"] == "calendar_release"
    ]
    headline_signals = [
        {"signal": a["signal"], "confidence": a["confidence"], "reason": a["message"], "type": "headline"}
        for a in head_alerts
    ]

    combined = combine_news_signals(calendar_signals, headline_signals, asset_id)

    # Price impact on recent alerts
    impacts = []
    for alert in (cal_alerts + head_alerts)[:10]:
        snap = state.get("price_snapshots", {}).get(alert["id"])
        if snap:
            impact = _track_price_impact(
                state, alert["id"], asset_id, snap["price"], current_price=quote_price,
            )
            if impact:
                impacts.append({
                    "alert_id": alert["id"],
                    "event": alert.get("event"),
                    "signal": alert.get("signal"),
                    **impact,
                })

    recent_log = state.get("alert_log", [])[:20]

    return {
        "combined_signal": combined["signal"],
        "combined_confidence": combined["confidence"],
        "combined_reasons": combined["reasons"],
        "active_alerts": cal_alerts + head_alerts,
        "upcoming_events": [e for e in enriched_events if e.get("minutes_until", -1) > 0][:10],
        "released_events": [e for e in enriched_events if _has_actual(e.get("actual", ""))][:8],
        "headline_signals": head_alerts,
        "price_impacts": impacts,
        "alert_history": recent_log,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_news_monitor() -> list[dict]:
    """Run monitor for all assets, return new alerts to broadcast."""
    from data.assets import ASSETS

    all_alerts = []
    for asset_id in ASSETS:
        cal, _ = scan_calendar_alerts(asset_id)
        head, _ = scan_headline_alerts(asset_id)
        for a in cal + head:
            a["asset_id"] = asset_id
        all_alerts.extend(cal + head)
    return all_alerts