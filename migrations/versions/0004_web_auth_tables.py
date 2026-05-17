"""web auth tables — web_users + magic_links + login_attempts

Revision ID: 0004_web_auth
Revises: 0003_orders
Create Date: 2026-05-13 00:00:00.000000

Sprint 03.5 — substitui o login por API Key por email+senha.

A tabela `api_keys` permanece intacta: continua sendo a credencial usada
em chamadas diretas à API REST (`Authorization: Bearer cf_...`). O que
muda é o fluxo *web*: o cookie de sessão passa a se referir a um
`web_user`, não mais a uma API Key.

Detalhes:
- `password_hash` é nullable porque um usuário cadastrado via convite ou
  via magic-link ainda pode não ter senha definida.
- `magic_links.token` carrega o segredo já em formato URL-safe gerado por
  `secrets.token_urlsafe()` — guardamos em texto pois o token é
  efêmero (TTL 15 min, single-use).
- `login_attempts.identifier` armazena o e-mail tentado (lowercase). O
  índice composto (identifier, attempted_at) viabiliza a janela móvel de
  rate-limit (5 falhas em 5 min).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_web_auth"
down_revision: str | None = "0003_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_users",
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
                "brands.id", ondelete="CASCADE", name="fk_web_users_brand_id_brands"
            ),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column(
            "role",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'operator'"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
        sa.UniqueConstraint("email", name="uq_web_users_email"),
    )
    op.create_index("ix_web_users_brand_id", "web_users", ["brand_id"], unique=False)

    op.create_table(
        "magic_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "web_users.id", ondelete="CASCADE", name="fk_magic_links_user_id_web_users"
            ),
            nullable=False,
        ),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("token", name="uq_magic_links_token"),
    )
    op.create_index("ix_magic_links_token", "magic_links", ["token"], unique=False)
    op.create_index("ix_magic_links_user_id", "magic_links", ["user_id"], unique=False)

    op.create_table(
        "login_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("identifier", sa.String(length=255), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "success",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_login_attempts_identifier_attempted_at",
        "login_attempts",
        ["identifier", "attempted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_login_attempts_identifier_attempted_at", table_name="login_attempts")
    op.drop_table("login_attempts")
    op.drop_index("ix_magic_links_user_id", table_name="magic_links")
    op.drop_index("ix_magic_links_token", table_name="magic_links")
    op.drop_table("magic_links")
    op.drop_index("ix_web_users_brand_id", table_name="web_users")
    op.drop_table("web_users")
