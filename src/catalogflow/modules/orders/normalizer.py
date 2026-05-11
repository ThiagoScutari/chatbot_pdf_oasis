"""Engine puro de normalização de pedidos.

Esqueleto reservado para Sprint 02. Quando implementado:

    class OrderNormalizer:
        def normalize(self, raw_data, catalog_products) -> OrderData: ...

Atribuições:
    - Cruzar SKUs com catalog_products (quando catalog_id fornecido)
    - Enriquecer com nome, preço, swatch hex
    - Calcular totais (peças, valor)
    - Retornar OrderData canônico (spec.md §7)
"""

from __future__ import annotations


class OrderNormalizer:
    """Normaliza RawOrderData em OrderData canônico. Implementação na Sprint 02."""

    def normalize(self, raw_data: object, catalog_products: object) -> object:
        _ = raw_data, catalog_products
        raise NotImplementedError("OrderNormalizer.normalize entra na Sprint 02")
