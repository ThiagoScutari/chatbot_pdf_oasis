"""Registry de estratégias de grade de tamanhos (ADR-010, Sprint 08 Fase A)."""

from __future__ import annotations

from typing import Final

from catalogflow.modules.catalog.strategies.base import GradeStrategy

GRADE_STRATEGIES: Final[dict[str, type[GradeStrategy]]] = {}


def register_grade_strategy(name: str, cls: type[GradeStrategy]) -> None:
    """Registra uma classe de estratégia de grade sob `name`.

    Levanta `ValueError` em registro duplicado (detecção precoce).
    """
    if name in GRADE_STRATEGIES:
        raise ValueError(
            f"Grade strategy already registered: {name!r}. Registered: {sorted(GRADE_STRATEGIES)}",
        )
    GRADE_STRATEGIES[name] = cls


def get_grade_strategy(name: str) -> type[GradeStrategy]:
    """Recupera a classe de estratégia de grade registrada sob `name`."""
    cls = GRADE_STRATEGIES.get(name)
    if cls is None:
        raise KeyError(
            f"Grade strategy not found: {name!r}. Available: {sorted(GRADE_STRATEGIES)}",
        )
    return cls


# Auto-discovery das estratégias concretas (ver nota em sku/__init__.py).
from catalogflow.modules.catalog.strategies.grade import alpha_range  # noqa: E402, F401
