"""brands.format_profile_id — wiring multi-marca do analyzer (ADR-010 D2)

Revision ID: 0009_brand_format_profile
Revises: 0008_catalogs_warnings
Create Date: 2026-06-01 00:00:00.000000

Adiciona `format_profile_id` (VARCHAR(64), NOT NULL, default
`'oasis_default'`) em `brands`. Cada brand passa a referenciar o
`BrandFormatProfile` usado pelo `PDFAnalyzer`. Brands existentes (incl.
Oasis em produção) herdam `oasis_default` automaticamente — processamento
preservado bit-a-bit.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_brand_format_profile"
down_revision: str | None = "0008_catalogs_warnings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "brands",
        sa.Column(
            "format_profile_id",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'oasis_default'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("brands", "format_profile_id")
