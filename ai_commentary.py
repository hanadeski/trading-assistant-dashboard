import streamlit as st

def render_ai_commentary(decision):
    st.markdown("### Assistant")
    if decision is None:
        st.info("Select an asset to see the decision explanation.")
        return

    st.markdown(f"**{decision.symbol} — {decision.action}**")
    st.write(decision.commentary)

    st.markdown("**Bias / Mode / Confidence**")
    st.write(f"{decision.bias.capitalize()} • {decision.mode.capitalize()} • {decision.confidence:.1f}/10")

    if decision.trade_plan:
        st.markdown("**Trade plan:**")
        tp = decision.trade_plan
        st.write(f"Entry: {tp.get('entry')}")
        st.write(f"Stop: {tp.get('stop')}")
        st.write(f"TP1: {tp.get('tp1')}")
        st.write(f"TP2: {tp.get('tp2')}")
        st.write(f"RR: {tp.get('rr')}")
