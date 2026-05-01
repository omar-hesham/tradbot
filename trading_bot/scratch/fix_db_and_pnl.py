import asyncio
from sqlalchemy import select, update
from core.database import get_session
from core.models import BotConfig, Trade, Position
import json

async def fix_all():
    async for session in get_session():
        print("Updating DB Config for 'no limits'...")
        
        # 1. Update max_trade_usd
        res = await session.execute(select(BotConfig).where(BotConfig.key == "max_trade_usd"))
        cfg = res.scalar_one_or_none()
        if cfg:
            cfg.value = "100000000.0"
        else:
            session.add(BotConfig(key="max_trade_usd", value="100000000.0"))
            
        # 2. Update max_open_trades
        res = await session.execute(select(BotConfig).where(BotConfig.key == "max_open_trades"))
        cfg = res.scalar_one_or_none()
        if cfg:
            cfg.value = "1000"
        else:
            session.add(BotConfig(key="max_open_trades", value="1000"))

        # 3. Update strategy_profiles
        profiles = {
            "hustle": {"enabled": True, "max_trade_usd": 100000000.0, "min_confidence": 0.1, "max_holding_minutes": 180, "stop_loss_pct": 99.0, "take_profit_pct": 1000.0},
            "swing": {"enabled": True, "max_trade_usd": 100000000.0, "min_confidence": 0.1, "max_holding_minutes": 4320, "stop_loss_pct": 99.0, "take_profit_pct": 1000.0},
            "macro": {"enabled": True, "max_trade_usd": 100000000.0, "min_confidence": 0.1, "max_holding_minutes": 43200, "stop_loss_pct": 99.0, "take_profit_pct": 1000.0},
            "moonshot": {"enabled": True, "max_trade_usd": 100000000.0, "min_confidence": 0.1, "max_holding_minutes": 60, "stop_loss_pct": 99.0, "take_profit_pct": 10000.0}
        }
        res = await session.execute(select(BotConfig).where(BotConfig.key == "strategy_profiles"))
        cfg = res.scalar_one_or_none()
        if cfg:
            cfg.value = json.dumps(profiles)
        else:
            session.add(BotConfig(key="strategy_profiles", value=json.dumps(profiles)))

        print("Backfilling PnL for existing trades...")
        # Get all SELL trades with no PnL
        res = await session.execute(select(Trade).where(Trade.side == "SELL", Trade.realized_pnl == None))
        sell_trades = res.scalars().all()
        
        for sell in sell_trades:
            # Find the most recent BUY trade for this symbol before this SELL
            res = await session.execute(
                select(Trade)
                .where(Trade.symbol == sell.symbol, Trade.side == "BUY", Trade.created_at < sell.created_at)
                .order_by(Trade.created_at.desc())
            )
            buy = res.scalars().first()
            if buy:
                pnl = (sell.price - buy.price) * sell.quantity
                sell.realized_pnl = pnl
                print(f"Updated {sell.symbol} SELL at {sell.created_at} with PnL: {pnl}")

        await session.commit()
        print("All fixes applied successfully.")

if __name__ == "__main__":
    asyncio.run(fix_all())
