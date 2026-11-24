"""Streamlit dashboard for the paper-trading bot.  Run:  streamlit run dashboard/app.py"""
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import altair as alt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paperbot import DISCLAIMER  # noqa: E402
from paperbot.config import load_config  # noqa: E402

cfg = load_config()
TZ = cfg["logging"].get("display_timezone", "America/Chicago")
CHART_TZ = "America/New_York"
st.set_page_config(page_title="Paper Trading Bot", page_icon="📈", layout="wide")
title_col, menu_col = st.columns([10, 1])
with title_col:
    st.title("📈 Paper Trading Bot")
with menu_col:
    with st.popover("☰", help="Switch trading mode", use_container_width=True):
        selected_mode = st.radio(
            "Trading mode",
            ["Crypto", "Stocks"],
            key="trading_mode",
            help="Choose which market dashboard to view.",
        )
st.warning(DISCLAIMER)

if st.session_state.get("trading_mode", "Crypto") == "Stocks":
    def render_stocks():
        st.title("📊 Stock Watchlist")
        st.caption("All 25 watchlist stocks are scanned for new completed 15-minute bars while the stock scanner is running.")
        st.info("Signal ideas only · no stock orders are placed · market data uses Alpaca’s free IEX feed.")
        db = cfg["storage"]["sqlite"]
        status = None
        signals = pd.DataFrame()
        if Path(db).exists():
            with sqlite3.connect(db) as c:
                has_status = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stock_scan_status'").fetchone()
                if has_status:
                    row = c.execute("SELECT scanned_at,status,detail FROM stock_scan_status WHERE id=1").fetchone()
                    status = dict(zip(("scanned_at", "status", "detail"), row)) if row else None
                    signals = pd.read_sql_query("SELECT symbol,candle_time,price,action,reason FROM stock_signals", c)

        symbols = cfg.get("stocks", {}).get("symbols", [])
        if status:
            if status["status"] == "connected":
                st.success(status["detail"])
            elif status["status"] == "needs_api_keys":
                st.warning("Stock scanning is ready, but it needs an Alpaca paper account API key and secret. Add them to the project’s local .env file, then restart the bot.")
            elif status["status"] == "error":
                st.error(status["detail"])
            else:
                st.info(status["detail"])
            st.caption(f"Last scan check: {pd.to_datetime(status['scanned_at'], utc=True).tz_convert('America/New_York'):%Y-%m-%d %H:%M:%S %Z}")
        else:
            st.warning("No scan has run yet. Start the stock scanner with python -m paperbot stocks.")

        st.subheader("Buy / sell ideas")
        if not signals.empty:
            signals["Latest bar (New York)"] = pd.to_datetime(signals["candle_time"], utc=True).dt.tz_convert("America/New_York").dt.strftime("%b %d %H:%M")
            signals = signals.rename(columns={"symbol": "Stock", "price": "Price", "action": "Action", "reason": "Reason"})
            signals = signals[["Stock", "Price", "Action", "Reason", "Latest bar (New York)"]]
            signals["Price"] = signals["Price"].map(lambda x: f"${x:,.2f}")
            signals = signals.set_index("Stock").reindex(symbols).reset_index()
            signals["Action"] = signals["Action"].fillna("WAIT")
            signals["Reason"] = signals["Reason"].fillna("No market bars returned for this stock yet.")
            signals["Price"] = signals["Price"].fillna("—")
            signals["Latest bar (New York)"] = signals["Latest bar (New York)"].fillna("—")
        else:
            wait_reason = (status["detail"] if status else "Waiting for first scan.")
            signals = pd.DataFrame([{"Stock": symbol, "Price": "—", "Action": "WAIT", "Reason": wait_reason, "Latest bar (New York)": "—"} for symbol in symbols])
        st.dataframe(signals, hide_index=True, width="stretch")
        st.caption("Checks repeat every minute, including during market closures; signals update when fresh bars arrive. Signals only; no trades are placed.")

    st.fragment(run_every=30)(render_stocks)()
    st.stop()


def q(sql: str) -> pd.DataFrame:
    db = cfg["storage"]["sqlite"]
    if not Path(db).exists():
        return pd.DataFrame()
    with sqlite3.connect(db) as c:
        if "FROM candles" in sql and not c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='candles'").fetchone():
            return pd.DataFrame()
        df = pd.read_sql_query(sql, c)
    for col in ("time", "candle_time"):
        if col in df:
            df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601").dt.tz_convert(TZ)
    return df


