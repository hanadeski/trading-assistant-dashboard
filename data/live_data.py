import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st


# cTrader symbol mapping (internal -> cTrader/live-assistant symbol)
CTRADER_SYMBOL_MAP = {
    # FX majors
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "USDCHF": "USDCHF",
    "AUDUSD": "AUDUSD",
    "NZDUSD": "NZDUSD",
    "USDCAD": "USDCAD",
    # Crosses
    "EURJPY": "EURJPY",
    "GBPJPY": "GBPJPY",
    "EURGBP": "EURGBP",
    "AUDJPY": "AUDJPY",
    "CADJPY": "CADJPY",
    # Metals / energy / indices
    "XAUUSD": "XAUUSD",
    "XAGUSD": "XAGUSD",
    "WTI": "USOIL",
    "US30": "US30",
    "US100": "US100",
    "US500": "US500",
}

# Interval mapping for cTrader/live-assistant endpoints
INTERVAL_TO_CTRADER = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "30m": "M30",
    "1h": "H1",
    "4h": "H4",
    "1d": "D1",
}


def _canonical_symbol(symbol: str) -> str:
    """Normalize broker symbols that may include suffixes (e.g., XAUUSD.a, US100-cash)."""
    s = (symbol or "").upper().strip()
    for sep in (".", "-", "_"):
        if sep in s:
            s = s.split(sep, 1)[0]
    return s


def _interval_minutes(interval: str) -> int:
    if interval.endswith("m"):
        return max(1, int(interval[:-1]))
    if interval.endswith("h"):
        return max(1, int(interval[:-1]) * 60)
    if interval.endswith("d"):
        return max(1, int(interval[:-1]) * 1440)
    return 15


def _period_to_count(period: str, interval: str) -> int:
    period = (period or "5d").strip().lower()
    unit = period[-1]
    try:
        value = int(period[:-1])
    except Exception:
        value = 5

    if unit == "d":
        minutes = value * 24 * 60
    elif unit == "w":
        minutes = value * 7 * 24 * 60
    elif unit == "m":
        minutes = value * 30 * 24 * 60
    else:
        minutes = 5 * 24 * 60

    bars = int(minutes / _interval_minutes(interval))
    return max(120, min(5000, bars))


def _read_secret(name: str) -> str:
    val = os.getenv(name, "").strip()
    if val:
        return val
    try:
        # flat secret key: CTRADER_CLIENT_ID
        direct = str(st.secrets.get(name, "")).strip()
        if direct:
            return direct

        # nested section support:
        # [ctrader]
        # client_id = "..."
        # client_secret = "..."
        # live_data_url = "..."
        section = st.secrets.get("ctrader", {})
        if isinstance(section, dict):
            key = name.lower().replace("ctrader_", "")
            nested = str(section.get(key, "")).strip()
            if nested:
                return nested
            # allow dashed key variants too
            nested2 = str(section.get(key.replace("_", "-"), "")).strip()
            if nested2:
                return nested2
        return ""
    except Exception:
        return ""


def _token_from_client_credentials() -> str:
    """
    Optional helper: obtain bearer token from your live-data assistant token endpoint.

    Required secrets for this flow:
      - CTRADER_TOKEN_URL
      - CTRADER_CLIENT_ID
      - CTRADER_CLIENT_SECRET
    If unavailable/fails, returns empty string and the code falls back to CTRADER_ACCESS_TOKEN.
    """
    token_url = _read_secret("CTRADER_TOKEN_URL")
    client_id = _read_secret("CTRADER_CLIENT_ID")
    client_secret = _read_secret("CTRADER_CLIENT_SECRET")
    if not token_url or not client_id or not client_secret:
        return ""

    try:
        r = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=12,
        )
        if r.status_code != 200:
            return ""
        payload = r.json() if r.text else {}
        token = str(payload.get("access_token", "")).strip()
        return token
    except Exception:
        return ""


def _normalize_candle_frame(df: pd.DataFrame) -> pd.DataFrame:
    # Normalize common column aliases
    rename_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "t": "time",
        "timestamp": "time",
        "datetime": "time",
    }
    cols = {c: rename_map.get(c, c) for c in df.columns}
    df = df.rename(columns=cols)

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
        df = df.dropna(subset=["time"]).set_index("time").sort_index()
    elif not isinstance(df.index, pd.DatetimeIndex):
        return pd.DataFrame()

    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            if col == "volume":
                df[col] = 0.0
            else:
                return pd.DataFrame()
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["open", "high", "low", "close"])
    return df


