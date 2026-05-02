from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from typing import Optional
import logging

logger = logging.getLogger(__name__)

from core.database import get_session
from sqlalchemy import select, func
from core.models import Trade, Position, AIDecision, BotConfig, LiveTradeRequest
from core.security import has_credential
from config.settings import get_settings
from exchange.binance_client import binance_client, is_configured
from trading.engine import get_bot_running, get_target_symbol
from trading.portfolio import get_portfolio, record_trade, get_paper_balance, record_position, close_position
from trading.strategy import get_strategy
from services.execution_engine import execute_live_trade
from api.schemas import (
    TradingStatusResponse,
    ManualOrderRequest,
)


router = APIRouter(prefix="/api/trading", tags=["trading"])


async def upsert_config(session, key: str, value: str):
    obj = BotConfig(key=key, value=value)
    await session.merge(obj)


@router.post("/start")
async def start_bot():
    async for session in get_session():
        await upsert_config(session, "bot_running", "true")
        await session.commit()
    return {"message": "Bot started"}


@router.post("/stop")
async def stop_bot():
    async for session in get_session():
        await upsert_config(session, "bot_running", "false")
        await session.commit()
    return {"message": "Bot stopped"}


@router.get("/status", response_model=TradingStatusResponse)
async def get_trading_status():
    running = await get_bot_running()
    strategy = await get_strategy()
    
    # Enrich: Update unrealized PnL with live prices before calculating total
    # We can fetch prices without auth for paper trading status
    try:
        from exchange.binance_client import binance_client
        tickers = await binance_client.get_all_tickers()
        price_map = {t["symbol"]: float(t["price"]) for t in tickers}
        from trading.portfolio import update_unrealized_pnl
        u_pnl = await update_unrealized_pnl(price_map)
        logger.info(f"STATUS ENRICH: Updated unrealized PnL: ${u_pnl:.2f} using {len(price_map)} tickers")
    except Exception as e:
        logger.warning(f"Failed to auto-update unrealized PnL in status: {e}")
    
    portfolio = await get_portfolio()
    
    # Calculate Realized PnL from all closed trades
    realized_pnl = 0.0
    async for session in get_session():
        res = await session.execute(select(func.sum(Trade.realized_pnl)).where(Trade.realized_pnl != None))
        realized_pnl = res.scalar() or 0.0
        
    unrealized_pnl = portfolio.unrealized_pnl
    total_pnl = realized_pnl + unrealized_pnl
    
    mode = "PAPER" if strategy.paper_trading else "LIVE"
    
    # Fetch Reconciliation metadata
    recon_status = "synced"
    recon_time = None
    async for session in get_session():
        res = await session.execute(select(BotConfig).where(BotConfig.key.in_(["reconciliation_status", "last_reconciliation_time"])))
        configs = {c.key: c.value for c in res.scalars().all()}
        recon_status = configs.get("reconciliation_status", "synced")
        recon_time = configs.get("last_reconciliation_time")
        break

    return TradingStatusResponse(
        running=running,
        mode=mode,
        open_positions=len(portfolio.positions),
        unrealized_pnl=round(unrealized_pnl, 2),
        realized_pnl=round(realized_pnl, 2),
        total_pnl=round(total_pnl, 2),
        reconciliation_status=recon_status,
        last_sync_time=recon_time
    )


