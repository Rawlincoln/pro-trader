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


def _vol_confirmed(df: pd.DataFrame, idx: int) -> bool:
    if idx < 0:
        idx = len(df) + idx
    if "volume" not in df.columns or "volume_sma_20" not in df.columns:
        return False
    row = df.iloc[idx]
    vol = float(row.get("volume", 0))
    sma = float(row.get("volume_sma_20", 0))
    return sma > 0 and vol >= sma * 1.15


def _add(
    patterns: list,
    name: str,
    ptype: str,
    bias: str,
    strength: str,
    desc: str,
    vol_ok: bool,
    df: pd.DataFrame,
    mark_idx: int,
):
    row = df.iloc[mark_idx]
    ts = df.index[mark_idx]
    time_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

    if bias == "bullish":
        price = float(row.low)
        marker_position = "below"
    elif bias == "bearish":
        price = float(row.high)
        marker_position = "above"
    else:
        price = float(row.close)
        marker_position = "above"

    patterns.append({
        "name": name,
        "type": ptype,
        "bias": bias,
        "strength": "strong" if vol_ok and strength == "strong" else strength,
        "volume_confirmed": vol_ok,
        "description": desc + (" (volume confirmed)" if vol_ok else ""),
        "candle_index": mark_idx,
        "time": time_str,
        "price": price,
        "marker_position": marker_position,
    })


