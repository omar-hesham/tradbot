import asyncio
from sqlalchemy import select, delete
from core.database import get_session
from core.models import Position

async def merge_positions():
    async for session in get_session():
        print("Starting global position merge/cleanup...")
        
        # Get all symbols that have positions
        res = await session.execute(select(Position.symbol).distinct())
        symbols = [r[0] for r in res.all()]
        
        for symbol in symbols:
            # Get all positions for this symbol
            res = await session.execute(
                select(Position).where(Position.symbol == symbol)
            )
            positions = res.scalars().all()
            
            if len(positions) > 1:
                print(f"Merging {len(positions)} positions for {symbol}...")
                total_qty = 0.0
                total_cost = 0.0
                
                for p in positions:
                    total_qty += p.quantity
                    total_cost += (p.quantity * p.avg_entry_price)
                
                avg_price = total_cost / total_qty if total_qty > 0 else 0
                
                # Delete all
                for p in positions:
                    await session.delete(p)
                
                # Create one new merged one
                if total_qty > 0:
                    new_pos = Position(
                        symbol=symbol,
                        quantity=total_qty,
                        avg_entry_price=avg_price,
                        unrealized_pnl=0.0
                    )
                    session.add(new_pos)
                    print(f"Created merged position for {symbol}: {total_qty} @ {avg_price}")
            else:
                print(f"Symbol {symbol} has 1 position. OK.")

        await session.commit()
        print("Position merge complete.")

if __name__ == "__main__":
    asyncio.run(merge_positions())
