"""Multi-asset price data fetcher using Yahoo Finance."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from data.assets import get_asset

logger = logging.getLogger(__name__)

TIMEFRAMES = {
    "1h": {"interval": "1h", "period": "60d"},
    "4h": {"interval": "1h", "period": "730d"},
}


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
        # Synthetic volume from range when broker feed lacks it (e.g. some FX)
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


def fetch_ohlc(timeframe: str, asset_id: str = "eurusd") -> pd.DataFrame:
    asset = get_asset(asset_id)
    cfg = TIMEFRAMES.get(timeframe)
    if not cfg:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    ticker = yf.Ticker(asset["yahoo_symbol"])
    df = ticker.history(period=cfg["period"], interval=cfg["interval"], auto_adjust=False)
    df = _normalize(df)

    if timeframe == "4h":
        df = resample_to_4h(df)

    return df.tail(500)


def fetch_live_quote(asset_id: str = "eurusd") -> dict[str, Any]:
    asset = get_asset(asset_id)
    ticker = yf.Ticker(asset["yahoo_symbol"])
    decimals = asset["decimals"]
    info: dict[str, Any] = {}

    try:
        fast = ticker.fast_info
        price = getattr(fast, "last_price", None) or getattr(fast, "lastPrice", None)
        if price:
            info["price"] = round(float(price), decimals)
    except Exception:
        pass

    if "price" not in info:
        df = fetch_ohlc("1h", asset_id)
        if not df.empty:
            info["price"] = round(float(df["close"].iloc[-1]), decimals)

    df_1h = fetch_ohlc("1h", asset_id)
    if len(df_1h) >= 2:
        prev = float(df_1h["close"].iloc[-2])
        curr = float(df_1h["close"].iloc[-1])
        info["change"] = round(curr - prev, decimals)
        info["change_pct"] = round((curr - prev) / prev * 100, 2) if prev else 0.0
        info["price"] = round(curr, decimals)
    elif "price" in info:
        info.setdefault("change", 0.0)
        info.setdefault("change_pct", 0.0)

    info["symbol"] = asset["name"]
    info["asset_id"] = asset["id"]
    info["timestamp"] = datetime.now(timezone.utc).isoformat()
    return info


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