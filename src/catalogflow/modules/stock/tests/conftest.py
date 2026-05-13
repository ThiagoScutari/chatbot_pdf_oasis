"""Fixtures locais ao módulo `stock`.

Espelha `orders/tests/conftest.py`: brand + other_brand commitadas, sem
FakeStorage (módulo `stock` não toca em S3).
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand


@pytest_asyncio.fixture
async def brand(db_session: AsyncSession) -> Brand:
    """Brand de teste persistida e commitada."""
    b = await auth_service.create_brand(
        db_session,
        slug="stock-test",
        name="Stock Test Brand",
    )
    await db_session.commit()
    return b


@pytest_asyncio.fixture
async def other_brand(db_session: AsyncSession) -> Brand:
    """Brand secundária — testes de isolamento multi-tenant."""
    b = await auth_service.create_brand(
        db_session,
        slug="stock-other",
        name="Stock Other Brand",
    )
    await db_session.commit()
    return b
