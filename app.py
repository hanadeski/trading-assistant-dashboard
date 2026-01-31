# force redeploy
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))
import streamlit as st
import pandas as pd
from engine.profiles import get_profiles
from engine.decision_layer import run_decisions
from engine.fvg import compute_fvg_context
from engine.portfolio import init_portfolio_state, update_portfolio


# live data import
from data.live_data import fetch_ohlc

from components.top_bar import render_top_bar
from components.asset_table import render_asset_table
from components.ai_commentary import render_ai_commentary
from components.asset_detail import render_asset_detail
from components.portfolio_panel import render_portfolio_panel
from alerts.telegram import send_telegram_message
from state.session_state import init_session_state

st.set_page_config(page_title="Trading Assistant", layout="wide", initial_sidebar_state="collapsed")

# Clean minimal dark theme
st.markdown("""
<style>
    .stApp { background: #0b0f14; color: #e6e6e6; }
    .block-container { padding-top: 1.1rem; }
    div[data-testid="stMetricValue"] { color: #e6e6e6; }
    div[data-testid="stMetricLabel"] { color: #9aa4ad; }
</style>
""", unsafe_allow_html=True)

init_session_state(st.session_state)
init_portfolio_state(st.session_state)
st.session_state.setdefault("portfolio_last_closed_count", 0)
st.session_state.setdefault("portfolio_last_open_count", 0)

try:
    profiles = get_profiles()
except Exception as e:
    st.error(f"Profiles failed: {e}")
    profiles = []

symbols = [p.symbol for p in profiles]

# STEP 2/3: live data -> build live factors
factors_by_symbol = {}

def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def atr(df, n=14):
    high, low, close = df["high"], df["low"], df["close"]
    tr = (high - low).to_frame("hl")
    tr["hc"] = (high - close.shift()).abs()
    tr["lc"] = (low - close.shift()).abs()
    return tr.max(axis=1).rolling(n).mean()
def to_native(x):
    """Convert numpy/pandas scalars to plain Python types for clean Debug output."""
    try:
        import numpy as np
        if isinstance(x, np.bool_):
            return bool(x)
        if isinstance(x, np.integer):
            return int(x)
        if isinstance(x, np.floating):
            return float(x)
    except Exception:
        pass
    return x

for sym in symbols:
    df = fetch_ohlc(sym, interval="15m", period="5d")

    if df is None or df.empty or len(df) < 60:
        factors_by_symbol[sym] = {
            "bias": "neutral",
            "session_boost": 0.0,
            "structure_ok": to_native(False),
            "liquidity_ok": to_native(False),
            "certified": to_native(False),
            "rr": to_native(0.0),
            "near_fvg": to_native(False),
            "fvg_score": to_native(0.0),
            "df": df,
            "news_risk": "none",
            "volatility_risk": "normal",
            "entry": "TBD",
            "stop": "TBD",
            "tp1": "TBD",
            "tp2": "TBD",
        }
        continue


    c = df["close"]
    ema_fast = ema(c, 20)
    ema_slow = ema(c, 50)

    # Bias (v1)
    if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
        bias = "bullish"
    elif ema_fast.iloc[-1] < ema_slow.iloc[-1]:
        bias = "bearish"
    else:
        bias = "neutral"

    # Structure OK (v1 proxy)
    slope = (ema_fast.iloc[-1] - ema_fast.iloc[-10])
    structure_ok = bool(abs(slope) > (c.iloc[-1] * 0.0002))

    # Liquidity OK (v1 proxy): range expansion
    last_range = (df["high"].iloc[-1] - df["low"].iloc[-1])
    avg_range = (df["high"] - df["low"]).rolling(20).mean().iloc[-1]
    liquidity_ok = bool(last_range > avg_range * 1.1)

    # --- Volatility risk (ATR as % of price) ---
    a = atr(df).iloc[-1]
    a = float(a) if pd.notna(a) else 0.0
    entry = float(c.iloc[-1])

    atr_pct = (a / entry) if entry else 0.0

    # Default FX thresholds
    high_thr = 0.006       # 0.6%
    extreme_thr = 0.010    # 1.0%

    # Commodities are noisier
    if sym in ("XAUUSD", "XAGUSD", "WTI"):
        high_thr = 0.008
        extreme_thr = 0.012

    if atr_pct >= extreme_thr:
        volatility_risk = "extreme"
    elif atr_pct >= high_thr:
        volatility_risk = "high"
    else:
        volatility_risk = "normal"

    if bias == "bullish":
        stop = entry - 1.2 * a
        tp1 = entry + 2.0 * (entry - stop)
        tp2 = entry + 3.0 * (entry - stop)
    elif bias == "bearish":
        stop = entry + 1.2 * a
        tp1 = entry - 2.0 * (stop - entry)
        tp2 = entry - 3.0 * (stop - entry)
    else:
        stop, tp1, tp2 = "TBD", "TBD", "TBD"

    if bias in ("bullish", "bearish"):
        risk = abs(entry - stop)
        reward = abs(tp1 - entry)
        rr = float(round((reward / risk) if risk else 0.0, 2))
    else:
        rr = 0.0

    # --- Certification ---
    certified = bool(liquidity_ok and structure_ok and rr >= 3.0)

    # --- FVG context (4.4B) ---
    fvg_ctx = compute_fvg_context(
        df,
        lookback=160,
        max_show=3,
        pad_bps=30.0
    )
    near_fvg = bool(fvg_ctx.get("near_fvg", False))
    fvg_score = float(fvg_ctx.get("fvg_score", 0.0))

    # --- Factors payload ---
    factors_by_symbol[sym] = {
        "bias": bias,
        "session_boost": to_native(0.5),
        "structure_ok": to_native(structure_ok),
        "liquidity_ok": to_native(liquidity_ok),
        "certified": to_native(certified),
        "rr": to_native(rr),
        "near_fvg": to_native(near_fvg),
        "fvg_score": to_native(fvg_score),
        "df": df,
        "news_risk": "none",
        "volatility_risk": volatility_risk,
        "entry": to_native(round(entry, 5)),
        "stop": to_native(round(stop, 5) if isinstance(stop, float) else stop),
        "tp1": to_native(round(tp1, 5) if isinstance(tp1, float) else tp1),
        "tp2": to_native(round(tp2, 5) if isinstance(tp2, float) else tp2),
    }

