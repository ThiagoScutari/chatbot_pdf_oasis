"""Testes do `OrderService` — com FakeStorage e dispatch_task mockado.

Cenários cobertos:
    - create_order valida tamanho/MIME e enfileira job
    - create_order com catalog_id de outra brand → NotFoundError
    - get_order isolamento multi-tenant
    - process_order happy path (sem catálogo) — items persistidos sem enriquecimento
    - process_order com catálogo — items enriquecidos via PDFAnalyzer real
    - process_order com PDF flattened → status=error, código PDF_FLATTENED, sem retry
    - process_order com race condition — segundo worker pula
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fakes import FakeStorage

from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Catalog, CatalogProduct, Job
from catalogflow.modules.catalog.pdf_analyzer import PDFAnalyzer
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.orders.service import (
    OrderService,
    source_pdf_key_for,
)
from catalogflow.shared.errors import (
    NotFoundError,
    PDFCorruptError,
    PDFFlattenedError,
    PDFTooLargeError,
)

FIXTURES_DIR = Path(__file__).resolve().parents[5] / "tests" / "fixtures"


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────


def _load(name: str) -> bytes:
    path = FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(f"fixture {name} ausente")
    return path.read_bytes()


class _FakeDispatchResult:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


class _SpyDispatch:
    """Substituto de `extract_order_task.delay`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, order_id: str, job_id: str) -> _FakeDispatchResult:
        self.calls.append((order_id, job_id))
        return _FakeDispatchResult(task_id=f"celery-{order_id[:8]}")


@pytest.fixture
def dispatch() -> _SpyDispatch:
    return _SpyDispatch()


def _build_service(
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    dispatch: _SpyDispatch | None = None,
) -> OrderService:
    return OrderService(
        db_session,
        storage=fake_storage,  # type: ignore[arg-type]
        dispatch_task=dispatch,
    )


async def _seed_catalog(
    db_session: AsyncSession,
    brand: Brand,
    catalog_fixture: str = "catalogo_1_produto_2_cores.pdf",
) -> Catalog:
    """Cria um Catalog ready + CatalogProducts via analyzer real.

    Isso garante que o normalizer encontre o SKU `0442500912-0` (a fixture
    `pedido_preenchido_v2.pdf` foi gerada a partir deste catálogo).
    """
    pdf_bytes = _load(catalog_fixture)
    metadata = PDFAnalyzer().analyze(pdf_bytes)
    catalog = Catalog(
        brand_id=brand.id,
        name="Test Catalog",
        collection=None,
        status="ready",
        source_key=f"{brand.id}/catalogs/seed/source.pdf",
        output_key=f"{brand.id}/catalogs/seed/editable.pdf",
    )
    db_session.add(catalog)
    await db_session.flush()

    for product in metadata.product_pages:
        db_session.add(
            CatalogProduct(
                catalog_id=catalog.id,
                sku=product.sku,
                name=product.name,
                price=Decimal("1388.00"),  # preço sintético — alimenta valor_total
                grade=product.grade,
                sizes=list(product.sizes) if product.sizes is not None else [],
                n_colors=product.n_colors,
                swatches=[s.to_dict() for s in product.swatches],
                page_index=product.page_index,
            )
        )
    await db_session.commit()
    return catalog


# ──────────────────────────────────────────────
#  create_order
# ──────────────────────────────────────────────


