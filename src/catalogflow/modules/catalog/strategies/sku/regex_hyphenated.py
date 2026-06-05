"""Estratégia de SKU para o padrão Oasis (N dígitos + hífen + 1 dígito).

Porta direta do comportamento histórico do `PDFAnalyzer` (ADR-010, Sprint
08 Fase B). Mesmo regex (`\\b(\\d{9,13}-\\d)\\b`) e mesmo fallback de
mapeamento palavra→SKU (match exato, depois substring) usados pelo
`_extract_legend_blocks` antes do refator.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

import pymupdf

from catalogflow.modules.catalog.strategies.base import (
    SkuMatch,
    SkuStrategy,
    StrategyContext,
)
from catalogflow.modules.catalog.strategies.sku import register_sku_strategy


class RegexHyphenatedSku(SkuStrategy):
    """Detecta SKUs no formato `\\d{9,13}-\\d` (padrão Oasis MOTION).

    Aceita parâmetro `pattern` para override do regex via profile JSON.
    """

    DEFAULT_PATTERN: ClassVar[str] = r"\b(\d{9,13}-\d)\b"

    def extract(
        self,
        ctx: StrategyContext,
        params: dict[str, Any],
    ) -> list[SkuMatch]:
        pattern_str = params.get("pattern", self.DEFAULT_PATTERN)
        pattern = re.compile(pattern_str)

        text = ctx.page_plumb.extract_text() or ""
        skus = pattern.findall(text)
        if not skus:
            return []

        words = ctx.page_plumb.extract_words()
        results: list[SkuMatch] = []
        for sku in skus:
            sku_word = next((w for w in words if w["text"] == sku), None)
            if sku_word is None:
                sku_word = next((w for w in words if sku in w["text"]), None)
            if sku_word is None:
                continue
            rect = pymupdf.Rect(  # type: ignore[no-untyped-call]
                float(sku_word["x0"]),
                float(sku_word["top"]),
                float(sku_word["x1"]),
                float(sku_word["bottom"]),
            )
            results.append(SkuMatch(sku=sku, rect=rect))
        return results


register_sku_strategy("regex_hyphenated", RegexHyphenatedSku)
