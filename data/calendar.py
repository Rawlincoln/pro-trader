"""Economic calendar for EUR/USD relevant events."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

_calendar_cache: dict = {"data": None, "fetched_at": 0}
CACHE_TTL = 600  # 10 min
EMPTY_ACTUAL = {"", "-", "N/A", "n/a"}

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
    if impact in ("low", "none"):
        return "low"
    return "medium"


def _has_actual(actual: str) -> bool:
    return bool(actual and str(actual).strip() not in EMPTY_ACTUAL)


def parse_event_datetime(event: dict) -> datetime | None:
    """Parse Forex Factory / MyFXBook style event timestamps."""
    date_raw = str(event.get("date", "")).strip()
    time_str = str(event.get("time", "")).strip()

    if not date_raw:
        return None

    if "T" in date_raw:
        try:
            dt = datetime.fromisoformat(date_raw)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    if not time_str:
        time_str = "12:00pm"
    for fmt in ("%m-%d-%Y %I:%M%p", "%m-%d-%Y %I:%M %p", "%m-%d-%Y"):
        try:
            raw = f"{date_raw} {time_str}".strip() if "%M" in fmt or "%p" in fmt else date_raw
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _display_date_time(event_dt: datetime | None, event: dict) -> tuple[str, str]:
    if event_dt:
        return event_dt.strftime("%m-%d-%Y"), event_dt.strftime("%I:%M%p").lower()
    date_raw = str(event.get("date", ""))
    if "T" in date_raw:
        try:
            dt = datetime.fromisoformat(date_raw).astimezone(timezone.utc)
            return dt.strftime("%m-%d-%Y"), dt.strftime("%I:%M%p").lower()
        except ValueError:
            pass
    return date_raw, str(event.get("time", ""))


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

    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    raw: list[dict] = []

    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "ProTrader/1.0"})
        resp.raise_for_status()
        raw = resp.json()
        _calendar_cache["data"] = raw
        _calendar_cache["fetched_at"] = _time.time()
    except Exception as exc:
        logger.warning("Calendar fetch failed for %s: %s", url, exc)
        if _calendar_cache.get("data"):
            logger.info("Using cached calendar data after fetch failure")
            return _calendar_cache["data"]

    return raw or _calendar_cache.get("data") or []


def fetch_calendar(days_ahead: int = 7, asset_id: str = "eurusd") -> list[dict[str, Any]]:
    from data.assets import get_asset

    asset = get_asset(asset_id)
    currencies = asset.get("calendar_currencies", HIGH_IMPACT_CURRENCIES)
    events: list[dict[str, Any]] = []

    seen_keys: set[str] = set()
    for item in _fetch_raw_calendar():
        if not _is_relevant(item, currencies):
            continue
        key = f"{item.get('title')}|{item.get('date')}|{item.get('country')}"
        if key in seen_keys:
            continue
        seen_keys.add(key)

        event_dt = parse_event_datetime(item)
        display_date, display_time = _display_date_time(event_dt, item)
        events.append({
            "title": item.get("title", "Unknown"),
            "currency": item.get("country", item.get("currency", "")),
            "date": display_date,
            "time": display_time,
            "impact": _parse_impact(item),
            "forecast": item.get("forecast", ""),
            "previous": item.get("previous", ""),
            "actual": item.get("actual", ""),
            "source": "Forex Factory",
        })

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_ahead)

    upcoming = []
    for ev in events:
        dt = parse_event_datetime(ev)
        if dt and now - timedelta(hours=2) <= dt <= cutoff:
            ev["datetime"] = dt.isoformat()
            upcoming.append(ev)

    upcoming.sort(key=lambda x: x.get("datetime", ""))
    return upcoming[:30]


def calendar_risk_assessment(events: list[dict]) -> dict[str, Any]:
    stats = build_calendar_stats(events)
    return {
        "risk_level": stats["risk_level"],
        "high_impact_count": stats["high_impact"],
        "next_event": stats.get("next_event"),
        "warning": stats.get("warning"),
        **stats,
    }


def build_calendar_stats(events: list[dict]) -> dict[str, Any]:
    """Rich calendar stats from Forex Factory data."""
    now = datetime.now(timezone.utc)
    high = [e for e in events if e.get("impact") == "high"]
    medium = [e for e in events if e.get("impact") == "medium"]
    low = [e for e in events if e.get("impact") == "low"]
    released = [e for e in events if _has_actual(e.get("actual", ""))]
    upcoming = [e for e in events if not _has_actual(e.get("actual", ""))]

    next_24h = []
    for ev in upcoming:
        dt = parse_event_datetime(ev)
        if dt and 0 <= (dt - now).total_seconds() <= 86400:
            next_24h.append(ev)

    beats = misses = inline = 0
    for ev in released:
        actual = ev.get("actual", "")
        forecast = ev.get("forecast", "") or ev.get("previous", "")
        if not _has_actual(actual) or not forecast or forecast in EMPTY_ACTUAL:
            continue
        try:
            a = float(re.sub(r"[%KMB,]", "", str(actual)))
            f = float(re.sub(r"[%KMB,]", "", str(forecast)))
            if abs(a - f) <= abs(f) * 0.01:
                inline += 1
            elif a > f:
                beats += 1
            else:
                misses += 1
        except ValueError:
            continue

    risk = "low"
    if len(high) >= 3 or len(next_24h) >= 4:
        risk = "high"
    elif len(high) >= 1 or len(next_24h) >= 2:
        risk = "medium"

    next_event = high[0] if high else (upcoming[0] if upcoming else None)
    warning = None
    if next_event:
        warning = (
            f"Upcoming: {next_event['title']} ({next_event['currency']}) - "
            f"{next_event.get('date', '')} {next_event.get('time', '')}"
        )

    currencies: dict[str, int] = {}
    for ev in events:
        cur = str(ev.get("currency", "")).upper()
        currencies[cur] = currencies.get(cur, 0) + 1

    return {
        "total_events": len(events),
        "high_impact": len(high),
        "medium_impact": len(medium),
        "low_impact": len(low),
        "released_count": len(released),
        "upcoming_count": len(upcoming),
        "next_24h_count": len(next_24h),
        "beats": beats,
        "misses": misses,
        "inline": inline,
        "currencies": currencies,
        "next_event": next_event,
        "warning": warning,
        "risk_level": risk,
        "source": "Forex Factory",
    }