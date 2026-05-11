"""OrderService — orquestração de extração de pedidos.

Esqueleto reservado para Sprint 02. Operações previstas:

    - create_order(brand_id, catalog_id?, pdf_bytes) → (Order, Job)
    - get_order(order_id, brand_id) → Order
    - process_order(order_id, job_id) → OrderData (executado pela Celery task)
"""

from __future__ import annotations


class OrderService:
    """Serviço do domínio `orders`. Implementação na Sprint 02."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        _ = args, kwargs
        raise NotImplementedError("OrderService entra na Sprint 02")
