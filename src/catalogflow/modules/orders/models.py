"""Modelos ORM do módulo `orders`.

`Order` representa um pedido (PDF preenchido recebido de uma lojista).
`OrderItem` é uma linha do pedido — granularidade SKU x cor x tamanho.

Schema SQL exato em `spec.md §7` e na migration `0003_orders_schema.py`.

Relationships:
- `Order.items` — `selectinload` no service (lição da Sprint 01 sobre
  `MissingGreenlet` em async/lazy load).
- `Order.romaneio` — 1:1 com `Romaneio` (back_populates em romaneio/models.py).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from catalogflow.infra.database import Base

if TYPE_CHECKING:
    # Apenas para anotações — em runtime SQLAlchemy resolve via class registry.
    from catalogflow.modules.romaneio.models import Romaneio

# Status válidos — não há CHECK constraint no banco (Fase A) por flexibilidade
# da Fase E para introduzir 'error' quando o PDF chega flatten.
OrderStatusValue = str  # "draft" | "extracted" | "confirmed" | "cancelled" | "error"


class Order(Base):
    """Pedido — agrupamento de itens enviado por uma lojista."""

    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    brand_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brands.id"),
        nullable=False,
        index=True,
    )
    catalog_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalogs.id"),
        nullable=True,
        index=True,
    )
    lojista_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lojista_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[OrderStatusValue] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'draft'"),
    )
    source_pdf_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    total_pecas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valor_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="(OrderItem.sku, OrderItem.color_index, OrderItem.size)",
    )
    # Forward reference por string — SQLAlchemy resolve via class registry no
    # primeiro acesso. `Romaneio` é definido em `romaneio/models.py`, importado
    # via env.py / lifespan do app.
    romaneio: Mapped[Romaneio | None] = relationship(
        "Romaneio",
        back_populates="order",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Order id={self.id} status={self.status}>"


class OrderItem(Base):
    """Linha de pedido — granularidade SKU x cor x tamanho.

    `UNIQUE(order_id, sku, color_index, size)` impede duplicidade quando
    o mesmo SKU/cor/tamanho aparece em múltiplos widgets (somatório feito
    no normalizer antes de persistir).
    """

    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        UniqueConstraint(
            "order_id",
            "sku",
            "color_index",
            "size",
            name="uq_order_items_order_sku_color_size",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    color_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    color_hex: Mapped[str | None] = mapped_column(String(7), nullable=True)
    size: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    stock_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    available_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)

    order: Mapped[Order] = relationship(back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<OrderItem sku={self.sku} cor{self.color_index} "
            f"{self.size}={self.quantity}>"
        )
