"""Estratégia de grade alfabética em faixa (PP-GG..P-M).

Porta do `GRADE_RE` + `SIZE_MAP` históricos do `PDFAnalyzer` (ADR-010,
Sprint 08 Fase B). Mesmas faixas suportadas, mesmas expansões de
tamanhos.

Observação: o parâmetro `tolerate_spaces` (para casar `P - GG` com
espaços ao redor do hífen, padrão FERLA) **não** entra nesta fase.
Será adicionado na Fase D conforme PRD.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from catalogflow.modules.catalog.strategies.base import (
    GradeMatch,
    GradeStrategy,
    ZoneContext,
)
from catalogflow.modules.catalog.strategies.grade import register_grade_strategy


class AlphaRangeGrade(GradeStrategy):
    """Detecta grade alfabética em faixa (PP-GG, P-G, etc.).

    Aceita `patterns` no profile para customizar a lista de labels.
    Sempre filtra pela `DEFAULT_SIZE_MAP` interna — se o label casa o
    regex mas não está no mapa, retorna `None` (label sem expansão de
    tamanhos = profile mal configurado).
    """

    DEFAULT_PATTERNS: ClassVar[tuple[str, ...]] = (
        "PP-GG",
        "PP-G",
        "PP-M",
        "P-GG",
        "P-G",
        "P-M",
    )

    DEFAULT_SIZE_MAP: ClassVar[dict[str, tuple[str, ...]]] = {
        "PP-M": ("PP", "P", "M"),
        "PP-G": ("PP", "P", "M", "G"),
        "PP-GG": ("PP", "P", "M", "G", "GG"),
        "P-M": ("P", "M"),
        "P-G": ("P", "M", "G"),
        "P-GG": ("P", "M", "G", "GG"),
    }

    def extract(
        self,
        zctx: ZoneContext,
        params: dict[str, Any],
    ) -> GradeMatch | None:
        patterns: tuple[str, ...] = tuple(params.get("patterns", self.DEFAULT_PATTERNS))
        regex = re.compile(r"\b(" + "|".join(re.escape(p) for p in patterns) + r")\b")

        match = regex.search(zctx.zone_text)
        if match is None:
            return None
        grade = match.group(1)
        sizes = self.DEFAULT_SIZE_MAP.get(grade)
        if sizes is None:
            return None
        return GradeMatch(grade=grade, sizes=sizes)


register_grade_strategy("alpha_range", AlphaRangeGrade)
