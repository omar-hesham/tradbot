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

            # 4. Generate Narrative (In a real app, we'd call an LLM agent here)
            # For Phase 5, we'll create a structured summary.
            win_loss = "WIN" if trade.realized_pnl > 0 else "LOSS"
            title = f"Post-Mortem: {trade.symbol} {win_loss} (${trade.realized_pnl:.2f})"
            
            content = f"""
TRADE SUMMARY:
- Symbol: {trade.symbol}
- Side: {trade.side}
- Realized PnL: ${trade.realized_pnl:.2f}
- Strategy Used: {trade.strategy}
- Close Time: {trade.created_at.isoformat()}

ORIGINAL HYPOTHESIS:
{trade.ai_reason or "N/A"}

POST-MORTEM ANALYSIS:
This {win_loss.lower()} occurred using the {trade.strategy} strategy. 
{"The trade successfully captured the intended move." if trade.realized_pnl > 0 else "The market moved against the original thesis."}
The AI reasoning at the time was: {trade.ai_reason or "Unknown"}.

LESSON LEARNED:
- Verification of {trade.strategy} parameters for {trade.symbol} is recommended.
- Memory injected for future context to prevent repeating similar {win_loss.lower()} patterns.
            """

            # 5. Ingest into RAG (starts as pending_review)
            await ingest_document(
                title=title,
                doc_type="postmortem",
                horizon=trade.strategy or "swing",
                asset=trade.symbol,
                content=content.strip(),
                status="pending_review"
            )
            
            logger.info(f"Post-Mortem document created for Trade #{trade.id}. Awaiting user approval.")

        except Exception as e:
            logger.error(f"Failed to generate post-mortem for trade {trade_id}: {e}")

post_mortem_engine = PostMortemEngine()
