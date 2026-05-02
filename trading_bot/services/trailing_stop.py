import logging
from sqlalchemy import select
from core.database import get_session
from core.models import Position
from exchange.binance_client import binance_client
from config.settings import get_settings
from services.execution_engine import execute_paper_trade, execute_live_trade
from ai_brain.schemas import TradeRecommendation

logger = logging.getLogger(__name__)

async def enforce_trailing_stops():
    """
    Fetches all open positions and updates their highest_price.
    If the current price drops below the trailing stop threshold, it liquidates the position.
    """
    settings = get_settings()
    
    # 1. Fetch all open positions
    async for session in get_session():
        result = await session.execute(select(Position))
        positions = result.scalars().all()
        
        if not positions:
            return  # No open positions to monitor

        # 2. Fetch current prices for all relevant symbols
        symbols = [p.symbol for p in positions]
        prices = await binance_client.fetch_multiple_prices(symbols)
        
        for pos in positions:
            current_price = prices.get(pos.symbol)
            if not current_price:
                continue
                
            # Initialize highest_price if missing
            if pos.highest_price is None:
                pos.highest_price = max(pos.avg_entry_price, current_price)
                
            # Initialize trailing_stop_pct if missing
            if pos.trailing_stop_pct is None:
                pos.trailing_stop_pct = 2.0  # Default 2%
                
            # Update high water mark
            if current_price > pos.highest_price:
                pos.highest_price = current_price
                session.add(pos)
                logger.info(f"Trailing Stop updated for {pos.symbol}: New highest price is ${pos.highest_price:.2f}")
                
            # Check trailing stop condition
            stop_price = pos.highest_price * (1 - (pos.trailing_stop_pct / 100))
            if current_price <= stop_price:
                logger.warning(
                    f"TRAILING STOP TRIGGERED for {pos.symbol}! "
                    f"Current: ${current_price:.2f}, Stop: ${stop_price:.2f} "
                    f"(High: ${pos.highest_price:.2f}, Drop: {pos.trailing_stop_pct}%)"
                )
                
                # Formulate a forced SELL recommendation
                rec = TradeRecommendation(
                    action="SELL",
                    symbol=pos.symbol,
                    current_price=current_price,
                    suggested_allocation_usd=current_price * pos.quantity,
                    confidence=1.0,
                    strategy="short_term",
                    reasoning_summary=f"Trailing Stop-Loss Triggered! Price dropped {pos.trailing_stop_pct}% from peak of ${pos.highest_price:.2f}.",
                    entry_conditions=[],
                    risk_factors=["Trailing stop execution"],
                    stop_loss_pct=0,
                    take_profit_pct=0,
                    max_holding_period_minutes=0,
                    should_execute=True
                )
                
                # Execute the trade
                if settings.PAPER_TRADING:
                    await execute_paper_trade(session, rec, current_price, pos.quantity)
                else:
                    await execute_live_trade(session, rec, current_price, pos.quantity)
                    
        await session.commit()
