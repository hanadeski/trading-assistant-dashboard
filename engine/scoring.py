from dataclasses import dataclass
from typing import Dict
from engine.fvg import detect_fvgs

@dataclass
class Decision:
    symbol: str
    bias: str
    mode: str
    confidence: float
    action: str
    commentary: str
    trade_plan: Dict

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def decide_from_factors(symbol: str, profile, factors: Dict) -> Decision:
    bias = factors.get("bias", "neutral")
    session_boost = float(factors.get("session_boost", 0.0))  # 0..1
    liquidity_ok = bool(factors.get("liquidity_ok", False))
    structure_ok = bool(factors.get("structure_ok", False))
    rr = float(factors.get("rr", 0.0))
    certified = bool(factors.get("certified", False))
    volatility_risk = factors.get("volatility_risk", "normal")  # normal/high/extreme
    news_risk = factors.get("news_risk", "none")  # none/near/aligned/against

    # FVG context
    near_fvg = bool(factors.get("near_fvg", False))
    fvg_score = float(factors.get("fvg_score", 0.0))
    fvg_gate = near_fvg and (fvg_score >= 0.6)

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

    score = clamp(score, 0.0, 10.0)

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

    rr_min = profile.rr_min
    rr_min_cert = profile.certified_rr_min

    # ------------------------
    # Hard stand-down conditions
    # ------------------------
    if news_risk == "against" or volatility_risk == "extreme":
        return Decision(symbol, bias, "standby", score, "DO NOTHING", "Stand down: risk environment is unfavourable.", {})

    # ------------------------
    # Decision ladder
    # ------------------------
    if score < 5.0:
        return Decision(symbol, bias, "standby", score, "DO NOTHING", "No edge: choppy or mid-range conditions.", {})

    if 5.0 <= score < 7.0:
        return Decision(symbol, bias, "conservative", score, "WATCH", "Watch: bias exists but confirmation is incomplete.", {})

    if 7.0 <= score < 9.0:
        mode = "balanced"
        if rr >= rr_min and liquidity_ok and structure_ok and bias in ("bullish", "bearish"):
            return Decision(symbol, bias, mode, score, "WAIT", "Good setup forming; wait for a cleaner trigger/entry.", {})
        return Decision(symbol, bias, mode, score, "WAIT", "Balanced conditions, but missing RR/liquidity/structure.", {})

    # score >= 9: near-certified / certified
    mode = "aggressive" if certified else "balanced"
    rr_ok = rr >= rr_min or (certified and rr >= rr_min_cert)

    if rr_ok and liquidity_ok and structure_ok and bias in ("bullish", "bearish"):
        if fvg_gate:
            return Decision(
                symbol,
                bias,
                mode,
                score,
                "WAIT",
                "Setup looks strong, but FVG context is strong—wait for cleaner confirmation/entry.",
                {}
            )

        action = "BUY NOW" if bias == "bullish" else "SELL NOW"
        trade_plan = {
            "entry": factors.get("entry", "TBD"),
            "stop": factors.get("stop", "TBD"),
            "tp1": factors.get("tp1", "TBD"),
            "tp2": factors.get("tp2", "TBD"),
            "rr": rr,
        }
        return Decision(symbol, bias, mode, score, action, "High-confidence setup: conditions align strongly.", trade_plan)

    return Decision(symbol, bias, mode, score, "WAIT", "Near-certified, but missing RR/liquidity/structure to trigger.", {})
