from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    SECRET_KEY: str = ""
    CMC_API_KEY: str = ""


    DB_PATH: str = "data/trading_bot.db"

    PAPER_TRADING: bool = True
    TRADING_INTERVAL_SECONDS: int = 60
    MAX_TRADE_USD: float = 100000000.0
    MAX_OPEN_TRADES: int = 1000

    RAG_RELIABLE_AUTO_REFRESH_ENABLED: bool = True
    RAG_RELIABLE_REFRESH_ON_STARTUP: bool = True
    RAG_RELIABLE_REFRESH_HOURS: int = 12
    RAG_RELIABLE_DOC_TTL_HOURS: int = 72


@lru_cache
def get_settings() -> Settings:
    return Settings()
