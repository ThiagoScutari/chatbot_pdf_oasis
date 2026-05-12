"""Integration test do pipeline completo de pedido (Sprint 02 §E7).

Chama os métodos `process_*` dos services diretamente — mesmo trabalho que
as Celery tasks fazem, sem a complicação de `asyncio.run` em thread + global
engine zumbi no Windows. Cobertura específica das tasks fica em
`tests/integration/test_celery_tasks.py`.

Fluxo:
    1. CatalogService.create_catalog + process_catalog.
    2. Preenche programaticamente os widgets do output (contrato AcroForm).
    3. OrderService.create_order + process_order com `catalog_id` do passo 1.
    4. Verifica Order.status == "extracted" + items enriquecidos.
    5. RomaneioService.generate_romaneio + process_romaneio.
    6. Download retorna PDF válido com o SKU + lojista no texto.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra import storage as storage_module
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Catalog, CatalogProduct, Job
from catalogflow.modules.catalog.service import CatalogService
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.orders.service import OrderService
from catalogflow.modules.romaneio.models import Romaneio
from catalogflow.modules.romaneio.service import RomaneioService
from catalogflow.shared.errors import PDFFlattenedError
from tests.fakes import FakeStorage

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────


def _load(name: str) -> bytes:
    path = FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(f"fixture {name} ausente — rode generate_fixtures.py")
    return path.read_bytes()


class _NoopDispatch:
    """Substitui `.delay()` real — evita toque no broker Redis nos testes."""

    def __call__(self, *args: str) -> None:
        return None


@pytest.fixture
def shared_storage() -> Iterator[FakeStorage]:
    """FakeStorage compartilhado entre catalog/orders/romaneio services.

    Também substitui o singleton global por compatibilidade caso algum
    componente caia no `get_storage_client()`.
    """
    original = storage_module._storage
    fake = FakeStorage()
    storage_module._storage = fake  # type: ignore[assignment]
    try:
        yield fake
    finally:
        storage_module._storage = original


@pytest_asyncio.fixture
async def brand(db_session: AsyncSession) -> Brand:
    b = await auth_service.create_brand(
        db_session,
        slug="pipeline-orders",
        name="Pipeline Orders Brand",
    )
    await db_session.commit()
    return b


def _fill_widgets(pdf_bytes: bytes, mapping: dict[str, str]) -> bytes:
    """Preenche widgets do PDF in-memory e devolve os bytes resultantes."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            for widget in page.widgets() or []:
                if widget.field_name in mapping:
                    widget.field_value = mapping[widget.field_name]
                    widget.update()
        out: bytes = doc.tobytes(clean=True, garbage=4, deflate=True)
    finally:
        doc.close()
    return out


# ──────────────────────────────────────────────
#  Test principal — pipeline ponta a ponta
# ──────────────────────────────────────────────


