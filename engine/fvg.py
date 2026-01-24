from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Literal, Dict, Any
import pandas as pd


FvgType = Literal["bull", "bear"]


@dataclass
class FVG:
    type: FvgType
    top: float
    bottom: float
    start: Any   # timestamp/index
    end: Any     # timestamp/index


def detect_fvgs(df: pd.DataFrame, lookback: int = 160) -> List[FVG]:
    """
    Simple 3-candle ICT-style FVG detection:
      Bull FVG: candle i-2 HIGH < candle i LOW  (gap up)
      Bear FVG: candle i-2 LOW  > candle i HIGH (gap down)

    Expects df with columns: ['high','low'] at minimum.
    """
    if df is None or df.empty:
        return []

    d = df.tail(lookback).copy()
    if len(d) < 5:
        return []

    highs = d["high"].values
    lows = d["low"].values
    idx = list(d.index)

    out: List[FVG] = []
    for i in range(2, len(d)):
        # Bullish gap
        if highs[i - 2] < lows[i]:
            out.append(
                FVG(
                    type="bull",
                    top=float(lows[i]),
                    bottom=float(highs[i - 2]),
                    start=idx[i - 2],
                    end=idx[i],
                )
            )

        # Bearish gap
        if lows[i - 2] > highs[i]:
            out.append(
                FVG(
                    type="bear",
                    top=float(lows[i - 2]),
                    bottom=float(highs[i]),
                    start=idx[i - 2],
                    end=idx[i],
                )
            )
    return out


def pick_recent_fvgs(fvgs: List[FVG], max_show: int = 3) -> List[FVG]:
    if not fvgs:
        return []
    return fvgs[-max_show:]


def price_in_zone(price: float, top: float, bottom: float, pad: float = 0.0) -> bool:
    z_top = max(top, bottom) + pad
    z_bot = min(top, bottom) - pad
    return z_bot <= price <= z_top


def nearest_fvg(
    df: pd.DataFrame,
    fvgs: List[FVG],
    pad_frac: float = 0.0003,
) -> Optional[Dict[str, Any]]:
    """
    Returns info about whether last close is near/inside any recent FVG.
    """
    if df is None or df.empty or not fvgs:
        return None

    last_price = float(df["close"].iloc[-1])
    pad = max(last_price * pad_frac, 0.0)

    # check most recent first
    for z in reversed(fvgs):
        if price_in_zone(last_price, z.top, z.bottom, pad=pad):
            return {
                "type": z.type,
                "top": z.top,
                "bottom": z.bottom,
                "start": z.start,
                "end": z.end,
                "last_price": last_price,
                "pad": pad,
            }
    return None
from dataclasses import
from typing import Tuple

def _zone_bounds(z) -> Tuple[float, float]:
    lo = float(min(z.top, z.bottom))
    hi = float(max(z.top, z.bottom))
    return lo, hi

def _is_touched_or_filled(df: pd.DataFrame, z) -> Tuple[bool, bool]:
    """
    touched: price has entered the zone after it formed
    filled:  price has traded through to the far side of the zone
    """
    lo, hi = _zone_bounds(z)

    # slice from the zone start onward (be safe if index types differ)
    try:
        d2 = df.loc[z.start:]
    except Exception:
        d2 = df

    if d2 is None or d2.empty:
        return False, False

    lows = d2["low"]
    highs = d2["high"]

    if z.type == "bull":
        touched = float(lows.min()) <= hi
        filled  = float(lows.min()) <= lo
    else:  # "bear"
        touched = float(highs.max()) >= lo
        filled  = float(highs.max()) >= hi

    return touched, filled

def _freshness_weight(age_bars: int) -> float:
    # Newer zones score higher; fades out by ~60 bars
    return max(0.0, 1.0 - (age_bars / 60.0))

def compute_fvg_context(
    df: pd.DataFrame,
    lookback: int = 160,
    max_show: int = 3,
    pad_bps: float = 30.0,  # 30 bps = 0.30%
) -> Dict[str, Any]:
    """
    Returns:
      near_fvg: bool (within pad of any recent zone)
      fvg_score: float (0..~3)
      fvgs: List[FVG] (most recent few)
      debug: optional small dict for UI/logging
    """
    out = {"near_fvg": False, "fvg_score": 0.0, "fvgs": [], "debug": {}}

    if df is None or df.empty or len(df) < 10:
        return out

    fvgs = detect_fvgs(df, lookback=lookback)
    if not fvgs:
        return out

    fvgs = fvgs[-max_show:]  # keep most recent few
    out["fvgs"] = fvgs

    # last price (support either "close" or "Close")
    if "close" in df.columns:
        last_price = float(df["close"].iloc[-1])
    else:
        last_price = float(df["Close"].iloc[-1])

    pad = last_price * (pad_bps / 10000.0)  # bps → decimal
    best = 0.0

    # bar positions for "freshness"
    idx_list = list(df.index)

    for z in fvgs:
        lo, hi = _zone_bounds(z)

        # proximity → 0..1
        inside = (lo - pad) <= last_price <= (hi + pad)
        if inside:
            prox = 1.0
            out["near_fvg"] = True
        else:
            # distance outside zone (0 = at boundary)
            dist = min(abs(last_price - lo), abs(last_price - hi))
            prox = max(0.0, 1.0 - (dist / (pad * 2.0)))  # fades quickly

        # size → 0..1 (relative to price, capped)
        gap = max(0.0, hi - lo)
        size = min(1.0, (gap / max(1e-9, last_price)) * 200.0)  # ~0.5% gap ≈ 1.0

        # freshness → 0..1
        try:
            start_pos = idx_list.index(z.start)
            age_bars = len(idx_list) - 1 - start_pos
        except Exception:
            age_bars = 60
        fresh = _freshness_weight(age_bars)

        # touched/filled → penalty
        touched, filled = _is_touched_or_filled(df, z)
        if filled:
            life = 0.0
        elif touched:
            life = 0.5
        else:
            life = 1.0

        # combine → 0..~3
        score = (1.2 * prox) + (0.9 * fresh) + (0.9 * size)
        score *= life

        best = max(best, score)

    out["fvg_score"] = round(best, 3)
    out["debug"] = {"pad": pad, "last_price": last_price}
    return out
