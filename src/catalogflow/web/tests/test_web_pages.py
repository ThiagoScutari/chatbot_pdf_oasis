"""Testes das páginas autenticadas (dashboard, lista de catálogos)."""

from __future__ import annotations

from httpx import AsyncClient

from catalogflow.web.auth import SESSION_COOKIE


async def _login(client: AsyncClient, api_key: str) -> None:
    """Faz POST /login e deixa o cookie assinado no cookie jar do client."""
    resp = await client.post("/login", data={"api_key": api_key})
    assert resp.status_code == 302
    assert SESSION_COOKIE in client.cookies


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
