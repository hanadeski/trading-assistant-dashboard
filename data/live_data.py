from datetime import datetime, timezone

import pandas as pd
import streamlit as st


# cTrader symbol mapping (internal -> cTrader symbol)
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

# Interval mapping to bridge/SDK-style timeframes
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
    Pull candles directly through cTrader adapter (SDK/upstream) without requiring
    CTRADER_LIVE_DATA_URL in Streamlit runtime.

    Required bridge/sdk env vars (process/runtime dependent):
      - CTRADER_CLIENT_ID
      - CTRADER_CLIENT_SECRET
      - CTRADER_ACCOUNT_ID

    Optional:
      - CTRADER_UPSTREAM_CANDLES_URL (if using upstream HTTP candle source)
      - CTRADER_TOKEN_URL / CTRADER_ACCESS_TOKEN / CTRADER_API_KEY
    """
    sym = _canonical_symbol(symbol)
    ctrader_symbol = CTRADER_SYMBOL_MAP.get(sym, sym)
    tf = INTERVAL_TO_CTRADER.get(interval, "M15")
    count = _period_to_count(period, interval)

    try:
        # Import lazily so app still loads even if SDK deps are missing.
        from ctrader_client import CTraderAdapter
    except Exception:
        out = pd.DataFrame()
        out.attrs["fetch_error"] = "ctrader_adapter_import_failed"
        return out

    try:
        adapter = CTraderAdapter()
        candles = adapter.fetch_candles(symbol=ctrader_symbol, timeframe=tf, count=count)
        if not candles:
            out = pd.DataFrame()
            out.attrs["fetch_error"] = "sdk_empty_candles"
            return out

        df = pd.DataFrame([c.as_json() if hasattr(c, "as_json") else c for c in candles])
        df = _normalize_candle_frame(df)
        if df.empty:
            out = pd.DataFrame()
            out.attrs["fetch_error"] = "normalized_frame_empty"
            return out

        df.attrs["used_ticker"] = ctrader_symbol
        df.attrs["provider"] = "ctrader_sdk"
        return df
    except Exception as e:
        out = pd.DataFrame()
        out.attrs["fetch_error"] = f"sdk_error:{e}"
        return out


@st.cache_data(ttl=30, show_spinner=False)
def fetch_ohlc(symbol: str, interval: str = "15m", period: str = "5d") -> pd.DataFrame:
    """
    Fetch OHLC from cTrader SDK/upstream adapter only.

    If source is unavailable, returns empty DataFrame to avoid
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
            out.attrs["provider"] = "ctrader_sdk"
            out.attrs["used_ticker"] = str(df.attrs.get("used_ticker", _canonical_symbol(symbol)))
            out.attrs["fetch_error"] = f"stale_bars_lag_{round(lag_min,2)}m"
            return out
    except Exception:
        pass

    return df
