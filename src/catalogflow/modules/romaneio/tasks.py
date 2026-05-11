"""Celery tasks do módulo `romaneio` — esqueleto reservado para Sprint 02.

Task prevista:
    @celery_app.task(name="romaneio.generate", bind=True, max_retries=3)
    def generate_romaneio_task(self, order_id: str, job_id: str) -> dict: ...
"""

from __future__ import annotations
