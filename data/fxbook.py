"""MyFXBook data: community outlook (API) + news (RSS fallback when scrape blocked)."""

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
API_ROOT = "https://www.myfxbook.com/api"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

FXBOOK_SYMBOLS = {
    "eurusd": "EURUSD",
    "gold": "XAUUSD",
}

_cache: dict[str, dict] = {}
CACHE_TTL = 600


def _get_cached(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry and time.time() - entry["fetched_at"] < CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(key: str, data: Any) -> None:
    _cache[key] = {"data": data, "fetched_at": time.time()}


def _normalize_symbol_row(row: dict) -> dict[str, Any]:
    """Normalize API or scrape row into dashboard structure."""
    sym = (row.get("name") or row.get("symbol") or "").upper()
    short_pct = float(row.get("shortPercentage") or row.get("short_percentage") or 0)
    long_pct = float(row.get("longPercentage") or row.get("long_percentage") or 0)
    short_vol = float(row.get("shortVolume") or row.get("volume_lots_short") or 0)
    long_vol = float(row.get("longVolume") or row.get("volume_lots_long") or 0)
    short_pos = int(row.get("shortPositions") or row.get("positions_short") or 0)
    long_pos = int(row.get("longPositions") or row.get("positions_long") or 0)
    total_pos = int(row.get("totalPositions") or (short_pos + long_pos))

    crowd_bias = "neutral"
    if long_pct >= 60:
        crowd_bias = "bullish"
    elif short_pct >= 60:
        crowd_bias = "bearish"

    return {
        "symbol": sym,
        "short": {
            "action": "Short",
            "percentage": short_pct,
            "volume_lots": short_vol,
            "positions": short_pos,
        },
        "long": {
            "action": "Long",
            "percentage": long_pct,
            "volume_lots": long_vol,
            "positions": long_pos,
        },
        "short_percentage": short_pct,
        "long_percentage": long_pct,
        "crowd_bias": crowd_bias,
        "popularity_pct": row.get("popularity_pct"),
        "total_positions": total_pos,
        "total_volume_lots": round(short_vol + long_vol, 2),
        "avg_short_price": row.get("avgShortPrice"),
        "avg_long_price": row.get("avgLongPrice"),
    }


def _fetch_outlook_api() -> dict[str, dict[str, Any]]:
    """Authenticated Myfxbook community outlook — works when HTML pages return 403."""
    try:
        from agent.config import load_config
        from data.myfxbook_sync import _login, MyfxbookError
    except Exception as exc:
        logger.debug("Myfxbook API imports unavailable: %s", exc)
        return {}

    cfg = load_config()
    email = (cfg.get("myfxbook_email") or "").strip()
    password = cfg.get("myfxbook_password") or ""
    if not email or not password:
        return {}

    try:
        session = _login(email, password)
        resp = requests.post(
            f"{API_ROOT}/get-community-outlook.json",
            params={"session": session},
            timeout=20,
            headers={"User-Agent": "ProTrader/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("MyFXBook outlook API failed: %s", exc)
        return {}

    if data.get("error"):
        logger.warning("MyFXBook outlook API error: %s", data.get("message"))
        return {}

    symbols: dict[str, dict[str, Any]] = {}
    for row in data.get("symbols") or []:
        norm = _normalize_symbol_row(row)
        if norm["symbol"]:
            symbols[norm["symbol"]] = norm
    return symbols


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


def _fetch_outlook_scrape() -> dict[str, dict[str, Any]]:
    """Legacy HTML scrape — often 403 now; kept as last resort."""
    try:
        resp = requests.get(f"{BASE_URL}/community/outlook", timeout=8, headers=HEADERS)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        logger.debug("MyFXBook outlook scrape unavailable: %s", exc)
        return {}

    symbols: dict[str, dict[str, Any]] = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
        if header[:5] != ["Symbol", "Action", "Percentage", "Volume", "Positions"]:
            continue

        cells = [c.get_text(" ", strip=True) for c in rows[1].find_all("td")]
        if len(cells) < 5 or not re.fullmatch(r"[A-Z0-9]{6,8}", cells[0]):
            continue

        short_pct = _parse_pct(cells[2]) or 0
        long_pct = 0.0
        long_vol = 0.0
        long_pos = 0
        if len(rows) > 2:
            long_cells = [c.get_text(" ", strip=True) for c in rows[2].find_all("td")]
            if len(long_cells) >= 4 and long_cells[0].lower() == "long":
                long_pct = _parse_pct(long_cells[1]) or 0
                long_vol = _parse_lots(long_cells[2]) or 0
                long_pos = _parse_positions(long_cells[3]) or 0

        symbols[cells[0]] = _normalize_symbol_row({
            "name": cells[0],
            "shortPercentage": short_pct,
            "longPercentage": long_pct,
            "shortVolume": _parse_lots(cells[3]) or 0,
            "longVolume": long_vol,
            "shortPositions": _parse_positions(cells[4]) or 0,
            "longPositions": long_pos,
        })
    return symbols


def fetch_community_outlook(symbol: str | None = None) -> dict[str, Any]:
    """Community positioning for one or all symbols (API preferred)."""
    cache_key = f"outlook:{symbol or 'all'}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    symbols = _fetch_outlook_api()
    source = "api"
    if not symbols:
        symbols = _fetch_outlook_scrape()
        source = "scrape" if symbols else "none"

    result: dict[str, Any] = {
        "symbols": symbols,
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not symbols:
        result["error"] = "MyFXBook community outlook unavailable (HTML blocked; login API failed)"

    if symbol:
        sym = symbol.upper()
        result = {
            "symbol": sym,
            "available": sym in symbols,
            "data": symbols.get(sym),
            "source": source,
            "updated_at": result["updated_at"],
            "error": result.get("error") if sym not in symbols else None,
        }

    _set_cache(cache_key, result)
    return result


def _scrape_all_fxbook_headlines() -> list[dict[str, Any]]:
    """Scrape MyFXBook news page — often 403; returns empty on failure."""
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
        # Expected often — HTML endpoints return 403; RSS elsewhere covers news.
        logger.debug("MyFXBook news scrape skipped: %s", exc)

    _set_cache("news:all", articles)
    return articles


def fetch_fxbook_news(limit: int = 20, asset_id: str = "eurusd") -> list[dict[str, Any]]:
    """Filter cached MyFXBook headlines for the active asset (may be empty if 403)."""
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
        "outlook_source": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if not symbol:
        stats["crowd_reason"] = "MyFXBook crowd data not available for crypto"
        return stats

    outlook = fetch_community_outlook(symbol)
    data = outlook.get("data")
    stats["outlook"] = data
    stats["outlook_source"] = outlook.get("source")

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
    elif outlook.get("error"):
        stats["crowd_reason"] = outlook["error"]

    return stats
