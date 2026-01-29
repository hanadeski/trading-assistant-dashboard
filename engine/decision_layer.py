import time
from typing import Dict, List

import streamlit as st
from engine.scoring import decide_from_factors, Decision

COOLDOWN_SECS = 60 * 30  # 30 minutes


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
        d = decide_from_factors(sym, p, factors_by_symbol.get(sym, {}))

        proposed_action = d.action  # capture what we were going to do

        # --- Step 4A execution gate ---
        if proposed_action in ("BUY NOW", "SELL NOW"):
            # 1) Minimum confidence to allow execution
            if d.confidence < 6.0:
                d = Decision(sym, d.bias, d.mode, d.confidence, "WAIT",
                             "Setup strong but below execution threshold (Step 4A).", {})

            # 2) Cooldown: prevent repeated same-direction firing
            last_action = last_fired.get(sym)
            last_ts = last_fired_ts.get(sym, 0)
            in_window = (now - last_ts) < COOLDOWN_SECS

            if in_window and last_action == proposed_action:
                d = Decision(sym, d.bias, d.mode, d.confidence, "WAIT",
                             "Already fired this direction recently (Step 4A).", {})

        # record fires (only if we are still firing)
        if d.action in ("BUY NOW", "SELL NOW"):
            last_fired[sym] = d.action
            last_fired_ts[sym] = now

        decisions.append(d)

    return decisions



