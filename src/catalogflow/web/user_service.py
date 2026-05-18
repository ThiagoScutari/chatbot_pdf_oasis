"""Serviço de usuários web — autenticação, magic-link e aprovação.

Encapsula toda a regra de negócio do fluxo de auth com email+senha:
- Hash de senha via bcrypt (lib `bcrypt` direta — passlib não é
  compatível com bcrypt>=4.1, então pulamos o wrapper)
- Geração e verificação de magic-link (TTL 15 min, single-use)
- Rate-limit por email (5 falhas em 5 min → bloqueio)
- Aprovação/recusa por admin

Decisões:
- `bcrypt.gensalt()` usa rounds=12 por default — custo ~250ms num
  M1, ok para login esporádico.
- Comparação de magic-link em texto puro é segura: o token é
  `secrets.token_urlsafe(32)` (256 bits de entropia), expira em 15 min,
  uso único e nunca é exposto fora do email do dono.
- Rate-limit usa só DB (sem Redis): a janela é pequena (5 min) e a
  consulta filtra por índice composto `(identifier, attempted_at)`.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import bcrypt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth.models import LoginAttempt, MagicLink, WebUser
from catalogflow.shared.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from catalogflow.web.email_service import EmailService

logger = logging.getLogger(__name__)

# Janela móvel do rate-limit + nº de falhas tolerado.
RATE_LIMIT_WINDOW: Final[timedelta] = timedelta(minutes=5)
RATE_LIMIT_MAX_FAILURES: Final[int] = 5

MAGIC_LINK_TTL: Final[timedelta] = timedelta(minutes=15)


def hash_password(plain: str) -> str:
    """Hash bcrypt da senha. Retorna o digest pronto para persistir."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Comparação constant-time da senha contra o hash bcrypt armazenado.

    `bcrypt.checkpw` é constant-time. Em caso de hash malformado (ex:
    string vazia ou corrompida no banco), levanta `ValueError` — tratamos
    como senha inválida em vez de propagar a exceção.
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


