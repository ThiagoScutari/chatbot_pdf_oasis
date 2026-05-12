"""FastAPI application factory.

Tudo que monta o app (routers, middlewares, handlers, lifespan) é
encapsulado em `create_app()`. O atributo module-level `app` é o que
`uvicorn catalogflow.main:app` consome em produção; em testes, chame
`create_app()` para obter uma instância isolada.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from catalogflow.infra import cache, database
from catalogflow.infra.settings import Settings, get_settings
from catalogflow.modules.auth.router import router as auth_router
from catalogflow.modules.catalog.router import router as catalog_router
from catalogflow.modules.orders.router import router as orders_router
from catalogflow.shared.errors import DomainError
from catalogflow.shared.jobs_router import router as jobs_router
from catalogflow.shared.middleware import RequestIdMiddleware, get_request_id
from catalogflow.shared.responses import error_response, ok

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Lifespan
# ──────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks.

    Startup: faz `SELECT 1` no Postgres e `PING` no Redis. Falha rápida se
    qualquer dependência crítica estiver inacessível.

    Shutdown: dispõe o engine e fecha o pool Redis.
    """
    logger.info("startup: verifying infrastructure dependencies")
    try:
        await database.check_connection()
        logger.info("startup: postgres ok")
    except Exception:
        logger.exception("startup: postgres unreachable")
        raise

    try:
        await cache.check_connection()
        logger.info("startup: redis ok")
    except Exception:
        logger.exception("startup: redis unreachable")
        raise

    try:
        yield
    finally:
        logger.info("shutdown: disposing infrastructure")
        await database.dispose_engine()
        await cache.close_redis()


# ──────────────────────────────────────────────
#  Exception handlers
# ──────────────────────────────────────────────


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    request_id = get_request_id(request)
    envelope = error_response(
        code=exc.code,
        message=exc.message,
        details=exc.details,
        request_id=request_id,
    )
    return JSONResponse(
        status_code=exc.http_status,
        content=envelope.model_dump(mode="json"),
    )


def _safe_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Converte os errors do Pydantic para JSON-safe.

    `ctx.error` carrega o `ValueError` original — não é serializável.
    Removemos `ctx` e mantemos apenas `loc/msg/type/input` quando presentes.
    """
    safe: list[dict[str, Any]] = []
    for err in exc.errors():
        item = {k: v for k, v in err.items() if k != "ctx"}
        # `input` pode conter UploadFile / bytes — coage para repr seguro.
        if "input" in item and not isinstance(item["input"], (str, int, float, bool, type(None))):
            item["input"] = repr(item["input"])[:200]
        safe.append(item)
    return safe


async def _validation_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    request_id = get_request_id(request)
    envelope = error_response(
        code="VALIDATION_ERROR",
        message="payload inválido",
        details={"errors": _safe_validation_errors(exc)},
        request_id=request_id,
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=envelope.model_dump(mode="json"),
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all. Não vaza detalhes internos para o cliente.

    O traceback fica nos logs com o `request_id` para correlação.
    """
    request_id = get_request_id(request)
    logger.exception(
        "unhandled exception",
        extra={"request_id": request_id, "path": request.url.path},
    )
    envelope = error_response(
        code="INTERNAL_ERROR",
        message="erro interno do servidor",
        details={},
        request_id=request_id,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=envelope.model_dump(mode="json"),
    )


# ──────────────────────────────────────────────
#  Health
# ──────────────────────────────────────────────


