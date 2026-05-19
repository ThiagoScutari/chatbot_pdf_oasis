# mypy: disable-error-code="no-untyped-call,attr-defined"
# ↑ pymupdf sem stubs; testes inspecionam o PDF gerado.
"""Testes do `RomaneioService` — geração + download.

Cenários cobertos:
    - generate_romaneio enfileira task e cria registro vazio (sem output_key)
    - generate_romaneio em order não-extracted → JobNotReadyError
    - process_romaneio happy path: gera PDF e persiste output_key
    - process_romaneio busca logo do storage quando brand.logo_key existe
    - process_romaneio sem logo (logo_key None) — não quebra
    - get_download_url retorna URL quando pronto
    - get_download_url levanta NotReady quando output_key ainda None
    - get_download_url isolamento multi-tenant
"""

from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal

import pymupdf
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fakes import FakeStorage

from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Job
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.romaneio.models import Romaneio
from catalogflow.modules.romaneio.service import (
    RomaneioService,
    romaneio_output_key_for,
)
from catalogflow.shared.errors import (
    JobNotReadyError,
    NotFoundError,
)

# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────


class _FakeDispatchResult:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


class _SpyDispatch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, romaneio_id: str, job_id: str) -> _FakeDispatchResult:
        self.calls.append((romaneio_id, job_id))
        # Inclui o tamanho da lista para evitar colisão com UNIQUE(celery_id)
        # quando o mesmo Romaneio é re-enfileirado no teste de regeneração.
        return _FakeDispatchResult(task_id=f"celery-{romaneio_id[:8]}-{len(self.calls)}")


@pytest.fixture
def dispatch() -> _SpyDispatch:
    return _SpyDispatch()


def _build_service(
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    dispatch: _SpyDispatch | None = None,
) -> RomaneioService:
    return RomaneioService(
        db_session,
        storage=fake_storage,  # type: ignore[arg-type]
        dispatch_task=dispatch,
    )


def _make_logo_png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8))
    pix.clear_with(120)
    return io.BytesIO(pix.tobytes("png")).getvalue()


