# force redeploy
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

import pandas as pd
import streamlit as st

from alerts.telegram import format_trade_alert, send_telegram_message
from components.ai_commentary import render_ai_commentary
from components.asset_detail import render_asset_detail
from components.asset_table import render_asset_table
from components.portfolio_panel import render_portfolio_panel
from components.top_bar import render_top_bar
from data.live_data import fetch_ohlc
from data.news_calendar import get_high_impact_news
from engine.decision_layer import run_decisions
from engine.fvg import compute_fvg_context
from engine.portfolio import init_portfolio_state, update_portfolio
from engine.profiles import get_profiles
from state.session_state import init_session_state


def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    for sep in (".", "-", "_"):
        if sep in s:
            s = s.split(sep, 1)[0]
    return s


def _price_decimals(symbol: str) -> int:
    s = _normalize_symbol(symbol)
    if s.endswith("JPY"):
        return 3
    if s in {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF"}:
        return 5
    if s in {"XAUUSD", "XAGUSD", "WTI", "US30", "US100", "NAS100", "USTEC", "US500", "SPX500", "UK100", "DE40"}:
        return 2
    if len(s) == 6 and s.isalpha():
        return 5
    return 2


def _round_price(value, symbol: str):
    try:
        return round(float(value), _price_decimals(symbol))
    except Exception:
        return value


st.set_page_config(page_title="Trading Assistant", layout="wide", initial_sidebar_state="collapsed")

# Clean minimal dark theme
st.markdown(
    """
<style>
    .stApp { background: #0b0f14; color: #e6e6e6; }
    .block-container { padding-top: 1.1rem; }
    div[data-testid="stMetricValue"] { color: #e6e6e6; }
    div[data-testid="stMetricLabel"] { color: #9aa4ad; }
</style>
""",
    unsafe_allow_html=True,
)

init_session_state(st.session_state)
init_portfolio_state(st.session_state)

if not st.session_state.get("profiles"):
    st.session_state.profiles = get_profiles()

st.session_state.setdefault("portfolio_last_closed_count", 0)
st.session_state.setdefault("portfolio_last_open_count", 0)
st.session_state.setdefault("last_good_ohlc", {})
st.session_state.setdefault("ohlc_errors", {})
st.session_state.setdefault("ohlc_used_fallback", set())
st.session_state.setdefault("last_alerted_action", {})
st.session_state.setdefault("last_alerted_ts", {})
st.session_state.setdefault("last_snapshot_ts", 0)

ALERT_COOLDOWN_SECS = 60 * 60
ALERT_CONFIDENCE_MIN = 8.0  # used for SNIPER / default alert gate
SNAPSHOT_INTERVAL_SECS = 30

TOP_PRIORITY_UNIVERSE = {
    "US100",  # NAS100
    "US30",
    "US500",  # SPX proxy
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "XAUUSD",
}

with st.sidebar.expander("⚙️ Safety toggles", expanded=False):
    DEBUG = st.toggle("DEBUG (show full exceptions)", value=False)
    ALERT_MODE3 = st.toggle("Telegram Mode 3 (opens + closes)", value=False)
    ALERT_HIGHCONF = st.toggle("High-confidence BUY/SELL alerts", value=True)
    LIVE_DATA = st.toggle("Live data (broker/API + fallback)", value=True)


def fail_soft(title: str, e: Exception):
    st.error(f"{title}: {e}")
    if DEBUG:
        st.exception(e)


def _alert_min_conf(decision) -> float:
    """
    Alert gating aligned with scoring/decision_layer:
      - CONTINUATION can alert at 6.5 (since it can execute at 6.5)
      - Everything else uses ALERT_CONFIDENCE_MIN (default 8.0)
    """
    setup = str((getattr(decision, "meta", {}) or {}).get("setup_type", "")).upper()
    if setup == "CONTINUATION":
        return 6.5
    return float(ALERT_CONFIDENCE_MIN)


def maybe_send_trade_alerts(decisions):
    now = int(time.time())
    last_action = st.session_state["last_alerted_action"]
    last_ts = st.session_state["last_alerted_ts"]

    for decision in decisions:
        if decision.action not in ("BUY NOW", "SELL NOW"):
            continue

        min_conf = _alert_min_conf(decision)

        # If High-confidence toggle is ON, still respect setup-specific thresholds
        # (continuation can alert at 6.5; sniper stays at ALERT_CONFIDENCE_MIN)
        if ALERT_HIGHCONF and decision.confidence < min_conf:
            continue

        last = last_action.get(decision.symbol)
        last_time = last_ts.get(decision.symbol, 0)
        in_window = (now - last_time) < ALERT_COOLDOWN_SECS

        if in_window:
            if not ALERT_MODE3:
                continue
            if last == decision.action:
                continue

        if send_telegram_message(format_trade_alert(decision)):
            last_action[decision.symbol] = decision.action
            last_ts[decision.symbol] = now


@st.cache_data(ttl=60, show_spinner=False)
def build_snapshot():
    profiles = get_profiles()
    symbols = [p.symbol for p in profiles]
    factors_by_symbol = {}

    def atr(df, n=14):
        high, low, close = df["high"], df["low"], df["close"]
        tr = (high - low).to_frame("hl")
        tr["hc"] = (high - close.shift()).abs()
        tr["lc"] = (low - close.shift()).abs()
        return tr.max(axis=1).rolling(n).mean()

    def session_name(now_utc: datetime) -> str:
        hour = now_utc.hour
        if 12 <= hour < 16:
            return "London + NY Overlap"
        if 7 <= hour < 12:
            return "London"
        if 12 <= hour < 21:
            return "New York"
        return "Asia / Off-hours"

    # ✅ Asia NOT stricter anymore
    def session_valid_flags(sess: str):
        return {
            "session_valid_sniper": True,
            "session_valid_continuation": True,
        }

    def current_4h_open(df_4h: pd.DataFrame, fallback_price: float) -> float:
        if df_4h is not None and not df_4h.empty and "open" in df_4h.columns:
            return float(df_4h["open"].iloc[-1])
        return float(fallback_price)

    def compute_liquidity_targets(df_15m: pd.DataFrame):
        if df_15m is None or df_15m.empty or len(df_15m) < 20:
            return None
        daily_high = float(df_15m["high"].tail(96).max()) if len(df_15m) >= 96 else float(df_15m["high"].max())
        daily_low = float(df_15m["low"].tail(96).min()) if len(df_15m) >= 96 else float(df_15m["low"].min())
        weekly_high = float(df_15m["high"].tail(96 * 5).max()) if len(df_15m) >= 96 * 5 else daily_high
        weekly_low = float(df_15m["low"].tail(96 * 5).min()) if len(df_15m) >= 96 * 5 else daily_low
        return {
            "daily_high": daily_high,
            "daily_low": daily_low,
            "weekly_high": weekly_high,
            "weekly_low": weekly_low,
        }

    def detect_accumulation(df_4h: pd.DataFrame) -> bool:
        if df_4h is None or df_4h.empty or len(df_4h) < 20:
            return False
        ranges = (df_4h["high"] - df_4h["low"]).tail(12)
        mean_range = float(ranges.mean()) if not ranges.empty else 0.0
        if mean_range <= 0:
            return False
        return float(ranges.iloc[-1]) <= (mean_range * 0.85)

    def detect_sweep(df_15m: pd.DataFrame):
        if df_15m is None or df_15m.empty or len(df_15m) < 40:
            return False, False
        prior_high = float(df_15m["high"].iloc[-35:-1].max())
        prior_low = float(df_15m["low"].iloc[-35:-1].min())
        last_high = float(df_15m["high"].iloc[-1])
        last_low = float(df_15m["low"].iloc[-1])
        last_close = float(df_15m["close"].iloc[-1])
        sweep_above = (last_high > prior_high) and (last_close < prior_high)
        sweep_below = (last_low < prior_low) and (last_close > prior_low)
        return sweep_above, sweep_below

    def detect_mss(df_15m: pd.DataFrame):
        if df_15m is None or df_15m.empty or len(df_15m) < 30:
            return False, False
        swing_high = float(df_15m["high"].iloc[-22:-1].max())
        swing_low = float(df_15m["low"].iloc[-22:-1].min())
        last_close = float(df_15m["close"].iloc[-1])
        return last_close > swing_high, last_close < swing_low

    # ✅ UPDATED (1) soft break + (2) dynamic body_ok threshold when narrative stacked
    def detect_entry_confirmation(
        df_15m: pd.DataFrame,
        po3_bias: str,
        *,
        body_ok_thr: float = 0.35,
        allow_soft_break: bool = True,
    ):
        if df_15m is None or df_15m.empty or len(df_15m) < 5:
            return False, "none"

        c2 = df_15m.iloc[-2]
        c3 = df_15m.iloc[-1]

        def _f(x):
            try:
                return float(x)
            except Exception:
                return 0.0

        def body(c):
            return abs(_f(c["close"]) - _f(c["open"]))

        def rng(c):
            return max(_f(c["high"]) - _f(c["low"]), 1e-9)

        def upper_wick(c):
            return _f(c["high"]) - max(_f(c["open"]), _f(c["close"]))

        def lower_wick(c):
            return min(_f(c["open"]), _f(c["close"])) - _f(c["low"])

        c2_rng = rng(c2)
        c3_rng = rng(c3)
        c2_wick_ratio = (upper_wick(c2) + lower_wick(c2)) / c2_rng

        c3_body = body(c3)
        body_ok = c3_body >= (body_ok_thr * c3_rng)

        c2_high = _f(c2["high"])
        c2_low = _f(c2["low"])
        c2_mid = (c2_high + c2_low) / 2.0

        c3_high = _f(c3["high"])
        c3_low = _f(c3["low"])
        c3_close = _f(c3["close"])

        soft_used = False

        if po3_bias == "bullish":
            hard_break = c3_high > c2_high
            soft_break = allow_soft_break and (c3_close > c2_mid)
            expands = body_ok and (hard_break or soft_break)
            soft_used = bool(soft_break and not hard_break)
        elif po3_bias == "bearish":
            hard_break = c3_low < c2_low
            soft_break = allow_soft_break and (c3_close < c2_mid)
            expands = body_ok and (hard_break or soft_break)
            soft_used = bool(soft_break and not hard_break)
        else:
            return False, "none"

        if not expands:
            return False, "none"

        if c2_wick_ratio <= 0.55:
            base = "reversal_expansion"
        elif c2_wick_ratio >= 0.70:
            base = "continuation_expansion"
        else:
            base = "expansion"

        if soft_used:
            return True, f"{base}_soft"

        return True, base

    # ✅ UPDATED (4) CISD slightly more lenient: allow break by high/low, not only close
    def detect_cisd_protected_swing(
        df_15m: pd.DataFrame,
        po3_bias: str,
        *,
        body_ok_thr: float = 0.35,
        allow_soft_break: bool = True,
    ):
        if df_15m is None or df_15m.empty or len(df_15m) < 10:
            return False, "none"

        c2 = df_15m.iloc[-2]
        c3 = df_15m.iloc[-1]

        def _f(x):
            try:
                return float(x)
            except Exception:
                return 0.0

        def body(c):
            return abs(_f(c["close"]) - _f(c["open"]))

        def rng(c):
            return max(_f(c["high"]) - _f(c["low"]), 1e-9)

        c3_rng = rng(c3)
        c3_body = body(c3)
        body_ok = c3_body >= (body_ok_thr * c3_rng)

        window = df_15m.iloc[-12:-2]
        if window is None or len(window) < 5:
            return False, "none"

        prior_low = float(window["low"].min())
        prior_high = float(window["high"].max())

        c2_low = _f(c2["low"])
        c2_high = _f(c2["high"])

        c3_close = _f(c3["close"])
        c3_high = _f(c3["high"])
        c3_low = _f(c3["low"])

        if po3_bias == "bullish":
            protected = c2_low > prior_low
            hard_break = c3_close > c2_high
            soft_break = allow_soft_break and (c3_high > c2_high)
            breaks = body_ok and (hard_break or soft_break)
            if protected and breaks:
                return True, "cisd_protected_swing_soft" if (soft_break and not hard_break) else "cisd_protected_swing"

        if po3_bias == "bearish":
            protected = c2_high < prior_high
            hard_break = c3_close < c2_low
            soft_break = allow_soft_break and (c3_low < c2_low)
            breaks = body_ok and (hard_break or soft_break)
            if protected and breaks:
                return True, "cisd_protected_swing_soft" if (soft_break and not hard_break) else "cisd_protected_swing"

        return False, "none"

    def price_action_bias(df_tf: pd.DataFrame) -> str:
        if df_tf is None or df_tf.empty or len(df_tf) < 50:
            return "neutral"

        recent = df_tf.tail(24)
        prev = df_tf.iloc[-48:-24]
        if prev is None or len(prev) < 10:
            prev = df_tf.tail(48)

        r_high, r_low = float(recent["high"].max()), float(recent["low"].min())
        p_high, p_low = float(prev["high"].max()), float(prev["low"].min())

        c0 = float(recent["close"].iloc[0])
        c1 = float(recent["close"].iloc[-1])

        up = (r_high > p_high) and (r_low > p_low) and (c1 > c0)
        dn = (r_high < p_high) and (r_low < p_low) and (c1 < c0)

        if up:
            return "bullish"
        if dn:
            return "bearish"
        return "neutral"

    def structure_ok_continuation(df_15m: pd.DataFrame, po3_bias: str) -> bool:
        if df_15m is None or df_15m.empty or len(df_15m) < 60:
            return False

        recent = df_15m.tail(24)
        prev = df_15m.iloc[-48:-24]
        if prev is None or len(prev) < 10:
            prev = df_15m.tail(48)

        recent_high = float(recent["high"].max())
        recent_low = float(recent["low"].min())
        prev_high = float(prev["high"].max())
        prev_low = float(prev["low"].min())

        rng_ = recent_high - recent_low
        last_close = float(df_15m["close"].iloc[-1])
        if last_close <= 0:
            return False

        min_move = last_close * 0.0006
        if rng_ < min_move:
            return False

        if po3_bias == "bullish":
            return (recent_high > prev_high) or (float(recent["close"].iloc[-1]) > float(recent["close"].iloc[0]))
        if po3_bias == "bearish":
            return (recent_low < prev_low) or (float(recent["close"].iloc[-1]) < float(recent["close"].iloc[0]))
        return False

    now_utc = datetime.now(timezone.utc)
    session_label = session_name(now_utc)

    flags = session_valid_flags(session_label)
    session_valid_sniper = flags["session_valid_sniper"]
    session_valid_continuation = flags["session_valid_continuation"]
    session_alignment = session_valid_continuation

    news_block = False
    try:
        news_block = len(get_high_impact_news()) > 0
    except Exception:
        news_block = False

    for sym in symbols:
        try:
            df = fetch_ohlc(sym, interval="15m", period="5d")
            htf_df = fetch_ohlc(sym, interval="4h", period="30d")
        except Exception:
            df = pd.DataFrame()
            htf_df = pd.DataFrame()

        if not isinstance(df, pd.DataFrame):
            df = pd.DataFrame()
        if not isinstance(htf_df, pd.DataFrame):
            htf_df = pd.DataFrame()

        if df.empty or len(df) < 60:
            factors_by_symbol[sym] = {
                "bias": "neutral",
                "po3_bias": "neutral",
                "po3_phase": "ACCUMULATION",
                "po3_active": False,
                "accumulation_detected": False,
                "liquidity_sweep": False,
                "agreement_reclaim": False,
                "mss_shift": False,
                "entry_confirmed": False,
                "entry_confirm_type": "none",
                "entry_confirmed_sniper": False,
                "entry_confirm_type_sniper": "none",
                "entry_confirmed_continuation": False,
                "entry_confirm_type_continuation": "none",
                "cisd_confirmed": False,
                "sniper_clean": False,
                "entry_quality": False,
                "session_alignment": session_alignment,
                "session_valid_sniper": session_valid_sniper,
                "session_valid_continuation": session_valid_continuation,
                "htf_alignment": False,
                "distribution_active": False,
                "session_name": session_label,
                "session_boost": 0.0,
                "structure_ok": False,
                "structure_ok_continuation": False,
                "liquidity_ok": False,
                "certified": False,
                "rr": 0.0,
                "near_fvg": False,
                "fvg_score": 0.0,
                "df": df,
                "news_risk": "against" if news_block else "none",
                "news_block": news_block,
                "volatility_risk": "normal",
                "data_provider": str(df.attrs.get("provider", "none")),
                "used_ticker": str(df.attrs.get("used_ticker", sym)),
                "fetch_error": str(df.attrs.get("fetch_error", "empty_or_insufficient_bars")),
                "entry": "TBD",
                "stop": "TBD",
                "tp1": "TBD",
                "tp2": "TBD",
                "is_priority": sym in TOP_PRIORITY_UNIVERSE,
            }
            continue

        trend_bias = price_action_bias(df)

        accumulation_detected = detect_accumulation(htf_df)
        sweep_above, sweep_below = detect_sweep(df)
        liquidity_sweep = sweep_above or sweep_below

        po3_bias = trend_bias
        if sweep_above:
            po3_bias = "bearish"
        elif sweep_below:
            po3_bias = "bullish"

        mss_bull, mss_bear = detect_mss(df)
        mss_shift = (po3_bias == "bullish" and mss_bull) or (po3_bias == "bearish" and mss_bear)

        agreement_line = current_4h_open(htf_df, float(df["close"].iloc[-1]))
        last_close = float(df["close"].iloc[-1])
        agreement_reclaim = (
            (po3_bias == "bullish" and last_close > agreement_line)
            or (po3_bias == "bearish" and last_close < agreement_line)
        )

        # ---------- Structure (NO EMA) ----------
        recent = df.tail(24)
        rng = float(recent["high"].max() - recent["low"].min())
        structure_ok = rng > (last_close * 0.0005)
        structure_ok_cont = structure_ok_continuation(df, po3_bias)

        # --- PO3 phase (Option B: allow DISTRIBUTION without sweep when trend is running) ---
        if liquidity_sweep and not mss_shift:
            po3_phase = "MANIPULATION"
        elif liquidity_sweep and mss_shift:
            po3_phase = "DISTRIBUTION"
        elif mss_shift and structure_ok_cont:
            po3_phase = "DISTRIBUTION"
        else:
            po3_phase = "ACCUMULATION"

        distribution_active = (po3_phase == "DISTRIBUTION")

        # ---------- Liquidity / Volatility ----------
        last_range = float(df["high"].iloc[-1] - df["low"].iloc[-1])
        avg_range = float((df["high"] - df["low"]).rolling(20).mean().iloc[-1])
        liquidity_ok = bool(avg_range > 0 and last_range > avg_range * 1.05)

        a = atr(df).iloc[-1]
        a = float(a) if pd.notna(a) else 0.0
        entry = float(df["close"].iloc[-1])

        high_thr, extreme_thr = 0.006, 0.010
        if sym in ("XAUUSD", "XAGUSD", "WTI"):
            high_thr, extreme_thr = 0.008, 0.012

        atr_pct = (a / entry) if entry else 0.0
        volatility_risk = "extreme" if atr_pct >= extreme_thr else "high" if atr_pct >= high_thr else "normal"

        # ---------- Stops/Targets ----------
        if po3_bias == "bullish":
            stop = entry - 1.2 * a
            tp1 = entry + 2.0 * (entry - stop)
        elif po3_bias == "bearish":
            stop = entry + 1.2 * a
            tp1 = entry - 2.0 * (stop - entry)
        else:
            stop = tp1 = "TBD"

        rr = (
            round(abs(tp1 - entry) / abs(entry - stop), 2)
            if po3_bias in ("bullish", "bearish") and stop != "TBD"
            else 0.0
        )

        liquidity_targets = compute_liquidity_targets(df)
        if po3_bias == "bullish" and liquidity_targets:
            tp2 = liquidity_targets.get("weekly_high", tp1)
        elif po3_bias == "bearish" and liquidity_targets:
            tp2 = liquidity_targets.get("weekly_low", tp1)
        else:
            tp2 = "TBD"

        fvg_ctx = compute_fvg_context(df, lookback=160, max_show=3)
        near_fvg = bool(fvg_ctx.get("near_fvg", False))
        fvg_score = float(fvg_ctx.get("fvg_score", 0.0))

        # ---------- Entry confirmation ----------
        stacked_narrative = bool(liquidity_sweep and mss_shift and agreement_reclaim)
        body_ok_thr = 0.30 if stacked_narrative else 0.35

        entry_confirmed_base, entry_confirm_type_base = detect_entry_confirmation(
            df,
            po3_bias,
            body_ok_thr=body_ok_thr,
            allow_soft_break=True,
        )

        cisd_confirmed, cisd_type = detect_cisd_protected_swing(
            df,
            po3_bias,
            body_ok_thr=body_ok_thr,
            allow_soft_break=True,
        )

        entry_confirmed_sniper = bool(entry_confirmed_base or cisd_confirmed)
        entry_confirm_type_sniper = cisd_type if cisd_confirmed else entry_confirm_type_base

        entry_confirmed_continuation = bool(entry_confirmed_base)
        entry_confirm_type_continuation = entry_confirm_type_base

        entry_confirmed = entry_confirmed_base
        entry_confirm_type = entry_confirm_type_base
        entry_quality = entry_confirmed

        htf_bias = price_action_bias(htf_df) if isinstance(htf_df, pd.DataFrame) else "neutral"
        htf_alignment = htf_bias in ("neutral", po3_bias)

        po3_active = liquidity_sweep and mss_shift

        sniper_clean = bool(accumulation_detected and liquidity_sweep and mss_shift and agreement_reclaim)

        certified = (
            accumulation_detected
            and liquidity_sweep
            and agreement_reclaim
            and mss_shift
            and entry_confirmed_sniper
            and rr >= 2.0
        )

        factors_by_symbol[sym] = {
            "bias": trend_bias,
            "po3_bias": po3_bias,
            "po3_phase": po3_phase,
            "po3_active": po3_active,
            "accumulation_detected": accumulation_detected,
            "liquidity_sweep": liquidity_sweep,
            "agreement_reclaim": agreement_reclaim,
            "mss_shift": mss_shift,
            "entry_confirmed": entry_confirmed,
            "entry_confirm_type": entry_confirm_type,
            "entry_quality": entry_quality,
            "entry_confirmed_sniper": entry_confirmed_sniper,
            "entry_confirm_type_sniper": entry_confirm_type_sniper,
            "entry_confirmed_continuation": entry_confirmed_continuation,
            "entry_confirm_type_continuation": entry_confirm_type_continuation,
            "cisd_confirmed": bool(cisd_confirmed),
            "sniper_clean": bool(sniper_clean),
            "session_alignment": session_alignment,
            "session_valid_sniper": session_valid_sniper,
            "session_valid_continuation": session_valid_continuation,
            "htf_alignment": htf_alignment,
            "htf_bias": htf_bias,
            "distribution_active": distribution_active,
            "session_name": session_label,
            "session_boost": 0.5 if sym in TOP_PRIORITY_UNIVERSE else 0.3,
            "structure_ok": structure_ok,
            "structure_ok_continuation": structure_ok_cont,
            "liquidity_ok": liquidity_ok,
            "certified": certified,
            "rr": rr,
            "near_fvg": near_fvg,
            "fvg_score": fvg_score,
            "df": df,
            "news_risk": "against" if news_block else "none",
            "news_block": news_block,
            "volatility_risk": volatility_risk,
            "data_provider": str(df.attrs.get("provider", "unknown")),
            "used_ticker": str(df.attrs.get("used_ticker", sym)),
            "entry": _round_price(entry, sym),
            "stop": _round_price(stop, sym),
            "tp1": _round_price(tp1, sym),
            "tp2": _round_price(tp2, sym),
            "agreement_line": _round_price(agreement_line, sym),
            "is_priority": sym in TOP_PRIORITY_UNIVERSE,
        }

    decisions = run_decisions(profiles, factors_by_symbol)

    # inject phase/setup metadata onto each decision for dashboard/alerts
    for d in decisions:
        f = factors_by_symbol.get(d.symbol, {})
        d.meta = dict(getattr(d, "meta", {}) or {})
        d.meta.setdefault("po3_phase", str(f.get("po3_phase", "ACCUMULATION")).upper())
        d.meta.setdefault("data_provider", f.get("data_provider", "unknown"))
        d.meta.setdefault("used_ticker", f.get("used_ticker", d.symbol))

    decisions_by_symbol = {d.symbol: d for d in decisions}
    return profiles, symbols, factors_by_symbol, decisions, decisions_by_symbol


profiles = st.session_state.get("profiles") or get_profiles()
st.session_state.profiles = profiles

decisions = st.session_state.get("decisions", [])
factors_by_symbol = st.session_state.get("factors_by_symbol", {})
decisions_by_symbol = st.session_state.get("decisions_by_symbol", {})

st.title("Trading Assistant")
st.caption("PO3 Sniper-first mode (4H accumulation → manipulation → distribution).")
st.divider()

if "snapshot_ready" not in st.session_state:
    st.session_state.snapshot_ready = False

now = int(time.time())
should_snapshot = (
    LIVE_DATA
    and (not st.session_state.snapshot_ready or (now - st.session_state.last_snapshot_ts) >= SNAPSHOT_INTERVAL_SECS)
)

if should_snapshot:
    with st.spinner("Building PO3 snapshot (live data)..."):
        try:
            profiles, symbols, factors_by_symbol, decisions, decisions_by_symbol = build_snapshot()

            update_portfolio(st.session_state, decisions, factors_by_symbol)

            st.session_state.profiles = profiles
            st.session_state.decisions = decisions
            st.session_state.factors_by_symbol = factors_by_symbol
            st.session_state.decisions_by_symbol = decisions_by_symbol
            st.session_state.snapshot_ready = True
            st.session_state.last_snapshot_ts = now

            maybe_send_trade_alerts(decisions)

        except Exception as e:
            st.session_state.snapshot_ready = False
            fail_soft("Snapshot build failed", e)
elif not LIVE_DATA:
    st.warning("Live data is OFF. Enable it in Safety toggles.")

try:
    render_portfolio_panel(st.session_state)
except Exception as e:
    fail_soft("Portfolio panel failed", e)

try:
    render_top_bar(news_flag="PO3 mode live")
except Exception as e:
    fail_soft("Top bar failed", e)

st.divider()

if not st.session_state.snapshot_ready:
    st.info("Waiting for snapshot / live feed.")
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
            top = sorted(decisions, key=lambda d: d.confidence, reverse=True)
            render_ai_commentary(top[0] if top else None)
