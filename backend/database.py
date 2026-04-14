"""
Database — SQLAlchemy engine configuration.

Two engines:
  sync_engine   — psycopg2, used by pandas pd.read_sql() at startup
  async_engine  — asyncpg, used by FastAPI endpoints for live queries
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Raw PostgreSQL URL (psycopg2 / standard format)
_RAW_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://wavemap:wavemap@localhost:5432/wavemap",
)

# Build sync URL (psycopg2)
SYNC_URL = _RAW_URL
if SYNC_URL.startswith("postgresql+asyncpg://"):
    SYNC_URL = SYNC_URL.replace("postgresql+asyncpg://", "postgresql://")

# Build async URL (asyncpg)
ASYNC_URL = _RAW_URL
if not ASYNC_URL.startswith("postgresql+asyncpg://"):
    ASYNC_URL = ASYNC_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# Sync engine — used once at startup by DataManager to load DataFrame
sync_engine = create_engine(SYNC_URL, pool_pre_ping=True, pool_size=2)

# Async engine — used by FastAPI endpoint handlers
async_engine = create_async_engine(
    ASYNC_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        yield session


async def create_tables() -> None:
    """Create all tables (idempotent — safe to call on every startup).

    Silently skips if PostgreSQL is not reachable (app will fall back to demo
    data in that case).
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        from models import Track  # noqa: F401 — registers model with Base
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Create extra indexes not expressible in the model
        async with async_engine.begin() as conn:
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_tracks_genre
                    ON tracks (track_genre);
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_tracks_popularity
                    ON tracks (popularity DESC NULLS LAST);
            """))
            # Full-text search index on track_name + artist_name
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_tracks_search
                    ON tracks
                    USING gin(to_tsvector('simple',
                        coalesce(track_name, '') || ' ' || coalesce(artist_name, '')
                    ));
            """))
        logger.info("Database tables ready")
    except Exception as exc:
        logger.warning("PostgreSQL not available, skipping table creation: %s", exc)
