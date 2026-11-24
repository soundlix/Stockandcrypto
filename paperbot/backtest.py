"""Walk-forward backtest on recent real 1m candles (simulation of the live loop).

The batch model is trained only on the first part of the window; the rest is
replayed candle by candle with online learning, stops and fees exactly as live.
Sentiment is held at today's value (historical sentiment isn't available for free
at 1m resolution) or disabled with --no-sentiment.
"""
from __future__ import annotations

import logging
from datetime import timezone

import numpy as np
import pandas as pd

from .broker import PaperBroker
from .data import MarketData
from .features import compute_indicators
from .model import HybridModel
from .sentiment import SentimentEngine
from .storage import Storage
from .strategy import EnsembleStrategy

log = logging.getLogger("paperbot.backtest")


def run_backtest(cfg: dict, candles: int = 3000, train_frac: float = 0.5, use_sentiment: bool = True,
                 histories: dict[str, pd.DataFrame] | None = None, persist: bool = True) -> dict:
    coins = [c.upper() for c in cfg["coins"]]
    bt_cfg = {**cfg, "model": {**cfg["model"], "dir": cfg["model"]["dir"] + "/backtest",
                               "retrain_hours": 1e9}}
    import os
    os.makedirs(bt_cfg["model"]["dir"], exist_ok=True)
    md = MarketData(cfg)
    hist = histories or {c: md.fetch_history(c, candles) for c in coins}
    sent = {c: 0.0 for c in coins}
    if use_sentiment and cfg.get("sentiment", {}).get("enabled", True):
        se = SentimentEngine(cfg)
        snap = se.snapshot(coins)
        sent = {c: se.coin_score(snap, c) for c in coins}
    strat = EnsembleStrategy(cfg)
    broker = PaperBroker(cfg, None)
    store = Storage(cfg["storage"]["sqlite"].replace(".db", "_backtest.db"), None, "backtest") if persist else None
    inds, models, start_idx = {}, {}, {}
    for c in coins:
        ind = compute_indicators(hist[c])
        split = int(len(ind) * train_frac)
        m = HybridModel(c, bt_cfg)
        m.fit_batch(ind.iloc[:split])
        inds[c], models[c], start_idx[c] = ind, m, split
    timeline = sorted(set().union(*[set(inds[c].index[start_idx[c]:]) for c in coins]))
    equity_curve, trades = [], []
    for ts in timeline:
        now = ts.to_pydatetime().astimezone(timezone.utc)
        for c in coins:
            ind = inds[c]
            if ts not in ind.index:
                continue
            i = ind.index.get_loc(ts)
            row = hist[c].loc[ts]
            broker.mark(c, float(row["close"]))
            t = broker.check_exits(c, float(row["high"]), float(row["low"]), now)
            if t:
                trades.append(t)
            trades += broker.update_risk(now)
            window = ind.iloc[: i + 1]
            models[c].update_online(window.iloc[-50:])  # only labels known at time ts
            p = models[c].predict_up(window)["p_up"]
            sig = strat.evaluate(c, window.iloc[-1], p, sent[c], c in broker.pf.positions,
                                 broker.can_enter(c, now), broker.held_minutes(c, now))
            if sig.action == "BUY":
                t = broker.buy(c, float(row["close"]), float(window["atr"].iloc[-1]), now, sig.reason)
                if t:
                    trades.append(t)
            elif sig.action == "SELL":
                trades.append(broker.sell(c, float(row["close"]), now, sig.reason))
        equity_curve.append(broker.equity())
    if store:
        for t in trades:
            store.log_trade(t)
    eq = np.array(equity_curve) if equity_curve else np.array([broker.pf.starting_balance])
    peak = np.maximum.accumulate(eq)
    sb = broker.pf.starting_balance
    bh = {}
    if timeline:
        for c in coins:
            prices = hist[c]["close"].sort_index()
            aligned = prices.loc[(prices.index >= timeline[0]) & (prices.index <= timeline[-1])].dropna()
            if len(aligned) >= 2 and aligned.iloc[0] != 0:
                bh[c] = float(aligned.iloc[-1] / aligned.iloc[0] - 1)
    sells = [t for t in trades if t.side == "SELL"]
    res = {
        "candles_replayed": len(timeline),
        "start": str(timeline[0]) if timeline else None, "end": str(timeline[-1]) if timeline else None,
        "final_equity": round(float(eq[-1]), 4), "return_pct": round(100 * (eq[-1] / sb - 1), 3),
        "max_drawdown_pct": round(100 * float(((peak - eq) / peak).max()), 3),
        "round_trips": len(sells), "win_rate_pct": round(100 * np.mean([t.pnl > 0 for t in sells]), 1) if sells else None,
        "fees_paid": round(broker.pf.fees_paid, 4),
        "buy_and_hold_pct": {c: round(100 * v, 3) for c, v in bh.items()},
        "batch_holdout_acc": {c: models[c].batch_val_acc for c in coins},
        "live_acc": {c: models[c].live_acc for c in coins},
        "sentiment_used": sent,
    }
    return res
