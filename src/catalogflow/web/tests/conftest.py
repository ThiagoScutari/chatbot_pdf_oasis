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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from catalogflow.infra.database import get_db
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand, WebUser
from catalogflow.shared.errors import DomainError
from catalogflow.web.router import router as web_router
from catalogflow.web.user_service import hash_password


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
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
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
async def sample_api_key(db_session: AsyncSession, sample_brand: Brand) -> str:
    """Cria uma API key + um WebUser ativo para a brand.

    Sprint 03.5: o login passou a ser email+senha. Mantemos esta fixture
    porque ela ainda é referenciada pelos testes do web — agora ela
    garante que existe um operador `SAMPLE_USER_EMAIL` ativo. O `raw`
    devolvido continua sendo uma API key real (útil para testes que
    chamam diretamente a API REST).
    """
    _, raw = await auth_service.create_api_key(
        db_session,
        brand_id=sample_brand.id,
        name="web-login-test",
    )
    # Garante que existe operador ativo para o _login(email+senha) funcionar.
    existing = await db_session.scalar(select(WebUser).where(WebUser.email == SAMPLE_USER_EMAIL))
    if existing is None:
        db_session.add(
            WebUser(
                brand_id=sample_brand.id,
                email=SAMPLE_USER_EMAIL,
                name="Operadora Teste",
                password_hash=hash_password(SAMPLE_USER_PASSWORD),
                role="operator",
                is_active=True,
            )
        )
    await db_session.commit()
    return raw


SAMPLE_USER_EMAIL = "operadora@oasis.com.br"
SAMPLE_USER_PASSWORD = "senha-teste-123"
SAMPLE_ADMIN_EMAIL = "admin@oasis.com.br"
SAMPLE_ADMIN_PASSWORD = "admin-teste-123"


@pytest_asyncio.fixture
async def sample_user(db_session: AsyncSession, sample_brand: Brand) -> WebUser:
    """Operador ativo já aprovado — login pronto por email/senha."""
    existing = await db_session.scalar(select(WebUser).where(WebUser.email == SAMPLE_USER_EMAIL))
    if existing is not None:
        return existing
    user = WebUser(
        brand_id=sample_brand.id,
        email=SAMPLE_USER_EMAIL,
        name="Operadora Teste",
        password_hash=hash_password(SAMPLE_USER_PASSWORD),
        role="operator",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def sample_admin(db_session: AsyncSession, sample_brand: Brand) -> WebUser:
    """Admin ativo para testar painel `/admin/users`."""
    user = WebUser(
        brand_id=sample_brand.id,
        email=SAMPLE_ADMIN_EMAIL,
        name="Admin Teste",
        password_hash=hash_password(SAMPLE_ADMIN_PASSWORD),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user
