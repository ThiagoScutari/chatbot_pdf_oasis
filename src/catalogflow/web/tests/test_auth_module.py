"""Testes do módulo `web/auth.py` — funções de sessão + dependências FastAPI.

As rotas HTTP já são exercitadas por `test_web_auth.py`. Aqui cobrimos
os caminhos internos de `verify_session` (branches de validação), as
funções de cookie em isolamento e as dependências FastAPI invocadas
diretamente como funções async (sem servidor).
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Response
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from catalogflow.modules.auth.models import ApiKey, Brand, WebUser
from catalogflow.web.auth import (
    _SALT,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    clear_session_cookie,
    create_session,
    mint_session_api_key,
    require_admin,
    require_session,
    require_session_api_key,
    require_session_brand,
    revoke_session_api_key,
    set_session_cookie,
    verify_session,
)

SECRET = "test-secret-not-for-production"


def _make_request(*, cookie_value: str | None = None) -> Request:
    """Constrói um starlette Request mínimo com (ou sem) cookie de sessão."""
    headers: list[tuple[bytes, bytes]] = []
    if cookie_value is not None:
        headers.append((b"cookie", f"{SESSION_COOKIE}={cookie_value}".encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
    }
    return Request(scope)


# ──────────────────────────────────────────────
#  verify_session — branches de validação
# ──────────────────────────────────────────────


class TestVerifySessionHappyPath:
    def test_round_trip_returns_uuid_and_key(self) -> None:
        """create_session → verify_session devolve (uuid, api_key)."""
        uid = uuid4()
        token = create_session(uid, "cf_plain_text_key", SECRET)
        decoded = verify_session(token, SECRET)
        assert decoded is not None
        assert decoded == (uid, "cf_plain_text_key")


class TestVerifySessionFailures:
    def test_bad_signature_returns_none(self) -> None:
        """Token assinado com outro segredo cai em BadSignature → None."""
        token = create_session(uuid4(), "k", "outro-segredo")
        assert verify_session(token, SECRET) is None

    def test_garbage_token_returns_none(self) -> None:
        """String que nem é um token válido também cai em BadSignature."""
        assert verify_session("totalmente-invalido", SECRET) is None

    def test_non_string_payload_returns_none(self) -> None:
        """Payload serializado como dict (não string) é rejeitado."""
        # Serializamos um dict diretamente — `loads` devolve dict, não str.
        ser = URLSafeTimedSerializer(secret_key=SECRET, salt=_SALT)
        token = ser.dumps({"u": str(uuid4()), "k": "k"})
        assert verify_session(token, SECRET) is None

    def test_invalid_json_inside_string_returns_none(self) -> None:
        """String que não é JSON válido cai em JSONDecodeError → None."""
        ser = URLSafeTimedSerializer(secret_key=SECRET, salt=_SALT)
        token = ser.dumps("isso aqui não é json")
        assert verify_session(token, SECRET) is None

    def test_json_not_dict_returns_none(self) -> None:
        """JSON válido mas que não é dict (ex: lista) é rejeitado."""
        ser = URLSafeTimedSerializer(secret_key=SECRET, salt=_SALT)
        token = ser.dumps(json.dumps([1, 2, 3]))
        assert verify_session(token, SECRET) is None

    def test_missing_uuid_field_returns_none(self) -> None:
        """Dict sem chave `u` é rejeitado."""
        ser = URLSafeTimedSerializer(secret_key=SECRET, salt=_SALT)
        token = ser.dumps(json.dumps({"k": "abc"}))
        assert verify_session(token, SECRET) is None

    def test_missing_api_key_field_returns_none(self) -> None:
        """Dict sem chave `k` é rejeitado."""
        ser = URLSafeTimedSerializer(secret_key=SECRET, salt=_SALT)
        token = ser.dumps(json.dumps({"u": str(uuid4())}))
        assert verify_session(token, SECRET) is None

    def test_uuid_field_not_a_valid_uuid_returns_none(self) -> None:
        """`u` presente mas não parseável como UUID → ValueError → None."""
        ser = URLSafeTimedSerializer(secret_key=SECRET, salt=_SALT)
        token = ser.dumps(json.dumps({"u": "isto-não-é-uuid", "k": "abc"}))
        assert verify_session(token, SECRET) is None

    def test_empty_api_key_returns_none(self) -> None:
        """Chave vazia (`k=""`) é rejeitada — bloqueia tokens "vazios"."""
        ser = URLSafeTimedSerializer(secret_key=SECRET, salt=_SALT)
        token = ser.dumps(json.dumps({"u": str(uuid4()), "k": ""}))
        assert verify_session(token, SECRET) is None


# ──────────────────────────────────────────────
#  Cookie helpers — set / clear
# ──────────────────────────────────────────────


class TestCookieHelpers:
    def test_set_session_cookie_sets_expected_attributes(self) -> None:
        """`set_session_cookie` grava cookie com httponly + samesite=lax."""
        resp = Response()
        set_session_cookie(resp, "my-token", secure=True)
        header = resp.headers["set-cookie"]
        assert SESSION_COOKIE in header
        assert "my-token" in header
        assert "HttpOnly" in header
        assert "Path=/" in header
        assert "SameSite=lax" in header
        assert f"Max-Age={SESSION_MAX_AGE}" in header
        assert "Secure" in header

    def test_set_session_cookie_secure_false_omits_secure_flag(self) -> None:
        """Em dev (`secure=False`) o cookie não vem com a flag Secure."""
        resp = Response()
        set_session_cookie(resp, "tok", secure=False)
        assert "Secure" not in resp.headers["set-cookie"]

    def test_clear_session_cookie_emits_delete_header(self) -> None:
        """`clear_session_cookie` emite Set-Cookie com Max-Age=0 ou equiv."""
        resp = Response()
        clear_session_cookie(resp)
        header = resp.headers["set-cookie"].lower()
        assert SESSION_COOKIE in header
        assert "max-age=0" in header or "expires=thu, 01 jan 1970" in header


# ──────────────────────────────────────────────
#  mint_session_api_key + revoke_session_api_key
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestMintAndRevokeSessionApiKey:
    async def test_mint_creates_persisted_api_key_with_session_prefix(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """`mint_session_api_key` insere ApiKey com nome `__web_session__:<uid>`."""
        plaintext = await mint_session_api_key(db_session, user=sample_user)
        assert plaintext.startswith("cf_")  # convenção definida em auth.service
        keys = list(
            await db_session.scalars(select(ApiKey).where(ApiKey.brand_id == sample_user.brand_id))
        )
        assert len(keys) == 1
        assert keys[0].name == f"__web_session__:{sample_user.id}"
        assert keys[0].expires_at is not None  # TTL = SESSION_MAX_AGE

    async def test_revoke_removes_row_by_plaintext(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """Após `revoke_session_api_key`, a linha some — best-effort idempotente."""
        plaintext = await mint_session_api_key(db_session, user=sample_user)
        await revoke_session_api_key(db_session, api_key=plaintext)
        keys = list(await db_session.scalars(select(ApiKey)))
        assert keys == []

    async def test_revoke_unknown_key_is_noop(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """Revogar uma chave que não existe não levanta — best-effort."""
        del sample_user  # só para garantir que o schema da brand já foi criado em outros testes
        # Sem exceção: o delete simplesmente não afeta linhas.
        await revoke_session_api_key(db_session, api_key="cf_inexistente")


# ──────────────────────────────────────────────
#  Dependências FastAPI — invocadas diretamente
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestRequireSession:
    async def test_no_cookie_raises_302_to_login(self, db_session: AsyncSession) -> None:
        """Sem cookie de sessão → HTTPException 302 → /login."""
        request = _make_request(cookie_value=None)
        with pytest.raises(HTTPException) as exc_info:
            await require_session(request, db=db_session)
        assert exc_info.value.status_code == 302
        assert exc_info.value.headers is not None
        assert exc_info.value.headers["Location"] == "/login"

    async def test_invalid_cookie_raises_302(self, db_session: AsyncSession) -> None:
        """Cookie presente mas inválido → 302."""
        request = _make_request(cookie_value="not-a-valid-token")
        with pytest.raises(HTTPException) as exc_info:
            await require_session(request, db=db_session)
        assert exc_info.value.status_code == 302

    async def test_user_not_found_raises_302(self, db_session: AsyncSession) -> None:
        """Cookie válido mas user_id não existe no DB → 302 /login."""
        from catalogflow.infra.settings import get_settings

        secret = get_settings().secret_key.get_secret_value()
        token = create_session(uuid4(), "cf_abc", secret)
        request = _make_request(cookie_value=token)
        with pytest.raises(HTTPException) as exc_info:
            await require_session(request, db=db_session)
        assert exc_info.value.status_code == 302

    async def test_inactive_user_raises_302(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """User existe mas `is_active=False` → 302 /login."""
        from catalogflow.infra.settings import get_settings
        from catalogflow.web.user_service import hash_password

        inactive = WebUser(
            brand_id=sample_brand.id,
            email="inactivo@oasis.com.br",
            name="Inativo",
            password_hash=hash_password("senha-de-teste"),
            role="operator",
            is_active=False,
        )
        db_session.add(inactive)
        await db_session.commit()
        await db_session.refresh(inactive)

        secret = get_settings().secret_key.get_secret_value()
        token = create_session(inactive.id, "cf_abc", secret)
        request = _make_request(cookie_value=token)
        with pytest.raises(HTTPException) as exc_info:
            await require_session(request, db=db_session)
        assert exc_info.value.status_code == 302

    async def test_active_user_returns_web_user(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """User ativo + cookie válido → retorna o WebUser."""
        from catalogflow.infra.settings import get_settings

        secret = get_settings().secret_key.get_secret_value()
        token = create_session(sample_user.id, "cf_abc", secret)
        request = _make_request(cookie_value=token)
        got = await require_session(request, db=db_session)
        assert got.id == sample_user.id


@pytest.mark.asyncio
class TestRequireSessionBrand:
    async def test_returns_brand_for_authenticated_user(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
        sample_brand: Brand,
    ) -> None:
        """`require_session_brand` devolve a Brand do user logado."""
        got = await require_session_brand(user=sample_user, db=db_session)
        assert got.id == sample_brand.id

    async def test_orphan_user_raises_302(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """User com brand_id inexistente → 302 /login.

        Cenário defensivo: na prática FKs evitam isso, mas o handler protege.
        """
        # Forçamos um brand_id fantasma sem violar FK (não persistimos a mudança).
        sample_user.brand_id = UUID("00000000-0000-0000-0000-000000000000")
        with pytest.raises(HTTPException) as exc_info:
            await require_session_brand(user=sample_user, db=db_session)
        assert exc_info.value.status_code == 302


@pytest.mark.asyncio
class TestRequireSessionApiKey:
    async def test_no_cookie_raises_302(self) -> None:
        """Sem cookie → 302 /login."""
        request = _make_request(cookie_value=None)
        with pytest.raises(HTTPException) as exc_info:
            require_session_api_key(request)
        assert exc_info.value.status_code == 302

    async def test_invalid_cookie_raises_302(self) -> None:
        """Cookie inválido → 302 /login (linha 173)."""
        request = _make_request(cookie_value="bogus")
        with pytest.raises(HTTPException) as exc_info:
            require_session_api_key(request)
        assert exc_info.value.status_code == 302

    async def test_valid_cookie_returns_embedded_api_key(self) -> None:
        """Cookie válido → retorna a API key plaintext embarcada."""
        from catalogflow.infra.settings import get_settings

        secret = get_settings().secret_key.get_secret_value()
        token = create_session(uuid4(), "cf_inside_cookie", secret)
        request = _make_request(cookie_value=token)
        assert require_session_api_key(request) == "cf_inside_cookie"


@pytest.mark.asyncio
class TestRequireAdmin:
    async def test_admin_user_passes(self, sample_admin: WebUser) -> None:
        """Role=admin → devolve o user."""
        got = await require_admin(user=sample_admin)
        assert got.id == sample_admin.id

    async def test_operator_user_redirected_to_dashboard(self, sample_user: WebUser) -> None:
        """Role=operator → 302 /dashboard (não /login)."""
        with pytest.raises(HTTPException) as exc_info:
            await require_admin(user=sample_user)
        assert exc_info.value.status_code == 302
        assert exc_info.value.headers is not None
        assert exc_info.value.headers["Location"] == "/dashboard"
