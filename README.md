# Crypto Paper-Trading Bot — BTC & ETH, 1-minute candles

> ⚠️ **Disclaimer — educational paper trading only.** This software places **simulated** trades
> with **fake money** ($100 starting balance). It never connects to an exchange account, needs
> **no API keys**, and cannot place real orders. Nothing here is financial advice. Short-timeframe
> crypto trading is extremely risky, and a model that does well on paper or in a backtest can still lose money live.

## What it does

| Area | Implementation |
|---|---|
| Coins / timeframe | BTC and ETH vs USD/USDT, closed **1-minute** candles |
| Live market data | Public, key-less endpoints via `ccxt`, in order: **Binance.US → Kraken → Coinbase Exchange** (all reachable from the US). It falls back automatically on errors and on stale data (Coinbase's public candles can lag 3–5 min). Bootstraps ~1,500 historical 1m candles at startup. |
| Indicators | RSI-14, MACD (12/26/9), EMA 9/21 cross, EMA-50 trend, Bollinger %B and width, ATR-14, volume z-score, multi-horizon returns, volatility |
| AI, "Learning: Both" | **Online:** `SGDClassifier` (log-loss) + `StandardScaler`, updated with `partial_fit` on every new candle as soon as its label is known. **Batch:** `HistGradientBoostingClassifier`, retrained every `retrain_hours` (default 6h) on the rolling history, with a time-ordered hold-out accuracy check. Both predict P(price is higher in `horizon`=5 min), are blended, and are saved to `data/models/` so learning carries over between restarts. |
| Sentiment | **Alternative.me Crypto Fear & Greed Index** (free, no key; contrarian by default) plus **news RSS headlines** (CoinDesk, Cointelegraph, Decrypt) scored with **VADER**, filtered per coin. Each source has its own cache, and the bot treats a source as neutral if it fails. |
| Ensemble | `score = w_ai·AI + w_ind·Indicators + w_sent·Sentiment` (weights 0.5/0.35/0.15, normalized, EMA-smoothed). A BUY needs score ≥ 0.40, P(up) ≥ 0.55 and price above EMA-50. A SELL happens when score ≤ −0.25 after a minimum 10-min hold. All of these are set in `config.yaml`. |
| Paper broker | Shared $100 cash across both coins, 0.1% fee per side, 5 bps slippage, risk-based sizing (1% equity at risk, max 45% per coin, $5 minimum order), ATR stop-loss/take-profit checked against each candle's high/low, re-entry cooldown, **daily loss pause** (5% from UTC day's starting equity → closes positions and halts entries until next UTC day), and **max-drawdown guard** (15% from peak → closes positions and halts new entries). Long-only, spot style. |
| Logging | `data/paperbot.db` (SQLite tables `trades`, `signals`, `equity`), `data/trades.csv`, `data/bot.log` (rotating), `data/state.json` (portfolio, so restarts pick up where they left off). Times are stored in UTC and shown in America/Chicago. |
| Dashboard | Streamlit: equity, cash, PnL, fees, win rate, open positions, 15-minute BTC/ETH candles built from one-minute data, equity curve, recent trades, and per-coin signal breakdown. Auto-refreshes every 30s. The crypto strategy still evaluates one-minute closes. |
| Modes | `live` (paper trading), `dry-run` (live signals, no simulated orders), `backtest` (walk-forward on recent real candles), `status`, `reset` |

The crypto process checks for fresh market data and writes portfolio status logs every 30 seconds. Signals and simulated orders are still evaluated only when a new completed one-minute candle arrives, so the bot will not make duplicate decisions between candles.

## Stock watchlist scanner

Run `python -m paperbot stocks` in its own terminal to scan all 25 symbols in `stocks.symbols` continuously. It checks the market-data feed once per minute and displays BUY / HOLD / SELL ideas; it does not submit stock orders. During weekends and market closures, no new bars arrive and the displayed signal stays at the last available bar. This scanner runs independently of the crypto bot.

