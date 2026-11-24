"""Hybrid ML model: online learner + periodic batch learner ("Learning: Both").

* Online: StandardScaler + SGDClassifier(log-loss), updated with partial_fit every
  time a new candle's label becomes known (i.e. `horizon` minutes later).
* Batch: HistGradientBoostingClassifier retrained every `retrain_hours` on the
  rolling history, with a time-ordered hold-out accuracy check.
Both are persisted to disk with joblib so learning survives restarts.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler

from .features import FEATURE_COLUMNS, training_set

log = logging.getLogger("paperbot.model")
CLASSES = np.array([0, 1])


class HybridModel:
    def __init__(self, coin: str, cfg: dict):
        m = cfg["model"]
        self.coin = coin
        self.horizon = int(m.get("horizon", 3))
        self.w_online = float(m.get("online_weight", 0.5))
        self.w_batch = float(m.get("batch_weight", 0.5))
        self.retrain_seconds = float(m.get("retrain_hours", 6)) * 3600
        self.min_rows = int(m.get("min_train_rows", 300))
        self.path = Path(m["dir"]) / f"{coin.lower()}_model.joblib"
        self.scaler = StandardScaler()
        self.online = SGDClassifier(loss="log_loss", alpha=1e-2, learning_rate="optimal", random_state=42)
        self.batch: HistGradientBoostingClassifier | None = None
        self.online_ready = False
        self.last_online_ts: pd.Timestamp | None = None
        self.last_batch_time: float = 0.0
        self.batch_val_acc: float | None = None
        self.online_updates = 0
        self.online_hits: list[int] = []   # rolling live accuracy of real-time predictions
        self.pending: dict = {}            # candle ts -> predicted class, scored once label known

    # ---------- persistence ----------
    STATE_KEYS = ("scaler", "online", "batch", "online_ready", "last_online_ts", "last_batch_time",
                  "batch_val_acc", "online_updates", "online_hits", "pending")

    def save(self) -> None:
        joblib.dump({k: getattr(self, k) for k in self.STATE_KEYS}, self.path)

    def load(self) -> bool:
        if not self.path.exists():
            return False
        try:
            state = joblib.load(self.path)
            for k in self.STATE_KEYS:
                if k in state:
                    setattr(self, k, state[k])
            log.info("[%s] loaded model (online updates=%d, batch val acc=%s)",
                     self.coin, self.online_updates, self._fmt(self.batch_val_acc))
            return True
        except Exception as e:
            log.warning("[%s] could not load model, starting fresh: %s", self.coin, e)
            return False

    @staticmethod
    def _fmt(x):
        return "n/a" if x is None else f"{x:.3f}"

    # ---------- training ----------
    def needs_batch_retrain(self) -> bool:
        return self.batch is None or time.time() - self.last_batch_time >= self.retrain_seconds

    def fit_batch(self, ind: pd.DataFrame) -> None:
        X, y = training_set(ind, self.horizon)
        if len(X) < self.min_rows:
            log.warning("[%s] only %d rows, skipping batch training", self.coin, len(X))
            return
        split = int(len(X) * 0.8)
        params = dict(max_iter=150, learning_rate=0.05, max_depth=3, min_samples_leaf=40,
                      l2_regularization=1.0, random_state=42)
        val = HistGradientBoostingClassifier(**params).fit(X.iloc[:split], y.iloc[:split])
        self.batch_val_acc = float((val.predict(X.iloc[split:]) == y.iloc[split:]).mean())
        self.batch = HistGradientBoostingClassifier(**params).fit(X, y)
        self.last_batch_time = time.time()
        # (re)warm the online learner on the same window if it has never been fitted
        if not self.online_ready:
            self.scaler.partial_fit(X.values)
            Xs = self.scaler.transform(X.values)
            for _ in range(3):
                self.online.partial_fit(Xs, y.values, classes=CLASSES)
            self.online_ready = True
            self.last_online_ts = X.index[-1]
        log.info("[%s] batch model trained on %d rows, hold-out accuracy %.3f (base rate up=%.3f)",
                 self.coin, len(X), self.batch_val_acc, y.mean())
        self.save()

    def update_online(self, ind: pd.DataFrame) -> int:
        """partial_fit on rows whose labels became known since the last update."""
        X, y = training_set(ind, self.horizon)
        if self.last_online_ts is not None:
            new = X.index > self.last_online_ts
            X, y = X[new], y[new]
        if X.empty:
            return 0
        self.scaler.partial_fit(X.values)
        Xs = self.scaler.transform(X.values)
        # score the predictions we actually made in real time (no look-ahead)
        for ts, lab in y.items():
            if ts in self.pending:
                self.online_hits.append(int(self.pending.pop(ts) == lab))
        self.online_hits = self.online_hits[-500:]
        if len(self.pending) > 1000:
            self.pending = dict(list(self.pending.items())[-500:])
        self.online.partial_fit(Xs, y.values, classes=CLASSES)
        self.online_ready = True
        self.last_online_ts = X.index[-1]
        self.online_updates += len(X)
        return len(X)

    @property
    def live_acc(self) -> float | None:
        return float(np.mean(self.online_hits)) if len(self.online_hits) >= 20 else None

    # ---------- inference ----------
    def predict_up(self, ind: pd.DataFrame) -> dict:
        row = ind[FEATURE_COLUMNS].iloc[[-1]]
        if row.isna().any(axis=1).iloc[0]:
            return {"p_up": 0.5, "p_online": None, "p_batch": None}
        p_on = float(self.online.predict_proba(self.scaler.transform(row.values))[0, 1]) if self.online_ready else None
        p_b = float(self.batch.predict_proba(row)[0, 1]) if self.batch is not None else None
        parts = [(p_on, self.w_online), (p_b, self.w_batch)]
        num = sum(p * w for p, w in parts if p is not None)
        den = sum(w for p, w in parts if p is not None)
        p_up = num / den if den else 0.5
        self.pending[row.index[-1]] = int(p_up > 0.5)
        return {"p_up": p_up, "p_online": p_on, "p_batch": p_b}
