import logging
from datetime import datetime, date
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select
from core.models import BotConfig, Position, Trade

class RuntimeSettings:
    def __init__(self, cache: dict):
        self._cache = cache

    @property
    def bot_running(self) -> bool:
        return self._cache.get("bot_running") == "true"

    @property
    def kill_switch_enabled(self) -> bool:
        return self._cache.get("kill_switch_enabled") == "true"

    @property
    def paper_trading(self) -> bool:
        return self._cache.get("paper_trading", "true") == "true"

    @property
    def live_trading_confirmed(self) -> bool:
        return self._cache.get("live_trading_confirmed") == "true"
        
    @property
    def max_open_trades(self) -> int:
        try:
            return int(self._cache.get("max_open_trades", "3"))
        except ValueError:
            return 3

    @property
    def max_trade_usd(self) -> float:
        try:
            return float(self._cache.get("max_trade_usd", "100.0"))
        except ValueError:
            return 100.0

    @property
    def daily_loss_limit(self) -> float:
        try:
            return float(self._cache.get("daily_loss_limit", "50.0"))
        except ValueError:
            return 50.0

    @property
    def strategy_profiles(self) -> dict:
        import json
        profiles_raw = self._cache.get("strategy_profiles")
        if profiles_raw:
            try:
                return json.loads(profiles_raw)
            except Exception:
                pass
        return {}

async def get_runtime_settings(db: AsyncSession) -> RuntimeSettings:
    result = await db.execute(select(BotConfig))
    configs = result.scalars().all()
    cache = {c.key: c.value for c in configs}
    return RuntimeSettings(cache)


async def is_max_open_positions_reached(db: AsyncSession, settings: RuntimeSettings) -> bool:
    res = await db.execute(select(Position))
    positions = res.scalars().all()
    return len(positions) >= settings.max_open_trades



async def is_daily_loss_limit_reached(db: AsyncSession) -> bool:
    """
    Calculates today's realized PnL from closed trades.
    If the net loss exceeds the configured daily_loss_limit_pct (of starting capital),
    automatically enables the kill switch to halt all trading.
    """
    logger = logging.getLogger(__name__)
    try:
        # Get loss limit config (default: 5% of max_trade_usd * max_open_trades)
        res = await db.execute(select(BotConfig))
        configs = res.scalars().all()
        config_map = {c.key: c.value for c in configs}
        
        daily_loss_limit_usd = float(config_map.get("daily_loss_limit", "0"))
        if daily_loss_limit_usd <= 0:
            # Not configured — skip check
            return False
        
        # Sum PnL from all SELL trades closed today
        today_start = datetime.combine(date.today(), datetime.min.time())
        res = await db.execute(
            select(Trade).where(
                Trade.side == "SELL",
                Trade.status == "filled",
                Trade.created_at >= today_start
            )
        )
        today_trades = res.scalars().all()
        
        total_realized_pnl = sum(
            getattr(t, "realized_pnl", 0.0) or 0.0
            for t in today_trades
        )
        
        if total_realized_pnl < -daily_loss_limit_usd:
            logger.warning(
                f"Daily loss limit breached: realized PnL ${total_realized_pnl:.2f} "
                f"exceeds limit -${daily_loss_limit_usd:.2f}. Enabling kill switch."
            )
            # Auto-enable kill switch
            res = await db.execute(
                select(BotConfig).where(BotConfig.key == "kill_switch_enabled")
            )
            kill_row = res.scalar_one_or_none()
            if kill_row:
                kill_row.value = "true"
            else:
                db.add(BotConfig(key="kill_switch_enabled", value="true"))
            await db.commit()
            return True
        
        return False
    except Exception as e:
        logger.error(f"Error checking daily loss limit: {e}")
        return False  # Fail open — don't halt trading on DB errors


async def can_trade_now(db: AsyncSession) -> tuple[bool, str]:
    settings = await get_runtime_settings(db)

    if settings.kill_switch_enabled:
        return False, "Kill switch active"

    if not settings.bot_running:
        return False, "Bot is stopped"

    if not settings.paper_trading and not settings.live_trading_confirmed:
        return False, "Live trading not confirmed"

    if await is_daily_loss_limit_reached(db):
        return False, "Daily loss limit reached"

    if await is_max_open_positions_reached(db, settings):
        return False, "Max open positions reached"

    return True, "Allowed"

