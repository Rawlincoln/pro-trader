"""Multi-timeframe strategy with expanded indicator confluence."""

from __future__ import annotations

from typing import Any

import pandas as pd

from analysis.indicators import add_all_indicators, indicator_snapshot, trend_from_emas
from analysis.patterns import detect_patterns, pattern_bias_score
from analysis.support_resistance import compute_levels, score_levels


def _score_trend(trend: str) -> int:
    return {
        "strong_bullish": 3, "bullish": 2, "neutral": 0,
        "bearish": -2, "strong_bearish": -3,
    }.get(trend, 0)


def _score_oscillator(signal: str, bullish_vals: tuple, bearish_vals: tuple) -> int:
    if signal in bullish_vals:
        return 2 if "over" not in signal else -1
    if signal in bearish_vals:
        return -2 if "over" not in signal else 1
    if signal == "bullish":
        return 1
    if signal == "bearish":
        return -1
    return 0


def _score_macd(macd_cross: str | None, histogram: float | None) -> int:
    score = 0
    if macd_cross == "bullish_cross":
        score += 2
    elif macd_cross == "bearish_cross":
        score -= 2
    if histogram is not None:
        score += 1 if histogram > 0 else -1
    return score


def _score_adx(adx_signal: str) -> int:
    return {
        "strong_bullish": 2, "bullish": 1, "strong_bearish": -2,
        "bearish": -1, "no_trend": 0,
    }.get(adx_signal, 0)


def _score_volume(ind: dict, bias: str) -> int:
    score = 0
    vol_conf = ind.get("volume_confirmation")
    obv = ind.get("obv_trend")
    ratio = ind.get("volume_ratio", 1.0)

    if bias == "bullish":
        if vol_conf == "bullish":
            score += 2
        elif vol_conf == "weak":
            score -= 1
        if obv == "bullish":
            score += 1
        elif obv == "bearish":
            score -= 1
    elif bias == "bearish":
        if vol_conf == "bearish":
            score += 2
        elif vol_conf == "weak":
            score -= 1
        if obv == "bearish":
            score += 1
        elif obv == "bullish":
            score -= 1

    if ratio >= 1.5:
        score += 1 if bias == "bullish" else (-1 if bias == "bearish" else 0)

    return score


def _score_ichimoku(signal: str) -> int:
    return {"bullish": 1, "bearish": -1}.get(signal, 0)


def _score_ema_cross(cross: str | None) -> int:
    if cross == "golden_cross":
        return 2
    if cross == "death_cross":
        return -2
    return 0


def analyze_timeframe(df: pd.DataFrame, label: str, asset: dict | None = None) -> dict[str, Any]:
    enriched = add_all_indicators(df)
    snapshot = indicator_snapshot(enriched, asset)
    levels = compute_levels(enriched, asset)
    patterns = detect_patterns(enriched)

    trend = snapshot.get("trend", "neutral")
    preliminary_bias = "bullish" if _score_trend(trend) > 0 else "bearish" if _score_trend(trend) < 0 else "neutral"

    trend_score = _score_trend(trend)
    rsi_score = _score_oscillator(snapshot.get("rsi_signal", "neutral"), ("oversold", "bullish"), ("overbought", "bearish"))
    macd_score = _score_macd(snapshot.get("macd_cross"), snapshot.get("macd_histogram"))
    stoch_score = 0
    sk, sd = snapshot.get("stoch_k"), snapshot.get("stoch_d")
    if sk and sd:
        if sk < 20 and sd < 20:
            stoch_score = 2
        elif sk > 80 and sd > 80:
            stoch_score = -2
        elif sk > sd:
            stoch_score = 1
        else:
            stoch_score = -1

    cci_score = _score_oscillator(snapshot.get("cci_signal", "neutral"), ("oversold", "bullish"), ("overbought", "bearish"))
    wr_score = _score_oscillator(snapshot.get("williams_signal", "neutral"), ("oversold", "bullish"), ("overbought", "bearish"))
    mfi_score = _score_oscillator(snapshot.get("mfi_signal", "neutral"), ("oversold", "bullish"), ("overbought", "bearish"))
    adx_score = _score_adx(snapshot.get("adx_signal", "no_trend"))
    ichimoku_score = _score_ichimoku(snapshot.get("ichimoku_signal", "neutral"))
    ema_cross_score = _score_ema_cross(snapshot.get("ema_cross"))
    pattern_score = pattern_bias_score(patterns)

    # Volume scored against preliminary trend direction
    vol_score = _score_volume(snapshot, preliminary_bias if preliminary_bias != "neutral" else "bullish")

    sr_score = score_levels(levels, preliminary_bias if preliminary_bias != "neutral" else "bullish")

    total = (
        trend_score + rsi_score + macd_score + stoch_score +
        cci_score + wr_score + mfi_score + adx_score +
        ichimoku_score + ema_cross_score + pattern_score +
        vol_score + sr_score
    )

    if total >= 6:
        bias = "bullish"
    elif total <= -6:
        bias = "bearish"
    elif total >= 3:
        bias = "bullish"
    elif total <= -3:
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "timeframe": label,
        "bias": bias,
        "score": total,
        "trend": trend,
        "indicators": snapshot,
        "levels": levels,
        "patterns": patterns,
        "breakdown": {
            "trend": trend_score,
            "rsi": rsi_score,
            "macd": macd_score,
            "stochastic": stoch_score,
            "cci": cci_score,
            "williams_r": wr_score,
            "mfi": mfi_score,
            "adx": adx_score,
            "ichimoku": ichimoku_score,
            "ema_cross": ema_cross_score,
            "patterns": pattern_score,
            "volume": vol_score,
            "support_resistance": sr_score,
        },
        "confluence_count": _count_confluence(snapshot, patterns, levels, bias),
    }


