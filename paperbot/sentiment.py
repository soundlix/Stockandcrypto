"""Market sentiment from free, key-less sources.

1. Alternative.me Crypto Fear & Greed Index  (https://api.alternative.me/fng/)
2. Crypto news RSS headlines (CoinDesk, Cointelegraph, Decrypt) scored with VADER.
Each returns a score in [-1, 1]; failures degrade gracefully to 0 (neutral).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import requests

from .utils import call_with_timeout

log = logging.getLogger("paperbot.sentiment")

KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth", "ether"],
}
GENERAL = ["crypto", "market", "sec", "etf", "fed", "stablecoin", "altcoin"]


@dataclass
class SentimentSnapshot:
    fear_greed_value: int | None = None
    fear_greed_label: str | None = None
    fear_greed_score: float = 0.0
    news_scores: dict = field(default_factory=dict)   # coin -> score
    news_counts: dict = field(default_factory=dict)   # coin -> headlines used


class SentimentEngine:
    def __init__(self, cfg: dict, session: requests.Session | None = None):
        self.cfg = cfg.get("sentiment", {})
        self.enabled = bool(self.cfg.get("enabled", True))
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = "paperbot/1.0 (educational paper trading)"
        self._fg = (0.0, None, None)  # (fetched_at, value, label)
        self._news = (0.0, [])        # (fetched_at, [headline,...])
        self._vader = None

    # ---------- Fear & Greed ----------
    def fear_greed(self) -> tuple[int | None, str | None]:
        ttl = 60 * float(self.cfg.get("fear_greed_refresh_minutes", 60))
        if self._fg[1] is not None and time.time() - self._fg[0] < ttl:
            return self._fg[1], self._fg[2]
        try:
            r = call_with_timeout(self.session.get, 15, "https://api.alternative.me/fng/?limit=1", timeout=10)
            r.raise_for_status()
            d = r.json()["data"][0]
            self._fg = (time.time(), int(d["value"]), d.get("value_classification"))
            log.info("Fear & Greed index: %s (%s)", self._fg[1], self._fg[2])
        except Exception as e:
            log.warning("Fear & Greed fetch failed: %s", e)
        return self._fg[1], self._fg[2]

    def fear_greed_score(self, value: int | None) -> float:
        if value is None:
            return 0.0
        s = (value - 50) / 50.0
        return -s if self.cfg.get("fear_greed_mode", "contrarian") == "contrarian" else s

    # ---------- News ----------
    def _analyzer(self):
        if self._vader is None:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            self._vader = SentimentIntensityAnalyzer()
        return self._vader

    def headlines(self) -> list[str]:
        ttl = 60 * float(self.cfg.get("news_refresh_minutes", 15))
        if self._news[1] and time.time() - self._news[0] < ttl:
            return self._news[1]
        import feedparser
        items: list[str] = []
        for url in self.cfg.get("news_feeds", []):
            try:
                r = call_with_timeout(self.session.get, 15, url, timeout=10)
                r.raise_for_status()
                feed = feedparser.parse(r.content)
                items += [e.get("title", "") for e in feed.entries[:40] if e.get("title")]
            except Exception as e:
                log.warning("News feed %s failed: %s", url, str(e)[:120])
        if items:
            self._news = (time.time(), items)
            log.info("Fetched %d news headlines", len(items))
        return self._news[1]

    def news_score(self, coin: str, heads: list[str]) -> tuple[float, int]:
        if not heads:
            return 0.0, 0
        kw = KEYWORDS.get(coin.upper(), [coin.lower()])
        an = self._analyzer()

        def match(h, words):
            return re.search(r"\b(" + "|".join(map(re.escape, words)) + r")\b", h, re.I) is not None

        specific = [h for h in heads if match(h, kw)]
        pool = specific if len(specific) >= 3 else specific + [h for h in heads if match(h, GENERAL)]
        if not pool:
            pool = heads
        scores = [an.polarity_scores(h)["compound"] for h in pool]
        return float(sum(scores) / len(scores)), len(pool)

    # ---------- Combined ----------
    def snapshot(self, coins: list[str]) -> SentimentSnapshot:
        snap = SentimentSnapshot()
        if not self.enabled:
            return snap
        val, label = self.fear_greed()
        snap.fear_greed_value, snap.fear_greed_label = val, label
        snap.fear_greed_score = self.fear_greed_score(val)
        heads = self.headlines()
        for c in coins:
            snap.news_scores[c], snap.news_counts[c] = self.news_score(c, heads)
        return snap

    def coin_score(self, snap: SentimentSnapshot, coin: str) -> float:
        if not self.enabled:
            return 0.0
        wf = float(self.cfg.get("fear_greed_weight", 0.5))
        wn = float(self.cfg.get("news_weight", 0.5))
        tot = (wf + wn) or 1.0
        s = (wf * snap.fear_greed_score + wn * snap.news_scores.get(coin, 0.0)) / tot
        return max(-1.0, min(1.0, s))
