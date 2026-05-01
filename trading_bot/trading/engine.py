import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from core.database import get_session
from core.models import Trade, Position, AIDecision, BotConfig
from config.settings import get_settings
from exchange.binance_client import binance_client, is_configured
from exchange.cmc_client import CMCClient
from ai_brain.provider_factory import get_ai_provider, AINotConfiguredError
from ai_brain.prompt_builder import build, parse_response, calculate_indicators
from trading.strategy import get_strategy
from trading.portfolio import (
    record_trade,
    record_position,
    close_position,
    update_unrealized_pnl,
)

logger = logging.getLogger(__name__)


async def get_bot_running() -> bool:
    async for session in get_session():
        result = await session.execute(
            select(BotConfig).where(BotConfig.key == "bot_running")
        )
        config = result.scalar_one_or_none()
        return config.value == "true" if config else False


async def get_target_symbol() -> str:
    async for session in get_session():
        result = await session.execute(
            select(BotConfig).where(BotConfig.key == "target_symbol")
        )
        config = result.scalar_one_or_none()
        return config.value if config else "BTCUSDT"


async def get_last_decisions(limit: int = 3) -> list[dict]:
    async for session in get_session():
        result = await session.execute(
            select(AIDecision)
            .order_by(AIDecision.timestamp.desc())
            .limit(limit)
        )
        decisions = result.scalars().all()
        return [
            {
                "action": d.parsed_action,
                "symbol": "BTCUSDT",
                "reason": d.raw_response[:100] if d.raw_response else "",
            }
            for d in decisions
        ]


