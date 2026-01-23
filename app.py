# force redeploy
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))
import streamlit as st
from engine.profiles import get_profiles
from engine.decision_layer import run_decisions
# live data import
from data.live_data import fetch_ohlc

from components.top_bar import render_top_bar
from components.asset_table import render_asset_table
from components.ai_commentary import render_ai_commentary
from components.asset_detail import render_asset_detail
from alerts.telegram import send_telegram_message, format_trade_alert
from state.session_state import init_session_state, can_send_alert, mark_alert_sent

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

profiles = get_profiles()
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

for sym in symbols:
    df = fetch_ohlc(sym, interval="15m", period="5d")

    if df.empty or len(df) < 60:
        factors_by_symbol[sym] = {
            "bias": "neutral",
            "session_boost": 0.2,
            "structure_ok": False,
            "liquidity_ok": False,
            "rr": 0.0,
            "news_risk": "none",
            "volatility_risk": "normal",
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
    structure_ok = abs(slope) > (c.iloc[-1] * 0.0002)

    # Liquidity OK (v1 proxy): range expansion
    last_range = (df["high"].iloc[-1] - df["low"].iloc[-1])
    avg_range = (df["high"] - df["low"]).rolling(20).mean().iloc[-1]
    liquidity_ok = last_range > avg_range * 1.2

    # ATR-based plan scaffold
    a = atr(df).iloc[-1]
    entry = float(c.iloc[-1])
    if a != a or a == 0:  # NaN check
        a = entry * 0.001

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
        rr = round((reward / risk) if risk else 0.0, 2)
    else:
        rr = 0.0

    certified = liquidity_ok and structure_ok and rr >= 3.0

    factors_by_symbol[sym] = {
        "bias": bias,
        "session_boost": 0.5,
        "structure_ok": structure_ok,
        "liquidity_ok": liquidity_ok,
        "certified": certified,
        "rr": rr,
        "news_risk": "none",
        "volatility_risk": "normal",
        "entry": round(entry, 5),
        "stop": round(stop, 5) if isinstance(stop, float) else stop,
        "tp1": round(tp1, 5) if isinstance(tp1, float) else tp1,
        "tp2": round(tp2, 5) if isinstance(tp2, float) else tp2,
    }

decisions = run_decisions(profiles, factors_by_symbol)
decisions_by_symbol = {d.symbol: d for d in decisions}


# Telegram alerts only on high-confidence BUY/SELL
for d in decisions:
    if d.confidence >= 9.0 and d.action in ("BUY NOW", "SELL NOW"):
        if can_send_alert(st.session_state, d.symbol, d.action):
            msg = format_trade_alert(d)
            ok = send_telegram_message(msg)
            mark_alert_sent(st.session_state, d.symbol, d.action)
            st.session_state[f"telegram_{d.symbol}"] = "sent" if ok else "not_configured"

render_top_bar(news_flag="Live prices (v1)")

selected = st.session_state.selected_symbol
if selected:
    pmap = {p.symbol: p for p in profiles}
    render_asset_detail(pmap[selected], decisions_by_symbol[selected])
else:
    left, right = st.columns([0.7, 0.3], gap="large")
    with left:
        render_asset_table(decisions, profiles)
    with right:
        top = sorted(decisions, key=lambda d: d.confidence, reverse=True)
        render_ai_commentary(top[0] if top else None)

    st.caption("Step 1 is running with mock data. Next: wire in real EUR/USD + Gold decisions and real charts, then live feeds.")
