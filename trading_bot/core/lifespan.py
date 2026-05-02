from contextlib import asynccontextmanager
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
from datetime import datetime, timedelta

from core.logging import setup_logging
from config.settings import get_settings
from core.database import init_db
from rich.console import Console

logger = setup_logging()
console = Console()

# We will initialize this here and export it if needed
scheduler = AsyncIOScheduler()

async def seed_rag_if_empty():
    from sqlalchemy.future import select
    from sqlalchemy import func
    from core.database import get_session
    from core.models import KnowledgeDocument
    from ai_brain.rag import ingest_document
    from data.starter_knowledge import STARTER_DOCUMENTS
    
    async for session in get_session():
        result = await session.execute(select(func.count()).select_from(KnowledgeDocument))
        count = result.scalar() or 0

    if count > 0:
        return  # Already has documents

    logger.info("RAG knowledge base is empty \u2014 seeding with starter documents...")
    for doc in STARTER_DOCUMENTS:
        try:
            await ingest_document(
                title=doc["title"],
                doc_type=doc["doc_type"],
                horizon=doc["horizon"],
                content=doc["content"],
                status="approved",
            )
        except Exception as e:
            logger.warning(f"Failed to seed RAG document '{doc['title']}': {e}")
    logger.info(f"Seeded {len(STARTER_DOCUMENTS)} starter RAG documents.")

def setup_scheduler():
    settings = get_settings()
    
    from trading.safe_runner import run_safe_short_term_agent, run_safe_hustle_agent, run_safe_long_term_agent
    from trading.agents.risk_manager import run_risk_manager_agent
    from exchange.exchange_cache import refresh_exchange_info
    from services.trailing_stop import enforce_trailing_stops
    from services.market_scanner import market_scanner
    from services.rag_reliable_feeder import refresh_reliable_market_knowledge

    # Short-Term Execution Agent
    scheduler.add_job(
        run_safe_short_term_agent,
        "interval",
        seconds=settings.TRADING_INTERVAL_SECONDS,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    
    # Trailing Stop Enforcement (Runs every 1 minute)
    scheduler.add_job(
        enforce_trailing_stops,
        "interval",
        minutes=1,
        max_instances=1,
        coalesce=True,
    )

    # Hourly exchange info cache refresh
    scheduler.add_job(
        refresh_exchange_info,
        "interval",
        hours=1,
        max_instances=1,
        coalesce=True,
    )
    
    # Risk Manager Agent (Runs every 4 hours)
    scheduler.add_job(
        run_risk_manager_agent,
        "interval",
        hours=4, 
        max_instances=1,
        coalesce=True,
    )

    # Market Scanner (Scans 50+ assets every 4 hours)
    scheduler.add_job(
        market_scanner.scan_market,
        "interval",
        hours=4,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now() + timedelta(seconds=20),
    )
    
    # Hustle Mode / Swing Agent (Runs daily)
    scheduler.add_job(
        run_safe_hustle_agent,
        "interval",
        hours=24,
        max_instances=1,
        coalesce=True,
    )
    
    # Long-Term Macro Thesis Agent (Runs weekly)
    scheduler.add_job(
        run_safe_long_term_agent,
        "interval",
        days=7,
        max_instances=1,
        coalesce=True,
    )

    if settings.RAG_RELIABLE_AUTO_REFRESH_ENABLED:
        scheduler.add_job(
            refresh_reliable_market_knowledge,
            "interval",
            hours=settings.RAG_RELIABLE_REFRESH_HOURS,
            max_instances=1,
            coalesce=True,
        )

    # State Reconciliation Service (Runs every 30 minutes)
    from services.reconciliation_service import reconciliation_service
    scheduler.add_job(
        reconciliation_service.run_reconciliation,
        "interval",
        minutes=30,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now() + timedelta(minutes=1), # Run soon after startup
    )

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    # STARTUP
    logger.info("Initializing application resources...")
    console.print("[green]Initializing database...[/green]")
    await init_db()

    console.print("[cyan]Seeding RAG knowledge base if empty...[/cyan]")
    await seed_rag_if_empty()
    
    # Prime the exchange info cache before any agent can trade
    console.print("[cyan]Loading Binance exchange info cache...[/cyan]")
    from exchange.exchange_cache import refresh_exchange_info
    await refresh_exchange_info()

    # Start Background Workers
    console.print("[cyan]Starting background tasks...[/cyan]")
    setup_scheduler()
    scheduler.start()
    
    # Start Telegram Polling
    from services.telegram_bot import telegram_polling
    telegram_task = asyncio.create_task(telegram_polling())
    
    # Start Real-Time Data Stream
    from exchange.data_stream import data_stream
    from exchange.order_validator import get_allowed_symbols
    allowed_symbols = await get_allowed_symbols()
    await data_stream.update_symbols(allowed_symbols)
    asyncio.create_task(data_stream.start())
    
    settings = get_settings()
    if settings.rag.refresh_on_startup:
        from services.rag_reliable_feeder import refresh_reliable_market_knowledge
        asyncio.create_task(refresh_reliable_market_knowledge())

    yield

    # SHUTDOWN
    logger.info("Shutting down application resources...")
    console.print("[yellow]Shutting down scheduler...[/yellow]")
    scheduler.shutdown()
    telegram_task.cancel()
    
    # Add any database connection closures or LLM pool shutdowns here
