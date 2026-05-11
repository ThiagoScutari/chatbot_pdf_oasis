"""Testes do `CatalogService` — com FakeStorage e dispatch_task mockado."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Catalog, CatalogProduct, Job
from catalogflow.modules.catalog.service import (
    CatalogService,
    output_key_for,
    source_key_for,
)
from catalogflow.modules.catalog.tests.conftest import FakeStorage
from catalogflow.shared.errors import (
    JobNotReadyError,
    NotFoundError,
    PDFCorruptError,
    PDFEncryptedError,
    PDFNoProductsError,
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
    """Mimics `AsyncResult` de Celery — `.id` é o que o service lê."""

    def __init__(self, task_id: str) -> None:
        self.id = task_id


class _SpyDispatch:
    """Substituto de `process_catalog_task.delay` para verificar enqueue."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, catalog_id: str, job_id: str) -> _FakeDispatchResult:
        self.calls.append((catalog_id, job_id))
        return _FakeDispatchResult(task_id=f"celery-{catalog_id[:8]}")


@pytest.fixture
def dispatch() -> _SpyDispatch:
    return _SpyDispatch()


def _build_service(
    db_session: AsyncSession,
    fake_storage: FakeStorage,
    dispatch: _SpyDispatch | None = None,
) -> CatalogService:
    return CatalogService(
        db_session,
        storage=fake_storage,  # type: ignore[arg-type]
        dispatch_task=dispatch,
    )


# ──────────────────────────────────────────────
#  create_catalog
# ──────────────────────────────────────────────


