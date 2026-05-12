"""Testes das rotas de autenticação por sessão da web UI."""

from __future__ import annotations

from httpx import AsyncClient

from catalogflow.web.auth import SESSION_COOKIE


class TestLoginPage:
    async def test_get_login_renders_form(self, client: AsyncClient) -> None:
        resp = await client.get("/login")
        assert resp.status_code == 200
        # HTML, não JSON.
        assert "text/html" in resp.headers.get("content-type", "")
        body = resp.text
        assert 'name="api_key"' in body
        assert "OASIS" in body

    async def test_get_login_no_inline_error(self, client: AsyncClient) -> None:
        resp = await client.get("/login")
        assert "Chave de acesso inválida" not in resp.text


class TestLoginSubmission:
    async def test_valid_key_creates_session_and_redirects(
        self,
        client: AsyncClient,
        sample_api_key: str,
    ) -> None:
        resp = await client.post("/login", data={"api_key": sample_api_key})
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"
        assert SESSION_COOKIE in resp.cookies
        # cookie não-vazio
        assert resp.cookies[SESSION_COOKIE]

    async def test_invalid_key_renders_error_inline(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post("/login", data={"api_key": "cf_not_a_real_key"})
        assert resp.status_code == 200
        assert "Chave de acesso inválida" in resp.text
        assert SESSION_COOKIE not in resp.cookies

    async def test_malformed_key_renders_error_inline(
        self, client: AsyncClient
    ) -> None:
        # sem prefixo `cf_` — auth service levanta MALFORMED_CREDENTIAL
        resp = await client.post("/login", data={"api_key": "xyz_no_prefix"})
        assert resp.status_code == 200
        assert "Chave de acesso inválida" in resp.text

    async def test_empty_key_returns_validation_error(
        self, client: AsyncClient
    ) -> None:
        # Form(..., min_length=1) → 422 do pipeline Pydantic
        resp = await client.post("/login", data={"api_key": ""})
        assert resp.status_code in (400, 422)


class TestRootRedirect:
    async def test_root_without_session_redirects_to_login(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_root_with_invalid_cookie_redirects_to_login(
        self, client: AsyncClient
    ) -> None:
        client.cookies.set(SESSION_COOKIE, "obviously-not-a-valid-token")
        resp = await client.get("/")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_root_with_valid_session_redirects_to_dashboard(
        self,
        client: AsyncClient,
        sample_api_key: str,
    ) -> None:
        # POST /login deixa o cookie assinado no cookie jar do client.
        login = await client.post("/login", data={"api_key": sample_api_key})
        assert login.status_code == 302
        assert SESSION_COOKIE in client.cookies

        resp = await client.get("/")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"


class TestLogout:
    async def test_logout_clears_cookie_and_redirects(
        self,
        client: AsyncClient,
        sample_api_key: str,
    ) -> None:
        # Estabelece sessão (cookie jar do client guarda o cf_session assinado).
        login = await client.post("/login", data={"api_key": sample_api_key})
        assert login.status_code == 302
        assert SESSION_COOKIE in client.cookies

        resp = await client.get("/logout")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

        # Set-Cookie deve mandar o cookie embora.
        set_cookie = resp.headers.get("set-cookie", "")
        assert SESSION_COOKIE in set_cookie
        # Expiração no passado ou max-age=0 sinaliza deleção.
        normalized = set_cookie.lower()
        assert (
            "max-age=0" in normalized
            or "expires=thu, 01 jan 1970" in normalized
            or 'cf_session=""' in normalized
            or "cf_session=;" in normalized
        )
