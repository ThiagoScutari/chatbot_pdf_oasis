"""Integração contra os catálogos REAIS de example/.

Estes testes existem porque a fixture sintética divergiu do catálogo
real e mascarou dois bugs (gate `bot_words` que descartava páginas sem
texto no rodapé + `positional_title` que confundia preço com nome). A
verdade é o catálogo real; estes testes a tornam o gate de CI — para que
"funciona no real" seja verificado, não descoberto numa demo.

Os PDFs reais vivem em `example/` e são **gitignored** (privacidade de
cliente — ver `.gitignore`). Quando ausentes (ex.: clone limpo, CI sem o
artefato), os testes dão `skip`. A estratégia de disponibilizá-los no CI
(secret, artefato, etc.) é decisão do PMO; o precedente é o
`test_pdf_analyzer_regression`, que usa skip-local + PDF via secret.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from catalogflow.modules.catalog.domain import SEVERITY_ERROR
from catalogflow.modules.catalog.pdf_analyzer import PDFAnalyzer

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "example"
FERLA = EXAMPLE_DIR / "catalogo_ferla.pdf"
OASIS = EXAMPLE_DIR / "catalogo_oasis.pdf"


@pytest.mark.skipif(not FERLA.is_file(), reason="catálogo FERLA real ausente")
def test_ferla_real_catalog_extracts_products() -> None:
    data = FERLA.read_bytes()
    meta = PDFAnalyzer().analyze(data, profile_id="prefixed_dual_price")

    # O FERLA real tem 7 páginas de produto (capa + verso sem SKU não
    # contam). `>=` evita fragilidade a variações de borda, mas o mínimo
    # prova que o pipeline extrai o catálogo inteiro — antes do hotfix
    # era 0 (PDFNoProductsError).
    assert meta.n_skus >= 7, "FERLA real deveria extrair >= 7 produtos"

    by_sku = {p.sku: p for p in meta.product_pages}
    # SKU prefixado detectado (regex_prefixed):
    assert "01010012" in by_sku

    first = by_sku["01010012"]

    # Bug 2: o nome NÃO pode ser a linha de preço.
    assert first.name is not None
    assert "Atacado" not in first.name
    assert "Varejo" not in first.name
    assert "299" not in first.name
    # Nome esperado (texto real do catálogo):
    assert "Camisa Polo Pima" in first.name

    # Grade e preço corretos (alpha_range + labeled_dual):
    assert first.grade == "P-GG"
    assert first.sizes == ["P", "M", "G", "GG"]
    assert first.price == Decimal("299")

    # Nenhum produto pode ter nome contaminado por preço/rótulo.
    for product in meta.product_pages:
        assert product.name is not None
        assert "Atacado" not in product.name
        assert "Varejo" not in product.name

    # Não-bloqueio (ADR-011): o FERLA não tem swatches no rodapé, então
    # warnings SWATCHES_NOT_DETECTED (info) são esperados; nenhum warning
    # de severidade `error` deve ocorrer.
    assert all(w.severity != SEVERITY_ERROR for w in meta.warnings)


@pytest.mark.skipif(not OASIS.is_file(), reason="catálogo Oasis real ausente")
def test_oasis_real_catalog_still_works() -> None:
    data = OASIS.read_bytes()
    meta = PDFAnalyzer().analyze(data, profile_id="hyphenated_single_price")

    # Baseline conhecido do Oasis MOTION: 38 SKUs / 33 páginas de produto.
    # O hotfix do gate `bot_words` não pode regredir o Oasis (golden
    # diff-zero); aqui garantimos o pipeline ponta-a-ponta no PDF real.
    assert meta.n_skus >= 38
    assert meta.n_product_pages >= 33
    # Catálogo bem-formado → sem degradação de severidade `error`.
    assert all(w.severity != SEVERITY_ERROR for w in meta.warnings)
