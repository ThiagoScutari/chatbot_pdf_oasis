"""Regressão golden file do `PDFAnalyzer` sobre o catálogo Oasis real.

Esta suite garante que o refator da Sprint 08 Fase B preserva
bit-a-bit o `CatalogMetadata` produzido sobre o catálogo real em
produção. Qualquer diff aqui é portão de merge fechado até decisão
explícita do PMO.

Política rígida: atualizar o golden requer PR isolado com aprovação
explícita do PMO. Não tente "ajustar" o golden em resposta a falha
deste teste — investigue a regressão no código.

O PDF de fixture é gitignored (CLAUDE.md: nunca commitar PDF real do
cliente). Quando ausente localmente, o teste é skipped — o gate de
mérito real é no CI, que dispõe do PDF via secret.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from catalogflow.modules.catalog.pdf_analyzer import (
    CatalogMetadata,
    PDFAnalyzer,
    ProductPageMeta,
    SwatchInfo,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
PDF_PATH = REPO_ROOT / "docs" / "catalogo_oasis.pdf"
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "catalog_metadata_oasis_golden.json"


def _serialize_swatch(s: SwatchInfo) -> dict[str, Any]:
    return {
        "x0": s.x0,
        "y0": s.y0,
        "fill_rgb": list(s.fill_rgb),
        "fill_hex": s.fill_hex,
    }


def _serialize_product(p: ProductPageMeta) -> dict[str, Any]:
    return {
        "sku": p.sku,
        "name": p.name,
        "price": str(p.price) if isinstance(p.price, Decimal) else None,
        "grade": p.grade,
        "sizes": list(p.sizes),
        "n_colors": p.n_colors,
        "swatches": [_serialize_swatch(s) for s in p.swatches],
        "page_index": p.page_index,
        "x_block_start": p.x_block_start,
        "x_block_end": p.x_block_end,
        "y_start": p.y_start,
        "y_end": p.y_end,
        "side": p.side,
        "n_products_on_page": p.n_products_on_page,
    }


def _serialize_metadata(m: CatalogMetadata) -> dict[str, Any]:
    return {
        "n_pages": m.n_pages,
        "n_product_pages": m.n_product_pages,
        "product_pages": [_serialize_product(p) for p in m.product_pages],
    }


@pytest.mark.skipif(
    not PDF_PATH.is_file(),
    reason=f"Oasis fixture indisponível em {PDF_PATH}; rodar localmente requer o PDF do cliente.",
)
def test_oasis_default_profile_matches_golden() -> None:
    pdf_bytes = PDF_PATH.read_bytes()

    metadata = PDFAnalyzer().analyze(pdf_bytes, profile_id="oasis_default")

    actual = _serialize_metadata(metadata)
    expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    assert actual == expected
