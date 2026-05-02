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
from data.starter_knowledge import STARTER_DOCUMENTS

console = Console()






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
