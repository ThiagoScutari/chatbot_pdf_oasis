"""Schemas Pydantic do módulo `orders`.

Pydantic v2. Convenções:
- `from_attributes=True` para schemas que serializam direto do ORM.
- `Field(default_factory=...)` para listas — evita aliasing entre instâncias.
- `Literal` para valores enumerados (status, source_format) — bate com o
  ORM via `OrderStatusValue: str` (sem CHECK no banco).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

OrderStatus = Literal["draft", "extracted", "confirmed", "cancelled", "error"]
SourceFormat = Literal["v1", "v2", "mixed"]
RomaneioStatus = Literal["pending", "processing", "ready", "error"]


# ──────────────────────────────────────────────
#  Request
# ──────────────────────────────────────────────


class OrderCreateRequest(BaseModel):
    """Form fields que acompanham o upload do PDF preenchido.

    `file` chega como `UploadFile` separado no endpoint.
    `catalog_id` opcional: quando fornecido, o normalizer enriquece os itens.
    """

    catalog_id: UUID | None = Field(default=None)
    lojista_name: str | None = Field(default=None, max_length=255)
    lojista_token: str | None = Field(default=None, max_length=64)


# ──────────────────────────────────────────────
#  Sub-payloads
# ──────────────────────────────────────────────


class OrderTotals(BaseModel):
    """Totais agregados do pedido — calculados pelo normalizer."""

    total_items: int = Field(description="Número de linhas (sku x cor x tamanho).")
    total_pecas: int = Field(description="Soma de todas as quantities.")
    valor_total: Decimal = Field(description="Valor total monetário do pedido.")
    n_skus: int = Field(description="SKUs distintos no pedido.")


class OrderItemResponse(BaseModel):
    """Linha do pedido — granularidade SKU x cor x tamanho."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sku: str
    product_name: str | None
    color_index: int
    color_hex: str | None
    size: str
    quantity: int
    unit_price: Decimal | None
    subtotal: Decimal | None = Field(
        default=None,
        description="quantity x unit_price; None quando o preço não está disponível.",
    )


# ──────────────────────────────────────────────
#  Response
# ──────────────────────────────────────────────


class OrderResponse(BaseModel):
    """Representação completa de um pedido (`GET /orders/{id}`)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    brand_id: UUID
    catalog_id: UUID | None
    lojista_token: str | None
    lojista_name: str | None
    status: OrderStatus
    total_pecas: int | None
    valor_total: Decimal | None
    extracted_at: datetime | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemResponse] = Field(default_factory=list)
    totals: OrderTotals | None = Field(
        default=None,
        description="Calculado on-demand pelo service quando o pedido está pronto.",
    )


class ExtractOrderResponse(BaseModel):
    """Resposta do `POST /api/v1/orders/extract` (202 Accepted)."""

    order_id: UUID
    job_id: UUID
    status: OrderStatus
    poll_url: str


class RomaneioStatusResponse(BaseModel):
    """Resposta de `GET /api/v1/orders/{id}/romaneio` quando ainda não pronto.

    Quando o romaneio já está disponível o endpoint emite 302 redirect — este
    schema cobre os casos de geração em andamento ou recém-enfileirada.
    """

    romaneio_id: UUID | None
    status: RomaneioStatus
    download_url: str | None
    job_id: UUID | None
    poll_url: str | None
