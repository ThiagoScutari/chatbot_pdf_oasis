# Análise do Estado Atual — CatalogFlow (PDF Oasis)

**Data:** 2026-05-19
**Branch:** `main` (último commit `4e326b4` — _docs: rewrite README with visual tour..._)
**Versão declarada:** `pyproject.toml` v0.1.0 · `spec.md` v0.2.0 · `CHANGELOG.md` última entrada v0.4.0 (Sprint 04)
**Origem dos dados:** leitura direta de `spec.md`, `CLAUDE.md`, `CHANGELOG.md`, `pyproject.toml`, `migrations/versions/`, `src/catalogflow/`, `docker/docker-compose.yml`, `.github/workflows/ci.yml` e execução de `pytest --collect-only`.

> Este relatório é puramente descritivo — nenhum arquivo do código-fonte foi alterado.

---

## 1. Contagem de código

Comandos efetivamente executados:

```bash
# Arquivos .py em src/ (excluindo testes e __pycache__)
find src -name "*.py" -not -path "*/tests/*" -not -path "*/__pycache__/*" | wc -l

# LOC Python em src/ (excluindo testes)
find src -name "*.py" -not -path "*/tests/*" -not -path "*/__pycache__/*" -exec wc -l {} +

# Templates HTML
find src -name "*.html"

# Arquivos de teste e funções de teste
find src -path "*/tests/*" -name "test_*.py" | wc -l    # testes dentro de cada módulo
find tests -name "*.py" -not -path "*/__pycache__/*"     # testes integração/e2e/fixtures
python -m pytest --collect-only -q                       # contagem de funções de teste
```

| Métrica | Valor |
|---|---|
| Arquivos `.py` em `src/` (exclui `tests/` e `__pycache__`) | **70** |
| LOC Python em `src/` (exclui `tests/`) | **12 191** |
| Arquivos `.py` em `src/` (inclui testes) | 113 |
| LOC Python em `src/` (inclui testes) | 25 453 |
| Templates HTML em `src/catalogflow/templates/` | **23** |
| Arquivos de teste em `src/.../tests/` (`test_*.py`) | 29 |
| Arquivos de teste em `tests/` (integration + e2e + fixtures) | 4 produtivos (`test_app.py`, `test_catalog_pipeline.py`, `test_order_pipeline.py`, `test_api_flows.py`) + 2 geradores de fixture |
| **Funções de teste coletadas (`pytest --collect-only`)** | **701** |
| Funções `def test_*` no fonte (sem parametrize) | 643 |

Detalhe dos templates (23): `base.html`, `login.html`, `register.html`, `forgot.html`, `dashboard.html`, `admin/users.html`, `errors/{404,500,magic_link}.html`, `catalogs/{list,upload,detail,_badge,_actions_strip,_upload_progress}.html`, `orders/{list,upload,detail,_badge,_stock_action,_submit_action,_romaneio_action,_upload_progress}.html`.

---

## 2. Cobertura de testes

Comando executado (em 2026-05-19, contra Postgres efêmero via `testcontainers`; `tests/integration` e `tests/e2e` excluídos para acelerar — exigem fixtures adicionais):

```bash
python -m pytest src/catalogflow \
  --cov=src/catalogflow --cov-report=term \
  --no-header -q --tb=no -p no:cacheprovider
```

Resultado:

```
TOTAL  4129  397  676  66  89%
Required test coverage of 80.0% reached. Total coverage: 89.20%
682 passed in 361.37s (0:06:01)
```

| Item | Valor |
|---|---|
| Threshold mínimo configurado (`pyproject.toml`) | 80% |
| **Cobertura agregada (`src/catalogflow`)** | **89.20%** |
| Statements totais | 4 129 |
| Statements não cobertos | 397 |
| Branches totais | 676 |
| Branches não cobertos | 66 |
| Testes executados nesta run | 682 passed |
| Tempo total | 361 s (≈ 6 min) |

### Cobertura por arquivo (extrato do relatório)

