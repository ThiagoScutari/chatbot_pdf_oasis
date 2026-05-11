"""Testes do `PDFAnalyzer` contra as fixtures sintéticas.

As fixtures são geradas por `tests/fixtures/generate_fixtures.py` e
commitadas. Se a estrutura visual mudar, regenere com:
    python tests/fixtures/generate_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from catalogflow.modules.catalog.pdf_analyzer import (
    CatalogMetadata,
    PDFAnalyzer,
    ProductPageMeta,
    SwatchInfo,
)
from catalogflow.shared.errors import (
    PDFCorruptError,
    PDFEncryptedError,
    PDFNoProductsError,
)

FIXTURES_DIR = Path(__file__).resolve().parents[5] / "tests" / "fixtures"


@pytest.fixture(scope="module")
def analyzer() -> PDFAnalyzer:
    return PDFAnalyzer()


def _load(name: str) -> bytes:
    path = FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(
            f"fixture {name} ausente — rode `python tests/fixtures/generate_fixtures.py`",
        )
    return path.read_bytes()


# ──────────────────────────────────────────────
#  Happy paths
# ──────────────────────────────────────────────


class TestOneProductOneColor:
    def test_returns_one_product_with_single_swatch(
        self,
        analyzer: PDFAnalyzer,
    ) -> None:
        meta = analyzer.analyze(_load("catalogo_1_produto_1_cor.pdf"))
        assert isinstance(meta, CatalogMetadata)
        assert meta.n_pages == 1
        assert meta.n_product_pages == 1
        assert meta.n_skus == 1

        (product,) = meta.product_pages
        assert isinstance(product, ProductPageMeta)
        assert product.sku == "0442500941-0"
        assert product.grade == "PP-G"
        assert product.sizes == ["PP", "P", "M", "G"]
        assert product.n_colors == 1
        assert product.side == "single"
        assert product.n_products_on_page == 1
        assert len(product.swatches) == 1
        assert isinstance(product.swatches[0], SwatchInfo)


class TestOneProductTwoColors:
    def test_detects_two_swatches(self, analyzer: PDFAnalyzer) -> None:
        meta = analyzer.analyze(_load("catalogo_1_produto_2_cores.pdf"))
        (product,) = meta.product_pages
        assert product.sku == "0442500912-0"
        assert product.n_colors == 2
        assert len(product.swatches) == 2
        # Hex válido em 6 chars
        for sw in product.swatches:
            assert sw.fill_hex.startswith("#")
            assert len(sw.fill_hex) == 7
        # Ordenados por x0 ascendente (contrato do _detect_swatches)
        xs = [sw.x0 for sw in product.swatches]
        assert xs == sorted(xs)


class TestTwoProductsSamePage:
    def test_splits_left_and_right(self, analyzer: PDFAnalyzer) -> None:
        meta = analyzer.analyze(_load("catalogo_2_produtos_pagina.pdf"))
        assert meta.n_pages == 1
        assert meta.n_product_pages == 1
        assert meta.n_skus == 2

        left, right = meta.product_pages
        assert left.side == "left"
        assert right.side == "right"
        assert left.sku == "0442500941-0"
        assert right.sku == "0322500004-0"
        assert left.n_products_on_page == 2
        assert right.n_products_on_page == 2

    def test_each_block_gets_only_its_swatches(
        self,
        analyzer: PDFAnalyzer,
    ) -> None:
        meta = analyzer.analyze(_load("catalogo_2_produtos_pagina.pdf"))
        left, right = meta.product_pages
        # Cada produto tem 1 swatch no lado correto.
        assert len(left.swatches) == 1
        assert len(right.swatches) == 1
        # Garantir que não cruzaram lados — swatch left tem x0 menor que right.
        assert left.swatches[0].x0 < right.swatches[0].x0


class TestGradePPG:
    def test_four_sizes(self, analyzer: PDFAnalyzer) -> None:
        meta = analyzer.analyze(_load("catalogo_pp_g.pdf"))
        (product,) = meta.product_pages
        assert product.grade == "PP-G"
        assert product.sizes == ["PP", "P", "M", "G"]


# ──────────────────────────────────────────────
#  Edge cases
# ──────────────────────────────────────────────


class TestNoProducts:
    def test_raises_pdf_no_products(self, analyzer: PDFAnalyzer) -> None:
        with pytest.raises(PDFNoProductsError) as exc_info:
            analyzer.analyze(_load("pdf_sem_produtos.pdf"))
        assert exc_info.value.code == "PDF_NO_PRODUCTS"


class TestEncrypted:
    def test_raises_pdf_encrypted(self, analyzer: PDFAnalyzer) -> None:
        with pytest.raises(PDFEncryptedError) as exc_info:
            analyzer.analyze(_load("pdf_criptografado.pdf"))
        assert exc_info.value.code == "PDF_ENCRYPTED"


class TestCorruptInput:
    def test_empty_bytes_raise(self, analyzer: PDFAnalyzer) -> None:
        with pytest.raises(PDFCorruptError) as exc_info:
            analyzer.analyze(b"")
        assert exc_info.value.code == "PDF_CORRUPT"

    def test_random_bytes_raise(self, analyzer: PDFAnalyzer) -> None:
        with pytest.raises(PDFCorruptError) as exc_info:
            analyzer.analyze(b"absolutely not a pdf file content")
        assert exc_info.value.code == "PDF_CORRUPT"


# ──────────────────────────────────────────────
#  Pureza do analyzer
# ──────────────────────────────────────────────


class TestPurity:
    def test_does_not_modify_input_bytes(self, analyzer: PDFAnalyzer) -> None:
        data = _load("catalogo_1_produto_1_cor.pdf")
        snapshot = bytes(data)
        analyzer.analyze(data)
        assert data == snapshot, "analyzer mutou os bytes de entrada"

    def test_two_runs_yield_equivalent_results(
        self,
        analyzer: PDFAnalyzer,
    ) -> None:
        data = _load("catalogo_1_produto_2_cores.pdf")
        a = analyzer.analyze(data)
        b = analyzer.analyze(data)
        assert a == b


class TestSwatchHexConversion:
    def test_hex_matches_rgb(self) -> None:
        an = PDFAnalyzer()
        assert an._rgb_to_hex((0.0, 0.0, 0.0)) == "#000000"
        assert an._rgb_to_hex((1.0, 1.0, 1.0)) == "#ffffff"
        # 0.5 → 128 (int(round(0.5 * 255)))
        hx = an._rgb_to_hex((0.5, 0.5, 0.5))
        assert hx in {"#7f7f7f", "#808080"}


class TestSwatchDetectionThreshold:
    def test_swatch_high_in_page_is_not_detected(
        self,
        analyzer: PDFAnalyzer,
    ) -> None:
        """Drawing fora da zona inferior (y0 < 0.92h) NÃO é swatch."""
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        # legenda válida na zona inferior
        page.insert_text(
            (50, 800),
            "JAQUETA REF: 0442500941-0 PP-G",
            fontsize=9,
        )
        # "swatch" no meio da página — deve ser ignorado.
        page.draw_rect(
            pymupdf.Rect(50, 400, 70, 420),
            color=(0, 0, 0),
            fill=(0.3, 0.4, 0.5),
        )
        data = doc.tobytes()
        doc.close()

        meta = analyzer.analyze(data)
        (product,) = meta.product_pages
        # Não pegou o quadrado do meio; fica com n_colors=1 (default mínimo).
        assert len(product.swatches) == 0
        assert product.n_colors == 1
