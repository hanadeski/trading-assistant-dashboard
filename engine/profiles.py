from dataclasses import dataclass

@dataclass(frozen=True)
class AssetProfile:
    symbol: str
    display: str
    asset_class: str
    volatility: str
    news_sensitivity: str
    aggression_default: str
    rr_min: float
    certified_rr_min: float

DEFAULT_PROFILES = [
    # FX Majors
    AssetProfile("EURUSD", "EUR/USD", "fx", "medium", "medium", "balanced", rr_min=1.8, certified_rr_min=1.3),
    AssetProfile("GBPUSD", "GBP/USD", "fx", "medium", "medium", "balanced", rr_min=1.8, certified_rr_min=1.3),
    AssetProfile("USDJPY", "USD/JPY", "fx", "medium", "medium", "balanced", rr_min=1.8, certified_rr_min=1.3),
    AssetProfile("USDCHF", "USD/CHF", "fx", "low", "medium", "balanced", rr_min=1.6, certified_rr_min=1.2),
    AssetProfile("AUDUSD", "AUD/USD", "fx", "medium", "medium", "balanced", rr_min=1.8, certified_rr_min=1.3),
    AssetProfile("NZDUSD", "NZD/USD", "fx", "medium", "medium", "balanced", rr_min=1.8, certified_rr_min=1.3),
    AssetProfile("USDCAD", "USD/CAD", "fx", "medium", "high", "balanced", rr_min=1.8, certified_rr_min=1.3),

    # FX Secondary
    AssetProfile("EURJPY", "EUR/JPY", "fx", "high", "medium", "conservative", rr_min=2.0, certified_rr_min=1.5),
    AssetProfile("GBPJPY", "GBP/JPY", "fx", "high", "medium", "conservative", rr_min=2.0, certified_rr_min=1.5),
    AssetProfile("EURGBP", "EUR/GBP", "fx", "low", "low", "balanced", rr_min=1.6, certified_rr_min=1.2),
    AssetProfile("AUDJPY", "AUD/JPY", "fx", "high", "medium", "conservative", rr_min=2.0, certified_rr_min=1.5),
    AssetProfile("CADJPY", "CAD/JPY", "fx", "high", "medium", "conservative", rr_min=2.0, certified_rr_min=1.5),

    # Commodities
    AssetProfile("XAUUSD", "XAU/USD (Gold)", "commodity", "high", "high", "conservative", rr_min=2.5, certified_rr_min=2.0),
    AssetProfile("XAGUSD", "XAG/USD (Silver)", "commodity", "high", "high", "conservative", rr_min=2.5, certified_rr_min=2.0),
    AssetProfile("WTI", "WTI Crude", "commodity", "high", "high", "conservative", rr_min=2.5, certified_rr_min=2.0),

    # Indices
    AssetProfile("US30", "US30 (Dow)", "index", "high", "high", "conservative", rr_min=2.2, certified_rr_min=1.8),
    AssetProfile("US100", "US100 (Nasdaq)", "index", "high", "high", "conservative", rr_min=2.2, certified_rr_min=1.8),
    AssetProfile("US500", "US500 (S&P 500)", "index", "medium", "high", "balanced", rr_min=2.0, certified_rr_min=1.6),
]

def get_profiles():
    return DEFAULT_PROFILES
