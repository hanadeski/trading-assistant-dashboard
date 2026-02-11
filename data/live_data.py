import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st
import yfinance as yf

# Map our internal symbols -> Yahoo tickers
YF_MAP = {
    # FX Majors
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",       # sometimes "USDJPY=X" works too
    "USDCHF": "CHF=X",
    "AUDUSD": "AUDUSD=X",
    "NZDUSD": "NZDUSD=X",
    "USDCAD": "CAD=X",

    # FX Secondary
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "EURGBP": "EURGBP=X",
    "AUDJPY": "AUDJPY=X",
    "CADJPY": "CADJPY=X",

    # Commodities (prefer futures for reliability on Streamlit Cloud)
    "XAUUSD": "GC=F",        # Gold futures
    "XAGUSD": "SI=F",        # Silver futures
    "WTI": "CL=F",

    # Indices (approximations)
    "US30": "^DJI",
    "US100": "^NDX",
    "US500": "^GSPC",
}

# Fallback tickers (try these if primary fails)
YF_FALLBACKS = {
    "XAUUSD": ["GC=F", "XAUUSD=X"],
    "XAGUSD": ["SI=F", "XAGUSD=X"],
}

# Optional broker/API-grade feed mapping (OANDA)
OANDA_MAP = {
    "EURUSD": "EUR_USD",
    "GBPUSD": "GBP_USD",
    "USDJPY": "USD_JPY",
    "USDCHF": "USD_CHF",
    "AUDUSD": "AUD_USD",
    "NZDUSD": "NZD_USD",
    "USDCAD": "USD_CAD",
    "EURJPY": "EUR_JPY",
    "GBPJPY": "GBP_JPY",
    "EURGBP": "EUR_GBP",
    "AUDJPY": "AUD_JPY",
    "CADJPY": "CAD_JPY",
    "XAUUSD": "XAU_USD",
    "XAGUSD": "XAG_USD",
    "WTI": "WTICO_USD",
    "US30": "US30_USD",
    "US100": "NAS100_USD",
    "US500": "SPX500_USD",
}

# Finnhub supports resolution by minute string/letter.
INTERVAL_TO_FINNHUB = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "240",
    "1d": "D",
}

# We fetch Forex/CFD through OANDA symbols on Finnhub.
FINNHUB_FOREX_MAP = {
    "EURUSD": "OANDA:EUR_USD",
    "GBPUSD": "OANDA:GBP_USD",
    "USDJPY": "OANDA:USD_JPY",
    "USDCHF": "OANDA:USD_CHF",
    "AUDUSD": "OANDA:AUD_USD",
    "NZDUSD": "OANDA:NZD_USD",
    "USDCAD": "OANDA:USD_CAD",
    "EURJPY": "OANDA:EUR_JPY",
    "GBPJPY": "OANDA:GBP_JPY",
    "EURGBP": "OANDA:EUR_GBP",
    "AUDJPY": "OANDA:AUD_JPY",
    "CADJPY": "OANDA:CAD_JPY",
    "XAUUSD": "OANDA:XAU_USD",
    "XAGUSD": "OANDA:XAG_USD",
}

INTERVAL_TO_OANDA = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "30m": "M30",
    "1h": "H1",
    "4h": "H4",
    "1d": "D",
}


def _interval_minutes(interval: str) -> int:
    if interval.endswith("m"):
        return max(1, int(interval[:-1]))
    if interval.endswith("h"):
        return max(1, int(interval[:-1]) * 60)
    if interval.endswith("d"):
        return max(1, int(interval[:-1]) * 1440)
    return 15


def _period_to_count(period: str, interval: str) -> int:
    # Keeps calls bounded while retaining enough history for EMA/ATR.
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
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def _fetch_finnhub_ohlc(symbol: str, interval: str, period: str) -> pd.DataFrame:
    token = _read_secret("FINNHUB_API_KEY")
    if not token:
        return pd.DataFrame()

    finnhub_symbol = FINNHUB_FOREX_MAP.get(symbol)
    resolution = INTERVAL_TO_FINNHUB.get(interval)
    if not finnhub_symbol or not resolution:
        return pd.DataFrame()

    bars = _period_to_count(period, interval)
    to_ts = int(time.time())
    from_ts = to_ts - (bars * _interval_minutes(interval) * 60)

    host = _read_secret("FINNHUB_API_HOST") or "https://finnhub.io"
    host = host.rstrip("/")

    params = urlencode(
        {
            "symbol": finnhub_symbol,
            "resolution": resolution,
            "from": from_ts,
            "to": to_ts,
            "token": token,
        }
    )
    url = f"{host}/api/v1/forex/candle?{params}"

    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError):
        return pd.DataFrame()

    if not isinstance(payload, dict) or payload.get("s") != "ok":
        return pd.DataFrame()

    t_vals = payload.get("t") or []
    o_vals = payload.get("o") or []
    h_vals = payload.get("h") or []
    l_vals = payload.get("l") or []
    c_vals = payload.get("c") or []
    v_vals = payload.get("v") or [0.0] * len(t_vals)

    n = min(len(t_vals), len(o_vals), len(h_vals), len(l_vals), len(c_vals), len(v_vals))
    if n == 0:
        return pd.DataFrame()

    try:
        df = pd.DataFrame(
            {
                "time": pd.to_datetime(t_vals[:n], unit="s", utc=True),
                "open": pd.to_numeric(o_vals[:n], errors="coerce"),
                "high": pd.to_numeric(h_vals[:n], errors="coerce"),
                "low": pd.to_numeric(l_vals[:n], errors="coerce"),
                "close": pd.to_numeric(c_vals[:n], errors="coerce"),
                "volume": pd.to_numeric(v_vals[:n], errors="coerce").fillna(0.0),
            }
        ).set_index("time").sort_index()
    except Exception:
        return pd.DataFrame()

    df = df[["open", "high", "low", "close", "volume"]].dropna()
    if df.empty:
        return pd.DataFrame()

    df.attrs["used_ticker"] = finnhub_symbol
    df.attrs["provider"] = "finnhub"
    return df


