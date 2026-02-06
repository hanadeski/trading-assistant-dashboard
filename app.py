# force redeploy
import sys
import time
from pathlib import Path
import json
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
from alerts.telegram import send_telegram_message, format_trade_alert
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
st.session_state.setdefault("last_alerted_action", {})   # dict[symbol] -> str
st.session_state.setdefault("last_alerted_ts", {})       # dict[symbol] -> int
st.session_state.setdefault("decision_log", [])          # list[dict]
st.session_state.setdefault(
    "adaptive_thresholds",
    {
        "setup_score_threshold": 7.0,
        "execution_score_threshold": 8.5,
        "execution_confidence_min": 8.5,
    },
)
st.session_state.setdefault("decision_log_max", 1000)

PERSIST_PATH = Path(__file__).parent / "state" / "decision_store.json"

def load_persisted_state():
    if not PERSIST_PATH.exists():
        return
    try:
        payload = json.loads(PERSIST_PATH.read_text())
    except Exception:
        return
    if isinstance(payload, dict):
        log = payload.get("decision_log")
        thresholds = payload.get("adaptive_thresholds")
        if isinstance(log, list):
            st.session_state["decision_log"] = log[-st.session_state["decision_log_max"] :]
        if isinstance(thresholds, dict):
            st.session_state["adaptive_thresholds"].update(thresholds)

def persist_state():
    payload = {
        "decision_log": st.session_state["decision_log"][-st.session_state["decision_log_max"] :],
        "adaptive_thresholds": st.session_state["adaptive_thresholds"],
    }
    def _json_safe(value):
        if isinstance(value, dict):
            return {k: _json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_json_safe(v) for v in value]
        if isinstance(value, set):
            return [_json_safe(v) for v in value]
        if isinstance(value, Path):
            return str(value)
        if hasattr(value, "item") and callable(value.item):
            try:
                return value.item()
            except Exception:
                pass
        return value

    PERSIST_PATH.write_text(json.dumps(_json_safe(payload)))

load_persisted_state()

ALERT_COOLDOWN_SECS = 60 * 30  # 30 minutes
ALERT_CONFIDENCE_MIN = 8.0

def maybe_send_trade_alerts(decisions):
    now = int(time.time())
    last_action = st.session_state["last_alerted_action"]
    last_ts = st.session_state["last_alerted_ts"]

    for decision in decisions:
        if decision.action not in ("BUY NOW", "SELL NOW"):
            continue
        if ALERT_HIGHCONF and decision.confidence < ALERT_CONFIDENCE_MIN:
            continue

        last = last_action.get(decision.symbol)
        last_time = last_ts.get(decision.symbol, 0)
        in_window = (now - last_time) < ALERT_COOLDOWN_SECS

        if in_window and last == decision.action and not ALERT_MODE3:
            continue

        if send_telegram_message(format_trade_alert(decision)):
            last_action[decision.symbol] = decision.action
            last_ts[decision.symbol] = now

def log_decisions(decisions, factors_by_symbol):
    now = int(time.time())
    log = st.session_state["decision_log"]
    for decision in decisions:
        factors = factors_by_symbol.get(decision.symbol, {})
        log.append({
            "ts": now,
            "symbol": decision.symbol,
            "action": decision.action,
            "confidence": round(float(decision.confidence), 2),
            "score": round(float(getattr(decision, "score", 0.0)), 2),
            "bias": decision.bias,
            "mode": decision.mode,
            "rr": factors.get("rr"),
            "volatility_risk": factors.get("volatility_risk"),
            "liquidity_ok": factors.get("liquidity_ok"),
            "structure_ok": factors.get("structure_ok"),
            "fvg_score": factors.get("fvg_score"),
            "htf_bias": factors.get("htf_bias"),
            "outcome": None,
        })
    if len(log) > st.session_state["decision_log_max"]:
        st.session_state["decision_log"] = log[-st.session_state["decision_log_max"] :]
    persist_state()

def adapt_thresholds():
    log = st.session_state["decision_log"]
    if len(log) < 60:
        return st.session_state["adaptive_thresholds"]

    recent = log[-120:]
    buy_sell = sum(1 for entry in recent if entry["action"] in ("BUY NOW", "SELL NOW"))
    ratio = buy_sell / max(len(recent), 1)

    thresholds = dict(st.session_state["adaptive_thresholds"])
    if ratio < 0.05:
        thresholds["execution_score_threshold"] = max(
            7.5, thresholds["execution_score_threshold"] - 0.2
        )
        thresholds["execution_confidence_min"] = max(
            7.8, thresholds["execution_confidence_min"] - 0.2
        )
    elif ratio > 0.15:
        thresholds["execution_score_threshold"] = min(
            9.2, thresholds["execution_score_threshold"] + 0.2
        )
        thresholds["execution_confidence_min"] = min(
            9.2, thresholds["execution_confidence_min"] + 0.2
        )

    outcomes = [entry for entry in recent if entry.get("outcome") in ("tp", "sl", "breakeven")]
    if len(outcomes) >= 20:
        wins = sum(1 for entry in outcomes if entry["outcome"] == "tp")
        win_rate = wins / max(len(outcomes), 1)
        if win_rate < 0.4:
            thresholds["execution_confidence_min"] = min(
                9.5, thresholds["execution_confidence_min"] + 0.2
            )
        elif win_rate > 0.6:
            thresholds["execution_confidence_min"] = max(
                7.8, thresholds["execution_confidence_min"] - 0.2
            )

    thresholds["setup_score_threshold"] = min(
        thresholds["execution_score_threshold"] - 0.6,
        thresholds["setup_score_threshold"],
    )

    st.session_state["adaptive_thresholds"] = thresholds
    persist_state()
    return thresholds

