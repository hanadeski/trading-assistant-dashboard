import yfinance as yf
import json
import os
from datetime import timedelta
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

# Map our internal symbols -> Yahoo tickers
YF_MAP = {
    # FX Majors
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "USDCHF": "CHF=X",
    "AUDUSD": "AUDUSD=X",
    "NZDUSD": "NZDUSD=X",
    "USDCAD": "CAD=X",

    # Commodities (premium)
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "WTI": "CL=F",

    # Indices
    "US30": "^DJI",
    "US100": "^NDX",
}

# Fallback tickers
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
    "XAUUSD": "XAU_USD",
    "XAGUSD": "XAG_USD",
    "WTI": "WTICO_USD",
    "US30": "US30_USD",
    "US100": "NAS100_USD",
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

# =========================================================
# OHLC FETCHING
# =========================================================

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


def _fetch_oanda_ohlc(symbol: str, interval: str, period: str) -> pd.DataFrame:
    token = os.getenv("OANDA_API_TOKEN", "").strip()
    if not token:
        return pd.DataFrame()

    instrument = OANDA_MAP.get(symbol)
    granularity = INTERVAL_TO_OANDA.get(interval)
    if not instrument or not granularity:
        return pd.DataFrame()

    count = _period_to_count(period, interval)
    host = os.getenv("OANDA_API_HOST", "https://api-fxpractice.oanda.com").rstrip("/")
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
    except Exception:
        return pd.DataFrame()

    candles = payload.get("candles", [])
    rows = []
    for c in candles:
        if not c.get("complete"):
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

    tickers_to_try = YF_FALLBACKS.get(symbol, [yf_ticker])

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

            if hasattr(tmp.columns, "levels"):
                tmp.columns = [c[0] if isinstance(c, tuple) else c for c in tmp.columns]

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

            tmp = tmp[["open", "high", "low", "close", "volume"]].dropna()

            if tmp.empty:
                continue

            tmp.attrs["used_ticker"] = t
            tmp.attrs["provider"] = "yfinance"
            return tmp

        except Exception:
            continue

    return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_ohlc(symbol: str, interval: str = "15m", period: str = "5d") -> pd.DataFrame:
    oanda_df = _fetch_oanda_ohlc(symbol, interval, period)
    if oanda_df is not None and not oanda_df.empty:
        return oanda_df

    return _fetch_yfinance_ohlc(symbol, interval, period)


# =========================================================
# PREMIUM MODE: CANDLE-CLOSE CONFIRMATION
# =========================================================

def compute_confirmation_factors(df: pd.DataFrame, bias: str) -> dict:
    """
    Adds premium confirmation filters:
    - confirmed_break: last closed candle broke structure
    - atr_displacement_ok: move is meaningful
    - close_direction_ok: candle closed in direction of bias
    """

    if df is None or df.empty or len(df) < 20:
        return {
            "confirmed_break": False,
            "atr_displacement_ok": False,
            "close_direction_ok": False,
        }

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # ATR for displacement
    df["tr"] = (df["high"] - df["low"]).abs()
    atr = df["tr"].rolling(14).mean().iloc[-1]

    # Directional close
    close_direction_ok = (
        (bias == "bullish" and last["close"] > last["open"]) or
        (bias == "bearish" and last["close"] < last["open"])
    )

    # Break confirmation (simple version)
    confirmed_break = (
        (bias == "bullish" and last["close"] > prev["high"]) or
        (bias == "bearish" and last["close"] < prev["low"])
    )

    # ATR displacement
    atr_displacement_ok = abs(last["close"] - prev["close"]) > (0.3 * atr)

    return {
        "confirmed_break": confirmed_break,
        "atr_displacement_ok": bool(atr_displacement_ok),
        "close_direction_ok": bool(close_direction_ok),
    }
