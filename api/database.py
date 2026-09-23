"""Database engine, session management, and schema initialization."""

import asyncio
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    """Return an async SQLAlchemy URL, with a stable local SQLite default.

    The API is async, so a plain ``sqlite:///...`` URL must be upgraded to
    the aiosqlite driver.  The default path is anchored to this package rather
    than the process working directory, which can differ on Render/containers.
    """
    configured = os.getenv("DATABASE_URL")
    if not configured:
        db_path = Path(__file__).resolve().parent / "trentradar.db"
        return f"sqlite+aiosqlite:///{db_path}"

    if configured.startswith("sqlite://") and not configured.startswith("sqlite+aiosqlite://"):
        configured = "sqlite+aiosqlite://" + configured[len("sqlite://") :]

    return configured


DATABASE_URL = _database_url()
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_schema_lock = asyncio.Lock()
_schema_ready = False


async def init_db() -> None:
    """Create any missing tables before the application starts serving traffic.

    Models are imported here deliberately so ``Base.metadata`` is populated
    even if this module is used independently of ``main.py``.
    """
    global _schema_ready

    async with _schema_lock:
        if _schema_ready:
            return

        import models  # noqa: F401  # register ORM models with Base.metadata

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        _schema_ready = True


async def get_db():
    """Yield a session, ensuring the schema exists for every worker/process.

    This is intentionally defensive: ASGI lifespan startup is expected to run,
    but some deployment/test harnesses can create requests without invoking
    lifespan events.  A single locked ``create_all`` check prevents a request
    from reaching an uninitialized SQLite database in that case.
    """
    await init_db()

    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()