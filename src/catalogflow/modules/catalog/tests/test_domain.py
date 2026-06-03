"""Testes da primitiva de domínio `AnalyzerWarning` (ADR-011 D1/D3)."""

from __future__ import annotations

import dataclasses

import pytest

from catalogflow.modules.catalog import domain
from catalogflow.modules.catalog.domain import AnalyzerWarning


def _warning(**overrides: object) -> AnalyzerWarning:
    base = {
        "code": domain.GRADE_NOT_DETECTED,
        "severity": domain.SEVERITY_ERROR,
        "page_index": 0,
        "sku": "0442500912-0",
        "message": "msg",
        "detected_value": None,
    }
    base.update(overrides)
    return AnalyzerWarning(**base)  # type: ignore[arg-type]


def test_analyzer_warning_is_frozen() -> None:
    w = _warning()
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.code = "OUTRO"  # type: ignore[misc]


def test_analyzer_warning_has_slots() -> None:
    w = _warning()
    assert not hasattr(w, "__dict__")
    assert AnalyzerWarning.__slots__ == (
        "code",
        "severity",
        "page_index",
        "sku",
        "message",
        "detected_value",
    )


def test_analyzer_warning_equality_by_value() -> None:
    assert _warning() == _warning()
    assert _warning(page_index=1) != _warning(page_index=2)


def test_analyzer_warning_is_hashable() -> None:
    # frozen=True torna a dataclass hashable — usável em set/dict.
    assert len({_warning(), _warning(), _warning(page_index=9)}) == 2


def test_severity_constants_have_expected_values() -> None:
    assert domain.SEVERITY_INFO == "info"
    assert domain.SEVERITY_WARNING == "warning"
    assert domain.SEVERITY_ERROR == "error"


def test_warning_code_constants_are_uppercase_with_underscores() -> None:
    for code in (
        domain.GRADE_NOT_DETECTED,
        domain.NAME_NOT_DETECTED,
        domain.PRICE_NOT_DETECTED,
        domain.SWATCHES_NOT_DETECTED,
        domain.FIELDS_NOT_INJECTED_NO_GRADE,
    ):
        assert code == code.upper()
        assert set(code) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ_")


def test_all_warning_codes_are_unique() -> None:
    codes = [
        domain.GRADE_NOT_DETECTED,
        domain.NAME_NOT_DETECTED,
        domain.PRICE_NOT_DETECTED,
        domain.SWATCHES_NOT_DETECTED,
        domain.FIELDS_NOT_INJECTED_NO_GRADE,
    ]
    assert len(codes) == len(set(codes))
