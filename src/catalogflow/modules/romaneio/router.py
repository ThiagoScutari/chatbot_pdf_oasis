"""Router HTTP do módulo `romaneio` — esqueleto reservado para Sprint 02.

Endpoint principal `GET /api/v1/orders/{order_id}/romaneio` será definido
provavelmente sob o router de `orders` ou aqui — decisão na Sprint 02.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/romaneio", tags=["romaneio"])
