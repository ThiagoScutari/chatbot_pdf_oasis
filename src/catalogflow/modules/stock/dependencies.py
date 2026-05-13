"""FastAPI dependencies do módulo `stock`."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.database import get_db
from catalogflow.modules.stock.service import StockService


async def get_stock_service(
    db: AsyncSession = Depends(get_db),
) -> StockService:
    """Constrói o `StockService` por request.

    Adapter é resolvido on-demand via `service.get_adapter()` lendo
    settings — não é injetado aqui para suportar troca de adapter sem
    rebuild (`ERP_ADAPTER=mock|consistem`).
    """
    return StockService(db)
