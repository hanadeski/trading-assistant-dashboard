import streamlit as st
import pandas as pd

def render_portfolio_panel(state):
    st.subheader("Portfolio")

    p = state.get("portfolio", {})
    equity = float(p.get("equity", 0.0))
    starting_equity = float(p.get("starting_equity", equity))
    realized_pnl = float(p.get("realized_pnl", 0.0))
    unrealized_pnl = float(p.get("unrealized_pnl", 0.0))
    total_pnl = realized_pnl + unrealized_pnl

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Equity", f"{equity:,.2f}")
    c2.metric("P&L (Total)", f"{total_pnl:,.2f}")
    c3.metric("P&L (Realized)", f"{realized_pnl:,.2f}")
    c4.metric("P&L (Unrealized)", f"{unrealized_pnl:,.2f}")

    # --- Open positions ---
    st.markdown("### Open Positions")
    positions = p.get("open_positions", [])
    if positions:
        rows = []
        for pos in positions:
        rows.append({
        "Symbol": pos.get("symbol"),
        "Side": pos.get("side"),
        "Size": pos.get("size"),
        "Entry": pos.get("entry"),
        "Stop": pos.get("stop"),
        "TP1": pos.get("tp1"),
        "TP2": pos.get("tp2"),
        "Opened": pos.get("opened_at"),
        "Unrealized PnL": pos.get("unrealized_pnl"),
    })

        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.caption("No open positions.")

    # --- Closed trades ---
    st.markdown("### Closed Trades")
    trades = p.get("closed_trades", [])
    if trades:
        st.dataframe(pd.DataFrame(trades).tail(50), use_container_width=True)
    else:
        st.caption("No closed trades yet.")

    # --- Equity curve-ish ---
    st.markdown("### Equity Curve (summary)")
    curve = p.get("equity_curve", [])
    if curve and len(curve) >= 2:
        df_curve = pd.DataFrame(curve)
        # supports either {"t":..., "equity":...} or similar
        equity_col = "equity" if "equity" in df_curve.columns else df_curve.columns[-1]
        st.line_chart(df_curve[equity_col])
    else:
        st.caption("Equity curve will appear after a few updates.")