| Arquivo | Cobertura |
|---|---:|
| `modules/auth/service.py` | 94% |
| `modules/auth/router.py` | 88% |
| `modules/catalog/pdf_analyzer.py` | 93% |
| `modules/catalog/field_injector.py` | 98% |
| `modules/catalog/service.py` | 93% |
| `modules/catalog/router.py` | 61% |
| `modules/catalog/tasks.py` | 69% |
| `modules/orders/extractor.py` | 96% |
| `modules/orders/normalizer.py` | 94% |
| `modules/orders/service.py` | 92% |
| `modules/orders/router.py` | 57% |
| `modules/orders/tasks.py` | 69% |
| `modules/romaneio/builder.py` | 77% |
| `modules/romaneio/service.py` | 82% |
| `modules/romaneio/router.py` | **0%** (esqueleto, ver §10) |
| `modules/romaneio/tasks.py` | 66% |
| `modules/stock/adapter.py` | 100% |
| `modules/stock/mock_adapter.py` | 86% |
| `modules/stock/consistem_adapter.py` | 94% |
| `modules/stock/service.py` | 86% |
| `modules/stock/router.py` | 55% |
| `modules/stock/tasks.py` | 100% |
| `shared/image_fetcher.py` | 100% |
| `shared/errors.py` | 100% |
| `web/router.py` | 100% |
| `web/data.py` | 100% |
| `web/user_service.py` | 100% |
| `web/auth.py` | 100% |

Observação: routers de domínio (`catalog`, `orders`, `stock`) caem a 55–61 % porque os caminhos mais exercitados aqui são os de integração HTTP em `tests/integration/test_*_pipeline.py` e `tests/e2e/test_api_flows.py`, **não incluídos nesta run**. No CI completo (`pytest tests/ src/catalogflow ...`) esses arquivos sobem.

### Distribuição de testes por módulo

Contagem direta a partir de `pytest --collect-only -q`:

| Módulo / área | Testes coletados |
|---|---:|
| `web/` (router + páginas + auth/data/email/user services) | **353** |
| `modules/catalog/` | 80 |
| `modules/orders/` | 80 |
| `modules/stock/` | 69 |
| `modules/romaneio/` | 41 |
| `modules/auth/` | 32 |
| `shared/` (image_fetcher) | 27 |
| `tests/integration/` | 16 |
| `tests/e2e/` | 3 |
| **Total** | **701** |

`modules/reservation/tests/` existe mas está vazio.

---

## 3. Migrations Alembic

Lidas em ordem de `down_revision` em `migrations/versions/`:

| # | Slug | Tabelas/colunas criadas (`upgrade`) | Drop em `downgrade` |
|---:|---|---|---|
| 0001 | `auth_tables` | `brands`, `api_keys` + `CREATE EXTENSION pgcrypto` | sim (reversível) |
| 0002 | `catalog_tables` | `catalogs`, `catalog_products`, `jobs` | sim |
| 0003 | `orders_schema` | adiciona coluna `brands.logo_key`; cria `orders`, `order_items`, `romaneios` | sim (`drop_column` + drops) |
| 0004 | `web_auth_tables` | `web_users`, `magic_links`, `login_attempts` | sim |
| 0005 | `erp_integration` | `stock_checks`, `erp_submissions` | sim |
| 0006 | `soft_delete` | adiciona `deleted_at` + `deleted_by` em `catalogs`, `orders`, `romaneios` + índices parciais `WHERE deleted_at IS NULL` | sim |

**Cadeia:** `0001_auth → 0002_catalog → 0003_orders → 0004_web_auth → 0005_erp → 0006_soft_delete`. Todas reversíveis. `migrations/env.py` está em modo async.

Nota: `order_items.stock_status` e `order_items.available_qty` já são criadas na 0003 (antecipação da Sprint 02 para o que a Sprint 04 consumiria depois) — a 0005 não as toca.

---

## 4. Módulos — estado real

Inspeção direta de `src/catalogflow/modules/<x>/`:

