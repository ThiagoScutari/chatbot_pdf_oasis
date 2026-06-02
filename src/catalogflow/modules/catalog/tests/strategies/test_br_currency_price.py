"""Testes da estratégia de preço `br_currency` (Sprint 08 Fase B).

Porta direta do `PRICE_RE` + `_parse_price` históricos do `PDFAnalyzer`.
Detecta preços no formato BR (`R$ 3.488,00` — ponto = milhar, vírgula
= decimal) e devolve `Decimal` exato. `label` sempre `None` no profile
Oasis (preço único por produto).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from unittest.mock import MagicMock

import pymupdf

from catalogflow.modules.catalog.strategies.base import ZoneContext
from catalogflow.modules.catalog.strategies.price.br_currency import (
    BrCurrencyPrice,
)


def _zctx(text: str) -> ZoneContext:
    return ZoneContext(
        sku="dummy",
        zone=cast(pymupdf.Rect, MagicMock()),
        zone_words=[],
        zone_text=text,
    )


def test_detects_price_with_thousands_separator() -> None:
    result = BrCurrencyPrice().extract(_zctx("VESTIDO R$ 3.488,00 PP-G"), {})

    assert result is not None
    assert result.value == Decimal("3488.00")


def test_detects_price_without_thousands_separator() -> None:
    result = BrCurrencyPrice().extract(_zctx("BLUSA R$ 299,00"), {})

    assert result is not None
    assert result.value == Decimal("299.00")


def test_returns_none_when_no_price_in_text() -> None:
    result = BrCurrencyPrice().extract(_zctx("sem preco aqui"), {})

    assert result is None


def test_returns_none_when_decimal_conversion_fails() -> None:
    """Pattern customizado captura algo que não converte em Decimal."""
    result = BrCurrencyPrice().extract(
        _zctx("PRICE: abc,xy"),
        {"pattern": r"PRICE:\s*([a-z.]+,[a-z]{2})"},
    )

    assert result is None


def test_picks_first_price_when_multiple_match() -> None:
    result = BrCurrencyPrice().extract(_zctx("R$ 100,00 ou R$ 200,00"), {})

    assert result is not None
    assert result.value == Decimal("100.00")


def test_label_is_always_none_in_br_currency() -> None:
    result = BrCurrencyPrice().extract(_zctx("R$ 50,00"), {})

    assert result is not None
    assert result.label is None


def _unused(_: Any) -> None:
    """Marcador no-op."""
