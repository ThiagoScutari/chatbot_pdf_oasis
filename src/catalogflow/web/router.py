"""Rotas web — páginas HTML servidas pelo próprio FastAPI.

Não vivem sob `/api/v1/` (não fazem parte do contrato público da API).
Sprint 03.5: o login passou a ser email+senha. O cookie `cf_session`
agora carrega `(user_id, ephemeral_api_key)` — o `user_id` identifica
o `WebUser`; a `api_key` é usada nas chamadas internas à API REST
(`/api/v1/...`) que o web layer proxia.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.database import get_db
from catalogflow.infra.settings import get_settings
from catalogflow.modules.auth.models import Brand, WebUser
from catalogflow.shared.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from catalogflow.web import _helpers, data
from catalogflow.web.auth import (
    SESSION_COOKIE,
    clear_session_cookie,
    create_session,
    mint_session_api_key,
    require_admin,
    require_session_api_key,
    require_session_brand,
    revoke_session_api_key,
    set_session_cookie,
    verify_session,
)
from catalogflow.web.product_image import fetch_product_image_url
from catalogflow.web.user_service import WebUserService

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
async def login_get(request: Request, notice: str | None = None) -> HTMLResponse:
    """Renderiza a tela de login.

    `notice` é um código simples para mostrar mensagens informativas
    (ex: `magic_sent`, `password_reset`) após redirects.
    """
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None, "notice": notice, "email": ""},
    )


# ──────────────────────────────────────────────
#  POST /login  →  valida email+senha e cria sessão
# ──────────────────────────────────────────────


def _render_login_error(
    request: Request,
    *,
    email: str,
    error: str,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error, "notice": None, "email": email},
        status_code=status_code,
    )


async def _start_session_for(
    request: Request,
    user: WebUser,
    db: AsyncSession,
    *,
    redirect_to: str = "/dashboard",
) -> Response:
    """Cria a sessão para `user`: mint da API Key + assinatura do cookie."""
    api_key = await mint_session_api_key(db, user=user)
    settings = get_settings()
    token = create_session(user.id, api_key, settings.secret_key.get_secret_value())
    response = RedirectResponse(url=redirect_to, status_code=status.HTTP_302_FOUND)
    set_session_cookie(response, token, secure=settings.is_production)
    return response


@router.post("/login")
async def login_post(
    request: Request,
    email: str = Form(..., min_length=1),
    password: str = Form(..., min_length=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Login por email+senha com rate-limit por email."""
    service = WebUserService(db)

    if not await service.check_rate_limit(email):
        return _render_login_error(
            request,
            email=email,
            error="Muitas tentativas. Tente novamente em alguns minutos.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    try:
        user = await service.authenticate(email, password)
    except AuthenticationError as exc:
        return _render_login_error(request, email=email, error=exc.message)

    return await _start_session_for(request, user, db)


# ──────────────────────────────────────────────
#  GET /forgot-password  →  formulário p/ pedir magic-link
# ──────────────────────────────────────────────


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "forgot.html",
        {"error": None, "sent": False, "email": ""},
    )


