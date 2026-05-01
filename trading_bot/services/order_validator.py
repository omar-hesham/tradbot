"""
Order Validator
===============
Validates AI trade recommendations against real Binance exchange rules
(LOT_SIZE, MIN_NOTIONAL, PRICE_FILTER) sourced from the exchange info cache.
Returns adjusted price/quantity values ready for order placement.
"""

import logging
from typing import Tuple
from exchange.exchange_cache import get_symbol_constraints, round_to_step

logger = logging.getLogger(__name__)


async def validate_exchange_rules(
    symbol: str, price: float, quantity: float
) -> Tuple[bool, str, float, float]:
    """
    Validates and adjusts an order against live Binance exchange constraints.

    Returns: (is_valid, reason, adjusted_price, adjusted_quantity)
    """
    constraints = await get_symbol_constraints(symbol)

    if constraints is None:
        # Symbol not in cache — could be delisted or cache missed; apply basic fallback
        logger.warning(f"No exchange constraints found for {symbol}, applying fallback validation.")
        adjusted_price = round(price, 4)
        adjusted_quantity = round(quantity, 6)
        if adjusted_price * adjusted_quantity < 10.0:
            return False, f"Order notional ${adjusted_price * adjusted_quantity:.2f} below $10 fallback MIN_NOTIONAL.", price, quantity
        return True, "Fallback: no exchange info cached", adjusted_price, adjusted_quantity

    if constraints.get("status") != "TRADING":
        return False, f"{symbol} is not currently in TRADING status on Binance.", price, quantity

    step_size = constraints["step_size"]
    tick_size = constraints["tick_size"]
    min_qty = constraints["min_qty"]
    max_qty = constraints["max_qty"]
    min_notional = constraints["min_notional"]

    # 1. Adjust quantity to valid step_size
    adjusted_quantity = round_to_step(quantity, step_size)
    if adjusted_quantity <= 0:
        return False, f"Quantity {quantity} rounds to 0 with step_size {step_size}.", price, quantity

    # 2. Adjust price to valid tick_size
    adjusted_price = round_to_step(price, tick_size) if tick_size > 0 else round(price, 4)

    # 3. Check min quantity
    if adjusted_quantity < min_qty:
        return (
            False,
            f"Adjusted quantity {adjusted_quantity} below LOT_SIZE minQty {min_qty} for {symbol}.",
            price,
            quantity,
        )

    # 4. Check max quantity
    if adjusted_quantity > max_qty:
        return (
            False,
            f"Adjusted quantity {adjusted_quantity} exceeds LOT_SIZE maxQty {max_qty} for {symbol}.",
            price,
            quantity,
        )

    # 5. Check MIN_NOTIONAL
    notional = adjusted_price * adjusted_quantity
    if notional < min_notional:
        return (
            False,
            f"Order notional ${notional:.2f} below MIN_NOTIONAL ${min_notional:.2f} for {symbol}.",
            price,
            quantity,
        )

    logger.debug(
        f"[OrderValidator] {symbol} VALID: qty={adjusted_quantity} price={adjusted_price} notional=${notional:.2f}"
    )
    return True, "Valid", adjusted_price, adjusted_quantity
