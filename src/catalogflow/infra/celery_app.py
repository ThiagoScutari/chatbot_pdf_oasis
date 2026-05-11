"""Celery app factory.

Convenções (ADR-002):
    - Broker e backend: Redis (DBs 1 e 2; ver `.env.example`).
    - Task routes: cada módulo tem sua queue dedicada (`catalog`, `orders`,
      `romaneio`). Workers podem escalar por queue independentemente.
    - Serialização: JSON (sem pickle — segurança).
    - Tasks recebem `entity_id: str` (UUID em string), nunca objetos ORM.
"""

from __future__ import annotations

from celery import Celery

from catalogflow.infra.settings import get_settings


def _build_celery_app() -> Celery:
    settings = get_settings()
    app = Celery("catalogflow")
    app.conf.update(
        broker_url=settings.celery_broker_url,
        result_backend=settings.celery_result_backend,
        result_expires=settings.celery_result_ttl_seconds,
        # Serialização — JSON only, sem pickle.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Timezone explícito.
        timezone="UTC",
        enable_utc=True,
        # Reliability defaults.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        # Roteamento por módulo — cada queue pode ter workers próprios.
        task_routes={
            "catalog.*": {"queue": "catalog"},
            "orders.*": {"queue": "orders"},
            "romaneio.*": {"queue": "romaneio"},
        },
        task_default_queue="default",
    )

    # Autodiscover: cada módulo declara suas tasks em `tasks.py`.
    app.autodiscover_tasks(
        packages=[
            "catalogflow.modules.catalog",
            "catalogflow.modules.orders",
            "catalogflow.modules.romaneio",
        ],
        related_name="tasks",
    )
    return app


# Singleton importado por `celery -A catalogflow.infra.celery_app worker`.
celery_app: Celery = _build_celery_app()
