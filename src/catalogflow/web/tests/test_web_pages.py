"""Testes das páginas autenticadas (dashboard, lista, upload, detalhe)."""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Catalog
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.romaneio.models import Romaneio
from catalogflow.web.auth import SESSION_COOKIE
from catalogflow.web.tests.conftest import SAMPLE_USER_EMAIL, SAMPLE_USER_PASSWORD


async def _login(client: AsyncClient, _api_key: str | None = None) -> None:
    """Faz POST /login com email+senha e popula o cookie de sessão.

    Aceita um `_api_key` por compat retroativa com a assinatura antiga.
    """
    resp = await client.post(
        "/login",
        data={"email": SAMPLE_USER_EMAIL, "password": SAMPLE_USER_PASSWORD},
    )
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


@pytest_asyncio.fixture
async def sample_order(
    db_session: AsyncSession,
    sample_brand: Brand,
    sample_catalog: Catalog,
) -> Order:
    """Pedido extraído com 2 SKUs e múltiplos tamanhos para testar detalhe."""
    from decimal import Decimal

    order = Order(
        brand_id=sample_brand.id,
        catalog_id=sample_catalog.id,
        lojista_name="Loja Moda Arte",
        status="extracted",
        total_pecas=10,
        valor_total=Decimal("15980.00"),
    )
    db_session.add(order)
    await db_session.flush()
    items = [
        OrderItem(
            order_id=order.id,
            sku="0442500941-0",
            product_name="Vestido Joana",
            color_index=1,
            size="PP",
            quantity=2,
            unit_price=Decimal("1598.00"),
        ),
        OrderItem(
            order_id=order.id,
            sku="0442500941-0",
            product_name="Vestido Joana",
            color_index=1,
            size="P",
            quantity=4,
            unit_price=Decimal("1598.00"),
        ),
        OrderItem(
            order_id=order.id,
            sku="0322500004-0",
            product_name="Jaqueta Berenice",
            color_index=1,
            size="M",
            quantity=4,
            unit_price=Decimal("3488.00"),
        ),
    ]
    for it in items:
        db_session.add(it)
    await db_session.commit()
    await db_session.refresh(order)
    return order


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


# ──────────────────────────────────────────────────────────────
#  Orders
# ──────────────────────────────────────────────────────────────


