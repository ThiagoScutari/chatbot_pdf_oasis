# PRD Sprint 01 — Project Foundation + Catalog Module

> **Projeto:** CatalogFlow  
> **Sprint:** 01 / Foundation  
> **Status:** Aprovado  
> **Data de início:** A definir  
> **Duração estimada:** 5–7 dias de trabalho do executor  
> **PMO:** Thiago Scutari  
> **Executor:** Claude Code  
> **Referência obrigatória:** `spec.md` (contrato técnico do projeto)

---

## Objetivo da Sprint

Estabelecer a fundação completa do projeto CatalogFlow e entregar o **pipeline de processamento de catálogo PDF funcionando de ponta a ponta** — do upload do PDF à disponibilização do PDF editável com campos AcroForm.

Ao final desta sprint, o comando `docker-compose up` deve levantar um sistema funcional onde é possível:
1. Autenticar-se via API Key
2. Fazer upload de um catálogo PDF
3. Receber um `job_id` e acompanhar o status
4. Baixar o PDF editável com campos de pedido (grade por cor × tamanho)
5. Executar `pytest` e ver ≥ 80% de cobertura passando no CI

---

## Contexto

O repositório já contém dois scripts funcionais de prova de conceito:
- `oasis_form_v2.py` — lógica de análise e injeção de campos AcroForm
- `oasis_romaneio.py` — geração de romaneio PDF

Esta sprint **migra e refatora** esses scripts para dentro da arquitetura modular definida no `spec.md`. Nenhuma lógica nova de PDF precisa ser inventada — o código já existe e foi validado. A sprint é de **estruturação, refatoração e hardening**.

---

## Entregáveis

### E1 — Estrutura de projeto completa

Criar a hierarquia de pastas e arquivos exatamente como especificado na seção 5 do `spec.md`:

```
catalogflow/
├── src/catalogflow/
│   ├── main.py
│   ├── modules/
│   │   ├── catalog/
│   │   ├── orders/       ← esqueleto apenas (sem lógica)
│   │   ├── romaneio/     ← esqueleto apenas (sem lógica)
│   │   └── auth/
│   ├── shared/
│   └── infra/
├── tests/
├── migrations/
├── docs/
├── .github/workflows/
├── docker/
└── [arquivos raiz]
```

Módulos `orders` e `romaneio` entram como **esqueleto** (arquivos vazios com docstrings): a lógica completa entra na Sprint 02.

---

### E2 — Configuração de projeto (`pyproject.toml`)

Arquivo `pyproject.toml` com:
- Todas as dependências da seção 4 do `spec.md`
- Configuração do `ruff` (lint + format, target Python 3.12)
- Configuração do `mypy` (strict mode)
- Configuração do `pytest` (asyncio mode, coverage threshold 80%, testpaths)
- Scripts de conveniência: `catalogflow-api`, `catalogflow-worker`

---

### E3 — Docker e Compose

`docker/Dockerfile` com build multi-stage:
- Stage `builder`: instala dependências
- Stage `production`: copia apenas o necessário, roda como usuário não-root `catalogflow`

`docker/docker-compose.yml` com serviços:
- `api` — FastAPI em porta 8000
- `worker` — Celery worker (concurrency=2 em dev)
- `beat` — Celery beat (scheduler)
- `postgres` — PostgreSQL 16, volume persistente
- `redis` — Redis 7, volume persistente
- `flower` — Celery monitoring em porta 5555 (dev only)

Variáveis de ambiente: via `.env` (gitignored) com template em `.env.example`.

---

### E4 — Infraestrutura (`src/catalogflow/infra/`)

