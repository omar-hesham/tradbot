from pydantic import BaseModel, Field
from typing import Literal, List

class TradeIntent(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    symbol: str
    current_price: float = 0.0
    suggested_allocation_usd: float = 0.0
    confidence: float = Field(ge=0, le=1)
    strategy: Literal["hustle", "swing", "macro", "moonshot", "short_term"]
    reasoning_summary: str
    entry_conditions: List[str] = []
    risk_factors: List[str] = []
    stop_loss_pct: float = Field(default=2.0, ge=0, le=50)
    take_profit_pct: float = Field(default=5.0, ge=0, le=100)
    max_holding_period_minutes: int = Field(default=0, ge=0)
    should_execute: bool = True
    
    # Hardened Deterministic Fields
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    quantity_type: Literal["BASE", "QUOTE"] = "QUOTE"
    max_slippage_pct: float = Field(default=1.0, ge=0.1, le=5.0)

# Legacy alias to avoid immediate breakage
TradeRecommendation = TradeIntent

