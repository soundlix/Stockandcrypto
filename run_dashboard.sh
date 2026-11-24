#!/usr/bin/env bash
# Open the Streamlit dashboard at http://localhost:8501
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/streamlit run dashboard/app.py --server.headless true --server.port "${PORT:-8501}"
