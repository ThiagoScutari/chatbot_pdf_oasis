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

_redis: Redis | None = None


def get_redis_client() -> Redis:
    """Retorna o cliente Redis, criando-o na primeira chamada.

    Mantém um único `ConnectionPool` por processo.
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


async def get_redis() -> AsyncIterator[Redis]:
    """Dependency FastAPI: injeta cliente Redis."""
    client = get_redis_client()
    try:
        yield client
    finally:
        # Conexão volta ao pool automaticamente; não fechar o cliente aqui.
        pass


async def close_redis() -> None:
    """Fecha o pool global. Chamado no shutdown da aplicação."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def check_connection() -> dict[str, Any]:
    """Healthcheck: PING no Redis."""
    client = get_redis_client()
    pong = await client.ping()
    return {"redis": "ok" if pong else "error", "ping": pong}