| Módulo | Arquivos presentes (fora de `tests/`) | Estado real |
|---|---|---|
| `catalog` | `models.py`, `schemas.py`, `service.py`, `router.py`, `tasks.py`, `pdf_analyzer.py`, `field_injector.py`, `dependencies.py` | **Implementado completo** — pipeline `upload → analyzer (puro) → injector (puro) → S3 → DB`. `service.py` = 405 LOC. |
| `orders` | `models.py`, `schemas.py`, `service.py`, `router.py`, `tasks.py`, `extractor.py`, `normalizer.py`, `dependencies.py` | **Implementado completo** (Sprint 02). Suporta formato v1 (`qty__SKU__TAM`) e v2 (`qty__SKU__corN__TAM`); detecta PDF achatado. `service.py` = 359 LOC. |
| `romaneio` | `models.py`, `service.py`, `router.py`, `tasks.py`, `builder.py` | **Implementado completo** (Sprint 02). `builder.py` puro `(order_data, config) → bytes`, suporta logo, fotos AMC QRCode e `available_map`. **Atenção:** `router.py` ainda é esqueleto de 11 linhas (`APIRouter(prefix="/api/v1/romaneio")` sem endpoints) — os endpoints de romaneio reais vivem em `orders/router.py`. |
| `auth` | `models.py`, `schemas.py`, `service.py`, `router.py`, `dependencies.py` (sem `tasks.py`) | **Implementado completo** — `Brand`, `ApiKey` (hash SHA-256 com prefixo `cf_`), `WebUser`, `MagicLink`, `LoginAttempt`. Router serve apenas o gate interno `/internal/*`. |
| `stock` | `models.py`, `schemas.py`, `service.py`, `router.py`, `tasks.py`, `adapter.py` (ABC), `mock_adapter.py`, `consistem_adapter.py`, `dependencies.py` | **Implementado completo** (Sprint 04). 4 endpoints REST, 2 adapters intercambiáveis em runtime via `ERP_ADAPTER`. **Pendente:** `ConsistemAdapter.submit_order` (`NotImplementedError`); `_build_cod_item` em formato provisório. |
| `reservation` | apenas `__init__.py` + `tests/__init__.py` | **Esqueleto vazio** — reservado para a Fase 3 do roadmap. |

---

## 5. Endpoints REST

Varredura: `grep "@router\.(get\|post\|put\|delete\|patch)" src/catalogflow/**/router.py`, mais o `/api/v1/health` montado direto em `main.py`.

### 5.1 API pública (`/api/v1/`)

| Método | Rota | Origem | Função |
|---|---|---|---|
| GET | `/api/v1/health` | `main.py` | Healthcheck (200 ok / 503 degraded) + contadores `jobs.{catalog_pending,order_pending,romaneio_pending}` |
| GET | `/api/v1/jobs/{job_id}` | `shared/jobs_router.py` | Polling genérico de jobs |
| POST | `/api/v1/catalogs/process` | `modules/catalog/router.py` | Upload + enfileira processamento (202) |
| GET | `/api/v1/catalogs/{catalog_id}` | `modules/catalog/router.py` | Metadados + produtos |
| GET | `/api/v1/catalogs/{catalog_id}/download` | `modules/catalog/router.py` | Bytes em dev / 302 presigned em prod |
| POST | `/api/v1/orders/extract` | `modules/orders/router.py` | Upload do PDF preenchido (202) |
| GET | `/api/v1/orders/{order_id}` | `modules/orders/router.py` | Pedido + items + totais |
| GET | `/api/v1/orders/{order_id}/romaneio` | `modules/orders/router.py` | 302 quando pronto, 202 senão |
| POST | `/api/v1/orders/{order_id}/stock-check` | `modules/stock/router.py` | Dispara consulta de estoque (202) |
| GET | `/api/v1/orders/{order_id}/stock-check` | `modules/stock/router.py` | Summary + items da última consulta |
| POST | `/api/v1/orders/{order_id}/submit` | `modules/stock/router.py` | Envia ao ERP (202) — body `{customer_code}` |
| GET | `/api/v1/orders/{order_id}/submission` | `modules/stock/router.py` | Estado do envio + `erp_reference` |

### 5.2 Rotas administrativas (gated por `X-Internal-Secret`)

| Método | Rota | Função |
|---|---|---|
| POST | `/internal/brands` | Cria uma brand (tenant) |
| POST | `/internal/brands/{brand_id}/api-keys` | Gera nova API key (devolve plaintext uma única vez) |

### 5.3 Web UI (`web/router.py`, `include_in_schema=False`)

