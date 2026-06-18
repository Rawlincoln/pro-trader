"""News & calendar event impact analysis with BUY/SELL signals."""

from __future__ import annotations

import re
from typing import Any


# higher_is: beat forecast = this direction for the currency
EVENT_RULES: list[dict] = [
    {"match": r"nfp|non.?farm|payroll|employment change", "currency": "USD", "higher_is": "strong",
     "impact": "high", "pip_estimate": 50},
    {"match": r"unemployment rate|jobless", "currency": "USD", "higher_is": "weak", "impact": "high", "pip_estimate": 30},
    {"match": r"cpi|consumer price|inflation rate|pce", "currency": "USD", "higher_is": "strong", "impact": "high", "pip_estimate": 40},
    {"match": r"ppi|producer price", "currency": "USD", "higher_is": "strong", "impact": "medium", "pip_estimate": 25},
    {"match": r"gdp", "currency": "USD", "higher_is": "strong", "impact": "high", "pip_estimate": 35},
    {"match": r"retail sales", "currency": "USD", "higher_is": "strong", "impact": "medium", "pip_estimate": 25},
    {"match": r"pmi|ism manufacturing|ism services", "currency": "USD", "higher_is": "strong", "impact": "medium", "pip_estimate": 20},
    {"match": r"trade balance", "currency": "USD", "higher_is": "weak", "impact": "medium", "pip_estimate": 15},
    {"match": r"consumer confidence|michigan", "currency": "USD", "higher_is": "strong", "impact": "medium", "pip_estimate": 15},
    {"match": r"fomc|fed interest|fed rate|federal funds", "currency": "USD", "higher_is": "strong", "impact": "high", "pip_estimate": 60},
    {"match": r"fed chair|powell", "currency": "USD", "higher_is": "strong", "impact": "high", "pip_estimate": 30},
    {"match": r"ecb|lagarde|eurozone.*rate|eur.*rate", "currency": "EUR", "higher_is": "strong", "impact": "high", "pip_estimate": 50},
    {"match": r"eurozone cpi|eurozone gdp|eurozone pmi|german", "currency": "EUR", "higher_is": "strong", "impact": "medium", "pip_estimate": 25},
    {"match": r"treasury|bond auction|yield", "currency": "USD", "higher_is": "strong", "impact": "medium", "pip_estimate": 20},
]

HEADLINE_RULES: list[dict] = [
    {"match": r"rate.?hike|hawkish|tighten|higher.?for.?longer", "usd": "strong", "sentiment": "hawkish", "strength": 2},
    {"match": r"rate.?cut|dovish|easing|lower.?rates", "usd": "weak", "sentiment": "dovish", "strength": 2},
    {"match": r"beats?.?forecast|exceeds?.?expect|stronger.?than.?expected|surprise.?gain", "usd": "strong", "sentiment": "bullish_usd", "strength": 2},
    {"match": r"miss(es)??.?forecast|below.?expect|weaker.?than.?expected|disappoint", "usd": "weak", "sentiment": "bearish_usd", "strength": 2},
    {"match": r"recession|slowdown|contraction|crisis|default", "usd": "weak", "sentiment": "risk_off", "strength": 2},
    {"match": r"rally|surge|soar|record.?high|all.?time.?high", "usd": "neutral", "sentiment": "risk_on", "strength": 1},
    {"match": r"ecb.*(hawkish|hike|raise)", "eur": "strong", "sentiment": "hawkish_eur", "strength": 2},
    {"match": r"ecb.*(dovish|cut|lower)", "eur": "weak", "sentiment": "dovish_eur", "strength": 2},
    {"match": r"geopolit|war|attack|sanction|conflict", "usd": "mixed", "sentiment": "safe_haven", "strength": 2},
    {"match": r"sec.*(approve|reject|lawsuit|ban)|etf.*(approve|reject)", "btc": "volatile", "sentiment": "crypto_reg", "strength": 2},
    {"match": r"bitcoin.*(surge|rally|soar|approve)", "btc": "strong", "sentiment": "bullish_btc", "strength": 2},
    {"match": r"bitcoin.*(crash|plunge|ban|hack)", "btc": "weak", "sentiment": "bearish_btc", "strength": 2},
]


def _parse_number(val: str) -> float | None:
    if not val or val in ("-", "", "N/A"):
        return None
    cleaned = re.sub(r"[%KMBkmb,]", "", str(val).strip())
    multiplier = 1.0
    if "K" in str(val).upper():
        multiplier = 1000
    elif "M" in str(val).upper():
        multiplier = 1_000_000
    elif "B" in str(val).upper():
        multiplier = 1_000_000_000
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def classify_event(title: str) -> dict | None:
    title_lower = title.lower()
    for rule in EVENT_RULES:
        if re.search(rule["match"], title_lower):
            return rule
    return None


