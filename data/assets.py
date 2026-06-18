"""Asset definitions for multi-market trading dashboard."""

from __future__ import annotations

import re

ASSETS: dict[str, dict] = {
    "eurusd": {
        "id": "eurusd",
        "name": "EUR/USD",
        "title": "EUR/USD Pro Trader",
        "yahoo_symbol": "EURUSD=X",
        "route": "/",
        "decimals": 5,
        "price_tolerance": 0.0015,
        "min_sl_distance": 0.0010,
        "near_level_distance": 0.003,
        "level_buffer": 0.0003,
        "chart_tick_format": ".5f",
        "show_agent": True,
        "news_title": "Forex News",
        "calendar_title": "Economic Calendar",
        "calendar_empty": "No upcoming EUR/USD events",
        "news_keywords": re.compile(
            r"eur[\s/\-]?usd|euro|ecb|european central|fed|fomc|dollar|greenback|"
            r"eurozone|inflation|rate.?decision|nfp|payroll|cpi|gdp|pmi",
            re.IGNORECASE,
        ),
        "calendar_currencies": {"USD", "EUR"},
    },
    "gold": {
        "id": "gold",
        "name": "Gold",
        "title": "Gold Pro Trader",
        "yahoo_symbol": "GC=F",
        "route": "/gold",
        "decimals": 2,
        "price_tolerance": 3.0,
        "min_sl_distance": 8.0,
        "near_level_distance": 15.0,
        "level_buffer": 2.0,
        "chart_tick_format": ".2f",
        "show_agent": False,
        "news_title": "Gold & Commodities News",
        "calendar_title": "Macro Calendar",
        "calendar_empty": "No upcoming gold-relevant events",
        "news_keywords": re.compile(
            r"gold|xau|bullion|precious.?metal|comex|fed|fomc|dollar|inflation|"
            r"cpi|rate.?decision|treasury|yield|geopolit|safe.?haven|opec",
            re.IGNORECASE,
        ),
        "calendar_currencies": {"USD"},
    },
    "bitcoin": {
        "id": "bitcoin",
        "name": "Bitcoin",
        "title": "Bitcoin Pro Trader",
        "yahoo_symbol": "BTC-USD",
        "route": "/bitcoin",
        "decimals": 2,
        "price_tolerance": 250.0,
        "min_sl_distance": 150.0,
        "near_level_distance": 800.0,
        "level_buffer": 100.0,
        "chart_tick_format": ".2f",
        "show_agent": False,
        "news_title": "Crypto News",
        "calendar_title": "Macro Calendar",
        "calendar_empty": "No upcoming macro events",
        "news_keywords": re.compile(
            r"bitcoin|btc|crypto|cryptocurrency|blockchain|ethereum|eth|"
            r"sec|etf|halving|binance|coinbase|defi|stablecoin|satoshi",
            re.IGNORECASE,
        ),
        "calendar_currencies": {"USD"},
    },
}

DEFAULT_ASSET = "eurusd"


def get_asset(asset_id: str | None) -> dict:
    key = (asset_id or DEFAULT_ASSET).lower()
    if key not in ASSETS:
        raise ValueError(f"Unknown asset: {asset_id}")
    return ASSETS[key]


def list_assets() -> list[dict]:
    return list(ASSETS.values())