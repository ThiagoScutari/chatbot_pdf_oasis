"""Camada de leitura específica da UI web.

Funções puras de leitura usadas pelas páginas (dashboard, lista de
catálogos). Mantêm o `web/router.py` enxuto e o acesso a SQL isolado
dos templates.

Multi-tenancy: toda função aqui recebe um `brand_id` explícito e o
inclui no `WHERE`. Nunca consulte essas tabelas sem filtro de brand
a partir da camada web.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalogflow.modules.catalog.models import Catalog, CatalogProduct, Job
from catalogflow.modules.orders.models import Order, OrderItem
from catalogflow.modules.romaneio.models import Romaneio
from catalogflow.modules.stock.models import ErpSubmission, StockCheck

# ──────────────────────────────────────────────
#  Dashboard
# ──────────────────────────────────────────────


@dataclass(slots=True)
class DashboardCounts:
    """Contagens exibidas nos 4 cards do dashboard."""

    catalogs_total: int
    catalogs_ready: int
    orders_total: int
    romaneios_total: int


@dataclass(slots=True)
class ActivityItem:
    """Linha unificada de atividade recente (catálogo ou pedido)."""

    kind: Literal["catalog", "order"]
    entity_id: UUID
    title: str
    status: str
    occurred_at: datetime


async def get_dashboard_counts(db: AsyncSession, brand_id: UUID) -> DashboardCounts:
    """Retorna as quatro contagens do dashboard em paralelo (single round-trip por query).

    Soft-deletes ficam fora — quem foi excluído pela UI não aparece no
    dashboard mesmo enquanto o registro ainda existir no banco.
    """
    catalogs_total = await db.scalar(
        select(func.count(Catalog.id)).where(
            Catalog.brand_id == brand_id,
            Catalog.deleted_at.is_(None),
        )
    )
    catalogs_ready = await db.scalar(
        select(func.count(Catalog.id)).where(
            Catalog.brand_id == brand_id,
            Catalog.status == "ready",
            Catalog.deleted_at.is_(None),
        )
    )
    orders_total = await db.scalar(
        select(func.count(Order.id)).where(
            Order.brand_id == brand_id,
            Order.deleted_at.is_(None),
        )
    )
    romaneios_total = await db.scalar(
        select(func.count(Romaneio.id)).where(
            Romaneio.brand_id == brand_id,
            Romaneio.deleted_at.is_(None),
        )
    )
    return DashboardCounts(
        catalogs_total=int(catalogs_total or 0),
        catalogs_ready=int(catalogs_ready or 0),
        orders_total=int(orders_total or 0),
        romaneios_total=int(romaneios_total or 0),
    )


async def get_recent_activity(
    db: AsyncSession,
    brand_id: UUID,
    *,
    limit: int = 5,
) -> list[ActivityItem]:
    """Últimos N eventos da brand, misturando catálogos e pedidos por data.

    Faz duas queries separadas (Catalog e Order), pega `limit` de cada,
    funde no Python e ordena. Custo aceitável até 5-10 itens; se virar
    relatório real, vira UNION ALL no banco.
    """
    cat_stmt = (
        select(Catalog.id, Catalog.name, Catalog.status, Catalog.created_at)
        .where(Catalog.brand_id == brand_id, Catalog.deleted_at.is_(None))
        .order_by(Catalog.created_at.desc())
        .limit(limit)
    )
    ord_stmt = (
        select(Order.id, Order.lojista_name, Order.status, Order.created_at)
        .where(Order.brand_id == brand_id, Order.deleted_at.is_(None))
        .order_by(Order.created_at.desc())
        .limit(limit)
    )

    cat_rows = (await db.execute(cat_stmt)).all()
    ord_rows = (await db.execute(ord_stmt)).all()

    items: list[ActivityItem] = []
    for cat in cat_rows:
        items.append(
            ActivityItem(
                kind="catalog",
                entity_id=cat.id,
                title=cat.name,
                status=cat.status,
                occurred_at=cat.created_at,
            )
        )
    for order in ord_rows:
        items.append(
            ActivityItem(
                kind="order",
                entity_id=order.id,
                title=order.lojista_name or "Lojista não identificada",
                status=order.status,
                occurred_at=order.created_at,
            )
        )

    items.sort(key=lambda it: it.occurred_at, reverse=True)
    return items[:limit]


# ──────────────────────────────────────────────
#  Listagem de catálogos
# ──────────────────────────────────────────────


@dataclass(slots=True)
class CatalogListItem:
    """Versão achatada de Catalog para a listagem (sem produtos)."""

    id: UUID
    name: str
    collection: str | None
    n_skus: int | None
    status: str
    created_at: datetime


@dataclass(slots=True)
class CatalogListPage:
    """Página de catálogos + metadados de paginação."""

    items: list[CatalogListItem]
    total: int
    page: int
    page_size: int

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page * self.page_size < self.total


async def list_catalogs(
    db: AsyncSession,
    brand_id: UUID,
    *,
    page: int = 1,
    page_size: int = 20,
) -> CatalogListPage:
    """Página de catálogos da brand, mais recentes primeiro."""
    page = max(page, 1)
    offset = (page - 1) * page_size

    total = await db.scalar(
        select(func.count(Catalog.id)).where(
            Catalog.brand_id == brand_id,
            Catalog.deleted_at.is_(None),
        )
    )

    stmt = (
        select(
            Catalog.id,
            Catalog.name,
            Catalog.collection,
            Catalog.n_skus,
            Catalog.status,
            Catalog.created_at,
        )
        .where(Catalog.brand_id == brand_id, Catalog.deleted_at.is_(None))
        .order_by(Catalog.created_at.desc())
        .limit(page_size)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).all()
    items = [
        CatalogListItem(
            id=r.id,
            name=r.name,
            collection=r.collection,
            n_skus=r.n_skus,
            status=r.status,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return CatalogListPage(
        items=items,
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@dataclass(slots=True)
class CatalogOption:
    """Catálogo enxuto para popular o `<select>` de origem no upload de pedido."""

    id: UUID
    name: str


async def list_ready_catalog_options(
    db: AsyncSession,
    brand_id: UUID,
    *,
    limit: int = 200,
) -> list[CatalogOption]:
    """Catálogos com status=ready da brand, mais recentes primeiro.

    Usado pelo dropdown "Catálogo de origem" na página de upload de pedido.
    Limit alto e sem paginação — em escala média de brand textil (dezenas
    de catálogos/ano), cabe num combo simples.
    """
    stmt = (
        select(Catalog.id, Catalog.name)
        .where(
            Catalog.brand_id == brand_id,
            Catalog.status == "ready",
            Catalog.deleted_at.is_(None),
        )
        .order_by(Catalog.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [CatalogOption(id=r.id, name=r.name) for r in rows]


async def get_catalog_status(
    db: AsyncSession,
    catalog_id: UUID,
    brand_id: UUID,
) -> str | None:
    """Status atual de um catálogo, ou `None` se inexistente / de outra brand."""
    stmt = select(Catalog.status).where(
        Catalog.id == catalog_id,
        Catalog.brand_id == brand_id,
        Catalog.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# ──────────────────────────────────────────────
#  Detalhe do catálogo
# ──────────────────────────────────────────────


async def get_catalog(
    db: AsyncSession,
    catalog_id: UUID,
    brand_id: UUID,
) -> Catalog | None:
    """Devolve o catálogo (sem produtos) ou `None` se não existe / outra brand."""
    stmt = select(Catalog).where(
        Catalog.id == catalog_id,
        Catalog.brand_id == brand_id,
        Catalog.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


@dataclass(slots=True)
class ProductListPage:
    """Página de produtos do catálogo (paginada para evitar tabela gigante)."""

    items: list[CatalogProduct]
    total: int
    page: int
    page_size: int

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page * self.page_size < self.total


async def list_catalog_products(
    db: AsyncSession,
    catalog_id: UUID,
    *,
    page: int = 1,
    page_size: int = 20,
) -> ProductListPage:
    """Página de produtos detectados, ordenados por `page_index` no PDF."""
    page = max(page, 1)
    offset = (page - 1) * page_size

    total = await db.scalar(
        select(func.count(CatalogProduct.id)).where(CatalogProduct.catalog_id == catalog_id)
    )
    stmt = (
        select(CatalogProduct)
        .where(CatalogProduct.catalog_id == catalog_id)
        .order_by(CatalogProduct.page_index, CatalogProduct.sku)
        .limit(page_size)
        .offset(offset)
    )
    items = list((await db.execute(stmt)).scalars().all())
    return ProductListPage(
        items=items,
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


# ──────────────────────────────────────────────
#  Job
# ──────────────────────────────────────────────


async def get_job_for_brand(
    db: AsyncSession,
    job_id: UUID,
    brand_id: UUID,
) -> Job | None:
    """Devolve o Job se pertence à brand, ou `None`."""
    stmt = select(Job).where(Job.id == job_id, Job.brand_id == brand_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# ──────────────────────────────────────────────
#  Lista de pedidos
# ──────────────────────────────────────────────


@dataclass(slots=True)
class OrderListItem:
    """Linha achatada da lista de pedidos (com nome do catálogo via JOIN)."""

    id: UUID
    lojista_name: str
    catalog_name: str | None
    total_pecas: int | None
    status: str
    created_at: datetime


@dataclass(slots=True)
class OrderListPage:
    items: list[OrderListItem]
    total: int
    page: int
    page_size: int

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page * self.page_size < self.total


async def list_orders(
    db: AsyncSession,
    brand_id: UUID,
    *,
    page: int = 1,
    page_size: int = 20,
) -> OrderListPage:
    """Página de pedidos da brand, mais recentes primeiro.

    LEFT JOIN com Catalog para trazer o nome do catálogo de origem
    quando `Order.catalog_id` está preenchido.
    """
    page = max(page, 1)
    offset = (page - 1) * page_size

    total = await db.scalar(
        select(func.count(Order.id)).where(
            Order.brand_id == brand_id,
            Order.deleted_at.is_(None),
        )
    )

    stmt = (
        select(
            Order.id,
            Order.lojista_name,
            Order.total_pecas,
            Order.status,
            Order.created_at,
            Catalog.name.label("catalog_name"),
        )
        .outerjoin(Catalog, Order.catalog_id == Catalog.id)
        .where(Order.brand_id == brand_id, Order.deleted_at.is_(None))
        .order_by(Order.created_at.desc())
        .limit(page_size)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).all()
    items = [
        OrderListItem(
            id=row.id,
            lojista_name=row.lojista_name or "Lojista não identificada",
            catalog_name=row.catalog_name,
            total_pecas=row.total_pecas,
            status=row.status,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return OrderListPage(
        items=items,
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


async def get_order_status(
    db: AsyncSession,
    order_id: UUID,
    brand_id: UUID,
) -> str | None:
    """Status atual de um pedido (None se outro tenant / inexistente)."""
    stmt = select(Order.status).where(
        Order.id == order_id,
        Order.brand_id == brand_id,
        Order.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# ──────────────────────────────────────────────
#  Detalhe do pedido
# ──────────────────────────────────────────────


@dataclass(slots=True)
class OrderDetail:
    """Conjunto coeso de tudo que a tela de detalhe precisa."""

    order: Order
    catalog_name: str | None
    romaneio: Romaneio | None
    stock_check: StockCheck | None
    submission: ErpSubmission | None


async def get_order_detail(
    db: AsyncSession,
    order_id: UUID,
    brand_id: UUID,
) -> OrderDetail | None:
    """Carrega o Order com items + romaneio + última stock_check + submission."""
    stmt = (
        select(Order)
        .where(
            Order.id == order_id,
            Order.brand_id == brand_id,
            Order.deleted_at.is_(None),
        )
        .options(
            selectinload(Order.items),
            selectinload(Order.romaneio),
        )
    )
    order = (await db.execute(stmt)).scalar_one_or_none()
    if order is None:
        return None

    # Romaneio carregado via selectinload pode estar soft-deleted: a UI
    # de pedido excluído nem chega aqui, mas se um pedido vivo tiver um
    # romaneio marcado como excluído, escondemos da UI.
    if order.romaneio is not None and order.romaneio.deleted_at is not None:
        order.romaneio = None

    catalog_name: str | None = None
    if order.catalog_id is not None:
        catalog_name = await db.scalar(
            select(Catalog.name).where(
                Catalog.id == order.catalog_id,
                Catalog.deleted_at.is_(None),
            )
        )

    stock_check = (
        await db.execute(
            select(StockCheck)
            .where(
                StockCheck.order_id == order_id,
                StockCheck.brand_id == brand_id,
            )
            .order_by(StockCheck.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    submission = (
        await db.execute(
            select(ErpSubmission).where(
                ErpSubmission.order_id == order_id,
                ErpSubmission.brand_id == brand_id,
            )
        )
    ).scalar_one_or_none()

    return OrderDetail(
        order=order,
        catalog_name=catalog_name,
        romaneio=order.romaneio,
        stock_check=stock_check,
        submission=submission,
    )


# ──────────────────────────────────────────────
#  Agrupamento de items para a tabela do detalhe
# ──────────────────────────────────────────────


@dataclass(slots=True)
class ColorRow:
    """Uma linha (SKU x cor) com quantidades por tamanho.

    A disponibilidade por tamanho é consultada via `stock_map` no template
    — não duplicamos a info aqui. `stock_status_worst` e `available_total`
    são agregações úteis (worst-case + soma) que evitam recomputar no
    Jinja; mantidas para o template e para o gerador de relatórios.
    """

    color_index: int
    color_hex: str | None
    qty_by_size: dict[str, int]
    total: int
    stock_status_worst: str | None = None
    available_total: int | None = None


@dataclass(slots=True)
class GroupedProduct:
    """Um SKU agrupado, com suas cores e totais."""

    sku: str
    product_name: str
    unit_price: object  # Decimal | None — evita import top-level
    color_rows: list[ColorRow]
    sizes_seen: list[str]
    total_pecas: int
    subtotal: object  # Decimal | None


# Ordem de severidade — define o pior status entre vários itens.
# Quanto MENOR o índice, mais grave. Usado em _worst_status.
_STATUS_SEVERITY: dict[str, int] = {
    "out_of_stock": 0,
    "partial": 1,
    "unknown": 2,
    "available": 3,
}


def _worst_status(statuses: list[str | None]) -> str | None:
    """Retorna o status mais grave da lista (None ignorado)."""
    valid = [s for s in statuses if s is not None]
    if not valid:
        return None
    return min(valid, key=lambda s: _STATUS_SEVERITY.get(s, 4))


# Tipo do lookup: (sku, color_index, size) -> available_qty (None = unknown).
StockMap = dict[tuple[str, int, str], int | None]


def build_stock_map(stock_check: StockCheck | None) -> StockMap:
    """Constrói o mapa `(sku, color_index, size) -> available_qty` a partir
    do snapshot JSONB do último `StockCheck`.

    Vazio quando não há consulta concluída. Inclui também os itens com
    `status="unknown"` — nesses casos `available_qty` vem `None`, e o
    template trata como "consulta falhou para esse item" (mostra "?").
    """
    if stock_check is None or stock_check.status != "completed":
        return {}
    items = stock_check.result.get("items", []) if stock_check.result else []
    result: StockMap = {}
    for entry in items:
        try:
            key = (
                str(entry["sku"]),
                int(entry["color_index"]),
                str(entry["size"]),
            )
        except (KeyError, ValueError, TypeError):
            continue
        available = entry.get("available")
        result[key] = int(available) if isinstance(available, int | float) else None
    return result


def count_pendency_items(stock_map: StockMap, items: list[OrderItem]) -> int:
    """Conta items do pedido cujo status no último check é partial ou out_of_stock.

    Lê de `OrderItem.stock_status` (atualizado pelo service no fim de cada
    consulta) — não precisa olhar o JSONB. `stock_map` é passado apenas para
    deixar claro o contrato; aqui usamos a coluna mais barata.
    """
    del stock_map  # contrato — não precisa nesta implementação
    return sum(1 for item in items if item.stock_status in ("partial", "out_of_stock"))


def group_items_by_sku(
    items: list[OrderItem],
    *,
    canonical_size_order: tuple[str, ...] = ("PP", "P", "M", "G", "GG", "XG"),
) -> list[GroupedProduct]:
    """Agrupa items por SKU → cor → tamanho.

    Devolve uma lista pronta para o template renderizar:
    - `sizes_seen` é a ordem canônica filtrada pelos tamanhos presentes.
    - `color_rows` é uma linha por cor, com `qty_by_size` populado e
      `total` somando todas as quantidades daquela cor.
    - `total_pecas` e `subtotal` somam tudo do SKU.
    """
    from decimal import Decimal

    by_sku: dict[str, list[OrderItem]] = {}
    for item in items:
        by_sku.setdefault(item.sku, []).append(item)

    grouped: list[GroupedProduct] = []
    for sku, sku_items in by_sku.items():
        # Tamanhos presentes nesse SKU, na ordem canônica.
        present_sizes = {it.size for it in sku_items}
        ordered_sizes = [s for s in canonical_size_order if s in present_sizes]
        # Tamanhos fora da ordem canônica entram no final (ex.: "U" único).
        for s in present_sizes - set(ordered_sizes):
            ordered_sizes.append(s)

        # Agrupa por cor.
        by_color: dict[int, list[OrderItem]] = {}
        for it in sku_items:
            by_color.setdefault(it.color_index, []).append(it)

        color_rows: list[ColorRow] = []
        for color_index in sorted(by_color):
            color_items = by_color[color_index]
            qty_by_size = dict.fromkeys(ordered_sizes, 0)
            for it in color_items:
                qty_by_size[it.size] = qty_by_size.get(it.size, 0) + it.quantity

            # Agregação de estoque por cor — pior status entre os tamanhos.
            statuses = [it.stock_status for it in color_items]
            stock_status = _worst_status(statuses)
            # `available_total` só faz sentido quando todos os tamanhos
            # têm disponibilidade conhecida — caso contrário fica None
            # (o template trata como "consulta parcial / incompleta").
            availables = [it.available_qty for it in color_items]
            if stock_status is not None and all(a is not None for a in availables):
                available_total = sum(a for a in availables if a is not None)
            else:
                available_total = None

            color_rows.append(
                ColorRow(
                    color_index=color_index,
                    color_hex=color_items[0].color_hex,
                    qty_by_size=qty_by_size,
                    total=sum(qty_by_size.values()),
                    stock_status_worst=stock_status,
                    available_total=available_total,
                )
            )

        total_pecas = sum(row.total for row in color_rows)
        unit_price = next(
            (it.unit_price for it in sku_items if it.unit_price is not None),
            None,
        )
        subtotal: Decimal | None
        if unit_price is not None:
            subtotal = unit_price * total_pecas
        else:
            subtotal = None

        product_name = (
            next(
                (it.product_name for it in sku_items if it.product_name),
                sku,
            )
            or sku
        )

        grouped.append(
            GroupedProduct(
                sku=sku,
                product_name=product_name,
                unit_price=unit_price,
                color_rows=color_rows,
                sizes_seen=ordered_sizes,
                total_pecas=total_pecas,
                subtotal=subtotal,
            )
        )

    # Ordena por nome do produto pra UX consistente.
    grouped.sort(key=lambda g: g.product_name)
    return grouped
