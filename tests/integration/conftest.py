"""Fixtures dos testes de integração.

Reaproveita o Postgres do `tests/conftest.py` e fornece um app FastAPI
real (via `create_app`) com `cache.check_connection` mockado — não há
testcontainer de Redis na Sprint 01.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra import cache as cache_module
from catalogflow.infra.database import get_db


@pytest.fixture
def app(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> FastAPI:
    """Cria um app real via `create_app()`, com Redis mockado.

    O lifespan NÃO é executado (httpx.ASGITransport não dispara), portanto
    a checagem de Redis no startup é evitada. Os endpoints que chamam
    `cache.check_connection` em runtime são atendidos pelo mock.
    """
    mock_check: Any = AsyncMock(return_value={"redis": "ok", "ping": True})
    monkeypatch.setattr(cache_module, "check_connection", mock_check)

    from catalogflow.main import create_app

    app = create_app()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = _override_get_db
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # `raise_app_exceptions=False` — sem isso, o transport re-levanta a
    # exceção original DEPOIS do exception_handler convertê-la em 500,
    # mascarando o comportamento do handler nos testes.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
