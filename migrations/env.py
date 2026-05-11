"""Alembic environment script — modo async com SQLAlchemy 2.0.

Lê a URL do banco a partir de `catalogflow.infra.settings.get_settings()`
em vez do `alembic.ini`, de modo que o mesmo arquivo serve dev/staging/prod.

Importa explicitamente todos os modelos para que `target_metadata` enxergue
todas as tabelas durante `alembic revision --autogenerate`.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from catalogflow.infra.database import Base
from catalogflow.infra.settings import get_settings

# Importações com efeito colateral: registram os modelos em Base.metadata.
# Mantenha esta lista sincronizada quando módulos novos forem criados.
from catalogflow.modules.auth import models as _auth_models  # noqa: F401
from catalogflow.modules.catalog import models as _catalog_models  # noqa: F401

# ──────────────────────────────────────────────
#  Configuração
# ──────────────────────────────────────────────

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Injeta a URL vinda das settings — sobrescreve o placeholder do .ini.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


# ──────────────────────────────────────────────
#  Offline mode (gera SQL sem conectar)
# ──────────────────────────────────────────────


def run_migrations_offline() -> None:
    """Roda migrations em modo offline (apenas emite SQL para stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ──────────────────────────────────────────────
#  Online mode (conecta e aplica)
# ──────────────────────────────────────────────


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Cria engine async e aplica migrations dentro de uma transação."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# ──────────────────────────────────────────────
#  Dispatcher
# ──────────────────────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
