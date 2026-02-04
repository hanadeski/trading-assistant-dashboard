from __future__ import annotations

import time
from typing import Dict, List

import streamlit as st

from engine.scoring import decide_from_factors, Decision
from engine.risk import apply_sizing

COOLDOWN_SECS = 60 * 30  # 30 minutes


def _get_bool(x, default=False) -> bool:
    try:
        return bool(x)
    except Exception:
        return default


def _get_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def run_decisions(profiles: List, factors_by_symbol: Dict[str, Dict]) -> List[Decision]:
    """
    Build decisions with:
    - regime gate (no chop / no extreme vol)
    - breakout trigger (WATCH -> BUY/SELL)
    - RR min >= 3 gate
    - sizing tier support (risk_mult/size_tier)
    - cooldown + confidence execution gate (existing behavior)
    """

    # Persist cooldown state across Streamlit reruns
    st.session_state.setdefault("_last_fired", {})      # symbol -> "BUY NOW"/"SELL NOW"
    st.session_state.setdefault("_last_fired_ts", {})   # symbol -> unix seconds

    last_fired: Dict[str, str] = st.session_state["_last_fired"]
    last_fired_ts: Dict[str, int] = st.session_state["_last_fired_ts"]

    decisions: List[Decision] = []
    now = int(time.time())

    for p in profiles:
        sym = p.symbol
        factors = factors_by_symbol.get(sym, {})

        # Base scoring (your existing logic)
        d = decide_from_factors(sym, p, factors)
        d = apply_sizing(d, p, factors)

        # -----------------------------
        # 1) Regime gate (chop/extreme)
        # -----------------------------
        structure_ok = _get_bool(factors.get("structure_ok", False))
        vol_risk = str(factors.get("volatility_risk", "normal")).lower()

        if (not structure_ok) or (vol_risk == "extreme"):
            # Force WAIT in chop/extreme volatility
            d = Decision(
                sym,
                getattr(d, "bias", "neutral"),
                getattr(d, "mode", getattr(p, "mode", "conservative")),
                getattr(d, "confidence", 0.0),
                "WAIT",
                "Regime gate: chop or extreme volatility",
                {},
            )
            decisions.append(d)
            continue

        # -----------------------------
        # 2) RR minimum gate
        # -----------------------------
        rr = _get_float(factors.get("rr", 0.0))
        min_rr = _get_float(factors.get("min_rr", 3.0))
        if rr < min_rr:
            # Don’t allow BUY/SELL if RR is too low
            if getattr(d, "action", "") in ("BUY NOW", "SELL NOW"):
                d = Decision(
                    sym,
                    getattr(d, "bias", "neutral"),
                    getattr(d, "mode", getattr(p, "mode", "conservative")),
                    getattr(d, "confidence", 0.0),
                    "WATCH",
                    f"RR gate: rr={rr:.2f} < min_rr={min_rr:.2f}",
                    {},
                )
            decisions.append(d)
            continue

        # -----------------------------
        # 3) Breakout trigger promotion
        # -----------------------------
        bull_break = _get_bool(factors.get("bull_break", False))
        bear_break = _get_bool(factors.get("bear_break", False))
        bias = str(getattr(d, "bias", factors.get("bias", "neutral"))).lower()

        proposed_action = getattr(d, "action", "WATCH")

        # Only promote to BUY/SELL if breakout matches bias
        if bias == "bullish" and bull_break:
            proposed_action = "BUY NOW"
        elif bias == "bearish" and bear_break:
            proposed_action = "SELL NOW"
        else:
            # No breakout => at most WATCH
            if proposed_action in ("BUY NOW", "SELL NOW"):
                proposed_action = "WATCH"

        # -----------------------------
        # 4) Sizing safety (risk_mult)
        # -----------------------------
        risk_mult = _get_float(factors.get("risk_mult", 1.0))
        if risk_mult <= 0.0 and proposed_action in ("BUY NOW", "SELL NOW"):
            proposed_action = "WAIT"

        # Write back proposed action if changed
        if proposed_action != getattr(d, "action", None):
            d = Decision(
                sym,
                getattr(d, "bias", "neutral"),
                getattr(d, "mode", getattr(p, "mode", "conservative")),
                getattr(d, "confidence", 0.0),
                proposed_action,
                "Breakout/regime/RR rules applied",
                {},
            )

        # -----------------------------
        # 5) Execution gate (confidence)
        # -----------------------------
        if getattr(d, "action", "") in ("BUY NOW", "SELL NOW"):
            if _get_float(getattr(d, "confidence", 0.0)) < 6.0:
                d = Decision(
                    sym,
                    getattr(d, "bias", "neutral"),
                    getattr(d, "mode", getattr(p, "mode", "conservative")),
                    getattr(d, "confidence", 0.0),
                    "WAIT",
                    "Confidence gate: below execution threshold",
                    {},
                )

        # -----------------------------
        # 6) Cooldown (prevent repeat fire)
        # -----------------------------
        if getattr(d, "action", "") in ("BUY NOW", "SELL NOW"):
            last_action = last_fired.get(sym)
            last_ts = last_fired_ts.get(sym, 0)
            in_window = (now - last_ts) < COOLDOWN_SECS

            if in_window and last_action == getattr(d, "action", None):
                d = Decision(
                    sym,
                    getattr(d, "bias", "neutral"),
                    getattr(d, "mode", getattr(p, "mode", "conservative")),
                    getattr(d, "confidence", 0.0),
                    "WAIT",
                    "Cooldown gate: already fired recently",
                    {},
                )
            else:
                last_fired[sym] = getattr(d, "action")
                last_fired_ts[sym] = now

        decisions.append(d)

    return decisions
