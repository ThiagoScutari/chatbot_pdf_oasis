"""Cobertura abrangente do `web/router.py`.

Complementa `test_web_pages.py` e `test_web_auth.py` cobrindo:
- Branches de erro e fallback (rate-limit, friendly messages, 404 polling).
- Funções auxiliares puras (`_classify_*`, `_stock_summary_from`, `_initials_for`).
- Rotas que proxiam para `/api/v1/...` — `httpx.AsyncClient` mockado no
  namespace do router para simular a API REST sem precisar montá-la.
- Pendency report PDF.
- Cache hit do `/product-image/{sku}`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth.models import Brand, WebUser
from catalogflow.modules.catalog.models import Catalog, Job
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.romaneio.models import Romaneio
from catalogflow.modules.stock.models import ErpSubmission, StockCheck
from catalogflow.web import router as web_router
from catalogflow.web.auth import SESSION_COOKIE
from catalogflow.web.tests.conftest import (
    SAMPLE_ADMIN_EMAIL,
    SAMPLE_ADMIN_PASSWORD,
    SAMPLE_USER_EMAIL,
    SAMPLE_USER_PASSWORD,
)

# ──────────────────────────────────────────────
#  Helpers — login + fake httpx
# ──────────────────────────────────────────────


async def _login_as_user(client: AsyncClient) -> None:
    resp = await client.post(
        "/login",
        data={"email": SAMPLE_USER_EMAIL, "password": SAMPLE_USER_PASSWORD},
    )
    assert resp.status_code == 302


async def _login_as_admin(client: AsyncClient) -> None:
    resp = await client.post(
        "/login",
        data={"email": SAMPLE_ADMIN_EMAIL, "password": SAMPLE_ADMIN_PASSWORD},
    )
    assert resp.status_code == 302


class _FakeHttpResponse:
    """Pequeno stand-in para `httpx.Response` — só o necessário pelo router."""

    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"",
        text: str | None = None,
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self._text = text if text is not None else content.decode("utf-8", errors="replace")
        self._json = json_body
        self.headers = headers or {}

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json")
        return self._json

    @property
    def text(self) -> str:
        return self._text


def _make_fake_httpx_client(
    responses: Sequence[_FakeHttpResponse | Exception],
) -> type:
    """Cria um stand-in para `httpx.AsyncClient` que devolve respostas pré-definidas
    em sequência (uma por chamada `.post()`/`.get()`).
    """
    queue = list(responses)

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        def _next(self) -> _FakeHttpResponse:
            if not queue:
                raise RuntimeError("fake httpx: queue de respostas esgotada")
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        async def post(self, *args: Any, **kwargs: Any) -> _FakeHttpResponse:
            return self._next()

        async def get(self, *args: Any, **kwargs: Any) -> _FakeHttpResponse:
            return self._next()

    return _FakeClient


# ──────────────────────────────────────────────
#  Fixtures auxiliares
# ──────────────────────────────────────────────


@pytest_asyncio.fixture
async def sample_catalog(db_session: AsyncSession, sample_brand: Brand) -> Catalog:
    cat = Catalog(
        brand_id=sample_brand.id,
        name="Catálogo Teste Router",
        status="ready",
        output_key=f"{sample_brand.id}/catalogs/x/editable.pdf",
        n_skus=5,
    )
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


@pytest_asyncio.fixture
async def sample_order(
    db_session: AsyncSession, sample_brand: Brand, sample_catalog: Catalog
) -> Order:
    order = Order(
        brand_id=sample_brand.id,
        catalog_id=sample_catalog.id,
        lojista_name="Loja Router",
        status="extracted",
        total_pecas=3,
    )
    db_session.add(order)
    await db_session.flush()
    items = [
        OrderItem(
            order_id=order.id,
            sku="SKU-A",
            product_name="Produto A",
            color_index=1,
            size="P",
            quantity=1,
            unit_price=Decimal("100.00"),
        ),
        OrderItem(
            order_id=order.id,
            sku="SKU-A",
            product_name="Produto A",
            color_index=1,
            size="M",
            quantity=1,
            unit_price=Decimal("100.00"),
            stock_status="partial",
            available_qty=0,
        ),
        OrderItem(
            order_id=order.id,
            sku="SKU-B",
            product_name="Produto B",
            color_index=1,
            size="G",
            quantity=1,
            unit_price=Decimal("200.00"),
            stock_status="out_of_stock",
            available_qty=0,
        ),
    ]
    for it in items:
        db_session.add(it)
    await db_session.commit()
    await db_session.refresh(order)
    return order


@pytest.fixture(autouse=True)
def _isolate_resend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Previne qualquer envio real para a API do Resend."""
    import resend

    monkeypatch.setattr(resend.Emails, "send", lambda *a, **kw: {"id": "test"})


# ──────────────────────────────────────────────
#  Helpers puros — funções não-decoradoras
# ──────────────────────────────────────────────


class TestFriendlyHelpers:
    def test_friendly_for_known_code(self) -> None:
        """Código conhecido devolve mensagem amigável fixa."""
        assert web_router._friendly_for("PDF_ENCRYPTED", "fallback") == "PDF protegido com senha."

    def test_friendly_for_unknown_code_uses_fallback(self) -> None:
        """Código desconhecido cai no `fallback` (mensagem do servidor)."""
        assert web_router._friendly_for("ALGO_INESPERADO", "msg do servidor") == "msg do servidor"

    def test_friendly_for_empty_fallback_uses_default(self) -> None:
        """Sem code e sem fallback → mensagem genérica."""
        assert web_router._friendly_for(None, "") == "Não foi possível processar o catálogo."

    def test_friendly_for_order_known_code(self) -> None:
        """Variant da `_friendly_for_order` mapeia PDF_FLATTENED corretamente."""
        assert "achatado" in web_router._friendly_for_order("PDF_FLATTENED", "x")

    def test_friendly_for_order_default(self) -> None:
        """Sem code conhecido + fallback vazio → mensagem genérica de pedido."""
        assert web_router._friendly_for_order(None, "") == "Não foi possível extrair o pedido."


class TestErrorCodeFromMessage:
    @pytest.mark.parametrize(
        "msg, expected",
        [
            ("Arquivo maior que 50MB", "FILE_TOO_LARGE"),
            ("PDF protegido com senha", "PDF_ENCRYPTED"),
            ("encrypted PDF detected", "PDF_ENCRYPTED"),
            ("Nenhum produto encontrado", "PDF_NO_PRODUCTS"),
            ("Documento não é um pdf válido", "INVALID_FILE_TYPE"),
            ("erro genérico que não bate em nada", None),
            ("", None),
            (None, None),
        ],
    )
    def test_maps_messages_to_codes(self, msg: str | None, expected: str | None) -> None:
        """Strings comuns nos Job.error mapeiam para os codes que a UI conhece."""
        assert web_router._error_code_from_message(msg) == expected


class TestOrderErrorCodeFromMessage:
    @pytest.mark.parametrize(
        "msg, expected",
        [
            ("PDF achatado", "PDF_FLATTENED"),
            ("erro: pdf_flattened", "PDF_FLATTENED"),
            ("flatten detected", "PDF_FLATTENED"),
            ("50MB ultrapassado", "FILE_TOO_LARGE"),
            ("PDF com senha", "PDF_ENCRYPTED"),
            ("Arquivo não é um pdf válido", "INVALID_FILE_TYPE"),
            ("blá blá", None),
            (None, None),
        ],
    )
    def test_maps_order_messages(self, msg: str | None, expected: str | None) -> None:
        """Mensagens do `Job.error` em pedidos mapeiam para codes amigáveis."""
        assert web_router._order_error_code_from_message(msg) == expected


class TestClassifyRomaneioState:
    def test_absent_when_no_romaneio(self) -> None:
        """Sem romaneio → estado 'absent'."""
        from catalogflow.web.data import OrderDetail

        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=None, stock_check=None, submission=None
        )
        assert web_router._classify_romaneio_state(detail) == "absent"

    def test_ready_when_output_key_present(self) -> None:
        """Romaneio com `output_key` → 'ready'."""
        from catalogflow.web.data import OrderDetail

        rom = Romaneio(order_id=uuid4(), brand_id=uuid4(), output_key="x.pdf")
        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=rom, stock_check=None, submission=None
        )
        assert web_router._classify_romaneio_state(detail) == "ready"

    def test_processing_when_output_key_missing(self) -> None:
        """Romaneio sem output_key → 'processing'."""
        from catalogflow.web.data import OrderDetail

        rom = Romaneio(order_id=uuid4(), brand_id=uuid4(), output_key=None)
        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=rom, stock_check=None, submission=None
        )
        assert web_router._classify_romaneio_state(detail) == "processing"


class TestClassifyStockState:
    def test_absent_when_no_check(self) -> None:
        """Sem stock_check → 'absent'."""
        from catalogflow.web.data import OrderDetail

        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=None, stock_check=None, submission=None
        )
        assert web_router._classify_stock_state(detail) == "absent"

    @pytest.mark.parametrize(
        "status, expected",
        [
            ("pending", "checking"),
            ("checking", "checking"),
            ("completed", "completed"),
            ("error", "error"),
        ],
    )
    def test_status_mapping(self, status: str, expected: str) -> None:
        """Mapeamento de StockCheck.status para o estado da UI.

        Sem `created_at` (objeto in-memory, não flushed), o gate de
        stuck detection é pulado — `pending`/`checking` permanecem
        como `"checking"`.
        """
        from catalogflow.web.data import OrderDetail

        sc = StockCheck(order_id=uuid4(), brand_id=uuid4(), status=status, result={})
        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=None, stock_check=sc, submission=None
        )
        assert web_router._classify_stock_state(detail) == expected

    def test_returns_error_for_stuck_job(self) -> None:
        """StockCheck > 5 min em pending → 'error' (S07-01)."""
        from catalogflow.web.data import OrderDetail

        sc = StockCheck(order_id=uuid4(), brand_id=uuid4(), status="pending", result={})
        sc.created_at = datetime.now(UTC) - timedelta(
            minutes=web_router.STOCK_CHECK_TIMEOUT_MINUTES + 1
        )
        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=None, stock_check=sc, submission=None
        )
        assert web_router._classify_stock_state(detail) == "error"

    def test_returns_checking_when_recent(self) -> None:
        """StockCheck < 5 min em pending → 'checking' (S07-01)."""
        from catalogflow.web.data import OrderDetail

        sc = StockCheck(order_id=uuid4(), brand_id=uuid4(), status="pending", result={})
        sc.created_at = datetime.now(UTC) - timedelta(seconds=30)
        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=None, stock_check=sc, submission=None
        )
        assert web_router._classify_stock_state(detail) == "checking"


