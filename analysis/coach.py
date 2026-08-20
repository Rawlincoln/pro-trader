"""Trade coach: one clear decision after all analysis modules agree.

Produces a human-facing card (verb, steps, grade, session, risk size)
so the UI does not re-derive trading logic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from data.assets import get_asset

# Rough pip size by asset (price units per pip)
_PIP = {
    "eurusd": 0.0001,
    "gold": 0.1,
    "bitcoin": 1.0,
}


def _pip_size(asset_id: str) -> float:
    return _PIP.get(asset_id, 0.0001)


def price_to_pips(distance: float | None, asset_id: str) -> float | None:
    if distance is None:
        return None
    pip = _pip_size(asset_id)
    if pip <= 0:
        return None
    return round(abs(float(distance)) / pip, 1)


def market_session(now: datetime | None = None) -> dict[str, Any]:
    """FX session context (UTC)."""
    now = now or datetime.now(timezone.utc)
    hour = now.hour
    # Rough sessions in UTC
    if 0 <= hour < 7:
        name, quality = "Asia", "low"
        note = "Asia session — EUR/USD often ranges; prefer WAIT unless at a strong level."
    elif 7 <= hour < 12:
        name, quality = "London", "high"
        note = "London session — best liquidity for EUR/USD."
    elif 12 <= hour < 16:
        name, quality = "London/NY overlap", "best"
        note = "London–New York overlap — highest volume window."
    elif 16 <= hour < 21:
        name, quality = "New York", "high"
        note = "New York session — solid for continuation / news reactions."
    else:
        name, quality = "Late NY / thin", "low"
        note = "Thin liquidity — wider spreads, skip marginal setups."

    # EAT = UTC+3 (Kenya) — no external tz db required
    eat = now + timedelta(hours=3)
    return {
        "name": name,
        "quality": quality,  # best | high | low
        "utc_hour": hour,
        "note": note,
        "local_hint": eat.strftime("%H:%M EAT"),
    }


def setup_grade(
    *,
    signal: str,
    confidence: float,
    timeframes_aligned: bool,
    confluence: float | None,
    calendar_risk: dict | None,
    user_sr: dict | None,
    plan: dict | None,
    session: dict | None,
    news_sentiment: dict | None,
    a_plus: dict | None = None,
) -> dict[str, Any]:
    """A–F quality grade for the current setup."""
    signal = (signal or "WAIT").upper()
    conf = float(confidence or 0)
    plan = plan or {}
    user_sr = user_sr or {}
    cal = calendar_risk or {}
    session = session or {}
    a_plus = a_plus or {}
    score = 0.0
    reasons: list[str] = []

    if signal == "WAIT":
        return {
            "letter": "—",
            "score": 0,
            "label": "No trade",
            "reasons": ["No actionable setup after full scan"],
        }

    # Confidence weight
    if conf >= 80:
        score += 30
        reasons.append("High confidence")
    elif conf >= 70:
        score += 24
        reasons.append("Solid confidence")
    elif conf >= 62:
        score += 16
        reasons.append("Acceptable confidence")
    else:
        score += 6
        reasons.append("Low confidence")

    if timeframes_aligned:
        score += 20
        reasons.append("1H + 4H aligned")
    else:
        score += 4
        reasons.append("Timeframes not aligned")

    conf_n = float(confluence or 0)
    if conf_n >= 5:
        score += 15
        reasons.append("Strong indicator confluence")
    elif conf_n >= 3.5:
        score += 10
        reasons.append("Moderate confluence")
    else:
        score += 3
        reasons.append("Weak confluence")

    # User structure
    if user_sr.get("near_user_support") and signal == "BUY":
        score += 15
        reasons.append("At your drawn support")
    elif user_sr.get("near_user_resistance") and signal == "SELL":
        score += 15
        reasons.append("At your drawn resistance")
    elif user_sr.get("has_drawings"):
        score += 6
        reasons.append("Using your drawn levels")
    else:
        score += 2
        reasons.append("No user S/R drawn yet")

    # Plan quality
    status = plan.get("position_status") or ""
    rr = plan.get("risk_reward")
    if status in ("ENTER_LONG", "ENTER_SHORT"):
        score += 8
        reasons.append("Entry ready")
    elif status == "WAITING_FOR_ENTRY":
        score += 5
        reasons.append("Waiting for level")
    elif status == "SKIP_POOR_RR":
        score -= 15
        reasons.append("Poor risk:reward")

    # A+ framework: bias + SFP at liquidity
    sfp = (a_plus.get("sfp") or {}) if a_plus else {}
    bias = (a_plus.get("bias") or {}) if a_plus else {}
    if sfp.get("detected") and sfp.get("aligned_with_bias") and sfp.get("quality") == "A":
        score += 14
        reasons.append("A+ swing failure at key liquidity")
    elif sfp.get("detected") and sfp.get("aligned_with_bias") and sfp.get("quality") == "B":
        score += 8
        reasons.append("A+ SFP (good) at liquidity pool")
    if bias.get("strength") == "strong" and bias.get("bias") in ("bullish", "bearish"):
        if (bias["bias"] == "bullish" and signal == "BUY") or (
            bias["bias"] == "bearish" and signal == "SELL"
        ):
            score += 8
            reasons.append(f"Aligned with strong HTF bias ({bias.get('pattern')})")
    if bias.get("target_ready") is False and signal in ("BUY", "SELL"):
        score -= 6
        reasons.append("Daily liquidity target already taken")

    if rr is not None:
        if rr >= 2.0:
            score += 10
            reasons.append(f"R:R 1:{rr}")
        elif rr >= 1.5:
            score += 7
        elif rr >= 1.3:
            score += 4
        else:
            score -= 8
            reasons.append("R:R too tight")

    # Session
    sq = session.get("quality")
    if sq == "best":
        score += 8
        reasons.append("Best session (overlap)")
    elif sq == "high":
        score += 5
        reasons.append(f"{session.get('name')} session")
    elif sq == "low":
        score -= 6
        reasons.append("Thin session")

    # Calendar
    if cal.get("risk_level") == "high":
        score -= 12
        reasons.append("High-impact news risk")

    news = news_sentiment or {}
    if signal == "BUY" and news.get("overall") == "bearish":
        score -= 4
        reasons.append("News against BUY")
    elif signal == "SELL" and news.get("overall") == "bullish":
        score -= 4
        reasons.append("News against SELL")
    elif news.get("overall") in ("bullish", "bearish"):
        score += 3

    score = max(0, min(100, score))
    if score >= 85:
        letter, label = "A", "Excellent"
    elif score >= 72:
        letter, label = "B", "Good"
    elif score >= 58:
        letter, label = "C", "Average"
    elif score >= 42:
        letter, label = "D", "Weak"
    else:
        letter, label = "F", "Avoid"

    # Cap grade in thin session or high calendar
    if letter in ("A", "B") and (sq == "low" or cal.get("risk_level") == "high"):
        letter, label = "C", "Caution"
        reasons.append("Grade capped due to session/news risk")

    return {
        "letter": letter,
        "score": round(score, 1),
        "label": label,
        "reasons": reasons[:6],
    }


def position_size_suggestion(
    *,
    asset_id: str,
    entry: float | None,
    stop_loss: float | None,
    equity: float = 1000.0,
    risk_percent: float = 1.0,
) -> dict[str, Any]:
    """Suggest lot size from risk % and SL distance (educational, not broker-exact)."""
    asset = get_asset(asset_id)
    if not entry or not stop_loss or entry == stop_loss:
        return {
            "lots": None,
            "risk_amount": None,
            "sl_pips": None,
            "note": "Need entry and stop to size the trade",
            "equity_assumed": equity,
            "risk_percent": risk_percent,
        }

    sl_dist = abs(float(entry) - float(stop_loss))
    sl_pips = price_to_pips(sl_dist, asset_id)
    risk_amount = equity * (risk_percent / 100.0)

    # Approx $ per pip per 0.01 lot
    if asset_id == "eurusd":
        usd_per_pip_per_mini = 1.0  # ~$1/pip per 0.01 lot on standard account
        pip = 0.0001
        # risk = lots * (sl_pips) * 10  for standard lot... use mini
        # For 0.01 lot EURUSD ≈ $0.10 per pip... actually standard is $10/pip per 1.0 lot
        # so 0.01 lot = $0.10/pip. Use: lots = risk / (sl_pips * 10) for full lots
        # Mini lots (0.01): risk_per_pip = 0.10 * (lots/0.01) = 10 * lots
        if sl_pips and sl_pips > 0:
            lots = risk_amount / (sl_pips * 10.0)  # standard lot $10/pip
            lots = max(0.01, round(lots, 2))
        else:
            lots = None
        note = f"~{lots} lots if account ≈ ${equity:.0f} risking {risk_percent}% (EURUSD approx)"
    elif asset_id == "gold":
        # XAU ~ $1/pip per 0.01 lot rough
        if sl_pips and sl_pips > 0:
            lots = risk_amount / (sl_pips * 1.0)
            lots = max(0.01, round(lots, 2))
        else:
            lots = None
        note = f"~{lots} lots gold (approx) for ${equity:.0f} @ {risk_percent}% risk"
    else:
        # Bitcoin: very rough — $1 move * lots
        if sl_dist > 0:
            lots = risk_amount / sl_dist
            lots = max(0.01, round(lots, 2))
        else:
            lots = None
        note = f"~{lots} units BTC (approx) — check your broker contract size"

    return {
        "lots": lots,
        "risk_amount": round(risk_amount, 2),
        "sl_pips": sl_pips,
        "sl_distance": round(sl_dist, asset["decimals"]),
        "note": note,
        "equity_assumed": equity,
        "risk_percent": risk_percent,
        "disclaimer": "Educational size only — verify with your broker",
    }


def build_coach_card(
    *,
    asset_id: str,
    signal: str,
    confidence: float,
    trade_plan: dict,
    exit_check: dict | None,
    technical: dict | None,
    calendar_risk: dict | None,
    user_sr: dict | None,
    news_sentiment: dict | None,
    signal_source: str = "technical",
    equity: float = 1000.0,
    risk_percent: float = 1.0,
    a_plus: dict | None = None,
) -> dict[str, Any]:
    """Single object the UI should trust for what to do next."""
    asset = get_asset(asset_id)
    plan = trade_plan or {}
    signal = (signal or "WAIT").upper()
    conf = float(confidence or 0)
    status = plan.get("position_status") or "NO_POSITION"
    technical = technical or {}
    session = market_session()
    a_plus = a_plus or {}

    grade = setup_grade(
        signal=signal,
        confidence=conf,
        timeframes_aligned=bool(technical.get("timeframes_aligned")),
        confluence=technical.get("confluence"),
        calendar_risk=calendar_risk,
        user_sr=user_sr,
        plan=plan,
        session=session,
        news_sentiment=news_sentiment,
        a_plus=a_plus,
    )

    # Soft-block weak grades
    if signal in ("BUY", "SELL") and grade["letter"] in ("D", "F"):
        signal = "WAIT"
        status = "NO_POSITION"
        grade = {
            **grade,
            "label": "Avoid — demoted",
            "reasons": grade.get("reasons", []) + ["Setup too weak to trade"],
        }

    # Session soft-block for EURUSD thin hours unless grade A/B and at user level
    if (
        asset_id == "eurusd"
        and signal in ("BUY", "SELL")
        and session["quality"] == "low"
        and grade["letter"] not in ("A", "B")
    ):
        signal = "WAIT"
        grade = {
            **grade,
            "letter": "D",
            "label": "Thin session",
            "reasons": grade.get("reasons", []) + [session["note"]],
        }

    sizing = position_size_suggestion(
        asset_id=asset_id,
        entry=plan.get("entry"),
        stop_loss=plan.get("stop_loss"),
        equity=equity,
        risk_percent=risk_percent,
    )

    entry = plan.get("entry")
    sl = plan.get("stop_loss")
    tp1 = plan.get("take_profit_1")
    tp2 = plan.get("take_profit_2")
    tp3 = plan.get("take_profit_3")
    price = plan.get("current_price")

    pip_sl = price_to_pips(
        abs(float(entry) - float(sl)) if entry and sl else None, asset_id
    )
    pip_tp1 = price_to_pips(
        abs(float(tp1) - float(entry)) if entry and tp1 else None, asset_id
    )
    pip_tp2 = price_to_pips(
        abs(float(tp2) - float(entry)) if entry and tp2 else None, asset_id
    )
    pip_tp3 = price_to_pips(
        abs(float(tp3) - float(entry)) if entry and tp3 else None, asset_id
    )
    # Live distance from current price (handy on cTrader)
    pip_sl_from_price = price_to_pips(
        abs(float(price) - float(sl)) if price and sl else None, asset_id
    )
    pip_tp1_from_price = price_to_pips(
        abs(float(tp1) - float(price)) if price and tp1 else None, asset_id
    )

    # Verb
    if signal == "BUY":
        if status == "ENTER_LONG":
            verb, plain = "BUY NOW", f"Open a BUY on {asset['name']} using the levels below."
        elif status == "WAITING_FOR_ENTRY":
            verb, plain = (
                "GET READY TO BUY",
                f"Bias is bullish. Wait for {asset['name']} to reach entry — do not chase.",
            )
        elif status == "SKIP_POOR_RR":
            verb, plain = "BUY BIAS — SKIP", "Direction OK but reward is too small vs risk."
        else:
            verb, plain = "BUY SETUP", f"Bullish plan on {asset['name']}."
    elif signal == "SELL":
        if status == "ENTER_SHORT":
            verb, plain = "SELL NOW", f"Open a SELL on {asset['name']} using the levels below."
        elif status == "WAITING_FOR_ENTRY":
            verb, plain = (
                "GET READY TO SELL",
                f"Bias is bearish. Wait for {asset['name']} to reach entry — do not chase.",
            )
        elif status == "SKIP_POOR_RR":
            verb, plain = "SELL BIAS — SKIP", "Direction OK but reward is too small vs risk."
        else:
            verb, plain = "SELL SETUP", f"Bearish plan on {asset['name']}."
    else:
        verb, plain = "WAIT — DO NOT TRADE", "No clear high-quality setup. Stay flat."

    steps = _steps(signal, status, plan, asset, sizing, calendar_risk, user_sr)

    exit_banner = None
    if exit_check and exit_check.get("reason"):
        exit_banner = {
            "text": (
                ("EXIT NOW: " if exit_check.get("should_exit") else "WATCH: ")
                + exit_check["reason"]
            ),
            "urgency": exit_check.get("urgency") or "none",
        }

    return {
        "signal": signal,
        "verb": verb,
        "plain": plain,
        "confidence": round(conf, 1),
        "grade": grade,
        "session": session,
        "status": status,
        "steps": steps,
        "levels": {
            "price": price,
            "entry": entry,
            "stop_loss": sl,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "take_profit_3": tp3,
            "risk_reward": plan.get("risk_reward"),
            "sl_pips": pip_sl,
            "tp1_pips": pip_tp1,
            "tp2_pips": pip_tp2,
            "tp3_pips": pip_tp3,
            "sl_pips_from_price": pip_sl_from_price,
            "tp1_pips_from_price": pip_tp1_from_price,
            "support": plan.get("nearest_support"),
            "resistance": plan.get("nearest_resistance"),
            "level_source": plan.get("level_source") or {},
        },
        "sizing": sizing,
        "exit_banner": exit_banner,
        "signal_source": signal_source,
        "checklist": _checklist(
            technical, calendar_risk, user_sr, plan, news_sentiment, session, grade
        ),
        "one_liner": _one_liner(signal, verb, grade, session, plan, asset["name"], a_plus),
        "a_plus_steps": (a_plus or {}).get("steps"),
    }


def _steps(
    signal: str,
    status: str,
    plan: dict,
    asset: dict,
    sizing: dict,
    calendar_risk: dict | None,
    user_sr: dict | None,
) -> list[str]:
    decimals = asset["decimals"]
    name = asset["name"]

    def f(v):
        if v is None:
            return "—"
        return f"{float(v):.{decimals}f}"

    steps: list[str] = []
    if signal == "WAIT":
        steps.append("Stay flat — do not open a new trade.")
        if plan.get("nearest_support") is not None:
            steps.append(f"Watch support near {f(plan['nearest_support'])}.")
        if plan.get("nearest_resistance") is not None:
            steps.append(f"Watch resistance near {f(plan['nearest_resistance'])}.")
        if not (user_sr or {}).get("has_drawings") and asset["id"] == "eurusd":
            steps.append(
                "Draw your main 4H support & resistance (H or Zone tools) so the plan can use them."
            )
        else:
            steps.append("Wait for 1H + 4H agreement and price to tag a key level.")
        if (calendar_risk or {}).get("risk_level") == "high":
            steps.append("High-impact news is near — reduce size or stay out.")
        return steps

    side = "BUY" if signal == "BUY" else "SELL"
    if status == "WAITING_FOR_ENTRY":
        steps.append(f"Do not enter at market. Wait for price ≈ {f(plan.get('entry'))}.")
        if plan.get("entry_trigger"):
            steps.append(plan["entry_trigger"])
    elif status == "SKIP_POOR_RR":
        steps.append("Skip — risk:reward is too poor.")
        steps.append(plan.get("entry_trigger") or "Wait for a cleaner level.")
        return steps
    else:
        steps.append(f"Enter {side} on {name} near {f(plan.get('entry'))}.")
        if plan.get("entry_trigger"):
            steps.append(plan["entry_trigger"])

    aid = asset.get("id") or "eurusd"
    entry_v = plan.get("entry")
    sl_v = plan.get("stop_loss")
    tp1_v = plan.get("take_profit_1")
    tp2_v = plan.get("take_profit_2")
    tp3_v = plan.get("take_profit_3")
    sl_pips = sizing.get("sl_pips") or price_to_pips(
        abs(float(entry_v) - float(sl_v)) if entry_v and sl_v else None, aid
    )
    tp1_pips = price_to_pips(
        abs(float(tp1_v) - float(entry_v)) if entry_v and tp1_v else None, aid
    )
    tp2_pips = price_to_pips(
        abs(float(tp2_v) - float(entry_v)) if entry_v and tp2_v else None, aid
    )
    tp3_pips = price_to_pips(
        abs(float(tp3_v) - float(entry_v)) if entry_v and tp3_v else None, aid
    )

    def _px_pips(price_s: str, pips) -> str:
        return f"{price_s} · {pips} pips" if pips is not None else price_s

    steps.append(f"Stop loss: {_px_pips(f(sl_v), sl_pips)} — exit if this breaks.")
    steps.append(
        f"Take profits: TP1 {_px_pips(f(tp1_v), tp1_pips)} (close ~50%), "
        f"TP2 {_px_pips(f(tp2_v), tp2_pips)}, TP3 {_px_pips(f(tp3_v), tp3_pips)}."
    )
    if plan.get("risk_reward"):
        steps.append(f"Risk:reward ≈ 1:{plan['risk_reward']}.")
    if sizing.get("lots"):
        steps.append(
            f"Suggested size ≈ {sizing['lots']} lots "
            f"(risk ~${sizing['risk_amount']} on ${sizing['equity_assumed']:.0f} account @ {sizing['risk_percent']}%)."
        )
    steps.append("After TP1, move stop to break-even. Avoid entering 30 min before high-impact news.")
    return steps


def _checklist(
    technical: dict | None,
    calendar_risk: dict | None,
    user_sr: dict | None,
    plan: dict,
    news_sentiment: dict | None,
    session: dict,
    grade: dict,
) -> list[dict[str, Any]]:
    technical = technical or {}
    user_sr = user_sr or {}
    news = news_sentiment or {}
    cal = calendar_risk or {}
    rr = plan.get("risk_reward")
    return [
        {
            "label": "1H + 4H aligned",
            "ok": bool(technical.get("timeframes_aligned")),
            "detail": "yes" if technical.get("timeframes_aligned") else "no",
        },
        {
            "label": "Setup grade",
            "ok": grade.get("letter") in ("A", "B", "C"),
            "detail": f"{grade.get('letter')} · {grade.get('label')}",
        },
        {
            "label": "Session",
            "ok": session.get("quality") in ("best", "high"),
            "detail": session.get("name"),
        },
        {
            "label": "News risk",
            "ok": cal.get("risk_level") != "high",
            "detail": cal.get("risk_level") or news.get("overall") or "ok",
        },
        {
            "label": "Your S/R levels",
            "ok": bool(user_sr.get("has_drawings")),
            "detail": (
                f"{user_sr.get('count_4h', 0)} on 4H"
                if user_sr.get("has_drawings")
                else "none drawn"
            ),
        },
        {
            "label": "Risk:reward",
            "ok": rr is None or float(rr) >= 1.3,
            "detail": f"1:{rr}" if rr is not None else "n/a",
        },
    ]


def _one_liner(
    signal: str,
    verb: str,
    grade: dict,
    session: dict,
    plan: dict,
    name: str,
    a_plus: dict | None = None,
) -> str:
    ap = a_plus or {}
    sfp = ap.get("sfp") or {}
    bias = (ap.get("bias") or {}).get("bias")
    extra = ""
    if sfp.get("detected") and sfp.get("aligned_with_bias"):
        extra = f" · A+ SFP {sfp.get('quality')} @ {sfp.get('level_kind')}"
    elif bias and bias != "neutral":
        extra = f" · HTF bias {bias}"
    if signal == "WAIT":
        return f"{verb}. Grade {grade.get('letter', '—')}. Session: {session.get('name')}.{extra}"
    return (
        f"{verb} on {name} · Grade {grade.get('letter')} · "
        f"{session.get('name')} · status {plan.get('position_status', '—')}{extra}"
    )
