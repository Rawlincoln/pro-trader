"""News aggregator with asset-specific filtering and multi-source RSS."""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests

from data.assets import get_asset

logger = logging.getLogger(__name__)

# Browser-like headers — many publishers block bare bots
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Global feeds — filtered by asset keywords after fetch
RSS_FEEDS = [
    ("FXStreet", "https://www.fxstreet.com/rss/news"),
    ("ForexLive", "https://www.forexlive.com/feed"),
    ("ForexLive Feedburner", "https://feeds.feedburner.com/forexlive"),
    ("Investing FX", "https://www.investing.com/rss/news_1.rss"),
    ("Investing Commodities", "https://www.investing.com/rss/news_25.rss"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("CNBC Markets", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("MarketWatch", "https://www.marketwatch.com/rss/topstories"),
]

# Asset-specific Google News queries (reliable, high volume)
GOOGLE_NEWS = {
    "eurusd": "EURUSD OR euro dollar OR ECB OR forex EUR",
    "gold": "XAUUSD OR gold price OR bullion OR COMEX gold",
    "bitcoin": "Bitcoin OR BTC crypto OR Bitcoin ETF",
}

SENTIMENT_POSITIVE = re.compile(
    r"rally|surge|gain|rise|bullish|strong|hawkish|beat|exceed|optimis|recovery|upside|soar|jump|"
    r"breakout|record high|hike|tightening",
    re.IGNORECASE,
)
SENTIMENT_NEGATIVE = re.compile(
    r"fall|drop|decline|bearish|weak|dovish|miss|concern|recession|downside|selloff|crash|plunge|"
    r"sink|cut|easing|slump|fear",
    re.IGNORECASE,
)

_rss_cache: dict = {"articles": [], "fetched_at": 0.0}
RSS_CACHE_TTL = 120
RSS_TIMEOUT = 6


def _score_sentiment(text: str) -> str:
    pos = len(SENTIMENT_POSITIVE.findall(text))
    neg = len(SENTIMENT_NEGATIVE.findall(text))
    if pos > neg + 1:
        return "bullish"
    if neg > pos + 1:
        return "bearish"
    return "neutral"


def _parse_feed_entries(source: str, content: bytes) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    feed = feedparser.parse(content)
    for entry in feed.entries[:25]:
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
    return articles


def _fetch_single_feed(source: str, url: str) -> list[dict[str, Any]]:
    try:
        resp = requests.get(url, timeout=RSS_TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
        return _parse_feed_entries(source, resp.content)
    except Exception as exc:
        logger.debug("Failed to fetch %s: %s", source, exc)
        return []


def _google_news_url(query: str) -> str:
    q = quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _fetch_all_rss(asset_id: str = "eurusd") -> list[dict[str, Any]]:
    cache_key = f"rss:{asset_id}"
    entry = _rss_cache.get(cache_key)
    if entry and time.time() - entry["fetched_at"] < RSS_CACHE_TTL:
        return entry["articles"]

    feeds = list(RSS_FEEDS)
    gq = GOOGLE_NEWS.get(asset_id)
    if gq:
        feeds.append((f"Google News ({asset_id})", _google_news_url(gq)))

    articles: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(10, len(feeds))) as pool:
        futures = [pool.submit(_fetch_single_feed, source, url) for source, url in feeds]
        for future in as_completed(futures):
            articles.extend(future.result())

    _rss_cache[cache_key] = {"articles": articles, "fetched_at": time.time()}
    # Keep legacy global key for any old callers
    _rss_cache["articles"] = articles
    _rss_cache["fetched_at"] = time.time()
    return articles


def fetch_news(limit: int = 15, asset_id: str = "eurusd") -> list[dict[str, Any]]:
    from data.fxbook import fetch_fxbook_news

    asset = get_asset(asset_id)
    keywords = asset["news_keywords"]
    articles: list[dict[str, Any]] = []
    seen: set[str] = set()

    # MyFXBook HTML news (often empty if 403) — non-blocking
    for article in fetch_fxbook_news(limit, asset_id):
        link = article.get("link", "")
        if link and link not in seen:
            seen.add(link)
            articles.append(article)

    for article in _fetch_all_rss(asset_id):
        link = article.get("link", "")
        if not link or link in seen:
            continue
        combined = f"{article['title']} {article.get('summary', '')}"
        # Google News already asset-scoped; still apply keywords for global feeds
        is_google = article.get("source", "").startswith("Google News")
        if not is_google and not keywords.search(combined):
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
        src = a.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
        if src == "MyFXBook":
            myfxbook_count += 1
        try:
            pub = datetime.fromisoformat(a["published"].replace("Z", "+00:00"))
            if (now - pub).total_seconds() <= 3600:
                recent_1h += 1
        except (ValueError, TypeError, KeyError):
            pass

    total = sum(counts.values()) or 1
    score = counts["bullish"] - counts["bearish"]
    if score >= 2:
        overall = "bullish"
    elif score <= -2:
        overall = "bearish"
    else:
        overall = "neutral"

    return {
        "overall": overall,
        "bullish": counts["bullish"],
        "bearish": counts["bearish"],
        "neutral": counts["neutral"],
        "score": score,
        "total": total,
        "bullish_pct": round(counts["bullish"] / total * 100, 1),
        "bearish_pct": round(counts["bearish"] / total * 100, 1),
        "sources": sources,
        "myfxbook_count": myfxbook_count,
        "recent_1h": recent_1h,
    }
