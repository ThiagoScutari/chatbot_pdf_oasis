# mypy: disable-error-code="no-untyped-call,attr-defined,operator,union-attr,var-annotated,arg-type"
# ↑ pymupdf sem stubs; widget.field_name/.rect retornam `Any | None` na visão
# do mypy, que cascateia em operator/union-attr/arg-type/var-annotated nos
# asserts dos testes — todos derivados da mesma causa-raiz.
"""Testes do `FieldInjector` — pipeline analyzer → injector com fixtures."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest

from catalogflow.modules.catalog.field_injector import (
    FieldInjector,
    count_fields,
    field_name_for,
)
from catalogflow.modules.catalog.pdf_analyzer import PDFAnalyzer
from catalogflow.shared.errors import PDFCorruptError, PDFEncryptedError

FIXTURES_DIR = Path(__file__).resolve().parents[5] / "tests" / "fixtures"

_FIELD_NAME_RE = re.compile(
    r"^qty__\d{10,13}-\d__cor\d+__(PP|P|M|G|GG)$",
)


@pytest.fixture(scope="module")
def analyzer() -> PDFAnalyzer:
    return PDFAnalyzer()


@pytest.fixture(scope="module")
def injector() -> FieldInjector:
    return FieldInjector()


def _load(name: str) -> bytes:
    path = FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(
            f"fixture {name} ausente — rode `python tests/fixtures/generate_fixtures.py`",
        )
    return path.read_bytes()


def _widgets(pdf_bytes: bytes) -> list[pymupdf.Widget]:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [w for page in doc for w in page.widgets()]
    finally:
        doc.close()


# ──────────────────────────────────────────────
#  field_name_for / count_fields
# ──────────────────────────────────────────────


class TestFieldNameFor:
    def test_v2_format(self) -> None:
        assert field_name_for("0442500941-0", 1, "PP") == "qty__0442500941-0__cor1__PP"
        assert field_name_for("0322500004-0", 3, "GG") == "qty__0322500004-0__cor3__GG"


class TestCountFields:
    def test_counts_per_product(self, analyzer: PDFAnalyzer) -> None:
        # 1 produto/1 cor/PP-G -> 1x4 = 4
        meta = analyzer.analyze(_load("catalogo_1_produto_1_cor.pdf"))
        assert count_fields(meta) == 4
        # 1 produto/2 cores/PP-G -> 2x4 = 8
        meta = analyzer.analyze(_load("catalogo_1_produto_2_cores.pdf"))
        assert count_fields(meta) == 8
        # 2 produtos (PP-M + PP-G) -> 1x3 + 1x4 = 7
        meta = analyzer.analyze(_load("catalogo_2_produtos_pagina.pdf"))
        assert count_fields(meta) == 7


# ──────────────────────────────────────────────
#  Pipeline analyzer → injector
# ──────────────────────────────────────────────


class TestInjectOneProductOneColor:
    def test_creates_one_row_of_widgets(
        self,
        analyzer: PDFAnalyzer,
        injector: FieldInjector,
    ) -> None:
        data = _load("catalogo_1_produto_1_cor.pdf")
        meta = analyzer.analyze(data)
        out = injector.inject(data, meta)
        widgets = _widgets(out)
        assert len(widgets) == 4
        names = {w.field_name for w in widgets}
        assert names == {
            "qty__0442500941-0__cor1__PP",
            "qty__0442500941-0__cor1__P",
            "qty__0442500941-0__cor1__M",
            "qty__0442500941-0__cor1__G",
        }


class TestInjectOneProductTwoColors:
    def test_creates_two_rows_of_widgets(
        self,
        analyzer: PDFAnalyzer,
        injector: FieldInjector,
    ) -> None:
        data = _load("catalogo_1_produto_2_cores.pdf")
        meta = analyzer.analyze(data)
        out = injector.inject(data, meta)
        widgets = _widgets(out)
        assert len(widgets) == 8
        # Para cada tamanho, há 2 cores.
        cor1 = [w for w in widgets if "__cor1__" in w.field_name]
        cor2 = [w for w in widgets if "__cor2__" in w.field_name]
        assert len(cor1) == 4
        assert len(cor2) == 4

    def test_widgets_dont_overlap_within_panel(
        self,
        analyzer: PDFAnalyzer,
        injector: FieldInjector,
    ) -> None:
        data = _load("catalogo_1_produto_2_cores.pdf")
        meta = analyzer.analyze(data)
        out = injector.inject(data, meta)
        widgets = _widgets(out)
        # Widgets numa mesma linha (cor) têm y interno aproximadamente igual; widgets de cores
        # diferentes têm faixas Y disjuntas. Validamos não-sobreposição entre faixas.
        rows = {}
        for w in widgets:
            cor = w.field_name.split("__cor")[1].split("__")[0]
            rows.setdefault(cor, []).append(w.rect)

        # Compara extremos verticais entre cor1 e cor2.
        y_max_cor1 = max(r.y1 for r in rows["1"])
        y_min_cor2 = min(r.y0 for r in rows["2"])
        assert y_min_cor2 >= y_max_cor1 - 0.5, "linhas de cor sobrepõem verticalmente"


class TestInjectTwoProductsPerPage:
    def test_creates_widgets_for_both_skus(
        self,
        analyzer: PDFAnalyzer,
        injector: FieldInjector,
    ) -> None:
        data = _load("catalogo_2_produtos_pagina.pdf")
        meta = analyzer.analyze(data)
        out = injector.inject(data, meta)
        widgets = _widgets(out)
        names = {w.field_name for w in widgets}
        # Esquerdo PP-M: 3 widgets
        for size in ("PP", "P", "M"):
            assert f"qty__0442500941-0__cor1__{size}" in names
        # Direito PP-G: 4 widgets
        for size in ("PP", "P", "M", "G"):
            assert f"qty__0322500004-0__cor1__{size}" in names
        assert len(widgets) == 7

    def test_left_panel_fields_are_compressed(
        self,
        analyzer: PDFAnalyzer,
        injector: FieldInjector,
    ) -> None:
        """Compressão à esquerda é acionada quando há produto direito vizinho.

        Comportamento espelha o POC: o painel reduz `campo_w` até `MIN_CAMPO_W`
        (50). Visualmente, widgets podem extrapolar o limite do painel se ainda
        assim não couberem — esse é o trade-off documentado do POC (oasis_form_v2.py)
        e preservá-lo é parte do contrato desta sprint.
        """
        from catalogflow.modules.catalog.field_injector import CAMPO_W, MIN_CAMPO_W

        data = _load("catalogo_2_produtos_pagina.pdf")
        meta = analyzer.analyze(data)
        out = injector.inject(data, meta)
        widgets = _widgets(out)

        # Largura efetiva ≈ diferença entre x0 de cells adjacentes na mesma linha.
        left_widgets = sorted(
            (w for w in widgets if w.field_name.startswith("qty__0442500941-0")),
            key=lambda w: w.rect.x0,
        )
        right_widgets = sorted(
            (w for w in widgets if w.field_name.startswith("qty__0322500004-0")),
            key=lambda w: w.rect.x0,
        )
        # Distância entre tamanhos adjacentes (PP→P) revela `campo_w` efetivo.
        delta_left = left_widgets[1].rect.x0 - left_widgets[0].rect.x0
        delta_right = right_widgets[1].rect.x0 - right_widgets[0].rect.x0

        # O painel direito não tem vizinho competindo — `campo_w == CAMPO_W` (82).
        assert delta_right == pytest.approx(CAMPO_W, abs=1.0)
        # O painel esquerdo foi comprimido até o piso `MIN_CAMPO_W` (50).
        assert delta_left == pytest.approx(MIN_CAMPO_W, abs=1.0)
        assert delta_left < delta_right


# ──────────────────────────────────────────────
#  Nomenclatura + AcroForm
# ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "fixture",
    [
        "catalogo_1_produto_1_cor.pdf",
        "catalogo_1_produto_2_cores.pdf",
        "catalogo_2_produtos_pagina.pdf",
        "catalogo_pp_g.pdf",
    ],
)
class TestFieldNamingConvention:
    def test_every_widget_matches_v2_pattern(
        self,
        fixture: str,
        analyzer: PDFAnalyzer,
        injector: FieldInjector,
    ) -> None:
        data = _load(fixture)
        meta = analyzer.analyze(data)
        out = injector.inject(data, meta)
        widgets = _widgets(out)
        assert widgets, f"sem widgets em {fixture}"
        for w in widgets:
            assert _FIELD_NAME_RE.fullmatch(w.field_name), (
                f"widget fora do padrão v2 em {fixture}: {w.field_name}"
            )

    def test_widget_field_type_is_text(
        self,
        fixture: str,
        analyzer: PDFAnalyzer,
        injector: FieldInjector,
    ) -> None:
        data = _load(fixture)
        meta = analyzer.analyze(data)
        out = injector.inject(data, meta)
        widgets = _widgets(out)
        for w in widgets:
            assert w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT

    def test_acroform_present_in_output(
        self,
        fixture: str,
        analyzer: PDFAnalyzer,
        injector: FieldInjector,
    ) -> None:
        data = _load(fixture)
        meta = analyzer.analyze(data)
        out = injector.inject(data, meta)
        doc = pymupdf.open(stream=out, filetype="pdf")
        try:
            # `is_form_pdf` é True quando o catálogo /AcroForm tem campos.
            assert doc.is_form_pdf
        finally:
            doc.close()


# ──────────────────────────────────────────────
#  Edge cases
# ──────────────────────────────────────────────


class TestPurity:
    def test_does_not_mutate_input(
        self,
        analyzer: PDFAnalyzer,
        injector: FieldInjector,
    ) -> None:
        data = _load("catalogo_1_produto_1_cor.pdf")
        snapshot = bytes(data)
        meta = analyzer.analyze(data)
        injector.inject(data, meta)
        assert data == snapshot


class TestInvalidInput:
    def test_empty_bytes_raise_corrupt(
        self, analyzer: PDFAnalyzer, injector: FieldInjector
    ) -> None:
        # Para forçar metadata válido, simulamos um metadata vazio mas é o pdf que é o problema.
        meta = analyzer.analyze(_load("catalogo_1_produto_1_cor.pdf"))
        with pytest.raises(PDFCorruptError):
            injector.inject(b"", meta)

    def test_random_bytes_raise_corrupt(
        self,
        analyzer: PDFAnalyzer,
        injector: FieldInjector,
    ) -> None:
        meta = analyzer.analyze(_load("catalogo_1_produto_1_cor.pdf"))
        with pytest.raises(PDFCorruptError):
            injector.inject(b"garbage not a pdf", meta)

    def test_encrypted_input_raises(
        self,
        analyzer: PDFAnalyzer,
        injector: FieldInjector,
    ) -> None:
        # metadata "qualquer" — o erro acontece antes de processar produtos.
        meta = analyzer.analyze(_load("catalogo_1_produto_1_cor.pdf"))
        with pytest.raises(PDFEncryptedError):
            injector.inject(_load("pdf_criptografado.pdf"), meta)
