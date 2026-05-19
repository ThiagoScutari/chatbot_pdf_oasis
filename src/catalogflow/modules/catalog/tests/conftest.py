"""Fixtures locais ao módulo `catalog`.

`fake_storage` substitui o `StorageClient` real por um dict in-memory.
A implementação vive em `tests/fakes.py` para reuso transversal.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fakes import FakeStorage

from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage()


@pytest_asyncio.fixture
async def brand(db_session: AsyncSession) -> Brand:
    """Brand de teste persistida e commitada."""
    b = await auth_service.create_brand(
        db_session,
        slug="catalog-test",
        name="Catalog Test Brand",
    )
    await db_session.commit()
    return b


@pytest_asyncio.fixture
async def other_brand(db_session: AsyncSession) -> Brand:
    """Brand secundária — usada para testar isolamento multi-tenant."""
    b = await auth_service.create_brand(
        db_session,
        slug="other-brand",
        name="Other Brand",
    )
    await db_session.commit()
    return b
