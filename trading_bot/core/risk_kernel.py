import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from sqlalchemy import select, func
from core.database import get_session
from core.models import Trade, DailyLossTracker
from config.settings import get_settings

logger = logging.getLogger(__name__)

class RiskKernel:
    """
    Risk Kernel - System-wide safety layer.
    Manages global state that affects all agents and strategies.
    """
    def __init__(self):
        self._kill_switch = False
        self._cooldowns: Dict[str, datetime] = {}

    async def is_system_safe(self) -> Tuple[bool, str]:
        """Checks global safety conditions."""
        if self._kill_switch:
            return False, "Global Kill Switch is ACTIVE."
        
        # Check daily loss limit across all trades
        settings = get_settings()
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        async for session in get_session():
            res = await session.execute(select(DailyLossTracker).where(DailyLossTracker.date == today))
            tracker = res.scalar_one_or_none()
            if tracker and tracker.total_loss >= settings.trading.daily_loss_limit:
                return False, f"Daily loss limit (${settings.trading.daily_loss_limit}) reached."

        return True, "Safe"

    def activate_kill_switch(self):
        self._kill_switch = True
        logger.critical("!!! RISK KERNEL: KILL SWITCH ACTIVATED !!!")

    def deactivate_kill_switch(self):
        self._kill_switch = False
        logger.info("Risk Kernel: Kill switch deactivated.")

    def set_cooldown(self, symbol: str, minutes: int = 60):
        self._cooldowns[symbol] = datetime.utcnow() + timedelta(minutes=minutes)
        logger.info(f"Risk Kernel: Cooldown set for {symbol} until {self._cooldowns[symbol]}")

    def is_in_cooldown(self, symbol: str) -> bool:
        until = self._cooldowns.get(symbol)
        if until and datetime.utcnow() < until:
            return True
        return False

risk_kernel = RiskKernel()