Stock data uses Alpaca's free IEX feed, which covers activity from IEX rather than the consolidated U.S. market tape. A free Alpaca paper account and its paper API key pair are required. Create a local `.env` file from `.env.example`, add the paper key and secret there, then run `python -m paperbot stocks`. Keep the keys private. Without them, the dashboard reports that scanning needs setup and shows WAIT for every symbol.

Each symbol has its own saved online model. It learns from historical 15-minute candle features and updates when the next four-bar (one-hour) direction becomes known. After it has enough labeled examples, its estimated probability influences BUY / HOLD / SELL ideas; before that, the scanner uses the EMA, RSI, and relative-volume fallback. Models persist in `data/models/stocks_online.joblib` across restarts. This is adaptive learning, not a guarantee of accurate or profitable predictions. The scanner does not simulate stock trades, so the separate $100 stock dashboard balance remains unchanged; no Alpaca orders are sent.

```
crypto-paper-bot/
├── config.yaml            # all settings (env overrides: PAPERBOT__SECTION__KEY=value)
├── paperbot/
│   ├── data.py            # ccxt public candles + exchange fallback
│   ├── features.py        # indicators / ML features / labels
│   ├── sentiment.py       # Fear & Greed + RSS/VADER
│   ├── model.py           # online SGD + batch gradient boosting, persistence
│   ├── strategy.py        # ensemble signal
│   ├── broker.py          # simulated portfolio, fees, slippage, SL/TP, DD guard
│   ├── storage.py         # SQLite + CSV
│   ├── bot.py             # live loop
│   ├── backtest.py        # walk-forward backtest
│   └── cli.py             # python -m paperbot ...
├── dashboard/app.py       # Streamlit dashboard
├── tests/                 # pytest (offline, synthetic data)
├── run.sh / run_dashboard.sh
├── deploy/                # macOS launchd agent + installer
├── Dockerfile / docker-compose.yml / .env.example
└── requirements.txt
```

## Run it on your MacBook

You need Python 3.10 or newer (`python3 --version`). If it's missing: `brew install python@3.12`, or use the python.org installer.

```bash
cd ~/Downloads && unzip crypto-paper-bot.zip && cd crypto-paper-bot   # wherever you saved it
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

python -m pytest -q                    # optional: run the tests (about 10s)
python -m paperbot backtest            # optional: walk-forward backtest on the last ~2 days
python -m paperbot live                # start paper trading (Ctrl+C to stop; state is saved)
```

Open the dashboard in a second Terminal tab:

```bash
cd ~/Downloads/crypto-paper-bot && source .venv/bin/activate
streamlit run dashboard/app.py         # opens http://localhost:8501
```

Shortcuts: `./run.sh` (creates the venv on first run, then runs `live`), `./run.sh dry-run`,
`./run.sh backtest --candles 3000`, `./run.sh status`, `./run.sh reset`, `./run_dashboard.sh`.
To test for a few minutes only: `python -m paperbot live --minutes 10`.

For an additive Oracle Cloud or Google Cloud setup with separate crypto, stock-scanner, and dashboard services, see [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md). It keeps the existing local launch setup intact.

GitHub Actions runs the offline test suite on each push and pull request and checks the Docker image for Oracle's ARM-based Always Free VM. GitHub Codespaces is configured for development; it is not the 24/7 host. Use the cloud deployment guide for independent, automatically restarted services.

To quickly see the simulated broker buy and sell, run `bash demo_crypto.sh` (defaults to four minutes). It uses deliberately loose signal thresholds and stores its separate $100 demo account, trades, and models under `data/demo/`; it does not change the normal Crypto paper portfolio. These forced demo signals are only for checking the order flow, not a trading strategy.

### Keep it running in the background on macOS (optional)

```bash
./deploy/install_launchd.sh             # starts now and at every login, restarts if it crashes
tail -f data/bot.log                    # watch it
./deploy/install_launchd.sh uninstall   # stop and remove
```

