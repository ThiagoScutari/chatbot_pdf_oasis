"""Testes da estratégia de nome `positional_title` (Sprint 08 Fase D).

Zero-vocabulário: seleciona o texto de maior peso tipográfico (`size`)
da zona Voronoi, agrupa palavras de tamanho próximo ao máximo (dentro de
`size_tolerance`) e as reordena na ordem de leitura. Requer que cada
palavra carregue a chave `size` (via `extra_attrs` no orquestrador).

Hotfix FERLA: a seleção exclui linhas que casam padrões de outros eixos
(preço/SKU/grade/rótulos) ANTES de medir a tipografia, porque no FERLA
real o preço é impresso em peso maior que o nome. Os cenários abaixo
cobrem essa exclusão por linha.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pymupdf

from catalogflow.modules.catalog.strategies.base import ZoneContext
from catalogflow.modules.catalog.strategies.name.positional_title import (
    PositionalTitleName,
)


def _word(
    text: str,
    *,
    size: float | None,
    top: float = 100.0,
    x0: float = 50.0,
    fontname: str = "Helvetica",
) -> dict[str, Any]:
    return {
        "text": text,
        "size": size,
        "fontname": fontname,
        "top": top,
        "x0": x0,
        "x1": x0 + 30.0,
        "bottom": top + 10.0,
    }


def _zctx(words: list[dict[str, Any]], *, sku: str = "01010012") -> ZoneContext:
    text = " ".join(str(w["text"]) for w in words)
    return ZoneContext(
        sku=sku,
        zone=cast(pymupdf.Rect, MagicMock()),
        zone_words=words,
        zone_text=text,
    )


def test_picks_largest_font_text() -> None:
    words = [
        _word("Ref:", size=9.0, top=200.0, x0=50.0),
        _word("01010012", size=9.0, top=200.0, x0=90.0),
        _word("Camisa", size=16.0, top=100.0, x0=50.0),
    ]
    result = PositionalTitleName().extract(_zctx(words), {})

    assert result is not None
    assert result.name == "Camisa"


def test_groups_words_of_same_size_into_title() -> None:
    words = [
        _word("Camisa", size=16.0, top=100.0, x0=50.0),
        _word("Polo", size=16.0, top=100.0, x0=110.0),
        _word("Clássica", size=16.0, top=100.0, x0=160.0),
        _word("detalhe", size=9.0, top=200.0, x0=50.0),
    ]
    result = PositionalTitleName().extract(_zctx(words), {})

    assert result is not None
    assert result.name == "Camisa Polo Clássica"


def test_returns_none_when_no_typography() -> None:
    # Palavras sem `size` (extração legada sem extra_attrs) → None.
    words = [_word("Camisa", size=None), _word("Polo", size=None)]
    result = PositionalTitleName().extract(_zctx(words), {})

    assert result is None


def test_returns_none_when_no_words() -> None:
    result = PositionalTitleName().extract(_zctx([]), {})

    assert result is None


def test_does_not_return_sku_as_name() -> None:
    # Zona contendo APENAS o SKU (linha de dígitos longos é ruído e é
    # excluída). Sem sobreviventes → None: o SKU nunca vira nome.
    words = [
        _word("01010012", size=22.0, top=100.0, x0=50.0),
    ]
    result = PositionalTitleName().extract(_zctx(words, sku="01010012"), {})

    assert result is None


def test_excludes_price_line_even_if_largest_font() -> None:
    # Caso FERLA real: o preço (13.0) é maior que o nome (12.0). A linha de
    # preço é excluída como ruído ANTES de medir a fonte; o nome (menor)
    # vence. Também guarda contra o bug do hífen órfão: "-" (13.0) não
    # pode sobreviver solto e virar o "nome".
    words = [
        _word("Camisa", size=12.0, top=100.0, x0=50.0),
        _word("Polo", size=12.0, top=100.0, x0=110.0),
        _word("Atacado", size=13.0, top=150.0, x0=50.0),
        _word("-", size=13.0, top=150.0, x0=110.0),
        _word("299", size=13.0, top=150.0, x0=130.0),
    ]
    result = PositionalTitleName().extract(_zctx(words), {})

    assert result is not None
    assert result.name == "Camisa Polo"


def test_excludes_sku_and_grade_lines() -> None:
    # Linhas de SKU rotulado e de grade são excluídas; sobra só o nome.
    words = [
        _word("Bermuda", size=12.0, top=100.0, x0=50.0),
        _word("Ref:", size=12.0, top=120.0, x0=50.0),
        _word("02010011", size=12.0, top=120.0, x0=90.0),
        _word("Grade:", size=12.0, top=140.0, x0=50.0),
        _word("P", size=12.0, top=140.0, x0=100.0),
        _word("-", size=12.0, top=140.0, x0=110.0),
        _word("GG", size=12.0, top=140.0, x0=120.0),
    ]
    result = PositionalTitleName().extract(_zctx(words), {})

    assert result is not None
    assert result.name == "Bermuda"


def test_picks_largest_non_noise_line() -> None:
    # Entre DUAS linhas não-ruído de tamanhos diferentes, vence a maior.
    words = [
        _word("Camisa", size=16.0, top=100.0, x0=50.0),
        _word("Premium", size=16.0, top=100.0, x0=110.0),
        _word("algodão", size=10.0, top=130.0, x0=50.0),
        _word("egípcio", size=10.0, top=130.0, x0=100.0),
    ]
    result = PositionalTitleName().extract(_zctx(words), {})

    assert result is not None
    assert result.name == "Camisa Premium"


def test_returns_none_when_all_lines_are_noise() -> None:
    # Zona só com dados estruturados (preço/SKU/grade) → sem nome → None.
    words = [
        _word("Ref:", size=12.0, top=100.0, x0=50.0),
        _word("02010011", size=12.0, top=100.0, x0=90.0),
        _word("Atacado", size=13.0, top=120.0, x0=50.0),
        _word("299", size=13.0, top=120.0, x0=110.0),
    ]
    result = PositionalTitleName().extract(_zctx(words), {})

    assert result is None


def test_respects_size_tolerance_param() -> None:
    # Com tolerância 0.5 (default), 15.0 NÃO entra no título de 16.0.
    words = [
        _word("Camisa", size=16.0, top=100.0, x0=50.0),
        _word("subtítulo", size=15.0, top=120.0, x0=50.0),
    ]
    tight = PositionalTitleName().extract(_zctx(words), {})
    assert tight is not None
    assert tight.name == "Camisa"

    # Com tolerância 2.0, o de 15.0 é agrupado.
    result = PositionalTitleName().extract(_zctx(words), {"size_tolerance": 2.0})
    assert result is not None
    assert result.name == "Camisa subtítulo"


def test_returns_none_when_title_text_is_empty() -> None:
    # Palavra com tipografia mas texto vazio → nome reconstruído vazio → None.
    words = [_word("", size=16.0, top=100.0, x0=50.0)]
    result = PositionalTitleName().extract(_zctx(words), {})

    assert result is None


def test_orders_title_words_by_reading_order() -> None:
    # Palavras fora de ordem na lista; saída deve seguir (top, x0).
    words = [
        _word("Premium", size=16.0, top=100.0, x0=200.0),
        _word("Gola", size=16.0, top=100.0, x0=110.0),
        _word("Camiseta", size=16.0, top=100.0, x0=50.0),
    ]
    result = PositionalTitleName().extract(_zctx(words), {})

    assert result is not None
    assert result.name == "Camiseta Gola Premium"
