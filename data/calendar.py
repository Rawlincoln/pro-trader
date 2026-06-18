"""Economic calendar for EUR/USD relevant events."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

_calendar_cache: dict = {"data": None, "fetched_at": 0}
CACHE_TTL = 300  # 5 min

HIGH_IMPACT_CURRENCIES = {"USD", "EUR"}
HIGH_IMPACT_KEYWORDS = {
    "interest rate", "rate decision", "fomc", "ecb", "nfp", "non-farm",
    "cpi", "inflation", "gdp", "pmi", "employment", "jobless", "retail sales",
    "trade balance", "consumer confidence", "ppi", "fed", "lagarde", "powell",
}


def _parse_impact(event: dict) -> str:
    impact = str(event.get("impact", "")).lower()
    if impact in ("high", "holiday"):
        return "high" if impact == "high" else "low"
    return "medium"


def _is_relevant(event: dict, currencies: set[str] | None = None) -> bool:
    allowed = currencies or HIGH_IMPACT_CURRENCIES
    currency = str(event.get("country", event.get("currency", ""))).upper()
    if currency in allowed:
        title = str(event.get("title", "")).lower()
        if any(kw in title for kw in HIGH_IMPACT_KEYWORDS):
            return True
        if _parse_impact(event) == "high":
            return True
    return False


def _fetch_raw_calendar() -> list[dict]:
    import time as _time

    if _calendar_cache["data"] and _time.time() - _calendar_cache["fetched_at"] < CACHE_TTL:
        return _calendar_cache["data"]

    urls = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json"]
    raw: list[dict] = []

    for url in urls:
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "ProTrader/1.0"})
            resp.raise_for_status()
            raw = resp.json()
            _calendar_cache["data"] = raw
            _calendar_cache["fetched_at"] = _time.time()
            break
        except Exception as exc:
            logger.warning("Calendar fetch failed for %s: %s", url, exc)

    return raw or _calendar_cache.get("data") or []


def fetch_calendar(days_ahead: int = 7, asset_id: str = "eurusd") -> list[dict[str, Any]]:
    from data.assets import get_asset

    asset = get_asset(asset_id)
    currencies = asset.get("calendar_currencies", HIGH_IMPACT_CURRENCIES)
    events: list[dict[str, Any]] = []

    for item in _fetch_raw_calendar():
        if not _is_relevant(item, currencies):
            continue
        events.append({
            "title": item.get("title", "Unknown"),
            "currency": item.get("country", item.get("currency", "")),
            "date": item.get("date", ""),
            "time": item.get("time", ""),
            "impact": _parse_impact(item),
            "forecast": item.get("forecast", ""),
            "previous": item.get("previous", ""),
            "actual": item.get("actual", ""),
        })

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_ahead)

    def parse_event_dt(ev: dict) -> datetime | None:
        try:
            date_str = ev.get("date", "")
            time_str = ev.get("time", "12:00pm")
            if not date_str:
                return None
            for fmt in ("%m-%d-%Y %I:%M%p", "%m-%d-%Y"):
                try:
                    dt = datetime.strptime(f"{date_str} {time_str}".strip(), fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        except Exception:
            pass
        return None

    upcoming = []
    for ev in events:
        dt = parse_event_dt(ev)
        if dt and now - timedelta(hours=2) <= dt <= cutoff:
            ev["datetime"] = dt.isoformat()
            upcoming.append(ev)

    upcoming.sort(key=lambda x: x.get("datetime", ""))
    return upcoming[:25]


def calendar_risk_assessment(events: list[dict]) -> dict[str, Any]:
    high_impact = [e for e in events if e.get("impact") == "high"]
    next_event = high_impact[0] if high_impact else (events[0] if events else None)

    risk = "low"
    if len(high_impact) >= 3:
        risk = "high"
    elif len(high_impact) >= 1:
        risk = "medium"

    warning = None
    if next_event:
        warning = f"Upcoming: {next_event['title']} ({next_event['currency']}) - {next_event.get('date', '')} {next_event.get('time', '')}"

    return {
        "risk_level": risk,
        "high_impact_count": len(high_impact),
        "next_event": next_event,
        "warning": warning,
    }