"""Testes integration dos endpoints HTTP de `catalog` + `/jobs`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from catalogflow.infra.database import get_db
from catalogflow.infra.settings import get_settings
from catalogflow.infra.storage import get_storage
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.auth.router import router as auth_router
from catalogflow.modules.catalog.dependencies import get_catalog_service
from catalogflow.modules.catalog.router import router as catalog_router
from catalogflow.modules.catalog.service import CatalogService
from catalogflow.modules.catalog.tests.conftest import FakeStorage
from catalogflow.shared.errors import DomainError
from catalogflow.shared.jobs_router import router as jobs_router
from catalogflow.shared.middleware import RequestIdMiddleware

FIXTURES_DIR = Path(__file__).resolve().parents[5] / "tests" / "fixtures"


# ──────────────────────────────────────────────
#  Helpers / fakes
# ──────────────────────────────────────────────


def _load_fixture(name: str) -> bytes:
    path = FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(f"fixture {name} ausente")
    return path.read_bytes()


class _FakeAsyncResult:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


class _SpyDispatch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, catalog_id: str, job_id: str) -> _FakeAsyncResult:
        self.calls.append((catalog_id, job_id))
        return _FakeAsyncResult(task_id=f"celery-{catalog_id[:8]}")


def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
            "meta": {"request_id": "test", "timestamp": "1970-01-01T00:00:00Z"},
        },
    )


# ──────────────────────────────────────────────
#  Fixtures locais
# ──────────────────────────────────────────────


@pytest.fixture
def dispatch_spy() -> _SpyDispatch:
    return _SpyDispatch()


@pytest.fixture
def app(
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    dispatch_spy: _SpyDispatch,
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(DomainError, _domain_error_handler)

    app.include_router(auth_router)
    app.include_router(catalog_router)
    app.include_router(jobs_router)

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
        # Service com dispatch fake — impede Celery real de tentar publicar.
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
async def auth_headers(db_session: AsyncSession, brand: Brand) -> dict[str, str]:
    """Cria uma API key válida para `brand` e retorna o header pronto."""
    _, raw = await auth_service.create_api_key(
        db_session,
        brand_id=brand.id,
        name="test",
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {raw}"}


@pytest_asyncio.fixture
async def other_brand_headers(db_session: AsyncSession, other_brand: Brand) -> dict[str, str]:
    _, raw = await auth_service.create_api_key(
        db_session,
        brand_id=other_brand.id,
        name="other",
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {raw}"}


# ──────────────────────────────────────────────
#  POST /api/v1/catalogs/process
# ──────────────────────────────────────────────


class TestProcessCatalog:
    async def test_no_auth_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/catalogs/process",
            files={"file": ("c.pdf", b"%PDF-1.4 ...", "application/pdf")},
            data={"name": "c"},
        )
        assert resp.status_code == 401

    async def test_invalid_api_key_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/catalogs/process",
            headers={"Authorization": "Bearer cf_doesnotexist"},
            files={"file": ("c.pdf", b"%PDF-1.4 ...", "application/pdf")},
            data={"name": "c"},
        )
        assert resp.status_code == 401

    async def test_non_pdf_returns_400(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await client.post(
            "/api/v1/catalogs/process",
            headers=auth_headers,
            files={"file": ("c.pdf", b"definitely not a pdf", "application/pdf")},
            data={"name": "c"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_FILE_TYPE"

    async def test_oversize_returns_400(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        max_bytes = get_settings().max_pdf_size_bytes
        # 1 byte além do limite.
        payload = b"%PDF" + b"\x00" * (max_bytes - 3)  # total = max_bytes + 1
        resp = await client.post(
            "/api/v1/catalogs/process",
            headers=auth_headers,
            files={"file": ("c.pdf", payload, "application/pdf")},
            data={"name": "c"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "FILE_TOO_LARGE"

    async def test_valid_upload_returns_202_with_envelope(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        dispatch_spy: _SpyDispatch,
        fake_storage: FakeStorage,
    ) -> None:
        pdf_bytes = _load_fixture("catalogo_1_produto_1_cor.pdf")
        resp = await client.post(
            "/api/v1/catalogs/process",
            headers=auth_headers,
            files={"file": ("inverno26.pdf", pdf_bytes, "application/pdf")},
            data={"name": "Inverno 26", "collection": "MOTION"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["status"] == "pending"
        assert data["poll_url"].startswith("/api/v1/jobs/")
        assert data["catalog_id"]
        assert data["job_id"]
        # Storage recebeu o arquivo
        keys = list(fake_storage.objects)
        assert len(keys) == 1
        assert keys[0].endswith("/source.pdf")
        # Spy: dispatch_task chamado com IDs em string
        assert len(dispatch_spy.calls) == 1
        cid, jid = dispatch_spy.calls[0]
        assert cid == data["catalog_id"]
        assert jid == data["job_id"]


# ──────────────────────────────────────────────
#  GET /api/v1/catalogs/{id}
# ──────────────────────────────────────────────


class TestGetCatalog:
    async def test_returns_catalog_with_envelope(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        # Cria via endpoint
        pdf_bytes = _load_fixture("catalogo_1_produto_1_cor.pdf")
        post = await client.post(
            "/api/v1/catalogs/process",
            headers=auth_headers,
            files={"file": ("c.pdf", pdf_bytes, "application/pdf")},
            data={"name": "C"},
        )
        catalog_id = post.json()["data"]["catalog_id"]

        # Busca via endpoint
        resp = await client.get(
            f"/api/v1/catalogs/{catalog_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["id"] == catalog_id
        assert body["data"]["status"] == "pending"
        assert body["data"]["products"] == []  # ainda não processado

    async def test_other_brand_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        other_brand_headers: dict[str, str],
    ) -> None:
        # Brand A cria
        post = await client.post(
            "/api/v1/catalogs/process",
            headers=auth_headers,
            files={
                "file": (
                    "c.pdf",
                    _load_fixture("catalogo_1_produto_1_cor.pdf"),
                    "application/pdf",
                ),
            },
            data={"name": "C"},
        )
        catalog_id = post.json()["data"]["catalog_id"]

        # Brand B tenta acessar
        resp = await client.get(
            f"/api/v1/catalogs/{catalog_id}",
            headers=other_brand_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "CATALOG_NOT_FOUND"

    async def test_unknown_catalog_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await client.get(
            f"/api/v1/catalogs/{uuid4()}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ──────────────────────────────────────────────
#  GET /api/v1/catalogs/{id}/download
# ──────────────────────────────────────────────


class TestDownloadCatalog:
    async def test_pending_catalog_returns_409(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        post = await client.post(
            "/api/v1/catalogs/process",
            headers=auth_headers,
            files={
                "file": (
                    "c.pdf",
                    _load_fixture("catalogo_1_produto_1_cor.pdf"),
                    "application/pdf",
                ),
            },
            data={"name": "C"},
        )
        catalog_id = post.json()["data"]["catalog_id"]

        resp = await client.get(
            f"/api/v1/catalogs/{catalog_id}/download",
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "CATALOG_NOT_READY"

    async def test_ready_catalog_returns_302(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
        fake_storage: FakeStorage,
    ) -> None:
        from catalogflow.modules.catalog.models import Catalog

        out_key = f"{brand.id}/catalogs/manual/editable.pdf"
        fake_storage.objects[out_key] = b"%PDF-pretend-output"
        catalog = Catalog(
            brand_id=brand.id,
            name="manual",
            collection=None,
            status="ready",
            source_key=f"{brand.id}/catalogs/manual/source.pdf",
            output_key=out_key,
        )
        db_session.add(catalog)
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/catalogs/{catalog.id}/download",
            headers=auth_headers,
        )
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "fake-s3" in loc
        assert out_key in loc


# ──────────────────────────────────────────────
#  GET /api/v1/jobs/{id}
# ──────────────────────────────────────────────


class TestJobsEndpoint:
    async def test_returns_job_with_envelope(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        post = await client.post(
            "/api/v1/catalogs/process",
            headers=auth_headers,
            files={
                "file": (
                    "c.pdf",
                    _load_fixture("catalogo_1_produto_1_cor.pdf"),
                    "application/pdf",
                ),
            },
            data={"name": "C"},
        )
        job_id = post.json()["data"]["job_id"]

        resp = await client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["id"] == job_id
        assert data["job_type"] == "catalog.process"
        assert data["status"] == "pending"
        assert data["progress"] == 0

    async def test_other_brand_job_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        other_brand_headers: dict[str, str],
    ) -> None:
        post = await client.post(
            "/api/v1/catalogs/process",
            headers=auth_headers,
            files={
                "file": (
                    "c.pdf",
                    _load_fixture("catalogo_1_produto_1_cor.pdf"),
                    "application/pdf",
                ),
            },
            data={"name": "C"},
        )
        job_id = post.json()["data"]["job_id"]

        resp = await client.get(f"/api/v1/jobs/{job_id}", headers=other_brand_headers)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "JOB_NOT_FOUND"

    async def test_unknown_job_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await client.get(f"/api/v1/jobs/{uuid4()}", headers=auth_headers)
        assert resp.status_code == 404
