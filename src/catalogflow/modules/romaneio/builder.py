"""Engine puro de construção do PDF de romaneio.

Esqueleto reservado para Sprint 02. Quando implementado:

    class RomaneioBuilder:
        def build(self, order_data: object, brand: object) -> bytes: ...

Layout (spec.md §6 — `romaneio`):
    - Cabeçalho: logo da marca + "ROMANEIO DE PEDIDO" + lojista + data
    - Por produto: nome, ref, preço, grid cor×tamanho, subtotal
    - Rodapé: total de peças, valor total, n_skus
    - Paginação automática com cabeçalho repetido
"""

from __future__ import annotations


class RomaneioBuilder:
    """Constrói o PDF do romaneio. Implementação na Sprint 02."""

    def build(self, order_data: object, brand: object) -> bytes:
        _ = order_data, brand
        raise NotImplementedError("RomaneioBuilder.build entra na Sprint 02")
