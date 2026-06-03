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

Inspeção via engine síncrono (`psycopg2`) — o engine async do projeto
não pode ser usado fora de um event loop.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

PROJECT_ROOT = Path(__file__).resolve().parents[5]


def _alembic_config() -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # env.py resolve a URL via get_settings(); DATABASE_URL já foi setado
    # por conftest._apply_migrations para o container de teste.
    return cfg


def _catalogs_columns() -> set[str]:
    """Colunas atuais de `catalogs`, lidas por um engine síncrono."""
    sync_url = os.environ["DATABASE_URL"].replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        return {c["name"] for c in inspect(engine).get_columns("catalogs")}
    finally:
        engine.dispose()


@pytest.mark.usefixtures("_apply_migrations")
def test_migration_0008_is_reversible() -> None:
    cfg = _alembic_config()

    # head já aplicado pelo conftest → coluna presente.
    assert "warnings" in _catalogs_columns()

    try:
        command.downgrade(cfg, "0007_jobs_started_at")
        assert "warnings" not in _catalogs_columns()
    finally:
        # Restaura o schema para não contaminar a sessão compartilhada.
        command.upgrade(cfg, "head")

    assert "warnings" in _catalogs_columns()