def _fetch_oanda_ohlc(symbol: str, interval: str, period: str) -> pd.DataFrame:
    token = _read_secret("OANDA_API_TOKEN")
    if not token:
        return pd.DataFrame()

    instrument = OANDA_MAP.get(symbol)
    granularity = INTERVAL_TO_OANDA.get(interval)
    if not instrument or not granularity:
        return pd.DataFrame()

    count = _period_to_count(period, interval)
    host = _read_secret("OANDA_API_HOST") or "https://api-fxpractice.oanda.com"
    host = host.rstrip("/")
    url = (
        f"{host}/v3/instruments/{instrument}/candles"
        f"?price=M&granularity={granularity}&count={count}"
    )

    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept-Datetime-Format": "RFC3339",
        },
    )

    try:
        with urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError):
        return pd.DataFrame()

    candles = payload.get("candles", []) if isinstance(payload, dict) else []
    rows = []
    for c in candles:
        if not isinstance(c, dict) or not c.get("complete"):
            continue
        mid = c.get("mid") or {}
        try:
            rows.append(
                {
                    "time": pd.to_datetime(c.get("time"), utc=True),
                    "open": float(mid.get("o")),
                    "high": float(mid.get("h")),
                    "low": float(mid.get("l")),
                    "close": float(mid.get("c")),
                    "volume": float(c.get("volume", 0.0)),
                }
            )
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("time").sort_index()
    df.attrs["used_ticker"] = instrument
    df.attrs["provider"] = "oanda"
    return df[["open", "high", "low", "close", "volume"]]


def _fetch_yfinance_ohlc(symbol: str, interval: str, period: str) -> pd.DataFrame:
    yf_ticker = YF_MAP.get(symbol, symbol)

    # Try primary + fallbacks
    tickers_to_try = YF_FALLBACKS.get(symbol)
    if not tickers_to_try:
        tickers_to_try = [yf_ticker]
    elif isinstance(tickers_to_try, str):
        tickers_to_try = [tickers_to_try]

    for t in tickers_to_try:
        try:
            tmp = yf.download(
                t,
                interval=interval,
                period=period,
                progress=False,
                threads=False,
                auto_adjust=False,
            )

            if tmp is None or tmp.empty:
                continue

            # Flatten MultiIndex if present
            if hasattr(tmp.columns, "levels"):
                tmp.columns = [c[0] if isinstance(c, tuple) else c for c in tmp.columns]

            # Ensure numeric OHLC
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                if col in tmp.columns:
                    tmp[col] = pd.to_numeric(tmp[col], errors="coerce")

            tmp = tmp.rename(
                columns={
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                }
            )

            keep = [c for c in ["open", "high", "low", "close", "volume"] if c in tmp.columns]
            tmp = tmp[keep].dropna()

            if tmp.empty:
                continue

            tmp.attrs["used_ticker"] = t
            tmp.attrs["provider"] = "yfinance"
            return tmp

        except Exception:
            continue

    return pd.DataFrame()


@st.cache_data(ttl=120, show_spinner=False)
def fetch_ohlc(symbol: str, interval: str = "15m", period: str = "5d") -> pd.DataFrame:
    """
    Fetch OHLC using provider cascade:
      1) Finnhub (if FINNHUB_API_KEY configured)
      2) OANDA (if OANDA_API_TOKEN configured)
      3) yfinance fallback

    Always returns a DataFrame (possibly empty) with columns:
    open/high/low/close/volume.
    """
    for fetcher in (_fetch_finnhub_ohlc, _fetch_oanda_ohlc, _fetch_yfinance_ohlc):
        df = fetcher(symbol, interval, period)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df

    # Never return None (prevents app-side None bugs)
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
