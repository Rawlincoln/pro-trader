"""News aggregator with asset-specific filtering."""

from __future__ import annotations

import logging
import re
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


def _score_sentiment(text: str) -> str:
    pos = len(SENTIMENT_POSITIVE.findall(text))
    neg = len(SENTIMENT_NEGATIVE.findall(text))
    if pos > neg + 1:
        return "bullish"
    if neg > pos + 1:
        return "bearish"
    return "neutral"


def fetch_news(limit: int = 15, asset_id: str = "eurusd") -> list[dict[str, Any]]:
    asset = get_asset(asset_id)
    keywords = asset["news_keywords"]
    articles: list[dict[str, Any]] = []
    seen: set[str] = set()

    for source, url in RSS_FEEDS:
        try:
            resp = requests.get(url, timeout=8, headers={"User-Agent": "ProTrader/1.0"})
            feed = feedparser.parse(resp.content)
            for entry in feed.entries[:25]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "")
                summary = entry.get("summary", entry.get("description", ""))
                summary = re.sub(r"<[^>]+>", "", summary)[:300]

                if not title or link in seen:
                    continue
                combined = f"{title} {summary}"
                if not keywords.search(combined):
                    continue

                seen.add(link)
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
                    "sentiment": _score_sentiment(combined),
                })
        except Exception as exc:
            logger.warning("Failed to fetch %s: %s", source, exc)

    articles.sort(key=lambda x: x["published"], reverse=True)
    return articles[:limit]


def news_sentiment_summary(articles: list[dict]) -> dict[str, Any]:
    if not articles:
        return {"overall": "neutral", "bullish": 0, "bearish": 0, "neutral": 0, "score": 0}

    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for a in articles:
        counts[a.get("sentiment", "neutral")] += 1

    score = counts["bullish"] - counts["bearish"]
    if score >= 2:
        overall = "bullish"
    elif score <= -2:
        overall = "bearish"
    else:
        overall = "neutral"

    return {**counts, "overall": overall, "score": score}