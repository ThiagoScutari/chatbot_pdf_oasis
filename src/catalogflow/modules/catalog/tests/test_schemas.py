"""Testes dos schemas Pydantic do `catalog` — foco no campo `warnings`
(ADR-011 D5) e na relaxação de `sizes`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from catalogflow.modules.catalog.schemas import (
    AnalyzerWarningSchema,
    CatalogProductResponse,
    CatalogResponse,
)

_WARNING_DICT = {
    "code": "NAME_NOT_DETECTED",
    "severity": "warning",
    "page_index": 4,
    "sku": "0442500912-0",
    "message": "Nome do produto não pôde ser extraído da página 5",
    "detected_value": None,
}


def test_analyzer_warning_schema_validates_from_dict() -> None:
    w = AnalyzerWarningSchema.model_validate(_WARNING_DICT)
    assert w.code == "NAME_NOT_DETECTED"
    assert w.severity == "warning"
    assert w.page_index == 4
    assert w.sku == "0442500912-0"
    assert w.detected_value is None


def _catalog_obj(*, warnings: list[dict[str, object]]) -> SimpleNamespace:
    """Objeto com atributos equivalentes a um `Catalog` ORM (from_attributes)."""
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        brand_id=uuid4(),
        name="Catálogo",
        collection=None,
        status="ready",
        n_pages=1,
        n_product_pages=1,
        n_skus=1,
        n_fields=4,
        error_message=None,
        created_at=now,
        updated_at=now,
        products=[],
        warnings=warnings,
    )


def test_catalog_response_warnings_defaults_empty() -> None:
    # Objeto sem o atributo `warnings` → default factory aplica [].
    now = datetime.now(UTC)
    obj = SimpleNamespace(
        id=uuid4(),
        brand_id=uuid4(),
        name="Catálogo",
        collection=None,
        status="pending",
        n_pages=None,
        n_product_pages=None,
        n_skus=None,
        n_fields=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        products=[],
        warnings=[],
    )
    resp = CatalogResponse.model_validate(obj)
    assert resp.warnings == []


def test_catalog_response_serializes_warnings_from_orm_dicts() -> None:
    resp = CatalogResponse.model_validate(_catalog_obj(warnings=[_WARNING_DICT]))
    assert len(resp.warnings) == 1
    assert isinstance(resp.warnings[0], AnalyzerWarningSchema)
    assert resp.warnings[0].code == "NAME_NOT_DETECTED"
    # Round-trip JSON expõe o array de warnings.
    dumped = resp.model_dump()
    assert dumped["warnings"][0]["severity"] == "warning"


def test_catalog_product_response_accepts_none_sizes() -> None:
    product = CatalogProductResponse.model_validate(
        {
            "id": uuid4(),
            "sku": "0442500941-0",
            "name": None,
            "price": None,
            "grade": None,
            "sizes": None,
            "n_colors": 1,
            "swatches": [],
            "page_index": 0,
        },
    )
    assert product.sizes is None
    assert product.grade is None
