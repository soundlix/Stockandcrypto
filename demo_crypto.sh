#!/usr/bin/env bash
# Short forced-signal demonstration of the simulated crypto broker.
# All balances, trades, and models are written under data/demo/.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="python3"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
fi

export PAPERBOT__STORAGE__DATA_DIR="data/demo"
export PAPERBOT__STORAGE__SQLITE="data/demo/paperbot.db"
export PAPERBOT__STORAGE__TRADES_CSV="data/demo/trades.csv"
export PAPERBOT__STORAGE__STATE_FILE="data/demo/state.json"
export PAPERBOT__MODEL__DIR="data/demo/models"
export PAPERBOT__LOGGING__FILE="data/demo/bot.log"

# Intentionally permissive thresholds make simulated entries and exits visible.
export PAPERBOT__STRATEGY__BUY_THRESHOLD="-1"
export PAPERBOT__STRATEGY__SELL_THRESHOLD="1"
export PAPERBOT__STRATEGY__MIN_AI_CONFIDENCE="0"
export PAPERBOT__STRATEGY__MIN_HOLD_MINUTES="0"
export PAPERBOT__STRATEGY__TREND_FILTER="false"
export PAPERBOT__PORTFOLIO__COOLDOWN_MINUTES="0"

exec "$PYTHON" -m paperbot live --minutes "${1:-4}"
