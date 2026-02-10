from dataclasses import dataclass, field
from typing import Dict
import pandas as pd
from engine.fvg import detect_fvgs

# --- Global thresholds (cleaned) ---
SETUP_SCORE_THRESHOLD = 6.6
EXECUTION_SCORE_THRESHOLD = 7.8
EXECUTION_CONFIDENCE_MIN = 7.5   # final intended value

@dataclass
class Decision:
    symbol: str
    bias: str
    mode: str
    confidence: float
    action: str
    commentary: str
    trade_plan: Dict
    score: float = 0.0

    # Risk / sizing
    risk_pct: float = 0.0
    size: float = 0.0
    meta: Dict = field(default_factory=dict)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# =========================================================
# SCORE BREAKDOWN
# =========================================================
def build_score_breakdown(profile, factors: Dict) -> Dict[str, float]:
    bias = factors.get("bias", "neutral")
    session_boost = float(factors.get("session_boost", 0.0))
    liquidity_ok = bool(factors.get("liquidity_ok", False))
    structure_ok = bool(factors.get("structure_ok", False))
    rr = float(factors.get("rr", 0.0))
    volatility_risk = factors.get("volatility_risk", "normal")
    news_risk = factors.get("news_risk", "none")
    htf_bias = factors.get("htf_bias", "neutral")
    regime = factors.get("regime", "range")
    fvg_score = float(factors.get("fvg_score", 0.0))

    # Base components
    bias_score = 2.5 if bias in ("bullish", "bearish") else 0.0
    structure_score = 2.5 if structure_ok else 0.0
    liquidity_score = 2.5 if liquidity_ok else 0.0
    session_score = 2.0 * session_boost
    rr_score = clamp(rr - 1.0, 0.0, 3.0)

    # Volatility penalty (cleaned)
    if volatility_risk == "high":
        volatility_penalty = -0.2
    elif volatility_risk == "extreme":
        volatility_penalty = -0.6
    else:
        volatility_penalty = 0.0

    # News penalty
    if news_risk == "against":
        news_penalty = -2.0
    elif news_risk == "near":
        news_penalty = -0.5
    else:
        news_penalty = 0.0

    # FVG penalty
    fvg_penalty = 0.0
    if fvg_score > 0.0:
        fvg_penalty = -min(0.6, 0.2 + 0.6 * fvg_score)

    # HTF conflict
    htf_penalty = -1.0 if htf_bias not in ("neutral", bias) else 0.0

    # Regime penalty (cleaned)
    regime_penalty = 0.0 if regime != "range" else -0.5

    # Total score
    score = clamp(
        bias_score
        + structure_score
        + liquidity_score
        + session_score
        + rr_score
        + volatility_penalty
        + news_penalty
        + fvg_penalty
        + htf_penalty
        + regime_penalty,
        0.0,
        10.0,
    )

    return {
        "bias_score": bias_score,
        "structure_score": structure_score,
        "liquidity_score": liquidity_score,
        "session_score": session_score,
        "rr_score": rr_score,
        "volatility_penalty": volatility_penalty,
        "news_penalty": news_penalty,
        "fvg_penalty": fvg_penalty,
        "htf_penalty": htf_penalty,
        "regime_penalty": regime_penalty,
        "total_score": score,
    }


