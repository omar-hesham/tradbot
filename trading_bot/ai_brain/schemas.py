from pydantic import BaseModel, Field
from typing import Literal, List

class TradeRecommendation(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    symbol: str
    current_price: float = 0.0
    suggested_allocation_usd: float = 0.0
    confidence: float = Field(ge=0, le=1)
    strategy: Literal["hustle", "swing", "macro", "moonshot", "short_term"]
    reasoning_summary: str
    entry_conditions: List[str]
    risk_factors: List[str]
    stop_loss_pct: float = Field(ge=0, le=50)
    take_profit_pct: float = Field(ge=0, le=100)
    max_holding_period_minutes: int = Field(ge=0)
    should_execute: bool

