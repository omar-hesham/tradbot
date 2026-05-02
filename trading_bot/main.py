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
from rich.console import Console
from config.settings import get_settings
from core.logging import setup_logging
from core.database import init_db, close_db
from core.lifespan import app_lifespan
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





def create_app() -> FastAPI:
    settings = get_settings()
    
    setup_logging()
    
    app = FastAPI(
        title="AI Crypto Trading Bot",
        version="1.0",
        description="AI-powered cryptocurrency trading bot with multi-model AI brain",
        lifespan=app_lifespan,
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
