"""Testes integration dos endpoints HTTP de `orders`.

Espelha o padrão de `catalog/tests/test_router.py`: monta um FastAPI
isolado com `RequestIdMiddleware`, exception handler de `DomainError`,
e overrides de `get_db`/`get_storage`/services com fakes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
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
from catalogflow.infra.storage import get_storage
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.auth.router import router as auth_router
from catalogflow.modules.orders.dependencies import (
    get_order_service,
    get_romaneio_service,
)
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.orders.router import router as orders_router
from catalogflow.modules.orders.service import OrderService
from catalogflow.modules.orders.tests.conftest import FakeStorage
from catalogflow.modules.romaneio.models import Romaneio
from catalogflow.modules.romaneio.service import (
    RomaneioService,
    romaneio_output_key_for,
)
from catalogflow.shared.errors import DomainError
from catalogflow.shared.jobs_router import router as jobs_router
from catalogflow.shared.middleware import RequestIdMiddleware

FIXTURES_DIR = Path(__file__).resolve().parents[5] / "tests" / "fixtures"


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────


def _load(name: str) -> bytes:
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

    def __call__(self, a: str, b: str) -> _FakeAsyncResult:
        self.calls.append((a, b))
        return _FakeAsyncResult(task_id=f"celery-{a[:8]}-{len(self.calls)}")


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
def order_dispatch() -> _SpyDispatch:
    return _SpyDispatch()


@pytest.fixture
def romaneio_dispatch() -> _SpyDispatch:
    return _SpyDispatch()


@pytest.fixture
def app(
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    order_dispatch: _SpyDispatch,
    romaneio_dispatch: _SpyDispatch,
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(DomainError, _domain_error_handler)

    app.include_router(auth_router)
    app.include_router(orders_router)
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

    async def _override_order_service(
        db: AsyncSession = Depends(get_db),
        storage: FakeStorage = Depends(get_storage),
    ) -> OrderService:
        return OrderService(
            db,
            storage=storage,  # type: ignore[arg-type]
            dispatch_task=order_dispatch,
        )

    async def _override_romaneio_service(
        db: AsyncSession = Depends(get_db),
        storage: FakeStorage = Depends(get_storage),
    ) -> RomaneioService:
        return RomaneioService(
            db,
            storage=storage,  # type: ignore[arg-type]
            dispatch_task=romaneio_dispatch,
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = _override_get_storage
    app.dependency_overrides[get_order_service] = _override_order_service
    app.dependency_overrides[get_romaneio_service] = _override_romaneio_service
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
    _, raw = await auth_service.create_api_key(
        db_session,
        brand_id=brand.id,
        name="test",
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {raw}"}


@pytest_asyncio.fixture
async def other_brand_headers(
    db_session: AsyncSession,
    other_brand: Brand,
) -> dict[str, str]:
    _, raw = await auth_service.create_api_key(
        db_session,
        brand_id=other_brand.id,
        name="other",
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {raw}"}


# ──────────────────────────────────────────────
#  POST /api/v1/orders/extract
# ──────────────────────────────────────────────


class TestExtractOrder:
    async def test_no_auth_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/orders/extract",
            files={"file": ("o.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert resp.status_code == 401

    async def test_non_pdf_returns_400(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await client.post(
            "/api/v1/orders/extract",
            headers=auth_headers,
            files={"file": ("o.pdf", b"not a pdf", "application/pdf")},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_FILE_TYPE"

    async def test_oversize_returns_400(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        from catalogflow.infra.settings import get_settings

        max_bytes = get_settings().max_pdf_size_bytes
        payload = b"%PDF" + b"\x00" * (max_bytes - 3)  # max_bytes + 1
        resp = await client.post(
            "/api/v1/orders/extract",
            headers=auth_headers,
            files={"file": ("o.pdf", payload, "application/pdf")},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "FILE_TOO_LARGE"

    async def test_valid_upload_returns_202(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        order_dispatch: _SpyDispatch,
        fake_storage: FakeStorage,
    ) -> None:
        pdf = _load("pedido_preenchido_v2.pdf")
        resp = await client.post(
            "/api/v1/orders/extract",
            headers=auth_headers,
            files={"file": ("p.pdf", pdf, "application/pdf")},
            data={"lojista_name": "Loja Demo"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["status"] == "draft"
        assert data["order_id"]
        assert data["job_id"]
        assert data["poll_url"].startswith("/api/v1/jobs/")
        assert len(order_dispatch.calls) == 1
        # PDF foi para o storage com prefixo da brand.
        keys = [k for k in fake_storage.objects if k.endswith("/source.pdf")]
        assert len(keys) == 1

    async def test_catalog_id_from_other_brand_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        other_brand: Brand,
    ) -> None:
        # Cria catálogo em other_brand
        from catalogflow.modules.catalog.models import Catalog

        foreign = Catalog(
            brand_id=other_brand.id,
            name="x",
            collection=None,
            status="ready",
            source_key=f"{other_brand.id}/catalogs/x/source.pdf",
            output_key=f"{other_brand.id}/catalogs/x/editable.pdf",
        )
        db_session.add(foreign)
        await db_session.commit()

        resp = await client.post(
            "/api/v1/orders/extract",
            headers=auth_headers,
            files={"file": ("p.pdf", _load("pedido_preenchido_v2.pdf"), "application/pdf")},
            data={"catalog_id": str(foreign.id)},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "CATALOG_NOT_FOUND"


# ──────────────────────────────────────────────
#  GET /api/v1/orders/{order_id}
# ──────────────────────────────────────────────


async def _seed_extracted_order(
    db_session: AsyncSession,
    brand: Brand,
    with_items: bool = True,
) -> Order:
    order = Order(
        brand_id=brand.id,
        catalog_id=None,
        lojista_name="Demo Lojista",
        lojista_token=None,
        status="extracted",
        source_pdf_key=f"{brand.id}/orders/seed/source.pdf",
        total_pecas=4 if with_items else 0,
        valor_total=Decimal("200.00") if with_items else None,
        extracted_at=datetime(2026, 5, 11, 14, 22),
    )
    db_session.add(order)
    await db_session.flush()
    if with_items:
        for i in range(2):
            db_session.add(
                OrderItem(
                    order_id=order.id,
                    sku=f"SKU{i:02d}",
                    product_name=f"Produto {i}",
                    color_index=1,
                    color_hex=None,
                    size="PP",
                    quantity=2,
                    unit_price=Decimal("50.00"),
                )
            )
    await db_session.commit()
    return order


class TestGetOrder:
    async def test_returns_order_with_envelope(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_extracted_order(db_session, brand)
        resp = await client.get(
            f"/api/v1/orders/{order.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["id"] == str(order.id)
        assert data["lojista_name"] == "Demo Lojista"
        assert data["status"] == "extracted"
        assert len(data["items"]) == 2
        # Subtotal calculado: 2 * 50 = 100 por item, total_pecas=4, valor=200
        assert data["totals"]["total_pecas"] == 4
        assert data["totals"]["n_skus"] == 2

    async def test_other_brand_returns_404(
        self,
        client: AsyncClient,
        other_brand_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_extracted_order(db_session, brand)
        resp = await client.get(
            f"/api/v1/orders/{order.id}",
            headers=other_brand_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "ORDER_NOT_FOUND"

    async def test_unknown_id_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await client.get(
            f"/api/v1/orders/{uuid4()}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ──────────────────────────────────────────────
#  GET /api/v1/orders/{order_id}/romaneio
# ──────────────────────────────────────────────


class TestGetOrderRomaneio:
    async def test_returns_202_with_job_when_not_started(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
        romaneio_dispatch: _SpyDispatch,
    ) -> None:
        order = await _seed_extracted_order(db_session, brand)
        resp = await client.get(
            f"/api/v1/orders/{order.id}/romaneio",
            headers=auth_headers,
        )
        assert resp.status_code == 202
        body = resp.json()
        data = body["data"]
        assert data["status"] == "pending"
        assert data["job_id"]
        assert data["download_url"] is None
        # Romaneio task enfileirada.
        assert len(romaneio_dispatch.calls) == 1

    async def test_returns_302_when_ready(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
        fake_storage: FakeStorage,
    ) -> None:
        order = await _seed_extracted_order(db_session, brand)
        # Cria romaneio já com output_key (e bytes no storage)
        output_key = romaneio_output_key_for(brand.id, order.id)
        await fake_storage.upload(output_key, b"%PDF-1.4 fake")
        romaneio = Romaneio(
            order_id=order.id,
            brand_id=brand.id,
            output_key=output_key,
        )
        db_session.add(romaneio)
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/orders/{order.id}/romaneio",
            headers=auth_headers,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.startswith("https://fake-s3/")
        assert output_key in location

    async def test_returns_202_when_romaneio_processing(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
        romaneio_dispatch: _SpyDispatch,
    ) -> None:
        order = await _seed_extracted_order(db_session, brand)
        # Romaneio existe mas sem output_key (em geração)
        romaneio = Romaneio(
            order_id=order.id,
            brand_id=brand.id,
            output_key=None,
        )
        db_session.add(romaneio)
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/orders/{order.id}/romaneio",
            headers=auth_headers,
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["data"]["status"] == "processing"
        assert body["data"]["job_id"]

    async def test_other_brand_returns_404(
        self,
        client: AsyncClient,
        other_brand_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_extracted_order(db_session, brand)
        resp = await client.get(
            f"/api/v1/orders/{order.id}/romaneio",
            headers=other_brand_headers,
        )
        assert resp.status_code == 404


# ──────────────────────────────────────────────
#  Polling via /api/v1/jobs/{id} reconhece order.extract / romaneio.generate
# ──────────────────────────────────────────────


class TestJobsEndpointForOrderTypes:
    async def test_returns_order_extract_job(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        # Cria pedido → job de extract
        resp = await client.post(
            "/api/v1/orders/extract",
            headers=auth_headers,
            files={
                "file": (
                    "p.pdf",
                    _load("pedido_preenchido_v2.pdf"),
                    "application/pdf",
                ),
            },
        )
        assert resp.status_code == 202
        job_id = resp.json()["data"]["job_id"]

        resp = await client.get(
            f"/api/v1/jobs/{job_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["job_type"] == "order.extract"
        assert data["status"] == "pending"
