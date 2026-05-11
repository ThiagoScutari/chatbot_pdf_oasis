"""Envelope de resposta padrão da API.

Formato (spec.md §8):

    Sucesso:
    { "success": true,  "data": <T>, "error": null, "meta": {...} }

    Erro:
    { "success": false, "data": null, "error": {code, message, details}, "meta": {...} }

`meta.request_id` é o UUID gerado pelo middleware `RequestIdMiddleware`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ErrorPayload(BaseModel):
    """Bloco `error` quando `success=false`."""

    code: str = Field(description="Identificador estável (ex: PDF_ENCRYPTED).")
    message: str = Field(description="Mensagem human-readable em pt-BR.")
    details: dict[str, Any] = Field(default_factory=dict)


class ResponseMeta(BaseModel):
    """Bloco `meta` presente em toda resposta."""

    request_id: str
    timestamp: datetime


class StandardResponse(BaseModel, Generic[T]):
    """Envelope canônico. `T` é o tipo do payload em `data`."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool
    data: T | None = None
    error: ErrorPayload | None = None
    meta: ResponseMeta


# ──────────────────────────────────────────────
#  Helpers de construção
# ──────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _ensure_request_id(request_id: str | None) -> str:
    """Garante um request_id presente. Usado quando o middleware não anexou um."""
    return request_id or str(uuid4())


def ok(
    data: T,
    *,
    request_id: str | None = None,
) -> StandardResponse[T]:
    """Envelope de sucesso."""
    return StandardResponse[T](
        success=True,
        data=data,
        error=None,
        meta=ResponseMeta(
            request_id=_ensure_request_id(request_id),
            timestamp=_now(),
        ),
    )


def error_response(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> StandardResponse[None]:
    """Envelope de erro (sem `data`)."""
    return StandardResponse[None](
        success=False,
        data=None,
        error=ErrorPayload(code=code, message=message, details=details or {}),
        meta=ResponseMeta(
            request_id=_ensure_request_id(request_id),
            timestamp=_now(),
        ),
    )
