import streamlit as st
from datetime import datetime, timezone

def session_name(now_utc: datetime) -> str:
    hour = now_utc.hour
    if 12 <= hour < 16:
        return "London + NY Overlap"
    if 7 <= hour < 12:
        return "London"
    if 12 <= hour < 21:
        return "New York"
    return "Asia / Off-hours"

def render_top_bar(news_flag: str = "None"):
    now = datetime.now(timezone.utc)
    session = session_name(now)
    col1, col2, col3 = st.columns([1.2, 1.2, 1.6])
    with col1:
        st.markdown(f"**🕒 {now.strftime('%H:%M')} UTC**")
    with col2:
        st.markdown(f"**🟢 {session}**")
    with col3:
        st.markdown(f"**⚠️ News risk:** {news_flag}")
    st.divider()
