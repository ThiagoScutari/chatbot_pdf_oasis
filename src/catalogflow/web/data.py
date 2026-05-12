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

from catalogflow.modules.catalog.models import Catalog, CatalogProduct, Job
from catalogflow.modules.orders.models import Order
from catalogflow.modules.romaneio.models import Romaneio

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
    """Retorna as quatro contagens do dashboard em paralelo (single round-trip por query)."""
    catalogs_total = await db.scalar(
        select(func.count(Catalog.id)).where(Catalog.brand_id == brand_id)
    )
    catalogs_ready = await db.scalar(
        select(func.count(Catalog.id)).where(
            Catalog.brand_id == brand_id,
            Catalog.status == "ready",
        )
    )
    orders_total = await db.scalar(
        select(func.count(Order.id)).where(Order.brand_id == brand_id)
    )
    romaneios_total = await db.scalar(
        select(func.count(Romaneio.id)).where(Romaneio.brand_id == brand_id)
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
        .where(Catalog.brand_id == brand_id)
        .order_by(Catalog.created_at.desc())
        .limit(limit)
    )
    ord_stmt = (
        select(Order.id, Order.lojista_name, Order.status, Order.created_at)
        .where(Order.brand_id == brand_id)
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
        select(func.count(Catalog.id)).where(Catalog.brand_id == brand_id)
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
        .where(Catalog.brand_id == brand_id)
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


async def get_catalog_status(
    db: AsyncSession,
    catalog_id: UUID,
    brand_id: UUID,
) -> str | None:
    """Status atual de um catálogo, ou `None` se inexistente / de outra brand."""
    stmt = select(Catalog.status).where(
        Catalog.id == catalog_id,
        Catalog.brand_id == brand_id,
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
        select(func.count(CatalogProduct.id)).where(
            CatalogProduct.catalog_id == catalog_id
        )
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
