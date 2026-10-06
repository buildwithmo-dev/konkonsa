import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


load_dotenv()


DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not configured. "
        "Set it to your Supabase PostgreSQL connection string."
    )


# Supabase commonly provides:
# postgresql://...
# or
# postgres://...
#
# SQLAlchemy async requires the asyncpg driver.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+asyncpg://",
        1,
    )
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql+asyncpg://",
        1,
    )


# Supabase session-mode poolers commonly cap client sessions at the
# configured pool size (often 15 on small projects). SQLAlchemy's defaults
# are pool_size=5 + max_overflow=10, which can therefore hit that ceiling
# before the application itself has meaningful load. Keep the application
# pool explicitly bounded and configurable per deployment.
def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}, got {value}")
    return value


engine_kwargs = {
    "echo": False,
    "pool_pre_ping": True,
}

# Pool sizing is only meaningful for PostgreSQL. SQLite's async driver uses
# its own pool implementation and should keep SQLAlchemy's defaults.
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    engine_kwargs.update(
        pool_size=_env_int("DB_POOL_SIZE", 5, minimum=1),
        max_overflow=_env_int("DB_MAX_OVERFLOW", 0),
        pool_timeout=_env_int("DB_POOL_TIMEOUT", 30, minimum=1),
        pool_recycle=_env_int("DB_POOL_RECYCLE", 1800, minimum=0),
    )

engine = create_async_engine(DATABASE_URL, **engine_kwargs)


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    Initialize database tables.

    Production migrations should eventually be handled
    through Alembic rather than create_all().
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def check_db():
    """Verify that the database connection is working."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

def raw_postgres_dsn() -> str | None:
    """
    Plain postgres:// DSN (no SQLAlchemy driver suffix) for the long-lived
    asyncpg LISTEN connection, which needs a dedicated session. Notifications
    use the bounded SQLAlchemy pool instead. Returns None on SQLite so realtime
    can degrade to a no-op in local development/tests.
    """
    if DATABASE_URL.startswith("postgresql+asyncpg://"):
        return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
    return None
