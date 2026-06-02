"""Testes da estratégia de swatches `geometric_bottom` (Sprint 08 Fase B).

Funde `_detect_swatches` + `_swatches_for` do `PDFAnalyzer` original
em uma única chamada. Critérios geométricos (zona inferior, tamanho
máximo, fill não-branco) preservados bit-a-bit; filtro horizontal por
zona Voronoi do SKU substitui o branch `n_prods == 1`.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pymupdf

from catalogflow.modules.catalog.strategies.base import StrategyContext
from catalogflow.modules.catalog.strategies.swatches.geometric_bottom import (
    GeometricBottomSwatches,
)

PAGE_W = 1179.0
PAGE_H = 2556.0
THRESHOLD_Y = PAGE_H * 0.920  # 2351.52


def _drawing(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    fill: tuple[float, float, float] | None,
) -> dict[str, Any]:
    return {"rect": pymupdf.Rect(x0, y0, x1, y1), "fill": fill}  # type: ignore[no-untyped-call]


def _ctx(drawings: list[dict[str, Any]]) -> StrategyContext:
    page_pymupdf = MagicMock()
    page_pymupdf.get_drawings.return_value = drawings
    return StrategyContext(
        page_pymupdf=page_pymupdf,
        page_plumb=cast(Any, MagicMock()),
        page_width=PAGE_W,
        page_height=PAGE_H,
        page_index=0,
    )


FULL_PAGE_ZONE = pymupdf.Rect(0.0, 0.0, PAGE_W, PAGE_H)  # type: ignore[no-untyped-call]


def test_detects_swatches_in_bottom_zone() -> None:
    drawings = [_drawing(100.0, THRESHOLD_Y + 10, 120.0, THRESHOLD_Y + 30, (0.5, 0.25, 0.1))]
    result = GeometricBottomSwatches().extract(_ctx(drawings), "SKU", FULL_PAGE_ZONE, {})

    assert len(result) == 1
    assert result[0].x0 == 100.0


def test_ignores_drawings_above_threshold_y() -> None:
    drawings = [_drawing(100.0, THRESHOLD_Y - 10, 120.0, THRESHOLD_Y + 10, (0.5, 0.5, 0.5))]
    result = GeometricBottomSwatches().extract(_ctx(drawings), "SKU", FULL_PAGE_ZONE, {})

    assert result == []


def test_ignores_drawings_wider_than_max_size() -> None:
    drawings = [
        _drawing(100.0, THRESHOLD_Y + 5, 100.0 + 45.0, THRESHOLD_Y + 30, (0.5, 0.5, 0.5)),
    ]
    result = GeometricBottomSwatches().extract(_ctx(drawings), "SKU", FULL_PAGE_ZONE, {})

    assert result == []


def test_ignores_drawings_taller_than_max_size() -> None:
    drawings = [
        _drawing(100.0, THRESHOLD_Y + 5, 120.0, THRESHOLD_Y + 5 + 45.0, (0.5, 0.5, 0.5)),
    ]
    result = GeometricBottomSwatches().extract(_ctx(drawings), "SKU", FULL_PAGE_ZONE, {})

    assert result == []


def test_ignores_drawings_without_fill() -> None:
    drawings = [_drawing(100.0, THRESHOLD_Y + 5, 120.0, THRESHOLD_Y + 25, None)]
    result = GeometricBottomSwatches().extract(_ctx(drawings), "SKU", FULL_PAGE_ZONE, {})

    assert result == []


def test_ignores_drawings_with_background_fill_white() -> None:
    drawings = [_drawing(100.0, THRESHOLD_Y + 5, 120.0, THRESHOLD_Y + 25, (1.0, 1.0, 1.0))]
    result = GeometricBottomSwatches().extract(_ctx(drawings), "SKU", FULL_PAGE_ZONE, {})

    assert result == []


def test_filters_by_horizontal_zone() -> None:
    drawings = [
        _drawing(100.0, THRESHOLD_Y + 5, 120.0, THRESHOLD_Y + 25, (0.5, 0.5, 0.5)),
        _drawing(800.0, THRESHOLD_Y + 5, 820.0, THRESHOLD_Y + 25, (0.2, 0.3, 0.4)),
    ]
    left_zone = pymupdf.Rect(0.0, 0.0, 500.0, PAGE_H)  # type: ignore[no-untyped-call]

    result = GeometricBottomSwatches().extract(_ctx(drawings), "SKU", left_zone, {})

    assert len(result) == 1
    assert result[0].x0 == 100.0


def test_results_sorted_by_x0() -> None:
    drawings = [
        _drawing(800.0, THRESHOLD_Y + 5, 820.0, THRESHOLD_Y + 25, (0.1, 0.2, 0.3)),
        _drawing(100.0, THRESHOLD_Y + 5, 120.0, THRESHOLD_Y + 25, (0.4, 0.5, 0.6)),
        _drawing(400.0, THRESHOLD_Y + 5, 420.0, THRESHOLD_Y + 25, (0.7, 0.8, 0.9)),
    ]
    result = GeometricBottomSwatches().extract(_ctx(drawings), "SKU", FULL_PAGE_ZONE, {})

    assert [s.x0 for s in result] == [100.0, 400.0, 800.0]


def test_fill_hex_format_is_lowercase_with_hash_prefix() -> None:
    drawings = [_drawing(100.0, THRESHOLD_Y + 5, 120.0, THRESHOLD_Y + 25, (0.5, 0.25, 0.75))]
    result = GeometricBottomSwatches().extract(_ctx(drawings), "SKU", FULL_PAGE_ZONE, {})

    hex_value = result[0].fill_hex
    assert hex_value.startswith("#")
    assert len(hex_value) == 7
    assert hex_value == hex_value.lower()


def test_custom_params_threshold_max_size_bg_rgb() -> None:
    drawings = [
        # Acima do threshold customizado (0.5 * 2556 = 1278), 5x5 < max_size=10, passa.
        _drawing(100.0, 1300.0, 105.0, 1305.0, (0.2, 0.3, 0.4)),
        # Tamanho ≥ max_size_pt customizado (10), descarta.
        _drawing(200.0, 1400.0, 211.0, 1420.0, (0.5, 0.5, 0.5)),
        # Fill = bg_rgb customizado (preto), descarta.
        _drawing(300.0, 1400.0, 305.0, 1405.0, (0.0, 0.0, 0.0)),
    ]
    params = {"threshold_frac": 0.5, "max_size_pt": 10.0, "bg_rgb": [0.0, 0.0, 0.0]}

    result = GeometricBottomSwatches().extract(_ctx(drawings), "SKU", FULL_PAGE_ZONE, params)

    assert len(result) == 1
    assert result[0].x0 == 100.0


def test_rgb_values_rounded_to_four_decimals() -> None:
    """Comportamento bit-a-bit do original (`round(float(fill[i]), 4)`)."""
    drawings = [
        _drawing(
            100.0,
            THRESHOLD_Y + 5,
            120.0,
            THRESHOLD_Y + 25,
            (0.123456789, 0.987654321, 0.5),
        ),
    ]

    result = GeometricBottomSwatches().extract(_ctx(drawings), "SKU", FULL_PAGE_ZONE, {})

    assert result[0].fill_rgb == (0.1235, 0.9877, 0.5)
