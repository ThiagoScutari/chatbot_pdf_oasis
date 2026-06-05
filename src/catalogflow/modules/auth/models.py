"""Modelos ORM do módulo `auth`.

Convenções:
- IDs UUID gerados pelo Postgres via `gen_random_uuid()`.
- Timestamps `TIMESTAMP WITH TIME ZONE`, default `now()` no servidor.
- `ApiKey.key_hash` é SHA-256 hex (64 chars). O token plaintext não persiste.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from catalogflow.infra.database import Base


class Brand(Base):
    """Tenant principal — uma marca de moda (ex: Oasis Resortwear)."""

    __tablename__ = "brands"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'starter'"),
    )
    # Aponta para o `BrandFormatProfile` (JSON versionado em código) usado
    # pelo `PDFAnalyzer` (ADR-010 D2). Brands existentes herdam
    # `oasis_default` via server_default — comportamento Oasis preservado.
    format_profile_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'oasis_default'"),
    )
    logo_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="brand",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Brand id={self.id} slug={self.slug!r}>"


class ApiKey(Base):
    """Token de autenticação para integrações da brand.

    Apenas o hash SHA-256 é persistido. O `key_prefix` (primeiros 8 chars do
    token raw) serve para identificação visual em logs/UI sem expor o token.
    """

    __tablename__ = "api_keys"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    brand_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    last_used: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    brand: Mapped[Brand] = relationship(back_populates="api_keys")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ApiKey id={self.id} prefix={self.key_prefix!r}>"


class WebUser(Base):
    """Usuário humano que loga na interface web via email+senha.

    Coexiste com `ApiKey`: o token `cf_...` segue válido para integrações
    diretas à API REST. A diferença é o canal: `WebUser` autentica a UI
    (cookie de sessão), `ApiKey` autentica chamadas HTTP server-to-server.

    Campos:
    - `password_hash`: bcrypt (lib `bcrypt` direta). Nullable para permitir cadastro
      pendente de aprovação onde a senha ainda não foi definida, ou
      acesso só por magic-link.
    - `role`: `'admin'` (gerencia usuários da brand) ou `'operator'`
      (uso normal da UI). Sem RBAC granular nesta fase.
    - `is_active`: novos cadastros começam `False` — admin precisa
      aprovar antes do primeiro login. Magic-link e senha checam isso.
    """

    __tablename__ = "web_users"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    brand_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'operator'"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    brand: Mapped[Brand] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WebUser id={self.id} email={self.email!r}>"


class MagicLink(Base):
    """Token de login sem senha — TTL 15min, uso único.

    O `token` é o segredo URL-safe que vai no link enviado por email
    (`/magic-link/{token}`). Comparado em texto puro: como já é um
    `secrets.token_urlsafe()` de 32 bytes (256 bits) e expira em 15 min,
    a janela de exploração é muito menor que a de uma senha permanente.
    `used_at` marca o consumo — verificar antes de aceitar.
    """

    __tablename__ = "magic_links"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("web_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[WebUser] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MagicLink id={self.id} user_id={self.user_id}>"


class LoginAttempt(Base):
    """Tentativa de login — feeds o rate-limit de 5 falhas / 5 min.

    `identifier` guarda o email em lowercase. Sucessos também são
    registrados pra resetar a janela de bloqueio (qualquer login OK
    nos últimos 5 min limpa a contagem de falhas anteriores).
    """

    __tablename__ = "login_attempts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    success: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("false"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LoginAttempt id={self.id} identifier={self.identifier!r}>"
