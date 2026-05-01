import logging
import asyncio
from datetime import datetime
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from exchange.binance_client import binance_client
from ai_brain.prompt_builder import calculate_indicators
from core.models import ScannedAsset
from core.database import get_session

logger = logging.getLogger(__name__)

class MarketScanner:
    """
    MarketScanner - Periodically scans 50+ assets to identify high-probability setups.
    It ranks assets based on technical indicators and 'Probability Score'.
    """

    async def scan_market(self):
        logger.info("Market Scanner: Starting comprehensive market scan...")
        try:
            # 1. Get Top 50 USDT pairs by volume
            tickers = await binance_client.get_all_tickers()
            usdt_pairs = [
                t for t in tickers 
                if t["symbol"].endswith("USDT") and "UP" not in t["symbol"] and "DOWN" not in t["symbol"]
            ]
            # Sort by volume and take top 50
            top_pairs = sorted(usdt_pairs, key=lambda x: float(x.get("volume", 0)), reverse=True)[:50]
            
            scanned_results = []

            # 2. Analyze each pair (throttled to avoid API bans)
            for pair_data in top_pairs:
                symbol = pair_data["symbol"]
                try:
                    # Fetch 4h OHLCV for trend analysis
                    klines = await binance_client.get_ohlcv(symbol, interval="4h", limit=30)
                    if not klines:
                        continue
                        
                    indicators = calculate_indicators(klines)
                    rsi = indicators.rsi_14
                    
                    # 3. Simple Probability Scoring Logic
                    # Example: Bullish if RSI < 40 (Oversold) or Trend is Up
                    score = 0.0
                    action = "NEUTRAL"
                    reasoning = []

                    if rsi and rsi < 40:
                        score += 40
                        reasoning.append(f"Oversold (RSI: {rsi:.1f})")
                        action = "BUY"
                    elif rsi and rsi > 60:
                        score += 30
                        reasoning.append(f"Overbought (RSI: {rsi:.1f})")
                        action = "SELL"
                    
                    # Check Price vs SMA (using sma_25 as trend indicator)
                    last_price = float(pair_data["price"])
                    ema_25 = indicators.sma_25
                    if ema_25 and last_price > ema_25:
                        score += 20
                        reasoning.append("Trading above 25 SMA (Bullish Trend)")
                    
                    # 4. Create ScannedAsset object
                    asset = ScannedAsset(
                        symbol=symbol,
                        score=score,
                        action=action if score > 30 else "NEUTRAL",
                        price=last_price,
                        change_24h=float(pair_data.get("change", 0)),
                        volume_24h=float(pair_data.get("volume", 0)),
                        rsi=rsi,
                        reasoning=", ".join(reasoning) if reasoning else "No significant technical signals."
                    )
                    scanned_results.append(asset)
                    
                    # Throttle slightly
                    await asyncio.sleep(0.1)

                except Exception as e:
                    logger.warning(f"Market Scanner: Failed to scan {symbol}: {e}")

            # 5. Save to DB (Update or Replace)
            async for session in get_session():
                # Clear old results first to keep it fresh
                await session.execute(delete(ScannedAsset))
                for res in scanned_results:
                    session.add(res)
                await session.commit()
            
            logger.info(f"Market Scanner: Scan complete. {len(scanned_results)} assets updated.")

        except Exception as e:
            logger.error(f"Market Scanner: Global scan error: {e}")

market_scanner = MarketScanner()
