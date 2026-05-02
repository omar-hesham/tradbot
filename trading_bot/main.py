import logging
import os
import traceback
import asyncio
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from rich.logging import RichHandler
from rich.console import Console
from config.settings import get_settings
from core.database import init_db, close_db
# Legacy engine removed in favor of multi-horizon agents

from api.routes import settings_routes, market_routes, trading_routes, ws_routes, analytics_routes, log_routes, rag_routes

console = Console()


STARTER_DOCUMENTS = [
    {
        "title": "Core Risk Management Rules",
        "doc_type": "logic_rule",
        "horizon": "short_term",
        "content": (
            "RULE: Never risk more than 2% of total capital on a single trade.\n"
            "RULE: Always set a stop-loss before entering any position.\n"
            "RULE: Do not enter a new position when the daily loss limit is within 20% of being breached.\n"
            "RULE: Avoid trading during the 30 minutes before and after major economic announcements (FOMC, CPI, NFP).\n"
            "RULE: If a position shows unrealized loss > 3%, review immediately. Close if fundamentals have changed.\n"
            "RULE: Never average down into a losing position.\n"
            "RULE: Take partial profits at 50% of the take-profit target to lock in gains."
        ),
    },
    {
        "title": "Short-Term Execution Guidelines",
        "doc_type": "thesis",
        "horizon": "short_term",
        "content": (
            "BTC is the market leader and should be prioritized for short-term signals.\n"
            "Resistance levels for BTC: $70,000 (major), $65,000 (intermediate), $60,000 (support).\n"
            "RSI above 70 on the 1-hour chart signals overbought; prefer SELL or HOLD.\n"
            "RSI below 30 on the 1-hour chart signals oversold; look for BUY opportunities.\n"
            "Volume confirmation is essential: a breakout without volume increase is a false signal.\n"
            "ETH/BTC ratio trending up indicates altcoin season — consider broader exposure.\n"
            "In high Fear & Greed (>75), expect mean reversion. In extreme fear (<25), look for accumulation.\n"
            "Short-term trades should target 2-4% take-profit with a 1-2% stop-loss (minimum 2:1 RR)."
        ),
    },
    {
        "title": "Macro Crypto Market Thesis",
        "doc_type": "thesis",
        "horizon": "long_term",
        "content": (
            "Bitcoin halving cycles historically precede major bull runs by 6-18 months.\n"
            "Macro tailwinds: institutional adoption, ETF approvals, and regulatory clarity drive sustained rallies.\n"
            "BTC dominance above 55% signals bear market for altcoins; below 45% signals altcoin season.\n"
            "Federal Reserve rate cuts are historically bullish for risk assets including crypto.\n"
            "On-chain metric: When exchange BTC supply drops significantly, it indicates long-term holding and bullish sentiment.\n"
            "DeFi TVL growth is a leading indicator for ETH and Layer-2 ecosystem tokens.\n"
            "Macro thesis: Crypto acts as digital gold and inflation hedge; allocate 1-5% of portfolio long-term.\n"
            "Watch for: US spot ETF inflows, institutional custody announcements, and sovereign adoption news."
        ),
    },
    {
        "title": "Hustle and Swing Trading Playbook",
        "doc_type": "thesis",
        "horizon": "swing",
        "content": (
            "Swing trades should last 1-7 days targeting 5-15% moves.\n"
            "Entry criteria: price breaking above 20-day moving average with above-average volume.\n"
            "Look for coins with strong narrative catalysts: protocol upgrades, partnerships, ecosystem growth.\n"
            "Layer-1 alternatives (SOL, AVAX, ADA) tend to pump when ETH gas fees spike.\n"
            "Meme coins and low-cap altcoins are hustle territory: high risk, tight stops, small size.\n"
            "Preferred swing setups: bull flag continuation, cup-and-handle breakout, and moving average bounces.\n"
            "Exit rules: sell 50% at first target, move stop to breakeven, let remaining 50% run.\n"
            "Avoid coins with >5% bid/ask spread on CEX — poor liquidity means bad fills on exit."
        ),
    },
]


