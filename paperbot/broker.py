"""Simulated broker / portfolio. NEVER talks to an exchange - pure bookkeeping.

Models: fees per side, adverse slippage, risk-based position sizing, ATR stop-loss
and take-profit (checked against each candle's high/low), cooldown and a max
drawdown guard. Shared USD cash across all coins. Long-only (spot-style).
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("paperbot.broker")


@dataclass
class Position:
    coin: str
    qty: float
    entry_price: float
    stop: float
    take_profit: float
    entry_time: str
    entry_fee: float
    cost: float  # USD spent incl. fee


@dataclass
class Trade:
    time: str
    coin: str
    side: str
    qty: float
    price: float
    fee: float
    value: float
    pnl: float
    reason: str
    cash_after: float
    equity_after: float


@dataclass
class Portfolio:
    cash: float
    starting_balance: float
    peak_equity: float
    positions: dict = field(default_factory=dict)
    last_exit: dict = field(default_factory=dict)  # coin -> iso time
    halted: bool = False
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    n_trades: int = 0
    n_wins: int = 0
    daily_date: str = ""
    daily_start_equity: float = 0.0
    daily_halted: bool = False


class PaperBroker:
    def __init__(self, cfg: dict, state_file: str | None = None):
        p = cfg["portfolio"]
        self.fee = float(p["fee_rate"])
        self.slip = float(p["slippage_bps"]) / 10_000
        self.max_pos = float(p["max_position_pct"])
        self.risk = float(p["risk_per_trade_pct"])
        self.min_order = float(p["min_order_usd"])
        self.sl_atr = float(p["stop_loss_atr"])
        self.tp_atr = float(p["take_profit_atr"])
        self.min_stop = float(p["min_stop_pct"])
        self.min_tp = float(p.get("min_take_profit_pct", 2 * self.min_stop))
        self.max_dd = float(p["max_drawdown_pct"])
        self.close_on_dd = bool(p.get("close_all_on_drawdown", True))
        self.daily_loss_limit = float(p.get("daily_loss_limit_pct", 0.05))
        self.close_on_daily_loss = bool(p.get("close_all_on_daily_loss", True))
        self.cooldown = timedelta(minutes=float(p.get("cooldown_minutes", 0)))
        self.state_file = Path(state_file) if state_file else None
        sb = float(p["starting_balance"])
        self.pf = Portfolio(cash=sb, starting_balance=sb, peak_equity=sb)
        self.prices: dict[str, float] = {}
        self.load()

    # ---------- state ----------
    def load(self) -> None:
        if self.state_file and self.state_file.exists():
            d = json.loads(self.state_file.read_text())
            self.prices = d.pop("prices", {}) or {}
            d.pop("equity", None)
            d.pop("updated", None)
            d["positions"] = {k: Position(**v) for k, v in d.get("positions", {}).items()}
            self.pf = Portfolio(**d)
            log.info("Resumed portfolio: cash $%.2f, %d open position(s)", self.pf.cash, len(self.pf.positions))

    def save(self) -> None:
        if not self.state_file:
            return
        d = asdict(self.pf)
        d["prices"] = self.prices
        d["equity"] = self.equity()
        d["updated"] = datetime.now(timezone.utc).isoformat()
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2))
        tmp.replace(self.state_file)

    # ---------- valuation ----------
    def mark(self, coin: str, price: float) -> None:
        self.prices[coin] = price

    def equity(self) -> float:
        return self.pf.cash + sum(p.qty * self.prices.get(c, p.entry_price) for c, p in self.pf.positions.items())

    def drawdown(self) -> float:
        return 1 - self.equity() / self.pf.peak_equity if self.pf.peak_equity else 0.0

    def update_risk(self, now: datetime) -> list[Trade]:
        eq = self.equity()
        today = now.astimezone(timezone.utc).date().isoformat()
        if self.pf.daily_date != today or self.pf.daily_start_equity <= 0:
            self.pf.daily_date = today
            self.pf.daily_start_equity = eq
            self.pf.daily_halted = False
        self.pf.peak_equity = max(self.pf.peak_equity, eq)
        trades = []
        daily_loss = max(0.0, 1 - eq / self.pf.daily_start_equity) if self.pf.daily_start_equity else 0.0
        if not self.pf.daily_halted and daily_loss >= self.daily_loss_limit:
            self.pf.daily_halted = True
            log.warning("DAILY LOSS LIMIT hit (%.2f%%): new entries halted until 00:00 UTC",
                        100 * daily_loss)
            if self.close_on_daily_loss:
                for c in list(self.pf.positions):
                    trades.append(self.sell(c, self.prices.get(c, self.pf.positions[c].entry_price), now, "daily loss limit"))
        if not self.pf.halted and self.drawdown() >= self.max_dd:
            self.pf.halted = True
            log.warning("MAX DRAWDOWN GUARD hit (%.1f%%): new entries halted", 100 * self.drawdown())
            if self.close_on_dd:
                for c in list(self.pf.positions):
                    trades.append(self.sell(c, self.prices.get(c, self.pf.positions[c].entry_price), now, "drawdown guard"))
        return trades

    def can_enter(self, coin: str, now: datetime) -> bool:
        if self.pf.halted or self.pf.daily_halted or coin in self.pf.positions:
            return False
        le = self.pf.last_exit.get(coin)
        if le and now - datetime.fromisoformat(le) < self.cooldown:
            return False
        return self.pf.cash >= self.min_order

    def held_minutes(self, coin: str, now: datetime) -> float:
        pos = self.pf.positions.get(coin)
        return (now - datetime.fromisoformat(pos.entry_time)).total_seconds() / 60 if pos else 0.0

    # ---------- orders ----------
    def size_order(self, price: float, atr_value: float) -> tuple[float, float, float]:
        """Return (usd_to_spend, stop, take_profit) from risk-based sizing."""
        stop_dist = max(self.sl_atr * atr_value, self.min_stop * price)
        stop, tp = price - stop_dist, price + max(self.tp_atr * atr_value, self.min_tp * price)
        eq = self.equity()
        risk_usd = eq * self.risk
        usd = min(risk_usd / (stop_dist / price), eq * self.max_pos, self.pf.cash)
        return usd, stop, tp

    def buy(self, coin: str, price: float, atr_value: float, now: datetime, reason: str) -> Trade | None:
        if not self.can_enter(coin, now):
            log.info("[%s] entry skipped: portfolio guard, existing position, cooldown, or insufficient cash", coin)
            return None
        if not math.isfinite(price) or price <= 0 or not math.isfinite(atr_value) or atr_value <= 0:
            log.warning("[%s] entry skipped: invalid price or ATR", coin)
            return None
        fill = price * (1 + self.slip)
        usd, stop, tp = self.size_order(fill, atr_value)  # stops/targets relative to the actual fill
        if usd < self.min_order:
            log.info("[%s] order $%.2f below minimum $%.2f - skipped", coin, usd, self.min_order)
            return None
        fee = usd * self.fee
        qty = (usd - fee) / fill
        self.pf.cash -= usd
        self.pf.fees_paid += fee
        self.pf.positions[coin] = Position(coin, qty, fill, stop, tp, now.isoformat(), fee, usd)
        self.mark(coin, price)
        t = Trade(now.isoformat(), coin, "BUY", qty, fill, fee, usd, 0.0, reason, self.pf.cash, self.equity())
        log.info("PAPER BUY  %s qty=%.8f @ $%.2f (spent $%.2f, fee $%.4f) SL $%.2f TP $%.2f | %s",
                 coin, qty, fill, usd, fee, stop, tp, reason)
        return t

    def sell(self, coin: str, price: float, now: datetime, reason: str) -> Trade:
        pos = self.pf.positions.pop(coin)
        fill = price * (1 - self.slip)
        gross = pos.qty * fill
        fee = gross * self.fee
        proceeds = gross - fee
        pnl = proceeds - pos.cost
        self.pf.cash += proceeds
        self.pf.fees_paid += fee
        self.pf.realized_pnl += pnl
        self.pf.n_trades += 1
        self.pf.n_wins += int(pnl > 0)
        self.pf.last_exit[coin] = now.isoformat()
        self.mark(coin, price)
        t = Trade(now.isoformat(), coin, "SELL", pos.qty, fill, fee, proceeds, pnl, reason, self.pf.cash, self.equity())
        log.info("PAPER SELL %s qty=%.8f @ $%.2f (proceeds $%.2f, fee $%.4f) PnL $%+.4f | %s",
                 coin, pos.qty, fill, proceeds, fee, pnl, reason)
        return t

    def check_exits(self, coin: str, high: float, low: float, now: datetime) -> Trade | None:
        """Stop-loss / take-profit against the candle range (stop assumed first)."""
        pos = self.pf.positions.get(coin)
        if not pos:
            return None
        if low <= pos.stop:
            return self.sell(coin, pos.stop, now, "stop-loss")
        if high >= pos.take_profit:
            return self.sell(coin, pos.take_profit, now, "take-profit")
        return None
