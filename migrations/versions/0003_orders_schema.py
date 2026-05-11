"""orders schema — brands.logo_key + orders + order_items + romaneios

Revision ID: 0003_orders
Revises: 0002_catalog
Create Date: 2026-05-11 00:00:00.000000

Sprint 02 — habilita o pipeline de extração de pedido e geração de romaneio.

- Adiciona `brands.logo_key` (S3 key da logo da marca, opcional).
- Cria `orders` (cabeçalho do pedido) com FK para `brands` e `catalogs`.
- Cria `order_items` com CHECK `quantity > 0` e UNIQUE
  `(order_id, sku, color_index, size)`.
- Cria `romaneios` 1:1 com `orders`.

SQL alinhado com `spec.md §7` e `docs/sprint_02/PRD_sprint_02.md §E1`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_orders"
down_revision: str | None = "0002_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── brands.logo_key ───────────────────────
    op.add_column(
        "brands",
        sa.Column("logo_key", sa.String(length=512), nullable=True),
    )

    # ── orders ────────────────────────────────
    op.create_table(
        "orders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "brands.id",
                name="fk_orders_brand_id_brands",
            ),
            nullable=False,
        ),
        sa.Column(
            "catalog_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "catalogs.id",
                name="fk_orders_catalog_id_catalogs",
            ),
            nullable=True,
        ),
        sa.Column("lojista_token", sa.String(length=64), nullable=True),
        sa.Column("lojista_name", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column("source_pdf_key", sa.String(length=512), nullable=True),
        sa.Column("total_pecas", sa.Integer(), nullable=True),
        sa.Column("valor_total", sa.Numeric(12, 2), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_orders_brand_id", "orders", ["brand_id"], unique=False)
    op.create_index("idx_orders_catalog_id", "orders", ["catalog_id"], unique=False)

    # ── order_items ───────────────────────────
    op.create_table(
        "order_items",
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
                name="fk_order_items_order_id_orders",
            ),
            nullable=False,
        ),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=255), nullable=True),
        sa.Column(
            "color_index",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("color_hex", sa.String(length=7), nullable=True),
        sa.Column("size", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("stock_status", sa.String(length=32), nullable=True),
        sa.Column("available_qty", sa.Integer(), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        sa.UniqueConstraint(
            "order_id",
            "sku",
            "color_index",
            "size",
            name="uq_order_items_order_sku_color_size",
        ),
    )
    op.create_index(
        "idx_order_items_order_id",
        "order_items",
        ["order_id"],
        unique=False,
    )

    # ── romaneios ─────────────────────────────
    op.create_table(
        "romaneios",
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
                name="fk_romaneios_order_id_orders",
            ),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "brands.id",
                name="fk_romaneios_brand_id_brands",
            ),
            nullable=False,
        ),
        sa.Column("output_key", sa.String(length=512), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("order_id", name="uq_romaneios_order_id"),
    )
    op.create_index(
        "idx_romaneios_brand_id",
        "romaneios",
        ["brand_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_romaneios_brand_id", table_name="romaneios")
    op.drop_table("romaneios")

    op.drop_index("idx_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")

    op.drop_index("idx_orders_catalog_id", table_name="orders")
    op.drop_index("idx_orders_brand_id", table_name="orders")
    op.drop_table("orders")

    op.drop_column("brands", "logo_key")
