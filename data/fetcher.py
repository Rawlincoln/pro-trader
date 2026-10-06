"""Multi-asset price data fetcher using Yahoo Finance + live quote APIs."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from data.assets import get_asset
from data.live_quotes import fetch_spot_quote

logger = logging.getLogger(__name__)

TIMEFRAMES = {
    "1h": {"interval": "1h", "period": "60d"},
    "4h": {"interval": "1h", "period": "90d"},
}

_ohlc_cache: dict[str, dict] = {}
OHLC_TTL = 60


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]
    required = ["open", "high", "low", "close"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing column: {col}")

    cols = required.copy()
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        cols.append("volume")
    else:
        df["volume"] = (df["high"] - df["low"]).abs() * 1_000_000

    df = df[cols].dropna(subset=required)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    if df_1h.empty:
        return df_1h
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df_1h.columns:
        agg["volume"] = "sum"
    return df_1h.resample("4h", label="right", closed="right").agg(agg).dropna()


def _load_ohlc_1h(asset_id: str) -> pd.DataFrame:
    cache_key = f"{asset_id}:1h"
    entry = _ohlc_cache.get(cache_key)
    if entry and time.time() - entry["fetched_at"] < OHLC_TTL:
        return entry["data"].copy()

    asset = get_asset(asset_id)
    cfg = TIMEFRAMES["1h"]
    ticker = yf.Ticker(asset["yahoo_symbol"])
    df = ticker.history(period=cfg["period"], interval=cfg["interval"], auto_adjust=False)
    df = _normalize(df).tail(500)
    _ohlc_cache[cache_key] = {"data": df, "fetched_at": time.time()}
    return df.copy()


def fetch_ohlc(timeframe: str, asset_id: str = "eurusd") -> pd.DataFrame:
    if timeframe == "1h":
        return _load_ohlc_1h(asset_id)
    if timeframe == "4h":
        return resample_to_4h(_load_ohlc_1h(asset_id)).tail(500)

    cfg = TIMEFRAMES.get(timeframe)
    if not cfg:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    asset = get_asset(asset_id)
    ticker = yf.Ticker(asset["yahoo_symbol"])
    df = ticker.history(period=cfg["period"], interval=cfg["interval"], auto_adjust=False)
    return _normalize(df).tail(500)


def _align_ohlc_to_live(df: pd.DataFrame, live_price: float, asset_id: str) -> pd.DataFrame:
    """Mark the working candle to the live print.

    Gold Yahoo bars are COMEX futures (often $20–40 above XM spot). When the
    gap is large, shift the whole series so charts match the live spot quote.
    """
    if df is None or df.empty or not live_price:
        return df
    last = float(df["close"].iloc[-1])
    if last <= 0:
        return df
    out = df.copy()
    gap = float(live_price) - last
    asset = get_asset(asset_id)
    # Gold: futures vs spot. Others: only retouch the last bar.
    shift_all = asset_id == "gold" and abs(gap) > max(float(asset["price_tolerance"]) * 3, 8.0)
    if shift_all:
        for col in ("open", "high", "low", "close"):
            out[col] = out[col] + gap
    idx = out.index[-1]
    close = float(live_price)
    out.at[idx, "close"] = close
    out.at[idx, "high"] = max(float(out.at[idx, "high"]), close)
    out.at[idx, "low"] = min(float(out.at[idx, "low"]), close)
    return out


def fetch_ohlc_bundle(asset_id: str = "eurusd") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch 1H once and derive 4H — avoids duplicate Yahoo calls."""
    df_1h = _load_ohlc_1h(asset_id)
    live = fetch_spot_quote(asset_id)
    if live and live.get("price"):
        df_1h = _align_ohlc_to_live(df_1h, float(live["price"]), asset_id)
    df_4h = resample_to_4h(df_1h).tail(500)
    return df_1h, df_4h


def _quote_from_ohlc(asset_id: str, df_1h: pd.DataFrame) -> dict[str, Any]:
    asset = get_asset(asset_id)
    decimals = asset["decimals"]
    info: dict[str, Any] = {
        "symbol": asset["name"],
        "asset_id": asset["id"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "yahoo-1h",
    }
    if len(df_1h) >= 2:
        prev = float(df_1h["close"].iloc[-2])
        curr = float(df_1h["close"].iloc[-1])
        info["change"] = round(curr - prev, decimals)
        info["change_pct"] = round((curr - prev) / prev * 100, 2) if prev else 0.0
        info["price"] = round(curr, decimals)
    elif not df_1h.empty:
        curr = float(df_1h["close"].iloc[-1])
        info["price"] = round(curr, decimals)
        info["change"] = 0.0
        info["change_pct"] = 0.0
    else:
        try:
            fast = yf.Ticker(asset["yahoo_symbol"]).fast_info
            price = getattr(fast, "last_price", None) or getattr(fast, "lastPrice", None)
            if price:
                info["price"] = round(float(price), decimals)
                info["change"] = 0.0
                info["change_pct"] = 0.0
                info["source"] = "yahoo-fast"
        except Exception:
            pass
    return info


def fetch_live_quote(
    asset_id: str = "eurusd",
    df_1h: pd.DataFrame | None = None,
) -> dict[str, Any]:
    live = fetch_spot_quote(asset_id)
    if live and live.get("price"):
        return live

    asset = get_asset(asset_id)
    if df_1h is None:
        try:
            df_1h = _load_ohlc_1h(asset_id)
        except Exception as exc:
            logger.warning("OHLC fallback quote failed for %s: %s", asset_id, exc)
            df_1h = pd.DataFrame()
    return _quote_from_ohlc(asset["id"], df_1h if df_1h is not None else pd.DataFrame())


def ohlc_to_chart(df: pd.DataFrame, limit: int = 120, asset_id: str = "eurusd") -> list[dict]:
    asset = get_asset(asset_id)
    decimals = asset["decimals"]
    subset = df.tail(limit)
    result = []
    for idx, row in subset.iterrows():
        point = {
            "time": idx.isoformat(),
            "open": round(float(row.open), decimals),
            "high": round(float(row.high), decimals),
            "low": round(float(row.low), decimals),
            "close": round(float(row.close), decimals),
        }
        if "volume" in subset.columns:
            point["volume"] = round(float(row.volume), 0)
        result.append(point)
    return result