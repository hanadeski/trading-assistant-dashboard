import time
from typing import Dict, List

from engine.scoring import decide_from_factors, Decision

# simple in-memory cooldown store (Streamlit reruns can reset; we'll persist in st.session_state later if needed)
_last_fired: Dict[str, str] = {}      # symbol -> action ("BUY NOW"/"SELL NOW")
_last_fired_ts: Dict[str, int] = {}   # symbol -> unix seconds

COOLDOWN_SECS = 60 * 30  # 30 minutes


def run_decisions(profiles: List, factors_by_symbol: Dict[str, Dict]) -> List[Decision]:
    decisions: List[Decision] = []
    now = int(time.time())

    for p in profiles:
        sym = p.symbol
        d = decide_from_factors(sym, p, factors_by_symbol.get(sym, {}))

        # --- Step 4A execution gate ---
        if d.action in ("BUY NOW", "SELL NOW"):
            # 1) Minimum confidence to allow execution
            if d.confidence < 6.0:
                d = Decision(
                    sym, d.bias, d.mode, d.confidence,
                    "WAIT",
                    "Setup strong but below execution threshold (Step 4A).",
                    {}
                )
            else:
                # 2) Prevent repeated same-direction firing within cooldown window
                last_action = _last_fired.get(sym)
                last_ts = _last_fired_ts.get(sym, 0)

                if last_action == d.action and (now - last_ts) < COOLDOWN_SECS:
                    d = Decision(
                        sym, d.bias, d.mode, d.confidence,
                        "WAIT",
                        f"Already fired this direction recently (Step 4A, cooldown {COOLDOWN_SECS//60}m).",
                        {}
                    )

        # record fires (only if we're still actually firing)
        if d.action in ("BUY NOW", "SELL NOW"):
            _last_fired[sym] = d.action
            _last_fired_ts[sym] = now

        decisions.append(d)
        
        if symbol == "XAUUSD":
            return Decision(symbol, bias, mode, 9.9, "BUY NOW",
                            "TEST: forcing BUY NOW to verify cooldown", {})

    return decisions