class TestOrdersList:
    async def test_requires_session(self, client: AsyncClient) -> None:
        resp = await client.get("/orders")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_empty_state(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        await _login(client, sample_api_key)
        resp = await client.get("/orders")
        assert resp.status_code == 200
        assert "Nenhum pedido ainda" in resp.text

    async def test_renders_orders(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
    ) -> None:
        await _login(client, sample_api_key)
        resp = await client.get("/orders")
        assert resp.status_code == 200
        body = resp.text
        assert sample_order.lojista_name is not None
        assert sample_order.lojista_name in body
        # Nome do catálogo via JOIN.
        assert "Inverno 26 MOTION" in body


class TestOrderDetail:
    async def test_requires_session(self, client: AsyncClient) -> None:
        bogus = uuid4()
        resp = await client.get(f"/orders/{bogus}")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_unknown_id_returns_friendly_404(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        await _login(client, sample_api_key)
        bogus = uuid4()
        resp = await client.get(f"/orders/{bogus}")
        assert resp.status_code == 404
        assert "text/html" in resp.headers.get("content-type", "")
        assert "Pedido não encontrado" in resp.text

    async def test_detail_renders_grouped_items(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
    ) -> None:
        await _login(client, sample_api_key)
        resp = await client.get(f"/orders/{sample_order.id}")
        assert resp.status_code == 200
        body = resp.text
        # Header
        assert sample_order.lojista_name is not None
        assert sample_order.lojista_name in body
        # Produtos agrupados
        assert "Vestido Joana" in body
        assert "Jaqueta Berenice" in body
        # Tamanhos presentes
        assert "PP" in body and ">P<" in body and "M" in body
        # Botão para gerar romaneio (estado absent — sample_order não tem romaneio)
        assert "Gerar romaneio" in body

    async def test_detail_isolates_other_brand(
        self,
        client: AsyncClient,
        sample_api_key: str,
        db_session: AsyncSession,
    ) -> None:
        from catalogflow.modules.auth import service as auth_service

        other = await auth_service.create_brand(
            db_session, slug="outra2", name="Outra Marca 2"
        )
        await db_session.commit()
        other_order = Order(
            brand_id=other.id,
            lojista_name="Loja Secreta",
            status="extracted",
        )
        db_session.add(other_order)
        await db_session.commit()
        await db_session.refresh(other_order)

        await _login(client, sample_api_key)
        resp = await client.get(f"/orders/{other_order.id}")
        assert resp.status_code == 404
        assert "Loja Secreta" not in resp.text


class TestOrderBadgeFragment:
    async def test_badge_unknown_returns_404(
        self, client: AsyncClient, sample_api_key: str
    ) -> None:
        await _login(client, sample_api_key)
        bogus = uuid4()
        resp = await client.get(f"/orders/{bogus}/_badge")
        assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────
#  Global error handlers — rotas web desconhecidas
# ──────────────────────────────────────────────────────────────


class TestProductImage:
    """Testes da rota `/product-image/{sku}` (Sprint 03 Fase F).

    Mockamos `fetch_product_image_url` (em vez de bater no AMC real)
    e `httpx.AsyncClient` para garantir comportamento determinístico.
    """

    async def test_requires_session(self, client: AsyncClient) -> None:
        resp = await client.get("/product-image/0142500001-0")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"

    async def test_returns_upstream_bytes_when_image_found(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Quando o AMC devolve a foto, encaminhamos os bytes."""
        from catalogflow.web import router as web_router

        # Mock 1: fetch_product_image_url devolve uma URL conhecida.
        async def fake_fetch(_sku: str) -> str:
            return "https://qrcode.amctextil.com.br/img/teste.jpg"

        monkeypatch.setattr(web_router, "fetch_product_image_url", fake_fetch)

        # Mock 2: AsyncClient.get retorna bytes JPEG.
        class _FakeResp:
            def __init__(self) -> None:
                self.status_code = 200
                self.content = b"\xff\xd8\xff\xe0FAKE-JPEG"
                self.headers: dict[str, str] = {"content-type": "image/jpeg"}

        class _FakeClient:
            def __init__(self, *a: object, **kw: object) -> None: ...
            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *a: object) -> None: ...
            async def get(self, *a: object, **kw: object) -> _FakeResp:
                return _FakeResp()

        monkeypatch.setattr("catalogflow.web.router.httpx.AsyncClient", _FakeClient)

        await _login(client, sample_api_key)
        resp = await client.get("/product-image/0142500001-0")

        assert resp.status_code == 200
        assert "image/jpeg" in resp.headers["content-type"]
        assert resp.content.startswith(b"\xff\xd8\xff")

    async def test_returns_svg_placeholder_for_unknown_sku(
        self,
        client: AsyncClient,
        sample_api_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SKU desconhecido ou erro upstream → SVG placeholder 200."""
        from catalogflow.web import router as web_router

        async def fake_fetch(_sku: str) -> None:
            return None  # nada encontrado no AMC

        monkeypatch.setattr(web_router, "fetch_product_image_url", fake_fetch)

        await _login(client, sample_api_key)
        resp = await client.get(
            "/product-image/sku-invalido?name=Vestido%20Joana"
        )

        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]
        body = resp.text
        # Iniciais "VJ" (Vestido Joana) renderizadas no SVG.
        assert ">VJ<" in body
        # Cor de fundo conforme paleta da marca.
        assert "#E8E0D5" in body


class TestSoftDeleteCatalog:
    """Soft-delete de catálogo via POST /catalogs/{id}/delete (Sprint 04+).

    Regras:
    - Marca `deleted_at` + `deleted_by` no registro, NÃO remove do banco.
    - Redireciona para /catalogs?notice=catalog_deleted.
    - Catálogo excluído some da lista e do dashboard.
    - Catálogo de outra brand → 404 (não vaza existência).
    """

    async def test_delete_marks_deleted_at_and_redirects(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_catalog: Catalog,
        db_session: AsyncSession,
    ) -> None:
        await _login(client, sample_api_key)
        resp = await client.post(f"/catalogs/{sample_catalog.id}/delete")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/catalogs?notice=catalog_deleted"

        await db_session.refresh(sample_catalog)
        assert sample_catalog.deleted_at is not None
        assert sample_catalog.deleted_by is not None

    async def test_deleted_catalog_hidden_from_list(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_catalog: Catalog,
    ) -> None:
        await _login(client, sample_api_key)
        # Antes da exclusão aparece.
        before = await client.get("/catalogs")
        assert sample_catalog.name in before.text

        await client.post(f"/catalogs/{sample_catalog.id}/delete")
        after = await client.get("/catalogs")
        assert sample_catalog.name not in after.text
        # Estado vazio reaparece.
        assert "Nenhum catálogo ainda" in after.text

    async def test_deleted_catalog_excluded_from_dashboard_counts(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_catalog: Catalog,
    ) -> None:
        """Catálogo deletado some dos contadores do /dashboard."""
        await _login(client, sample_api_key)
        # Estado inicial: 1 catálogo ready (sample_catalog).
        before = await client.get("/dashboard")
        assert before.status_code == 200

        await client.post(f"/catalogs/{sample_catalog.id}/delete")
        after = await client.get("/dashboard")
        assert after.status_code == 200
        # Heurística: o nome do catálogo apareceria como atividade recente.
        # Como o sample_catalog é a única atividade da brand, soft-delete o
        # remove e o dashboard volta a mostrar o estado vazio das atividades.
        assert sample_catalog.name not in after.text

    async def test_delete_other_brand_returns_404(
        self,
        client: AsyncClient,
        sample_api_key: str,
        db_session: AsyncSession,
    ) -> None:
        from catalogflow.modules.auth import service as auth_service

        other = await auth_service.create_brand(
            db_session, slug="outra3", name="Outra Marca 3"
        )
        await db_session.commit()
        foreign = Catalog(brand_id=other.id, name="Catálogo alheio", status="ready")
        db_session.add(foreign)
        await db_session.commit()
        await db_session.refresh(foreign)

        await _login(client, sample_api_key)
        resp = await client.post(f"/catalogs/{foreign.id}/delete")
        assert resp.status_code == 404
        # Garante que NÃO foi marcado como excluído.
        await db_session.refresh(foreign)
        assert foreign.deleted_at is None


class TestSoftDeleteOrder:
    """Soft-delete de pedido via POST /orders/{id}/delete.

    Marca o Order e o Romaneio associado (se houver). Pedido excluído
    some da lista; pedido de outra brand → 404.
    """

    async def test_delete_marks_deleted_at_and_redirects(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        db_session: AsyncSession,
    ) -> None:
        await _login(client, sample_api_key)
        resp = await client.post(f"/orders/{sample_order.id}/delete")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/orders?notice=order_deleted"

        await db_session.refresh(sample_order)
        assert sample_order.deleted_at is not None
        assert sample_order.deleted_by is not None

    async def test_delete_cascades_to_romaneio(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
        db_session: AsyncSession,
    ) -> None:
        """Pedido com Romaneio: ambos ficam com deleted_at preenchido."""
        romaneio = Romaneio(
            order_id=sample_order.id,
            brand_id=sample_order.brand_id,
            output_key=f"{sample_order.brand_id}/orders/{sample_order.id}/romaneio.pdf",
        )
        db_session.add(romaneio)
        await db_session.commit()
        await db_session.refresh(romaneio)
        assert romaneio.deleted_at is None

        await _login(client, sample_api_key)
        await client.post(f"/orders/{sample_order.id}/delete")

        # Reler o romaneio do banco pela chave primária.
        rom = await db_session.scalar(
            select(Romaneio).where(Romaneio.id == romaneio.id)
        )
        assert rom is not None
        assert rom.deleted_at is not None

    async def test_deleted_order_hidden_from_list(
        self,
        client: AsyncClient,
        sample_api_key: str,
        sample_order: Order,
    ) -> None:
        await _login(client, sample_api_key)
        before = await client.get("/orders")
        assert sample_order.lojista_name is not None
        assert sample_order.lojista_name in before.text

        await client.post(f"/orders/{sample_order.id}/delete")
        after = await client.get("/orders")
        assert sample_order.lojista_name not in after.text

    async def test_delete_other_brand_returns_404(
        self,
        client: AsyncClient,
        sample_api_key: str,
        db_session: AsyncSession,
    ) -> None:
        from catalogflow.modules.auth import service as auth_service

        other = await auth_service.create_brand(
            db_session, slug="outra4", name="Outra Marca 4"
        )
        await db_session.commit()
        foreign = Order(
            brand_id=other.id,
            lojista_name="Loja Secreta 2",
            status="extracted",
        )
        db_session.add(foreign)
        await db_session.commit()
        await db_session.refresh(foreign)

        await _login(client, sample_api_key)
        resp = await client.post(f"/orders/{foreign.id}/delete")
        assert resp.status_code == 404
        await db_session.refresh(foreign)
        assert foreign.deleted_at is None


class TestWebErrorPages:
    """Garante que rotas web nunca retornam JSON para o navegador.

    Estes testes precisam dos exception handlers globais registrados em
    `main.create_app()`. O conftest desta suite monta um FastAPI mínimo
    sem esses handlers, então construímos um app dedicado aqui.
    """

    async def test_unknown_web_route_returns_html_404(self) -> None:
        """Browser pedindo página inexistente → HTML 404 elegante.

        Passamos `Accept: text/html` para simular o User-Agent — é a
        mesma heurística que `_is_web_path` em main.py usa pra decidir
        entre envelope JSON e template HTML.
        """
        from httpx import ASGITransport, AsyncClient

        from catalogflow.main import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as ac:
            resp = await ac.get(
                "/this-page-does-not-exist",
                headers={"Accept": "text/html"},
            )

        assert resp.status_code == 404
        assert "text/html" in resp.headers.get("content-type", "")
        # Template elegante do projeto, não a página padrão do Starlette.
        assert "Voltar ao início" in resp.text

    async def test_api_unknown_route_remains_json(self) -> None:
        """Cliente API (sem Accept text/html) pra rota /api/v1/* → JSON."""
        from httpx import ASGITransport, AsyncClient

        from catalogflow.main import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as ac:
            resp = await ac.get("/api/v1/no-such-endpoint")

        assert resp.status_code == 404
        assert "application/json" in resp.headers.get("content-type", "")
