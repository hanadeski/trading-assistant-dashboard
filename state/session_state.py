import time

def init_session_state(st):
    if "selected_symbol" not in st:
        st.selected_symbol = None
    if "last_alert_ts" not in st:
        st.last_alert_ts = {}  # symbol -> timestamp
    if "alert_cooldown_sec" not in st:
        st.alert_cooldown_sec = 15 * 60  # 15 minutes cooldown per symbol
    if "last_action" not in st:
        st.last_action = {}  # symbol -> action string

def can_send_alert(st, symbol: str, action: str) -> bool:
    now = time.time()
    last_ts = st.last_alert_ts.get(symbol, 0)
    last_action = st.last_action.get(symbol)
    # Only send alerts for BUY NOW / SELL NOW
    if action not in ("BUY NOW", "SELL NOW"):
        st.last_action[symbol] = action
        return False
    # Cooldown & only on state change
    if last_action == action and (now - last_ts) < st.alert_cooldown_sec:
        return False
    return True

def mark_alert_sent(st, symbol: str, action: str):
    st.last_alert_ts[symbol] = time.time()
    st.last_action[symbol] = action
