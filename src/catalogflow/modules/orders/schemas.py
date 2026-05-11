"""Schemas Pydantic do módulo `orders` — esqueleto reservado para Sprint 02.

Quando implementado, conterá:
    - OrderExtractRequest (file via UploadFile, catalog_id?, lojista_name?)
    - OrderItemResponse (sku, color_index, color_hex, size, quantity, unit_price)
    - OrderResponse (OrderData canônico — ver spec.md §7)
    - OrderTotals (total_items, total_pecas, valor_total, n_skus)
"""

from __future__ import annotations
