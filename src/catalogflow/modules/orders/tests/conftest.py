"""Fixtures locais ao módulo `orders`.

Espelha o padrão de `catalog/tests/conftest.py`: brand persistida + FakeStorage.
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
        slug="orders-test",
        name="Orders Test Brand",
    )
    await db_session.commit()
    return b


@pytest_asyncio.fixture
async def other_brand(db_session: AsyncSession) -> Brand:
    """Brand secundária — usada para testar isolamento multi-tenant."""
    b = await auth_service.create_brand(
        db_session,
        slug="orders-other",
        name="Orders Other Brand",
    )
    await db_session.commit()
    return b
