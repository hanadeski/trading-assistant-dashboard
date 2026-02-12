from dataclasses import dataclass, field
from typing import Dict


# ✅ Continuation threshold (Step 3 execution) can be 6.5
SETUP_SCORE_THRESHOLD = 6.5

# ✅ Sniper stays stricter
EXECUTION_CONFIDENCE_MIN = 8.0

MIN_RR = 2.0


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


def build_score_breakdown(profile, factors: Dict) -> Dict[str, float]:
    """
    PO3 confidence model (max 10):
      PO3 active +2
      Liquidity sweep +2
      Agreement reclaim +1
      MSS shift +2
      Entry confirmation +1     (uses setup-correct confirm if available)
      Session alignment +1
      HTF bias +1

    ✅ B2: If distribution_active True, add +0.5 bonus (continuation-friendly)

    NOTE:
      Sniper clean +1.0 is NOT added here — it's applied only at sniper execution check.
    """
    po3_active = bool(factors.get("po3_active", False))
    liquidity_sweep = bool(factors.get("liquidity_sweep", False))
    agreement_reclaim = bool(factors.get("agreement_reclaim", False))
    mss_shift = bool(factors.get("mss_shift", False))
    session_alignment = bool(factors.get("session_alignment", False))
    htf_alignment = bool(factors.get("htf_alignment", False))
    distribution_active = bool(factors.get("distribution_active", False))

    # Prefer setup-specific confirmations if present, else generic
    entry_confirmed_any = bool(
        factors.get("entry_confirmed_sniper", False)
        or factors.get("entry_confirmed_continuation", False)
        or factors.get("entry_confirmed", False)
    )

    po3_score = 2.0 if po3_active else 0.0
    sweep_score = 2.0 if liquidity_sweep else 0.0
    agreement_score = 1.0 if agreement_reclaim else 0.0
    mss_score = 2.0 if mss_shift else 0.0
    entry_score = 1.0 if entry_confirmed_any else 0.0
    session_score = 1.0 if session_alignment else 0.0
    htf_score = 1.0 if htf_alignment else 0.0

    distribution_bonus = 0.5 if distribution_active else 0.0

    total_score = (
        po3_score
        + sweep_score
        + agreement_score
        + mss_score
        + entry_score
        + session_score
        + htf_score
        + distribution_bonus
    )
    total_score = clamp(total_score, 0.0, 10.0)

    return {
        "po3_score": po3_score,
        "sweep_score": sweep_score,
        "agreement_score": agreement_score,
        "mss_score": mss_score,
        "entry_score": entry_score,
        "session_score": session_score,
        "htf_score": htf_score,
        "distribution_bonus": distribution_bonus,
        "bias_score": 0.0,
        "structure_score": 0.0,
        "liquidity_score": 0.0,
        "rr_score": 0.0,
        "volatility_penalty": 0.0,
        "news_penalty": 0.0,
        "htf_penalty": 0.0,
        "regime_penalty": 0.0,
        "total_score": total_score,
    }


