"""Estratégia de preço rotulado dual (Atacado/Varejo — FERLA-like).

O catálogo FERLA expõe dois valores por produto (`Atacado - 299` /
`Varejo - 319`), sem prefixo `R$` e (em geral) sem decimal. Esta
estratégia captura ambos os rótulos configuráveis e devolve o
`PriceMatch` do rótulo primário (`primary_label`), preenchendo `label`
com esse rótulo. Quando o primário não é encontrado, devolve o primeiro
rótulo presente.

Difere de `br_currency` (Oasis, preço único com `R$`) tanto no parsing
(aceita inteiro e decimal BR) quanto na semântica de `label`.
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


class LabeledDualPrice(PriceStrategy):
    """Detecta preços rotulados (Atacado/Varejo). Retorna o primário."""

    DEFAULT_LABELS: ClassVar[tuple[str, ...]] = ("Atacado", "Varejo")
    DEFAULT_PRIMARY: ClassVar[str] = "Atacado"
    # Valor inteiro ou decimal BR, com ou sem R$. Ex.: "299", "1.299,00".
    DEFAULT_VALUE_PATTERN: ClassVar[str] = r"R?\$?\s*([\d.]+(?:,\d{2})?)"

    def extract(
        self,
        zctx: ZoneContext,
        params: dict[str, Any],
    ) -> PriceMatch | None:
        labels: tuple[str, ...] = tuple(params.get("labels", self.DEFAULT_LABELS))
        primary: str = params.get("primary_label", self.DEFAULT_PRIMARY)
        value_pat: str = params.get("value_pattern", self.DEFAULT_VALUE_PATTERN)

        found: dict[str, Decimal] = {}
        for label in labels:
            # Ex.: "Atacado - 299" / "Atacado: R$ 299,00".
            pat = re.compile(
                rf"{re.escape(label)}\s*[-:]?\s*{value_pat}",
                re.IGNORECASE,
            )
            m = pat.search(zctx.zone_text)
            if m is None:
                continue
            parsed = self._parse_value(m.group(1))
            if parsed is not None:
                found[label] = parsed

        if not found:
            return None

        # Primário se presente; senão, o primeiro rótulo encontrado.
        if primary in found:
            return PriceMatch(value=found[primary], label=primary)
        first_label = next(iter(found))
        return PriceMatch(value=found[first_label], label=first_label)

    @staticmethod
    def _parse_value(raw: str) -> Decimal | None:
        """Converte "1.299,00" ou "299" para Decimal. None em falha.

        Remove pontos de milhar e troca vírgula decimal por ponto. "299"
        (sem vírgula nem ponto) permanece "299" → Decimal("299").
        """
        cleaned = raw.replace(".", "").replace(",", ".")
        try:
            return Decimal(cleaned)
        except (ArithmeticError, ValueError):
            return None


register_price_strategy("labeled_dual", LabeledDualPrice)
