import numpy as np
import pandas as pd

from paperbot.data import closed_only, to_frame
from paperbot.features import FEATURE_COLUMNS, compute_indicators, make_labels, rsi, training_set


def test_indicators_shape_and_ranges(candles):
    ind = compute_indicators(candles)
    assert set(FEATURE_COLUMNS) <= set(ind.columns)
    r = ind["rsi_14"].dropna()
    assert ((r >= 0) & (r <= 100)).all()
    assert (ind["atr"].dropna() > 0).all()


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 50, dtype=float))
    assert rsi(up).iloc[-1] == 100.0
    assert rsi(up[::-1].reset_index(drop=True)).iloc[-1] < 1


def test_labels_have_no_future_for_last_rows(candles):
    y = make_labels(candles["close"], 5)
    assert y.iloc[-5:].isna().all() and y.iloc[:-5].notna().all()
    X, yy = training_set(compute_indicators(candles), 5)
    assert len(X) == len(yy) and X.notna().all().all()


def test_closed_only_drops_forming_candle():
    now = 1_700_000_000_000 - (1_700_000_000_000 % 60_000) + 30_000  # 30s into a minute
    rows = [[now - 30_000 - 120_000, 1, 1, 1, 1, 1], [now - 30_000 - 60_000, 1, 1, 1, 1, 1], [now - 30_000, 1, 1, 1, 1, 1]]
    df = closed_only(to_frame(rows), now_ms=now)
    assert len(df) == 2


def test_call_with_timeout():
    import time

    import pytest

    from paperbot.utils import call_with_timeout
    assert call_with_timeout(lambda x: x * 2, 1, 21) == 42
    with pytest.raises(TimeoutError):
        call_with_timeout(time.sleep, 0.2, 5)


def test_call_with_timeout_passes_timeout_kwarg():
    from paperbot.utils import call_with_timeout
    assert call_with_timeout(lambda url, timeout=None: (url, timeout), 1, "u", timeout=10) == ("u", 10)
