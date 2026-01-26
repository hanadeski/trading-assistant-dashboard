import yfinance as yf
import pandas as pd
import streamlit as st

# Map our internal symbols -> Yahoo tickers
YF_MAP = {
    # FX Majors
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",        # Yahoo uses JPY=X for USD/JPY (USDJPY=X also often works)
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

    # Commodities
    "XAUUSD": "XAUUSD=X",
    "XAGUSD": "XAGUSD=X",
    "WTI": "CL=F",

    # Indices (approximations)
    "US30": "^DJI",
    "US100": "^NDX",
    "US500": "^GSPC",
}

@st.cache_data(ttl=300)
def fetch_ohlc(symbol: str, interval: str = "15m", period: str = "5d") -> pd.DataFrame:
    yf_ticker = YF_MAP.get(symbol, symbol)

    df = yf.download(
        yf_ticker,
        interval=interval,
        period=period,
        progress=False,
        auto_adjust=False,
        threads=False,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    # Flatten MultiIndex columns if present
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    # Ensure numeric OHLC
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

    df = df[["open", "high", "low", "close", "volume"]].dropna()

    return df
