"""Estratégia de nome por posição/tipografia (default dos profiles novos).

Zero-vocabulário (ADR-010 D3): em vez de casar categorias conhecidas
(`category_vocabulary`), seleciona o texto de maior peso tipográfico
(`size`) dentro da zona Voronoi do SKU. Palavras de tamanho próximo ao
máximo (dentro de `size_tolerance`) são agrupadas e reordenadas na
ordem de leitura (top, depois x0) para reconstruir o título.

Heurística "maior fonte = título", REFINADA (hotfix FERLA): no catálogo
FERLA real o **preço** é impresso em peso maior (13.0 Bold) que o **nome**
(12.0 Regular), então "maior fonte" sozinho devolvia a linha de preço
como nome. A seleção agora **exclui linhas que casam padrões de outros
eixos** (preço, SKU, grade, rótulos) ANTES de medir a tipografia. O nome
é o maior texto *que não é preço/SKU/grade/rótulo*.

A exclusão é feita por LINHA (palavras agrupadas por `top`), não por
palavra: a linha "Atacado - 299" é descartada inteira — caso contrário o
hífen ("-"), que não casa nenhum padrão de ruído, sobreviveria solto e
poderia virar o "nome" de maior fonte.

Funciona para qualquer marca cujos catálogos tenham hierarquia
tipográfica clara — o nome do produto é, por construção visual, o texto
de maior destaque na zona *entre as linhas que não são dados estruturados*.

Requer que cada dict em `zone_words` carregue a chave `size` (garantido
pela extração com `extra_attrs=["size", "fontname"]` no orquestrador).
Quando a tipografia está ausente (palavras sem `size`), devolve `None` —
o orquestrador trata como degradação não-bloqueante (ADR-011).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, ClassVar

from catalogflow.modules.catalog.strategies.base import (
    NameMatch,
    NameStrategy,
    ZoneContext,
)
from catalogflow.modules.catalog.strategies.name import register_name_strategy


class PositionalTitleName(NameStrategy):
    """Extrai o nome como o maior texto que não é preço/SKU/grade/rótulo."""

    # Padrões de "não-nome": linhas que casam estes padrões são dados
    # estruturados de outros eixos (preço/SKU/grade), nunca o nome do
    # produto, e são excluídas antes de medir a tipografia.
    #
    # Espelham deliberadamente os padrões de `regex_prefixed` (SKU),
    # `labeled_dual` (preço) e `alpha_range` (grade). Mantidos LOCAIS (em
    # vez de importados dessas estratégias) para evitar acoplamento e o
    # efeito colateral de re-registro no import dos módulos de estratégia.
    _NOISE_PATTERNS: ClassVar[tuple[str, ...]] = (
        r"(?:Ref|Cód|Cod|SKU)[:\s]",  # rótulo de SKU
        r"\d{6,13}",  # sequência longa de dígitos (SKU)
        r"(?:Atacado|Varejo|Preço|Preco)",  # rótulos de preço
        r"R?\$?\s*\d+[.,]?\d*",  # valores monetários / numéricos
        r"(?:Grade|Tam|Tamanho)[:\s]",  # rótulo de grade
        r"\b[PMG]{1,2}\s*-\s*[PMG]{1,2}\b",  # faixa de grade (P - GG)
    )

    _noise_re: ClassVar[re.Pattern[str]] = re.compile(
        "|".join(_NOISE_PATTERNS),
        re.IGNORECASE,
    )

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

        # Agrupa palavras em linhas (mesmo `top` arredondado) e descarta as
        # linhas que casam ruído (preço/SKU/grade). A exclusão é por linha
        # inteira para não deixar tokens órfãos (ex.: o "-" de "Atacado - 299").
        lines: dict[float, list[dict[str, Any]]] = defaultdict(list)
        for w in typed:
            lines[round(float(w["top"]), 0)].append(w)

        survivors: list[dict[str, Any]] = []
        for line_words in lines.values():
            line_words.sort(key=lambda w: float(w["x0"]))
            line_text = " ".join(str(w["text"]) for w in line_words).strip()
            if not line_text or self._noise_re.search(line_text):
                continue  # linha é preço/SKU/grade/rótulo → não compete p/ nome
            survivors.extend(line_words)

        if not survivors:
            return None

        # Entre as palavras sobreviventes, o nome é o texto de maior peso
        # tipográfico (comportamento original preservado para o caso comum
        # em que o nome JÁ é a maior fonte). Palavras dentro de
        # `size_tolerance` do máximo são agrupadas como a mesma linha de
        # título e reordenadas na ordem de leitura (top, depois x0).
        max_size = max(float(w["size"]) for w in survivors)
        tol = float(params.get("size_tolerance", 0.5))
        title_words = [w for w in survivors if float(w["size"]) >= max_size - tol]

        title_words.sort(key=lambda w: (round(float(w["top"]), 1), float(w["x0"])))
        name = " ".join(str(w["text"]) for w in title_words).strip()

        if not name:
            return None
        # Filtro defensivo: o SKU não deve virar "nome" se escapar do ruído.
        if name == zctx.sku:
            return None
        return NameMatch(name=name)


register_name_strategy("positional_title", PositionalTitleName)
