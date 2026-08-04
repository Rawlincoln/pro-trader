"""Entry, exit, stop-loss and take-profit signal generation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from data.assets import get_asset


def _round_price(value: float, decimals: int) -> float:
    return round(value, decimals)


def _fmt(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def _pick_nearest_levels(
    levels_1h: dict,
    levels_4h: dict,
    user_sr: dict | None,
) -> tuple[float | None, float | None, str, str]:
    """Prefer user-drawn 4H levels, then merged levels_4h/1h."""
    user_sr = user_sr or {}
    if user_sr.get("has_drawings"):
        ns = user_sr.get("nearest_support")
        nr = user_sr.get("nearest_resistance")
        if ns is not None or nr is not None:
            return (
                float(ns) if ns is not None else levels_4h.get("nearest_support") or levels_1h.get("nearest_support"),
                float(nr) if nr is not None else levels_4h.get("nearest_resistance") or levels_1h.get("nearest_resistance"),
                "user_drawing" if ns is not None else (levels_4h.get("support_source") or "auto"),
                "user_drawing" if nr is not None else (levels_4h.get("resistance_source") or "auto"),
            )
    # Prefer 4H auto over 1H for structural S/R (user said 4H matters most)
    ns = levels_4h.get("nearest_support") or levels_1h.get("nearest_support")
    nr = levels_4h.get("nearest_resistance") or levels_1h.get("nearest_resistance")
    return ns, nr, levels_4h.get("support_source") or "auto", levels_4h.get("resistance_source") or "auto"


def generate_trade_plan(
    signal: str,
    price: float,
    atr: float | None,
    levels_1h: dict,
    levels_4h: dict,
    confidence: float,
    asset_id: str = "eurusd",
    user_sr: dict | None = None,
) -> dict[str, Any]:
    asset = get_asset(asset_id)
    decimals = asset["decimals"]
    min_sl = asset["min_sl_distance"]
    near_dist = asset["near_level_distance"]
    buffer = asset["level_buffer"]
    name = asset["name"]
    # User-drawn levels get a slightly wider "at level" window
    user_near = near_dist * 1.35

    atr = atr or min_sl
    nearest_support, nearest_resistance, support_src, resistance_src = _pick_nearest_levels(
        levels_1h, levels_4h, user_sr
    )
    pivots = levels_1h.get("pivots", {})
    user_sr = user_sr or {}

    plan: dict[str, Any] = {
        "action": signal,
        "current_price": _round_price(price, decimals),
        "confidence": confidence,
        "entry": None,
        "stop_loss": None,
        "take_profit_1": None,
        "take_profit_2": None,
        "take_profit_3": None,
        "risk_reward": None,
        "entry_trigger": None,
        "exit_trigger": None,
        "position_status": "NO_POSITION",
        "instructions": [],
        "level_source": {
            "support": support_src,
            "resistance": resistance_src,
        },
        "user_levels_active": bool(user_sr.get("has_drawings")),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if signal == "WAIT":
        plan["instructions"] = [
            "No high-probability setup — stay flat",
            "Wait for 1H and 4H timeframe alignment",
            "Prefer bounces at your drawn 4H support / rejections at drawn 4H resistance",
        ]
        if nearest_support:
            tag = " (your drawing)" if support_src == "user_drawing" else ""
            plan["instructions"].append(
                f"Watch support at {_fmt(nearest_support, decimals)}{tag} for bounce"
            )
        if nearest_resistance:
            tag = " (your drawing)" if resistance_src == "user_drawing" else ""
            plan["instructions"].append(
                f"Watch resistance at {_fmt(nearest_resistance, decimals)}{tag} for rejection"
            )
        if not user_sr.get("has_drawings"):
            plan["instructions"].append(
                "Tip: draw H-lines / zones on the 4H chart — they drive entries & stops"
            )
        return plan

    # Tighter risk: 1.2 ATR SL default; user-level SL just beyond the line
    sl_distance = max(atr * 1.2, min_sl)
    tp1_distance = sl_distance * 1.5
    tp2_distance = sl_distance * 2.5
    tp3_distance = sl_distance * 3.5
    high_conf = confidence >= 70
    near_level = False
    level_tag_s = " [your 4H/S/R]" if support_src == "user_drawing" else ""
    level_tag_r = " [your 4H/S/R]" if resistance_src == "user_drawing" else ""
    active_near = user_near if (support_src == "user_drawing" or resistance_src == "user_drawing") else near_dist

    if signal == "BUY":
        entry = price
        at_user_sup = (
            nearest_support is not None
            and support_src == "user_drawing"
            and 0 <= price - float(nearest_support) <= active_near
        )
        at_any_sup = nearest_support is not None and price - float(nearest_support) < near_dist

        if at_user_sup or at_any_sup:
            entry = float(nearest_support) + buffer
            near_level = True
            plan["entry_trigger"] = (
                f"Enter on bullish rejection above {_fmt(nearest_support, decimals)}"
                f"{level_tag_s}"
            )
            position_status = (
                "WAITING_FOR_ENTRY" if price - entry > active_near * 0.25 else "ENTER_LONG"
            )
        elif nearest_support is not None and support_src == "user_drawing":
            # Structural edge: always wait for pullback to user 4H support on BUY
            entry = float(nearest_support) + buffer
            near_level = False
            plan["entry_trigger"] = (
                f"Wait for pullback to your drawn support {_fmt(nearest_support, decimals)} "
                f"— EUR/USD respects these 4H levels"
            )
            position_status = "WAITING_FOR_ENTRY"
        elif high_conf:
            plan["entry_trigger"] = (
                f"High-confidence BUY — market ~{_fmt(price, decimals)} "
                f"or pullback to support / your 4H line"
            )
            position_status = "ENTER_LONG"
        else:
            entry = (float(nearest_support) + buffer) if nearest_support else price - atr * 0.35
            plan["entry_trigger"] = (
                f"Do not chase — wait for pullback toward {_fmt(entry, decimals)} "
                f"(conf {confidence:.0f}%)"
            )
            position_status = "WAITING_FOR_ENTRY"

        if nearest_support is not None:
            stop_loss = float(nearest_support) - buffer
            if support_src == "user_drawing":
                stop_loss = float(nearest_support) - max(buffer, atr * 0.25)
        else:
            stop_loss = price - sl_distance
        stop_loss = min(stop_loss, entry - sl_distance * 0.5)

        tp1 = entry + tp1_distance
        # Prefer next user/auto resistance as structural targets
        tp2 = nearest_resistance or pivots.get("r1") or (entry + tp2_distance)
        tp3 = pivots.get("r2") or (entry + tp3_distance)
        if nearest_resistance:
            # ladder: TP1 mid-way to resistance, TP2 at resistance
            dist_to_r = float(nearest_resistance) - entry
            if dist_to_r > sl_distance:
                tp1 = entry + dist_to_r * 0.5
                tp2 = float(nearest_resistance) - buffer
                tp3 = float(nearest_resistance) + dist_to_r * 0.35
        if nearest_resistance and float(tp2) > float(nearest_resistance):
            tp2 = float(nearest_resistance) - buffer

        risk_units = entry - stop_loss
        plan.update({
            "entry": _round_price(entry, decimals),
            "stop_loss": _round_price(stop_loss, decimals),
            "take_profit_1": _round_price(tp1, decimals),
            "take_profit_2": _round_price(float(tp2), decimals),
            "take_profit_3": _round_price(float(tp3), decimals),
            "position_status": position_status,
            "exit_trigger": (
                f"Exit if 1H close below {_fmt(stop_loss, decimals)}"
                f"{' (under your support)' if support_src == 'user_drawing' else ''}"
            ),
            "instructions": [
                f"BUY {name} at {_fmt(entry, decimals)} ({position_status})",
                f"Stop Loss: {_fmt(stop_loss, decimals)}{level_tag_s} "
                f"(risk: {_fmt(risk_units, decimals)})",
                f"TP1 (50%): {_fmt(tp1, decimals)} — take partial, move SL to BE",
                f"TP2 (30%): {_fmt(float(tp2), decimals)}{level_tag_r} — trail stop",
                f"TP3 (20%): {_fmt(float(tp3), decimals)} — final target",
                "Honor your 4H drawn levels — skip if price is mid-range with no level nearby",
            ],
        })

    elif signal == "SELL":
        entry = price
        at_user_res = (
            nearest_resistance is not None
            and resistance_src == "user_drawing"
            and 0 <= float(nearest_resistance) - price <= active_near
        )
        at_any_res = nearest_resistance is not None and float(nearest_resistance) - price < near_dist

        if at_user_res or at_any_res:
            entry = float(nearest_resistance) - buffer
            near_level = True
            plan["entry_trigger"] = (
                f"Enter on bearish rejection below {_fmt(nearest_resistance, decimals)}"
                f"{level_tag_r}"
            )
            position_status = (
                "WAITING_FOR_ENTRY" if entry - price > active_near * 0.25 else "ENTER_SHORT"
            )
        elif nearest_resistance is not None and resistance_src == "user_drawing":
            entry = float(nearest_resistance) - buffer
            plan["entry_trigger"] = (
                f"Wait for rally to your drawn resistance {_fmt(nearest_resistance, decimals)} "
                f"— EUR/USD respects these 4H levels"
            )
            position_status = "WAITING_FOR_ENTRY"
        elif high_conf:
            plan["entry_trigger"] = (
                f"High-confidence SELL — market ~{_fmt(price, decimals)} "
                f"or rally to resistance / your 4H line"
            )
            position_status = "ENTER_SHORT"
        else:
            entry = (float(nearest_resistance) - buffer) if nearest_resistance else price + atr * 0.35
            plan["entry_trigger"] = (
                f"Do not chase — wait for rally toward {_fmt(entry, decimals)} "
                f"(conf {confidence:.0f}%)"
            )
            position_status = "WAITING_FOR_ENTRY"

        if nearest_resistance is not None:
            stop_loss = float(nearest_resistance) + buffer
            if resistance_src == "user_drawing":
                stop_loss = float(nearest_resistance) + max(buffer, atr * 0.25)
        else:
            stop_loss = price + sl_distance
        stop_loss = max(stop_loss, entry + sl_distance * 0.5)

        tp1 = entry - tp1_distance
        tp2 = nearest_support or pivots.get("s1") or (entry - tp2_distance)
        tp3 = pivots.get("s2") or (entry - tp3_distance)
        if nearest_support:
            dist_to_s = entry - float(nearest_support)
            if dist_to_s > sl_distance:
                tp1 = entry - dist_to_s * 0.5
                tp2 = float(nearest_support) + buffer
                tp3 = float(nearest_support) - dist_to_s * 0.35
        if nearest_support and float(tp2) < float(nearest_support):
            tp2 = float(nearest_support) + buffer

        risk_units = stop_loss - entry
        plan.update({
            "entry": _round_price(entry, decimals),
            "stop_loss": _round_price(stop_loss, decimals),
            "take_profit_1": _round_price(tp1, decimals),
            "take_profit_2": _round_price(float(tp2), decimals),
            "take_profit_3": _round_price(float(tp3), decimals),
            "position_status": position_status,
            "exit_trigger": (
                f"Exit if 1H close above {_fmt(stop_loss, decimals)}"
                f"{' (above your resistance)' if resistance_src == 'user_drawing' else ''}"
            ),
            "instructions": [
                f"SELL {name} at {_fmt(entry, decimals)} ({position_status})",
                f"Stop Loss: {_fmt(stop_loss, decimals)}{level_tag_r} "
                f"(risk: {_fmt(risk_units, decimals)})",
                f"TP1 (50%): {_fmt(tp1, decimals)} — take partial, move SL to BE",
                f"TP2 (30%): {_fmt(float(tp2), decimals)}{level_tag_s} — trail stop",
                f"TP3 (20%): {_fmt(float(tp3), decimals)} — final target",
                "Honor your 4H drawn levels — skip if price is mid-range with no level nearby",
            ],
        })

    if plan["entry"] and plan["stop_loss"] and plan["take_profit_2"]:
        risk = abs(plan["entry"] - plan["stop_loss"])
        reward = abs(plan["take_profit_2"] - plan["entry"])
        plan["risk_reward"] = round(reward / risk, 2) if risk else None
        # Reject poor R:R setups
        if plan["risk_reward"] is not None and plan["risk_reward"] < 1.3:
            plan["position_status"] = "SKIP_POOR_RR"
            plan["instructions"].insert(
                0,
                f"R:R {plan['risk_reward']} < 1.3 — skip or wait for better entry",
            )

    plan["near_key_level"] = near_level
    plan["nearest_support"] = (
        _round_price(float(nearest_support), decimals) if nearest_support is not None else None
    )
    plan["nearest_resistance"] = (
        _round_price(float(nearest_resistance), decimals) if nearest_resistance is not None else None
    )
    return plan


def check_exit_conditions(
    current_price: float,
    trade_plan: dict,
    indicators_1h: dict,
) -> dict[str, Any]:
    action = trade_plan.get("action")
    if action not in ("BUY", "SELL"):
        return {"should_exit": False, "reason": None, "urgency": "none"}

    # Only evaluate exits for active / entered style plans
    status = trade_plan.get("position_status") or ""
    if status in ("NO_POSITION", "SKIP_POOR_RR", "WAITING_FOR_ENTRY"):
        return {
            "should_exit": False,
            "reason": "No open-style plan — exit checks inactive",
            "urgency": "none",
        }

    sl = trade_plan.get("stop_loss")
    tp1 = trade_plan.get("take_profit_1")
    tp2 = trade_plan.get("take_profit_2")
    tp3 = trade_plan.get("take_profit_3")
    entry = trade_plan.get("entry")

    exit_info: dict[str, Any] = {"should_exit": False, "reason": None, "urgency": "none"}

    if action == "BUY":
        if sl and current_price <= sl:
            exit_info = {"should_exit": True, "reason": "Stop loss hit", "urgency": "immediate"}
        elif tp3 and current_price >= tp3:
            exit_info = {"should_exit": True, "reason": "TP3 reached - close remaining", "urgency": "immediate"}
        elif tp2 and current_price >= tp2:
            exit_info = {"should_exit": False, "reason": "TP2 reached - trail stop / partial exit", "urgency": "consider"}
        elif tp1 and current_price >= tp1:
            exit_info = {"should_exit": False, "reason": "TP1 reached - take 50% profit, SL → BE", "urgency": "consider"}
        elif (
            entry
            and current_price > entry
            and indicators_1h.get("rsi_signal") == "overbought"
            and indicators_1h.get("macd_cross") == "bearish_cross"
        ):
            exit_info = {
                "should_exit": True,
                "reason": "In-profit bearish reversal on 1H — protect gains",
                "urgency": "consider",
            }

    elif action == "SELL":
        if sl and current_price >= sl:
            exit_info = {"should_exit": True, "reason": "Stop loss hit", "urgency": "immediate"}
        elif tp3 and current_price <= tp3:
            exit_info = {"should_exit": True, "reason": "TP3 reached - close remaining", "urgency": "immediate"}
        elif tp2 and current_price <= tp2:
            exit_info = {"should_exit": False, "reason": "TP2 reached - trail stop / partial exit", "urgency": "consider"}
        elif tp1 and current_price <= tp1:
            exit_info = {"should_exit": False, "reason": "TP1 reached - take 50% profit, SL → BE", "urgency": "consider"}
        elif (
            entry
            and current_price < entry
            and indicators_1h.get("rsi_signal") == "oversold"
            and indicators_1h.get("macd_cross") == "bullish_cross"
        ):
            exit_info = {
                "should_exit": True,
                "reason": "In-profit bullish reversal on 1H — protect gains",
                "urgency": "consider",
            }

    return exit_info


def build_full_analysis(
    df_1h,
    df_4h,
    news_sentiment: dict,
    calendar_risk: dict,
    asset_id: str = "eurusd",
    user_sr: dict | None = None,
) -> dict[str, Any]:
    from analysis.strategy import (
        analyze_timeframe,
        apply_fundamental_adjustment,
        apply_user_level_boost,
        combine_timeframes,
    )
    from data.user_levels import build_user_sr_snapshot, merge_levels_with_user

    asset = get_asset(asset_id)
    analysis_1h = analyze_timeframe(df_1h, "1H", asset)
    analysis_4h = analyze_timeframe(df_4h, "4H", asset)

    price = analysis_1h["indicators"]["price"]
    if user_sr is None:
        user_sr = build_user_sr_snapshot(
            asset_id,
            float(price or 0),
            decimals=asset["decimals"],
            near_dist=asset["near_level_distance"],
        )

    # Fold user drawings into S/R (4H drawings preferred)
    analysis_1h["levels"] = merge_levels_with_user(analysis_1h["levels"], user_sr, prefer_user=True)
    analysis_4h["levels"] = merge_levels_with_user(analysis_4h["levels"], user_sr, prefer_user=True)

    technical = combine_timeframes(analysis_4h, analysis_1h)
    technical = apply_fundamental_adjustment(technical, news_sentiment, calendar_risk, asset)
    technical = apply_user_level_boost(technical, user_sr, asset_id=asset_id)

    atr_val = analysis_1h["indicators"].get("atr")

    trade_plan = generate_trade_plan(
        signal=technical["signal"],
        price=price,
        atr=atr_val,
        levels_1h=analysis_1h["levels"],
        levels_4h=analysis_4h["levels"],
        confidence=technical["confidence"],
        asset_id=asset_id,
        user_sr=user_sr,
    )

    exit_check = check_exit_conditions(price, trade_plan, analysis_1h["indicators"])

    return {
        "analysis_1h": analysis_1h,
        "analysis_4h": analysis_4h,
        "technical": technical,
        "trade_plan": trade_plan,
        "exit_check": exit_check,
        "user_sr": user_sr,
    }