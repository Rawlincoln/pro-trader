"""Entry, exit, stop-loss and take-profit signal generation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from data.assets import get_asset


def _round_price(value: float, decimals: int) -> float:
    return round(value, decimals)


def _fmt(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def generate_trade_plan(
    signal: str,
    price: float,
    atr: float | None,
    levels_1h: dict,
    levels_4h: dict,
    confidence: float,
    asset_id: str = "eurusd",
) -> dict[str, Any]:
    asset = get_asset(asset_id)
    decimals = asset["decimals"]
    min_sl = asset["min_sl_distance"]
    near_dist = asset["near_level_distance"]
    buffer = asset["level_buffer"]
    name = asset["name"]

    atr = atr or min_sl
    nearest_support = levels_1h.get("nearest_support") or levels_4h.get("nearest_support")
    nearest_resistance = levels_1h.get("nearest_resistance") or levels_4h.get("nearest_resistance")
    pivots = levels_1h.get("pivots", {})

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
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if signal == "WAIT":
        plan["instructions"] = [
            "No high-probability setup - stay flat",
            "Wait for 1H and 4H timeframe alignment",
            "Monitor support/resistance for breakout or rejection",
        ]
        if nearest_support:
            plan["instructions"].append(f"Watch support at {_fmt(nearest_support, decimals)} for bounce")
        if nearest_resistance:
            plan["instructions"].append(f"Watch resistance at {_fmt(nearest_resistance, decimals)} for rejection")
        return plan

    sl_distance = max(atr * 1.5, min_sl)
    tp1_distance = sl_distance * 1.5
    tp2_distance = sl_distance * 2.5
    tp3_distance = sl_distance * 4.0

    if signal == "BUY":
        entry = price
        if nearest_support and price - nearest_support < near_dist:
            entry = nearest_support + buffer
            plan["entry_trigger"] = f"Enter on bullish rejection above {_fmt(nearest_support, decimals)}"
        else:
            plan["entry_trigger"] = f"Enter NOW at market ~{_fmt(price, decimals)} or on 1H pullback to EMA20"

        stop_loss = (nearest_support - buffer) if nearest_support else price - sl_distance
        stop_loss = min(stop_loss, price - sl_distance)

        tp1 = price + tp1_distance
        tp2 = pivots.get("r1") or (price + tp2_distance)
        tp3 = pivots.get("r2") or nearest_resistance or (price + tp3_distance)

        risk_units = entry - stop_loss
        plan.update({
            "entry": _round_price(entry, decimals),
            "stop_loss": _round_price(stop_loss, decimals),
            "take_profit_1": _round_price(tp1, decimals),
            "take_profit_2": _round_price(float(tp2), decimals),
            "take_profit_3": _round_price(float(tp3), decimals),
            "position_status": "ENTER_LONG",
            "exit_trigger": f"Exit if price closes below {_fmt(stop_loss, decimals)} on 1H",
            "instructions": [
                f"BUY {name} at {_fmt(entry, decimals)}",
                f"Stop Loss: {_fmt(stop_loss, decimals)} (risk: {_fmt(risk_units, decimals)})",
                f"TP1 (50%): {_fmt(tp1, decimals)} - take partial profit",
                f"TP2 (30%): {_fmt(float(tp2), decimals)} - trail stop to breakeven",
                f"TP3 (20%): {_fmt(float(tp3), decimals)} - final target",
                "Move stop to breakeven after TP1 hit",
            ],
        })

    elif signal == "SELL":
        entry = price
        if nearest_resistance and nearest_resistance - price < near_dist:
            entry = nearest_resistance - buffer
            plan["entry_trigger"] = f"Enter on bearish rejection below {_fmt(nearest_resistance, decimals)}"
        else:
            plan["entry_trigger"] = f"Enter NOW at market ~{_fmt(price, decimals)} or on 1H rally to EMA20"

        stop_loss = (nearest_resistance + buffer) if nearest_resistance else price + sl_distance
        stop_loss = max(stop_loss, price + sl_distance)

        tp1 = price - tp1_distance
        tp2 = pivots.get("s1") or (price - tp2_distance)
        tp3 = pivots.get("s2") or nearest_support or (price - tp3_distance)

        risk_units = stop_loss - entry
        plan.update({
            "entry": _round_price(entry, decimals),
            "stop_loss": _round_price(stop_loss, decimals),
            "take_profit_1": _round_price(tp1, decimals),
            "take_profit_2": _round_price(float(tp2), decimals),
            "take_profit_3": _round_price(float(tp3), decimals),
            "position_status": "ENTER_SHORT",
            "exit_trigger": f"Exit if price closes above {_fmt(stop_loss, decimals)} on 1H",
            "instructions": [
                f"SELL {name} at {_fmt(entry, decimals)}",
                f"Stop Loss: {_fmt(stop_loss, decimals)} (risk: {_fmt(risk_units, decimals)})",
                f"TP1 (50%): {_fmt(tp1, decimals)} - take partial profit",
                f"TP2 (30%): {_fmt(float(tp2), decimals)} - trail stop to breakeven",
                f"TP3 (20%): {_fmt(float(tp3), decimals)} - final target",
                "Move stop to breakeven after TP1 hit",
            ],
        })

    if plan["entry"] and plan["stop_loss"] and plan["take_profit_2"]:
        risk = abs(plan["entry"] - plan["stop_loss"])
        reward = abs(plan["take_profit_2"] - plan["entry"])
        plan["risk_reward"] = round(reward / risk, 2) if risk else None

    return plan


def check_exit_conditions(
    current_price: float,
    trade_plan: dict,
    indicators_1h: dict,
) -> dict[str, Any]:
    action = trade_plan.get("action")
    if action not in ("BUY", "SELL"):
        return {"should_exit": False, "reason": None}

    sl = trade_plan.get("stop_loss")
    tp1 = trade_plan.get("take_profit_1")
    tp2 = trade_plan.get("take_profit_2")
    tp3 = trade_plan.get("take_profit_3")

    exit_info = {"should_exit": False, "reason": None, "urgency": "none"}

    if action == "BUY":
        if sl and current_price <= sl:
            exit_info = {"should_exit": True, "reason": "Stop loss hit", "urgency": "immediate"}
        elif tp3 and current_price >= tp3:
            exit_info = {"should_exit": True, "reason": "TP3 reached - close remaining", "urgency": "immediate"}
        elif tp2 and current_price >= tp2:
            exit_info = {"should_exit": False, "reason": "TP2 reached - consider partial exit", "urgency": "consider"}
        elif tp1 and current_price >= tp1:
            exit_info = {"should_exit": False, "reason": "TP1 reached - take 50% profit", "urgency": "consider"}
        elif indicators_1h.get("rsi_signal") == "overbought" and indicators_1h.get("macd_cross") == "bearish_cross":
            exit_info = {"should_exit": True, "reason": "Bearish reversal signals on 1H", "urgency": "consider"}

    elif action == "SELL":
        if sl and current_price >= sl:
            exit_info = {"should_exit": True, "reason": "Stop loss hit", "urgency": "immediate"}
        elif tp3 and current_price <= tp3:
            exit_info = {"should_exit": True, "reason": "TP3 reached - close remaining", "urgency": "immediate"}
        elif tp2 and current_price <= tp2:
            exit_info = {"should_exit": False, "reason": "TP2 reached - consider partial exit", "urgency": "consider"}
        elif tp1 and current_price <= tp1:
            exit_info = {"should_exit": False, "reason": "TP1 reached - take 50% profit", "urgency": "consider"}
        elif indicators_1h.get("rsi_signal") == "oversold" and indicators_1h.get("macd_cross") == "bullish_cross":
            exit_info = {"should_exit": True, "reason": "Bullish reversal signals on 1H", "urgency": "consider"}

    return exit_info


def build_full_analysis(
    df_1h,
    df_4h,
    news_sentiment: dict,
    calendar_risk: dict,
    asset_id: str = "eurusd",
) -> dict[str, Any]:
    from analysis.strategy import analyze_timeframe, combine_timeframes, apply_fundamental_adjustment

    asset = get_asset(asset_id)
    analysis_1h = analyze_timeframe(df_1h, "1H", asset)
    analysis_4h = analyze_timeframe(df_4h, "4H", asset)
    technical = combine_timeframes(analysis_4h, analysis_1h)
    technical = apply_fundamental_adjustment(technical, news_sentiment, calendar_risk, asset)

    price = analysis_1h["indicators"]["price"]
    atr_val = analysis_1h["indicators"].get("atr")

    trade_plan = generate_trade_plan(
        signal=technical["signal"],
        price=price,
        atr=atr_val,
        levels_1h=analysis_1h["levels"],
        levels_4h=analysis_4h["levels"],
        confidence=technical["confidence"],
        asset_id=asset_id,
    )

    exit_check = check_exit_conditions(price, trade_plan, analysis_1h["indicators"])

    return {
        "analysis_1h": analysis_1h,
        "analysis_4h": analysis_4h,
        "technical": technical,
        "trade_plan": trade_plan,
        "exit_check": exit_check,
    }