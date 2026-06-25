"""
Attention Liquidity Index (ALI) for Bitcoin.

Analogy: attention is liquidity — marketing, influencers, and narrative recruit
the next buyer. Rising buzz + recruitment language tends to pull price up;
fading attention after a peak often means liquidity is drying up.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Rolling scan history for momentum (30s refresh ≈ 48 scans / 24 min)
_scan_history: deque[dict[str, float]] = deque(maxlen=96)
_coingecko_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}
COINGECKO_TTL = 300

RECRUITMENT_PATTERNS: list[tuple[str, int, str]] = [
    (r"influencer|kol\b|celebrity|elon|musk|trump|saylor|microstrategy|wood|ark invest", 3, "influencer"),
    (r"viral|trending|mainstream|mass.?adoption|fomo|hype", 2, "marketing"),
    (r"etf.{0,25}(inflow|record|approve|demand|launch)|institutional.{0,20}(buy|inflow|adopt|accum)", 3, "institutional"),
    (r"whale|accumulat|treasury|corporate.{0,12}buy|fund.{0,10}buy", 2, "recruitment"),
    (r"record.{0,12}(high|inflow)|all.?time.?high|\bath\b|milestone", 2, "hype"),
    (r"adoption|partnership|launch|nation|legal|approve|bill", 1, "narrative"),
    (r"buy.{0,8}bitcoin|stack.{0,8}sats|hodl|long.?term.?hold", 2, "buyer_recruitment"),
]

DISTRIBUTION_PATTERNS: list[tuple[str, int, str]] = [
    (r"crash|plunge|ban|hack|fraud|ponzi|bubble.?burst|collapse", -3, "fear"),
    (r"sell.?off|capitulat|liquidat|outflow|dump|exit.?liquidity", -2, "distribution"),
    (r"bearish|warning|overbought|top.?signal|correction", -2, "caution"),
    (r"sec.{0,15}(sue|reject|ban|lawsuit)|regulat.{0,15}crack", -2, "regulatory"),
]


def _parse_published(published: str) -> datetime | None:
    if not published:
        return None
    try:
        return datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return None


def _articles_in_window(articles: list[dict], hours: float) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = hours * 3600
    recent = []
    for a in articles:
        pub = _parse_published(a.get("published", ""))
        if pub and (now - pub).total_seconds() <= cutoff:
            recent.append(a)
    return recent


def _score_text_patterns(text: str, patterns: list[tuple[str, int, str]]) -> tuple[float, list[str]]:
    score = 0.0
    tags: list[str] = []
    lower = text.lower()
    for pattern, weight, tag in patterns:
        if re.search(pattern, lower):
            score += weight
            if tag not in tags:
                tags.append(tag)
    return score, tags


def _scan_article(article: dict) -> dict[str, Any]:
    text = f"{article.get('title', '')} {article.get('summary', '')}"
    recruit, recruit_tags = _score_text_patterns(text, RECRUITMENT_PATTERNS)
    fear, fear_tags = _score_text_patterns(text, DISTRIBUTION_PATTERNS)
    sentiment = article.get("sentiment", "neutral")
    sent_boost = {"bullish": 1.5, "bearish": -1.5}.get(sentiment, 0)
    return {
        "title": article.get("title", ""),
        "source": article.get("source", ""),
        "published": article.get("published", ""),
        "recruitment_score": recruit + sent_boost if recruit > 0 else recruit,
        "fear_score": abs(fear) + (abs(sent_boost) if fear == 0 and sentiment == "bearish" else 0),
        "recruit_tags": recruit_tags,
        "fear_tags": fear_tags,
        "net_attention": recruit + fear + sent_boost,
    }


def _fetch_coingecko_context() -> dict[str, Any]:
    if _coingecko_cache["data"] and time.time() - _coingecko_cache["fetched_at"] < COINGECKO_TTL:
        return _coingecko_cache["data"]

    result: dict[str, Any] = {
        "trending_rank": None,
        "trending_score": 0,
        "sentiment_up_pct": None,
        "watchlist_users": None,
        "market_cap_rank": 1,
        "error": None,
    }
    try:
        trending = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            timeout=8,
            headers={"Accept": "application/json"},
        )
        if trending.ok:
            coins = trending.json().get("coins") or []
            for i, item in enumerate(coins):
                coin = item.get("item") or {}
                if (coin.get("symbol") or "").upper() == "BTC" or coin.get("id") == "bitcoin":
                    result["trending_rank"] = i + 1
                    result["trending_score"] = max(0, 100 - i * 12)
                    break
            if result["trending_rank"] is None and coins:
                result["trending_score"] = 15

        coin = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin",
            params={"localization": "false", "tickers": "false", "market_data": "true",
                    "community_data": "true", "developer_data": "false"},
            timeout=8,
            headers={"Accept": "application/json"},
        )
        if coin.ok:
            data = coin.json()
            cd = data.get("community_data") or {}
            md = data.get("market_data") or {}
            result["sentiment_up_pct"] = cd.get("sentiment_votes_up_percentage")
            result["watchlist_users"] = cd.get("watchlist_portfolio_users")
            result["market_cap_rank"] = (md.get("market_cap_rank") or 1)
    except requests.RequestException as exc:
        result["error"] = str(exc)
        logger.warning("CoinGecko attention fetch failed: %s", exc)

    _coingecko_cache["data"] = result
    _coingecko_cache["fetched_at"] = time.time()
    return result


def _clamp(value: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, value))


def _detect_phase(ali: float, momentum: float, recruitment: float, fear: float) -> str:
    if ali >= 82 and momentum <= -5:
        return "PEAK"
    if ali >= 70 and momentum >= 8:
        return "HYPE"
    if ali >= 45 and momentum >= 3 and recruitment > fear:
        return "ACCUMULATION"
    if ali >= 50 and momentum <= -8:
        return "FADING"
    if ali < 28:
        return "QUIET"
    if recruitment > fear + 8:
        return "ACCUMULATION"
    return "NEUTRAL"


def _price_attention_divergence(
    price_change_pct: float | None,
    buzz_momentum: float,
    ali: float,
) -> dict[str, Any]:
    if price_change_pct is None:
        return {"type": "none", "label": "No price data", "bias": "neutral"}

    pct = float(price_change_pct)
    if pct > 1.5 and buzz_momentum < -6:
        return {
            "type": "exhaustion",
            "label": "Price up but buzz fading — late-cycle exhaustion risk",
            "bias": "bearish",
        }
    if pct < -1.0 and buzz_momentum > 8 and ali >= 40:
        return {
            "type": "contrarian_recruitment",
            "label": "Dip + rising buzz — influencers recruiting dip buyers",
            "bias": "bullish",
        }
    if abs(pct) < 0.8 and buzz_momentum > 10 and ali >= 35:
        return {
            "type": "stealth_accumulation",
            "label": "Buzz building before price — early liquidity wave",
            "bias": "bullish",
        }
    if pct > 3 and ali >= 80:
        return {
            "type": "euphoria",
            "label": "Price + attention both extreme — exit liquidity zone",
            "bias": "caution",
        }
    return {"type": "aligned", "label": "Price and attention roughly aligned", "bias": "neutral"}


def _attention_signal(
    ali: float,
    phase: str,
    recruitment: float,
    fear: float,
    divergence: dict[str, Any],
) -> tuple[str, float, str]:
    net = recruitment - fear
    div_type = divergence.get("type", "none")

    if phase == "QUIET" and ali < 25:
        return "WAIT", 42.0, "Thin attention — low narrative liquidity"

    if div_type == "exhaustion":
        return "SELL", min(88, 58 + ali * 0.25), divergence["label"]

    if div_type == "euphoria":
        return "WAIT", 52.0, divergence["label"]

    if div_type in ("stealth_accumulation", "contrarian_recruitment"):
        if net < -5:
            return "WAIT", 48.0, f"{divergence['label']} — fear/exit talk still dominates"
        conf = min(85, max(55, 58 + net * 1.2 + ali * 0.15))
        return "BUY", conf, divergence["label"]

    if phase == "FADING" and net < 5:
        return "SELL", min(82, 55 + (ali - 30) * 0.3), "Attention fading — next buyers not showing up"

    if phase in ("ACCUMULATION", "HYPE") and net >= 8:
        conf = min(90, max(58, 52 + net * 1.5 + ali * 0.2))
        return "BUY", conf, "Narrative recruiting buyers — attention as liquidity inflow"

    if phase == "PEAK" and net >= 5:
        return "WAIT", 48.0, "Peak attention — late entrants may be exit liquidity"

    if net >= 12 and ali >= 40:
        return "BUY", min(78, max(58, 50 + net)), "Strong recruitment language in headlines"

    if fear > recruitment + 10:
        return "SELL", min(80, max(58, 50 + fear - recruitment)), "Fear/distribution narrative dominating"

    return "WAIT", 45.0, "Mixed attention signals — no clear liquidity edge"


def build_attention_liquidity(
    news: list[dict] | None = None,
    news_sent: dict | None = None,
    quote: dict | None = None,
) -> dict[str, Any]:
    """Build Attention Liquidity Index and trading bias from news + market context."""
    articles = news or []
    news_sent = news_sent or {}
    quote = quote or {}

    scanned = [_scan_article(a) for a in articles]
    recent_1h = _articles_in_window(articles, 1)
    recent_6h = _articles_in_window(articles, 6)
    recent_24h = _articles_in_window(articles, 24)

    buzz_1h = len(recent_1h)
    buzz_6h = len(recent_6h)
    buzz_24h = len(recent_24h)

    recruitment_total = sum(s["recruitment_score"] for s in scanned)
    fear_total = sum(s["fear_score"] for s in scanned)

    # Normalize component scores to 0–100
    buzz_volume = _clamp(buzz_24h * 8 + buzz_6h * 3, 0, 100)
    if buzz_6h:
        hourly_rate = buzz_1h / max(buzz_6h / 6, 0.5)
        buzz_momentum = _clamp((hourly_rate - 1.0) * 40 + (buzz_1h - buzz_6h / 6) * 15, -50, 50)
    else:
        buzz_momentum = _clamp(buzz_1h * 12, 0, 50)

    marketing_score = _clamp(recruitment_total * 4, 0, 100)
    fear_score = _clamp(fear_total * 4, 0, 100)
    news_velocity = _clamp(news_sent.get("recent_1h", 0) * 15 + buzz_1h * 10, 0, 100)

    gecko = _fetch_coingecko_context()
    trending_score = gecko.get("trending_score", 0)
    sentiment_up = gecko.get("sentiment_up_pct")
    community_score = 0.0
    if sentiment_up is not None:
        community_score = _clamp(float(sentiment_up) * 0.85, 0, 100)

    ali = round(
        buzz_volume * 0.22
        + marketing_score * 0.28
        + news_velocity * 0.18
        + trending_score * 0.12
        + community_score * 0.10
        + max(0, buzz_momentum) * 0.10,
        1,
    )

    _scan_history.append({"ali": ali, "ts": time.time()})
    hist_momentum = 0.0
    if len(_scan_history) >= 4:
        recent = list(_scan_history)[-3:]
        older = list(_scan_history)[-6:-3]
        if older:
            hist_momentum = sum(h["ali"] for h in recent) / len(recent) - sum(h["ali"] for h in older) / len(older)

    combined_momentum = round(buzz_momentum * 0.6 + hist_momentum * 0.4, 1)
    phase = _detect_phase(ali, combined_momentum, marketing_score, fear_score)

    price_change_pct = quote.get("change_pct")
    divergence = _price_attention_divergence(price_change_pct, combined_momentum, ali)
    signal, confidence, reason = _attention_signal(ali, phase, marketing_score, fear_score, divergence)

    drivers = sorted(scanned, key=lambda x: x["net_attention"], reverse=True)[:6]
    driver_headlines = [
        {
            "title": d["title"],
            "source": d["source"],
            "tags": d["recruit_tags"] + d["fear_tags"],
            "net": round(d["net_attention"], 1),
        }
        for d in drivers if d["net_attention"] != 0
    ][:5]

    liquidity_label = {
        "QUIET": "Dry — few new buyers being recruited",
        "ACCUMULATION": "Building — narrative pulling in buyers",
        "HYPE": "Hot — marketing & influencers active",
        "PEAK": "Saturated — max attention, late buyers risk",
        "FADING": "Cooling — liquidity leaving the narrative",
        "NEUTRAL": "Mixed — watch for recruitment shifts",
    }.get(phase, "Mixed")

    return {
        "index": ali,
        "phase": phase,
        "liquidity_label": liquidity_label,
        "signal": signal,
        "confidence": round(confidence, 1),
        "reason": reason,
        "momentum": combined_momentum,
        "divergence": divergence,
        "components": {
            "buzz_volume": round(buzz_volume, 1),
            "marketing_influencer": round(marketing_score, 1),
            "news_velocity": round(news_velocity, 1),
            "fear_distribution": round(fear_score, 1),
            "coingecko_trending": round(trending_score, 1),
            "community_sentiment": round(community_score, 1),
        },
        "counts": {
            "headlines_1h": buzz_1h,
            "headlines_6h": buzz_6h,
            "headlines_24h": buzz_24h,
            "recruitment_hits": sum(1 for s in scanned if s["recruitment_score"] > 0),
            "fear_hits": sum(1 for s in scanned if s["fear_score"] > 0),
        },
        "coingecko": gecko,
        "drivers": driver_headlines,
        "analogy": "Attention is liquidity — buzz and influencers recruit the next buyer",
    }


def blend_attention_into_signal(
    technical_signal: str,
    technical_conf: float,
    final_signal: str,
    final_conf: float,
    attention: dict[str, Any],
) -> tuple[str, float, str, list[str]]:
    """Merge attention liquidity bias into the dashboard signal."""
    notes: list[str] = []
    att_signal = attention.get("signal", "WAIT")
    att_conf = float(attention.get("confidence", 40))
    ali = attention.get("index", 0)
    phase = attention.get("phase", "NEUTRAL")

    notes.append(f"Attention Liquidity {ali:.0f}/100 ({phase}) — {attention.get('liquidity_label', '')}")

    if att_signal == "WAIT" or att_conf < 58:
        return final_signal, final_conf, "technical", notes

    source = "technical"

    if att_signal == final_signal:
        blended = round(final_conf * 0.55 + att_conf * 0.45, 1)
        notes.append(f"Attention aligns with {att_signal} — liquidity supports move")
        source = "attention+technical" if att_conf >= 60 else source
        return final_signal, min(97, blended), source, notes

    if att_conf >= 72:
        blended = round(technical_conf * 0.35 + att_conf * 0.65, 1)
        notes.append(attention.get("reason", "Attention-driven bias"))
        return att_signal, min(92, blended), "attention", notes

    if att_conf >= 65 and technical_signal == "WAIT":
        notes.append(attention.get("reason", ""))
        return att_signal, att_conf, "attention", notes

    notes.append(f"Attention suggests {att_signal} but technical disagrees — reduced weight")
    return final_signal, final_conf, source, notes