async def seed_rag_if_empty():
    """Seeds the knowledge base with starter documents if it has no approved documents."""
    from sqlalchemy import select, func
    from core.database import get_session
    from core.models import KnowledgeDocument
    from ai_brain.rag import ingest_document

    async for session in get_session():
        result = await session.execute(select(func.count()).select_from(KnowledgeDocument))
        count = result.scalar() or 0

    if count > 0:
        return  # Already has documents

    logging.getLogger(__name__).info("RAG knowledge base is empty — seeding with starter documents...")
    for doc in STARTER_DOCUMENTS:
        try:
            await ingest_document(
                title=doc["title"],
                doc_type=doc["doc_type"],
                horizon=doc["horizon"],
                content=doc["content"],
                status="approved",  # Auto-approve starter documents
            )
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to seed RAG document '{doc['title']}': {e}")
    logging.getLogger(__name__).info(f"Seeded {len(STARTER_DOCUMENTS)} starter RAG documents.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    console.print("[green]Initializing database...[/green]")
    await init_db()

    console.print("[cyan]Seeding RAG knowledge base if empty...[/cyan]")
    await seed_rag_if_empty()
    
    # Start Telegram Polling
    from services.telegram_bot import telegram_polling
    asyncio.create_task(telegram_polling())
    from services.rag_reliable_feeder import refresh_reliable_market_knowledge

    scheduler = AsyncIOScheduler()
    settings = get_settings()
    from trading.safe_runner import run_safe_short_term_agent, run_safe_hustle_agent, run_safe_long_term_agent
    from trading.agents.risk_manager import run_risk_manager_agent
    from exchange.exchange_cache import refresh_exchange_info
    
    # Prime the exchange info cache before any agent can trade
    console.print("[cyan]Loading Binance exchange info cache...[/cyan]")
    await refresh_exchange_info()
    
    # 1. Short-Term Execution Agent (Runs frequently using interval defined in UI)
    scheduler.add_job(
        run_safe_short_term_agent,
        "interval",
        seconds=settings.TRADING_INTERVAL_SECONDS,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    
    # 1.5 Trailing Stop Enforcement (Runs every 1 minute)
    from services.trailing_stop import enforce_trailing_stops
    scheduler.add_job(
        enforce_trailing_stops,
        "interval",
        minutes=1,
        max_instances=1,
        coalesce=True,
    )

    # 2. Hourly exchange info cache refresh (keeps LOT_SIZE / MIN_NOTIONAL accurate)
    scheduler.add_job(
        refresh_exchange_info,
        "interval",
        hours=1,
        max_instances=1,
        coalesce=True,
    )
    
    # 2. Risk Manager Agent (Runs every 4 hours to verify constraints)
    scheduler.add_job(
        run_risk_manager_agent,
        "interval",
        hours=4, 
        max_instances=1,
        coalesce=True,
    )

    # 3. Market Scanner (Scans 50+ assets every 4 hours)
    from services.market_scanner import market_scanner
    scheduler.add_job(
        market_scanner.scan_market,
        "interval",
        hours=4,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now() + timedelta(seconds=20),
    )
    
    # 3. Hustle Mode / Swing Agent (Runs daily at 07:00 or every 24h)
    scheduler.add_job(
        run_safe_hustle_agent,
        "interval",
        hours=24,
        max_instances=1,
        coalesce=True,
    )
    
    # 4. Long-Term Macro Thesis Agent (Runs weekly)
    scheduler.add_job(
        run_safe_long_term_agent,
        "interval",
        days=7,
        max_instances=1,
        coalesce=True,

    )

    if settings.RAG_RELIABLE_REFRESH_ON_STARTUP:
        try:
            console.print("[cyan]Refreshing reliable-source RAG context...[/cyan]")
            await asyncio.wait_for(
                refresh_reliable_market_knowledge(
                    auto_approve=True,
                    include_binance=True,
                    include_cmc=True,
                ),
                timeout=90,
            )
        except Exception as e:
            logging.getLogger(__name__).warning(f"Reliable RAG startup refresh skipped: {e}")

    if settings.RAG_RELIABLE_AUTO_REFRESH_ENABLED:
        scheduler.add_job(
            refresh_reliable_market_knowledge,
            "interval",
            hours=max(1, settings.RAG_RELIABLE_REFRESH_HOURS),
            kwargs={
                "auto_approve": True,
                "include_binance": True,
                "include_cmc": True,
            },
            max_instances=1,
            coalesce=True,
        )

    scheduler.start()
    console.print(f"[green]Bot started at {settings.APP_HOST}:{settings.APP_PORT}[/green]")
    console.print(f"[green]Dashboard: http://{settings.APP_HOST}:{settings.APP_PORT}[/green]")
    
    yield
    
    scheduler.shutdown()
    console.print("[yellow]Shutting down...[/yellow]")
    await close_db()


def create_app() -> FastAPI:
    settings = get_settings()
    
    import os
    from logging.handlers import RotatingFileHandler
    
    os.makedirs("logs", exist_ok=True)
    
    # Create file handlers
    file_handler = RotatingFileHandler("logs/app.log", maxBytes=5*1024*1024, backupCount=3)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    
    error_handler = RotatingFileHandler("logs/error.log", maxBytes=5*1024*1024, backupCount=3)
    error_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s\n%(exc_info)s"))
    error_handler.setLevel(logging.ERROR)
    
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(message)s",
        handlers=[
            RichHandler(rich_tracebacks=True), 
            file_handler, 
            error_handler
        ],
    )
    
    app = FastAPI(
        title="AI Crypto Trading Bot",
        version="1.0",
        description="AI-powered cryptocurrency trading bot with multi-model AI brain",
        lifespan=lifespan,
    )
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://{settings.APP_HOST}:{settings.APP_PORT}",
            f"http://localhost:{settings.APP_PORT}",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    base_path = os.path.dirname(os.path.abspath(__file__))
    app.mount("/static", StaticFiles(directory=os.path.join(base_path, "dashboard")), "static")
    
    app.include_router(settings_routes.router)
    app.include_router(market_routes.router)
    app.include_router(trading_routes.router)
    app.include_router(ws_routes.router)
    app.include_router(analytics_routes.router)
    app.include_router(log_routes.router)
    app.include_router(rag_routes.router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logging.getLogger("server_errors").exception(
            "Unhandled exception on %s %s", request.method, request.url.path
        )
        log_routes.append_jsonl(
            log_routes.SERVER_ERROR_LOG,
            {
                "source": "server",
                "method": request.method,
                "path": request.url.path,
                "query": str(request.url.query),
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "logged": True},
        )
    
    @app.get("/")
    async def root():
        from fastapi.responses import FileResponse
        import os
        base_path = os.path.dirname(os.path.abspath(__file__))
        return FileResponse(os.path.join(base_path, "dashboard", "index.html"))
    
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(app, host=settings.APP_HOST, port=settings.APP_PORT)
