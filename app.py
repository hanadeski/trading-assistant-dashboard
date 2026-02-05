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
# Ensure profiles exist even before snapshot so the homepage can render
if not st.session_state.get("profiles"):
    st.session_state.profiles = get_profiles()
st.session_state.setdefault("portfolio_last_closed_count", 0)
st.session_state.setdefault("portfolio_last_open_count", 0)
# --- Step 11A: Keep last-known-good market data so the UI never goes blank ---
st.session_state.setdefault("last_good_ohlc", {})        # dict[symbol] -> pd.DataFrame
st.session_state.setdefault("ohlc_errors", {})           # dict[symbol] -> str
st.session_state.setdefault("ohlc_used_fallback", set()) # set of symbols that used fallback this run
# =========================
# 10B — Safety / debug toggles
# =========================
with st.sidebar.expander("⚙️ Safety toggles", expanded=False):
    DEBUG = st.toggle("DEBUG (show full exceptions)", value=False)
    ALERT_MODE3 = st.toggle("Telegram Mode 3 (opens + closes)", value=True)
    ALERT_HIGHCONF = st.toggle("High-confidence BUY/SELL alerts", value=True)
    LIVE_DATA = st.toggle("Live data (yfinance)", value=True)

def fail_soft(title: str, e: Exception):
    st.error(f"{title}: {e}")
    if DEBUG:
        st.exception(e)

# 12 – Snapshot Cache (12.1 → 12.5)
# =========================

@st.cache_data(ttl=60, show_spinner=False)
def build_snapshot():
    """
    Build a stable snapshot of:
    - profiles
    - symbols
    - factors_by_symbol
    - decisions
    - decisions_by_symbol
    """
    # --- Profiles ---
    profiles = get_profiles()
    symbols = [p.symbol for p in profiles]

    # --- Live factors ---
    factors_by_symbol = {}

    def ema(series, n):
        return series.ewm(span=n, adjust=False).mean()

    def atr(df, n=14):
        high, low, close = df["high"], df["low"], df["close"]
        tr = (high - low).to_frame("hl")
        tr["hc"] = (high - close.shift()).abs()
        tr["lc"] = (low - close.shift()).abs()
        return tr.max(axis=1).rolling(n).mean()

    def detect_regime(structure_ok: bool, liquidity_ok: bool, volatility_risk: str) -> str:
        """
        Simple regime classifier:
        - extreme_vol: ATR% too high -> block execution
        - chop: no structure -> no forcing BUY/SELL
        - transition: structure but liquidity weak -> WATCH only
        - trend: structure + liquidity -> allow breakout logic later
        """
        if volatility_risk == "extreme":
            return "extreme_vol"
        if not structure_ok:
            return "chop"
        if structure_ok and not liquidity_ok:
            return "transition"
        return "trend"

    for sym in symbols:
        try:
            df = fetch_ohlc(sym, interval="15m", period="5d")
        except Exception:
            df = None

        if df is None or df.empty or len(df) < 60:
            factors_by_symbol[sym] = {
                "bias": "neutral",
                "session_boost": 0.0,
                "structure_ok": False,
                "liquidity_ok": False,
                "certified": False,
                "rr": 0.0,
                "near_fvg": False,
                "fvg_score": 0.0,
                "df": df,
                "news_risk": "none",
                "volatility_risk": "normal",
                "regime": "no_data",
                "entry": "TBD",
                "stop": "TBD",
                "tp1": "TBD",
                "tp2": "TBD",
            }
            continue

        # === your existing factor logic stays the same ===
        c = df["close"]
        ema_fast = ema(c, 20)
        ema_slow = ema(c, 50)

        if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
            bias = "bullish"
        elif ema_fast.iloc[-1] < ema_slow.iloc[-1]:
            bias = "bearish"
        else:
            bias = "neutral"

        slope = ema_fast.iloc[-1] - ema_fast.iloc[-10]
        structure_ok = abs(slope) > (c.iloc[-1] * 0.0002)

        last_range = df["high"].iloc[-1] - df["low"].iloc[-1]
        avg_range = (df["high"] - df["low"]).rolling(20).mean().iloc[-1]
        liquidity_ok = last_range > avg_range * 1.1

        a = atr(df).iloc[-1]
        a = float(a) if pd.notna(a) else 0.0
        entry = float(c.iloc[-1])
        atr_pct = (a / entry) if entry else 0.0

        high_thr, extreme_thr = 0.006, 0.010
        if sym in ("XAUUSD", "XAGUSD", "WTI"):
            high_thr, extreme_thr = 0.008, 0.012

        volatility_risk = (
            "extreme" if atr_pct >= extreme_thr
            else "high" if atr_pct >= high_thr
            else "normal"
        )
        # --- Regime detection ---
        regime = detect_regime(
            structure_ok=structure_ok,
            liquidity_ok=liquidity_ok,
            volatility_risk=volatility_risk,
        )

        if bias == "bullish":
            stop = entry - 1.2 * a
            tp1 = entry + 2 * (entry - stop)
            tp2 = entry + 3 * (entry - stop)
        elif bias == "bearish":
            stop = entry + 1.2 * a
            tp1 = entry - 2 * (stop - entry)
            tp2 = entry - 3 * (stop - entry)
        else:
            stop = tp1 = tp2 = "TBD"

        rr = (
            round(abs(tp1 - entry) / abs(entry - stop), 2)
            if bias in ("bullish", "bearish") and stop != "TBD"
            else 0.0
        )

        certified = liquidity_ok and structure_ok and rr >= 3.0

        factors_by_symbol[sym] = {
            "bias": bias,
            "session_boost": 0.5,
            "structure_ok": structure_ok,
            "liquidity_ok": liquidity_ok,
            "certified": certified,
            "rr": rr,
            "near_fvg": False,
            "fvg_score": 0.0,
            "df": df,
            "news_risk": "none",
            "volatility_risk": volatility_risk,
            "regime": regime,
            "entry": round(entry, 5),
            "stop": round(stop, 5) if isinstance(stop, float) else stop,
            "tp1": round(tp1, 5) if isinstance(tp1, float) else tp1,
            "tp2": round(tp2, 5) if isinstance(tp2, float) else tp2,
        }

    # --- Decisions ---
    decisions = run_decisions(profiles, factors_by_symbol)
    decisions_by_symbol = {d.symbol: d for d in decisions}

    return profiles, symbols, factors_by_symbol, decisions, decisions_by_symbol

