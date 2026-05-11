# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) e
versionamento [SemVer](https://semver.org/spec/v2.0.0.html).

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
