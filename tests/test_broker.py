from datetime import datetime, timedelta, timezone

import pytest

from paperbot.broker import PaperBroker

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_buy_sell_fees_and_slippage(cfg):
    b = PaperBroker(cfg, None)
    t = b.buy("BTC", 50_000, 50, NOW, "test")
    assert t is not None and b.pf.cash < 100
    assert t.value <= 100 * cfg["portfolio"]["max_position_pct"] + 1e-9
    assert t.price == pytest.approx(50_000 * (1 + cfg["portfolio"]["slippage_bps"] / 1e4))
    s = b.sell("BTC", 50_000, NOW + timedelta(minutes=1), "test")
    assert s.pnl < 0  # flat price round trip loses fees + slippage
    assert b.equity() == pytest.approx(b.pf.cash)
    assert b.pf.n_trades == 1 and b.pf.fees_paid > 0


def test_stop_loss_and_take_profit(cfg):
    b = PaperBroker(cfg, None)
    b.buy("ETH", 3000, 5, NOW, "t")
    pos = b.pf.positions["ETH"]
    assert pos.stop < pos.entry_price < pos.take_profit
    t = b.check_exits("ETH", high=3001, low=pos.stop - 1, now=NOW)
    assert t.reason == "stop-loss" and "ETH" not in b.pf.positions
    b.buy("BTC", 100, 0.1, NOW, "t")
    tp = b.pf.positions["BTC"].take_profit
    t = b.check_exits("BTC", high=tp + 1, low=99.9, now=NOW)
    assert t.reason == "take-profit" and t.pnl > 0


def test_cooldown_and_drawdown_guard(cfg):
    b = PaperBroker(cfg, None)
    b.buy("BTC", 100, 0.1, NOW, "t")
    b.sell("BTC", 100, NOW, "t")
    assert not b.can_enter("BTC", NOW + timedelta(minutes=1))
    assert b.can_enter("BTC", NOW + timedelta(minutes=60))
    reentry_time = NOW + timedelta(minutes=60)
    b.buy("BTC", 100, 0.1, reentry_time, "t")
    b.mark("BTC", 1)  # crash
    trades = b.update_risk(reentry_time)
    assert b.pf.halted and trades and not b.pf.positions
    assert not b.can_enter("ETH", reentry_time)


def test_state_persistence(cfg):
    b = PaperBroker(cfg, cfg["storage"]["state_file"])
    b.buy("BTC", 50_000, 50, NOW, "t")
    b.save()
    b2 = PaperBroker(cfg, cfg["storage"]["state_file"])
    assert "BTC" in b2.pf.positions and b2.pf.cash == pytest.approx(b.pf.cash)


def test_targets_relative_to_fill_even_with_tiny_tp(cfg):
    cfg["portfolio"].update(min_take_profit_pct=0.0001, take_profit_atr=0.01, min_stop_pct=0.0001, stop_loss_atr=0.01)
    b = PaperBroker(cfg, None)
    b.buy("BTC", 60_000, 1, NOW, "t")
    p = b.pf.positions["BTC"]
    assert p.stop < p.entry_price < p.take_profit
