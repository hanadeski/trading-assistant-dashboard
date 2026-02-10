import os
import finnhub
import pandas as pd
from datetime import datetime, timedelta

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

client = finnhub.Client(api_key=FINNHUB_API_KEY)

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

        data = client.forex_candles(symbol, resolution, start, end)

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
