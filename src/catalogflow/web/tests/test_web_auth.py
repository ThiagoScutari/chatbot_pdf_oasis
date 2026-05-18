"""Testes das rotas de autenticação por sessão da web UI.

Sprint 03.5: o login mudou de API Key para email+senha. Cobertura aqui:
- GET/POST /login (form e submissão)
- POST /login com rate-limit
- GET /forgot-password e magic link
- GET /register
- /admin/users (rota administrativa) — gating por role
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth.models import WebUser
from catalogflow.web.auth import SESSION_COOKIE
from catalogflow.web.tests.conftest import (
    SAMPLE_ADMIN_EMAIL,
    SAMPLE_ADMIN_PASSWORD,
    SAMPLE_USER_EMAIL,
    SAMPLE_USER_PASSWORD,
)


class TestLoginPage:
    async def test_get_login_renders_form(self, client: AsyncClient) -> None:
        resp = await client.get("/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        body = resp.text
        assert 'name="email"' in body
        assert 'name="password"' in body
        assert "OASIS" in body

    async def test_get_login_no_inline_error(self, client: AsyncClient) -> None:
        resp = await client.get("/login")
        assert "incorretos" not in resp.text.lower()


class TestLoginSubmission:
    async def test_valid_credentials_create_session_and_redirect(
        self,
        client: AsyncClient,
        sample_user: WebUser,
    ) -> None:
        del sample_user  # fixture cria o usuário no DB
        resp = await client.post(
            "/login",
            data={"email": SAMPLE_USER_EMAIL, "password": SAMPLE_USER_PASSWORD},
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"
        assert SESSION_COOKIE in resp.cookies
        assert resp.cookies[SESSION_COOKIE]

    async def test_wrong_password_renders_error_inline(
        self, client: AsyncClient, sample_user: WebUser
    ) -> None:
        del sample_user
        resp = await client.post(
            "/login",
            data={"email": SAMPLE_USER_EMAIL, "password": "errada-errada"},
        )
        assert resp.status_code == 200
        assert "incorret" in resp.text.lower()
        assert SESSION_COOKIE not in resp.cookies

    async def test_unknown_email_renders_error_inline(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/login",
            data={"email": "ninguem@nada.com", "password": "qualquer"},
        )
        assert resp.status_code == 200
        assert "incorret" in resp.text.lower()

    async def test_inactive_user_cannot_login(
        self, client: AsyncClient, db_session: AsyncSession, sample_brand: object
    ) -> None:
        from catalogflow.web.user_service import hash_password

        brand_id = sample_brand.id  # type: ignore[attr-defined]
        user = WebUser(
            brand_id=brand_id,
            email="pendente@oasis.com.br",
            name="Pendente",
            password_hash=hash_password("senha-pendente-1"),
            role="operator",
            is_active=False,
        )
        db_session.add(user)
        await db_session.commit()
        resp = await client.post(
            "/login",
            data={"email": "pendente@oasis.com.br", "password": "senha-pendente-1"},
        )
        assert resp.status_code == 200
        assert "incorret" in resp.text.lower()
        assert SESSION_COOKIE not in resp.cookies

    async def test_rate_limit_blocks_after_5_failures(
        self, client: AsyncClient, sample_user: WebUser
    ) -> None:
        del sample_user
        for _ in range(5):
            await client.post(
                "/login",
                data={"email": SAMPLE_USER_EMAIL, "password": "errada"},
            )
        resp = await client.post(
            "/login",
            data={"email": SAMPLE_USER_EMAIL, "password": SAMPLE_USER_PASSWORD},
        )
        # 6ª tentativa cai no rate-limit antes de checar a senha.
        assert resp.status_code == 429
        assert "Muitas tentativas" in resp.text


class TestRootRedirect:
    async def test_root_without_session_redirects_to_login(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_root_with_invalid_cookie_redirects_to_login(self, client: AsyncClient) -> None:
        client.cookies.set(SESSION_COOKIE, "obviously-not-a-valid-token")
        resp = await client.get("/")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_root_with_valid_session_redirects_to_dashboard(
        self,
        client: AsyncClient,
        sample_user: WebUser,
    ) -> None:
        del sample_user
        login = await client.post(
            "/login",
            data={"email": SAMPLE_USER_EMAIL, "password": SAMPLE_USER_PASSWORD},
        )
        assert login.status_code == 302
        assert SESSION_COOKIE in client.cookies

        resp = await client.get("/")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"


class TestLogout:
    async def test_logout_clears_cookie_and_redirects(
        self,
        client: AsyncClient,
        sample_user: WebUser,
    ) -> None:
        del sample_user
        login = await client.post(
            "/login",
            data={"email": SAMPLE_USER_EMAIL, "password": SAMPLE_USER_PASSWORD},
        )
        assert login.status_code == 302
        assert SESSION_COOKIE in client.cookies

        resp = await client.get("/logout")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

        set_cookie = resp.headers.get("set-cookie", "")
        assert SESSION_COOKIE in set_cookie
        normalized = set_cookie.lower()
        assert (
            "max-age=0" in normalized
            or "expires=thu, 01 jan 1970" in normalized
            or 'cf_session=""' in normalized
            or "cf_session=;" in normalized
        )


class TestForgotPassword:
    async def test_get_forgot_renders_form(self, client: AsyncClient) -> None:
        resp = await client.get("/forgot-password")
        assert resp.status_code == 200
        assert 'name="email"' in resp.text
        assert "Recuperar acesso" in resp.text

    async def test_post_forgot_always_returns_confirmation(self, client: AsyncClient) -> None:
        """Mesma resposta para email existente vs inexistente — sem oracle."""
        resp = await client.post(
            "/forgot-password",
            data={"email": "ninguem@nada.com"},
        )
        assert resp.status_code == 200
        assert "se houver" in resp.text.lower() or "enviamos" in resp.text.lower()

    async def test_post_forgot_creates_magic_link_for_real_user(
        self,
        client: AsyncClient,
        sample_user: WebUser,
        db_session: AsyncSession,
    ) -> None:
        from sqlalchemy import select

        from catalogflow.modules.auth.models import MagicLink

        resp = await client.post(
            "/forgot-password",
            data={"email": SAMPLE_USER_EMAIL},
        )
        assert resp.status_code == 200
        link = await db_session.scalar(select(MagicLink).where(MagicLink.user_id == sample_user.id))
        assert link is not None
        assert link.used_at is None


class TestMagicLinkConsume:
    async def test_invalid_token_renders_error_page(self, client: AsyncClient) -> None:
        resp = await client.get("/magic-link/totally-invalid")
        assert resp.status_code == 400
        assert "Link inválido" in resp.text

    async def test_valid_token_creates_session(
        self,
        client: AsyncClient,
        sample_user: WebUser,
        db_session: AsyncSession,
    ) -> None:
        from catalogflow.web.user_service import WebUserService

        service = WebUserService(db_session)
        ok = await service.send_magic_link(SAMPLE_USER_EMAIL)
        assert ok
        await db_session.commit()

        from sqlalchemy import select

        from catalogflow.modules.auth.models import MagicLink

        link = await db_session.scalar(select(MagicLink).where(MagicLink.user_id == sample_user.id))
        assert link is not None
        token = link.token

        resp = await client.get(f"/magic-link/{token}")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"
        assert SESSION_COOKIE in resp.cookies


class TestRegister:
    async def test_get_register_renders_form(self, client: AsyncClient) -> None:
        resp = await client.get("/register")
        assert resp.status_code == 200
        assert 'name="name"' in resp.text
        assert 'name="email"' in resp.text
        assert 'name="password"' in resp.text

    async def test_post_register_creates_pending_user(
        self,
        client: AsyncClient,
        sample_brand: object,
        db_session: AsyncSession,
    ) -> None:
        del sample_brand  # fixture só precisa criar a brand
        resp = await client.post(
            "/register",
            data={
                "name": "Nova Usuária",
                "email": "nova@oasis.com.br",
                "password": "senha-nova-123",
            },
        )
        assert resp.status_code == 200
        assert "Recebemos seu pedido" in resp.text

        from sqlalchemy import select

        user = await db_session.scalar(select(WebUser).where(WebUser.email == "nova@oasis.com.br"))
        assert user is not None
        assert user.is_active is False
        assert user.role == "operator"

    async def test_post_register_rejects_duplicate_email(
        self,
        client: AsyncClient,
        sample_user: WebUser,
    ) -> None:
        del sample_user
        resp = await client.post(
            "/register",
            data={
                "name": "Outro",
                "email": SAMPLE_USER_EMAIL,
                "password": "senha-outro-123",
            },
        )
        assert resp.status_code == 400
        assert "Já existe" in resp.text


class TestAdminUsers:
    async def test_admin_panel_requires_admin_role(
        self,
        client: AsyncClient,
        sample_user: WebUser,
    ) -> None:
        del sample_user
        # Login como operator
        await client.post(
            "/login",
            data={"email": SAMPLE_USER_EMAIL, "password": SAMPLE_USER_PASSWORD},
        )
        resp = await client.get("/admin/users")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"

    async def test_admin_panel_lists_pending_users(
        self,
        client: AsyncClient,
        sample_admin: WebUser,
        db_session: AsyncSession,
    ) -> None:
        from catalogflow.web.user_service import hash_password

        pending = WebUser(
            brand_id=sample_admin.brand_id,
            email="pendente2@oasis.com.br",
            name="Pendente Dois",
            password_hash=hash_password("uma-senha-aqui"),
            role="operator",
            is_active=False,
        )
        db_session.add(pending)
        await db_session.commit()

        await client.post(
            "/login",
            data={"email": SAMPLE_ADMIN_EMAIL, "password": SAMPLE_ADMIN_PASSWORD},
        )
        resp = await client.get("/admin/users")
        assert resp.status_code == 200
        assert "Pendente Dois" in resp.text
        assert "pendente2@oasis.com.br" in resp.text

    async def test_admin_approve_marks_active(
        self,
        client: AsyncClient,
        sample_admin: WebUser,
        db_session: AsyncSession,
    ) -> None:
        from catalogflow.web.user_service import hash_password

        target = WebUser(
            brand_id=sample_admin.brand_id,
            email="alvo@oasis.com.br",
            name="Alvo",
            password_hash=hash_password("password-aqui"),
            role="operator",
            is_active=False,
        )
        db_session.add(target)
        await db_session.commit()
        await db_session.refresh(target)

        await client.post(
            "/login",
            data={"email": SAMPLE_ADMIN_EMAIL, "password": SAMPLE_ADMIN_PASSWORD},
        )
        resp = await client.post(f"/admin/users/{target.id}/approve")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/admin/users"

        await db_session.refresh(target)
        assert target.is_active is True


@pytest.fixture(autouse=True)
def _isolate_email_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garante que os testes nunca chamam a SDK do Resend.

    O `EmailService` cai em modo dev quando `resend_api_key` é vazio
    (default da `Settings()` em test) — mas reforçamos com monkeypatch
    para defender contra qualquer config local.
    """

    def _noop(*args: object, **kwargs: object) -> object:
        return {"id": "test"}

    import resend

    monkeypatch.setattr(resend.Emails, "send", _noop)