Não pertencem à API pública. Para referência (32 rotas):

| Categoria | Rotas |
|---|---|
| Auth | `GET /`, `GET/POST /login`, `GET /logout`, `GET/POST /forgot-password`, `GET /magic-link/{token}`, `GET/POST /register` |
| Admin | `GET /admin/users`, `POST /admin/users/{user_id}/approve`, `POST /admin/users/{user_id}/deny` |
| Dashboard | `GET /dashboard` |
| Catálogos | `GET /catalogs`, `GET /catalogs/{id}/_badge`, `GET/POST /catalogs/upload`, `GET /catalogs/upload/poll/{job_id}`, `GET /catalogs/{id}`, `GET /catalogs/{id}/_actions_strip`, `GET /catalogs/{id}/download`, `POST /catalogs/{id}/delete` |
| Pedidos | `GET /orders`, `POST /orders/{id}/delete`, `GET/POST /orders/upload`, `GET /orders/upload/poll/{job_id}`, `GET /orders/{id}/_badge`, `GET /orders/{id}`, `POST /orders/{id}/romaneio`, `POST /orders/{id}/regenerate-romaneio`, `GET /orders/{id}/romaneio/poll`, `GET /orders/{id}/romaneio/download`, `GET /orders/{id}/pendency-report` |
| ERP (proxies da UI) | `POST /orders/{id}/stock-check-web`, `GET /orders/{id}/stock-check-poll`, `POST /orders/{id}/submit-web`, `GET /orders/{id}/submit-poll` |
| Imagens | `GET /product-image/{sku}` |

---

## 6. ADRs

`spec.md §3` é a fonte de verdade — `docs/adr/` contém apenas `.gitkeep` (diretório vazio, planejado mas nunca preenchido).

| # | Título | Localização |
|---:|---|---|
| ADR-001 | Monolito modular (não microserviços) | `spec.md` linha 93 |
| ADR-002 | FastAPI + Celery (não Django, não Flask) | `spec.md` linha 108 |
| ADR-003 | PostgreSQL + Redis (não SQLite) | `spec.md` linha 121 |
| ADR-004 | PyMuPDF (AGPL) — repositório público como conformidade | `spec.md` linha 131 |
| ADR-005 | S3-compatible storage para arquivos PDF | `spec.md` linha 145 |
| ADR-006 | Versionamento de API com prefixo `/api/v1/` | `spec.md` linha 153 |
| ADR-007 | Zonas de Voronoi horizontal para extração de metadados por SKU | `spec.md` linha 159 |
| ADR-008 | Mypy — `ignore_missing_imports` para libs externas, `type: ignore` nos call sites | `spec.md` linha 179 |
| ADR-009 | `pre-commit` como portão local obrigatório | `spec.md` linha 196 |

---

## 7. Integração ERP

### 7.1 Adapters presentes

`src/catalogflow/modules/stock/`:

| Arquivo | Conteúdo | Estado |
|---|---|---|
| `adapter.py` | ABC `StockAdapter` + dataclasses `StockQuery`/`StockResult` + `StockStatus` literal | implementado |
| `mock_adapter.py` | `MockStockAdapter` — distribuição determinística 70/20/10 por hash MD5; `submit_order` aceita sempre e devolve `MOCK-<8 hex>` | implementado |
| `consistem_adapter.py` | `ConsistemAdapter` — HTTP real com `httpx.AsyncClient`, `Semaphore(5)`, header `empresa`, fórmula contábil `estoqueAtual − estReservPedido − estReservProducao − estReservLotes`, timeout 3 s por request | **Parcial** |

> **`src/catalogflow/modules/stock/adapters/` existe mas contém apenas `__init__.py`** — diverge do que `spec.md §5` previa (`adapters/base.py`, `adapters/generic_http.py`). Os adapters reais estão um nível acima, em `modules/stock/`. Ver §10.

### 7.2 `ConsistemAdapter.submit_order`

`consistem_adapter.py:205-214`:

```python
async def submit_order(
    self,
    order_reference: str,
    customer_code: str,
    items: list[StockQuery],
) -> dict[str, Any]:
    raise NotImplementedError(
        "ConsistemAdapter.submit_order: aguardando definição do endpoint "
        "de pedido no Consistem.",
    )
```

