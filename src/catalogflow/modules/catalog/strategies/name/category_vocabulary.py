"""Estratégia de nome via vocabulário de categorias de moda.

Porta direta do `NAME_RE` histórico do `PDFAnalyzer` (ADR-010, Sprint
08 Fase B). Cobre 10 categorias da moda feminina Oasis (JAQUETA,
CALÇA/CALCA, VESTIDO, CONJUNTO, BLUSA, BODY, SHORT, BLAZER, SAIA, TOP),
captura a categoria + sufixos textuais e devolve o resultado em
UPPERCASE — comportamento bit-a-bit igual à linha 382 do analyzer
original (`zone_names[0].upper()`).

Default da Sprint 08 passa a ser `positional_title` (Fase D). Esta
estratégia continua disponível como opt-in via `format_profile`.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from catalogflow.modules.catalog.strategies.base import (
    NameMatch,
    NameStrategy,
    ZoneContext,
)
from catalogflow.modules.catalog.strategies.name import register_name_strategy


class CategoryVocabularyName(NameStrategy):
    """Detecta nome de produto via vocabulário de categorias de moda."""

    DEFAULT_PATTERN: ClassVar[str] = (
        r"\b((?:JAQUETA|CAL[ÇC]A|VESTIDO|CONJUNTO|BLUSA|BODY|SHORT|BLAZER|SAIA|TOP)"
        r"(?:\s+[A-Za-zÀ-Ýà-ý]{2,})*)\b"
    )

    def extract(
        self,
        zctx: ZoneContext,
        params: dict[str, Any],
    ) -> NameMatch | None:
        pattern_str = params.get("pattern", self.DEFAULT_PATTERN)
        pattern = re.compile(pattern_str, re.IGNORECASE)

        match = pattern.search(zctx.zone_text)
        if match is None:
            return None
        return NameMatch(name=match.group(1).upper())


register_name_strategy("category_vocabulary", CategoryVocabularyName)
