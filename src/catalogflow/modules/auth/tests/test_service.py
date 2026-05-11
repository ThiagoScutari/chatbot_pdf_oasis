"""Testes do service de `auth`."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth import service as auth_service
from catalogflow.shared.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
)


class TestCreateBrand:
    async def test_creates_with_default_plan(self, db_session: AsyncSession) -> None:
        brand = await auth_service.create_brand(
            db_session,
            slug="oasis",
            name="Oasis Resortwear",
        )
        await db_session.commit()
        assert brand.id is not None
        assert brand.slug == "oasis"
        assert brand.plan == "starter"
        assert brand.created_at is not None

    async def test_duplicate_slug_raises_conflict(self, db_session: AsyncSession) -> None:
        await auth_service.create_brand(db_session, slug="dup", name="A")
        await db_session.commit()
        with pytest.raises(ConflictError) as exc_info:
            await auth_service.create_brand(db_session, slug="dup", name="B")
        assert exc_info.value.code == "BRAND_SLUG_TAKEN"


class TestCreateApiKey:
    async def test_returns_raw_key_and_persists_only_hash(
        self,
        db_session: AsyncSession,
    ) -> None:
        brand = await auth_service.create_brand(db_session, slug="x1", name="X1")
        await db_session.commit()

        api_key, raw = await auth_service.create_api_key(
            db_session,
            brand_id=brand.id,
            name="integração-erp",
        )
        await db_session.commit()

        assert raw.startswith("cf_")
        assert len(raw) > 30
        # O hash persistido é EXATAMENTE SHA-256(raw).
        expected_hash = hashlib.sha256(raw.encode()).hexdigest()
        assert api_key.key_hash == expected_hash
        # Plaintext NUNCA fica no model após o retorno.
        assert raw not in (api_key.name, api_key.key_hash, api_key.key_prefix)
        # Prefixo é prefixo visual do raw key.
        assert api_key.key_prefix == raw[:8]

    async def test_unknown_brand_raises_not_found(
        self,
        db_session: AsyncSession,
    ) -> None:
        from uuid import uuid4

        with pytest.raises(NotFoundError) as exc_info:
            await auth_service.create_api_key(
                db_session,
                brand_id=uuid4(),
                name="orfã",
            )
        assert exc_info.value.code == "BRAND_NOT_FOUND"

    async def test_two_keys_have_distinct_hashes(
        self,
        db_session: AsyncSession,
    ) -> None:
        brand = await auth_service.create_brand(db_session, slug="x2", name="X2")
        await db_session.commit()
        _, raw_a = await auth_service.create_api_key(
            db_session, brand_id=brand.id, name="A"
        )
        _, raw_b = await auth_service.create_api_key(
            db_session, brand_id=brand.id, name="B"
        )
        await db_session.commit()
        assert raw_a != raw_b


class TestVerifyApiKey:
    async def test_valid_key_returns_brand(self, db_session: AsyncSession) -> None:
        brand = await auth_service.create_brand(db_session, slug="vk", name="VK")
        await db_session.commit()
        _, raw = await auth_service.create_api_key(
            db_session, brand_id=brand.id, name="ok"
        )
        await db_session.commit()

        resolved = await auth_service.verify_api_key(db_session, raw)
        assert resolved.id == brand.id

    async def test_missing_credential_raises(self, db_session: AsyncSession) -> None:
        with pytest.raises(AuthenticationError) as exc_info:
            await auth_service.verify_api_key(db_session, "")
        assert exc_info.value.code == "MISSING_CREDENTIAL"

    async def test_malformed_prefix_raises(self, db_session: AsyncSession) -> None:
        with pytest.raises(AuthenticationError) as exc_info:
            await auth_service.verify_api_key(db_session, "wrong_prefix_xxx")
        assert exc_info.value.code == "MALFORMED_CREDENTIAL"

    async def test_unknown_token_raises(self, db_session: AsyncSession) -> None:
        with pytest.raises(AuthenticationError) as exc_info:
            await auth_service.verify_api_key(db_session, "cf_unknown_random_token_zzz")
        assert exc_info.value.code == "INVALID_CREDENTIAL"

    async def test_expired_token_raises(self, db_session: AsyncSession) -> None:
        brand = await auth_service.create_brand(db_session, slug="exp", name="Exp")
        await db_session.commit()
        past = datetime.now(UTC) - timedelta(minutes=1)
        _, raw = await auth_service.create_api_key(
            db_session, brand_id=brand.id, name="old", expires_at=past
        )
        await db_session.commit()
        with pytest.raises(AuthenticationError) as exc_info:
            await auth_service.verify_api_key(db_session, raw)
        assert exc_info.value.code == "CREDENTIAL_EXPIRED"

    async def test_future_expiration_is_accepted(self, db_session: AsyncSession) -> None:
        brand = await auth_service.create_brand(db_session, slug="fut", name="Fut")
        await db_session.commit()
        future = datetime.now(UTC) + timedelta(days=30)
        _, raw = await auth_service.create_api_key(
            db_session, brand_id=brand.id, name="ok", expires_at=future
        )
        await db_session.commit()
        resolved = await auth_service.verify_api_key(db_session, raw)
        assert resolved.id == brand.id


class TestTouchLastUsed:
    async def test_updates_timestamp(self, db_session: AsyncSession) -> None:
        brand = await auth_service.create_brand(db_session, slug="tl", name="TL")
        await db_session.commit()
        api_key, raw = await auth_service.create_api_key(
            db_session, brand_id=brand.id, name="ok"
        )
        await db_session.commit()
        assert api_key.last_used is None

        await auth_service.touch_last_used(db_session, raw)

        await db_session.refresh(api_key)
        assert api_key.last_used is not None

    async def test_unknown_key_is_silent(self, db_session: AsyncSession) -> None:
        # Não deve levantar — best effort.
        await auth_service.touch_last_used(db_session, "cf_unknown")


class TestRotateApiKey:
    async def test_rotates_hash_and_resets_last_used(
        self,
        db_session: AsyncSession,
    ) -> None:
        brand = await auth_service.create_brand(db_session, slug="rot", name="Rot")
        await db_session.commit()
        api_key, raw_old = await auth_service.create_api_key(
            db_session, brand_id=brand.id, name="rotaroma"
        )
        await db_session.commit()
        old_hash = api_key.key_hash

        rotated, raw_new = await auth_service.rotate_api_key(
            db_session, api_key_id=api_key.id
        )
        await db_session.commit()
        assert raw_new != raw_old
        assert rotated.key_hash != old_hash
        assert rotated.last_used is None

        # Token antigo deixa de funcionar.
        with pytest.raises(AuthenticationError):
            await auth_service.verify_api_key(db_session, raw_old)

        # Token novo é aceito.
        resolved = await auth_service.verify_api_key(db_session, raw_new)
        assert resolved.id == brand.id


class TestHashKey:
    def test_deterministic(self) -> None:
        digest_a = auth_service.hash_key("cf_sample")
        digest_b = auth_service.hash_key("cf_sample")
        assert digest_a == digest_b
        assert len(digest_a) == 64

    def test_distinct_inputs_distinct_hashes(self) -> None:
        assert auth_service.hash_key("cf_a") != auth_service.hash_key("cf_b")
