"""erp integration — stock_checks + erp_submissions

Revision ID: 0005_erp
Revises: 0004_web_auth
Create Date: 2026-05-13 00:00:00.000000

Sprint 04 — habilita os fluxos de integração com ERP.

- Cria `stock_checks`: registro de cada consulta de disponibilidade de estoque
  para um pedido. `result` (JSONB) guarda o snapshot completo retornado pelo
  adapter (itens com sku/size/color/requested/available/status).
- Cria `erp_submissions`: registro de cada envio de pedido ao ERP. UNIQUE em
  `order_id` — um pedido só pode ter um envio ativo. `erp_reference` é
  preenchido após o ERP aceitar (ex.: `MOCK-a7f3e91b` ou número do Consistem).

Nota: as colunas `order_items.stock_status` e `order_items.available_qty`
já foram criadas na migration 0003 — esta migration não as toca.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_erp"
down_revision: str | None = "0004_web_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── stock_checks ──────────────────────────
    op.create_table(
        "stock_checks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "orders.id",
                ondelete="CASCADE",
                name="fk_stock_checks_order_id_orders",
            ),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "brands.id",
                ondelete="CASCADE",
                name="fk_stock_checks_brand_id_brands",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','checking','completed','error')",
            name="ck_stock_checks_status",
        ),
    )
    op.create_index(
        "idx_stock_checks_order",
        "stock_checks",
        ["order_id"],
        unique=False,
    )

    # ── erp_submissions ───────────────────────
    op.create_table(
        "erp_submissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "orders.id",
                ondelete="CASCADE",
                name="fk_erp_submissions_order_id_orders",
            ),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "brands.id",
                ondelete="CASCADE",
                name="fk_erp_submissions_brand_id_brands",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erp_reference", sa.String(length=255), nullable=True),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("order_id", name="uq_erp_submissions_order_id"),
        sa.CheckConstraint(
            "status IN ('pending','submitting','accepted',"
            "'partially_accepted','rejected','error')",
            name="ck_erp_submissions_status",
        ),
    )
    op.create_index(
        "idx_erp_submissions_order",
        "erp_submissions",
        ["order_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_erp_submissions_order", table_name="erp_submissions")
    op.drop_table("erp_submissions")

    op.drop_index("idx_stock_checks_order", table_name="stock_checks")
    op.drop_table("stock_checks")
