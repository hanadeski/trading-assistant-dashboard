from dataclasses import dataclass, field
from typing import Dict


SETUP_SCORE_THRESHOLD = 7.0
EXECUTION_SCORE_THRESHOLD = 8.5
EXECUTION_CONFIDENCE_MIN = 8.5
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
      Entry quality +1
      Session alignment +1
      HTF bias +1
    """
    po3_active = bool(factors.get("po3_active", False))
    liquidity_sweep = bool(factors.get("liquidity_sweep", False))
    agreement_reclaim = bool(factors.get("agreement_reclaim", False))
    mss_shift = bool(factors.get("mss_shift", False))
    entry_quality = bool(factors.get("entry_quality", False))
    session_alignment = bool(factors.get("session_alignment", False))
    htf_alignment = bool(factors.get("htf_alignment", False))

    po3_score = 2.0 if po3_active else 0.0
    sweep_score = 2.0 if liquidity_sweep else 0.0
    agreement_score = 1.0 if agreement_reclaim else 0.0
    mss_score = 2.0 if mss_shift else 0.0
    entry_score = 1.0 if entry_quality else 0.0
    session_score = 1.0 if session_alignment else 0.0
    htf_score = 1.0 if htf_alignment else 0.0

    total_score = clamp(
        po3_score
        + sweep_score
        + agreement_score
        + mss_score
        + entry_score
        + session_score
        + htf_score,
        0.0,
        10.0,
    )

    # Compatibility keys kept for existing UI components.
    return {
        "po3_score": po3_score,
        "sweep_score": sweep_score,
        "agreement_score": agreement_score,
        "mss_score": mss_score,
        "entry_score": entry_score,
        "session_score": session_score,
        "htf_score": htf_score,
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
    news_block = bool(factors.get("news_block", False))
    session_name = str(factors.get("session_name", ""))
    session_valid = bool(factors.get("session_valid", True))
    is_asia = "asia" in session_name.lower()

    po3_phase = str(factors.get("po3_phase", "ACCUMULATION")).upper()
    accumulation_detected = bool(factors.get("accumulation_detected", False))
    liquidity_sweep = bool(factors.get("liquidity_sweep", False))
    agreement_reclaim = bool(factors.get("agreement_reclaim", False))
    mss_shift = bool(factors.get("mss_shift", False))
    entry_quality = bool(factors.get("entry_quality", False))
    htf_alignment = bool(factors.get("htf_alignment", False))
    distribution_active = bool(factors.get("distribution_active", False))
    structure_ok = bool(factors.get("structure_ok", False))

    score_breakdown = build_score_breakdown(profile, factors)
    confidence = score_breakdown["total_score"]

    base_meta = {
        "po3_phase": po3_phase,
        "setup_type": "NONE",
        "model": "PO3_SNIPER_FIRST",
    }

    if news_block:
        return Decision(
            symbol,
            po3_bias,
            "standby",
            confidence,
            "DO NOTHING",
            "High-impact news window: block new entries for ±15 minutes.",
            {},
            score=confidence,
            meta=base_meta,
        )

    if rr < MIN_RR:
        return Decision(
            symbol,
            po3_bias,
            "standby",
            confidence,
            "WAIT",
            "RR below minimum 2.0 requirement.",
            {},
            score=confidence,
            meta=base_meta,
        )

    # Sniper requirements (all required)
    sniper_ready = (
        accumulation_detected
        and liquidity_sweep
        and agreement_reclaim
        and mss_shift
        and entry_quality
        and session_valid
        and rr >= MIN_RR
        and not news_block
    )

    # Asia is allowed but stricter for sniper setups.
    if is_asia:
        sniper_ready = sniper_ready and htf_alignment and confidence >= 9.0

    if sniper_ready and confidence >= EXECUTION_CONFIDENCE_MIN and po3_bias in ("bullish", "bearish"):
        action = "BUY NOW" if po3_bias == "bullish" else "SELL NOW"
        trade_plan = {
            "entry": factors.get("entry", "TBD"),
            "stop": factors.get("stop", "TBD"),
            "tp1": factors.get("tp1", "TBD"),  # >= 2R by construction
            "tp2": factors.get("tp2", "TBD"),  # HTF liquidity target when available
            "rr": rr,
        }
        meta = {**base_meta, "setup_type": "SNIPER"}
        return Decision(
            symbol,
            po3_bias,
            "sniper",
            confidence,
            action,
            "PO3 sniper setup confirmed (accumulation → sweep → agreement reclaim → MSS).",
            trade_plan,
            score=confidence,
            meta=meta,
        )

    # Continuation requirements (lighter but controlled)
    continuation_ready = (
        distribution_active
        and structure_ok
        and entry_quality
        and session_valid
        and rr >= MIN_RR
        and po3_bias in ("bullish", "bearish")
    )
    if is_asia:
        continuation_ready = continuation_ready and htf_alignment

    if continuation_ready and confidence >= SETUP_SCORE_THRESHOLD:
        action = "BUY NOW" if po3_bias == "bullish" else "SELL NOW"
        trade_plan = {
            "entry": factors.get("entry", "TBD"),
            "stop": factors.get("stop", "TBD"),
            "tp1": factors.get("tp1", "TBD"),
            "tp2": factors.get("tp2", "TBD"),
            "rr": rr,
        }
        meta = {**base_meta, "setup_type": "CONTINUATION"}
        return Decision(
            symbol,
            po3_bias,
            "continuation",
            confidence,
            action,
            "Controlled continuation: distribution active with intact structure.",
            trade_plan,
            score=confidence,
            meta=meta,
        )

    commentary = "No clean PO3 narrative yet (no forced trades)."
    if not liquidity_sweep:
        commentary = "Waiting for valid liquidity sweep (Phase 2 manipulation)."
    elif liquidity_sweep and not mss_shift:
        commentary = "Sweep detected; waiting for 15m MSS and displacement confirmation."

    return Decision(
        symbol,
        po3_bias,
        "standby",
        confidence,
        "WATCH",
        commentary,
        {},
        score=confidence,
        meta=base_meta,
    )
