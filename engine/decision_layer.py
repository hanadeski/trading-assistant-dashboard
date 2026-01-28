from typing import Dict, List
from engine.scoring import decide_from_factors, Decision

from typing import Dict, List
from engine.scoring import decide_from_factors, Decision

# simple in-memory cooldown store (Streamlit reruns can reset; we'll persist in st.session_state later if needed)
_last_fired: Dict[str, str] = {}   # symbol -> action ("BUY NOW"/"SELL NOW")
_last_fired_ts: Dict[str, int] = {}  # symbol -> unix seconds


def run_decisions(profiles: List, factors_by_symbol: Dict[str, Dict]) -> List[Decision]:
    decisions: List[Decision] = []
    for p in profiles:
        sym = p.symbol
        d = decide_from_factors(sym, p, factors_by_symbol.get(sym, {}))

        # --- Step 4A execution gate ---
        # 1) Minimum confidence to allow execution
        if d.action in ("BUY NOW", "SELL NOW"):
            if d.confidence < 9.2:
                d = Decision(sym, d.bias, d.mode, d.confidence, "WAIT",
                             "Setup strong but below execution threshold (Step 4A).", {})

            # 2) Prevent repeated same-direction firing
            last_action = _last_fired.get(sym)
            if last_action == d.action:
                d = Decision(sym, d.bias, d.mode, d.confidence, "WAIT",
                             "Already fired this direction recently (Step 4A).", {})

        # record fires
        if d.action in ("BUY NOW", "SELL NOW"):
            _last_fired[sym] = d.action

        decisions.append(d)

    return decisions

