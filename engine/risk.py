# engine/risk.py
from __future__ import annotations

from dataclasses import is_dataclass
from typing import Dict, Any

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _get_equity(profile, default_equity: float = 10000.0) -> float:
    for name in ("equity", "account_equity", "balance", "account_balance"):
        if hasattr(profile, name):
            try:
                v = float(getattr(profile, name))
                if v > 0:
                    return v
            except Exception:
                pass
    return float(default_equity)

def _mode_base_risk_pct(mode: str) -> float:
    """
    Risk per trade as fraction of equity (0.0025 = 0.25%)
    """
    m = (mode or "").lower()

    # Your engine modes
    if m == "standby":
        return 0.0
    if m == "sniper":
        return 0.0025   # 0.25%
    if m == "continuation":
        return 0.0020   # slightly lower by default

    # Optional "style" modes (if you add them later)
    if m == "conservative":
        return 0.0025
    if m == "balanced":
        return 0.0050
    if m == "aggressive":
        return 0.0075

    return 0.0025

def _volatility_mult(vol_risk: str) -> float:
    v = (vol_risk or "normal").lower()
    if v == "high":
        return 0.6
    if v == "extreme":
        return 0.25
    return 1.0

def _confidence_mult(confidence: float) -> float:
    if confidence < 5.0:
        return 0.0
    if confidence < 7.0:
        return 0.6
    if confidence < 7.8:
        return 0.9
    return 1.0

def apply_sizing(decision, profile, factors: Dict[str, Any], default_equity: float = 10000.0):
    """
    Adds:
      - decision.risk_pct (fraction)
      - decision.size (position units, generic)
      - decision.meta (merged sizing details)
    Also merges into trade_plan when possible.
    """
    entry = factors.get("entry", None)
    stop = factors.get("stop", None)
    try:
        entry_f = float(entry)
        stop_f = float(stop)
    except Exception:
        return decision

    stop_dist = abs(entry_f - stop_f)
    if stop_dist <= 0:
        return decision

    equity = _get_equity(profile, default_equity=default_equity)
    confidence = float(getattr(decision, "confidence", 0.0))
    score = float(getattr(decision, "score", confidence))

    base_risk = _mode_base_risk_pct(getattr(decision, "mode", "sniper"))
    conf_mult = _confidence_mult(confidence)
    score_mult = clamp(score / 10.0, 0.6, 1.1)
    vol_mult = _volatility_mult(factors.get("volatility_risk", "normal"))

    risk_pct = base_risk * conf_mult * score_mult * vol_mult
    risk_pct = clamp(risk_pct, 0.0, 0.01)  # max 1%

    risk_amt = equity * risk_pct
    size = (risk_amt / stop_dist) if stop_dist else 0.0

    tp = getattr(decision, "trade_plan", {}) or {}
    if isinstance(tp, dict):
        tp = dict(tp)
        tp.update({
            "risk_pct": round(risk_pct, 6),
            "risk_amount": round(risk_amt, 2),
            "stop_dist": round(stop_dist, 6),
            "size": round(size, 6),
            "equity_used": round(equity, 2),
        })

    sizing_meta = {
        "sizing": {
            "equity": round(equity, 2),
            "base_risk_pct": base_risk,
            "conf_mult": round(conf_mult, 4),
            "score_mult": round(score_mult, 4),
            "vol_mult": vol_mult,
            "stop_dist": round(stop_dist, 6),
        }
    }

    if not is_dataclass(decision):
        return decision

    # IMPORTANT: merge meta instead of overwriting it
    existing_meta = getattr(decision, "meta", {}) or {}
    if not isinstance(existing_meta, dict):
        existing_meta = {}

    decision.risk_pct = risk_pct
    decision.size = size
    decision.meta = {**existing_meta, **sizing_meta}
    decision.trade_plan = tp
    return decision