class TestOrderPipelineEndToEnd:
    async def test_full_pipeline_catalog_to_romaneio(
        self,
        db_session: AsyncSession,
        shared_storage: FakeStorage,
        brand: Brand,
    ) -> None:
        noop = _NoopDispatch()

        # ── 1) Catalog: upload + processamento
        catalog_service = CatalogService(
            db_session,
            storage=shared_storage,  # type: ignore[arg-type]
            dispatch_task=noop,
        )
        pdf_catalog = _load("catalogo_1_produto_2_cores.pdf")
        catalog, catalog_job = await catalog_service.create_catalog(
            brand_id=brand.id,
            name="Pipeline Inverno 26",
            collection="MOTION",
            pdf_bytes=pdf_catalog,
        )
        await db_session.commit()

        result = await catalog_service.process_catalog(
            catalog_id=catalog.id,
            job_id=catalog_job.id,
        )
        await db_session.commit()
        assert result["n_skus"] == 1
        assert result["n_fields"] == 8  # 1 SKU x 2 cores x 4 sizes

        catalog_db = await db_session.get(Catalog, catalog.id)
        assert catalog_db is not None
        await db_session.refresh(catalog_db)
        assert catalog_db.status == "ready"
        assert catalog_db.output_key is not None

        # PDFAnalyzer não extrai preço (não está codificado nas fixtures
        # sintéticas). Em produção o ERP popula isso depois — aqui simulamos.
        prods_stmt = select(CatalogProduct).where(
            CatalogProduct.catalog_id == catalog_db.id,
        )
        for product in (await db_session.execute(prods_stmt)).scalars():
            product.price = Decimal("1388.00")
        await db_session.commit()

        # ── 2) Preenche o PDF editável programaticamente
        editable_bytes = shared_storage.objects[catalog_db.output_key]
        sku = "0442500912-0"
        fill_mapping = {
            f"qty__{sku}__cor1__PP": "2",
            f"qty__{sku}__cor1__P": "3",
            f"qty__{sku}__cor1__M": "1",
            f"qty__{sku}__cor1__G": "4",
            f"qty__{sku}__cor2__PP": "1",
            f"qty__{sku}__cor2__P": "2",
            f"qty__{sku}__cor2__M": "0",  # zero descartado pelo extractor
            f"qty__{sku}__cor2__G": "3",
        }
        filled_bytes = _fill_widgets(editable_bytes, fill_mapping)

        check_doc = pymupdf.open(stream=filled_bytes, filetype="pdf")
        try:
            assert check_doc.is_form_pdf
        finally:
            check_doc.close()

        # ── 3) Order: upload + processamento
        order_service = OrderService(
            db_session,
            storage=shared_storage,  # type: ignore[arg-type]
            dispatch_task=noop,
        )
        order, order_job = await order_service.create_order(
            brand_id=brand.id,
            pdf_bytes=filled_bytes,
            catalog_id=catalog_db.id,
            lojista_name="Loja Pipeline Demo",
            lojista_token=None,
        )
        await db_session.commit()

        order_result = await order_service.process_order(
            order_id=order.id,
            job_id=order_job.id,
        )
        await db_session.commit()
        assert order_result["source_format"] == "v2"
        # 2+3+1+4+1+2+3 = 16 (M=0 da cor2 descartado)
        assert order_result["total_pecas"] == 16

        order_db = await db_session.get(Order, order.id)
        assert order_db is not None
        await db_session.refresh(order_db)
        assert order_db.status == "extracted"

        items_stmt = select(OrderItem).where(OrderItem.order_id == order.id)
        items = list((await db_session.execute(items_stmt)).scalars())
        assert len(items) == 7
        assert all(item.product_name is not None for item in items)
        assert all(item.unit_price is not None for item in items)

        # ── 4) Romaneio: geração + processamento
        romaneio_service = RomaneioService(
            db_session,
            storage=shared_storage,  # type: ignore[arg-type]
            dispatch_task=noop,
        )
        romaneio, romaneio_job = await romaneio_service.generate_romaneio(
            order.id,
            brand.id,
        )
        await db_session.commit()
        assert romaneio.output_key is None

        await romaneio_service.process_romaneio(
            romaneio_id=romaneio.id,
            job_id=romaneio_job.id,
        )
        await db_session.commit()

        romaneio_db = await db_session.get(Romaneio, romaneio.id)
        assert romaneio_db is not None
        await db_session.refresh(romaneio_db)
        assert romaneio_db.output_key is not None

        # ── 5) Download retorna PDF válido com SKU + lojista
        url = await romaneio_service.get_download_url(order.id, brand.id)
        assert url.startswith("https://fake-s3/")

        pdf_bytes = shared_storage.objects[romaneio_db.output_key]
        assert len(pdf_bytes) > 0
        out_doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            assert out_doc.page_count >= 1
            text = out_doc[0].get_text()
            assert sku in text
            assert "Loja Pipeline Demo" in text
        finally:
            out_doc.close()

        # ── 6) Todos os 3 jobs em status="success"
        for job_id in (catalog_job.id, order_job.id, romaneio_job.id):
            job = await db_session.get(Job, job_id)
            assert job is not None
            await db_session.refresh(job)
            assert job.status == "success", (
                f"job {job_id} ficou em {job.status}, esperado 'success'"
            )

    async def test_flattened_pdf_results_in_permanent_error(
        self,
        db_session: AsyncSession,
        shared_storage: FakeStorage,
        brand: Brand,
    ) -> None:
        """PDF achatado → status='error' (permanente, sem retry)."""
        order_service = OrderService(
            db_session,
            storage=shared_storage,  # type: ignore[arg-type]
            dispatch_task=_NoopDispatch(),
        )
        flattened_bytes = _load("pedido_flattened.pdf")
        order, order_job = await order_service.create_order(
            brand_id=brand.id,
            pdf_bytes=flattened_bytes,
            catalog_id=None,
            lojista_name=None,
            lojista_token=None,
        )
        await db_session.commit()

        with pytest.raises(PDFFlattenedError):
            await order_service.process_order(
                order_id=order.id,
                job_id=order_job.id,
            )
        await db_session.commit()

        order_db = await db_session.get(Order, order.id)
        assert order_db is not None
        await db_session.refresh(order_db)
        assert order_db.status == "error"

        job_db = await db_session.get(Job, order_job.id)
        assert job_db is not None
        await db_session.refresh(job_db)
        assert job_db.status == "error"  # permanente, não 'retry'
        assert job_db.error is not None
        assert "PDF_FLATTENED" in job_db.error
