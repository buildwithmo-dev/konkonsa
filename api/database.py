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


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)


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
    Plain postgres:// DSN (no SQLAlchemy driver suffix) for direct asyncpg use
    — e.g. LISTEN/NOTIFY, which the SQLAlchemy async engine doesn't expose.
    Returns None when running on SQLite (local dev / pytest), so callers can
    degrade to a no-op instead of crashing.
    """
    if DATABASE_URL.startswith("postgresql+asyncpg://"):
        return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
    return None