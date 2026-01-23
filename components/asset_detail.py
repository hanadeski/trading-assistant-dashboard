import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np

from data.live_data import fetch_ohlc


def render_asset_detail(profile, decision):
    st.markdown(f"## {profile.display}")
    st.caption(f"Symbol: {profile.symbol} • {profile.asset_class} • Volatility: {profile.volatility}")

    c1, c2, c3, c4 = st.columns([1,1,1,1])
    c1.metric("Bias", decision.bias.capitalize())
    c2.metric("Mode", decision.mode.capitalize())
    c3.metric("Confidence", f"{decision.confidence:.1f}/10")
    c4.metric("Action", decision.action)

    st.divider()

    df = fetch_ohlc(profile.symbol, interval="15m", period="5d")

if df is None or df.empty or len(df) < 5:
    st.warning("Live chart data unavailable for this symbol right now.")
    if st.button("⬅ Back to dashboard"):
        st.session_state.selected_symbol = None
    return

    fig = go.Figure(data=[go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"]
    )])
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("### Decision")
    if decision.action in ("BUY NOW", "SELL NOW"):
        st.success(decision.action)
    elif decision.action in ("WAIT", "WATCH"):
        st.warning(decision.action)
    else:
        st.info(decision.action)

    st.write(decision.commentary)

    if decision.trade_plan:
        st.markdown("### Trade plan")
        tp = decision.trade_plan
        st.write(f"**Entry:** {tp.get('entry')}")
        st.write(f"**Stop:** {tp.get('stop')}")
        st.write(f"**TP1:** {tp.get('tp1')}")
        st.write(f"**TP2:** {tp.get('tp2')}")
        st.write(f"**RR:** {tp.get('rr')}")

    st.divider()
    if st.button("⬅ Back to dashboard"):
        st.session_state.selected_symbol = None
