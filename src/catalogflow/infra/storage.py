"""Wrapper de armazenamento S3-compatible (Cloudflare R2 em produção).

Decisão (ADR-005): banco armazena apenas metadados + chave do objeto. Nunca
gravar bytes de PDF em colunas do Postgres.

Convenção de chaves: `{brand_id}/{kind}/{uuid}.pdf` — todo isolamento
multi-tenant passa pelo prefixo `{brand_id}/`. Ver CLAUDE.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aioboto3
from botocore.config import Config

from catalogflow.infra.settings import Settings, get_settings

if TYPE_CHECKING:
    from types_aiobotocore_s3.client import S3Client  # type: ignore[import-not-found]


class StorageError(RuntimeError):
    """Erro de operação contra o storage S3-compatible."""


class StorageClient:
    """Cliente assíncrono para upload/download/presign em S3 ou R2.

    Construído via `from_settings()` para reuso da configuração global.
    Cada operação abre e fecha um cliente aioboto3 — o session por baixo
    cuida do connection pooling.
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        presigned_url_ttl_seconds: int,
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._presigned_ttl = presigned_url_ttl_seconds
        self._session = aioboto3.Session()

    # ── factory ───────────────────────────────
    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> StorageClient:
        s = settings or get_settings()
        return cls(
            bucket=s.s3_bucket,
            endpoint_url=s.s3_endpoint_url,
            region=s.s3_region,
            access_key_id=s.aws_access_key_id.get_secret_value(),
            secret_access_key=s.aws_secret_access_key.get_secret_value(),
            presigned_url_ttl_seconds=s.s3_presigned_url_ttl_seconds,
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    # ── client context ────────────────────────
    def _client(self) -> Any:
        """Retorna context manager do cliente S3 aioboto3."""
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual" if self._endpoint_url is None else "path"},
        )
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            region_name=self._region,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            config=config,
        )

    # ── operações ─────────────────────────────
    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/pdf",
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Faz upload de `data` na chave `key`. Retorna a chave gravada."""
        try:
            async with self._client() as s3:  # type: ignore[union-attr]
                await s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                    Metadata=metadata or {},
                )
        except Exception as exc:  # pragma: no cover - rede
            raise StorageError(f"upload failed for {key}: {exc}") from exc
        return key

    async def download(self, key: str) -> bytes:
        """Baixa o objeto e retorna seu corpo em bytes."""
        try:
            async with self._client() as s3:  # type: ignore[union-attr]
                obj = await s3.get_object(Bucket=self._bucket, Key=key)
                body = await obj["Body"].read()
                if not isinstance(body, bytes):
                    raise StorageError(f"unexpected body type for {key}")
                return body
        except StorageError:
            raise
        except Exception as exc:  # pragma: no cover - rede
            raise StorageError(f"download failed for {key}: {exc}") from exc

    async def presigned_url(self, key: str, *, expires_in: int | None = None) -> str:
        """Gera URL assinada para download via HTTP direto."""
        ttl = expires_in if expires_in is not None else self._presigned_ttl
        try:
            async with self._client() as s3:  # type: ignore[union-attr]
                url = await s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self._bucket, "Key": key},
                    ExpiresIn=ttl,
                )
                if not isinstance(url, str):
                    raise StorageError(f"unexpected url type for {key}")
                return url
        except StorageError:
            raise
        except Exception as exc:  # pragma: no cover - rede
            raise StorageError(f"presigned_url failed for {key}: {exc}") from exc

    async def delete(self, key: str) -> None:
        """Remove o objeto. Idempotente — não falha se o objeto não existe."""
        try:
            async with self._client() as s3:  # type: ignore[union-attr]
                await s3.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:  # pragma: no cover - rede
            raise StorageError(f"delete failed for {key}: {exc}") from exc

    async def exists(self, key: str) -> bool:
        """Retorna True se o objeto existe."""
        try:
            async with self._client() as s3:  # type: ignore[union-attr]
                await s3.head_object(Bucket=self._bucket, Key=key)
                return True
        except Exception:
            return False


# ──────────────────────────────────────────────
#  Singleton + FastAPI dependency
# ──────────────────────────────────────────────

_storage: StorageClient | None = None


def get_storage_client() -> StorageClient:
    """Retorna o `StorageClient` global, criando-o na primeira chamada."""
    global _storage
    if _storage is None:
        _storage = StorageClient.from_settings()
    return _storage


async def get_storage() -> StorageClient:
    """FastAPI dependency — retorna o singleton.

    Em testes, faça override:
        app.dependency_overrides[get_storage] = lambda: fake_storage
    """
    return get_storage_client()


def reset_storage_client() -> None:
    """Limpa o singleton — útil em testes."""
    global _storage
    _storage = None
