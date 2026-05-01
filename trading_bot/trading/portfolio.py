from datetime import datetime
from typing import Optional, Any
from dataclasses import dataclass
import logging

from sqlalchemy import select, update
from core.database import get_session
from core.models import Trade, Position, BotConfig

logger = logging.getLogger(__name__)


@dataclass
class Portfolio:
    total_value_usd: float
    unrealized_pnl: float
    positions: list[dict]


async def get_paper_balance() -> float:
    async for session in get_session():
        res = await session.execute(select(BotConfig).where(BotConfig.key == "paper_balance_usdt"))
        config = res.scalar_one_or_none()
        if not config:
            # Initialize with 10,000 if not set
            config = BotConfig(key="paper_balance_usdt", value="10000.0")
            session.add(config)
            await session.commit()
            return 10000.0
        return float(config.value)


async def update_paper_balance(amount: float):
    """Adds (or subtracts if negative) from the persistent paper balance."""
    async for session in get_session():
        res = await session.execute(select(BotConfig).where(BotConfig.key == "paper_balance_usdt"))
        config = res.scalar_one_or_none()
        current = 10000.0
        if config:
            current = float(config.value)
        else:
            config = BotConfig(key="paper_balance_usdt", value=str(current))
            session.add(config)
        
        new_balance = current + amount
        config.value = str(new_balance)
        session.add(config)
        await session.commit()
        logger.info(f"PAPER BALANCE PERSISTED: New balance is ${new_balance:.2f} (change: {amount:+.2f})")


async def get_portfolio() -> Portfolio:
    async for session in get_session():
        result = await session.execute(select(Position))
        positions = result.scalars().all()

        total_value = 0.0
        unrealized_pnl = 0.0
        position_list = []

        for pos in positions:
            value = pos.quantity * pos.avg_entry_price
            pnl = pos.unrealized_pnl
            total_value += value
            unrealized_pnl += pnl
            position_list.append(
                {
                    "id": pos.id,
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "avg_entry_price": pos.avg_entry_price,
                    "unrealized_pnl": pnl,
                    "opened_at": pos.opened_at.isoformat(),
                }
            )

        logger.info(f"PORTFOLIO SUM: Total Value=${total_value:.2f}, Unrealized PnL=${unrealized_pnl:.2f} across {len(position_list)} positions")
        return Portfolio(
            total_value_usd=total_value,
            unrealized_pnl=unrealized_pnl,
            positions=position_list,
        )


async def record_trade(
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    status: str,
    ai_reason: Optional[str] = None,
    strategy: Optional[str] = None,
    realized_pnl: Optional[float] = None,
) -> Trade:
    async for session in get_session():
        trade = Trade(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            status=status,
            ai_reason=ai_reason,
            strategy=strategy,
            realized_pnl=realized_pnl,
        )
        session.add(trade)
        await session.commit()

        # Update paper balance if applicable
        if status == "paper":
            cost = quantity * price
            logger.info(f"PAPER BALANCE TRIGGER: {side} {symbol} cost=${cost:.2f}")
            if side.upper() == "BUY":
                await update_paper_balance(-cost)
            else:
                await update_paper_balance(cost)
        else:
            logger.debug(f"record_trade: status is {status}, not paper. Skipping balance update.")

        return trade



async def record_position(
    symbol: str,
    quantity: float,
    avg_entry_price: float,
) -> Position:
    async for session in get_session():
        result = await session.execute(
            select(Position).where(Position.symbol == symbol)
        )
        position = result.scalars().first()
        
        if position:
            # Update existing position (weighted average)
            new_total_quantity = position.quantity + quantity
            if new_total_quantity > 0:
                new_avg_price = (
                    (position.avg_entry_price * position.quantity) + (avg_entry_price * quantity)
                ) / new_total_quantity
                position.avg_entry_price = new_avg_price
                position.quantity = new_total_quantity
            else:
                # Should not happen for record_position (which is for BUYS)
                position.quantity = 0
            session.add(position)
        else:
            # Create new position
            position = Position(
                symbol=symbol,
                quantity=quantity,
                avg_entry_price=avg_entry_price,
                unrealized_pnl=0.0,
            )
            session.add(position)
            
        await session.commit()
        return position


async def close_position(symbol: str, exit_price: float) -> Optional[float]:
    async for session in get_session():
        result = await session.execute(
            select(Position).where(Position.symbol == symbol)
        )
        # Use .first() to handle any legacy duplicates gracefully
        position = result.scalars().first()
        if not position:
            return None

        pnl = (exit_price - position.avg_entry_price) * position.quantity
        await session.delete(position)
        await session.commit()
        return pnl


async def update_unrealized_pnl(current_prices: dict[str, float]) -> float:
    total_pnl = 0.0
    async for session in get_session():
        result = await session.execute(select(Position))
        positions = result.scalars().all()

        matches = 0
        for pos in positions:
            current_price = current_prices.get(pos.symbol)
            if current_price:
                pnl = (current_price - pos.avg_entry_price) * pos.quantity
                pos.unrealized_pnl = pnl
                total_pnl += pnl
                matches += 1
            else:
                logger.debug(f"No live price for {pos.symbol} in update_unrealized_pnl")
        
        logger.info(f"update_unrealized_pnl: Updated {matches}/{len(positions)} positions. Total PnL: ${total_pnl:.2f}")
        await session.commit()
    return total_pnl