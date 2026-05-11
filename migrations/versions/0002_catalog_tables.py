"""catalog tables — catalogs + catalog_products + jobs

Revision ID: 0002_catalog
Revises: 0001_auth
Create Date: 2026-05-11 00:00:00.000000

Cria as tabelas do domínio de catálogo + a tabela genérica `jobs` usada por
todas as Celery tasks. SQL fiel a `spec.md §7`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_catalog"
down_revision: str | None = "0001_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── catalogs ──────────────────────────────
    op.create_table(
        "catalogs",
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
                ondelete="CASCADE",
                name="fk_catalogs_brand_id_brands",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("collection", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("source_key", sa.String(length=512), nullable=True),
        sa.Column("output_key", sa.String(length=512), nullable=True),
        sa.Column("n_pages", sa.Integer(), nullable=True),
        sa.Column("n_product_pages", sa.Integer(), nullable=True),
        sa.Column("n_skus", sa.Integer(), nullable=True),
        sa.Column("n_fields", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
        sa.CheckConstraint(
            "status IN ('pending','processing','ready','error')",
            name="ck_catalogs_status",
        ),
    )
    op.create_index("ix_catalogs_brand_id", "catalogs", ["brand_id"], unique=False)
    op.create_index(
        "ix_catalogs_brand_status",
        "catalogs",
        ["brand_id", "status"],
        unique=False,
    )

    # ── catalog_products ──────────────────────
    op.create_table(
        "catalog_products",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "catalog_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "catalogs.id",
                ondelete="CASCADE",
                name="fk_catalog_products_catalog_id_catalogs",
            ),
            nullable=False,
        ),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=True),
        sa.Column("grade", sa.String(length=16), nullable=True),
        sa.Column(
            "sizes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "n_colors",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "swatches",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "catalog_id",
            "sku",
            "page_index",
            name="uq_catalog_products_catalog_sku_page",
        ),
    )
    op.create_index(
        "ix_catalog_products_catalog_id",
        "catalog_products",
        ["catalog_id"],
        unique=False,
    )

    # ── jobs ──────────────────────────────────
    op.create_table(
        "jobs",
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
                ondelete="CASCADE",
                name="fk_jobs_brand_id_brands",
            ),
            nullable=False,
        ),
        sa.Column("celery_id", sa.String(length=255), nullable=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "progress",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
        sa.UniqueConstraint("celery_id", name="uq_jobs_celery_id"),
        sa.CheckConstraint(
            "status IN ('pending','running','success','error','retry')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_jobs_progress_range",
        ),
    )
    op.create_index("ix_jobs_brand_id", "jobs", ["brand_id"], unique=False)
    op.create_index("ix_jobs_entity_id", "jobs", ["entity_id"], unique=False)
    op.create_index(
        "ix_jobs_brand_status",
        "jobs",
        ["brand_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_brand_status", table_name="jobs")
    op.drop_index("ix_jobs_entity_id", table_name="jobs")
    op.drop_index("ix_jobs_brand_id", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("ix_catalog_products_catalog_id", table_name="catalog_products")
    op.drop_table("catalog_products")

    op.drop_index("ix_catalogs_brand_status", table_name="catalogs")
    op.drop_index("ix_catalogs_brand_id", table_name="catalogs")
    op.drop_table("catalogs")
