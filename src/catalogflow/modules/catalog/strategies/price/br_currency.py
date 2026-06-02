"""Estratégia de preço em real brasileiro (`R$ 3.488,00`).

Porta direta do `PRICE_RE` + `_parse_price` históricos do `PDFAnalyzer`
(ADR-010, Sprint 08 Fase B). Mesmo regex (`R\\$\\s*([\\d.]+,\\d{2})`)
e mesma conversão (remove pontos de milhar, substitui vírgula por
ponto, instancia `Decimal`).

`label` é sempre `None` nesta estratégia — Oasis tem preço único.
Suporte a múltiplos preços (atacado/varejo) é a estratégia
`labeled_dual` na Fase D.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, ClassVar

from catalogflow.modules.catalog.strategies.base import (
    PriceMatch,
    PriceStrategy,
    ZoneContext,
)
from catalogflow.modules.catalog.strategies.price import register_price_strategy


class BrCurrencyPrice(PriceStrategy):
    """Detecta preço em formato BR: `R$ 3.488,00`."""

    DEFAULT_PATTERN: ClassVar[str] = r"R\$\s*([\d.]+,\d{2})"

    def extract(
        self,
        zctx: ZoneContext,
        params: dict[str, Any],
    ) -> PriceMatch | None:
        pattern_str = params.get("pattern", self.DEFAULT_PATTERN)
        pattern = re.compile(pattern_str)

        matches = pattern.findall(zctx.zone_text)
        if not matches:
            return None
        raw = matches[0]
        try:
            value = Decimal(raw.replace(".", "").replace(",", "."))
        except (ArithmeticError, ValueError):
            return None
        return PriceMatch(value=value, label=None)


register_price_strategy("br_currency", BrCurrencyPrice)
