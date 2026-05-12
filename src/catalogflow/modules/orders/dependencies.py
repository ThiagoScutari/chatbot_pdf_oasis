"""FastAPI dependencies do módulo `orders`."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.database import get_db
from catalogflow.infra.storage import StorageClient, get_storage
from catalogflow.modules.orders.service import OrderService
from catalogflow.modules.romaneio.service import RomaneioService


async def get_order_service(
    db: AsyncSession = Depends(get_db),
    storage: StorageClient = Depends(get_storage),
) -> OrderService:
    """Constrói o `OrderService` por request."""
    return OrderService(db, storage=storage)


async def get_romaneio_service(
    db: AsyncSession = Depends(get_db),
    storage: StorageClient = Depends(get_storage),
) -> RomaneioService:
    """Constrói o `RomaneioService` por request."""
    return RomaneioService(db, storage=storage)
