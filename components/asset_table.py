import streamlit as st
import pandas as pd

from engine.symbols import symbol_meta


def build_table(decisions, profiles):
    rows = []

    if not decisions:
        for p in profiles:
            rows.append(
                {
                    "Symbol": p.symbol,
                    "Bias": "–",
                    "Confidence": "–",
                    "Action": "–",
                }
            )
        return pd.DataFrame(rows)

    for d in decisions:
        rows.append(
            {
                "Symbol": d.symbol,
                "Bias": d.bias.capitalize(),
                "Confidence": f"{d.confidence:.1f}/10",
                "Action": d.action,
            }
        )

    return pd.DataFrame(rows)


def render_asset_table(decisions, profiles):
    if decisions is None:
        decisions = []

    df = build_table(decisions, profiles)

    st.markdown("### Watchlist")

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )

    # Click-to-select in session state (simple)
    if decisions:
        selected = st.selectbox(
            "Select a symbol to view details",
            options=[d.symbol for d in decisions],
            key="asset_select",
        )
        if selected:
            st.session_state.selected_symbol = selected
