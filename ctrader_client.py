"""
Minimal cTrader -> OHLC bridge for Trading Assistant.

Run this as a sidecar service and point Streamlit secret `CTRADER_LIVE_DATA_URL`
to: http://<host>:<port>/candles

This bridge returns a JSON shape compatible with `data/live_data._fetch_ctrader_ohlc`:
{
  "candles": [
    {"time": "2026-02-25T12:00:00Z", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "volume": 1234}
  ]
}

Why this file exists:
- Your dashboard uses HTTP candles from `CTRADER_LIVE_DATA_URL`.
- cTrader Open API SDK is socket/protobuf based.
- This bridge converts SDK output into the HTTP candle JSON your app expects.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List

from fastapi import FastAPI, HTTPException, Query


# --------- Config helpers ---------

def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def as_json(self) -> Dict:
        return {
            "time": self.time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "volume": float(self.volume),
        }


class CTraderAdapter:
    """
    cTrader Open API adapter.

    IMPORTANT:
    - This file is only a bridge shell until you implement the two methods below.
    - Put this snippet/import in this file (NOT in app.py):
        from ctrader_open_api import Client, EndPoints, Auth
    - Put your auth/connect logic in `connect()`.
    - Put your historical-candle request logic in `fetch_candles()`.

    The bridge endpoint (`/candles`) is already wired to use this adapter.
    """

    def __init__(self):
        self.client_id = _env("CTRADER_CLIENT_ID")
        self.client_secret = _env("CTRADER_CLIENT_SECRET")
        self.account_id = _env("CTRADER_ACCOUNT_ID")
        self.connected = False
        self._client = None

    def connect(self) -> None:
        """
        Initialize SDK client + auth.

        Paste/adapt your cTrader SDK setup here.
        Example skeleton:
            from ctrader_open_api import Client, EndPoints, Auth
            host = EndPoints.PROTOBUF_LIVE_HOST
            port = EndPoints.PROTOBUF_PORT
            client = Client(host, port)
            auth = Auth(client)
            # perform auth handshake, account authorization, etc.
            self._client = client
            self.connected = True
        """
        if self.connected:
            return

        # Keep startup explicit if credentials are missing
        if not self.client_id or not self.client_secret or not self.account_id:
            raise RuntimeError(
                "Missing CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET / CTRADER_ACCOUNT_ID env vars"
            )

        # --- TODO: Replace with real SDK connect/auth code ---
        # We intentionally do not fake candles; failing loudly is safer for live trading.
        raise RuntimeError(
            "cTrader SDK connect not implemented in ctrader_client.py. "
            "Add your SDK connection logic in CTraderAdapter.connect()."
        )

    def fetch_candles(self, symbol: str, timeframe: str, count: int) -> List[Candle]:
        """
        Fetch historical candles from cTrader SDK and normalize to Candle list.

        Replace TODO with real SDK request mapping.
        """
        self.connect()

        # --- TODO: Replace with real SDK candle request/parse code ---
        # Example output format expected by bridge:
        # return [Candle(time=..., open=..., high=..., low=..., close=..., volume=...)]
        raise RuntimeError(
            "cTrader SDK candle fetch not implemented in ctrader_client.py. "
            "Add your request/parse logic in CTraderAdapter.fetch_candles()."
        )


# --------- API ---------

app = FastAPI(title="cTrader Candle Bridge", version="0.1.0")
adapter = CTraderAdapter()


@app.get("/health")
def health() -> Dict:
    return {
        "ok": True,
        "connected": bool(adapter.connected),
        "has_client_id": bool(adapter.client_id),
        "has_client_secret": bool(adapter.client_secret),
        "has_account_id": bool(adapter.account_id),
    }


@app.get("/candles")
def candles(
    symbol: str = Query(..., description="Symbol, e.g. XAUUSD"),
    timeframe: str = Query("M15", description="M1/M5/M15/M30/H1/H4/D1"),
    count: int = Query(300, ge=10, le=5000),
) -> Dict:
    try:
        out = adapter.fetch_candles(symbol=symbol, timeframe=timeframe, count=count)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"ctrader_bridge_error: {e}")

    return {"candles": [c.as_json() for c in out]}


if __name__ == "__main__":
    import uvicorn

    host = _env("CTRADER_BRIDGE_HOST", "0.0.0.0")
    port = int(_env("CTRADER_BRIDGE_PORT", "8787"))
    uvicorn.run(app, host=host, port=port)
