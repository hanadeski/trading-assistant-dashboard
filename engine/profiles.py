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

# =========================================================
# PREMIUM-MODE PROFILES (CURATED + STRICT RR)
# =========================================================

DEFAULT_PROFILES = [
    # FX Majors (premium RR)
    AssetProfile("EURUSD", "EUR/USD", "fx", "medium", "medium", "balanced", rr_min=2.5, certified_rr_min=2.5),
    AssetProfile("GBPUSD", "GBP/USD", "fx", "medium", "medium", "balanced", rr_min=2.5, certified_rr_min=2.5),
    AssetProfile("USDJPY", "USD/JPY", "fx", "medium", "medium", "balanced", rr_min=2.5, certified_rr_min=2.5),
    AssetProfile("USDCHF", "USD/CHF", "fx", "low", "medium", "balanced", rr_min=2.5, certified_rr_min=2.5),
    AssetProfile("AUDUSD", "AUD/USD", "fx", "medium", "medium", "balanced", rr_min=2.5, certified_rr_min=2.5),
    AssetProfile("NZDUSD", "NZD/USD", "fx", "medium", "medium", "balanced", rr_min=2.5, certified_rr_min=2.5),
    AssetProfile("USDCAD", "USD/CAD", "fx", "medium", "high", "balanced", rr_min=2.5, certified_rr_min=2.5),

    # Metals
    AssetProfile("XAUUSD", "XAU/USD (Gold)", "commodity", "high", "high", "conservative", rr_min=2.5, certified_rr_min=2.5),
    AssetProfile("XAGUSD", "XAG/USD (Silver)", "commodity", "high", "high", "conservative", rr_min=2.5, certified_rr_min=2.5),

    # Energy
    AssetProfile("WTI", "WTI Crude", "commodity", "high", "high", "conservative", rr_min=2.5, certified_rr_min=2.5),

    # Indices
    AssetProfile("US30", "US30 (Dow)", "index", "high", "high", "conservative", rr_min=2.5, certified_rr_min=2.5),
    AssetProfile("US100", "US100 (Nasdaq)", "index", "high", "high", "conservative", rr_min=2.5, certified_rr_min=2.5),
]

def get_profiles():
    return DEFAULT_PROFILES
