from typing import Optional, List, Dict, Any
from pydantic import BaseModel

class BinanceKeysRequest(BaseModel):
    api_key: str
    api_secret: str

class CMCKeyRequest(BaseModel):
    api_key: str

class AIConfigRequest(BaseModel):
    provider: str
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None   # for Openclaw / Ollama

class StrategyRequest(BaseModel):
    allowed_symbols: List[str]
    max_trade_usd: float
    max_open_trades: int
    confidence_threshold: float
    stop_loss_pct: float
    take_profit_pct: float
    paper_trading: bool
    trading_interval_seconds: int
    profiles: Optional[Dict[str, Any]] = None
    daily_loss_limit_usd: Optional[float] = 0.0


class SettingsStatusResponse(BaseModel):
    binance_configured: bool
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None


class TradingStatusResponse(BaseModel):
    running: bool
    mode: str
    open_positions: int
    unrealized_pnl: float
    realized_pnl: float
    total_pnl: float


class ManualOrderRequest(BaseModel):
    symbol: str
    side: str          # "BUY" or "SELL"
    quantity: float
    paper_trading: bool = True
