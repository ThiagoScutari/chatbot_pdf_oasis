"""Testes das páginas autenticadas (dashboard, lista, upload, detalhe)."""

from __future__ import annotations

from uuid import uuid4

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Catalog
from catalogflow.web.auth import SESSION_COOKIE


async def _login(client: AsyncClient, api_key: str) -> None:
    """Faz POST /login e deixa o cookie assinado no cookie jar do client."""
    resp = await client.post("/login", data={"api_key": api_key})
    assert resp.status_code == 302
    assert SESSION_COOKIE in client.cookies


@pytest_asyncio.fixture
async def sample_catalog(
    db_session: AsyncSession, sample_brand: Brand
) -> Catalog:
    """Catálogo já 'pronto' para testar o detalhe."""
    catalog = Catalog(
        brand_id=sample_brand.id,
        name="Inverno 26 MOTION",
        collection="MOTION",
        status="ready",
        source_key=f"{sample_brand.id}/catalogs/x/source.pdf",
        output_key=f"{sample_brand.id}/catalogs/x/editable.pdf",
        n_pages=70,
        n_product_pages=31,
        n_skus=36,
        n_fields=148,
    )
    db_session.add(catalog)
    await db_session.commit()
    await db_session.refresh(catalog)
    return catalog


class TestDashboard:
    async def test_dashboard_without_session_redirects_to_login(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/dashboard")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_dashboard_with_session_renders(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        await _login(client, sample_api_key)
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        body = resp.text
        # Saudação com o nome da brand fixture.
        assert "Oasis Resortwear" in body
        # Cards de contagem (0 catálogos / pedidos no estado inicial).
        assert "Catálogos" in body
        assert "Pedidos" in body
        assert "Romaneios" in body

    async def test_dashboard_with_invalid_session_redirects(
        self, client: AsyncClient
    ) -> None:
        client.cookies.set(SESSION_COOKIE, "not-a-valid-token")
        resp = await client.get("/dashboard")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"


class TestCatalogsList:
    async def test_catalogs_without_session_redirects_to_login(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/catalogs")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_catalogs_with_session_renders_empty_state(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        await _login(client, sample_api_key)
        resp = await client.get("/catalogs")
        assert resp.status_code == 200
        body = resp.text
        # Estado vazio — sem catalogs criados na fixture.
        assert "Nenhum catálogo ainda" in body
        # CTA de envio.
        assert "+ Enviar primeiro catálogo" in body or "/catalogs/upload" in body

    async def test_catalogs_with_invalid_session_redirects(
        self, client: AsyncClient
    ) -> None:
        client.cookies.set(SESSION_COOKIE, "not-a-valid-token")
        resp = await client.get("/catalogs")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"


class TestCatalogBadgeFragment:
    async def test_badge_for_unknown_id_returns_404(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        await _login(client, sample_api_key)
        # UUID válido em forma mas inexistente.
        resp = await client.get(
            "/catalogs/00000000-0000-0000-0000-000000000000/_badge"
        )
        assert resp.status_code == 404

    async def test_badge_requires_session(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/catalogs/00000000-0000-0000-0000-000000000000/_badge"
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"


class TestCatalogUploadForm:
    async def test_upload_form_requires_session(self, client: AsyncClient) -> None:
        resp = await client.get("/catalogs/upload")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_upload_form_renders(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        await _login(client, sample_api_key)
        resp = await client.get("/catalogs/upload")
        assert resp.status_code == 200
        body = resp.text
        assert "Novo catálogo" in body
        assert 'name="file"' in body
        assert 'name="name"' in body
        # Alpine machine state expostas no template.
        assert "uploadFlow" in body


class TestCatalogDetail:
    async def test_detail_requires_session(self, client: AsyncClient) -> None:
        bogus = uuid4()
        resp = await client.get(f"/catalogs/{bogus}")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_detail_unknown_id_returns_friendly_404(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        await _login(client, sample_api_key)
        bogus = uuid4()
        resp = await client.get(f"/catalogs/{bogus}")
        assert resp.status_code == 404
        # Template HTML elegante, não JSON.
        assert "text/html" in resp.headers.get("content-type", "")
        assert "Catálogo não encontrado" in resp.text
        assert "Voltar ao início" in resp.text

    async def test_detail_renders_for_existing_catalog(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_catalog: Catalog,
    ) -> None:
        await _login(client, sample_api_key)
        resp = await client.get(f"/catalogs/{sample_catalog.id}")
        assert resp.status_code == 200
        body = resp.text
        assert sample_catalog.name in body
        assert "MOTION" in body
        # Botão de download presente quando status é 'ready'.
        assert f"/catalogs/{sample_catalog.id}/download" in body

    async def test_detail_isolates_other_brand(
        self,
        client: AsyncClient,
        sample_api_key: str,
        db_session: AsyncSession,
    ) -> None:
        """Catálogo de outra brand → 404 elegante (sem vazar existência)."""
        from catalogflow.modules.auth import service as auth_service

        other = await auth_service.create_brand(
            db_session, slug="outra", name="Outra Marca"
        )
        await db_session.commit()
        other_catalog = Catalog(
            brand_id=other.id,
            name="Catálogo secreto",
            status="ready",
        )
        db_session.add(other_catalog)
        await db_session.commit()
        await db_session.refresh(other_catalog)

        await _login(client, sample_api_key)
        resp = await client.get(f"/catalogs/{other_catalog.id}")
        assert resp.status_code == 404
        assert "Catálogo secreto" not in resp.text
