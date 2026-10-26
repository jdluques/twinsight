"""Motor async de SQLAlchemy 2 y utilidades de sesión.

SQLite con WAL por defecto (RNF-006); el cambio a PostgreSQL es solo
configuración (``TWINSIGHT_DATABASE_URL``).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings


class Base(DeclarativeBase):
    """Base declarativa de todos los modelos relacionales."""


def new_id() -> str:
    """UUID v4 de aplicación (PK TEXT, Diseño §3.2)."""
    return str(uuid.uuid4())


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _create_engine() -> AsyncEngine:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, future=True)
    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _rec):  # pragma: no cover - glue
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _create_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def init_db() -> None:
    """Crea las tablas si no existen (MVP; Alembic en producción)."""
    from . import models  # noqa: F401 - registra los modelos en Base.metadata

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependencia FastAPI: una sesión por request."""
    async with get_sessionmaker()() as session:
        yield session