# =========================================================
# UI — Always render homepage (never blank)
# =========================================================

# Ensure profiles always exist (even before snapshot)
profiles = st.session_state.get("profiles") or get_profiles()
st.session_state.profiles = profiles

decisions = st.session_state.get("decisions", [])
factors_by_symbol = st.session_state.get("factors_by_symbol", {})
decisions_by_symbol = st.session_state.get("decisions_by_symbol", {})

# ---------------------------------------------------------
# Header
# ---------------------------------------------------------
st.title("Trading Assistant")
st.caption("Booting… if this takes long, live data may be rate-limited.")
st.divider()

# ---------------------------------------------------------
# Snapshot state
# ---------------------------------------------------------
if "snapshot_ready" not in st.session_state:
    st.session_state.snapshot_ready = False

# ---------------------------------------------------------
# Snapshot button
# ---------------------------------------------------------
if st.button("🔄 Build Snapshot"):
    if not LIVE_DATA:
        st.warning("Live data is OFF. Enable it in Safety toggles.")
    else:
        with st.spinner("Building snapshot (live data)..."):
            try:
                (
                    profiles,
                    symbols,
                    factors_by_symbol,
                    decisions,
                    decisions_by_symbol,
                ) = build_snapshot()

                update_portfolio(st.session_state, decisions, factors_by_symbol)

                st.session_state.profiles = profiles
                st.session_state.decisions = decisions
                st.session_state.factors_by_symbol = factors_by_symbol
                st.session_state.decisions_by_symbol = decisions_by_symbol
                st.session_state.snapshot_ready = True

                st.success("Snapshot built ✅")

            except Exception as e:
                st.session_state.snapshot_ready = False
                fail_soft("Snapshot build failed", e)

# ---------------------------------------------------------
# Stable top UI (ALWAYS visible)
# ---------------------------------------------------------
try:
    render_portfolio_panel(st.session_state)
except Exception as e:
    fail_soft("Portfolio panel failed", e)

try:
    render_top_bar(news_flag="Live prices (v1)")
except Exception as e:
    fail_soft("Top bar failed", e)

st.divider()

# ---------------------------------------------------------
# Homepage body
# ---------------------------------------------------------
if not st.session_state.snapshot_ready:
    st.info(
        "Click **Build Snapshot** to load live data. "
        "If Yahoo is rate-limiting, wait a minute and try again."
    )
    render_asset_table([], profiles)

else:
    selected = st.session_state.get("selected_symbol")

    if selected:
        pmap = {p.symbol: p for p in profiles}
        render_asset_detail(
            pmap.get(selected),
            decisions_by_symbol.get(selected),
            factors_by_symbol.get(selected, {}),
        )
        render_ai_commentary(decisions_by_symbol.get(selected))

    else:
        left, right = st.columns([0.7, 0.3], gap="large")

        with left:
            render_asset_table(decisions, profiles)

        with right:
            top = sorted(
                decisions, key=lambda d: d.confidence, reverse=True
            )
            render_ai_commentary(top[0] if top else None)
