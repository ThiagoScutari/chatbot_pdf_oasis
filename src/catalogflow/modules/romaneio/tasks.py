"""Celery task de geração de romaneio.

Diferente de `order.extract`, não há classe de erro "permanente" para a
geração — falhas de download/upload de storage e exceções inesperadas
no builder são transitórias por natureza. Todos os erros entram em retry
com backoff exponencial (max 3).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from celery import Task

from catalogflow.infra.celery_app import celery_app
from catalogflow.infra.database import get_session_factory
from catalogflow.modules.romaneio.service import RomaneioService
from catalogflow.shared.image_fetcher import fetch_product_images

logger = logging.getLogger(__name__)


async def _run_process_romaneio(
    romaneio_id: UUID,
    job_id: UUID,
) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        # Em produção, injetamos o fetcher real — o PDF sai com fotos.
        # Em testes, RomaneioService(db_session) sem `image_fetcher` mantém
        # o PDF gerado offline (sem chamadas ao AMC).
        service = RomaneioService(session, image_fetcher=fetch_product_images)
        try:
            result = await service.process_romaneio(
                romaneio_id=romaneio_id,
                job_id=job_id,
            )
            await session.commit()
            return result
        except Exception:
            await session.commit()
            raise


@celery_app.task(
    bind=True,
    name="romaneio.generate",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(),
    acks_late=True,
)
def generate_romaneio_task(
    self: Task,
    romaneio_id: str,
    job_id: str,
) -> dict[str, Any]:
    """Entrada Celery: gera o PDF, faz upload, atualiza `output_key`."""
    rid = UUID(romaneio_id)
    jid = UUID(job_id)
    logger.info("romaneio.generate start (romaneio=%s job=%s)", rid, jid)
    try:
        result = asyncio.run(_run_process_romaneio(rid, jid))
        logger.info("romaneio.generate success (romaneio=%s)", rid)
        return result
    except Exception as exc:
        countdown = 60 * (2**self.request.retries)
        logger.exception(
            "romaneio.generate failure (romaneio=%s) — retry in %ss",
            rid,
            countdown,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc
