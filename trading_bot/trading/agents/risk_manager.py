import json
import logging
from datetime import datetime, timezone
from sqlalchemy import select, update
from core.database import get_session
from core.models import Position, BotConfig
from trading.strategy import get_strategy, DEFAULT_PROFILES
from ai_brain.provider_factory import get_ai_provider
from ai_brain.ai_runtime import auto_ai_session
from exchange.binance_client import binance_client
from trading.portfolio import close_position

logger = logging.getLogger(__name__)

async def bot_is_running() -> bool:
    async for session in get_session():
        result = await session.execute(select(BotConfig).where(BotConfig.key == "bot_running"))
        config = result.scalar_one_or_none()
        return config.value == "true" if config else False

async def run_risk_manager_agent():
    """
    Executes the [RISK-CHECK] intelligence protocol.
    Runs every 4 hours to verify position invariants, max loss thresholds, and capital preservation.
    """
    if not await bot_is_running():
        logger.debug("Risk manager skipped because bot_running=false.")
        return

    logger.info("Executing Portfolio Risk Manager...")
    
    # Get active positions and current prices
    positions_snapshot = []
    total_unrealized = 0.0
    
    async for session in get_session():
        result = await session.execute(select(Position))
        active_positions = result.scalars().all()
        
        for pos in active_positions:
            try:
                current_price = await binance_client.get_ticker_price(pos.symbol)
                unrealized = (current_price - pos.avg_entry_price) * pos.quantity
                pct_loss = ((current_price - pos.avg_entry_price) / pos.avg_entry_price) * 100
                total_unrealized += unrealized
                
                positions_snapshot.append({
                    "id": pos.id,
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "entry_price": pos.avg_entry_price,
                    "current_price": current_price,
                    "unrealized_usd": unrealized,
                    "pnl_pct": pct_loss,
                    "opened_at": pos.opened_at,
                })
            except Exception:
                pass
                
    if not positions_snapshot:
        logger.info("Risk Manager passed. No active positions.")
        return

    # Persist refreshed unrealized P&L values back to DB
    async for session in get_session():
        for snap in positions_snapshot:
            await session.execute(
                update(Position)
                .where(Position.id == snap["id"])
                .values(unrealized_pnl=snap["unrealized_usd"])
            )
        await session.commit()

    # Check against hard strategy limits first (deterministic failsafe, bypassed AI entirely)
    strategy = await get_strategy()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    for p in positions_snapshot:
        # 1. Max drawdown breach — emergency close
        if p["unrealized_usd"] <= -(strategy.max_drawdown_usd):
            logger.warning(f"URGENT RISK TRIGGER: {p['symbol']} breached max drawdown (${strategy.max_drawdown_usd}). Emergency closing.")
            try:
                await close_position(p["symbol"], p["current_price"])
                try:
                    from api.routes.ws_routes import manager as ws_manager
                    await ws_manager.broadcast_alert(
                        f"DRAWDOWN CLOSE: {p['symbol']} — loss ${p['unrealized_usd']:.2f}",
                        level="error", symbol=p["symbol"], pnl=p["unrealized_usd"]
                    )
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Drawdown close failed for {p['symbol']}: {e}")
            continue

        # 2. Max holding period exceeded — close stale positions
        opened_at = p.get("opened_at")
        if opened_at:
            if isinstance(opened_at, str):
                opened_at = datetime.fromisoformat(opened_at.replace("Z", "+00:00")).replace(tzinfo=None)
            age_minutes = (now_utc - opened_at).total_seconds() / 60
            # Use the most conservative max_holding_minutes across all active profiles
            max_hold = min(
                prof.max_holding_minutes
                for prof in (strategy.profiles or DEFAULT_PROFILES).values()
                if prof.enabled
            )
            if age_minutes > max_hold:
                logger.warning(
                    f"STALE POSITION: {p['symbol']} open {age_minutes:.0f}m > limit {max_hold}m. Closing."
                )
                try:
                    await close_position(p["symbol"], p["current_price"])
                    try:
                        from api.routes.ws_routes import manager as ws_manager
                        await ws_manager.broadcast_alert(
                            f"AGED CLOSE: {p['symbol']} held {age_minutes:.0f}m — PnL ${p['unrealized_usd']:+.2f}",
                            level="warning", symbol=p["symbol"], pnl=p["unrealized_usd"]
                        )
                    except Exception:
                        pass
                except Exception as e:
                    logger.error(f"Aged position close failed for {p['symbol']}: {e}")

    # Build prompt for broader AI correlation/stale analysis 
    system_prompt = (
        "You are CryptoBot AI Risk Manager, an elite quantitative risk analyst.\n"
        "Your only goal is capital preservation.\n"
        "- Output valid JSON only.\n"
    )
    
    user_prompt = f"""
[RISK-CHECK] Evaluate current portfolio health and flag any immediate risks.

=== PORTFOLIO STATUS ===
UNREALIZED P&L: ${total_unrealized:,.2f}
OPEN POSITIONS:
{json.dumps(positions_snapshot, indent=2)}

=== TASK ===
Review the portfolio for risks: correlations, stale items lacking volume, or macro weakness.
Respond ONLY in this JSON format:
{{
  "portfolio_health": "healthy|caution|danger",
  "immediate_actions": [
    {{
      "urgency": "immediate",
      "asset": "SYMBOL",
      "action": "close|reduce|hold",
      "reason": "Specific reason with data"
    }}
  ]
}}
    """
    
    try:
        async with auto_ai_session() as (can_run_ai, pause_reason):
            if not can_run_ai:
                logger.info(f"Risk manager skipped AI request: {pause_reason}")
                return
            ai = await get_ai_provider()
            response = await ai.ask(system_prompt, user_prompt)
        
        start_idx = response.find("{")
        end_idx = response.rfind("}") + 1
        if start_idx != -1 and end_idx != -1:
            data = json.loads(response[start_idx:end_idx])
            health = data.get("portfolio_health", "healthy")
            actions = data.get("immediate_actions", [])
            
            logger.info(f"AI Risk Assessment: {health.upper()}. {len(actions)} actions recommended.")
            
            # Execute AI-Driven Risk Cuts
            for action in actions:
                if action.get("action") == "close":
                    symbol = action.get("asset")
                    # Find position by symbol
                    pos_to_close = next((pos for pos in positions_snapshot if pos["symbol"] == symbol), None)
                    if pos_to_close:
                        logger.warning(f"AI RISK OVERRIDE - Closing {symbol} due to: {action.get('reason')}")
                        await close_position(symbol, pos_to_close["current_price"])
                        
    except Exception as e:
        logger.exception(f"Risk Manager agent failed: {e}")
