"""Schemas Pydantic do módulo `auth` (DTOs de entrada/saída)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

BrandPlan = Literal["starter", "growth", "enterprise"]

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class BrandCreateRequest(BaseModel):
    """Payload para `POST /internal/brands`."""

    slug: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    plan: BrandPlan = "starter"

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str) -> str:
        lowered = value.lower().strip()
        if not _SLUG_RE.fullmatch(lowered):
            raise ValueError(
                "slug must be lowercase alphanumeric with optional hyphens",
            )
        return lowered


class BrandResponse(BaseModel):
    """Representação pública de uma `Brand`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    plan: BrandPlan
    created_at: datetime
    updated_at: datetime


class ApiKeyCreateRequest(BaseModel):
    """Payload para `POST /internal/brands/{id}/api-keys`."""

    name: str = Field(min_length=1, max_length=128)
    expires_at: datetime | None = None


class ApiKeyResponse(BaseModel):
    """Metadados públicos de uma `ApiKey` — sem o token bruto."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    brand_id: UUID
    name: str
    key_prefix: str
    last_used: datetime | None
    expires_at: datetime | None
    created_at: datetime


class ApiKeyCreateResponse(BaseModel):
    """Resposta da criação de uma API Key.

    O campo `raw_key` é retornado **uma única vez** no momento da criação e
    nunca mais. Após esse instante, apenas o `key_hash` permanece no banco.
    """

    api_key: ApiKeyResponse
    raw_key: str = Field(
        description="Token em plaintext. Armazene com segurança — não pode ser recuperado depois.",
    )