#### `infra/settings.py`
Pydantic `BaseSettings` com:
- `DATABASE_URL`, `REDIS_URL`
- `SECRET_KEY`, `ALGORITHM` (JWT)
- `S3_BUCKET`, `S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- `MAX_PDF_SIZE_MB` (default: 50)
- `MAX_CONCURRENT_JOBS_STARTER` (default: 5)
- `SENTRY_DSN` (opcional)
- `ENVIRONMENT` (development | staging | production)

#### `infra/database.py`
- Engine assíncrono SQLAlchemy 2.0
- `AsyncSession` factory
- `Base` declarativa
- `get_db()` como FastAPI dependency

#### `infra/storage.py`
- Wrapper sobre `boto3` para S3/R2
- `StorageClient.upload(key, data) -> str`
- `StorageClient.download(key) -> bytes`
- `StorageClient.presigned_url(key, expires_in=3600) -> str`
- `StorageClient.delete(key) -> None`
- Injetável como FastAPI dependency

#### `infra/cache.py`
- Wrapper sobre `redis.asyncio`
- `get_redis()` como FastAPI dependency

#### `infra/celery_app.py`
- Celery app configurada com Redis broker
- Task serialization: JSON
- Result backend: Redis com TTL de 24h
- Task routes definidas por módulo

---

### E5 — Módulo `auth` completo

#### Modelos (`auth/models.py`)
- `Brand`: id (UUID), slug, name, plan, created_at
- `ApiKey`: id, brand_id (FK), name, key_hash (SHA-256), key_prefix (8 chars), last_used, expires_at
- Sem modelo `User` por ora (auth por API Key apenas na Sprint 01)

#### Service (`auth/service.py`)
- `create_brand(slug, name, plan) -> Brand`
- `create_api_key(brand_id, name) -> tuple[ApiKey, str]` — retorna model + plaintext key (única vez)
- `verify_api_key(raw_key) -> Brand | None`
- `rotate_api_key(api_key_id) -> tuple[ApiKey, str]`

#### Dependency (`auth/dependencies.py`)
- `get_current_brand(authorization: str = Header(...)) -> Brand`
- Extrai Bearer token, faz hash SHA-256, busca no banco
- Levanta `HTTPException(401)` se inválido ou expirado
- Atualiza `last_used` de forma assíncrona (não bloqueia a request)

#### Router (`auth/router.py`)
Apenas endpoints de setup/admin (não expostos na API pública):
- `POST /internal/brands` — criar brand (protegido por `INTERNAL_SECRET`)
- `POST /internal/brands/{id}/api-keys` — criar API key

#### Testes (`auth/tests/`)
- `test_service.py`: criar brand, criar API key, verificar hash correto, verificar key inválida, verificar expiração
- `test_dependencies.py`: header válido passa, header inválido retorna 401, header ausente retorna 401

---

### E6 — Módulo `catalog` completo

#### Modelos (`catalog/models.py`)
- `Catalog`: todos os campos da seção 7 do `spec.md`
- `CatalogProduct`: todos os campos da seção 7 do `spec.md`

#### Schemas Pydantic (`catalog/schemas.py`)
- `CatalogCreateRequest`: name (str), collection (str | None)
- `CatalogResponse`: todos os campos públicos
- `CatalogProductResponse`: sku, name, price, grade, sizes, n_colors, page_index
- `JobResponse`: job_id, status, progress, result, error

#### `catalog/pdf_analyzer.py` — migrado de `oasis_form_v2.py`

Classe `PDFAnalyzer` com métodos puros (sem side effects de I/O):

```python
class PDFAnalyzer:
    def analyze(self, pdf_bytes: bytes) -> CatalogMetadata:
        """
        Analisa o PDF e retorna metadados completos.
        Levanta CatalogAnalysisError se o PDF não tiver produtos.
        """
    
    def _detect_product_pages(self, doc) -> list[int]:
        """Identifica páginas com SKU válido via regex."""
    
    def _extract_page_metadata(self, page, page_idx) -> list[ProductPageMeta]:
        """Extrai SKU, grade, preço de uma página."""
    
    def _detect_swatches(self, page) -> list[SwatchInfo]:
        """Detecta quadrados coloridos (drawings) no rodapé da página."""
