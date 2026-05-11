"""CatalogService — orquestra storage + DB + engines.

Tudo o que toca disco/rede/banco vive aqui. Os engines `PDFAnalyzer` e
`FieldInjector` permanecem puros (`bytes → bytes` / `bytes → dataclass`).

Multi-tenancy (CLAUDE.md): toda query inclui `brand_id` no WHERE; chaves
S3 são prefixadas por `{brand_id}/`.

Race condition de jobs (CLAUDE.md #5): a transição `pending → running` é
feita com `UPDATE WHERE status = 'pending'` para garantir que apenas um
worker assuma cada job.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalogflow.infra.settings import Settings, get_settings
from catalogflow.infra.storage import StorageClient, get_storage_client
from catalogflow.modules.catalog.field_injector import FieldInjector, count_fields
from catalogflow.modules.catalog.models import Catalog, CatalogProduct, Job
from catalogflow.modules.catalog.pdf_analyzer import (
    CatalogMetadata,
    PDFAnalyzer,
    SwatchInfo,
)
from catalogflow.shared.errors import (
    JobNotReadyError,
    NotFoundError,
    PDFCorruptError,
    PDFEncryptedError,
    PDFNoProductsError,
    PDFTooLargeError,
)

logger = logging.getLogger(__name__)

# Erros tratados como permanentes — não disparam retry no Celery.
_PERMANENT_ERRORS: tuple[type[Exception], ...] = (
    PDFCorruptError,
    PDFEncryptedError,
    PDFNoProductsError,
    PDFTooLargeError,
)


# ──────────────────────────────────────────────
#  Convenções de chave S3
# ──────────────────────────────────────────────


def source_key_for(brand_id: UUID, catalog_id: UUID) -> str:
    """Chave do PDF original — `{brand}/catalogs/{catalog}/source.pdf`."""
    return f"{brand_id}/catalogs/{catalog_id}/source.pdf"


def output_key_for(brand_id: UUID, catalog_id: UUID) -> str:
    """Chave do PDF editável — `{brand}/catalogs/{catalog}/editable.pdf`."""
    return f"{brand_id}/catalogs/{catalog_id}/editable.pdf"


# ──────────────────────────────────────────────
#  Service
# ──────────────────────────────────────────────


class CatalogService:
    """Operações de domínio sobre catálogos PDF.

    `dispatch_task` é injetável para testes — `None` faz a operação de
    enqueue em produção (`process_catalog_task.delay(...)`).
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        storage: StorageClient | None = None,
        analyzer: PDFAnalyzer | None = None,
        injector: FieldInjector | None = None,
        settings: Settings | None = None,
        dispatch_task: object | None = None,
    ) -> None:
        self.db = db
        self.storage = storage or get_storage_client()
        self.analyzer = analyzer or PDFAnalyzer()
        self.injector = injector or FieldInjector()
        self.settings = settings or get_settings()
        self._dispatch_task = dispatch_task

    # ── Criação ───────────────────────────────

    async def create_catalog(
        self,
        *,
        brand_id: UUID,
        name: str,
        collection: str | None,
        pdf_bytes: bytes,
    ) -> tuple[Catalog, Job]:
        """Valida o upload, persiste o original no storage e enfileira o job.

        Retorna o `Catalog` (status=pending) e o `Job` criado.
        """
        self._validate_size(pdf_bytes)
        self._validate_signature(pdf_bytes)

        catalog_id = uuid4()
        source_key = source_key_for(brand_id, catalog_id)
        await self.storage.upload(source_key, pdf_bytes)

        catalog = Catalog(
            id=catalog_id,
            brand_id=brand_id,
            name=name,
            collection=collection,
            status="pending",
            source_key=source_key,
        )
        job = Job(
            brand_id=brand_id,
            job_type="catalog.process",
            entity_id=catalog_id,
            status="pending",
        )
        self.db.add(catalog)
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(catalog)
        await self.db.refresh(job)

        celery_id = self._enqueue(catalog_id=catalog.id, job_id=job.id)
        if celery_id is not None:
            job.celery_id = celery_id
            await self.db.flush()

        return catalog, job

    # ── Consulta ──────────────────────────────

    async def get_catalog(self, catalog_id: UUID, brand_id: UUID) -> Catalog:
        """Recupera o catálogo verificando isolamento por brand.

        Faz eager load de `products` via `selectinload` — endpoints sempre
        precisam, e lazy load não funciona em contexto async sem greenlet.

        Levanta `NotFoundError` (404) tanto para id inexistente quanto para
        catálogo de outra brand. Não vaza informação sobre existência.
        """
        stmt = (
            select(Catalog)
            .where(Catalog.id == catalog_id, Catalog.brand_id == brand_id)
            .options(selectinload(Catalog.products))
        )
        result = await self.db.execute(stmt)
        catalog = result.scalar_one_or_none()
        if catalog is None:
            raise NotFoundError(
                f"catalog {catalog_id} não encontrado",
                code="CATALOG_NOT_FOUND",
                details={"catalog_id": str(catalog_id)},
            )
        return catalog

    async def get_download_url(self, catalog_id: UUID, brand_id: UUID) -> str:
        """Gera URL assinada para download do PDF editável.

        Levanta `JobNotReadyError` (409) se o status ainda não é `ready`.
        """
        catalog = await self.get_catalog(catalog_id, brand_id)
        if catalog.status != "ready" or not catalog.output_key:
            raise JobNotReadyError(
                f"catalog {catalog_id} ainda não está pronto (status={catalog.status})",
                code="CATALOG_NOT_READY",
                details={"catalog_id": str(catalog_id), "status": catalog.status},
            )
        return await self.storage.presigned_url(catalog.output_key)

    # ── Processamento (invocado pela Celery task) ──────────

    async def process_catalog(
        self,
        *,
        catalog_id: UUID,
        job_id: UUID,
    ) -> dict[str, Any]:
        """Pipeline completo: download → analyze → inject → upload → persist.

        Retorna um dict serializável (consumido como resultado do Celery job).
        Levanta erros de domínio em falha — o caller decide retry/erro permanente.
        """
        if not await self._claim_job(job_id):
            logger.info("job %s já foi reivindicado por outro worker", job_id)
            return {"skipped": True, "job_id": str(job_id)}

        catalog = await self.db.get(Catalog, catalog_id)
        if catalog is None:
            raise NotFoundError(
                f"catalog {catalog_id} não encontrado",
                code="CATALOG_NOT_FOUND",
            )
        if not catalog.source_key:
            raise PDFCorruptError(
                "catalog sem source_key — upload nunca foi concluído",
                code="CATALOG_MISSING_SOURCE",
            )

        catalog.status = "processing"
        await self.db.flush()

        try:
            pdf_bytes = await self.storage.download(catalog.source_key)
            metadata = self.analyzer.analyze(pdf_bytes)
            output_bytes = self.injector.inject(pdf_bytes, metadata)
            output_key = output_key_for(catalog.brand_id, catalog.id)
            await self.storage.upload(output_key, output_bytes)
            n_fields = count_fields(metadata)
            await self._persist_products(catalog.id, metadata)
            await self._mark_success(
                catalog=catalog,
                job_id=job_id,
                output_key=output_key,
                metadata=metadata,
                n_fields=n_fields,
            )
            return {
                "catalog_id": str(catalog.id),
                "n_fields": n_fields,
                "n_skus": metadata.n_skus,
                "output_key": output_key,
            }
        except Exception as exc:
            await self._mark_error(catalog=catalog, job_id=job_id, error=exc)
            raise

    # ── Helpers internos ──────────────────────

    def _validate_size(self, pdf_bytes: bytes) -> None:
        if len(pdf_bytes) > self.settings.max_pdf_size_bytes:
            raise PDFTooLargeError(
                f"upload excede {self.settings.max_pdf_size_mb}MB",
                code="FILE_TOO_LARGE",
                details={
                    "size_bytes": len(pdf_bytes),
                    "limit_bytes": self.settings.max_pdf_size_bytes,
                },
            )

    def _validate_signature(self, pdf_bytes: bytes) -> None:
        if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
            raise PDFCorruptError(
                "arquivo não é um PDF válido",
                code="INVALID_FILE_TYPE",
            )

    def _enqueue(self, *, catalog_id: UUID, job_id: UUID) -> str | None:
        """Dispara a task Celery. Retorna o celery_id (str) ou None em testes."""
        if self._dispatch_task is not None:
            result = self._dispatch_task(str(catalog_id), str(job_id))  # type: ignore[operator]
            return getattr(result, "id", None) if result is not None else None
        # Import tardio — evita ciclo entre service.py e tasks.py.
        from catalogflow.modules.catalog.tasks import process_catalog_task

        async_result = process_catalog_task.delay(str(catalog_id), str(job_id))
        return str(async_result.id)

    async def _claim_job(self, job_id: UUID) -> bool:
        """Transição pending → running com proteção contra dois workers.

        Implementa `UPDATE jobs SET status='running' WHERE id=X AND status='pending'`
        (CLAUDE.md #5). Retorna True se este worker assumiu o job.
        """
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status == "pending")
            .values(status="running", progress=10)
            .returning(Job.id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _persist_products(
        self,
        catalog_id: UUID,
        metadata: CatalogMetadata,
    ) -> None:
        for product in metadata.product_pages:
            self.db.add(
                CatalogProduct(
                    catalog_id=catalog_id,
                    sku=product.sku,
                    name=product.name,
                    grade=product.grade,
                    sizes=list(product.sizes),
                    n_colors=product.n_colors,
                    swatches=[self._swatch_to_dict(s) for s in product.swatches],
                    page_index=product.page_index,
                ),
            )
        await self.db.flush()

    @staticmethod
    def _swatch_to_dict(swatch: SwatchInfo) -> dict[str, Any]:
        return swatch.to_dict()

    async def _mark_success(
        self,
        *,
        catalog: Catalog,
        job_id: UUID,
        output_key: str,
        metadata: CatalogMetadata,
        n_fields: int,
    ) -> None:
        catalog.status = "ready"
        catalog.output_key = output_key
        catalog.n_pages = metadata.n_pages
        catalog.n_product_pages = metadata.n_product_pages
        catalog.n_skus = metadata.n_skus
        catalog.n_fields = n_fields
        catalog.error_message = None

        job = await self.db.get(Job, job_id)
        if job is not None:
            job.status = "success"
            job.progress = 100
            job.error = None
            job.result = {
                "catalog_id": str(catalog.id),
                "n_skus": metadata.n_skus,
                "n_fields": n_fields,
                "output_key": output_key,
            }

    async def _mark_error(
        self,
        *,
        catalog: Catalog,
        job_id: UUID,
        error: BaseException,
    ) -> None:
        catalog.status = "error"
        catalog.error_message = str(error)

        job = await self.db.get(Job, job_id)
        if job is not None:
            permanent = isinstance(error, _PERMANENT_ERRORS)
            job.status = "error" if permanent else "retry"
            job.error = str(error)
        # commit é responsabilidade do orquestrador (task / dependency)
        # mas garantimos flush para que mesmo se a exceção subir, o estado
        # já esteja na sessão.
        await self.db.flush()
