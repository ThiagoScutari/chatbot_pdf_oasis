"""Registry de estratégias de SKU (ADR-010, Sprint 08 Fase A).

A Fase A entrega apenas o mecanismo de registry — vazio. Estratégias
concretas (`regex_hyphenated`, `regex_prefixed`, etc.) chegam na Fase B
e na Fase D, conforme PRD da Sprint 08.
"""

from __future__ import annotations

from typing import Final

from catalogflow.modules.catalog.strategies.base import SkuStrategy

SKU_STRATEGIES: Final[dict[str, type[SkuStrategy]]] = {}


def register_sku_strategy(name: str, cls: type[SkuStrategy]) -> None:
    """Registra uma classe de estratégia de SKU sob `name`.

    Levanta `ValueError` quando já houver estratégia registrada com o
    mesmo nome — registro duplicado é sempre erro programático, então
    falha cedo em vez de sobrescrever silenciosamente.
    """
    if name in SKU_STRATEGIES:
        raise ValueError(
            f"SKU strategy already registered: {name!r}. Registered: {sorted(SKU_STRATEGIES)}",
        )
    SKU_STRATEGIES[name] = cls


def get_sku_strategy(name: str) -> type[SkuStrategy]:
    """Recupera a classe de estratégia de SKU registrada sob `name`.

    Levanta `KeyError` com mensagem útil (lista os IDs disponíveis)
    quando o nome não está registrado.
    """
    cls = SKU_STRATEGIES.get(name)
    if cls is None:
        raise KeyError(
            f"SKU strategy not found: {name!r}. Available: {sorted(SKU_STRATEGIES)}",
        )
    return cls


# Auto-discovery das estratégias concretas — import com efeito colateral.
# Posicionado no fim do módulo para evitar ImportError circular: cada
# estratégia importa `register_sku_strategy` deste módulo, que já está
# definido neste ponto.
from catalogflow.modules.catalog.strategies.sku import regex_hyphenated  # noqa: E402, F401