→ **Não implementado.** A task Celery `stock.submit` trata `NotImplementedError` como erro **permanente** (sem retry), conforme `CHANGELOG.md` v0.4.0.

### 7.3 `_build_cod_item`

`consistem_adapter.py:86-97`:

```python
def _build_cod_item(self, sku: str, size: str, color_index: int) -> str:
    """Formato provisório: "{sku}.{size}.{color_index}"."""
    return f"{sku}.{size}.{color_index}"
```

→ **Mapeamento provisório**. A docstring registra que o formato real depende de definição da Oasis e que apenas esta função muda quando chegar.

### 7.4 Variáveis de ambiente ERP (lidas por `infra/settings.py`)

| Var | Default | Tipo |
|---|---|---|
| `ERP_ADAPTER` | `"mock"` | `Literal["mock", "consistem"]` — selecionado em **runtime** por `StockService.get_adapter()`, sem rebuild |
| `ERP_BASE_URL` | `"https://api.consistem.com.br"` | str |
| `ERP_API_KEY` | `None` | `SecretStr | None` |
| `ERP_EMPRESA` | `"50"` | str (AMC Têxtil) |
| `ERP_COD_NATUREZA` | `505` | int (estoque nacional) |
| `ERP_TIMEOUT` | `30` | int (timeout agregado do `httpx.AsyncClient`) |

`.env.example` confirma essas 6 chaves.

---

## 8. Deploy / Infraestrutura

### 8.1 Serviços do `docker/docker-compose.yml`

| Serviço | Imagem | Porta host → container | Notas |
|---|---|---|---|
| `api` | build local (`docker/Dockerfile` target=`production`) | **8004 → 8000** | Uvicorn com `--reload`, serve API REST + UI web |
| `worker` | mesma imagem | — | Celery worker, concurrency 2, filas `catalog,orders,romaneio,stock,default` |
| `beat` | mesma imagem | — | Celery beat (scheduler) |
| `flower` | mesma imagem | **5556 → 5555** | Monitoring do Celery (dev only) |
| `postgres` | `postgres:16-alpine` | **5437 → 5432** | Volume `postgres_data`; healthcheck `pg_isready` |
| `redis` | `redis:7-alpine` | **6380 → 6379** | `--appendonly yes`; healthcheck `redis-cli ping` |
| `minio` | `minio/minio:latest` | **9002 → 9000** (S3), **9003 → 9001** (console) | Substitui R2/S3 em dev e produção atual |
| `minio-init` | `minio/mc:latest` | — | Cria o bucket `${S3_BUCKET:-catalogflow-dev}` |

### 8.2 Produção

- URL pública: <https://catalogo.thiagoscutari.com.br>
- VPS: `162.240.102.45` (citada em `spec.md §4`)
- **Proxy reverso: Traefik** — confirmado em `spec.md §4` linha 240 ("VPS + Docker Compose + Traefik") e `README.md` linhas 556/679/702.
- **HTTPS:** servido pelo Traefik (não há referência a Certbot/Caddy/Nginx no repositório). Sem manifesto Traefik commitado em `deploy/` (diretório não existe) — a configuração do proxy mora **fora do repositório**, no host de produção.
- Deploy automatizado: **não existe** — `spec.md` previa `.github/workflows/deploy.yml`, mas apenas `ci.yml` está commitado. README confirma: _"Deploy atual: manual via VPS + Docker Compose + Traefik (HTTPS). CI/CD de deploy automatizado está planejado para uma sprint futura."_

---

## 9. Stack — dependências de produção

Bloco `[project] dependencies` de `pyproject.toml` (versões mínimas):