async def _seed_extracted_order(
    db_session: AsyncSession,
    brand: Brand,
    n_items: int = 2,
) -> Order:
    """Cria order já em status `extracted` com items prontos."""
    order = Order(
        brand_id=brand.id,
        catalog_id=None,
        lojista_name="Loja Demo",
        lojista_token=None,
        status="extracted",
        source_pdf_key=f"{brand.id}/orders/seed/source.pdf",
        total_pecas=n_items * 2,
        valor_total=Decimal("200.00"),
        extracted_at=datetime(2026, 5, 11, 14, 22),
    )
    db_session.add(order)
    await db_session.flush()

    for i in range(n_items):
        db_session.add(
            OrderItem(
                order_id=order.id,
                sku=f"SKU{i:03d}",
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


@pytest_asyncio.fixture
async def extracted_order(db_session: AsyncSession, brand: Brand) -> Order:
    return await _seed_extracted_order(db_session, brand)


# ──────────────────────────────────────────────
#  generate_romaneio
# ──────────────────────────────────────────────


class TestGenerateRomaneio:
    async def test_creates_romaneio_and_enqueues_job(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        extracted_order: Order,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        romaneio, job = await service.generate_romaneio(
            extracted_order.id,
            brand.id,
        )
        await db_session.commit()

        assert romaneio.order_id == extracted_order.id
        assert romaneio.brand_id == brand.id
        # Output_key ainda não — task vai preencher.
        assert romaneio.output_key is None

        assert job.job_type == "romaneio.generate"
        assert job.entity_id == romaneio.id
        assert job.status == "pending"
        assert job.celery_id is not None

        assert dispatch.calls == [(str(romaneio.id), str(job.id))]

    async def test_reuses_existing_romaneio_on_regeneration(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        extracted_order: Order,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        first, _ = await service.generate_romaneio(extracted_order.id, brand.id)
        await db_session.commit()
        second, _ = await service.generate_romaneio(extracted_order.id, brand.id)
        await db_session.commit()
        # UNIQUE(order_id) garante que é o mesmo registro reaproveitado.
        assert first.id == second.id

    async def test_raises_not_ready_when_order_not_extracted(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        order = Order(
            brand_id=brand.id,
            catalog_id=None,
            lojista_name=None,
            lojista_token=None,
            status="draft",
            source_pdf_key=f"{brand.id}/orders/x/source.pdf",
        )
        db_session.add(order)
        await db_session.commit()

        with pytest.raises(JobNotReadyError) as exc_info:
            await service.generate_romaneio(order.id, brand.id)
        assert exc_info.value.code == "ORDER_NOT_READY"

    async def test_other_brand_order_returns_not_found(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        other_brand: Brand,
    ) -> None:
        order = await _seed_extracted_order(db_session, brand)
        service = _build_service(db_session, fake_storage, dispatch)
        with pytest.raises(NotFoundError):
            await service.generate_romaneio(order.id, other_brand.id)


# ──────────────────────────────────────────────
#  process_romaneio
# ──────────────────────────────────────────────


class TestProcessRomaneio:
    async def test_happy_path_uploads_pdf_and_persists_key(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        extracted_order: Order,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        romaneio, job = await service.generate_romaneio(
            extracted_order.id,
            brand.id,
        )
        await db_session.commit()

        result = await service.process_romaneio(
            romaneio_id=romaneio.id,
            job_id=job.id,
        )
        await db_session.commit()

        expected_key = romaneio_output_key_for(brand.id, extracted_order.id)
        assert result["output_key"] == expected_key
        assert result["size_bytes"] > 0

        # PDF foi para o storage e é abrível pelo PyMuPDF.
        assert expected_key in fake_storage.objects
        pdf_bytes = fake_storage.objects[expected_key]
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            assert doc.page_count >= 1
        finally:
            doc.close()

        await db_session.refresh(romaneio)
        assert romaneio.output_key == expected_key

        await db_session.refresh(job)
        assert job.status == "success"
        assert job.progress == 100

    async def test_downloads_brand_logo_when_logo_key_set(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        extracted_order: Order,
    ) -> None:
        # Coloca logo no storage e referencia em Brand.logo_key.
        logo_bytes = _make_logo_png()
        logo_key = f"{brand.id}/logo.png"
        await fake_storage.upload(logo_key, logo_bytes)
        brand.logo_key = logo_key
        await db_session.commit()

        service = _build_service(db_session, fake_storage, dispatch)
        romaneio, job = await service.generate_romaneio(
            extracted_order.id,
            brand.id,
        )
        await db_session.commit()

        await service.process_romaneio(romaneio_id=romaneio.id, job_id=job.id)
        await db_session.commit()

        # PDF gerado contém pelo menos uma imagem (a logo) na primeira página.
        await db_session.refresh(romaneio)
        assert romaneio.output_key is not None
        pdf_bytes = fake_storage.objects[romaneio.output_key]
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            assert len(doc[0].get_images()) >= 1
        finally:
            doc.close()

    async def test_no_logo_does_not_break(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        extracted_order: Order,
    ) -> None:
        # brand.logo_key permanece None.
        service = _build_service(db_session, fake_storage, dispatch)
        romaneio, job = await service.generate_romaneio(
            extracted_order.id,
            brand.id,
        )
        await db_session.commit()
        result = await service.process_romaneio(
            romaneio_id=romaneio.id,
            job_id=job.id,
        )
        await db_session.commit()
        assert result["size_bytes"] > 0

    async def test_race_condition_second_worker_skips(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        extracted_order: Order,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        romaneio, job = await service.generate_romaneio(
            extracted_order.id,
            brand.id,
        )
        await db_session.commit()
        first = await service.process_romaneio(
            romaneio_id=romaneio.id,
            job_id=job.id,
        )
        await db_session.commit()
        assert "output_key" in first

        second = await service.process_romaneio(
            romaneio_id=romaneio.id,
            job_id=job.id,
        )
        assert second == {"skipped": True, "job_id": str(job.id)}


# ──────────────────────────────────────────────
#  get_download_url
# ──────────────────────────────────────────────


class TestGetDownloadUrl:
    async def test_returns_presigned_url_when_ready(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        extracted_order: Order,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        romaneio, job = await service.generate_romaneio(
            extracted_order.id,
            brand.id,
        )
        await db_session.commit()
        await service.process_romaneio(romaneio_id=romaneio.id, job_id=job.id)
        await db_session.commit()

        url = await service.get_download_url(extracted_order.id, brand.id)
        assert url.startswith("https://fake-s3/")
        assert romaneio_output_key_for(brand.id, extracted_order.id) in url

    async def test_raises_not_ready_when_output_key_missing(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        extracted_order: Order,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        # Cria romaneio sem chamar process_romaneio
        await service.generate_romaneio(extracted_order.id, brand.id)
        await db_session.commit()

        with pytest.raises(JobNotReadyError) as exc_info:
            await service.get_download_url(extracted_order.id, brand.id)
        assert exc_info.value.code == "ROMANEIO_NOT_READY"

    async def test_raises_not_found_when_no_romaneio(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        brand: Brand,
        extracted_order: Order,
    ) -> None:
        service = _build_service(db_session, fake_storage)
        with pytest.raises(NotFoundError) as exc_info:
            await service.get_download_url(extracted_order.id, brand.id)
        assert exc_info.value.code == "ROMANEIO_NOT_FOUND"

    async def test_other_brand_returns_not_found(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        other_brand: Brand,
        extracted_order: Order,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        romaneio, job = await service.generate_romaneio(
            extracted_order.id,
            brand.id,
        )
        await db_session.commit()
        await service.process_romaneio(romaneio_id=romaneio.id, job_id=job.id)
        await db_session.commit()

        with pytest.raises(NotFoundError):
            await service.get_download_url(extracted_order.id, other_brand.id)


# ──────────────────────────────────────────────
#  Helpers de teste
# ──────────────────────────────────────────────


class TestRomaneioJobBookkeeping:
    """Sanidade do registro Job — útil pro endpoint de polling (Fase F)."""

    async def test_job_created_with_correct_type(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        extracted_order: Order,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        _, job = await service.generate_romaneio(extracted_order.id, brand.id)
        await db_session.commit()

        loaded = await db_session.get(Job, job.id)
        assert loaded is not None
        assert loaded.job_type == "romaneio.generate"
        assert loaded.brand_id == brand.id

    async def test_romaneio_unique_per_order(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        extracted_order: Order,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        r1, _ = await service.generate_romaneio(extracted_order.id, brand.id)
        await db_session.commit()
        r2, _ = await service.generate_romaneio(extracted_order.id, brand.id)
        await db_session.commit()
        assert r1.id == r2.id  # mesmo registro reaproveitado

        # find_romaneio_for_brand recupera só o da brand correta.
        found = await service.find_romaneio_for_brand(
            extracted_order.id,
            brand.id,
        )
        assert found is not None
        assert found.id == r1.id

    async def test_find_romaneio_for_brand_isolation(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        other_brand: Brand,
        extracted_order: Order,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        await service.generate_romaneio(extracted_order.id, brand.id)
        await db_session.commit()
        assert (
            await service.find_romaneio_for_brand(
                extracted_order.id,
                other_brand.id,
            )
            is None
        )


_ = Romaneio  # silencia import-not-used quando os asserts não usam o tipo
