"""Paper-only stock watchlist scanner using Alpaca's public market-data API."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler

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
        self.learning_path = Path(stocks.get("learning_model_file", "data/models/stocks_online.joblib"))
        if not self.learning_path.is_absolute():
            self.learning_path = Path(__file__).resolve().parents[1] / self.learning_path
        self.learning: dict[str, dict] = {}
        if self.learning_path.exists():
            try:
                self.learning = joblib.load(self.learning_path)
                log.info("Loaded saved stock learning model for %d symbols", len(self.learning))
            except Exception as exc:
                log.warning("Could not load stock learning model; starting fresh: %s", exc)

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
            headers = {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}
            all_bars: dict[str, list[dict]] = {}
            page_token = None
            while True:
                if page_token:
                    params["page_token"] = page_token
                else:
                    params.pop("page_token", None)
                response = self.session.get(
                    f"{self.base_url.rstrip('/')}/v2/stocks/bars", params=params,
                    headers=headers, timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
                for symbol, bars in payload.get("bars", {}).items():
                    all_bars.setdefault(symbol, []).extend(bars)
                next_token = payload.get("next_page_token")
                if not next_token:
                    break
                if next_token == page_token:
                    raise RuntimeError("Market data returned the same page token twice.")
                page_token = next_token
            results = []
            for symbol in self.symbols:
                result = self._evaluate(symbol, all_bars.get(symbol, []))
                if result:
                    results.append(result)
            if results:
                self.learning_path.parent.mkdir(parents=True, exist_ok=True)
                joblib.dump(self.learning, self.learning_path)
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

    def _evaluate(self, symbol: str, bars: list[dict]) -> dict | None:
        if not bars:
            return None
        frame = pd.DataFrame(bars)
        frame["t"] = pd.to_datetime(frame["t"], utc=True)
        for col in ("o", "h", "l", "c", "v"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["c"]).sort_values("t").set_index("t", drop=False)
        if frame.empty:
            return None
        close = frame["c"]
        learned = self._learn(symbol, frame)
        price = float(close.iloc[-1])
        candle_time = frame["t"].iloc[-1].isoformat()
        if len(frame) < 50:
            action, reason = "WAIT", f"Need 50 completed 15-minute bars; received {len(frame)}."
        elif learned is not None:
            probability, updates, accuracy = learned
            if probability >= 0.58:
                action = "BUY"
            elif probability <= 0.42:
                action = "SELL"
            else:
                action = "HOLD"
            accuracy_text = f"; recent hit rate {accuracy:.0%}" if accuracy is not None else ""
            reason = (f"Adaptive model estimates {probability:.0%} chance of a higher close over the next hour; "
                      f"trained on {updates} past 15-minute outcomes{accuracy_text}.")
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

    def _learn(self, symbol: str, frame: pd.DataFrame) -> tuple[float, int, float | None] | None:
        """Train on matured four-bar outcomes and predict the next four-bar direction."""
        close, volume = frame["c"], frame["v"].fillna(0)
        X = pd.DataFrame(index=frame.index)
        X["ret_1"] = close.pct_change(1)
        X["ret_3"] = close.pct_change(3)
        X["ret_5"] = close.pct_change(5)
        X["ema_ratio"] = close.ewm(span=20, adjust=False).mean() / close.ewm(span=50, adjust=False).mean() - 1
        delta = close.diff()
        gains = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        losses = -delta.clip(upper=0).ewm(alpha=1 / 14, adjust=False).mean()
        rs = gains / losses.replace(0, np.nan)
        X["rsi"] = 100 - 100 / (1 + rs)
        X["volume_z"] = (volume - volume.rolling(20).mean()) / volume.rolling(20).std().replace(0, np.nan)
        X["range_pct"] = (frame["h"] - frame["l"]) / close
        times = pd.to_datetime(frame["t"], utc=True)
        minute = times.dt.hour * 60 + times.dt.minute
        X["minute_sin"] = np.sin(2 * np.pi * minute / 1440).to_numpy()
        X["minute_cos"] = np.cos(2 * np.pi * minute / 1440).to_numpy()
        X = X.replace([np.inf, -np.inf], np.nan)
        prediction_features = X.iloc[[-1]].copy()
        future = close.shift(-4)
        y = (future > close).astype(int)
        valid = X.notna().all(axis=1) & future.notna()
        X, y = X[valid], y[valid]
        if X.empty:
            return None

        state = self.learning.setdefault(symbol, {
            "scaler": StandardScaler(),
            "model": SGDClassifier(loss="log_loss", alpha=0.001, learning_rate="optimal", random_state=42),
            "ready": False, "last_trained": None, "updates": 0, "pending": {}, "hits": [],
        })
        # Score prior live predictions only after their four-bar outcome is known.
        for ts, label in y.items():
            key = ts.isoformat()
            if key in state["pending"]:
                state["hits"].append(int(state["pending"].pop(key) == int(label)))
        state["hits"] = state["hits"][-200:]

        new = X if state["last_trained"] is None else X[X.index > pd.Timestamp(state["last_trained"])]
        new_y = y.loc[new.index]
        # Warm up on a small history, then update as soon as each new label matures.
        if (not state["ready"] and len(new) >= 20) or (state["ready"] and len(new) >= 1):
            state["scaler"].partial_fit(new.values)
            scaled = state["scaler"].transform(new.values)
            if state["ready"]:
                state["model"].partial_fit(scaled, new_y.values)
            else:
                state["model"].partial_fit(scaled, new_y.values, classes=np.array([0, 1]))
                state["ready"] = True
            state["updates"] += len(new)
            state["last_trained"] = new.index[-1].isoformat()

        if not state["ready"]:
            return None
        latest = prediction_features
        if latest.isna().any(axis=1).iloc[0]:
            return None
        probability = float(state["model"].predict_proba(state["scaler"].transform(latest.values))[0, 1])
        state["pending"][latest.index[-1].isoformat()] = int(probability >= 0.5)
        if len(state["pending"]) > 200:
            state["pending"] = dict(list(state["pending"].items())[-100:])
        accuracy = float(np.mean(state["hits"])) if len(state["hits"]) >= 10 else None
        return probability, int(state["updates"]), accuracy
