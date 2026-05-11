"""Fakes compartilhados pela suite de testes."""

from __future__ import annotations


class FakeStorage:
    """Implementação in-memory do contrato `StorageClient`.

    Não herda do real porque `aioboto3` exige credenciais válidas para
    instanciar o cliente — preferimos duck typing nos testes.

    API replicada (`upload`, `download`, `presigned_url`, `delete`, `exists`).
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
