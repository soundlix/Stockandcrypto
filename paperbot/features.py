"""Technical-indicator features (RSI, MACD, EMA cross, Bollinger, ATR, volume)."""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "ret_1", "ret_3", "ret_5", "ret_15", "vol_15", "rsi_14", "macd_n", "macd_sig_n",
    "macd_hist_n", "ema_ratio", "ema_cross", "close_ema50", "bb_pctb", "bb_width",
    "atr_pct", "vol_z", "hl_range", "minute_sin", "minute_cos",
]


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.mask((loss == 0) & (gain > 0), 100.0)
    out = out.mask((loss == 0) & (gain == 0), 50.0)
    return out


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a frame of raw indicators + normalised model features (same index)."""
    c = df["close"]
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    for n in (1, 3, 5, 15):
        out[f"ret_{n}"] = c.pct_change(n)
    out["vol_15"] = c.pct_change().rolling(15).std()
    out["rsi_14"] = rsi(c, 14)
    macd = ema(c, 12) - ema(c, 26)
    sig = ema(macd, 9)
    out["macd"], out["macd_signal"] = macd, sig
    out["macd_n"], out["macd_sig_n"], out["macd_hist_n"] = macd / c, sig / c, (macd - sig) / c
    e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
    out["ema_fast"], out["ema_slow"] = e9, e21
    out["ema_ratio"] = e9 / e21 - 1
    out["ema_cross"] = np.sign(out["ema_ratio"])
    out["close_ema50"] = c / e50 - 1
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    upper, lower = mid + 2 * sd, mid - 2 * sd
    out["bb_pctb"] = (c - lower) / (upper - lower).replace(0, np.nan)
    out["bb_width"] = (upper - lower) / mid
    out["atr"] = atr(df, 14)
    out["atr_pct"] = out["atr"] / c
    v = df["volume"]
    out["vol_z"] = (v - v.rolling(30).mean()) / v.rolling(30).std().replace(0, np.nan)
    out["hl_range"] = (df["high"] - df["low"]) / c
    minute = df.index.hour * 60 + df.index.minute
    out["minute_sin"] = np.sin(2 * np.pi * minute / 1440)
    out["minute_cos"] = np.cos(2 * np.pi * minute / 1440)
    return out.replace([np.inf, -np.inf], np.nan)


def make_labels(close: pd.Series, horizon: int) -> pd.Series:
    """1 if close `horizon` candles ahead is higher, 0 if not, NaN if unknown yet."""
    fut = close.shift(-horizon)
    lab = (fut > close).astype(float)
    lab[fut.isna()] = np.nan
    return lab


def training_set(ind: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, pd.Series]:
    y = make_labels(ind["close"], horizon)
    X = ind[FEATURE_COLUMNS]
    mask = X.notna().all(axis=1) & y.notna()
    return X[mask], y[mask].astype(int)
