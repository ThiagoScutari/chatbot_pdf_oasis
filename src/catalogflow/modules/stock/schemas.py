"""Schemas Pydantic do módulo `stock`.

Pydantic v2. Convenções:
- `from_attributes=True` para serializar direto do ORM (`StockCheck`, `ErpSubmission`).
- Status enumerados via `Literal` — bate com os CHECK constraints do banco.
- `summary` é calculado on-demand pelo router a partir do `result` JSONB.

`product_name` em `StockItemResult` é enriquecimento opcional — vem do
join com `OrderItem` que é feito no service ao montar o resultado.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from catalogflow.modules.stock.adapter import StockStatus

StockCheckStatusLiteral = Literal["pending", "checking", "completed", "error"]
ErpSubmissionStatusLiteral = Literal[
    "pending",
    "submitting",
    "accepted",
    "partially_accepted",
    "rejected",
    "error",
]


# ──────────────────────────────────────────────
#  POST /orders/{id}/stock-check — resposta 202
# ──────────────────────────────────────────────


class StockCheckEnqueueResponse(BaseModel):
    """Retornado pelo POST que dispara a consulta — análogo ao job_id."""

    stock_check_id: UUID
    job_id: UUID
    status: StockCheckStatusLiteral


# ──────────────────────────────────────────────
#  GET /orders/{id}/stock-check — resposta 200
# ──────────────────────────────────────────────


class StockItemResult(BaseModel):
    """Linha do resultado por item — espelha um elemento de `result.items`."""

    sku: str
    product_name: str | None = None
    size: str
    color_index: int
    color_hex: str | None = None
    requested: int
    available: int | None
    status: StockStatus


class StockCheckSummary(BaseModel):
    """Agregação por status — exibida no topo da seção de estoque."""

    total_items: int = Field(ge=0)
    available: int = Field(ge=0)
    partial: int = Field(ge=0)
    out_of_stock: int = Field(ge=0)
    unknown: int = Field(default=0, ge=0)


class StockCheckResponse(BaseModel):
    """Resposta completa do GET — status + summary + items."""

    model_config = ConfigDict(from_attributes=True)

    stock_check_id: UUID
    status: StockCheckStatusLiteral
    checked_at: datetime | None
    summary: StockCheckSummary
    items: list[StockItemResult]
    error_message: str | None = None


# ──────────────────────────────────────────────
#  POST /orders/{id}/submit — request + resposta 202
# ──────────────────────────────────────────────


class ErpSubmissionRequest(BaseModel):
    """Body do POST de envio. `customer_code` identifica a lojista no ERP."""

    customer_code: str = Field(min_length=1, max_length=64)


class ErpSubmissionEnqueueResponse(BaseModel):
    """Retornado pelo POST que dispara o envio — análogo ao job_id."""

    submission_id: UUID
    job_id: UUID
    status: ErpSubmissionStatusLiteral


# ──────────────────────────────────────────────
#  GET /orders/{id}/submission — resposta 200
# ──────────────────────────────────────────────


class ErpSubmissionResponse(BaseModel):
    """Resposta completa do GET — status + referência do ERP."""

    model_config = ConfigDict(from_attributes=True)

    submission_id: UUID
    status: ErpSubmissionStatusLiteral
    submitted_at: datetime | None
    erp_reference: str | None
    error_message: str | None = None
