"""Registry de estratégias de preço (ADR-010, Sprint 08 Fase A)."""

from __future__ import annotations

from typing import Final

from catalogflow.modules.catalog.strategies.base import PriceStrategy

PRICE_STRATEGIES: Final[dict[str, type[PriceStrategy]]] = {}


def register_price_strategy(name: str, cls: type[PriceStrategy]) -> None:
    """Registra uma classe de estratégia de preço sob `name`.

    Levanta `ValueError` em registro duplicado (detecção precoce).
    """
    if name in PRICE_STRATEGIES:
        raise ValueError(
            f"Price strategy already registered: {name!r}. Registered: {sorted(PRICE_STRATEGIES)}",
        )
    PRICE_STRATEGIES[name] = cls


def get_price_strategy(name: str) -> type[PriceStrategy]:
    """Recupera a classe de estratégia de preço registrada sob `name`."""
    cls = PRICE_STRATEGIES.get(name)
    if cls is None:
        raise KeyError(
            f"Price strategy not found: {name!r}. Available: {sorted(PRICE_STRATEGIES)}",
        )
    return cls
