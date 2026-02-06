from dataclasses import dataclass, field
from typing import Dict
import pandas as pd
from engine.fvg import detect_fvgs

SETUP_SCORE_THRESHOLD = 7.0
EXECUTION_SCORE_THRESHOLD = 8.5
EXECUTION_CONFIDENCE_MIN = 8.5

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

    # --- Risk / sizing (Step 5B) ---
    risk_pct: float = 0.0
    size: float = 0.0
    meta: Dict = field(default_factory=dict)

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

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
    execution_score_threshold = float(
        factors.get("execution_score_threshold", EXECUTION_SCORE_THRESHOLD)
    )
    execution_confidence_min = float(
        factors.get("execution_confidence_min", EXECUTION_CONFIDENCE_MIN)
    )

    # Hard caps: never trade in hostile regimes
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
        # --- RR thresholds ---
    rr_min = profile.rr_min
    rr_min_cert = profile.certified_rr_min

    # ------------------------
    # Base scoring
    # ------------------------
    score = 0.0
    if bias in ("bullish", "bearish"):
        score += 2.0
    if structure_ok:
        score += 2.0
    if liquidity_ok:
        score += 2.0
    score += 2.0 * session_boost
    score += clamp((rr - 1.0), 0.0, 2.0)

    # ------------------------
    # Risk penalties
    # ------------------------
    if volatility_risk == "high":
        score -= 0.5
    elif volatility_risk == "extreme":
        score -= 1.5

    if news_risk == "against":
        score -= 2.0
    elif news_risk == "near":
        score -= 0.5

    # --- soft de-risk adjustment (4.5A) ---
    if fvg_score > 0.0:
        score -= min(0.6, 0.2 + 0.6 * fvg_score)

    if htf_bias not in ("neutral", bias):
        score -= 1.0

    if regime == "range":
        score -= 0.8

    score = clamp(score, 0.0, 10.0)
    # --- Confidence calibration ---
    # Ensure scores map cleanly to decision strength
    if score < 5.0:
        confidence = score
    elif score < 7.0:
        confidence = score * 0.9
    elif score < 9.0:
        confidence = score * 0.95
    else:
        confidence = min(score, 10.0)
    # ------------------------
    # Decision defaults
    # ------------------------
    mode = profile.aggression_default
    action = "WAIT"
    commentary = "Conditions developing."
    trade_plan: Dict = {}

    # --- FVG messaging (4.5B) ---
    if fvg_score >= 0.6:
        commentary += " Strong FVG context nearby—expect volatility; reduce size and wait for clean confirmation."
    elif fvg_score >= 0.3:
        commentary += " Mild FVG context nearby—expect reaction; be selective on entry."

    if near_fvg:
        commentary += " Price is near a Fair Value Gap (FVG); expect reactions and fakeouts—wait for confirmation."

    if htf_bias not in ("neutral", bias):
        commentary += " Higher-timeframe bias conflicts; wait for alignment."
    if regime == "range":
        commentary += " Range-bound regime detected; demand cleaner trend confirmation."

    # ------------------------
    
    # ------------------------
    # Decision ladder
    # ------------------------
    if score < 5.0:
        return Decision(
            symbol, bias, "standby", confidence, "DO NOTHING",
            "No edge: choppy or mid-range conditions.", {},
            score=score
        )
    
        # -----------------------------
    # Decision ladder (Balanced)
    # -----------------------------
    
    # 1) Low score = WATCH
    if score < setup_score_threshold:
        return Decision(
            symbol, bias, "conservative", confidence,
            "WATCH",
            "Watch: bias exists but confirmation is incomplete.",
            {},
            score=score
        )
    
    # 2) Mid score = WAIT (only if structure + RR are decent), else WATCH
    if score < execution_score_threshold:
        # Balanced: require RR + structure for "WAIT"
        if rr >= rr_min and structure_ok and bias in ("bullish", "bearish"):
            return Decision(
                symbol, bias, mode, confidence,
                "WAIT",
                "Good setup forming; wait for a cleaner trigger/entry.",
                {},
                score=score
            )
    
        return Decision(
            symbol, bias, mode, confidence,
            "WATCH",
            "Conditions improving, but missing liquidity/structure/RR to progress.",
            {},
            score=score
        )
    
    
    # 3) High score zone: decide whether we can trigger
    mode = "aggressive" if certified else "balanced"
    
    # RR gate: use rr_min_cert only if certified, else rr_min
    rr_needed = rr_min_cert if certified else rr_min
    rr_ok = rr >= rr_needed
    
    # Balanced trigger:
    # Must have structure + RR + direction + liquidity to trade
    # If volatility is high, we cap at WAIT (even if everything else is good)
    if rr_ok and structure_ok and bias in ("bullish", "bearish"):
    
        if not liquidity_ok and confidence < (execution_confidence_min + 0.5):
            return Decision(
                symbol, bias, mode, confidence,
                "WAIT",
                "High score, but liquidity not confirmed; wait for cleaner conditions.",
                {},
                score=score
            )
    
        # FVG is a quality gate: without strong FVG we downgrade BUY/SELL -> WAIT
        if not fvg_gate and confidence < (execution_confidence_min + 0.5):
            return Decision(
                symbol, bias, mode, confidence,
                "WAIT",
                "Setup is strong, but FVG context isn’t strong enough; wait for cleaner confirmation/entry.",
                {},
                score=score
            )
            # --- Final risk throttle ---
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
            "High-confidence setup: conditions align strongly.", trade_plan,
            score=score
        )
    
    # - Must have structure + RR + directional bias
    # - Liquidity is preferred: without it we won't fire BUY/SELL
    # - FVG is a *quality* gate: without strong FVG, we downgrade BUY/SELL -> WAIT
    if rr_ok and structure_ok and bias in ("bullish", "bearish"):
        if not liquidity_ok and confidence < (execution_confidence_min + 0.5):
            return Decision(
                symbol, bias, mode, confidence,
                "WAIT",
                "High score, but liquidity not confirmed; wait for cleaner conditions.",
                {},
                score=score
            )
    
        # Liquidity OK + RR OK + structure OK => eligible to trade
        # If FVG isn't strong enough, be conservative and WAIT
        if not fvg_gate and confidence < (execution_confidence_min + 0.5):
            return Decision(
                symbol, bias, mode, confidence,
                "WAIT",
                "Setup is strong, but FVG context isn’t strong enough; wait for cleaner confirmation/entry.",
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
            "High-confidence setup: conditions align strongly.", trade_plan,
            score=score
        )
