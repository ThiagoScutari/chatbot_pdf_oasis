"""Testes do `StockService` — adapter Mock + isolamento multi-tenant.

Cenários cobertos:
- `get_adapter` retorna Mock vs Consistem conforme `settings.erp_adapter`
- `enqueue_stock_check` cria StockCheck + Job e enfileira task
- `check_order_stock` executa o pipeline com MockAdapter e popula items
- `get_stock_check` retorna a consulta mais recente
- `enqueue_submission` + `submit_order_to_erp` happy path com MockAdapter
- `enqueue_submission` em pedido já aceito → `ConflictError`
- Pedido de outra brand → `NotFoundError`
- Submit via ConsistemAdapter levanta `NotImplementedError` (job marca `error`)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.settings import Settings
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Job
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.stock.adapter import StockAdapter, StockQuery, StockResult
from catalogflow.modules.stock.consistem_adapter import ConsistemAdapter
from catalogflow.modules.stock.mock_adapter import MockStockAdapter
from catalogflow.modules.stock.models import ErpSubmission, StockCheck
from catalogflow.modules.stock.service import StockService, summarize_stock_check
from catalogflow.shared.errors import ConflictError, NotFoundError

# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────


class _FakeDispatchResult:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


class _SpyDispatchCheck:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, order_id: str, sc_id: str, job_id: str) -> _FakeDispatchResult:
        self.calls.append((order_id, sc_id, job_id))
        return _FakeDispatchResult(task_id=f"celery-{order_id[:8]}")


class _SpyDispatchSubmit:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, order_id: str, customer_code: str, job_id: str) -> _FakeDispatchResult:
        self.calls.append((order_id, customer_code, job_id))
        return _FakeDispatchResult(task_id=f"celery-{order_id[:8]}")


class _ScriptedAdapter(StockAdapter):
    """Adapter controlável para testar submit_order custom (rejeição etc.)."""

    def __init__(
        self,
        check_results: list[StockResult],
        submit_result: dict[str, Any],
    ) -> None:
        self.check_results = check_results
        self.submit_result = submit_result
        self.check_calls = 0
        self.submit_calls = 0

    async def check_availability(self, items: list[StockQuery]) -> list[StockResult]:
        self.check_calls += 1
        return self.check_results

    async def submit_order(
        self,
        order_reference: str,
        customer_code: str,
        items: list[StockQuery],
    ) -> dict[str, Any]:
        self.submit_calls += 1
        return self.submit_result


async def _seed_order(
    db_session: AsyncSession,
    brand: Brand,
    n_items: int = 3,
) -> Order:
    """Cria Order com N OrderItems e faz commit."""
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
                color_hex="#abcdef",
                size="M",
                quantity=4,
                unit_price=Decimal("100.00"),
            )
        )
    await db_session.commit()
    await db_session.refresh(order)
    return order


# ──────────────────────────────────────────────
#  get_adapter — seleção via settings
# ──────────────────────────────────────────────


class TestGetAdapter:
    async def test_default_mock(self, db_session: AsyncSession) -> None:
        # Settings tem default `erp_adapter="mock"`.
        service = StockService(db_session, settings=Settings(erp_adapter="mock"))
        adapter = service.get_adapter()
        assert isinstance(adapter, MockStockAdapter)

    async def test_consistem_when_configured(self, db_session: AsyncSession) -> None:
        service = StockService(
            db_session,
            settings=Settings(
                erp_adapter="consistem",
                erp_base_url="https://api.test",
                erp_empresa="50",
                erp_cod_natureza=505,
            ),
        )
        adapter = service.get_adapter()
        assert isinstance(adapter, ConsistemAdapter)
        assert adapter.base_url == "https://api.test"

    async def test_override_wins(self, db_session: AsyncSession) -> None:
        """Adapter passado no __init__ prevalece sobre settings."""
        custom = MockStockAdapter()
        service = StockService(
            db_session,
            adapter=custom,
            settings=Settings(erp_adapter="consistem"),
        )
        assert service.get_adapter() is custom


# ──────────────────────────────────────────────
#  enqueue_stock_check
# ──────────────────────────────────────────────


class TestEnqueueStockCheck:
    async def test_creates_records_and_enqueues(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        dispatch = _SpyDispatchCheck()
        service = StockService(
            db_session,
            adapter=MockStockAdapter(),
            dispatch_check=dispatch,
        )

        stock_check, job = await service.enqueue_stock_check(order.id, brand.id)
        await db_session.commit()

        assert stock_check.status == "pending"
        assert stock_check.order_id == order.id
        assert stock_check.brand_id == brand.id
        assert job.job_type == "stock.check"
        assert job.entity_id == order.id
        assert job.celery_id is not None
        assert dispatch.calls == [(str(order.id), str(stock_check.id), str(job.id))]

    async def test_unknown_order_returns_not_found(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        service = StockService(db_session, adapter=MockStockAdapter())
        with pytest.raises(NotFoundError) as exc_info:
            await service.enqueue_stock_check(uuid4(), brand.id)
        assert exc_info.value.code == "ORDER_NOT_FOUND"

    async def test_other_brand_order_returns_not_found(
        self,
        db_session: AsyncSession,
        brand: Brand,
        other_brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, other_brand)
        service = StockService(db_session, adapter=MockStockAdapter())
        with pytest.raises(NotFoundError) as exc_info:
            await service.enqueue_stock_check(order.id, brand.id)
        assert exc_info.value.code == "ORDER_NOT_FOUND"


# ──────────────────────────────────────────────
#  check_order_stock (pipeline executado pela task)
# ──────────────────────────────────────────────


class TestCheckOrderStock:
    async def test_happy_path_with_mock_adapter(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand, n_items=5)
        service = StockService(
            db_session,
            adapter=MockStockAdapter(),
            dispatch_check=_SpyDispatchCheck(),
        )
        stock_check, job = await service.enqueue_stock_check(order.id, brand.id)
        await db_session.commit()

        result = await service.check_order_stock(
            order_id=order.id,
            stock_check_id=stock_check.id,
            job_id=job.id,
        )
        await db_session.commit()

        assert result["total_items"] == 5

        await db_session.refresh(stock_check)
        await db_session.refresh(job)
        assert stock_check.status == "completed"
        assert stock_check.checked_at is not None
        assert len(stock_check.result["items"]) == 5
        assert job.status == "success"
        assert job.progress == 100

        # order_items foram atualizados.
        items = list(
            (
                await db_session.execute(select(OrderItem).where(OrderItem.order_id == order.id))
            ).scalars(),
        )
        assert all(item.stock_status is not None for item in items)
        # Mock nunca devolve unknown → available_qty sempre populado.
        assert all(item.available_qty is not None for item in items)

    async def test_race_condition_second_call_skips(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand, n_items=2)
        service = StockService(
            db_session,
            adapter=MockStockAdapter(),
            dispatch_check=_SpyDispatchCheck(),
        )
        stock_check, job = await service.enqueue_stock_check(order.id, brand.id)
        await db_session.commit()

        first = await service.check_order_stock(
            order_id=order.id,
            stock_check_id=stock_check.id,
            job_id=job.id,
        )
        assert "total_items" in first

        second = await service.check_order_stock(
            order_id=order.id,
            stock_check_id=stock_check.id,
            job_id=job.id,
        )
        assert second == {"skipped": True, "job_id": str(job.id)}


# ──────────────────────────────────────────────
#  get_stock_check
# ──────────────────────────────────────────────


class TestGetStockCheck:
    async def test_returns_none_when_never_checked(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        service = StockService(db_session)
        assert await service.get_stock_check(order.id, brand.id) is None

    async def test_returns_latest_when_multiple_runs(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        service = StockService(
            db_session,
            adapter=MockStockAdapter(),
            dispatch_check=_SpyDispatchCheck(),
        )

        first, _ = await service.enqueue_stock_check(order.id, brand.id)
        await db_session.commit()
        second, _ = await service.enqueue_stock_check(order.id, brand.id)
        await db_session.commit()

        latest = await service.get_stock_check(order.id, brand.id)
        assert latest is not None
        assert latest.id == second.id
        assert latest.id != first.id

    async def test_other_brand_returns_not_found(
        self,
        db_session: AsyncSession,
        brand: Brand,
        other_brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        service = StockService(db_session)
        with pytest.raises(NotFoundError):
            await service.get_stock_check(order.id, other_brand.id)


# ──────────────────────────────────────────────
#  enqueue_submission + submit_order_to_erp
# ──────────────────────────────────────────────


class TestSubmission:
    async def test_happy_path_with_mock_adapter(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand, n_items=3)
        service = StockService(
            db_session,
            adapter=MockStockAdapter(),
            dispatch_submit=_SpyDispatchSubmit(),
        )

        submission, job = await service.enqueue_submission(
            order.id,
            brand.id,
            customer_code="LOJA-42",
        )
        await db_session.commit()

        assert submission.status == "pending"
        assert submission.brand_id == brand.id
        assert job.job_type == "stock.submit"

        result = await service.submit_order_to_erp(
            order_id=order.id,
            customer_code="LOJA-42",
            job_id=job.id,
        )
        await db_session.commit()

        await db_session.refresh(submission)
        await db_session.refresh(job)
        assert submission.status == "accepted"
        assert submission.erp_reference is not None
        assert submission.erp_reference.startswith("MOCK-")
        assert submission.submitted_at is not None
        assert job.status == "success"
        assert result["status"] == "accepted"

    async def test_rejected_marks_status_rejected(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        adapter = _ScriptedAdapter(
            check_results=[],
            submit_result={
                "accepted": False,
                "erp_reference": None,
                "rejected_items": [],
                "message": "cliente bloqueado",
            },
        )
        service = StockService(
            db_session,
            adapter=adapter,
            dispatch_submit=_SpyDispatchSubmit(),
        )
        submission, job = await service.enqueue_submission(order.id, brand.id, "C1")
        await db_session.commit()

        await service.submit_order_to_erp(
            order_id=order.id,
            customer_code="C1",
            job_id=job.id,
        )
        await db_session.commit()

        await db_session.refresh(submission)
        assert submission.status == "rejected"
        assert submission.error_message == "cliente bloqueado"

    async def test_partially_accepted_when_rejected_items_present(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        adapter = _ScriptedAdapter(
            check_results=[],
            submit_result={
                "accepted": True,
                "erp_reference": "ERP-001",
                "rejected_items": [{"sku": "SKU-X"}],
                "message": "alguns itens sem estoque",
            },
        )
        service = StockService(
            db_session,
            adapter=adapter,
            dispatch_submit=_SpyDispatchSubmit(),
        )
        submission, job = await service.enqueue_submission(order.id, brand.id, "C1")
        await db_session.commit()
        await service.submit_order_to_erp(
            order_id=order.id,
            customer_code="C1",
            job_id=job.id,
        )
        await db_session.commit()

        await db_session.refresh(submission)
        assert submission.status == "partially_accepted"
        assert submission.erp_reference == "ERP-001"

    async def test_already_accepted_raises_conflict(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, brand)
        service = StockService(
            db_session,
            adapter=MockStockAdapter(),
            dispatch_submit=_SpyDispatchSubmit(),
        )
        submission, job = await service.enqueue_submission(order.id, brand.id, "C1")
        await db_session.commit()
        await service.submit_order_to_erp(
            order_id=order.id,
            customer_code="C1",
            job_id=job.id,
        )
        await db_session.commit()
        await db_session.refresh(submission)
        assert submission.status == "accepted"

        with pytest.raises(ConflictError) as exc_info:
            await service.enqueue_submission(order.id, brand.id, "C2")
        assert exc_info.value.code == "ORDER_ALREADY_SUBMITTED"

    async def test_consistem_submit_raises_not_implemented(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        """ConsistemAdapter.submit_order não implementado — job vira `error`."""
        order = await _seed_order(db_session, brand)
        service = StockService(
            db_session,
            adapter=ConsistemAdapter(base_url="https://x", api_key=None),
            dispatch_submit=_SpyDispatchSubmit(),
        )
        submission, job = await service.enqueue_submission(order.id, brand.id, "C1")
        await db_session.commit()

        with pytest.raises(NotImplementedError):
            await service.submit_order_to_erp(
                order_id=order.id,
                customer_code="C1",
                job_id=job.id,
            )
        await db_session.commit()
        await db_session.refresh(submission)
        await db_session.refresh(job)
        assert submission.status == "error"
        # NotImplementedError é permanente — job marca `error`, não `retry`.
        assert job.status == "error"

    async def test_other_brand_returns_not_found(
        self,
        db_session: AsyncSession,
        brand: Brand,
        other_brand: Brand,
    ) -> None:
        order = await _seed_order(db_session, other_brand)
        service = StockService(
            db_session,
            adapter=MockStockAdapter(),
            dispatch_submit=_SpyDispatchSubmit(),
        )
        with pytest.raises(NotFoundError):
            await service.enqueue_submission(order.id, brand.id, "C1")


# ──────────────────────────────────────────────
#  summarize_stock_check (função pura)
# ──────────────────────────────────────────────


def test_summarize_counts_by_status() -> None:
    sc = StockCheck()
    sc.result = {
        "items": [
            {"status": "available"},
            {"status": "available"},
            {"status": "partial"},
            {"status": "out_of_stock"},
            {"status": "unknown"},
        ],
    }
    summary = summarize_stock_check(sc)
    assert summary == {
        "total_items": 5,
        "available": 2,
        "partial": 1,
        "out_of_stock": 1,
        "unknown": 1,
    }


def test_summarize_empty_result() -> None:
    sc = StockCheck()
    sc.result = {}
    summary = summarize_stock_check(sc)
    assert summary == {
        "total_items": 0,
        "available": 0,
        "partial": 0,
        "out_of_stock": 0,
        "unknown": 0,
    }
