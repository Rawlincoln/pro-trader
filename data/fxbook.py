"""MyFXBook public data: community outlook + forex news."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from data.assets import get_asset
from data.news import _score_sentiment

logger = logging.getLogger(__name__)

BASE_URL = "https://www.myfxbook.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ProTrader/1.0",
}

FXBOOK_SYMBOLS = {
    "eurusd": "EURUSD",
    "gold": "XAUUSD",
}

_cache: dict[str, dict] = {}
CACHE_TTL = 600
NEWS_PAGE_TTL = 300


def _get_cached(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry and time.time() - entry["fetched_at"] < CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(key: str, data: Any) -> None:
    _cache[key] = {"data": data, "fetched_at": time.time()}


def _parse_pct(value: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", value or "")
    return float(m.group(1)) if m else None


def _parse_lots(value: str) -> float | None:
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*lots?", value or "", re.I)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def _parse_positions(value: str) -> int | None:
    m = re.search(r"([\d,]+)", value or "")
    return int(m.group(1).replace(",", "")) if m else None


def _parse_popularity(html: str, symbol: str) -> float | None:
    m = re.search(
        rf"(\d+(?:\.\d+)?)% of traders are currently trading {re.escape(symbol)}",
        html,
        re.I,
    )
    return float(m.group(1)) if m else None


def fetch_community_outlook(symbol: str | None = None) -> dict[str, Any]:
    """Scrape MyFXBook community positioning for one or all symbols."""
    cache_key = f"outlook:{symbol or 'all'}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {"symbols": {}, "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        resp = requests.get(f"{BASE_URL}/community/outlook", timeout=8, headers=HEADERS)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
            if header[:5] != ["Symbol", "Action", "Percentage", "Volume", "Positions"]:
                continue

            cells = [c.get_text(" ", strip=True) for c in rows[1].find_all("td")]
            if len(cells) < 5:
                continue

            sym = cells[0]
            if not re.fullmatch(r"[A-Z0-9]{6,8}", sym):
                continue

            short_row = {
                "action": cells[1],
                "percentage": _parse_pct(cells[2]),
                "volume_lots": _parse_lots(cells[3]),
                "positions": _parse_positions(cells[4]),
            }
            long_row = {"action": "Long", "percentage": None, "volume_lots": None, "positions": None}
            if len(rows) > 2:
                long_cells = [c.get_text(" ", strip=True) for c in rows[2].find_all("td")]
                if len(long_cells) >= 4 and long_cells[0].lower() == "long":
                    long_row = {
                        "action": "Long",
                        "percentage": _parse_pct(long_cells[1]),
                        "volume_lots": _parse_lots(long_cells[2]),
                        "positions": _parse_positions(long_cells[3]),
                    }

            short_pct = short_row["percentage"] or 0
            long_pct = long_row["percentage"] or 0
            crowd_bias = "neutral"
            if long_pct >= 60:
                crowd_bias = "bullish"
            elif short_pct >= 60:
                crowd_bias = "bearish"

            result["symbols"][sym] = {
                "symbol": sym,
                "short": short_row,
                "long": long_row,
                "short_percentage": short_pct,
                "long_percentage": long_pct,
                "crowd_bias": crowd_bias,
                "popularity_pct": _parse_popularity(resp.text, sym),
                "total_positions": (short_row["positions"] or 0) + (long_row["positions"] or 0),
                "total_volume_lots": round(
                    (short_row["volume_lots"] or 0) + (long_row["volume_lots"] or 0), 2
                ),
            }

        if symbol:
            sym = symbol.upper()
            result = {
                "symbol": sym,
                "available": sym in result["symbols"],
                "data": result["symbols"].get(sym),
                "updated_at": result["updated_at"],
            }
    except Exception as exc:
        logger.warning("MyFXBook outlook fetch failed: %s", exc)
        result["error"] = str(exc)
        if symbol:
            result = {"symbol": symbol.upper(), "available": False, "data": None, "error": str(exc)}

    _set_cache(cache_key, result)
    return result


def _scrape_all_fxbook_headlines() -> list[dict[str, Any]]:
    """Scrape MyFXBook news page once; filter per asset later."""
    cached = _get_cached("news:all")
    if cached is not None:
        return cached

    articles: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        resp = requests.get(f"{BASE_URL}/news", timeout=8, headers=HEADERS)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for link in soup.select("a[href*='/news/']"):
            href = link.get("href", "")
            if not href or href in seen:
                continue
            title = link.get_text(" ", strip=True)
            if not title or len(title) < 12:
                continue

            full_url = urljoin(BASE_URL, href)
            slug = href.rstrip("/").split("/")[-1]
            if not slug.isdigit():
                continue

            parent = link.find_parent(["div", "article", "li"]) or link.parent
            summary = ""
            if parent:
                summary = parent.get_text(" ", strip=True)
                if summary.startswith(title):
                    summary = summary[len(title):].strip()[:300]

            seen.add(href)
            combined = f"{title} {summary}"
            articles.append({
                "source": "MyFXBook",
                "title": title,
                "summary": summary,
                "link": full_url,
                "published": datetime.now(timezone.utc).isoformat(),
                "sentiment": _score_sentiment(combined),
            })
    except Exception as exc:
        logger.warning("MyFXBook news fetch failed: %s", exc)

    _cache["news:all"] = {"data": articles, "fetched_at": time.time()}
    return articles


def fetch_fxbook_news(limit: int = 20, asset_id: str = "eurusd") -> list[dict[str, Any]]:
    """Filter cached MyFXBook headlines for the active asset."""
    asset = get_asset(asset_id)
    keywords = asset["news_keywords"]
    articles = [
        a for a in _scrape_all_fxbook_headlines()
        if keywords.search(f"{a['title']} {a.get('summary', '')}")
    ]
    return articles[:limit]


def _contrarian_signal(crowd_bias: str, asset_id: str) -> tuple[str, str]:
    """Map crowd positioning to a contrarian trading hint."""
    if crowd_bias == "bullish":
        if asset_id == "eurusd":
            return "SELL", "MyFXBook crowd heavily long — contrarian bearish bias"
        if asset_id == "gold":
            return "SELL", "MyFXBook crowd heavily long gold — contrarian bearish bias"
    if crowd_bias == "bearish":
        if asset_id == "eurusd":
            return "BUY", "MyFXBook crowd heavily short — contrarian bullish bias"
        if asset_id == "gold":
            return "BUY", "MyFXBook crowd heavily short gold — contrarian bullish bias"
    return "WAIT", "MyFXBook crowd positioning balanced"


def build_fxbook_stats(asset_id: str = "eurusd") -> dict[str, Any]:
    """Aggregate MyFXBook stats for dashboard display."""
    asset = get_asset(asset_id)
    symbol = FXBOOK_SYMBOLS.get(asset_id)
    news = fetch_fxbook_news(15, asset_id)

    stats: dict[str, Any] = {
        "source": "MyFXBook",
        "symbol": symbol,
        "news_count": len(news),
        "news_bullish": sum(1 for n in news if n.get("sentiment") == "bullish"),
        "news_bearish": sum(1 for n in news if n.get("sentiment") == "bearish"),
        "news_neutral": sum(1 for n in news if n.get("sentiment") == "neutral"),
        "articles": news[:8],
        "outlook": None,
        "crowd_signal": "WAIT",
        "crowd_reason": "No MyFXBook crowd data for this asset",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if not symbol:
        stats["crowd_reason"] = "MyFXBook crowd data not available for crypto"
        return stats

    outlook = fetch_community_outlook(symbol)
    data = outlook.get("data")
    stats["outlook"] = data

    if data:
        crowd = data.get("crowd_bias", "neutral")
        signal, reason = _contrarian_signal(crowd, asset_id)
        stats["crowd_bias"] = crowd
        stats["short_pct"] = data.get("short_percentage")
        stats["long_pct"] = data.get("long_percentage")
        stats["popularity_pct"] = data.get("popularity_pct")
        stats["total_positions"] = data.get("total_positions")
        stats["total_volume_lots"] = data.get("total_volume_lots")
        stats["crowd_signal"] = signal
        stats["crowd_reason"] = reason

    return stats