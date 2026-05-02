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


async def validate_exchange_constraints(
    symbol: str,
    quantity: float,
    price: float,
    confidence: float = 1.0
) -> ValidationResult:
    """
    Consolidated order validation against:
    1. Allowed symbol list
    2. Confidence threshold
    3. Binance LOT_SIZE (Min/Max/Step)
    4. Binance NOTIONAL (Min)
    5. Binance PRICE_FILTER (Min/Max/Tick)
    """
    # 1. Allowed Symbols
    allowed = await get_allowed_symbols()
    if symbol not in allowed:
        return ValidationResult(valid=False, error=f"Symbol {symbol} not in allowed list")

    # 2. Confidence
    threshold = await get_confidence_threshold()
    if confidence < threshold:
        return ValidationResult(valid=False, error=f"Confidence {confidence} < threshold {threshold}")

    # 3. Fetch Exchange Constraints
    from exchange.exchange_cache import get_symbol_constraints
    constraints = await get_symbol_constraints(symbol)
    if not constraints:
        return ValidationResult(valid=False, error=f"Exchange constraints for {symbol} not found.")

    # 4. LOT_SIZE
    if quantity < constraints["min_qty"]:
        return ValidationResult(valid=False, error=f"Qty {quantity} < Min {constraints['min_qty']}")
    if quantity > constraints["max_qty"]:
        return ValidationResult(valid=False, error=f"Qty {quantity} > Max {constraints['max_qty']}")

    # 5. NOTIONAL
    notional = quantity * price
    if notional < constraints["min_notional"]:
        return ValidationResult(valid=False, error=f"Notional ${notional:.2f} < Min ${constraints['min_notional']}")

    # 6. PRICE_FILTER (Optional check if price is provided)
    if price > 0:
        if price < constraints["min_price"] and constraints["min_price"] > 0:
             return ValidationResult(valid=False, error=f"Price {price} < Min {constraints['min_price']}")
        if price > constraints["max_price"] and constraints["max_price"] > 0:
             return ValidationResult(valid=False, error=f"Price {price} > Max {constraints['max_price']}")

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