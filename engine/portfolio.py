# engine/portfolio.py
from __future__ import annotations

import time
from typing import Dict, Any, List, Optional


def _now_ts() -> int:
    return int(time.time())


def _to_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        if isinstance(x, (int, float)):
            return float(x)
        # strings like "TBD"
        return float(x)
    except Exception:
        return default


def init_portfolio_state(state, starting_equity: float = 10000.0) -> None:
    """
    Creates state["portfolio"] in the structure the portfolio panel expects.
    Safe to call every rerun.
    """
    if "portfolio" not in state:
        state["portfolio"] = {
            "starting_equity": float(starting_equity),
            "equity": float(starting_equity),
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "open_positions": [],   # list of dicts
            "closed_trades": [],    # list of dicts
            "equity_curve": [],     # list of dicts (t, equity)
        }


def _find_open_position(open_positions: List[Dict[str, Any]], symbol: str) -> Optional[Dict[str, Any]]:
    for pos in open_positions:
        if pos.get("symbol") == symbol:
            return pos
    return None


def _remove_open_position(open_positions: List[Dict[str, Any]], symbol: str) -> None:
    open_positions[:] = [p for p in open_positions if p.get("symbol") != symbol]


def _last_price_from_factors(factors: Dict[str, Any]) -> Optional[float]:
    """
    Tries:
      - factors["df"]["close"].iloc[-1]
      - factors["entry"]
    """
    df = factors.get("df")
    try:
        if df is not None and hasattr(df, "__getitem__") and "close" in df:
            return float(df["close"].iloc[-1])
    except Exception:
        pass

    return _to_float(factors.get("entry"), default=None)


