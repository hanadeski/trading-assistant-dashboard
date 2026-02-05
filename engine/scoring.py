from dataclasses import dataclass, field
from typing import Dict
import pandas as pd
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
    regime = factors.get("regime", "trend")  # trend | transition | chop | extreme_vol | no_data
    
    # --- Breakout fields ---
    breakout_up = bool(factors.get("breakout_up", False))
    breakout_dn = bool(factors.get("breakout_dn", False))
    breakout_level_up = factors.get("breakout_level_up", None)
    breakout_level_dn = factors.get("breakout_level_dn", None)
    lookback = int(factors.get("breakout_lookback", 20))


    # ------------------------
    # Hard caps / regime gate
    # ------------------------
    if news_risk == "against":
        return Decision(
            symbol, bias, "standby", 0.0,
            "DO NOTHING",
            "Stand down: news risk is against the setup.",
            {}
        )

    if regime in ("extreme_vol", "no_data"):
        return Decision(
            symbol, bias, "standby", 0.0,
            "WAIT",
            f"Regime = {regime.upper()}. Stand aside until conditions normalize / data returns.",
            {}
        )

    if regime == "chop":
        return Decision(
            symbol, bias, "conservative", 5.0,
            "WATCH",
            "Regime = CHOP. No clean structure—avoid forcing trades; wait for breakout trigger.",
            {}
        )

    if regime == "transition":
        return Decision(
            symbol, bias, "conservative", 6.0,
            "WATCH",
            "Regime = TRANSITION. Structure exists but liquidity is weak—watch only.",
            {}
        )

    # ------------------------
    # FVG context
    # ------------------------
    near_fvg = bool(factors.get("near_fvg", False))
    fvg_score = float(factors.get("fvg_score", 0.0))
    fvg_gate = near_fvg and (fvg_score >= 0.6)

    # ------------------------
    # RR thresholds
    # ------------------------
    rr_min = float(profile.rr_min)
    rr_min_cert = float(profile.certified_rr_min)

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

    if news_risk == "near":
        score -= 0.5

    # Soft de-risk: if FVG score present, reduce score a bit (as you had)
    if fvg_score > 0.0:
        score -= min(0.6, 0.2 + 0.6 * fvg_score)

    score = clamp(score, 0.0, 10.0)

    # ------------------------
    # Confidence calibration
    # ------------------------
    if score < 5.0:
        confidence = score
    elif score < 7.0:
        confidence = score * 0.9
    elif score < 9.0:
        confidence = score * 0.95
    else:
        confidence = min(score, 10.0)

    # Volatility-based confidence cap
    if volatility_risk == "high":
        confidence = min(confidence, 8.5)
    elif volatility_risk == "extreme":
        confidence = min(confidence, 6.0)

    # ------------------------
    # Defaults + commentary
    # ------------------------
    mode = profile.aggression_default
    action = "WAIT"
    commentary = "Conditions developing."
    trade_plan: Dict = {}

    # FVG messaging
    if fvg_score >= 0.6:
        commentary += " Strong FVG context nearby—expect volatility; reduce size and wait for clean confirmation."
    elif fvg_score >= 0.3:
        commentary += " Mild FVG context nearby—expect reaction; be selective on entry."
    if near_fvg:
        commentary += " Price is near a Fair Value Gap (FVG); expect reactions and fakeouts—wait for confirmation."

    # ------------------------
    # Decision ladder
    # ------------------------
    if score < 5.0:
        return Decision(symbol, bias, "standby", confidence, "DO NOTHING",
                        "No edge: choppy or mid-range conditions.", {})

    # --- Breakout promotion ---
    if regime == "trend" and volatility_risk == "normal" and structure_ok and liquidity_ok:
        if rr >= 3.0:
            if bias == "bullish" and breakout_up:
                trade_plan = {
                    "entry": factors.get("entry", "TBD"),
                    "stop": factors.get("stop", "TBD"),
                    "tp1": factors.get("tp1", "TBD"),
                    "tp2": factors.get("tp2", "TBD"),
                    "rr": rr,
                    "breakout": {
                        "dir": "up",
                        "level": breakout_level_up,
                        "lookback": lookback,
                    },
                }
                return Decision(
                    symbol, bias, "balanced", confidence,
                    "BUY NOW",
                    "Breakout confirmed above prior high.",
                    trade_plan
                )
    
            if bias == "bearish" and breakout_dn:
                trade_plan = {
                    "entry": factors.get("entry", "TBD"),
                    "stop": factors.get("stop", "TBD"),
                    "tp1": factors.get("tp1", "TBD"),
                    "tp2": factors.get("tp2", "TBD"),
                    "rr": rr,
                    "breakout": {
                        "dir": "down",
                        "level": breakout_level_dn,
                        "lookback": lookback,
                    },
                }
                return Decision(
                    symbol, bias, "balanced", confidence,
                    "SELL NOW",
                    "Breakout confirmed below prior low.",
                    trade_plan
                )

    
    if score < 7.0:
        return Decision(symbol, bias, "conservative", confidence, "WATCH",
                        "Watch: bias exists but confirmation is incomplete.", {})

    if score < 9.0:
        if rr >= rr_min and structure_ok and bias in ("bullish", "bearish"):
            return Decision(symbol, bias, mode, confidence, "WAIT",
                            "Good setup forming; wait for a cleaner trigger/entry.", {})
        return Decision(symbol, bias, mode, confidence, "WATCH",
                        "Conditions improving, but missing liquidity/structure/RR to progress.", {})

    # High score zone (>= 9)
    mode = "aggressive" if certified else "balanced"

    rr_needed = rr_min_cert if certified else rr_min
    rr_needed = max(3.0, rr_needed)  # absolute minimum RR floor
    rr_ok = rr >= rr_needed

    if not (rr_ok and structure_ok and bias in ("bullish", "bearish")):
        return Decision(symbol, bias, mode, confidence, "WAIT",
                        "High score, but missing RR/structure/bias alignment.", {})

    # If vol is high, don't execute yet
    if volatility_risk == "high":
        return Decision(symbol, bias, mode, confidence, "WAIT",
                        "Volatility is high: wait for cleaner conditions / confirmation.", {})

    if not liquidity_ok:
        return Decision(symbol, bias, mode, confidence, "WAIT",
                        "High score, but liquidity not confirmed; wait for cleaner conditions.", {})

    # Quality gate: require strong FVG context for execution (for now)
    if not fvg_gate:
        return Decision(symbol, bias, mode, confidence, "WAIT",
                        "Setup is strong, but FVG context isn’t strong enough; wait for cleaner confirmation/entry.", {})

    # Final throttle
    if confidence < 9.0:
        return Decision(symbol, bias, mode, confidence, "WAIT",
                        "Setup forming, but confidence below execution threshold.", {})

    action = "BUY NOW" if bias == "bullish" else "SELL NOW"
    trade_plan = {
        "entry": factors.get("entry", "TBD"),
        "stop": factors.get("stop", "TBD"),
        "tp1": factors.get("tp1", "TBD"),
        "tp2": factors.get("tp2", "TBD"),
        "rr": rr,
        "regime": regime,
    }
    return Decision(symbol, bias, mode, confidence, action,
                    "High-confidence setup: conditions align strongly.", trade_plan)

    
    # - Must have structure + RR + directional bias
    # - Liquidity is preferred: without it we won't fire BUY/SELL
    # - FVG is a *quality* gate: without strong FVG, we downgrade BUY/SELL -> WAIT
    if rr_ok and structure_ok and bias in ("bullish", "bearish"):
        if not liquidity_ok:
            return Decision(
                symbol, bias, mode, confidence,
                "WAIT",
                "High score, but liquidity not confirmed; wait for cleaner conditions.",
                {}
            )
    
        # Liquidity OK + RR OK + structure OK => eligible to trade
        # If FVG isn't strong enough, be conservative and WAIT
        if not fvg_gate:
            return Decision(
                symbol, bias, mode, confidence,
                "WAIT",
                "Setup is strong, but FVG context isn’t strong enough; wait for cleaner confirmation/entry.",
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
        return Decision(symbol, bias, mode, confidence, action, "High-confidence setup: conditions align strongly.", trade_plan)
    
    