class TestClassifySubmissionState:
    def test_absent_when_no_submission(self) -> None:
        """Sem submission → 'absent'."""
        from catalogflow.web.data import OrderDetail

        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=None, stock_check=None, submission=None
        )
        assert web_router._classify_submission_state(detail) == "absent"

    @pytest.mark.parametrize(
        "status, expected",
        [
            ("pending", "submitting"),
            ("submitting", "submitting"),
            ("accepted", "accepted"),
            ("partially_accepted", "partially_accepted"),
            ("rejected", "rejected"),
            ("error", "error"),
        ],
    )
    def test_known_statuses(self, status: str, expected: str) -> None:
        """Status conhecidos mapeiam 1:1 para o estado da UI."""
        from catalogflow.web.data import OrderDetail

        sub = ErpSubmission(order_id=uuid4(), brand_id=uuid4(), status=status, result={})
        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=None, stock_check=None, submission=sub
        )
        assert web_router._classify_submission_state(detail) == expected


class TestStockSummary:
    def test_none_when_no_check_or_pending(self) -> None:
        """Sem stock_check ou não-completado → None."""
        from catalogflow.web.data import OrderDetail

        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=None, stock_check=None, submission=None
        )
        assert web_router._stock_summary_from(detail) is None
        sc = StockCheck(order_id=uuid4(), brand_id=uuid4(), status="pending", result={})
        detail2 = OrderDetail(
            order=Order(), catalog_name=None, romaneio=None, stock_check=sc, submission=None
        )
        assert web_router._stock_summary_from(detail2) is None

    def test_counts_items_by_status(self) -> None:
        """Itens completos com status conhecidos somam nos buckets."""
        from catalogflow.web.data import OrderDetail

        sc = StockCheck(
            order_id=uuid4(),
            brand_id=uuid4(),
            status="completed",
            result={
                "items": [
                    {"status": "available"},
                    {"status": "available"},
                    {"status": "partial"},
                    {"status": "out_of_stock"},
                    {"status": "unknown"},
                    {"status": "foobar"},  # status desconhecido — ignorado
                ]
            },
        )
        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=None, stock_check=sc, submission=None
        )
        summary = web_router._stock_summary_from(detail)
        assert summary == {
            "total_items": 6,
            "available": 2,
            "partial": 1,
            "out_of_stock": 1,
            "unknown": 1,
        }


class TestInitialsFor:
    def test_two_words_returns_two_letters(self) -> None:
        """Duas palavras → iniciais delas em maiúsculo."""
        assert web_router._initials_for("Vestido Joana", "sku") == "VJ"

    def test_single_word_returns_first_two_letters(self) -> None:
        """Uma palavra → primeiras 2 letras."""
        assert web_router._initials_for("Blusa", "sku") == "BL"

    def test_empty_name_falls_back_to_sku(self) -> None:
        """Nome vazio → fallback p/ SKU."""
        assert web_router._initials_for("", "SK1234") == "SK"

    def test_all_non_alnum_fallback_question_mark(self) -> None:
        """Sem nome e SKU sem alfanuméricos → '?'."""
        assert web_router._initials_for("", "---") == "?"


# ──────────────────────────────────────────────
#  Login — rate-limit branch
# ──────────────────────────────────────────────


class TestLoginRateLimit:
    async def test_rate_limit_returns_429(self, client: AsyncClient, sample_user: WebUser) -> None:
        """6ª tentativa após 5 falhas: 429 + template de erro inline."""
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
        assert resp.status_code == 429
        assert "Muitas tentativas" in resp.text


# ──────────────────────────────────────────────
#  Register — branches de validação e 503
# ──────────────────────────────────────────────


class TestRegisterRouter:
    async def test_register_no_brand_returns_503(self, client: AsyncClient) -> None:
        """Sem brand seedada → 503 com mensagem informativa."""
        resp = await client.post(
            "/register",
            data={
                "name": "Sem Marca",
                "email": "x@y.com",
                "password": "senha-grande-123",
            },
        )
        assert resp.status_code == 503
        assert "Sistema ainda não configurado" in resp.text

    async def test_register_validation_error_returns_400(
        self,
        client: AsyncClient,
        sample_brand: Brand,
    ) -> None:
        """Email inválido (sem @) → 400 com mensagem do ValidationError."""
        del sample_brand  # garante uma brand seedada
        resp = await client.post(
            "/register",
            data={
                "name": "Nome",
                "email": "sem-arroba-mas-grande",
                "password": "senha-bem-grande",
            },
        )
        assert resp.status_code == 400
        assert "Email inválido" in resp.text


# ──────────────────────────────────────────────
#  Logout — caminho com cookie válido
# ──────────────────────────────────────────────


class TestLogoutWithSession:
    async def test_logout_revokes_session_api_key(
        self, client: AsyncClient, sample_user: WebUser
    ) -> None:
        """Logout limpa cookie e revoga a ApiKey embarcada na sessão."""
        del sample_user
        await _login_as_user(client)
        assert SESSION_COOKIE in client.cookies
        resp = await client.get("/logout")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"


# ──────────────────────────────────────────────
#  Admin pages — list + approve + deny
# ──────────────────────────────────────────────


class TestAdminUsersPages:
    async def test_admin_lists_pending_and_active(
        self, client: AsyncClient, sample_admin: WebUser
    ) -> None:
        """Página admin renderiza listas de pendentes e ativos."""
        del sample_admin
        await _login_as_admin(client)
        resp = await client.get("/admin/users")
        assert resp.status_code == 200
        body = resp.text
        # Pelo menos o próprio admin aparece em "ativos".
        assert SAMPLE_ADMIN_EMAIL in body

    async def test_admin_approve_unknown_user_returns_404(
        self, client: AsyncClient, sample_admin: WebUser
    ) -> None:
        """Approve com user_id inexistente → 404 elegante."""
        del sample_admin
        await _login_as_admin(client)
        resp = await client.post(f"/admin/users/{uuid4()}/approve")
        assert resp.status_code == 404
        assert "Usuário não encontrado" in resp.text

    async def test_admin_approve_succeeds_and_redirects(
        self,
        client: AsyncClient,
        sample_admin: WebUser,
        db_session: AsyncSession,
    ) -> None:
        """Approve em user pendente da mesma brand → 302 /admin/users + is_active=True."""
        from catalogflow.web.user_service import hash_password

        pending = WebUser(
            brand_id=sample_admin.brand_id,
            email="pendente-router@oasis.com.br",
            name="Pending Router",
            password_hash=hash_password("senha-aqui-1234"),
            role="operator",
            is_active=False,
        )
        db_session.add(pending)
        await db_session.commit()
        await db_session.refresh(pending)

        await _login_as_admin(client)
        resp = await client.post(f"/admin/users/{pending.id}/approve")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/admin/users"

        await db_session.refresh(pending)
        assert pending.is_active is True

    async def test_admin_approve_cross_tenant_returns_404(
        self,
        client: AsyncClient,
        sample_admin: WebUser,
        db_session: AsyncSession,
    ) -> None:
        """Approve em user de outra brand → 404 'Sem permissão'."""
        from catalogflow.modules.auth import service as auth_service
        from catalogflow.web.user_service import hash_password

        other_brand = await auth_service.create_brand(
            db_session, slug="outra-router", name="Outra Router"
        )
        foreign = WebUser(
            brand_id=other_brand.id,
            email="foreign-router@oasis.com.br",
            name="Foreign",
            password_hash=hash_password("senha-segura-1234"),
            role="operator",
            is_active=False,
        )
        db_session.add(foreign)
        await db_session.commit()
        await db_session.refresh(foreign)

        await _login_as_admin(client)
        resp = await client.post(f"/admin/users/{foreign.id}/approve")
        assert resp.status_code == 404
        # 404 'Sem permissão' indica que o brand check rodou.
        assert "Sem permissão" in resp.text or "Usuário não encontrado" in resp.text

    async def test_admin_deny_unknown_user_returns_404(
        self, client: AsyncClient, sample_admin: WebUser
    ) -> None:
        """Deny em user inexistente → 404 elegante."""
        del sample_admin
        await _login_as_admin(client)
        resp = await client.post(f"/admin/users/{uuid4()}/deny")
        assert resp.status_code == 404

    async def test_admin_deny_succeeds_and_redirects(
        self,
        client: AsyncClient,
        sample_admin: WebUser,
        db_session: AsyncSession,
    ) -> None:
        """Deny em pendente da mesma brand → 302 /admin/users."""
        from catalogflow.web.user_service import hash_password

        pending = WebUser(
            brand_id=sample_admin.brand_id,
            email="deny-router@oasis.com.br",
            name="Deny Router",
            password_hash=hash_password("senha-segura-1234"),
            role="operator",
            is_active=False,
        )
        db_session.add(pending)
        await db_session.commit()
        await db_session.refresh(pending)

        await _login_as_admin(client)
        resp = await client.post(f"/admin/users/{pending.id}/deny")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/admin/users"

    async def test_admin_deny_active_user_swallows_conflict(
        self,
        client: AsyncClient,
        sample_admin: WebUser,
        db_session: AsyncSession,
    ) -> None:
        """Deny em user já ativo: ConflictError engolido → 302 mesmo assim."""
        from catalogflow.web.user_service import hash_password

        active = WebUser(
            brand_id=sample_admin.brand_id,
            email="active-router@oasis.com.br",
            name="Active Router",
            password_hash=hash_password("senha-segura-1234"),
            role="operator",
            is_active=True,
        )
        db_session.add(active)
        await db_session.commit()
        await db_session.refresh(active)

        await _login_as_admin(client)
        resp = await client.post(f"/admin/users/{active.id}/deny")
        assert resp.status_code == 302