def decide_from_factors(symbol: str, profile, factors: Dict) -> Decision:
    po3_bias = factors.get("po3_bias", factors.get("bias", "neutral"))
    rr = float(factors.get("rr", 0.0))

    # News = warning only, never blocks
    news_block = bool(factors.get("news_block", False))
    news_note = " ⚠️ News risk: high-impact events nearby." if news_block else ""

    # Session flags
    session_valid_sniper = bool(factors.get("session_valid_sniper", True))
    session_valid_continuation = bool(factors.get("session_valid_continuation", True))

    po3_phase = str(factors.get("po3_phase", "ACCUMULATION")).upper()
    accumulation_detected = bool(factors.get("accumulation_detected", False))
    liquidity_sweep = bool(factors.get("liquidity_sweep", False))
    agreement_reclaim = bool(factors.get("agreement_reclaim", False))
    mss_shift = bool(factors.get("mss_shift", False))

    htf_alignment = bool(factors.get("htf_alignment", False))
    distribution_active = bool(factors.get("distribution_active", False))

    # Continuation structure (fallback to old structure_ok if missing)
    structure_ok = bool(factors.get("structure_ok", False))
    structure_ok_cont = bool(factors.get("structure_ok_continuation", structure_ok))

    # Setup-correct entry confirmation
    entry_confirmed_sniper = bool(
        factors.get("entry_confirmed_sniper", factors.get("entry_confirmed", False))
    )
    entry_type_sniper = str(
        factors.get("entry_confirm_type_sniper", factors.get("entry_confirm_type", "none"))
    )

    entry_confirmed_cont = bool(
        factors.get("entry_confirmed_continuation", factors.get("entry_confirmed", False))
    )
    entry_type_cont = str(
        factors.get("entry_confirm_type_continuation", factors.get("entry_confirm_type", "none"))
    )

    # Base score (includes distribution bonus)
    score_breakdown = build_score_breakdown(profile, factors)
    confidence = float(score_breakdown["total_score"])

    # ✅ Sniper-only clean bonus (+1.0) applied ONLY for sniper execution checks
    sniper_clean = bool(factors.get("sniper_clean", False))
    sniper_bonus = 1.0 if sniper_clean else 0.0
    sniper_confidence = clamp(confidence + sniper_bonus, 0.0, 10.0)

    base_meta = {
        "po3_phase": po3_phase,
        "setup_type": "NONE",  # overwritten on execution returns below
        "model": "PO3_SNIPER_FIRST",
        "news_flag": bool(news_block),
        "htf_alignment": bool(htf_alignment),
        "structure_ok_continuation": bool(structure_ok_cont),
        "entry_confirm_type_sniper": entry_type_sniper,
        "entry_confirm_type_continuation": entry_type_cont,
        "cisd_confirmed": bool(factors.get("cisd_confirmed", False)),
        "distribution_bonus": float(score_breakdown.get("distribution_bonus", 0.0)),
        # debug visibility:
        "sniper_clean": bool(sniper_clean),
        "sniper_bonus": float(sniper_bonus),
        "sniper_confidence": float(sniper_confidence),
    }

    if rr < MIN_RR:
        return Decision(
            symbol=symbol,
            bias=po3_bias,
            mode="standby",
            confidence=confidence,
            action="WAIT",
            commentary="RR below minimum 2.0 requirement." + news_note,
            trade_plan={},
            score=confidence,
            meta=base_meta,
        )

    # -----------------------
    # SNIPER
    # -----------------------
    sniper_phase_ok = po3_phase in ("ACCUMULATION", "MANIPULATION")

    sniper_ready = (
        sniper_phase_ok
        and accumulation_detected
        and liquidity_sweep
        and agreement_reclaim
        and mss_shift
        and entry_confirmed_sniper
        and session_valid_sniper
        and rr >= MIN_RR
        and po3_bias in ("bullish", "bearish")
    )

    # ✅ Use sniper_confidence ONLY here
    if sniper_ready and sniper_confidence >= EXECUTION_CONFIDENCE_MIN:
        action = "BUY NOW" if po3_bias == "bullish" else "SELL NOW"
        trade_plan = {
            "entry": factors.get("entry", "TBD"),
            "stop": factors.get("stop", "TBD"),
            "tp1": factors.get("tp1", "TBD"),
            "tp2": factors.get("tp2", "TBD"),
            "rr": rr,
        }
        meta = {**base_meta, "setup_type": "SNIPER", "entry_confirm_type": entry_type_sniper}
        bonus_tag = " + clean bonus" if sniper_bonus > 0 else ""
        return Decision(
            symbol=symbol,
            bias=po3_bias,
            mode="sniper",
            confidence=confidence,  # keep displayed confidence consistent
            action=action,
            commentary=f"PO3 SNIPER ({entry_type_sniper}): accumulation → sweep → agreement reclaim → MSS.{bonus_tag}"
            + news_note,
            trade_plan=trade_plan,
            score=confidence,
            meta=meta,
        )

    # -----------------------
    # CONTINUATION
    # -----------------------
    continuation_phase_ok = (po3_phase == "DISTRIBUTION") and distribution_active

    continuation_ready = (
        continuation_phase_ok
        and structure_ok_cont
        and entry_confirmed_cont
        and session_valid_continuation
        and rr >= MIN_RR
        and po3_bias in ("bullish", "bearish")
    )

    # ✅ Continuation uses base confidence threshold (6.5)
    if continuation_ready and confidence >= SETUP_SCORE_THRESHOLD:
        action = "BUY NOW" if po3_bias == "bullish" else "SELL NOW"
        trade_plan = {
            "entry": factors.get("entry", "TBD"),
            "stop": factors.get("stop", "TBD"),
            "tp1": factors.get("tp1", "TBD"),
            "tp2": factors.get("tp2", "TBD"),
            "rr": rr,
        }

        htf_tag = " (HTF aligned)" if htf_alignment else " (HTF not aligned)"
        meta = {**base_meta, "setup_type": "CONTINUATION", "entry_confirm_type": entry_type_cont}

        return Decision(
            symbol=symbol,
            bias=po3_bias,
            mode="continuation",
            confidence=confidence,
            action=action,
            commentary=f"CONTINUATION ({entry_type_cont}): distribution active + structure OK{htf_tag}."
            + news_note,
            trade_plan=trade_plan,
            score=confidence,
            meta=meta,
        )

    # -----------------------
    # STANDBY messaging
    # -----------------------
    if po3_phase == "ACCUMULATION" and structure_ok_cont:
        commentary = "Trend developing; waiting for MSS/confirmation (not forcing sweep)."
    elif not liquidity_sweep:
        commentary = "Waiting for valid liquidity sweep (Phase 2 manipulation)."
    elif liquidity_sweep and not mss_shift:
        commentary = "Sweep detected; waiting for 15m MSS and displacement confirmation."
    elif (liquidity_sweep and mss_shift) and not entry_confirmed_sniper:
        commentary = "PO3 structure present; waiting for SNIPER entry confirmation (wick→expansion OR CISD)."
    elif continuation_phase_ok and not entry_confirmed_cont:
        commentary = "Distribution active; waiting for CONTINUATION entry confirmation (wick→expansion)."
    else:
        commentary = "No clean PO3 narrative yet (no forced trades)."

    return Decision(
        symbol=symbol,
        bias=po3_bias,
        mode="standby",
        confidence=confidence,
        action="WATCH",
        commentary=commentary + news_note,
        trade_plan={},
        score=confidence,
        meta=base_meta,
    )