def _bar_hl_from_factors(factors: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    df = factors.get("df")
    try:
        if df is not None and "high" in df and "low" in df:
            return float(df["high"].iloc[-1]), float(df["low"].iloc[-1])
    except Exception:
        pass
    return None, None


def _calc_unrealized(pos: Dict[str, Any], last_price: float) -> float:
    side = (pos.get("side") or "").lower()
    entry = _to_float(pos.get("entry"), default=0.0) or 0.0
    size = _to_float(pos.get("size"), default=0.0) or 0.0

    if side == "buy":
        return (last_price - entry) * size
    if side == "sell":
        return (entry - last_price) * size
    return 0.0


def _close_position(portfolio: Dict[str, Any], pos: Dict[str, Any], exit_price: float, reason: str) -> None:
    """
    Realize PnL, move to closed_trades, remove from open_positions.
    """
    side = (pos.get("side") or "").lower()
    entry = _to_float(pos.get("entry"), default=0.0) or 0.0
    size = _to_float(pos.get("size"), default=0.0) or 0.0

    if side == "buy":
        realized = (exit_price - entry) * size
    elif side == "sell":
        realized = (entry - exit_price) * size
    else:
        realized = 0.0

    trade = {
        "symbol": pos.get("symbol"),
        "side": pos.get("side"),
        "size": size,
        "entry": entry,
        "exit": exit_price,
        "pnl": realized,
        "opened_at": pos.get("opened_at"),
        "closed_at": _now_ts(),
        "reason": reason,
        "stop": pos.get("stop"),
        "tp1": pos.get("tp1"),
        "tp2": pos.get("tp2"),
        "confidence": pos.get("confidence"),
        "risk_pct": pos.get("risk_pct"),
    }

    portfolio["realized_pnl"] = float(portfolio.get("realized_pnl", 0.0)) + float(realized)
    portfolio["equity"] = float(portfolio.get("starting_equity", 0.0)) + float(portfolio["realized_pnl"]) + float(portfolio.get("unrealized_pnl", 0.0))

    portfolio.setdefault("closed_trades", []).append(trade)

    # remove open
    _remove_open_position(portfolio.setdefault("open_positions", []), pos.get("symbol"))


def update_portfolio(state, decisions: List[Any], factors_by_symbol: Dict[str, Dict[str, Any]]) -> None:
    """
    Paper portfolio updater:
      - Marks to market existing positions using latest price
      - Optionally closes on stop/tp (simple last-bar check)
      - Opens a position when decision.action is BUY NOW / SELL NOW
      - Closes if opposite signal appears while a position exists
    """
    init_portfolio_state(state)
    p = state["portfolio"]

    open_positions: List[Dict[str, Any]] = p.get("open_positions", [])
    closed_trades: List[Dict[str, Any]] = p.get("closed_trades", [])
    equity_curve: List[Dict[str, Any]] = p.get("equity_curve", [])

    # --- 1) Mark-to-market & stop/tp checks ---
    total_unreal = 0.0
    # Work on a copy so we can close while iterating
    for pos in list(open_positions):
        sym = pos.get("symbol")
        factors = factors_by_symbol.get(sym, {})
        last_price = _last_price_from_factors(factors)
        if last_price is None:
            continue

        # stop / tp check using last bar high/low (simple)
        bar_high, bar_low = _bar_hl_from_factors(factors)
        stop = _to_float(pos.get("stop"), default=None)
        tp1 = _to_float(pos.get("tp1"), default=None)
        # tp2 kept for display; we close full at tp1 for now (simple)

        side = (pos.get("side") or "").lower()

        # If we have high/low, use them for hit detection
        if bar_high is not None and bar_low is not None:
            if side == "buy":
                if stop is not None and bar_low <= stop:
                    _close_position(p, pos, stop, "STOP")
                    continue
                if tp1 is not None and bar_high >= tp1:
                    _close_position(p, pos, tp1, "TP1")
                    continue
            elif side == "sell":
                if stop is not None and bar_high >= stop:
                    _close_position(p, pos, stop, "STOP")
                    continue
                if tp1 is not None and bar_low <= tp1:
                    _close_position(p, pos, tp1, "TP1")
                    continue

        # otherwise unrealized
        unreal = _calc_unrealized(pos, last_price)
        pos["unrealized_pnl"] = float(unreal)
        total_unreal += float(unreal)

    p["unrealized_pnl"] = float(total_unreal)
    p["equity"] = float(p.get("starting_equity", 0.0)) + float(p.get("realized_pnl", 0.0)) + float(p.get("unrealized_pnl", 0.0))

    # record equity curve point (lightweight)
    equity_curve.append({"t": _now_ts(), "equity": float(p["equity"])})
    # keep last 500 points
    if len(equity_curve) > 500:
        del equity_curve[:-500]

    # --- 2) Process new decisions (open/close on signals) ---
    for d in decisions:
        sym = getattr(d, "symbol", None)
        action = getattr(d, "action", None)
        if not sym or action not in ("BUY NOW", "SELL NOW"):
            continue

        factors = factors_by_symbol.get(sym, {})
        last_price = _last_price_from_factors(factors)
        trade_plan = getattr(d, "trade_plan", {}) or {}

        # Normalize fields
        side = "buy" if action == "BUY NOW" else "sell"
        entry = _to_float(trade_plan.get("entry") or factors.get("entry"), default=None)
        stop = _to_float(trade_plan.get("stop") or factors.get("stop"), default=None)
        tp1 = _to_float(trade_plan.get("tp1") or factors.get("tp1"), default=None)
        tp2 = _to_float(trade_plan.get("tp2") or factors.get("tp2"), default=None)

        # size / risk from decision (set by engine/risk.py)
        size = _to_float(getattr(d, "size", 0.0), default=0.0) or 0.0
        risk_pct = _to_float(getattr(d, "risk_pct", 0.0), default=0.0) or 0.0
        confidence = _to_float(getattr(d, "confidence", 0.0), default=0.0) or 0.0

        existing = _find_open_position(open_positions, sym)
        
        # --- Step 8B: open / reverse on new signal ---

        # If we already have a position:
        if existing:
            # Reverse if signal flips
            if existing.get("side") != side:
                exit_px = float(last_price if last_price is not None else entry if entry is not None else 0.0)
                _close_position(p, existing, exit_px, "REVERSE")
                existing = None
            else:
                # Same-direction signal while already in position -> ignore
                continue
        
        # If no position exists, open one (only if we have the basics)
        if entry is None:
            entry = last_price
        
        # Require numeric entry/stop and a positive size
        if entry is None or stop is None or size <= 0:
            continue
        
        pos = {
            "symbol": sym,
            "side": side,                 # "buy" / "sell"
            "size": float(size),
            "entry": float(entry),
            "stop": float(stop),
            "tp1": float(tp1) if tp1 is not None else None,
            "tp2": float(tp2) if tp2 is not None else None,
            "opened_at": _now_ts(),
            "confidence": float(confidence),
            "risk_pct": float(risk_pct),
            "unrealized_pnl": 0.0,
        }
        
        open_positions.append(pos)

        # Open new position (only if we have at least entry and size)
        if entry is None or size <= 0:
            # Can't open; just skip quietly
            continue

        new_pos = {
            "symbol": sym,
            "side": side,
            "size": float(size),
            "entry": float(entry),
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "opened_at": _now_ts(),
            "unrealized_pnl": 0.0,
            "confidence": float(confidence),
            "risk_pct": float(risk_pct),
        }

        # replace any stale position record for symbol, then append
        _remove_open_position(open_positions, sym)
        open_positions.append(new_pos)

    # Re-assign to ensure state reflects latest lists
    p["open_positions"] = open_positions
    p["closed_trades"] = closed_trades
    p["equity_curve"] = equity_curve
