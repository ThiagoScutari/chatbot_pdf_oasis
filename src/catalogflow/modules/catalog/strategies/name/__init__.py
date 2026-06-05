"""Registry de estratégias de nome do produto (ADR-010, Sprint 08 Fase A)."""

from __future__ import annotations

from typing import Final

from catalogflow.modules.catalog.strategies.base import NameStrategy

NAME_STRATEGIES: Final[dict[str, type[NameStrategy]]] = {}


def register_name_strategy(name: str, cls: type[NameStrategy]) -> None:
    """Registra uma classe de estratégia de nome sob `name`.

    Levanta `ValueError` em registro duplicado (detecção precoce).
    """
    if name in NAME_STRATEGIES:
        raise ValueError(
            f"Name strategy already registered: {name!r}. Registered: {sorted(NAME_STRATEGIES)}",
        )
    NAME_STRATEGIES[name] = cls


def get_name_strategy(name: str) -> type[NameStrategy]:
    """Recupera a classe de estratégia de nome registrada sob `name`."""
    cls = NAME_STRATEGIES.get(name)
    if cls is None:
        raise KeyError(
            f"Name strategy not found: {name!r}. Available: {sorted(NAME_STRATEGIES)}",
        )
    return cls


# Auto-discovery das estratégias concretas (ver nota em sku/__init__.py).
from catalogflow.modules.catalog.strategies.name import (  # noqa: E402, F401
    category_vocabulary,
    positional_title,
)
