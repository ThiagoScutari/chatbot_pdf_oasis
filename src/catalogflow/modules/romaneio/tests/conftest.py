"""Fixtures locais do módulo `romaneio`."""

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
    b = await auth_service.create_brand(
        db_session,
        slug="romaneio-test",
        name="Romaneio Test Brand",
    )
    await db_session.commit()
    return b


@pytest_asyncio.fixture
async def other_brand(db_session: AsyncSession) -> Brand:
    b = await auth_service.create_brand(
        db_session,
        slug="romaneio-other",
        name="Romaneio Other Brand",
    )
    await db_session.commit()
    return b
