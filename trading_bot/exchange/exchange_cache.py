"""
Exchange Info Cache
===================
Fetches and caches Binance exchangeInfo on startup (and refreshes every hour).
The order_validator reads from this cache to enforce LOT_SIZE, MIN_NOTIONAL,
and PRICE_FILTER constraints without hitting the API on every order.
"""

import asyncio
import logging
import time
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

# In-memory cache: symbol -> parsed filter dict
_exchange_info_cache: Dict[str, Dict[str, Any]] = {}
_last_refresh: float = 0
_REFRESH_INTERVAL_SECONDS = 3600  # refresh every hour


def _parse_symbol_filters(symbol_info: dict) -> dict:
    """Extracts the critical trading constraints from a raw Binance symbol object."""
    filters = {f["filterType"]: f for f in symbol_info.get("filters", [])}
    lot = filters.get("LOT_SIZE", {})
    notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
    price_f = filters.get("PRICE_FILTER", {})

    return {
        "symbol": symbol_info.get("symbol"),
        "status": symbol_info.get("status", "UNKNOWN"),
        "base_asset": symbol_info.get("baseAsset", ""),
        "quote_asset": symbol_info.get("quoteAsset", ""),
        "min_qty": float(lot.get("minQty", "0.00000001")),
        "max_qty": float(lot.get("maxQty", "99999999")),
        "step_size": float(lot.get("stepSize", "0.00000001")),
        "min_notional": float(notional.get("minNotional", "10.0")),
        "min_price": float(price_f.get("minPrice", "0")),
        "max_price": float(price_f.get("maxPrice", "0")),
        "tick_size": float(price_f.get("tickSize", "0.01")),
    }


async def refresh_exchange_info() -> bool:
    """Fetches full exchangeInfo from Binance and rebuilds the cache."""
    global _exchange_info_cache, _last_refresh
    try:
        from exchange.binance_client import binance_client, is_configured
        if not is_configured():
            logger.debug("Binance not configured — skipping exchange info cache refresh.")
            return False

        loop = asyncio.get_event_loop()
        client = binance_client._get_client()
        exchange_info = await loop.run_in_executor(None, client.get_exchange_info)

        new_cache: Dict[str, Dict[str, Any]] = {}
        for sym in exchange_info.get("symbols", []):
            if sym.get("status") == "TRADING" and sym.get("quoteAsset") == "USDT":
                parsed = _parse_symbol_filters(sym)
                new_cache[sym["symbol"]] = parsed

        _exchange_info_cache = new_cache
        _last_refresh = time.time()
        logger.info(f"Exchange info cache refreshed: {len(_exchange_info_cache)} USDT trading pairs loaded.")
        return True
    except Exception as e:
        logger.warning(f"Failed to refresh exchange info cache: {e}")
        return False


async def get_symbol_constraints(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Returns cached exchange constraints for the given symbol.
    Auto-refreshes if the cache is empty or stale.
    """
    global _last_refresh
    if not _exchange_info_cache or (time.time() - _last_refresh > _REFRESH_INTERVAL_SECONDS):
        await refresh_exchange_info()
    return _exchange_info_cache.get(symbol)


def round_to_step(value: float, step: float) -> float:
    """Floors a float value to the nearest valid Binance step_size increment."""
    if step <= 0:
        return round(value, 8)
    
    from decimal import Decimal
    step_dec = Decimal(str(step))
    value_dec = Decimal(str(value))
    
    # Calculate precision from step
    # Example: 0.00001 -> 5
    precision = abs(step_dec.as_tuple().exponent)
    
    steps = int(value_dec / step_dec)
    rounded = float(steps * step_dec)
    return round(rounded, precision)