async def _count_pending_jobs() -> dict[str, int]:
    """Contagem de jobs em `pending` por tipo — usada pelo healthcheck.

    Não é parte do health "estrito" (não derruba o status), apenas
    observabilidade. Falha silenciosamente devolvendo zeros se o DB
    estiver indisponível — o status do DB já é refletido em outro campo.
    """
    from sqlalchemy import func, select

    from catalogflow.modules.catalog.models import Job

    counts = {"catalog_pending": 0, "order_pending": 0, "romaneio_pending": 0}
    type_to_key = {
        "catalog.process": "catalog_pending",
        "order.extract": "order_pending",
        "romaneio.generate": "romaneio_pending",
    }
    try:
        factory = database.get_session_factory()
        async with factory() as session:
            stmt = (
                select(Job.job_type, func.count(Job.id))
                .where(Job.status == "pending")
                .group_by(Job.job_type)
            )
            result = await session.execute(stmt)
            for job_type, n in result.all():
                key = type_to_key.get(job_type)
                if key:
                    counts[key] = int(n)
    except Exception:
        # observabilidade não pode derrubar /health — log e segue
        logger.exception("health: pending-jobs count failed")
    return counts


async def _health(request: Request) -> JSONResponse:
    """Healthcheck sondando dependências.

    Retorna 200 com `success=true` quando tudo OK; 503 quando alguma
    dependência respondeu erro. O payload também traz contagens de jobs
    pendentes por tipo — útil para dashboards e alertas.
    """
    payload: dict[str, object] = {"status": "ok"}
    http_status = status.HTTP_200_OK

    try:
        await database.check_connection()
        payload["db"] = "ok"
    except Exception:
        logger.exception("health: postgres check failed")
        payload["db"] = "error"
        payload["status"] = "degraded"
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE

    try:
        await cache.check_connection()
        payload["redis"] = "ok"
    except Exception:
        logger.exception("health: redis check failed")
        payload["redis"] = "error"
        payload["status"] = "degraded"
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE

    # Pending-jobs por tipo (sempre presente; zeros quando DB falhou).
    payload["jobs"] = await _count_pending_jobs()

    envelope = ok(payload, request_id=get_request_id(request))
    return JSONResponse(status_code=http_status, content=envelope.model_dump(mode="json"))


# ──────────────────────────────────────────────
#  App factory
# ──────────────────────────────────────────────


def _configure_logging(settings: Settings) -> None:
    """Logging básico — substituído por structlog em fase posterior."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def create_app(*, settings: Settings | None = None) -> FastAPI:
    """Factory do FastAPI app.

    Em testes, passe um `Settings` customizado ou monkeypatch
    `get_settings`. Em produção, deixe o default — lê do ambiente.
    """
    cfg = settings or get_settings()
    _configure_logging(cfg)

    app = FastAPI(
        title="CatalogFlow API",
        version="0.1.0",
        description=(
            "Transforma catálogos PDF visuais em instrumentos de captura de "
            "pedido e extrai pedidos preenchidos em romaneios estruturados."
        ),
        lifespan=lifespan,
        docs_url="/api/v1/docs" if not cfg.is_production else None,
        redoc_url="/api/v1/redoc" if not cfg.is_production else None,
        openapi_url="/api/v1/openapi.json",
    )

    # ── Middlewares (ordem importa: o último adicionado é o mais externo)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # ── Exception handlers
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)

    # ── Health (sem prefixo de router p/ deixar acessível mesmo sem auth)
    app.add_api_route(
        "/api/v1/health",
        _health,
        methods=["GET"],
        tags=["meta"],
        summary="Verifica disponibilidade da API e dependências.",
    )

    # ── Routers de domínio
    app.include_router(auth_router)
    app.include_router(catalog_router)
    app.include_router(orders_router)
    app.include_router(jobs_router)

    logger.info(
        "app: ready (env=%s, cors=%s)",
        cfg.environment,
        cfg.cors_origins,
    )
    return app


# ──────────────────────────────────────────────
#  Lazy `app` (PEP 562)
# ──────────────────────────────────────────────
# `uvicorn catalogflow.main:app` continua funcionando — o atributo é
# materializado na primeira leitura. Testes que importam `catalogflow.main`
# (sem referenciar `app`) não disparam a fábrica nem leem o .env real.

_app_instance: FastAPI | None = None


def __getattr__(name: str) -> Any:
    global _app_instance
    if name == "app":
        if _app_instance is None:
            _app_instance = create_app()
        return _app_instance
    raise AttributeError(f"module 'catalogflow.main' has no attribute {name!r}")
