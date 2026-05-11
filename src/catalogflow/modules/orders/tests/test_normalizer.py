"""Testes do `OrderNormalizer` — função pura RawOrderData → NormalizedOrderData.

Cenários cobertos:
    - Normalização sem catálogo (campos enriquecíveis ficam None)
    - Normalização com catálogo (product_name, unit_price, color_hex preenchidos)
    - SKU presente no PDF ausente do catálogo → warning + item preservado
    - Totais (total_items, total_pecas, valor_total, n_skus)
    - Agrupamento por SKU mantendo cores diferentes em itens separados
    - Agregação de duplicatas em (sku, color_index, size)
    - Ordenação por page_index quando catálogo disponível; alfabética sem catálogo
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from catalogflow.modules.catalog.models import CatalogProduct
from catalogflow.modules.orders.extractor import RawOrderData, RawOrderItem
from catalogflow.modules.orders.normalizer import (
    NormalizedOrderData,
    NormalizedOrderItem,
    OrderNormalizer,
)

# ──────────────────────────────────────────────
#  Helpers — construção concisa de inputs
# ──────────────────────────────────────────────


def make_raw_item(
    sku: str,
    color_index: int,
    size: str,
    quantity: int,
    source_format: str = "v2",
) -> RawOrderItem:
    return RawOrderItem(
        field_name=f"qty__{sku}__cor{color_index}__{size}",
        sku=sku,
        color_index=color_index,
        size=size,
        quantity=quantity,
        source_format=source_format,  # type: ignore[arg-type]
    )


def make_raw(items: list[RawOrderItem], source_format: str = "v2") -> RawOrderData:
    return RawOrderData(
        items=items,
        n_pages_scanned=1,
        n_fields_found=len(items),
        n_fields_filled=len(items),
        n_fields_discarded=0,
        has_acroform=True,
        source_format=source_format,  # type: ignore[arg-type]
    )


def make_catalog_product(
    sku: str,
    name: str,
    price: Decimal,
    sizes: list[str],
    n_colors: int = 1,
    swatches: list[dict[str, Any]] | None = None,
    page_index: int = 0,
) -> CatalogProduct:
    """Constrói CatalogProduct sem persistir — só atribuição de atributos."""
    product = CatalogProduct(
        sku=sku,
        name=name,
        price=price,
        grade="PP-G",
        sizes=sizes,
        n_colors=n_colors,
        swatches=swatches or [],
        page_index=page_index,
    )
    return product


@pytest.fixture
def normalizer() -> OrderNormalizer:
    return OrderNormalizer()


# ──────────────────────────────────────────────
#  Sem catálogo
# ──────────────────────────────────────────────


class TestWithoutCatalog:
    def test_items_have_none_for_enrichment_fields(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        raw = make_raw(
            [
                make_raw_item("0442500941-0", 1, "PP", 2),
                make_raw_item("0442500941-0", 1, "P", 3),
            ]
        )
        result = normalizer.normalize(raw, catalog_products=None)

        assert isinstance(result, NormalizedOrderData)
        assert len(result.items) == 2
        for item in result.items:
            assert isinstance(item, NormalizedOrderItem)
            assert item.product_name is None
            assert item.unit_price is None
            assert item.color_hex is None
            assert item.subtotal is None

    def test_no_warnings_when_catalog_omitted(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        raw = make_raw([make_raw_item("0442500941-0", 1, "PP", 2)])
        result = normalizer.normalize(raw, catalog_products=None)
        assert result.warnings == []

    def test_totals_computed_without_catalog(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        raw = make_raw(
            [
                make_raw_item("0442500941-0", 1, "PP", 2),
                make_raw_item("0442500941-0", 1, "P", 3),
                make_raw_item("0322500004-0", 1, "M", 1),
            ]
        )
        result = normalizer.normalize(raw, catalog_products=None)
        assert result.totals.total_items == 3
        assert result.totals.total_pecas == 6
        assert result.totals.valor_total == Decimal("0")
        assert result.totals.n_skus == 2


# ──────────────────────────────────────────────
#  Com catálogo — enriquecimento
# ──────────────────────────────────────────────


class TestWithCatalog:
    def test_enriches_product_name_and_unit_price(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        catalog = [
            make_catalog_product(
                sku="0442500941-0",
                name="Vestido Joana",
                price=Decimal("1598.00"),
                sizes=["PP", "P", "M", "G"],
            ),
        ]
        raw = make_raw([make_raw_item("0442500941-0", 1, "PP", 2)])
        result = normalizer.normalize(raw, catalog_products=catalog)

        item = result.items[0]
        assert item.product_name == "Vestido Joana"
        assert item.unit_price == Decimal("1598.00")
        assert item.subtotal == Decimal("3196.00")  # 1598 * 2

    def test_color_hex_from_swatch(self, normalizer: OrderNormalizer) -> None:
        swatches: list[dict[str, Any]] = [
            {"fill_hex": "#24151b", "x0": 50.0, "y0": 820.0},
            {"fill_hex": "#a3b2c5", "x0": 80.0, "y0": 820.0},
        ]
        catalog = [
            make_catalog_product(
                sku="0442500912-0",
                name="Vestido Safira",
                price=Decimal("1388.00"),
                sizes=["PP", "P"],
                n_colors=2,
                swatches=swatches,
            ),
        ]
        raw = make_raw(
            [
                make_raw_item("0442500912-0", 1, "PP", 1),
                make_raw_item("0442500912-0", 2, "PP", 2),
            ]
        )
        result = normalizer.normalize(raw, catalog_products=catalog)

        by_color = {item.color_index: item.color_hex for item in result.items}
        assert by_color == {1: "#24151b", 2: "#a3b2c5"}

    def test_color_hex_none_when_color_index_exceeds_swatches(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        catalog = [
            make_catalog_product(
                sku="X",
                name="Produto X",
                price=Decimal("10.00"),
                sizes=["PP"],
                n_colors=1,
                swatches=[{"fill_hex": "#aaaaaa"}],
            ),
        ]
        # Pedido com cor3 mas catálogo só tem 1 swatch.
        raw = make_raw([make_raw_item("X", 3, "PP", 1)])
        result = normalizer.normalize(raw, catalog_products=catalog)
        assert result.items[0].color_hex is None
        # Mas o item segue preservado com enriquecimento parcial.
        assert result.items[0].product_name == "Produto X"

    def test_valor_total_aggregates_all_lines(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        catalog = [
            make_catalog_product(
                sku="A",
                name="Prod A",
                price=Decimal("100.00"),
                sizes=["PP"],
            ),
            make_catalog_product(
                sku="B",
                name="Prod B",
                price=Decimal("50.00"),
                sizes=["PP"],
                page_index=1,
            ),
        ]
        raw = make_raw(
            [
                make_raw_item("A", 1, "PP", 3),  # 3 * 100 = 300
                make_raw_item("B", 1, "PP", 4),  # 4 * 50  = 200
            ]
        )
        result = normalizer.normalize(raw, catalog_products=catalog)
        assert result.totals.valor_total == Decimal("500.00")
        assert result.totals.total_pecas == 7
        assert result.totals.n_skus == 2


# ──────────────────────────────────────────────
#  Warnings
# ──────────────────────────────────────────────


class TestWarnings:
    def test_warns_when_sku_missing_from_catalog(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        catalog = [
            make_catalog_product(
                sku="KNOWN",
                name="Conhecido",
                price=Decimal("10.00"),
                sizes=["PP"],
            )
        ]
        raw = make_raw(
            [
                make_raw_item("KNOWN", 1, "PP", 1),
                make_raw_item("PHANTOM", 1, "M", 5),
            ]
        )
        result = normalizer.normalize(raw, catalog_products=catalog)

        # Item órfão preservado, com warning emitido.
        assert len(result.items) == 2
        phantom = next(i for i in result.items if i.sku == "PHANTOM")
        assert phantom.product_name is None
        assert phantom.unit_price is None
        assert len(result.warnings) == 1
        assert "PHANTOM" in result.warnings[0]

    def test_no_warning_when_catalog_empty_list_provided(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        # Lista vazia ≠ None — vazia significa "catálogo carregado e sem produtos".
        # Comportamento: TODOS os SKUs do PDF viram warning (catalog_products foi
        # passado mas está vazio).
        raw = make_raw([make_raw_item("X", 1, "PP", 1)])
        result = normalizer.normalize(raw, catalog_products=[])
        assert len(result.warnings) == 1
        assert "X" in result.warnings[0]


# ──────────────────────────────────────────────
#  Agregação e agrupamento
# ──────────────────────────────────────────────


class TestAggregation:
    def test_duplicates_sum_quantities(self, normalizer: OrderNormalizer) -> None:
        """v1+v2 mistos podem produzir (SKU, cor=1, tam=PP) em duas entradas raw."""
        raw = make_raw(
            [
                make_raw_item("X", 1, "PP", 2, source_format="v2"),
                make_raw_item("X", 1, "PP", 3, source_format="v1"),
            ],
            source_format="mixed",
        )
        result = normalizer.normalize(raw, catalog_products=None)
        assert len(result.items) == 1
        assert result.items[0].quantity == 5

    def test_same_sku_different_colors_kept_separate(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        raw = make_raw(
            [
                make_raw_item("X", 1, "PP", 2),
                make_raw_item("X", 2, "PP", 3),
            ]
        )
        result = normalizer.normalize(raw, catalog_products=None)
        assert len(result.items) == 2
        quantities_by_color = {i.color_index: i.quantity for i in result.items}
        assert quantities_by_color == {1: 2, 2: 3}


# ──────────────────────────────────────────────
#  Ordenação
# ──────────────────────────────────────────────


class TestSorting:
    def test_sorts_by_page_index_when_catalog_available(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        catalog = [
            make_catalog_product(
                sku="LATE",
                name="L",
                price=Decimal("10"),
                sizes=["PP"],
                page_index=5,
            ),
            make_catalog_product(
                sku="EARLY",
                name="E",
                price=Decimal("10"),
                sizes=["PP"],
                page_index=1,
            ),
        ]
        raw = make_raw(
            [
                make_raw_item("LATE", 1, "PP", 1),
                make_raw_item("EARLY", 1, "PP", 1),
            ]
        )
        result = normalizer.normalize(raw, catalog_products=catalog)
        skus_in_order = [item.sku for item in result.items]
        assert skus_in_order == ["EARLY", "LATE"]

    def test_canonical_size_order_pp_to_gg(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        catalog = [
            make_catalog_product(
                sku="X",
                name="X",
                price=Decimal("10"),
                sizes=["PP", "P", "M", "G", "GG"],
            )
        ]
        raw = make_raw(
            [
                make_raw_item("X", 1, "GG", 1),
                make_raw_item("X", 1, "P", 1),
                make_raw_item("X", 1, "PP", 1),
                make_raw_item("X", 1, "G", 1),
                make_raw_item("X", 1, "M", 1),
            ]
        )
        result = normalizer.normalize(raw, catalog_products=catalog)
        assert [i.size for i in result.items] == ["PP", "P", "M", "G", "GG"]

    def test_alphabetical_sku_order_without_catalog(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        raw = make_raw(
            [
                make_raw_item("Z", 1, "PP", 1),
                make_raw_item("A", 1, "PP", 1),
                make_raw_item("M", 1, "PP", 1),
            ]
        )
        result = normalizer.normalize(raw, catalog_products=None)
        assert [i.sku for i in result.items] == ["A", "M", "Z"]


# ──────────────────────────────────────────────
#  Source format propagado
# ──────────────────────────────────────────────


class TestSourceFormatPropagation:
    @pytest.mark.parametrize("fmt", ["v1", "v2", "mixed"])
    def test_passes_through_to_normalized(
        self,
        normalizer: OrderNormalizer,
        fmt: str,
    ) -> None:
        raw = make_raw(
            [make_raw_item("X", 1, "PP", 1)],
            source_format=fmt,
        )
        result = normalizer.normalize(raw, catalog_products=None)
        assert result.source_format == fmt


# ──────────────────────────────────────────────
#  Vazio
# ──────────────────────────────────────────────


class TestEmpty:
    def test_empty_raw_yields_empty_normalized(
        self,
        normalizer: OrderNormalizer,
    ) -> None:
        raw = make_raw([])
        result = normalizer.normalize(raw, catalog_products=None)
        assert result.items == []
        assert result.totals.total_items == 0
        assert result.totals.total_pecas == 0
        assert result.totals.valor_total == Decimal("0")
        assert result.totals.n_skus == 0
        assert result.warnings == []