```

Dataclasses de resultado:
- `SwatchInfo(x0, y0, fill_rgb, fill_hex)`
- `ProductPageMeta(sku, name, price, grade, sizes, n_colors, swatches, page_index, x_block_start, x_block_end, y_start, y_end, side, n_products_on_page)`
- `CatalogMetadata(n_pages, n_product_pages, product_pages: list[ProductPageMeta])`

#### `catalog/field_injector.py` — migrado de `oasis_form_v2.py`

Classe `FieldInjector` com método principal puro:

```python
class FieldInjector:
    def inject(self, pdf_bytes: bytes, metadata: CatalogMetadata) -> bytes:
        """
        Injeta campos AcroForm no PDF e retorna os bytes do PDF modificado.
        Não faz I/O. Recebe bytes, retorna bytes.
        """
    
    def _draw_panel(self, page, product_meta: ProductPageMeta) -> int:
        """Desenha o painel visual e insere widgets. Retorna n_fields inseridos."""
    
    def _calculate_panel_rect(self, product_meta, page_w, page_h, all_products) -> PanelRect:
        """Calcula posição do painel sem overlapping."""
```

**Regra crítica:** `field_injector.py` não abre arquivos. Recebe `bytes`, retorna `bytes`. Todo I/O fica no `service.py`.

#### `catalog/service.py`

```python
class CatalogService:
    async def create_catalog(self, brand_id, name, collection, pdf_bytes) -> tuple[Catalog, Job]:
        """Valida, faz upload, cria registros, enfileira job."""
    
    async def get_catalog(self, catalog_id, brand_id) -> Catalog:
        """Busca catálogo. Levanta NotFoundError se não for da brand."""
    
    async def get_download_url(self, catalog_id, brand_id) -> str:
        """Retorna presigned URL. Levanta NotReadyError se status != ready."""
    
    async def process_catalog(self, catalog_id) -> None:
        """Lógica de processamento. Chamada pela Celery task."""
```

#### `catalog/tasks.py`

```python
@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="catalog.process"
)
def process_catalog_task(self, catalog_id: str) -> dict:
    """
    Wrapper síncrono para a lógica assíncrona do service.
    Atualiza Job.status em pending → running → success/error.
    Em caso de erro, faz retry com backoff exponencial.
    """
```

#### `catalog/router.py`

```python
router = APIRouter(prefix="/api/v1/catalogs", tags=["catalog"])

@router.post("/process", status_code=202)
async def process_catalog(
    file: UploadFile,
    name: str = Form(...),
    collection: str | None = Form(None),
    brand: Brand = Depends(get_current_brand),
    service: CatalogService = Depends()
) -> StandardResponse[ProcessCatalogResponse]: ...

@router.get("/{catalog_id}")
async def get_catalog(
    catalog_id: UUID,
    brand: Brand = Depends(get_current_brand),
    service: CatalogService = Depends()
) -> StandardResponse[CatalogResponse]: ...