def _count_confluence(snapshot: dict, patterns: list, levels: dict, bias: str) -> int:
    count = 0
    checks = []

    if bias == "bullish":
        checks = [
            snapshot.get("trend") in ("bullish", "strong_bullish"),
            snapshot.get("rsi_signal") in ("oversold", "bullish"),
            snapshot.get("macd_cross") == "bullish_cross" or (snapshot.get("macd_histogram") or 0) > 0,
            snapshot.get("adx_signal") in ("bullish", "strong_bullish"),
            snapshot.get("volume_confirmation") == "bullish",
            snapshot.get("obv_trend") == "bullish",
            snapshot.get("ichimoku_signal") == "bullish",
            levels.get("price_position") == "near_support",
            any(p["bias"] == "bullish" for p in patterns),
        ]
    elif bias == "bearish":
        checks = [
            snapshot.get("trend") in ("bearish", "strong_bearish"),
            snapshot.get("rsi_signal") in ("overbought", "bearish"),
            snapshot.get("macd_cross") == "bearish_cross" or (snapshot.get("macd_histogram") or 0) < 0,
            snapshot.get("adx_signal") in ("bearish", "strong_bearish"),
            snapshot.get("volume_confirmation") == "bearish",
            snapshot.get("obv_trend") == "bearish",
            snapshot.get("ichimoku_signal") == "bearish",
            levels.get("price_position") == "near_resistance",
            any(p["bias"] == "bearish" for p in patterns),
        ]

    count = sum(1 for c in checks if c)
    return count


def combine_timeframes(analysis_4h: dict, analysis_1h: dict) -> dict[str, Any]:
    score_4h = analysis_4h["score"]
    score_1h = analysis_1h["score"]
    conf_4h = analysis_4h.get("confluence_count", 0)
    conf_1h = analysis_1h.get("confluence_count", 0)

    combined_score = round(score_4h * 0.6 + score_1h * 0.4, 2)
    combined_confluence = round(conf_4h * 0.6 + conf_1h * 0.4, 1)

    bias_4h = analysis_4h["bias"]
    bias_1h = analysis_1h["bias"]
    aligned = bias_4h == bias_1h and bias_4h != "neutral"
    conflict = (
        (bias_4h == "bullish" and bias_1h == "bearish")
        or (bias_4h == "bearish" and bias_1h == "bullish")
    )

    if conflict:
        signal = "WAIT"
        confidence = 25
    elif aligned and combined_score >= 5 and combined_confluence >= 4:
        signal = "BUY"
        confidence = min(97, 65 + combined_score * 2 + combined_confluence * 2)
    elif aligned and combined_score <= -5 and combined_confluence >= 4:
        signal = "SELL"
        confidence = min(97, 65 + abs(combined_score) * 2 + combined_confluence * 2)
    elif aligned and combined_score >= 4:
        signal = "BUY"
        confidence = min(90, 58 + combined_score * 2.5)
    elif aligned and combined_score <= -4:
        signal = "SELL"
        confidence = min(90, 58 + abs(combined_score) * 2.5)
    elif combined_score >= 4 and combined_confluence >= 3:
        signal = "BUY"
        confidence = 55 + combined_score * 2 + combined_confluence
    elif combined_score <= -4 and combined_confluence >= 3:
        signal = "SELL"
        confidence = 55 + abs(combined_score) * 2 + combined_confluence
    elif combined_score >= 3:
        signal = "BUY"
        confidence = 48 + combined_score * 2
    elif combined_score <= -3:
        signal = "SELL"
        confidence = 48 + abs(combined_score) * 2
    else:
        signal = "WAIT"
        confidence = 35

    return {
        "combined_score": combined_score,
        "confluence": combined_confluence,
        "signal": signal,
        "confidence": round(confidence, 1),
        "timeframes_aligned": aligned,
        "timeframes_conflict": conflict,
        "primary_trend": analysis_4h["trend"],
        "entry_timeframe": analysis_1h["bias"],
    }


def apply_fundamental_adjustment(
    technical: dict,
    news_sentiment: dict,
    calendar_risk: dict,
    asset: dict | None = None,
) -> dict:
    adj_score = technical["combined_score"]
    notes: list[str] = []
    asset_name = asset.get("name", "market") if asset else "market"

    news_score = news_sentiment.get("score", 0)
    if news_score >= 2:
        adj_score += 1.5
        notes.append(f"News sentiment is bullish for {asset_name}")
    elif news_score <= -2:
        adj_score -= 1.5
        notes.append(f"News sentiment is bearish for {asset_name}")

    if calendar_risk.get("risk_level") == "high":
        notes.append("HIGH IMPACT events ahead - reduce position size or wait")
        technical = {**technical, "confidence": max(25, technical["confidence"] - 20)}

    signal = technical["signal"]
    conf = technical["confidence"]

    if adj_score >= 5 and technical.get("confluence", 0) >= 4:
        signal = "BUY"
        conf = min(97, conf + 3)
    elif adj_score <= -5 and technical.get("confluence", 0) >= 4:
        signal = "SELL"
        conf = min(97, conf + 3)
    elif adj_score >= 4:
        signal = "BUY" if signal != "SELL" else signal
    elif adj_score <= -4:
        signal = "SELL" if signal != "BUY" else signal
    elif abs(adj_score) < 3 or technical.get("confluence", 0) < 2:
        signal = "WAIT"
        conf = max(30, conf - 10)

    return {
        **technical,
        "adjusted_score": round(adj_score, 2),
        "fundamental_notes": notes,
        "signal": signal,
        "confidence": round(conf, 1),
    }