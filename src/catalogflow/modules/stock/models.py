"""Modelos ORM do módulo `stock`.

`StockCheck` registra cada consulta de disponibilidade de estoque para um
pedido. `ErpSubmission` registra cada envio de pedido ao ERP. Ambos os
modelos seguem o schema definido na migration `0005_erp_integration.py`
e referenciam `orders` + `brands` para isolamento multi-tenant.

`result` é JSONB livre — guarda o payload completo do adapter para
auditoria. Os schemas Pydantic (`schemas.py`, Fase C) tipam a leitura.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from catalogflow.infra.database import Base

# Status válidos — espelham os CHECK constraints na migration 0005.
StockCheckStatus = str  # "pending" | "checking" | "completed" | "error"
ErpSubmissionStatus = str  # "pending" | "submitting" | "accepted"
#                          | "partially_accepted" | "rejected" | "error"


class StockCheck(Base):
    """Consulta de estoque para um pedido — uma linha por consulta disparada.

    Pedidos podem ter múltiplas `StockCheck` ao longo do tempo (re-consulta
    após N dias, por exemplo). O `result` JSONB carrega o snapshot completo
    do que o adapter respondeu na consulta — útil para auditoria histórica
    mesmo quando o estoque atual já mudou.
    """

    __tablename__ = "stock_checks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','checking','completed','error')",
            name="ck_stock_checks_status",
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
    brand_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[StockCheckStatus] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
    )
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StockCheck id={self.id} order_id={self.order_id} status={self.status}>"


class ErpSubmission(Base):
    """Envio de pedido ao ERP — único por pedido (UNIQUE em `order_id`).

    Um pedido só pode ter uma submissão ativa. Re-submeter após `rejected`
    requer atualizar a linha existente (ou apagar e reinserir), nunca criar
    uma segunda — o UNIQUE constraint impede.

    `erp_reference` é preenchido após o ERP aceitar (ex.: `MOCK-a7f3e91b`
    para o MockAdapter ou número do pedido no Consistem).
    """

    __tablename__ = "erp_submissions"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_erp_submissions_order_id"),
        CheckConstraint(
            "status IN ('pending','submitting','accepted','partially_accepted','rejected','error')",
            name="ck_erp_submissions_status",
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
    brand_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[ErpSubmissionStatus] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    erp_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ErpSubmission id={self.id} order_id={self.order_id} "
            f"status={self.status} ref={self.erp_reference}>"
        )
