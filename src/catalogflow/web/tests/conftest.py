"""Fixtures locais aos testes do módulo `web`.

Monta um app FastAPI mínimo apenas com o web router + handlers de erro
suficientes para asserts. Reaproveita `db_session` do conftest raiz.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from catalogflow.infra.database import get_db
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand
from catalogflow.shared.errors import DomainError
from catalogflow.web.router import router as web_router


def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handler mínimo para DomainError nos testes do web."""
    assert isinstance(exc, DomainError)
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@pytest.fixture
def app(db_session: AsyncSession) -> FastAPI:
    """App FastAPI mínimo para testar o web router."""
    app = FastAPI()
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.include_router(web_router)

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
    """Cliente HTTP async sobre o ASGI in-process. follow_redirects=False
    para deixar o teste asserts em códigos 302 explícitos."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def sample_brand(db_session: AsyncSession) -> Brand:
    brand = await auth_service.create_brand(
        db_session,
        slug="oasis",
        name="Oasis Resortwear",
    )
    await db_session.commit()
    return brand


@pytest_asyncio.fixture
async def sample_api_key(
    db_session: AsyncSession, sample_brand: Brand
) -> str:
    """Cria uma API key para a brand e retorna o raw token."""
    _, raw = await auth_service.create_api_key(
        db_session,
        brand_id=sample_brand.id,
        name="web-login-test",
    )
    await db_session.commit()
    return raw