class TestCreateCatalog:
    async def test_creates_records_and_enqueues_job(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        pdf_bytes = _load("catalogo_1_produto_1_cor.pdf")

        catalog, job = await service.create_catalog(
            brand_id=brand.id,
            name="Inverno 26",
            collection="MOTION",
            pdf_bytes=pdf_bytes,
        )
        await db_session.commit()

        # Catalog persistido em pending com source_key
        assert catalog.status == "pending"
        assert catalog.source_key == source_key_for(brand.id, catalog.id)
        assert catalog.brand_id == brand.id

        # Bytes foram para o storage na chave correta
        assert fake_storage.objects[catalog.source_key] == pdf_bytes

        # Job em pending, com celery_id preenchido
        assert job.status == "pending"
        assert job.job_type == "catalog.process"
        assert job.entity_id == catalog.id
        assert job.celery_id is not None

        # Dispatch foi chamado com (catalog_id, job_id) em string
        assert dispatch.calls == [(str(catalog.id), str(job.id))]

    async def test_rejects_oversize_upload(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        # +1 byte além do limite
        huge = b"%PDF" + b"\x00" * (service.settings.max_pdf_size_bytes)
        with pytest.raises(PDFTooLargeError) as exc_info:
            await service.create_catalog(
                brand_id=brand.id,
                name="big",
                collection=None,
                pdf_bytes=huge,
            )
        assert exc_info.value.code == "FILE_TOO_LARGE"

    async def test_rejects_non_pdf_signature(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        with pytest.raises(PDFCorruptError) as exc_info:
            await service.create_catalog(
                brand_id=brand.id,
                name="bad",
                collection=None,
                pdf_bytes=b"not a pdf",
            )
        assert exc_info.value.code == "INVALID_FILE_TYPE"


# ──────────────────────────────────────────────
#  get_catalog (isolamento multi-tenant)
# ──────────────────────────────────────────────


class TestGetCatalog:
    async def test_returns_catalog_for_owning_brand(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        catalog, _ = await service.create_catalog(
            brand_id=brand.id,
            name="A",
            collection=None,
            pdf_bytes=_load("catalogo_1_produto_1_cor.pdf"),
        )
        await db_session.commit()
        found = await service.get_catalog(catalog.id, brand.id)
        assert found.id == catalog.id

    async def test_returns_not_found_for_other_brand(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
        other_brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        catalog, _ = await service.create_catalog(
            brand_id=brand.id,
            name="A",
            collection=None,
            pdf_bytes=_load("catalogo_1_produto_1_cor.pdf"),
        )
        await db_session.commit()
        with pytest.raises(NotFoundError) as exc_info:
            await service.get_catalog(catalog.id, other_brand.id)
        assert exc_info.value.code == "CATALOG_NOT_FOUND"

    async def test_returns_not_found_for_unknown_id(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage)
        with pytest.raises(NotFoundError):
            await service.get_catalog(uuid4(), brand.id)


# ──────────────────────────────────────────────
#  get_download_url
# ──────────────────────────────────────────────


class TestGetDownloadUrl:
    async def test_raises_not_ready_when_pending(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        catalog, _ = await service.create_catalog(
            brand_id=brand.id,
            name="A",
            collection=None,
            pdf_bytes=_load("catalogo_1_produto_1_cor.pdf"),
        )
        await db_session.commit()
        with pytest.raises(JobNotReadyError) as exc_info:
            await service.get_download_url(catalog.id, brand.id)
        assert exc_info.value.code == "CATALOG_NOT_READY"

    async def test_returns_presigned_url_when_ready(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        brand: Brand,
    ) -> None:
        # Cria catalog ja em ready manualmente
        out_key = f"{brand.id}/catalogs/manual/editable.pdf"
        catalog = Catalog(
            brand_id=brand.id,
            name="ready",
            collection=None,
            status="ready",
            source_key=f"{brand.id}/catalogs/manual/source.pdf",
            output_key=out_key,
        )
        db_session.add(catalog)
        await db_session.commit()

        service = _build_service(db_session, fake_storage)
        url = await service.get_download_url(catalog.id, brand.id)
        assert url.startswith("https://fake-s3/")
        assert out_key in url


# ──────────────────────────────────────────────
#  process_catalog (pipeline completo)
# ──────────────────────────────────────────────


async def _seed_pending_catalog(
    service: CatalogService,
    db_session: AsyncSession,
    brand: Brand,
    fixture_name: str,
) -> tuple[Catalog, Job]:
    catalog, job = await service.create_catalog(
        brand_id=brand.id,
        name="x",
        collection=None,
        pdf_bytes=_load(fixture_name),
    )
    await db_session.commit()
    return catalog, job


class TestProcessCatalog:
    async def test_happy_path_updates_status_and_persists_products(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        catalog, job = await _seed_pending_catalog(
            service, db_session, brand, "catalogo_1_produto_2_cores.pdf"
        )

        result = await service.process_catalog(
            catalog_id=catalog.id,
            job_id=job.id,
        )
        await db_session.commit()

        # Resultado serializável
        assert isinstance(result, dict)
        assert result["n_skus"] == 1
        assert result["n_fields"] == 8  # 2 cores × 4 tamanhos PP-G
        assert result["output_key"] == output_key_for(brand.id, catalog.id)

        # Catalog atualizado
        await db_session.refresh(catalog)
        assert catalog.status == "ready"
        assert catalog.output_key == result["output_key"]
        assert catalog.n_fields == 8
        assert catalog.n_pages == 1
        assert catalog.n_skus == 1

        # Job atualizado
        await db_session.refresh(job)
        assert job.status == "success"
        assert job.progress == 100
        assert job.result is not None
        assert job.result["n_fields"] == 8

        # CatalogProducts persistidos
        stmt = select(CatalogProduct).where(CatalogProduct.catalog_id == catalog.id)
        products = list((await db_session.execute(stmt)).scalars())
        assert len(products) == 1
        assert products[0].n_colors == 2

        # Bytes do output PDF estão no storage e contêm widgets
        assert result["output_key"] in fake_storage.objects
        assert fake_storage.objects[result["output_key"]] != fake_storage.objects[catalog.source_key]

    async def test_encrypted_pdf_marks_catalog_and_job_as_error(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        catalog, job = await _seed_pending_catalog(
            service, db_session, brand, "pdf_criptografado.pdf"
        )
        with pytest.raises(PDFEncryptedError):
            await service.process_catalog(
                catalog_id=catalog.id,
                job_id=job.id,
            )
        await db_session.commit()

        await db_session.refresh(catalog)
        await db_session.refresh(job)
        assert catalog.status == "error"
        assert catalog.error_message
        assert job.status == "error"
        assert job.error

    async def test_no_products_pdf_marks_error(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        service = _build_service(db_session, fake_storage, dispatch)
        catalog, job = await _seed_pending_catalog(
            service, db_session, brand, "pdf_sem_produtos.pdf"
        )
        with pytest.raises(PDFNoProductsError):
            await service.process_catalog(
                catalog_id=catalog.id,
                job_id=job.id,
            )
        await db_session.commit()
        await db_session.refresh(job)
        assert job.status == "error"

    async def test_race_condition_second_worker_skips(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        dispatch: _SpyDispatch,
        brand: Brand,
    ) -> None:
        """`UPDATE WHERE status='pending'` impede dois workers de processar o mesmo job."""
        service = _build_service(db_session, fake_storage, dispatch)
        catalog, job = await _seed_pending_catalog(
            service, db_session, brand, "catalogo_1_produto_1_cor.pdf"
        )
        # Primeiro worker faz o trabalho completo
        first = await service.process_catalog(catalog_id=catalog.id, job_id=job.id)
        await db_session.commit()
        assert first.get("output_key")

        # Segundo worker (mesmo `process_catalog`) — não-pending, deve pular
        second = await service.process_catalog(catalog_id=catalog.id, job_id=job.id)
        assert second == {"skipped": True, "job_id": str(job.id)}


# ──────────────────────────────────────────────
#  Helpers de chave
# ──────────────────────────────────────────────


class TestKeyHelpers:
    def test_source_key_includes_brand_id(self) -> None:
        from uuid import UUID as _U

        bid = _U("00000000-0000-0000-0000-000000000001")
        cid = _U("00000000-0000-0000-0000-00000000000a")
        assert source_key_for(bid, cid).startswith(f"{bid}/")
        assert source_key_for(bid, cid).endswith("/source.pdf")
        assert output_key_for(bid, cid).endswith("/editable.pdf")
