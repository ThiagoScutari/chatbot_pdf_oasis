"""RomaneioService — orquestra geração + persistência + download.

O builder permanece puro (bytes-in/bytes-out); o service:
  1. Carrega Order com items + brand (selectinload).
  2. Baixa a logo do storage se `brand.logo_key` existir.
  3. Aplica OrderNormalizer (já temos OrderItem persistido — converte para
     `NormalizedOrderItem` sem precisar do extractor).
  4. Chama `RomaneioBuilder.build()` → bytes.
  5. Faz upload do PDF e atualiza `Romaneio.output_key`.

Multi-tenancy: queries filtram por `brand_id`. Romaneio é 1:1 com Order
— UNIQUE garante; o service usa `INSERT ... ON CONFLICT DO NOTHING`
semantics via lookup + insert separado.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalogflow.infra.settings import Settings, get_settings
from catalogflow.infra.storage import StorageClient, get_storage_client
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Job
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.orders.normalizer import (
    NormalizedOrderData,
    NormalizedOrderItem,
    NormalizedTotals,
)
from catalogflow.modules.romaneio.builder import RomaneioBuilder, RomaneioConfig
from catalogflow.modules.romaneio.models import Romaneio
from catalogflow.shared.errors import JobNotReadyError, NotFoundError

# Tipo do callable injetável que busca fotos de produto.
# Recebe lista de SKUs, devolve `dict[sku, image_bytes]` — SKUs sem
# foto são omitidos. `None` aqui significa "não buscar fotos" — usado
# nos testes para não disparar I/O de rede.
ImageFetcher = Callable[[list[str]], Awaitable[dict[str, bytes]]]

logger = logging.getLogger(__name__)


def romaneio_output_key_for(brand_id: UUID, order_id: UUID) -> str:
    """`{brand}/orders/{order}/romaneio.pdf`."""
    return f"{brand_id}/orders/{order_id}/romaneio.pdf"


# ──────────────────────────────────────────────
#  Service
# ──────────────────────────────────────────────


class RomaneioService:
    """Operações de domínio sobre romaneios."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        storage: StorageClient | None = None,
        builder: RomaneioBuilder | None = None,
        settings: Settings | None = None,
        dispatch_task: object | None = None,
        image_fetcher: ImageFetcher | None = None,
    ) -> None:
        self.db = db
        self.storage = storage or get_storage_client()
        self.builder = builder or RomaneioBuilder()
        self.settings = settings or get_settings()
        self._dispatch_task = dispatch_task
        # `image_fetcher` é opcional por design — `None` deixa o PDF
        # sair sem fotos (mantém testes existentes verdes sem precisar
        # mockar rede). Produção injeta `fetch_product_images` via tasks.
        self._image_fetcher = image_fetcher

    # ── Criação / enfileiramento ──────────────

    async def generate_romaneio(
        self,
        order_id: UUID,
        brand_id: UUID,
    ) -> tuple[Romaneio, Job]:
        """Cria o registro `Romaneio` (sem `output_key` ainda) e enfileira task.

        Se já existir Romaneio para o pedido, reutiliza (sobrescreve no
        process_romaneio). Order precisa estar em status `extracted`.
        """
        order = await self._load_order_for_generation(order_id, brand_id)

        existing = await self._find_romaneio(order_id)
        if existing is None:
            romaneio = Romaneio(
                order_id=order.id,
                brand_id=order.brand_id,
                output_key=None,
            )
            self.db.add(romaneio)
            await self.db.flush()
            await self.db.refresh(romaneio)
        else:
            romaneio = existing

        job = Job(
            brand_id=order.brand_id,
            job_type="romaneio.generate",
            entity_id=romaneio.id,
            status="pending",
        )
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(job)

        celery_id = self._enqueue(romaneio_id=romaneio.id, job_id=job.id)
        if celery_id is not None:
            job.celery_id = celery_id
            await self.db.flush()

        return romaneio, job

    # ── Consulta de URL ───────────────────────

    async def get_download_url(self, order_id: UUID, brand_id: UUID) -> str:
        """Retorna presigned URL do PDF gerado.

        Levanta `JobNotReadyError` se o romaneio ainda não foi materializado.
        """
        romaneio = await self._find_romaneio_for_brand(order_id, brand_id)
        if romaneio is None:
            raise NotFoundError(
                f"romaneio do order {order_id} não encontrado",
                code="ROMANEIO_NOT_FOUND",
                details={"order_id": str(order_id)},
            )
        if not romaneio.output_key:
            raise JobNotReadyError(
                f"romaneio do order {order_id} ainda em geração",
                code="ROMANEIO_NOT_READY",
                details={"order_id": str(order_id)},
            )
        return await self.storage.presigned_url(romaneio.output_key)

    async def find_romaneio_for_brand(
        self,
        order_id: UUID,
        brand_id: UUID,
    ) -> Romaneio | None:
        """Lookup público — usado pelo router para decidir 302 vs 202."""
        return await self._find_romaneio_for_brand(order_id, brand_id)

    # ── Processamento (invocado pela Celery task) ──────────

    async def process_romaneio(
        self,
        *,
        romaneio_id: UUID,
        job_id: UUID,
    ) -> dict[str, Any]:
        """Pipeline: load order → fetch logo → build PDF → upload → persist key."""
        if not await self._claim_job(job_id):
            logger.info("job %s já reivindicado por outro worker", job_id)
            return {"skipped": True, "job_id": str(job_id)}

        romaneio = await self.db.get(Romaneio, romaneio_id)
        if romaneio is None:
            raise NotFoundError(
                f"romaneio {romaneio_id} não encontrado",
                code="ROMANEIO_NOT_FOUND",
            )

        try:
            order, brand = await self._load_order_with_items_and_brand(
                romaneio.order_id,
            )

            logo_bytes: bytes | None = None
            if brand.logo_key:
                try:
                    logo_bytes = await self.storage.download(brand.logo_key)
                except Exception as exc:
                    logger.warning(
                        "falha ao baixar logo da brand %s (key=%s): %s",
                        brand.id,
                        brand.logo_key,
                        exc,
                    )
                    logo_bytes = None

            order_data = self._build_normalized_from_items(order.items)
            config = RomaneioConfig(
                brand_name=brand.name,
                logo_bytes=logo_bytes,
                lojista_name=order.lojista_name or "—",
                emitted_at=order.extracted_at,
            )

            # Fotos dos produtos — best-effort. `image_fetcher` é None
            # nos testes (sem rede); produção injeta `fetch_product_images`.
            product_images: dict[str, bytes] = {}
            if self._image_fetcher is not None:
                skus = sorted({item.sku for item in order.items})
                try:
                    product_images = await self._image_fetcher(skus)
                except Exception:
                    # Fetcher é best-effort por contrato, mas dupla camada
                    # de segurança aqui — o PDF sai sem fotos em vez de
                    # quebrar a geração inteira.
                    logger.warning(
                        "romaneio: image_fetcher falhou — PDF sairá sem fotos",
                        exc_info=True,
                    )

            pdf_bytes = self.builder.build(
                order_data,
                config,
                product_images=product_images or None,
            )

            output_key = romaneio_output_key_for(brand.id, order.id)
            await self.storage.upload(output_key, pdf_bytes)

            await self._mark_success(
                romaneio=romaneio,
                job_id=job_id,
                output_key=output_key,
                size_bytes=len(pdf_bytes),
            )
            return {
                "romaneio_id": str(romaneio.id),
                "order_id": str(order.id),
                "output_key": output_key,
                "size_bytes": len(pdf_bytes),
            }
        except Exception as exc:
            await self._mark_error(job_id=job_id, error=exc)
            raise

    # ── Helpers internos ──────────────────────

    async def _load_order_for_generation(
        self,
        order_id: UUID,
        brand_id: UUID,
    ) -> Order:
        """Order precisa pertencer à brand e estar em status `extracted`."""
        stmt = select(Order).where(
            Order.id == order_id,
            Order.brand_id == brand_id,
            Order.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        order = result.scalar_one_or_none()
        if order is None:
            raise NotFoundError(
                f"order {order_id} não encontrado",
                code="ORDER_NOT_FOUND",
                details={"order_id": str(order_id)},
            )
        if order.status != "extracted":
            raise JobNotReadyError(
                f"order {order_id} ainda não foi extraído (status={order.status})",
                code="ORDER_NOT_READY",
                details={"order_id": str(order_id), "status": order.status},
            )
        return order

    async def _load_order_with_items_and_brand(
        self,
        order_id: UUID,
    ) -> tuple[Order, Brand]:
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
        )
        result = await self.db.execute(stmt)
        order = result.scalar_one_or_none()
        if order is None:
            raise NotFoundError(
                f"order {order_id} não encontrado",
                code="ORDER_NOT_FOUND",
            )
        # `Brand` é eager-load separado — relacionamento many-to-one simples.
        brand = await self.db.get(Brand, order.brand_id)
        if brand is None:
            raise NotFoundError(
                f"brand {order.brand_id} não encontrada",
                code="BRAND_NOT_FOUND",
            )
        return order, brand

    async def _find_romaneio(self, order_id: UUID) -> Romaneio | None:
        stmt = select(Romaneio).where(
            Romaneio.order_id == order_id,
            Romaneio.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _find_romaneio_for_brand(
        self,
        order_id: UUID,
        brand_id: UUID,
    ) -> Romaneio | None:
        stmt = select(Romaneio).where(
            Romaneio.order_id == order_id,
            Romaneio.brand_id == brand_id,
            Romaneio.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    def _enqueue(self, *, romaneio_id: UUID, job_id: UUID) -> str | None:
        if self._dispatch_task is not None:
            result = self._dispatch_task(  # type: ignore[operator]
                str(romaneio_id),
                str(job_id),
            )
            return getattr(result, "id", None) if result is not None else None
        from catalogflow.modules.romaneio.tasks import generate_romaneio_task

        async_result = generate_romaneio_task.delay(str(romaneio_id), str(job_id))
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

    def _build_normalized_from_items(
        self,
        items: list[OrderItem],
    ) -> NormalizedOrderData:
        """Converte OrderItems persistidos para NormalizedOrderData.

        O extractor + normalizer já rodaram no `process_order`. Aqui é
        apenas a conversão `OrderItem → NormalizedOrderItem` (zero lógica).
        """
        normalized_items = [
            NormalizedOrderItem(
                sku=item.sku,
                product_name=item.product_name,
                color_index=item.color_index,
                color_hex=item.color_hex,
                size=item.size,
                quantity=item.quantity,
                unit_price=item.unit_price,
            )
            for item in items
        ]
        total_pecas = sum(i.quantity for i in normalized_items)
        from decimal import Decimal as _Dec

        valor_total = sum(
            (i.subtotal for i in normalized_items if i.subtotal is not None),
            start=_Dec("0"),
        )
        totals = NormalizedTotals(
            total_items=len(normalized_items),
            total_pecas=total_pecas,
            valor_total=valor_total,
            n_skus=len({i.sku for i in normalized_items}),
        )
        return NormalizedOrderData(
            items=normalized_items,
            totals=totals,
            source_format="v2",  # nesse ponto já é canônico — informativo
            warnings=[],
        )

    async def _mark_success(
        self,
        *,
        romaneio: Romaneio,
        job_id: UUID,
        output_key: str,
        size_bytes: int,
    ) -> None:
        romaneio.output_key = output_key
        job = await self.db.get(Job, job_id)
        if job is not None:
            job.status = "success"
            job.progress = 100
            job.error = None
            job.result = {
                "romaneio_id": str(romaneio.id),
                "output_key": output_key,
                "size_bytes": size_bytes,
            }

    async def _mark_error(
        self,
        *,
        job_id: UUID,
        error: BaseException,
    ) -> None:
        job = await self.db.get(Job, job_id)
        if job is not None:
            # Romaneio é considerado tudo transitório (erros de storage,
            # builder etc.) — diferente de orders onde flatten é permanente.
            job.status = "retry"
            error_code = getattr(error, "code", None)
            job.error = (
                f"{error_code}: {error}" if error_code else str(error)
            )
        await self.db.flush()