class WebUserService:
    """Operações de domínio sobre `WebUser` + `MagicLink` + `LoginAttempt`."""

    def __init__(self, session: AsyncSession, email_service: EmailService | None = None) -> None:
        self._session = session
        self._email = email_service or EmailService()

    # ── Cadastro / aprovação ─────────────────────

    async def request_access(
        self,
        *,
        brand_id: UUID,
        name: str,
        email: str,
        password: str,
    ) -> WebUser:
        """Cria um `WebUser` `is_active=False` aguardando aprovação.

        Valida unicidade de email. Hash da senha é feito agora mesmo —
        a senha em texto puro nunca persiste, nem ao longo do fluxo de
        aprovação.
        """
        norm = _normalize_email(email)
        if not norm or "@" not in norm:
            raise ValidationError("Email inválido.", code="INVALID_EMAIL")
        if len(password) < 8:
            raise ValidationError(
                "Senha precisa ter ao menos 8 caracteres.",
                code="WEAK_PASSWORD",
            )

        existing = await self._session.scalar(select(WebUser).where(WebUser.email == norm))
        if existing is not None:
            raise ConflictError(
                "Já existe um cadastro com este email.",
                code="EMAIL_TAKEN",
            )

        user = WebUser(
            brand_id=brand_id,
            email=norm,
            name=name.strip(),
            password_hash=hash_password(password),
            role="operator",
            is_active=False,
        )
        self._session.add(user)
        await self._session.flush()

        # Notifica o admin — best-effort, falha não bloqueia o cadastro.
        self._email.send_access_request(requester_name=user.name, requester_email=user.email)
        return user

    async def approve_user(self, user_id: UUID) -> WebUser:
        """Marca o usuário como ativo e dispara email de boas-vindas."""
        user = await self._get_user_or_raise(user_id)
        if user.is_active:
            return user
        user.is_active = True
        await self._session.flush()
        self._email.send_access_approved(to_email=user.email, name=user.name)
        return user

    async def deny_user(self, user_id: UUID) -> WebUser:
        """Recusa um cadastro pendente: envia email e remove o registro.

        Optei por DELETE (e não soft-delete) porque o usuário pode tentar
        de novo com o mesmo email mais tarde e estar lá um registro
        `is_active=False` indefinido seria pior UX.
        """
        user = await self._get_user_or_raise(user_id)
        if user.is_active:
            raise ConflictError(
                "Não é possível recusar um usuário já ativo.",
                code="USER_ALREADY_ACTIVE",
            )
        email = user.email
        name = user.name
        await self._session.delete(user)
        await self._session.flush()
        self._email.send_access_denied(to_email=email, name=name)
        # Devolvemos uma "cópia" desanexada apenas com os campos lidos —
        # o caller só precisa do email/name para logs/feedback.
        return WebUser(id=user_id, brand_id=user.brand_id, email=email, name=name)

    async def list_pending_users(self, brand_id: UUID) -> list[WebUser]:
        """Pedidos de acesso aguardando aprovação para a brand."""
        result = await self._session.scalars(
            select(WebUser)
            .where(WebUser.brand_id == brand_id, WebUser.is_active.is_(False))
            .order_by(WebUser.created_at.desc())
        )
        return list(result.all())

    async def list_active_users(self, brand_id: UUID) -> list[WebUser]:
        """Usuários já aprovados (para o painel admin enxergar a base)."""
        result = await self._session.scalars(
            select(WebUser)
            .where(WebUser.brand_id == brand_id, WebUser.is_active.is_(True))
            .order_by(WebUser.created_at.desc())
        )
        return list(result.all())

    # ── Login senha ──────────────────────────────

    async def check_rate_limit(self, email: str) -> bool:
        """Retorna `True` se o email ainda PODE tentar logar.

        Conta falhas dentro da janela móvel. Um sucesso recente zera
        a contagem (qualquer login bem-sucedido nos últimos 5 min
        considera o ator "limpo").
        """
        norm = _normalize_email(email)
        since = _now() - RATE_LIMIT_WINDOW
        rows = await self._session.scalars(
            select(LoginAttempt)
            .where(
                LoginAttempt.identifier == norm,
                LoginAttempt.attempted_at >= since,
            )
            .order_by(LoginAttempt.attempted_at.desc())
        )
        attempts = list(rows.all())
        if any(a.success for a in attempts):
            return True
        return len(attempts) < RATE_LIMIT_MAX_FAILURES

    async def authenticate(self, email: str, password: str) -> WebUser:
        """Autentica por email+senha. Registra tentativa em `login_attempts`.

        Levanta `AuthenticationError` em qualquer falha — mensagem
        genérica para não vazar diferença entre "email inexistente",
        "senha errada" e "usuário inativo".
        """
        norm = _normalize_email(email)
        user = await self._session.scalar(select(WebUser).where(WebUser.email == norm))
        ok = (
            user is not None
            and user.is_active
            and user.password_hash is not None
            and verify_password(password, user.password_hash)
        )
        await self._record_attempt(norm, success=ok)
        if not ok or user is None:
            raise AuthenticationError(
                "Email ou senha incorretos.",
                code="INVALID_CREDENTIALS",
            )
        return user

    async def _record_attempt(self, identifier: str, *, success: bool) -> None:
        self._session.add(LoginAttempt(identifier=identifier, success=success))
        await self._session.flush()

    # ── Magic link ───────────────────────────────

    async def send_magic_link(self, email: str) -> bool:
        """Gera um magic-link e envia. Retorna `True` se o usuário existe.

        Sempre devolvemos uma resposta vaga ao chamador da rota (não
        confirma se o email existe) — esta função é interna; é o router
        que omite a confirmação ao browser.
        """
        norm = _normalize_email(email)
        user = await self._session.scalar(
            select(WebUser).where(WebUser.email == norm, WebUser.is_active.is_(True))
        )
        if user is None:
            return False
        token = secrets.token_urlsafe(32)
        link = MagicLink(
            user_id=user.id,
            token=token,
            expires_at=_now() + MAGIC_LINK_TTL,
        )
        self._session.add(link)
        await self._session.flush()
        self._email.send_magic_link(to_email=user.email, name=user.name, token=token)
        return True

    async def verify_magic_link(self, token: str) -> WebUser:
        """Consome um magic-link válido e retorna o usuário associado.

        Marca o link como usado dentro da mesma transação — race entre
        dois cliques é resolvido pelo `used_at` ser checado antes do
        update (UPDATE ... WHERE used_at IS NULL retornaria 0 rows na
        segunda chamada, mas SQLAlchemy core puro não nos dá isso; em
        vez disso, fazemos a leitura+escrita na mesma transação que o
        chamador commitará).
        """
        link = await self._session.scalar(select(MagicLink).where(MagicLink.token == token))
        if link is None:
            raise NotFoundError("Link inválido ou expirado.", code="MAGIC_LINK_INVALID")
        if link.used_at is not None:
            raise AuthenticationError(
                "Este link já foi usado.",
                code="MAGIC_LINK_USED",
            )
        if link.expires_at < _now():
            raise AuthenticationError(
                "Link expirado. Solicite um novo.",
                code="MAGIC_LINK_EXPIRED",
            )
        user = await self._get_user_or_raise(link.user_id)
        if not user.is_active:
            raise AuthenticationError(
                "Sua conta ainda não foi aprovada.",
                code="USER_INACTIVE",
            )
        link.used_at = _now()
        await self._session.flush()
        return user

    async def reset_password(self, user_id: UUID, new_password: str) -> WebUser:
        """Define nova senha para um usuário (usado após magic-link)."""
        if len(new_password) < 8:
            raise ValidationError(
                "Senha precisa ter ao menos 8 caracteres.",
                code="WEAK_PASSWORD",
            )
        user = await self._get_user_or_raise(user_id)
        user.password_hash = hash_password(new_password)
        await self._session.flush()
        return user

    # ── Resolver de usuário ──────────────────────

    async def get_by_id(self, user_id: UUID) -> WebUser | None:
        result: WebUser | None = await self._session.scalar(
            select(WebUser).where(WebUser.id == user_id)
        )
        return result

    async def _get_user_or_raise(self, user_id: UUID) -> WebUser:
        user = await self.get_by_id(user_id)
        if user is None:
            raise NotFoundError("Usuário não encontrado.", code="USER_NOT_FOUND")
        return user

    # ── Manutenção ───────────────────────────────

    async def purge_expired_magic_links(self) -> int:
        """Apaga links expirados há mais de 24h. Retorna o nº removido.

        Não é chamado em runtime de request — é util para uma task
        Celery periódica futura ou para o script de seed.
        """
        cutoff = _now() - timedelta(hours=24)
        result = await self._session.execute(delete(MagicLink).where(MagicLink.expires_at < cutoff))
        return result.rowcount or 0
