"""Seed de desenvolvimento — cria a brand `oasis`, um admin web e uma API key.

Uso:
    python -m catalogflow.scripts.seed_dev

Idempotente quanto à brand e ao admin (reutiliza se já existirem), mas
SEMPRE gera uma nova API key — copie a saída e use no
`Authorization: Bearer <key>`.

A senha do admin é fixa em `oasis123` (dev local). Em outros ambientes
NÃO use este seed.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Final

from sqlalchemy import select

from catalogflow.infra.database import dispose_engine, get_session_factory
from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import WebUser
from catalogflow.web.user_service import hash_password

ADMIN_EMAIL: Final[str] = "admin@oasis.com.br"
ADMIN_NAME: Final[str] = "Admin Oasis"
ADMIN_PASSWORD: Final[str] = "oasis123"  # noqa: S105  # dev seed only


async def _seed() -> tuple[str, str, str, bool]:
    """Cria/recupera brand `oasis` + admin + nova key.

    Retorna `(slug, prefix, raw_key, admin_created)`.
    """
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

        admin = await session.scalar(
            select(WebUser).where(WebUser.email == ADMIN_EMAIL)
        )
        admin_created = False
        if admin is None:
            admin = WebUser(
                brand_id=brand.id,
                email=ADMIN_EMAIL,
                name=ADMIN_NAME,
                password_hash=hash_password(ADMIN_PASSWORD),
                role="admin",
                is_active=True,
            )
            session.add(admin)
            await session.flush()
            admin_created = True

        api_key, raw = await auth_service.create_api_key(
            session,
            brand_id=brand.id,
            name="dev-local",
        )
        await session.commit()
        return brand.slug, api_key.key_prefix, raw, admin_created


def main() -> None:
    slug, prefix, raw, admin_created = asyncio.run(_seed())
    try:
        print("-" * 60)
        print(f"Brand criada/recuperada: {slug}")
        print(f"Admin: {ADMIN_EMAIL} ({'criado' if admin_created else 'já existia'})")
        if admin_created:
            print(f"Senha admin (dev):       {ADMIN_PASSWORD}")
        print(f"API key prefix:          {prefix}")
        print("-" * 60)
        print()
        print("⚠️  COPIE A LINHA ABAIXO — ela só aparece uma vez:")
        print()
        print(f'export CATALOGFLOW_API_KEY="{raw}"')
        print()
        print(
            "Use como: curl -H \"Authorization: Bearer $CATALOGFLOW_API_KEY\" "
            "http://localhost:8000/api/v1/health",
        )
        print("-" * 60)
    finally:
        asyncio.run(dispose_engine())


if __name__ == "__main__":  # pragma: no cover
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
