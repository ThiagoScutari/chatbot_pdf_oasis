"""jobs.started_at — timestamp de início de execução

Revision ID: 0007_jobs_started_at
Revises: 0006_soft_delete
Create Date: 2026-06-01 00:00:00.000000

Adiciona `started_at` (timezone-aware, nullable) em `jobs`. Preenchido
por `_claim_job` ao transitar `pending → running` — habilita detecção
de jobs stuck (S07-01) sem depender de `updated_at`, que é sobrescrito
em cada UPDATE subsequente.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_jobs_started_at"
down_revision: str | None = "0006_soft_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "started_at")
