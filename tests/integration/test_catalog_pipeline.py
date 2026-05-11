"""Integration test do pipeline completo de catalog.

Testa o caminho real (DB Postgres + storage in-memory + engines reais),
sem passar por HTTP nem Celery — chama `CatalogService.process_catalog`
diretamente, simulando o que a Celery task faria.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Catalog, CatalogProduct, Job
from catalogflow.modules.catalog.service import CatalogService
from tests.fakes import FakeStorage

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> bytes:
    path = FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(f"fixture {name} ausente — rode generate_fixtures.py")
    return path.read_bytes()


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage()


class _SpyDispatch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, catalog_id: str, job_id: str) -> Any:
        self.calls.append((catalog_id, job_id))

        class _R:
            id = f"celery-{catalog_id[:8]}"

        return _R()


@pytest_asyncio.fixture
async def brand(db_session: AsyncSession) -> Brand:
    b = await auth_service.create_brand(
        db_session,
        slug="pipeline-test",
        name="Pipeline Test",
    )
    await db_session.commit()
    return b


class TestCatalogPipelineEndToEnd:
    async def test_full_pipeline_creates_editable_pdf(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        brand: Brand,
    ) -> None:
        """clone → upload → process → ready → output PDF tem AcroForm."""
        dispatch = _SpyDispatch()
        service = CatalogService(
            db_session,
            storage=fake_storage,  # type: ignore[arg-type]
            dispatch_task=dispatch,
        )

        # 1) Upload — análogo ao POST /process
        pdf_in = _load("catalogo_1_produto_2_cores.pdf")
        catalog, job = await service.create_catalog(
            brand_id=brand.id,
            name="Pipeline",
            collection="DEMO",
            pdf_bytes=pdf_in,
        )
        await db_session.commit()
        assert catalog.status == "pending"
        assert job.status == "pending"
        assert dispatch.calls == [(str(catalog.id), str(job.id))]

        # 2) Worker — emula a Celery task chamando process_catalog
        result = await service.process_catalog(
            catalog_id=catalog.id,
            job_id=job.id,
        )
        await db_session.commit()
        assert result["n_skus"] == 1
        assert result["n_fields"] == 8  # 2 cores × 4 tamanhos PP-G

        # 3) Estado final do Catalog
        await db_session.refresh(catalog)
        assert catalog.status == "ready"
        assert catalog.output_key is not None
        assert catalog.n_pages == 1
        assert catalog.n_skus == 1
        assert catalog.n_fields == 8
        assert catalog.error_message is None

        # 4) Job chegou em success com result populado
        await db_session.refresh(job)
        assert job.status == "success"
        assert job.progress == 100
        assert job.result == {
            "catalog_id": str(catalog.id),
            "n_skus": 1,
            "n_fields": 8,
            "output_key": catalog.output_key,
        }

        # 5) CatalogProduct persistido
        stmt = select(CatalogProduct).where(CatalogProduct.catalog_id == catalog.id)
        products = list((await db_session.execute(stmt)).scalars())
        assert len(products) == 1
        assert products[0].sku == "0442500912-0"
        assert products[0].n_colors == 2

        # 6) PDF editável foi gravado e contém widgets corretos
        pdf_out = fake_storage.objects[catalog.output_key]
        doc = pymupdf.open(stream=pdf_out, filetype="pdf")
        try:
            widgets = [w for page in doc for w in page.widgets()]
            assert len(widgets) == 8
            # Toda nomenclatura segue v2
            for w in widgets:
                assert w.field_name.startswith("qty__0442500912-0__cor")
            # /AcroForm presente
            assert doc.is_form_pdf
        finally:
            doc.close()

    async def test_brand_isolation_in_pipeline(
        self,
        db_session: AsyncSession,
        fake_storage: FakeStorage,
        brand: Brand,
    ) -> None:
        """Catálogo da brand A não é visível para a brand B."""
        other = await auth_service.create_brand(
            db_session, slug="pipeline-other", name="Other"
        )
        await db_session.commit()

        service_a = CatalogService(
            db_session,
            storage=fake_storage,  # type: ignore[arg-type]
            dispatch_task=_SpyDispatch(),
        )
        catalog_a, _ = await service_a.create_catalog(
            brand_id=brand.id,
            name="A",
            collection=None,
            pdf_bytes=_load("catalogo_1_produto_1_cor.pdf"),
        )
        await db_session.commit()

        from catalogflow.shared.errors import NotFoundError

        with pytest.raises(NotFoundError):
            await service_a.get_catalog(catalog_a.id, other.id)

    async def test_jobs_for_brand_b_not_visible_to_brand_a(
        self,
        db_session: AsyncSession,
        brand: Brand,
    ) -> None:
        """Mesmo princípio aplicado à tabela `jobs`."""
        other = await auth_service.create_brand(
            db_session, slug="jobs-other", name="Other"
        )
        await db_session.commit()
        # Cria jobs para ambas as brands manualmente
        job_a = Job(brand_id=brand.id, job_type="catalog.process", status="pending")
        job_b = Job(brand_id=other.id, job_type="catalog.process", status="pending")
        db_session.add_all([job_a, job_b])
        await db_session.commit()

        # Query típica do endpoint /jobs/{id}
        stmt_visible = select(Job).where(Job.id == job_a.id, Job.brand_id == brand.id)
        stmt_other = select(Job).where(Job.id == job_b.id, Job.brand_id == brand.id)
        assert (await db_session.execute(stmt_visible)).scalar_one_or_none() is not None
        assert (await db_session.execute(stmt_other)).scalar_one_or_none() is None
