import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paperbot.config import load_config  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    c = load_config()
    c["storage"].update(sqlite=str(tmp_path / "t.db"), trades_csv=str(tmp_path / "t.csv"),
                        state_file=str(tmp_path / "state.json"))
    c["model"]["dir"] = str(tmp_path / "models")
    Path(c["model"]["dir"]).mkdir()
    c["sentiment"]["enabled"] = False
    return c


@pytest.fixture
def candles():
    rng = np.random.default_rng(0)
    n = 800
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    close = 50_000 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    high = close * (1 + rng.uniform(0, 0.001, n))
    low = close * (1 - rng.uniform(0, 0.001, n))
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close,
                         "volume": rng.uniform(1, 10, n)}, index=idx)