def render():
    state_f = Path(cfg["storage"]["state_file"])
    state = json.loads(state_f.read_text()) if state_f.exists() else None
    if not state:
        st.info("No portfolio yet. Start the bot with `./run.sh` (or `python -m paperbot live`).")
    else:
        sb, eq = state["starting_balance"], state.get("equity", state["cash"])
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Equity", f"${eq:,.2f}", f"{100 * (eq / sb - 1):+.2f}%")
        c2.metric("Cash", f"${state['cash']:,.2f}")
        c3.metric("Realized PnL", f"${state['realized_pnl']:+,.4f}")
        c4.metric("Fees paid", f"${state['fees_paid']:,.4f}")
        wr = 100 * state["n_wins"] / state["n_trades"] if state["n_trades"] else 0
        c5.metric("Closed trades / win rate", f"{state['n_trades']} / {wr:.0f}%")
        if state.get("halted"):
            st.error("Max-drawdown guard triggered: new entries halted. Use `python -m paperbot reset` to start over.")
        st.caption(f"Last update: {pd.Timestamp(state.get('updated')).tz_convert(TZ):%Y-%m-%d %H:%M:%S %Z}")

        candles = q("SELECT candle_time, coin, open, high, low, close, volume FROM candles "
                "WHERE datetime(candle_time) >= datetime((SELECT MAX(candle_time) FROM candles), "
                "'-8 hours') ORDER BY candle_time")
    if not candles.empty:
        st.subheader("Market candles")
        coins = sorted(candles["coin"].unique())
        selected_coin = st.selectbox("Coin", coins, key="candle_coin")
        chart_data = candles[candles["coin"] == selected_coin].copy()
        # Keep New York wall time in the chart data; Vega otherwise serializes
        # timezone-aware values as UTC and can shift the displayed axis.
        chart_data["candle_time"] = (
            pd.to_datetime(chart_data["candle_time"], utc=True)
            .dt.tz_convert(CHART_TZ).dt.tz_localize(None)
        )
        chart_data = chart_data.set_index("candle_time").sort_index()
        bars = chart_data.resample("15min", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        complete = chart_data["close"].resample("15min", label="left", closed="left").count() == 15
        chart_data = bars[complete].tail(16).reset_index()
        chart_data["up"] = chart_data["close"] >= chart_data["open"]
        price_scale = alt.Scale(zero=False, nice=True)
        x_axis = alt.X("candle_time:T", title="New York time", axis=alt.Axis(format="%H:%M"))
        candle_color = alt.condition(alt.datum.up, alt.value("#22c55e"), alt.value("#f04438"))
        tooltip = [alt.Tooltip("candle_time:T", title="New York time", format="%b %d %H:%M"),
                   alt.Tooltip("open:Q", format="$,.2f"), alt.Tooltip("high:Q", format="$,.2f"),
                   alt.Tooltip("low:Q", format="$,.2f"), alt.Tooltip("close:Q", format="$,.2f"),
                   alt.Tooltip("volume:Q", format=".4f")]
        base = alt.Chart(chart_data).encode(x=x_axis, color=candle_color, tooltip=tooltip)
        wicks = base.mark_rule(strokeWidth=2).encode(
            y=alt.Y("low:Q", title="Price (USD)", scale=price_scale, axis=alt.Axis(format="$,.2f")),
            y2="high:Q")
        bodies = base.mark_bar(size=28).encode(
            y=alt.Y("open:Q", scale=price_scale), y2="close:Q")
        price_chart = (wicks + bodies).properties(height=420, title=f"{selected_coin} · 15 minute candles")
        volume_chart = alt.Chart(chart_data).mark_bar(size=28).encode(
            x=alt.X("candle_time:T", title=None, axis=alt.Axis(format="%H:%M")),
            y=alt.Y("volume:Q", title="Volume", axis=alt.Axis(format="~s")),
            color=candle_color,
            tooltip=[alt.Tooltip("candle_time:T", title="New York time", format="%b %d %H:%M"),
                     alt.Tooltip("volume:Q", format=".4f")]
        ).properties(height=105)
        st.altair_chart(alt.vconcat(price_chart, volume_chart, spacing=4).resolve_scale(x="shared"), width="stretch")
        st.caption("Latest 16 completed 15-minute candles (4 hours) · New York time · green closed higher, red closed lower.")

        st.subheader("Open positions")
        rows = []
        for coin, p in state["positions"].items():
            px = state.get("prices", {}).get(coin, p["entry_price"])
            rows.append({"coin": coin, "qty": p["qty"], "entry": p["entry_price"], "last": px,
                         "value $": p["qty"] * px, "unrealized $": p["qty"] * px - p["cost"],
                         "stop": p["stop"], "take profit": p["take_profit"], "opened": p["entry_time"]})
        st.dataframe(pd.DataFrame(rows) if rows else pd.DataFrame([{"info": "flat - no open positions"}]),
                     width="stretch")

    eqdf = q("SELECT time, equity, cash FROM equity ORDER BY id")
    if not eqdf.empty:
        st.subheader("Equity curve")
        st.line_chart(eqdf.set_index("time")[["equity", "cash"]])

    st.subheader("Recent trades")
    tr = q("SELECT time, coin, side, qty, price, fee, value, pnl, reason, equity_after FROM trades "
           "WHERE mode='live' ORDER BY id DESC LIMIT 50")
    st.dataframe(tr if not tr.empty else pd.DataFrame([{"info": "no trades yet"}]), width="stretch")

    st.subheader("Latest signals")
    sig = q("SELECT candle_time, coin, close, action, score, ai_score, indicator_score, sentiment_score, p_up, "
            "rsi, fear_greed, reason FROM signals ORDER BY id DESC LIMIT 40")
    if not sig.empty:
        for coin in sig["coin"].unique():
            s = sig[sig["coin"] == coin].sort_values("candle_time")
            st.caption(f"{coin} ensemble score (last 40 signals)")
            st.line_chart(s.set_index("candle_time")[["score", "ai_score", "indicator_score", "sentiment_score"]])
        st.dataframe(sig, width="stretch")



auto = st.sidebar.checkbox("Auto-refresh every 30s", value=True)
st.fragment(run_every=30 if auto else None)(render)()
