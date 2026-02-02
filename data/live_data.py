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


@st.cache_data(ttl=300, show_spinner=False)
def fetch_ohlc(symbol: str, interval: str = "15m", period: str = "5d") -> pd.DataFrame:
    """
    Fetch OHLC from Yahoo with robust fallbacks and rate-limit handling.
    Always returns a DataFrame (possibly empty) with columns: open/high/low/close/volume.
    """
    yf_ticker = YF_MAP.get(symbol, symbol)

    # Try primary + fallbacks
    tickers_to_try = YF_FALLBACKS.get(symbol, [yf_ticker])

    last_err = None
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
                last_err = f"No data for {t}"
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
                last_err = f"Empty after cleanup for {t}"
                continue

            # record which ticker worked
            tmp.attrs["used_ticker"] = t
            return tmp

        except Exception as e:
            # Soft-fail on rate limits + any yfinance errors
            last_err = repr(e)
            continue

    # All failed -> return empty DF (caller will use fallback last_good if you wired that)
    return pd.DataFrame()
