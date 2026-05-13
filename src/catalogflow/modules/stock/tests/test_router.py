"""Testes integration dos endpoints HTTP de `stock`.

Espelha `orders/tests/test_router.py`: FastAPI isolado com middleware de
request_id, exception handler de DomainError, overrides de get_db e
get_stock_service injetando MockStockAdapter + dispatch spies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from catalogflow.infra.database import get_db
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.auth.router import router as auth_router
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.stock.adapter import StockAdapter, StockQuery, StockResult
from catalogflow.modules.stock.dependencies import get_stock_service
from catalogflow.modules.stock.mock_adapter import MockStockAdapter
from catalogflow.modules.stock.router import router as stock_router
from catalogflow.modules.stock.service import StockService
from catalogflow.shared.errors import DomainError
from catalogflow.shared.middleware import RequestIdMiddleware


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────


class _FakeAsyncResult:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


class _SpyDispatch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> _FakeAsyncResult:
        self.calls.append(args)
        return _FakeAsyncResult(task_id=f"celery-{len(self.calls)}")


class _InProcessAdapter(StockAdapter):
    """Adapter que executa direto na request — útil para testar
    GET /stock-check sem precisar de Celery worker rodando."""

    async def check_availability(self, items: list[StockQuery]) -> list[StockResult]:
        return [
            StockResult(
                sku=q.sku,
                size=q.size,
                color_index=q.color_index,
                requested_qty=q.requested_qty,
                available_qty=q.requested_qty,
                status="available",
            )
            for q in items
        ]

    async def submit_order(
        self,
        order_reference: str,
        customer_code: str,
        items: list[StockQuery],
    ) -> dict[str, Any]:
        return {
            "accepted": True,
            "erp_reference": "TEST-REF-001",
            "rejected_items": [],
            "message": "ok",
        }


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


async def _seed_order(
    db_session: AsyncSession,
    brand: Brand,
    n_items: int = 3,
) -> Order:
    order = Order(
        brand_id=brand.id,
        status="extracted",
        source_pdf_key=f"{brand.id}/orders/test/source.pdf",
        total_pecas=n_items * 4,
    )
    db_session.add(order)
    await db_session.flush()
    for i in range(n_items):
        db_session.add(
            OrderItem(
                order_id=order.id,
                sku=f"SKU-{i:04d}",
                product_name=f"Produto {i}",
                color_index=1,
                color_hex="#aabbcc",
                size="M",
                quantity=4,
                unit_price=Decimal("100.00"),
            ),
        )
    await db_session.commit()
    await db_session.refresh(order)
    return order


# ──────────────────────────────────────────────
#  Fixtures
# ──────────────────────────────────────────────


@pytest.fixture
def check_dispatch() -> _SpyDispatch:
    return _SpyDispatch()


@pytest.fixture
def submit_dispatch() -> _SpyDispatch:
    return _SpyDispatch()


@pytest.fixture
def stock_adapter() -> _InProcessAdapter:
    return _InProcessAdapter()


@pytest.fixture
def app(
    db_session: AsyncSession,
    stock_adapter: _InProcessAdapter,
    check_dispatch: _SpyDispatch,
    submit_dispatch: _SpyDispatch,
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.include_router(auth_router)
    app.include_router(stock_router)

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    async def _override_stock_service(
        db: AsyncSession = Depends(get_db),
    ) -> StockService:
        return StockService(
            db,
            adapter=stock_adapter,
            dispatch_check=check_dispatch,
            dispatch_submit=submit_dispatch,
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_stock_service] = _override_stock_service
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
        db_session, brand_id=brand.id, name="test",
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {raw}"}


@pytest_asyncio.fixture
async def other_brand_headers(
    db_session: AsyncSession,
    other_brand: Brand,
) -> dict[str, str]:
    _, raw = await auth_service.create_api_key(
        db_session, brand_id=other_brand.id, name="other",
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {raw}"}


# ──────────────────────────────────────────────
#  POST /api/v1/orders/{id}/stock-check
# ──────────────────────────────────────────────


class TestPostStockCheck:
    async def test_no_auth_returns_401(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        resp = await client.post(f"/api/v1/orders/{order.id}/stock-check")
        assert resp.status_code == 401

    async def test_returns_202_with_ids(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
        check_dispatch: _SpyDispatch,
    ) -> None:
        order = await _seed_order(db_session, brand)
        resp = await client.post(
            f"/api/v1/orders/{order.id}/stock-check",
            headers=auth_headers,
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["status"] == "pending"
        assert body["data"]["stock_check_id"]
        assert body["data"]["job_id"]
        assert len(check_dispatch.calls) == 1

    async def test_unknown_order_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await client.post(
            f"/api/v1/orders/{uuid4()}/stock-check",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "ORDER_NOT_FOUND"

    async def test_cross_tenant_returns_404(
        self,
        client: AsyncClient,
        other_brand_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        """Pedido da `brand`, request autenticada como `other_brand` → 404."""
        order = await _seed_order(db_session, brand)
        resp = await client.post(
            f"/api/v1/orders/{order.id}/stock-check",
            headers=other_brand_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "ORDER_NOT_FOUND"


# ──────────────────────────────────────────────
#  GET /api/v1/orders/{id}/stock-check
# ──────────────────────────────────────────────


class TestGetStockCheck:
    async def test_no_check_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        resp = await client.get(
            f"/api/v1/orders/{order.id}/stock-check",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "STOCK_CHECK_NOT_FOUND"

    async def test_returns_summary_and_items_after_run(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
        stock_adapter: _InProcessAdapter,
        check_dispatch: _SpyDispatch,
        submit_dispatch: _SpyDispatch,
    ) -> None:
        order = await _seed_order(db_session, brand, n_items=3)
        # Enfileira e executa o pipeline diretamente para popular o resultado.
        service = StockService(
            db_session,
            adapter=stock_adapter,
            dispatch_check=check_dispatch,
            dispatch_submit=submit_dispatch,
        )
        sc, job = await service.enqueue_stock_check(order.id, brand.id)
        await db_session.commit()
        await service.check_order_stock(
            order_id=order.id, stock_check_id=sc.id, job_id=job.id,
        )
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/orders/{order.id}/stock-check",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "completed"
        assert data["summary"]["total_items"] == 3
        # _InProcessAdapter sempre devolve "available".
        assert data["summary"]["available"] == 3
        assert len(data["items"]) == 3
        assert data["items"][0]["status"] == "available"
        assert data["items"][0]["available"] == 4

    async def test_cross_tenant_returns_404(
        self,
        client: AsyncClient,
        other_brand_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        resp = await client.get(
            f"/api/v1/orders/{order.id}/stock-check",
            headers=other_brand_headers,
        )
        assert resp.status_code == 404


# ──────────────────────────────────────────────
#  POST /api/v1/orders/{id}/submit
# ──────────────────────────────────────────────


class TestPostSubmit:
    async def test_returns_202_with_ids(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
        submit_dispatch: _SpyDispatch,
    ) -> None:
        order = await _seed_order(db_session, brand)
        resp = await client.post(
            f"/api/v1/orders/{order.id}/submit",
            headers=auth_headers,
            json={"customer_code": "LOJA-42"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["data"]["status"] == "pending"
        assert body["data"]["submission_id"]
        assert body["data"]["job_id"]
        # spy recebeu (order_id, customer_code, job_id)
        assert len(submit_dispatch.calls) == 1
        assert submit_dispatch.calls[0][1] == "LOJA-42"

    async def test_missing_customer_code_returns_422(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        resp = await client.post(
            f"/api/v1/orders/{order.id}/submit",
            headers=auth_headers,
            json={},
        )
        assert resp.status_code == 422

    async def test_empty_customer_code_returns_422(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        resp = await client.post(
            f"/api/v1/orders/{order.id}/submit",
            headers=auth_headers,
            json={"customer_code": ""},
        )
        assert resp.status_code == 422

    async def test_unknown_order_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = await client.post(
            f"/api/v1/orders/{uuid4()}/submit",
            headers=auth_headers,
            json={"customer_code": "X"},
        )
        assert resp.status_code == 404

    async def test_cross_tenant_returns_404(
        self,
        client: AsyncClient,
        other_brand_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        resp = await client.post(
            f"/api/v1/orders/{order.id}/submit",
            headers=other_brand_headers,
            json={"customer_code": "X"},
        )
        assert resp.status_code == 404


# ──────────────────────────────────────────────
#  GET /api/v1/orders/{id}/submission
# ──────────────────────────────────────────────


class TestGetSubmission:
    async def test_no_submission_returns_404(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        resp = await client.get(
            f"/api/v1/orders/{order.id}/submission",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SUBMISSION_NOT_FOUND"

    async def test_returns_accepted_status_and_reference(
        self,
        client: AsyncClient,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        brand: Brand,
        stock_adapter: _InProcessAdapter,
        check_dispatch: _SpyDispatch,
        submit_dispatch: _SpyDispatch,
    ) -> None:
        order = await _seed_order(db_session, brand)
        service = StockService(
            db_session,
            adapter=stock_adapter,
            dispatch_check=check_dispatch,
            dispatch_submit=submit_dispatch,
        )
        submission, job = await service.enqueue_submission(
            order.id, brand.id, "LOJA-42",
        )
        await db_session.commit()
        await service.submit_order_to_erp(
            order_id=order.id, customer_code="LOJA-42", job_id=job.id,
        )
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/orders/{order.id}/submission",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "accepted"
        assert data["erp_reference"] == "TEST-REF-001"
        assert data["submitted_at"] is not None
