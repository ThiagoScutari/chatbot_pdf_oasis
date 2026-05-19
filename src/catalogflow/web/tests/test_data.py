"""Testes do `web/data.py` — camada de leitura específica da UI.

Foco: seed direto em DB para acionar os caminhos com dados (a maioria
das linhas faltantes está nos loops que constroem dataclasses). Cada
teste cria objetos mínimos e valida o shape do retorno.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from catalogflow.modules.auth import service as auth_service
from catalogflow.modules.auth.models import Brand
from catalogflow.modules.catalog.models import Catalog, CatalogProduct, Job
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.romaneio.models import Romaneio
from catalogflow.modules.stock.models import ErpSubmission, StockCheck
from catalogflow.web.data import (
    ColorRow,
    _worst_status,
    build_stock_map,
    count_pendency_items,
    get_catalog,
    get_catalog_status,
    get_dashboard_counts,
    get_job_for_brand,
    get_order_detail,
    get_order_status,
    get_recent_activity,
    group_items_by_sku,
    list_catalog_products,
    list_catalogs,
    list_orders,
    list_ready_catalog_options,
)


@pytest.fixture
async def brand(db_session: AsyncSession) -> Brand:
    b = await auth_service.create_brand(db_session, slug="data-test", name="DataTest")
    await db_session.commit()
    return b


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def _seed_catalog(
    db: AsyncSession, brand: Brand, *, status: str = "ready", name: str = "Cat A"
) -> Catalog:
    cat = Catalog(brand_id=brand.id, name=name, status=status, n_skus=3, collection="V25")
    db.add(cat)
    await db.flush()
    return cat


async def _seed_order(
    db: AsyncSession,
    brand: Brand,
    *,
    catalog: Catalog | None = None,
    lojista: str | None = "Loja X",
) -> Order:
    order = Order(
        brand_id=brand.id,
        catalog_id=catalog.id if catalog else None,
        lojista_name=lojista,
        status="draft",
        total_pecas=10,
    )
    db.add(order)
    await db.flush()
    return order


# ──────────────────────────────────────────────
#  Dashboard counts + recent activity
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestDashboardCounts:
    async def test_counts_reflect_real_data(self, db_session: AsyncSession, brand: Brand) -> None:
        """Contagens batem com o que foi inserido (ready conta separado)."""
        await _seed_catalog(db_session, brand, status="ready")
        await _seed_catalog(db_session, brand, status="processing")
        await _seed_order(db_session, brand)
        await db_session.flush()

        counts = await get_dashboard_counts(db_session, brand.id)
        assert counts.catalogs_total == 2
        assert counts.catalogs_ready == 1
        assert counts.orders_total == 1
        assert counts.romaneios_total == 0

    async def test_soft_deleted_are_excluded(self, db_session: AsyncSession, brand: Brand) -> None:
        """Catálogos com `deleted_at` definido somem da contagem."""
        cat = await _seed_catalog(db_session, brand, status="ready")
        cat.deleted_at = _now()
        await db_session.flush()

        counts = await get_dashboard_counts(db_session, brand.id)
        assert counts.catalogs_total == 0
        assert counts.catalogs_ready == 0


@pytest.mark.asyncio
class TestRecentActivity:
    async def test_mixes_catalogs_and_orders_sorted_by_date(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """Mistura catálogos + pedidos, ordenados por created_at desc."""
        cat = await _seed_catalog(db_session, brand, name="Verão 2025")
        await _seed_order(db_session, brand, catalog=cat, lojista="Loja Z")
        await db_session.flush()

        items = await get_recent_activity(db_session, brand.id, limit=5)
        kinds = {i.kind for i in items}
        assert kinds == {"catalog", "order"}
        assert any(i.title == "Verão 2025" for i in items)
        assert any(i.title == "Loja Z" for i in items)

    async def test_order_without_name_falls_back(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """Pedido sem `lojista_name` mostra 'Lojista não identificada'."""
        await _seed_order(db_session, brand, lojista=None)
        await db_session.flush()
        items = await get_recent_activity(db_session, brand.id)
        assert any(i.title == "Lojista não identificada" for i in items)


# ──────────────────────────────────────────────
#  Listagem de catálogos + status + detalhes
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestCatalogListings:
    async def test_list_catalogs_returns_page_with_items(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """Página de catálogos é paginada e devolve itens com metadados."""
        await _seed_catalog(db_session, brand, name="Cat 1")
        await _seed_catalog(db_session, brand, name="Cat 2")
        await db_session.flush()

        page = await list_catalogs(db_session, brand.id, page=1, page_size=10)
        assert page.total == 2
        assert {i.name for i in page.items} == {"Cat 1", "Cat 2"}
        assert page.has_prev is False
        assert page.has_next is False

    async def test_list_catalogs_pagination_flags(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """has_prev/has_next variam com a página atual."""
        for i in range(3):
            await _seed_catalog(db_session, brand, name=f"Cat {i}")
        await db_session.flush()
        page2 = await list_catalogs(db_session, brand.id, page=2, page_size=1)
        assert page2.has_prev is True
        assert page2.has_next is True

    async def test_list_ready_catalog_options(self, db_session: AsyncSession, brand: Brand) -> None:
        """Só catálogos `ready` aparecem nas opções de upload."""
        await _seed_catalog(db_session, brand, status="ready", name="OK")
        await _seed_catalog(db_session, brand, status="processing", name="NÃO")
        await db_session.flush()
        opts = await list_ready_catalog_options(db_session, brand.id)
        assert {o.name for o in opts} == {"OK"}

    async def test_get_catalog_status_returns_value(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """Status do catálogo é devolvido como string."""
        cat = await _seed_catalog(db_session, brand, status="ready")
        await db_session.flush()
        assert await get_catalog_status(db_session, cat.id, brand.id) == "ready"

    async def test_get_catalog_status_returns_none_for_other_brand(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """Catálogo de outra brand → None (isolamento multi-tenant)."""
        cat = await _seed_catalog(db_session, brand)
        await db_session.flush()
        assert await get_catalog_status(db_session, cat.id, uuid4()) is None

    async def test_get_catalog_returns_object(self, db_session: AsyncSession, brand: Brand) -> None:
        """`get_catalog` devolve o ORM completo."""
        cat = await _seed_catalog(db_session, brand)
        await db_session.flush()
        got = await get_catalog(db_session, cat.id, brand.id)
        assert got is not None
        assert got.id == cat.id

    async def test_list_catalog_products_paginates(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """Produtos são devolvidos em página, ordenados por page_index."""
        cat = await _seed_catalog(db_session, brand)
        for i in range(3):
            db_session.add(
                CatalogProduct(
                    catalog_id=cat.id,
                    sku=f"SKU{i}",
                    page_index=i,
                    sizes=["P"],
                    n_colors=1,
                    swatches=[],
                )
            )
        await db_session.flush()

        page = await list_catalog_products(db_session, cat.id, page=1, page_size=10)
        assert page.total == 3
        assert [p.sku for p in page.items] == ["SKU0", "SKU1", "SKU2"]
        assert page.has_prev is False
        assert page.has_next is False


# ──────────────────────────────────────────────
#  Job
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetJobForBrand:
    async def test_returns_job_when_brand_matches(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """Job da brand → devolve o objeto."""
        job = Job(brand_id=brand.id, job_type="catalog.process", status="success")
        db_session.add(job)
        await db_session.flush()
        got = await get_job_for_brand(db_session, job.id, brand.id)
        assert got is not None
        assert got.id == job.id

    async def test_returns_none_for_other_brand(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """Job de brand diferente → None."""
        job = Job(brand_id=brand.id, job_type="catalog.process", status="success")
        db_session.add(job)
        await db_session.flush()
        assert await get_job_for_brand(db_session, job.id, uuid4()) is None


# ──────────────────────────────────────────────
#  Lista de pedidos
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestListOrders:
    async def test_lists_orders_with_catalog_name_via_join(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """LEFT JOIN traz o nome do catálogo quando presente."""
        cat = await _seed_catalog(db_session, brand, name="V25 Coll")
        await _seed_order(db_session, brand, catalog=cat, lojista="Loja A")
        await _seed_order(db_session, brand, catalog=None, lojista=None)
        await db_session.flush()
        page = await list_orders(db_session, brand.id)
        assert page.total == 2
        names = {(i.lojista_name, i.catalog_name) for i in page.items}
        assert ("Loja A", "V25 Coll") in names
        assert ("Lojista não identificada", None) in names

    async def test_get_order_status(self, db_session: AsyncSession, brand: Brand) -> None:
        """get_order_status devolve o status atual ou None."""
        order = await _seed_order(db_session, brand)
        await db_session.flush()
        assert await get_order_status(db_session, order.id, brand.id) == "draft"
        assert await get_order_status(db_session, order.id, uuid4()) is None


# ──────────────────────────────────────────────
#  Detalhe do pedido — vários relacionamentos
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetOrderDetail:
    async def test_returns_none_for_unknown_order(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """Order inexistente → None."""
        assert await get_order_detail(db_session, uuid4(), brand.id) is None

    async def test_hydrates_romaneio_stock_check_submission(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """Detalhe carrega romaneio + stock_check + submission + catalog_name."""
        cat = await _seed_catalog(db_session, brand, name="V25")
        order = await _seed_order(db_session, brand, catalog=cat)
        rom = Romaneio(order_id=order.id, brand_id=brand.id, output_key="x.pdf")
        sc = StockCheck(
            order_id=order.id,
            brand_id=brand.id,
            status="completed",
            result={"items": []},
            checked_at=_now(),
        )
        sub = ErpSubmission(
            order_id=order.id,
            brand_id=brand.id,
            status="accepted",
            result={},
            erp_reference="ERP-1",
        )
        db_session.add_all([rom, sc, sub])
        await db_session.flush()

        detail = await get_order_detail(db_session, order.id, brand.id)
        assert detail is not None
        assert detail.catalog_name == "V25"
        assert detail.romaneio is not None
        assert detail.stock_check is not None
        assert detail.submission is not None
        assert detail.submission.erp_reference == "ERP-1"

    async def test_hides_soft_deleted_romaneio(
        self, db_session: AsyncSession, brand: Brand
    ) -> None:
        """Romaneio com `deleted_at` definido não aparece no detalhe."""
        order = await _seed_order(db_session, brand)
        rom = Romaneio(
            order_id=order.id,
            brand_id=brand.id,
            output_key="x.pdf",
            deleted_at=_now(),
        )
        db_session.add(rom)
        await db_session.flush()

        detail = await get_order_detail(db_session, order.id, brand.id)
        assert detail is not None
        assert detail.romaneio is None


# ──────────────────────────────────────────────
#  Helpers puros — _worst_status, build_stock_map, count_pendency
# ──────────────────────────────────────────────


class TestWorstStatus:
    def test_all_none_returns_none(self) -> None:
        """Lista só de None → None."""
        assert _worst_status([None, None]) is None

    def test_picks_most_severe(self) -> None:
        """out_of_stock vence partial vence available."""
        assert _worst_status(["available", "partial", "out_of_stock"]) == "out_of_stock"
        assert _worst_status(["available", "partial"]) == "partial"

    def test_unknown_status_treated_as_least_severe(self) -> None:
        """Status fora do mapa cai pro fallback (4) — não vence nenhum mapeado."""
        assert _worst_status(["foobar", "partial"]) == "partial"


class TestBuildStockMap:
    def test_returns_empty_when_check_is_none(self) -> None:
        """Sem StockCheck → mapa vazio."""
        assert build_stock_map(None) == {}

    def test_returns_empty_when_status_not_completed(self) -> None:
        """StockCheck `pending`/`failed` ignorado."""
        sc = StockCheck(
            order_id=uuid4(),
            brand_id=uuid4(),
            status="pending",
            result={"items": [{"sku": "S1", "color_index": 1, "size": "P", "available": 10}]},
        )
        assert build_stock_map(sc) == {}

    def test_parses_items_into_keyed_map(self) -> None:
        """Items completos viram entradas (sku, color, size) → available_qty."""
        sc = StockCheck(
            order_id=uuid4(),
            brand_id=uuid4(),
            status="completed",
            result={
                "items": [
                    {"sku": "S1", "color_index": 1, "size": "P", "available": 5},
                    {"sku": "S1", "color_index": 1, "size": "M", "available": None},
                    {"sku": "S2", "color_index": 2, "size": "G", "available": 7.0},
                ]
            },
        )
        m = build_stock_map(sc)
        assert m[("S1", 1, "P")] == 5
        assert m[("S1", 1, "M")] is None
        assert m[("S2", 2, "G")] == 7

    def test_malformed_items_are_skipped(self) -> None:
        """Item sem campos obrigatórios é ignorado — não levanta."""
        sc = StockCheck(
            order_id=uuid4(),
            brand_id=uuid4(),
            status="completed",
            result={
                "items": [
                    {"sku": "OK", "color_index": 1, "size": "P", "available": 1},
                    {"sku": "MISSING_COLOR"},
                    {"color_index": "abc", "size": "P", "sku": "BAD"},
                ]
            },
        )
        m = build_stock_map(sc)
        assert list(m.keys()) == [("OK", 1, "P")]

    def test_empty_result_returns_empty(self) -> None:
        """`result` None / sem items → mapa vazio."""
        sc = StockCheck(
            order_id=uuid4(),
            brand_id=uuid4(),
            status="completed",
            result={},
        )
        assert build_stock_map(sc) == {}


class TestCountPendencyItems:
    def test_counts_partial_and_out_of_stock(self) -> None:
        """Conta items com stock_status nos níveis problemáticos."""
        items = [
            OrderItem(
                order_id=uuid4(),
                sku="A",
                color_index=1,
                size="P",
                quantity=1,
                stock_status="partial",
            ),
            OrderItem(
                order_id=uuid4(),
                sku="B",
                color_index=1,
                size="P",
                quantity=1,
                stock_status="available",
            ),
            OrderItem(
                order_id=uuid4(),
                sku="C",
                color_index=1,
                size="P",
                quantity=1,
                stock_status="out_of_stock",
            ),
        ]
        assert count_pendency_items({}, items) == 2


# ──────────────────────────────────────────────
#  group_items_by_sku — agregação multi-cor
# ──────────────────────────────────────────────


class TestGroupItemsBySku:
    def test_groups_by_sku_then_color_with_subtotal(self) -> None:
        """SKU agrupado tem color_rows e subtotal=unit_price*total_pecas."""
        order_id = uuid4()
        items = [
            OrderItem(
                order_id=order_id,
                sku="S1",
                color_index=1,
                size="P",
                quantity=2,
                unit_price=Decimal("10.00"),
                color_hex="#FF0000",
                product_name="Vestido",
                stock_status="available",
                available_qty=5,
            ),
            OrderItem(
                order_id=order_id,
                sku="S1",
                color_index=1,
                size="M",
                quantity=3,
                unit_price=Decimal("10.00"),
                color_hex="#FF0000",
                stock_status="available",
                available_qty=5,
            ),
            OrderItem(
                order_id=order_id,
                sku="S1",
                color_index=2,
                size="P",
                quantity=1,
                unit_price=Decimal("10.00"),
                color_hex="#00FF00",
                stock_status="partial",
                available_qty=None,  # disponibilidade desconhecida
            ),
        ]
        grouped = group_items_by_sku(items)
        assert len(grouped) == 1
        product = grouped[0]
        assert product.sku == "S1"
        assert product.product_name == "Vestido"
        assert product.total_pecas == 6
        assert product.subtotal == Decimal("60.00")
        # Duas cores
        assert len(product.color_rows) == 2
        # available_total é None quando há item sem available_qty
        partial_row = next(r for r in product.color_rows if r.color_index == 2)
        assert partial_row.available_total is None

    def test_unknown_sizes_appended_at_end(self) -> None:
        """Tamanhos fora da ordem canônica entram no final."""
        items = [
            OrderItem(
                order_id=uuid4(),
                sku="S2",
                color_index=1,
                size="U",  # fora da ordem canônica
                quantity=1,
            ),
            OrderItem(
                order_id=uuid4(),
                sku="S2",
                color_index=1,
                size="P",
                quantity=1,
            ),
        ]
        grouped = group_items_by_sku(items)
        assert grouped[0].sizes_seen == ["P", "U"]

    def test_product_name_fallback_to_sku(self) -> None:
        """SKU sem product_name → product_name vira o SKU."""
        items = [
            OrderItem(
                order_id=uuid4(),
                sku="S3",
                color_index=1,
                size="P",
                quantity=1,
            )
        ]
        grouped = group_items_by_sku(items)
        assert grouped[0].product_name == "S3"
        assert grouped[0].subtotal is None  # sem unit_price


class TestColorRowDefaults:
    def test_default_stock_fields_are_none(self) -> None:
        """ColorRow tem stock_status_worst/available_total None por padrão."""
        row = ColorRow(color_index=1, color_hex=None, qty_by_size={"P": 1}, total=1)
        assert row.stock_status_worst is None
        assert row.available_total is None
