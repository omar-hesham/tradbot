import logging
import asyncio
from core.database import get_session
from core.bot_state import can_trade_now, get_runtime_settings
from services.risk_engine import risk_engine
from services.execution_engine import execute_paper_trade, execute_live_trade
from exchange.binance_client import binance_client
from ai_brain.ai_runtime import auto_ai_pause_status, is_ai_busy

logger = logging.getLogger(__name__)

async def safe_agent_runner(agent_name: str, agent_func):
    """
    Safely runs a trading agent by enforcing global bot state,
    risk engine limits, and exchange rules.
    """
    paused, pause_reason = auto_ai_pause_status()
    if paused or is_ai_busy():
        logger.info(
            "Agent %s skipped: %s",
            agent_name,
            pause_reason or "another AI request is running",
        )
        return

    async for db in get_session():
        allowed, reason = await can_trade_now(db)
        if not allowed:
            logger.info(f"Agent {agent_name} skipped: {reason}")
            return
        break

    # 1. Run Agent
    recommendation = await agent_func()
    if not recommendation:
        return

    async for db in get_session():
        try:
            await process_recommendation(db, agent_name, recommendation)
        except Exception as e:
            logger.exception(f"Safe runner failed for {agent_name}: {e}")

async def process_recommendation(db, agent_name, recommendation):
    """
    Handles the common risk validation and execution logic for any recommendation.
    """
    allowed, reason = await can_trade_now(db)

    if not allowed:
        logger.info(f"Recommendation for {agent_name} skipped: {reason}")
        return

    # 2. Risk Engine Gatekeeper (Handles confidence, limits, and exchange rules)
    approved, risk_reason = await risk_engine.approve(db, recommendation)
    if not approved:
        logger.warning(f"Risk Engine rejected {agent_name} recommendation: {risk_reason}")
        return

    # 3. Execution (Risk Engine might have adjusted suggested_allocation_usd)
    current_price = recommendation.current_price
    quantity = recommendation.suggested_allocation_usd / current_price if current_price > 0 else 0
    
    # Final rounding to exchange stepSize
    from exchange.exchange_cache import get_symbol_constraints, round_to_step
    constraints = await get_symbol_constraints(recommendation.symbol)
    if constraints:
        quantity = round_to_step(quantity, constraints["step_size"])


    settings = await get_runtime_settings(db)
    if settings.paper_trading:
        await execute_paper_trade(db, recommendation, current_price, quantity)
    else:
        # PHASE 5 SAFETY: All live trades must be manually approved via the dashboard


        from core.models import LiveTradeRequest
        request = LiveTradeRequest(
            symbol=recommendation.symbol,
            side=recommendation.action,
            quantity=quantity,
            price=current_price,
            allocation_usd=recommendation.suggested_allocation_usd,
            ai_reason=recommendation.reasoning_summary,
            strategy=recommendation.strategy,
            status="pending"
        )
        db.add(request)
        await db.commit()
        logger.info(f"PHASE 5: {recommendation.symbol} {recommendation.action} request queued for manual approval.")



async def safe_scanner_runner(agent_name: str, agent_func):
    """
    Safely runs a scanner agent (hustle/long_term).
    These agents don't execute trades directly, but we still check if the bot is allowed to run.
    """
    async for db in get_session():
        try:
            allowed, reason = await can_trade_now(db)
            if not allowed:
                logger.info(f"Scanner Agent {agent_name} skipped: {reason}")
                return

            await agent_func()

        except Exception as e:
            logger.exception(f"Safe scanner failed for {agent_name}: {e}")

async def run_safe_short_term_agent():
    from trading.agents.short_term_agent import generate_short_term_recommendation
    await safe_agent_runner("short_term", generate_short_term_recommendation)

async def run_safe_hustle_agent():
    from trading.agents.hustle_agent import run_hustle_agent
    await safe_scanner_runner("hustle", run_hustle_agent)

async def run_safe_long_term_agent():
    from trading.agents.long_term_agent import run_long_term_analysis
    await safe_scanner_runner("long_term", run_long_term_analysis)
