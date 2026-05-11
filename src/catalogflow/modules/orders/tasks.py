"""Celery tasks do módulo `orders`.

Tasks Celery são SÍNCRONAS por design. O pipeline real é async (asyncpg +
aioboto3), então abrimos um event loop por execução.

Convenções:
- Tasks recebem `order_id: str, job_id: str` (UUID em string) — nunca ORM.
- Erros permanentes (PDFCorruptError, PDFFlattenedError, PDFTooLargeError)
  NÃO disparam retry — o estado já foi gravado como `error` no banco.
- PDFFlattenedError é o caso-chave do PRD (Armadilha #3): nunca-retryable
  porque arquivo achatado não volta a ter `/AcroForm` por reprocessamento.
- Demais erros disparam retry com backoff exponencial (max 3 tentativas).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from celery import Task

from catalogflow.infra.celery_app import celery_app
from catalogflow.infra.database import get_session_factory
from catalogflow.modules.orders.service import OrderService
from catalogflow.shared.errors import (
    PDFCorruptError,
    PDFFlattenedError,
    PDFTooLargeError,
)

logger = logging.getLogger(__name__)

_PERMANENT_ERRORS: tuple[type[Exception], ...] = (
    PDFCorruptError,
    PDFFlattenedError,
    PDFTooLargeError,
)


async def _run_process_order(order_id: UUID, job_id: UUID) -> dict[str, Any]:
    """Executa o pipeline em uma sessão dedicada do worker."""
    factory = get_session_factory()
    async with factory() as session:
        service = OrderService(session)
        try:
            result = await service.process_order(
                order_id=order_id,
                job_id=job_id,
            )
            await session.commit()
            return result
        except Exception:
            # `process_order` já gravou o estado de erro via `_mark_error`;
            # commit é necessário para persistir o estado mesmo na exceção.
            await session.commit()
            raise


@celery_app.task(
    bind=True,
    name="order.extract",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(),  # retry manual — distinguimos erros permanentes.
    acks_late=True,
)
def extract_order_task(self: Task, order_id: str, job_id: str) -> dict[str, Any]:
    """Entrada Celery: dispara o pipeline assíncrono de extração de pedido.

    Backoff exponencial em erros transitórios: 60s, 120s, 240s.
    Erros permanentes (PDFFlattenedError em destaque) sobem direto sem retry.
    """
    oid = UUID(order_id)
    jid = UUID(job_id)
    logger.info("order.extract start (order=%s job=%s)", oid, jid)
    try:
        result = asyncio.run(_run_process_order(oid, jid))
        logger.info("order.extract success (order=%s)", oid)
        return result
    except _PERMANENT_ERRORS as exc:
        logger.warning(
            "order.extract permanent failure (order=%s code=%s): %s",
            oid,
            getattr(exc, "code", "UNKNOWN"),
            exc,
        )
        # NÃO retry — estado de erro já gravado pelo service.
        raise
    except Exception as exc:
        countdown = 60 * (2**self.request.retries)
        logger.exception(
            "order.extract transient failure (order=%s) — retry in %ss",
            oid,
            countdown,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc
