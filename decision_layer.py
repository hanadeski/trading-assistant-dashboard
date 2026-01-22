from typing import Dict, List
from engine.scoring import decide_from_factors, Decision

def run_decisions(profiles: List, factors_by_symbol: Dict[str, Dict]) -> List[Decision]:
    return [decide_from_factors(p.symbol, p, factors_by_symbol.get(p.symbol, {})) for p in profiles]
