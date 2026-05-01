import json
import logging
from datetime import datetime
from sqlalchemy import select
from ai_brain.provider_factory import get_ai_provider
from ai_brain.ai_runtime import auto_ai_session
from exchange.cmc_client import CMCClient
from ai_brain.rag import search_knowledge, ingest_document
from core.database import get_session
from core.models import AIRecommendation, BotConfig

logger = logging.getLogger(__name__)

async def bot_is_running() -> bool:
    async for session in get_session():
        result = await session.execute(select(BotConfig).where(BotConfig.key == "bot_running"))
        config = result.scalar_one_or_none()
        return config.value == "true" if config else False


async def run_long_term_analysis():
    """
    Executes the [LONG-TERM] intelligence protocol.
    Aggregates fundamental data, queries RAG, and produces macro investment theses.
    """
    if not await bot_is_running():
        logger.debug("Long-term agent skipped because bot_running=false.")
        return

    logger.info("Executing Long-Term Analysis Agent...")
    
    # 1. Fetch Macro Data
    cmc = CMCClient()
    metrics = await cmc.get_global_metrics()
    fng = await cmc.get_fear_and_greed()
    
    # 2. Query RAG for past theses to ensure consistency
    rag_context = await search_knowledge("What is our current long-term thesis on BTC and macro cycle patterns?", horizon="long_term")
    rag_text = "\n".join([f"- From {x['source']}: {x['text']}" for x in rag_context])
    
    # 3. Construct System & User Payload
    system_prompt = (
        "You are CryptoBot AI, an elite autonomous cryptocurrency trading analyst.\n"
        "Your role is to analyze market data, identify trading opportunities, and provide clear structured decisions.\n"
        "- Capital preservation is ALWAYS more important than profit generation.\n"
        "- Treat PREVIOUS KNOWLEDGE & CONTEXT as historical AI analysis, NOT as factual market data. Rely on input metrics.\n"
        "- Output valid JSON only.\n"
    )
    
    user_prompt = f"""
[LONG-TERM] Perform a comprehensive weekly market analysis.

=== PREVIOUS KNOWLEDGE & CONTEXT ===
{rag_text if rag_text else "No prior history stored in RAG memory."}

=== INPUT DATA ===
DATE: {datetime.utcnow().strftime("%Y-%m-%d")}
GLOBAL METRICS:
- Total Crypto Market Cap: ${metrics.get('total_market_cap', 0):,.2f}
- BTC Dominance: {metrics.get('btc_dominance', 0):.2f}%
- Active Cryptocurrencies: {metrics.get('active_cryptocurrencies', 0)}
- DeFi Market Cap: ${metrics.get('defi_market_cap', 0):,.2f}
FEAR & GREED: {fng.get('name', 'Neutral')} ({fng.get('value', 50)})

=== TASK ===
Analyze this data and produce a long-term investment thesis.
Respond ONLY in this JSON format:
{{
  "analysis_date": "YYYY-MM-DD",
  "market_cycle": "accumulation|markup|distribution|markdown",
  "market_sentiment": "bearish|neutral|bullish",
  "macro_comment": "2-3 sentence overview",
  "recommendations": [
    {{
      "asset": "BTC",
      "action": "accumulate|hold|avoid",
      "thesis": "...",
      "position_size_pct": 10,
      "confidence": 85
    }}
  ]
}}
    """
    
    try:
        async with auto_ai_session() as (can_run_ai, pause_reason):
            if not can_run_ai:
                logger.info(f"Long-term agent skipped AI request: {pause_reason}")
                return
            ai = await get_ai_provider()
            response = await ai.ask(system_prompt, user_prompt)
        
        # 4. Parse JSON
        start_idx = response.find("{")
        end_idx = response.rfind("}") + 1
        if start_idx != -1 and end_idx != -1:
            data = json.loads(response[start_idx:end_idx])
            
            # Store new thesis into RAG automatically to build memory!
            macro = data.get("macro_comment", "")
            if macro:
                await ingest_document(
                    title=f"Macro Thesis {datetime.utcnow().strftime('%Y-%m-%d')}",
                    doc_type="thesis",
                    horizon="long_term",
                    content=macro
                )
                
            # Log recommendations
            recs = data.get("recommendations", [])
            async for session in get_session():
                for r in recs:
                    rec_db = AIRecommendation(
                        symbol=r.get("asset", "UNK"),
                        current_price=0.0, # Handled later by executors
                        suggested_allocation_usd=0.0,
                        reason=f"LONG TERM THESIS: {r.get('thesis', '')} (Action: {r.get('action')})",
                        confidence=r.get("confidence", 0),
                        sentiment=data.get("market_sentiment", "neutral")
                    )
                    session.add(rec_db)
                await session.commit()
                
            logger.info(f"Long-term analysis complete. Found {len(recs)} macro setups.")
            
    except Exception as e:
        logger.exception(f"Long-term agent failed: {e}")