A sleeping Mac pauses the bot. When it wakes, the bot picks up from the latest candles, but it misses any trades it would have made while asleep. To prevent sleep while it runs, use `caffeinate -i python -m paperbot live`. For a bot that runs around the clock, use the server option below.

## Run it on a server / VPS (cloud)

Any small Linux VPS works (1 vCPU / 1 GB RAM is enough). Example with Docker on Ubuntu:

```bash
# on the server
curl -fsSL https://get.docker.com | sh
scp crypto-paper-bot.zip user@SERVER:~      # run this one from your Mac
unzip crypto-paper-bot.zip && cd crypto-paper-bot
cp .env.example .env                        # optional overrides; no secrets needed
docker compose up -d --build                # starts the bot and the dashboard
docker compose logs -f bot
```

The dashboard is bound to `127.0.0.1:8501` on the server by default. Open it from your Mac through an SSH tunnel:
`ssh -L 8501:localhost:8501 user@SERVER`, then go to http://localhost:8501. If you set
`DASHBOARD_BIND=0.0.0.0` to expose it publicly, put a firewall or reverse proxy with auth in front of it.
Data stays in `./data` on the host. To update: `docker compose up -d --build`. To stop: `docker compose down`.

Without Docker: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, then run
`.venv/bin/python -m paperbot live` under `systemd`, `tmux` or `screen`.

> Region note: a VPS outside the US might be blocked by Binance.US. The bot then falls back to
> Kraken and Coinbase on its own. You can also set the order yourself:
> `PAPERBOT__DATA__EXCHANGES='[kraken, coinbaseexchange, binanceus]'`.

## Configuration

Everything is in `config.yaml`. Any value can be overridden with environment variables or `.env`:
`PAPERBOT__PORTFOLIO__STARTING_BALANCE=250`, `PAPERBOT__STRATEGY__BUY_THRESHOLD=0.3`, and so on.
Useful knobs:
- `strategy.weights`: how much the AI, indicators and sentiment each count.
- `strategy.buy_threshold` / `sell_threshold` / `min_ai_confidence`: higher values mean fewer, pickier trades.
- `model.horizon`, `model.retrain_hours`, `model.online_weight` / `batch_weight`.
- `portfolio.*`: fees, slippage, sizing, stops, drawdown guard.
- `sentiment.fear_greed_mode`: `contrarian` (fear = bullish) or `momentum`.

`python -m paperbot reset` starts the portfolio over at the starting balance and keeps the old state as `state.json.bak`. Delete `data/models/` to retrain the models from scratch.

## Limitations, read before trusting any number

- **Fees dominate at 1 minute.** A round trip costs about 0.3% (fees plus slippage), while a typical 1-minute move is around 0.05–0.1%. Most 1m strategies lose money after costs. Expect that, and judge the bot on what you learn from it.
- The batch model's hold-out accuracy is usually 52–62%, a weak edge. Batch "hold-out" accuracy and the live accuracy the bot logs (scored on predictions it actually made) are separate measurements. Neither one promises profit.
- The backtest holds sentiment at today's value, because free historical 1m-resolution sentiment doesn't exist. It also uses each candle's close as the fill price, and when a candle touches both the stop and the take-profit it assumes the stop hit first.
- Public data can arrive late or have gaps. The bot falls back between exchanges, so prices may come from slightly different venues (USD vs USDT).
- RSS feeds and the Fear & Greed API can change or rate-limit. The bot treats a failed source as neutral.
- Long-only spot simulation: no shorting, leverage, funding or partial fills.

## Sample output

`sample_logs/` has real logs from verification runs on live market data (Nov 23, 2026, America/Chicago):
- `live_run_default_config.log`: 7 minutes with the default config. Every signal was HOLD because no score cleared the conservative buy threshold.
- `live_run_forced_trades_demo.log` and `forced_trades_demo_trades.csv`: a short run with the thresholds deliberately loosened through env overrides, only to exercise the simulated BUY, stop-loss and SELL path.
