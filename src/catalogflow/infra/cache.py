"""Cliente Redis async — singleton por processo.

Redis cumpre três papéis (ADR-003):
- Broker do Celery (DB 1)
- Result backend do Celery (DB 2)
- Cache de aplicação (DB 0, via este módulo)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis, from_url

from catalogflow.infra.settings import get_settings

_redis: Redis[str] | None = None
_redis_binary: Redis[bytes] | None = None


def get_redis_client() -> Redis[str]:
    """Retorna o cliente Redis, criando-o na primeira chamada.

    Mantém um único `ConnectionPool` por processo. Os valores retornados
    são strings (decode_responses=True) — para cachear payloads binários
    (imagens etc.) use `get_redis_binary_client()`.
    """
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
    return _redis


def get_redis_binary_client() -> Redis[bytes]:
    """Cliente Redis binário (`decode_responses=False`).

    Usado pelo cache de fotos de produto — `redis-py` por padrão decodifica
    GETs em UTF-8, o que falha para bytes JPEG. Mantemos um pool separado
    para esses casos, evitando misturar text/bytes no mesmo cliente.
    """
    global _redis_binary
    if _redis_binary is None:
        settings = get_settings()
        _redis_binary = from_url(
            settings.redis_url,
            decode_responses=False,
            max_connections=10,
        )
    return _redis_binary


async def get_redis() -> AsyncIterator[Redis[str]]:
    """Dependency FastAPI: injeta cliente Redis."""
    client = get_redis_client()
    try:
        yield client
    finally:
        # Conexão volta ao pool automaticamente; não fechar o cliente aqui.
        pass


async def close_redis() -> None:
    """Fecha os pools globais. Chamado no shutdown da aplicação."""
    global _redis, _redis_binary
    if _redis is not None:
        # redis-py 5.x adicionou aclose() (preferido em async); stubs antigos
        # podem não expor o atributo. Fallback para close() seria síncrono.
        await _redis.aclose()  # type: ignore[attr-defined]
        _redis = None
    if _redis_binary is not None:
        await _redis_binary.aclose()  # type: ignore[attr-defined]
        _redis_binary = None


async def check_connection() -> dict[str, Any]:
    """Healthcheck: PING no Redis."""
    client = get_redis_client()
    pong = await client.ping()
    return {"redis": "ok" if pong else "error", "ping": pong}
