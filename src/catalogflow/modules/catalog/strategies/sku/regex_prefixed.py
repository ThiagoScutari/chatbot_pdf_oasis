"""Estratégia de SKU para padrões prefixados (FERLA-like).

Detecta SKUs no formato `<rótulo>: <dígitos>` — ex.: `Ref: 01010012`.
Diferente do `regex_hyphenated` (Oasis), aceita dígitos sem hífen final
e exige um rótulo (`Ref`, `Cód`, `Cod`, `SKU`) antes do código.

Espelha a estrutura de `regex_hyphenated`: detecta os SKUs no texto da
página inteira e mapeia cada um para a palavra correspondente
(`extract_words`) para obter a bounding box usada nas zonas Voronoi.
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


class RegexPrefixedSku(SkuStrategy):
    """Detecta SKU prefixado por rótulo (`Ref:`, `Cód:`, `SKU`).

    Aceita parâmetro `pattern` para override do regex via profile JSON. O
    grupo capturado é apenas a sequência de dígitos (sem o rótulo).
    """

    # Captura o grupo de dígitos após o rótulo. `Ref: 01010012` → "01010012".
    DEFAULT_PATTERN: ClassVar[str] = r"(?:Ref|Cód|Cod|SKU)[:\s]+(\d{6,13})"

    def extract(
        self,
        ctx: StrategyContext,
        params: dict[str, Any],
    ) -> list[SkuMatch]:
        pattern = re.compile(params.get("pattern", self.DEFAULT_PATTERN), re.IGNORECASE)

        text = ctx.page_plumb.extract_text() or ""
        skus = pattern.findall(text)
        if not skus:
            return []

        words = ctx.page_plumb.extract_words(extra_attrs=["size", "fontname"])
        results: list[SkuMatch] = []
        for sku in skus:
            # O grupo capturado é só os dígitos. Primeiro tenta match exato
            # da palavra (`Ref:` separado por espaço); senão substring
            # (`Ref:01010012` colado em uma única palavra).
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


register_sku_strategy("regex_prefixed", RegexPrefixedSku)
