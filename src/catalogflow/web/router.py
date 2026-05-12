"""Rotas web — páginas HTML servidas pelo próprio FastAPI.

Não vivem sob `/api/v1/` (não fazem parte do contrato público da API).
Toda rota protegida usa o dependency `require_session`, que lê o cookie
`cf_session` e devolve a API Key plaintext da gerente para chamadas
internas à API REST.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.database import get_db
from catalogflow.infra.settings import get_settings
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand
from catalogflow.shared.errors import AuthenticationError
from catalogflow.web import _helpers, data
from catalogflow.web.auth import (
    SESSION_COOKIE,
    clear_session_cookie,
    create_session,
    require_session_brand,
    set_session_cookie,
    verify_session,
)

logger = logging.getLogger(__name__)

# Templates ficam em `src/catalogflow/templates/` (irmão de `web/`).
_TEMPLATES_DIR: Path = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Helpers expostos como globals — evita repetir `_helpers.format_*` nos templates.
templates.env.globals["format_date_long_pt"] = _helpers.format_date_long_pt
templates.env.globals["format_date_short_pt"] = _helpers.format_date_short_pt
templates.env.globals["humanize_when"] = _helpers.humanize_when
templates.env.globals["catalog_status_badge"] = _helpers.catalog_status_badge
templates.env.globals["order_status_badge"] = _helpers.order_status_badge

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


# ──────────────────────────────────────────────
#  GET /dashboard
# ──────────────────────────────────────────────


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Visão geral da brand — 4 contagens + atividade recente."""
    counts = await data.get_dashboard_counts(db, brand.id)
    activity = await data.get_recent_activity(db, brand.id, limit=5)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "brand": brand,
            "counts": counts,
            "activity": activity,
            "today": datetime.now(),
        },
    )


# ──────────────────────────────────────────────
#  GET /catalogs (lista)
# ──────────────────────────────────────────────


@router.get("/catalogs", response_class=HTMLResponse)
async def catalogs_list(
    request: Request,
    page: int = 1,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Lista paginada de catálogos da brand."""
    catalog_page = await data.list_catalogs(db, brand.id, page=page)
    return templates.TemplateResponse(
        request,
        "catalogs/list.html",
        {
            "brand": brand,
            "page": catalog_page,
        },
    )


# ──────────────────────────────────────────────
#  GET /catalogs/{id}/_badge  — fragmento p/ polling HTMX
# ──────────────────────────────────────────────


@router.get("/catalogs/{catalog_id}/_badge", response_class=HTMLResponse)
async def catalog_badge(
    request: Request,
    catalog_id: UUID,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Devolve apenas o `<span>` do badge — usado por `hx-get` a cada 3s
    nas linhas em processamento. Não há header/footer/main; é fragmento puro."""
    current_status = await data.get_catalog_status(db, catalog_id, brand.id)
    if current_status is None:
        # 404 silencioso → HTMX simplesmente para o polling.
        return HTMLResponse(content="", status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "catalogs/_badge.html",
        {
            "catalog_id": catalog_id,
            "status": current_status,
        },
    )
