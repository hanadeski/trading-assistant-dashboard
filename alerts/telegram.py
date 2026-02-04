import os
import requests

def send_telegram_message(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

def format_trade_alert(decision) -> str:
    header = f"<b>{decision.action}</b> — <b>{decision.symbol}</b>"
    sub = f"Mode: {decision.mode.capitalize()} | Confidence: {decision.confidence:.1f}/10 | Bias: {decision.bias.capitalize()}"
    if decision.trade_plan:
        tp = decision.trade_plan
        body = (
            f"Entry: {tp.get('entry')}\n"
            f"Stop: {tp.get('stop')}\n"
            f"TP1: {tp.get('tp1')}\n"
            f"TP2: {tp.get('tp2')}\n"
            f"RR: {tp.get('rr')}"
        )
    else:
        body = decision.commentary
    return f"{header}\n{sub}\n\n{body}"
import streamlit as st

def send_trade_alert_once(decision) -> bool:
    """
    Prevent duplicate Telegram alerts during Streamlit reruns.
    One alert per symbol + action until app reset.
    """
    key = f"tg_sent_{decision.symbol}_{decision.action}"

    if st.session_state.get(key):
        return False

    message = format_trade_alert(decision)
    ok = send_telegram_message(message)

    if ok:
        st.session_state[key] = True

    return ok
