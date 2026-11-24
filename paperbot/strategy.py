"""Ensemble signal = AI model + technical indicators + market sentiment."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class Signal:
    coin: str
    action: str          # BUY / SELL / HOLD
    score: float         # ensemble score in [-1, 1]
    ai_score: float
    indicator_score: float
    sentiment_score: float
    p_up: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def indicator_score(last: pd.Series) -> float:
    """Rule-based vote from classic indicators, in [-1, 1]."""
    votes = []
    r = last.get("rsi_14")
    if pd.notna(r):  # oversold bullish, overbought bearish, linear in between
        votes.append(float(np.clip((50 - r) / 20, -1, 1)))
    h = last.get("macd_hist_n")
    if pd.notna(h):
        votes.append(float(np.tanh(h / 2e-4)))
    if pd.notna(last.get("ema_ratio")):
        votes.append(float(np.tanh(last["ema_ratio"] / 5e-4)))
    b = last.get("bb_pctb")
    if pd.notna(b):  # mean reversion at the bands
        votes.append(float(np.clip((0.5 - b) * 2, -1, 1)) * 0.7)
    if not votes:
        return 0.0
    s = float(np.mean(votes))
    vz = last.get("vol_z")
    if pd.notna(vz) and vz > 1.0:  # volume confirmation amplifies conviction
        s *= 1.25
    return float(np.clip(s, -1, 1))


class EnsembleStrategy:
    def __init__(self, cfg: dict):
        s = cfg["strategy"]
        w = s["weights"]
        tot = sum(float(v) for v in w.values()) or 1.0
        self.w_ai = float(w.get("ai", 0)) / tot
        self.w_ind = float(w.get("indicators", 0)) / tot
        self.w_sent = float(w.get("sentiment", 0)) / tot
        self.buy_th = float(s["buy_threshold"])
        self.sell_th = float(s["sell_threshold"])
        self.min_conf = float(s.get("min_ai_confidence", 0.5))
        self.ai_scale = float(s.get("ai_scale", 0.3))        # |p_up-0.5|*2 == ai_scale -> full score
        self.alpha = 2.0 / (float(s.get("score_smoothing", 5)) + 1)  # EMA smoothing of the score
        self.min_hold = float(s.get("min_hold_minutes", 10))
        self.trend_filter = bool(s.get("trend_filter", True))
        self._smooth: dict[str, float] = {}

    def evaluate(self, coin: str, last: pd.Series, p_up: float, sentiment: float,
                 has_position: bool, can_enter: bool = True, held_minutes: float = 1e9) -> Signal:
        ai = float(np.clip((p_up - 0.5) * 2 / self.ai_scale, -1, 1))
        ind = indicator_score(last)
        sent = float(np.clip(sentiment, -1, 1))
        raw = self.w_ai * ai + self.w_ind * ind + self.w_sent * sent
        prev = self._smooth.get(coin)
        score = raw if prev is None else prev + self.alpha * (raw - prev)
        self._smooth[coin] = score
        action, reason = "HOLD", "no edge"
        if has_position:
            if score <= self.sell_th and held_minutes >= self.min_hold:
                action, reason = "SELL", f"score {score:+.3f} <= sell threshold {self.sell_th:+.2f}"
            elif score <= self.sell_th:
                reason = f"exit signal but min hold {self.min_hold:.0f}m not reached"
            else:
                reason = "holding position"
        elif score >= self.buy_th and p_up >= self.min_conf:
            if self.trend_filter and pd.notna(last.get("close_ema50")) and last["close_ema50"] < 0:
                reason = "buy signal filtered: price below EMA50 (trend filter)"
            elif can_enter:
                action, reason = "BUY", f"score {score:+.3f} >= {self.buy_th:+.2f}, P(up)={p_up:.3f}"
            else:
                reason = "entry blocked (drawdown guard/cooldown/funds)"
        elif score >= self.buy_th:
            reason = f"score ok but P(up) {p_up:.3f} < {self.min_conf:.2f}"
        return Signal(coin, action, round(score, 4), round(ai, 4), round(ind, 4), round(sent, 4),
                      round(p_up, 4), reason)
