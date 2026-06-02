# mypy: disable-error-code="no-untyped-call,attr-defined"
# ↑ pymupdf/fitz sem stubs; testes inspecionam PDFs via Document/Rect/Point.
"""Testes do `PDFAnalyzer` contra as fixtures sintéticas.

As fixtures são geradas por `tests/fixtures/generate_fixtures.py` e
commitadas. Se a estrutura visual mudar, regenere com:
    python tests/fixtures/generate_fixtures.py
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import fitz
import pymupdf
import pytest

from catalogflow.modules.catalog.field_injector import FieldInjector
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
        # Sprint 08 (ADR-010): _rgb_to_hex migrou para a estratégia
        # `GeometricBottomSwatches`. O contrato bit-a-bit é preservado.
        from catalogflow.modules.catalog.strategies.swatches.geometric_bottom import (
            GeometricBottomSwatches,
        )

        assert GeometricBottomSwatches._rgb_to_hex((0.0, 0.0, 0.0)) == "#000000"
        assert GeometricBottomSwatches._rgb_to_hex((1.0, 1.0, 1.0)) == "#ffffff"
        # 0.5 → 128 (int(round(0.5 * 255)))
        hx = GeometricBottomSwatches._rgb_to_hex((0.5, 0.5, 0.5))
        assert hx in {"#7f7f7f", "#808080"}


class TestPriceExtraction:
    """Preço do produto vem na 3ª linha do bloco no formato `R$ 3.488,00`."""

    def test_extracts_decimal_with_thousand_separator(
        self,
        analyzer: PDFAnalyzer,
    ) -> None:
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 780), "0322500004-0", fontsize=9)
        page.insert_text((50, 790), "JAQUETA BERENICE", fontsize=9)
        page.insert_text((50, 800), "R$ 3.488,00", fontsize=9)
        page.insert_text((50, 810), "PP-G", fontsize=9)
        page.draw_rect(
            pymupdf.Rect(50, 820, 70, 840),
            color=(0.0, 0.0, 0.0),
            fill=(0.3, 0.4, 0.5),
        )
        data = doc.tobytes()
        doc.close()

        meta = analyzer.analyze(data)
        (product,) = meta.product_pages
        assert product.price == Decimal("3488.00")

    def test_extracts_price_without_thousand_separator(
        self,
        analyzer: PDFAnalyzer,
    ) -> None:
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 780), "0322500004-0", fontsize=9)
        page.insert_text((50, 790), "BLUSA SIMPLES", fontsize=9)
        page.insert_text((50, 800), "R$ 122,50", fontsize=9)
        page.insert_text((50, 810), "PP-M", fontsize=9)
        page.draw_rect(
            pymupdf.Rect(50, 820, 70, 840),
            color=(0.0, 0.0, 0.0),
            fill=(0.1, 0.2, 0.3),
        )
        data = doc.tobytes()
        doc.close()

        meta = analyzer.analyze(data)
        (product,) = meta.product_pages
        assert product.price == Decimal("122.50")

    def test_returns_none_when_price_absent(
        self,
        analyzer: PDFAnalyzer,
    ) -> None:
        """Fixtures sintéticas existentes não têm preço → price=None."""
        meta = analyzer.analyze(_load("catalogo_1_produto_1_cor.pdf"))
        (product,) = meta.product_pages
        assert product.price is None


# ──────────────────────────────────────────────
#  S05-01 — SKU com 9 dígitos
# ──────────────────────────────────────────────


@pytest.fixture
def pdf_sku_9_digits() -> bytes:
    return _load("catalogo_sku_9_digitos.pdf")


@pytest.fixture
def pdf_1_produto_1_cor() -> bytes:
    return _load("catalogo_1_produto_1_cor.pdf")


def test_sku_9_digits_is_detected(pdf_sku_9_digits: bytes) -> None:
    """SKU com 9 dígitos deve ser detectado como página de produto."""
    result = PDFAnalyzer().analyze(pdf_bytes=pdf_sku_9_digits)
    assert result.n_product_pages == 1
    assert result.product_pages[0].sku == "442500908-0"


def test_sku_9_digits_fields_are_injected(pdf_sku_9_digits: bytes) -> None:
    """PDF com SKU de 9 dígitos deve receber campos AcroForm após injeção."""
    metadata = PDFAnalyzer().analyze(pdf_bytes=pdf_sku_9_digits)
    output = FieldInjector().inject(pdf_sku_9_digits, metadata)
    doc = pymupdf.open(stream=output, filetype="pdf")
    try:
        widgets = [w for page in doc for w in (page.widgets() or [])]
    finally:
        doc.close()
    assert len(widgets) > 0


def test_sku_10_digits_unaffected(pdf_1_produto_1_cor: bytes) -> None:
    """Regressão: SKU de 10 dígitos não deve ser afetado pela correção."""
    result = PDFAnalyzer().analyze(pdf_bytes=pdf_1_produto_1_cor)
    assert result.n_product_pages >= 1
    assert re.match(r"^\d{10}-\d$", result.product_pages[0].sku)


# ──────────────────────────────────────────────
#  S05-02 — Zonas de Voronoi horizontal (ADR-007)
# ──────────────────────────────────────────────


def test_assign_name_zones_single_sku() -> None:
    zones = PDFAnalyzer()._assign_name_zones(
        [("SKU-A", fitz.Rect(100, 200, 200, 220))],
        page_width=720,
        page_height=1080,
    )
    assert zones["SKU-A"].x0 == 0.0
    assert zones["SKU-A"].x1 == 720.0


def test_assign_name_zones_two_skus_midpoint() -> None:
    zones = PDFAnalyzer()._assign_name_zones(
        [
            ("SKU-A", fitz.Rect(180, 900, 280, 920)),
            ("SKU-B", fitz.Rect(540, 900, 640, 920)),
        ],
        page_width=720,
        page_height=1080,
    )
    mid = (180 + 540) / 2
    assert zones["SKU-A"].x0 == 0.0
    assert zones["SKU-A"].x1 == pytest.approx(mid)
    assert zones["SKU-B"].x0 == pytest.approx(mid)
    assert zones["SKU-B"].x1 == 720.0


def test_assign_name_zones_three_skus() -> None:
    zones = PDFAnalyzer()._assign_name_zones(
        [
            ("SKU-A", fitz.Rect(120, 900, 200, 920)),
            ("SKU-B", fitz.Rect(360, 900, 440, 920)),
            ("SKU-C", fitz.Rect(600, 900, 680, 920)),
        ],
        page_width=720,
        page_height=1080,
    )
    mid_ab = (120 + 360) / 2
    mid_bc = (360 + 600) / 2
    assert zones["SKU-A"].x1 == pytest.approx(mid_ab)
    assert zones["SKU-B"].x0 == pytest.approx(mid_ab)
    assert zones["SKU-B"].x1 == pytest.approx(mid_bc)
    assert zones["SKU-C"].x0 == pytest.approx(mid_bc)
    assert zones["SKU-C"].x1 == 720.0


def test_assign_name_zones_asymmetric_layout() -> None:
    """Fronteira segue os dados, não o centro fixo da página."""
    zones = PDFAnalyzer()._assign_name_zones(
        [
            ("SKU-A", fitz.Rect(100, 900, 200, 920)),
            ("SKU-B", fitz.Rect(480, 900, 580, 920)),
        ],
        page_width=720,
        page_height=1080,
    )
    mid = (100 + 480) / 2  # 290.0, não 360.0 (page_width/2)
    assert zones["SKU-A"].x1 == pytest.approx(mid)
    assert zones["SKU-B"].x0 == pytest.approx(mid)


def test_assign_name_zones_contiguous_and_non_overlapping() -> None:
    zones = PDFAnalyzer()._assign_name_zones(
        [
            ("SKU-A", fitz.Rect(50, 900, 150, 920)),
            ("SKU-B", fitz.Rect(300, 900, 400, 920)),
            ("SKU-C", fitz.Rect(550, 900, 650, 920)),
        ],
        page_width=720,
        page_height=1080,
    )
    sorted_z = sorted(zones.values(), key=lambda r: r.x0)
    assert sorted_z[0].x0 == pytest.approx(0.0)
    assert sorted_z[-1].x1 == pytest.approx(720.0)
    for i in range(len(sorted_z) - 1):
        assert sorted_z[i].x1 == pytest.approx(sorted_z[i + 1].x0)


@pytest.fixture
def pdf_dois_produtos_nomes_distintos() -> bytes:
    return _load("catalogo_dois_produtos_nomes_distintos.pdf")


def test_two_products_names_not_swapped(
    pdf_dois_produtos_nomes_distintos: bytes,
) -> None:
    """Cada produto deve ter seu próprio nome — sem vazamento entre vizinhos."""
    result = PDFAnalyzer().analyze(pdf_bytes=pdf_dois_produtos_nomes_distintos)
    skus = {p.sku: p.name for p in result.product_pages}
    assert skus.get("0322500004-0") == "JAQUETA BERENICE"
    assert skus.get("0142500001-0") == "CALÇA CAPRI ESTHER"


def test_single_product_name_unaffected(pdf_1_produto_1_cor: bytes) -> None:
    """Regressão: página com 1 produto não deve ter seu nome alterado."""
    result = PDFAnalyzer().analyze(pdf_bytes=pdf_1_produto_1_cor)
    assert result.product_pages[0].name is not None
    assert len(result.product_pages[0].name) > 0


def test_two_products_each_receives_acroform_fields(
    pdf_dois_produtos_nomes_distintos: bytes,
) -> None:
    """Ambos os produtos da página dupla devem receber campos AcroForm."""
    metadata = PDFAnalyzer().analyze(pdf_bytes=pdf_dois_produtos_nomes_distintos)
    output = FieldInjector().inject(pdf_dois_produtos_nomes_distintos, metadata)
    doc = pymupdf.open(stream=output, filetype="pdf")
    try:
        field_names = [w.field_name for page in doc for w in (page.widgets() or [])]
    finally:
        doc.close()
    assert any("0322500004-0" in f for f in field_names), (
        "Nenhum campo gerado para JAQUETA BERENICE"
    )
    assert any("0142500001-0" in f for f in field_names), (
        "Nenhum campo gerado para CALÇA CAPRI ESTHER"
    )


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
