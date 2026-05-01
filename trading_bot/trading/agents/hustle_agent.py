import logging
from datetime import datetime
from sqlalchemy import select
from ai_brain.provider_factory import get_ai_provider
from ai_brain.ai_runtime import auto_ai_session
from exchange.cmc_client import CMCClient
from ai_brain.rag import search_knowledge
from core.database import get_session
from core.models import AIRecommendation, BotConfig

logger = logging.getLogger(__name__)

async def bot_is_running() -> bool:
    async for session in get_session():
        result = await session.execute(select(BotConfig).where(BotConfig.key == "bot_running"))
        config = result.scalar_one_or_none()
        return config.value == "true" if config else False


async def run_hustle_agent():
    """
    Executes the [HUSTLE] intelligence protocol.
    Scans for daily anomalies, trending coins, and narative plays (1 to 7 day swings).
    """
    if not await bot_is_running():
        logger.debug("Hustle agent skipped because bot_running=false.")
        return

    logger.info("Executing Hustle Daily Scanner Agent...")
    
    # 1. Fetch Hustle Data
    cmc = CMCClient()
    trending = await cmc.get_global_metrics() # CMC trending usually comes from specific cmc queries, simulating via global for now
    fng = await cmc.get_fear_and_greed()
    
    # 2. Query RAG for recent active narrative notes or short-lived ideas
    rag_context = await search_knowledge("What are the active altcoin narratives and recent hype volume spikes?", horizon="hustle")
    rag_text = "\n".join([f"- From {x['source']}: {x['text']}" for x in rag_context])
    
    # 3. Construct System & User Payload
    system_prompt = (
        "You are CryptoBot AI, an elite autonomous cryptocurrency trading analyst.\n"
        "Your role is to analyze market data, identify trading opportunities, and provide clear structured decisions.\n"
        "- Capital preservation is ALWAYS more important than profit generation.\n"
        "- Treat PREVIOUS KNOWLEDGE & NARRATIVES as unverified historical context, NOT as factual market data. Defer to real-time inputs.\n"
        "- Output valid JSON only.\n"
    )
    
    user_prompt = f"""
[HUSTLE] Scan the market for today's best swing trading opportunities.

=== PREVIOUS KNOWLEDGE & NARRATIVES ===
{rag_text if rag_text else "No prior narrative notes stored."}

=== INPUT DATA ===
DATE: {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC
FEAR & GREED: {fng.get('name', 'Neutral')} ({fng.get('value', 50)})
MARKET DYNAMICS: Total Marketcap is ${trending.get('total_market_cap', 0):,.2f}

=== TASK ===
Identify 1-3 swing trading opportunities for the next 1-7 days based on current sentiment.
Respond ONLY in this JSON format:
{{
  "scan_date": "YYYY-MM-DD",
  "market_mood": "risk_on|risk_off|mixed",
  "opportunities": [
    {{
      "asset": "SYMBOL",
      "opportunity_type": "trending|volume_spike|narrative_play",
      "narrative": "What is the story driving this?",
      "confidence": 0
    }}
  ]
}}
    """
    
    try:
        async with auto_ai_session() as (can_run_ai, pause_reason):
            if not can_run_ai:
                logger.info(f"Hustle agent skipped AI request: {pause_reason}")
                return
            ai = await get_ai_provider()
            response = await ai.ask(system_prompt, user_prompt)
        
        # 4. Parse JSON
        start_idx = response.find("{")
        end_idx = response.rfind("}") + 1
        if start_idx != -1 and end_idx != -1:
            data = json.loads(response[start_idx:end_idx])
            
            recs = data.get("opportunities", [])
            async for session in get_session():
                for r in recs:
                    rec_db = AIRecommendation(
                        symbol=r.get("asset", "UNK"),
                        current_price=0.0,
                        suggested_allocation_usd=0.0,
                        reason=f"HUSTLE SWING (1-7D): {r.get('narrative', '')}",
                        confidence=r.get("confidence", 0),
                        sentiment=data.get("market_mood", "mixed")
                    )
                    session.add(rec_db)
                await session.commit()
                
            logger.info(f"Hustle agent complete. Found {len(recs)} swing setups.")
            
    except Exception as e:
        logger.exception(f"Hustle agent failed: {e}")
