import streamlit as st
import plotly.graph_objects as go

from data.live_data import fetch_ohlc


def render_asset_detail(profile, decision):

    if st.button("⬅ Back to dashboard"):
        st.session_state.selected_symbol = None
       st.rerun()

    st.markdown(f"## {profile.display}")
    st.caption(f"Symbol: {profile.symbol} • {profile.asset_class} • Volatility: {profile.volatility}")


    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    c1.metric("Bias", str(decision.bias).capitalize())
    c2.metric("Mode", str(decision.mode).capitalize())
    c3.metric("Confidence", f"{float(decision.confidence):.1f}/10")
    c4.metric("Action", str(decision.action))

    st.divider()

    # Live OHLC
    df = fetch_ohlc(profile.symbol, interval="15m", period="5d")

    if df is None or df.empty or len(df) < 5:
        st.warning("Live chart data unavailable for this symbol right now.")
        if st.button("⬅ Back to dashboard"):
            st.session_state.selected_symbol = None
        return

    # Candlestick chart
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=df.index,
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
            )
        ]
    )
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("### Decision")
    action = str(decision.action)

    if action in ("BUY NOW", "SELL NOW"):
        st.success(action)
    elif action in ("WAIT", "WATCH"):
        st.warning(action)
    else:
        st.info(action)

    # Show optional plan fields if present on the decision object
    plan_cols = st.columns(4)
    for i, key in enumerate(["entry", "stop", "tp1", "tp2"]):
        if hasattr(decision, key):
            plan_cols[i].metric(key.upper(), str(getattr(decision, key)))

    if hasattr(decision, "rr"):
        st.caption(f"RR: {getattr(decision, 'rr')}")

    st.write(getattr(decision, "commentary", ""))