| Categoria | Pacote | Versão mínima |
|---|---|---|
| Web | `fastapi` | ≥ 0.115.0 |
| Web | `uvicorn[standard]` | ≥ 0.32.0 |
| Web | `python-multipart` | ≥ 0.0.12 |
| Validação | `pydantic` | ≥ 2.9.0 |
| Validação | `pydantic-settings` | ≥ 2.6.0 |
| Validação | `email-validator` | ≥ 2.2.0 |
| ORM | `sqlalchemy[asyncio]` | ≥ 2.0.36 |
| Migrations | `alembic` | ≥ 1.14.0 |
| DB driver | `asyncpg` | ≥ 0.30.0 |
| Queue | `celery` | ≥ 5.4.0 |
| Cache/broker | `redis` | ≥ 5.2.0 |
| Monitoring | `flower` | ≥ 2.0.1 |
| PDF | `pymupdf` | ≥ 1.27.0 |
| PDF | `pdfplumber` | ≥ 0.11.0 |
| PDF | `pypdfform` | ≥ 4.0.0 |
| PDF | `qrcode[pil]` | ≥ 8.0 |
| Storage | `boto3` | ≥ 1.35.0 |
| Storage | `aioboto3` | ≥ 13.2.0 |
| Auth | `python-jose[cryptography]` | ≥ 3.3.0 |
| Auth | `bcrypt` | ≥ 4.0 |
| HTTP client | `httpx` | ≥ 0.28 |
| Web UI | `jinja2` | ≥ 3.1 |
| Web UI | `itsdangerous` | ≥ 2.1 |
| Scraping (thumbs) | `beautifulsoup4` | ≥ 4.12 |
| Email | `resend` | ≥ 2.0 |
| MIME | `python-magic` (linux/mac) / `python-magic-bin` (win) | ≥ 0.4.27 / ≥ 0.4.14 |
| Logs | `structlog` | ≥ 24.4.0 |
| Erros | `sentry-sdk[fastapi,celery]` | ≥ 2.18.0 |

Python: **≥ 3.12** (`requires-python = ">=3.12"`).

---

## 10. Divergências entre `spec.md`, `CLAUDE.md` e o código real