def _currency_bias(currency: str, beat: bool, rule: dict) -> str:
    higher_strong = rule["higher_is"] == "strong"
    if currency == "USD":
        if beat == higher_strong:
            return "usd_strong"
        return "usd_weak"
    if currency == "EUR":
        if beat == higher_strong:
            return "eur_strong"
        return "eur_weak"
    return "neutral"


def asset_signal_from_bias(bias: str, asset_id: str) -> tuple[str, str, float]:
    """Return (signal, reason, confidence) for asset given currency bias."""
    if bias == "neutral":
        return "WAIT", "No clear directional bias from data", 40.0

    if asset_id == "eurusd":
        if bias == "usd_strong":
            return "SELL", "USD strengthened — bearish EUR/USD", 75.0
        if bias == "usd_weak":
            return "BUY", "USD weakened — bullish EUR/USD", 75.0
        if bias == "eur_strong":
            return "BUY", "EUR strengthened — bullish EUR/USD", 78.0
        if bias == "eur_weak":
            return "SELL", "EUR weakened — bearish EUR/USD", 78.0

    if asset_id == "gold":
        if bias in ("usd_strong",):
            return "SELL", "Strong USD typically pressures gold", 70.0
        if bias in ("usd_weak",):
            return "BUY", "Weak USD supports gold prices", 72.0
        if bias == "eur_weak":
            return "BUY", "Risk-off / EUR weakness — safe haven bid for gold", 65.0

    if asset_id == "bitcoin":
        if bias == "usd_weak":
            return "BUY", "Weak USD / risk-on supports crypto", 68.0
        if bias == "usd_strong":
            return "SELL", "Strong USD / risk-off pressures crypto", 65.0

    return "WAIT", "Mixed macro impact", 45.0


def analyze_calendar_release(event: dict, asset_id: str = "eurusd") -> dict[str, Any]:
    """Analyze released calendar data and produce trading signal."""
    title = event.get("title", "")
    rule = classify_event(title)
    currency = event.get("currency", "USD").upper()
    actual = _parse_number(event.get("actual", ""))
    forecast = _parse_number(event.get("forecast", ""))
    previous = _parse_number(event.get("previous", ""))

    result: dict[str, Any] = {
        "event": title,
        "currency": currency,
        "actual": event.get("actual", ""),
        "forecast": event.get("forecast", ""),
        "previous": event.get("previous", ""),
        "signal": "WAIT",
        "confidence": 40.0,
        "impact": event.get("impact", "medium"),
        "bias": "neutral",
        "reason": "",
        "pip_estimate": 20,
        "surprise": None,
    }

    if not rule:
        rule = {"currency": currency, "higher_is": "strong", "impact": "medium", "pip_estimate": 15}

    result["pip_estimate"] = rule.get("pip_estimate", 20)

    if actual is None:
        result["reason"] = "Awaiting actual release data"
        return result

    ref = forecast if forecast is not None else previous
    if ref is None:
        result["reason"] = f"Released: {event.get('actual')} — no forecast to compare"
        result["confidence"] = 50.0
        return result

    beat = actual > ref
    miss = actual < ref
    surprise_pct = abs(actual - ref) / abs(ref) * 100 if ref != 0 else 0
    result["surprise"] = round(surprise_pct, 2)

    bias = _currency_bias(rule["currency"], beat, rule)
    if miss and not beat:
        bias = _currency_bias(rule["currency"], False, rule)

    if abs(actual - ref) < abs(ref) * 0.01:
        bias = "neutral"
        result["reason"] = "In-line with expectations — limited surprise"
        result["confidence"] = 45.0
        return result

    signal, reason, conf = asset_signal_from_bias(bias, asset_id)
    result.update({
        "signal": signal,
        "confidence": min(95, conf + min(surprise_pct, 15)),
        "bias": bias,
        "reason": f"{'Beat' if beat else 'Missed'} forecast ({actual} vs {ref}): {reason}",
    })

    if rule.get("impact") == "high":
        result["confidence"] = min(97, result["confidence"] + 5)

    return result


