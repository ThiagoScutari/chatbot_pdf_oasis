"""Seed de desenvolvimento — cria a brand `oasis` e imprime uma API key.

Uso:
    python -m catalogflow.scripts.seed_dev

Idempotente quanto à brand (reutiliza se já existir), mas SEMPRE gera uma
nova API key — copie a saída e use no `Authorization: Bearer <key>`.

A key bruta aparece em stdout UMA ÚNICA VEZ. Após esse instante, apenas o
hash SHA-256 fica no banco.
"""

from __future__ import annotations

import asyncio
import sys

from catalogflow.infra.database import dispose_engine, get_session_factory
from catalogflow.modules.auth import service as auth_service


async def _seed() -> tuple[str, str, str]:
    """Cria/recupera brand `oasis` + nova key. Retorna `(slug, prefix, raw)`."""
    factory = get_session_factory()
    async with factory() as session:
        brand = await auth_service.get_brand_by_slug(session, "oasis")
        if brand is None:
            brand = await auth_service.create_brand(
                session,
                slug="oasis",
                name="Oasis Resortwear",
                plan="growth",
            )
        api_key, raw = await auth_service.create_api_key(
            session,
            brand_id=brand.id,
            name="dev-local",
        )
        await session.commit()
        return brand.slug, api_key.key_prefix, raw


def main() -> None:
    slug, prefix, raw = asyncio.run(_seed())
    try:
        print("─" * 60)
        print(f"Brand criada/recuperada: {slug}")
        print(f"API key prefix:          {prefix}")
        print("─" * 60)
        print()
        print("⚠️  COPIE A LINHA ABAIXO — ela só aparece uma vez:")
        print()
        print(f'export CATALOGFLOW_API_KEY="{raw}"')
        print()
        print(
            "Use como: curl -H \"Authorization: Bearer $CATALOGFLOW_API_KEY\" "
            "http://localhost:8000/api/v1/health",
        )
        print("─" * 60)
    finally:
        asyncio.run(dispose_engine())


if __name__ == "__main__":  # pragma: no cover
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
