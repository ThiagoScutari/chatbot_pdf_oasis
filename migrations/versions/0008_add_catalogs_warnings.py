"""catalogs.warnings — observabilidade não-bloqueante do analyzer

Revision ID: 0008_catalogs_warnings
Revises: 0007_jobs_started_at
Create Date: 2026-06-01 00:00:00.000000

Adiciona `warnings` (JSONB, NOT NULL, default `'[]'`) em `catalogs` para
persistir a lista de `AnalyzerWarning` produzida durante o processamento
(ADR-011 D5). Catálogos existentes herdam `[]` automaticamente.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008_catalogs_warnings"
down_revision: str | None = "0007_jobs_started_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalogs",
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("catalogs", "warnings")
