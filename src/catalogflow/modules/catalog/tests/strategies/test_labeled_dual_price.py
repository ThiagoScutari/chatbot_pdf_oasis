"""Testes da estratégia de preço `labeled_dual` (Sprint 08 Fase D).

Detecta preços rotulados (FERLA: `Atacado - 299` / `Varejo - 319`),
sem `R$` e sem decimal obrigatório. Devolve o `PriceMatch` do rótulo
primário (`primary_label`, default "Atacado"); na ausência do primário,
o primeiro rótulo encontrado.
"""

from __future__ import annotations

from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock

import pymupdf

from catalogflow.modules.catalog.strategies.base import ZoneContext
from catalogflow.modules.catalog.strategies.price.labeled_dual import (
    LabeledDualPrice,
)


def _zctx(text: str) -> ZoneContext:
    return ZoneContext(
        sku="dummy",
        zone=cast(pymupdf.Rect, MagicMock()),
        zone_words=[],
        zone_text=text,
    )


def test_detects_atacado_and_varejo() -> None:
    result = LabeledDualPrice().extract(_zctx("Atacado - 299 Varejo - 319"), {})

    assert result is not None
    # Ambos foram capturados; o primário (Atacado) é o devolvido.
    assert result.value == Decimal("299")
    assert result.label == "Atacado"


def test_returns_primary_label_value() -> None:
    result = LabeledDualPrice().extract(_zctx("Varejo - 319 Atacado - 299"), {})

    assert result is not None
    assert result.label == "Atacado"
    assert result.value == Decimal("299")


def test_custom_primary_label() -> None:
    result = LabeledDualPrice().extract(
        _zctx("Atacado - 299 Varejo - 319"),
        {"primary_label": "Varejo"},
    )

    assert result is not None
    assert result.label == "Varejo"
    assert result.value == Decimal("319")


def test_parses_integer_value() -> None:
    result = LabeledDualPrice().extract(_zctx("Atacado - 299"), {})

    assert result is not None
    assert result.value == Decimal("299")


def test_parses_br_decimal() -> None:
    result = LabeledDualPrice().extract(_zctx("Atacado: R$ 1.299,00"), {})

    assert result is not None
    assert result.value == Decimal("1299.00")


def test_returns_none_when_no_label_match() -> None:
    result = LabeledDualPrice().extract(_zctx("Preço sob consulta"), {})

    assert result is None


def test_falls_back_to_first_label_when_primary_absent() -> None:
    # Só "Varejo" presente; primário "Atacado" ausente → devolve Varejo.
    result = LabeledDualPrice().extract(_zctx("Varejo - 319"), {})

    assert result is not None
    assert result.label == "Varejo"
    assert result.value == Decimal("319")


def test_custom_labels_via_params() -> None:
    result = LabeledDualPrice().extract(
        _zctx("Distribuidor - 89 Consumidor - 129"),
        {"labels": ["Distribuidor", "Consumidor"], "primary_label": "Consumidor"},
    )

    assert result is not None
    assert result.label == "Consumidor"
    assert result.value == Decimal("129")


def test_returns_none_when_label_has_no_numeric_value() -> None:
    # Rótulo presente mas sem dígitos após ele → regex não casa.
    result = LabeledDualPrice().extract(_zctx("Atacado - sob consulta"), {})

    assert result is None


def test_returns_none_when_captured_value_is_unparseable() -> None:
    # O grupo casa "..." (só pontos), mas `Decimal("")` falha → _parse_value None.
    result = LabeledDualPrice().extract(_zctx("Atacado - ..."), {})

    assert result is None