def record_trade_outcome(symbol: str, outcome: str):
    """
    Stub for manual outcome tracking (e.g., 'tp', 'sl', 'breakeven').
    """
    for entry in reversed(st.session_state["decision_log"]):
        if entry.get("symbol") == symbol and entry.get("outcome") is None:
            entry["outcome"] = outcome
            break
    persist_state()
# =========================
# 10B — Safety / debug toggles
# =========================
with st.sidebar.expander("⚙️ Safety toggles", expanded=False):
    DEBUG = st.toggle("DEBUG (show full exceptions)", value=False)
    ALERT_MODE3 = st.toggle("Telegram Mode 3 (opens + closes)", value=True)
    ALERT_HIGHCONF = st.toggle("High-confidence BUY/SELL alerts", value=True)
    LIVE_DATA = st.toggle("Live data (yfinance)", value=True)
    AUTO_REFRESH = st.toggle("Auto-refresh snapshot", value=False)
    REFRESH_SECONDS = st.slider("Refresh interval (seconds)", 30, 600, 120, step=30)

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

    def market_regime(close_series):
        if close_series is None or close_series.empty or len(close_series) < 55:
            return "range"
        ema_mid = close_series.ewm(span=50, adjust=False).mean()
        slope = ema_mid.iloc[-1] - ema_mid.iloc[-10]
        slope_pct = abs(slope) / close_series.iloc[-1]
        return "trend" if slope_pct > 0.0006 else "range"

    thresholds = adapt_thresholds()

    for sym in symbols:
        try:
            df = fetch_ohlc(sym, interval="15m", period="5d")
            htf_df = fetch_ohlc(sym, interval="1h", period="1mo")
        except Exception:
            df = None
            htf_df = None

        if df is None or df.empty or len(df) < 60:
            factors_by_symbol[sym] = {
                "bias": "neutral",
                "session_boost": 0.0,
                "structure_ok": False,
                "liquidity_ok": False,
                "certified": False,
                "regime": "range",
                "rr": 0.0,
                "near_fvg": False,
                "fvg_score": 0.0,
                "df": df,
                "htf_bias": "neutral",
                "news_risk": "none",
                "volatility_risk": "normal",
                "entry": "TBD",
                "stop": "TBD",
                "tp1": "TBD",
                "tp2": "TBD",
                "setup_score_threshold": thresholds["setup_score_threshold"],
                "execution_score_threshold": thresholds["execution_score_threshold"],
                "execution_confidence_min": thresholds["execution_confidence_min"],
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

        regime = market_regime(c)

        htf_bias = "neutral"
        if htf_df is not None and not htf_df.empty and len(htf_df) >= 50:
            htf_c = htf_df["close"]
            htf_fast = ema(htf_c, 20)
            htf_slow = ema(htf_c, 50)
            if htf_fast.iloc[-1] > htf_slow.iloc[-1]:
                htf_bias = "bullish"
            elif htf_fast.iloc[-1] < htf_slow.iloc[-1]:
                htf_bias = "bearish"

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
            "regime": regime,
            "rr": rr,
            "near_fvg": False,
            "fvg_score": 0.0,
            "df": df,
            "htf_bias": htf_bias,
            "news_risk": "none",
            "volatility_risk": volatility_risk,
            "entry": round(entry, 5),
            "stop": round(stop, 5) if isinstance(stop, float) else stop,
            "tp1": round(tp1, 5) if isinstance(tp1, float) else tp1,
            "tp2": round(tp2, 5) if isinstance(tp2, float) else tp2,
            "setup_score_threshold": thresholds["setup_score_threshold"],
            "execution_score_threshold": thresholds["execution_score_threshold"],
            "execution_confidence_min": thresholds["execution_confidence_min"],
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
if AUTO_REFRESH:
    st_autorefresh = getattr(st, "autorefresh", None)
    if callable(st_autorefresh):
        st_autorefresh(interval=REFRESH_SECONDS * 1000, key="snapshot_autorefresh")
    else:
        st.caption("Auto-refresh not available in this Streamlit version.")

def run_snapshot():
    if not LIVE_DATA:
        st.warning("Live data is OFF. Enable it in Safety toggles.")
        return
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
            maybe_send_trade_alerts(decisions)
            log_decisions(decisions, factors_by_symbol)

            st.session_state.profiles = profiles
            st.session_state.decisions = decisions
            st.session_state.factors_by_symbol = factors_by_symbol
            st.session_state.decisions_by_symbol = decisions_by_symbol
            st.session_state.snapshot_ready = True

            st.success("Snapshot built ✅")

        except Exception as e:
            st.session_state.snapshot_ready = False
            fail_soft("Snapshot build failed", e)

if st.button("🔄 Build Snapshot"):
    run_snapshot()
elif AUTO_REFRESH and LIVE_DATA:
    last_auto = st.session_state.get("last_auto_snapshot_ts", 0)
    now = time.time()
    if now - last_auto >= max(REFRESH_SECONDS - 1, 1):
        st.session_state["last_auto_snapshot_ts"] = now
        run_snapshot()

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
