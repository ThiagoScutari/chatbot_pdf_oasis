"""Estratégia de grade alfabética em faixa (PP-GG..P-M).

Porta do `GRADE_RE` + `SIZE_MAP` históricos do `PDFAnalyzer` (ADR-010,
Sprint 08 Fase B). Mesmas faixas suportadas, mesmas expansões de
tamanhos.

Parâmetro `tolerate_spaces` (Fase D): quando `True`, o regex aceita
espaços ao redor do hífen (`P - GG`, padrão FERLA) e o label detectado é
normalizado para a forma sem espaços (`P-GG`) antes do lookup no
`SIZE_MAP`. Default `False` preserva o comportamento Oasis bit-a-bit.
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
        tolerate_spaces = bool(params.get("tolerate_spaces", False))

        if tolerate_spaces:
            # "P-GG" → "P\s*-\s*GG" para casar "P - GG" (padrão FERLA).
            alts = "|".join(re.escape(p).replace(r"\-", r"\s*-\s*") for p in patterns)
        else:
            alts = "|".join(re.escape(p) for p in patterns)
        regex = re.compile(r"\b(" + alts + r")\b")

        match = regex.search(zctx.zone_text)
        if match is None:
            return None
        # Normaliza o label removendo espaços ao redor do hífen para o
        # lookup no SIZE_MAP e consistência com o Oasis (`P-GG`, não
        # `P - GG`). Com `tolerate_spaces=False` o `grade_raw` já vem sem
        # espaços, então a normalização é no-op — comportamento idêntico
        # ao anterior (golden diff-zero).
        grade_raw = match.group(1)
        grade = re.sub(r"\s*-\s*", "-", grade_raw)
        sizes = self.DEFAULT_SIZE_MAP.get(grade)
        if sizes is None:
            return None
        return GradeMatch(grade=grade, sizes=sizes)


register_grade_strategy("alpha_range", AlphaRangeGrade)
