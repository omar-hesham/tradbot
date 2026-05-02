"""
Execution Engine
================
Separates paper and live trade execution. 
On SELL fills, computes realized_pnl against the open Position entry price
so the daily loss limit tracker has accurate data.
"""

import logging
from sqlalchemy.orm import Session
from sqlalchemy import select
from ai_brain.schemas import TradeIntent
from core.models import Position
from trading.portfolio import record_trade, close_position
from services.post_mortem_engine import post_mortem_engine

logger = logging.getLogger(__name__)


async def execute_paper_trade(
    db: Session,
    recommendation: TradeIntent,
    price: float,
    quantity: float,
) -> None:
    """Records a simulated paper trade with a 0.1% fee spread."""
    executed_price = price * 1.001 if recommendation.action == "BUY" else price * 0.999

    realized_pnl = None
    if recommendation.action == "SELL":
        realized_pnl = await close_position(recommendation.symbol, executed_price)

    logger.info(
        f"[PAPER] {recommendation.action} {quantity} {recommendation.symbol} @ {executed_price:.4f}"
        + (f" | PnL: ${realized_pnl:.2f}" if realized_pnl is not None else "")
    )

    trade = await record_trade(
        symbol=recommendation.symbol,
        side=recommendation.action,
        quantity=quantity,
        price=executed_price,
        status="paper",
        ai_reason=recommendation.reasoning_summary,
        strategy=recommendation.strategy,
        realized_pnl=realized_pnl,
    )

    # Broadcast alert to dashboard
    try:
        from api.routes.ws_routes import manager as ws_manager
        msg = f"[PAPER] {recommendation.action} {recommendation.symbol} @ ${executed_price:.2f}"
        if realized_pnl is not None:
            msg += f" | PnL: ${realized_pnl:+.2f}"
        level = "success" if (realized_pnl or 0) >= 0 else "warning"
        await ws_manager.broadcast_alert(msg, level=level, symbol=recommendation.symbol, pnl=realized_pnl)
        
        # Send Telegram Alert
        from services.telegram_bot import send_telegram_message
        tg_msg = (
            f"📄 <b>PAPER TRADE EXECUTED</b>\n"
            f"<b>Action:</b> {recommendation.action}\n"
            f"<b>Symbol:</b> {recommendation.symbol}\n"
            f"<b>Quantity:</b> {quantity}\n"
            f"<b>Price:</b> ${executed_price:.4f}\n"
        )
        if realized_pnl is not None:
            tg_msg += f"<b>PnL:</b> ${realized_pnl:+.2f}\n"
        tg_msg += f"<i>{recommendation.reasoning_summary}</i>"
        await send_telegram_message(tg_msg)
        
    except Exception:
        pass

    # 3. Post-Mortem Analysis (only for closed trades)
    if realized_pnl is not None:
        await post_mortem_engine.analyze_trade(db, trade.id)


async def execute_live_trade(
    db: Session,
    recommendation: TradeIntent,
    price: float,
    quantity: float,
) -> None:
    """Places a real market order on Binance, then records it with realized PnL."""
    from exchange.binance_client import binance_client

    realized_pnl = None
    executed_price = price  # Will be updated from the Binance fill

    try:
        order = await binance_client.place_market_order(
            symbol=recommendation.symbol,
            side=recommendation.action,
            quantity=quantity,
        )
        # Extract average fill price from the order response
        fills = order.get("fills", [])
        if fills:
            total_qty = sum(float(f["qty"]) for f in fills)
            executed_price = (
                sum(float(f["price"]) * float(f["qty"]) for f in fills) / total_qty
                if total_qty > 0
                else price
            )
        else:
            executed_price = float(order.get("price", price)) or price

        logger.info(
            f"[LIVE] Order placed: {recommendation.action} {quantity} {recommendation.symbol} "
            f"@ avg {executed_price:.4f} | OrderId: {order.get('orderId')}"
        )

        if recommendation.action == "SELL":
            realized_pnl = await close_position(recommendation.symbol, executed_price)

    except Exception as e:
        logger.exception(f"[LIVE] Binance order failed for {recommendation.symbol}: {e}")
        # Record as failed so we have an audit trail
        await record_trade(
            symbol=recommendation.symbol,
            side=recommendation.action,
            quantity=quantity,
            price=price,
            status="failed",
            ai_reason=f"Execution error: {e}",
            strategy=recommendation.strategy,
        )
        return

    trade = await record_trade(
        symbol=recommendation.symbol,
        side=recommendation.action,
        quantity=quantity,
        price=executed_price,
        status="filled",
        ai_reason=recommendation.reasoning_summary,
        strategy=recommendation.strategy,
        realized_pnl=realized_pnl,
    )

    # Broadcast alert to dashboard
    try:
        from api.routes.ws_routes import manager as ws_manager
        msg = f"[LIVE] {recommendation.action} {recommendation.symbol} @ ${executed_price:.2f}"
        if realized_pnl is not None:
            msg += f" | PnL: ${realized_pnl:+.2f}"
        level = "success" if (realized_pnl or 0) >= 0 else "error"
        await ws_manager.broadcast_alert(msg, level=level, symbol=recommendation.symbol, pnl=realized_pnl)
        
        # Send Telegram Alert
        from services.telegram_bot import send_telegram_message
        tg_msg = (
            f"🚀 <b>LIVE TRADE EXECUTED</b>\n"
            f"<b>Action:</b> {recommendation.action}\n"
            f"<b>Symbol:</b> {recommendation.symbol}\n"
            f"<b>Quantity:</b> {quantity}\n"
            f"<b>Price:</b> ${executed_price:.4f}\n"
        )
        if realized_pnl is not None:
            tg_msg += f"<b>PnL:</b> ${realized_pnl:+.2f}\n"
        tg_msg += f"<i>{recommendation.reasoning_summary}</i>"
        await send_telegram_message(tg_msg)
        
    except Exception:
        pass

    # 4. Update Daily Loss Tracker (only for live closed trades)
    if realized_pnl is not None and realized_pnl < 0:
        from core.models import DailyLossTracker
        from datetime import datetime
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        res = await db.execute(select(DailyLossTracker).where(DailyLossTracker.date == today))
        tracker = res.scalar_one_or_none()
        if not tracker:
            tracker = DailyLossTracker(date=today, total_loss=0.0)
            db.add(tracker)
        
        tracker.total_loss += abs(realized_pnl)
        logger.info(f"PHASE 5: Updated daily loss tracker. Total today: ${tracker.total_loss:.2f}")

    # 5. Post-Mortem Analysis (only for closed trades)
    if realized_pnl is not None:
        await post_mortem_engine.analyze_trade(db, trade.id)
