"""Testes da estratégia de SKU `regex_prefixed` (Sprint 08 Fase D).

Detecta SKUs prefixados por rótulo (`Ref: 01010012`, padrão FERLA), N
dígitos sem hífen final. Espelha o contrato de `regex_hyphenated`:

- Casa `(?:Ref|Cód|Cod|SKU)[:\\s]+(\\d{6,13})` (case-insensitive).
- Captura apenas os dígitos e localiza a palavra correspondente no fluxo
  do pdfplumber (match exato, fallback substring) para extrair o `Rect`.
- Lista vazia se a página não casa nenhum SKU prefixado.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pymupdf
import pytest

from catalogflow.modules.catalog.strategies.base import StrategyContext
from catalogflow.modules.catalog.strategies.sku.regex_prefixed import (
    RegexPrefixedSku,
)


def _make_ctx(text: str, words: list[dict[str, Any]]) -> StrategyContext:
    page_plumb = MagicMock()
    page_plumb.extract_text.return_value = text
    page_plumb.extract_words.return_value = words
    return StrategyContext(
        page_pymupdf=cast(Any, MagicMock()),
        page_plumb=page_plumb,
        page_width=595.0,
        page_height=842.0,
        page_index=0,
    )


def _word(text: str, x0: float, top: float, x1: float, bottom: float) -> dict[str, Any]:
    return {"text": text, "x0": x0, "top": top, "x1": x1, "bottom": bottom}


def test_detects_ref_prefixed_sku() -> None:
    ctx = _make_ctx(
        text="Camisa Polo Pima Clássica Ref: 01010012 Grade: P - GG",
        words=[_word("01010012", 100.0, 790.0, 160.0, 800.0)],
    )

    result = RegexPrefixedSku().extract(ctx, {})

    assert len(result) == 1
    assert result[0].sku == "01010012"


def test_detects_with_alternate_labels() -> None:
    for label in ("Cód:", "Cod:", "SKU"):
        ctx = _make_ctx(
            text=f"Produto {label} 01010012",
            words=[_word("01010012", 100.0, 790.0, 160.0, 800.0)],
        )
        result = RegexPrefixedSku().extract(ctx, {})
        assert [m.sku for m in result] == ["01010012"], label


def test_returns_empty_when_no_prefix_match() -> None:
    # Dígitos presentes, mas sem rótulo Ref/Cód/SKU antes deles.
    ctx = _make_ctx(text="total 01010012 unidades", words=[])

    result = RegexPrefixedSku().extract(ctx, {})

    assert result == []


def test_handles_sku_glued_to_label() -> None:
    # `Ref:01010012` colado: a palavra do pdfplumber contém o rótulo, então
    # o match exato falha e o fallback substring resolve.
    ctx = _make_ctx(
        text="Ref:01010012",
        words=[_word("Ref:01010012", 100.0, 790.0, 200.0, 800.0)],
    )

    result = RegexPrefixedSku().extract(ctx, {})

    assert len(result) == 1
    assert result[0].sku == "01010012"


def test_multiple_skus_on_page() -> None:
    ctx = _make_ctx(
        text="Ref: 01010013 e Ref: 01010014",
        words=[
            _word("01010013", 60.0, 790.0, 120.0, 800.0),
            _word("01010014", 420.0, 790.0, 480.0, 800.0),
        ],
    )

    result = RegexPrefixedSku().extract(ctx, {})

    assert [m.sku for m in result] == ["01010013", "01010014"]


def test_custom_pattern_via_params() -> None:
    ctx = _make_ctx(
        text="Código do item: AB-9912",
        words=[_word("AB-9912", 100.0, 790.0, 170.0, 800.0)],
    )

    result = RegexPrefixedSku().extract(
        ctx,
        {"pattern": r"item:\s*([A-Z]+-\d+)"},
    )

    assert len(result) == 1
    assert result[0].sku == "AB-9912"


def test_case_insensitive_label() -> None:
    for variant in ("ref:", "REF:", "Ref:"):
        ctx = _make_ctx(
            text=f"{variant} 01010012",
            words=[_word("01010012", 100.0, 790.0, 160.0, 800.0)],
        )
        result = RegexPrefixedSku().extract(ctx, {})
        assert [m.sku for m in result] == ["01010012"], variant


def test_returns_rect_with_pymupdf_coords() -> None:
    ctx = _make_ctx(
        text="Ref: 01010012",
        words=[_word("01010012", 100.5, 790.25, 160.75, 800.5)],
    )

    result = RegexPrefixedSku().extract(ctx, {})

    assert isinstance(result[0].rect, pymupdf.Rect)
    assert result[0].rect.x0 == pytest.approx(100.5)
    assert result[0].rect.y1 == pytest.approx(800.5)


def test_sku_without_word_mapping_is_dropped() -> None:
    # SKU no texto mas ausente das words → não há Rect, descarta.
    ctx = _make_ctx(
        text="Ref: 01010012",
        words=[_word("outra", 0.0, 0.0, 50.0, 10.0)],
    )

    result = RegexPrefixedSku().extract(ctx, {})

    assert result == []