async def run_decision_cycle():
    logger.info("Starting decision cycle...")

    running = await get_bot_running()
    if not running:
        logger.info("Bot is not running, skipping cycle")
        return

    settings = get_settings()
    target_symbol = await get_target_symbol()
    strategy = await get_strategy()

    current_price = 0.0
    if strategy.paper_trading:
        current_price = 10000.0
    elif is_configured():
        try:
            current_price = await binance_client.get_ticker_price(target_symbol)
        except Exception as e:
            logger.error(f"Failed to fetch price: {e}")
            return

    ohlcv = []
    if not strategy.paper_trading and is_configured():
        try:
            ohlcv = await binance_client.get_ohlcv(target_symbol, "5m", 20)
        except Exception as e:
            logger.warning(f"Failed to fetch OHLCV: {e}")

    async for session in get_session():
        result = await session.execute(select(Position))
        positions = result.scalars().all()
        position_list = [
            {
                "symbol": p.symbol,
                "quantity": p.quantity,
                "avg_entry_price": p.avg_entry_price,
                "unrealized_pnl": p.unrealized_pnl,
            }
            for p in positions
        ]

    last_decisions = await get_last_decisions()
    balance_usd = 10000.0 if strategy.paper_trading else 1000.0

    # Fetch Macro Data if CMC configured
    from core.security import get_credential
    cmc_api_key = get_credential("CMC_API_KEY") or settings.CMC_API_KEY
    macro_data = {"fear_and_greed": None, "sentiment": "Unknown", "btc_dominance": None}
    if cmc_api_key:
        try:
            from exchange.cmc_client import CMCClient
            cmc = CMCClient(api_key=cmc_api_key)
            import asyncio
            fng, global_m = await asyncio.gather(
                cmc.get_fear_and_greed(),
                cmc.get_global_metrics()
            )
            macro_data["fear_and_greed"] = fng.get("value")
            macro_data["sentiment"] = fng.get("value_classification", "Unknown")
            macro_data["btc_dominance"] = global_m.get("btc_dominance")
        except Exception as e:
            logger.warning(f"Failed to fetch CMC macro data: {e}")

    indicators = calculate_indicators(ohlcv)
    # Inject macro data into indicators
    indicators.fear_and_greed = macro_data["fear_and_greed"]
    indicators.sentiment = macro_data["sentiment"]
    indicators.btc_dominance = macro_data["btc_dominance"]

    system_prompt, user_prompt = build(
        symbol=target_symbol,
        current_price=indicators.current_price,
        ohlcv=ohlcv,
        positions=position_list,
        balance_usd=balance_usd,
        last_decisions=last_decisions,
        indicators=indicators,
        max_trade_usd=strategy.max_trade_usd,
        max_open_trades=strategy.max_open_trades,
        allowed_symbols=strategy.allowed_symbols,
        confidence_threshold=strategy.confidence_threshold,
        paper_trading=strategy.paper_trading,
    )

    ai_response = ""
    provider = None
    try:
        provider = await get_ai_provider()
        ai_response = await provider.ask(system_prompt, user_prompt)
    except AINotConfiguredError as e:
        logger.warning(f"AI not configured: {e}")
        return
    except Exception as e:
        logger.error(f"AI request failed: {e}")
        await record_decision(system_prompt, ai_response, "ERROR")
        return

    logger.info(f"AI Response: {ai_response}")

    parsed = parse_response(ai_response)
    if not parsed:
        logger.warning("Failed to parse AI response")
        await record_decision(system_prompt, ai_response, "PARSE_ERROR")
        return

    action = parsed.get("action", "HOLD")
    await record_decision(system_prompt, ai_response, action)

    if action == "HOLD":
        logger.info("AI chose to HOLD")
        return

    from exchange.order_validator import validate_order

    validation = await validate_order(
        action=action,
        symbol=parsed.get("symbol", target_symbol),
        quantity_usd=parsed.get("quantity_usd", 0),
        confidence=parsed.get("confidence", 0),
    )

    if not validation.valid:
        logger.warning(f"Order validation failed: {validation.error}")
        await record_trade(
            symbol=parsed.get("symbol", target_symbol),
            side=action,
            quantity=0,
            price=0,
            status="rejected",
            ai_reason=validation.error,
        )
        return

    quantity = parsed.get("quantity_usd", 0) / current_price if current_price > 0 else 0

    if strategy.paper_trading:
        pnl = None
        if action == "SELL":
            pnl = await close_position(parsed.get("symbol", target_symbol), current_price)
            
        await record_trade(
            symbol=parsed.get("symbol", target_symbol),
            side=action,
            quantity=quantity,
            price=current_price,
            status="paper",
            ai_reason=parsed.get("reason", ""),
            realized_pnl=pnl
        )

        if action == "BUY":
            await record_position(
                symbol=parsed.get("symbol", target_symbol),
                quantity=quantity,
                avg_entry_price=current_price,
            )

        logger.info(f"Paper trade executed: {action} {quantity} {parsed.get('symbol', target_symbol)}")
    else:
        if provider and provider.provider_name == "codex":
            approval_reason = (
                "Codex CLI is advisory-only. Order passed risk validation but requires manual approval "
                "before any live execution."
            )
            logger.info("Codex advisory generated a live candidate; recording pending approval instead of executing")
            await record_trade(
                symbol=parsed.get("symbol", target_symbol),
                side=action,
                quantity=quantity,
                price=current_price,
                status="pending_approval",
                ai_reason=f"{parsed.get('reason', '')} | {approval_reason}",
            )
            return
        try:
            order = await binance_client.place_market_order(
                symbol=parsed.get("symbol", target_symbol),
                side=action,
                quantity=quantity,
            )
            await record_trade(
                symbol=parsed.get("symbol", target_symbol),
                side=action,
                quantity=quantity,
                price=current_price,
                status="live",
                ai_reason=parsed.get("reason", ""),
            )
            logger.info(f"Live trade executed: {order}")
        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            await record_trade(
                symbol=parsed.get("symbol", target_symbol),
                side=action,
                quantity=quantity,
                price=current_price,
                status="failed",
                ai_reason=str(e),
            )


async def record_decision(prompt: str, response: str, action: str):
    async for session in get_session():
        decision = AIDecision(
            prompt_snapshot=prompt[:2000],
            raw_response=response[:2000],
            parsed_action=action,
        )
        session.add(decision)
        await session.commit()
