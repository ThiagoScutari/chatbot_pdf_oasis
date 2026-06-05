"""Reversibilidade da migration 0009 (`brands.format_profile_id`) — ADR-010 D2.

Espelha `test_migration_0008` (corrigido): teste **síncrono** e
auto-contido, inspeção de schema via engine **async (asyncpg)** dentro de
um `asyncio.run` próprio (o projeto declara apenas asyncpg, nunca
psycopg2). Restaura `head` no `finally` — o container Postgres é
session-scoped e compartilhado, então deixar o schema degradado
contaminaria os testes seguintes.
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
    return cfg


def _brands_columns(async_url: str) -> set[str]:
    """Nomes das colunas de `brands`, lidos por um engine async (asyncpg)."""

    async def _columns() -> set[str]:
        engine = create_async_engine(async_url, future=True)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(
                    lambda sync_conn: {
                        col["name"] for col in inspect(sync_conn).get_columns("brands")
                    },
                )
        finally:
            await engine.dispose()

    return asyncio.run(_columns())


@pytest.mark.usefixtures("_apply_migrations")
def test_migration_0009_is_reversible(database_url: str) -> None:
    cfg = _alembic_config()

    # head já aplicado pelo conftest → coluna presente.
    assert "format_profile_id" in _brands_columns(database_url)

    try:
        command.downgrade(cfg, "0008_catalogs_warnings")
        assert "format_profile_id" not in _brands_columns(database_url)
    finally:
        # Restaura o schema para não contaminar a sessão compartilhada.
        command.upgrade(cfg, "head")

    assert "format_profile_id" in _brands_columns(database_url)
