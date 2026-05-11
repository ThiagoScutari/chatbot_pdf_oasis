"""FastAPI dependencies do módulo `auth`.

`get_current_brand` é o gate de toda rota pública. Política:
- Sem header `Authorization` → `AuthenticationError(MISSING_CREDENTIAL)`
- Header sem esquema `Bearer` → `AuthenticationError(MALFORMED_CREDENTIAL)`
- Token desconhecido ou expirado → `AuthenticationError(...)`
- `last_used` é atualizado em `BackgroundTasks` para não bloquear a resposta.
"""

from __future__ import annotations

from fastapi import BackgroundTasks, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.database import get_db, get_session_factory
from catalogflow.infra.settings import get_settings
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand
from catalogflow.shared.errors import AuthenticationError


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise AuthenticationError(
            "header Authorization ausente",
            code="MISSING_CREDENTIAL",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError(
            "esperado 'Authorization: Bearer <token>'",
            code="MALFORMED_CREDENTIAL",
        )
    return token.strip()


async def _touch_last_used_bg(raw_key: str) -> None:
    """Atualiza `last_used` em sessão própria (rode em BackgroundTasks)."""
    factory = get_session_factory()
    async with factory() as session:
        await auth_service.touch_last_used(session, raw_key)


async def get_current_brand(
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> Brand:
    """Resolve a `Brand` autenticada a partir do header `Authorization`.

    Use como `brand: Brand = Depends(get_current_brand)` em handlers.
    """
    raw = _extract_bearer(authorization)
    brand = await auth_service.verify_api_key(db, raw)
    background_tasks.add_task(_touch_last_used_bg, raw)
    return brand


async def require_internal_secret(
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
) -> None:
    """Gate para rotas em `/internal/*`.

    O segredo é configurado em `.env` (`INTERNAL_SECRET`). Use somente para
    automações administrativas — nunca exponha esse segredo a clientes.
    """
    expected = get_settings().internal_secret.get_secret_value()
    if not expected or not x_internal_secret:
        raise AuthenticationError(
            "rota interna — header X-Internal-Secret obrigatório",
            code="MISSING_INTERNAL_SECRET",
        )
    if not _constant_time_eq(x_internal_secret, expected):
        raise AuthenticationError(
            "rota interna — segredo inválido",
            code="INVALID_INTERNAL_SECRET",
        )


def _constant_time_eq(a: str, b: str) -> bool:
    """Comparação constant-time de strings — evita timing attacks."""
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
