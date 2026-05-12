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
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
    require_session,
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
    api_key: str = Depends(require_session),
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
    api_key: str = Depends(require_session),
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

    grouped = data.group_items_by_sku(list(detail.order.items))
    return templates.TemplateResponse(
        request,
        "orders/detail.html",
        {
            "detail": detail,
            "grouped": grouped,
            "romaneio_state": _classify_romaneio_state(detail),
            "auto_download": False,
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
    api_key: str = Depends(require_session),
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
    api_key: str = Depends(require_session),
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
