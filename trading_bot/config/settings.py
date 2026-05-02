from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel, Field

class DatabaseSettings(BaseModel):
    url: str = "sqlite+aiosqlite:///data/trading_bot.db"
    path: str = "data/trading_bot.db"
    echo: bool = False

class TradingSettings(BaseModel):
    paper_trading: bool = True
    interval_seconds: int = 60
    max_trade_usd: float = 100000000.0
    max_open_trades: int = 1000
    default_trailing_stop_pct: float = 2.0

class TelegramSettings(BaseModel):
    bot_token: str = ""
    chat_id: str = ""

class RAGSettings(BaseModel):
    auto_refresh_enabled: bool = True
    refresh_on_startup: bool = True
    refresh_hours: int = 12
    doc_ttl_hours: int = 72

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_nested_delimiter="__"
    )

    ENVIRONMENT: Literal["dev", "prod", "test"] = "dev"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    SECRET_KEY: str = ""
    CMC_API_KEY: str = ""

    # Nested configurations
    db: DatabaseSettings = DatabaseSettings()
    trading: TradingSettings = TradingSettings()
    telegram: TelegramSettings = TelegramSettings()
    rag: RAGSettings = RAGSettings()

    # Legacy mappings to not break existing code immediately
    @property
    def DB_PATH(self) -> str: return self.db.path
    
    @property
    def PAPER_TRADING(self) -> bool: return self.trading.paper_trading
    
    @property
    def TRADING_INTERVAL_SECONDS(self) -> int: return self.trading.interval_seconds
    
    @property
    def MAX_TRADE_USD(self) -> float: return self.trading.max_trade_usd
    
    @property
    def MAX_OPEN_TRADES(self) -> int: return self.trading.max_open_trades
    
    @property
    def TELEGRAM_BOT_TOKEN(self) -> str: return self.telegram.bot_token
    
    @property
    def TELEGRAM_CHAT_ID(self) -> str: return self.telegram.chat_id

    @property
    def RAG_RELIABLE_AUTO_REFRESH_ENABLED(self) -> bool: return self.rag.auto_refresh_enabled

    @property
    def RAG_RELIABLE_REFRESH_ON_STARTUP(self) -> bool: return self.rag.refresh_on_startup

    @property
    def RAG_RELIABLE_REFRESH_HOURS(self) -> int: return self.rag.refresh_hours

    @property
    def RAG_RELIABLE_DOC_TTL_HOURS(self) -> int: return self.rag.doc_ttl_hours

@lru_cache
def get_settings() -> Settings:
    return Settings()
