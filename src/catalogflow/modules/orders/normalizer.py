"""Engine puro de normalização de pedidos.

Contrato (CLAUDE.md):
    RawOrderData + list[CatalogProduct] | None → NormalizedOrderData
    Zero I/O. O service é quem carrega `CatalogProduct` do banco (com
    selectinload) e injeta no normalizer.

Responsabilidades:
    - Agregar quantidades duplicadas em (sku, color_index, size).
      Necessário quando um PDF mistura v1+v2 e o mesmo (SKU, color=1, size)
      aparece nos dois formatos.
    - Quando catalog_products fornecido: enriquecer product_name,
      unit_price e color_hex (via swatches).
    - Sempre calcular totais (total_items, total_pecas, valor_total, n_skus).
    - Adicionar warnings para SKUs presentes no PDF ausentes do catálogo.
    - Ordenar por (page_index, color_index, size) quando possível; fallback
      (sku, color_index, size) sem catálogo.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from catalogflow.modules.orders.extractor import RawOrderData

if TYPE_CHECKING:  # evita ciclo de import em runtime
    from catalogflow.modules.catalog.models import CatalogProduct


# ──────────────────────────────────────────────
#  Dataclasses de saída
# ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NormalizedOrderItem:
    """Item enriquecido — payload pronto para persistir como `OrderItem`."""

    sku: str
    product_name: str | None
    color_index: int
    color_hex: str | None
    size: str
    quantity: int
    unit_price: Decimal | None

    @property
    def subtotal(self) -> Decimal | None:
        if self.unit_price is None:
            return None
        return self.unit_price * Decimal(self.quantity)


@dataclass(frozen=True, slots=True)
class NormalizedTotals:
    """Agregados monetários e contagens do pedido."""

    total_items: int
    total_pecas: int
    valor_total: Decimal
    n_skus: int


@dataclass(frozen=True, slots=True)
class NormalizedOrderData:
    """Output canônico — input pronto para o `RomaneioBuilder` e persistência."""

    items: list[NormalizedOrderItem]
    totals: NormalizedTotals
    source_format: Literal["v1", "v2", "mixed"]
    warnings: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────
#  Normalizer
# ──────────────────────────────────────────────


class OrderNormalizer:
    """Transforma `RawOrderData` em `NormalizedOrderData` pronto para uso."""

    def normalize(
        self,
        raw: RawOrderData,
        catalog_products: list[CatalogProduct] | None = None,
    ) -> NormalizedOrderData:
        """Agrega + enriquece + calcula totais.

        Args:
            raw: saída do `OrderExtractor`.
            catalog_products: opcional. Quando presente, viabiliza
                enriquecimento por SKU (nome, preço, cor hex).
        """
        # 1. Agrega duplicatas por (sku, color_index, size).
        aggregated: dict[tuple[str, int, str], int] = defaultdict(int)
        for item in raw.items:
            key = (item.sku, item.color_index, item.size)
            aggregated[key] += item.quantity

        # 2. Indexa catalog_products por SKU para lookup O(1).
        index = _build_catalog_index(catalog_products or [])

        # 3. Coleta SKUs ausentes do catálogo (warning).
        warnings: list[str] = []
        if catalog_products is not None:
            sku_pedido = {sku for (sku, _, _) in aggregated}
            sku_no_catalogo = sku_pedido - set(index.keys())
            for sku in sorted(sku_no_catalogo):
                warnings.append(f"SKU {sku} presente no PDF mas ausente do catálogo")

        # 4. Materializa NormalizedOrderItem com enriquecimento opcional.
        items: list[NormalizedOrderItem] = []
        for (sku, color_index, size), quantity in aggregated.items():
            product = index.get(sku)
            color_hex = _color_hex_for(product, color_index)
            items.append(
                NormalizedOrderItem(
                    sku=sku,
                    product_name=product.name if product is not None else None,
                    color_index=color_index,
                    color_hex=color_hex,
                    size=size,
                    quantity=quantity,
                    unit_price=product.price if product is not None else None,
                )
            )

        # 5. Ordena: (page_index, color_index, size) com catálogo;
        #            (sku, color_index, size) sem catálogo.
        items.sort(key=_sort_key_factory(index))

        # 6. Calcula totais.
        totals = _calculate_totals(items)

        return NormalizedOrderData(
            items=items,
            totals=totals,
            source_format=raw.source_format,
            warnings=warnings,
        )


# ──────────────────────────────────────────────
#  Helpers puros
# ──────────────────────────────────────────────


def _build_catalog_index(products: list[CatalogProduct]) -> dict[str, CatalogProduct]:
    """Mapeia SKU → primeiro `CatalogProduct` encontrado.

    Se o mesmo SKU aparece em múltiplas páginas (raro mas possível em
    catálogos com 2 cores em páginas separadas), preserva o primeiro pelo
    menor `page_index` — bate com a ordenação esperada do romaneio.
    """
    index: dict[str, CatalogProduct] = {}
    for product in sorted(products, key=lambda p: p.page_index):
        if product.sku not in index:
            index[product.sku] = product
    return index


def _color_hex_for(
    product: CatalogProduct | None,
    color_index: int,
) -> str | None:
    """Retorna o hex do swatch correspondente a `color_index` (1-based)."""
    if product is None:
        return None
    swatches = product.swatches or []
    idx = color_index - 1
    if idx < 0 or idx >= len(swatches):
        return None
    swatch = swatches[idx]
    if not isinstance(swatch, dict):
        return None
    fill_hex = swatch.get("fill_hex")
    if not isinstance(fill_hex, str):
        return None
    return fill_hex


_SIZE_ORDER = {"PP": 0, "P": 1, "M": 2, "G": 3, "GG": 4}


def _size_sort_value(size: str) -> tuple[int, str]:
    """Ordena tamanhos canônicos (PP→GG); desconhecidos vão para o fim."""
    if size in _SIZE_ORDER:
        return (_SIZE_ORDER[size], "")
    return (len(_SIZE_ORDER), size)


def _sort_key_factory(
    index: dict[str, CatalogProduct],
) -> Callable[[NormalizedOrderItem], tuple[Any, ...]]:
    """Devolve uma `key` para `list.sort` apropriada à presença de catálogo."""
    if index:

        def with_catalog(item: NormalizedOrderItem) -> tuple[Any, ...]:
            product = index.get(item.sku)
            page_index = product.page_index if product is not None else 10_000
            return (page_index, item.color_index, _size_sort_value(item.size))

        return with_catalog

    def without_catalog(item: NormalizedOrderItem) -> tuple[Any, ...]:
        return (item.sku, item.color_index, _size_sort_value(item.size))

    return without_catalog


def _calculate_totals(items: list[NormalizedOrderItem]) -> NormalizedTotals:
    """Soma peças e valor; conta linhas e SKUs distintos."""
    total_pecas = sum(item.quantity for item in items)
    valor_total = sum(
        (item.subtotal for item in items if item.subtotal is not None),
        start=Decimal("0"),
    )
    n_skus = len({item.sku for item in items})
    return NormalizedTotals(
        total_items=len(items),
        total_pecas=total_pecas,
        valor_total=valor_total,
        n_skus=n_skus,
    )