try:
    decisions = run_decisions(profiles, factors_by_symbol)
    decisions_by_symbol = {d.symbol: d for d in decisions}
    update_portfolio(st.session_state, decisions, factors_by_symbol)
except Exception as e:
    st.error(f"Decision/portfolio stage failed: {e}")
    decisions = []
    decisions_by_symbol = {}

# --- Step 9: Telegram alerts (Mode 3 = opens + closes, ignore TP1_PARTIAL) ---
st.session_state.setdefault("portfolio_last_open_count", 0)
st.session_state.setdefault("portfolio_last_closed_count", 0)

try:
    p = st.session_state.get("portfolio", {}) or {}
    opens = p.get("open_positions", []) or []
    closes = p.get("closed_trades", []) or []

    # New OPEN alerts (only newly-added rows)
    last_open_n = int(st.session_state.get("portfolio_last_open_count", 0))
    now_open_n = len(opens)
    if now_open_n > last_open_n:
        for pos in opens[last_open_n:now_open_n]:
            msg = (
                f"🟦 OPEN {pos.get('symbol')} | {pos.get('side')} | "
                f"size={pos.get('size')} entry={pos.get('entry')} stop={pos.get('stop')} "
                f"tp1={pos.get('tp1')} tp2={pos.get('tp2')} | risk%={pos.get('risk_pct')}"
            )
            # only send if Telegram is configured (function should handle this, but we guard anyway)
            send_telegram_message(msg)
    st.session_state["portfolio_last_open_count"] = now_open_n

    # New CLOSE alerts (ignore TP1_PARTIAL)
    last_close_n = int(st.session_state.get("portfolio_last_closed_count", 0))
    now_close_n = len(closes)
    if now_close_n > last_close_n:
        for t in closes[last_close_n:now_close_n]:
            reason = (t.get("reason") or "").upper()
            if reason == "TP1_PARTIAL":
                continue

            msg = (
                f"✅ CLOSE {t.get('symbol')} | {t.get('side')} | "
                f"exit={t.get('exit')} pnl={t.get('pnl')} | reason={reason}"
            )
            send_telegram_message(msg)
    st.session_state["portfolio_last_closed_count"] = now_close_n

except Exception as e:
    # Don't crash the app if Telegram isn't configured / network fails
    st.session_state["telegram_error"] = str(e)

render_portfolio_panel(st.session_state)
st.write("✅ DEBUG: after portfolio panel")

render_top_bar(news_flag="Live prices (v1)")
st.write("✅ DEBUG: after top bar")


selected = st.session_state.selected_symbol

try:
    if selected:
        pmap = {p.symbol: p for p in profiles}

        render_asset_detail(
            pmap[selected],
            decisions_by_symbol.get(selected),
            factors=factors_by_symbol.get(selected, {})
        )

        render_ai_commentary(
            decisions_by_symbol.get(selected)
        )

    else:
        left, right = st.columns([0.7, 0.3], gap="large")
        with left:
            render_asset_table(decisions, profiles)
        with right:
            top = sorted(decisions, key=lambda d: d.confidence, reverse=True)
            render_ai_commentary(top[0] if top else None)

except Exception as e:
    st.error(f"UI render stage failed: {e}")
    st.exception(e)

    st.caption("Step 1 is running with mock data. Next: wire in real EUR/USD + Gold decisions and real charts, then live feeds.")
