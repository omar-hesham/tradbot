
import asyncio
from core.database import get_session
from core.models import BotConfig, Position
from sqlalchemy import select, func

async def check_db():
    async for session in get_session():
        # Check BotConfig
        res = await session.execute(select(BotConfig))
        configs = res.scalars().all()
        print("=== BOT CONFIG ===")
        for c in configs:
            print(f"{c.key}: {c.value}")
        
        # Check for duplicates
        res = await session.execute(
            select(BotConfig.key, func.count(BotConfig.key))
            .group_by(BotConfig.key)
            .having(func.count(BotConfig.key) > 1)
        )
        dupes = res.all()
        if dupes:
            print("\n!!! DUPLICATE CONFIGS FOUND !!!")
            for key, count in dupes:
                print(f"{key}: {count} occurrences")
        
        # Check Positions
        res = await session.execute(select(Position))
        positions = res.scalars().all()
        print("\n=== POSITIONS ===")
        for p in positions:
            print(f"{p.symbol}: {p.quantity} @ {p.avg_entry_price}")

if __name__ == "__main__":
    asyncio.run(check_db())
