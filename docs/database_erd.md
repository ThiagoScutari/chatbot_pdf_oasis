<!--
  Database ERD — CatalogFlow
  Gerado em: 2026-05-16
  Versão do spec: 0.1.0-draft (spec.md §7)

  Escopo: as 8 tabelas core do domínio definidas em spec.md §7.
  Tabelas adicionais introduzidas em sprints posteriores (web_users,
  magic_links, login_attempts, stock_checks, erp_submissions) NÃO
  estão representadas aqui por estarem fora do §7 do spec.

  Para renderizar: cole o bloco `erDiagram` abaixo em https://mermaid.live
  ou abra este arquivo no GitHub (renderização nativa de Mermaid).
-->

# CatalogFlow — Database ERD

```mermaid
erDiagram
    brands ||--o{ api_keys : "issues"
    brands ||--o{ catalogs : "owns"
    brands ||--o{ orders : "owns"
    brands ||--o{ romaneios : "owns"
    brands ||--o{ jobs : "schedules"
    catalogs ||--o{ catalog_products : "contains"
    catalogs ||--o{ orders : "originates"
    orders ||--o{ order_items : "has"
    orders ||--|| romaneios : "generates"

    brands {
        UUID id PK
        VARCHAR slug UNIQUE "NOT NULL, len 64"
        VARCHAR name "NOT NULL, len 255"
        VARCHAR plan "NOT NULL, len 32, default 'starter'"
        TIMESTAMPTZ created_at "NOT NULL, default NOW()"
        TIMESTAMPTZ updated_at "NOT NULL, default NOW()"
    }

    api_keys {
        UUID id PK
        UUID brand_id FK "NOT NULL, ON DELETE CASCADE"
        VARCHAR name "NOT NULL, len 128"
        VARCHAR key_hash UNIQUE "NOT NULL, SHA-256 hex (64)"
        VARCHAR key_prefix "NOT NULL, len 8"
        TIMESTAMPTZ last_used "nullable"
        TIMESTAMPTZ expires_at "nullable"
        TIMESTAMPTZ created_at "NOT NULL, default NOW()"
    }

    catalogs {
        UUID id PK
        UUID brand_id FK "NOT NULL, ON DELETE CASCADE"
        VARCHAR name "NOT NULL, len 255"
        VARCHAR collection "nullable, len 128"
        VARCHAR status "NOT NULL, default 'pending'"
        VARCHAR source_key "nullable, len 512"
        VARCHAR output_key "nullable, len 512"
        INTEGER n_pages "nullable"
        INTEGER n_product_pages "nullable"
        INTEGER n_skus "nullable"
        INTEGER n_fields "nullable"
        TEXT error_message "nullable"
        JSONB metadata "NOT NULL, default '{}'"
        TIMESTAMPTZ created_at "NOT NULL, default NOW()"
        TIMESTAMPTZ updated_at "NOT NULL, default NOW()"
    }

    catalog_products {
        UUID id PK
        UUID catalog_id FK "NOT NULL, ON DELETE CASCADE"
        VARCHAR sku "NOT NULL, len 64"
        VARCHAR name "nullable, len 255"
        NUMERIC price "nullable, precision 10,2"
        VARCHAR grade "nullable, len 16"
        JSONB sizes "NOT NULL, default '[]'"
        INTEGER n_colors "NOT NULL, default 1"
        JSONB swatches "NOT NULL, default '[]'"
        INTEGER page_index "NOT NULL"
    }

    orders {
        UUID id PK
        UUID brand_id FK "NOT NULL"
        UUID catalog_id FK "nullable"
        VARCHAR lojista_token "nullable, len 64"
        VARCHAR lojista_name "nullable, len 255"
        VARCHAR status "NOT NULL, default 'draft'"
        VARCHAR source_pdf_key "nullable, len 512"
        INTEGER total_pecas "nullable"
        NUMERIC valor_total "nullable, precision 12,2"
        TIMESTAMPTZ extracted_at "nullable"
        TIMESTAMPTZ confirmed_at "nullable"
        TIMESTAMPTZ created_at "NOT NULL, default NOW()"
        TIMESTAMPTZ updated_at "NOT NULL, default NOW()"
    }

    order_items {
        UUID id PK
        UUID order_id FK "NOT NULL, ON DELETE CASCADE"
        VARCHAR sku "NOT NULL, len 64"
        VARCHAR product_name "nullable, len 255"
        INTEGER color_index "NOT NULL, default 1"
        VARCHAR color_hex "nullable, len 7"
        VARCHAR size "NOT NULL, len 8"
        INTEGER quantity "NOT NULL, CHECK > 0"
        NUMERIC unit_price "nullable, precision 10,2"
        VARCHAR stock_status "nullable, len 32"
        INTEGER available_qty "nullable"
    }

    romaneios {
        UUID id PK
        UUID order_id FK "NOT NULL, UNIQUE"
        UUID brand_id FK "NOT NULL"
        VARCHAR output_key "nullable, len 512"
        TIMESTAMPTZ generated_at "NOT NULL, default NOW()"
    }

    jobs {
        UUID id PK
        UUID brand_id FK "NOT NULL"
        VARCHAR celery_id UNIQUE "nullable, len 255"
        VARCHAR job_type "NOT NULL, len 64"
        UUID entity_id "nullable, polymorphic ref"
        VARCHAR status "NOT NULL, default 'pending'"
        INTEGER progress "NOT NULL, default 0, CHECK 0..100"
        JSONB result "nullable"
        TEXT error "nullable"
        INTEGER retry_count "NOT NULL, default 0"
        TIMESTAMPTZ created_at "NOT NULL, default NOW()"
        TIMESTAMPTZ updated_at "NOT NULL, default NOW()"
    }
```

## Notas

- **`jobs.entity_id`** é uma referência polimórfica (não há FK física): aponta para `catalogs.id`, `orders.id` ou `romaneios.id` dependendo de `job_type` (`catalog.process`, `order.extract`, `romaneio.generate`).
- **`catalog_products`** tem `UNIQUE(catalog_id, sku, page_index)` — o mesmo SKU pode aparecer em páginas distintas (variações de cor).
- **`order_items`** tem `UNIQUE(order_id, sku, color_index, size)` — granularidade canônica de uma linha de pedido.
- **`romaneios`** é 1:1 com `orders` via `UNIQUE(order_id)`.
- Todos os relacionamentos com `brands` implementam o isolamento multi-tenant exigido pelo spec §12.3.
