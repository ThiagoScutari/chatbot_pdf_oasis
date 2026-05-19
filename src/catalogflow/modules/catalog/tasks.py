"""Celery tasks do módulo `catalog`.

Tasks Celery são SÍNCRONAS por design do framework. Como o pipeline real
é async (SQLAlchemy asyncpg + aioboto3), abrimos um event loop dedicado
por execução e rodamos `CatalogService.process_catalog` lá dentro.

Convenções:
- Tasks recebem `catalog_id: str, job_id: str` (UUID em string) — nunca
  objetos ORM. JSON-serialização only.
- Erros permanentes (PDFCorruptError/PDFEncryptedError/PDFNoProductsError/
  PDFTooLargeError) não disparam retry — o estado já foi gravado como
  `error` no banco.
- Demais erros disparam retry com backoff exponencial (max 3 tentativas).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from celery import Task

from catalogflow.infra.celery_app import celery_app
from catalogflow.infra.database import dispose_engine, get_session_factory
from catalogflow.modules.catalog.service import CatalogService
from catalogflow.shared.errors import (
    PDFCorruptError,
    PDFEncryptedError,
    PDFNoProductsError,
    PDFTooLargeError,
)

logger = logging.getLogger(__name__)

_PERMANENT_ERRORS: tuple[type[Exception], ...] = (
    PDFCorruptError,
    PDFEncryptedError,
    PDFNoProductsError,
    PDFTooLargeError,
)


async def _run_process_catalog(catalog_id: UUID, job_id: UUID) -> dict[str, Any]:
    """Executa o pipeline em uma sessão dedicada do worker.

    `asyncio.run()` em `process_catalog_task` cria um event loop novo a
    cada execução. O singleton de engine em `infra.database`, porém, é
    cacheado entre tasks — então o pool retorna conexões asyncpg ligadas
    a loops já fechados. SQLAlchemy faz `ping` no checkout e quebra com
    `RuntimeError: got Future attached to a different loop`. O sintoma
    só aparece quando há múltiplos commits dentro de uma task (cada um
    força checkout/checkin do pool); antes da Sprint 03 Fase F, o
    pipeline tinha um único commit no final e o problema ficava latente.

    Solução: dispor o engine global antes do trabalho. O próximo
    `get_session_factory()` recria engine + pool atrelados ao loop
    deste `asyncio.run()`, e tudo funciona até o final.
    """
    await dispose_engine()
    factory = get_session_factory()
    try:
        async with factory() as session:
            service = CatalogService(session)
            try:
                result = await service.process_catalog(
                    catalog_id=catalog_id,
                    job_id=job_id,
                )
                await session.commit()
                return result
            except Exception:
                # `process_catalog` já gravou o estado de erro via `_mark_error`;
                # commit é necessário para persistir esse estado mesmo
                # quando a exceção sobe.
                await session.commit()
                raise
    finally:
        # Dispõe ao sair também — evita que a próxima task no mesmo worker
        # encontre um pool atrelado a este loop, prestes a ser fechado pelo
        # `asyncio.run()`.
        await dispose_engine()


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="catalog.process",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(),  # retry manual — distinguimos erros permanentes.
    acks_late=True,
)
def process_catalog_task(self: Task, catalog_id: str, job_id: str) -> dict[str, Any]:
    """Entrada Celery: dispara o pipeline assíncrono.

    `bind=True` permite acessar `self.request` para implementar retry com
    backoff exponencial: 60s, 120s, 240s (default_retry_delay x 2^retries).
    """
    cid = UUID(catalog_id)
    jid = UUID(job_id)
    logger.info("catalog.process start (catalog=%s job=%s)", cid, jid)
    try:
        result = asyncio.run(_run_process_catalog(cid, jid))
        logger.info("catalog.process success (catalog=%s)", cid)
        return result
    except _PERMANENT_ERRORS as exc:
        logger.warning(
            "catalog.process permanent failure (catalog=%s): %s",
            cid,
            exc,
        )
        # Não retry — estado de erro já gravado pelo service.
        raise
    except Exception as exc:
        countdown = 60 * (2**self.request.retries)
        logger.exception(
            "catalog.process transient failure (catalog=%s) — retry in %ss",
            cid,
            countdown,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc


@celery_app.task(name="catalog.shutdown", ignore_result=True)  # type: ignore[misc]
def _shutdown_engine() -> None:
    """Sinal manual para liberar engine — útil em SIGTERM custom no worker."""
    asyncio.run(dispose_engine())
