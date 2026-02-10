import time
from typing import Dict, List

import streamlit as st
from engine.scoring import decide_from_factors, Decision
from engine.risk import apply_sizing

# Premium cooldown: 60 minutes, absolute block
COOLDOWN_SECS = 60 * 60


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

        # ---------------------------------------------------------
        # PREMIUM MODE: STRICT COOLDOWN (NO FLIPS, NO SPAM)
        # ---------------------------------------------------------
        if proposed_action in ("BUY NOW", "SELL NOW"):
            last_action = last_fired.get(sym)
            last_ts = last_fired_ts.get(sym, 0)
            in_window = (now - last_ts) < COOLDOWN_SECS

            if in_window:
                # Block ALL signals inside cooldown (same or opposite direction)
                d = Decision(
                    sym, d.bias, d.mode, d.confidence,
                    "WAIT",
                    "Cooldown active — premium mode blocks all signals during cooldown.",
                    {},
                    score=d.score
                )

        # ---------------------------------------------------------
        # RECORD EXECUTIONS (ONLY IF STILL FIRING)
        # ---------------------------------------------------------
        if d.action in ("BUY NOW", "SELL NOW"):
            last_fired[sym] = d.action
            last_fired_ts[sym] = now

        decisions.append(d)

    return decisions
