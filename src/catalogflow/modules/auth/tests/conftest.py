"""Fixtures locais ao módulo `auth`.

Reaproveita `db_session` de `tests/conftest.py` e prepara um app FastAPI
mínimo para testes do dependency `get_current_brand` e do router interno.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from catalogflow.infra.database import get_db
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.dependencies import get_current_brand
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.auth.router import router as auth_router
from catalogflow.shared.errors import DomainError


def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handler local — converte DomainError em envelope minimal.

    O handler global definitivo entra na Fase C; aqui basta o suficiente para
    asserts de status code e código de erro nos testes.
    """
    assert isinstance(exc, DomainError)
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        },
    )


@pytest.fixture
def app(db_session: AsyncSession) -> FastAPI:
    """App FastAPI mínimo — só rotas necessárias para testar auth."""
    app = FastAPI()
    app.add_exception_handler(DomainError, _domain_error_handler)

    @app.get("/whoami")
    async def whoami(brand: Brand = Depends(get_current_brand)) -> dict[str, str]:
        return {"brand_id": str(brand.id), "slug": brand.slug}

    app.include_router(auth_router)

    # Override de `get_db` — injeta a sessão de teste e replica o
    # ciclo de commit/rollback do `get_db` real.
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
    """Cliente HTTP async sobre o ASGI in-process."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def sample_brand(db_session: AsyncSession) -> Brand:
    """Brand de teste com slug fixo."""
    brand = await auth_service.create_brand(
        db_session,
        slug="acme",
        name="ACME Moda",
    )
    await db_session.commit()
    return brand


@pytest_asyncio.fixture
async def sample_api_key(db_session: AsyncSession, sample_brand: Brand) -> tuple[str, str]:
    """Cria uma API key para `sample_brand`. Retorna `(raw_key, prefix)`."""
    api_key, raw = await auth_service.create_api_key(
        db_session,
        brand_id=sample_brand.id,
        name="test-key",
    )
    await db_session.commit()
    return raw, api_key.key_prefix