@router.get("/{catalog_id}/download")
async def download_catalog(
    catalog_id: UUID,
    brand: Brand = Depends(get_current_brand),
    service: CatalogService = Depends()
) -> RedirectResponse: ...
```

Router adicional em `shared/`:
```python
# shared router para jobs
@router.get("/api/v1/jobs/{job_id}")
async def get_job(...) -> StandardResponse[JobResponse]: ...
```

#### `catalog/tests/`

**`test_pdf_analyzer.py`** — testa com PDFs de fixture reais:

| Fixture | Cenário testado |
|---------|----------------|
| `catalogo_1_produto_1_cor.pdf` | Happy path básico |
| `catalogo_1_produto_2_cores.pdf` | Detecção de 2 swatches |
| `catalogo_2_produtos_pagina.pdf` | Layout de 2 produtos por página |
| `catalogo_pp_g.pdf` | Grade com 4 tamanhos |
| `pdf_sem_produtos.pdf` | Levanta `CatalogAnalysisError` |
| `pdf_criptografado.pdf` | Levanta `PDFEncryptedError` |
| `pdf_corrompido.pdf` | Levanta `PDFCorruptError` |

**`test_field_injector.py`**:
- Injetar em PDF de 1 produto → n_fields correto
- Injetar em PDF de 2 produtos por página → campos não se sobrepõem
- Nomenclatura correta: `qty__SKU__cor1__PP`
- PDF resultante tem `/AcroForm` válido (verificado com PyMuPDF)

**`test_service.py`**:
- Criar catálogo enfileira job
- Catálogo de outra brand retorna NotFoundError
- Catálogo com status pending retorna NotReadyError no download
- `process_catalog` atualiza status corretamente

**`test_router.py`** (integration via `httpx.AsyncClient`):
- Upload sem auth → 401
- Upload com API key inválida → 401
- Upload de arquivo não-PDF → 400 `INVALID_FILE_TYPE`
- Upload de PDF > 50MB → 400 `FILE_TOO_LARGE`
- Upload válido → 202 com job_id
- GET catálogo de outra brand → 404
- GET job existente → 200 com status

---

### E7 — Migrations (Alembic)

- Configurar Alembic com `env.py` async
- `migrations/versions/001_initial_schema.py` — criar todas as tabelas da seção 7 do `spec.md`
- Script de seed para desenvolvimento: cria brand "oasis" + API key de teste, imprime a key no stdout

---

### E8 — Pipeline CI/CD (`.github/workflows/ci.yml`)

Jobs em sequência (cada um depende do anterior):

1. **`quality`**: ruff check, ruff format --check, mypy --strict
2. **`test`**: pytest com Postgres + Redis reais via services do GitHub Actions, coverage ≥ 80%
3. **`build`**: docker build multi-stage, smoke test na imagem (`docker run --rm <image> python -c "from catalogflow import main"`)
4. **`security`**: pip-audit (falha se CVSS ≥ 7.0), bandit -r src/

---

### E9 — `app.main` e endpoints base

`src/catalogflow/main.py`:
- FastAPI app factory (função `create_app()`)
- Lifespan: startup (testar conexão DB + Redis), shutdown (fechar pools)
- Registro de todos os routers
- Middleware: request ID, CORS (configurável), rate limiting global
- `GET /api/v1/health` → `{"status": "ok", "db": "ok", "redis": "ok"}`
- Exception handlers globais para erros de domínio → respostas padronizadas

---

### E10 — Fixtures de PDF para testes

Gerar programaticamente (não usar PDFs reais da Oasis em fixtures de CI):

Script `tests/fixtures/generate_fixtures.py` que cria PDFs mínimos de teste usando PyMuPDF:
- PDFs com swatches e texto simulados, sem fotos reais
- Executado uma vez, resultado commitado em `tests/fixtures/*.pdf`

O catálogo real da Oasis (`example/`) é usado apenas para testes manuais e smoke tests, nunca para CI automatizado.

---

## Acceptance Criteria

| ID | Critério | Verificação |
|----|---------|------------|
| AC-01 | `docker-compose up` levanta todos os serviços sem erro | Manual |
| AC-02 | `curl -X POST /api/v1/catalogs/process` com PDF válido retorna 202 + job_id | Automated (E2E) |
| AC-03 | Polling `GET /api/v1/jobs/{id}` atinge status `success` em < 60s para catálogo de 70 páginas | Manual com catálogo Oasis |
| AC-04 | PDF resultante tem campos AcroForm com nomenclatura `qty__SKU__corN__TAM` | Automated (unit) |
| AC-05 | PDF resultante é preenchível no Adobe Reader e Xodo | Manual |
| AC-06 | `pytest tests/` passa com cobertura ≥ 80% | CI |
| AC-07 | `ruff check . && mypy src/` passa sem erros | CI |
| AC-08 | `docker build` conclui com sucesso em < 5 minutos | CI |
| AC-09 | API key inválida sempre retorna 401 (nunca 500) | Automated |
| AC-10 | PDF da brand A não é acessível com API key da brand B | Automated |
| AC-11 | PDF criptografado retorna 400 com código `PDF_ENCRYPTED` | Automated |
| AC-12 | Upload de arquivo > 50MB retorna 400 antes de processar | Automated |
| AC-13 | MIME type do upload é verificado server-side (não só extensão) | Automated |

---

## Definition of Done (DoD)

Uma tarefa está **pronta** quando:

- [ ] Código implementado e commitado em branch `feature/sprint-01-<nome>`
- [ ] Testes unitários escritos e passando (não "vou escrever depois")
- [ ] Mypy sem erros no arquivo modificado
- [ ] Ruff sem warnings no arquivo modificado
- [ ] PR criado com description descrevendo o que muda e por quê
- [ ] CI verde no PR (quality + test + build)
- [ ] `spec.md` consultado — nenhuma decisão que contradiz os ADRs

A sprint está **concluída** quando:

- [ ] Todos os entregáveis E1–E10 completos
- [ ] Todos os ACs passando
- [ ] `docker-compose up && pytest` funciona em máquina limpa (zero estado local)
- [ ] README.md atualizado com instruções de setup de 5 minutos
- [ ] CHANGELOG.md com entry da Sprint 01

---

## Out of Scope (esta sprint)

- ❌ Módulo `orders` com lógica completa (Sprint 02)
- ❌ Módulo `romaneio` com lógica completa (Sprint 02)
- ❌ Módulo `stock` e `reservation` (Sprint 03+)
- ❌ Web UI / Frontend (Sprint 03)
- ❌ Webhook de notificação (Sprint 02)
- ❌ Modelo `User` com login/senha (Sprint 03)
- ❌ QR Code por produto no PDF (Sprint 02, opcional)
- ❌ Deploy em produção (Sprint 04)
- ❌ Integração com Sentry (Sprint 04)

---

## Ordem de Implementação Recomendada

O executor deve seguir esta ordem para ter um sistema integrado o mais cedo possível:

```
1. Estrutura de pastas + pyproject.toml + .env.example
2. docker-compose.yml + Dockerfile
3. infra/settings.py + infra/database.py + infra/cache.py
4. Alembic init + migration 001 (tabelas auth)
5. auth/models.py + auth/service.py + auth/tests/
6. infra/storage.py
7. catalog/models.py + Alembic migration 002 (tabelas catalog)
8. catalog/pdf_analyzer.py (migrado de oasis_form_v2.py) + tests
9. catalog/field_injector.py (migrado de oasis_form_v2.py) + tests
10. infra/celery_app.py + catalog/tasks.py
11. catalog/service.py + tests
12. main.py + catalog/router.py + auth/router.py + shared/router.py
13. tests/integration/ + tests/e2e/
14. .github/workflows/ci.yml
15. tests/fixtures/generate_fixtures.py
16. README.md + CHANGELOG.md
```

**Regra:** nenhum passo depende de um step posterior. O sistema deve ser executável (mesmo que incompleto) após cada passo.

---

## Referências

| Documento | Localização | Uso |
|-----------|------------|-----|
| Spec técnico | `spec.md` | Fonte de verdade para todas as decisões |
| Scripts originais | `oasis_form_v2.py`, `oasis_romaneio.py` | Lógica de PDF a migrar |
| Catálogo exemplo | `example/CATÁLOGO OASIS MOTION_original.pdf` | Smoke test manual |
| PDF editável exemplo | `example/OASIS_MOTION_v2_editavel.pdf` | Referência visual do output esperado |
| Romaneio exemplo | `example/romaneio_demo.pdf` | Referência visual (Sprint 02) |
