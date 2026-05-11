"""Rotas administrativas (`/internal/*`) do módulo `auth`.

Endereços EXPOSTOS NA REDE INTERNA. Protegidos por `X-Internal-Secret`.
NÃO entram em `/api/v1/` e NÃO devem ser publicados em OpenAPI público.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.database import get_db
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.dependencies import require_internal_secret
from catalogflow.modules.auth.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyResponse,
    BrandCreateRequest,
    BrandResponse,
)

router = APIRouter(
    prefix="/internal",
    tags=["internal-auth"],
    dependencies=[Depends(require_internal_secret)],
    include_in_schema=False,
)


@router.post(
    "/brands",
    response_model=BrandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_brand_endpoint(
    payload: BrandCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> BrandResponse:
    """Cria uma nova brand."""
    brand = await auth_service.create_brand(
        db,
        slug=payload.slug,
        name=payload.name,
        plan=payload.plan,
    )
    return BrandResponse.model_validate(brand)


@router.post(
    "/brands/{brand_id}/api-keys",
    response_model=ApiKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key_endpoint(
    brand_id: UUID,
    payload: ApiKeyCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreateResponse:
    """Cria uma API key para a brand. O `raw_key` é retornado UMA única vez."""
    api_key, raw = await auth_service.create_api_key(
        db,
        brand_id=brand_id,
        name=payload.name,
        expires_at=payload.expires_at,
    )
    return ApiKeyCreateResponse(
        api_key=ApiKeyResponse.model_validate(api_key),
        raw_key=raw,
    )
