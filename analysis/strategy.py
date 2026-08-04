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
    """Stricter multi-TF rules: require 1H+4H alignment for actionable signals."""
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

    reasons: list[str] = []
    signal = "WAIT"
    confidence = 35.0

    if conflict:
        confidence = 22
        reasons.append("1H and 4H conflict — stay flat")
    elif not aligned:
        confidence = 32
        reasons.append("Timeframes not aligned — wait for 1H to match 4H")
        # Only exceptional single-TF impulse with strong confluence
        if combined_score >= 8 and combined_confluence >= 6 and bias_4h == "bullish":
            signal = "BUY"
            confidence = min(72, 52 + combined_score)
            reasons.append("Strong 4H impulse without full alignment (reduced confidence)")
        elif combined_score <= -8 and combined_confluence >= 6 and bias_4h == "bearish":
            signal = "SELL"
            confidence = min(72, 52 + abs(combined_score))
            reasons.append("Strong 4H impulse without full alignment (reduced confidence)")
    elif aligned and combined_score >= 6 and combined_confluence >= 5:
        signal = "BUY"
        confidence = min(97, 70 + combined_score * 1.5 + combined_confluence * 1.5)
        reasons.append("Strong aligned bullish setup")
    elif aligned and combined_score <= -6 and combined_confluence >= 5:
        signal = "SELL"
        confidence = min(97, 70 + abs(combined_score) * 1.5 + combined_confluence * 1.5)
        reasons.append("Strong aligned bearish setup")
    elif aligned and combined_score >= 5 and combined_confluence >= 4:
        signal = "BUY"
        confidence = min(88, 62 + combined_score * 2)
        reasons.append("Aligned bullish — solid confluence")
    elif aligned and combined_score <= -5 and combined_confluence >= 4:
        signal = "SELL"
        confidence = min(88, 62 + abs(combined_score) * 2)
        reasons.append("Aligned bearish — solid confluence")
    elif aligned and abs(combined_score) >= 4:
        # Mild alignment: signal only if confluence supports, lower confidence
        if combined_score >= 4 and combined_confluence >= 4:
            signal = "BUY"
            confidence = min(75, 55 + combined_score * 2)
            reasons.append("Mild aligned bullish — use tighter risk")
        elif combined_score <= -4 and combined_confluence >= 4:
            signal = "SELL"
            confidence = min(75, 55 + abs(combined_score) * 2)
            reasons.append("Mild aligned bearish — use tighter risk")
        else:
            reasons.append("Aligned but weak confluence — wait for confirmation")
            confidence = 40
    else:
        reasons.append("Score below threshold — no high-probability setup")
        confidence = 35

    # Floor: never issue BUY/SELL below 58 technical confidence
    if signal in ("BUY", "SELL") and confidence < 58:
        reasons.append(f"Confidence {confidence:.0f}% too low — forced WAIT")
        signal = "WAIT"
        confidence = max(30, confidence - 5)

    return {
        "combined_score": combined_score,
        "confluence": combined_confluence,
        "signal": signal,
        "confidence": round(confidence, 1),
        "timeframes_aligned": aligned,
        "timeframes_conflict": conflict,
        "primary_trend": analysis_4h["trend"],
        "entry_timeframe": analysis_1h["bias"],
        "signal_reasons": reasons,
    }


