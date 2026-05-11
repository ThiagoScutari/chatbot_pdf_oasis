"""Lógica de autenticação multi-tenant.

Token format:
    `cf_<base64url(secrets.token_bytes(32))>`
    Total ~46 chars. O prefixo `cf_` é constante (config `API_KEY_PREFIX`).

Persistência:
    Apenas o hash SHA-256 do token raw é gravado. O plaintext é retornado
    UMA ÚNICA VEZ no momento da criação e nunca mais.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.settings import get_settings
from catalogflow.modules.auth.models import ApiKey, Brand
from catalogflow.shared.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
)

_KEY_RANDOM_BYTES: Final = 32
_KEY_PREFIX_LEN: Final = 8


def _generate_raw_key(prefix: str) -> str:
    """Gera token `<prefix><random urlsafe>`.

    Comprimento total: len(prefix) + ~43 chars (32 bytes em base64url).
    """
    random_part = secrets.token_urlsafe(_KEY_RANDOM_BYTES)
    return f"{prefix}{random_part}"


def hash_key(raw_key: str) -> str:
    """Calcula o hash SHA-256 hex do token. Use comparação constant-time."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _key_prefix(raw_key: str) -> str:
    """Primeiros 8 chars do token — armazenados para identificação visual."""
    return raw_key[:_KEY_PREFIX_LEN]


# ──────────────────────────────────────────────
#  Brand
# ──────────────────────────────────────────────


async def create_brand(
    db: AsyncSession,
    *,
    slug: str,
    name: str,
    plan: str = "starter",
) -> Brand:
    """Cria uma nova brand. Levanta `ConflictError` se o slug já existe."""
    brand = Brand(slug=slug, name=name, plan=plan)
    db.add(brand)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            f"brand slug '{slug}' já existe",
            code="BRAND_SLUG_TAKEN",
            details={"slug": slug},
        ) from exc
    await db.refresh(brand)
    return brand


async def get_brand_by_id(db: AsyncSession, brand_id: UUID) -> Brand:
    """Busca brand por id ou levanta `NotFoundError`."""
    brand = await db.get(Brand, brand_id)
    if brand is None:
        raise NotFoundError(
            f"brand {brand_id} não encontrada",
            code="BRAND_NOT_FOUND",
            details={"brand_id": str(brand_id)},
        )
    return brand


async def get_brand_by_slug(db: AsyncSession, slug: str) -> Brand | None:
    """Retorna brand por slug ou `None`."""
    stmt = select(Brand).where(Brand.slug == slug)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# ──────────────────────────────────────────────
#  ApiKey
# ──────────────────────────────────────────────


async def create_api_key(
    db: AsyncSession,
    *,
    brand_id: UUID,
    name: str,
    expires_at: datetime | None = None,
) -> tuple[ApiKey, str]:
    """Cria uma API Key e retorna `(model, raw_token)`.

    O `raw_token` retornado é o ÚNICO momento em que o plaintext é exposto.
    Após esse retorno, apenas `key_hash` permanece no banco.
    """
    await get_brand_by_id(db, brand_id)  # garante que a brand existe

    settings = get_settings()
    raw = _generate_raw_key(settings.api_key_prefix)
    api_key = ApiKey(
        brand_id=brand_id,
        name=name,
        key_hash=hash_key(raw),
        key_prefix=_key_prefix(raw),
        expires_at=expires_at,
    )
    db.add(api_key)
    try:
        await db.flush()
    except IntegrityError as exc:  # pragma: no cover - colisão de SHA-256 é absurda
        await db.rollback()
        raise ConflictError(
            "colisão improvável de hash de api key — tente novamente",
            code="API_KEY_HASH_COLLISION",
        ) from exc
    await db.refresh(api_key)
    return api_key, raw


async def verify_api_key(db: AsyncSession, raw_key: str) -> Brand:
    """Valida `raw_key` e retorna a `Brand` dona.

    Levanta `AuthenticationError` se o token é inválido, desconhecido ou
    expirado. Não atualiza `last_used` — isso é responsabilidade do caller
    via `touch_last_used()` (para não bloquear o caminho crítico).
    """
    if not raw_key:
        raise AuthenticationError(
            "credencial ausente",
            code="MISSING_CREDENTIAL",
        )

    settings = get_settings()
    if not raw_key.startswith(settings.api_key_prefix):
        raise AuthenticationError(
            "credencial em formato inválido",
            code="MALFORMED_CREDENTIAL",
        )

    digest = hash_key(raw_key)
    stmt = select(ApiKey).where(ApiKey.key_hash == digest)
    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise AuthenticationError(
            "credencial inválida",
            code="INVALID_CREDENTIAL",
        )

    if api_key.expires_at is not None:
        now = datetime.now(UTC)
        # Postgres devolve TIMESTAMPTZ como aware; protegemos contra naive
        # apenas para evitar surpresa em SQLite/sqlite-like (não suportado).
        expires_at = api_key.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            raise AuthenticationError(
                "credencial expirada",
                code="CREDENTIAL_EXPIRED",
                details={"expired_at": expires_at.isoformat()},
            )

    brand = await db.get(Brand, api_key.brand_id)
    if brand is None:  # pragma: no cover - FK CASCADE garante isso
        raise AuthenticationError(
            "credencial órfã (brand removida)",
            code="ORPHAN_CREDENTIAL",
        )
    return brand


async def touch_last_used(db: AsyncSession, raw_key: str) -> None:
    """Atualiza `last_used = now()` para a chave. Best-effort (silencia erros).

    Chamado por dependency em BackgroundTasks para não bloquear a request.
    """
    try:
        digest = hash_key(raw_key)
        stmt = select(ApiKey).where(ApiKey.key_hash == digest)
        result = await db.execute(stmt)
        api_key = result.scalar_one_or_none()
        if api_key is not None:
            api_key.last_used = datetime.now(UTC)
            await db.commit()
    except Exception:  # pragma: no cover - best effort
        await db.rollback()


async def rotate_api_key(
    db: AsyncSession,
    *,
    api_key_id: UUID,
) -> tuple[ApiKey, str]:
    """Gera novo token para a chave dada. Retorna `(model, raw_token)`."""
    api_key = await db.get(ApiKey, api_key_id)
    if api_key is None:
        raise NotFoundError(
            f"api key {api_key_id} não encontrada",
            code="API_KEY_NOT_FOUND",
            details={"api_key_id": str(api_key_id)},
        )
    settings = get_settings()
    raw = _generate_raw_key(settings.api_key_prefix)
    api_key.key_hash = hash_key(raw)
    api_key.key_prefix = _key_prefix(raw)
    api_key.last_used = None
    await db.flush()
    await db.refresh(api_key)
    return api_key, raw