def analyze_headline(title: str, summary: str = "", asset_id: str = "eurusd") -> dict[str, Any]:
    """Instant signal from breaking news headline."""
    text = f"{title} {summary}".lower()
    usd_bias = "neutral"
    eur_bias = "neutral"
    btc_bias = "neutral"
    sentiment = "neutral"
    strength = 0
    matched_rules = []

    for rule in HEADLINE_RULES:
        if re.search(rule["match"], text):
            matched_rules.append(rule["sentiment"])
            strength += rule.get("strength", 1)
            if rule.get("usd") == "strong":
                usd_bias = "strong"
            elif rule.get("usd") == "weak":
                usd_bias = "weak"
            if rule.get("eur") == "strong":
                eur_bias = "strong"
            elif rule.get("eur") == "weak":
                eur_bias = "weak"
            if rule.get("btc") == "strong":
                btc_bias = "strong"
            elif rule.get("btc") == "weak":
                btc_bias = "weak"
            sentiment = rule.get("sentiment", sentiment)

    bias = "neutral"
    if asset_id == "eurusd":
        if eur_bias == "strong":
            bias = "eur_strong"
        elif eur_bias == "weak":
            bias = "eur_weak"
        elif usd_bias == "strong":
            bias = "usd_strong"
        elif usd_bias == "weak":
            bias = "usd_weak"
    elif asset_id == "gold":
        if usd_bias == "strong":
            bias = "usd_strong"
        elif usd_bias == "weak":
            bias = "usd_weak"
        if "safe_haven" in sentiment:
            bias = "eur_weak"  # risk-off proxy for gold buy
    elif asset_id == "bitcoin":
        if btc_bias == "strong":
            bias = "usd_weak"
        elif btc_bias == "weak":
            bias = "usd_strong"

    signal, reason, conf = asset_signal_from_bias(bias, asset_id)
    if not matched_rules:
        # Fallback sentiment scoring
        pos = len(re.findall(r"bullish|rally|surge|gain|rise|beat|hawkish", text))
        neg = len(re.findall(r"bearish|fall|drop|miss|dovish|crash|plunge", text))
        if pos > neg + 1:
            signal, reason, conf = "BUY", "Headline sentiment bullish", 55.0
        elif neg > pos + 1:
            signal, reason, conf = "SELL", "Headline sentiment bearish", 55.0
        else:
            signal, reason, conf = "WAIT", "Neutral headline", 40.0

    return {
        "title": title,
        "signal": signal,
        "confidence": min(90, conf + strength * 3),
        "reason": reason,
        "sentiment": sentiment,
        "matched": matched_rules,
        "type": "headline",
    }


def pre_event_bias(event: dict, asset_id: str = "eurusd") -> dict[str, Any]:
    """Pre-event trading bias based on forecast vs previous."""
    title = event.get("title", "")
    rule = classify_event(title) or {"currency": event.get("currency", "USD"), "higher_is": "strong", "impact": "medium"}
    forecast = _parse_number(event.get("forecast", ""))
    previous = _parse_number(event.get("previous", ""))

    minutes_to = event.get("minutes_until", 999)

    result = {
        "event": title,
        "currency": event.get("currency"),
        "impact": event.get("impact", "medium"),
        "minutes_until": minutes_to,
        "signal": "WAIT",
        "confidence": 50.0,
        "strategy": "wait_for_release",
        "reason": "High-impact event approaching — wait for data or trade the breakout",
        "pip_estimate": rule.get("pip_estimate", 20),
    }

    if minutes_to <= 5:
        result["strategy"] = "breakout"
        result["reason"] = (
            f"IMMINENT: {title} in {minutes_to}min. "
            "Set straddle orders or wait 30sec after release for direction."
        )
        result["confidence"] = 60.0
    elif minutes_to <= 15:
        result["strategy"] = "reduce_exposure"
        result["reason"] = f"WARNING: {title} in {minutes_to}min — reduce size or close positions"
        result["confidence"] = 55.0
    elif minutes_to <= 60:
        result["strategy"] = "prepare"
        result["reason"] = f"UPCOMING: {title} in {minutes_to}min — prepare entry plan"
        result["confidence"] = 50.0

    if forecast is not None and previous is not None:
        improving = forecast > previous
        bias = _currency_bias(rule["currency"], improving, rule)
        signal, reason, conf = asset_signal_from_bias(bias, asset_id)
        if signal != "WAIT":
            result["expected_signal"] = signal
            result["expected_reason"] = f"Forecast ({forecast}) vs Previous ({previous}): {reason}"
            result["expected_confidence"] = conf * 0.7  # lower confidence pre-release

    return result


def combine_news_signals(
    calendar_signals: list[dict],
    headline_signals: list[dict],
    asset_id: str,
) -> dict[str, Any]:
    """Merge calendar + headline into one actionable news signal."""
    scores = {"BUY": 0.0, "SELL": 0.0, "WAIT": 0.0}
    reasons = []

    for sig in calendar_signals + headline_signals:
        s = sig.get("signal", "WAIT")
        conf = sig.get("confidence", 50)
        weight = 1.5 if sig.get("type") == "calendar_release" else 1.0
        scores[s] = scores.get(s, 0) + conf * weight
        if sig.get("reason"):
            reasons.append(sig["reason"])

    best = max(scores, key=scores.get)
    total = sum(scores.values()) or 1
    confidence = round(scores[best] / total * 100, 1) if best != "WAIT" else 40.0

    if scores["BUY"] > 0 and scores["SELL"] > 0:
        ratio = scores["BUY"] / scores["SELL"] if scores["SELL"] else 99
        if ratio < 1.3 and ratio > 0.77:
            best = "WAIT"
            confidence = 35.0
            reasons.append("Conflicting news signals — stay flat")

    return {
        "signal": best,
        "confidence": min(97, confidence),
        "reasons": reasons[:5],
        "scores": scores,
    }