"""Testes do `WebUserService` — regras de cadastro, login e magic-link.

Cobre todos os branches que o teste de rotas (test_web_auth.py) não
exercita, em particular os caminhos de erro (`ValidationError`,
`ConflictError`, `NotFoundError`, `AuthenticationError`).

Estratégia: usa `db_session` real (testcontainers Postgres) e uma
`EmailService` fake injetada por construtor que registra envios sem
chamar SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import (
    Brand,
    LoginAttempt,
    MagicLink,
    WebUser,
)
from catalogflow.shared.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from catalogflow.web.user_service import (
    MAGIC_LINK_TTL,
    RATE_LIMIT_MAX_FAILURES,
    WebUserService,
    hash_password,
    verify_password,
)

# ──────────────────────────────────────────────
#  EmailService fake — registra chamadas
# ──────────────────────────────────────────────


@dataclass
class FakeEmailService:
    """Substitui o `EmailService` real: registra chamadas sem rede."""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def send_magic_link(self, *, to_email: str, name: str, token: str) -> bool:
        self.calls.append(("magic_link", {"to": to_email, "name": name, "token": token}))
        return True

    def send_access_approved(self, *, to_email: str, name: str) -> bool:
        self.calls.append(("approved", {"to": to_email, "name": name}))
        return True

    def send_access_denied(self, *, to_email: str, name: str) -> bool:
        self.calls.append(("denied", {"to": to_email, "name": name}))
        return True

    def send_access_request(self, *, requester_name: str, requester_email: str) -> bool:
        self.calls.append(("request", {"name": requester_name, "email": requester_email}))
        return True


@pytest.fixture
def fake_email() -> FakeEmailService:
    return FakeEmailService()


@pytest.fixture
async def brand(db_session: AsyncSession) -> Brand:
    b = await auth_service.create_brand(db_session, slug="us-test", name="UserService Test")
    await db_session.commit()
    return b


def _make_service(db_session: AsyncSession, fake_email: FakeEmailService) -> WebUserService:
    return WebUserService(session=db_session, email_service=fake_email)  # type: ignore[arg-type]


# ──────────────────────────────────────────────
#  Helpers puros
# ──────────────────────────────────────────────


class TestPasswordHelpers:
    def test_hash_and_verify_round_trip(self) -> None:
        """hash_password + verify_password aceita a senha original."""
        h = hash_password("minha-senha-123")
        assert verify_password("minha-senha-123", h) is True

    def test_verify_rejects_wrong_password(self) -> None:
        """Senha errada → False."""
        h = hash_password("certa-1234")
        assert verify_password("errada-9999", h) is False

    def test_verify_password_handles_malformed_hash(self) -> None:
        """Hash corrompido faz bcrypt levantar ValueError → tratado como falha."""
        assert verify_password("qualquer", "hash-corrompido-totalmente") is False


# ──────────────────────────────────────────────
#  request_access — validações e happy path
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestRequestAccess:
    async def test_creates_inactive_user_and_notifies_admin(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """User criado com `is_active=False`; admin recebe notificação."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id,
            name="Ana",
            email="ANA@example.com  ",
            password="senha-muito-segura",
        )
        assert user.is_active is False
        assert user.email == "ana@example.com"
        assert user.role == "operator"
        assert len(fake_email.calls) == 1
        assert fake_email.calls[0][0] == "request"

    async def test_invalid_email_raises_validation_error(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """Email sem `@` levanta ValidationError code=INVALID_EMAIL."""
        svc = _make_service(db_session, fake_email)
        with pytest.raises(ValidationError) as exc_info:
            await svc.request_access(
                brand_id=brand.id, name="A", email="sem-arroba", password="senha-segura"
            )
        assert exc_info.value.code == "INVALID_EMAIL"

    async def test_weak_password_raises_validation_error(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """Senha < 8 chars levanta ValidationError code=WEAK_PASSWORD."""
        svc = _make_service(db_session, fake_email)
        with pytest.raises(ValidationError) as exc_info:
            await svc.request_access(brand_id=brand.id, name="A", email="a@b.com", password="curta")
        assert exc_info.value.code == "WEAK_PASSWORD"

    async def test_duplicate_email_raises_conflict_error(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """Cadastro com email já existente → ConflictError EMAIL_TAKEN."""
        svc = _make_service(db_session, fake_email)
        await svc.request_access(
            brand_id=brand.id, name="A", email="dup@example.com", password="senha-12345"
        )
        with pytest.raises(ConflictError) as exc_info:
            await svc.request_access(
                brand_id=brand.id,
                name="Outro",
                email="dup@example.com",
                password="outra-senha-123",
            )
        assert exc_info.value.code == "EMAIL_TAKEN"


# ──────────────────────────────────────────────
#  approve_user / deny_user
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestApproveAndDeny:
    async def test_approve_activates_user_and_sends_email(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """approve_user: is_active=True + envia email de aprovação."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="B", email="b@x.com", password="senha-segura1"
        )
        fake_email.calls.clear()
        approved = await svc.approve_user(user.id)
        assert approved.is_active is True
        assert any(c[0] == "approved" for c in fake_email.calls)

    async def test_approve_already_active_is_idempotent(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """approve_user em user já ativo: retorna user sem reenviar email."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="C", email="c@x.com", password="senha-segura1"
        )
        await svc.approve_user(user.id)
        fake_email.calls.clear()
        again = await svc.approve_user(user.id)
        assert again.is_active is True
        assert fake_email.calls == []  # nenhum email novo

    async def test_approve_unknown_user_raises_not_found(
        self,
        db_session: AsyncSession,
        fake_email: FakeEmailService,
    ) -> None:
        """approve_user em ID inexistente → NotFoundError USER_NOT_FOUND."""
        svc = _make_service(db_session, fake_email)
        with pytest.raises(NotFoundError) as exc_info:
            await svc.approve_user(uuid4())
        assert exc_info.value.code == "USER_NOT_FOUND"

    async def test_deny_inactive_user_deletes_and_sends_email(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """deny_user em pendente: DELETE + email de recusa."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="D", email="d@x.com", password="senha-segura1"
        )
        fake_email.calls.clear()
        copy = await svc.deny_user(user.id)
        assert copy.email == "d@x.com"
        # Linha foi removida
        check = await db_session.scalar(select(WebUser).where(WebUser.id == user.id))
        assert check is None
        assert any(c[0] == "denied" for c in fake_email.calls)

    async def test_deny_active_user_raises_conflict(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """deny_user num user já aprovado → ConflictError USER_ALREADY_ACTIVE."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="E", email="e@x.com", password="senha-segura1"
        )
        await svc.approve_user(user.id)
        with pytest.raises(ConflictError) as exc_info:
            await svc.deny_user(user.id)
        assert exc_info.value.code == "USER_ALREADY_ACTIVE"


# ──────────────────────────────────────────────
#  Listagens
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestListings:
    async def test_list_pending_and_active(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """list_pending_users e list_active_users devolvem listas filtradas."""
        svc = _make_service(db_session, fake_email)
        u1 = await svc.request_access(
            brand_id=brand.id, name="A", email="a1@x.com", password="senha-segura1"
        )
        u2 = await svc.request_access(
            brand_id=brand.id, name="B", email="a2@x.com", password="senha-segura2"
        )
        await svc.approve_user(u2.id)

        pending = await svc.list_pending_users(brand.id)
        active = await svc.list_active_users(brand.id)
        assert [u.id for u in pending] == [u1.id]
        assert [u.id for u in active] == [u2.id]


# ──────────────────────────────────────────────
#  Rate limit
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestCheckRateLimit:
    async def test_allows_under_threshold(
        self,
        db_session: AsyncSession,
        fake_email: FakeEmailService,
    ) -> None:
        """Poucas falhas (abaixo do limite) → permite tentativa."""
        svc = _make_service(db_session, fake_email)
        for _ in range(RATE_LIMIT_MAX_FAILURES - 1):
            db_session.add(LoginAttempt(identifier="abc@x.com", success=False))
        await db_session.flush()
        assert await svc.check_rate_limit("abc@x.com") is True

    async def test_blocks_after_threshold_of_failures(
        self,
        db_session: AsyncSession,
        fake_email: FakeEmailService,
    ) -> None:
        """5 falhas em 5min → bloqueia."""
        svc = _make_service(db_session, fake_email)
        for _ in range(RATE_LIMIT_MAX_FAILURES):
            db_session.add(LoginAttempt(identifier="boo@x.com", success=False))
        await db_session.flush()
        assert await svc.check_rate_limit("boo@x.com") is False

    async def test_recent_success_resets_rate_limit(
        self,
        db_session: AsyncSession,
        fake_email: FakeEmailService,
    ) -> None:
        """Um sucesso recente zera as falhas — segue podendo tentar."""
        svc = _make_service(db_session, fake_email)
        for _ in range(RATE_LIMIT_MAX_FAILURES):
            db_session.add(LoginAttempt(identifier="ok@x.com", success=False))
        db_session.add(LoginAttempt(identifier="ok@x.com", success=True))
        await db_session.flush()
        assert await svc.check_rate_limit("ok@x.com") is True


# ──────────────────────────────────────────────
#  authenticate
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestAuthenticate:
    async def test_valid_credentials_returns_user(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """Credenciais válidas → retorna user + grava attempt success=True."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="X", email="x@x.com", password="senha-segura1"
        )
        await svc.approve_user(user.id)
        got = await svc.authenticate("x@x.com", "senha-segura1")
        assert got.id == user.id
        # Attempt registrada
        attempts = list(
            await db_session.scalars(
                select(LoginAttempt).where(LoginAttempt.identifier == "x@x.com")
            )
        )
        assert any(a.success for a in attempts)

    async def test_wrong_password_raises_and_records_failure(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """Senha errada → AuthenticationError INVALID_CREDENTIALS."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="Y", email="y@x.com", password="senha-segura1"
        )
        await svc.approve_user(user.id)
        with pytest.raises(AuthenticationError) as exc_info:
            await svc.authenticate("y@x.com", "errada-1234")
        assert exc_info.value.code == "INVALID_CREDENTIALS"

    async def test_unknown_email_raises_same_error(
        self,
        db_session: AsyncSession,
        fake_email: FakeEmailService,
    ) -> None:
        """Email inexistente → mesma exceção (sem oracle)."""
        svc = _make_service(db_session, fake_email)
        with pytest.raises(AuthenticationError) as exc_info:
            await svc.authenticate("ninguem@x.com", "qualquer-coisa")
        assert exc_info.value.code == "INVALID_CREDENTIALS"

    async def test_inactive_user_cannot_authenticate(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """User existente mas `is_active=False` → AuthenticationError."""
        svc = _make_service(db_session, fake_email)
        await svc.request_access(
            brand_id=brand.id, name="Z", email="z@x.com", password="senha-segura1"
        )
        # Não aprovamos — user permanece inativo
        with pytest.raises(AuthenticationError):
            await svc.authenticate("z@x.com", "senha-segura1")


# ──────────────────────────────────────────────
#  Magic link — send + verify
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestMagicLink:
    async def test_send_magic_link_creates_token_for_active_user(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """send_magic_link: cria MagicLink + envia email."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="ML", email="ml@x.com", password="senha-segura1"
        )
        await svc.approve_user(user.id)
        fake_email.calls.clear()
        ok = await svc.send_magic_link("ml@x.com")
        assert ok is True
        link = await db_session.scalar(select(MagicLink).where(MagicLink.user_id == user.id))
        assert link is not None
        assert any(c[0] == "magic_link" for c in fake_email.calls)

    async def test_send_magic_link_returns_false_for_unknown_email(
        self,
        db_session: AsyncSession,
        fake_email: FakeEmailService,
    ) -> None:
        """Email sem user ativo → retorna False sem criar link."""
        svc = _make_service(db_session, fake_email)
        assert await svc.send_magic_link("ninguem@x.com") is False
        assert fake_email.calls == []

    async def test_verify_magic_link_consumes_token_and_returns_user(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """verify_magic_link: válido → marca used_at + devolve user."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="V", email="v@x.com", password="senha-segura1"
        )
        await svc.approve_user(user.id)
        await svc.send_magic_link("v@x.com")
        link = await db_session.scalar(select(MagicLink).where(MagicLink.user_id == user.id))
        assert link is not None
        got = await svc.verify_magic_link(link.token)
        assert got.id == user.id
        await db_session.refresh(link)
        assert link.used_at is not None

    async def test_verify_unknown_token_raises_not_found(
        self,
        db_session: AsyncSession,
        fake_email: FakeEmailService,
    ) -> None:
        """Token desconhecido → NotFoundError MAGIC_LINK_INVALID."""
        svc = _make_service(db_session, fake_email)
        with pytest.raises(NotFoundError) as exc_info:
            await svc.verify_magic_link("token-que-nao-existe")
        assert exc_info.value.code == "MAGIC_LINK_INVALID"

    async def test_verify_used_token_raises(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """Token já consumido → AuthenticationError MAGIC_LINK_USED."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="U", email="u@x.com", password="senha-segura1"
        )
        await svc.approve_user(user.id)
        await svc.send_magic_link("u@x.com")
        link = await db_session.scalar(select(MagicLink).where(MagicLink.user_id == user.id))
        assert link is not None
        await svc.verify_magic_link(link.token)
        with pytest.raises(AuthenticationError) as exc_info:
            await svc.verify_magic_link(link.token)
        assert exc_info.value.code == "MAGIC_LINK_USED"

    async def test_verify_expired_token_raises(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """Token expirado → AuthenticationError MAGIC_LINK_EXPIRED."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="E", email="exp@x.com", password="senha-segura1"
        )
        await svc.approve_user(user.id)
        # Cria link expirado direto, ignorando o helper.
        link = MagicLink(
            user_id=user.id,
            token="tok-expirado",
            expires_at=datetime.now(tz=UTC) - timedelta(seconds=10),
        )
        db_session.add(link)
        await db_session.flush()
        with pytest.raises(AuthenticationError) as exc_info:
            await svc.verify_magic_link("tok-expirado")
        assert exc_info.value.code == "MAGIC_LINK_EXPIRED"

    async def test_verify_inactive_user_raises(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """Token aponta para user que deixou de estar ativo → AuthenticationError."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="I", email="ina@x.com", password="senha-segura1"
        )
        # Criamos um link manualmente sem ativar o user.
        link = MagicLink(
            user_id=user.id,
            token="tok-inactive",
            expires_at=datetime.now(tz=UTC) + MAGIC_LINK_TTL,
        )
        db_session.add(link)
        await db_session.flush()
        with pytest.raises(AuthenticationError) as exc_info:
            await svc.verify_magic_link("tok-inactive")
        assert exc_info.value.code == "USER_INACTIVE"


# ──────────────────────────────────────────────
#  reset_password
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestResetPassword:
    async def test_resets_when_valid(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """reset_password atualiza o hash; verify_password autentica com a nova."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="R", email="r@x.com", password="senha-segura1"
        )
        await svc.reset_password(user.id, "nova-senha-forte-321")
        await db_session.refresh(user)
        assert user.password_hash is not None
        assert verify_password("nova-senha-forte-321", user.password_hash) is True

    async def test_reset_weak_password_raises(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """Senha < 8 chars no reset → ValidationError WEAK_PASSWORD."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="Q", email="q@x.com", password="senha-segura1"
        )
        with pytest.raises(ValidationError) as exc_info:
            await svc.reset_password(user.id, "curta")
        assert exc_info.value.code == "WEAK_PASSWORD"


# ──────────────────────────────────────────────
#  get_by_id + purge
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetByIdAndPurge:
    async def test_get_by_id_returns_none_when_missing(
        self,
        db_session: AsyncSession,
        fake_email: FakeEmailService,
    ) -> None:
        """get_by_id para UUID inexistente → None."""
        svc = _make_service(db_session, fake_email)
        assert await svc.get_by_id(uuid4()) is None

    async def test_purge_expired_magic_links_removes_old(
        self,
        db_session: AsyncSession,
        brand: Brand,
        fake_email: FakeEmailService,
    ) -> None:
        """purge_expired_magic_links remove links expirados há > 24h."""
        svc = _make_service(db_session, fake_email)
        user = await svc.request_access(
            brand_id=brand.id, name="P", email="p@x.com", password="senha-segura1"
        )
        now = datetime.now(tz=UTC)
        old = MagicLink(
            user_id=user.id,
            token="muito-antigo",
            expires_at=now - timedelta(hours=48),
        )
        recent = MagicLink(
            user_id=user.id,
            token="recente",
            expires_at=now + timedelta(minutes=10),
        )
        db_session.add_all([old, recent])
        await db_session.flush()
        removed = await svc.purge_expired_magic_links()
        assert removed == 1
        remaining = list(await db_session.scalars(select(MagicLink)))
        assert [m.token for m in remaining] == ["recente"]
