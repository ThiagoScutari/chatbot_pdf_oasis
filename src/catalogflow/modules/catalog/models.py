"""Modelos ORM do módulo `catalog`.

`Catalog` é a entidade raiz (1 PDF de catálogo, 1 registro).
`CatalogProduct` é o detalhamento por produto detectado durante a análise.
`Job` é genérico — usado por todas as Celery tasks do projeto.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from catalogflow.infra.database import Base

# ──────────────────────────────────────────────
#  Constantes de status (também em check constraints)
# ──────────────────────────────────────────────

CatalogStatusValue = str  # "pending" | "processing" | "ready" | "error"
JobStatusValue = str  # "pending" | "running" | "success" | "error" | "retry"


class Catalog(Base):
    """Catálogo PDF submetido para processamento.

    Ciclo de vida:
        pending → processing → ready
                              → error
    """

    __tablename__ = "catalogs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','ready','error')",
            name="ck_catalogs_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    brand_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    collection: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[CatalogStatusValue] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
    )
    source_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    output_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    n_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_product_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_skus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_fields: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",  # nome da coluna no banco; `metadata` é reservado no Base
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("web_users.id", ondelete="SET NULL"),
        nullable=True,
    )

    products: Mapped[list[CatalogProduct]] = relationship(
        back_populates="catalog",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CatalogProduct.page_index",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Catalog id={self.id} status={self.status}>"


class CatalogProduct(Base):
    """Produto detectado em uma página do catálogo."""

    __tablename__ = "catalog_products"
    __table_args__ = (
        UniqueConstraint(
            "catalog_id",
            "sku",
            "page_index",
            name="uq_catalog_products_catalog_sku_page",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    catalog_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalogs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    grade: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sizes: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    n_colors: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    swatches: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    page_index: Mapped[int] = mapped_column(Integer, nullable=False)

    catalog: Mapped[Catalog] = relationship(back_populates="products")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CatalogProduct sku={self.sku} page={self.page_index}>"


class Job(Base):
    """Tarefa assíncrona genérica do projeto (Celery + Postgres).

    O `entity_id` aponta para o recurso de domínio (catalog, order...) e o
    `job_type` discrimina o caso (`catalog.process`, `order.extract`...).
    `status` segue: pending → running → success | error | retry.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','success','error','retry')",
            name="ck_jobs_status",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_jobs_progress_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    brand_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    celery_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
    )
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    status: Mapped[JobStatusValue] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
    )
    progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job id={self.id} type={self.job_type} status={self.status}>"
