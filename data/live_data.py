import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st
import yfinance as yf

# -----------------------
# Optional MT5 support (ICMarkets)
# -----------------------
try:
    import MetaTrader5 as mt5  # noqa
except Exception:
    mt5 = None

# -----------------------
# Optional TradingView support (better chart alignment than Yahoo)
# -----------------------
try:
    from tvDatafeed import TvDatafeed, Interval  # type: ignore
    _TV = TvDatafeed()  # no login needed for most symbols
except Exception:
    TvDatafeed = None
    Interval = None
    _TV = None


# -----------------------
# Secrets helpers
# -----------------------
def _read_secret(name: str) -> str:
    val = os.getenv(name, "").strip()
    if val:
        return val
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def _read_secret_json(name: str):
    raw = _read_secret(name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


# -----------------------
# ICMarkets / MT5 symbol mapping
# -----------------------
# Override any symbol explicitly:
# IC_SYMBOL_MAP_JSON='{"XAUUSD":"XAUUSD.", "US100":"NAS100.", "UK100":"UK100."}'
IC_SYMBOL_MAP = _read_secret_json("IC_SYMBOL_MAP_JSON") or {}

# Or suffix/prefix approach:
# IC_SYMBOL_SUFFIX="."  IC_SYMBOL_PREFIX=""
IC_SYMBOL_SUFFIX = _read_secret("IC_SYMBOL_SUFFIX")
IC_SYMBOL_PREFIX = _read_secret("IC_SYMBOL_PREFIX")


def _icmarkets_symbol(symbol: str) -> str:
    if symbol in IC_SYMBOL_MAP:
        return str(IC_SYMBOL_MAP[symbol])
    return f"{IC_SYMBOL_PREFIX}{symbol}{IC_SYMBOL_SUFFIX}"


# -----------------------
# MT5 timeframe mapping
# -----------------------
MT5_TF = {
    "1m": getattr(mt5, "TIMEFRAME_M1", None),
    "5m": getattr(mt5, "TIMEFRAME_M5", None),
    "15m": getattr(mt5, "TIMEFRAME_M15", None),
    "30m": getattr(mt5, "TIMEFRAME_M30", None),
    "1h": getattr(mt5, "TIMEFRAME_H1", None),
    "4h": getattr(mt5, "TIMEFRAME_H4", None),
    "1d": getattr(mt5, "TIMEFRAME_D1", None),
}


def _interval_minutes(interval: str) -> int:
    if interval.endswith("m"):
        return max(1, int(interval[:-1]))
    if interval.endswith("h"):
        return max(1, int(interval[:-1]) * 60)
    if interval.endswith("d"):
        return max(1, int(interval[:-1]) * 1440)
    return 15


def _period_to_count(period: str, interval: str, *, lo: int = 120, hi: int = 5000) -> int:
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
    return max(lo, min(hi, bars))


# -----------------------
# MT5 fetch
# -----------------------
def _mt5_login_ok() -> bool:
    if mt5 is None:
        return False
    login = _read_secret("MT5_LOGIN")
    password = _read_secret("MT5_PASSWORD")
    server = _read_secret("MT5_SERVER")
    return bool(login and password and server)


def _mt5_init() -> bool:
    """
    MT5 only works if the Streamlit runtime is on the same machine as the MT5 terminal.
    On Streamlit Cloud this is typically NOT the case, so this will fail and we fall back.
    """
    if mt5 is None:
        return False

    if st.session_state.get("_mt5_inited") is True:
        return True
    if st.session_state.get("_mt5_inited") is False:
        return False

    login = _read_secret("MT5_LOGIN")
    password = _read_secret("MT5_PASSWORD")
    server = _read_secret("MT5_SERVER")
    path = _read_secret("MT5_PATH")  # optional

    try:
        ok = mt5.initialize(path) if path else mt5.initialize()
        if not ok:
            st.session_state["_mt5_inited"] = False
            return False

        ok2 = mt5.login(int(login), password=password, server=server)
        st.session_state["_mt5_inited"] = bool(ok2)
        return bool(ok2)
    except Exception:
        st.session_state["_mt5_inited"] = False
        return False


def _fetch_mt5_ohlc(symbol: str, interval: str, period: str) -> pd.DataFrame:
    if not _mt5_login_ok():
        return pd.DataFrame()
    if not _mt5_init():
        return pd.DataFrame()

    tf = MT5_TF.get(interval)
    if tf is None:
        return pd.DataFrame()

    sym = _icmarkets_symbol(symbol)

    try:
        if not mt5.symbol_select(sym, True):
            if not mt5.symbol_select(symbol, True):
                return pd.DataFrame()
            sym = symbol
    except Exception:
        return pd.DataFrame()

    bars = _period_to_count(period, interval, lo=200, hi=8000)

    try:
        rates = mt5.copy_rates_from_pos(sym, tf, 0, bars)
    except Exception:
        rates = None

    if rates is None or len(rates) == 0:
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    if "time" not in df.columns:
        return pd.DataFrame()

    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("time").sort_index()

    if "tick_volume" in df.columns and "volume" not in df.columns:
        df["volume"] = df["tick_volume"]

    out = df[["open", "high", "low", "close", "volume"]].dropna()
    if out.empty:
        return pd.DataFrame()

    out.attrs["provider"] = "mt5"
    out.attrs["used_ticker"] = sym
    return out


# -----------------------
# TradingView mapping + fetch
# -----------------------
# You can override this whole map via secrets:
# TV_SYMBOL_MAP_JSON='{"XAUUSD":{"symbol":"XAUUSD","exchange":"OANDA"}}'
TV_SYMBOL_MAP = _read_secret_json("TV_SYMBOL_MAP_JSON") or {}

# Default TradingView map (covers your app universe + common additions)
# NOTE: exchange names here are TradingView "sources" (OANDA/FXCM/FOREXCOM/ICMARKETS etc.)
DEFAULT_TV_MAP = {
    # FX Majors
    "EURUSD": {"symbol": "EURUSD", "exchange": "OANDA"},
    "GBPUSD": {"symbol": "GBPUSD", "exchange": "OANDA"},
    "USDJPY": {"symbol": "USDJPY", "exchange": "OANDA"},
    "USDCHF": {"symbol": "USDCHF", "exchange": "OANDA"},
    "AUDUSD": {"symbol": "AUDUSD", "exchange": "OANDA"},
    "NZDUSD": {"symbol": "NZDUSD", "exchange": "OANDA"},
    "USDCAD": {"symbol": "USDCAD", "exchange": "OANDA"},

    # FX Crosses (common)
    "EURJPY": {"symbol": "EURJPY", "exchange": "OANDA"},
    "GBPJPY": {"symbol": "GBPJPY", "exchange": "OANDA"},
    "EURGBP": {"symbol": "EURGBP", "exchange": "OANDA"},
    "AUDJPY": {"symbol": "AUDJPY", "exchange": "OANDA"},
    "CADJPY": {"symbol": "CADJPY", "exchange": "OANDA"},

    # Metals / Energy (CFD-style spot symbols on TV feeds)
    "XAUUSD": {"symbol": "XAUUSD", "exchange": "OANDA"},
    "XAGUSD": {"symbol": "XAGUSD", "exchange": "OANDA"},
    "WTI": {"symbol": "WTICOUSD", "exchange": "TVC"},  # common TV symbol (works often)

    # Indices CFDs (names vary by feed; these are common)
    "US30": {"symbol": "US30", "exchange": "OANDA"},
    "US100": {"symbol": "NAS100", "exchange": "OANDA"},
    "US500": {"symbol": "SPX500", "exchange": "OANDA"},
    "UK100": {"symbol": "UK100", "exchange": "OANDA"},
}

# Map interval string -> tvdatafeed Interval
_TV_INTERVAL = {
    "1m": getattr(Interval, "in_1_minute", None),
    "5m": getattr(Interval, "in_5_minute", None),
    "15m": getattr(Interval, "in_15_minute", None),
    "30m": getattr(Interval, "in_30_minute", None),
    "1h": getattr(Interval, "in_1_hour", None),
    "4h": getattr(Interval, "in_4_hour", None),
    "1d": getattr(Interval, "in_daily", None),
}


def _tv_lookup(symbol: str):
    if symbol in TV_SYMBOL_MAP and isinstance(TV_SYMBOL_MAP[symbol], dict):
        return TV_SYMBOL_MAP[symbol]
    return DEFAULT_TV_MAP.get(symbol)


def _fetch_tradingview_ohlc(symbol: str, interval: str, period: str) -> pd.DataFrame:
    if _TV is None:
        return pd.DataFrame()

    cfg = _tv_lookup(symbol)
    if not cfg:
        return pd.DataFrame()

    tv_symbol = str(cfg.get("symbol", "")).strip()
    tv_exchange = str(cfg.get("exchange", "")).strip()
    if not tv_symbol or not tv_exchange:
        return pd.DataFrame()

    tv_interval = _TV_INTERVAL.get(interval)
    if not tv_interval:
        return pd.DataFrame()

    # bars: bounded
    n_bars = _period_to_count(period, interval, lo=200, hi=1200)

    try:
        df = _TV.get_hist(symbol=tv_symbol, exchange=tv_exchange, interval=tv_interval, n_bars=n_bars)
    except Exception:
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    # tvdatafeed returns index as datetime; columns are usually: open, high, low, close, volume
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    if not keep or any(c not in df.columns for c in ["open", "high", "low", "close"]):
        return pd.DataFrame()

    out = df[keep].copy()
    out = out.dropna()
    if out.empty:
        return pd.DataFrame()

    out.attrs["provider"] = "tradingview"
    out.attrs["used_ticker"] = f"{tv_exchange}:{tv_symbol}"
    return out


# -----------------------
# Finnhub (optional)
# -----------------------
INTERVAL_TO_FINNHUB = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "240",
    "1d": "D",
}

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

    host = (_read_secret("FINNHUB_API_HOST") or "https://finnhub.io").rstrip("/")
    params = urlencode({"symbol": finnhub_symbol, "resolution": resolution, "from": from_ts, "to": to_ts, "token": token})
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

    df.attrs["provider"] = "finnhub"
    df.attrs["used_ticker"] = finnhub_symbol
    return df


# -----------------------
# OANDA (optional)
# -----------------------
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
    "UK100": "UK100_GBP",  # may vary by account; used as fallback only
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

def _fetch_oanda_ohlc(symbol: str, interval: str, period: str) -> pd.DataFrame:
    token = _read_secret("OANDA_API_TOKEN")
    if not token:
        return pd.DataFrame()

    instrument = OANDA_MAP.get(symbol)
    granularity = INTERVAL_TO_OANDA.get(interval)
    if not instrument or not granularity:
        return pd.DataFrame()

    count = _period_to_count(period, interval)
    host = (_read_secret("OANDA_API_HOST") or "https://api-fxpractice.oanda.com").rstrip("/")
    url = f"{host}/v3/instruments/{instrument}/candles?price=M&granularity={granularity}&count={count}"

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
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    if df.empty:
        return pd.DataFrame()

    df.attrs["provider"] = "oanda"
    df.attrs["used_ticker"] = instrument
    return df


# -----------------------
# yfinance fallback (last resort)
# -----------------------
YF_MAP = {
    # FX Majors
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "USDCHF": "CHF=X",
    "AUDUSD": "AUDUSD=X",
    "NZDUSD": "NZDUSD=X",
    "USDCAD": "CAD=X",

    # FX Crosses
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "EURGBP": "EURGBP=X",
    "AUDJPY": "AUDJPY=X",
    "CADJPY": "CADJPY=X",

    # Commodities (NOT broker-spot)
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "WTI": "CL=F",

    # Indices approximations
    "US30": "^DJI",
    "US100": "^NDX",
    "US500": "^GSPC",
    "UK100": "^FTSE",
}

