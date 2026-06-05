"""Reversibilidade da migration 0008 (`catalogs.warnings`) — ADR-011 D5.

Teste **síncrono** e auto-contido. Dois motivos:

1. `migrations/env.py` aplica migrations via `asyncio.run(...)` em modo
   online. Chamar `command.downgrade/upgrade` de dentro de um teste
   `async` quebraria com "asyncio.run() cannot be called from a running
   event loop". Logo, este teste é `def` puro (sem event loop ativo).
2. Não existe fixture `alembic_config` compartilhada (decisão registrada
   na Fase C). O teste monta seu próprio `Config` espelhando
   `conftest._apply_migrations` e **sempre restaura `head` no `finally`**:
   o container Postgres é session-scoped e compartilhado, então deixar o
   schema degradado contaminaria os testes seguintes.

Inspeção do schema via engine **async (asyncpg)** dentro de um
`asyncio.run` próprio — o projeto usa um único driver (asyncpg) e não
declara `psycopg2`. Seguro porque o teste é síncrono (não há loop
rodando), espelhando o que o `env.py` do Alembic já faz internamente.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[5]


def _alembic_config() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # env.py resolve a URL via get_settings(); DATABASE_URL já foi setado
    # por conftest._apply_migrations para o container de teste.
    return cfg


def _catalogs_columns(async_url: str) -> set[str]:
    """Nomes das colunas de `catalogs`, lidos por um engine async.

    Usa `conn.run_sync(inspect(...))` (a API de introspecção síncrona não
    roda direto sobre `AsyncConnection`) dentro de um `asyncio.run`
    próprio. Evita depender de `psycopg2` (driver sync não declarado).
    """

    async def _columns() -> set[str]:
        engine = create_async_engine(async_url, future=True)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(
                    lambda sync_conn: {
                        col["name"] for col in inspect(sync_conn).get_columns("catalogs")
                    },
                )
        finally:
            await engine.dispose()

    return asyncio.run(_columns())


@pytest.mark.usefixtures("_apply_migrations")
def test_migration_0008_is_reversible(database_url: str) -> None:
    cfg = _alembic_config()

    # head já aplicado pelo conftest → coluna presente.
    assert "warnings" in _catalogs_columns(database_url)

    try:
        command.downgrade(cfg, "0007_jobs_started_at")
        assert "warnings" not in _catalogs_columns(database_url)
    finally:
        # Restaura o schema para não contaminar a sessão compartilhada.
        command.upgrade(cfg, "head")

    assert "warnings" in _catalogs_columns(database_url)
