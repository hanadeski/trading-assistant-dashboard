import time
from typing import Dict, List

import streamlit as st
from engine.scoring import decide_from_factors, Decision
from engine.risk import apply_sizing

COOLDOWN_SECS = 60 * 60  # 60 minutes


def _downgrade_to_wait(d: Decision, reason: str) -> Decision:
    """
    Keep trade_plan/meta/sizing, but prevent execution.
    """
    return Decision(
        symbol=d.symbol,
        bias=d.bias,
        mode="standby",
        confidence=d.confidence,
        action="WAIT",
        commentary=reason,
        trade_plan=d.trade_plan,     # keep
        score=d.score,              # keep
        risk_pct=d.risk_pct,         # keep
        size=d.size,                 # keep
        meta=d.meta,                 # keep
    )


def run_decisions(profiles: List, factors_by_symbol: Dict[str, Dict]) -> List[Decision]:
    # Persist cooldown state across Streamlit reruns
    if "_last_fired" not in st.session_state:
        st.session_state["_last_fired"] = {}      # symbol -> "BUY NOW"/"SELL NOW"
    if "_last_fired_ts" not in st.session_state:
        st.session_state["_last_fired_ts"] = {}   # symbol -> unix seconds

    last_fired: Dict[str, str] = st.session_state["_last_fired"]
    last_fired_ts: Dict[str, int] = st.session_state["_last_fired_ts"]

    decisions: List[Decision] = []
    now = int(time.time())

    for p in profiles:
        sym = p.symbol

        factors = factors_by_symbol.get(sym, {})
        d = decide_from_factors(sym, p, factors)
        d = apply_sizing(d, p, factors)

        proposed_action = d.action

        # --- Step 4A execution gate ---
        if proposed_action in ("BUY NOW", "SELL NOW"):
            # 1) Minimum confidence to allow execution (very low guardrail)
            if d.confidence < 7.0:
                d = _downgrade_to_wait(d, "Setup detected but confidence is extremely low (Step 4A).")

            # 2) Cooldown: prevent repeated same-direction firing
            last_action = last_fired.get(sym)
            last_ts = last_fired_ts.get(sym, 0)
            in_window = (now - last_ts) < COOLDOWN_SECS

            if in_window and last_action == proposed_action:
                d = _downgrade_to_wait(d, "Already fired this direction recently (Step 4A cooldown).")

        # record fires (only if we are still firing)
        if d.action in ("BUY NOW", "SELL NOW"):
            last_fired[sym] = d.action
            last_fired_ts[sym] = now

        decisions.append(d)

    return decisions
