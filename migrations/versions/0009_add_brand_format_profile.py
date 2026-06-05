"""brands.format_profile_id — wiring multi-marca do analyzer (ADR-010 D2)

Revision ID: 0009_brand_format_profile
Revises: 0008_catalogs_warnings
Create Date: 2026-06-01 00:00:00.000000

Adiciona `format_profile_id` (VARCHAR(64), NOT NULL, default
`'hyphenated_single_price'`) em `brands`. Cada brand passa a referenciar
o `BrandFormatProfile` usado pelo `PDFAnalyzer`. Brands existentes (incl.
Oasis em produção) herdam `hyphenated_single_price` automaticamente —
processamento preservado bit-a-bit.

Nota (Fase E): esta migration foi editada in-place para o novo nome de
profile. Seguro porque a `0009` nunca foi aplicada a nenhum banco real
(não está em `main`); não é uma migration de dados.
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
            server_default=sa.text("'hyphenated_single_price'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("brands", "format_profile_id")
