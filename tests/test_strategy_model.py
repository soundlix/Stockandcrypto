import pandas as pd

from paperbot.backtest import run_backtest
from paperbot.features import compute_indicators
from paperbot.model import HybridModel
from paperbot.sentiment import SentimentEngine
from paperbot.storage import Storage
from paperbot.strategy import EnsembleStrategy, indicator_score


def test_indicator_score_bounds(candles):
    ind = compute_indicators(candles)
    for i in range(100, len(ind), 50):
        assert -1 <= indicator_score(ind.iloc[i]) <= 1


def test_strategy_actions(cfg):
    cfg["strategy"]["score_smoothing"] = 1
    cfg["strategy"]["trend_filter"] = False
    s = EnsembleStrategy(cfg)
    bull = pd.Series({"rsi_14": 25, "macd_hist_n": 1e-3, "ema_ratio": 1e-3, "bb_pctb": 0.0, "vol_z": 2})
    bear = pd.Series({"rsi_14": 80, "macd_hist_n": -1e-3, "ema_ratio": -1e-3, "bb_pctb": 1.0, "vol_z": 0})
    assert s.evaluate("BTC", bull, 0.7, 0.5, has_position=False).action == "BUY"
    assert s.evaluate("BTC", bull, 0.7, 0.5, has_position=False, can_enter=False).action == "HOLD"
    assert s.evaluate("BTC", bear, 0.3, -0.5, has_position=True).action == "SELL"
    assert s.evaluate("BTC", bear, 0.3, -0.5, has_position=True, held_minutes=1).action == "HOLD"
    assert s.evaluate("ETH", bear, 0.3, -0.5, has_position=False).action == "HOLD"


def test_model_batch_online_and_persistence(cfg, candles):
    ind = compute_indicators(candles)
    m = HybridModel("BTC", cfg)
    m.fit_batch(ind.iloc[:600])
    p = m.predict_up(ind.iloc[:600])
    assert 0 <= p["p_up"] <= 1 and p["p_online"] is not None and p["p_batch"] is not None
    n = m.update_online(ind.iloc[:700])
    assert n > 0
    m.save()
    m2 = HybridModel("BTC", cfg)
    assert m2.load() and m2.online_updates == m.online_updates and m2.batch is not None


def test_sentiment_scoring_offline(cfg):
    cfg["sentiment"]["enabled"] = True
    se = SentimentEngine(cfg)
    assert se.fear_greed_score(10) > 0  # contrarian: extreme fear is bullish
    cfg["sentiment"]["fear_greed_mode"] = "momentum"
    assert SentimentEngine(cfg).fear_greed_score(10) < 0
    good, n = se.news_score("BTC", ["Bitcoin rally delivers great gains, investors happy", "Ethereum crashes"])
    assert good > 0 and n >= 1


def test_backtest_offline(cfg, candles, tmp_path):
    hist = {"BTC": candles, "ETH": candles * 0.06}
    res = run_backtest(cfg, histories=hist, use_sentiment=False, persist=True)
    assert res["candles_replayed"] == 400 and res["final_equity"] > 0


def test_storage(cfg):
    st = Storage(cfg["storage"]["sqlite"], cfg["storage"]["trades_csv"])
    st.log_equity("t", 100, 100, 0, 0, 0)
    st.log_signal({"time": "t", "coin": "BTC", "action": "HOLD"})
    assert st.conn.execute("select count(*) from signals").fetchone()[0] == 1
