"""Modelo ORM do módulo `romaneio`.

`Romaneio` é a contraparte 1:1 de `Order` — armazena o PDF gerado pelo
`RomaneioBuilder`. O service guarda apenas a chave S3 (`output_key`); a
geração da URL assinada é feita on-demand no endpoint de download.

Schema SQL exato em `spec.md §7` e na migration `0003_orders_schema.py`.

`Order.romaneio` (back_populates) é declarada em `orders/models.py` via
string forward reference — quando `Romaneio` aparece no class registry do
SQLAlchemy (importação deste módulo), a relação resolve nos dois sentidos.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from catalogflow.infra.database import Base
from catalogflow.modules.orders.models import Order


class Romaneio(Base):
    """Romaneio PDF gerado para um pedido.

    Relação 1:1 com `Order` — `UNIQUE(order_id)` garante que um pedido
    nunca tenha dois romaneios. Regeneração sobrescreve `output_key` no
    mesmo registro (decisão da Fase E).
    """

    __tablename__ = "romaneios"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_romaneios_order_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id"),
        nullable=False,
    )
    brand_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brands.id"),
        nullable=False,
        index=True,
    )
    output_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
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

    order: Mapped[Order] = relationship(back_populates="romaneio")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Romaneio id={self.id} order_id={self.order_id}>"
