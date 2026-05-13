"""StockService — orquestra adapter + DB + jobs para o domínio de estoque.

Cada método HTTP cai aqui antes de tocar adapter/banco. Multi-tenancy: toda
query inclui `brand_id` no WHERE — pedidos de outra brand viram
`NotFoundError` (não vazamos existência).

Race condition de jobs (CLAUDE.md): a transição `pending → running` é
feita com `UPDATE WHERE status = 'pending'` no `_claim_job`.

`get_adapter` lê `settings.erp_adapter` em runtime — trocar de adapter é
uma única variável de ambiente.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalogflow.infra.settings import Settings, get_settings
from catalogflow.modules.catalog.models import Job
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.stock.adapter import (
    StockAdapter,
    StockQuery,
    StockResult,
)
from catalogflow.modules.stock.consistem_adapter import ConsistemAdapter
from catalogflow.modules.stock.mock_adapter import MockStockAdapter
from catalogflow.modules.stock.models import ErpSubmission, StockCheck
from catalogflow.shared.errors import ConflictError, NotFoundError

logger = logging.getLogger(__name__)


class StockService:
    """Operações de domínio sobre consulta de estoque e envio ao ERP.

    `adapter` é injetável para testes (passe um `MockStockAdapter`); em
    produção o default delega para `get_adapter()` que lê settings.

    `dispatch_check` / `dispatch_submit` são spies opcionais para testes
    de router/service — passe `None` em produção (faz `task.delay`).
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        adapter: StockAdapter | None = None,
        settings: Settings | None = None,
        dispatch_check: object | None = None,
        dispatch_submit: object | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._adapter_override = adapter
        self._dispatch_check = dispatch_check
        self._dispatch_submit = dispatch_submit

    # ─────────────────────────────────────────
    #  Seleção de adapter (Adapter Pattern)
    # ─────────────────────────────────────────

    def get_adapter(self) -> StockAdapter:
        """Retorna o adapter configurado em `settings.erp_adapter`.

        Em testes, passe `adapter=...` no construtor — esse override
        ganha precedência. Em produção, lê settings em runtime.
        """
        if self._adapter_override is not None:
            return self._adapter_override
        if self.settings.erp_adapter == "consistem":
            api_key = (
                self.settings.erp_api_key.get_secret_value()
                if self.settings.erp_api_key is not None
                else None
            )
            return ConsistemAdapter(
                base_url=self.settings.erp_base_url,
                api_key=api_key,
                empresa=self.settings.erp_empresa,
                cod_natureza=self.settings.erp_cod_natureza,
                timeout=self.settings.erp_timeout,
            )
        return MockStockAdapter()

    # ─────────────────────────────────────────
    #  check_order_stock — enfileira (router) + executa (task)
    # ─────────────────────────────────────────

    async def enqueue_stock_check(
        self,
        order_id: UUID,
        brand_id: UUID,
    ) -> tuple[StockCheck, Job]:
        """Cria StockCheck(pending) + Job + enfileira a Celery task.

        Idempotência: re-disparar enquanto há um check `pending`/`checking`
        cria um novo registro — auditoria preserva histórico. A consulta
        anterior fica intacta com o `checked_at` original.
        """
        order = await self._load_order_owned(order_id, brand_id)

        stock_check = StockCheck(
            order_id=order.id,
            brand_id=brand_id,
            status="pending",
            result={},
        )
        job = Job(
            brand_id=brand_id,
            job_type="stock.check",
            entity_id=order.id,
            status="pending",
        )
        self.db.add(stock_check)
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(stock_check)
        await self.db.refresh(job)

        celery_id = self._enqueue_check(
            order_id=order.id,
            stock_check_id=stock_check.id,
            job_id=job.id,
        )
        if celery_id is not None:
            job.celery_id = celery_id
            await self.db.flush()

        return stock_check, job

    async def check_order_stock(
        self,
        *,
        order_id: UUID,
        stock_check_id: UUID,
        job_id: UUID,
    ) -> dict[str, Any]:
        """Pipeline executado pela Celery task.

        1. Reivindica o job (`pending → running`).
        2. Carrega OrderItems do pedido (sem filtro de brand — quem
           reivindicou o job já passou pela autorização no enqueue).
        3. Converte para StockQuery e chama adapter.check_availability.
        4. Atualiza order_items com stock_status/available_qty.
        5. Persiste StockCheck.result e marca `completed`.
        6. Marca o job `success`.
        """
        if not await self._claim_job(job_id):
            logger.info("stock.check: job %s já reivindicado", job_id)
            return {"skipped": True, "job_id": str(job_id)}

        stock_check = await self.db.get(StockCheck, stock_check_id)
        if stock_check is None:
            raise NotFoundError(
                f"stock_check {stock_check_id} não encontrado",
                code="STOCK_CHECK_NOT_FOUND",
            )

        order = await self._load_order_owned(order_id, stock_check.brand_id)

        try:
            stock_check.status = "checking"
            await self.db.flush()

            queries = self._build_queries(order)
            results = await self.get_adapter().check_availability(queries)

            await self._apply_results_to_items(order, results)
            stock_check.result = self._serialize_results(order, results)
            stock_check.status = "completed"
            stock_check.checked_at = datetime.now(UTC)
            await self._mark_job_success(
                job_id,
                {
                    "stock_check_id": str(stock_check.id),
                    "total_items": len(results),
                },
            )
            return {
                "stock_check_id": str(stock_check.id),
                "total_items": len(results),
            }
        except Exception as exc:
            stock_check.status = "error"
            stock_check.error_message = str(exc)
            await self._mark_job_error(job_id, exc)
            raise

    async def get_stock_check(
        self,
        order_id: UUID,
        brand_id: UUID,
    ) -> StockCheck | None:
        """Retorna o `StockCheck` mais recente do pedido (None se nunca rodou)."""
        await self._load_order_owned(order_id, brand_id)
        stmt = (
            select(StockCheck)
            .where(
                StockCheck.order_id == order_id,
                StockCheck.brand_id == brand_id,
            )
            .order_by(StockCheck.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    # ─────────────────────────────────────────
    #  submit_order_to_erp — enfileira + executa
    # ─────────────────────────────────────────

    async def enqueue_submission(
        self,
        order_id: UUID,
        brand_id: UUID,
        customer_code: str,
    ) -> tuple[ErpSubmission, Job]:
        """Cria ErpSubmission(pending) + Job + enfileira a Celery task.

        UNIQUE(order_id) impede dois envios concorrentes — o segundo
        gera `ConflictError` (HTTP 409) em vez de duplicar pedido no ERP.
        """
        order = await self._load_order_owned(order_id, brand_id)

        # Verifica se já existe submissão em estado terminal — bloqueia
        # nova tentativa. Estados não-terminais (pending/submitting/error)
        # podem ser re-enfileirados (caso de retry manual).
        existing = await self._existing_submission(order.id)
        if existing is not None and existing.status in {
            "accepted",
            "partially_accepted",
            "rejected",
        }:
            raise ConflictError(
                f"pedido {order.id} já foi enviado ao ERP",
                code="ORDER_ALREADY_SUBMITTED",
                details={
                    "submission_id": str(existing.id),
                    "status": existing.status,
                    "erp_reference": existing.erp_reference,
                },
            )

        if existing is None:
            submission = ErpSubmission(
                order_id=order.id,
                brand_id=brand_id,
                status="pending",
                result={"customer_code": customer_code},
            )
            self.db.add(submission)
        else:
            # Reaproveita o registro existente (pending/submitting/error)
            # — o UNIQUE constraint impede criar um segundo.
            existing.status = "pending"
            existing.error_message = None
            existing.result = {"customer_code": customer_code}
            submission = existing

        job = Job(
            brand_id=brand_id,
            job_type="stock.submit",
            entity_id=order.id,
            status="pending",
        )
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(submission)
        await self.db.refresh(job)

        celery_id = self._enqueue_submit(
            order_id=order.id,
            customer_code=customer_code,
            job_id=job.id,
        )
        if celery_id is not None:
            job.celery_id = celery_id
            await self.db.flush()

        return submission, job

    async def submit_order_to_erp(
        self,
        *,
        order_id: UUID,
        customer_code: str,
        job_id: UUID,
    ) -> dict[str, Any]:
        """Pipeline executado pela Celery task."""
        if not await self._claim_job(job_id):
            logger.info("stock.submit: job %s já reivindicado", job_id)
            return {"skipped": True, "job_id": str(job_id)}

        submission = await self._existing_submission(order_id)
        if submission is None:
            raise NotFoundError(
                f"submission do pedido {order_id} não encontrada",
                code="SUBMISSION_NOT_FOUND",
            )

        order = await self._load_order_owned(order_id, submission.brand_id)

        try:
            submission.status = "submitting"
            await self.db.flush()

            queries = self._build_queries(order)
            adapter_result = await self.get_adapter().submit_order(
                order_reference=str(order.id),
                customer_code=customer_code,
                items=queries,
            )

            submission.erp_reference = adapter_result.get("erp_reference")
            submission.result = {
                "customer_code": customer_code,
                "adapter_response": adapter_result,
            }
            submission.submitted_at = datetime.now(UTC)
            accepted = bool(adapter_result.get("accepted", False))
            rejected_items = adapter_result.get("rejected_items", [])
            if accepted and not rejected_items:
                submission.status = "accepted"
            elif accepted and rejected_items:
                submission.status = "partially_accepted"
            else:
                submission.status = "rejected"
                submission.error_message = adapter_result.get("message")

            await self._mark_job_success(
                job_id,
                {
                    "submission_id": str(submission.id),
                    "status": submission.status,
                    "erp_reference": submission.erp_reference,
                },
            )
            return {
                "submission_id": str(submission.id),
                "status": submission.status,
                "erp_reference": submission.erp_reference,
            }
        except Exception as exc:
            submission.status = "error"
            submission.error_message = str(exc)
            await self._mark_job_error(job_id, exc)
            raise

    async def get_submission(
        self,
        order_id: UUID,
        brand_id: UUID,
    ) -> ErpSubmission | None:
        """Retorna a submissão (única) do pedido — None se nunca disparou."""
        await self._load_order_owned(order_id, brand_id)
        stmt = select(ErpSubmission).where(
            ErpSubmission.order_id == order_id,
            ErpSubmission.brand_id == brand_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    # ─────────────────────────────────────────
    #  Helpers internos
    # ─────────────────────────────────────────

    async def _load_order_owned(self, order_id: UUID, brand_id: UUID) -> Order:
        """Carrega `Order` exigindo `brand_id` — cross-tenant vira 404."""
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

    async def _existing_submission(self, order_id: UUID) -> ErpSubmission | None:
        stmt = select(ErpSubmission).where(ErpSubmission.order_id == order_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def _build_queries(order: Order) -> list[StockQuery]:
        return [
            StockQuery(
                sku=item.sku,
                size=item.size,
                color_index=item.color_index,
                requested_qty=item.quantity,
            )
            for item in order.items
        ]

    async def _apply_results_to_items(
        self,
        order: Order,
        results: list[StockResult],
    ) -> None:
        """Faz UPDATE em order_items.stock_status / available_qty.

        Casamento via (sku, size, color_index) — combinação única dentro
        de um pedido (UNIQUE constraint na migration 0003).
        """
        by_key = {(r.sku, r.size, r.color_index): r for r in results}
        for item in order.items:
            key = (item.sku, item.size, item.color_index)
            r = by_key.get(key)
            if r is None:
                continue
            item.stock_status = r.status
            item.available_qty = r.available_qty
        await self.db.flush()

    @staticmethod
    def _serialize_results(
        order: Order,
        results: list[StockResult],
    ) -> dict[str, Any]:
        """Empacota o snapshot para JSONB com enriquecimento de product_name."""
        names = {
            (item.sku, item.size, item.color_index): item.product_name
            for item in order.items
        }
        hexes = {
            (item.sku, item.size, item.color_index): item.color_hex
            for item in order.items
        }
        return {
            "items": [
                {
                    "sku": r.sku,
                    "product_name": names.get((r.sku, r.size, r.color_index)),
                    "size": r.size,
                    "color_index": r.color_index,
                    "color_hex": hexes.get((r.sku, r.size, r.color_index)),
                    "requested": r.requested_qty,
                    "available": r.available_qty,
                    "status": r.status,
                }
                for r in results
            ],
        }

    async def _claim_job(self, job_id: UUID) -> bool:
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status == "pending")
            .values(status="running", progress=10)
            .returning(Job.id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _mark_job_success(
        self,
        job_id: UUID,
        payload: dict[str, Any],
    ) -> None:
        job = await self.db.get(Job, job_id)
        if job is not None:
            job.status = "success"
            job.progress = 100
            job.error = None
            job.result = payload

    async def _mark_job_error(
        self,
        job_id: UUID,
        exc: BaseException,
    ) -> None:
        job = await self.db.get(Job, job_id)
        if job is not None:
            # Erros transitórios (rede, ERP fora do ar) → "retry";
            # NotImplementedError (Consistem.submit_order) → "error".
            permanent = isinstance(exc, NotImplementedError)
            job.status = "error" if permanent else "retry"
            code = getattr(exc, "code", None)
            job.error = f"{code}: {exc}" if code else str(exc)
        await self.db.flush()

    # ─────────────────────────────────────────
    #  Dispatch para Celery (mockable em testes)
    # ─────────────────────────────────────────

    def _enqueue_check(
        self,
        *,
        order_id: UUID,
        stock_check_id: UUID,
        job_id: UUID,
    ) -> str | None:
        if self._dispatch_check is not None:
            result = self._dispatch_check(  # type: ignore[operator]
                str(order_id),
                str(stock_check_id),
                str(job_id),
            )
            return getattr(result, "id", None) if result is not None else None
        from catalogflow.modules.stock.tasks import check_stock_task

        async_result = check_stock_task.delay(
            str(order_id),
            str(stock_check_id),
            str(job_id),
        )
        return str(async_result.id)

    def _enqueue_submit(
        self,
        *,
        order_id: UUID,
        customer_code: str,
        job_id: UUID,
    ) -> str | None:
        if self._dispatch_submit is not None:
            result = self._dispatch_submit(  # type: ignore[operator]
                str(order_id),
                customer_code,
                str(job_id),
            )
            return getattr(result, "id", None) if result is not None else None
        from catalogflow.modules.stock.tasks import submit_order_task

        async_result = submit_order_task.delay(
            str(order_id),
            customer_code,
            str(job_id),
        )
        return str(async_result.id)


# ──────────────────────────────────────────────
#  Sumário do StockCheck — função pura (router usa)
# ──────────────────────────────────────────────


def summarize_stock_check(stock_check: StockCheck) -> dict[str, int]:
    """Agrega contadores por status a partir de `stock_check.result`.

    Função pura — vive aqui para ser reusada por router web (HTMX) e
    router JSON sem duplicação.
    """
    items: list[dict[str, Any]] = stock_check.result.get("items", []) if stock_check.result else []
    summary = {
        "total_items": len(items),
        "available": 0,
        "partial": 0,
        "out_of_stock": 0,
        "unknown": 0,
    }
    for item in items:
        status = item.get("status")
        if status in summary:
            summary[status] += 1
    return summary
