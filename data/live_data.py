import finnhub
import pandas as pd
from datetime import datetime, timedelta
from streamlit import secrets

# Initialize Finnhub client using Streamlit secrets
client = finnhub.Client(api_key=secrets["FINNHUB_API_KEY"])

# Correct Finnhub symbol mapping
FINNHUB_SYMBOL_MAP = {
    # FX (OANDA)
    "EURUSD": "OANDA:EUR_USD",
    "GBPUSD": "OANDA:GBP_USD",
    "USDJPY": "OANDA:USD_JPY",
    "AUDUSD": "OANDA:AUD_USD",
    "NZDUSD": "OANDA:NZD_USD",
    "USDCAD": "OANDA:USD_CAD",
    "USDCHF": "OANDA:USD_CHF",

    # Metals
    "XAUUSD": "OANDA:XAU_USD",
    "XAGUSD": "OANDA:XAG_USD",

    # Oil
    "WTI": "OANDA:WTICO_USD",
    "BRENT": "OANDA:BCO_USD",

    # Indices (use stock candles)
    "US30": "DJI",
    "US100": "NDX",
}

# Map your intervals to Finnhub resolutions
RESOLUTION_MAP = {
    "15m": "15",
    "1h": "60",
}

# Map your periods to number of days
PERIOD_MAP = {
    "5d": 5,
    "1mo": 30,
}

def fetch_ohlc(symbol, interval="15m", period="5d"):
    try:
        resolution = RESOLUTION_MAP[interval]
        days = PERIOD_MAP[period]

        end = int(datetime.utcnow().timestamp())
        start = int((datetime.utcnow() - timedelta(days=days)).timestamp())

        # Convert to Finnhub symbol
        finnhub_symbol = FINNHUB_SYMBOL_MAP.get(symbol, symbol)

        # FX uses forex_candles, indices use stock_candles
        if finnhub_symbol.startswith("OANDA:"):
            data = client.forex_candles(finnhub_symbol, resolution, start, end)
        else:
            data = client.stock_candles(finnhub_symbol, resolution, start, end)

        # Finnhub returns status "no_data" if empty
        if data.get("s") != "ok":
            return None

        df = pd.DataFrame({
            "open": data["o"],
            "high": data["h"],
            "low": data["l"],
            "close": data["c"],
            "volume": data["v"],
            "time": pd.to_datetime(data["t"], unit="s")
        })

        df.set_index("time", inplace=True)
        return df

    except Exception:
        return None
