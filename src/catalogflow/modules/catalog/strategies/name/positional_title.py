"""Estratégia de nome por posição/tipografia (default dos profiles novos).

Zero-vocabulário (ADR-010 D3): em vez de casar categorias conhecidas
(`category_vocabulary`), seleciona o texto de maior peso tipográfico
(`size`) dentro da zona Voronoi do SKU. Palavras de tamanho próximo ao
máximo (dentro de `size_tolerance`) são agrupadas e reordenadas na
ordem de leitura (top, depois x0) para reconstruir o título.

Funciona para qualquer marca cujos catálogos tenham hierarquia
tipográfica clara — o nome do produto é, por construção visual, o texto
de maior destaque na zona.

Requer que cada dict em `zone_words` carregue a chave `size` (garantido
pela extração com `extra_attrs=["size", "fontname"]` no orquestrador).
Quando a tipografia está ausente (palavras sem `size`), devolve `None` —
o orquestrador trata como degradação não-bloqueante (ADR-011).
"""

from __future__ import annotations

from typing import Any

from catalogflow.modules.catalog.strategies.base import (
    NameMatch,
    NameStrategy,
    ZoneContext,
)
from catalogflow.modules.catalog.strategies.name import register_name_strategy


class PositionalTitleName(NameStrategy):
    """Extrai o nome como o texto de maior peso tipográfico da zona."""

    def extract(
        self,
        zctx: ZoneContext,
        params: dict[str, Any],
    ) -> NameMatch | None:
        words = zctx.zone_words
        if not words:
            return None

        # Só palavras com tipografia disponível entram na medição.
        typed = [w for w in words if w.get("size") is not None]
        if not typed:
            return None

        max_size = max(float(w["size"]) for w in typed)
        # Tolerância para agrupar palavras "da mesma linha de título".
        tol = float(params.get("size_tolerance", 0.5))
        title_words = [w for w in typed if float(w["size"]) >= max_size - tol]

        # Reconstrói o texto na ordem de leitura (top, depois x0).
        title_words.sort(key=lambda w: (round(float(w["top"]), 1), float(w["x0"])))
        name = " ".join(str(w["text"]) for w in title_words).strip()

        if not name:
            return None
        # Filtro defensivo: o SKU não deve virar "nome" se for o maior texto.
        if name == zctx.sku:
            return None
        return NameMatch(name=name)


register_name_strategy("positional_title", PositionalTitleName)
