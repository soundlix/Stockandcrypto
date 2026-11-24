"""Command line entry point:  python -m paperbot {live,dry-run,backtest,status}"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from . import DISCLAIMER
from .config import load_config


def setup_logging(cfg: dict) -> None:
    lc = cfg["logging"]
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S %Z")
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(lc.get("level", "INFO")).upper(), logging.INFO))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.handlers = [sh]
    if lc.get("file"):
        fh = RotatingFileHandler(lc["file"], maxBytes=5_000_000, backupCount=3)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    for noisy in ("urllib3", "ccxt"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="paperbot", description=DISCLAIMER)
    ap.add_argument("--config", help="path to config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("live", help="paper-trade on live 1m candles (simulated orders)")
    p.add_argument("--minutes", type=float, help="stop after N minutes (default: run forever)")
    p = sub.add_parser("dry-run", help="live data + signals only, no simulated orders")
    p.add_argument("--minutes", type=float)
    p = sub.add_parser("stocks", help="continuously scan the configured stock watchlist (signals only)")
    p.add_argument("--minutes", type=float, help="stop after N minutes (default: run forever)")
    p = sub.add_parser("backtest", help="walk-forward backtest on recent real candles")
    p.add_argument("--candles", type=int, default=3000)
    p.add_argument("--train-frac", type=float, default=0.5)
    p.add_argument("--no-sentiment", action="store_true")
    sub.add_parser("status", help="print saved paper portfolio")
    sub.add_parser("reset", help="reset the paper portfolio to the starting balance")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    setup_logging(cfg)
    log = logging.getLogger("paperbot")
    print(f"\n*** {DISCLAIMER} ***\n")

    if a.cmd in ("live", "dry-run"):
        from .bot import PaperTradingBot
        PaperTradingBot(cfg, trade=a.cmd == "live").run(a.minutes)
    elif a.cmd == "stocks":
        from .storage import Storage
        from .stock_scanner import StockScanner
        st = cfg["storage"]
        store = Storage(st["sqlite"], trades_csv=None, mode="stock-scan")
        StockScanner(cfg, store).run(a.minutes)
    elif a.cmd == "backtest":
        from .backtest import run_backtest
        res = run_backtest(cfg, a.candles, a.train_frac, not a.no_sentiment)
        log.info("Backtest result:\n%s", json.dumps(res, indent=2, default=str))
    elif a.cmd == "status":
        from pathlib import Path
        f = Path(cfg["storage"]["state_file"])
        print(f.read_text() if f.exists() else "No portfolio state yet - run `live` first.")
    elif a.cmd == "reset":
        from pathlib import Path
        f = Path(cfg["storage"]["state_file"])
        if f.exists():
            f.rename(f.with_suffix(".json.bak"))
        print(f"Portfolio reset to ${cfg['portfolio']['starting_balance']:.2f} (old state kept as state.json.bak).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
