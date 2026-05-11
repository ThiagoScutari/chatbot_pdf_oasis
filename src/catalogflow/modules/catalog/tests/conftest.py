"""Fixtures locais ao módulo `catalog`.

`fake_storage` substitui o `StorageClient` real por um dict in-memory,
suficiente para validar o contrato `upload`/`download`/`presigned_url`/`delete`.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand


class FakeStorage:
    """Implementação in-memory do contrato `StorageClient`.

    Não herda do real porque `aioboto3` exige credenciais válidas para
    construir o cliente — preferimos duck typing nos testes.
    """

    bucket = "test-bucket"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/pdf",
        metadata: dict[str, str] | None = None,
    ) -> str:
        _ = content_type, metadata
        self.objects[key] = bytes(data)
        return key

    async def download(self, key: str) -> bytes:
        return self.objects[key]

    async def presigned_url(self, key: str, *, expires_in: int | None = None) -> str:
        _ = expires_in
        return f"https://fake-s3/{self.bucket}/{key}?token=test"

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.deleted.append(key)

    async def exists(self, key: str) -> bool:
        return key in self.objects


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
