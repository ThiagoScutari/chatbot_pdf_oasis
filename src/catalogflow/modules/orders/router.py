"""Router HTTP do módulo `orders` — esqueleto reservado para Sprint 02.

Endpoints previstos:
    - POST /api/v1/orders/extract
    - GET  /api/v1/orders/{order_id}
    - GET  /api/v1/orders/{order_id}/romaneio

Não está montado no app principal nesta sprint — `main.py` ainda não inclui
este router.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])
