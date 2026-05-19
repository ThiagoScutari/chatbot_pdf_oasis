"""Celery tasks do módulo `stock`.

Tasks Celery são síncronas por design. O pipeline real é async (asyncpg +
httpx), então abrimos um event loop por execução via `asyncio.run`.

Convenções (espelham `orders.tasks`):
- Tasks recebem `order_id: str, ...` (UUIDs em string) — nunca ORM.
- Retry exponencial: 60s, 120s, 240s (max 3 tentativas).
- `NotImplementedError` (ConsistemAdapter.submit_order) é tratado como
  ERRO PERMANENTE — não dispara retry, o estado já foi marcado como
  `error` no service.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from celery import Task

from catalogflow.infra.celery_app import celery_app
from catalogflow.infra.database import dispose_engine, get_session_factory
from catalogflow.modules.stock.service import StockService

logger = logging.getLogger(__name__)

# Erros permanentes que não devem disparar retry. NotImplementedError
# é o caso do ConsistemAdapter.submit_order enquanto a Oasis não define
# o contrato — retry só prolonga o sofrimento.
_PERMANENT_ERRORS: tuple[type[BaseException], ...] = (NotImplementedError,)


async def _run_check(
    order_id: UUID,
    stock_check_id: UUID,
    job_id: UUID,
) -> dict[str, Any]:
    """Mesma proteção contra event-loop conflict de `catalog.tasks`,
    `romaneio.tasks` e `orders.tasks`: ver docstring deles para o motivo.
    """
    await dispose_engine()
    factory = get_session_factory()
    try:
        async with factory() as session:
            service = StockService(session)
            try:
                result = await service.check_order_stock(
                    order_id=order_id,
                    stock_check_id=stock_check_id,
                    job_id=job_id,
                )
                await session.commit()
                return result
            except Exception:
                # `check_order_stock` já gravou estado de erro via _mark_job_error;
                # commit para persistir mesmo na exceção.
                await session.commit()
                raise
    finally:
        await dispose_engine()


async def _run_submit(
    order_id: UUID,
    customer_code: str,
    job_id: UUID,
) -> dict[str, Any]:
    """Mesma proteção contra event-loop conflict — ver `_run_check`."""
    await dispose_engine()
    factory = get_session_factory()
    try:
        async with factory() as session:
            service = StockService(session)
            try:
                result = await service.submit_order_to_erp(
                    order_id=order_id,
                    customer_code=customer_code,
                    job_id=job_id,
                )
                await session.commit()
                return result
            except Exception:
                await session.commit()
                raise
    finally:
        await dispose_engine()


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="stock.check",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(),
    acks_late=True,
)
def check_stock_task(
    self: Task,
    order_id: str,
    stock_check_id: str,
    job_id: str,
) -> dict[str, Any]:
    """Entrada Celery: dispara a consulta assíncrona de estoque.

    Backoff exponencial em erros transitórios: 60s → 120s → 240s.
    """
    oid = UUID(order_id)
    scid = UUID(stock_check_id)
    jid = UUID(job_id)
    logger.info("stock.check start (order=%s stock_check=%s)", oid, scid)
    try:
        result = asyncio.run(_run_check(oid, scid, jid))
        logger.info("stock.check success (order=%s)", oid)
        return result
    except _PERMANENT_ERRORS as exc:
        logger.warning("stock.check permanent failure (order=%s): %s", oid, exc)
        raise
    except Exception as exc:
        countdown = 60 * (2**self.request.retries)
        logger.exception(
            "stock.check transient failure (order=%s) — retry in %ss",
            oid,
            countdown,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="stock.submit",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(),
    acks_late=True,
)
def submit_order_task(
    self: Task,
    order_id: str,
    customer_code: str,
    job_id: str,
) -> dict[str, Any]:
    """Entrada Celery: envia o pedido ao ERP via adapter configurado."""
    oid = UUID(order_id)
    jid = UUID(job_id)
    logger.info("stock.submit start (order=%s)", oid)
    try:
        result = asyncio.run(_run_submit(oid, customer_code, jid))
        logger.info("stock.submit success (order=%s)", oid)
        return result
    except _PERMANENT_ERRORS as exc:
        logger.warning("stock.submit permanent failure (order=%s): %s", oid, exc)
        raise
    except Exception as exc:
        countdown = 60 * (2**self.request.retries)
        logger.exception(
            "stock.submit transient failure (order=%s) — retry in %ss",
            oid,
            countdown,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc
