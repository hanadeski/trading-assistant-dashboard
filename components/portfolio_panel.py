import streamlit as st
import pandas as pd

def render_portfolio_panel(state):
    st.subheader("Portfolio")
        # one-run guard so reset/close doesn't trap the app in reruns
    if state.get("_did_portfolio_action"):
        state["_did_portfolio_action"] = False
        # --- Actions ---
    p = state.get("portfolio", {})

    colA, colB, colC = st.columns([1, 1, 2])
    with colA:
        reset = st.button("Reset portfolio", type="secondary")
    with colB:
        confirm_close = st.checkbox("Confirm close all", value=False)
        close_all = st.button("Close all positions", disabled=not confirm_close)
    with colC:
        st.caption("Reset clears history. Close-all clears open positions (UI-level).")

    if reset:
        starting = float(p.get("starting_equity", 10000.0)) if isinstance(p, dict) else 10000.0
        state["portfolio"] = {
            "starting_equity": starting,
            "equity": starting,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "open_positions": [],
            "closed_trades": [],
            "equity_curve": [],
        }

        # reset alert counters
        state["portfolio_last_open_count"] = 0
        state["portfolio_last_closed_count"] = 0

    st.success("Portfolio reset.")
    state["_did_portfolio_action"] = True


    if close_all:
        p = state.get("portfolio", {})
        p["open_positions"] = []
        p["unrealized_pnl"] = 0.0
        state["portfolio"] = p
        st.success("All positions cleared.")
        state["_did_portfolio_action"] = True

    # --- Portfolio summary ---
    p = state.get("portfolio", {})
    equity = float(p.get("equity", 0.0))
    starting_equity = float(p.get("starting_equity", 10000.0))
    realized_pnl = float(p.get("realized_pnl", 0.0))
    unrealized_pnl = float(p.get("unrealized_pnl", 0.0))
    total_pnl = realized_pnl + unrealized_pnl


    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Equity", f"{equity:,.2f}", delta=f"{(equity - starting_equity):+,.2f}")
    c2.metric("P&L (Total)", f"{total_pnl:,.2f}", delta=f"{total_pnl:+,.2f}")
    c3.metric("P&L (Realized)", f"{realized_pnl:,.2f}", delta=f"{realized_pnl:+,.2f}")
    c4.metric("P&L (Unrealized)", f"{unrealized_pnl:,.2f}", delta=f"{unrealized_pnl:+,.2f}")

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
    
        df_pos = pd.DataFrame(rows)
    
        if "Symbol" in df_pos.columns:
            df_pos = df_pos.sort_values("Symbol")
    
        for col in ["Size", "Entry", "Stop", "TP1", "TP2", "Unrealized PnL"]:
            if col in df_pos.columns:
                df_pos[col] = pd.to_numeric(df_pos[col], errors="coerce")
    
        st.dataframe(df_pos, use_container_width=True)
    else:
        st.caption("No open positions.")

    # --- Closed trades ---
    st.markdown("### Closed Trades")
    trades = p.get("closed_trades", [])
    if trades:
        df_tr = pd.DataFrame(trades)

        if "closed_at" in df_tr.columns:
            df_tr = df_tr.sort_values("closed_at", ascending=False)

        for col in ["size", "entry", "exit", "pnl"]:
            if col in df_tr.columns:
                df_tr[col] = pd.to_numeric(df_tr[col], errors="coerce")

        st.dataframe(df_tr.head(50), use_container_width=True)
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
