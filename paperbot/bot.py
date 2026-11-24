"""Live paper-trading loop: live 1m candles -> features -> AI + indicators + sentiment -> simulated trades."""
from __future__ import annotations

import logging
import signal as sigmod
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from . import DISCLAIMER
from .broker import PaperBroker
from .data import MarketData
from .features import compute_indicators
from .model import HybridModel
from .sentiment import SentimentEngine
from .storage import Storage
from .strategy import EnsembleStrategy

log = logging.getLogger("paperbot.bot")


class PaperTradingBot:
    def __init__(self, cfg: dict, trade: bool = True):
        self.cfg = cfg
        self.trade = trade  # False = dry-run (signals only, no simulated orders)
        self.coins = [c.upper() for c in cfg["coins"]]
        self.data = MarketData(cfg)
        self.sentiment = SentimentEngine(cfg)
        self.strategy = EnsembleStrategy(cfg)
        st = cfg["storage"]
        self.broker = PaperBroker(cfg, st["state_file"] if trade else None)
        self.store = Storage(st["sqlite"], st["trades_csv"], mode="live" if trade else "dry-run")
        self.models = {c: HybridModel(c, cfg) for c in self.coins}
        self.history: dict[str, pd.DataFrame] = {}
        self.max_hist = int(cfg["data"]["max_history"])
        self.tz = ZoneInfo(cfg["logging"].get("display_timezone", "America/Chicago"))
        self._stop = False

    # ---------- setup ----------
    def bootstrap(self) -> None:
        log.info(DISCLAIMER)
        n = int(self.cfg["data"]["bootstrap_candles"])
        for c in self.coins:
            self.history[c] = self.data.fetch_history(c, n)
            self.store.log_candles(c, self.history[c])
            m = self.models[c]
            m.load()
            ind = compute_indicators(self.history[c])
            if m.needs_batch_retrain():
                m.fit_batch(ind)
            else:
                k = m.update_online(ind)
                log.info("[%s] caught up online learner on %d new rows", c, k)
            self.broker.mark(c, float(self.history[c]["close"].iloc[-1]))

    # ---------- one cycle ----------
    def step(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        snap = self.sentiment.snapshot(self.coins)
        results = []
        for c in self.coins:
            hist = self.history[c]
            new = pd.DataFrame()
            for attempt in range(4):  # exchanges can publish the closed candle a few seconds late
                recent = self.data.fetch_recent(c, limit=10)
                new = recent[recent.index > hist.index[-1]]
                if not new.empty or self._stop:
                    break
                time.sleep(5)
            if new.empty:
                log.info("[%s] no new closed candle yet", c)
                continue
            hist = pd.concat([hist, new]).tail(self.max_hist)
            self.history[c] = hist
            self.store.log_candles(c, new)
            model = self.models[c]
            ind = compute_indicators(hist)
            n_upd = model.update_online(ind)
            if model.needs_batch_retrain():
                log.info("[%s] scheduled batch retrain", c)
                model.fit_batch(ind)
            else:
                model.save()
            last_candle = new.iloc[-1]
            close = float(last_candle["close"])
            self.broker.mark(c, close)
            # stop-loss / take-profit on every newly closed candle
            if self.trade:
                for ts, row in new.iterrows():
                    t = self.broker.check_exits(c, float(row["high"]), float(row["low"]), now)
                    if t:
                        self.store.log_trade(t)
                for t in self.broker.update_risk(now):
                    self.store.log_trade(t)
            pred = model.predict_up(ind)
            last = ind.iloc[-1]
            sent = self.sentiment.coin_score(snap, c)
            has_pos = c in self.broker.pf.positions
            sig = self.strategy.evaluate(c, last, pred["p_up"], sent, has_pos,
                                         self.trade and self.broker.can_enter(c, now),
                                         self.broker.held_minutes(c, now))
            if self.trade:
                if sig.action == "BUY":
                    t = self.broker.buy(c, close, float(last["atr"]), now, sig.reason)
                    if t:
                        self.store.log_trade(t)
                elif sig.action == "SELL":
                    self.store.log_trade(self.broker.sell(c, close, now, sig.reason))
            local = hist.index[-1].tz_convert(self.tz).strftime("%H:%M %Z")
            acc = model.live_acc
            log.info("[%s] candle %s close $%.2f | RSI %.1f | P(up) %.3f (online %s / batch %s) | "
                     "AI %+.2f IND %+.2f SENT %+.2f => score %+.3f %s (%s) | +%d online upd%s",
                     c, local, close, last["rsi_14"], pred["p_up"], _f(pred["p_online"]), _f(pred["p_batch"]),
                     sig.ai_score, sig.indicator_score, sig.sentiment_score, sig.score, sig.action, sig.reason,
                     n_upd, f", live acc {acc:.2f}" if acc is not None else "")
            row = {"time": now.isoformat(), "candle_time": hist.index[-1].isoformat(), "close": close,
                   "p_online": pred["p_online"], "p_batch": pred["p_batch"], "rsi": float(last["rsi_14"]),
                   "fear_greed": snap.fear_greed_value, **sig.to_dict()}
            self.store.log_signal(row)
            results.append(row)
        if self.trade:
            pf = self.broker.pf
            eq = self.broker.equity()
            self.store.log_equity(now.isoformat(), pf.cash, eq, pf.realized_pnl, self.broker.drawdown(), len(pf.positions))
            self.broker.save()
            pos = ", ".join(f"{c} {p.qty:.6f}@{p.entry_price:.2f}" for c, p in pf.positions.items()) or "none"
            pause = " | HALTED (drawdown guard)" if pf.halted else (
                " | PAUSED (daily loss limit until 00:00 UTC)" if pf.daily_halted else "")
            log.info("PORTFOLIO equity $%.2f (%+.2f%%) | cash $%.2f | realized $%+.4f | fees $%.4f | "
                     "trades %d | open: %s%s", eq, 100 * (eq / pf.starting_balance - 1), pf.cash, pf.realized_pnl,
                     pf.fees_paid, pf.n_trades, pos, pause)
        return results

    # ---------- loop ----------
    def run(self, max_minutes: float | None = None) -> None:
        for s in (sigmod.SIGINT, sigmod.SIGTERM):
            sigmod.signal(s, lambda *_: self._request_stop())
        self.bootstrap()
        interval = max(5.0, float(self.cfg["data"].get("poll_interval_seconds", 30)))
        delay = float(self.cfg["data"].get("poll_delay_seconds", 6))
        deadline = time.time() + max_minutes * 60 if max_minutes else None
        while not self._stop:
            # Poll and log every interval; new decisions still require a completed 1m candle.
            wait = interval - (time.time() % interval) + delay
            if deadline and time.time() + wait > deadline:
                log.info("Reached --minutes limit, stopping.")
                break
            if self._sleep(wait):
                break
            try:
                self.step()
            except Exception:
                log.exception("Cycle failed; will retry next poll")
        self.broker.save()
        for m in self.models.values():
            m.save()
        log.info("Stopped. State saved.")

    def _request_stop(self):
        log.info("Shutdown requested...")
        self._stop = True

    def _sleep(self, secs: float) -> bool:
        end = time.time() + secs
        while time.time() < end:
            if self._stop:
                return True
            time.sleep(min(1.0, end - time.time()))
        return self._stop


def _f(x):
    return "n/a" if x is None else f"{x:.3f}"