def _fetch_ctrader_ohlc(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """
    Pull candles from your cTrader live-data assistant endpoint.

    Required secret:
      - CTRADER_LIVE_DATA_URL

    Optional auth secrets:
      - CTRADER_ACCESS_TOKEN
      - CTRADER_TOKEN_URL + CTRADER_CLIENT_ID + CTRADER_CLIENT_SECRET
      - CTRADER_API_KEY (for custom assistant gateways)

    Expected response formats:
      1) {"candles": [{"time":..., "open":..., "high":..., "low":..., "close":..., "volume":...}, ...]}
      2) [{"time":..., "open":..., ...}, ...]
      3) {"t": [...], "o": [...], "h": [...], "l": [...], "c": [...], "v": [...]}  (array style)
    """
    base_url = _read_secret("CTRADER_LIVE_DATA_URL")
    if not base_url:
        out = pd.DataFrame()
        out.attrs["fetch_error"] = "missing_ctrader_live_data_url"
        return out

    sym = _canonical_symbol(symbol)
    ctrader_symbol = CTRADER_SYMBOL_MAP.get(sym, sym)
    tf = INTERVAL_TO_CTRADER.get(interval, "M15")
    count = _period_to_count(period, interval)

    token = _read_secret("CTRADER_ACCESS_TOKEN") or _token_from_client_credentials()
    api_key = _read_secret("CTRADER_API_KEY")

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["X-API-Key"] = api_key

    params = {
        "symbol": ctrader_symbol,
        "timeframe": tf,
        "count": count,
    }

    try:
        r = requests.get(base_url, params=params, headers=headers, timeout=15)
        if r.status_code != 200:
            out = pd.DataFrame()
            out.attrs["fetch_error"] = f"http_{r.status_code}"
            return out
        payload = r.json() if r.text else {}
    except Exception:
        out = pd.DataFrame()
        out.attrs["fetch_error"] = "request_failed"
        return out

    if isinstance(payload, dict) and {"t", "o", "h", "l", "c"}.issubset(payload.keys()):
        t_vals = payload.get("t", [])
        o_vals = payload.get("o", [])
        h_vals = payload.get("h", [])
        l_vals = payload.get("l", [])
        c_vals = payload.get("c", [])
        v_vals = payload.get("v", [0.0] * len(t_vals))
        n = min(len(t_vals), len(o_vals), len(h_vals), len(l_vals), len(c_vals), len(v_vals))
        if n == 0:
            return pd.DataFrame()
        df = pd.DataFrame(
            {
                "time": pd.to_datetime(t_vals[:n], utc=True, errors="coerce"),
                "open": o_vals[:n],
                "high": h_vals[:n],
                "low": l_vals[:n],
                "close": c_vals[:n],
                "volume": v_vals[:n],
            }
        )
    elif isinstance(payload, dict) and isinstance(payload.get("candles"), list):
        df = pd.DataFrame(payload.get("candles", []))
    elif isinstance(payload, list):
        df = pd.DataFrame(payload)
    else:
        out = pd.DataFrame()
        out.attrs["fetch_error"] = "unexpected_payload_shape"
        return out

    df = _normalize_candle_frame(df)
    if df.empty:
        out = pd.DataFrame()
        out.attrs["fetch_error"] = "normalized_frame_empty"
        return out

    df.attrs["used_ticker"] = ctrader_symbol
    df.attrs["provider"] = "ctrader"
    return df


@st.cache_data(ttl=30, show_spinner=False)
def fetch_ohlc(symbol: str, interval: str = "15m", period: str = "5d") -> pd.DataFrame:
    """
    Fetch OHLC from cTrader live-data source only.

    If cTrader source is unavailable, returns empty DataFrame to avoid
    silently falling back to non-broker feeds.
    """
    df = _fetch_ctrader_ohlc(symbol, interval, period)
    if not isinstance(df, pd.DataFrame) or df.empty:
        out = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        out.attrs["provider"] = "none"
        out.attrs["used_ticker"] = _canonical_symbol(symbol)
        out.attrs["fetch_error"] = str(getattr(df, "attrs", {}).get("fetch_error", "ctrader_source_unavailable"))
        return out

    # Guard against stale bars
    try:
        last_ts = pd.to_datetime(df.index[-1], utc=True)
        now_ts = datetime.now(timezone.utc)
        max_lag_min = max(10, 3 * _interval_minutes(interval))
        lag_min = (now_ts - last_ts.to_pydatetime()).total_seconds() / 60.0
        if lag_min > max_lag_min:
            out = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            out.attrs["provider"] = "ctrader"
            out.attrs["used_ticker"] = str(df.attrs.get("used_ticker", _canonical_symbol(symbol)))
            out.attrs["fetch_error"] = f"stale_bars_lag_{round(lag_min,2)}m"
            return out
    except Exception:
        pass

    return df
