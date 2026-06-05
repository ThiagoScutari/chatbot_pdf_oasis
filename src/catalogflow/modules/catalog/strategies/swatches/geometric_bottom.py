"""Estratégia de swatches geométricos na zona inferior da página.

Funde duas funções do `PDFAnalyzer` original (ADR-010, Sprint 08 Fase B):

- `_detect_swatches`: filtro geométrico (y0 ≥ threshold, lados <
  max_size, fill presente e diferente do background).
- `_swatches_for`: filtro horizontal por zona Voronoi do SKU.

Equivalência matemática crítica: o original tinha um branch
`if n_prods == 1 or side == "single": return list(all_swatches)`. Em
`_assign_name_zones`, para n=1 a zona é
`Rect(0, 0, page_width, page_height)`. O filtro
`zone.x0 <= rect.x0 < zone.x1` (i.e., `0 <= rect.x0 < page_width`)
casa todos os swatches que já passaram no filtro geométrico — saída
idêntica ao branch antigo. O golden file da Fase B é o juiz final
desta equivalência.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pymupdf

from catalogflow.modules.catalog.strategies.base import (
    StrategyContext,
    SwatchesStrategy,
    SwatchMatch,
)
from catalogflow.modules.catalog.strategies.swatches import (
    register_swatches_strategy,
)


class GeometricBottomSwatches(SwatchesStrategy):
    """Detecta swatches geométricos na zona inferior da página."""

    DEFAULT_THRESHOLD_FRAC: ClassVar[float] = 0.920
    DEFAULT_MAX_SIZE_PT: ClassVar[float] = 45.0
    DEFAULT_BG_RGB: ClassVar[tuple[float, float, float]] = (1.0, 1.0, 1.0)

    def extract(
        self,
        ctx: StrategyContext,
        sku: str,
        zone: pymupdf.Rect,
        params: dict[str, Any],
    ) -> list[SwatchMatch]:
        threshold_frac = float(params.get("threshold_frac", self.DEFAULT_THRESHOLD_FRAC))
        max_size = float(params.get("max_size_pt", self.DEFAULT_MAX_SIZE_PT))
        bg_rgb = tuple(params.get("bg_rgb", self.DEFAULT_BG_RGB))

        threshold = ctx.page_height * threshold_frac

        results: list[SwatchMatch] = []
        for d in ctx.page_pymupdf.get_drawings():
            rect = d["rect"]
            fill = d.get("fill")
            if not (
                rect.y0 >= threshold
                and rect.width < max_size
                and rect.height < max_size
                and fill is not None
                and tuple(fill) != bg_rgb
            ):
                continue
            if not (zone.x0 <= rect.x0 < zone.x1):
                continue
            rgb = (
                round(float(fill[0]), 4),
                round(float(fill[1]), 4),
                round(float(fill[2]), 4),
            )
            results.append(
                SwatchMatch(
                    x0=float(rect.x0),
                    y0=float(rect.y0),
                    fill_rgb=rgb,
                    fill_hex=self._rgb_to_hex(rgb),
                ),
            )
        results.sort(key=lambda s: s.x0)
        return results

    @staticmethod
    def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
        """Mesmo formato emitido por `PDFAnalyzer._rgb_to_hex` original.

        Invariante: prefixo `#`, lowercase, 7 chars no total.
        """
        return f"#{round(rgb[0] * 255):02x}{round(rgb[1] * 255):02x}{round(rgb[2] * 255):02x}"


register_swatches_strategy("geometric_bottom", GeometricBottomSwatches)
