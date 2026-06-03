"""Gera o golden file da análise Oasis a partir do `pdf_analyzer.py` atual.

Uso:
    python scripts/generate_oasis_golden.py

Deve ser executado **antes** de qualquer refator do `pdf_analyzer.py`
na Sprint 08 Fase B. O JSON resultante é o baseline da suite de
regressão (`tests/test_pdf_analyzer_regression.py`).

Atualizar o golden requer PR isolado com aprovação explícita do PMO.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from catalogflow.modules.catalog.domain import AnalyzerWarning
from catalogflow.modules.catalog.pdf_analyzer import (
    CatalogMetadata,
    PDFAnalyzer,
    ProductPageMeta,
    SwatchInfo,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
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
        "sizes": list(p.sizes) if p.sizes is not None else None,
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


def _serialize_warning(w: AnalyzerWarning) -> dict[str, Any]:
    return {
        "code": w.code,
        "severity": w.severity,
        "page_index": w.page_index,
        "sku": w.sku,
        "message": w.message,
        "detected_value": w.detected_value,
    }


def serialize_metadata(m: CatalogMetadata) -> dict[str, Any]:
    return {
        "n_pages": m.n_pages,
        "n_product_pages": m.n_product_pages,
        "product_pages": [_serialize_product(p) for p in m.product_pages],
        "warnings": [_serialize_warning(w) for w in m.warnings],
    }


def main() -> None:
    if not PDF_PATH.is_file():
        raise SystemExit(f"PDF Oasis não encontrado em {PDF_PATH}")

    pdf_bytes = PDF_PATH.read_bytes()
    metadata = PDFAnalyzer().analyze(pdf_bytes)
    payload = serialize_metadata(metadata)

    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Golden gerado: {GOLDEN_PATH}")
    print(f"  n_pages          = {metadata.n_pages}")
    print(f"  n_product_pages  = {metadata.n_product_pages}")
    print(f"  n_skus           = {metadata.n_skus}")
    print(f"  n_warnings       = {len(metadata.warnings)}")
    for w in metadata.warnings:
        print(f"    - {w.code} (p{w.page_index + 1}, sku={w.sku})")


if __name__ == "__main__":
    main()
