"""Celery tasks do módulo `orders` — esqueleto reservado para Sprint 02.

Task prevista:
    @celery_app.task(name="orders.extract", bind=True, max_retries=3)
    def extract_order_task(self, order_id: str, job_id: str) -> dict: ...

Não decoramos a função aqui para evitar registrá-la no autodiscover do
Celery antes da implementação definitiva.
"""

from __future__ import annotations
