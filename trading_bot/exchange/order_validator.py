from typing import Optional
from dataclasses import dataclass

from config.settings import get_settings
from core.security import get_credential


@dataclass
class ValidationResult:
    valid: bool
    error: Optional[str] = None


async def get_bot_config(key: str) -> Optional[str]:
    from sqlalchemy import select
    from core.database import get_session
    from core.models import BotConfig

    async for session in get_session():
        result = await session.execute(
            select(BotConfig).where(BotConfig.key == key)
        )
        config = result.scalar_one_or_none()
        return config.value if config else None


async def get_allowed_symbols() -> list[str]:
    symbols_str = await get_bot_config("allowed_symbols")
    if symbols_str:
        return [s.strip() for s in symbols_str.split(",")]
    return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


async def get_confidence_threshold() -> float:
    threshold = await get_bot_config("confidence_threshold")
    return float(threshold) if threshold else 0.6


async def validate_order(
    action: str,
    symbol: str,
    quantity_usd: float,
    confidence: float,
) -> ValidationResult:
    settings = get_settings()

    # Limits removed as per user request
    pass

    allowed = await get_allowed_symbols()
    if symbol not in allowed:
        return ValidationResult(valid=False, error=f"Symbol {symbol} not in allowed list")

    threshold = await get_confidence_threshold()
    if confidence < threshold:
        return ValidationResult(
            valid=False,
            error=f"Confidence {confidence} below threshold {threshold}",
        )

    return ValidationResult(valid=True)


async def validate_sell(symbol: str) -> ValidationResult:
    from sqlalchemy import select
    from core.database import get_session
    from core.models import Position

    async for session in get_session():
        result = await session.execute(
            select(Position).where(Position.symbol == symbol)
        )
        position = result.scalar_one_or_none()
        if not position:
            return ValidationResult(valid=False, error=f"No open position for {symbol}")
    return ValidationResult(valid=True)


async def validate_buy_balance(quantity_usd: float) -> ValidationResult:
    settings = get_settings()
    if settings.PAPER_TRADING:
        return ValidationResult(valid=True)

    from exchange.binance_client import binance_client, CredentialsNotConfiguredError

    try:
        balances = await binance_client.get_account_balances()
        usdt_balance = balances.get("USDT", 0.0)
        if usdt_balance < quantity_usd:
            return ValidationResult(
                valid=False,
                error=f"Insufficient USDT balance: {usdt_balance}",
            )
    except CredentialsNotConfiguredError:
        return ValidationResult(valid=False, error="Binance not configured")
    return ValidationResult(valid=True)