@router.post("/reconcile")
async def trigger_reconciliation():
    from services.reconciliation_service import reconciliation_service
    try:
        await reconciliation_service.run_reconciliation()
        return {"success": True, "message": "Reconciliation completed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
async def get_positions():
    portfolio = await get_portfolio()
    positions = portfolio.positions

    # Enrich with live P&L
    try:
        symbols = list({p["symbol"] for p in positions})
        prices = {}
        if symbols:
            # Try fetching without auth for P&L display
            from exchange.binance_client import binance_client
            tickers = await binance_client.get_all_tickers()
            prices = {t["symbol"]: float(t["price"]) for t in tickers if t["symbol"] in symbols}
            
        for p in positions:
            live_price = prices.get(p["symbol"])
            if live_price:
                p["current_price"] = live_price
                p["unrealized_pnl"] = round(
                    (live_price - p["avg_entry_price"]) * p["quantity"], 4
                )
                p["unrealized_pnl_pct"] = round(
                    (live_price - p["avg_entry_price"]) / p["avg_entry_price"] * 100, 2
                )
            else:
                p.setdefault("current_price", p["avg_entry_price"])
                p.setdefault("unrealized_pnl_pct", 0.0)
    except Exception as e:
        logger.warning(f"Live P&L enrichment failed: {e}")

    return {"positions": positions}


@router.get("/orders")
async def get_orders(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    symbol: Optional[str] = None,
):
    offset = (page - 1) * size
    async for session in get_session():
        query = select(Trade).order_by(Trade.created_at.desc())
        if symbol:
            query = query.where(Trade.symbol == symbol)
        query = query.offset(offset).limit(size)
        result = await session.execute(query)
        trades = result.scalars().all()
        return {
            "trades": [
                {
                    "id": t.id,
                    "symbol": t.symbol,
                    "side": t.side,
                    "quantity": t.quantity,
                    "price": t.price,
                    "status": t.status,
                    "ai_reason": t.ai_reason,
                    "created_at": t.created_at.isoformat(),
                }
                for t in trades
            ],
            "page": page,
            "size": size,
            "total": len(trades),
        }


@router.post("/manual-order")
async def place_manual_order(request: ManualOrderRequest):
    strategy = await get_strategy()
    paper_mode = request.paper_trading if hasattr(request, 'paper_trading') else strategy.paper_trading

    if not paper_mode and not is_configured():
        raise HTTPException(status_code=403, detail="Binance not configured")

    current_price = 0.0
    if paper_mode:
        try:
            current_price = await binance_client.get_ticker_price(request.symbol) if is_configured() else 10000.0
        except:
            current_price = 10000.0
    else:
        try:
            current_price = await binance_client.get_ticker_price(request.symbol)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    quantity = request.quantity
    total_value = quantity * current_price
    if total_value > strategy.max_trade_usd:
        raise HTTPException(status_code=400, detail=f"Order value ${total_value:.2f} exceeds max ${strategy.max_trade_usd}")

    status = "paper" if paper_mode else "live"
    pnl = None
    if paper_mode and request.side.upper() == "SELL":
        pnl = await close_position(request.symbol, current_price)

    await record_trade(
        symbol=request.symbol, side=request.side,
        quantity=quantity, price=current_price,
        status=status, ai_reason="Manual order",
        realized_pnl=pnl
    )
    
    if paper_mode and request.side.upper() == "BUY":
        await record_position(
            symbol=request.symbol,
            quantity=quantity,
            avg_entry_price=current_price
        )

    return {"message": "Order placed", "symbol": request.symbol, "side": request.side,
            "quantity": quantity, "price": current_price, "status": status}


@router.get("/ai-decisions")
async def get_ai_decisions(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    offset = (page - 1) * size
    async for session in get_session():
        result = await session.execute(
            select(AIDecision).order_by(AIDecision.timestamp.desc()).offset(offset).limit(size)
        )
        decisions = result.scalars().all()
        return {
            "decisions": [
                {
                    "id": d.id,
                    "prompt": d.prompt_snapshot[:500] if d.prompt_snapshot else None,
                    "response": d.raw_response[:500] if d.raw_response else None,
                    "action": d.parsed_action,
                    "timestamp": d.timestamp.isoformat(),
                }
                for d in decisions
            ],
            "page": page,
            "size": size,
        }


@router.get("/balance")
async def get_balance():
    strategy = await get_strategy()
    if strategy.paper_trading:
        balance = await get_paper_balance()
        return {"balance": {"USDT": balance}}
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance not configured")
    try:
        balances = await binance_client.get_account_balances()
        return {"balance": balances}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/wallet/spot")
async def get_spot_wallet(mode: Optional[str] = None):
    strategy = await get_strategy()
    is_paper = strategy.paper_trading if mode != "LIVE" else False
    if is_paper:
        balance = await get_paper_balance()
        return {
            "wallet_type": "spot",
            "balances": [
                {"asset": "USDT", "free": balance, "locked": 0.0, "total": balance},
            ]
        }
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance not configured")
    try:
        balances = await binance_client.get_spot_balances()
        return {"wallet_type": "spot", "balances": balances}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/wallet/funding")
async def get_funding_wallet(mode: Optional[str] = None):
    strategy = await get_strategy()
    is_paper = strategy.paper_trading if mode != "LIVE" else False
    if is_paper:
        return {
            "wallet_type": "funding",
            "balances": [
                {"asset": "USDT", "free": 0.0, "locked": 0.0, "total": 0.0}
            ]
        }
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance not configured")
    try:
        balances = await binance_client.get_funding_balances()
        return {"wallet_type": "funding", "balances": balances}
    except Exception as e:
        return {"wallet_type": "funding", "balances": [], "error": str(e)}


@router.get("/wallet/all")
async def get_all_wallets(mode: Optional[str] = None):
    strategy = await get_strategy()
    is_paper = strategy.paper_trading if mode != "LIVE" else False

    if is_paper:
        balance = await get_paper_balance()
        return {
            "spot": [
                {"asset": "USDT", "free": balance, "locked": 0.0, "total": balance},
            ],
            "funding": [],
            "total_estimated_usd": balance
        }

    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance not configured")

    try:
        logger.info(f"Fetching wallets in mode: {mode}")
        spot = await binance_client.get_spot_balances()
        funding = await binance_client.get_funding_balances()
        
        total_usd = sum(b.get("usd_value", 0) for b in spot) + sum(b.get("usd_value", 0) for b in funding)
        
        return {
            "spot": spot,
            "funding": funding,
            "total_estimated_usd": total_usd
        }
    except Exception as e:
        logger.error(f"Error fetching live wallets: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
@router.get("/live-requests")
async def get_live_requests():
    async for session in get_session():
        result = await session.execute(
            select(LiveTradeRequest).where(LiveTradeRequest.status == "pending").order_by(LiveTradeRequest.created_at.desc())
        )
        requests = result.scalars().all()
        return requests


@router.post("/live-requests/{req_id}/approve")
async def approve_live_request(req_id: int):
    async for session in get_session():
        result = await session.execute(
            select(LiveTradeRequest).where(LiveTradeRequest.id == req_id)
        )
        req = result.scalar_one_or_none()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        
        if req.status != "pending":
            raise HTTPException(status_code=400, detail="Request already processed")

        # Execute Live Trade
        try:
            # We recreate a mock recommendation object for the execution engine
            from ai_brain.schemas import TradeIntent
            rec = TradeIntent(
                symbol=req.symbol,
                action=req.side,
                current_price=req.price,
                suggested_allocation_usd=req.allocation_usd,
                confidence=0.8, # Fallback
                strategy=req.strategy or "short_term",
                reasoning_summary=req.ai_reason or "Manually approved",
                entry_conditions=[],
                risk_factors=[],
                stop_loss_pct=2.0,
                take_profit_pct=5.0,
                max_holding_period_minutes=60,
                should_execute=True
            )

            
            await execute_live_trade(session, rec, req.price, req.quantity)
            
            req.status = "executed"
            await session.commit()
            return {"message": "Live trade executed successfully"}
        except Exception as e:
            logger.error(f"Live trade execution failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@router.post("/live-requests/{req_id}/reject")
async def reject_live_request(req_id: int):
    async for session in get_session():
        result = await session.execute(
            select(LiveTradeRequest).where(LiveTradeRequest.id == req_id)
        )
        req = result.scalar_one_or_none()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        
        req.status = "rejected"
        await session.commit()
        return {"message": "Request rejected"}
