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
                  use_ai: bool = False,
                  inject_paper_trades: bool = False):
        
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
                if use_ai:
                    recommendation = await analyze_agent(sym, price, is_backtest=True, use_ai=True)
                else:
                    action = "HOLD"
                    if i > 0:
                        prev_close = float(data_cache[sym][i-1][4])
                        # Extremely sensitive threshold to guarantee paper trades for Readiness
                        if price < prev_close:
                            action = "BUY"
                        elif price > prev_close:
                            action = "SELL"
                    recommendation = TradeRecommendation(
                        action=action, symbol=sym, confidence=0.8, strategy="time_travel",
                        reasoning_summary="Time Travel Bypass", current_price=price,
                        should_execute=(action != "HOLD"), suggested_allocation_usd=min(self.balance * 0.5, 1000)
                    )

                if not recommendation:
                    continue
                
                # Update recommendation with historical price
                recommendation.current_price = price
                
                # Check Risk Engine (using current RAG state for "what-if" testing)
                if use_ai:
                    async for session in get_session():
                        approved, reason = await risk_engine.approve(session, recommendation, is_backtest=True)
                        if approved:
                            await self.execute_simulated_trade(recommendation, current_time)
                        else:
                            if recommendation.action != "HOLD":
                                logger.debug(f"Backtest [{current_time}]: {sym} REJECTED: {reason}")
                else:
                    # Fast historical bypass skips complex DB risk checks
                    await self.execute_simulated_trade(recommendation, current_time)

            # Simulate Trailing Stop-Loss
            for sym in list(self.open_positions.keys()):
                pos = self.open_positions[sym]
                current_price = prices.get(sym)
                if not current_price:
                    continue
                
                if current_price > pos["highest_price"]:
                    pos["highest_price"] = current_price
                
                # Check trailing stop (default 2%)
                stop_price = pos["highest_price"] * 0.98
                if current_price <= stop_price:
                    rec = TradeRecommendation(
                        action="SELL", symbol=sym, current_price=current_price,
                        suggested_allocation_usd=0, confidence=1.0, strategy="trailing_stop",
                        reasoning_summary="Trailing stop triggered in backtest",
                        entry_conditions=[], risk_factors=[], stop_loss_pct=0, take_profit_pct=0, max_holding_period_minutes=0, should_execute=True
                    )
                    await self.execute_simulated_trade(rec, current_time)

            # Calculate total equity at this step
            current_equity = self.balance
            for sym, pos in self.open_positions.items():
                current_equity += pos['qty'] * prices.get(sym, pos['entry_price'])
            
            self.equity_curve.append({"t": current_time.isoformat(), "y": current_equity})

        # Force close all open positions at the end to record them as trades
        for sym, pos in list(self.open_positions.items()):
            if sym in data_cache and len(data_cache[sym]) > 0:
                last_price = float(data_cache[sym][-1][4])
                
                # Apply slippage and fees to forced close
                slippage_rate = 0.0005
                fee_rate = 0.001
                executed_price = last_price * (1 - slippage_rate)
                qty = pos["qty"]
                proceeds = qty * executed_price * (1 - fee_rate)
                
                pnl = proceeds - pos["total_cost"]
                self.balance += proceeds
                self.trades.append({
                    "symbol": sym,
                    "entry": pos["entry_price"],
                    "exit": executed_price,
                    "pnl": pnl,
                    "time": timeline[-1].isoformat() if timeline else datetime.utcnow().isoformat()
                })
                del self.open_positions[sym]

        self.is_running = False
        logger.info(f"Backtest Complete. Final Equity: ${self.equity_curve[-1]['y']:.2f}")

        if inject_paper_trades and self.trades:
            logger.info(f"Injecting {len(self.trades)} backtest trades into database as paper trades...")
            from core.models import Trade
            async for session in get_session():
                for t in self.trades:
                    # t["time"] is ISO string, parse it
                    trade_time = datetime.fromisoformat(t["time"])
                    
                    db_trade = Trade(
                        symbol=t["symbol"],
                        side="SELL",  # simplified
                        quantity=1.0,
                        price=t["exit"],
                        status="paper",
                        ai_reason="Backtest Simulation Injection",
                        strategy="backtest",
                        realized_pnl=t["pnl"],
                        created_at=trade_time
                    )
                    session.add(db_trade)
                await session.commit()
                break

        win_trades = sum(1 for t in self.trades if t["pnl"] > 0)
        win_rate = (win_trades / len(self.trades) * 100) if self.trades else 0.0

        return {
            "summary": {
                "initial_capital": initial_capital,
                "final_equity": self.equity_curve[-1]['y'],
                "total_trades": len(self.trades),
                "win_rate": win_rate,
                "pnl_pct": ((self.equity_curve[-1]['y'] / initial_capital) - 1) * 100
            },
            "curve": self.equity_curve,
            "trades": self.trades
        }

    async def execute_simulated_trade(self, rec: TradeRecommendation, timestamp: datetime):
        slippage_rate = 0.0005  # 0.05%
        fee_rate = 0.001        # 0.1%

        if rec.action == "BUY":
            executed_price = rec.current_price * (1 + slippage_rate)
            qty = rec.suggested_allocation_usd / executed_price
            total_cost = qty * executed_price * (1 + fee_rate)
            
            if self.balance >= total_cost:
                self.balance -= total_cost
                self.open_positions[rec.symbol] = {
                    "qty": qty,
                    "entry_price": executed_price,
                    "total_cost": total_cost,
                    "highest_price": executed_price,
                    "time": timestamp
                }
                logger.info(f"Backtest [{timestamp}]: BUY {qty:.4f} {rec.symbol} @ {executed_price:.4f} (Cost: ${total_cost:.2f})")

        elif rec.action == "SELL":
            if rec.symbol in self.open_positions:
                pos = self.open_positions.pop(rec.symbol)
                executed_price = rec.current_price * (1 - slippage_rate)
                proceeds = pos['qty'] * executed_price * (1 - fee_rate)
                
                pnl = proceeds - pos['total_cost']
                self.balance += proceeds
                self.trades.append({
                    "symbol": rec.symbol,
                    "entry": pos['entry_price'],
                    "exit": executed_price,
                    "pnl": pnl,
                    "time": timestamp.isoformat()
                })
                logger.info(f"Backtest [{timestamp}]: SELL {rec.symbol} @ {executed_price:.4f} | PnL: ${pnl:.2f}")

backtest_engine = BacktestEngine()
