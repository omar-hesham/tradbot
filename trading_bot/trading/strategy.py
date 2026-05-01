import json
from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class StrategyProfile:
    enabled: bool
    max_trade_usd: float
    min_confidence: float
    max_holding_minutes: int
    stop_loss_pct: float
    take_profit_pct: float


@dataclass
class Strategy:
    allowed_symbols: list[str]
    max_trade_usd: float
    max_open_trades: int
    confidence_threshold: float
    stop_loss_pct: float
    take_profit_pct: float
    paper_trading: bool
    trading_interval_seconds: int
    max_drawdown_usd: float = 250.0
    profiles: dict[str, StrategyProfile] = None


DEFAULT_PROFILES = {
    "hustle": StrategyProfile(enabled=True, max_trade_usd=100000000.0, min_confidence=0.1, max_holding_minutes=180, stop_loss_pct=99.0, take_profit_pct=1000.0),
    "swing": StrategyProfile(enabled=True, max_trade_usd=100000000.0, min_confidence=0.1, max_holding_minutes=4320, stop_loss_pct=99.0, take_profit_pct=1000.0),
    "macro": StrategyProfile(enabled=True, max_trade_usd=100000000.0, min_confidence=0.1, max_holding_minutes=43200, stop_loss_pct=99.0, take_profit_pct=1000.0),
    "moonshot": StrategyProfile(enabled=True, max_trade_usd=100000000.0, min_confidence=0.1, max_holding_minutes=60, stop_loss_pct=99.0, take_profit_pct=10000.0)
}

DEFAULT_STRATEGY = Strategy(
    allowed_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    max_trade_usd=100000000.0,
    max_open_trades=1000,
    confidence_threshold=0.1,
    stop_loss_pct=99.0,
    take_profit_pct=1000.0,
    paper_trading=True,
    trading_interval_seconds=60,
    max_drawdown_usd=250.0,
    profiles=DEFAULT_PROFILES
)


async def get_strategy() -> Strategy:
    from sqlalchemy import select
    from core.database import get_session
    from core.models import BotConfig

    async for session in get_session():
        result = await session.execute(select(BotConfig))
        configs = result.scalars().all()

        config_dict = {c.key: c.value for c in configs}
        
        profiles_raw = config_dict.get("strategy_profiles", None)
        profiles = {}
        if profiles_raw:
            try:
                parsed = json.loads(profiles_raw)
                for k, v in parsed.items():
                    profiles[k] = StrategyProfile(**v)
            except Exception:
                profiles = DEFAULT_PROFILES
        else:
            profiles = DEFAULT_PROFILES

        return Strategy(
            allowed_symbols=config_dict.get("allowed_symbols", ",".join(DEFAULT_STRATEGY.allowed_symbols)).split(","),
            max_trade_usd=float(config_dict.get("max_trade_usd", DEFAULT_STRATEGY.max_trade_usd)),
            max_open_trades=int(config_dict.get("max_open_trades", DEFAULT_STRATEGY.max_open_trades)),
            confidence_threshold=float(config_dict.get("confidence_threshold", DEFAULT_STRATEGY.confidence_threshold)),
            stop_loss_pct=float(config_dict.get("stop_loss_pct", DEFAULT_STRATEGY.stop_loss_pct)),
            take_profit_pct=float(config_dict.get("take_profit_pct", DEFAULT_STRATEGY.take_profit_pct)),
            paper_trading=config_dict.get("paper_trading", "true").lower() == "true",
            trading_interval_seconds=int(config_dict.get("trading_interval_seconds", DEFAULT_STRATEGY.trading_interval_seconds)),
            max_drawdown_usd=float(config_dict.get("max_drawdown_usd", DEFAULT_STRATEGY.max_drawdown_usd)),
            profiles=profiles
        )
