"""Schemas Pydantic do módulo `catalog`."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CatalogStatus = Literal["pending", "processing", "ready", "error"]
JobStatus = Literal["pending", "running", "success", "error", "retry"]


# ──────────────────────────────────────────────
#  Request
# ──────────────────────────────────────────────


class CatalogCreateRequest(BaseModel):
    """Form fields que acompanham o upload do PDF.

    O arquivo em si chega como `UploadFile` separado no endpoint.
    """

    name: str = Field(min_length=1, max_length=255)
    collection: str | None = Field(default=None, max_length=128)


# ──────────────────────────────────────────────
#  Sub-payloads
# ──────────────────────────────────────────────


class SwatchPayload(BaseModel):
    """Quadrado de cor detectado no rodapé da página."""

    x0: float
    y0: float
    fill_hex: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    fill_rgb: tuple[float, float, float]


class AnalyzerWarningSchema(BaseModel):
    """Serialização de um `AnalyzerWarning` na API (ADR-011 D5)."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    severity: str
    page_index: int
    sku: str | None
    message: str
    detected_value: str | None


class CatalogProductResponse(BaseModel):
    """Produto detectado durante a análise do PDF."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sku: str
    name: str | None
    price: Decimal | None
    grade: str | None
    sizes: list[str] | None
    n_colors: int
    swatches: list[dict[str, Any]] = Field(default_factory=list)
    page_index: int


# ──────────────────────────────────────────────
#  Response
# ──────────────────────────────────────────────


class CatalogResponse(BaseModel):
    """Representação completa de um catálogo."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    brand_id: UUID
    name: str
    collection: str | None
    status: CatalogStatus
    n_pages: int | None
    n_product_pages: int | None
    n_skus: int | None
    n_fields: int | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    products: list[CatalogProductResponse] = Field(default_factory=list)
    warnings: list[AnalyzerWarningSchema] = Field(default_factory=list)


class ProcessCatalogResponse(BaseModel):
    """Resposta do `POST /api/v1/catalogs/process` (202 Accepted)."""

    catalog_id: UUID
    job_id: UUID
    status: CatalogStatus
    poll_url: str


# ──────────────────────────────────────────────
#  Jobs (compartilhado por todos os módulos)
# ──────────────────────────────────────────────


class JobResponse(BaseModel):
    """Snapshot público de um `Job`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_type: str
    status: JobStatus
    progress: int
    entity_id: UUID | None
    result: dict[str, Any] | None
    error: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime
