"""
A+ 3-step price-action framework (bias → liquidity → swing failure).

Inspired by the common institutional-flow day-trading model:
1. Daily / HTF bias from structure + candle relationships
2. Liquidity pools (PDH/PDL, session H/L, equal highs/lows)
3. Swing Failure Pattern (SFP) at those pools for entries

Educational signal enhancement — not financial advice.
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Optional

import pandas as pd

from data.assets import get_asset


def resample_to_daily(df_1h: pd.DataFrame) -> pd.DataFrame:
    if df_1h is None or df_1h.empty:
        return pd.DataFrame()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df_1h.columns:
        agg["volume"] = "sum"
    return df_1h.resample("1D", label="left", closed="left").agg(agg).dropna()


def _candle_relation(prev: pd.Series, cur: pd.Series) -> dict[str, Any]:
    """Classify the relationship between two consecutive candles."""
    hh = float(cur["high"]) > float(prev["high"])
    hl = float(cur["low"]) > float(prev["low"])
    lh = float(cur["high"]) < float(prev["high"])
    ll = float(cur["low"]) < float(prev["low"])
    close_above_prev_high = float(cur["close"]) > float(prev["high"])
    close_below_prev_low = float(cur["close"]) < float(prev["low"])
    bullish_body = float(cur["close"]) > float(cur["open"])
    bearish_body = float(cur["close"]) < float(cur["open"])
    outside = float(cur["high"]) > float(prev["high"]) and float(cur["low"]) < float(prev["low"])
    inside = float(cur["high"]) <= float(prev["high"]) and float(cur["low"]) >= float(prev["low"])
    body = abs(float(cur["close"]) - float(cur["open"]))
    range_ = max(float(cur["high"]) - float(cur["low"]), 1e-12)
    body_ratio = body / range_

    pattern = "neutral"
    strength = "neutral"
    direction = "neutral"

    if inside:
        pattern = "inside"
        strength = "neutral"
        direction = "neutral"
    elif outside and bullish_body and body_ratio >= 0.35:
        pattern = "outside_bullish"
        strength = "strong"
        direction = "bullish"
    elif outside and bearish_body and body_ratio >= 0.35:
        pattern = "outside_bearish"
        strength = "strong"
        direction = "bearish"
    elif hh and hl and close_above_prev_high:
        pattern = "hh_hl_close_above"
        strength = "strong"
        direction = "bullish"
    elif hh and hl and not close_above_prev_high:
        pattern = "hh_hl_weak_close"
        strength = "weak"
        direction = "bullish"
    elif lh and ll and close_below_prev_low:
        pattern = "lh_ll_close_below"
        strength = "strong"
        direction = "bearish"
    elif lh and ll and not close_below_prev_low:
        pattern = "lh_ll_weak_close"
        strength = "weak"
        direction = "bearish"
    elif hh and hl and bearish_body:
        pattern = "caution_bullish_reject"
        strength = "caution"
        direction = "bearish"  # early warning of turn
    elif lh and ll and bullish_body:
        pattern = "caution_bearish_reject"
        strength = "caution"
        direction = "bullish"
    elif outside:
        # Outside but indecisive body
        pattern = "outside_indecisive"
        strength = "weak"
        direction = "bullish" if bullish_body else ("bearish" if bearish_body else "neutral")

    return {
        "pattern": pattern,
        "strength": strength,
        "direction": direction,
        "hh": hh,
        "hl": hl,
        "lh": lh,
        "ll": ll,
        "outside": outside,
        "inside": inside,
        "close_above_prev_high": close_above_prev_high,
        "close_below_prev_low": close_below_prev_low,
        "body_ratio": round(body_ratio, 3),
    }


def compute_htf_bias(df: pd.DataFrame, label: str = "daily") -> dict[str, Any]:
    """
    Form directional lean from last 2 completed HTF candles.
    Returns bullish / bearish / neutral with strength strong|weak|caution|neutral.
    """
    if df is None or len(df) < 3:
        return {
            "bias": "neutral",
            "strength": "neutral",
            "label": label,
            "conviction": 0,
            "pattern": "insufficient_data",
            "reason": f"Need more {label} candles for bias",
            "invalidation": None,
            "questions": {},
        }

    # Use last two *completed* candles (exclude forming bar when possible)
    completed = df.iloc[:-1] if len(df) >= 4 else df
    if len(completed) < 2:
        completed = df
    prev = completed.iloc[-2]
    cur = completed.iloc[-1]
    rel = _candle_relation(prev, cur)

    bias = rel["direction"] if rel["direction"] != "neutral" else "neutral"
    strength = rel["strength"]
    conviction = {
        "strong": 85,
        "weak": 62,
        "caution": 45,
        "neutral": 30,
    }.get(strength, 40)
    if bias == "neutral":
        conviction = 28

    # Invalidation: below last swing low (bull) / above last swing high (bear)
    invalidation = None
    if bias == "bullish":
        invalidation = round(float(cur["low"]), 8)
    elif bias == "bearish":
        invalidation = round(float(cur["high"]), 8)

    q1 = (
        f"{label}: {rel['pattern']} → {bias} ({strength})"
    )
    reasons = [q1]
    if rel["inside"]:
        reasons.append("Inside candle — sit out until a break / SFP at extremes")
    if strength == "caution":
        reasons.append("Caution candle — prefer reversal/retracement, low continuation conviction")

    return {
        "bias": bias,
        "strength": strength,
        "label": label,
        "conviction": conviction,
        "pattern": rel["pattern"],
        "reason": "; ".join(reasons),
        "invalidation": invalidation,
        "candle": {
            "open": float(cur["open"]),
            "high": float(cur["high"]),
            "low": float(cur["low"]),
            "close": float(cur["close"]),
        },
        "relation": rel,
        "questions": {
            "structure": q1,
            "untouched_target": None,  # filled after liquidity
            "invalidation": f"Bias dead if price holds beyond {invalidation}" if invalidation else None,
        },
    }


def _session_bounds(ts: pd.Timestamp) -> str:
    """Rough FX session name for a UTC timestamp."""
    h = ts.tz_convert("UTC").hour if ts.tzinfo else ts.hour
    if 0 <= h < 7:
        return "asia"
    if 7 <= h < 12:
        return "london"
    if 12 <= h < 21:
        return "newyork"
    return "asia"


def compute_liquidity_pools(
    df_1h: pd.DataFrame,
    df_daily: pd.DataFrame,
    asset_id: str = "eurusd",
) -> dict[str, Any]:
    """Mark PDH/PDL, session highs/lows, and equal highs/lows (liquidity)."""
    asset = get_asset(asset_id)
    decimals = asset["decimals"]
    tol = asset["price_tolerance"]

    pools: list[dict[str, Any]] = []
    pdh = pdl = None
    pdh_taken = pdl_taken = False
    price = float(df_1h["close"].iloc[-1]) if df_1h is not None and len(df_1h) else 0.0

    if df_daily is not None and len(df_daily) >= 2:
        # Previous completed daily candle
        prev_d = df_daily.iloc[-2]
        pdh = round(float(prev_d["high"]), decimals)
        pdl = round(float(prev_d["low"]), decimals)
        # Has today's range already taken them?
        today = df_daily.iloc[-1]
        pdh_taken = float(today["high"]) >= pdh - tol * 0.1
        pdl_taken = float(today["low"]) <= pdl + tol * 0.1
        pools.append({
            "kind": "pdh",
            "side": "buy_stops",
            "price": pdh,
            "taken": pdh_taken,
            "label": "Previous Daily High",
        })
        pools.append({
            "kind": "pdl",
            "side": "sell_stops",
            "price": pdl,
            "taken": pdl_taken,
            "label": "Previous Daily Low",
        })

    # Session highs/lows from last ~48h of 1H bars
    session_levels: dict[str, dict[str, float]] = {}
    if df_1h is not None and len(df_1h) >= 12:
        recent = df_1h.tail(72).copy()
        recent["session"] = [_session_bounds(ts) for ts in recent.index]
        # Group by date+session for the most recent complete-ish sessions
        for sess in ("asia", "london", "newyork"):
            part = recent[recent["session"] == sess]
            if part.empty:
                continue
            # Last contiguous block of this session
            sh = round(float(part["high"].max()), decimals)
            sl = round(float(part["low"].min()), decimals)
            session_levels[sess] = {"high": sh, "low": sl}
            pools.append({
                "kind": f"{sess}_high",
                "side": "buy_stops",
                "price": sh,
                "taken": price >= sh - tol,
                "label": f"{sess.title()} session high",
            })
            pools.append({
                "kind": f"{sess}_low",
                "side": "sell_stops",
                "price": sl,
                "taken": price <= sl + tol,
                "label": f"{sess.title()} session low",
            })

    # Equal highs / equal lows from swing points on 1H
    equal_highs: list[float] = []
    equal_lows: list[float] = []
    if df_1h is not None and len(df_1h) >= 30:
        from analysis.support_resistance import find_swing_points, cluster_levels
        highs, lows = find_swing_points(df_1h.tail(120), window=3)
        # Cluster — levels that appear as near-duplicates = equal H/L
        ch = cluster_levels(highs, tolerance=tol, decimals=decimals)
        cl = cluster_levels(lows, tolerance=tol, decimals=decimals)
        # Equal = cluster formed from 2+ raw swings near same price
        for level in ch:
            near = sum(1 for h in highs if abs(h - level) <= tol)
            if near >= 2:
                equal_highs.append(level)
                pools.append({
                    "kind": "equal_high",
                    "side": "buy_stops",
                    "price": level,
                    "taken": price >= level - tol * 0.25,
                    "label": "Equal highs (liquidity)",
                })
        for level in cl:
            near = sum(1 for x in lows if abs(x - level) <= tol)
            if near >= 2:
                equal_lows.append(level)
                pools.append({
                    "kind": "equal_low",
                    "side": "sell_stops",
                    "price": level,
                    "taken": price <= level + tol * 0.25,
                    "label": "Equal lows (liquidity)",
                })

    # Prefer untouched pools as targets
    bullish_targets = [
        p for p in pools
        if p["side"] == "buy_stops" and not p.get("taken") and p["kind"] in ("pdh", "equal_high", "london_high", "newyork_high", "asia_high")
    ]
    bearish_targets = [
        p for p in pools
        if p["side"] == "sell_stops" and not p.get("taken") and p["kind"] in ("pdl", "equal_low", "london_low", "newyork_low", "asia_low")
    ]
    # Sort: PDH/PDL first, then equal, then session
    rank = {"pdh": 0, "pdl": 0, "equal_high": 1, "equal_low": 1}
    bullish_targets.sort(key=lambda p: (rank.get(p["kind"], 2), abs(p["price"] - price)))
    bearish_targets.sort(key=lambda p: (rank.get(p["kind"], 2), abs(p["price"] - price)))

    return {
        "pdh": pdh,
        "pdl": pdl,
        "pdh_taken": pdh_taken,
        "pdl_taken": pdl_taken,
        "session_levels": session_levels,
        "equal_highs": equal_highs[:5],
        "equal_lows": equal_lows[:5],
        "pools": pools,
        "bullish_targets": bullish_targets[:4],
        "bearish_targets": bearish_targets[:4],
        "price": price,
        "note": (
            "Professionals hunt stops above highs / below lows. "
            "Default bullish target = untouched PDH; bearish = untouched PDL."
        ),
    }


def _find_recent_swing_high(df: pd.DataFrame, lookback: int = 20, window: int = 2) -> Optional[dict]:
    if df is None or len(df) < window * 2 + 3:
        return None
    segment = df.tail(lookback + window)
    # Prefer swing before the last 1-2 bars (so we can detect a raid)
    end = len(segment) - 2
    start = window
    best = None
    for i in range(start, end):
        hi = float(segment["high"].iloc[i])
        left = segment["high"].iloc[i - window : i]
        right = segment["high"].iloc[i + 1 : i + 1 + window]
        if hi >= float(left.max()) and hi >= float(right.max()):
            best = {
                "price": hi,
                "index": segment.index[i],
                "iloc": i,
            }
    return best


def _find_recent_swing_low(df: pd.DataFrame, lookback: int = 20, window: int = 2) -> Optional[dict]:
    if df is None or len(df) < window * 2 + 3:
        return None
    segment = df.tail(lookback + window)
    end = len(segment) - 2
    start = window
    best = None
    for i in range(start, end):
        lo = float(segment["low"].iloc[i])
        left = segment["low"].iloc[i - window : i]
        right = segment["low"].iloc[i + 1 : i + 1 + window]
        if lo <= float(left.min()) and lo <= float(right.min()):
            best = {
                "price": lo,
                "index": segment.index[i],
                "iloc": i,
            }
    return best


def detect_swing_failure(
    df: pd.DataFrame,
    liquidity: dict[str, Any],
    bias: dict[str, Any],
    asset_id: str = "eurusd",
) -> dict[str, Any]:
    """
    Swing Failure Pattern: raid a meaningful swing / liquidity pool, then close back inside.
    Bearish SFP: take out high, close back below → short with bias.
    Bullish SFP: take out low, close back above → long with bias.
    """
    asset = get_asset(asset_id)
    decimals = asset["decimals"]
    tol = asset["price_tolerance"]

    empty = {
        "detected": False,
        "side": None,
        "quality": None,
        "level": None,
        "level_kind": None,
        "entry": None,
        "stop": None,
        "target": None,
        "reason": "No swing failure at a key pool",
        "aligned_with_bias": False,
    }
    if df is None or len(df) < 15:
        return empty

    candle = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(candle["close"])
    high = float(candle["high"])
    low = float(candle["low"])

    # Meaningful levels to raid
    key_highs: list[tuple[float, str]] = []
    key_lows: list[tuple[float, str]] = []
    if liquidity.get("pdh"):
        key_highs.append((float(liquidity["pdh"]), "pdh"))
    if liquidity.get("pdl"):
        key_lows.append((float(liquidity["pdl"]), "pdl"))
    for p in liquidity.get("pools") or []:
        if p["side"] == "buy_stops" and p["kind"] != "pdh":
            key_highs.append((float(p["price"]), p["kind"]))
        if p["side"] == "sell_stops" and p["kind"] != "pdl":
            key_lows.append((float(p["price"]), p["kind"]))

    swing_h = _find_recent_swing_high(df)
    swing_l = _find_recent_swing_low(df)
    if swing_h:
        key_highs.append((float(swing_h["price"]), "swing_high"))
    if swing_l:
        key_lows.append((float(swing_l["price"]), "swing_low"))

    bias_dir = (bias or {}).get("bias") or "neutral"
    candidates: list[dict[str, Any]] = []

    # Bearish SFP: wick above level, close back below
    for level, kind in key_highs:
        raided = high > level + tol * 0.05
        closed_back = price < level and float(candle["close"]) < level
        # Also accept: previous bar raided and this bar confirms close below
        prev_raid = float(prev["high"]) > level and float(prev["close"]) < level
        if (raided and closed_back) or (prev_raid and price < level):
            rejection = (high - price) / max(high - low, 1e-12)
            quick = rejection >= 0.45 or (raided and closed_back and float(candle["close"]) < float(candle["open"]))
            meaningful = kind in ("pdh", "equal_high", "asia_high", "london_high", "newyork_high", "swing_high")
            aligned = bias_dir in ("bearish", "neutral")  # neutral OK after inside-day break
            if bias_dir == "bullish":
                aligned = False
            quality = "A" if (meaningful and aligned and quick) else ("B" if meaningful and (aligned or quick) else "C")
            target = None
            tlist = liquidity.get("bearish_targets") or []
            if tlist:
                target = tlist[0]["price"]
            elif liquidity.get("pdl"):
                target = liquidity["pdl"]
            candidates.append({
                "detected": True,
                "side": "SELL",
                "quality": quality,
                "level": round(level, decimals),
                "level_kind": kind,
                "entry": round(price, decimals),
                "stop": round(high + tol * 0.15, decimals),
                "target": round(float(target), decimals) if target else None,
                "reason": (
                    f"Bearish SFP: raided {kind} @ {level:.{decimals}f}, closed back below"
                    + (" · quick rejection" if quick else "")
                ),
                "aligned_with_bias": aligned,
                "rejection_score": round(rejection, 2),
            })

    # Bullish SFP
    for level, kind in key_lows:
        raided = low < level - tol * 0.05
        closed_back = price > level and float(candle["close"]) > level
        prev_raid = float(prev["low"]) < level and float(prev["close"]) > level
        if (raided and closed_back) or (prev_raid and price > level):
            rejection = (price - low) / max(high - low, 1e-12)
            quick = rejection >= 0.45 or (raided and closed_back and float(candle["close"]) > float(candle["open"]))
            meaningful = kind in ("pdl", "equal_low", "asia_low", "london_low", "newyork_low", "swing_low")
            aligned = bias_dir in ("bullish", "neutral")
            if bias_dir == "bearish":
                aligned = False
            quality = "A" if (meaningful and aligned and quick) else ("B" if meaningful and (aligned or quick) else "C")
            target = None
            tlist = liquidity.get("bullish_targets") or []
            if tlist:
                target = tlist[0]["price"]
            elif liquidity.get("pdh"):
                target = liquidity["pdh"]
            candidates.append({
                "detected": True,
                "side": "BUY",
                "quality": quality,
                "level": round(level, decimals),
                "level_kind": kind,
                "entry": round(price, decimals),
                "stop": round(low - tol * 0.15, decimals),
                "target": round(float(target), decimals) if target else None,
                "reason": (
                    f"Bullish SFP: raided {kind} @ {level:.{decimals}f}, closed back above"
                    + (" · quick rejection" if quick else "")
                ),
                "aligned_with_bias": aligned,
                "rejection_score": round(rejection, 2),
            })

    if not candidates:
        return empty

    # Prefer A quality + aligned
    rank = {"A": 0, "B": 1, "C": 2}
    candidates.sort(key=lambda c: (rank.get(c["quality"], 9), 0 if c["aligned_with_bias"] else 1))
    best = candidates[0]
    return best


def build_a_plus_framework(
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame | None = None,
    asset_id: str = "eurusd",
) -> dict[str, Any]:
    """Full 3-step package for one asset."""
    df_daily = resample_to_daily(df_1h) if df_1h is not None else pd.DataFrame()

    # Prefer daily bias; fall back to 4H if daily is inside/neutral weak
    daily_bias = compute_htf_bias(df_daily, "daily")
    h4_bias = compute_htf_bias(df_4h if df_4h is not None else pd.DataFrame(), "4h")

    if daily_bias["bias"] != "neutral" and daily_bias["strength"] in ("strong", "weak"):
        bias = daily_bias
        bias_source = "daily"
    elif h4_bias["bias"] != "neutral":
        bias = h4_bias
        bias_source = "4h"
    else:
        bias = daily_bias
        bias_source = "daily"

    liquidity = compute_liquidity_pools(df_1h, df_daily, asset_id)

    # Fill "what has not been taken" into bias questions
    if bias["bias"] == "bullish":
        if liquidity.get("pdh") and not liquidity.get("pdh_taken"):
            bias["questions"]["untouched_target"] = f"PDH {liquidity['pdh']} still open — primary target"
            bias["target_ready"] = True
        elif liquidity.get("pdh_taken"):
            bias["questions"]["untouched_target"] = "PDH already taken — size down or skip"
            bias["target_ready"] = False
            if bias["strength"] == "strong":
                bias["strength"] = "weak"
                bias["conviction"] = min(bias["conviction"], 55)
        else:
            bias["target_ready"] = bool(liquidity.get("bullish_targets"))
    elif bias["bias"] == "bearish":
        if liquidity.get("pdl") and not liquidity.get("pdl_taken"):
            bias["questions"]["untouched_target"] = f"PDL {liquidity['pdl']} still open — primary target"
            bias["target_ready"] = True
        elif liquidity.get("pdl_taken"):
            bias["questions"]["untouched_target"] = "PDL already taken — size down or skip"
            bias["target_ready"] = False
            if bias["strength"] == "strong":
                bias["strength"] = "weak"
                bias["conviction"] = min(bias["conviction"], 55)
        else:
            bias["target_ready"] = bool(liquidity.get("bearish_targets"))
    else:
        bias["target_ready"] = False
        bias["questions"]["untouched_target"] = "Neutral / inside — wait for SFP at PDH or PDL"

    # Invalidation first — if HTF idea is dead, lean neutral before SFP alignment
    notes: list[str] = [
        f"Bias ({bias_source}): {bias['bias']} · {bias['strength']} · {bias['pattern']}",
    ]
    if bias.get("invalidation") and df_1h is not None and len(df_1h):
        px = float(df_1h["close"].iloc[-1])
        inv = float(bias["invalidation"])
        if bias["bias"] == "bullish" and px < inv:
            notes.append("Prior bullish invalidation hit — bias → neutral (wait for fresh SFP)")
            bias = {**bias, "bias": "neutral", "strength": "neutral", "invalidated": True}
        elif bias["bias"] == "bearish" and px > inv:
            notes.append("Prior bearish invalidation hit — bias → neutral (wait for fresh SFP)")
            bias = {**bias, "bias": "neutral", "strength": "neutral", "invalidated": True}

    # Detect SFP on 1H (execution) — also check last closed 1H if forming bar weak
    sfp = detect_swing_failure(df_1h, liquidity, bias, asset_id)
    if not sfp.get("detected") and df_1h is not None and len(df_1h) >= 16:
        sfp_closed = detect_swing_failure(df_1h.iloc[:-1], liquidity, bias, asset_id)
        if sfp_closed.get("detected") and sfp_closed.get("quality") in ("A", "B"):
            sfp = sfp_closed
            sfp["reason"] = (sfp.get("reason") or "") + " (prior 1H close)"

    # Composite actionable signal from framework alone
    framework_signal = "WAIT"
    framework_conf = 40.0
    if bias.get("questions", {}).get("untouched_target"):
        notes.append(bias["questions"]["untouched_target"])

    if bias["bias"] == "neutral" or bias["strength"] == "neutral":
        notes.append("Neutral HTF — only trade if SFP prints at PDH/PDL/session pool")
    elif bias.get("target_ready") is False and bias["bias"] in ("bullish", "bearish"):
        notes.append("Primary daily target already raided — reduce size / skip")

    if sfp.get("detected") and sfp.get("aligned_with_bias") and sfp.get("quality") in ("A", "B"):
        framework_signal = sfp["side"]
        framework_conf = 78.0 if sfp["quality"] == "A" else 70.0
        if bias["strength"] == "strong":
            framework_conf = min(92.0, framework_conf + 8)
        if bias["strength"] == "weak":
            framework_conf = max(62.0, framework_conf - 6)
        # Neutral bias + SFP at key pool is still valid (inside-day break model)
        if bias.get("invalidated") or bias["bias"] == "neutral":
            framework_conf = min(framework_conf, 74.0)
        notes.append(sfp["reason"])
        notes.append(f"SFP quality {sfp['quality']} · stop {sfp.get('stop')} · target {sfp.get('target')}")
    elif sfp.get("detected") and not sfp.get("aligned_with_bias"):
        notes.append(f"SFP ignored (against bias): {sfp.get('reason')}")
    elif sfp.get("detected"):
        notes.append(f"SFP low quality ({sfp.get('quality')}): {sfp.get('reason')}")

    return {
        "name": "A+ 3-step (bias → liquidity → SFP)",
        "bias": bias,
        "bias_source": bias_source,
        "daily_bias": daily_bias,
        "h4_bias": h4_bias,
        "liquidity": {
            "pdh": liquidity.get("pdh"),
            "pdl": liquidity.get("pdl"),
            "pdh_taken": liquidity.get("pdh_taken"),
            "pdl_taken": liquidity.get("pdl_taken"),
            "session_levels": liquidity.get("session_levels"),
            "equal_highs": liquidity.get("equal_highs"),
            "equal_lows": liquidity.get("equal_lows"),
            "bullish_targets": liquidity.get("bullish_targets"),
            "bearish_targets": liquidity.get("bearish_targets"),
            "note": liquidity.get("note"),
        },
        "sfp": sfp,
        "signal": framework_signal,
        "confidence": round(framework_conf, 1),
        "notes": notes,
        "steps": {
            "1_bias": f"{bias['bias']} ({bias['strength']}) via {bias_source}",
            "2_liquidity": (
                f"PDH {liquidity.get('pdh')}{'✓taken' if liquidity.get('pdh_taken') else ' open'} · "
                f"PDL {liquidity.get('pdl')}{'✓taken' if liquidity.get('pdl_taken') else ' open'}"
            ),
            "3_sfp": sfp.get("reason") if sfp.get("detected") else "Waiting for SFP at key pool",
        },
    }


def apply_a_plus_to_signal(
    tech_signal: str,
    tech_conf: float,
    framework: dict[str, Any] | None,
) -> tuple[str, float, list[str]]:
    """
    Merge A+ framework into technical signal.
    - SFP A/B aligned: can upgrade WAIT → BUY/SELL or boost conf
    - Against-bias technicals: demote
    - Neutral inside-day: prefer WAIT unless SFP prints
    """
    notes: list[str] = []
    if not framework:
        return tech_signal, tech_conf, notes

    signal = (tech_signal or "WAIT").upper()
    conf = float(tech_conf or 0)
    fw_sig = (framework.get("signal") or "WAIT").upper()
    fw_conf = float(framework.get("confidence") or 0)
    bias = (framework.get("bias") or {}).get("bias") or "neutral"
    strength = (framework.get("bias") or {}).get("strength") or "neutral"
    sfp = framework.get("sfp") or {}

    notes.append(f"A+ step1 bias={bias}/{strength}")
    if framework.get("steps"):
        notes.append(f"A+ liq: {framework['steps'].get('2_liquidity')}")

    # Conflict: technical fights strong HTF bias without SFP support
    if (
        strength == "strong"
        and bias == "bullish"
        and signal == "SELL"
        and fw_sig != "SELL"
    ):
        notes.append("A+: demoted SELL — fights strong bullish HTF bias")
        signal, conf = "WAIT", max(30.0, conf - 15)
    elif (
        strength == "strong"
        and bias == "bearish"
        and signal == "BUY"
        and fw_sig != "BUY"
    ):
        notes.append("A+: demoted BUY — fights strong bearish HTF bias")
        signal, conf = "WAIT", max(30.0, conf - 15)

    # SFP confirmation — strongest upgrade
    if fw_sig in ("BUY", "SELL") and sfp.get("quality") in ("A", "B") and sfp.get("aligned_with_bias"):
        if signal == "WAIT" or signal == fw_sig:
            if signal == "WAIT":
                notes.append(f"A+: SFP unlocked {fw_sig} ({sfp.get('quality')})")
            else:
                notes.append(f"A+: SFP confirms {fw_sig} — confidence boost")
            signal = fw_sig
            conf = max(conf, fw_conf)
            conf = min(96.0, conf + (6 if sfp.get("quality") == "A" else 3))
        elif signal != fw_sig:
            # Prefer SFP+bias over conflicting tech when SFP is A
            if sfp.get("quality") == "A":
                notes.append(f"A+: SFP A overrides conflicting tech → {fw_sig}")
                signal = fw_sig
                conf = fw_conf
            else:
                notes.append("A+: tech vs SFP conflict — WAIT")
                signal, conf = "WAIT", max(32.0, min(conf, fw_conf) - 8)

    # Inside / neutral day without SFP → stay flat-ish
    if bias == "neutral" and strength in ("neutral", "caution") and not sfp.get("detected"):
        if signal in ("BUY", "SELL") and conf < 80:
            notes.append("A+: neutral/inside HTF — WAIT for SFP at PDH/PDL")
            signal, conf = "WAIT", max(28.0, conf - 10)

    # Target already taken → haircut
    if framework.get("bias", {}).get("target_ready") is False and signal in ("BUY", "SELL"):
        notes.append("A+: primary daily liquidity already taken — haircut confidence")
        conf = max(50.0, conf - 8)

    return signal, round(conf, 1), notes


def a_plus_plan_overrides(framework: dict[str, Any] | None, trade_plan: dict[str, Any]) -> dict[str, Any]:
    """If SFP active, prefer its stop/target (opposing liquidity)."""
    if not framework or not trade_plan:
        return trade_plan
    sfp = framework.get("sfp") or {}
    if not sfp.get("detected") or not sfp.get("aligned_with_bias"):
        return trade_plan
    if (framework.get("signal") or "WAIT") not in ("BUY", "SELL"):
        return trade_plan
    plan = dict(trade_plan)
    if sfp.get("stop") is not None:
        plan["stop_loss"] = sfp["stop"]
        plan["exit_trigger"] = f"Invalidation beyond SFP wick @ {sfp['stop']}"
    if sfp.get("target") is not None:
        plan["take_profit_2"] = sfp["target"]
        # Keep tp1 as midpoint toward target if entry known
        entry = plan.get("entry") or sfp.get("entry")
        if entry is not None:
            mid = (float(entry) + float(sfp["target"])) / 2.0
            plan["take_profit_1"] = round(mid, 8)
        plan["entry_trigger"] = sfp.get("reason") or plan.get("entry_trigger")
    plan["a_plus_sfp"] = True
    return plan
