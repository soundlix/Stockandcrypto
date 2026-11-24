"""Paper-only stock watchlist scanner using Alpaca's public market-data API."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

log = logging.getLogger("paperbot.stocks")


class StockScanner:
    def __init__(self, cfg: dict, store):
        self.cfg = cfg
        self.store = store
        stocks = cfg.get("stocks", {})
        self.symbols = [s.upper() for s in stocks.get("symbols", [])]
        self.feed = stocks.get("feed", "iex")
        self.base_url = stocks.get("data_url", "https://data.alpaca.markets")
        self.enabled = bool(stocks.get("enabled", True))
        self.timeout = int(stocks.get("request_timeout_seconds", 15))
        self.poll_seconds = int(stocks.get("poll_seconds", 60))
        self.key = os.getenv("ALPACA_API_KEY", "")
        self.secret = os.getenv("ALPACA_API_SECRET", "")
        self.session = requests.Session()

    def scan(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.enabled:
            self.store.log_stock_scan([], "disabled", "Stock scanning is turned off in config.", now)
            return
        if not self.symbols:
            self.store.log_stock_scan([], "not_configured", "No stock symbols are configured.", now)
            return
        if not self.key or not self.secret:
            self.store.log_stock_scan(
                [], "needs_api_keys",
                "Add ALPACA_API_KEY and ALPACA_API_SECRET to the local .env file to enable stock market data.", now,
            )
            return

        params = {
            "symbols": ",".join(self.symbols), "timeframe": "15Min",
            "start": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
            "limit": 10000, "feed": self.feed, "adjustment": "raw", "sort": "asc",
        }
        try:
            response = self.session.get(
                f"{self.base_url.rstrip('/')}/v2/stocks/bars", params=params,
                headers={"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            results = []
            for symbol in self.symbols:
                result = self._evaluate(symbol, payload.get("bars", {}).get(symbol, []))
                if result:
                    results.append(result)
            status = "connected" if results else "no_data"
            detail = f"Scanned {len(results)} of {len(self.symbols)} stocks using Alpaca {self.feed.upper()} data."
            self.store.log_stock_scan(results, status, detail, now)
            log.info("Stock scan: %s", detail)
        except Exception as exc:
            detail = f"Stock data scan failed: {str(exc)[:300]}"
            self.store.log_stock_scan([], "error", detail, now)
            log.warning(detail)

    def run(self, max_minutes: float | None = None) -> None:
        """Continuously check the entire configured watchlist, independent of crypto."""
        deadline = time.monotonic() + max_minutes * 60 if max_minutes else None
        log.info("Stock scanner started for %d symbols; checking every %ds", len(self.symbols), self.poll_seconds)
        try:
            while deadline is None or time.monotonic() < deadline:
                started = time.monotonic()
                self.scan()
                remaining = self.poll_seconds - (time.monotonic() - started)
                if deadline is not None:
                    remaining = min(remaining, max(0, deadline - time.monotonic()))
                if remaining > 0:
                    time.sleep(remaining)
        except KeyboardInterrupt:
            log.info("Stock scanner stopped by user.")

    @staticmethod
    def _evaluate(symbol: str, bars: list[dict]) -> dict | None:
        if not bars:
            return None
        frame = pd.DataFrame(bars)
        frame["t"] = pd.to_datetime(frame["t"], utc=True)
        for col in ("c", "v"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["c"]).sort_values("t")
        if frame.empty:
            return None
        close = frame["c"]
        price = float(close.iloc[-1])
        candle_time = frame["t"].iloc[-1].isoformat()
        if len(frame) < 50:
            action, reason = "WAIT", f"Need 50 completed 15-minute bars; received {len(frame)}."
        else:
            ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
            ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
            delta = close.diff()
            gains = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
            losses = -delta.clip(upper=0).ewm(alpha=1 / 14, adjust=False).mean()
            rs = gains / losses.replace(0, float("nan"))
            rsi = float((100 - 100 / (1 + rs)).iloc[-1]) if pd.notna(rs.iloc[-1]) else 100.0
            volume = pd.to_numeric(frame["v"], errors="coerce").fillna(0)
            avg_volume = volume.rolling(20).mean().iloc[-1]
            volume_ok = pd.notna(avg_volume) and avg_volume > 0 and volume.iloc[-1] >= avg_volume
            if price > ema20 > ema50 and 50 <= rsi <= 70 and volume_ok:
                action, reason = "BUY", "Price is above rising short- and medium-term averages; RSI and volume confirm momentum."
            elif price < ema20 or rsi >= 75:
                action, reason = "SELL", "Price fell below its short-term average or RSI is overbought; momentum weakened."
            else:
                action, reason = "HOLD", "Trend, momentum, and volume do not agree on a clear entry or exit."
        return {"symbol": symbol, "candle_time": candle_time, "price": price,
                "action": action, "reason": reason}
