import asyncio
import time
import logging
from sqlalchemy import select, delete
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from exchange.binance_client import binance_client, is_configured
from core.database import get_session
from core.models import AIRecommendation, AIDecision
from ai_brain.provider_factory import get_ai_provider
from ai_brain.prompt_builder import (
    build_portfolio_scanner_prompt, 
    parse_response,
    TradingIndicators
)
from exchange.cmc_client import CMCClient
from config.settings import get_settings
from core.security import get_credential
from ai_brain.ai_runtime import manual_ai_session, pause_auto_ai


router = APIRouter(prefix="/api/market", tags=["market"])
logger = logging.getLogger(__name__)

# Server-side ticker cache (3s TTL) — prevents Binance rate bans during 5s polling
_ticker_cache = {"data": None, "timestamp": 0, "ttl": 3}


@router.get("/price/{symbol}")
async def get_price(symbol: str):
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance credentials not configured")

    try:
        price = await binance_client.get_ticker_price(symbol)
        return {"symbol": symbol, "price": price}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/ohlcv/{symbol}")
async def get_ohlcv(
    symbol: str,
    interval: str = "5m",
    limit: int = 100,
):
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance credentials not configured")

    try:
        ohlcv = await binance_client.get_ohlcv(symbol, interval, limit)
        return {"symbol": symbol, "interval": interval, "candles": ohlcv}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tickers")
