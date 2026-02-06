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

def _color_bias(val: str) -> str:
    text = str(val).lower()
    if "bullish" in text:
        return "color: #2dd4bf; font-weight: 600;"
    if "bearish" in text:
        return "color: #fb7185; font-weight: 600;"
    return "color: #9aa4ad;"

def _color_action(val: str) -> str:
    text = str(val).upper()
    if "BUY NOW" in text:
        return "color: #22c55e; font-weight: 700;"
    if "SELL NOW" in text:
        return "color: #ef4444; font-weight: 700;"
    if "WATCH" in text:
        return "color: #fbbf24; font-weight: 700;"
    if "WAIT" in text:
        return "color: #f59e0b; font-weight: 700;"
    return "color: #9aa4ad;"

def render_asset_table(decisions, profiles):
    st.markdown("## Watchlist")
    st.caption("Click a symbol button to open details. Telegram alerts only fire on high-confidence BUY/SELL.")

    prof_map = {p.symbol: p for p in profiles}

    rows = []
    if not decisions:
        st.info("Waiting for live data / decisions…")
        for profile in profiles:
            rows.append({
                "Asset": profile.display,
                "Symbol": profile.symbol,
                "Bias": emoji_bias("neutral"),
                "Mode": "",
                "Confidence": "—",
                "Action": style_action("WAIT"),
            })
    else:
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
                st.session_state["selected_from_click"] = True

    # Table view
    display_df = df.drop(columns=["Symbol"])
    styled = display_df.style.applymap(_color_bias, subset=["Bias"]).applymap(
        _color_action, subset=["Action"]
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)