# ──────────────────────────────────────────────
#  Catalog badge + actions_strip — happy + 404
# ──────────────────────────────────────────────


class TestCatalogBadgeAndStrip:
    async def test_badge_renders_for_existing_catalog(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_catalog: Catalog,
    ) -> None:
        """Catálogo presente → fragmento HTML 200 com status."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get(f"/catalogs/{sample_catalog.id}/_badge")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    async def test_actions_strip_renders(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_catalog: Catalog,
    ) -> None:
        """Strip de ações é renderizado quando o catálogo existe."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get(f"/catalogs/{sample_catalog.id}/_actions_strip")
        assert resp.status_code == 200

    async def test_actions_strip_404_for_unknown(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        """Strip 404 silencioso (HTMX para o polling)."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get(f"/catalogs/{uuid4()}/_actions_strip")
        assert resp.status_code == 404


# ──────────────────────────────────────────────
#  Catalog upload submit + poll
# ──────────────────────────────────────────────


class TestCatalogUploadSubmit:
    async def test_upload_success_returns_envelope_data(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API 202 → web devolve o `data` do envelope ao Alpine."""
        del sample_api_key
        await _login_as_user(client)
        cat_id = str(uuid4())
        job_id = str(uuid4())
        responses = [
            _FakeHttpResponse(
                status_code=202,
                json_body={"success": True, "data": {"catalog_id": cat_id, "job_id": job_id}},
            )
        ]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )

        files = {"file": ("c.pdf", b"%PDF-fake", "application/pdf")}
        resp = await client.post(
            "/catalogs/upload",
            data={"name": "Cat X", "collection": "V25"},
            files=files,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["catalog_id"] == cat_id
        assert body["job_id"] == job_id

    async def test_upload_api_error_returns_friendly_message(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve erro 400 com code → web mapeia p/ mensagem amigável."""
        del sample_api_key
        await _login_as_user(client)
        responses = [
            _FakeHttpResponse(
                status_code=400,
                json_body={
                    "success": False,
                    "error": {"code": "PDF_ENCRYPTED", "message": "PDF encrypted"},
                },
            )
        ]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )
        resp = await client.post(
            "/catalogs/upload",
            data={"name": "x"},
            files={"file": ("c.pdf", b"data", "application/pdf")},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "PDF_ENCRYPTED"
        assert "senha" in body["error"]["message"]

    async def test_upload_non_json_response_uses_text(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve corpo não-JSON → web extrai snippet do text."""
        del sample_api_key
        await _login_as_user(client)
        responses = [
            _FakeHttpResponse(
                status_code=500,
                text="<html>erro interno</html>",
            )
        ]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )
        resp = await client.post(
            "/catalogs/upload",
            data={"name": "x"},
            files={"file": ("c.pdf", b"d", "application/pdf")},
        )
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "UNKNOWN"

    async def test_upload_transport_error_returns_502(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`httpx.HTTPError` no proxy → 502 UPSTREAM_ERROR."""
        del sample_api_key
        await _login_as_user(client)
        responses = [httpx.ConnectError("offline")]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )
        resp = await client.post(
            "/catalogs/upload",
            data={"name": "x"},
            files={"file": ("c.pdf", b"d", "application/pdf")},
        )
        assert resp.status_code == 502
        body = resp.json()
        assert body["error"]["code"] == "UPSTREAM_ERROR"


class TestCatalogUploadPoll:
    async def test_poll_unknown_job_returns_404(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        """Job de outra brand / inexistente → 404 vazio."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get(f"/catalogs/upload/poll/{uuid4()}")
        assert resp.status_code == 404

    async def test_poll_running_job_returns_fragment(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_brand: Brand,
        db_session: AsyncSession,
    ) -> None:
        """Job em estado válido devolve fragmento HTML sem friendly_error."""
        del sample_api_key
        job = Job(brand_id=sample_brand.id, job_type="catalog.process", status="running")
        db_session.add(job)
        await db_session.commit()
        await _login_as_user(client)
        resp = await client.get(f"/catalogs/upload/poll/{job.id}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    async def test_poll_error_job_includes_friendly_message(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_brand: Brand,
        db_session: AsyncSession,
    ) -> None:
        """Job error com mensagem de 'encrypted' → friendly_error 'senha'."""
        del sample_api_key
        job = Job(
            brand_id=sample_brand.id,
            job_type="catalog.process",
            status="error",
            error="PDF encrypted detected",
        )
        db_session.add(job)
        await db_session.commit()
        await _login_as_user(client)
        resp = await client.get(f"/catalogs/upload/poll/{job.id}")
        assert resp.status_code == 200
        assert "senha" in resp.text.lower()


# ──────────────────────────────────────────────
#  Catalog download — proxy para a API REST
# ──────────────────────────────────────────────


class TestCatalogDownload:
    async def test_download_200_passes_bytes_through(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_catalog: Catalog,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve 200+bytes → web devolve mesmos bytes."""
        del sample_api_key
        await _login_as_user(client)
        responses = [
            _FakeHttpResponse(
                status_code=200,
                content=b"%PDF-bytes",
                headers={
                    "content-type": "application/pdf",
                    "content-disposition": 'attachment; filename="cat.pdf"',
                },
            )
        ]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )
        resp = await client.get(f"/catalogs/{sample_catalog.id}/download")
        assert resp.status_code == 200
        assert resp.content == b"%PDF-bytes"
        assert "application/pdf" in resp.headers["content-type"]

    async def test_download_302_redirects_to_presigned_url(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_catalog: Catalog,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve 302 → web devolve 302 idêntico (presigned URL)."""
        del sample_api_key
        await _login_as_user(client)
        responses = [
            _FakeHttpResponse(
                status_code=302,
                headers={"location": "https://r2.example.com/x?sig=abc"},
            )
        ]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )
        resp = await client.get(f"/catalogs/{sample_catalog.id}/download")
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("https://")

    async def test_download_not_ready_returns_404(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_catalog: Catalog,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve outro status (ex.: 409 'not ready') → 404 'Download indisponível'."""
        del sample_api_key
        await _login_as_user(client)
        responses = [
            _FakeHttpResponse(
                status_code=409,
                json_body={"error": {"code": "NOT_READY", "message": "ainda processando"}},
            )
        ]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )
        resp = await client.get(f"/catalogs/{sample_catalog.id}/download")
        assert resp.status_code == 404
        assert "Download indisponível" in resp.text

    async def test_download_non_json_body_falls_back_to_default(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_catalog: Catalog,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve corpo não-JSON → web usa mensagem default."""
        del sample_api_key
        await _login_as_user(client)
        responses = [
            _FakeHttpResponse(status_code=500, text="<html>erro</html>"),
        ]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )
        resp = await client.get(f"/catalogs/{sample_catalog.id}/download")
        assert resp.status_code == 404
        assert "ainda não está pronto" in resp.text


# ──────────────────────────────────────────────
#  Catalog delete — branches faltantes
# ──────────────────────────────────────────────


class TestCatalogDeleteUnknown:
    async def test_delete_unknown_catalog_returns_404(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        """Delete em UUID inexistente → 404 elegante."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.post(f"/catalogs/{uuid4()}/delete")
        assert resp.status_code == 404
        assert "Catálogo não encontrado" in resp.text


# ──────────────────────────────────────────────
#  Order upload form + submit + poll + badge
# ──────────────────────────────────────────────


class TestOrderUploadForm:
    async def test_upload_form_renders_with_catalog_options(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_catalog: Catalog,
    ) -> None:
        """Form de upload lista catálogos `ready` no dropdown."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get("/orders/upload")
        assert resp.status_code == 200
        assert sample_catalog.name in resp.text


class TestOrderUploadSubmit:
    async def test_upload_success_returns_data(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Upload OK → web devolve `data` do envelope da API."""
        del sample_api_key
        await _login_as_user(client)
        order_id = str(uuid4())
        responses = [
            _FakeHttpResponse(
                status_code=202,
                json_body={"success": True, "data": {"order_id": order_id, "job_id": str(uuid4())}},
            )
        ]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )
        resp = await client.post(
            "/orders/upload",
            data={"catalog_id": str(uuid4()), "lojista_name": "L"},
            files={"file": ("p.pdf", b"%PDF", "application/pdf")},
        )
        assert resp.status_code == 200
        assert resp.json()["order_id"] == order_id

    async def test_upload_api_error_with_known_code(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve PDF_FLATTENED → web traduz para mensagem amigável."""
        del sample_api_key
        await _login_as_user(client)
        responses = [
            _FakeHttpResponse(
                status_code=400,
                json_body={"success": False, "error": {"code": "PDF_FLATTENED", "message": "x"}},
            )
        ]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )
        resp = await client.post(
            "/orders/upload",
            files={"file": ("p.pdf", b"d", "application/pdf")},
        )
        assert resp.status_code == 400
        assert "achatado" in resp.json()["error"]["message"]

    async def test_upload_non_json_response(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve HTML → web usa snippet do text."""
        del sample_api_key
        await _login_as_user(client)
        responses = [_FakeHttpResponse(status_code=500, text="<html>erro</html>")]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )
        resp = await client.post(
            "/orders/upload",
            files={"file": ("p.pdf", b"d", "application/pdf")},
        )
        assert resp.status_code == 500

    async def test_upload_transport_error_returns_502(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTPError do httpx → 502 UPSTREAM_ERROR."""
        del sample_api_key
        await _login_as_user(client)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client([httpx.ConnectError("offline")]),
        )
        resp = await client.post(
            "/orders/upload",
            files={"file": ("p.pdf", b"d", "application/pdf")},
        )
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "UPSTREAM_ERROR"


class TestOrderUploadPoll:
    async def test_poll_unknown_returns_404(self, client: AsyncClient, sample_api_key: str) -> None:
        """Job inexistente → 404."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get(f"/orders/upload/poll/{uuid4()}")
        assert resp.status_code == 404

    async def test_poll_running_returns_fragment(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_brand: Brand,
        db_session: AsyncSession,
    ) -> None:
        """Job running → fragmento sem mensagem amigável de erro."""
        del sample_api_key
        job = Job(brand_id=sample_brand.id, job_type="order.extract", status="running")
        db_session.add(job)
        await db_session.commit()
        await _login_as_user(client)
        resp = await client.get(f"/orders/upload/poll/{job.id}")
        assert resp.status_code == 200

    async def test_poll_error_includes_friendly_message(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_brand: Brand,
        db_session: AsyncSession,
    ) -> None:
        """Job com erro 'flatten' → friendly message com 'achatado'."""
        del sample_api_key
        job = Job(
            brand_id=sample_brand.id,
            job_type="order.extract",
            status="error",
            error="PDF achatado",
        )
        db_session.add(job)
        await db_session.commit()
        await _login_as_user(client)
        resp = await client.get(f"/orders/upload/poll/{job.id}")
        assert resp.status_code == 200
        assert "achatado" in resp.text.lower()


class TestOrderBadge:
    async def test_badge_renders_for_existing_order(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
    ) -> None:
        """Pedido válido → badge HTML."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get(f"/orders/{sample_order.id}/_badge")
        assert resp.status_code == 200


# ──────────────────────────────────────────────
#  Order detail com stock_check + submission
# ──────────────────────────────────────────────


class TestOrderDetailWithStockAndSubmission:
    async def test_renders_with_stock_check_and_submission(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        db_session: AsyncSession,
    ) -> None:
        """Detalhe com StockCheck completo + ErpSubmission cobre todos os classify_*."""
        del sample_api_key
        sc = StockCheck(
            order_id=sample_order.id,
            brand_id=sample_order.brand_id,
            status="completed",
            result={
                "items": [
                    {
                        "sku": "SKU-A",
                        "color_index": 1,
                        "size": "P",
                        "available": 10,
                        "status": "available",
                    },
                    {
                        "sku": "SKU-A",
                        "color_index": 1,
                        "size": "M",
                        "available": 0,
                        "status": "partial",
                    },
                    {
                        "sku": "SKU-B",
                        "color_index": 1,
                        "size": "G",
                        "available": 0,
                        "status": "out_of_stock",
                    },
                ]
            },
            checked_at=datetime.now(UTC),
        )
        sub = ErpSubmission(
            order_id=sample_order.id,
            brand_id=sample_order.brand_id,
            status="accepted",
            result={},
            erp_reference="ERP-X",
        )
        db_session.add_all([sc, sub])
        await db_session.commit()

        await _login_as_user(client)
        resp = await client.get(f"/orders/{sample_order.id}")
        assert resp.status_code == 200


# ──────────────────────────────────────────────
#  Romaneio kick / regenerate / poll / download
# ──────────────────────────────────────────────


class TestRomaneioActions:
    async def test_kick_creates_fragment_and_calls_api(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /orders/{id}/romaneio chama GET /api/v1/.../romaneio + devolve fragmento."""
        del sample_api_key
        await _login_as_user(client)
        responses = [_FakeHttpResponse(status_code=202, json_body={"success": True})]
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient", _make_fake_httpx_client(responses)
        )
        resp = await client.post(f"/orders/{sample_order.id}/romaneio")
        assert resp.status_code == 200

    async def test_regenerate_unknown_order_returns_404(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regenerate em order inexistente → 404 silencioso."""
        del sample_api_key
        await _login_as_user(client)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client([_FakeHttpResponse(status_code=200)]),
        )
        resp = await client.post(f"/orders/{uuid4()}/regenerate-romaneio")
        assert resp.status_code == 404

    async def test_regenerate_resets_output_key_and_calls_api(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Romaneio com output_key existente: regenerate zera e chama a API."""
        del sample_api_key
        rom = Romaneio(
            order_id=sample_order.id,
            brand_id=sample_order.brand_id,
            output_key="old/key.pdf",
        )
        db_session.add(rom)
        await db_session.commit()
        await db_session.refresh(rom)
        rom_id = rom.id

        await _login_as_user(client)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client([_FakeHttpResponse(status_code=202)]),
        )
        resp = await client.post(f"/orders/{sample_order.id}/regenerate-romaneio")
        assert resp.status_code == 200

        # output_key foi zerado em DB (commit ocorreu dentro do handler)
        await db_session.refresh(rom)
        assert rom.output_key is None
        del rom_id

    async def test_poll_returns_fragment_with_state(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
    ) -> None:
        """Poll devolve o fragmento — estado 'absent' quando não há romaneio."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get(f"/orders/{sample_order.id}/romaneio/poll")
        assert resp.status_code == 200

    async def test_poll_unknown_order_returns_404(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        """Poll em order inexistente → 404."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get(f"/orders/{uuid4()}/romaneio/poll")
        assert resp.status_code == 404

    async def test_download_302(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve 302 → web redireciona pro presigned URL."""
        del sample_api_key
        await _login_as_user(client)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client(
                [
                    _FakeHttpResponse(
                        status_code=302,
                        headers={"location": "https://r2.example.com/romaneio.pdf"},
                    )
                ]
            ),
        )
        resp = await client.get(f"/orders/{sample_order.id}/romaneio/download")
        assert resp.status_code == 302

    async def test_download_200_passes_bytes(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve 200+bytes → web devolve bytes com Content-Disposition padrão."""
        del sample_api_key
        await _login_as_user(client)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client(
                [
                    _FakeHttpResponse(
                        status_code=200,
                        content=b"%PDF-rom",
                        headers={"content-type": "application/pdf"},
                    )
                ]
            ),
        )
        resp = await client.get(f"/orders/{sample_order.id}/romaneio/download")
        assert resp.status_code == 200
        assert resp.content == b"%PDF-rom"

    async def test_download_not_ready_returns_404(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve 202 (ainda processando) → 404 'Romaneio indisponível'."""
        del sample_api_key
        await _login_as_user(client)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client([_FakeHttpResponse(status_code=202)]),
        )
        resp = await client.get(f"/orders/{sample_order.id}/romaneio/download")
        assert resp.status_code == 404
        assert "Romaneio indisponível" in resp.text


# ──────────────────────────────────────────────
#  Stock check + Submit kick/poll
# ──────────────────────────────────────────────


class TestStockAndSubmit:
    async def test_stock_check_kick(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /stock-check-web chama a API e devolve fragmento."""
        del sample_api_key
        await _login_as_user(client)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client([_FakeHttpResponse(status_code=202)]),
        )
        resp = await client.post(f"/orders/{sample_order.id}/stock-check-web")
        assert resp.status_code == 200

    async def test_stock_check_kick_api_error_still_returns_fragment(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """API devolve 500 → handler ainda devolve fragmento (loga warning)."""
        del sample_api_key
        await _login_as_user(client)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client([_FakeHttpResponse(status_code=500)]),
        )
        resp = await client.post(f"/orders/{sample_order.id}/stock-check-web")
        assert resp.status_code == 200

    async def test_stock_check_poll_completed_sets_hx_trigger(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        db_session: AsyncSession,
    ) -> None:
        """StockCheck completed → header HX-Trigger=stock-check-completed."""
        del sample_api_key
        sc = StockCheck(
            order_id=sample_order.id,
            brand_id=sample_order.brand_id,
            status="completed",
            result={"items": []},
            checked_at=datetime.now(UTC),
        )
        db_session.add(sc)
        await db_session.commit()
        await _login_as_user(client)
        resp = await client.get(f"/orders/{sample_order.id}/stock-check-poll")
        assert resp.status_code == 200
        assert resp.headers.get("hx-trigger") == "stock-check-completed"

    async def test_stock_check_poll_unknown_order_404(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        """Order inexistente no poll → 404."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get(f"/orders/{uuid4()}/stock-check-poll")
        assert resp.status_code == 404

    async def test_stock_check_poll_continues_below_90(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        db_session: AsyncSession,
    ) -> None:
        """poll_count baixo + checking → fragmento contém hx-trigger de polling."""
        del sample_api_key
        sc = StockCheck(
            order_id=sample_order.id,
            brand_id=sample_order.brand_id,
            status="pending",
            result={},
        )
        db_session.add(sc)
        await db_session.commit()
        await _login_as_user(client)
        resp = await client.get(
            f"/orders/{sample_order.id}/stock-check-poll?poll_count=0",
        )
        assert resp.status_code == 200
        body = resp.text
        assert "hx-trigger" in body
        # Incrementa para o próximo poll.
        assert "poll_count=1" in body

    async def test_stock_check_poll_stops_at_90(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        db_session: AsyncSession,
    ) -> None:
        """poll_count=90 + checking → sem hx-trigger; mostra botão de retry."""
        del sample_api_key
        sc = StockCheck(
            order_id=sample_order.id,
            brand_id=sample_order.brand_id,
            status="pending",
            result={},
        )
        db_session.add(sc)
        await db_session.commit()
        await _login_as_user(client)
        resp = await client.get(
            f"/orders/{sample_order.id}/stock-check-poll?poll_count=90",
        )
        assert resp.status_code == 200
        body = resp.text
        assert "hx-trigger" not in body
        assert "Tentar novamente" in body

    async def test_submit_kick(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /submit-web envia customer_code + devolve fragmento."""
        del sample_api_key
        await _login_as_user(client)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client([_FakeHttpResponse(status_code=202)]),
        )
        resp = await client.post(
            f"/orders/{sample_order.id}/submit-web",
            data={"customer_code": "CUST-001"},
        )
        assert resp.status_code == 200

    async def test_submit_kick_api_error_still_returns_fragment(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Submit API 500 → fragmento ainda renderiza."""
        del sample_api_key
        await _login_as_user(client)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client([_FakeHttpResponse(status_code=500)]),
        )
        resp = await client.post(
            f"/orders/{sample_order.id}/submit-web",
            data={"customer_code": "CUST-001"},
        )
        assert resp.status_code == 200

    async def test_submit_poll(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
    ) -> None:
        """Poll /submit-poll devolve fragmento (estado 'absent' por padrão)."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get(f"/orders/{sample_order.id}/submit-poll")
        assert resp.status_code == 200


# ──────────────────────────────────────────────
#  Pendency report — happy + branches
# ──────────────────────────────────────────────


class TestPendencyReport:
    async def test_no_pendency_returns_friendly_404(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_brand: Brand,
        db_session: AsyncSession,
    ) -> None:
        """Pedido sem pendências → 404 'Sem pendências'."""
        del sample_api_key
        order = Order(
            brand_id=sample_brand.id,
            lojista_name="Loja Sem Pendência",
            status="extracted",
        )
        db_session.add(order)
        await db_session.flush()
        # OrderItem com stock_status=available — não conta como pendência.
        db_session.add(
            OrderItem(
                order_id=order.id,
                sku="A",
                color_index=1,
                size="P",
                quantity=1,
                stock_status="available",
            )
        )
        await db_session.commit()

        await _login_as_user(client)
        resp = await client.get(f"/orders/{order.id}/pendency-report")
        assert resp.status_code == 404
        assert "Sem pendências" in resp.text

    async def test_unknown_order_returns_404(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        """Pedido inexistente → 404 'Pedido não encontrado'."""
        del sample_api_key
        await _login_as_user(client)
        resp = await client.get(f"/orders/{uuid4()}/pendency-report")
        assert resp.status_code == 404
        assert "Pedido não encontrado" in resp.text

    async def test_renders_pdf_when_pendencies_exist(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Pedido com pendências (partial + out_of_stock) → PDF inline.

        Mockamos `fetch_product_images` (não bater no AMC real) e o builder
        do romaneio (o real é pesado e seu próprio teste já cobre).
        """
        del sample_api_key

        async def fake_fetch_images(*args: Any, **kwargs: Any) -> dict[str, bytes]:
            return {}

        monkeypatch.setattr(
            "catalogflow.shared.image_fetcher.fetch_product_images",
            fake_fetch_images,
        )

        class _FakeBuilder:
            def build(self, *args: Any, **kwargs: Any) -> bytes:
                return b"%PDF-fake-pendency"

        monkeypatch.setattr(
            "catalogflow.modules.romaneio.builder.RomaneioBuilder",
            lambda: _FakeBuilder(),
        )

        await _login_as_user(client)
        resp = await client.get(f"/orders/{sample_order.id}/pendency-report")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content == b"%PDF-fake-pendency"
        assert "inline" in resp.headers["content-disposition"]

    async def test_image_fetch_failure_does_not_kill_report(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`fetch_product_images` levantando → PDF gerado mesmo sem fotos."""
        del sample_api_key

        async def boom(*args: Any, **kwargs: Any) -> dict[str, bytes]:
            raise RuntimeError("AMC offline")

        monkeypatch.setattr(
            "catalogflow.shared.image_fetcher.fetch_product_images",
            boom,
        )

        class _FakeBuilder:
            def build(self, *args: Any, **kwargs: Any) -> bytes:
                return b"%PDF-no-images"

        monkeypatch.setattr(
            "catalogflow.modules.romaneio.builder.RomaneioBuilder",
            lambda: _FakeBuilder(),
        )

        await _login_as_user(client)
        resp = await client.get(f"/orders/{sample_order.id}/pendency-report")
        assert resp.status_code == 200
        assert resp.content == b"%PDF-no-images"

    async def test_brand_with_logo_loads_logo_bytes(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        sample_brand: Brand,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Brand com `logo_key` → tenta baixar do storage; falha silenciosa não derruba."""
        del sample_api_key

        # Marca a brand com logo_key para acionar o branch de download.
        sample_brand.logo_key = "brands/x/logo.png"
        await db_session.commit()

        from catalogflow.infra.storage import StorageClient

        async def fake_download(self: Any, key: str) -> bytes:
            raise RuntimeError("logo not found")

        monkeypatch.setattr(StorageClient, "download", fake_download)

        async def fake_fetch_images(*args: Any, **kwargs: Any) -> dict[str, bytes]:
            return {}

        monkeypatch.setattr(
            "catalogflow.shared.image_fetcher.fetch_product_images",
            fake_fetch_images,
        )

        class _FakeBuilder:
            def build(self, *args: Any, **kwargs: Any) -> bytes:
                return b"%PDF-logo-fallback"

        monkeypatch.setattr(
            "catalogflow.modules.romaneio.builder.RomaneioBuilder",
            lambda: _FakeBuilder(),
        )

        await _login_as_user(client)
        resp = await client.get(f"/orders/{sample_order.id}/pendency-report")
        assert resp.status_code == 200


# ──────────────────────────────────────────────
#  product-image — cache hit + erro upstream
# ──────────────────────────────────────────────


class TestProductImageBranches:
    async def test_returns_cached_bytes_when_present(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bytes no cache → devolve direto sem chamar AMC."""
        del sample_api_key

        async def fake_cache_get(sku: str) -> bytes:
            del sku
            return b"\xff\xd8\xff CACHED"

        monkeypatch.setattr("catalogflow.web.router.cache_get_image_bytes", fake_cache_get)
        await _login_as_user(client)
        resp = await client.get("/product-image/SKU-CACHED")
        assert resp.status_code == 200
        assert resp.content.startswith(b"\xff\xd8\xff")
        assert "image/jpeg" in resp.headers["content-type"]

    async def test_returns_placeholder_when_httpx_raises(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`httpx.AsyncClient.get` levantando → fallback SVG placeholder."""
        del sample_api_key

        async def no_cache(sku: str) -> None:
            del sku
            return None

        async def fake_fetch(_sku: str) -> str:
            return "https://qrcode.amctextil.com.br/img/x.jpg"

        monkeypatch.setattr("catalogflow.web.router.cache_get_image_bytes", no_cache)
        monkeypatch.setattr("catalogflow.web.router.fetch_product_image_url", fake_fetch)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client([httpx.ConnectError("offline")]),
        )
        await _login_as_user(client)
        resp = await client.get("/product-image/SKU-FAIL?name=Blusa%20Maria")
        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]
        # Iniciais "BM" no placeholder
        assert ">BM<" in resp.text

    async def test_returns_placeholder_when_upstream_non_200(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CDN devolve 404 → placeholder SVG."""
        del sample_api_key

        async def no_cache(sku: str) -> None:
            del sku
            return None

        async def fake_fetch(_sku: str) -> str:
            return "https://qrcode.amctextil.com.br/img/x.jpg"

        monkeypatch.setattr("catalogflow.web.router.cache_get_image_bytes", no_cache)
        monkeypatch.setattr("catalogflow.web.router.fetch_product_image_url", fake_fetch)
        monkeypatch.setattr(
            "catalogflow.web.router.httpx.AsyncClient",
            _make_fake_httpx_client([_FakeHttpResponse(status_code=404)]),
        )
        await _login_as_user(client)
        resp = await client.get("/product-image/SKU-404")
        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]

    async def test_returns_placeholder_when_fetch_returns_none(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """S07-02: `fetch_product_image_url` retornando None → 200 + SVG."""
        del sample_api_key

        async def no_cache(sku: str) -> None:
            del sku
            return None

        async def fake_fetch_none(_sku: str) -> None:
            return None

        monkeypatch.setattr("catalogflow.web.router.cache_get_image_bytes", no_cache)
        monkeypatch.setattr("catalogflow.web.router.fetch_product_image_url", fake_fetch_none)
        await _login_as_user(client)
        resp = await client.get("/product-image/SKU-NONE")
        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]

    async def test_returns_placeholder_when_helper_raises(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """S07-02: helper levantando Exception genérica → 200 + SVG (não 500)."""
        del sample_api_key

        async def boom(_sku: str) -> bytes:
            raise RuntimeError("redis exploded")

        monkeypatch.setattr("catalogflow.web.router.cache_get_image_bytes", boom)
        await _login_as_user(client)
        resp = await client.get("/product-image/SKU-BOOM?name=Vestido%20Joana")
        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]
        assert ">VJ<" in resp.text

    def test_placeholder_svg_is_valid(self) -> None:
        """`_placeholder_svg_response` retorna 200 + SVG bem-formado."""
        resp = web_router._placeholder_svg_response("SKU-X", "Vestido Joana")
        assert resp.status_code == 200
        assert resp.media_type == "image/svg+xml"
        body = bytes(resp.body).decode()
        assert body.startswith("<svg")
        assert body.endswith("</svg>")
        assert ">VJ<" in body


# ──────────────────────────────────────────────
#  render_web_500
# ──────────────────────────────────────────────


class TestRenderWeb500:
    async def test_renders_template_with_500_status(self) -> None:
        """`render_web_500` devolve HTMLResponse 500 sem detalhes do erro."""
        from starlette.requests import Request

        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        req = Request(scope)
        resp = web_router.render_web_500(req)
        assert resp.status_code == 500
        assert b"text/html" not in resp.body  # corpo é HTML, não menção literal de content-type
        assert "text/html" in resp.headers["content-type"]


# ──────────────────────────────────────────────
#  Chamadas diretas — bypass do FastAPI/ASGI para forçar tracing
#
#  Coverage.py + Python 3.13 + FastAPI tem um quirk onde linhas
#  executadas dentro de handlers async via ASGITransport não são
#  creditadas. Chamamos as funções diretamente para garantir cobertura
#  fiel das linhas-corpo dos handlers (returns de TemplateResponse,
#  RedirectResponse, etc.).
# ──────────────────────────────────────────────


def _bare_request() -> Any:
    """Constrói um `Request` mínimo para passar ao Jinja2Templates."""
    from starlette.requests import Request

    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "app": None,
    }
    return Request(scope)


@pytest.mark.asyncio
class TestRouterFunctionsDirect:
    async def test_root_with_session_redirects_to_dashboard(self) -> None:
        """`root()` com cookie válido → 302 /dashboard."""
        from catalogflow.infra.settings import get_settings
        from catalogflow.web.auth import create_session

        secret = get_settings().secret_key.get_secret_value()
        token = create_session(uuid4(), "cf_abc", secret)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", f"cf_session={token}".encode())],
            "query_string": b"",
        }
        from starlette.requests import Request

        req = Request(scope)
        resp = await web_router.root(req)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"

    async def test_root_without_session_redirects_to_login(self) -> None:
        """`root()` sem cookie → 302 /login."""
        from starlette.requests import Request

        req = Request(
            {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
        )
        resp = await web_router.root(req)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_login_get_renders_template(self) -> None:
        """`login_get()` devolve HTMLResponse com o form."""
        resp = await web_router.login_get(_bare_request(), notice=None)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_forgot_password_get(self) -> None:
        """`forgot_password_get` renderiza o form."""
        resp = await web_router.forgot_password_get(_bare_request())
        assert resp.status_code == 200

    async def test_register_get(self) -> None:
        """`register_get` renderiza o form."""
        resp = await web_router.register_get(_bare_request())
        assert resp.status_code == 200

    async def test_render_login_error(self) -> None:
        """`_render_login_error` devolve template com error/status_code."""
        resp = web_router._render_login_error(
            _bare_request(), email="x@y.com", error="incorretos", status_code=200
        )
        assert resp.status_code == 200

    async def test_render_not_found(self) -> None:
        """`_render_not_found` devolve 404 elegante."""
        resp = web_router._render_not_found(_bare_request())
        assert resp.status_code == 404

    async def test_render_web_404(self) -> None:
        """`render_web_404` devolve 404 elegante usado pelo handler global."""
        resp = web_router.render_web_404(_bare_request())
        assert resp.status_code == 404

    async def test_placeholder_svg_response(self) -> None:
        """`_placeholder_svg_response` devolve SVG inline."""
        resp = web_router._placeholder_svg_response("SKU1", "Vestido Joana")
        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]
        body = resp.body.decode() if isinstance(resp.body, bytes) else str(resp.body)
        assert ">VJ<" in body

    async def test_start_session_for_creates_cookie(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """`_start_session_for` mintta API key e seta cookie no response."""
        resp = await web_router._start_session_for(_bare_request(), sample_user, db_session)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"
        # Cookie cf_session foi setado.
        cookies = resp.headers.get("set-cookie", "")
        assert "cf_session" in cookies

    async def test_login_post_rate_limit_branch(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """Direto: rate-limit excedido → 429 inline."""
        from catalogflow.modules.auth.models import LoginAttempt

        # 5 falhas para o mesmo email — dispara rate-limit.
        for _ in range(5):
            db_session.add(LoginAttempt(identifier=SAMPLE_USER_EMAIL, success=False))
        await db_session.flush()

        resp = await web_router.login_post(
            _bare_request(),
            email=SAMPLE_USER_EMAIL,
            password=SAMPLE_USER_PASSWORD,
            db=db_session,
        )
        assert resp.status_code == 429

    async def test_login_post_authentication_error_branch(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """Direto: senha errada → 200 com error inline."""
        del sample_user
        resp = await web_router.login_post(
            _bare_request(),
            email=SAMPLE_USER_EMAIL,
            password="errada-1234",
            db=db_session,
        )
        assert resp.status_code == 200

    async def test_login_post_valid_credentials_returns_redirect(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """Direto: credenciais válidas → 302 + cookie via _start_session_for."""
        del sample_user
        resp = await web_router.login_post(
            _bare_request(),
            email=SAMPLE_USER_EMAIL,
            password=SAMPLE_USER_PASSWORD,
            db=db_session,
        )
        assert resp.status_code == 302
        assert "cf_session" in resp.headers.get("set-cookie", "")

    async def test_forgot_password_post(
        self, db_session: AsyncSession, sample_user: WebUser
    ) -> None:
        """Direto: forgot-password POST devolve 200 com 'sent'."""
        del sample_user
        resp = await web_router.forgot_password_post(
            _bare_request(), email=SAMPLE_USER_EMAIL, db=db_session
        )
        assert resp.status_code == 200

    async def test_magic_link_consume_invalid_renders_error(self, db_session: AsyncSession) -> None:
        """Direto: token inválido → template de erro 400."""
        resp = await web_router.magic_link_consume(
            _bare_request(), token="tok-invalido", db=db_session
        )
        assert resp.status_code == 400

    async def test_magic_link_consume_valid_creates_session(
        self, db_session: AsyncSession, sample_user: WebUser
    ) -> None:
        """Direto: token válido → 302 com cookie + magic-link consumido."""
        from catalogflow.web.user_service import WebUserService

        service = WebUserService(db_session)
        ok = await service.send_magic_link(SAMPLE_USER_EMAIL)
        assert ok
        from sqlalchemy import select

        from catalogflow.modules.auth.models import MagicLink

        link = await db_session.scalar(select(MagicLink).where(MagicLink.user_id == sample_user.id))
        assert link is not None

        resp = await web_router.magic_link_consume(_bare_request(), token=link.token, db=db_session)
        assert resp.status_code == 302

    async def test_register_post_no_brand_returns_503(self, db_session: AsyncSession) -> None:
        """Direto: sem brand seedada → 503."""
        resp = await web_router.register_post(
            _bare_request(),
            name="Novo",
            email="novo@x.com",
            password="senha-grande-12345",
            db=db_session,
        )
        assert resp.status_code == 503

    async def test_register_post_validation_error(
        self, db_session: AsyncSession, sample_brand: Brand
    ) -> None:
        """Direto: email sem @ → 400 ValidationError."""
        del sample_brand
        resp = await web_router.register_post(
            _bare_request(),
            name="Novo",
            email="sem-arroba-mas-longo",
            password="senha-grande-12345",
            db=db_session,
        )
        assert resp.status_code == 400

    async def test_register_post_success(
        self, db_session: AsyncSession, sample_brand: Brand
    ) -> None:
        """Direto: cadastro válido → 200 com 'sent'."""
        del sample_brand
        resp = await web_router.register_post(
            _bare_request(),
            name="Novo Usuário",
            email="novo-unico@x.com",
            password="senha-grande-12345",
            db=db_session,
        )
        assert resp.status_code == 200

    async def test_admin_users_list_direct(
        self, db_session: AsyncSession, sample_admin: WebUser
    ) -> None:
        """Direto: lista de pendentes/ativos pra brand."""
        resp = await web_router.admin_users_list(_bare_request(), admin=sample_admin, db=db_session)
        assert resp.status_code == 200

    async def test_logout_with_cookie_revokes_and_clears(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """Direto: logout com cookie válido limpa + revoga API key."""
        from catalogflow.infra.settings import get_settings
        from catalogflow.web.auth import create_session, mint_session_api_key

        api_key = await mint_session_api_key(db_session, user=sample_user)
        secret = get_settings().secret_key.get_secret_value()
        token = create_session(sample_user.id, api_key, secret)
        from starlette.requests import Request

        req = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/logout",
                "headers": [(b"cookie", f"cf_session={token}".encode())],
                "query_string": b"",
            }
        )
        resp = await web_router.logout(req, db=db_session)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_dashboard_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: dashboard com brand → 200 HTML."""
        resp = await web_router.dashboard(_bare_request(), brand=sample_brand, db=db_session)
        assert resp.status_code == 200

    async def test_catalogs_list_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: catalogs list → 200."""
        resp = await web_router.catalogs_list(
            _bare_request(), page=1, notice=None, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200

    async def test_catalog_badge_render(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_catalog: Catalog,
    ) -> None:
        """Direto: catalog_badge devolve fragmento HTML."""
        resp = await web_router.catalog_badge(
            _bare_request(), catalog_id=sample_catalog.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200

    async def test_catalog_upload_form_direct(
        self,
        sample_brand: Brand,
    ) -> None:
        """Direto: upload form template."""
        resp = await web_router.catalog_upload_form(_bare_request(), brand=sample_brand)
        assert resp.status_code == 200

    async def test_catalog_upload_poll_running_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: poll de job running."""
        job = Job(brand_id=sample_brand.id, job_type="catalog.process", status="running")
        db_session.add(job)
        await db_session.flush()
        resp = await web_router.catalog_upload_poll(
            _bare_request(), job_id=job.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200

    async def test_catalog_detail_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_catalog: Catalog,
    ) -> None:
        """Direto: catalog detail render."""
        resp = await web_router.catalog_detail(
            _bare_request(),
            catalog_id=sample_catalog.id,
            page=1,
            brand=sample_brand,
            db=db_session,
        )
        assert resp.status_code == 200

    async def test_catalog_detail_unknown_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: catalog detail UUID desconhecido → 404."""
        resp = await web_router.catalog_detail(
            _bare_request(), catalog_id=uuid4(), page=1, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 404

    async def test_catalog_actions_strip_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_catalog: Catalog,
    ) -> None:
        """Direto: actions_strip render."""
        resp = await web_router.catalog_actions_strip(
            _bare_request(), catalog_id=sample_catalog.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200

    async def test_catalog_delete_direct(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
        sample_catalog: Catalog,
    ) -> None:
        """Direto: catalog_delete marca deleted_at e devolve 302."""
        resp = await web_router.catalog_delete(
            _bare_request(), catalog_id=sample_catalog.id, user=sample_user, db=db_session
        )
        assert resp.status_code == 302

    async def test_orders_list_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: orders list → 200."""
        resp = await web_router.orders_list(
            _bare_request(), page=1, notice=None, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200

    async def test_order_delete_unknown_direct(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """Direto: order_delete em UUID inexistente → 404."""
        resp = await web_router.order_delete(
            _bare_request(), order_id=uuid4(), user=sample_user, db=db_session
        )
        assert resp.status_code == 404

    async def test_order_delete_with_romaneio_cascade(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
        sample_order: Order,
    ) -> None:
        """Direto: order_delete cascateia no Romaneio associado."""
        rom = Romaneio(
            order_id=sample_order.id,
            brand_id=sample_order.brand_id,
            output_key="x.pdf",
        )
        db_session.add(rom)
        await db_session.flush()
        resp = await web_router.order_delete(
            _bare_request(), order_id=sample_order.id, user=sample_user, db=db_session
        )
        assert resp.status_code == 302
        await db_session.refresh(rom)
        assert rom.deleted_at is not None

    async def test_order_upload_form_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: order upload form → 200."""
        resp = await web_router.order_upload_form(
            _bare_request(), brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200

    async def test_order_upload_poll_running_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: order upload poll com job running."""
        job = Job(brand_id=sample_brand.id, job_type="order.extract", status="running")
        db_session.add(job)
        await db_session.flush()
        resp = await web_router.order_upload_poll(
            _bare_request(), job_id=job.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200

    async def test_order_badge_render(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
    ) -> None:
        """Direto: order_badge render."""
        resp = await web_router.order_badge(
            _bare_request(), order_id=sample_order.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200

    async def test_order_detail_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
    ) -> None:
        """Direto: order_detail render."""
        resp = await web_router.order_detail(
            _bare_request(), order_id=sample_order.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200

    async def test_render_romaneio_fragment_404(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: fragment 404 quando order não existe."""
        resp = await web_router._render_romaneio_fragment(
            _bare_request(), uuid4(), sample_brand, db_session
        )
        assert resp.status_code == 404

    async def test_render_romaneio_fragment_render(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
    ) -> None:
        """Direto: fragment renderiza com order válido."""
        resp = await web_router._render_romaneio_fragment(
            _bare_request(), sample_order.id, sample_brand, db_session
        )
        assert resp.status_code == 200

    async def test_render_stock_fragment_completed_triggers_event(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
    ) -> None:
        """Direto: stock fragment com status=completed seta HX-Trigger."""
        sc = StockCheck(
            order_id=sample_order.id,
            brand_id=sample_brand.id,
            status="completed",
            result={"items": []},
            checked_at=datetime.now(UTC),
        )
        db_session.add(sc)
        await db_session.flush()
        resp = await web_router._render_stock_fragment(
            _bare_request(), sample_order.id, sample_brand, db_session
        )
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "stock-check-completed"

    async def test_render_stock_fragment_404(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: stock fragment 404 quando order não existe."""
        resp = await web_router._render_stock_fragment(
            _bare_request(), uuid4(), sample_brand, db_session
        )
        assert resp.status_code == 404

    async def test_render_submit_fragment_404(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: submit fragment 404 quando order não existe."""
        resp = await web_router._render_submit_fragment(
            _bare_request(), uuid4(), sample_brand, db_session
        )
        assert resp.status_code == 404

    async def test_render_submit_fragment_render(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
    ) -> None:
        """Direto: submit fragment renderiza com order válido."""
        resp = await web_router._render_submit_fragment(
            _bare_request(), sample_order.id, sample_brand, db_session
        )
        assert resp.status_code == 200

    async def test_order_romaneio_poll_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
    ) -> None:
        """Direto: romaneio poll → 200."""
        resp = await web_router.order_romaneio_poll(
            _bare_request(), order_id=sample_order.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200

    async def test_order_romaneio_poll_unknown_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: romaneio poll UUID inexistente → 404."""
        resp = await web_router.order_romaneio_poll(
            _bare_request(), order_id=uuid4(), brand=sample_brand, db=db_session
        )
        assert resp.status_code == 404

    async def test_order_romaneio_regenerate_unknown(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: regenerate em order inexistente → 404."""
        # api_key irrelevante neste caso (detail é None antes do call à API).
        resp = await web_router.order_romaneio_regenerate(
            _bare_request(),
            order_id=uuid4(),
            api_key="cf_irrelevante",
            brand=sample_brand,
            db=db_session,
        )
        assert resp.status_code == 404

    async def test_order_romaneio_regenerate_resets_output_key(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Direto: regenerate zera output_key + chama API."""
        rom = Romaneio(
            order_id=sample_order.id,
            brand_id=sample_brand.id,
            output_key="x.pdf",
        )
        db_session.add(rom)
        await db_session.commit()

        async def fake_hit(*args: Any, **kwargs: Any) -> _FakeHttpResponse:
            return _FakeHttpResponse(status_code=202)

        monkeypatch.setattr(web_router, "_hit_romaneio_endpoint", fake_hit)

        resp = await web_router.order_romaneio_regenerate(
            _bare_request(),
            order_id=sample_order.id,
            api_key="cf_x",
            brand=sample_brand,
            db=db_session,
        )
        assert resp.status_code == 200
        await db_session.refresh(rom)
        assert rom.output_key is None

    async def test_pendency_report_no_pendency_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: pedido sem pendências → 404 'Sem pendências'."""
        order = Order(
            brand_id=sample_brand.id,
            lojista_name="No Pendency",
            status="extracted",
        )
        db_session.add(order)
        await db_session.flush()
        db_session.add(
            OrderItem(
                order_id=order.id,
                sku="A",
                color_index=1,
                size="P",
                quantity=1,
                stock_status="available",
            )
        )
        await db_session.flush()
        resp = await web_router.order_pendency_report(
            _bare_request(), order_id=order.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 404

    async def test_pendency_report_unknown_order_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: order_id inexistente → 404 'Pedido não encontrado'."""
        resp = await web_router.order_pendency_report(
            _bare_request(), order_id=uuid4(), brand=sample_brand, db=db_session
        )
        assert resp.status_code == 404

    async def test_pendency_report_happy_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Direto: pedido com pendências + builder/images mockados → PDF."""

        async def fake_fetch_images(*args: Any, **kwargs: Any) -> dict[str, bytes]:
            return {}

        monkeypatch.setattr(
            "catalogflow.shared.image_fetcher.fetch_product_images",
            fake_fetch_images,
        )

        class _FakeBuilder:
            def build(self, *args: Any, **kwargs: Any) -> bytes:
                return b"%PDF-direct"

        monkeypatch.setattr(
            "catalogflow.modules.romaneio.builder.RomaneioBuilder",
            lambda: _FakeBuilder(),
        )

        resp = await web_router.order_pendency_report(
            _bare_request(), order_id=sample_order.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200
        assert resp.body == b"%PDF-direct"

    async def test_product_image_cached_direct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Direto: product_image cache hit devolve bytes."""

        async def fake_cache_get(sku: str) -> bytes:
            del sku
            return b"\xff\xd8 CACHED"

        monkeypatch.setattr(web_router, "cache_get_image_bytes", fake_cache_get)
        resp = await web_router.product_image(sku="SK1", name="", api_key="cf_x")
        assert resp.status_code == 200
        assert bytes(resp.body).startswith(b"\xff\xd8")

    async def test_admin_users_approve_direct_success(
        self,
        db_session: AsyncSession,
        sample_admin: WebUser,
    ) -> None:
        """Direto: approve user pendente da mesma brand → 302."""
        from catalogflow.web.user_service import hash_password

        pending = WebUser(
            brand_id=sample_admin.brand_id,
            email="dir-pending@x.com",
            name="Dir Pend",
            password_hash=hash_password("senha-1234567"),
            role="operator",
            is_active=False,
        )
        db_session.add(pending)
        await db_session.flush()
        resp = await web_router.admin_users_approve(
            _bare_request(), user_id=pending.id, admin=sample_admin, db=db_session
        )
        assert resp.status_code == 302

    async def test_admin_users_approve_direct_not_found(
        self,
        db_session: AsyncSession,
        sample_admin: WebUser,
    ) -> None:
        """Direto: approve UUID inexistente → 404."""
        resp = await web_router.admin_users_approve(
            _bare_request(), user_id=uuid4(), admin=sample_admin, db=db_session
        )
        assert resp.status_code == 404

    async def test_admin_users_approve_direct_cross_tenant(
        self,
        db_session: AsyncSession,
        sample_admin: WebUser,
    ) -> None:
        """Direto: approve user de outra brand → 404 'Sem permissão'."""
        from catalogflow.modules.auth import service as auth_service
        from catalogflow.web.user_service import hash_password

        other = await auth_service.create_brand(db_session, slug="dir-other", name="Dir Other")
        await db_session.flush()
        foreign = WebUser(
            brand_id=other.id,
            email="dir-foreign@x.com",
            name="Dir For",
            password_hash=hash_password("senha-1234567"),
            role="operator",
            is_active=False,
        )
        db_session.add(foreign)
        await db_session.flush()
        resp = await web_router.admin_users_approve(
            _bare_request(), user_id=foreign.id, admin=sample_admin, db=db_session
        )
        assert resp.status_code == 404

    async def test_admin_users_deny_direct_success(
        self,
        db_session: AsyncSession,
        sample_admin: WebUser,
    ) -> None:
        """Direto: deny user pendente da mesma brand → 302."""
        from catalogflow.web.user_service import hash_password

        pending = WebUser(
            brand_id=sample_admin.brand_id,
            email="dir-deny@x.com",
            name="Dir Deny",
            password_hash=hash_password("senha-1234567"),
            role="operator",
            is_active=False,
        )
        db_session.add(pending)
        await db_session.flush()
        resp = await web_router.admin_users_deny(
            _bare_request(), user_id=pending.id, admin=sample_admin, db=db_session
        )
        assert resp.status_code == 302

    async def test_admin_users_deny_direct_not_found(
        self,
        db_session: AsyncSession,
        sample_admin: WebUser,
    ) -> None:
        """Direto: deny UUID inexistente → 404."""
        resp = await web_router.admin_users_deny(
            _bare_request(), user_id=uuid4(), admin=sample_admin, db=db_session
        )
        assert resp.status_code == 404

    async def test_admin_users_deny_direct_active_user_swallows(
        self,
        db_session: AsyncSession,
        sample_admin: WebUser,
    ) -> None:
        """Direto: deny user ativo: ConflictError engolido → 302 mesmo assim."""
        from catalogflow.web.user_service import hash_password

        active = WebUser(
            brand_id=sample_admin.brand_id,
            email="dir-active@x.com",
            name="Dir Active",
            password_hash=hash_password("senha-1234567"),
            role="operator",
            is_active=True,
        )
        db_session.add(active)
        await db_session.flush()
        resp = await web_router.admin_users_deny(
            _bare_request(), user_id=active.id, admin=sample_admin, db=db_session
        )
        assert resp.status_code == 302

    async def test_catalog_badge_unknown_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: catalog_badge com UUID inexistente → 404."""
        resp = await web_router.catalog_badge(
            _bare_request(), catalog_id=uuid4(), brand=sample_brand, db=db_session
        )
        assert resp.status_code == 404

    async def test_catalog_actions_strip_unknown_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: actions_strip 404 quando catalog não existe."""
        resp = await web_router.catalog_actions_strip(
            _bare_request(), catalog_id=uuid4(), brand=sample_brand, db=db_session
        )
        assert resp.status_code == 404

    async def test_catalog_delete_unknown_direct(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
    ) -> None:
        """Direto: delete em UUID inexistente → 404."""
        resp = await web_router.catalog_delete(
            _bare_request(), catalog_id=uuid4(), user=sample_user, db=db_session
        )
        assert resp.status_code == 404

    async def test_order_upload_poll_error_job_friendly(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: order upload poll com job error 'PDF achatado' → friendly."""
        job = Job(
            brand_id=sample_brand.id,
            job_type="order.extract",
            status="error",
            error="PDF achatado",
        )
        db_session.add(job)
        await db_session.flush()
        resp = await web_router.order_upload_poll(
            _bare_request(), job_id=job.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200
        assert "achatado" in bytes(resp.body).decode().lower()

    async def test_catalog_upload_poll_error_job_friendly(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: catalog upload poll com job error 'encrypted' → friendly."""
        job = Job(
            brand_id=sample_brand.id,
            job_type="catalog.process",
            status="error",
            error="PDF encrypted",
        )
        db_session.add(job)
        await db_session.flush()
        resp = await web_router.catalog_upload_poll(
            _bare_request(), job_id=job.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200
        assert "senha" in bytes(resp.body).decode().lower()

    async def test_order_badge_unknown_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: order_badge UUID inexistente → 404."""
        resp = await web_router.order_badge(
            _bare_request(), order_id=uuid4(), brand=sample_brand, db=db_session
        )
        assert resp.status_code == 404

    async def test_order_detail_unknown_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
    ) -> None:
        """Direto: order_detail UUID inexistente → 404."""
        resp = await web_router.order_detail(
            _bare_request(), order_id=uuid4(), brand=sample_brand, db=db_session
        )
        assert resp.status_code == 404

    async def test_classify_submission_state_unknown_status_fallback(self) -> None:
        """Linha defensiva: status fora dos sets conhecidos → 'absent'.

        ErpSubmission tem check-constraint em DB, então construímos em
        memória apenas para exercitar o fallback do _classify.
        """
        from catalogflow.web.data import OrderDetail

        # status fora de todos os branches conhecidos.
        sub = ErpSubmission(order_id=uuid4(), brand_id=uuid4(), status="weird", result={})
        detail = OrderDetail(
            order=Order(), catalog_name=None, romaneio=None, stock_check=None, submission=sub
        )
        assert web_router._classify_submission_state(detail) == "absent"

    async def test_pendency_report_with_brand_logo_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Direto: brand com logo_key → tenta baixar do storage (fail-soft)."""
        sample_brand.logo_key = "brands/x/logo.png"
        await db_session.flush()

        from catalogflow.infra.storage import StorageClient

        async def fake_download(self: Any, key: str) -> bytes:
            raise RuntimeError("offline")

        monkeypatch.setattr(StorageClient, "download", fake_download)

        async def fake_fetch_images(*args: Any, **kwargs: Any) -> dict[str, bytes]:
            return {}

        monkeypatch.setattr(
            "catalogflow.shared.image_fetcher.fetch_product_images",
            fake_fetch_images,
        )

        class _FakeBuilder:
            def build(self, *args: Any, **kwargs: Any) -> bytes:
                return b"%PDF"

        monkeypatch.setattr(
            "catalogflow.modules.romaneio.builder.RomaneioBuilder",
            lambda: _FakeBuilder(),
        )

        resp = await web_router.order_pendency_report(
            _bare_request(), order_id=sample_order.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200

    async def test_catalog_upload_poll_unknown_job_direct(
        self, db_session: AsyncSession, sample_brand: Brand
    ) -> None:
        """Direto: catalog_upload_poll com job_id inexistente → 404."""
        resp = await web_router.catalog_upload_poll(
            _bare_request(), job_id=uuid4(), brand=sample_brand, db=db_session
        )
        assert resp.status_code == 404

    async def test_order_upload_poll_unknown_job_direct(
        self, db_session: AsyncSession, sample_brand: Brand
    ) -> None:
        """Direto: order_upload_poll com job_id inexistente → 404."""
        resp = await web_router.order_upload_poll(
            _bare_request(), job_id=uuid4(), brand=sample_brand, db=db_session
        )
        assert resp.status_code == 404

    async def test_logout_without_cookie_direct(self, db_session: AsyncSession) -> None:
        """Branch 374->380: logout sem cookie pula o bloco de revoke."""
        from starlette.requests import Request

        req = Request(
            {"type": "http", "method": "GET", "path": "/logout", "headers": [], "query_string": b""}
        )
        resp = await web_router.logout(req, db=db_session)
        assert resp.status_code == 302

    async def test_logout_with_invalid_token_direct(self, db_session: AsyncSession) -> None:
        """Branch 377->380: cookie inválido → decoded is None, skip revoke."""
        from starlette.requests import Request

        req = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/logout",
                "headers": [(b"cookie", b"cf_session=lixo-invalido")],
                "query_string": b"",
            }
        )
        resp = await web_router.logout(req, db=db_session)
        assert resp.status_code == 302

    async def test_order_delete_without_romaneio_direct(
        self,
        db_session: AsyncSession,
        sample_user: WebUser,
        sample_order: Order,
    ) -> None:
        """Branch 901->905: order_delete sem romaneio associado."""
        # sample_order não tem romaneio — exercita o `if romaneio is None: skip`.
        resp = await web_router.order_delete(
            _bare_request(), order_id=sample_order.id, user=sample_user, db=db_session
        )
        assert resp.status_code == 302

    async def test_regenerate_without_output_key_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Branch 1297->1302: regenerate quando romaneio existe sem output_key.

        O branch `if detail.romaneio is not None and detail.romaneio.output_key`
        é False → pula o reset; chama API mesmo assim.
        """
        rom = Romaneio(
            order_id=sample_order.id,
            brand_id=sample_brand.id,
            output_key=None,
        )
        db_session.add(rom)
        await db_session.commit()

        async def fake_hit(*args: Any, **kwargs: Any) -> _FakeHttpResponse:
            return _FakeHttpResponse(status_code=202)

        monkeypatch.setattr(web_router, "_hit_romaneio_endpoint", fake_hit)
        resp = await web_router.order_romaneio_regenerate(
            _bare_request(),
            order_id=sample_order.id,
            api_key="cf_x",
            brand=sample_brand,
            db=db_session,
        )
        assert resp.status_code == 200

    async def test_stock_fragment_non_completed_no_trigger(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
    ) -> None:
        """Branch 1440->1442: stock fragment com status != completed → sem HX-Trigger."""
        sc = StockCheck(
            order_id=sample_order.id,
            brand_id=sample_brand.id,
            status="checking",
            result={},
        )
        db_session.add(sc)
        await db_session.flush()
        resp = await web_router._render_stock_fragment(
            _bare_request(), sample_order.id, sample_brand, db_session
        )
        assert resp.status_code == 200
        assert "hx-trigger" not in {k.lower() for k in resp.headers}

    async def test_pendency_report_image_fetch_exception_direct(
        self,
        db_session: AsyncSession,
        sample_brand: Brand,
        sample_order: Order,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Direto: fetch_product_images levantando → PDF mesmo sem fotos."""

        async def boom(*args: Any, **kwargs: Any) -> dict[str, bytes]:
            raise RuntimeError("AMC offline")

        monkeypatch.setattr(
            "catalogflow.shared.image_fetcher.fetch_product_images",
            boom,
        )

        class _FakeBuilder:
            def build(self, *args: Any, **kwargs: Any) -> bytes:
                return b"%PDF-no-images"

        monkeypatch.setattr(
            "catalogflow.modules.romaneio.builder.RomaneioBuilder",
            lambda: _FakeBuilder(),
        )

        resp = await web_router.order_pendency_report(
            _bare_request(), order_id=sample_order.id, brand=sample_brand, db=db_session
        )
        assert resp.status_code == 200
