"""Email transacional via Resend.

Wrapper minimalista sobre a SDK do Resend que envia 3 emails:
- Magic link de acesso (`send_magic_link`)
- Aprovação de cadastro (`send_access_approved`)
- Recusa de cadastro (`send_access_denied`)
- Notificação ao admin de novo pedido (`send_access_request`)

Política de falha (fail-soft): qualquer erro de rede / 5xx do Resend é
logado e a função retorna `False`. Nunca propagamos exceção, porque o
chamador (rotas web) não pode quebrar o fluxo do usuário só porque o
provedor de email caiu.

Modo dev: quando `settings.resend_api_key` está vazio, o serviço loga o
conteúdo do email em INFO e retorna `True` sem chamar o Resend. Isso
permite rodar localmente sem credenciais.
"""

from __future__ import annotations

import logging
from typing import Any

import resend

from catalogflow.infra.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class EmailService:
    """Cliente Resend stateless. Recriar por request é barato (HTTP keepalive
    é gerenciado pela SDK)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        api_key = self._settings.resend_api_key.get_secret_value()
        self._dev_mode = not api_key
        if not self._dev_mode:
            # A SDK global é configurada por módulo — não há cliente por
            # instância. Setamos a cada `__init__` por idempotência (custo
            # zero: só um atributo de módulo).
            resend.api_key = api_key

    # ── API pública ──────────────────────────────

    def send_magic_link(self, to_email: str, name: str, token: str) -> bool:
        """Envia o link de login de uso único."""
        link = f"{self._settings.public_base_url}/magic-link/{token}"
        subject = "Seu link de acesso CatalogFlow"
        html = _render_magic_link_html(name=name, link=link)
        return self._send(to_email, subject, html)

    def send_access_approved(self, to_email: str, name: str) -> bool:
        """Notifica o usuário que o admin aprovou o acesso."""
        login_url = f"{self._settings.public_base_url}/login"
        subject = "Seu acesso ao CatalogFlow foi aprovado"
        html = _render_approval_html(name=name, login_url=login_url)
        return self._send(to_email, subject, html)

    def send_access_denied(self, to_email: str, name: str) -> bool:
        """Notifica o usuário que o admin recusou o acesso."""
        subject = "Sobre seu cadastro no CatalogFlow"
        html = _render_denial_html(name=name)
        return self._send(to_email, subject, html)

    def send_access_request(self, requester_name: str, requester_email: str) -> bool:
        """Avisa o admin que há um novo pedido de acesso pendente."""
        review_url = f"{self._settings.public_base_url}/admin/users"
        subject = f"Novo pedido de acesso: {requester_name}"
        html = _render_admin_notice_html(
            requester_name=requester_name,
            requester_email=requester_email,
            review_url=review_url,
        )
        return self._send(self._settings.admin_email, subject, html)

    # ── Internals ────────────────────────────────

    def _send(self, to_email: str, subject: str, html: str) -> bool:
        if self._dev_mode:
            logger.info(
                "email[dev] to=%s subject=%r body_preview=%r",
                to_email,
                subject,
                _strip_tags(html)[:200],
            )
            return True
        params: dict[str, Any] = {
            "from": self._settings.resend_from_email,
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
        try:
            # resend.Emails.send expects a SendParams TypedDict; nosso dict tem
            # as mesmas chaves mas mypy não consegue inferir a equivalência.
            resend.Emails.send(params)  # type: ignore[arg-type]
            logger.info("email sent to=%s subject=%r", to_email, subject)
            return True
        except Exception as exc:  # fail-soft: log e retorna False
            logger.warning(
                "email send failed to=%s subject=%r err=%s",
                to_email,
                subject,
                exc,
            )
            return False


# ──────────────────────────────────────────────
#  Templates HTML inline — kept simple
# ──────────────────────────────────────────────


def _envelope(body_html: str) -> str:
    return (
        '<div style="font-family:Inter,Arial,sans-serif;max-width:520px;'
        'margin:0 auto;padding:24px;color:#2A241F;">'
        f"{body_html}"
        '<p style="color:#7A6E65;font-size:12px;margin-top:32px;">'
        "CatalogFlow — Oasis Resortwear"
        "</p></div>"
    )


def _render_magic_link_html(*, name: str, link: str) -> str:
    body = (
        f"<h2 style=\"font-family:'Cormorant Garamond',Georgia,serif;\">Olá, {name}</h2>"
        "<p>Você solicitou um link de acesso ao CatalogFlow. Clique abaixo "
        "para entrar — o link expira em 15 minutos e funciona uma vez só.</p>"
        f'<p><a href="{link}" '
        'style="display:inline-block;background:#2A241F;color:#FAF8F5;'
        'padding:12px 24px;text-decoration:none;border-radius:4px;">'
        "Entrar na minha conta</a></p>"
        '<p style="color:#7A6E65;font-size:13px;">Se você não pediu este link, '
        "pode ignorar este email.</p>"
    )
    return _envelope(body)


def _render_approval_html(*, name: str, login_url: str) -> str:
    body = (
        f"<h2 style=\"font-family:'Cormorant Garamond',Georgia,serif;\">Bem-vinda, {name}</h2>"
        "<p>Seu acesso ao CatalogFlow foi aprovado. Você já pode entrar "
        "com seu email e senha.</p>"
        f'<p><a href="{login_url}" '
        'style="display:inline-block;background:#2A241F;color:#FAF8F5;'
        'padding:12px 24px;text-decoration:none;border-radius:4px;">'
        "Ir para o login</a></p>"
    )
    return _envelope(body)


def _render_denial_html(*, name: str) -> str:
    body = (
        f"<h2 style=\"font-family:'Cormorant Garamond',Georgia,serif;\">Olá, {name}</h2>"
        "<p>Agradecemos seu interesse no CatalogFlow. Infelizmente, neste "
        "momento não conseguimos aprovar seu cadastro.</p>"
        "<p>Se acredita que houve um engano, responda este email para "
        "conversarmos.</p>"
    )
    return _envelope(body)


def _render_admin_notice_html(*, requester_name: str, requester_email: str, review_url: str) -> str:
    body = (
        "<h2 style=\"font-family:'Cormorant Garamond',Georgia,serif;\">"
        "Novo pedido de acesso</h2>"
        f"<p><strong>{requester_name}</strong> ({requester_email}) "
        "solicitou acesso ao CatalogFlow.</p>"
        f'<p><a href="{review_url}" '
        'style="display:inline-block;background:#2A241F;color:#FAF8F5;'
        'padding:12px 24px;text-decoration:none;border-radius:4px;">'
        "Revisar pedidos pendentes</a></p>"
    )
    return _envelope(body)


def _strip_tags(html: str) -> str:
    """Conversão crua de HTML→texto para o log do modo dev — sem dependência."""
    out: list[str] = []
    in_tag = False
    for ch in html:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            out.append(ch)
    return "".join(out)
