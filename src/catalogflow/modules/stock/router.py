"""Endpoints HTTP do módulo `stock`.

Sob `/api/v1/orders/{order_id}/`. Autenticação via Bearer (API key).
Multi-tenant: cross-tenant retorna 404 via `service._load_order_owned`.

Endpoints:
- POST /stock-check  → 202 (enfileira consulta)
- GET  /stock-check  → 200 (resultado da última consulta) | 404 (sem consulta)
- POST /submit       → 202 (enfileira envio ao ERP) | 409 (já enviado)
- GET  /submission   → 200 (estado do envio) | 404 (não enviado)
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from catalogflow.modules.auth.dependencies import get_current_brand
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Job
from catalogflow.modules.stock.dependencies import get_stock_service
from catalogflow.modules.stock.schemas import (
    ErpSubmissionEnqueueResponse,
    ErpSubmissionRequest,
    ErpSubmissionResponse,
    StockCheckEnqueueResponse,
    StockCheckResponse,
    StockCheckSummary,
    StockItemResult,
)
from catalogflow.modules.stock.service import StockService, summarize_stock_check
from catalogflow.shared.errors import NotFoundError
from catalogflow.shared.middleware import get_request_id
from catalogflow.shared.responses import StandardResponse, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orders", tags=["stock"])


# ──────────────────────────────────────────────
#  POST /api/v1/orders/{order_id}/stock-check
# ──────────────────────────────────────────────


@router.post(
    "/{order_id}/stock-check",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Dispara consulta assíncrona de estoque para todos os itens do pedido.",
)
async def enqueue_stock_check(
    order_id: UUID,
    request: Request,
    brand: Brand = Depends(get_current_brand),
    service: StockService = Depends(get_stock_service),
) -> StandardResponse[StockCheckEnqueueResponse]:
    """Cria StockCheck(pending), Job(pending) e enfileira `stock.check`.

    Cross-tenant: pedido de outra brand → 404 `ORDER_NOT_FOUND`.
    """
    stock_check, job = await service.enqueue_stock_check(order_id, brand.id)
    # Idempotência (S07-01B): quando o service devolve `job=None`, já
    # havia um check ativo — recuperamos o Job correspondente para manter
    # o contrato da resposta (job_id é obrigatório no schema).
    if job is None:
        latest_job_stmt = (
            select(Job)
            .where(Job.entity_id == stock_check.order_id, Job.job_type == "stock.check")
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        job = (await service.db.execute(latest_job_stmt)).scalar_one()
    payload = StockCheckEnqueueResponse(
        stock_check_id=stock_check.id,
        job_id=job.id,
        status="pending",
    )
    logger.info(
        "stock-check enqueued (order=%s check=%s job=%s brand=%s)",
        order_id,
        stock_check.id,
        job.id,
        brand.id,
    )
    return ok(payload, request_id=get_request_id(request))


# ──────────────────────────────────────────────
#  GET /api/v1/orders/{order_id}/stock-check
# ──────────────────────────────────────────────


@router.get(
    "/{order_id}/stock-check",
    summary="Retorna o resultado da última consulta de estoque (summary + items).",
)
async def get_stock_check(
    order_id: UUID,
    request: Request,
    brand: Brand = Depends(get_current_brand),
    service: StockService = Depends(get_stock_service),
) -> StandardResponse[StockCheckResponse]:
    stock_check = await service.get_stock_check(order_id, brand.id)
    if stock_check is None:
        raise NotFoundError(
            f"nenhuma consulta de estoque registrada para o pedido {order_id}",
            code="STOCK_CHECK_NOT_FOUND",
            details={"order_id": str(order_id)},
        )

    summary_counts = summarize_stock_check(stock_check)
    raw_items = stock_check.result.get("items", []) if stock_check.result else []
    items = [
        StockItemResult(
            sku=item["sku"],
            product_name=item.get("product_name"),
            size=item["size"],
            color_index=item["color_index"],
            color_hex=item.get("color_hex"),
            requested=item["requested"],
            available=item.get("available"),
            status=item["status"],
        )
        for item in raw_items
    ]
    payload = StockCheckResponse(
        stock_check_id=stock_check.id,
        status=stock_check.status,  # type: ignore[arg-type]
        checked_at=stock_check.checked_at,
        summary=StockCheckSummary(**summary_counts),
        items=items,
        error_message=stock_check.error_message,
    )
    return ok(payload, request_id=get_request_id(request))


# ──────────────────────────────────────────────
#  POST /api/v1/orders/{order_id}/submit
# ──────────────────────────────────────────────


@router.post(
    "/{order_id}/submit",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Envia o pedido ao ERP. Body: {customer_code}.",
)
async def enqueue_submission(
    order_id: UUID,
    request: Request,
    body: ErpSubmissionRequest,
    brand: Brand = Depends(get_current_brand),
    service: StockService = Depends(get_stock_service),
) -> JSONResponse:
    """Cria ErpSubmission(pending) e enfileira `stock.submit`.

    Conflito (já aceito anteriormente): 409 `ORDER_ALREADY_SUBMITTED`.
    """
    submission, job = await service.enqueue_submission(
        order_id,
        brand.id,
        body.customer_code,
    )
    payload = ErpSubmissionEnqueueResponse(
        submission_id=submission.id,
        job_id=job.id,
        status="pending",
    )
    logger.info(
        "stock-submit enqueued (order=%s submission=%s job=%s brand=%s)",
        order_id,
        submission.id,
        job.id,
        brand.id,
    )
    envelope = ok(payload, request_id=get_request_id(request))
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=envelope.model_dump(mode="json"),
    )


# ──────────────────────────────────────────────
#  GET /api/v1/orders/{order_id}/submission
# ──────────────────────────────────────────────


@router.get(
    "/{order_id}/submission",
    summary="Retorna o estado do envio do pedido ao ERP.",
)
async def get_submission(
    order_id: UUID,
    request: Request,
    brand: Brand = Depends(get_current_brand),
    service: StockService = Depends(get_stock_service),
) -> StandardResponse[ErpSubmissionResponse]:
    submission = await service.get_submission(order_id, brand.id)
    if submission is None:
        raise NotFoundError(
            f"nenhuma submissão registrada para o pedido {order_id}",
            code="SUBMISSION_NOT_FOUND",
            details={"order_id": str(order_id)},
        )
    payload = ErpSubmissionResponse(
        submission_id=submission.id,
        status=submission.status,  # type: ignore[arg-type]
        submitted_at=submission.submitted_at,
        erp_reference=submission.erp_reference,
        error_message=submission.error_message,
    )
    return ok(payload, request_id=get_request_id(request))