| # | Onde a divergência aparece | Estado real | Risco / observação |
|---:|---|---|---|
| 1 | `spec.md §5` lista `modules/stock/adapters/{base.py, generic_http.py}` | `modules/stock/adapters/` existe apenas com `__init__.py`. Adapters reais (`adapter.py`, `mock_adapter.py`, `consistem_adapter.py`) ficam um nível acima, em `modules/stock/`. | Cosmético — afeta navegação e expectativa de onde adicionar novos adapters. |
| 2 | `CLAUDE.md` (Critical Constraints): _"PyMuPDF license: Do not deploy to production without commercial license or switching to PyPDFForm fallback."_ | `spec.md` ADR-004 (atualizado em 2026-05-11) declara que **manter o repositório público no GitHub satisfaz a AGPL**, dispensando licença Artifex. Já está em produção. | **`CLAUDE.md` está desatualizado** em relação à decisão arquitetural vigente. Confunde futuras decisões de "fechar" o repositório. |
| 3 | `spec.md §6` e o esqueleto `modules/romaneio/router.py` declaram `prefix="/api/v1/romaneio"` | `router.py` não tem nenhum endpoint. O download/disparo de romaneio vive em `GET /api/v1/orders/{order_id}/romaneio` (em `orders/router.py`). | Cosmético — leva alguém procurando endpoint de romaneio ao lugar errado. Remover ou implementar. |
| 4 | `spec.md §5` prevê `docs/adr/ADR-001-...md` a `ADR-006-...md` como arquivos separados | `docs/adr/` contém apenas `.gitkeep`; todos os 9 ADRs vivem em `spec.md §3`. | Aceitável (escolha de centralizar no spec), mas o spec está se referenciando a si mesmo. |
| 5 | `spec.md §4` lista PostgreSQL na porta **5432** | `docker-compose.yml` mapeia **5437 → 5432** no host (evita conflito com Postgres local). Mesma lógica para Redis (6380→6379) e MinIO (9002→9000, 9003→9001). | Sem risco — apenas exige `.env` correto em dev. |
| 6 | `spec.md §4` lista `Sentry` e `OpenTelemetry` na stack ("não implantado — Sprint futura") | `sentry-sdk` está em `pyproject.toml` e `infra/settings.py` aceita `sentry_dsn`, mas **não há código de instrumentação Sentry ativo** em `main.py` (nenhum `sentry_sdk.init(...)`). OpenTelemetry: não presente. | Esperado pelo próprio spec — apenas reforça o débito de observabilidade. |
| 7 | `spec.md §5` prevê `shared/utils/file.py` e `shared/utils/mime.py` (sanitização e detecção MIME) | `src/catalogflow/shared/utils/` contém apenas `__init__.py`. Validação de MIME está embutida em `catalog/service.py` / `orders/service.py` (assinatura `%PDF` + `python-magic`). | Cosmético — não bloqueia, mas o spec aponta para arquivos inexistentes. |
| 8 | `spec.md §5` prevê `shared/pagination.py` (Page / PageParams) | Arquivo não existe. Paginação web está dentro de `web/data.py`. | Cosmético — sem impacto operacional. |
| 9 | `spec.md §5` prevê `.github/workflows/deploy.yml` | Apenas `ci.yml` existe. Deploy é manual (confirmado no README). | Aceitável (deploy CI/CD planejado para sprint futura). |
| 10 | `CLAUDE.md` "Common Mistakes #10": `pre-commit install` obrigatório / ADR-009 introduz `pre-commit` como portão local | Em conformidade — não é divergência, apenas confirma que ADR-009 (Sprint 06) é o consolidador. | OK. |
| 11 | `pyproject.toml` declara `version = "0.1.0"`; `spec.md` cabeçalho declara `Versão: 0.2.0`; `CHANGELOG.md` está em `[0.4.0]` (Sprint 04) | Três fontes de "versão" divergem. | Cosmético, mas confunde quem chega do `pip show`. |
| 12 | `spec.md §6` (módulo `stock`): assinatura `check_availability(skus: list[str]) -> dict[str, StockInfo]` | Implementação real: `check_availability(items: list[StockQuery]) -> list[StockResult]`. | Spec mais alta-nível; a interface real é mais granular (`sku, size, color_index, requested_qty`). Atualizar spec. |
| 13 | Migração `0005_erp` cria `stock_checks` e `erp_submissions` | `spec.md §7` ("Schemas SQL") **não documenta** essas tabelas — só menciona `order_items.stock_status`/`available_qty`. | Spec ficou para trás da Sprint 04. |
| 14 | Migração `0006_soft_delete` adiciona `deleted_at` / `deleted_by` em `catalogs`, `orders`, `romaneios` | `spec.md §7` não menciona soft-delete. | Spec ficou para trás. |
| 15 | `spec.md §6` (`auth`) lista apenas `Brand`, `ApiKey`, `User` | Implementação acrescenta `WebUser` + `MagicLink` + `LoginAttempt` (Sprint 03.5). | Spec ficou para trás. |

---

## Resumo executivo

1. **Backend está consolidado:** 70 arquivos / 12 191 LOC em `src/`, **701 testes coletados**, threshold ≥ 80 % de cobertura imposto no CI, 6 migrations reversíveis encadeadas e 6 módulos de domínio — todos implementados exceto `reservation` (Fase 3, esqueleto).
2. **API REST tem 14 endpoints públicos** sob `/api/v1/` (+ 2 internos gated por `X-Internal-Secret`), cobrindo todo o ciclo `catálogo → pedido → romaneio → estoque → envio ao ERP`; a UI web servida no mesmo container expõe outras 32 rotas Jinja2 + HTMX.
3. **Integração ERP entregue parcialmente:** `MockStockAdapter` 100 % e `ConsistemAdapter.check_availability` 100 % (com fórmula contábil e Semaphore(5)); pendentes **apenas** `ConsistemAdapter.submit_order` (`NotImplementedError`) e o mapeamento real de `_build_cod_item` — ambos bloqueados pela definição da Oasis.
4. **Em produção** em <https://catalogo.thiagoscutari.com.br> sobre VPS + Docker Compose + **Traefik (HTTPS)**; storage S3 via MinIO; deploy ainda manual (não há `deploy.yml` no CI).
5. **Documentação parcialmente desatualizada:** `spec.md` e `CLAUDE.md` precisam alinhar com o que existe em código (soft-delete, `web_users`, `stock_checks`/`erp_submissions`, assinatura real do `StockAdapter`, status do PyMuPDF/AGPL via repositório público); divergências catalogadas em §10.
