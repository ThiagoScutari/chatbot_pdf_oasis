"""Modelos ORM do módulo `orders` — esqueleto reservado para Sprint 02.

Quando implementado, este arquivo conterá:
    - Order (id, brand_id FK brands, catalog_id FK catalogs, lojista_token,
      lojista_name, status, source_pdf_key, total_pecas, valor_total,
      extracted_at, confirmed_at, created_at, updated_at)
    - OrderItem (id, order_id FK orders ON DELETE CASCADE, sku, product_name,
      color_index, color_hex, size, quantity > 0, unit_price,
      stock_status [F2], available_qty [F2], UNIQUE(order_id,sku,color_index,size))

Schema SQL exato em `spec.md §7`. A migration será 0003_orders_tables.
"""

from __future__ import annotations
