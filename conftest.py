"""Fixtures globais de teste.

Estrutura:
- `_pg_container` (session) — sobe Postgres 16 via testcontainers UMA vez.
- `database_url` (session) — URL `postgresql+asyncpg://` para o container.
- `_apply_migrations` (session, autouse) — patcha settings + roda Alembic upgrade head.
- `_async_engine` (session) — engine async reusável.
- `db_session` (function) — sessão async limpa entre testes (TRUNCATE no teardown).

Decisão (ADR-003): nunca SQLite, mesmo em testes. CLAUDE.md proíbe explicitamente.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: F401  (reservado p/ futuras configs)
from testcontainers.postgres import PostgresContainer

PROJECT_ROOT = Path(__file__).resolve().parent

# Tabelas alvo do TRUNCATE entre testes. Mantenha sincronizada com modelos
# conforme novos módulos forem adicionados. Ordem: filhos antes dos pais
# para o caso de FK sem CASCADE no TRUNCATE.
_TABLES_TO_TRUNCATE: tuple[str, ...] = (
    "jobs",
    "romaneios",
    "order_items",
    "orders",
    "catalog_products",
    "catalogs",
    "api_keys",
    "brands",
)


# ──────────────────────────────────────────────
#  Testcontainer
# ──────────────────────────────────────────────


@pytest.fixture(scope="session")
def _pg_container() -> Iterator[PostgresContainer]:
    """Sobe Postgres 16 efêmero (uma vez por sessão pytest)."""
    container = PostgresContainer("postgres:16-alpine", driver="asyncpg")
    with container as pg:
        yield pg


@pytest.fixture(scope="session")
def database_url(_pg_container: PostgresContainer) -> str:
    """URL DSN do container já no driver `postgresql+asyncpg://`."""
    url = _pg_container.get_connection_url()
    if "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


# ──────────────────────────────────────────────
#  Settings + migrations
# ──────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(database_url: str) -> Iterator[None]:
    """Patcha settings e aplica `alembic upgrade head` no container."""
    os.environ["DATABASE_URL"] = database_url
    os.environ.setdefault("INTERNAL_SECRET", "test-internal-secret")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use")
    # Suprime S3_PUBLIC_URL vindo do .env do dev — testes exercitam o
    # caminho de produção (presigned URL), não o atalho de download direto.
    os.environ["S3_PUBLIC_URL"] = ""

    # Limpa o cache do singleton de settings — recarrega vars novas.
    from catalogflow.infra import settings as _settings_mod

    _settings_mod.get_settings.cache_clear()

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    yield


# ──────────────────────────────────────────────
#  Engine + sessões
# ──────────────────────────────────────────────


@pytest_asyncio.fixture
async def _async_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """Engine async **function-scoped**.

    Tentativa anterior com `scope="session"` quebrou no Windows: asyncpg
    + pytest-asyncio reaproveitavam um connection pool ligado a um event
    loop fechado, resultando em `RuntimeError('Event loop is closed')` /
    `NoneType.send`. Criar engine fresco por teste é mais lento mas
    estável.

    Também limpa o singleton global de `infra/database.py` para que
    código de produção que chama `get_engine()` (ex: health endpoint) não
    reutilize um engine ligado a um loop fechado de um teste anterior.
    """
    # Garantir que qualquer engine global pré-existente seja descartado
    # antes deste teste — caso outro teste tenha criado.
    from catalogflow.infra import database as _db

    if _db._engine is not None:
        try:
            await _db._engine.dispose()
        except Exception:
            pass
        _db._engine = None
        _db._session_factory = None

    engine = create_async_engine(database_url, poolclass=NullPool, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()
        # Limpa o singleton global para o próximo teste.
        if _db._engine is not None:
            try:
                await _db._engine.dispose()
            except Exception:
                pass
            _db._engine = None
            _db._session_factory = None


@pytest_asyncio.fixture
async def db_session(_async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Sessão async limpa para cada teste; TRUNCATE no teardown."""
    factory = async_sessionmaker(_async_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session
        # commit pode ter ocorrido dentro do teste; pode não ter. Em ambos os
        # casos, o TRUNCATE abaixo limpa o estado para o próximo teste.

    truncate_sql = text(
        "TRUNCATE TABLE "
        + ", ".join(_TABLES_TO_TRUNCATE)
        + " RESTART IDENTITY CASCADE",
    )
    async with _async_engine.begin() as conn:
        await conn.execute(truncate_sql)


# ──────────────────────────────────────────────
#  Constantes úteis aos testes
# ──────────────────────────────────────────────


INTERNAL_SECRET_HEADER = {"X-Internal-Secret": "test-internal-secret"}
