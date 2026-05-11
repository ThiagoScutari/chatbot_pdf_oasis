"""Testes do dependency `get_current_brand` e do gate `require_internal_secret`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand


class TestGetCurrentBrand:
    async def test_valid_bearer_returns_brand(
        self,
        client: AsyncClient,
        sample_brand: Brand,
        sample_api_key: tuple[str, str],
    ) -> None:
        raw, _ = sample_api_key
        resp = await client.get(
            "/whoami",
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "brand_id": str(sample_brand.id),
            "slug": sample_brand.slug,
        }

    async def test_missing_header_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get("/whoami")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "MISSING_CREDENTIAL"

    async def test_wrong_scheme_returns_401(
        self,
        client: AsyncClient,
        sample_api_key: tuple[str, str],
    ) -> None:
        raw, _ = sample_api_key
        resp = await client.get(
            "/whoami",
            headers={"Authorization": f"Basic {raw}"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "MALFORMED_CREDENTIAL"

    async def test_empty_bearer_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/whoami",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "MALFORMED_CREDENTIAL"

    async def test_invalid_token_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/whoami",
            headers={"Authorization": "Bearer cf_does_not_exist"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "INVALID_CREDENTIAL"

    async def test_token_without_prefix_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/whoami",
            headers={"Authorization": "Bearer xyz_no_prefix"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "MALFORMED_CREDENTIAL"

    async def test_expired_token_returns_401(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        brand = await auth_service.create_brand(db_session, slug="expx", name="ExpX")
        await db_session.commit()
        past = datetime.now(UTC) - timedelta(minutes=5)
        _, raw = await auth_service.create_api_key(
            db_session, brand_id=brand.id, name="old", expires_at=past
        )
        await db_session.commit()

        resp = await client.get(
            "/whoami",
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "CREDENTIAL_EXPIRED"


class TestInternalSecretGate:
    async def test_missing_secret_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/internal/brands",
            json={"slug": "x", "name": "X"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "MISSING_INTERNAL_SECRET"

    async def test_wrong_secret_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/internal/brands",
            headers={"X-Internal-Secret": "wrong"},
            json={"slug": "x", "name": "X"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "INVALID_INTERNAL_SECRET"

    async def test_correct_secret_creates_brand(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/internal/brands",
            headers={"X-Internal-Secret": "test-internal-secret"},
            json={"slug": "newbrand", "name": "New Brand"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["slug"] == "newbrand"
        assert body["plan"] == "starter"

    async def test_create_api_key_returns_raw_once(
        self,
        client: AsyncClient,
        sample_brand: Brand,
    ) -> None:
        resp = await client.post(
            f"/internal/brands/{sample_brand.id}/api-keys",
            headers={"X-Internal-Secret": "test-internal-secret"},
            json={"name": "ci-bot"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["raw_key"].startswith("cf_")
        # O hash não aparece na resposta — apenas o prefix visual.
        assert "key_hash" not in body["api_key"]
        assert body["api_key"]["key_prefix"] == body["raw_key"][:8]


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", "Bearer  ", "bearer_without_space"],
)
async def test_malformed_authorization_variants_return_401(
    client: AsyncClient,
    header: str | None,
) -> None:
    headers = {"Authorization": header} if header is not None else {}
    resp = await client.get("/whoami", headers=headers)
    assert resp.status_code == 401
