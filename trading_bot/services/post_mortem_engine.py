import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from core.models import Trade, AIDecision
from ai_brain.rag import ingest_document

logger = logging.getLogger(__name__)

class PostMortemEngine:
    """
    Analyzes closed trades to generate 'Institutional Memory' for the RAG system.
    Triggered after a SELL fill.
    """

    async def analyze_trade(self, session: AsyncSession, trade_id: int):
        # 1. Fetch the closed trade
        result = await session.execute(select(Trade).where(Trade.id == trade_id))
        trade = result.scalar_one_or_none()
        
        if not trade or trade.side != "SELL" or trade.realized_pnl is None:
            return

        # 2. Check significance (only analyze big wins or losses to avoid noise)
        # Threshold: > 2% win or < -1% loss (or any loss)
        # For simplicity, we'll analyze any trade with PnL != 0
        if abs(trade.realized_pnl) < 1.0: # Skip tiny dust trades
            return

        logger.info(f"Triggering Post-Mortem for Trade #{trade.id} ({trade.symbol}) | PnL: ${trade.realized_pnl:.2f}")

        try:
            # 3. Gather context: Get the most recent AIDecision for this symbol before the trade
            # (In a more advanced setup, we'd link Trade -> Decision directly)
            dec_result = await session.execute(
                select(AIDecision)
                .where(AIDecision.timestamp <= trade.created_at)
                .order_by(AIDecision.timestamp.desc())
                .limit(1)
            )
            decision = dec_result.scalar_one_or_none()
            original_reason = decision.parsed_action if decision else "No decision logs found."

            # 4. Generate Narrative using AI
            from ai_brain.provider_factory import get_ai_provider
            ai_provider = get_ai_provider()
            
            win_loss = "WIN" if trade.realized_pnl > 0 else "LOSS"
            
            prompt = f"""
            Analyze the following closed trade for TradBot:
            
            Symbol: {trade.symbol}
            Action: {trade.side}
            Realized PnL: ${trade.realized_pnl:.2f}
            Strategy: {trade.strategy}
            Original Reasoning: {trade.ai_reason or "N/A"}
            
            Task:
            1. Determine why this trade was a {win_loss}.
            2. Extract a "Lesson Learned" that can be used to improve future trades for this symbol or strategy.
            3. Formulate the response as a clear, concise markdown document intended for an institutional knowledge base.
            """
            
            analysis = await ai_provider.generate_text(prompt, system_prompt="You are a Senior Trading Quantitative Analyst.")
            
            title = f"AI Post-Mortem: {trade.symbol} {win_loss} (${trade.realized_pnl:.2f})"
            
            # 5. Ingest into RAG (starts as pending_review)
            await ingest_document(
                title=title,
                doc_type="postmortem",
                horizon=trade.strategy or "swing",
                asset=trade.symbol,
                content=analysis.strip(),
                status="pending_review"
            )
            
            logger.info(f"AI Post-Mortem created for Trade #{trade.id}. Awaiting user approval.")

        except Exception as e:
            logger.error(f"Failed to generate AI post-mortem for trade {trade_id}: {e}")

post_mortem_engine = PostMortemEngine()
