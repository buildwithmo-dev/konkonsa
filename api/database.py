"""Database configuration for local development and Supabase/Postgres production."""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    """Return an async SQLAlchemy URL.

    Supabase/Render normally expose DATABASE_URL as a standard postgres:// URL.
    SQLAlchemy's async engine needs the asyncpg dialect, so normalize it here.
    """
    raw = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./trentradar.db").strip()
    if raw.startswith("postgres://"):
        return "postgresql+asyncpg://" + raw[len("postgres://") :]
    if raw.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw[len("postgresql://") :]
    if raw.startswith("postgresql+psycopg://"):
        return "postgresql+asyncpg://" + raw[len("postgresql+psycopg://") :]
    return raw


DATABASE_URL = _database_url()

engine_kwargs: dict = {"echo": os.getenv("SQL_ECHO", "false").lower() == "true"}
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    # Supabase recommends pooled connections for application traffic.  Keep the
    # pool bounded so a Render deploy cannot exhaust Postgres connections.
    engine_kwargs.update(
        pool_pre_ping=True,
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800")),
    )
else:
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine: AsyncEngine = create_async_engine(DATABASE_URL, **engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def check_db() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def init_db() -> None:
    """Local/test convenience only.

    Production schema changes are applied by Alembic during deployment. Keeping
    create_all out of the production startup path prevents schema drift and
    destructive surprises when multiple Render instances boot together.
    """
    if os.getenv("AUTO_CREATE_TABLES", "false").lower() != "true":
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
