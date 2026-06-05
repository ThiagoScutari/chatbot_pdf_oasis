"""Testes das dataclasses de output das estratégias.

Provam invariantes estruturais: frozen, slots, igualdade por valor,
hashabilidade. Específicos: `GradeMatch.sizes` é tupla imutável.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

import pymupdf
import pytest

from catalogflow.modules.catalog.strategies.base import (
    GradeMatch,
    NameMatch,
    PriceMatch,
    SkuMatch,
    SwatchMatch,
)


def _build_sku_match() -> SkuMatch:
    return SkuMatch(
        sku="0442500912-0",
        rect=pymupdf.Rect(0.0, 0.0, 10.0, 5.0),  # type: ignore[no-untyped-call]
    )


def _build_grade_match() -> GradeMatch:
    return GradeMatch(grade="PP-G", sizes=("PP", "P", "M", "G"))


def _build_price_match() -> PriceMatch:
    return PriceMatch(value=Decimal("3488.00"), label="Atacado")


def _build_name_match() -> NameMatch:
    return NameMatch(name="VESTIDO LONGO")


def _build_swatch_match() -> SwatchMatch:
    return SwatchMatch(
        x0=120.5,
        y0=2400.0,
        fill_rgb=(0.5, 0.25, 0.75),
        fill_hex="#8040bf",
    )


DATACLASS_FACTORIES: list[tuple[str, Any]] = [
    ("SkuMatch", _build_sku_match),
    ("GradeMatch", _build_grade_match),
    ("PriceMatch", _build_price_match),
    ("NameMatch", _build_name_match),
    ("SwatchMatch", _build_swatch_match),
]


@pytest.mark.parametrize(("label", "factory"), DATACLASS_FACTORIES)
def test_dataclass_is_frozen(label: str, factory: Any) -> None:
    instance = factory()
    field_name = next(iter(dataclasses.fields(instance))).name

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, "qualquer")


@pytest.mark.parametrize(("label", "factory"), DATACLASS_FACTORIES)
def test_dataclass_has_slots(label: str, factory: Any) -> None:
    instance = factory()

    assert hasattr(type(instance), "__slots__"), f"{label} sem __slots__"
    assert not hasattr(instance, "__dict__"), f"{label} possui __dict__"


@pytest.mark.parametrize(("label", "factory"), DATACLASS_FACTORIES)
def test_dataclass_equality_by_value(label: str, factory: Any) -> None:
    a = factory()
    b = factory()

    assert a == b


@pytest.mark.parametrize(("label", "factory"), DATACLASS_FACTORIES)
def test_dataclass_is_hashable(label: str, factory: Any) -> None:
    instance = factory()

    hash(instance)


# ──────────────────────────────────────────────
#  Específicos do GradeMatch
# ──────────────────────────────────────────────


def test_grade_match_sizes_is_tuple() -> None:
    match = _build_grade_match()

    assert isinstance(match.sizes, tuple)


def test_grade_match_sizes_is_immutable() -> None:
    match = _build_grade_match()

    with pytest.raises(TypeError):
        match.sizes[0] = "XX"  # type: ignore[index]
