"""Testes da estratégia de nome `category_vocabulary` (Sprint 08 Fase B).

Porta direta do `NAME_RE` histórico do `PDFAnalyzer` (10 categorias de
moda feminina Oasis: JAQUETA, CALÇA/CALCA, VESTIDO, CONJUNTO, BLUSA,
BODY, SHORT, BLAZER, SAIA, TOP). Default a partir da Sprint 08 é
`positional_title` (Fase D); esta estratégia permanece como opt-in.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pymupdf
import pytest

from catalogflow.modules.catalog.strategies.base import ZoneContext
from catalogflow.modules.catalog.strategies.name.category_vocabulary import (
    CategoryVocabularyName,
)


def _zctx(text: str) -> ZoneContext:
    return ZoneContext(
        sku="dummy",
        zone=cast(pymupdf.Rect, MagicMock()),
        zone_words=[],
        zone_text=text,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("JAQUETA URBANA PRO 0322500004-0", "JAQUETA URBANA PRO"),
        ("CALÇA CAPRI ESTHER", "CALÇA CAPRI ESTHER"),
        ("CALCA SLIM PRETO", "CALCA SLIM PRETO"),
        ("VESTIDO LONGO 1234567890-1", "VESTIDO LONGO"),
        ("CONJUNTO BÁSICO", "CONJUNTO BÁSICO"),
        ("BLUSA SIMPLES", "BLUSA SIMPLES"),
        ("BODY RENDADO", "BODY RENDADO"),
        ("SHORT JEANS", "SHORT JEANS"),
        ("BLAZER ALFAIATARIA", "BLAZER ALFAIATARIA"),
        ("SAIA MIDI", "SAIA MIDI"),
        ("TOP CROPPED", "TOP CROPPED"),
    ],
)
def test_detects_each_category(text: str, expected: str) -> None:
    result = CategoryVocabularyName().extract(_zctx(text), {})

    assert result is not None
    assert result.name == expected


def test_returns_none_when_no_category_matches() -> None:
    result = CategoryVocabularyName().extract(_zctx("PEÇA SEM CATEGORIA"), {})

    assert result is None


def test_match_is_case_insensitive_but_output_uppercase() -> None:
    result = CategoryVocabularyName().extract(_zctx("Jaqueta Berenice"), {})

    assert result is not None
    assert result.name == "JAQUETA BERENICE"


def test_greedy_capture_extends_through_subsequent_words() -> None:
    """O regex é intencionalmente greedy (porta direta do NAME_RE original).

    Em produção isso é seguro porque `_extract_legend_blocks` (e o novo
    orquestrador) restringem `zone_text` à zona Voronoi do SKU, então o
    nome de um produto nunca vê palavras do vizinho. Este teste
    documenta o comportamento bit-a-bit; mudá-lo é mudar o golden file.
    """
    result = CategoryVocabularyName().extract(
        _zctx("BLUSA UMA depois VESTIDO DOIS"),
        {},
    )

    assert result is not None
    assert result.name == "BLUSA UMA DEPOIS VESTIDO DOIS"


def test_custom_pattern_via_params_overrides_default() -> None:
    result = CategoryVocabularyName().extract(
        _zctx("CAMISA POLO MASCULINA"),
        {"pattern": r"\b((?:CAMISA|BERMUDA)(?:\s+[A-Za-z]{2,})*)\b"},
    )

    assert result is not None
    assert result.name == "CAMISA POLO MASCULINA"


def _unused(_: Any) -> None:
    """Marcador no-op."""
