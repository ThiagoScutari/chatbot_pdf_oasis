"""Fixtures dos testes E2E.

Monta um app real via `create_app()` com:
    - get_db apontando para a sessão do testcontainer
    - get_storage trocado por FakeStorage
    - get_catalog_service injetando dispatch_task fake (Celery não roda em E2E)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra import cache as cache_module
from catalogflow.infra.database import get_db
from catalogflow.infra.storage import get_storage
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.dependencies import get_catalog_service
from catalogflow.modules.catalog.service import CatalogService
from tests.fakes import FakeStorage


class _FakeAsyncResult:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


class SpyDispatch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, catalog_id: str, job_id: str) -> _FakeAsyncResult:
        self.calls.append((catalog_id, job_id))
        return _FakeAsyncResult(task_id=f"celery-{catalog_id[:8]}")


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def dispatch_spy() -> SpyDispatch:
    return SpyDispatch()


@pytest.fixture
def app(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    dispatch_spy: SpyDispatch,
) -> FastAPI:
    # Redis check é mockado — não precisamos de Redis em E2E.
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

    async def _override_get_storage() -> FakeStorage:
        return fake_storage

    async def _override_get_catalog_service(
        db: AsyncSession = Depends(get_db),
        storage: FakeStorage = Depends(get_storage),
    ) -> CatalogService:
        return CatalogService(
            db,
            storage=storage,  # type: ignore[arg-type]
            dispatch_task=dispatch_spy,
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = _override_get_storage
    app.dependency_overrides[get_catalog_service] = _override_get_catalog_service
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def brand_with_key(
    db_session: AsyncSession,
) -> tuple[Brand, str]:
    """Cria brand + API key. Retorna `(brand, raw_key)`."""
    brand = await auth_service.create_brand(
        db_session, slug="e2e", name="E2E Brand"
    )
    _, raw = await auth_service.create_api_key(
        db_session, brand_id=brand.id, name="e2e-test"
    )
    await db_session.commit()
    return brand, raw
