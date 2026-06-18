"""Candlestick pattern recognition with volume confirmation."""

from __future__ import annotations

import pandas as pd


def _body(row) -> float:
    return abs(row.close - row.open)


def _range(row) -> float:
    return row.high - row.low


def _is_bullish(row) -> bool:
    return row.close > row.open


def _is_bearish(row) -> bool:
    return row.close < row.open


def _vol_confirmed(df: pd.DataFrame, idx: int = -1) -> bool:
    if "volume" not in df.columns or "volume_sma_20" not in df.columns:
        return False
    row = df.iloc[idx]
    vol = float(row.get("volume", 0))
    sma = float(row.get("volume_sma_20", 0))
    return sma > 0 and vol >= sma * 1.15


def _add(patterns: list, name: str, ptype: str, bias: str, strength: str, desc: str, vol_ok: bool):
    patterns.append({
        "name": name,
        "type": ptype,
        "bias": bias,
        "strength": "strong" if vol_ok and strength == "strong" else strength,
        "volume_confirmed": vol_ok,
        "description": desc + (" (volume confirmed)" if vol_ok else ""),
    })


def detect_patterns(df: pd.DataFrame) -> list[dict]:
    if len(df) < 3:
        return []

    patterns: list[dict] = []
    c0 = df.iloc[-1]
    c1 = df.iloc[-2]
    c2 = df.iloc[-3]
    vol0 = _vol_confirmed(df, -1)

    body0 = _body(c0)
    body1 = _body(c1)
    range0 = _range(c0) or 1e-8
    range1 = _range(c1) or 1e-8
    upper_wick0 = c0.high - max(c0.open, c0.close)
    lower_wick0 = min(c0.open, c0.close) - c0.low

    # Doji family
    if body0 / range0 < 0.1:
        if lower_wick0 > upper_wick0 * 2:
            _add(patterns, "Dragonfly Doji", "reversal", "bullish", "strong",
                 "Dragonfly doji - strong bullish rejection", vol0)
        elif upper_wick0 > lower_wick0 * 2:
            _add(patterns, "Gravestone Doji", "reversal", "bearish", "strong",
                 "Gravestone doji - strong bearish rejection", vol0)
        else:
            _add(patterns, "Doji", "reversal", "neutral", "medium",
                 "Indecision - potential reversal at key levels", vol0)

    # Spinning top
    if 0.1 <= body0 / range0 <= 0.3 and upper_wick0 > body0 and lower_wick0 > body0:
        _add(patterns, "Spinning Top", "reversal", "neutral", "weak",
             "Indecision spinning top", vol0)

    # Marubozu
    if body0 / range0 > 0.9:
        if _is_bullish(c0):
            _add(patterns, "Bullish Marubozu", "continuation", "bullish", "strong",
                 "Full bullish body - strong momentum", vol0)
        else:
            _add(patterns, "Bearish Marubozu", "continuation", "bearish", "strong",
                 "Full bearish body - strong momentum", vol0)

    # Hammer / Hanging Man
    if lower_wick0 > body0 * 2 and upper_wick0 < body0 * 0.5:
        if _is_bearish(c1) or c1.close < c1.open:
            _add(patterns, "Hammer", "reversal", "bullish", "strong",
                 "Bullish hammer - buyers rejecting lower prices", vol0)
        else:
            _add(patterns, "Hanging Man", "reversal", "bearish", "medium",
                 "Hanging man at top - potential bearish reversal", vol0)

    # Shooting Star / Inverted Hammer
    if upper_wick0 > body0 * 2 and lower_wick0 < body0 * 0.5:
        if _is_bullish(c1):
            _add(patterns, "Shooting Star", "reversal", "bearish", "strong",
                 "Shooting star - sellers rejecting higher prices", vol0)
        else:
            _add(patterns, "Inverted Hammer", "reversal", "bullish", "medium",
                 "Inverted hammer - potential bullish reversal", vol0)

    # Engulfing
    if _is_bearish(c1) and _is_bullish(c0) and c0.close > c1.open and c0.open < c1.close:
        _add(patterns, "Bullish Engulfing", "reversal", "bullish", "strong",
             "Bullish engulfing - strong buyer momentum", vol0)

    if _is_bullish(c1) and _is_bearish(c0) and c0.close < c1.open and c0.open > c1.close:
        _add(patterns, "Bearish Engulfing", "reversal", "bearish", "strong",
             "Bearish engulfing - strong seller momentum", vol0)

    # Harami
    if _is_bearish(c1) and _is_bullish(c0) and body0 < body1 * 0.5:
        if c0.high < c1.open and c0.low > c1.close:
            _add(patterns, "Bullish Harami", "reversal", "bullish", "medium",
                 "Bullish harami - selling pressure fading", vol0)

    if _is_bullish(c1) and _is_bearish(c0) and body0 < body1 * 0.5:
        if c0.high < c1.close and c0.low > c1.open:
            _add(patterns, "Bearish Harami", "reversal", "bearish", "medium",
                 "Bearish harami - buying pressure fading", vol0)

    # Piercing Line / Dark Cloud Cover
    if _is_bearish(c1) and _is_bullish(c0):
        midpoint = (c1.open + c1.close) / 2
        if c0.open < c1.low and c0.close > midpoint and c0.close < c1.open:
            _add(patterns, "Piercing Line", "reversal", "bullish", "strong",
                 "Piercing line - bullish reversal", vol0)

    if _is_bullish(c1) and _is_bearish(c0):
        midpoint = (c1.open + c1.close) / 2
        if c0.open > c1.high and c0.close < midpoint and c0.close > c1.open:
            _add(patterns, "Dark Cloud Cover", "reversal", "bearish", "strong",
                 "Dark cloud cover - bearish reversal", vol0)

    # Tweezer tops/bottoms
    if abs(c0.high - c1.high) / range0 < 0.05 and _is_bullish(c1) and _is_bearish(c0):
        _add(patterns, "Tweezer Top", "reversal", "bearish", "medium",
             "Tweezer top - double rejection at highs", vol0)

    if abs(c0.low - c1.low) / range0 < 0.05 and _is_bearish(c1) and _is_bullish(c0):
        _add(patterns, "Tweezer Bottom", "reversal", "bullish", "medium",
             "Tweezer bottom - double rejection at lows", vol0)

    # Morning / Evening Star
    if _is_bearish(c2) and body1 / (_range(c1) or 1e-8) < 0.3 and _is_bullish(c0):
        if c0.close > (c2.open + c2.close) / 2:
            _add(patterns, "Morning Star", "reversal", "bullish", "strong",
                 "Morning star - three-candle bullish reversal", vol0)

    if _is_bullish(c2) and body1 / (_range(c1) or 1e-8) < 0.3 and _is_bearish(c0):
        if c0.close < (c2.open + c2.close) / 2:
            _add(patterns, "Evening Star", "reversal", "bearish", "strong",
                 "Evening star - three-candle bearish reversal", vol0)

    # Three soldiers / crows
    if all(_is_bullish(df.iloc[i]) for i in (-3, -2, -1)):
        if c1.close > c2.close and c0.close > c1.close:
            _add(patterns, "Three White Soldiers", "continuation", "bullish", "strong",
                 "Three white soldiers - sustained bullish momentum", vol0)

    if all(_is_bearish(df.iloc[i]) for i in (-3, -2, -1)):
        if c1.close < c2.close and c0.close < c1.close:
            _add(patterns, "Three Black Crows", "continuation", "bearish", "strong",
                 "Three black crows - sustained bearish momentum", vol0)

    # Rising / Falling three methods (simplified 5-candle)
    if len(df) >= 5:
        c3 = df.iloc[-4]
        c4 = df.iloc[-5]
        if all(_is_bullish(df.iloc[i]) for i in (-5, -1)) and _is_bearish(c3) and _is_bearish(c2):
            if c3.high < c4.close and c2.high < c4.close:
                _add(patterns, "Rising Three Methods", "continuation", "bullish", "strong",
                     "Rising three methods - bullish continuation", vol0)

        if all(_is_bearish(df.iloc[i]) for i in (-5, -1)) and _is_bullish(c3) and _is_bullish(c2):
            if c3.low > c4.close and c2.low > c4.close:
                _add(patterns, "Falling Three Methods", "continuation", "bearish", "strong",
                     "Falling three methods - bearish continuation", vol0)

    return patterns


def pattern_bias_score(patterns: list[dict]) -> int:
    score = 0
    weights = {"strong": 2, "medium": 1, "weak": 1}
    for p in patterns:
        w = weights.get(p.get("strength", "medium"), 1)
        if p.get("volume_confirmed"):
            w += 1
        if p.get("bias") == "bullish":
            score += w
        elif p.get("bias") == "bearish":
            score -= w
    return score