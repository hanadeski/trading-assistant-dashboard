# engine/portfolio.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional, List, Tuple
import time


@dataclass
class Position:
    symbol: str
    side: str                 # "long" or "short"
    entry: float
    size: float               # generic units
    stop: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    opened_ts: int = 0

    # live fields
    last_price: Optional[float] = None
    unrealized_pnl: float = 0.0


@dataclass
class Trade:
    symbol: str
    side: str
    entry: float
    exit: float
    size: float
    pnl: float
    reason: str               # "stop" / "tp1" / "tp2" / "manual" / "unknown"
    opened_ts: int
    closed_ts: int


def _to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        # "TBD" or other strings -> None
        return float(x)
    except Exception:
        return None


def _last_close_from_factors(factors: Dict[str, Any]) -> Optional[float]:
    df = factors.get("df", None)
    try:
        if df is None or getattr(df, "empty", True):
            return None
        return float(df["close"].iloc[-1])
    except Exception:
        return None


def _pnl(side: str, entry: float, price: float, size: float) -> float:
    if side == "long":
        return (price - entry) * size
    return (entry - price) * size


def init_portfolio_state(st_session_state) -> None:
    # Keep these stable across reruns
    if "_portfolio" not in st_session_state:
        st_session_state["_portfolio"] = {
            "equity": 10_000.0,          # paper equity
            "cash": 10_000.0,
            "open": {},                  # symbol -> Position dict
            "closed": [],                # list[Trade dict]
            "last_update_ts": 0,
        }


def update_portfolio(
    st_session_state,
    decisions: List[Any],
    factors_by_symbol: Dict[str, Dict[str, Any]],
    now_ts: Optional[int] = None,
) -> None:
    """
    - Opens a position when decision.action is BUY NOW / SELL NOW and entry is numeric.
    - Updates mark-to-market every run.
    - Auto-closes on stop/tp1/tp2 if levels exist.
    """
    init_portfolio_state(st_session_state)
    P = st_session_state["_portfolio"]
    if now_ts is None:
        now_ts = int(time.time())

    open_positions: Dict[str, Dict[str, Any]] = P["open"]
    closed_trades: List[Dict[str, Any]] = P["closed"]

    # 1) Mark-to-market + auto-close
    to_close: List[Tuple[str, float, str]] = []  # (sym, exit_price, reason)

    for sym, pos_dict in list(open_positions.items()):
        pos = Position(**pos_dict)
        last_price = _last_close_from_factors(factors_by_symbol.get(sym, {}))
        if last_price is None:
            continue

        pos.last_price = last_price
        pos.unrealized_pnl = _pnl(pos.side, pos.entry, last_price, pos.size)

        # stop/tp checks
        if pos.stop is not None:
            if pos.side == "long" and last_price <= pos.stop:
                to_close.append((sym, last_price, "stop"))
            if pos.side == "short" and last_price >= pos.stop:
                to_close.append((sym, last_price, "stop"))

        if pos.tp1 is not None:
            if pos.side == "long" and last_price >= pos.tp1:
                to_close.append((sym, last_price, "tp1"))
            if pos.side == "short" and last_price <= pos.tp1:
                to_close.append((sym, last_price, "tp1"))

        if pos.tp2 is not None:
            if pos.side == "long" and last_price >= pos.tp2:
                to_close.append((sym, last_price, "tp2"))
            if pos.side == "short" and last_price <= pos.tp2:
                to_close.append((sym, last_price, "tp2"))

        open_positions[sym] = asdict(pos)

    # close (dedupe: if both tp1 and tp2 triggered, prefer tp2)
    if to_close:
        priority = {"stop": 0, "tp1": 1, "tp2": 2, "manual": 3, "unknown": -1}
        best: Dict[str, Tuple[float, str]] = {}
        for sym, px, reason in to_close:
            if sym not in best or priority[reason] > priority[best[sym][1]]:
                best[sym] = (px, reason)

        for sym, (exit_px, reason) in best.items():
            pos = Position(**open_positions[sym])
            pnl = _pnl(pos.side, pos.entry, exit_px, pos.size)

            closed_trades.append(asdict(Trade(
                symbol=pos.symbol,
                side=pos.side,
                entry=pos.entry,
                exit=exit_px,
                size=pos.size,
                pnl=pnl,
                reason=reason,
                opened_ts=pos.opened_ts,
                closed_ts=now_ts,
            )))

            # Update equity/cash in a very simple way
            P["equity"] = float(P["equity"]) + float(pnl)
            P["cash"] = float(P["cash"]) + float(pnl)

            del open_positions[sym]

    # 2) Open new positions from decisions (only if not already open)
    for d in decisions:
        sym = getattr(d, "symbol", None)
        action = getattr(d, "action", "")
        if not sym or sym in open_positions:
            continue

        if action not in ("BUY NOW", "SELL NOW"):
            continue

        factors = factors_by_symbol.get(sym, {})
        entry = _to_float(factors.get("entry", None))
        if entry is None:
            continue  # can't trade without numeric entry

        stop = _to_float(factors.get("stop", None))
        tp1 = _to_float(factors.get("tp1", None))
        tp2 = _to_float(factors.get("tp2", None))

        size = _to_float(getattr(d, "size", None)) or 0.0
        if size <= 0.0:
            # fallback if sizing didn't populate for some reason
            size = 1.0

        side = "long" if action == "BUY NOW" else "short"
        pos = Position(
            symbol=sym,
            side=side,
            entry=float(entry),
            size=float(size),
            stop=stop,
            tp1=tp1,
            tp2=tp2,
            opened_ts=now_ts,
        )
        open_positions[sym] = asdict(pos)

    P["open"] = open_positions
    P["closed"] = closed_trades
    P["last_update_ts"] = now_ts
