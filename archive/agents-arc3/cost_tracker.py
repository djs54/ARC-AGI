from dataclasses import dataclass
from typing import Dict, Optional

@dataclass
class CostTracker:
    """B180: Accumulates token usage and computes dollar cost per puzzle."""
    model_name: str
    input_price_per_m: float = 0.0
    output_price_per_m: float = 0.0
    budget_usd: float = float('inf')

    _tokens_in: int = 0
    _tokens_out: int = 0

    def record(self, tokens_in: int, tokens_out: int):
        try:
            self._tokens_in += int(tokens_in)
            self._tokens_out += int(tokens_out)
        except (TypeError, ValueError):
            pass

    @property
    def total_cost_usd(self) -> float:
        try:
            input_price = float(self.input_price_per_m)
            output_price = float(self.output_price_per_m)
            
            tin = 0
            try:
                tin = int(self._tokens_in)
            except (TypeError, ValueError):
                pass
                
            tout = 0
            try:
                tout = int(self._tokens_out)
            except (TypeError, ValueError):
                pass
                
            return (tin * input_price + tout * output_price) / 1_000_000
        except (TypeError, ValueError):
            return 0.0

    @property
    def budget_exhausted(self) -> bool:
        try:
            return self.total_cost_usd >= float(self.budget_usd)
        except (TypeError, ValueError):
            return False

    def summary(self) -> dict:
        return {
            "tokens_in": self._tokens_in,
            "tokens_out": self._tokens_out,
            "cost_usd": self.total_cost_usd,
            "budget_usd": self.budget_usd,
            "budget_exhausted": self.budget_exhausted,
            "model": self.model_name
        }
