import random
from typing import Dict

def mock_factors_for_symbols(symbols) -> Dict[str, Dict]:
    out = {}
    for s in symbols:
        bias = random.choice(["bullish", "bearish", "neutral"])
        session_boost = random.choice([0.2, 0.5, 0.8, 1.0])
        structure_ok = random.choice([True, True, False])
        liquidity_ok = random.choice([True, False, False])
        certified = random.choice([False, False, True])
        rr = round(random.uniform(1.1, 4.2), 2)
        news_risk = random.choice(["none", "none", "near", "aligned", "against"])
        volatility_risk = random.choice(["normal", "normal", "high", "extreme"])
        out[s] = {
            "bias": bias,
            "session_boost": session_boost,
            "structure_ok": structure_ok,
            "liquidity_ok": liquidity_ok,
            "certified": certified,
            "rr": rr,
            "news_risk": news_risk,
            "volatility_risk": volatility_risk,
            "entry": "TBD",
            "stop": "TBD",
            "tp1": "TBD",
            "tp2": "TBD",
        }
    return out
