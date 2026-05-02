import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from rich.logging import RichHandler
from config.settings import get_settings

def setup_logging():
    settings = get_settings()
    
    os.makedirs("logs", exist_ok=True)
    
    # Define formats
    file_format = "%(asctime)s - %(name)s - [%(levelname)s] - %(message)s"
    
    # Create file handlers
    file_handler = RotatingFileHandler("logs/app.log", maxBytes=5*1024*1024, backupCount=3)
    file_handler.setFormatter(logging.Formatter(file_format))
    
    error_handler = RotatingFileHandler("logs/error.log", maxBytes=5*1024*1024, backupCount=3)
    error_handler.setFormatter(logging.Formatter(file_format + "\n%(exc_info)s"))
    error_handler.setLevel(logging.ERROR)
    
    # Basic configuration with Rich for console
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(message)s",
        handlers=[
            RichHandler(rich_tracebacks=True),
            file_handler,
            error_handler
        ]
    )

    # Reduce noise from chatty libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return logging.getLogger("tradbot")
