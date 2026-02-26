"""
cTrader candle bridge for Trading Assistant.

This service exposes:
  - GET /health
  - GET /candles?symbol=...&timeframe=...&count=...

How it works:
1) Preferred: pull candles from an upstream HTTP source via CTRADER_UPSTREAM_CANDLES_URL.
2) Optional: attempt direct SDK usage if `ctrader_open_api` is installed and exposes candle methods.

This service is optional when the app runs direct SDK mode.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

import requests

try:
    from fastapi import FastAPI, HTTPException, Query
    import uvicorn

    FASTAPI_AVAILABLE = True
except Exception:
    FASTAPI_AVAILABLE = False


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

    def as_json(self) -> Dict[str, Any]:
        return {
            "time": self.time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "volume": float(self.volume),
        }


class CTraderAdapter:
    def __init__(self):
        self.client_id = _env("CTRADER_CLIENT_ID")
        self.client_secret = _env("CTRADER_CLIENT_SECRET")
        self.account_id = _env("CTRADER_ACCOUNT_ID")
        self.token_url = _env("CTRADER_TOKEN_URL")
        self.access_token = _env("CTRADER_ACCESS_TOKEN")
        self.api_key = _env("CTRADER_API_KEY")
        self.upstream_candles_url = _env("CTRADER_UPSTREAM_CANDLES_URL")
        self.connected = False
        self._client = None

    def connect(self) -> None:
        """Connect/authenticate once for upstream API or SDK mode."""
        if self.connected:
            return

        if not self.client_id or not self.client_secret or not self.account_id:
            raise RuntimeError(
                "Missing CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET / CTRADER_ACCOUNT_ID env vars"
            )

        # Upstream endpoint mode does not require SDK; just ensure token if configured.
        if self.upstream_candles_url:
            if not self.access_token and self.token_url:
                self.access_token = self._fetch_access_token()
            self.connected = True
            return

        # Optional SDK mode if library exists in runtime.
        try:
            from ctrader_open_api import Client, EndPoints, Auth  # type: ignore

            host = EndPoints.PROTOBUF_LIVE_HOST
            port = EndPoints.PROTOBUF_PORT
            self._client = Client(host, port)
            # Keep reference so users can expand auth/session handling per their SDK version.
            self._auth = Auth(self._client)
            self.connected = True
            return
        except Exception as e:
            raise RuntimeError(
                "No usable data source. Set CTRADER_UPSTREAM_CANDLES_URL or install/configure "
                "ctrader_open_api SDK in bridge runtime."
            ) from e

    def _fetch_access_token(self) -> str:
        r = requests.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=15,
        )
        if r.status_code != 200:
            raise RuntimeError(f"token_http_{r.status_code}")
        payload = r.json() if r.text else {}
        token = str(payload.get("access_token", "")).strip()
        if not token:
            raise RuntimeError("token_missing_access_token")
        return token

    @staticmethod
    def _parse_time(v: Any) -> datetime:
        if isinstance(v, (int, float)):
            # milliseconds or seconds
            ts = float(v)
            if ts > 10_000_000_000:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        s = str(v).strip()
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone(timezone.utc)

    @classmethod
    def _normalize_payload(cls, payload: Any) -> List[Candle]:
        if isinstance(payload, dict) and isinstance(payload.get("candles"), list):
            items = payload["candles"]
        elif isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict) and {"t", "o", "h", "l", "c"}.issubset(payload.keys()):
            t = payload.get("t", [])
            o = payload.get("o", [])
            h = payload.get("h", [])
            l = payload.get("l", [])
            c = payload.get("c", [])
            v = payload.get("v", [0.0] * len(t))
            n = min(len(t), len(o), len(h), len(l), len(c), len(v))
            items = [
                {"time": t[i], "open": o[i], "high": h[i], "low": l[i], "close": c[i], "volume": v[i]}
                for i in range(n)
            ]
        else:
            raise RuntimeError("unexpected_payload_shape")

        out: List[Candle] = []
        for x in items:
            out.append(
                Candle(
                    time=cls._parse_time(x.get("time") or x.get("t") or x.get("timestamp") or x.get("datetime")),
                    open=float(x.get("open", x.get("o"))),
                    high=float(x.get("high", x.get("h"))),
                    low=float(x.get("low", x.get("l"))),
                    close=float(x.get("close", x.get("c"))),
                    volume=float(x.get("volume", x.get("v", 0.0) or 0.0)),
                )
            )

        if not out:
            raise RuntimeError("normalized_frame_empty")

        return out

    def _fetch_via_upstream(self, symbol: str, timeframe: str, count: int) -> List[Candle]:
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        r = requests.get(
            self.upstream_candles_url,
            params={"symbol": symbol, "timeframe": timeframe, "count": count},
            headers=headers,
            timeout=20,
        )
        if r.status_code != 200:
            raise RuntimeError(f"upstream_http_{r.status_code}")

        payload = r.json() if r.text else {}
        return self._normalize_payload(payload)

    def fetch_candles(self, symbol: str, timeframe: str, count: int) -> List[Candle]:
        self.connect()

        if self.upstream_candles_url:
            return self._fetch_via_upstream(symbol, timeframe, count)

        # Best-effort SDK mode fallback for custom runtimes.
        if self._client is None:
            raise RuntimeError("sdk_client_unavailable")

        for method_name in ("fetch_candles", "get_candles", "get_trendbars"):
            m = getattr(self._client, method_name, None)
            if callable(m):
                payload = m(symbol=symbol, timeframe=timeframe, count=count)
                return self._normalize_payload(payload)

        raise RuntimeError(
            "sdk_candle_method_not_found: expected one of fetch_candles/get_candles/get_trendbars"
        )


adapter = CTraderAdapter()


def _health_payload() -> Dict[str, Any]:
    return {
        "ok": True,
        "connected": bool(adapter.connected),
        "has_client_id": bool(adapter.client_id),
        "has_client_secret": bool(adapter.client_secret),
        "has_account_id": bool(adapter.account_id),
        "has_upstream_url": bool(adapter.upstream_candles_url),
    }


def _candles_payload(symbol: str, timeframe: str, count: int) -> Dict[str, Any]:
    out = adapter.fetch_candles(symbol=symbol, timeframe=timeframe, count=count)
    return {"candles": [c.as_json() for c in out]}


if FASTAPI_AVAILABLE:
    app = FastAPI(title="cTrader Candle Bridge", version="0.2.0")

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return _health_payload()

    @app.get("/candles")
    def candles(
        symbol: str = Query(..., description="Symbol, e.g. XAUUSD"),
        timeframe: str = Query("M15", description="M1/M5/M15/M30/H1/H4/D1"),
        count: int = Query(300, ge=10, le=5000),
    ) -> Dict[str, Any]:
        try:
            return _candles_payload(symbol, timeframe, count)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"ctrader_bridge_error: {e}")


def _run_stdlib_server(host: str, port: int) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._send(200, _health_payload())
                    return
                if parsed.path == "/candles":
                    q = parse_qs(parsed.query)
                    symbol = (q.get("symbol") or [""])[0]
                    timeframe = (q.get("timeframe") or ["M15"])[0]
                    count = int((q.get("count") or ["300"])[0])
                    if not symbol:
                        self._send(400, {"error": "symbol is required"})
                        return
                    self._send(200, _candles_payload(symbol, timeframe, count))
                    return
                self._send(404, {"error": "not_found"})
            except Exception as e:
                self._send(503, {"error": f"ctrader_bridge_error: {e}"})

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"cTrader bridge listening on http://{host}:{port} (stdlib)")
    httpd.serve_forever()


if __name__ == "__main__":
    host = _env("CTRADER_BRIDGE_HOST", "0.0.0.0")
    port = int(_env("CTRADER_BRIDGE_PORT", "8787"))

    if FASTAPI_AVAILABLE:
        uvicorn.run(app, host=host, port=port)
    else:
        _run_stdlib_server(host, port)
