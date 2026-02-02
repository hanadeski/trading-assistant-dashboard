import streamlit as st
import pandas as pd

def emoji_bias(bias: str) -> str:
    return {"bullish":"🟢 Bullish", "bearish":"🔴 Bearish", "neutral":"⚪ Neutral"}.get(bias, "⚪ Neutral")

def style_action(action: str) -> str:
    if action == "BUY NOW":
        return "🟢 BUY NOW"
    if action == "SELL NOW":
        return "🔴 SELL NOW"
    if action == "WATCH":
        return "🟡 WATCH"
    if action == "WAIT":
        return "🟠 WAIT"
    return "⚫ DO NOTHING"

def render_asset_table(decisions, profiles):
    st.markdown("## Watchlist")
    st.caption("Click a symbol button to open details. Telegram alerts only fire on high-confidence BUY/SELL.")

    # Hard guard: if decisions is empty, don't build a df that can crash UI
    if not decisions:
        st.info("Waiting for live data / decisions…")
        return

    prof_map = {p.symbol: p for p in profiles}

    rows = []
    for d in decisions:
        p = prof_map.get(d.symbol)
        rows.append({
            "Asset": (p.display if p else d.symbol),
            "Symbol": d.symbol,
            "Bias": emoji_bias(d.bias),
            "Mode": (d.mode.capitalize() if getattr(d, "mode", None) else ""),
            "Confidence": f"{float(getattr(d, 'confidence', 0.0)):.1f}/10",
            "Action": style_action(getattr(d, "action", "")),
        })

    # Force a stable schema no matter what
    df = pd.DataFrame(rows, columns=["Asset", "Symbol", "Bias", "Mode", "Confidence", "Action"])

    # Quick symbol buttons (safe even if df is weird)
    symbols = df.get("Symbol", pd.Series(dtype=str)).dropna().astype(str).tolist()

    cols = st.columns(6)
    for i, sym in enumerate(symbols[:18]):
        with cols[i % 6]:
            if st.button(sym, use_container_width=True):
                st.session_state.selected_symbol = sym

    # Table view
    st.dataframe(df.drop(columns=["Symbol"]), use_container_width=True, hide_index=True)

    st.markdown("## Watchlist")
    st.caption("Click a symbol button to open details. Telegram alerts only fire on high-confidence BUY/SELL.")

    # Quick symbol buttons (safe even if Symbol missing)
    symbols = df.get("Symbol", pd.Series(dtype=str)).tolist()
    cols = st.columns(6)
    for i, sym in enumerate(symbols[:18]):
        with cols[i % 6]:
            if st.button(sym, use_container_width=True):
                st.session_state.selected_symbol = sym

    st.dataframe(
        df.drop(columns=["Symbol"]),
        use_container_width=True,
        hide_index=True
    )