YF_FALLBACKS = {
    "XAUUSD": ["GC=F", "XAUUSD=X"],
    "XAGUSD": ["SI=F", "XAGUSD=X"],
}

def _fetch_yfinance_ohlc(symbol: str, interval: str, period: str) -> pd.DataFrame:
    yf_ticker = YF_MAP.get(symbol, symbol)
    tickers_to_try = YF_FALLBACKS.get(symbol) or [yf_ticker]
    if isinstance(tickers_to_try, str):
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

            if hasattr(tmp.columns, "levels"):
                tmp.columns = [c[0] if isinstance(c, tuple) else c for c in tmp.columns]

            for col in ["Open", "High", "Low", "Close", "Volume"]:
                if col in tmp.columns:
                    tmp[col] = pd.to_numeric(tmp[col], errors="coerce")

            tmp = tmp.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
            keep = [c for c in ["open", "high", "low", "close", "volume"] if c in tmp.columns]
            tmp = tmp[keep].dropna()
            if tmp.empty:
                continue

            tmp.attrs["provider"] = "yfinance"
            tmp.attrs["used_ticker"] = t
            return tmp
        except Exception:
            continue

    return pd.DataFrame()


# -----------------------
# Main entry: provider cascade
# -----------------------
@st.cache_data(ttl=60, show_spinner=False)
def fetch_ohlc(symbol: str, interval: str = "15m", period: str = "5d") -> pd.DataFrame:
    """
    Provider cascade (best alignment first):
      0) MT5 (ICMarkets) ONLY if running on same machine + MetaTrader5 installed + credentials work
      1) TradingView (tvdatafeed) best chart alignment on Streamlit Cloud
      2) Finnhub (if configured)
      3) OANDA (if configured)
      4) yfinance last resort

    Returns DataFrame with columns: open/high/low/close/volume (may be empty).
    """
    fetchers = []

    # Prefer MT5 if actually usable
    if _mt5_login_ok() and mt5 is not None:
        fetchers.append(_fetch_mt5_ohlc)

    # Prefer TradingView next (Streamlit Cloud friendly)
    if _TV is not None:
        fetchers.append(_fetch_tradingview_ohlc)

    # Then your old providers
    fetchers.extend([_fetch_finnhub_ohlc, _fetch_oanda_ohlc])

    for fetcher in fetchers:
        df = fetcher(symbol, interval, period)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df

    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