def apply_fundamental_adjustment(
    technical: dict,
    news_sentiment: dict,
    calendar_risk: dict,
    asset: dict | None = None,
) -> dict:
    """Adjust confidence/notes; do not invent weak directional signals from news alone."""
    adj_score = technical["combined_score"]
    notes: list[str] = list(technical.get("signal_reasons") or [])
    asset_name = asset.get("name", "market") if asset else "market"

    news_score = news_sentiment.get("score", 0)
    if news_score >= 2:
        adj_score += 1.0
        notes.append(f"News sentiment is bullish for {asset_name}")
    elif news_score <= -2:
        adj_score -= 1.0
        notes.append(f"News sentiment is bearish for {asset_name}")

    signal = technical["signal"]
    conf = float(technical["confidence"])

    if calendar_risk.get("risk_level") == "high":
        notes.append("HIGH IMPACT events ahead — reduce size or wait")
        conf = max(25, conf - 18)
        if conf < 62 and signal in ("BUY", "SELL"):
            notes.append("High-impact calendar — signal demoted to WAIT")
            signal = "WAIT"

    # News may reinforce or slightly boost an existing technical signal
    if signal == "BUY" and news_score >= 2 and technical.get("confluence", 0) >= 4:
        conf = min(97, conf + 4)
    elif signal == "SELL" and news_score <= -2 and technical.get("confluence", 0) >= 4:
        conf = min(97, conf + 4)
    # News alone must not create a trade unless technical was already strong
    elif signal == "WAIT" and abs(adj_score) >= 7 and technical.get("confluence", 0) >= 5:
        if adj_score >= 7 and news_score >= 3:
            signal = "BUY"
            conf = min(68, 58 + abs(adj_score))
            notes.append("News + strong score unlocked cautious BUY")
        elif adj_score <= -7 and news_score <= -3:
            signal = "SELL"
            conf = min(68, 58 + abs(adj_score))
            notes.append("News + strong score unlocked cautious SELL")

    # Final gate: weak confluence → wait
    if signal in ("BUY", "SELL") and technical.get("confluence", 0) < 3:
        notes.append("Confluence too low — WAIT")
        signal = "WAIT"
        conf = max(30, conf - 10)

    if signal in ("BUY", "SELL") and conf < 58:
        notes.append("Final confidence floor — WAIT")
        signal = "WAIT"

    return {
        **technical,
        "adjusted_score": round(adj_score, 2),
        "fundamental_notes": notes,
        "signal": signal,
        "confidence": round(conf, 1),
    }


def apply_user_level_boost(
    technical: dict,
    user_sr: dict | None,
    asset_id: str = "eurusd",
) -> dict:
    """Boost / gate signals using trader-drawn 4H (and 1H) S/R levels.

    EUR/USD in particular is treated as highly level-respectful: prefer
    entries only when price is interacting with user-drawn structure.
    """
    if not user_sr or not user_sr.get("has_drawings"):
        return technical

    signal = technical.get("signal", "WAIT")
    conf = float(technical.get("confidence", 0))
    notes = list(technical.get("fundamental_notes") or [])
    near_s = bool(user_sr.get("near_user_support"))
    near_r = bool(user_sr.get("near_user_resistance"))
    count_4h = int(user_sr.get("count_4h") or 0)

    notes.append(
        f"Your drawings active: {count_4h}×4H + {user_sr.get('count_1h', 0)}×1H levels"
    )

    # Align with structure
    if signal == "BUY" and near_s:
        conf = min(97, conf + (10 if asset_id == "eurusd" else 7))
        notes.append("Price at your drawn support — BUY confluence boosted")
    elif signal == "SELL" and near_r:
        conf = min(97, conf + (10 if asset_id == "eurusd" else 7))
        notes.append("Price at your drawn resistance — SELL confluence boosted")
    elif signal == "BUY" and near_r:
        # Buying into resistance — reduce conviction
        conf = max(30, conf - 12)
        notes.append("BUY into your drawn resistance — confidence cut; wait for break/retest")
        if conf < 62:
            signal = "WAIT"
            notes.append("Demoted to WAIT — do not long into your resistance")
    elif signal == "SELL" and near_s:
        conf = max(30, conf - 12)
        notes.append("SELL into your drawn support — confidence cut; wait for break/retest")
        if conf < 62:
            signal = "WAIT"
            notes.append("Demoted to WAIT — do not short into your support")

    # EUR/USD: if 4H drawings exist and price is mid-range (not near a user level),
    # prefer WAIT unless confidence is already very high
    if asset_id == "eurusd" and count_4h >= 1 and signal in ("BUY", "SELL"):
        if not near_s and not near_r and conf < 78:
            notes.append(
                "EUR/USD mid-range vs your 4H levels — wait for price to tag a drawn line"
            )
            signal = "WAIT"
            conf = max(35, conf - 8)

    # Allow a cautious bounce/rejection signal when technical was WAIT but price
    # is sitting on a user level with decent 4H bias alignment
    if signal == "WAIT" and (near_s or near_r):
        bias = technical.get("entry_timeframe") or technical.get("primary_trend") or ""
        if near_s and "bull" in str(bias):
            signal = "BUY"
            conf = max(conf, 60)
            notes.append("Bounce setup at your support + bullish 1H bias")
        elif near_r and "bear" in str(bias):
            signal = "SELL"
            conf = max(conf, 60)
            notes.append("Rejection setup at your resistance + bearish 1H bias")

    if signal in ("BUY", "SELL") and conf < 58:
        signal = "WAIT"

    return {
        **technical,
        "signal": signal,
        "confidence": round(conf, 1),
        "fundamental_notes": notes,
        "user_level_boost": True,
    }