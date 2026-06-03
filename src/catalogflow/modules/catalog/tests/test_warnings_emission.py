# mypy: disable-error-code="no-untyped-call"
"""Emissão de `AnalyzerWarning` pelo `PDFAnalyzer` (ADR-011 D2/D3).

Estratégia de teste (decisão da Fase C): em vez de registrar estratégias
fake, construímos PDFs sintéticos que disparam as degradações reais sob
o profile `oasis_default`. Assim exercitamos o caminho `None` das
estratégias reais (grade/name/price/swatches) **junto** com a emissão de
warning do orquestrador — mais fiel que mockar os registries.

Geometria: pagina 595x842; o threshold da zona inferior e
`842 * 0.92 ~= 774.6`. Todo texto e swatch ficam abaixo disso para que o
SKU seja detectado como pagina de produto.
"""

from __future__ import annotations

import pymupdf
import pytest

from catalogflow.modules.catalog import domain
from catalogflow.modules.catalog.pdf_analyzer import PDFAnalyzer
from catalogflow.shared.errors import PDFNoProductsError

_SKU = "0442500941-0"


def _make_pdf(
    *,
    include_name: bool = True,
    include_price: bool = True,
    include_grade: bool = True,
    include_swatch: bool = True,
) -> bytes:
    """Produto único na zona inferior, com cada eixo ligável/desligável."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 800), _SKU, fontsize=9)
    if include_name:
        page.insert_text((50, 810), "JAQUETA BERENICE", fontsize=9)
    if include_price:
        page.insert_text((50, 820), "R$ 100,00", fontsize=9)
    if include_grade:
        page.insert_text((50, 830), "PP-G", fontsize=9)
    if include_swatch:
        page.draw_rect(
            pymupdf.Rect(300, 815, 320, 835),
            color=(0.0, 0.0, 0.0),
            fill=(0.3, 0.4, 0.5),
        )
    data: bytes = doc.tobytes()
    doc.close()
    return data


@pytest.fixture(scope="module")
def analyzer() -> PDFAnalyzer:
    return PDFAnalyzer()


def _codes(meta_warnings: list[domain.AnalyzerWarning]) -> list[str]:
    return [w.code for w in meta_warnings]


def test_grade_not_detected_emits_error_warning(analyzer: PDFAnalyzer) -> None:
    meta = analyzer.analyze(_make_pdf(include_grade=False))
    grade_warnings = [w for w in meta.warnings if w.code == domain.GRADE_NOT_DETECTED]
    assert len(grade_warnings) == 1
    assert grade_warnings[0].severity == domain.SEVERITY_ERROR
    # grade ausente → produto persiste com grade/sizes None (sem default).
    (product,) = meta.product_pages
    assert product.grade is None
    assert product.sizes is None


def test_name_not_detected_emits_warning_severity(analyzer: PDFAnalyzer) -> None:
    meta = analyzer.analyze(_make_pdf(include_name=False))
    name_warnings = [w for w in meta.warnings if w.code == domain.NAME_NOT_DETECTED]
    assert len(name_warnings) == 1
    assert name_warnings[0].severity == domain.SEVERITY_WARNING


def test_price_not_detected_emits_warning_severity(analyzer: PDFAnalyzer) -> None:
    meta = analyzer.analyze(_make_pdf(include_price=False))
    price_warnings = [w for w in meta.warnings if w.code == domain.PRICE_NOT_DETECTED]
    assert len(price_warnings) == 1
    assert price_warnings[0].severity == domain.SEVERITY_WARNING


def test_no_swatches_emits_info_warning(analyzer: PDFAnalyzer) -> None:
    meta = analyzer.analyze(_make_pdf(include_swatch=False))
    swatch_warnings = [w for w in meta.warnings if w.code == domain.SWATCHES_NOT_DETECTED]
    assert len(swatch_warnings) == 1
    assert swatch_warnings[0].severity == domain.SEVERITY_INFO


def test_multiple_warnings_preserve_order(analyzer: PDFAnalyzer) -> None:
    meta = analyzer.analyze(
        _make_pdf(
            include_name=False,
            include_price=False,
            include_grade=False,
            include_swatch=False,
        ),
    )
    # Ordem de emissão dentro de um produto: grade → name → price → swatches.
    assert _codes(meta.warnings) == [
        domain.GRADE_NOT_DETECTED,
        domain.NAME_NOT_DETECTED,
        domain.PRICE_NOT_DETECTED,
        domain.SWATCHES_NOT_DETECTED,
    ]


def test_no_warnings_when_everything_detected(analyzer: PDFAnalyzer) -> None:
    meta = analyzer.analyze(_make_pdf())
    assert meta.warnings == []
    (product,) = meta.product_pages
    assert product.grade == "PP-G"
    assert product.sizes == ["PP", "P", "M", "G"]


def test_warnings_carry_correct_sku_and_page_index(analyzer: PDFAnalyzer) -> None:
    meta = analyzer.analyze(_make_pdf(include_grade=False))
    (warning,) = [w for w in meta.warnings if w.code == domain.GRADE_NOT_DETECTED]
    assert warning.sku == _SKU
    assert warning.page_index == 0


def test_pdf_no_products_still_raises_exception(analyzer: PDFAnalyzer) -> None:
    """Catálogo sem nenhum produto continua sendo falha global, não warning."""
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)  # página vazia, sem SKU
    data = doc.tobytes()
    doc.close()
    with pytest.raises(PDFNoProductsError):
        analyzer.analyze(data)
