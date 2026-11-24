"""Small Docker health checks for cloud services; uses only Python's stdlib."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("/app/data")


def check_crypto() -> bool:
    state_file = DATA / "state.json"
    if not state_file.is_file():
        return False
    try:
        state = json.loads(state_file.read_text())
        updated = datetime.fromisoformat(state["updated"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
        return 0 <= age < 240
    except (OSError, ValueError, KeyError, TypeError):
        return False


def check_stocks() -> bool:
    db = DATA / "paperbot.db"
    if not db.is_file():
        return False
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3) as conn:
            row = conn.execute("SELECT scanned_at FROM stock_scan_status WHERE id=1").fetchone()
        if not row or not row[0]:
            return False
        scanned = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - scanned.astimezone(timezone.utc)).total_seconds()
        return 0 <= age < 240
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return False


if __name__ == "__main__":
    checks = {"crypto": check_crypto, "stocks": check_stocks}
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit(0 if name in checks and checks[name]() else 1)