def _detect_at(df: pd.DataFrame, i: int) -> list[dict]:
    if i < 2 or i >= len(df):
        return []

    patterns: list[dict] = []
    c0 = df.iloc[i]
    c1 = df.iloc[i - 1]
    c2 = df.iloc[i - 2]
    vol0 = _vol_confirmed(df, i)

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
                 "Dragonfly doji - strong bullish rejection", vol0, df, i)
        elif upper_wick0 > lower_wick0 * 2:
            _add(patterns, "Gravestone Doji", "reversal", "bearish", "strong",
                 "Gravestone doji - strong bearish rejection", vol0, df, i)
        else:
            _add(patterns, "Doji", "reversal", "neutral", "medium",
                 "Indecision - potential reversal at key levels", vol0, df, i)

    # Spinning top
    if 0.1 <= body0 / range0 <= 0.3 and upper_wick0 > body0 and lower_wick0 > body0:
        _add(patterns, "Spinning Top", "reversal", "neutral", "weak",
             "Indecision spinning top", vol0, df, i)

    # Marubozu
    if body0 / range0 > 0.9:
        if _is_bullish(c0):
            _add(patterns, "Bullish Marubozu", "continuation", "bullish", "strong",
                 "Full bullish body - strong momentum", vol0, df, i)
        else:
            _add(patterns, "Bearish Marubozu", "continuation", "bearish", "strong",
                 "Full bearish body - strong momentum", vol0, df, i)

    # Hammer / Hanging Man
    if lower_wick0 > body0 * 2 and upper_wick0 < body0 * 0.5:
        if _is_bearish(c1) or c1.close < c1.open:
            _add(patterns, "Hammer", "reversal", "bullish", "strong",
                 "Bullish hammer - buyers rejecting lower prices", vol0, df, i)
        else:
            _add(patterns, "Hanging Man", "reversal", "bearish", "medium",
                 "Hanging man at top - potential bearish reversal", vol0, df, i)

    # Shooting Star / Inverted Hammer
    if upper_wick0 > body0 * 2 and lower_wick0 < body0 * 0.5:
        if _is_bullish(c1):
            _add(patterns, "Shooting Star", "reversal", "bearish", "strong",
                 "Shooting star - sellers rejecting higher prices", vol0, df, i)
        else:
            _add(patterns, "Inverted Hammer", "reversal", "bullish", "medium",
                 "Inverted hammer - potential bullish reversal", vol0, df, i)

    # Engulfing
    if _is_bearish(c1) and _is_bullish(c0) and c0.close > c1.open and c0.open < c1.close:
        _add(patterns, "Bullish Engulfing", "reversal", "bullish", "strong",
             "Bullish engulfing - strong buyer momentum", vol0, df, i)

    if _is_bullish(c1) and _is_bearish(c0) and c0.close < c1.open and c0.open > c1.close:
        _add(patterns, "Bearish Engulfing", "reversal", "bearish", "strong",
             "Bearish engulfing - strong seller momentum", vol0, df, i)

    # Harami
    if _is_bearish(c1) and _is_bullish(c0) and body0 < body1 * 0.5:
        if c0.high < c1.open and c0.low > c1.close:
            _add(patterns, "Bullish Harami", "reversal", "bullish", "medium",
                 "Bullish harami - selling pressure fading", vol0, df, i)

    if _is_bullish(c1) and _is_bearish(c0) and body0 < body1 * 0.5:
        if c0.high < c1.close and c0.low > c1.open:
            _add(patterns, "Bearish Harami", "reversal", "bearish", "medium",
                 "Bearish harami - buying pressure fading", vol0, df, i)

    # Piercing Line / Dark Cloud Cover
    if _is_bearish(c1) and _is_bullish(c0):
        midpoint = (c1.open + c1.close) / 2
        if c0.open < c1.low and c0.close > midpoint and c0.close < c1.open:
            _add(patterns, "Piercing Line", "reversal", "bullish", "strong",
                 "Piercing line - bullish reversal", vol0, df, i)

    if _is_bullish(c1) and _is_bearish(c0):
        midpoint = (c1.open + c1.close) / 2
        if c0.open > c1.high and c0.close < midpoint and c0.close > c1.open:
            _add(patterns, "Dark Cloud Cover", "reversal", "bearish", "strong",
                 "Dark cloud cover - bearish reversal", vol0, df, i)

    # Tweezer tops/bottoms
    if abs(c0.high - c1.high) / range0 < 0.05 and _is_bullish(c1) and _is_bearish(c0):
        _add(patterns, "Tweezer Top", "reversal", "bearish", "medium",
             "Tweezer top - double rejection at highs", vol0, df, i)

    if abs(c0.low - c1.low) / range0 < 0.05 and _is_bearish(c1) and _is_bullish(c0):
        _add(patterns, "Tweezer Bottom", "reversal", "bullish", "medium",
             "Tweezer bottom - double rejection at lows", vol0, df, i)

    # Morning / Evening Star
    if _is_bearish(c2) and body1 / (_range(c1) or 1e-8) < 0.3 and _is_bullish(c0):
        if c0.close > (c2.open + c2.close) / 2:
            _add(patterns, "Morning Star", "reversal", "bullish", "strong",
                 "Morning star - three-candle bullish reversal", vol0, df, i)

    if _is_bullish(c2) and body1 / (_range(c1) or 1e-8) < 0.3 and _is_bearish(c0):
        if c0.close < (c2.open + c2.close) / 2:
            _add(patterns, "Evening Star", "reversal", "bearish", "strong",
                 "Evening star - three-candle bearish reversal", vol0, df, i)

    # Three soldiers / crows
    if all(_is_bullish(df.iloc[j]) for j in (i - 2, i - 1, i)):
        if c1.close > c2.close and c0.close > c1.close:
            _add(patterns, "Three White Soldiers", "continuation", "bullish", "strong",
                 "Three white soldiers - sustained bullish momentum", vol0, df, i)

    if all(_is_bearish(df.iloc[j]) for j in (i - 2, i - 1, i)):
        if c1.close < c2.close and c0.close < c1.close:
            _add(patterns, "Three Black Crows", "continuation", "bearish", "strong",
                 "Three black crows - sustained bearish momentum", vol0, df, i)

    # Rising / Falling three methods (simplified 5-candle)
    if i >= 4:
        c3 = df.iloc[i - 3]
        c4 = df.iloc[i - 4]
        if all(_is_bullish(df.iloc[j]) for j in (i - 4, i)) and _is_bearish(c3) and _is_bearish(c2):
            if c3.high < c4.close and c2.high < c4.close:
                _add(patterns, "Rising Three Methods", "continuation", "bullish", "strong",
                     "Rising three methods - bullish continuation", vol0, df, i)

        if all(_is_bearish(df.iloc[j]) for j in (i - 4, i)) and _is_bullish(c3) and _is_bullish(c2):
            if c3.low > c4.close and c2.low > c4.close:
                _add(patterns, "Falling Three Methods", "continuation", "bearish", "strong",
                     "Falling three methods - bearish continuation", vol0, df, i)

    return patterns


def detect_patterns(df: pd.DataFrame) -> list[dict]:
    """Detect patterns on the latest candle (used for signal scoring)."""
    if len(df) < 3:
        return []
    return _detect_at(df, len(df) - 1)


def pick_primary_pattern(
    patterns: list[dict],
    bias: str = "neutral",
    signal: str = "WAIT",
) -> dict | None:
    """Return the single pattern most relevant to the current prediction."""
    if not patterns:
        return None

    target_bias: str | None = None
    if signal == "BUY":
        target_bias = "bullish"
    elif signal == "SELL":
        target_bias = "bearish"
    elif bias in ("bullish", "bearish"):
        target_bias = bias

    strength_weight = {"strong": 3, "medium": 2, "weak": 1}

    def relevance(p: dict) -> float:
        score = float(strength_weight.get(p.get("strength", "medium"), 1))
        if p.get("volume_confirmed"):
            score += 1.5
        p_bias = p.get("bias")
        if target_bias:
            if p_bias == target_bias:
                score += 6
            elif p_bias == "neutral":
                score += 1
            else:
                score -= 4
        return score

    return max(patterns, key=relevance)


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