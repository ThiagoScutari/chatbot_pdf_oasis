"""Registry de estratégias de swatches (ADR-010, Sprint 08 Fase A)."""

from __future__ import annotations

from typing import Final

from catalogflow.modules.catalog.strategies.base import SwatchesStrategy

SWATCHES_STRATEGIES: Final[dict[str, type[SwatchesStrategy]]] = {}


def register_swatches_strategy(name: str, cls: type[SwatchesStrategy]) -> None:
    """Registra uma classe de estratégia de swatches sob `name`.

    Levanta `ValueError` em registro duplicado (detecção precoce).
    """
    if name in SWATCHES_STRATEGIES:
        raise ValueError(
            f"Swatches strategy already registered: {name!r}. "
            f"Registered: {sorted(SWATCHES_STRATEGIES)}",
        )
    SWATCHES_STRATEGIES[name] = cls


def get_swatches_strategy(name: str) -> type[SwatchesStrategy]:
    """Recupera a classe de estratégia de swatches registrada sob `name`."""
    cls = SWATCHES_STRATEGIES.get(name)
    if cls is None:
        raise KeyError(
            f"Swatches strategy not found: {name!r}. Available: {sorted(SWATCHES_STRATEGIES)}",
        )
    return cls


# Auto-discovery das estratégias concretas (ver nota em sku/__init__.py).
from catalogflow.modules.catalog.strategies.swatches import geometric_bottom  # noqa: E402, F401
