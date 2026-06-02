"""Testes da estratégia de grade `alpha_range` (Sprint 08 Fase B).

Porta do `GRADE_RE` + `SIZE_MAP` históricos do `PDFAnalyzer`. Detecta
6 faixas alfabéticas (PP-GG..P-M) e devolve a expansão de tamanhos.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pymupdf

from catalogflow.modules.catalog.strategies.base import ZoneContext
from catalogflow.modules.catalog.strategies.grade.alpha_range import (
    AlphaRangeGrade,
)


def _zctx(text: str) -> ZoneContext:
    return ZoneContext(
        sku="dummy",
        zone=cast(pymupdf.Rect, MagicMock()),
        zone_words=[],
        zone_text=text,
    )


def test_detects_pp_gg_grade() -> None:
    result = AlphaRangeGrade().extract(_zctx("VESTIDO MIA PP-GG R$ 299,00"), {})

    assert result is not None
    assert result.grade == "PP-GG"
    assert result.sizes == ("PP", "P", "M", "G", "GG")


def test_detects_pp_g_grade() -> None:
    result = AlphaRangeGrade().extract(_zctx("BLUSA PP-G"), {})

    assert result is not None
    assert result.grade == "PP-G"
    assert result.sizes == ("PP", "P", "M", "G")


def test_detects_pp_m_grade() -> None:
    result = AlphaRangeGrade().extract(_zctx("JAQUETA PP-M"), {})

    assert result is not None
    assert result.grade == "PP-M"
    assert result.sizes == ("PP", "P", "M")


def test_detects_p_gg_grade() -> None:
    result = AlphaRangeGrade().extract(_zctx("CONJUNTO P-GG"), {})

    assert result is not None
    assert result.grade == "P-GG"
    assert result.sizes == ("P", "M", "G", "GG")


def test_detects_p_g_grade() -> None:
    result = AlphaRangeGrade().extract(_zctx("BODY P-G"), {})

    assert result is not None
    assert result.grade == "P-G"
    assert result.sizes == ("P", "M", "G")


def test_detects_p_m_grade() -> None:
    result = AlphaRangeGrade().extract(_zctx("SHORT P-M"), {})

    assert result is not None
    assert result.grade == "P-M"
    assert result.sizes == ("P", "M")


def test_returns_none_when_no_grade_in_text() -> None:
    result = AlphaRangeGrade().extract(_zctx("nada de grade aqui"), {})

    assert result is None


def test_custom_patterns_via_params_overrides_default() -> None:
    custom = ("XS-XL",)
    # Default map não tem XS-XL, então a expansão fica None → método devolve None
    # (porque size map não casa). Para validar override do PATTERN sem mexer no
    # map, usamos um pattern que casa um label que ESTÁ no DEFAULT_SIZE_MAP.
    result = AlphaRangeGrade().extract(
        _zctx("etiqueta P-M no rodapé"),
        {"patterns": ["P-M"]},
    )

    assert result is not None
    assert result.grade == "P-M"
    # Custom que não casa nada no texto retorna None.
    result_none = AlphaRangeGrade().extract(_zctx("não tem etiqueta"), {"patterns": list(custom)})
    assert result_none is None


def test_sizes_is_tuple_not_list() -> None:
    result = AlphaRangeGrade().extract(_zctx("BLUSA PP-G"), {})

    assert result is not None
    assert isinstance(result.sizes, tuple)


def test_returns_none_when_pattern_matches_but_size_map_does_not() -> None:
    """Se patterns aceita um label fora do DEFAULT_SIZE_MAP, retorna None.

    Salvaguarda contra profile mal configurado — orquestrador trata como
    ausência (mesmo comportamento de quando não há match).
    """
    result = AlphaRangeGrade().extract(
        _zctx("etiqueta XS-XL"),
        {"patterns": ["XS-XL"]},
    )

    assert result is None


def _unused_marker(_: Any) -> None:
    """Marcador para suprimir ruff F811 do parâmetro Any importado."""
