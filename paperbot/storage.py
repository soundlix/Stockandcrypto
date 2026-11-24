"""Persist trades, signals and equity snapshots to SQLite (+ trades CSV)."""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import asdict
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, coin TEXT, side TEXT, qty REAL,
  price REAL, fee REAL, value REAL, pnl REAL, reason TEXT, cash_after REAL, equity_after REAL, mode TEXT);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, candle_time TEXT, coin TEXT, close REAL,
  action TEXT, score REAL, ai_score REAL, indicator_score REAL, sentiment_score REAL,
  p_up REAL, p_online REAL, p_batch REAL, rsi REAL, fear_greed INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS equity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, cash REAL, equity REAL,
  realized_pnl REAL, drawdown REAL, open_positions INTEGER);
CREATE TABLE IF NOT EXISTS candles (
  coin TEXT NOT NULL, candle_time TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY (coin, candle_time));
CREATE TABLE IF NOT EXISTS stock_signals (
  symbol TEXT PRIMARY KEY, candle_time TEXT, price REAL, action TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS stock_scan_status (
  id INTEGER PRIMARY KEY CHECK (id=1), scanned_at TEXT, status TEXT, detail TEXT);
"""
TRADE_FIELDS = ["time", "coin", "side", "qty", "price", "fee", "value", "pnl", "reason", "cash_after", "equity_after"]


class Storage:
    def __init__(self, sqlite_path: str, trades_csv: str | None = None, mode: str = "live"):
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.csv = Path(trades_csv) if trades_csv else None
        self.mode = mode

    def log_trade(self, t) -> None:
        d = asdict(t)
        self.conn.execute(
            f"INSERT INTO trades ({','.join(TRADE_FIELDS)}, mode) VALUES ({','.join('?' * len(TRADE_FIELDS))}, ?)",
            [d[k] for k in TRADE_FIELDS] + [self.mode])
        self.conn.commit()
        if self.csv:
            new = not self.csv.exists()
            with open(self.csv, "a", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=TRADE_FIELDS + ["mode"])
                if new:
                    w.writeheader()
                w.writerow({**{k: d[k] for k in TRADE_FIELDS}, "mode": self.mode})

    def log_signal(self, row: dict) -> None:
        cols = ["time", "candle_time", "coin", "close", "action", "score", "ai_score", "indicator_score",
                "sentiment_score", "p_up", "p_online", "p_batch", "rsi", "fear_greed", "reason"]
        self.conn.execute(f"INSERT INTO signals ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          [row.get(c) for c in cols])
        self.conn.commit()

    def log_candles(self, coin: str, candles) -> None:
        """Persist OHLCV candles for the dashboard's candlestick chart."""
        rows = [(coin, ts.isoformat(), float(r.open), float(r.high), float(r.low),
                 float(r.close), float(r.volume)) for ts, r in candles.iterrows()]
        if rows:
            self.conn.executemany(
                "INSERT OR REPLACE INTO candles (coin,candle_time,open,high,low,close,volume) "
                "VALUES (?,?,?,?,?,?,?)", rows)
            self.conn.commit()

    def log_stock_scan(self, rows: list[dict], status: str, detail: str, scanned_at: str) -> None:
        if rows:
            self.conn.executemany(
                "INSERT INTO stock_signals (symbol,candle_time,price,action,reason) VALUES (?,?,?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET candle_time=excluded.candle_time, price=excluded.price, "
                "action=excluded.action, reason=excluded.reason",
                [(r["symbol"], r["candle_time"], r["price"], r["action"], r["reason"]) for r in rows],
            )
        self.conn.execute(
            "INSERT INTO stock_scan_status (id,scanned_at,status,detail) VALUES (1,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET scanned_at=excluded.scanned_at, status=excluded.status, detail=excluded.detail",
            (scanned_at, status, detail),
        )
        self.conn.commit()

    def log_equity(self, time: str, cash: float, equity: float, realized: float, dd: float, n_open: int) -> None:
        self.conn.execute("INSERT INTO equity (time, cash, equity, realized_pnl, drawdown, open_positions) "
                          "VALUES (?,?,?,?,?,?)", (time, cash, equity, realized, dd, n_open))
        self.conn.commit()
