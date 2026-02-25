import os
import requests
import time
import streamlit as st
from data.news_calendar import get_high_impact_news

def _get_secret(name: str):
    # Streamlit Cloud secrets first
    try:
        v = st.secrets.get(name)
        if v:
            return str(v)
    except Exception:
        pass
    # Fallback: environment variables (local)
    v = os.getenv(name)
    return v if v else None

def send_telegram_message(text: str) -> bool:
    token = _get_secret("TELEGRAM_BOT_TOKEN")
    chat_id = _get_secret("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}  # plain text

    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

def format_trade_alert(decision) -> str:
    symbol = getattr(decision, "symbol", "UNKNOWN")
    action = getattr(decision, "action", "WAIT")
    conf = float(getattr(decision, "confidence", 0.0))
    bias = getattr(decision, "bias", "neutral").capitalize()

    meta = getattr(decision, "meta", {}) or {}
    setup_type = str(meta.get("setup_type", "NONE")).upper()
    used_ticker = str(meta.get("used_ticker", "")).strip()
    data_provider = str(meta.get("data_provider", "")).strip()

    tp = getattr(decision, "trade_plan", {}) or {}
    entry = tp.get("entry", getattr(decision, "entry", "N/A"))
    stop  = tp.get("stop",  getattr(decision, "stop",  "N/A"))
    tp1   = tp.get("tp1",   getattr(decision, "tp1",   "N/A"))
    tp2   = tp.get("tp2",   getattr(decision, "tp2",   "N/A"))
    rr    = tp.get("rr",    getattr(decision, "rr",    "N/A"))

    reason = getattr(decision, "commentary", "")
    reason_line = f"\n{reason}" if reason else ""

    tp_line = f"TP {tp1}"
    if tp2 not in (None, "", "N/A"):
        tp_line += f" / {tp2}"

    if setup_type == "SNIPER":
        header = "🚨 SNIPER"
    elif setup_type == "CONTINUATION":
        header = "⚡ CONTINUATION"
    else:
        header = "📡 SIGNAL"

    source_line = ""
    if used_ticker:
        source_line = f"\nSource {used_ticker}"
        if data_provider:
            source_line += f" ({data_provider})"

    return (
        f"{header}\n"
        f"{symbol} — {action}"
        f"{source_line}\n"
        f"Entry {entry}\n"
        f"SL {stop}\n"
        f"{tp_line}\n"
        f"RR {rr} | Conf {conf:.1f} | Bias {bias}"
        f"{reason_line}"
    )


ALLOWED_ACTIONS = {"BUY NOW", "SELL NOW"}
MIN_CONFIDENCE = 9.0
MIN_RR = 2.0
COOLDOWN_SEC = 15 * 60  # 15 minutes per symbol

def send_trade_alert_once(decision) -> bool:
    """
    Prevent duplicate Telegram alerts during Streamlit reruns.
    One alert per symbol + action until app reset.
    Plus hard quality gates + per-symbol cooldown.
    """

    # Respect ARM toggle (safety)
    if not st.session_state.get("arm_alerts", True):
        return False
            
    # --- NEWS FILTER ---
    news_events = get_high_impact_news()
    if news_events:
        return False

    symbol = getattr(decision, "symbol", "UNKNOWN")
    action = getattr(decision, "action", "")
    conf = float(getattr(decision, "confidence", 0.0))

    tp = getattr(decision, "trade_plan", {}) or {}
    rr = tp.get("rr", 0) or 0
    try:
        rr_val = float(rr)
    except Exception:
        rr_val = 0.0

    # ---- Hard gates (quality) ----
    if action not in ALLOWED_ACTIONS:
        return False
    if conf < MIN_CONFIDENCE:
        return False
    if rr_val < MIN_RR:
        return False

    # ---- Cooldown (anti-chop spam) ----
    now = time.time()
    cool_key = f"tg_last_{symbol}"
    last_ts = st.session_state.get(cool_key, 0)
    if now - last_ts < COOLDOWN_SEC:
        return False

    key = f"{symbol}:{action}"

    # init cache
    if "sent_alerts" not in st.session_state:
        st.session_state["sent_alerts"] = set()

    # one-time key guard per app lifecycle
    if key in st.session_state["sent_alerts"]:
        return False

    msg = format_trade_alert(decision)
    ok = send_telegram_message(msg)
    if ok:
        st.session_state["sent_alerts"].add(key)
        st.session_state[cool_key] = now
    return ok
