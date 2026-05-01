import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_brain.schemas import TradeRecommendation
from core.database import get_session
from core.models import Trade, Position
from services.risk_engine import risk_engine
from trading.agents.short_term_agent import analyze as analyze_agent
from exchange.binance_client import binance_client

logger = logging.getLogger(__name__)

class BacktestEngine:
    """
    Historical Replay Engine.
    Simulates the bot's logic against historical market data.
    """

    def __init__(self):
        self.equity_curve = []
        self.trades = []
        self.open_positions = {}
        self.balance = 0.0
        self.is_running = False

    async def run(self, 
                  symbols: List[str], 
                  start_date: datetime, 
                  end_date: datetime, 
                  initial_capital: float = 1000.0,
                  interval: str = "1h",
                  use_ai: bool = False):
        
        self.balance = initial_capital
        self.equity_curve = [{"t": start_date.isoformat(), "y": initial_capital}]
        self.trades = []
        self.open_positions = {}
        self.is_running = True

        logger.info(f"Starting Backtest: {start_date} to {end_date} | Capital: ${initial_capital}")

        # 1. Fetch Historical Data for all symbols
        # Note: In a production setup, we'd batch fetch or use a local CSV store.
        # For this implementation, we fetch chunks from Binance.
        data_cache = {}
        for sym in symbols:
            logger.info(f"Fetching historical data for {sym}...")
            klines = await binance_client.get_klines(sym, interval, start_date, end_date)
            data_cache[sym] = klines or []

        # 2. Iterate through time
        # We assume klines are aligned or we iterate by the first symbol's timestamps
        if not symbols or symbols[0] not in data_cache or not data_cache[symbols[0]]:
            self.is_running = False
            return {
                "summary": {
                    "initial_capital": initial_capital,
                    "final_equity": initial_capital,
                    "total_trades": 0,
                    "pnl_pct": 0.0,
                },
                "curve": self.equity_curve,
                "trades": [],
            }

        timeline = [datetime.fromtimestamp(k[0]/1000) for k in data_cache[symbols[0]]]
        
        for i, current_time in enumerate(timeline):
            # Update Price for all symbols at this step
            prices = {}
            for sym in symbols:
                if i < len(data_cache[sym]):
                    prices[sym] = float(data_cache[sym][i][4]) # Close price
            
            # Run Agent for each symbol
            for sym, price in prices.items():
                # Simulate the Agent's view at this time
                # In a real backtest, we'd pass historical indicators. 
                # Here we simulate a simplified agent call.
                recommendation = await analyze_agent(sym, price, is_backtest=True, use_ai=use_ai)
                if not recommendation:
                    continue
                
                # Update recommendation with historical price
                recommendation.current_price = price
                
                # Check Risk Engine (using current RAG state for "what-if" testing)
                # Note: We pass a mock session or handle DB logic carefully
                async for session in get_session():
                    approved, reason = await risk_engine.approve(session, recommendation, is_backtest=True)
                    
                    if approved:
                        await self.execute_simulated_trade(recommendation, current_time)
                    else:
                        if recommendation.action != "HOLD":
                            logger.debug(f"Backtest [{current_time}]: {sym} REJECTED: {reason}")

            # Calculate total equity at this step
            current_equity = self.balance
            for sym, pos in self.open_positions.items():
                current_equity += pos['qty'] * prices.get(sym, pos['entry_price'])
            
            self.equity_curve.append({"t": current_time.isoformat(), "y": current_equity})

        self.is_running = False
        logger.info(f"Backtest Complete. Final Equity: ${self.equity_curve[-1]['y']:.2f}")
        return {
            "summary": {
                "initial_capital": initial_capital,
                "final_equity": self.equity_curve[-1]['y'],
                "total_trades": len(self.trades),
                "pnl_pct": ((self.equity_curve[-1]['y'] / initial_capital) - 1) * 100
            },
            "curve": self.equity_curve,
            "trades": self.trades
        }

    async def execute_simulated_trade(self, rec: TradeRecommendation, timestamp: datetime):
        if rec.action == "BUY":
            # Simple market buy simulation
            qty = rec.suggested_allocation_usd / rec.current_price
            if self.balance >= rec.suggested_allocation_usd:
                self.balance -= rec.suggested_allocation_usd
                self.open_positions[rec.symbol] = {
                    "qty": qty,
                    "entry_price": rec.current_price,
                    "time": timestamp
                }
                logger.info(f"Backtest [{timestamp}]: BUY {qty:.4f} {rec.symbol} @ {rec.current_price}")

        elif rec.action == "SELL":
            if rec.symbol in self.open_positions:
                pos = self.open_positions.pop(rec.symbol)
                proceeds = pos['qty'] * rec.current_price
                pnl = proceeds - (pos['qty'] * pos['entry_price'])
                self.balance += proceeds
                self.trades.append({
                    "symbol": rec.symbol,
                    "entry": pos['entry_price'],
                    "exit": rec.current_price,
                    "pnl": pnl,
                    "time": timestamp.isoformat()
                })
                logger.info(f"Backtest [{timestamp}]: SELL {rec.symbol} @ {rec.current_price} | PnL: ${pnl:.2f}")

backtest_engine = BacktestEngine()
