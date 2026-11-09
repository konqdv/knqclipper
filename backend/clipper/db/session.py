"""Async engine/session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from clipper.db.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


async def init_db(url: str) -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"timeout": 30}
    _engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(_engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if url.startswith("sqlite"):
            await conn.run_sync(_add_missing_columns)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


# Additive, dependency-free migrations for existing SQLite databases (no Alembic yet).
_ADDED_COLUMNS = {
    "publications": [("status", "VARCHAR(16) DEFAULT 'registered'"), ("error", "TEXT"), ("log", "JSON")],
}


def _add_missing_columns(conn) -> None:  # pragma: no cover - exercised via init_db

    for table, columns in _ADDED_COLUMNS.items():
        existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns:
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


async def close_db() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("database not initialised")
    async with _sessionmaker() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with session_scope() as s:
        yield s
