"""Near-real-time quotes from public APIs (no keys).

The dashboard used Yahoo 1H candle closes, which lag and — for gold — track
COMEX futures (GC=F) instead of XM-style XAUUSD spot. Live price prefers:

  EUR/USD  Binance EURUSDT, then Yahoo 1-minute
  Gold     gold-api.com spot XAU, then Binance PAXG, then Yahoo GC=F 1m
  Bitcoin  Binance BTCUSDT, then Coinbase, then Yahoo 1-minute
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from data.assets import get_asset

logger = logging.getLogger(__name__)

QUOTE_TTL = 4.0
HTTP_TIMEOUT = 6.0
_UA = "Mozilla/5.0 (compatible; ProTrader/1.0; +https://pro-trader.onrender.com)"

_session = requests.Session()
_session.headers.update({"User-Agent": _UA, "Accept": "application/json"})

_cache: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_json(url: str, timeout: float = HTTP_TIMEOUT) -> Any:
    resp = _session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _binance_24hr(symbol: str) -> dict[str, Any] | None:
    data = _get_json(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}")
    price = float(data["lastPrice"])
    change = float(data.get("priceChange") or 0)
    pct = float(data.get("priceChangePercent") or 0)
    return {"price": price, "change": change, "change_pct": pct, "source": f"binance:{symbol}"}


def _coinbase_spot(pair: str) -> dict[str, Any] | None:
    data = _get_json(f"https://api.coinbase.com/v2/prices/{pair}/spot")
    price = float((data.get("data") or {})["amount"])
    return {"price": price, "change": 0.0, "change_pct": 0.0, "source": f"coinbase:{pair}"}


def _gold_api_spot() -> dict[str, Any] | None:
    data = _get_json("https://api.gold-api.com/price/XAU")
    price = float(data["price"])
    out: dict[str, Any] = {
        "price": price,
        "change": 0.0,
        "change_pct": 0.0,
        "source": "gold-api:XAU",
    }
    # Pair with PAXG 24h % so the header still shows a day move
    try:
        paxg = _binance_24hr("PAXGUSDT")
        if paxg:
            pct = float(paxg["change_pct"])
            out["change_pct"] = round(pct, 2)
            out["change"] = round(price * pct / 100.0, 2)
    except Exception as exc:
        logger.debug("PAXG 24h for gold change skipped: %s", exc)
    return out


def _yahoo_1m(symbol: str) -> dict[str, Any] | None:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?interval=1m&range=1d&includePrePost=true"
    )
    payload = _get_json(url)
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return None
    row = result[0]
    meta = row.get("meta") or {}
    price = meta.get("regularMarketPrice")
    closes = ((row.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    for val in reversed(closes):
        if val is not None:
            price = val
            break
    if price is None:
        return None
    price = float(price)
    prev = meta.get("previousClose") or meta.get("chartPreviousClose") or price
    prev = float(prev)
    change = price - prev
    pct = (change / prev * 100) if prev else 0.0
    return {
        "price": price,
        "change": change,
        "change_pct": pct,
        "source": f"yahoo1m:{symbol}",
    }


_SOURCES: dict[str, list[Callable[[], dict[str, Any] | None]]] = {
    "eurusd": [
        lambda: _binance_24hr("EURUSDT"),
        lambda: _yahoo_1m("EURUSD=X"),
    ],
    "gold": [
        _gold_api_spot,
        lambda: _binance_24hr("PAXGUSDT"),
        lambda: _yahoo_1m("GC=F"),
    ],
    "bitcoin": [
        lambda: _binance_24hr("BTCUSDT"),
        lambda: _coinbase_spot("BTC-USD"),
        lambda: _yahoo_1m("BTC-USD"),
    ],
}


def _finalize(asset_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    asset = get_asset(asset_id)
    decimals = asset["decimals"]
    price = round(float(raw["price"]), decimals)
    change = round(float(raw.get("change") or 0), decimals)
    pct = round(float(raw.get("change_pct") or 0), 2)
    return {
        "symbol": asset["name"],
        "asset_id": asset["id"],
        "price": price,
        "change": change,
        "change_pct": pct,
        "source": raw.get("source") or "live",
        "timestamp": _now_iso(),
    }


def fetch_spot_quote(asset_id: str, ttl: float = QUOTE_TTL) -> dict[str, Any] | None:
    """Return a fresh (or briefly cached) live quote, or None if every source fails."""
    key = (asset_id or "").lower()
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit["fetched_at"] < ttl:
            return dict(hit["quote"])

    sources = _SOURCES.get(key) or []
    last_err: Exception | None = None
    for fetch in sources:
        try:
            raw = fetch()
            if not raw or not raw.get("price"):
                continue
            quote = _finalize(key, raw)
            with _lock:
                _cache[key] = {"quote": quote, "fetched_at": time.time()}
            return dict(quote)
        except Exception as exc:
            last_err = exc
            logger.debug("Live quote %s source failed: %s", key, exc)
    if last_err:
        logger.warning("All live quote sources failed for %s: %s", key, last_err)
    return None
