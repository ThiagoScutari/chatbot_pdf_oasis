"""Autenticação por sessão para a UI web (Sprint 03.5).

Cookie de sessão (`cf_session`) carrega um dict assinado por HMAC com:
- `u`: UUID (str) do `WebUser` logado.
- `k`: API Key plaintext (`cf_...`) emitida **por sessão**, válida só
  enquanto o cookie está vigente. Usada pelo *proxy* da web layer para
  chamar a API REST interna (`/api/v1/...`).

Por que armazenar a API Key dentro do cookie?
A API REST autentica via `Authorization: Bearer cf_<token>`. Como o
hash SHA-256 é a única coisa persistida, não temos como "recuperar" o
plaintext depois — então o emitimos só no login e o seguramos no cookie
assinado. Cookie é `httponly`+`samesite=lax`+`secure` em produção, e
o token vive no máximo 8h (mesmo TTL do cookie).

No logout, a API Key da sessão é **revogada** (linha apagada de
`api_keys`), garantindo que mesmo que o cookie vaze depois, o token
embutido nele não passe mais pela validação SHA-256.
"""

from __future__ import annotations

import json
from typing import Final
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.database import get_db
from catalogflow.infra.settings import get_settings
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import ApiKey, Brand, WebUser

SESSION_COOKIE: Final[str] = "cf_session"
SESSION_MAX_AGE: Final[int] = 60 * 60 * 8  # 8 horas
_SALT: Final[str] = "catalogflow.web.session"
_WEB_API_KEY_NAME: Final[str] = "__web_session__"


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=secret_key, salt=_SALT)


def create_session(user_id: UUID, api_key: str, secret_key: str) -> str:
    """Serializa `(user_id, api_key)` num token assinado pelo `secret_key`."""
    payload = json.dumps({"u": str(user_id), "k": api_key})
    return _serializer(secret_key).dumps(payload)


def verify_session(token: str, secret_key: str) -> tuple[UUID, str] | None:
    """Valida o token e retorna `(user_id, api_key)`, ou `None` se inválido."""
    try:
        value = _serializer(secret_key).loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(value, str):
        return None
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw_uid = data.get("u")
    raw_key = data.get("k")
    if not isinstance(raw_uid, str) or not isinstance(raw_key, str):
        return None
    try:
        user_id = UUID(raw_uid)
    except ValueError:
        return None
    if not raw_key:
        return None
    return user_id, raw_key


def set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    """Configura o cookie `cf_session` na resposta."""
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Apaga o cookie de sessão (usado no logout)."""
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def _redirect_to_login() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_302_FOUND,
        headers={"Location": "/login"},
    )


async def mint_session_api_key(db: AsyncSession, *, user: WebUser) -> str:
    """Cria uma API Key ephemera para a sessão do `user`. Retorna o plaintext.

    Nome marcado com `__web_session__` para que o admin não confunda com
    chaves de integração. A chave fica visível na lista da brand mas seu
    propósito é só dar voz à web UI quando ela proxia para a API REST.
    """
    from datetime import UTC, datetime, timedelta

    expires_at = datetime.now(tz=UTC) + timedelta(seconds=SESSION_MAX_AGE)
    _, plaintext = await auth_service.create_api_key(
        db,
        brand_id=user.brand_id,
        name=f"{_WEB_API_KEY_NAME}:{user.id}",
        expires_at=expires_at,
    )
    return plaintext


async def revoke_session_api_key(db: AsyncSession, *, api_key: str) -> None:
    """Apaga a `ApiKey` que corresponde ao token. Best-effort."""
    digest = auth_service.hash_key(api_key)
    await db.execute(delete(ApiKey).where(ApiKey.key_hash == digest))


async def require_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> WebUser:
    """Dependency FastAPI: devolve o `WebUser` autenticado pelo cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _redirect_to_login()
    secret = get_settings().secret_key.get_secret_value()
    decoded = verify_session(token, secret)
    if decoded is None:
        raise _redirect_to_login()
    user_id, _api_key = decoded

    user = await db.scalar(select(WebUser).where(WebUser.id == user_id))
    if user is None or not user.is_active:
        raise _redirect_to_login()
    return user


async def require_session_brand(
    user: WebUser = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> Brand:
    """Devolve a `Brand` do usuário autenticado."""
    brand = await db.scalar(select(Brand).where(Brand.id == user.brand_id))
    if brand is None:
        raise _redirect_to_login()
    return brand


def require_session_api_key(request: Request) -> str:
    """Devolve a API Key plaintext embutida no cookie.

    Não revalida o user — usar com `require_session` quando o handler
    precisar dos dois. Se o cookie está inválido, levanta 302/login.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _redirect_to_login()
    secret = get_settings().secret_key.get_secret_value()
    decoded = verify_session(token, secret)
    if decoded is None:
        raise _redirect_to_login()
    return decoded[1]


async def require_admin(user: WebUser = Depends(require_session)) -> WebUser:
    """Dependency para rotas administrativas — exige `role='admin'`."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/dashboard"},
        )
    return user
