import streamlit as st

from engine.profiles import get_profiles
from engine.decision_layer import run_decisions
from data.mock_data import mock_factors_for_symbols
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

# STEP 1: mock data
factors_by_symbol = mock_factors_for_symbols(symbols)
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

render_top_bar(news_flag="Mock (Step 2 will be real)")

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
