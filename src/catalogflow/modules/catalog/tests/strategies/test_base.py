"""Testes das ABCs e dataclasses de contexto em `strategies/base.py`.

A fase A define a infraestrutura do Strategy Pattern. Estes testes provam
as invariantes estruturais (ABCs não instanciáveis, dataclasses frozen +
slots) sem depender de nenhum PDF real.
"""

from __future__ import annotations

import dataclasses
from typing import Any, cast

import pymupdf
import pytest

from catalogflow.modules.catalog.strategies.base import (
    GradeStrategy,
    NameStrategy,
    PriceStrategy,
    SkuStrategy,
    StrategyContext,
    SwatchesStrategy,
    ZoneContext,
)

# ──────────────────────────────────────────────
#  ABCs não instanciáveis
# ──────────────────────────────────────────────

ABC_CLASSES = [
    SkuStrategy,
    GradeStrategy,
    PriceStrategy,
    NameStrategy,
    SwatchesStrategy,
]


@pytest.mark.parametrize("abc_cls", ABC_CLASSES)
def test_strategy_abc_cannot_be_instantiated_directly(abc_cls: type) -> None:
    with pytest.raises(TypeError):
        abc_cls()


@pytest.mark.parametrize("abc_cls", ABC_CLASSES)
def test_subclass_without_extract_cannot_be_instantiated(abc_cls: type) -> None:
    incomplete_subclass = type("Incomplete", (abc_cls,), {})

    with pytest.raises(TypeError):
        incomplete_subclass()


def _make_extract(return_value: Any) -> Any:
    def extract(self: Any, *args: Any, **kwargs: Any) -> Any:
        return return_value

    return extract


@pytest.mark.parametrize(
    ("abc_cls", "return_value"),
    [
        (SkuStrategy, []),
        (GradeStrategy, None),
        (PriceStrategy, None),
        (NameStrategy, None),
        (SwatchesStrategy, []),
    ],
)
def test_subclass_with_extract_can_be_instantiated(abc_cls: type, return_value: Any) -> None:
    concrete = type("Concrete", (abc_cls,), {"extract": _make_extract(return_value)})

    instance = concrete()

    assert isinstance(instance, abc_cls)


# ──────────────────────────────────────────────
#  Dataclasses de contexto: frozen + slots
# ──────────────────────────────────────────────


def _build_strategy_context() -> StrategyContext:
    # Os testes desta seção checam apenas frozen/slots — não exercitam os
    # objetos PyMuPDF/pdfplumber, então `object()` basta como sentinel.
    return StrategyContext(
        page_pymupdf=cast(Any, object()),
        page_plumb=cast(Any, object()),
        page_width=595.0,
        page_height=842.0,
        page_index=0,
    )


def _build_zone_context() -> ZoneContext:
    return ZoneContext(
        sku="0442500912-0",
        zone=cast(pymupdf.Rect, object()),
        zone_words=[{"text": "PP-G", "x0": 0.0, "x1": 10.0}],
        zone_text="PP-G",
    )


def test_strategy_context_is_frozen() -> None:
    ctx = _build_strategy_context()

    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.page_width = 999.0  # type: ignore[misc]


def test_zone_context_is_frozen() -> None:
    zctx = _build_zone_context()

    with pytest.raises(dataclasses.FrozenInstanceError):
        zctx.sku = "OUTRO"  # type: ignore[misc]


def test_strategy_context_has_slots() -> None:
    assert hasattr(StrategyContext, "__slots__")
    assert not hasattr(_build_strategy_context(), "__dict__")


def test_zone_context_has_slots() -> None:
    assert hasattr(ZoneContext, "__slots__")
    assert not hasattr(_build_zone_context(), "__dict__")
