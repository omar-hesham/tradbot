import logging
import asyncio

from trading.strategy import get_strategy
from exchange.binance_client import binance_client
from ai_brain.prompt_builder import build, parse_response, calculate_indicators
from ai_brain.provider_factory import get_ai_provider, AINotConfiguredError
from ai_brain.ai_runtime import auto_ai_session
from ai_brain.rag import search_knowledge
from ai_brain.schemas import TradeIntent

from core.database import get_session
from core.models import AIDecision, AIRecommendation, BotConfig, ScannedAsset
from sqlalchemy import select, desc

logger = logging.getLogger(__name__)

async def get_target_symbol() -> str:
    async for session in get_session():
        result = await session.execute(
            select(BotConfig).where(BotConfig.key == "target_symbol")
        )
        config = result.scalar_one_or_none()
        return config.value if config else "BTCUSDT"

async def analyze(symbol: str, price: float = None, is_backtest: bool = False, use_ai: bool = True) -> TradeIntent:
    """
    Core analysis logic for a specific symbol.
    Used by both the live agent and the backtest engine.
    """
    strategy = await get_strategy()
    try:
        current_price = price or await binance_client.get_ticker_price(symbol)
        klines = await binance_client.get_ohlcv(symbol, interval="1h", limit=50)
        indicators = calculate_indicators(klines)

        # Heuristic Bypass for Fast Backtesting
        if is_backtest and not use_ai:
            action = "HOLD"
            confidence = 0.5
            reason = "Heuristic: Neutral"

            if indicators.rsi_14 is not None:
                if indicators.rsi_14 < 30:
                    action = "BUY"
                    confidence = 0.75
                    reason = f"Heuristic: Oversold (RSI={indicators.rsi_14:.1f})"
                elif indicators.rsi_14 > 70:
                    action = "SELL"
                    confidence = 0.8
                    reason = f"Heuristic: Overbought (RSI={indicators.rsi_14:.1f})"
            
            # Trend following component
            if action == "HOLD" and indicators.sma_7 and indicators.sma_25:
                if indicators.sma_7 > indicators.sma_25 * 1.01:
                    action = "BUY"
                    confidence = 0.65
                    reason = "Heuristic: Bullish SMA Cross"
                elif indicators.sma_7 < indicators.sma_25 * 0.99:
                    action = "SELL"
                    confidence = 0.65
                    reason = "Heuristic: Bearish SMA Cross"

            return TradeIntent(
                action=action,
                symbol=symbol,
                confidence=confidence,
                strategy="heuristic",
                reasoning_summary=reason,
                current_price=current_price,
                should_execute=action in ["BUY", "SELL"]
            )

        rag_context = await search_knowledge(
            f"What are the short-term execution rules or critical resistance levels for {symbol}?",
            horizon="short_term",
        )
        rag_text = "\n".join([f"- From {x['source']}: {x['text']}" for x in rag_context])

        system_prompt, user_prompt = build(
            symbol=symbol,
            current_price=current_price,
            ohlcv=klines[-20:],
            positions=[],
            balance_usd=strategy.max_trade_usd,
            last_decisions=[],
            indicators=indicators,
            max_trade_usd=strategy.max_trade_usd,
            max_open_trades=strategy.max_open_trades,
            allowed_symbols=strategy.allowed_symbols,
            confidence_threshold=strategy.confidence_threshold,
            paper_trading=strategy.paper_trading,
        )
        
        system_prompt += "\n\nYou MUST respond ONLY with valid JSON matching the TradeIntent schema."
        
        if rag_text:
            user_prompt = (
                f"[SHORT-TERM] Analyze {symbol} for an immediate trading decision.\n\n"
                f"=== RECENT RAG MEMORY ===\n{rag_text}\n\n{user_prompt}"
            )

        async with auto_ai_session() as (can_run_ai, pause_reason):
            if not can_run_ai:
                return None
            ai = await get_ai_provider()
            response = await asyncio.wait_for(ai.ask(system_prompt, user_prompt), timeout=45)
        
        parsed = parse_response(response)
        if not isinstance(parsed, dict) or parsed.get("error"):
            return None

        action = parsed.get("action", "HOLD")
        confidence = float(parsed.get("confidence", 0.0))
        reasoning = parsed.get("reason", parsed.get("reasoning_summary", ""))

        return TradeIntent(
            action=action if action in ["BUY", "SELL"] else "HOLD",
            symbol=symbol,
            confidence=confidence,
            strategy="moonshot",
            reasoning_summary=reasoning,
            current_price=current_price,
            entry_conditions=parsed.get("entry_conditions", []),
            risk_factors=parsed.get("risk_factors", []),
            stop_loss_pct=float(parsed.get("stop_loss_pct", 1.5)),
            take_profit_pct=float(parsed.get("take_profit_pct", 3.0)),
            max_holding_period_minutes=int(parsed.get("max_holding_period_minutes", 240)),
            should_execute=action in ["BUY", "SELL"]
        )

    except asyncio.TimeoutError:
        logger.warning("Agent analyze timed out for %s after 45s", symbol)
        return None
    except Exception:
        logger.exception("Agent analyze failed for %s", symbol)
        return None

async def _get_scanner_symbols(limit: int = 2) -> list[str]:
    """Returns top BUY-ranked symbols from the last market scan."""
    async for session in get_session():
        result = await session.execute(
            select(ScannedAsset)
            .where(ScannedAsset.action == "BUY")
            .order_by(desc(ScannedAsset.score))
            .limit(limit)
        )
        assets = result.scalars().all()
        return [a.symbol for a in assets]
    return []


async def generate_short_term_recommendation() -> TradeIntent:
    """
    Executes the [SHORT-TERM] intelligence protocol.
    Analyzes the configured target symbol plus the top 2 BUY-ranked scanned assets.
    Returns the highest-confidence actionable recommendation.
    """
    logger.info("Executing Short-Term Trading Agent...")
    target_symbol = await get_target_symbol()

    # Build candidate list: primary target + top scanner picks (deduped)
    scanner_symbols = await _get_scanner_symbols(limit=2)
    candidates = [target_symbol] + [s for s in scanner_symbols if s != target_symbol]

    best: TradeIntent = None
    for symbol in candidates:
        rec = await analyze(symbol)
        if rec and rec.action in ("BUY", "SELL"):
            if best is None or rec.confidence > best.confidence:
                best = rec

    # Fall back to HOLD recommendation on the primary symbol if nothing actionable
    recommendation = best or await analyze(target_symbol)

    if recommendation:
        async for session in get_session():
            decision = AIDecision(
                prompt_snapshot="[Logged via analyze method]",
                raw_response=recommendation.reasoning_summary,
                parsed_action=recommendation.action
            )
            session.add(decision)

            # Persist structured recommendation so analytics + approval queue can use it
            if recommendation.action in ("BUY", "SELL"):
                rec = AIRecommendation(
                    symbol=recommendation.symbol,
                    current_price=recommendation.current_price,
                    suggested_allocation_usd=recommendation.suggested_allocation_usd,
                    reason=recommendation.reasoning_summary,
                    confidence=recommendation.confidence,
                    sentiment="Bullish" if recommendation.action == "BUY" else "Bearish",
                )
                session.add(rec)

            await session.commit()

    return recommendation
