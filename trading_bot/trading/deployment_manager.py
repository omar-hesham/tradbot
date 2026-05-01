import asyncio
import logging
from typing import List, Dict, Any
from sqlalchemy import select
from core.database import get_session
from core.models import AIRecommendation, Trade, Position, BotConfig
from exchange.binance_client import binance_client, is_configured
from trading.portfolio import record_trade, record_position, close_position
from trading.strategy import get_strategy

logger = logging.getLogger(__name__)

class DeploymentManager:
    """Manages mass deployment of AI recommended portfolio picks."""
    
    @staticmethod
    async def get_available_usdt() -> float:
        """Fetches free USDT balance from Binance or paper simulation."""
        strategy = await get_strategy()
        if strategy.paper_trading:
            return 10000.0 # Virtual balance for paper trading
            
        if not is_configured():
            return 0.0
            
        try:
            balances = await binance_client.get_account_balances()
            usdt = next((b for b in balances if b["asset"] == "USDT"), None)
            return float(usdt["free"]) if usdt else 0.0
        except Exception as e:
            logger.error(f"Failed to fetch USDT balance: {e}")
            return 0.0

    @staticmethod
    async def deploy_portfolio(budget_pct: float) -> Dict[str, Any]:
        """
        Executes market buy orders for current AI recommendations.
        budget_pct: 0.0 to 1.0 (percentage of available USDT to deploy)
        """
        strategy = await get_strategy()
        available_usdt = await DeploymentManager.get_available_usdt()
        total_deployment_usd = available_usdt * budget_pct
        
        if total_deployment_usd <= 0:
            return {"success": False, "error": "Insufficient balance or invalid budget percentage"}

        async for session in get_session():
            result = await session.execute(
                select(AIRecommendation).order_by(AIRecommendation.id.desc()).limit(10)
            )
            recs = result.scalars().all()
        
        if not recs:
            return {"success": False, "error": "No active recommendations to deploy"}

        # Filter unique symbols (last seen)
        unique_recs = {}
        for r in recs:
            if r.symbol not in unique_recs:
                unique_recs[r.symbol] = r
        
        recs_to_buy = list(unique_recs.values())
        
        # Calculate individual allocations
        # We respect the AI's relative weighting but scale to the user's budget_pct
        total_ai_usd = sum(r.suggested_allocation_usd for r in recs_to_buy)
        if total_ai_usd <= 0:
            return {"success": False, "error": "AI suggestions had zero allocation"}

        results = []
        for r in recs_to_buy:
            # Scale allocation: (AI Share / Total AI) * User Budget
            target_usd = (r.suggested_allocation_usd / total_ai_usd) * total_deployment_usd
            
            logger.info(f"Deploying ${target_usd:.2f} to {r.symbol}")
            
            try:
                # 1. Fetch current price
                current_price = await binance_client.get_ticker_price(r.symbol)
                qty = target_usd / current_price
                
                if strategy.paper_trading:
                    # Paper Trade
                    await record_trade(
                        symbol=r.symbol,
                        side="BUY",
                        quantity=qty,
                        price=current_price,
                        status="paper",
                        ai_reason=f"Mass Deployment ({budget_pct*100}% budget): {r.reason}"
                    )
                    await record_position(r.symbol, qty, current_price)
                    results.append({"symbol": r.symbol, "status": "success", "amount": target_usd})
                else:
                    # Live Trade
                    order = await binance_client.place_market_order(r.symbol, "BUY", qty)
                    await record_trade(
                        symbol=r.symbol,
                        side="BUY",
                        quantity=qty,
                        price=current_price,
                        status="live",
                        ai_reason=f"Mass Deployment: {r.reason}"
                    )
                    results.append({"symbol": r.symbol, "status": "success", "amount": target_usd})
                    
            except Exception as e:
                logger.error(f"Failed to deploy to {r.symbol}: {e}")
                results.append({"symbol": r.symbol, "status": "failed", "error": str(e)})

        return {
            "success": True, 
            "deployed_usd": total_deployment_usd,
            "orders": results
        }
