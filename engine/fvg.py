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
