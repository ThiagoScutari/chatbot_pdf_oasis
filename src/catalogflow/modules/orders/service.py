"""OrderService — orquestra storage + DB + engines de pedido.

Tudo o que toca disco/rede/banco vive aqui. Os engines `OrderExtractor` e
`OrderNormalizer` permanecem puros.

Multi-tenancy (CLAUDE.md): toda query inclui `brand_id` no WHERE; chaves
S3 são prefixadas por `{brand_id}/`.

Race condition de jobs (CLAUDE.md #5): a transição `pending → running` é
feita com `UPDATE WHERE status = 'pending'`.

PDFFlattenedError é tratado como erro permanente — NÃO dispara retry no
Celery (Armadilha #3 do PRD).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalogflow.infra.settings import Settings, get_settings
from catalogflow.infra.storage import StorageClient, get_storage_client
from catalogflow.modules.catalog.models import Catalog, CatalogProduct, Job
from catalogflow.modules.orders.extractor import OrderExtractor
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.orders.normalizer import (
    NormalizedOrderData,
    NormalizedOrderItem,
    OrderNormalizer,
)
from catalogflow.shared.errors import (
    NotFoundError,
    PDFCorruptError,
    PDFFlattenedError,
    PDFTooLargeError,
)

logger = logging.getLogger(__name__)

# Erros tratados como permanentes — não disparam retry no Celery.
# PDFFlattenedError é o cenário-chave da Fase E: arquivo achatado nunca vai
# voltar a ter `/AcroForm` por reprocessamento.
_PERMANENT_ERRORS: tuple[type[Exception], ...] = (
    PDFCorruptError,
    PDFFlattenedError,
    PDFTooLargeError,
)


# ──────────────────────────────────────────────
#  Convenções de chave S3
# ──────────────────────────────────────────────


def source_pdf_key_for(brand_id: UUID, order_id: UUID) -> str:
    """`{brand}/orders/{order}/source.pdf` — PDF preenchido recebido."""
    return f"{brand_id}/orders/{order_id}/source.pdf"


# ──────────────────────────────────────────────
#  Service
# ──────────────────────────────────────────────


class OrderService:
    """Operações de domínio sobre pedidos.

    `dispatch_task` injetável para testes — `None` faz enqueue real
    (`extract_order_task.delay(...)`).
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        storage: StorageClient | None = None,
        extractor: OrderExtractor | None = None,
        normalizer: OrderNormalizer | None = None,
        settings: Settings | None = None,
        dispatch_task: object | None = None,
    ) -> None:
        self.db = db
        self.storage = storage or get_storage_client()
        self.extractor = extractor or OrderExtractor()
        self.normalizer = normalizer or OrderNormalizer()
        self.settings = settings or get_settings()
        self._dispatch_task = dispatch_task

    # ── Criação ───────────────────────────────

    async def create_order(
        self,
        *,
        brand_id: UUID,
        pdf_bytes: bytes,
        catalog_id: UUID | None,
        lojista_name: str | None,
        lojista_token: str | None,
    ) -> tuple[Order, Job]:
        """Valida o upload, persiste no storage, cria Order + Job, enfileira task."""
        self._validate_size(pdf_bytes)
        self._validate_signature(pdf_bytes)

        if catalog_id is not None:
            await self._ensure_catalog_owned(catalog_id, brand_id)

        order_id = uuid4()
        source_key = source_pdf_key_for(brand_id, order_id)
        await self.storage.upload(source_key, pdf_bytes)

        order = Order(
            id=order_id,
            brand_id=brand_id,
            catalog_id=catalog_id,
            lojista_name=lojista_name,
            lojista_token=lojista_token,
            status="draft",
            source_pdf_key=source_key,
        )
        job = Job(
            brand_id=brand_id,
            job_type="order.extract",
            entity_id=order_id,
            status="pending",
        )
        self.db.add(order)
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(order)
        await self.db.refresh(job)

        celery_id = self._enqueue(order_id=order.id, job_id=job.id)
        if celery_id is not None:
            job.celery_id = celery_id
            await self.db.flush()

        return order, job

    # ── Consulta ──────────────────────────────

    async def get_order(self, order_id: UUID, brand_id: UUID) -> Order:
        """Eager load `items` via `selectinload` (lição de Sprint 01)."""
        stmt = (
            select(Order)
            .where(Order.id == order_id, Order.brand_id == brand_id)
            .options(selectinload(Order.items))
        )
        result = await self.db.execute(stmt)
        order = result.scalar_one_or_none()
        if order is None:
            raise NotFoundError(
                f"order {order_id} não encontrado",
                code="ORDER_NOT_FOUND",
                details={"order_id": str(order_id)},
            )
        return order

    # ── Processamento (invocado pela Celery task) ──────────

    async def process_order(
        self,
        *,
        order_id: UUID,
        job_id: UUID,
    ) -> dict[str, Any]:
        """Pipeline: download → extract → normalize → persist items.

        PDFFlattenedError marca o pedido em `status='error'` e o job em
        `status='error'` (permanente, sem retry).
        """
        if not await self._claim_job(job_id):
            logger.info("job %s já reivindicado por outro worker", job_id)
            return {"skipped": True, "job_id": str(job_id)}

        order = await self.db.get(Order, order_id)
        if order is None:
            raise NotFoundError(
                f"order {order_id} não encontrado",
                code="ORDER_NOT_FOUND",
            )
        if not order.source_pdf_key:
            raise PDFCorruptError(
                "order sem source_pdf_key — upload nunca foi concluído",
                code="ORDER_MISSING_SOURCE",
            )

        try:
            pdf_bytes = await self.storage.download(order.source_pdf_key)
            raw = self.extractor.extract(pdf_bytes)

            catalog_products: list[CatalogProduct] | None = None
            if order.catalog_id is not None:
                catalog_products = await self._load_catalog_products(order.catalog_id)

            normalized = self.normalizer.normalize(raw, catalog_products)
            await self._persist_items(order.id, normalized)
            await self._mark_success(
                order=order,
                job_id=job_id,
                normalized=normalized,
            )
            return {
                "order_id": str(order.id),
                "n_items": normalized.totals.total_items,
                "total_pecas": normalized.totals.total_pecas,
                "n_skus": normalized.totals.n_skus,
                "source_format": normalized.source_format,
            }
        except Exception as exc:
            await self._mark_error(order=order, job_id=job_id, error=exc)
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

    async def _ensure_catalog_owned(self, catalog_id: UUID, brand_id: UUID) -> None:
        """Catalog cross-tenant é tratado como NotFound — não vaza existência."""
        stmt = select(Catalog.id).where(
            Catalog.id == catalog_id,
            Catalog.brand_id == brand_id,
        )
        result = await self.db.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise NotFoundError(
                f"catalog {catalog_id} não encontrado",
                code="CATALOG_NOT_FOUND",
                details={"catalog_id": str(catalog_id)},
            )

    async def _load_catalog_products(
        self,
        catalog_id: UUID,
    ) -> list[CatalogProduct]:
        stmt = select(CatalogProduct).where(CatalogProduct.catalog_id == catalog_id)
        result = await self.db.execute(stmt)
        return list(result.scalars())

    def _enqueue(self, *, order_id: UUID, job_id: UUID) -> str | None:
        """Dispara a task Celery. Retorna celery_id ou None em testes."""
        if self._dispatch_task is not None:
            result = self._dispatch_task(str(order_id), str(job_id))  # type: ignore[operator]
            return getattr(result, "id", None) if result is not None else None
        # Import tardio — evita ciclo entre service.py e tasks.py.
        from catalogflow.modules.orders.tasks import extract_order_task

        async_result = extract_order_task.delay(str(order_id), str(job_id))
        return str(async_result.id)

    async def _claim_job(self, job_id: UUID) -> bool:
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status == "pending")
            .values(status="running", progress=10)
            .returning(Job.id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _persist_items(
        self,
        order_id: UUID,
        normalized: NormalizedOrderData,
    ) -> None:
        for item in normalized.items:
            self.db.add(self._build_order_item(order_id, item))
        await self.db.flush()

    @staticmethod
    def _build_order_item(
        order_id: UUID,
        item: NormalizedOrderItem,
    ) -> OrderItem:
        return OrderItem(
            order_id=order_id,
            sku=item.sku,
            product_name=item.product_name,
            color_index=item.color_index,
            color_hex=item.color_hex,
            size=item.size,
            quantity=item.quantity,
            unit_price=item.unit_price,
        )

    async def _mark_success(
        self,
        *,
        order: Order,
        job_id: UUID,
        normalized: NormalizedOrderData,
    ) -> None:
        order.status = "extracted"
        order.total_pecas = normalized.totals.total_pecas
        order.valor_total = (
            normalized.totals.valor_total
            if normalized.totals.valor_total != Decimal("0")
            else None
        )
        order.extracted_at = datetime.now(UTC)

        job = await self.db.get(Job, job_id)
        if job is not None:
            job.status = "success"
            job.progress = 100
            job.error = None
            job.result = {
                "order_id": str(order.id),
                "n_items": normalized.totals.total_items,
                "total_pecas": normalized.totals.total_pecas,
                "n_skus": normalized.totals.n_skus,
                "source_format": normalized.source_format,
                "warnings": list(normalized.warnings),
            }

    async def _mark_error(
        self,
        *,
        order: Order,
        job_id: UUID,
        error: BaseException,
    ) -> None:
        order.status = "error"

        job = await self.db.get(Job, job_id)
        if job is not None:
            permanent = isinstance(error, _PERMANENT_ERRORS)
            job.status = "error" if permanent else "retry"
            error_code = getattr(error, "code", None)
            if error_code:
                job.error = f"{error_code}: {error}"
            else:
                job.error = str(error)
        await self.db.flush()