async def get_all_tickers(
    limit: int = Query(50, ge=1, le=200),
    sort_by: str = Query("volume", pattern="^(volume|price|change)$")
):
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance credentials not configured")

    try:
        now = time.time()
        if (now - _ticker_cache["timestamp"]) < _ticker_cache["ttl"] and _ticker_cache["data"]:
            tickers = _ticker_cache["data"]
        else:
            tickers = await binance_client.get_all_tickers()
            _ticker_cache["data"] = tickers
            _ticker_cache["timestamp"] = now
        
        detailed_tickers = []
        for t in tickers[:200]:
            try:
                price = float(t.get("price", 0))
                if price > 0:
                    detailed_tickers.append({
                        "symbol": t["symbol"],
                        "price": price,
                        "change": t.get("change", "0"),
                        "volume": t.get("volume", 0),
                    })
            except:
                pass
        
        if sort_by == "volume":
            detailed_tickers.sort(key=lambda x: float(x.get("volume", 0)), reverse=True)
        elif sort_by == "price":
            detailed_tickers.sort(key=lambda x: float(x.get("price", 0)), reverse=True)
        elif sort_by == "change":
            detailed_tickers.sort(key=lambda x: float(x.get("change", 0)), reverse=True)
        
        return {"tickers": detailed_tickers[:limit]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tickers/all")
async def get_all_tickers_detailed(limit: int = 200):
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance credentials not configured")

    try:
        tickers = await binance_client.get_all_tickers()
        
        detailed_tickers = []
        for t in tickers[:limit]:
            try:
                price = float(t.get("price", 0))
                if price > 0:
                    detailed_tickers.append({
                        "symbol": t["symbol"],
                        "price": price,
                        "change_24h": t.get("change", "0"),
                        "volume": t.get("volume", 0),
                    })
            except:
                pass
        
        detailed_tickers.sort(key=lambda x: x["symbol"])
        return {"tickers": detailed_tickers, "total": len(detailed_tickers)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/orderbook/{symbol}")
async def get_orderbook(symbol: str, limit: int = 20):
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance credentials not configured")

    try:
        from binance.client import Client
        client = binance_client._get_client()
        
        loop = asyncio.get_event_loop()
        depth = await loop.run_in_executor(
            None, lambda: client.get_order_book(symbol=symbol, limit=limit)
        )
        
        bids = [[float(p[0]), float(p[1])] for p in depth.get("bids", [])]
        asks = [[float(p[0]), float(p[1])] for p in depth.get("asks", [])]
        
        best_bid = bids[0][0] if bids else 0
        best_ask = asks[0][0] if asks else 0
        spread = best_ask - best_bid if bids and asks else 0
        
        return {
            "symbol": symbol,
            "bids": bids,
            "asks": asks,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "spread_pct": (spread / best_bid * 100) if best_bid > 0 else 0
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/24h/{symbol}")
async def get_24h_stats(symbol: str):
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance credentials not configured")

    try:
        from binance.client import Client
        client = binance_client._get_client()
        
        loop = asyncio.get_event_loop()
        stats = await loop.run_in_executor(
            None, lambda: client.get_ticker(symbol=symbol)
        )
        
        return {
            "symbol": stats["symbol"],
            "price": float(stats["lastPrice"]),
            "price_change": float(stats["priceChange"]),
            "price_change_pct": float(stats["priceChangePercent"]),
            "high_24h": float(stats["highPrice"]),
            "low_24h": float(stats["lowPrice"]),
            "volume": float(stats["volume"]),
            "quote_volume": float(stats["quoteVolume"]),
            "open_price": float(stats["openPrice"]),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/trades/{symbol}")
async def get_recent_trades(symbol: str, limit: int = 50):
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance credentials not configured")

    try:
        from binance.client import Client
        client = binance_client._get_client()
        
        loop = asyncio.get_event_loop()
        trades = await loop.run_in_executor(
            None, lambda: client.get_recent_trades(symbol=symbol, limit=limit)
        )
        
        return {
            "symbol": symbol,
            "trades": [
                {
                    "id": t["id"],
                    "price": float(t["price"]),
                    "qty": float(t["qty"]),
                    "time": t["time"],
                    "isBuyerMaker": t["isBuyerMaker"]
                }
                for t in trades
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/symbols")
async def get_symbols(
    quote: str = "USDT",
    limit: int = 200
):
    if not is_configured():
        return {
            "symbols": [
                "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "DOTUSDT", "MATICUSDT",
                "LINKUSDT", "LTCUSDT", "UNIUSDT", "ATOMUSDT", "XLMUSDT"
            ]
        }

    try:
        from binance.client import Client
        client = binance_client._get_client()
        
        loop = asyncio.get_event_loop()
        exchange_info = await loop.run_in_executor(None, client.get_exchange_info)
        
        symbols = [
            s["symbol"] for s in exchange_info["symbols"]
            if s["quoteAsset"] == quote and s["status"] == "TRADING"
        ]
        
        return {
            "symbols": symbols[:limit],
            "quote": quote,
            "total": len(symbols)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/search")
async def search_symbols(q: str = Query("", min_length=1)):
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance credentials not configured")

    try:
        from binance.client import Client
        client = binance_client._get_client()
        
        loop = asyncio.get_event_loop()
        exchange_info = await loop.run_in_executor(None, client.get_exchange_info)
        
        q_upper = q.upper()
        symbols = [
            s["symbol"] for s in exchange_info["symbols"]
            if s["quoteAsset"] == "USDT" 
            and s["status"] == "TRADING"
            and (q_upper in s["symbol"] or q_upper in s["baseAsset"])
        ]
        
        return {"symbols": symbols[:50], "query": q}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/top-movers")
async def get_top_movers(limit: int = 10):
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance credentials not configured")

    try:
        from binance.client import Client
        client = binance_client._get_client()
        
        loop = asyncio.get_event_loop()
        tickers = await loop.run_in_executor(None, client.get_ticker)
        
        movers = []
        for t in tickers:
            if t["symbol"].endswith("USDT"):
                try:
                    change = float(t["priceChangePercent"])
                    if abs(change) > 1:
                        movers.append({
                            "symbol": t["symbol"],
                            "price": float(t["lastPrice"]),
                            "change_pct": change,
                            "volume": float(t["quoteVolume"]),
                            "high": float(t["highPrice"]),
                            "low": float(t["lowPrice"]),
                        })
                except:
                    pass
        
        movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        
        return {"movers": movers[:limit]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/ai-recommendations")
async def get_ai_recommendations():
    async for session in get_session():
        result = await session.execute(
            select(AIRecommendation).order_by(AIRecommendation.timestamp.desc()).limit(10)
        )
        recs = result.scalars().all()
        return {
            "recommendations": [
                {
                    "id": r.id,
                    "symbol": r.symbol,
                    "price": r.current_price,
                    "allocation_usd": r.suggested_allocation_usd,
                    "reason": r.reason,
                    "sentiment": r.sentiment,
                    "confidence": r.confidence,
                    "timestamp": r.timestamp
                }
                for r in recs
            ]
        }

from ai_brain.shared_state import add_log, clear_logs, set_thought, get_logs


def _scanner_from_trade_decision(parsed: dict, total_budget: float) -> Optional[list[dict]]:
    """
    Some providers ignore scanner format and return a single trade decision object.
    Convert it into scanner recommendation format.
    """
    if not isinstance(parsed, dict):
        return None

    symbol = str(parsed.get("symbol", "")).upper().strip()
    if not symbol:
        return None

    allocation = parsed.get("suggested_allocation_usd", parsed.get("quantity_usd", 0))
    try:
        allocation = float(allocation or 0)
    except (TypeError, ValueError):
        allocation = 0.0

    if allocation <= 0:
        allocation = round(total_budget * 0.2, 2)

    return [{
        "symbol": symbol,
        "suggested_allocation_usd": allocation,
        "reason": parsed.get("reason") or parsed.get("reasoning_summary") or "Converted from single trade response",
        "confidence": float(parsed.get("confidence", 0) or 0),
        "sentiment": parsed.get("sentiment", "Neutral"),
    }]

@router.get("/ai-scanner/status")
async def get_ai_scanner_status():
    return get_logs()

@router.post("/ai-scanner")
async def run_ai_scanner(strategy_type: str = "macro"):
    """Triggers the AI to scan top coins and suggest a portfolio."""
    if not is_configured():
        raise HTTPException(status_code=403, detail="Binance not configured")

    try:
        pause_auto_ai(120, "manual portfolio scanner")
        clear_logs()
        add_log("Connecting to Database Memory Matrix...")
        
        add_log("Pinging Binance REST API for Deep Market Pulse (250 assets)...")
        # 1. Fetch Tickers
        all_tickers_raw = await binance_client.get_all_tickers()
        
        processed_tickers = []
        for t in all_tickers_raw:
            try:
                symbol = t["symbol"]
                if not symbol.endswith("USDT") or "UP" in symbol or "DOWN" in symbol:
                    continue
                processed_tickers.append({
                    "symbol": symbol,
                    "price": float(t.get("price", 0)),
                    "change": float(t.get("change", 0)),
                    "volume": float(t.get("quoteVolume", 0))
                })
            except: pass

        # Sort by volume and limit to 250 as requested
        processed_tickers.sort(key=lambda x: x["volume"], reverse=True)
        tickers = processed_tickers[:250]
        
        # Calculate Movers for Pulse
        gainers = sorted(tickers, key=lambda x: x["change"], reverse=True)[:10]
        losers = sorted(tickers, key=lambda x: x["change"])[:10]
        volume_leaders = tickers[:10]
        
        add_log("Fetching Macro Sentiment and Fear/Greed Index...")
        # 2. Get Macro Data (Reuse CMC logic)
        settings = get_settings()
        cmc_key = get_credential("CMC_API_KEY") or settings.CMC_API_KEY
        indicators = TradingIndicators(current_price=0, price_change_1h_pct=0, sma_7=0, sma_25=0, rsi_14=0)
        trending_coins = []
        
        if cmc_key:
            try:
                cmc = CMCClient(api_key=cmc_key)
                fng = await cmc.get_fear_and_greed()
                global_m = await cmc.get_global_metrics()
                trending_coins = await cmc.get_trending()
                indicators.fear_and_greed = fng.get("value")
                indicators.sentiment = fng.get("value_classification", "Unknown")
                indicators.btc_dominance = global_m.get("btc_dominance")
            except: pass

        add_log("Analyzing RAG Knowledge Matrix & Current Portfolio...")
        # 3. Get RAG Context
        from ai_brain.rag import search_knowledge
        rag_results = await search_knowledge("What are the lessons learned from recent trades and what is the current market strategy?", top_k=5)
        rag_context = "\n".join([f"- {r['text']}" for r in rag_results])
        if not rag_context:
            rag_context = "No specific trade lessons found. Follow general risk management rules."

        # 4. Get Current Positions
        from trading.portfolio import get_portfolio
        portfolio = await get_portfolio()
        positions_summary = "\n".join([
            f"- {p['symbol']}: Qty {p['quantity']:.4f} | Entry ${p['avg_entry_price']:.2f} | PnL ${p['unrealized_pnl']:+.2f}"
            for p in portfolio.positions
        ]) or "No open positions."

        add_log(f"Assembling Neural Prompt (Horizon: {strategy_type.upper()})...")
        # 4. Build Prompt
        from trading.strategy import get_strategy
        current_strategy = await get_strategy()
        profile = current_strategy.profiles.get(strategy_type.lower())
        if not profile or not profile.enabled:
            raise HTTPException(status_code=400, detail=f"Strategy profile '{strategy_type}' is disabled or unknown.")
        
        total_budget = float(profile.max_trade_usd) * 5 # Allow scanning for a larger portfolio
        system_p, user_p = build_portfolio_scanner_prompt(
            tickers=tickers,
            total_budget=total_budget,
            indicators=indicators,
            strategy_type=strategy_type,
            gainers=gainers,
            losers=losers,
            trending=trending_coins,
            rag_context=rag_context,
            positions_summary=positions_summary
        )
        
        add_log(f"Querying AI with {len(tickers)} Live Market Contexts...")
        set_thought("Thinking...\n\nAnalyzing MACD anomalies, volume profile distributions, and semantic RAG vectors. Computing optimal resource allocations...")
        
        # 4. Ask AI. Manual scans pause scheduled agents so providers are not
        # hit by overlapping requests.
        async with manual_ai_session("manual portfolio scanner"):
            provider = await get_ai_provider()
            ai_response = await provider.ask(system_p, user_p)
        
        set_thought("AI analysis complete. Validating recommendations payload...")
        add_log("Core AI analysis complete! Parsing structured payload...")
        
        # Save raw decision for debugging before parsing
        async for session in get_session():
            decision = AIDecision(
                prompt_snapshot=user_p[:2000],
                raw_response=ai_response[:2000],
                parsed_action="SCANNER"
            )
            session.add(decision)
            await session.commit()

        add_log("Injecting intelligence into DB...")
        # 5. Parse & Save Recommendations
        recommendations = parse_response(ai_response)
        add_log(f"Parsed {len(recommendations) if isinstance(recommendations, list) else 1 if recommendations else 0} recommendations from AI.")
        
        # Check if the AI returned an error object
        if isinstance(recommendations, dict) and "error" in recommendations:
            logger.warning("AI scanner provider returned explicit error payload: %s", recommendations.get("error"))
            add_log("AI provider returned an error payload.")
            set_thought("AI provider returned an error payload.")
            raise HTTPException(status_code=400, detail=f"AI brain error: {recommendations['error']}")

        if isinstance(recommendations, dict):
            recommendations = _scanner_from_trade_decision(recommendations, total_budget) or []
            if recommendations:
                add_log("Converted single trade payload into scanner recommendation format.")

        if not isinstance(recommendations, list):
            recommendations = []

        if not recommendations:
            warning_msg = "AI returned no actionable portfolio opportunities; existing recommendations were kept."
            logger.warning("AI scanner produced no usable recommendations. raw_snippet=%s", (ai_response or "")[:300])
            add_log(warning_msg)
            set_thought("No actionable opportunities found in this scan.")
            return {
                "success": True,
                "count": 0,
                "symbols": [],
                "warning": warning_msg,
                "kept_existing": True,
            }

        fallback_allocation = round(total_budget / len(recommendations), 2)
        ticker_symbols = {t["symbol"] for t in tickers}
        valid_recommendations = []
        for item in recommendations:
            symbol = str(item.get("symbol", "")).upper()
            
            # Filter out invalid/group symbols
            if any(x in symbol for x in ["PORTFOLIO", "BASKET", "INDEX"]):
                logger.info(f"Scanner: Filtering out generic symbol '{symbol}'")
                continue
                
            if symbol and symbol not in ticker_symbols and f"{symbol}USDT" in ticker_symbols:
                symbol = f"{symbol}USDT"
            
            if symbol not in ticker_symbols:
                logger.info(f"Scanner: Filtering out unknown ticker '{symbol}'")
                continue
                
            item["symbol"] = symbol
            valid_recommendations.append(item)
        
        recommendations = valid_recommendations
        for item in recommendations:
            if float(item.get("suggested_allocation_usd", 0) or 0) <= 0:
                item["suggested_allocation_usd"] = fallback_allocation

        async for session in get_session():
            # Replace recommendations only after the new AI payload is valid.
            await session.execute(delete(AIRecommendation))
            
            saved_recs = []
            for item in recommendations:
                # Find current price
                ticker = next((t for t in tickers if t["symbol"] == item["symbol"]), None)
                price = ticker["price"] if ticker else 0.0
                
                rec = AIRecommendation(
                    symbol=item["symbol"],
                    current_price=price,
                    suggested_allocation_usd=float(item.get("suggested_allocation_usd", 0)),
                    reason=item.get("reason", ""),
                    confidence=float(item.get("confidence", 0)),
                    sentiment=item.get("sentiment", "Neutral")
                )
                session.add(rec)
                saved_recs.append(item["symbol"])
            
            await session.commit()
        set_thought(f"Scan complete: {len(recommendations)} opportunities identified.")
        return {"success": True, "count": len(recommendations), "symbols": saved_recs}

    except HTTPException as e:
        logger.warning("AI scanner HTTP error %s: %s", e.status_code, e.detail)
        add_log(f"Scanner ended with HTTP {e.status_code}: {e.detail}")
        set_thought(f"Scanner finished with warning: {e.detail}")
        raise
    except Exception as e:
        logger.exception("AI scanner failed unexpectedly")
        add_log(f"Scanner failed unexpectedly: {e}")
        set_thought("Scanner failed unexpectedly. Check logs for details.")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/portfolio/deploy")
async def deploy_portfolio_route(payload: dict):
    """Executes market orders for all active AI recommendations."""
    budget_pct = float(payload.get("budget_percentage", 0.5))
    if budget_pct < 0.05 or budget_pct > 1.0:
        raise HTTPException(status_code=400, detail="Budget percentage must be between 0.05 (5%) and 1.0 (100%)")

    from trading.deployment_manager import DeploymentManager
    try:
        result = await DeploymentManager.deploy_portfolio(budget_pct)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result.get("error", "Deployment failed"))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