# =========================================================
# DECISION ENGINE
# =========================================================
def decide_from_factors(symbol: str, profile, factors: Dict) -> Decision:
    bias = factors.get("bias", "neutral")
    session_boost = float(factors.get("session_boost", 0.0))
    liquidity_ok = bool(factors.get("liquidity_ok", False))
    structure_ok = bool(factors.get("structure_ok", False))
    rr = float(factors.get("rr", 0.0))
    certified = bool(factors.get("certified", False))
    volatility_risk = factors.get("volatility_risk", "normal")
    news_risk = factors.get("news_risk", "none")
    htf_bias = factors.get("htf_bias", "neutral")
    regime = factors.get("regime", "range")

    setup_score_threshold = float(factors.get("setup_score_threshold", SETUP_SCORE_THRESHOLD))
    execution_score_threshold = float(factors.get("execution_score_threshold", EXECUTION_SCORE_THRESHOLD))
    execution_confidence_min = float(factors.get("execution_confidence_min", EXECUTION_CONFIDENCE_MIN))

    # Hard stop: hostile news regime
    if news_risk == "against":
        return Decision(
            symbol, bias, "standby", 0.0,
            "DO NOTHING",
            "Stand down: risk regime is hostile.",
            {},
            score=0.0
        )

    # FVG context
    near_fvg = bool(factors.get("near_fvg", False))
    fvg_score = float(factors.get("fvg_score", 0.0))
    fvg_gate = near_fvg and (fvg_score >= 0.6)

    # RR requirements (cleaned)
    rr_min = profile.rr_min
    rr_min_cert = profile.certified_rr_min
    rr_required = 2.5  # final intended override

    # Base scoring
    score_breakdown = build_score_breakdown(profile, factors)
    score = score_breakdown["total_score"]

    # Confidence calibration
    if score < 5.0:
        confidence = score
    elif score < 7.0:
        confidence = score * 0.9
    elif score < 9.0:
        confidence = score * 0.95
    else:
        confidence = min(score, 10.0)

    # Defaults
    mode = profile.aggression_default
    action = "WAIT"
    commentary = "Conditions developing."
    trade_plan = {}

    # FVG messaging
    if fvg_score >= 0.6:
        commentary += " Strong FVG context detected."
    elif fvg_score >= 0.3:
        commentary += " Mild FVG context nearby—expect reaction; be selective."

    if near_fvg:
        commentary += " Price is near an FVG; expect reactions and fakeouts."

    if htf_bias not in ("neutral", bias):
        commentary += " Higher-timeframe conflict present."

    if regime == "range":
        commentary += " Range-bound regime; demand cleaner trend confirmation."

    # -----------------------------------------------------
    # Decision ladder
    # -----------------------------------------------------

    # 1) Very weak score
    if score < 5.0:
        return Decision(
            symbol, bias, "standby", confidence, "DO NOTHING",
            "No edge: choppy or mid-range conditions.",
            {},
            score=score
        )

    # 2) Core rules (strict)
    core_rules_ok = (
        bias in ("bullish", "bearish")
        and structure_ok
        and liquidity_ok
        and certified
        and fvg_gate
        and rr >= rr_required
        and confidence >= 8.0
        and news_risk != "against"
    )

    if core_rules_ok:
        action = "BUY NOW" if bias == "bullish" else "SELL NOW"
        trade_plan = {
            "entry": factors.get("entry", "TBD"),
            "stop": factors.get("stop", "TBD"),
            "tp1": factors.get("tp1", "TBD"),
            "tp2": factors.get("tp2", "TBD"),
            "rr": rr,
        }
        return Decision(
            symbol, bias, mode, confidence, action,
            "Core setup aligned (bias/structure/liquidity/RR).",
            trade_plan,
            score=score,
        )

    # 3) High score zone
    mode = "aggressive" if certified else "balanced"
    rr_needed = rr_min_cert if certified else rr_min
    rr_ok = rr >= rr_needed

    if rr_ok and structure_ok and bias in ("bullish", "bearish"):

        if not liquidity_ok:
            return Decision(
                symbol, bias, mode, confidence,
                "WAIT",
                "High score, but liquidity not confirmed.",
                {},
                score=score
            )

        if not fvg_gate:
            return Decision(
                symbol, bias, mode, confidence,
                "WAIT",
                "Setup strong, but FVG context isn’t strong enough.",
                {},
                score=score
            )

        if confidence < execution_confidence_min:
            return Decision(
                symbol, bias, mode, confidence,
                "WAIT",
                "Setup forming, but confidence below execution threshold.",
                {},
                score=score
            )

        action = "BUY NOW" if bias == "bullish" else "SELL NOW"
        trade_plan = {
            "entry": factors.get("entry", "TBD"),
            "stop": factors.get("stop", "TBD"),
            "tp1": factors.get("tp1", "TBD"),
            "tp2": factors.get("tp2", "TBD"),
            "rr": rr,
        }
        return Decision(
            symbol, bias, mode, confidence, action,
            "High-confidence setup: conditions align strongly.",
            trade_plan,
            score=score
        )

    # 4) Default fallback
    return Decision(
        symbol, bias, mode, confidence,
        "WAIT",
        "No actionable setup yet; wait for confirmation.",
        {},
        score=score
    )
