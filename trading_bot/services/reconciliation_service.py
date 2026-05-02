import logging
import asyncio
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session
from core.models import Position, Trade
from exchange.binance_client import binance_client, is_configured
from config.settings import get_settings

logger = logging.getLogger(__name__)

class ReconciliationService:
    """
    Reconciliation Service - Detects and fixes state drift between:
    - Bot Database (Positions/Trades)
    - Binance Exchange (Actual Balances/Orders)
    """

    async def run_reconciliation(self):
        if not is_configured():
            logger.debug("Binance not configured — skipping reconciliation.")
            return

        async for session in get_session():
            await self._reconcile_positions(session)
            await self._reconcile_from_trade_history(session)
            await self._reconcile_pending_trades(session)
            await session.commit()

    async def _reconcile_positions(self, session: AsyncSession):
        """Syncs local positions with Binance spot balances."""
        try:
            balances = await binance_client.get_account_balances()
            
            # Fetch all local positions
            res = await session.execute(select(Position))
            local_positions = res.scalars().all()
            
            for pos in local_positions:
                asset = pos.symbol.replace("USDT", "")
                actual_balance = balances.get(asset, 0.0)
                
                # If balance is near zero but DB says we have a position -> CLOSED
                if actual_balance < 0.000001: # Small threshold
                    logger.warning(f"RECONCILIATION: Position {pos.symbol} not found on Binance. Removing from DB.")
                    await session.execute(delete(Position).where(Position.id == pos.id))
                else:
                    # Sync quantity if it has drifted significantly (> 1%)
                    drift_pct = abs(pos.quantity - actual_balance) / max(actual_balance, 0.000001)
                    if drift_pct > 0.01:
                        logger.info(f"RECONCILIATION: Correcting {pos.symbol} quantity from {pos.quantity} to {actual_balance}")
                        pos.quantity = actual_balance

            # Detect Ghost Positions (on Binance but not in DB)
            settings = get_settings()
            for asset, balance in balances.items():
                if asset == "USDT": continue
                symbol = f"{asset}USDT"
                
                # Check if this asset has significant value (> $5)
                # This avoids noise from dust
                price = 0.0
                try:
                    price = await binance_client.get_ticker_price(symbol)
                except: continue
                
                if balance * price > 5.0:
                    res = await session.execute(select(Position).where(Position.symbol == symbol))
                    if not res.scalar_one_or_none():
                        logger.warning(f"RECONCILIATION: Ghost Position detected! {symbol} (Qty: {balance}). Importing to local database.")
                        new_pos = Position(
                            symbol=symbol,
                            quantity=balance,
                            avg_entry_price=price, # Default to current price for imported positions
                            unrealized_pnl=0.0
                        )
                        session.add(new_pos)
                        # Store metadata about the import in BotConfig or similar if needed

        except Exception as e:
            logger.error(f"Error during position reconciliation: {e}")

    async def _reconcile_from_trade_history(self, session: AsyncSession):
        """
        Scans trade history to find symbols that should have positions 
        (Buy exists but no Sell) and restores them if missing.
        """
        try:
            from sqlalchemy import func
            # Get cumulative quantity per symbol
            # This is a simplified approach: sum(BUY) - sum(SELL)
            res = await session.execute(
                select(Trade.symbol)
                .group_by(Trade.symbol)
            )
            symbols = res.scalars().all()

            for symbol in symbols:
                # Check if position already exists
                pos_res = await session.execute(select(Position).where(Position.symbol == symbol))
                if pos_res.scalar_one_or_none():
                    continue

                # Calculate cumulative quantity from filled/paper trades
                # Note: We only count filled or paper trades
                buy_res = await session.execute(
                    select(func.sum(Trade.quantity))
                    .where(Trade.symbol == symbol, Trade.side == "BUY", Trade.status.in_(["filled", "paper"]))
                )
                sell_res = await session.execute(
                    select(func.sum(Trade.quantity))
                    .where(Trade.symbol == symbol, Trade.side == "SELL", Trade.status.in_(["filled", "paper"]))
                )
                
                total_bought = buy_res.scalar() or 0.0
                total_sold = sell_res.scalar() or 0.0
                remaining = total_bought - total_sold

                if remaining > 0.000001:
                    # Get the last buy price for entry price estimation
                    last_buy = await session.execute(
                        select(Trade.price)
                        .where(Trade.symbol == symbol, Trade.side == "BUY", Trade.status.in_(["filled", "paper"]))
                        .order_by(Trade.id.desc())
                        .limit(1)
                    )
                    entry_price = last_buy.scalar() or 0.0
                    
                    logger.warning(f"RECONCILIATION: Recovered position from history for {symbol} (Qty: {remaining})")
                    new_pos = Position(
                        symbol=symbol,
                        quantity=remaining,
                        avg_entry_price=entry_price,
                        unrealized_pnl=0.0
                    )
                    session.add(new_pos)

        except Exception as e:
            logger.error(f"Error during history reconciliation: {e}")

    async def _reconcile_pending_trades(self, session: AsyncSession):
        """Checks if pending trades in DB were actually filled or cancelled on Binance."""
        try:
            res = await session.execute(select(Trade).where(Trade.status == "pending"))
            pending_trades = res.scalars().all()

            for trade in pending_trades:
                # This is simplified. Ideally we should track Binance orderId in Trade model.
                # Since we might not have it for older trades, we check recent orders for the symbol.
                orders = await binance_client.get_all_orders(trade.symbol, limit=5)
                
                # Try to find a matching order by quantity/side/status
                for o in orders:
                    o_qty = float(o.get('origQty', 0))
                    if o['side'] == trade.side and abs(o_qty - trade.quantity) < 0.0001:
                        status = o['status']
                        if status == 'FILLED':
                            logger.info(f"RECONCILIATION: Pending trade {trade.symbol} found as FILLED on Binance. Updating DB.")
                            trade.status = "filled"
                            trade.price = float(o['price']) if float(o['price']) > 0 else float(o['cummulativeQuoteQty'])/float(o['executedQty'])
                        elif status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                            logger.info(f"RECONCILIATION: Pending trade {trade.symbol} found as {status} on Binance. Removing from DB.")
                            await session.delete(trade)
                        break

        except Exception as e:
            logger.error(f"Error during trade reconciliation: {e}")

reconciliation_service = ReconciliationService()
