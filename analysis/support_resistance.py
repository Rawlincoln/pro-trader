"""Support and resistance with Fibonacci, volume nodes, and strength scoring."""

from __future__ import annotations

import numpy as np
import pandas as pd


def find_swing_points(df: pd.DataFrame, window: int = 5) -> tuple[list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []

    if len(df) < window * 2 + 1:
        return highs, lows

    for i in range(window, len(df) - window):
        segment_high = df["high"].iloc[i - window : i + window + 1]
        segment_low = df["low"].iloc[i - window : i + window + 1]
        if df["high"].iloc[i] == segment_high.max():
            highs.append(float(df["high"].iloc[i]))
        if df["low"].iloc[i] == segment_low.min():
            lows.append(float(df["low"].iloc[i]))

    return highs, lows


def cluster_levels(levels: list[float], tolerance: float = 0.0015, decimals: int = 5) -> list[float]:
    if not levels:
        return []

    sorted_levels = sorted(levels)
    clusters: list[list[float]] = [[sorted_levels[0]]]

    for level in sorted_levels[1:]:
        if abs(level - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(level)
        else:
            clusters.append([level])

    return [round(float(np.mean(c)), decimals) for c in clusters]


def pivot_points(df: pd.DataFrame, decimals: int = 5) -> dict[str, float]:
    if df.empty:
        return {}
    row = df.iloc[-1]
    pivot = (row.high + row.low + row.close) / 3
    r1 = 2 * pivot - row.low
    s1 = 2 * pivot - row.high
    r2 = pivot + (row.high - row.low)
    s2 = pivot - (row.high - row.low)
    r3 = row.high + 2 * (pivot - row.low)
    s3 = row.low - 2 * (row.high - pivot)
    return {
        "pivot": round(float(pivot), decimals),
        "r1": round(float(r1), decimals),
        "r2": round(float(r2), decimals),
        "r3": round(float(r3), decimals),
        "s1": round(float(s1), decimals),
        "s2": round(float(s2), decimals),
        "s3": round(float(s3), decimals),
    }


def fibonacci_levels(df: pd.DataFrame, lookback: int = 50, decimals: int = 5) -> dict[str, float]:
    if len(df) < lookback:
        return {}
    segment = df.tail(lookback)
    swing_high = float(segment["high"].max())
    swing_low = float(segment["low"].min())
    diff = swing_high - swing_low
    if diff <= 0:
        return {}

    levels = {
        "fib_0": round(swing_low, decimals),
        "fib_236": round(swing_high - diff * 0.236, decimals),
        "fib_382": round(swing_high - diff * 0.382, decimals),
        "fib_500": round(swing_high - diff * 0.5, decimals),
        "fib_618": round(swing_high - diff * 0.618, decimals),
        "fib_786": round(swing_high - diff * 0.786, decimals),
        "fib_100": round(swing_high, decimals),
        "swing_high": round(swing_high, decimals),
        "swing_low": round(swing_low, decimals),
    }
    return levels


def volume_nodes(df: pd.DataFrame, bins: int = 20, decimals: int = 5) -> list[dict]:
    if "volume" not in df.columns or len(df) < 20:
        return []

    segment = df.tail(80).copy()
    price_min = segment["low"].min()
    price_max = segment["high"].max()
    if price_max <= price_min:
        return []

    segment["mid"] = (segment["high"] + segment["low"]) / 2
    segment["bin"] = pd.cut(segment["mid"], bins=bins, labels=False)

    nodes = (
        segment.groupby("bin", observed=True)
        .agg(vol=("volume", "sum"), price=("mid", "mean"))
        .sort_values("vol", ascending=False)
        .head(5)
    )

    return [
        {"price": round(float(row.price), decimals), "volume": round(float(row.vol), 0)}
        for _, row in nodes.iterrows()
    ]


def psychological_levels(price: float, asset: dict | None = None) -> list[float]:
    decimals = asset.get("decimals", 5) if asset else 5
    if price <= 0:
        return []

    if decimals >= 4:  # forex
        step = 0.0050
        base = round(price / step) * step
        return [round(base + i * step, decimals) for i in (-2, -1, 0, 1, 2)]

    if price > 10000:  # bitcoin
        step = 500.0
        base = round(price / step) * step
        return [round(base + i * step, decimals) for i in (-2, -1, 0, 1, 2)]

    # gold
    step = 10.0
    base = round(price / step) * step
    return [round(base + i * step, decimals) for i in (-2, -1, 0, 1, 2)]


def _level_strength(touches: int, has_volume: bool) -> str:
    if touches >= 3 and has_volume:
        return "very_strong"
    if touches >= 2 or has_volume:
        return "strong"
    return "moderate"


def compute_levels(df: pd.DataFrame, asset: dict | None = None) -> dict:
    tolerance = asset.get("price_tolerance", 0.0015) if asset else 0.0015
    decimals = asset.get("decimals", 5) if asset else 5
    near_dist = asset.get("near_level_distance", 0.002) if asset else 0.002

    highs, lows = find_swing_points(df, window=5)
    highs_wide, lows_wide = find_swing_points(df, window=10)

    all_resistance = cluster_levels(highs + highs_wide, tolerance, decimals)
    all_support = cluster_levels(lows + lows_wide, tolerance, decimals)

    resistance = all_resistance[-6:]
    support = all_support[:6]

    pivots = pivot_points(df.tail(1), decimals)
    fib = fibonacci_levels(df, decimals=decimals)
    vol_nodes = volume_nodes(df, decimals=decimals)
    price = float(df["close"].iloc[-1])
    psych = psychological_levels(price, asset)

    # Merge fib levels into S/R pool
    fib_prices = [v for k, v in fib.items() if k.startswith("fib_")]
    for fp in fib_prices:
        if fp < price and fp not in support:
            support.append(fp)
        elif fp > price and fp not in resistance:
            resistance.append(fp)
    support = sorted(set(support))
    resistance = sorted(set(resistance))

    nearest_support = None
    nearest_resistance = None
    for s in reversed(support):
        if s < price:
            nearest_support = s
            break
    for r in resistance:
        if r > price:
            nearest_resistance = r
            break

    # Count touches for strength
    def count_touches(level: float) -> int:
        touches = 0
        for _, row in df.tail(100).iterrows():
            if abs(row["high"] - level) <= tolerance or abs(row["low"] - level) <= tolerance:
                touches += 1
        return touches

    vol_prices = {n["price"] for n in vol_nodes}
    support_strength = _level_strength(
        count_touches(nearest_support) if nearest_support else 0,
        nearest_support in vol_prices if nearest_support else False,
    )
    resistance_strength = _level_strength(
        count_touches(nearest_resistance) if nearest_resistance else 0,
        nearest_resistance in vol_prices if nearest_resistance else False,
    )

    return {
        "support": support[-5:],
        "resistance": resistance[:5],
        "pivots": pivots,
        "fibonacci": fib,
        "volume_nodes": vol_nodes,
        "psychological": psych,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "support_strength": support_strength,
        "resistance_strength": resistance_strength,
        "price_position": _price_position(price, nearest_support, nearest_resistance, near_dist),
    }


def _price_position(
    price: float,
    support: float | None,
    resistance: float | None,
    near_dist: float = 0.002,
) -> str:
    if support and resistance:
        mid = (support + resistance) / 2
        if price > mid + (resistance - mid) * 0.6:
            return "near_resistance"
        if price < mid - (mid - support) * 0.6:
            return "near_support"
        return "mid_range"
    if support and price - support < near_dist:
        return "near_support"
    if resistance and resistance - price < near_dist:
        return "near_resistance"
    return "unknown"


def score_levels(levels: dict, bias: str) -> int:
    """Score S/R context for strategy."""
    score = 0
    pos = levels.get("price_position", "unknown")

    if bias == "bullish":
        if pos == "near_support":
            score += 2
        elif pos == "near_resistance":
            score -= 2
        if levels.get("support_strength") in ("strong", "very_strong"):
            score += 1
    elif bias == "bearish":
        if pos == "near_resistance":
            score += 2
        elif pos == "near_support":
            score -= 2
        if levels.get("resistance_strength") in ("strong", "very_strong"):
            score += 1

    return score