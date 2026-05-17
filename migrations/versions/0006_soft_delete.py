"""soft-delete — catalogs, orders, romaneios

Revision ID: 0006_soft_delete
Revises: 0005_erp
Create Date: 2026-05-17 00:00:00.000000

Adiciona `deleted_at` e `deleted_by` (FK p/ web_users) em `catalogs`,
`orders` e `romaneios`. Toda exclusão pela UI vira soft-delete:

- Catálogo: retenção 60 dias (job de limpeza permanente é futuro).
- Pedido e romaneio: somem da UI imediatamente, mas ficam para auditoria.

`catalog_products`, `order_items`, `stock_checks` e `erp_submissions`
não recebem coluna — seguem o pai (cascade lógico via filtro).

Índices parciais `WHERE deleted_at IS NULL` mantêm o custo das listagens
constante mesmo com volume grande de itens excluídos acumulados.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_soft_delete"
down_revision: str | None = "0005_erp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES: tuple[str, ...] = ("catalogs", "orders", "romaneios")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(
                "deleted_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "web_users.id",
                    name=f"fk_{table}_deleted_by_web_users",
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
        )

    # Índices parciais — só linhas vivas. Mantêm o custo das listagens
    # constante quando o número de soft-deleted crescer.
    op.create_index(
        "idx_catalogs_deleted",
        "catalogs",
        ["deleted_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_orders_deleted",
        "orders",
        ["deleted_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_orders_deleted", table_name="orders")
    op.drop_index("idx_catalogs_deleted", table_name="catalogs")
    for table in _TABLES:
        op.drop_constraint(
            f"fk_{table}_deleted_by_web_users",
            table,
            type_="foreignkey",
        )
        op.drop_column(table, "deleted_by")
        op.drop_column(table, "deleted_at")
