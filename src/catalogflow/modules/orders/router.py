"""Endpoints HTTP do módulo `orders`.

Todos sob `/api/v1/orders/`. Autenticação via `Authorization: Bearer cf_...`.
Multi-tenant: queries filtram por `brand_id` no service.

`GET /{id}/romaneio`:
  - Se romaneio pronto: 302 redirect para presigned URL do PDF.
  - Se em andamento ou ainda não iniciado: 202 com job_id (enfileira se preciso).
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse

from catalogflow.modules.auth.dependencies import get_current_brand
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.orders.dependencies import (
    get_order_service,
    get_romaneio_service,
)
from catalogflow.modules.orders.schemas import (
    ExtractOrderResponse,
    OrderItemResponse,
    OrderResponse,
    OrderTotals,
    RomaneioStatusResponse,
)
from catalogflow.modules.orders.service import OrderService
from catalogflow.modules.romaneio.service import RomaneioService
from catalogflow.shared.middleware import get_request_id
from catalogflow.shared.responses import StandardResponse, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


# ──────────────────────────────────────────────
#  POST /api/v1/orders/extract
# ──────────────────────────────────────────────


@router.post(
    "/extract",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submete um PDF preenchido para extração assíncrona do pedido.",
)
async def extract_order(
    request: Request,
    file: UploadFile = File(..., description="PDF preenchido com AcroForm."),
    catalog_id: UUID | None = Form(default=None),
    lojista_name: str | None = Form(default=None, max_length=255),
    lojista_token: str | None = Form(default=None, max_length=64),
    brand: Brand = Depends(get_current_brand),
    service: OrderService = Depends(get_order_service),
) -> StandardResponse[ExtractOrderResponse]:
    """Aceita o upload, valida, enfileira `order.extract` e devolve `job_id`.

    Validações server-side: assinatura `%PDF`, tamanho ≤ max_pdf_size.
    Cross-tenant: `catalog_id` de outra brand → 404 (sem vazar existência).
    """
    pdf_bytes = await file.read()
    order, job = await service.create_order(
        brand_id=brand.id,
        pdf_bytes=pdf_bytes,
        catalog_id=catalog_id,
        lojista_name=lojista_name,
        lojista_token=lojista_token,
    )
    payload = ExtractOrderResponse(
        order_id=order.id,
        job_id=job.id,
        status="draft",
        poll_url=f"/api/v1/jobs/{job.id}",
    )
    logger.info(
        "order.extract accepted (order=%s job=%s brand=%s)",
        order.id,
        job.id,
        brand.id,
    )
    return ok(payload, request_id=get_request_id(request))


# ──────────────────────────────────────────────
#  GET /api/v1/orders/{order_id}
# ──────────────────────────────────────────────


@router.get(
    "/{order_id}",
    summary="Retorna o pedido completo com items e totais.",
)
async def get_order_endpoint(
    order_id: UUID,
    request: Request,
    brand: Brand = Depends(get_current_brand),
    service: OrderService = Depends(get_order_service),
) -> StandardResponse[OrderResponse]:
    """Recupera o pedido com items eager-loaded via `selectinload`."""
    order = await service.get_order(order_id, brand.id)

    items = [
        OrderItemResponse(
            id=item.id,
            sku=item.sku,
            product_name=item.product_name,
            color_index=item.color_index,
            color_hex=item.color_hex,
            size=item.size,
            quantity=item.quantity,
            unit_price=item.unit_price,
            subtotal=(
                item.unit_price * item.quantity
                if item.unit_price is not None
                else None
            ),
        )
        for item in order.items
    ]

    totals: OrderTotals | None = None
    if items:
        from decimal import Decimal

        valor_total = sum(
            (it.subtotal for it in items if it.subtotal is not None),
            start=Decimal("0"),
        )
        totals = OrderTotals(
            total_items=len(items),
            total_pecas=sum(it.quantity for it in items),
            valor_total=valor_total,
            n_skus=len({it.sku for it in items}),
        )

    payload = OrderResponse(
        id=order.id,
        brand_id=order.brand_id,
        catalog_id=order.catalog_id,
        lojista_token=order.lojista_token,
        lojista_name=order.lojista_name,
        status=order.status,  # type: ignore[arg-type]
        total_pecas=order.total_pecas,
        valor_total=order.valor_total,
        extracted_at=order.extracted_at,
        confirmed_at=order.confirmed_at,
        created_at=order.created_at,
        updated_at=order.updated_at,
        items=items,
        totals=totals,
    )
    return ok(payload, request_id=get_request_id(request))


# ──────────────────────────────────────────────
#  GET /api/v1/orders/{order_id}/romaneio
# ──────────────────────────────────────────────


@router.get(
    "/{order_id}/romaneio",
    summary="Romaneio do pedido — 302 se pronto, 202 com job_id se em andamento.",
)
async def get_order_romaneio(
    order_id: UUID,
    request: Request,
    brand: Brand = Depends(get_current_brand),
    order_service: OrderService = Depends(get_order_service),
    romaneio_service: RomaneioService = Depends(get_romaneio_service),
) -> Response:
    """Roteia entre 302 (PDF pronto), 202 (gerando), ou enfileira nova geração.

    Esse endpoint NÃO depende do romaneio existir — se necessário, chama
    `generate_romaneio()` no service (que enfileira a task).
    """
    # Garante que o order pertence à brand — levanta NotFoundError (404) caso
    # contrário, evitando vazar a existência do pedido.
    order = await order_service.get_order(order_id, brand.id)

    romaneio = await romaneio_service.find_romaneio_for_brand(order.id, brand.id)

    # Caso 1: PDF já pronto — bytes diretos em dev, 302 em produção.
    if romaneio is not None and romaneio.output_key:
        if romaneio_service.settings.s3_public_url:
            pdf_bytes = await romaneio_service.storage.download(romaneio.output_key)
            return Response(content=pdf_bytes, media_type="application/pdf")
        url = await romaneio_service.get_download_url(order.id, brand.id)
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)

    # Caso 2/3: não existe ou ainda sem output_key — enfileira / re-enfileira.
    new_romaneio, job = await romaneio_service.generate_romaneio(order.id, brand.id)
    payload = RomaneioStatusResponse(
        romaneio_id=new_romaneio.id,
        status="processing" if romaneio is not None else "pending",
        download_url=None,
        job_id=job.id,
        poll_url=f"/api/v1/jobs/{job.id}",
    )
    envelope = ok(payload, request_id=get_request_id(request))
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=envelope.model_dump(mode="json"),
    )
