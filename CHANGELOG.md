# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) e
versionamento [SemVer](https://semver.org/spec/v2.0.0.html).

---

## [0.2.0] — Sprint 02: Order extraction + Romaneio

Fecha o ciclo de pedido ponta a ponta. Quando uma lojista preenche o PDF
editável gerado na Sprint 01 e devolve, o sistema extrai os campos, estrutura
o pedido (com enriquecimento opcional via `catalog_id`) e gera o romaneio
PDF profissional. Toda a parte de extração/geração é função pura (bytes-in,
bytes-out) — I/O fica confinado nos services.

### Added — Schema & migrations (Fase A)

- Migration `0003_orders_schema.py` (reversível) criando `orders`,
  `order_items` (UNIQUE `(order_id, sku, color_index, size)` + CHECK
  `quantity > 0`), `romaneios` (UNIQUE `order_id` para 1:1) e a coluna
  `brands.logo_key` (S3 key da logo da marca, opcional).
- `auth/models.Brand.logo_key: Mapped[str | None]`.
- Índices: `idx_orders_brand_id`, `idx_orders_catalog_id`,
  `idx_order_items_order_id`, `idx_romaneios_brand_id`.

### Added — Fixtures de pedido (Fase B)

- `tests/fixtures/generate_order_fixtures.py` reusa `PDFAnalyzer` +
  `FieldInjector` da Sprint 01 e produz 6 fixtures determinísticas:
  `pedido_preenchido_v2.pdf`, `pedido_preenchido_v1.pdf` (legado),
  `pedido_campos_vazios.pdf`, `pedido_valores_invalidos.pdf`,
  `pedido_flattened.pdf` (sem `/AcroForm`), `pedido_mixed_v1_v2.pdf`.
- Validado que `widget.field_name = ...` + `widget.update()` no PyMuPDF
  persiste rename para gerar v1 a partir de v2.

### Added — Extractor + Normalizer (Fase C)

- `orders/extractor.py` — `OrderExtractor` puro (`bytes → RawOrderData`):
  regex v2 (`qty__SKU__corN__TAM`) tentado antes do v1
  (`qty__SKU__TAM`, color_index=1); valores não-numéricos/float/negativos/
  zero descartados silenciosamente; PDF sem `/AcroForm` levanta
  `PDFFlattenedError`. Helpers `_parse_quantity`, `_parse_field_name`,
  `_consolidate_source_format` testáveis isoladamente.
- `orders/normalizer.py` — `OrderNormalizer` puro: agrega duplicatas em
  `(sku, color_index, size)`, enriquece via `CatalogProduct` (nome, preço,
  hex do swatch), warnings para SKU órfão, totais (peças, valor, n_skus),
  ordenação por `page_index` quando catálogo disponível.
- `orders/{models,schemas}.py` — Order/OrderItem SQLAlchemy 2.0 com
  selectinload-friendly relationship; schemas Pydantic v2
  (`OrderResponse`, `OrderTotals`, `ExtractOrderResponse`,
  `RomaneioStatusResponse`).

### Added — RomaneioBuilder (Fase D)

- `romaneio/models.py` — `Romaneio` 1:1 com `Order`. `Order.romaneio`
  back_populates via string forward reference (padrão SQLAlchemy 2.0).
- `romaneio/builder.py` — `RomaneioBuilder` puro (`OrderData + Config →
  bytes`): cabeçalho com logo opcional (`page.insert_image(stream=)`),
  faixa brand, lojista, data em pt-BR; bloco por SKU com grid cor x
  tamanho; paginação automática com cabeçalho repetido; totalizador
  final. Formato monetário pt-BR via string mangling (sem
  `locale.setlocale`); `format_currency` e `format_date_pt_br` exportados.

### Added — Services + tasks (Fase E)

- `orders/service.OrderService`: `create_order` valida MIME/tamanho/
  catalog cross-tenant, `get_order` (selectinload),
  `process_order` (download → extract → normalize → persist).
- `orders/tasks.extract_order_task`: `PDFFlattenedError` é tratado como
  **permanente** e NÃO dispara `self.retry()` (Armadilha #3 do PRD);
  erros transitórios sobem com backoff exponencial `60s × 2^n`.
- `romaneio/service.RomaneioService`: `generate_romaneio` reaproveita
  Romaneio existente (UNIQUE `order_id`); `process_romaneio` baixa logo
  do storage se `brand.logo_key`, constrói PDF e faz upload com chave
  `{brand}/orders/{order}/romaneio.pdf`; `get_download_url` retorna
  presigned URL.
- `romaneio/tasks.generate_romaneio_task` com retry exponencial para
  todos os erros (geração sem classe "permanente" — falhas de
  storage/builder são por natureza transientes).

### Added — Routers + health (Fase F)

- `orders/router.py` montado em `main.py`:
  - `POST /api/v1/orders/extract` (202) — multipart upload, valida e
    enfileira `order.extract`.
  - `GET /api/v1/orders/{id}` (200) — order completo com items + totals.
  - `GET /api/v1/orders/{id}/romaneio` — 302 redirect para presigned URL
    quando pronto; 202 com `job_id` em andamento ou enfileira nova
    geração.
- `GET /api/v1/health` estendido com contagens `jobs.{catalog_pending,
  order_pending, romaneio_pending}` — útil para dashboards e alertas.
- `shared/jobs_router.py` já era genérico — reconhece automaticamente
  `order.extract` e `romaneio.generate`.

### Added — Tests (Sprint 02)

- 34 testes em `orders/tests/test_extractor.py` cobrindo todas as 6
  fixtures + edge cases + funções puras + pureza.
- 18 testes em `orders/tests/test_normalizer.py` (sem/com catálogo,
  warnings, agregação, totais, ordenação, source_format propagation).
- 24 testes em `romaneio/tests/test_builder.py` (PDF válido, conteúdo
  textual, logo presente/ausente/corrompida, paginação, sem preço, sem
  itens, helpers de formato).
- 11 testes em `orders/tests/test_service.py` (criação, validação,
  isolamento, processo, PDF flatten, race condition).
- 15 testes em `romaneio/tests/test_service.py` (generate, process com/
  sem logo, get_download_url, isolamento, bookkeeping).
- 13 testes em `orders/tests/test_router.py` (HTTP integration via
  httpx — auth, MIME, size, isolamento, romaneio endpoint redirects).
- 10 testes em `*/tests/test_tasks.py` cobrindo o wrapper Celery de
  catalog/orders/romaneio (resolve dívida da Armadilha #5 do PRD).
- `tests/integration/test_order_pipeline.py` cobre o pipeline ponta a
  ponta: catalog → fill widgets → extract → romaneio → download.
- `_TABLES_TO_TRUNCATE` em `conftest.py` agora inclui `romaneios`,
  `order_items`, `orders` (ordem dependência-respeitada).

### Fixed / Infra

- `alembic.ini` ganhou `path_separator = os` — silencia
  `DeprecationWarning` introduzido em Alembic 1.14+ que era promovido
  a erro pelo `filterwarnings = ["error", ...]` do pyproject.
- Singleton de `infra.storage._storage` continua respeitando
  `dispose_engine` em testes — testcontainer não vaza entre runs.

### Decisões registradas

- **`PDFFlattenedError` permanente** — Erro de dados: novo retry não
  recupera. Estado de erro gravado no `Order` e no `Job` antes da
  exceção subir, garantindo observabilidade mesmo sem retry.
- **Builder com `(order_data, config)`** — Mantém a assinatura do PRD;
  `RomaneioConfig` mescla branding (logo, brand_name) e contexto do
  pedido (lojista_name, emitted_at), simplificando a chamada do service.
- **Catálogo opcional** — `process_order` sem `catalog_id` produz items
  sem enriquecimento (`product_name` / `unit_price` = `None`), conforme
  PRD. Romaneio funciona com totais sem valor monetário.
- **Logo opcional + fail-soft** — Download da logo do storage com
  `try/except`: logo corrompida ou ausente cai pro cabeçalho textual,
  nunca derruba a geração do romaneio.

### Next (Sprint 03 — preview)

- Webhook de notificação (`catalog.ready`, `order.extracted`,
  `romaneio.ready`).
- Módulo `stock` com `StockAdapter` (Fase 2 do roadmap).
- Web UI mínima (upload + status + download).
- Módulo `User` com login/senha.

---

## [0.1.0] — 2026-05-11 — Sprint 01: Foundation

Primeira sprint do CatalogFlow. Entrega a fundação completa do projeto e o
pipeline ponta-a-ponta de processamento de catálogos PDF, do upload
autenticado à entrega do PDF com campos AcroForm injetados.

### Added — Infra & estrutura (Fase A)

- Estrutura modular `src/catalogflow/{modules,shared,infra,scripts}` +
  `tests/{integration,e2e,fixtures}` com todos os `__init__.py`.
- `pyproject.toml` com dependências completas (FastAPI, SQLAlchemy 2.0,
  Celery, PyMuPDF, pdfplumber, aioboto3, etc.) e configuração de
  `ruff`/`mypy --strict`/`pytest`/`coverage` (threshold 80%).
- `.env.example` documentando todas as variáveis suportadas.
- `docker/Dockerfile` multi-stage (builder + production), rodando como
  usuário não-root `catalogflow`.
- `docker/docker-compose.yml` levantando `api`, `worker`, `beat`, `flower`,
  `postgres`, `redis` e `minio` (substituto de R2/S3 em dev).
- `infra/settings.py` (Pydantic BaseSettings com `SecretStr`),
  `infra/database.py` (SQLAlchemy async + `get_db()`),
  `infra/cache.py` (Redis async pool),
  `infra/storage.py` (`StorageClient` upload/download/presigned/delete).
- `.gitignore` com regras para `.env`, `example/*.pdf`, caches e venv.

### Added — Auth & multi-tenancy (Fase B)

- Alembic configurado em modo async (`migrations/env.py`), com
  `0001_auth_tables.py` reversível criando `brands` + `api_keys` (hash
  SHA-256, prefixo `cf_`).
- `auth/{models,schemas,service,router,dependencies}.py`.
- `get_current_brand()` (dependency) com `BackgroundTasks` para
  `last_used`; `require_internal_secret()` com comparação constant-time.
- Rotas administrativas `POST /internal/brands` e
  `POST /internal/brands/{id}/api-keys` (gated por `X-Internal-Secret`).
- Testes (≥ 26 casos): criação, slug duplicado, key inválida/expirada,
  rotação invalida o token antigo, gate interno 401 sem/errado/correto.
- Script de seed `python -m catalogflow.scripts.seed_dev` cria a brand
  `oasis` + uma API key (raw retornado uma única vez).

### Added — App principal (Fase C)

- `main.py` com `create_app()` factory + lazy `app` via PEP 562 — testes
  importam o módulo sem disparar `get_settings()`.
- Lifespan: testa Postgres e Redis no startup, dispõe pools no shutdown.
- `shared/responses.py`: envelope padrão `StandardResponse[T]` com
  `request_id` e `timestamp` em `meta`.
- `shared/middleware.py`: `RequestIdMiddleware` lê/gera UUID4 no header
  `X-Request-ID` e ecoa na resposta.
- 3 exception handlers: `DomainError` → 4xx via envelope;
  `RequestValidationError` → 422; `Exception` (catch-all) → 500
  estéril (não vaza traceback).
- `GET /api/v1/health` retorna 200 quando ok, **503** se alguma
  dependência respondeu erro (probe-friendly).
- 11 integration tests (handlers, request_id, CORS preflight, envelope,
  health, traceback isolation).

### Added — Catalog: pipeline completo (Fase D)

- Migration `0002_catalog_tables.py` cria `catalogs`, `catalog_products`
  (UNIQUE `(catalog_id,sku,page_index)`), `jobs` (CHECK status + progress).
- `catalog/models.py` com Catalog/CatalogProduct/Job em SQLAlchemy 2.0.
- `catalog/schemas.py` com DTOs (`CatalogResponse`,
  `ProcessCatalogResponse`, `JobResponse`, `CatalogProductResponse`).
- `catalog/pdf_analyzer.py` — engine **puro** (`bytes → CatalogMetadata`),
  migrado de `oasis_form_v2.py`: regex SKU/grade fiéis, threshold de
  swatch 0.92, lógica `single`/`left`/`right`, dataclasses `frozen+slots`.
- `catalog/field_injector.py` — engine **puro** (`bytes + metadata → bytes`),
  todas as constantes idênticas ao POC. Compressão à esquerda quando há
  vizinho direito; helpers públicos `field_name_for()` e `count_fields()`.
- `infra/celery_app.py` com routes por módulo, JSON-only,
  `acks_late + prefetch_multiplier=1` para reliability.
- `catalog/tasks.py` com `process_catalog_task` (bind=True, max_retries=3,
  backoff exponencial). Erros permanentes vs. transitórios distintos.
- `catalog/service.py` com isolamento multi-tenant em todo SELECT,
  validação de assinatura `%PDF`, validação de tamanho contra
  `max_pdf_size_bytes`, e `_claim_job` race-safe via
  `UPDATE WHERE status='pending' RETURNING id`.
- `catalog/router.py` (3 endpoints) + `shared/jobs_router.py`
  (`GET /api/v1/jobs/{id}` filtrado por brand).
- 6 fixtures sintéticas geradas via `tests/fixtures/generate_fixtures.py`
  (1 produto/1 cor, 1 produto/2 cores, 2 produtos/página, grade PP-G,
  sem produtos, criptografado).
- ≥ 70 testes do módulo (analyzer, injector, service com FakeStorage,
  router HTTP).

### Added — Esqueletos para Sprints futuras (Fase E)

- `orders/{models,schemas,service,router,tasks,extractor,normalizer}.py`
  como esqueleto com `NotImplementedError("Sprint 02")`. Router não está
  registrado no `create_app`.
- `romaneio/{service,router,tasks,builder}.py` mesmo padrão.

### Added — CI & finalização (Fase F)

- `.github/workflows/ci.yml` com 4 jobs: `quality` (ruff + mypy),
  `test` (pytest + coverage 80%), `build` (docker multi-stage + smoke),
  `security` (pip-audit + bandit). Concurrency cancela runs anteriores.
- `tests/integration/test_catalog_pipeline.py` exercita o pipeline real
  com Postgres + storage in-memory + engines reais (sem Celery).
- `tests/e2e/test_api_flows.py` cobre o flow HTTP completo via httpx
  (health → upload → poll → simular worker → poll → download).
- `tests/fakes.py` centraliza `FakeStorage` (compartilhado entre
  conftests).
- `README.md` com setup em 5 minutos, smoke test do upload, descrição
  da stack local e troubleshooting.

### Decisões arquiteturais relevantes

- **Funções de PDF puras** (bytes-in, bytes-out) — testáveis sem I/O,
  preparadas para extração para microserviço.
- **PostgreSQL + Redis sempre** (ADR-003) — sem SQLite mesmo em testes;
  `testcontainers` provê Postgres efêmero.
- **PyMuPDF AGPL** (ADR-004) — licença comercial Artifex obrigatória
  antes do go-live em produção.
- **S3-compatible storage** (ADR-005) — banco grava só metadados +
  chave; bytes vivem no R2.
- **API key SHA-256 com prefixo `cf_`** — plaintext exposto uma única
  vez; comparação por hash é O(1) por índice UNIQUE.
- **`UPDATE WHERE status='pending'` race-safe** — impede dois workers
  pegarem o mesmo job mesmo sem locks distribuídos.
- **Envelope JSON único** — `success`/`data`/`error`/`meta` em toda
  resposta; `meta.request_id` propaga via `X-Request-ID`.

### Next (Sprint 02 — preview)

- Implementação de `orders/extractor` + `normalizer` (parse de campos
  AcroForm preenchidos, suporte a v1 e v2 do formato).
- `romaneio/builder` gerando o PDF profissional (header, grids, totais,
  paginação).
- Webhook de notificação (`catalog.ready`, `order.extracted`).
- Detecção de PDF achatado (`PDF_FLATTENED`) e fallback documentado.
