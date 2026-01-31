import yfinance as yf
import pandas as pd
import streamlit as st

# Map our internal symbols -> Yahoo tickers
YF_MAP = {
    # FX majors
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "USDCHF": "CHF=X",
    "AUDUSD": "AUDUSD=X",
    "NZDUSD": "NZDUSD=X",
    "USDCAD": "CAD=X",

    # FX secondary
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "EURGBP": "EURGBP=X",
    "AUDJPY": "AUDJPY=X",
    "CADJPY": "CADJPY=X",

    # Commodities
    "XAUUSD": "XAUUSD=X",
    "XAGUSD": "XAGUSD=X",
    "WTI": "CL=F",

    # Indices
    "US30": "^DJI",
    "US100": "^NDX",
    "US500": "^GSPC",
}

# Fallbacks for symbols that fail regionally
YF_FALLBACKS = {
    "XAUUSD": ["XAUUSD=X", "GC=F"],
    "XAGUSD": ["XAGUSD=X", "SI=F"],
}


@st.cache_data(ttl=300)
def fetch_ohlc(symbol: str, interval: str = "15m", period: str = "5d") -> pd.DataFrame:
    yf_ticker = YF_MAP.get(symbol, symbol)
    tickers_to_try = YF_FALLBACKS.get(symbol, [yf_ticker])

    df = None
    used_ticker = None

    for t in tickers_to_try:
        try:
            tmp = yf.download(
                t,
                interval=interval,
                period=period,
                progress=False,
                threads=False,
            )
        except Exception:
            tmp = None

        if tmp is not None and not tmp.empty:
            df = tmp
            used_ticker = t
            break

    if df is None or df.empty:
        return pd.DataFrame()

    # Flatten columns if needed
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    # Ensure numeric
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )

    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep].dropna()

    try:
        df.attrs["used_ticker"] = used_ticker
    except Exception:
        pass

    return df
