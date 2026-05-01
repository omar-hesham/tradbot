import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import text
from config.settings import get_settings


Base = declarative_base()


def get_engine():
    # 1. Check for Production Database URL (PostgreSQL)
    url = os.environ.get("DATABASE_URL")
    
    if url:
        # Standardize to async driver if using Postgres
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://")
        return create_async_engine(url, echo=False)
    
    # 2. Fallback to Local SQLite
    settings = get_settings()
    db_path = settings.DB_PATH
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else "data", exist_ok=True)
    sqlite_url = f"sqlite+aiosqlite:///{db_path}"
    return create_async_engine(sqlite_url, echo=False)


_engine = None
_session_factory = None


def _sqlite_has_column(sync_conn, table_name: str, column_name: str) -> bool:
    rows = sync_conn.execute(text(f"PRAGMA table_info('{table_name}')")).fetchall()
    return any(str(row[1]) == column_name for row in rows)


def _run_legacy_sqlite_migrations(sync_conn) -> None:
    """
    Minimal auto-migrations for legacy local SQLite databases.
    Keeps older user DBs compatible with newer ORM models.
    """
    if sync_conn.dialect.name != "sqlite":
        return

    if not _sqlite_has_column(sync_conn, "trades", "strategy"):
        sync_conn.execute(text("ALTER TABLE trades ADD COLUMN strategy VARCHAR(20)"))
    if not _sqlite_has_column(sync_conn, "trades", "realized_pnl"):
        sync_conn.execute(text("ALTER TABLE trades ADD COLUMN realized_pnl FLOAT"))


async def get_session() -> AsyncSession:
    global _engine, _session_factory
    if _engine is None:
        _engine = get_engine()
        _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    async with _session_factory() as session:
        yield session


async def init_db():
    global _engine, _session_factory
    _engine = get_engine()
    _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_run_legacy_sqlite_migrations)


async def close_db():
    global _engine
    if _engine:
        await _engine.dispose()
