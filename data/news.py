"""News aggregator with asset-specific filtering."""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import feedparser
import requests

from data.assets import get_asset

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
    ("DailyFX", "https://www.dailyfx.com/feeds/market-news"),
    ("Investing.com FX", "https://www.investing.com/rss/news.rss"),
    ("ForexLive", "https://www.forexlive.com/feed/news"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
]

SENTIMENT_POSITIVE = re.compile(
    r"rally|surge|gain|rise|bullish|strong|hawkish|beat|exceed|optimis|recovery|upside|soar|jump",
    re.IGNORECASE,
)
SENTIMENT_NEGATIVE = re.compile(
    r"fall|drop|decline|bearish|weak|dovish|miss|concern|recession|downside|selloff|crash|plunge|sink",
    re.IGNORECASE,
)

_rss_cache: dict = {"articles": [], "fetched_at": 0.0}
RSS_CACHE_TTL = 120
RSS_TIMEOUT = 4


def _score_sentiment(text: str) -> str:
    pos = len(SENTIMENT_POSITIVE.findall(text))
    neg = len(SENTIMENT_NEGATIVE.findall(text))
    if pos > neg + 1:
        return "bullish"
    if neg > pos + 1:
        return "bearish"
    return "neutral"


def _fetch_single_feed(source: str, url: str) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    try:
        resp = requests.get(url, timeout=RSS_TIMEOUT, headers={"User-Agent": "ProTrader/1.0"})
        feed = feedparser.parse(resp.content)
        for entry in feed.entries[:20]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            if not title or not link:
                continue
            summary = entry.get("summary", entry.get("description", ""))
            summary = re.sub(r"<[^>]+>", "", summary)[:300]
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                dt = datetime(*published[:6], tzinfo=timezone.utc)
                pub_str = dt.isoformat()
            else:
                pub_str = datetime.now(timezone.utc).isoformat()
            articles.append({
                "source": source,
                "title": title,
                "summary": summary,
                "link": link,
                "published": pub_str,
                "sentiment": _score_sentiment(f"{title} {summary}"),
            })
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", source, exc)
    return articles


def _fetch_all_rss() -> list[dict[str, Any]]:
    if _rss_cache["articles"] and time.time() - _rss_cache["fetched_at"] < RSS_CACHE_TTL:
        return _rss_cache["articles"]

    articles: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(RSS_FEEDS)) as pool:
        futures = [pool.submit(_fetch_single_feed, source, url) for source, url in RSS_FEEDS]
        for future in as_completed(futures):
            articles.extend(future.result())

    _rss_cache["articles"] = articles
    _rss_cache["fetched_at"] = time.time()
    return articles


def fetch_news(limit: int = 15, asset_id: str = "eurusd") -> list[dict[str, Any]]:
    from data.fxbook import fetch_fxbook_news

    asset = get_asset(asset_id)
    keywords = asset["news_keywords"]
    articles: list[dict[str, Any]] = []
    seen: set[str] = set()

    for article in fetch_fxbook_news(limit, asset_id):
        link = article.get("link", "")
        if link and link not in seen:
            seen.add(link)
            articles.append(article)

    for article in _fetch_all_rss():
        link = article.get("link", "")
        if not link or link in seen:
            continue
        combined = f"{article['title']} {article.get('summary', '')}"
        if not keywords.search(combined):
            continue
        seen.add(link)
        articles.append(article)

    articles.sort(key=lambda x: x["published"], reverse=True)
    return articles[:limit]


def news_sentiment_summary(articles: list[dict]) -> dict[str, Any]:
    if not articles:
        return {
            "overall": "neutral",
            "bullish": 0,
            "bearish": 0,
            "neutral": 0,
            "score": 0,
            "total": 0,
            "bullish_pct": 0,
            "bearish_pct": 0,
            "sources": {},
            "myfxbook_count": 0,
            "recent_1h": 0,
        }

    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    sources: dict[str, int] = {}
    myfxbook_count = 0
    recent_1h = 0
    now = datetime.now(timezone.utc)

    for a in articles:
        sentiment = a.get("sentiment", "neutral")
        counts[sentiment] = counts.get(sentiment, 0) + 1
        source = a.get("source", "Unknown")
        sources[source] = sources.get(source, 0) + 1
        if source == "MyFXBook":
            myfxbook_count += 1
        try:
            pub = datetime.fromisoformat(a.get("published", "").replace("Z", "+00:00"))
            if (now - pub).total_seconds() <= 3600:
                recent_1h += 1
        except Exception:
            pass

    total = len(articles)
    score = counts["bullish"] - counts["bearish"]
    if score >= 2:
        overall = "bullish"
    elif score <= -2:
        overall = "bearish"
    else:
        overall = "neutral"

    return {
        **counts,
        "overall": overall,
        "score": score,
        "total": total,
        "bullish_pct": round(counts["bullish"] / total * 100, 1),
        "bearish_pct": round(counts["bearish"] / total * 100, 1),
        "sources": sources,
        "myfxbook_count": myfxbook_count,
        "recent_1h": recent_1h,
    }