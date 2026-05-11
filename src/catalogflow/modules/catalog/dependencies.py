"""FastAPI dependencies do módulo `catalog`."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.infra.database import get_db
from catalogflow.infra.storage import StorageClient, get_storage
from catalogflow.modules.catalog.service import CatalogService


async def get_catalog_service(
    db: AsyncSession = Depends(get_db),
    storage: StorageClient = Depends(get_storage),
) -> CatalogService:
    """Constrói o `CatalogService` por request.

    Usar como `Depends(get_catalog_service)` em endpoints. Em testes,
    sobrescreva `get_storage`/`get_db` para injetar mocks.
    """
    return CatalogService(db, storage=storage)
