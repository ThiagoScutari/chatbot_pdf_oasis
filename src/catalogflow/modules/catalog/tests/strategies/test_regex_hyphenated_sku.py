"""Testes da estratégia de SKU `regex_hyphenated` (Sprint 08 Fase B).

Porta direta do comportamento histórico do `PDFAnalyzer` sobre o
catálogo Oasis. O contrato exato é:

- Casa `\\b(\\d{9,13}-\\d)\\b` (9-13 dígitos + hífen + 1 dígito).
- Localiza o SKU no fluxo de palavras do pdfplumber para extrair seu
  `Rect`; aceita match exato e, em fallback, substring.
- Lista vazia se a página não casa nenhum SKU.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pymupdf
import pytest

from catalogflow.modules.catalog.strategies.base import StrategyContext
from catalogflow.modules.catalog.strategies.sku.regex_hyphenated import (
    RegexHyphenatedSku,
)


def _make_ctx(text: str, words: list[dict[str, Any]]) -> StrategyContext:
    page_plumb = MagicMock()
    page_plumb.extract_text.return_value = text
    page_plumb.extract_words.return_value = words
    return StrategyContext(
        page_pymupdf=cast(Any, MagicMock()),
        page_plumb=page_plumb,
        page_width=1179.0,
        page_height=2556.0,
        page_index=0,
    )


def _word(text: str, x0: float, top: float, x1: float, bottom: float) -> dict[str, Any]:
    return {"text": text, "x0": x0, "top": top, "x1": x1, "bottom": bottom}


def test_detects_single_oasis_sku_in_text() -> None:
    ctx = _make_ctx(
        text="JAQUETA BERENICE 0322500004-0 PP-M R$ 3.488,00",
        words=[_word("0322500004-0", 100.0, 800.0, 200.0, 815.0)],
    )

    result = RegexHyphenatedSku().extract(ctx, {})

    assert len(result) == 1
    assert result[0].sku == "0322500004-0"


def test_returns_empty_when_text_has_no_match() -> None:
    ctx = _make_ctx(text="capa do catalogo sem produtos", words=[])

    result = RegexHyphenatedSku().extract(ctx, {})

    assert result == []


def test_detects_multiple_skus_on_same_page() -> None:
    ctx = _make_ctx(
        text="JAQUETA 0322500004-0 PP-M e CALCA 0142500001-0 PP-G",
        words=[
            _word("0322500004-0", 100.0, 800.0, 200.0, 815.0),
            _word("0142500001-0", 600.0, 800.0, 700.0, 815.0),
        ],
    )

    result = RegexHyphenatedSku().extract(ctx, {})

    assert [m.sku for m in result] == ["0322500004-0", "0142500001-0"]


def test_skus_without_word_mapping_are_dropped() -> None:
    # SKU aparece no texto extraído (extract_text) mas não nas words
    # (extract_words). Cenário raro mas que o código original tolera.
    ctx = _make_ctx(
        text="0322500004-0 perdido",
        words=[_word("outraCoisa", 0.0, 0.0, 50.0, 10.0)],
    )

    result = RegexHyphenatedSku().extract(ctx, {})

    assert result == []


def test_falls_back_to_substring_match_when_literal_fails() -> None:
    ctx = _make_ctx(
        text="REF: 0322500004-0",
        # Word concatenada com prefixo — match exato falha, substring funciona.
        words=[_word("REF:0322500004-0", 100.0, 800.0, 250.0, 815.0)],
    )

    result = RegexHyphenatedSku().extract(ctx, {})

    assert len(result) == 1
    assert result[0].sku == "0322500004-0"


def test_custom_pattern_via_params_overrides_default() -> None:
    ctx = _make_ctx(
        text="codigo ABC123-X-PP",
        words=[_word("ABC123-X", 100.0, 800.0, 180.0, 815.0)],
    )

    result = RegexHyphenatedSku().extract(ctx, {"pattern": r"\b([A-Z]+\d+-[A-Z])\b"})

    assert len(result) == 1
    assert result[0].sku == "ABC123-X"


def test_returns_rect_with_pymupdf_coords() -> None:
    ctx = _make_ctx(
        text="0322500004-0",
        words=[_word("0322500004-0", 100.5, 800.25, 200.75, 815.5)],
    )

    result = RegexHyphenatedSku().extract(ctx, {})

    assert isinstance(result[0].rect, pymupdf.Rect)
    assert result[0].rect.x0 == pytest.approx(100.5)
    assert result[0].rect.y0 == pytest.approx(800.25)
    assert result[0].rect.x1 == pytest.approx(200.75)
    assert result[0].rect.y1 == pytest.approx(815.5)
