"""Camada de banco de dados — engine async, sessão, base declarativa.

Convenções (ADR-003):
- Postgres 16 com driver `asyncpg`. Sem SQLite, nem em dev/teste.
- Sessão por request via `get_db()` (FastAPI dependency).
- Modelos herdam de `Base` (DeclarativeBase) — sem `Base.metadata.create_all()`
  em código de produção: schema é gerenciado por Alembic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from catalogflow.infra.settings import get_settings

# Convenção de nomes para constraints — facilita migrations e debugging.
# Ver: https://alembic.sqlalchemy.org/en/latest/naming.html
_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base declarativa para todos os modelos ORM do CatalogFlow."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)


# ──────────────────────────────────────────────
#  Engine + session factory (singletons)
# ──────────────────────────────────────────────

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Retorna o engine async, criando-o na primeira chamada."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,
            echo=settings.database_echo,
            future=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Factory de `AsyncSession` ligada ao engine corrente."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependency FastAPI: sessão por request com commit/rollback automáticos.

    Em endpoints, usar:
        async def endpoint(db: AsyncSession = Depends(get_db)) -> ...:
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Fecha o engine e libera o pool. Chamado no shutdown da aplicação."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def check_connection() -> dict[str, Any]:
    """Healthcheck simples: roda `SELECT 1`. Levanta em caso de falha."""
    from sqlalchemy import text

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        value = result.scalar_one()
        return {"db": "ok", "value": value}
