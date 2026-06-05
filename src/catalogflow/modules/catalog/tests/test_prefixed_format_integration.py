"""Integração: pipeline do `PDFAnalyzer` sobre a fixture do formato prefixado.

Roda `PDFAnalyzer().analyze(bytes, profile_id="prefixed_dual_price")`
sobre o catálogo sintético gerado por
`tests/fixtures/generate_prefixed_catalog_fixtures.py` e prova que as
estratégias do formato prefixado (regex_prefixed, labeled_dual,
positional_title, alpha_range+tolerate_spaces) extraem SKU, grade, preço
e nome end-to-end. O formato foi observado no catálogo FERLA que motivou
a ADR-010.

A fixture tem 3 produtos (1 na página 0, 2 na página 1). Critério da
ADR-010 é ≥ 5/7 no catálogo real; aqui, sendo a fixture menor, todos os
3 devem ser detectados.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from catalogflow.modules.catalog.pdf_analyzer import CatalogMetadata, PDFAnalyzer

FIXTURES_DIR = Path(__file__).resolve().parents[5] / "tests" / "fixtures"
PREFIXED_FIXTURE = "catalogo_prefixed_dual_price.pdf"


def _prefixed_bytes() -> bytes:
    path = FIXTURES_DIR / PREFIXED_FIXTURE
    if not path.exists():
        pytest.skip(
            f"fixture {PREFIXED_FIXTURE} ausente — rode "
            "`python tests/fixtures/generate_prefixed_catalog_fixtures.py`",
        )
    return path.read_bytes()


@pytest.fixture(scope="module")
def prefixed_meta() -> CatalogMetadata:
    return PDFAnalyzer().analyze(_prefixed_bytes(), profile_id="prefixed_dual_price")


def test_prefixed_fixture_processes_with_profile(prefixed_meta: CatalogMetadata) -> None:
    # Todos os 3 produtos da fixture detectados, cada um com SKU e grade.
    assert prefixed_meta.n_skus == 3
    skus = {p.sku for p in prefixed_meta.product_pages}
    assert skus == {"01010012", "01010013", "01010014"}
    for product in prefixed_meta.product_pages:
        assert product.sku
        assert product.grade == "P-GG"


def test_prefixed_products_have_names_via_positional_title(
    prefixed_meta: CatalogMetadata,
) -> None:
    names = {p.sku: p.name for p in prefixed_meta.product_pages}
    # Nome isolado por tipografia — NÃO é o SKU.
    assert names["01010012"] == "Camisa Polo Pima Clássica"
    assert names["01010013"] == "Camiseta Gola V Premium"
    assert names["01010014"] == "Bermuda Sarja Slim"
    for sku, name in names.items():
        assert name is not None
        assert sku not in name


def test_prefixed_products_have_dual_price_primary(prefixed_meta: CatalogMetadata) -> None:
    prices = {p.sku: p.price for p in prefixed_meta.product_pages}
    # Primário "Atacado" é o valor devolvido.
    assert prices["01010012"] == Decimal("299")
    assert prices["01010013"] == Decimal("199")
    assert prices["01010014"] == Decimal("159")


def test_prefixed_grade_expanded_correctly(prefixed_meta: CatalogMetadata) -> None:
    for product in prefixed_meta.product_pages:
        assert product.sizes == ["P", "M", "G", "GG"]


def test_prefixed_extraction_is_warning_free(prefixed_meta: CatalogMetadata) -> None:
    # Fixture bem-formada → nenhuma degradação local (ADR-011).
    assert prefixed_meta.warnings == []


def test_prefixed_voronoi_splits_two_products_on_page(
    prefixed_meta: CatalogMetadata,
) -> None:
    page1 = [p for p in prefixed_meta.product_pages if p.page_index == 1]
    sides = {p.side for p in page1}
    assert sides == {"left", "right"}
