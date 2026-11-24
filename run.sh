#!/usr/bin/env bash
# Start the PAPER-trading bot (simulated orders only). Usage: ./run.sh [live|dry-run|backtest|status|reset] [args]
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python -m paperbot "${@:-live}"