class TestCreateOrder:
    async def test_creates_records_and_enqueues_job(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        pdf = _load("pedido_preenchido_v2.pdf")

        order, job = await service.create_order(
            brand_id=brand.id,
            pdf_bytes=pdf,
            catalog_id=None,
            lojista_name="Loja Demo",
            lojista_token="abc-123",
        )
        await db_session.commit()

        assert order.status == "draft"
        assert order.brand_id == brand.id
        assert order.source_pdf_key == source_pdf_key_for(brand.id, order.id)
        assert fake_storage.objects[order.source_pdf_key] == pdf

        assert job.status == "pending"
        assert job.job_type == "order.extract"
        assert job.entity_id == order.id
        assert job.celery_id is not None

        assert dispatch.calls == [(str(order.id), str(job.id))]

    async def test_rejects_oversize_upload(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        oversized = b"%PDF" + b"x" * (60 * 1024 * 1024)
        with pytest.raises(PDFTooLargeError):
            await service.create_order(
                brand_id=brand.id,
                pdf_bytes=oversized,
                catalog_id=None,
                lojista_name=None,
                lojista_token=None,
            )

    async def test_rejects_non_pdf_signature(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        with pytest.raises(PDFCorruptError) as exc_info:
            await service.create_order(
                brand_id=brand.id,
                pdf_bytes=b"not a pdf",
                catalog_id=None,
                lojista_name=None,
                lojista_token=None,
            )
        assert exc_info.value.code == "INVALID_FILE_TYPE"

    async def test_rejects_catalog_from_other_brand(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        other_brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        # Catalog pertence à other_brand
        foreign_catalog = await _seed_catalog(db_session, other_brand)

        with pytest.raises(NotFoundError) as exc_info:
            await service.create_order(
                brand_id=brand.id,
                pdf_bytes=_load("pedido_preenchido_v2.pdf"),
                catalog_id=foreign_catalog.id,
                lojista_name=None,
                lojista_token=None,
            )
        assert exc_info.value.code == "CATALOG_NOT_FOUND"


# ──────────────────────────────────────────────
#  get_order
# ──────────────────────────────────────────────


class TestGetOrder:
    async def test_returns_order_for_owning_brand(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        order, _ = await service.create_order(
            brand_id=brand.id,
            pdf_bytes=_load("pedido_preenchido_v2.pdf"),
            catalog_id=None,
            lojista_name=None,
            lojista_token=None,
        )
        await db_session.commit()

        fetched = await service.get_order(order.id, brand.id)
        assert fetched.id == order.id
        # items é eager-loaded via selectinload
        assert isinstance(fetched.items, list)

    async def test_other_brand_returns_not_found(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        brand: Brand,
        other_brand: Brand,
        dispatch: _SpyDispatch,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        order, _ = await service.create_order(
            brand_id=brand.id,
            pdf_bytes=_load("pedido_preenchido_v2.pdf"),
            catalog_id=None,
            lojista_name=None,
            lojista_token=None,
        )
        await db_session.commit()

        with pytest.raises(NotFoundError):
            await service.get_order(order.id, other_brand.id)

    async def test_unknown_id_returns_not_found(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage)
        with pytest.raises(NotFoundError):
            await service.get_order(uuid4(), brand.id)


# ──────────────────────────────────────────────
#  process_order
# ──────────────────────────────────────────────


async def _seed_pending_order(
    service: OrderService,
    db_session: AsyncSession,
    brand: Brand,
    fixture_name: str,
    catalog_id: object | None = None,
) -> tuple[Order, Job]:
    order, job = await service.create_order(
        brand_id=brand.id,
        pdf_bytes=_load(fixture_name),
        catalog_id=catalog_id,  # type: ignore[arg-type]
        lojista_name="L",
        lojista_token=None,
    )
    await db_session.commit()
    return order, job


class TestProcessOrder:
    async def test_happy_path_without_catalog(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        order, job = await _seed_pending_order(
            service,
            db_session,
            brand,
            "pedido_preenchido_v2.pdf",
        )
        result = await service.process_order(order_id=order.id, job_id=job.id)
        await db_session.commit()

        assert result["source_format"] == "v2"
        assert result["n_skus"] == 1
        # 1 SKU x 2 cores x 4 sizes, todos > 0
        assert result["total_pecas"] == 20

        await db_session.refresh(order)
        await db_session.refresh(job)
        assert order.status == "extracted"
        assert order.total_pecas == 20
        # Sem catálogo → unit_price None → valor_total fica None.
        assert order.valor_total is None
        assert order.extracted_at is not None
        assert job.status == "success"
        assert job.progress == 100

        # Items persistidos sem enriquecimento.
        items_stmt = select(OrderItem).where(OrderItem.order_id == order.id)
        items = list((await db_session.execute(items_stmt)).scalars())
        assert len(items) == 8
        assert all(item.product_name is None for item in items)
        assert all(item.unit_price is None for item in items)

    async def test_happy_path_with_catalog_enriches_items(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        catalog = await _seed_catalog(db_session, brand)
        service = _build_service(db_session, fake_storage, dispatch)
        order, job = await _seed_pending_order(
            service,
            db_session,
            brand,
            "pedido_preenchido_v2.pdf",
            catalog_id=catalog.id,
        )
        result = await service.process_order(order_id=order.id, job_id=job.id)
        await db_session.commit()

        assert result["source_format"] == "v2"
        # Items enriquecidos com product_name + unit_price do catálogo.
        items_stmt = select(OrderItem).where(OrderItem.order_id == order.id)
        items = list((await db_session.execute(items_stmt)).scalars())
        assert all(item.product_name is not None for item in items)
        assert all(item.unit_price == Decimal("1388.00") for item in items)

        await db_session.refresh(order)
        # 20 peças x 1388.00 = 27760.00
        assert order.valor_total == Decimal("27760.00")

    async def test_flattened_pdf_marks_error_and_does_not_persist_items(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        order, job = await _seed_pending_order(
            service,
            db_session,
            brand,
            "pedido_flattened.pdf",
        )
        with pytest.raises(PDFFlattenedError) as exc_info:
            await service.process_order(order_id=order.id, job_id=job.id)
        assert exc_info.value.code == "PDF_FLATTENED"
        await db_session.commit()

        await db_session.refresh(order)
        await db_session.refresh(job)
        assert order.status == "error"
        # Job marcado como erro (permanente) — NÃO retry.
        assert job.status == "error"
        assert job.error is not None
        assert "PDF_FLATTENED" in job.error

        # Nenhum item persistido — falha aconteceu antes do persist.
        items_stmt = select(OrderItem).where(OrderItem.order_id == order.id)
        items = list((await db_session.execute(items_stmt)).scalars())
        assert items == []

    async def test_race_condition_second_worker_skips(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        order, job = await _seed_pending_order(
            service,
            db_session,
            brand,
            "pedido_preenchido_v2.pdf",
        )
        first = await service.process_order(order_id=order.id, job_id=job.id)
        await db_session.commit()
        assert first.get("source_format") == "v2"

        second = await service.process_order(order_id=order.id, job_id=job.id)
        assert second == {"skipped": True, "job_id": str(job.id)}
