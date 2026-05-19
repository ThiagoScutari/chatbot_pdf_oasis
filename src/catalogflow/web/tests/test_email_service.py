"""Testes para `web/email_service.py` — wrapper Resend fail-soft.

Cobre os 4 envios + modo dev + branch de erro.
A SDK do Resend (`resend.Emails.send`) é mockada via monkeypatch — nenhuma
chamada real é feita.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from catalogflow.infra.settings import Settings
from catalogflow.web.email_service import (
    EmailService,
    _envelope,
    _render_admin_notice_html,
    _render_approval_html,
    _render_denial_html,
    _render_magic_link_html,
    _strip_tags,
)

# ──────────────────────────────────────────────
#  Settings helpers
# ──────────────────────────────────────────────


def _settings_with_api_key(api_key: str = "re_test_key") -> Settings:
    """Settings com api_key — modo produção (chama resend.Emails.send)."""
    return Settings(
        database_url="postgresql+asyncpg://x:x@localhost:5432/x",
        secret_key=SecretStr("secret"),
        internal_secret=SecretStr("internal"),
        aws_access_key_id=SecretStr("x"),
        aws_secret_access_key=SecretStr("x"),
        resend_api_key=SecretStr(api_key),
        public_base_url="https://catalogo.example.com",
        admin_email="admin@example.com",
        resend_from_email="no-reply@example.com",
    )


def _settings_dev_mode() -> Settings:
    """Settings sem api_key — modo dev (não chama Resend)."""
    return Settings(
        database_url="postgresql+asyncpg://x:x@localhost:5432/x",
        secret_key=SecretStr("secret"),
        internal_secret=SecretStr("internal"),
        aws_access_key_id=SecretStr("x"),
        aws_secret_access_key=SecretStr("x"),
        resend_api_key=SecretStr(""),
        public_base_url="https://catalogo.example.com",
        admin_email="admin@example.com",
    )


# ──────────────────────────────────────────────
#  Modo dev — não chama Resend
# ──────────────────────────────────────────────


class TestDevModeSkipsResend:
    def test_send_magic_link_does_not_call_resend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sem api_key, magic_link retorna True sem chamar Resend."""
        called = {"count": 0}

        def fake_send(params: dict[str, Any]) -> None:
            called["count"] += 1

        monkeypatch.setattr("resend.Emails.send", fake_send)
        service = EmailService(settings=_settings_dev_mode())
        result = service.send_magic_link("user@example.com", "Ana", "tok-abc")
        assert result is True
        assert called["count"] == 0

    def test_dev_mode_path_for_all_4_methods(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Os 4 métodos públicos retornam True em modo dev sem chamar SDK."""

        def fake_send(params: dict[str, Any]) -> None:
            raise AssertionError("resend.Emails.send não deveria ser chamado em dev")

        monkeypatch.setattr("resend.Emails.send", fake_send)
        service = EmailService(settings=_settings_dev_mode())
        assert service.send_magic_link("a@b.com", "Ana", "tok") is True
        assert service.send_access_approved("a@b.com", "Ana") is True
        assert service.send_access_denied("a@b.com", "Ana") is True
        assert service.send_access_request("Ana", "a@b.com") is True


# ──────────────────────────────────────────────
#  Modo produção — chama Resend (mockado)
# ──────────────────────────────────────────────


class TestProductionModeCallsResend:
    def test_send_magic_link_calls_resend_with_link(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Magic link compõe a URL e passa para resend.Emails.send."""
        captured: list[dict[str, Any]] = []

        def fake_send(params: dict[str, Any]) -> None:
            captured.append(params)

        monkeypatch.setattr("resend.Emails.send", fake_send)
        service = EmailService(settings=_settings_with_api_key())
        ok = service.send_magic_link("user@example.com", "Ana", "tok-123")
        assert ok is True
        assert len(captured) == 1
        params = captured[0]
        assert params["to"] == ["user@example.com"]
        assert "Seu link de acesso" in params["subject"]
        assert "https://catalogo.example.com/magic-link/tok-123" in params["html"]
        assert "Ana" in params["html"]

    def test_send_access_approved_includes_login_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Aprovação inclui link para /login."""
        captured: list[dict[str, Any]] = []
        monkeypatch.setattr("resend.Emails.send", lambda p: captured.append(p))
        service = EmailService(settings=_settings_with_api_key())
        ok = service.send_access_approved("user@example.com", "Bruno")
        assert ok is True
        assert "https://catalogo.example.com/login" in captured[0]["html"]
        assert "aprovado" in captured[0]["subject"].lower()

    def test_send_access_denied_does_not_include_login_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recusa não tem link de login (usuário não pode acessar)."""
        captured: list[dict[str, Any]] = []
        monkeypatch.setattr("resend.Emails.send", lambda p: captured.append(p))
        service = EmailService(settings=_settings_with_api_key())
        ok = service.send_access_denied("user@example.com", "Carla")
        assert ok is True
        assert "/login" not in captured[0]["html"]
        assert "Carla" in captured[0]["html"]

    def test_send_access_request_goes_to_admin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Notificação de pedido vai ao admin_email, não ao requester."""
        captured: list[dict[str, Any]] = []
        monkeypatch.setattr("resend.Emails.send", lambda p: captured.append(p))
        service = EmailService(settings=_settings_with_api_key())
        ok = service.send_access_request("Diana", "diana@cliente.com")
        assert ok is True
        params = captured[0]
        assert params["to"] == ["admin@example.com"]
        assert "Diana" in params["html"]
        assert "diana@cliente.com" in params["html"]
        assert "/admin/users" in params["html"]


# ──────────────────────────────────────────────
#  Fail-soft — exceção da SDK não propaga
# ──────────────────────────────────────────────


class TestFailSoft:
    def test_resend_exception_returns_false_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Erro da SDK Resend → retorno False, nunca propaga exceção."""

        def fake_send(params: dict[str, Any]) -> None:
            raise RuntimeError("network down")

        monkeypatch.setattr("resend.Emails.send", fake_send)
        service = EmailService(settings=_settings_with_api_key())
        # Sem try/except aqui — se a exceção vazasse, o teste falharia naturalmente.
        result = service.send_magic_link("user@example.com", "Eva", "tok")
        assert result is False


# ──────────────────────────────────────────────
#  Templates HTML — smoke tests dos renderers internos
# ──────────────────────────────────────────────


class TestHtmlRenderers:
    def test_envelope_wraps_body_with_footer(self) -> None:
        """`_envelope` injeta o body e adiciona o rodapé padrão."""
        html = _envelope("<p>oi</p>")
        assert "<p>oi</p>" in html
        assert "CatalogFlow" in html
        assert "Oasis Resortwear" in html

    def test_magic_link_renders_name_and_link(self) -> None:
        html = _render_magic_link_html(name="Fátima", link="https://x/y")
        assert "Fátima" in html
        assert "https://x/y" in html
        assert "Entrar na minha conta" in html

    def test_approval_renders_login_url(self) -> None:
        html = _render_approval_html(name="Gabriela", login_url="https://x/login")
        assert "Gabriela" in html
        assert "https://x/login" in html

    def test_denial_does_not_include_login_url(self) -> None:
        html = _render_denial_html(name="Helena")
        assert "Helena" in html
        assert "/login" not in html

    def test_admin_notice_includes_review_url_and_requester(self) -> None:
        html = _render_admin_notice_html(
            requester_name="Iara",
            requester_email="iara@x.com",
            review_url="https://x/admin/users",
        )
        assert "Iara" in html
        assert "iara@x.com" in html
        assert "https://x/admin/users" in html


class TestStripTags:
    def test_removes_simple_tags(self) -> None:
        """Texto entre `<...>` é descartado; o resto sobrevive."""
        assert _strip_tags("<p>oi</p>") == "oi"

    def test_handles_nested_tags(self) -> None:
        """Tags aninhadas: cada `<` reentra no modo `in_tag`."""
        assert _strip_tags("<div><span>oi</span></div>") == "oi"

    def test_preserves_text_without_tags(self) -> None:
        """Sem tags, retorna a string original."""
        assert _strip_tags("hello world") == "hello world"

    def test_handles_unclosed_tag(self) -> None:
        """Tag aberta sem fechamento: tudo após `<` é descartado."""
        assert _strip_tags("hello <world") == "hello "
