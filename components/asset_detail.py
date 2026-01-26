# VERSION_MARKER_20260125_1
import streamlit as st
import plotly.graph_objects as go

from data.live_data import fetch_ohlc

def detect_fvgs(df, lookback=120):
    # Simple 3-candle FVG detection (ICT-style)
    # Bullish FVG: candle i-2 HIGH < candle i LOW
    # Bearish FVG: candle i-2 LOW  > candle i HIGH
    # Returns list of dicts

    d = df.tail(lookback).copy()
    if len(d) < 5:
        return []

    highs = d["high"].values
    lows = d["low"].values
    idx = list(d.index)

    fvgs = []
    for i in range(2, len(d)):
        # Bullish FVG
        if highs[i - 2] < lows[i]:
            fvgs.append(
                {
                    "type": "bull",
                    "top": float(lows[i]),
                    "bottom": float(highs[i - 2]),
                    "start": idx[i - 2],
                    "end": idx[i],
                }
            )

        # Bearish FVG
        if lows[i - 2] > highs[i]:
            fvgs.append(
                {
                    "type": "bear",
                    "top": float(lows[i - 2]),
                    "bottom": float(highs[i]),
                    "start": idx[i - 2],
                    "end": idx[i],
                }
            )

    return fvgs


def pick_recent_fvgs(fvgs, max_show=3):
    """Keep the most recent few FVGs."""
    if not fvgs:
        return []
    return fvgs[-max_show:]


def price_in_zone(price, zone_top, zone_bottom, pad=0.0):
    top = max(zone_top, zone_bottom) + pad
    bottom = min(zone_top, zone_bottom) - pad
    return bottom <= price <= top

def render_asset_detail(profile, decision, factors=None):
    factors = factors or {}

    if st.button("← Back to dashboard"):
        st.session_state.selected_symbol = None
        st.rerun()

    st.markdown(f"## {profile.display}")
    st.caption(
        f"Symbol: {profile.symbol} • {profile.asset_class} • Volatility: {profile.volatility}"
    )

    c1, c2, c3, c4, c5 = st.columns((1, 1, 1, 1, 1))
    c1.metric("Bias", str(decision.bias).capitalize())
    c2.metric("Mode", str(decision.mode).capitalize())
    c3.metric("Confidence", f"{float(decision.confidence):.1f}/10")
    c4.metric("Action", str(decision.action))

    fvg_score = float(factors.get("fvg_score", 0.0))
    near_fvg = bool(factors.get("near_fvg", False))

    c5.metric("FVG Score", f"{fvg_score:.2f}")
    st.caption(f"Near FVG: {'✅ Yes' if near_fvg else '– No'}")
    # Fetch live data early so we can show the data source
    df = fetch_ohlc(profile.symbol, interval="15m", period="5d")
    used_ticker = df.attrs.get("used_ticker")
    if used_ticker:
        st.caption(f"Data source: {used_ticker}")


    
    st.divider()
    

    # --------------------
    # Live data
    # --------------------

    if df is None or df.empty or len(df) < 5:
        st.warning("Live chart data unavailable for this symbol right now.")
        return

    # --------------------
    # Base chart
    # --------------------
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

    # --------------------
    # FVG overlays (recent only)
    # --------------------
    fvgs = pick_recent_fvgs(detect_fvgs(df, lookback=160), max_show=3)
    last_price = float(df["close"].iloc[-1])
    near_fvg = False

    for z in fvgs:
        x0 = z["start"]
        x1 = df.index[-1]
        y0 = min(z["top"], z["bottom"])
        y1 = max(z["top"], z["bottom"])

        fig.add_shape(
            type="rect",
            x0=x0,
            x1=x1,
            y0=y0,
            y1=y1,
            line=dict(width=1),
            fillcolor="rgba(0, 255, 0, 0.10)" if z["type"] == "bull" else "rgba(255, 0, 0, 0.10)",
            layer="below",
        )

        if price_in_zone(last_price, z["top"], z["bottom"], pad=(last_price * 0.0003)):
            near_fvg = True

    if fvgs:
        st.caption(f"FVGs shown: {len(fvgs)} (most recent).")

    # --------------------
    # Render chart
    # --------------------
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)

    if near_fvg:
        st.info(
            "Price is trading near a Fair Value Gap (FVG).\n"
            "Expect reactions and fakeouts — wait for confirmation."
        )

        # --------------------
        # Decision section
    st.markdown("### Decision")
    
    action = str(decision.action)
    if action in ("BUY NOW", "SELL NOW"):
        st.success(action)
    elif action in ("WAIT", "WATCH"):
        st.warning(action)
    else:
        st.info(action)
    
    if hasattr(decision, "commentary"):
        st.write(decision.commentary)
    
    # ------------------------
    # Debug (internal)
    # ------------------------
    with st.expander("Debug: factors", expanded=False):
        st.json(factors)
