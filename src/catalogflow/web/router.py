"""Rotas web — páginas HTML servidas pelo próprio FastAPI.

Não vivem sob `/api/v1/` (não fazem parte do contrato público da API).
Toda rota protegida usa o dependency `require_session`, que lê o cookie
`cf_session` e devolve a API Key plaintext da gerente para chamadas
internas à API REST.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.database import get_db
from catalogflow.infra.settings import get_settings
from catalogflow.modules.auth import service as auth_service
from catalogflow.shared.errors import AuthenticationError
from catalogflow.web.auth import (
    SESSION_COOKIE,
    clear_session_cookie,
    create_session,
    set_session_cookie,
    verify_session,
)

logger = logging.getLogger(__name__)

# Templates ficam em `src/catalogflow/templates/` (irmão de `web/`).
_TEMPLATES_DIR: Path = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(include_in_schema=False)


# ──────────────────────────────────────────────
#  GET /  →  /dashboard (com sessão) ou /login
# ──────────────────────────────────────────────


@router.get("/", response_class=RedirectResponse)
async def root(request: Request) -> RedirectResponse:
    """Roteamento raiz: redireciona conforme estado da sessão."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        secret = get_settings().secret_key.get_secret_value()
        if verify_session(token, secret) is not None:
            return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


# ──────────────────────────────────────────────
#  GET /login  →  formulário
# ──────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request) -> HTMLResponse:
    """Renderiza a tela de login."""
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None},
    )


# ──────────────────────────────────────────────
#  POST /login  →  valida e cria sessão
# ──────────────────────────────────────────────


@router.post("/login")
async def login_post(
    request: Request,
    api_key: str = Form(..., min_length=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Valida a API Key contra o auth service e cria a sessão.

    Decisão de validação (Sprint 03 Fase B): a verificação acontece direto
    no `auth_service.verify_api_key` — não roteamos por `GET /api/v1/health`
    porque esse endpoint é público (não exige Bearer) e portanto não pode
    diferenciar uma chave válida de uma inválida. Manter a verificação no
    serviço evita uma indireção HTTP desnecessária para validar um segredo
    que já temos em mãos.
    """
    try:
        await auth_service.verify_api_key(db, api_key)
    except AuthenticationError:
        # Mensagem genérica — não vaza se a chave nunca existiu vs expirou.
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Chave de acesso inválida"},
            status_code=status.HTTP_200_OK,
        )

    settings = get_settings()
    token = create_session(api_key, settings.secret_key.get_secret_value())

    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    set_session_cookie(response, token, secure=settings.is_production)
    return response


# ──────────────────────────────────────────────
#  GET /logout  →  limpa sessão
# ──────────────────────────────────────────────


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Encerra a sessão: apaga o cookie e volta para o login."""
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    clear_session_cookie(response)
    return response
