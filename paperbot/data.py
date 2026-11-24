"""Live/historical 1m candles from public, key-less exchange endpoints via ccxt.

Only *public market data* methods are used. No API keys, no order endpoints.
Exchanges are tried in order (default Binance.US -> Kraken -> Coinbase Exchange)
so it works from the US where binance.com is geo-blocked.
"""
from __future__ import annotations

import logging
import time

import ccxt
import pandas as pd

from .utils import call_with_timeout

log = logging.getLogger("paperbot.data")
TF_MS = 60_000
COLUMNS = ["open", "high", "low", "close", "volume"]


def to_frame(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts"] + COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz="UTC", name="ts"))
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop_duplicates("ts").set_index("ts").sort_index()
    return df[COLUMNS].astype(float)


def closed_only(df: pd.DataFrame, now_ms: int | None = None) -> pd.DataFrame:
    """Drop the still-forming candle (a 1m candle is closed once ts+60s <= now)."""
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = pd.Timestamp(now_ms - TF_MS, unit="ms", tz="UTC")
    return df[df.index <= cutoff]


class MarketData:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        dcfg = cfg["data"]
        self.exchange_ids: list[str] = list(dcfg["exchanges"])
        self.quotes: dict = dcfg.get("quote", {})
        self.timeout = int(dcfg.get("request_timeout_ms", 15000))
        self.max_stale = float(dcfg.get("max_staleness_seconds", 150))
        self._clients: dict[str, ccxt.Exchange] = {}
        self.active: str | None = None  # sticky exchange once one works

    def _client(self, ex_id: str) -> ccxt.Exchange:
        if ex_id not in self._clients:
            klass = getattr(ccxt, ex_id)
            self._clients[ex_id] = klass({"enableRateLimit": True, "timeout": self.timeout})
        return self._clients[ex_id]

    def _ohlcv(self, ex_id: str, *args, **kwargs) -> list:
        """fetch_ohlcv with a hard wall-clock limit; a hung client is discarded."""
        try:
            return call_with_timeout(self._client(ex_id).fetch_ohlcv, self.timeout / 1000 + 5, *args, **kwargs)
        except TimeoutError:
            self._clients.pop(ex_id, None)  # fresh HTTP session next time
            raise

    def symbol(self, coin: str, ex_id: str) -> str:
        return f"{coin}/{self.quotes.get(ex_id, 'USD')}"

    def _order(self) -> list[str]:
        if self.active and self.active in self.exchange_ids:
            return [self.active] + [e for e in self.exchange_ids if e != self.active]
        return self.exchange_ids

    def fetch_recent(self, coin: str, limit: int = 10) -> pd.DataFrame:
        """Most recent *closed* 1m candles, with exchange fallback."""
        last_err = None
        for ex_id in self._order():
            try:
                rows = self._ohlcv(ex_id, self.symbol(coin, ex_id), "1m", limit=limit + 2)
                df = closed_only(to_frame(rows))
                if df.empty:
                    raise RuntimeError("no candles returned")
                age = time.time() - (df.index[-1].timestamp() + 60)
                if age > self.max_stale:  # e.g. Coinbase public candles can lag several minutes
                    raise RuntimeError(f"stale data: last closed candle is {age:.0f}s old")
                if self.active != ex_id:
                    log.info("Market data source: %s (%s)", ex_id, self.symbol(coin, ex_id))
                self.active = ex_id
                return df.tail(limit)
            except Exception as e:  # network / geo-block / symbol errors
                last_err = e
                log.warning("fetch_recent %s on %s failed: %s", coin, ex_id, str(e)[:200])
        raise RuntimeError(f"All exchanges failed for {coin}: {last_err}")

    def fetch_history(self, coin: str, n: int) -> pd.DataFrame:
        """Paginate backwards-in-time to get ~n closed 1m candles."""
        last_err = None
        for ex_id in self._order():
            try:
                sym = self.symbol(coin, ex_id)
                now = int(time.time() * 1000)
                since = now - (n + 5) * TF_MS
                page = 300 if ex_id.startswith("coinbase") else 1000
                frames, guard = [], 0
                while since < now - TF_MS and guard < 60:
                    guard += 1
                    rows = self._ohlcv(ex_id, sym, "1m", since=since, limit=page)
                    if not rows:
                        since += page * TF_MS
                        continue
                    frames.append(to_frame(rows))
                    nxt = rows[-1][0] + TF_MS
                    if nxt <= since:
                        break
                    since = nxt
                df = closed_only(pd.concat(frames)) if frames else to_frame([])
                df = df[~df.index.duplicated(keep="last")].sort_index()
                if len(df) < min(200, n):
                    raise RuntimeError(f"only {len(df)} candles")
                self.active = ex_id
                log.info("Loaded %d historical 1m candles for %s from %s (%s -> %s)",
                         len(df), coin, ex_id, df.index[0], df.index[-1])
                return df.tail(n)
            except Exception as e:
                last_err = e
                log.warning("fetch_history %s on %s failed: %s", coin, ex_id, str(e)[:200])
        raise RuntimeError(f"History bootstrap failed for {coin}: {last_err}")
