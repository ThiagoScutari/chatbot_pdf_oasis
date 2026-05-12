"""Autenticação por sessão para a UI web.

A gerente comercial se autentica uma vez na tela de login digitando sua
API Key (`cf_...`). O backend assina essa key num token HMAC via
`itsdangerous.URLSafeTimedSerializer` e envia no cookie `cf_session` com
TTL de 8 horas.

Em cada request de página protegida, o dependency `require_session`:
1. Lê o cookie `cf_session`.
2. Valida assinatura e idade do token.
3. Retorna a API Key recuperada — usada nas chamadas internas à API REST.
4. Em qualquer falha (cookie ausente, assinatura inválida, expirado) →
   levanta `HTTPException 302` com `Location: /login`. O browser segue o
   redirect; o body JSON da exceção é ignorado por ele.

Segurança:
- Cookie `httponly=True`, `samesite="lax"`, e `secure=True` em produção.
- Segredo HMAC vem de `settings.secret_key` — o mesmo usado para JWT.
- A API Key plaintext **só** vive dentro do cookie assinado; nunca é
  serializada em URLs, logs ou templates.
"""

from __future__ import annotations

from typing import Final

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.database import get_db
from catalogflow.infra.settings import get_settings
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand
from catalogflow.shared.errors import AuthenticationError

SESSION_COOKIE: Final[str] = "cf_session"
SESSION_MAX_AGE: Final[int] = 60 * 60 * 8  # 8 horas
_SALT: Final[str] = "catalogflow.web.session"


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=secret_key, salt=_SALT)


def create_session(api_key: str, secret_key: str) -> str:
    """Serializa a API Key num token assinado pelo `secret_key`.

    O TTL é aplicado na verificação (via `max_age` em `verify_session`),
    não no token em si — o timestamp embutido é o que permite a checagem.
    """
    return _serializer(secret_key).dumps(api_key)


def verify_session(token: str, secret_key: str) -> str | None:
    """Valida o token e retorna a API Key, ou `None` se inválido/expirado.

    Trata silenciosamente qualquer erro de assinatura ou expiração — para o
    chamador, só importa saber se há credencial confiável ou não.
    """
    try:
        value = _serializer(secret_key).loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(value, str) or not value:
        return None
    return value


def set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    """Configura o cookie `cf_session` na resposta.

    Usa `httponly` para impedir leitura por JavaScript e `samesite="lax"`
    para evitar envio em requests cross-site não-navegacionais. O flag
    `secure` deve ser `True` em produção (cookie só trafega via HTTPS).
    """
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


def require_session(request: Request) -> str:
    """Dependency FastAPI para rotas web protegidas.

    Lê o cookie `cf_session`, valida e retorna a API Key plaintext.
    Em qualquer falha, levanta `HTTPException(302)` para `/login` — o
    browser segue o redirect automaticamente.
    """
    token = request.cookies.get(SESSION_COOKIE)
    api_key: str | None = None
    if token:
        secret = get_settings().secret_key.get_secret_value()
        api_key = verify_session(token, secret)

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/login"},
        )
    return api_key


async def require_session_brand(
    api_key: str = Depends(require_session),
    db: AsyncSession = Depends(get_db),
) -> Brand:
    """Resolve a `Brand` autenticada a partir do cookie de sessão.

    Útil em rotas web protegidas que precisam consultar dados da marca
    (lista de catálogos, contagens do dashboard etc.). Se a assinatura
    do cookie é válida mas a chave foi revogada/deletada — caso raro mas
    possível — devolve o usuário para a tela de login em vez de jogar
    erro 401 JSON (que o browser não saberia tratar).
    """
    try:
        return await auth_service.verify_api_key(db, api_key)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/login"},
        ) from exc