@router.post("/forgot-password")
async def forgot_password_post(
    request: Request,
    email: str = Form(..., min_length=1),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Envia magic-link para o email. Responde igual quer o email exista ou não."""
    service = WebUserService(db)
    await service.send_magic_link(email)
    # UX: sempre mostra "se existir, enviamos" — evita oracle de existência.
    return templates.TemplateResponse(
        request,
        "forgot.html",
        {"error": None, "sent": True, "email": email},
    )


# ──────────────────────────────────────────────
#  GET /magic-link/{token}  →  consome o link e loga
# ──────────────────────────────────────────────


@router.get("/magic-link/{token}")
async def magic_link_consume(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = WebUserService(db)
    try:
        user = await service.verify_magic_link(token)
    except (AuthenticationError, NotFoundError) as exc:
        return templates.TemplateResponse(
            request,
            "errors/magic_link.html",
            {"message": exc.message},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return await _start_session_for(request, user, db)


# ──────────────────────────────────────────────
#  GET/POST /register  →  pedido de acesso (admin aprova depois)
# ──────────────────────────────────────────────


@router.get("/register", response_class=HTMLResponse)
async def register_get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "register.html",
        {"error": None, "sent": False, "name": "", "email": ""},
    )


@router.post("/register")
async def register_post(
    request: Request,
    name: str = Form(..., min_length=2, max_length=255),
    email: str = Form(..., min_length=1),
    password: str = Form(..., min_length=8),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Cria `WebUser` `is_active=False` e dispara notificação ao admin."""
    # Resolve a brand padrão — primeira ordem de criação. Sprint inicial
    # só tem uma brand seedada. Em multi-brand real, este endpoint exigirá
    # invite token (TODO Sprint 04).
    from sqlalchemy import select as _select

    brand = await db.scalar(_select(Brand).order_by(Brand.created_at.asc()))
    if brand is None:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "error": "Sistema ainda não configurado. Avise o administrador.",
                "sent": False,
                "name": name,
                "email": email,
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    service = WebUserService(db)
    try:
        await service.request_access(
            brand_id=brand.id,
            name=name,
            email=email,
            password=password,
        )
    except (ValidationError, ConflictError) as exc:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "error": exc.message,
                "sent": False,
                "name": name,
                "email": email,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return templates.TemplateResponse(
        request,
        "register.html",
        {"error": None, "sent": True, "name": name, "email": email},
    )


# ──────────────────────────────────────────────
#  Admin: lista + aprovação
# ──────────────────────────────────────────────


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_list(
    request: Request,
    admin: WebUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    service = WebUserService(db)
    pending = await service.list_pending_users(admin.brand_id)
    active = await service.list_active_users(admin.brand_id)
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {"admin": admin, "pending": pending, "active": active},
    )


@router.post("/admin/users/{user_id}/approve")
async def admin_users_approve(
    request: Request,
    user_id: UUID,
    admin: WebUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = WebUserService(db)
    try:
        user = await service.approve_user(user_id)
    except NotFoundError:
        return _render_not_found(
            request,
            title="Usuário não encontrado",
            message="Este pedido pode ter sido removido.",
        )
    if user.brand_id != admin.brand_id:
        return _render_not_found(
            request,
            title="Sem permissão",
            message="Você não pode aprovar usuários de outra marca.",
        )
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


@router.post("/admin/users/{user_id}/deny")
async def admin_users_deny(
    request: Request,
    user_id: UUID,
    admin: WebUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = WebUserService(db)
    # Validar tenant antes de remover.
    target = await service.get_by_id(user_id)
    if target is None or target.brand_id != admin.brand_id:
        return _render_not_found(
            request,
            title="Usuário não encontrado",
            message="Este pedido pode ter sido removido.",
        )
    try:
        await service.deny_user(user_id)
    except (NotFoundError, ConflictError):
        pass
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


# ──────────────────────────────────────────────
#  GET /logout  →  limpa sessão + revoga API Key da sessão
# ──────────────────────────────────────────────


@router.get("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    """Encerra a sessão: revoga a ApiKey da sessão e apaga o cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        secret = get_settings().secret_key.get_secret_value()
        decoded = verify_session(token, secret)
        if decoded is not None:
            _user_id, api_key = decoded
            await revoke_session_api_key(db, api_key=api_key)
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


# ──────────────────────────────────────────────
#  GET /catalogs/upload  — formulário
# ──────────────────────────────────────────────


@router.get("/catalogs/upload", response_class=HTMLResponse)
async def catalog_upload_form(
    request: Request,
    brand: Brand = Depends(require_session_brand),
) -> HTMLResponse:
    """Renderiza o formulário de envio de catálogo.

    `brand` é injetado para que o gate de sessão dispare 302/login antes
    de renderizar o template — não é consumido pela página em si.
    """
    del brand  # parâmetro existe apenas para acionar o dependency.
    return templates.TemplateResponse(request, "catalogs/upload.html", {})


# ──────────────────────────────────────────────
#  POST /catalogs/upload  — proxy para /api/v1/catalogs/process
# ──────────────────────────────────────────────


_FRIENDLY_ERROR_MESSAGES: dict[str, str] = {
    "FILE_TOO_LARGE": "Arquivo maior que 50 MB.",
    "PDF_ENCRYPTED": "PDF protegido com senha.",
    "INVALID_FILE_TYPE": "O arquivo não é um PDF válido.",
    "PDF_NO_PRODUCTS": "Nenhum produto detectado no catálogo.",
    "PDF_CORRUPT": "Arquivo PDF inválido ou corrompido.",
}


def _friendly_for(code: str | None, fallback: str) -> str:
    if code and code in _FRIENDLY_ERROR_MESSAGES:
        return _FRIENDLY_ERROR_MESSAGES[code]
    return fallback or "Não foi possível processar o catálogo."


@router.post("/catalogs/upload")
async def catalog_upload_submit(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(..., min_length=1, max_length=255),
    collection: str | None = Form(default=None, max_length=128),
    api_key: str = Depends(require_session_api_key),
) -> JSONResponse:
    """Encaminha o upload para a API REST e devolve um JSON enxuto
    para o Alpine consumir (job_id + catalog_id, ou erro estruturado).

    Vai via httpx ASGI in-process — mesma rota que um cliente externo
    usaria, mas sem TCP roundtrip. A validação fica concentrada na API.
    """
    pdf_bytes = await file.read()
    files = {
        "file": (file.filename or "catalog.pdf", pdf_bytes, "application/pdf"),
    }
    form_data: dict[str, str] = {"name": name}
    if collection:
        form_data["collection"] = collection

    transport = httpx.ASGITransport(app=request.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://internal",
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        try:
            api_resp = await client.post(
                "/api/v1/catalogs/process",
                data=form_data,
                files=files,
                timeout=60.0,
            )
        except httpx.HTTPError as exc:
            logger.exception("upload: erro de transporte para a API")
            return JSONResponse(
                {"success": False, "error": {"code": "UPSTREAM_ERROR", "message": str(exc)}},
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

    if api_resp.status_code in (200, 201, 202):
        envelope = api_resp.json()
        # Envelope padrão {success, data:{catalog_id, job_id, ...}}.
        return JSONResponse(envelope.get("data", {}), status_code=200)

    # Erro: extrai code/message do envelope e devolve em formato amigável.
    try:
        body = api_resp.json()
    except ValueError:
        body = {"error": {"code": "UNKNOWN", "message": api_resp.text[:300]}}
    err = body.get("error", {}) if isinstance(body, dict) else {}
    friendly = _friendly_for(err.get("code"), err.get("message", ""))
    return JSONResponse(
        {
            "success": False,
            "error": {
                "code": err.get("code", "UNKNOWN"),
                "message": friendly,
            },
        },
        status_code=api_resp.status_code,
    )


# ──────────────────────────────────────────────
#  GET /catalogs/upload/poll/{job_id}  — fragmento HTMX
# ──────────────────────────────────────────────


@router.get(
    "/catalogs/upload/poll/{job_id}",
    response_class=HTMLResponse,
)
async def catalog_upload_poll(
    request: Request,
    job_id: UUID,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Estado atual do job de processamento — retornado como fragmento HTML."""
    job = await data.get_job_for_brand(db, job_id, brand.id)
    if job is None:
        return HTMLResponse(content="", status_code=status.HTTP_404_NOT_FOUND)

    friendly_error: str | None = None
    if job.status not in ("pending", "running", "success"):
        friendly_error = _friendly_for(
            _error_code_from_message(job.error),
            job.error or "Ocorreu um erro durante o processamento.",
        )

    return templates.TemplateResponse(
        request,
        "catalogs/_upload_progress.html",
        {
            "job": job,
            "friendly_error": friendly_error,
        },
    )


def _error_code_from_message(message: str | None) -> str | None:
    """Tenta extrair um code conhecido do `Job.error` (best-effort).

    O service salva apenas `str(exc)` em `Job.error` — não o `code`. Para
    o web layer dar uma mensagem amigável, fazemos um lookup textual leve;
    se nada bater, o fallback do `_friendly_for` cobre.
    """
    if not message:
        return None
    lower = message.lower()
    if "50mb" in lower or "file_too_large" in lower:
        return "FILE_TOO_LARGE"
    if "encrypt" in lower or "senha" in lower:
        return "PDF_ENCRYPTED"
    if "no_products" in lower or "nenhum produto" in lower:
        return "PDF_NO_PRODUCTS"
    if "invalid_file_type" in lower or "não é um pdf" in lower:
        return "INVALID_FILE_TYPE"
    return None


# ──────────────────────────────────────────────
#  GET /catalogs/{id}  — detalhe
# ──────────────────────────────────────────────


def _render_not_found(
    request: Request,
    *,
    title: str = "Catálogo não encontrado",
    message: str = "Este catálogo pode ter sido removido ou nunca existiu.",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "errors/404.html",
        {"title": title, "message": message},
        status_code=status.HTTP_404_NOT_FOUND,
    )


# ──────────────────────────────────────────────
#  Helpers públicos — usados pelos handlers globais em main.py
# ──────────────────────────────────────────────


def render_web_404(
    request: Request,
    *,
    title: str = "Não encontramos esta página",
    message: str = "A página pode ter sido removida ou o endereço está incorreto.",
) -> HTMLResponse:
    """Renderiza o template 404 elegante para rotas web desconhecidas."""
    return templates.TemplateResponse(
        request,
        "errors/404.html",
        {"title": title, "message": message},
        status_code=status.HTTP_404_NOT_FOUND,
    )


def render_web_500(request: Request) -> HTMLResponse:
    """Renderiza o template 500 elegante para erros internos em rotas web.

    Não recebe detalhes do erro — a página é deliberadamente estéril
    para nunca vazar traceback / dados sensíveis para o navegador.
    """
    return templates.TemplateResponse(
        request,
        "errors/500.html",
        {},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


@router.get("/catalogs/{catalog_id}", response_class=HTMLResponse)
async def catalog_detail(
    request: Request,
    catalog_id: UUID,
    page: int = 1,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Detalhe do catálogo + lista paginada de produtos."""
    catalog = await data.get_catalog(db, catalog_id, brand.id)
    if catalog is None:
        return _render_not_found(request)
    products = await data.list_catalog_products(db, catalog_id, page=page)
    return templates.TemplateResponse(
        request,
        "catalogs/detail.html",
        {
            "catalog": catalog,
            "products": products,
        },
    )


# ──────────────────────────────────────────────
#  GET /catalogs/{id}/_actions_strip  — fragmento polling
# ──────────────────────────────────────────────


@router.get(
    "/catalogs/{catalog_id}/_actions_strip",
    response_class=HTMLResponse,
)
async def catalog_actions_strip(
    request: Request,
    catalog_id: UUID,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Strip de ações da página de detalhe — re-renderizado a cada 3s
    enquanto status é pending/processing."""
    catalog = await data.get_catalog(db, catalog_id, brand.id)
    if catalog is None:
        return HTMLResponse(content="", status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "catalogs/_actions_strip.html",
        {"catalog": catalog},
    )


# ──────────────────────────────────────────────
#  GET /catalogs/{id}/download  — proxy para a API
# ──────────────────────────────────────────────


@router.get("/catalogs/{catalog_id}/download")
async def catalog_download(
    request: Request,
    catalog_id: UUID,
    api_key: str = Depends(require_session_api_key),
) -> Response:
    """Encaminha o download do PDF editável.

    Em dev a API serve os bytes diretos (`Content-Type: application/pdf`);
    em produção devolve 302 para uma URL assinada do storage. Aqui
    repassamos ambos os caminhos: bytes viram Response, 302 vira redirect.
    """
    transport = httpx.ASGITransport(app=request.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://internal",
        headers={"Authorization": f"Bearer {api_key}"},
        follow_redirects=False,
    ) as client:
        api_resp = await client.get(
            f"/api/v1/catalogs/{catalog_id}/download",
            timeout=60.0,
        )

    if api_resp.status_code == 302:
        location = api_resp.headers.get("location", "")
        return RedirectResponse(url=location, status_code=status.HTTP_302_FOUND)

    if api_resp.status_code == 200:
        return Response(
            content=api_resp.content,
            media_type=api_resp.headers.get("content-type", "application/pdf"),
            headers={
                "Content-Disposition": api_resp.headers.get(
                    "content-disposition",
                    f'attachment; filename="catalog-{catalog_id}.pdf"',
                ),
            },
        )

    # Não está pronto ou outro erro: 404 elegante.
    body: dict[str, Any] = {}
    try:
        body = api_resp.json()
    except ValueError:
        pass
    err = body.get("error", {}) if isinstance(body, dict) else {}
    return _render_not_found(
        request,
        title="Download indisponível",
        message=err.get("message")
        or "O catálogo ainda não está pronto para download.",
    )


# ═════════════════════════════════════════════════════════════
#  ORDERS — lista, detalhe e ação do romaneio
# ═════════════════════════════════════════════════════════════


@router.get("/orders", response_class=HTMLResponse)
async def orders_list(
    request: Request,
    page: int = 1,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Lista paginada de pedidos da brand."""
    order_page = await data.list_orders(db, brand.id, page=page)
    return templates.TemplateResponse(
        request,
        "orders/list.html",
        {"brand": brand, "page": order_page},
    )


@router.get("/orders/{order_id}/_badge", response_class=HTMLResponse)
async def order_badge(
    request: Request,
    order_id: UUID,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Fragmento de badge — polling em pedidos com status draft."""
    current_status = await data.get_order_status(db, order_id, brand.id)
    if current_status is None:
        return HTMLResponse(content="", status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "orders/_badge.html",
        {"order_id": order_id, "status": current_status},
    )


def _classify_romaneio_state(detail: data.OrderDetail) -> str:
    """Mapeia o estado do romaneio para o template `_romaneio_action`.

    Retorna:
      - "ready":      romaneio existe e tem output_key (PDF pronto).
      - "processing": romaneio existe sem output_key (job em andamento).
      - "absent":     nenhum romaneio ainda — usuário precisa disparar.
    """
    rom = detail.romaneio
    if rom is None:
        return "absent"
    if rom.output_key:
        return "ready"
    return "processing"


def _classify_stock_state(detail: data.OrderDetail) -> str:
    """`absent | checking | completed | error` para o fragmento _stock_action."""
    sc = detail.stock_check
    if sc is None:
        return "absent"
    if sc.status in ("pending", "checking"):
        return "checking"
    if sc.status == "completed":
        return "completed"
    return "error"


def _classify_submission_state(detail: data.OrderDetail) -> str:
    """Mapeia ErpSubmission.status para o estado do fragmento de envio.

    Estado `error` permite re-submeter; `accepted`/`partially_accepted`
    são terminais e não mostram o form de novo. `rejected` mostra o
    form de reenvio (operador pode corrigir o customer_code).
    """
    sub = detail.submission
    if sub is None:
        return "absent"
    if sub.status in ("pending", "submitting"):
        return "submitting"
    if sub.status in ("accepted", "partially_accepted", "rejected", "error"):
        return sub.status
    return "absent"


def _stock_summary_from(detail: data.OrderDetail) -> dict[str, int] | None:
    """Conta itens por status no `stock_check.result` (None se não houver)."""
    if detail.stock_check is None or detail.stock_check.status != "completed":
        return None
    items: list[dict[str, Any]] = (
        detail.stock_check.result.get("items", []) if detail.stock_check.result else []
    )
    summary = {
        "total_items": len(items),
        "available": 0,
        "partial": 0,
        "out_of_stock": 0,
        "unknown": 0,
    }
    for item in items:
        status = item.get("status")
        if status in summary:
            summary[status] += 1
    return summary


@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(
    request: Request,
    order_id: UUID,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Detalhe do pedido — items agrupados por SKU/cor + ação do romaneio."""
    detail = await data.get_order_detail(db, order_id, brand.id)
    if detail is None:
        return _render_not_found(
            request,
            title="Pedido não encontrado",
            message="Este pedido pode ter sido removido ou nunca existiu.",
        )

    items_list = list(detail.order.items)
    grouped = data.group_items_by_sku(items_list)
    stock_map = data.build_stock_map(detail.stock_check)
    pendency_count = data.count_pendency_items(stock_map, items_list)
    return templates.TemplateResponse(
        request,
        "orders/detail.html",
        {
            "detail": detail,
            "grouped": grouped,
            "romaneio_state": _classify_romaneio_state(detail),
            "auto_download": False,
            "stock_state": _classify_stock_state(detail),
            "stock_summary": _stock_summary_from(detail),
            "stock_checked_at": (
                detail.stock_check.checked_at if detail.stock_check else None
            ),
            "stock_error": (
                detail.stock_check.error_message if detail.stock_check else None
            ),
            "submission_state": _classify_submission_state(detail),
            "submission": detail.submission,
            "stock_map": stock_map,
            "pendency_count": pendency_count,
        },
    )


# ──────────────────────────────────────────────
#  Romaneio actions (HTMX)
# ──────────────────────────────────────────────


async def _hit_romaneio_endpoint(
    request: Request,
    order_id: UUID,
    api_key: str,
) -> httpx.Response:
    """Chama GET /api/v1/orders/{id}/romaneio (que dispara/retorna conforme estado)."""
    transport = httpx.ASGITransport(app=request.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://internal",
        headers={"Authorization": f"Bearer {api_key}"},
        follow_redirects=False,
    ) as client:
        return await client.get(
            f"/api/v1/orders/{order_id}/romaneio",
            timeout=60.0,
        )


async def _render_romaneio_fragment(
    request: Request,
    order_id: UUID,
    brand: Brand,
    db: AsyncSession,
    *,
    auto_download: bool = False,
) -> HTMLResponse:
    """Renderiza `_romaneio_action.html` com o estado atual do romaneio."""
    detail = await data.get_order_detail(db, order_id, brand.id)
    if detail is None:
        return HTMLResponse(content="", status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "orders/_romaneio_action.html",
        {
            "order": detail.order,
            "romaneio_state": _classify_romaneio_state(detail),
            "auto_download": auto_download,
        },
    )


@router.post("/orders/{order_id}/romaneio", response_class=HTMLResponse)
async def order_romaneio_kick(
    request: Request,
    order_id: UUID,
    api_key: str = Depends(require_session_api_key),
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Dispara geração do romaneio via API e devolve o fragmento de polling.

    A API expõe apenas `GET /orders/{id}/romaneio` — o GET enfileira o
    job quando necessário. Aqui usamos um POST para que o botão do
    front fique semanticamente correto, e por baixo chamamos o GET.
    """
    await _hit_romaneio_endpoint(request, order_id, api_key)
    return await _render_romaneio_fragment(request, order_id, brand, db)


@router.post("/orders/{order_id}/regenerate-romaneio", response_class=HTMLResponse)
async def order_romaneio_regenerate(
    request: Request,
    order_id: UUID,
    api_key: str = Depends(require_session_api_key),
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Deleta o romaneio existente e dispara uma nova geração.

    Fluxo:
      1. Carrega detalhe do pedido (incluindo o Romaneio se houver).
      2. Se há romaneio: deleta o objeto do storage (`output_key`) e
         apaga o registro do banco — clean slate para a UI ver "processing"
         imediatamente em vez de continuar mostrando o PDF anterior.
      3. Chama o mesmo fluxo do botão "Gerar romaneio" via API REST
         (`GET /api/v1/orders/{id}/romaneio`), que cria o `Romaneio`
         novo + Job e enfileira a task.
      4. Devolve o fragmento HTMX no estado atual (deve ser "processing").

    Útil para reprocessar quando entram fotos/dados novos sem precisar
    de intervenção manual no banco.
    """
    # 1. Carrega para descobrir o romaneio atual (se houver).
    detail = await data.get_order_detail(db, order_id, brand.id)
    if detail is None:
        return HTMLResponse(content="", status_code=status.HTTP_404_NOT_FOUND)

    # 2. Apaga o romaneio existente — best-effort no storage, hard delete
    # no banco. A deleção do storage é idempotente; logamos warning em
    # caso de falha mas seguimos o fluxo (o Romaneio.output_key
    # eventualmente será sobrescrito pelo novo PDF mesmo assim).
    if detail.romaneio is not None:
        from catalogflow.infra.storage import get_storage_client

        if detail.romaneio.output_key:
            storage = get_storage_client()
            try:
                await storage.delete(detail.romaneio.output_key)
            except Exception:
                logger.warning(
                    "regenerate: falha ao deletar %s do storage — segue",
                    detail.romaneio.output_key,
                    exc_info=True,
                )
        await db.delete(detail.romaneio)
        await db.flush()

    # 3. Mesma chamada que o botão "Gerar romaneio" — proxia para a API
    # que cria Romaneio + Job e enfileira a task assincronamente.
    await _hit_romaneio_endpoint(request, order_id, api_key)

    # 4. Devolve fragmento (deve estar em "processing" agora — Romaneio
    # foi recém-criado sem output_key).
    return await _render_romaneio_fragment(request, order_id, brand, db)


@router.get("/orders/{order_id}/romaneio/poll", response_class=HTMLResponse)
async def order_romaneio_poll(
    request: Request,
    order_id: UUID,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Fragmento de polling do romaneio (chamado a cada 2s pelo HTMX).

    Se o estado virou `ready`, inclui um script que dispara o download
    automaticamente na primeira ocorrência (`auto_download=True`).
    """
    detail = await data.get_order_detail(db, order_id, brand.id)
    if detail is None:
        return HTMLResponse(content="", status_code=status.HTTP_404_NOT_FOUND)
    state = _classify_romaneio_state(detail)
    return templates.TemplateResponse(
        request,
        "orders/_romaneio_action.html",
        {
            "order": detail.order,
            "romaneio_state": state,
            "auto_download": state == "ready",
        },
    )


@router.get("/orders/{order_id}/romaneio/download")
async def order_romaneio_download(
    request: Request,
    order_id: UUID,
    api_key: str = Depends(require_session_api_key),
) -> Response:
    """Proxy do download do romaneio.

    Em dev a API devolve os bytes do PDF; em produção devolve 302 para
    presigned URL. Em ambos os casos repassamos para o browser.
    """
    api_resp = await _hit_romaneio_endpoint(request, order_id, api_key)

    if api_resp.status_code == 302:
        location = api_resp.headers.get("location", "")
        return RedirectResponse(url=location, status_code=status.HTTP_302_FOUND)

    if api_resp.status_code == 200:
        return Response(
            content=api_resp.content,
            media_type=api_resp.headers.get("content-type", "application/pdf"),
            headers={
                "Content-Disposition": api_resp.headers.get(
                    "content-disposition",
                    f'attachment; filename="romaneio-{order_id}.pdf"',
                ),
            },
        )

    # 202 (enfileirado) ou outro — não é um download válido neste ponto.
    return _render_not_found(
        request,
        title="Romaneio indisponível",
        message="O romaneio ainda não está pronto para download.",
    )


# ──────────────────────────────────────────────
#  Stock check + ERP submission (HTMX, Sprint 04)
# ──────────────────────────────────────────────


async def _hit_stock_check_post(
    request: Request,
    order_id: UUID,
    api_key: str,
) -> httpx.Response:
    """Chama POST /api/v1/orders/{id}/stock-check (enfileira consulta)."""
    transport = httpx.ASGITransport(app=request.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://internal",
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        return await client.post(
            f"/api/v1/orders/{order_id}/stock-check",
            timeout=30.0,
        )


async def _hit_submit_post(
    request: Request,
    order_id: UUID,
    api_key: str,
    customer_code: str,
) -> httpx.Response:
    """Chama POST /api/v1/orders/{id}/submit (enfileira envio ao ERP)."""
    transport = httpx.ASGITransport(app=request.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://internal",
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        return await client.post(
            f"/api/v1/orders/{order_id}/submit",
            json={"customer_code": customer_code},
            timeout=30.0,
        )


async def _render_stock_fragment(
    request: Request,
    order_id: UUID,
    brand: Brand,
    db: AsyncSession,
) -> HTMLResponse:
    """Renderiza `_stock_action.html` com o estado atual do StockCheck."""
    detail = await data.get_order_detail(db, order_id, brand.id)
    if detail is None:
        return HTMLResponse(content="", status_code=status.HTTP_404_NOT_FOUND)

    state = _classify_stock_state(detail)
    response = templates.TemplateResponse(
        request,
        "orders/_stock_action.html",
        {
            "order": detail.order,
            "stock_state": state,
            "stock_summary": _stock_summary_from(detail),
            "stock_checked_at": detail.stock_check.checked_at if detail.stock_check else None,
            "stock_error": detail.stock_check.error_message if detail.stock_check else None,
        },
    )
    # Quando a consulta terminou, sinaliza o HTMX para recarregar a página
    # de items (badges precisam refletir os novos stock_status). Header
    # HX-Trigger dispara um evento custom no client.
    if state == "completed":
        response.headers["HX-Trigger"] = "stock-check-completed"
    return response


async def _render_submit_fragment(
    request: Request,
    order_id: UUID,
    brand: Brand,
    db: AsyncSession,
) -> HTMLResponse:
    """Renderiza `_submit_action.html` com o estado atual da submission."""
    detail = await data.get_order_detail(db, order_id, brand.id)
    if detail is None:
        return HTMLResponse(content="", status_code=status.HTTP_404_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "orders/_submit_action.html",
        {
            "order": detail.order,
            "submission_state": _classify_submission_state(detail),
            "submission": detail.submission,
        },
    )


@router.post("/orders/{order_id}/stock-check-web", response_class=HTMLResponse)
async def order_stock_check_kick(
    request: Request,
    order_id: UUID,
    api_key: str = Depends(require_session_api_key),
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Dispara consulta de estoque via API e devolve o fragmento de polling."""
    api_resp = await _hit_stock_check_post(request, order_id, api_key)
    if api_resp.status_code not in (200, 202):
        logger.warning(
            "stock-check kick: API respondeu %s para order=%s",
            api_resp.status_code,
            order_id,
        )
    return await _render_stock_fragment(request, order_id, brand, db)


@router.get("/orders/{order_id}/stock-check-poll", response_class=HTMLResponse)
async def order_stock_check_poll(
    request: Request,
    order_id: UUID,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Fragmento de polling — HTMX chama a cada 2s enquanto `checking`."""
    return await _render_stock_fragment(request, order_id, brand, db)


@router.post("/orders/{order_id}/submit-web", response_class=HTMLResponse)
async def order_submit_kick(
    request: Request,
    order_id: UUID,
    customer_code: str = Form(..., min_length=1, max_length=64),
    api_key: str = Depends(require_session_api_key),
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Dispara envio ao ERP via API e devolve o fragmento de polling."""
    api_resp = await _hit_submit_post(request, order_id, api_key, customer_code)
    if api_resp.status_code not in (200, 202):
        logger.warning(
            "submit kick: API respondeu %s para order=%s",
            api_resp.status_code,
            order_id,
        )
    return await _render_submit_fragment(request, order_id, brand, db)


@router.get("/orders/{order_id}/submit-poll", response_class=HTMLResponse)
async def order_submit_poll(
    request: Request,
    order_id: UUID,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Fragmento de polling do envio — HTMX chama a cada 2s enquanto submitting."""
    return await _render_submit_fragment(request, order_id, brand, db)


# ──────────────────────────────────────────────
#  GET /orders/{order_id}/pendency-report  — PDF on-the-fly
# ──────────────────────────────────────────────


@router.get("/orders/{order_id}/pendency-report")
async def order_pendency_report(
    request: Request,
    order_id: UUID,
    brand: Brand = Depends(require_session_brand),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Gera relatório de pendências em PDF on-the-fly.

    Filtra itens com stock_status em {partial, out_of_stock} e gera um
    PDF usando o RomaneioBuilder com a sub-linha "Disponível" populada
    a partir do último StockCheck. Não persiste no storage — é um
    documento descartável para a lojista.

    Cross-tenant: 404 (via get_order_detail). Pedido sem pendências: 404
    com mensagem amigável (botão não deveria nem estar visível, mas
    protege contra link copiado/marcado).
    """
    from decimal import Decimal

    from catalogflow.modules.auth.models import Brand as _BrandModel
    from catalogflow.modules.orders.normalizer import (
        NormalizedOrderData,
        NormalizedOrderItem,
        NormalizedTotals,
    )
    from catalogflow.modules.romaneio.builder import (
        RomaneioBuilder,
        RomaneioConfig,
    )
    from catalogflow.shared.image_fetcher import fetch_product_images

    detail = await data.get_order_detail(db, order_id, brand.id)
    if detail is None:
        return _render_not_found(
            request,
            title="Pedido não encontrado",
            message="Este pedido pode ter sido removido ou nunca existiu.",
        )

    items_list = list(detail.order.items)
    stock_map = data.build_stock_map(detail.stock_check)
    pendency_items = [
        item for item in items_list if item.stock_status in ("partial", "out_of_stock")
    ]
    if not pendency_items:
        return _render_not_found(
            request,
            title="Sem pendências",
            message="Este pedido não tem itens com pendência de estoque.",
        )

    # Reusa o brand record para popular o cabeçalho do relatório com a
    # mesma identidade visual do romaneio.
    brand_record = await db.get(_BrandModel, brand.id)
    brand_name = brand_record.name if brand_record else brand.name

    # Carrega a logo (best-effort — se falhar, o builder segue sem logo).
    logo_bytes: bytes | None = None
    if brand_record and brand_record.logo_key:
        try:
            from catalogflow.infra.storage import get_storage_client

            storage = get_storage_client()
            logo_bytes = await storage.download(brand_record.logo_key)
        except Exception:
            logger.warning("pendency-report: falha ao baixar logo")
            logo_bytes = None

    # Constrói NormalizedOrderData apenas com os itens em pendência.
    normalized_items = [
        NormalizedOrderItem(
            sku=it.sku,
            product_name=it.product_name,
            color_index=it.color_index,
            color_hex=it.color_hex,
            size=it.size,
            quantity=it.quantity,
            unit_price=it.unit_price,
        )
        for it in pendency_items
    ]
    totals = NormalizedTotals(
        total_items=len(normalized_items),
        total_pecas=sum(i.quantity for i in normalized_items),
        valor_total=Decimal("0"),  # relatório de pendências não mostra preços
        n_skus=len({i.sku for i in normalized_items}),
    )
    order_data = NormalizedOrderData(
        items=normalized_items,
        totals=totals,
        source_format="v2",
        warnings=[],
    )

    config = RomaneioConfig(
        brand_name=brand_name,
        logo_bytes=logo_bytes,
        lojista_name=detail.order.lojista_name or "—",
        emitted_at=None,  # now
        title="RELATÓRIO DE PENDÊNCIAS",
        show_prices=False,
        footer_note="Itens acima não puderam ser atendidos integralmente.",
    )

    # Fotos dos SKUs pendentes — best-effort, sem caching (relatório
    # é descartável). Falha de qualquer SKU não derruba o PDF.
    pending_skus = sorted({it.sku for it in pendency_items})
    try:
        product_images = await fetch_product_images(pending_skus)
    except Exception:
        logger.warning(
            "pendency-report: falha ao buscar fotos — PDF sairá sem elas",
            exc_info=True,
        )
        product_images = {}

    pdf_bytes = RomaneioBuilder().build(
        order_data,
        config,
        available_map=stock_map,
        product_images=product_images or None,
    )

    filename = f"pendencias-{order_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


# ═════════════════════════════════════════════════════════════
#  GET /product-image/{sku}  — proxy de thumbnail + placeholder SVG
# ═════════════════════════════════════════════════════════════


_PLACEHOLDER_BG = "#E8E0D5"
_PLACEHOLDER_FG = "#7A6E65"


def _initials_for(name: str, sku: str) -> str:
    """Extrai até 2 letras iniciais do nome do produto.

    Fallback: primeiros 2 caracteres alfanuméricos do SKU. Usado dentro
    do SVG placeholder, então deve sair MAIÚSCULO e tirar acentos com
    `ascii` no mínimo possível — Jinja já passa unicode.
    """
    words = [w for w in (name or "").split() if w]
    if words:
        if len(words) == 1:
            return words[0][:2].upper()
        return (words[0][0] + words[1][0]).upper()
    fallback = "".join(c for c in (sku or "") if c.isalnum())
    return fallback[:2].upper() or "?"


def _placeholder_svg_response(sku: str, name: str) -> Response:
    """SVG inline 100x100 com iniciais — escalável via CSS no template.

    Servido com `Cache-Control: public, max-age=3600` para não bater
    nesta rota a cada scroll. Fonts dentro de SVG não herdam do parent;
    usamos Georgia como fallback universalmente disponível.
    """
    initials = _initials_for(name, sku)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'viewBox="0 0 100 100" preserveAspectRatio="xMidYMid slice">'
        f'<rect width="100" height="100" fill="{_PLACEHOLDER_BG}"/>'
        f'<text x="50" y="50" text-anchor="middle" '
        f'dominant-baseline="central" '
        f'font-family="Cormorant Garamond, Georgia, serif" '
        f'font-size="36" font-weight="500" fill="{_PLACEHOLDER_FG}">'
        f"{initials}</text>"
        "</svg>"
    )
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/product-image/{sku}")
async def product_image(
    sku: str,
    name: str = "",
    api_key: str = Depends(require_session_api_key),
) -> Response:
    """Devolve thumbnail do produto: foto do AMC ou SVG placeholder.

    O endpoint é protegido por sessão pra que SKUs aleatórios de
    terceiros não usem o servidor como proxy genérico de scraping.
    O parâmetro `name` é opcional — quando presente, dá iniciais
    bonitas para o placeholder; sem ele, caímos no SKU.
    """
    del api_key  # gate de sessão; não usado pelo handler

    image_url = await fetch_product_image_url(sku)
    if image_url:
        upstream: httpx.Response | None
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                upstream = await client.get(image_url)
        except httpx.HTTPError:
            upstream = None

        if upstream is not None and upstream.status_code == 200:
            return Response(
                content=upstream.content,
                media_type=upstream.headers.get("content-type", "image/jpeg"),
                headers={"Cache-Control": "public, max-age=86400"},
            )

    return _placeholder_svg_response(sku, name)
