# CatalogFlow — Especificação Técnica do Produto

> **Versão:** 0.6.0  
> **Status:** Ativo — Fase 1 (MVP) em produção  
> **Autores:** Thiago Scutari (PMO), Claude Sonnet 4.6 (arquitetura)  
> **Executor:** Claude Code  
> **Criado em:** 2026-05-11  
> **Última atualização:** 2026-05-19

---

## Índice

1. [Visão e Problema](#1-visão-e-problema)
2. [Solução e Proposta de Valor](#2-solução-e-proposta-de-valor)
3. [Decisões Arquiteturais (ADRs)](#3-decisões-arquiteturais-adrs)
4. [Stack Técnico](#4-stack-técnico)
5. [Estrutura do Projeto](#5-estrutura-do-projeto)
6. [Módulos e Responsabilidades](#6-módulos-e-responsabilidades)
7. [Modelos de Dados](#7-modelos-de-dados)
8. [API Contract](#8-api-contract)
9. [Pipelines de Processamento](#9-pipelines-de-processamento)
10. [Estratégia de Testes](#10-estratégia-de-testes)
11. [Pipeline CI/CD](#11-pipeline-cicd)
12. [Segurança](#12-segurança)
13. [Requisitos Não-Funcionais](#13-requisitos-não-funcionais)
14. [Roadmap de Fases](#14-roadmap-de-fases)
15. [Fora de Escopo (YAGNI)](#15-fora-de-escopo-yagni)
16. [Glossário](#16-glossário)

---

## 1. Visão e Problema

### Problema

Empresas de moda brasileiras distribuem catálogos de produtos em PDF visual (gerado por agência de marketing, sem campos editáveis). O fluxo de pedido atual é:

1. Gerente comercial envia PDF para lojistas via WhatsApp
2. Lojista imprime o catálogo, escreve quantidades à mão por produto/cor/tamanho
3. Lojista fotografa ou tira print da página preenchida
4. Envia a foto por WhatsApp para a gerente comercial
5. Gerente interpreta a imagem manualmente, consulta estoque e monta o pedido no sistema

**Consequências mensuráveis:**
- 2–4 horas/dia de trabalho manual repetitivo da gerente comercial
- Taxa de erro de transcrição estimada em 8–15% (pedido errado de tamanho/quantidade)
- Ciclo de pedido de 1–3 dias; concorrência com catálogo digital fecha em horas
- Impossibilidade de analytics sobre quais produtos são mais pedidos por lojista

### Oportunidade

O catálogo PDF bonito (produzido por agência) continua sendo o instrumento de comunicação de moda. O problema não é o PDF — é a ausência de um mecanismo de captura estruturada de dados. A solução não deve substituir o catálogo: deve torná-lo interativo.

---

## 2. Solução e Proposta de Valor

### CatalogFlow

Plataforma SaaS que transforma catálogos PDF visuais em instrumentos de captura de pedido, e processa PDFs preenchidos em romaneios estruturados — automaticamente, via API ou interface web.

**Fluxo primário (Fase 1):**

```
[PDF visual da agência]
        ↓
  POST /api/v1/catalogs/process
        ↓
[PDF editável com AcroForm]  →  distribuído via WhatsApp
        ↓ (lojista preenche quantidades)
  POST /api/v1/orders/extract
        ↓
[Romaneio PDF]  +  [JSON estruturado do pedido]
```

**Proposta de valor quantificada:**
- De 2–4h/dia para <10 minutos de revisão estruturada
- Taxa de erro → 0% na transcrição (dados são digitados pela lojista)
- Ciclo de pedido: de dias para horas
- Dados estruturados habilitam analytics, histórico, forecasting

**Modelo de negócio:**
- SaaS B2B com assinatura mensal por marca (não por lojista)
- Tier Starter: R$499/mês — até 3 catálogos ativos, pedidos ilimitados
- Tier Growth: R$999/mês — catálogos ilimitados + API access + webhook
- Tier Enterprise: negociado — integração ERP, SLA, suporte dedicado

---

## 3. Decisões Arquiteturais (ADRs)

> **Fonte única:** cada ADR vive em `docs/adr/ADR-NNN-slug.md`. Esta seção
> é apenas o índice. Mudanças de decisão devem editar o arquivo dedicado e
> manter esta tabela em sincronia. Ver [`docs/adr/README.md`](./docs/adr/README.md)
> para o formato.

| # | Título | Arquivo |
|---:|---|---|
| ADR-001 | Monolito Modular (não microserviços) | [`docs/adr/ADR-001-monolito-modular.md`](./docs/adr/ADR-001-monolito-modular.md) |
| ADR-002 | FastAPI + Celery (não Django, não Flask) | [`docs/adr/ADR-002-fastapi-celery.md`](./docs/adr/ADR-002-fastapi-celery.md) |
| ADR-003 | PostgreSQL + Redis (não SQLite) | [`docs/adr/ADR-003-postgres-redis.md`](./docs/adr/ADR-003-postgres-redis.md) |
| ADR-004 | PyMuPDF (AGPL) — repositório público como conformidade | [`docs/adr/ADR-004-pymupdf-license.md`](./docs/adr/ADR-004-pymupdf-license.md) |
| ADR-005 | S3-compatible storage para arquivos PDF | [`docs/adr/ADR-005-s3-storage.md`](./docs/adr/ADR-005-s3-storage.md) |
| ADR-006 | Versionamento de API com prefixo `/api/v1/` | [`docs/adr/ADR-006-api-versioning.md`](./docs/adr/ADR-006-api-versioning.md) |
| ADR-007 | Zonas de Voronoi horizontal para extração de metadados por SKU | [`docs/adr/ADR-007-voronoi-zones.md`](./docs/adr/ADR-007-voronoi-zones.md) |
| ADR-008 | Mypy — `ignore_missing_imports` para libs externas, `type: ignore` nos call sites | [`docs/adr/ADR-008-mypy-config.md`](./docs/adr/ADR-008-mypy-config.md) |
| ADR-009 | `pre-commit` como portão local obrigatório | [`docs/adr/ADR-009-pre-commit.md`](./docs/adr/ADR-009-pre-commit.md) |

---

## 4. Stack Técnico

| Camada | Tecnologia | Versão | Justificativa |
|--------|-----------|--------|---------------|
| Runtime | Python | 3.12+ | Type hints maduros, asyncio estável |
| Web Framework | FastAPI | 0.115+ | Async nativo, OpenAPI, Pydantic v2 |
| Validação | Pydantic | v2 | Performance 10x v1, serialização automática |
| ORM | SQLAlchemy | 2.0 | Async support, type-safe queries |
| Migrations | Alembic | latest | Nunca alterar schema manualmente |
| Queue | Celery | 5.x | Retry, ETA, priorities, monitoring |
| Broker/Cache | Redis | 7.x | Celery broker + resultado + cache |
| Banco de dados | PostgreSQL | 16 | Concurrent writes, JSONB, full-text |
| PDF Engine | PyMuPDF (fitz) | 1.27+ | AcroForm, swatch detection, overlay |
| PDF Parse | pdfplumber | latest | Extração de texto/coordenadas |
| PDF Fallback | PyPDFForm | 4.x | MIT license, AcroForm |
| QR Code | qrcode[pil] | 8.x | Geração de QR por produto |
| File Storage | boto3 (S3/R2) | latest | Upload/download de PDFs |
| Auth | python-jose + bcrypt | latest | JWT + API keys |
| HTTP Client | httpx | latest | Async requests para ERP (Fase 2) |
| Testes | pytest + pytest-asyncio | latest | Suite completa |
| Fixtures | factory-boy | latest | Dados de teste reproduzíveis |
| DB em testes | testcontainers | latest | Postgres real em CI |
| Coverage | pytest-cov | latest | Threshold mínimo 80% |
| Lint | ruff | latest | Lint + format em uma ferramenta |
| Type check | mypy | latest | Strict mode |
| Segurança | pip-audit + bandit | latest | Vulnerabilidades e SAST |
| Containerização | Docker | 27+ | Multi-stage build, non-root user |
| CI/CD | GitHub Actions | — | Pipeline completo |
| Deploy | VPS + Docker Compose + Traefik | — | Implantado em produção (VPS 162.240.102.45) |
| Monitoring | Sentry | latest | Erros + performance (não implantado — Sprint futura) |
| APM | OpenTelemetry | latest | Traces de jobs Celery (não implantado — Sprint futura) |
| File Storage (local/prod) | MinIO | latest | S3-compatible; R2 planejado para escala |
| Web UI | Jinja2 + HTMX + Alpine.js | latest | Sprint 03 |
| Email | Resend | latest | Magic link + notificações (Sprint 03.5) |
| Pre-commit | ruff + mypy hooks | latest | Sprint 06 — obrigatório no setup |

---

## 5. Estrutura do Projeto

```
catalogflow/
├── src/
│   ├── catalogflow/
│   │   ├── __init__.py
│   │   ├── main.py                     # FastAPI app factory
│   │   │
│   │   ├── modules/                    # Domínios de negócio
│   │   │   ├── catalog/                # Processamento do catálogo PDF
│   │   │   │   ├── __init__.py
│   │   │   │   ├── models.py           # SQLAlchemy ORM models
│   │   │   │   ├── schemas.py          # Pydantic schemas (I/O)
│   │   │   │   ├── service.py          # Business logic
│   │   │   │   ├── router.py           # FastAPI router
│   │   │   │   ├── tasks.py            # Celery tasks
│   │   │   │   ├── pdf_analyzer.py     # Swatch detection, layout analysis
│   │   │   │   ├── field_injector.py   # AcroForm field placement
│   │   │   │   └── tests/
│   │   │   │       ├── __init__.py
│   │   │   │       ├── test_service.py
│   │   │   │       ├── test_pdf_analyzer.py
│   │   │   │       ├── test_field_injector.py
│   │   │   │       └── fixtures/       # PDFs de teste
│   │   │   │
│   │   │   ├── orders/                 # Extração de pedidos preenchidos
│   │   │   │   ├── __init__.py
│   │   │   │   ├── models.py
│   │   │   │   ├── schemas.py
│   │   │   │   ├── service.py
│   │   │   │   ├── router.py
│   │   │   │   ├── tasks.py
│   │   │   │   ├── extractor.py        # Lê campos AcroForm preenchidos
│   │   │   │   ├── normalizer.py       # qty__SKU__corN__TAM → OrderItem
│   │   │   │   └── tests/
│   │   │   │       ├── test_service.py
│   │   │   │       ├── test_extractor.py
│   │   │   │       └── test_normalizer.py
│   │   │   │
│   │   │   ├── romaneio/               # Geração do romaneio PDF
│   │   │   │   ├── __init__.py
│   │   │   │   ├── service.py
│   │   │   │   ├── router.py
│   │   │   │   ├── tasks.py
│   │   │   │   ├── builder.py          # PDF builder (PyMuPDF)
│   │   │   │   └── tests/
│   │   │   │       ├── test_service.py
│   │   │   │       └── test_builder.py
│   │   │   │
│   │   │   ├── stock/                  # Integração de estoque (Sprint 04)
│   │   │   │   ├── __init__.py
│   │   │   │   ├── models.py
│   │   │   │   ├── schemas.py
│   │   │   │   ├── service.py
│   │   │   │   ├── router.py
│   │   │   │   ├── tasks.py
│   │   │   │   ├── dependencies.py
│   │   │   │   ├── adapter.py          # ABC StockAdapter + dataclasses
│   │   │   │   ├── mock_adapter.py     # MockStockAdapter (demo/CI)
│   │   │   │   ├── consistem_adapter.py# ConsistemAdapter (HTTP real)
│   │   │   │   └── tests/
│   │   │   │
│   │   │   ├── reservation/            # [Fase 3] Reserva de estoque
│   │   │   │   ├── __init__.py
│   │   │   │   ├── models.py
│   │   │   │   ├── schemas.py
│   │   │   │   ├── service.py
│   │   │   │   ├── router.py
│   │   │   │   └── tests/
│   │   │   │
│   │   │   └── auth/                   # Autenticação e autorização
│   │   │       ├── __init__.py
│   │   │       ├── models.py           # Brand, ApiKey, User
│   │   │       ├── schemas.py
│   │   │       ├── service.py
│   │   │       ├── router.py
│   │   │       ├── dependencies.py     # FastAPI Depends()
│   │   │       └── tests/
│   │   │
│   │   ├── shared/                     # Código transversal
│   │   │   ├── __init__.py
│   │   │   ├── errors.py               # Exceções de domínio
│   │   │   ├── responses.py            # Envelopes de resposta padrão
│   │   │   ├── middleware.py           # RequestIdMiddleware
│   │   │   ├── jobs_router.py          # GET /api/v1/jobs/{id}
│   │   │   ├── image_fetcher.py        # Scraping AMC QRCode (UI + PDFs)
│   │   │   └── utils/                  # placeholder (vazio)
│   │   │
│   │   # Notas:
│   │   # - Validação de MIME (`%PDF` + python-magic) está embutida em
│   │   #   catalog/service.py e orders/service.py — não há shared/utils/{file,mime}.py.
│   │   # - Paginação web vive em web/data.py — não há shared/pagination.py.
│   │   #
│   │   └── infra/                      # Dependências externas
│   │       ├── __init__.py
│   │       ├── database.py             # Engine, Session, Base
│   │       ├── storage.py              # S3/R2 client wrapper
│   │       ├── cache.py                # Redis client wrapper
│   │       ├── celery_app.py           # Celery app factory
│   │       └── settings.py             # Pydantic Settings (env vars)
│   │
├── tests/
│   ├── conftest.py                     # Fixtures globais (DB, client, storage mock)
│   ├── integration/
│   │   ├── test_catalog_pipeline.py    # Upload → processamento → download
│   │   └── test_order_pipeline.py      # Upload preenchido → romaneio
│   └── e2e/
│       └── test_api_flows.py           # Full HTTP flows via httpx
│
├── migrations/
│   ├── env.py
│   └── versions/
│
├── docs/
│   ├── adr/                            # Fonte única dos ADRs (ver §3)
│   │   ├── README.md
│   │   ├── ADR-001-monolito-modular.md
│   │   ├── ADR-002-fastapi-celery.md
│   │   ├── ADR-003-postgres-redis.md
│   │   ├── ADR-004-pymupdf-license.md
│   │   ├── ADR-005-s3-storage.md
│   │   ├── ADR-006-api-versioning.md
│   │   ├── ADR-007-voronoi-zones.md
│   │   ├── ADR-008-mypy-config.md
│   │   └── ADR-009-pre-commit.md
│   ├── sprint_XX/                      # PRD + prompts por sprint
│   └── analise_estado_atual.md         # snapshot técnico
│
├── .github/
│   └── workflows/
│       └── ci.yml                      # Lint + Type + Tests + Build
│                                       # (deploy é manual; CD planejado)
│
├── docker/
│   ├── Dockerfile                      # Multi-stage, non-root
│   └── docker-compose.yml              # Dev: API + Worker + Postgres + Redis
│
├── pyproject.toml                      # Deps, ruff, mypy, pytest config
├── .env.example                        # Todas as variáveis necessárias
├── .pre-commit-config.yaml             # Hooks: ruff, mypy, conventional commits
├── spec.md                             # Este documento
├── CHANGELOG.md                        # Conventional Commits format
└── README.md
```

---

## 6. Módulos e Responsabilidades

### `catalog` — Processamento do catálogo PDF

**Responsabilidade única:** Receber um PDF visual de catálogo de moda e retornar um PDF com campos AcroForm de pedido inseridos.

**Processo interno:**
1. Validar PDF (tamanho, MIME server-side, senha, integridade)
2. Analisar estrutura: identificar páginas de produto vs editorial
3. Extrair metadados por produto: SKU, nome, preço, grade de tamanhos
4. Detectar swatches de cor (drawings vetoriais no rodapé)
5. Calcular posicionamento dos painéis de pedido (sem cobrir design)
6. Inserir campos AcroForm com nomenclatura `qty__<SKU>__cor<N>__<TAM>`
7. Inserir QR Code por produto linkando ao formulário web (opcional)
8. Salvar PDF resultante no storage
9. Persistir metadados no banco (catalog_id, n_pages, n_skus, n_fields)

**Não é responsabilidade deste módulo:** autenticação, cobrança, envio por WhatsApp.

---

### `orders` — Extração de pedidos preenchidos

**Responsabilidade única:** Receber um PDF com campos AcroForm preenchidos e retornar um OrderData estruturado.

**Processo interno:**
1. Validar PDF (mesmo processo do catalog, mais: verificar presença de /AcroForm)
2. Iterar todos os widgets de todas as páginas
3. Para cada widget com valor não-vazio: parsear `qty__<SKU>__cor<N>__<TAM>`
4. Suporte a formato legado v1: `qty__<SKU>__<TAM>` (cor1 implícito)
5. Sanitizar e validar valores (inteiros positivos)
6. Agrupar por SKU → por cor → por tamanho
7. Calcular totais (peças por SKU, total geral)
8. Retornar `OrderData` com schema canônico

**Detecção de PDF achatado (flatten):** Se o PDF não contiver `/AcroForm` ou tiver campos mas sem valores, sinalizar para pipeline de visão computacional (Fase futura). Retornar erro estruturado `{ "error": "PDF_FLATTENED", "fallback": "vision_pipeline" }`.

---

### `romaneio` — Geração do romaneio

**Responsabilidade única:** Receber `OrderData` e gerar um PDF de romaneio profissional.

**Layout do romaneio:**
- Cabeçalho: logo Oasis + "ROMANEIO DE PEDIDO" + lojista + data
- Por produto: nome, ref, preço unitário, grid cor×tamanho, subtotal
- Rodapé: total de peças, valor total, número de referências
- Paginação automática com cabeçalho repetido

**Internacionalização:** valores monetários em BRL, datas em pt-BR.

---

### `auth` — Autenticação e autorização

**Responsabilidade:** Multi-tenant. Cada `Brand` (marca de moda) tem seus
catálogos, pedidos e lojistas isolados.

**Entidades (em SQLAlchemy 2.0, persistidas via migrations 0001 e 0004):**

- `Brand` — a empresa de moda (Oasis Resortwear, etc.).
- `ApiKey` — chave SHA-256, prefixo `cf_`, para acesso programático à API
  REST (`Authorization: Bearer cf_...`). Plaintext exposto uma única vez.
- `WebUser` — operador humano (gerente comercial) com e-mail/senha;
  `role` (`operator` | `admin`) + `is_active` (aprovação manual pelo admin).
- `MagicLink` — token URL-safe de uso único (TTL 15 min) gerado por
  `secrets.token_urlsafe()`, usado para login passwordless via Resend.
- `LoginAttempt` — registro de tentativa de login (`identifier` + `success`
  + `attempted_at`) para a janela móvel de rate-limit (5 falhas em 5 min).

**Estratégia:**

- **API REST** — `Authorization: Bearer cf_<key>`. Nunca expor a key crua
  após geração; armazenamos apenas o hash SHA-256 + os primeiros 8 chars
  para identificação visual.
- **UI web** — sessão por cookie HMAC assinado (`itsdangerous`, TTL 8 h)
  amarrada a um `WebUser`. Fluxos de e-mail/senha + magic link via Resend
  (Sprint 03.5).

---

### `stock` — Integração de estoque *(Sprint 04 — entregue)*

**Responsabilidade:** Dado um `Order`, (a) consultar a disponibilidade real
por `(sku, size, color_index)` no ERP da marca e persistir o resultado em
`stock_checks` + `order_items.stock_status`/`available_qty`; (b) enviar o
pedido ao ERP por `customer_code`, persistindo em `erp_submissions` com
idempotência por `order_id` (UNIQUE).

**Adapter Pattern.** Interface única `StockAdapter` (ABC) consumida pelo
`StockService`. A implementação concreta é escolhida em **runtime** pelo
valor de `ERP_ADAPTER` (`mock` | `consistem`) — trocar entre adapters não
requer rebuild da imagem.

Assinatura real (`modules/stock/adapter.py`):

```python
class StockAdapter(ABC):
    @abstractmethod
    async def check_availability(
        self, queries: list[StockQuery]
    ) -> list[StockResult]: ...

    @abstractmethod
    async def submit_order(
        self,
        order_reference: str,
        customer_code: str,
        items: list[StockQuery],
    ) -> dict[str, Any]: ...

@dataclass(frozen=True)
class StockQuery:
    sku: str
    size: str
    color_index: int
    requested_qty: int

@dataclass(frozen=True)
class StockResult:
    sku: str
    size: str
    color_index: int
    requested_qty: int
    available_qty: int | None      # None apenas quando status == "unknown"
    status: Literal["available", "partial", "out_of_stock", "unknown"]
```

**Contrato:**

- `check_availability` é idempotente; falhas por item viram
  `status="unknown"` (nunca derrubam o batch).
- `submit_order` não é idempotente por natureza — o `erp_reference`
  devolvido é o que permite deduplicar no lado CatalogFlow.

**Adapters concretos hoje:**

- `MockStockAdapter` — distribuição determinística 70/20/10 via hash MD5;
  `submit_order` devolve `MOCK-<8 hex>`. Cobre demo, dev e CI sem rede.
- `ConsistemAdapter` — HTTP real contra Consistem da AMC Têxtil:
  `GET /saldoEstoqueAtual/{codItem}/{codNatureza}` com header `empresa`,
  fórmula contábil `estoqueAtual − estReservPedido − estReservProducao −
  estReservLotes`, paralelismo com `Semaphore(5)`. `submit_order` ainda
  é `NotImplementedError` (endpoint de criação de pedido aguarda definição
  da Oasis); `_build_cod_item` está em formato provisório `"{sku}.{size}.{color_index}"`
  e é a **única** função a alterar quando o mapeamento real chegar.

---

### `reservation` — Reserva de estoque *(Fase 3)*

**Responsabilidade:** Criar reservas otimistas no banco com TTL. Coordenar com ERP via `StockAdapter`. Confirmar ou expirar reservas.

**Race condition prevention:** `SELECT FOR UPDATE` em todas as operações de reserva. Sem lock distribuído — confiança no PostgreSQL.

---

## 7. Modelos de Dados

### Diagrama de entidades

```
Brand (1) ──── (N) ApiKey
Brand (1) ──── (N) WebUser
WebUser (1) ── (N) MagicLink
LoginAttempt  (sem FK; janela móvel para rate-limit)

Brand (1) ──── (N) Catalog
Catalog (1) ── (N) CatalogProduct
Catalog (1) ── (N) Order
Order (1) ──── (N) OrderItem
Order (1) ──── (1) Romaneio
Order (1) ──── (N) StockCheck         # Sprint 04
Order (1) ──── (1) ErpSubmission      # Sprint 04 — UNIQUE(order_id)

Job (N) ──────────── (linked to: Catalog | Order | Romaneio | StockCheck | ErpSubmission)
```

**Soft-delete (migration 0006):** `catalogs`, `orders` e `romaneios`
carregam `deleted_at` + `deleted_by` (FK → `web_users`). A UI esconde
linhas com `deleted_at IS NOT NULL`; auditoria preserva o histórico.
`catalog_products`, `order_items`, `stock_checks` e `erp_submissions`
herdam a exclusão lógica pelo pai (cascade lógico via filtro). Índices
parciais `WHERE deleted_at IS NULL` mantêm o custo das listagens
constante mesmo com volume acumulado.

### Schemas SQL (referência — Alembic gera as migrations)

```sql
-- brands
CREATE TABLE brands (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        VARCHAR(64) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    plan        VARCHAR(32) NOT NULL DEFAULT 'starter',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- api_keys
CREATE TABLE api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id    UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    name        VARCHAR(128) NOT NULL,              -- "Integração ERP", "Claude Code"
    key_hash    VARCHAR(64) NOT NULL UNIQUE,        -- SHA-256 do token
    key_prefix  VARCHAR(8)  NOT NULL,               -- Primeiros 8 chars (para identificação)
    last_used   TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- catalogs
CREATE TABLE catalogs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    collection      VARCHAR(128),                   -- "Winter 26 / MOTION"
    status          VARCHAR(32) NOT NULL DEFAULT 'pending',
    -- pending | processing | ready | error
    source_key      VARCHAR(512),                   -- S3 key do PDF original
    output_key      VARCHAR(512),                   -- S3 key do PDF editável
    n_pages         INTEGER,
    n_product_pages INTEGER,
    n_skus          INTEGER,
    n_fields        INTEGER,
    error_message   TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- catalog_products
CREATE TABLE catalog_products (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_id  UUID NOT NULL REFERENCES catalogs(id) ON DELETE CASCADE,
    sku         VARCHAR(64) NOT NULL,
    name        VARCHAR(255),
    price       NUMERIC(10,2),
    grade       VARCHAR(16),                        -- "PP-M", "PP-G", etc.
    sizes       JSONB NOT NULL,                     -- ["PP","P","M"]
    n_colors    INTEGER NOT NULL DEFAULT 1,
    swatches    JSONB DEFAULT '[]',                 -- [{fill_hex, x0, y0}]
    page_index  INTEGER NOT NULL,
    UNIQUE(catalog_id, sku, page_index)             -- mesmo SKU pode ter 2 cores em páginas diferentes
);

-- orders
CREATE TABLE orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        UUID NOT NULL REFERENCES brands(id),
    catalog_id      UUID REFERENCES catalogs(id),
    lojista_token   VARCHAR(64),                    -- identificação da lojista
    lojista_name    VARCHAR(255),
    status          VARCHAR(32) NOT NULL DEFAULT 'draft',
    -- draft | extracted | confirmed | cancelled
    source_pdf_key  VARCHAR(512),                   -- PDF preenchido recebido
    total_pecas     INTEGER,
    valor_total     NUMERIC(12,2),
    extracted_at    TIMESTAMPTZ,
    confirmed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- order_items
CREATE TABLE order_items (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id    UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    sku         VARCHAR(64) NOT NULL,
    product_name VARCHAR(255),
    color_index INTEGER NOT NULL DEFAULT 1,
    color_hex   VARCHAR(7),                         -- swatch hex para identificação
    size        VARCHAR(8) NOT NULL,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(10,2),
    -- Fase 2:
    stock_status VARCHAR(32),                       -- available | out_of_stock | partial
    available_qty INTEGER,
    UNIQUE(order_id, sku, color_index, size)
);

-- romaneios
CREATE TABLE romaneios (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id    UUID NOT NULL UNIQUE REFERENCES orders(id),
    brand_id    UUID NOT NULL REFERENCES brands(id),
    output_key  VARCHAR(512),                       -- S3 key do PDF romaneio
    generated_at TIMESTAMPTZ DEFAULT NOW()
);

-- jobs (fila de processamento assíncrono)
CREATE TABLE jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id    UUID NOT NULL REFERENCES brands(id),
    celery_id   VARCHAR(255) UNIQUE,                -- task ID do Celery
    job_type    VARCHAR(64) NOT NULL,               -- catalog.process | order.extract | romaneio.generate | stock.check | stock.submit
    entity_id   UUID,                               -- ID do catalog, order, etc.
    status      VARCHAR(32) NOT NULL DEFAULT 'pending',
    -- pending | running | success | error | retry
    progress    INTEGER DEFAULT 0,                  -- 0-100
    result      JSONB,
    error       TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- web_users (Sprint 03.5 — auth web com email+senha + magic link)
CREATE TABLE web_users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id      UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    email         VARCHAR(255) NOT NULL UNIQUE,
    name          VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255),                    -- nullable: pode logar via magic-link
    role          VARCHAR(32) NOT NULL DEFAULT 'operator',  -- operator | admin
    is_active     BOOLEAN NOT NULL DEFAULT false,  -- exige aprovação do admin
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- magic_links (token URL-safe de uso único, TTL 15 min)
CREATE TABLE magic_links (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,
    token      VARCHAR(128) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- login_attempts (rate-limit: 5 falhas em 5 min por identifier)
CREATE TABLE login_attempts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    identifier   VARCHAR(255) NOT NULL,            -- e-mail tentado (lowercase)
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    success      BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX ix_login_attempts_identifier_attempted_at
    ON login_attempts (identifier, attempted_at);

-- stock_checks (Sprint 04 — uma linha por consulta de disponibilidade)
CREATE TABLE stock_checks (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id       UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    brand_id       UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    status         VARCHAR(32) NOT NULL DEFAULT 'pending',
    -- pending | running | completed | error
    result         JSONB,                          -- snapshot completo do que o adapter devolveu
    checked_at     TIMESTAMPTZ,
    error_message  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_stock_checks_order ON stock_checks (order_id);

-- erp_submissions (Sprint 04 — UNIQUE order_id: um envio ativo por pedido)
CREATE TABLE erp_submissions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id       UUID NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
    brand_id       UUID NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    customer_code  VARCHAR(64) NOT NULL,
    status         VARCHAR(32) NOT NULL DEFAULT 'pending',
    -- pending | running | accepted | partially_accepted | rejected | error
    erp_reference  VARCHAR(128),                   -- ex.: "MOCK-a7f3e91b" ou número do Consistem
    submitted_at   TIMESTAMPTZ,
    error_message  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_erp_submissions_order ON erp_submissions (order_id);

-- Soft-delete (migration 0006) — aplicado a catalogs / orders / romaneios:
ALTER TABLE catalogs  ADD COLUMN deleted_at TIMESTAMPTZ,
                      ADD COLUMN deleted_by UUID REFERENCES web_users(id) ON DELETE SET NULL;
ALTER TABLE orders    ADD COLUMN deleted_at TIMESTAMPTZ,
                      ADD COLUMN deleted_by UUID REFERENCES web_users(id) ON DELETE SET NULL;
ALTER TABLE romaneios ADD COLUMN deleted_at TIMESTAMPTZ,
                      ADD COLUMN deleted_by UUID REFERENCES web_users(id) ON DELETE SET NULL;
-- Índices parciais para manter as listagens rápidas:
CREATE INDEX ix_catalogs_alive  ON catalogs  (brand_id, created_at) WHERE deleted_at IS NULL;
CREATE INDEX ix_orders_alive    ON orders    (brand_id, created_at) WHERE deleted_at IS NULL;
CREATE INDEX ix_romaneios_alive ON romaneios (brand_id, generated_at) WHERE deleted_at IS NULL;
```

### Schema canônico do pedido (JSON interno)

```json
{
  "order_id": "uuid",
  "catalog_id": "uuid",
  "brand_id": "uuid",
  "lojista_token": "abc-7f3e",
  "lojista_name": "Loja Moda & Arte",
  "collection": "Winter 26 / MOTION",
  "extracted_at": "2026-05-11T14:22:00-03:00",
  "source_format": "v2",
  "items": [
    {
      "sku": "0442500941-0",
      "product_name": "Vestido Joana",
      "color_index": 1,
      "color_hex": "#24151b",
      "size": "PP",
      "quantity": 2,
      "unit_price": 1598.00
    }
  ],
  "totals": {
    "total_items": 8,
    "total_pecas": 32,
    "valor_total": 51136.00,
    "n_skus": 3
  }
}
```

---

## 8. API Contract

### Autenticação

Todas as rotas da API exigem header:
```
Authorization: Bearer cf_<api_key>
```

Erros de autenticação retornam sempre `401` com envelope padrão.

### Envelope de resposta padrão

```json
{
  "success": true,
  "data": {},
  "error": null,
  "meta": {
    "request_id": "uuid",
    "timestamp": "ISO-8601"
  }
}
```

Erros:
```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "CATALOG_TOO_LARGE",
    "message": "PDF excede o limite de 50MB",
    "details": {}
  },
  "meta": { "request_id": "uuid", "timestamp": "ISO-8601" }
}
```

### Endpoints — Fase 1

#### `POST /api/v1/catalogs/process`

Envia um catálogo PDF para processamento assíncrono.

**Request:** `multipart/form-data`
- `file` (required): PDF binário, max 50MB
- `name` (required): nome do catálogo (ex: "Inverno 26 MOTION")
- `collection` (optional): string identificadora da coleção

**Response 202:**
```json
{
  "data": {
    "catalog_id": "uuid",
    "job_id": "uuid",
    "status": "pending",
    "poll_url": "/api/v1/jobs/uuid"
  }
}
```

**Erros possíveis:**
- `400 INVALID_FILE_TYPE` — não é PDF
- `400 FILE_TOO_LARGE` — acima de 50MB
- `400 PDF_ENCRYPTED` — PDF protegido com senha
- `422 PDF_NO_PRODUCTS` — nenhuma página de produto detectada
- `429 RATE_LIMIT_EXCEEDED` — limite de processamento paralelo

---

#### `GET /api/v1/catalogs/{catalog_id}`

Retorna metadados e status do processamento.

**Response 200:**
```json
{
  "data": {
    "catalog_id": "uuid",
    "name": "Inverno 26 MOTION",
    "status": "ready",
    "n_pages": 70,
    "n_product_pages": 31,
    "n_skus": 36,
    "n_fields": 150,
    "download_url": "https://r2.catalogflow.com/...",
    "download_expires_at": "ISO-8601",
    "products": [
      {
        "sku": "0442500941-0",
        "name": "Vestido Joana",
        "price": 1598.00,
        "grade": "PP-G",
        "sizes": ["PP","P","M","G"],
        "n_colors": 2,
        "page_index": 7
      }
    ]
  }
}
```

---

#### `GET /api/v1/catalogs/{catalog_id}/download`

Redireciona para URL assinada do S3 (presigned URL, 1h de validade).

**Response:** `302 Found` com `Location: <presigned_url>`

---

#### `POST /api/v1/orders/extract`

Recebe um PDF preenchido e extrai o pedido.

**Request:** `multipart/form-data`
- `file` (required): PDF preenchido
- `catalog_id` (optional): UUID do catálogo de origem (para validação cruzada de SKUs)
- `lojista_name` (optional): identificação da lojista

**Response 202:**
```json
{
  "data": {
    "order_id": "uuid",
    "job_id": "uuid",
    "status": "pending",
    "poll_url": "/api/v1/jobs/uuid"
  }
}
```

---

#### `GET /api/v1/orders/{order_id}`

Retorna o pedido completo com todos os itens.

**Response 200:** `OrderData` canônico (ver seção 7)

---

#### `GET /api/v1/orders/{order_id}/romaneio`

Retorna o romaneio em PDF (gera se ainda não existir).

**Response:**
- `200` com `Content-Type: application/pdf` se já gerado
- `202` com job_id se geração em andamento

---

#### `GET /api/v1/jobs/{job_id}`

Polling de status de um job assíncrono.

**Response 200:**
```json
{
  "data": {
    "job_id": "uuid",
    "job_type": "catalog.process",
    "status": "running",
    "progress": 65,
    "entity_id": "uuid",
    "created_at": "ISO-8601",
    "updated_at": "ISO-8601",
    "result": null,
    "error": null
  }
}
```

**Quando `status == "success"`, `result` contém:**
```json
{
  "catalog_id": "uuid",
  "download_url": "https://..."
}
```

---

### Webhook (opcional, Tier Growth+)

Quando configurado, o sistema envia `POST` para a URL cadastrada com payload:

```json
{
  "event": "catalog.ready",
  "job_id": "uuid",
  "entity_id": "uuid",
  "brand_id": "uuid",
  "timestamp": "ISO-8601",
  "data": {}
}
```

Eventos: `catalog.ready`, `catalog.error`, `order.extracted`, `romaneio.ready`

Retry: até 5 tentativas com backoff exponencial. Signature HMAC-SHA256 no header `X-CatalogFlow-Signature`.

---

## 9. Pipelines de Processamento

### Pipeline 1: Catalog Processing

```
[HTTP POST /catalogs/process]
        │
        ├─ Validação síncrona (tipo, tamanho, integridade PDF)
        ├─ Upload source PDF → storage (source_key)
        ├─ Criar registro Catalog (status=pending)
        ├─ Criar Job (status=pending)
        └─ Enqueue Celery task → retornar job_id

[Celery Worker: task catalog.process]
        │
        ├─ Atualizar Job (status=running)
        ├─ Download PDF do storage
        ├─ PDFAnalyzer.analyze(pdf)
        │   ├─ Identificar páginas de produto (pdfplumber)
        │   ├─ Extrair SKUs, preços, grades
        │   ├─ Detectar swatches de cor (PyMuPDF drawings)
        │   └─ Retornar CatalogMetadata
        │
        ├─ Persistir CatalogProducts no banco
        │
        ├─ FieldInjector.inject(pdf, metadata)
        │   ├─ Por página de produto:
        │   │   ├─ Calcular posição do painel
        │   │   ├─ Desenhar painel visual (draw_rect, insert_text)
        │   │   └─ Inserir widgets AcroForm por (SKU × cor × tamanho)
        │   └─ Retornar PDF modificado
        │
        ├─ Upload output PDF → storage (output_key)
        ├─ Atualizar Catalog (status=ready, n_fields=X)
        ├─ Atualizar Job (status=success, result={...})
        └─ Disparar webhook se configurado
```

### Pipeline 2: Order Extraction

```
[HTTP POST /orders/extract]
        │
        ├─ Validação síncrona
        ├─ Upload filled PDF → storage
        ├─ Criar Order (status=draft)
        ├─ Criar Job
        └─ Enqueue Celery task → retornar job_id

[Celery Worker: task order.extract]
        │
        ├─ Download PDF do storage
        ├─ OrderExtractor.extract(pdf)
        │   ├─ Iterar todos os widgets de todas as páginas
        │   ├─ Filtrar campo_name não-vazio e valor positivo
        │   ├─ Parsear: qty__SKU__corN__TAM (v2) ou qty__SKU__TAM (v1)
        │   ├─ Detectar flatten (sem AcroForm) → sinalizar fallback
        │   └─ Retornar RawOrderData
        │
        ├─ OrderNormalizer.normalize(raw_data, catalog_products)
        │   ├─ Cruzar SKUs com catalog_products (quando catalog_id fornecido)
        │   ├─ Enriquecer com nome, preço, swatch hex
        │   ├─ Calcular totais
        │   └─ Retornar OrderData canônico
        │
        ├─ Persistir OrderItems no banco
        ├─ Atualizar Order (status=extracted, total_pecas=X)
        └─ Atualizar Job (status=success)
```

### Pipeline 3: Romaneio Generation

```
[HTTP GET /orders/{id}/romaneio ou trigger automático]
        │
        └─ Enqueue Celery task → retornar job_id

[Celery Worker: task romaneio.generate]
        │
        ├─ Carregar OrderData completo do banco
        ├─ RomaneioBuilder.build(order_data, brand)
        │   ├─ Cabeçalho com logo da marca
        │   ├─ Por SKU: bloco com grid cor×tamanho
        │   ├─ Paginação automática
        │   └─ Rodapé com totais
        │
        ├─ Upload romaneio PDF → storage
        ├─ Criar Romaneio (output_key)
        └─ Atualizar Job (status=success)
```

---

## 10. Estratégia de Testes

### Princípio (Akita): todo bug corrigido ganha um teste. Sem exceções.

### Pirâmide de testes

```
         /\
        /E2E\          ← Poucos, lentos, caros. Cobrem happy paths completos.
       /──────\
      /  Integ. \      ← Médios. Testam módulo + banco + storage (mocked S3).
     /────────────\
    / Unit tests    \  ← Muitos, rápidos, baratos. Cada função, cada edge case.
   /──────────────────\
```

### Unit Tests — `tests/modules/*/tests/`

**Cobertura mínima: 80%** (configurado em `pyproject.toml`, falha o CI abaixo disso)

Cada módulo testa:
- `service.py`: toda a lógica de negócio com banco em memória (sem I/O real)
- Analisadores PDF (`pdf_analyzer`, `extractor`, `normalizer`): testados com PDFs reais de fixture
- Edge cases obrigatórios:
  - PDF achatado (flatten) → erro com fallback correto
  - PDF com 2 produtos na mesma página
  - PDF com produto de 1 cor vs 2 cores
  - Campos preenchidos com valores inválidos (texto, negativo, zero)
  - Campo v1 (legado) processado corretamente
  - PDF sem produtos → `PDF_NO_PRODUCTS`
  - PDF criptografado → `PDF_ENCRYPTED`

**Fixtures de PDF:**
- `fixtures/catalogo_1_produto_1_cor.pdf` — 1 página, simples
- `fixtures/catalogo_1_produto_2_cores.pdf` — 2 cores detectadas
- `fixtures/catalogo_2_produtos_pagina.pdf` — 2 produtos por página
- `fixtures/catalogo_pp_g.pdf` — grade PP-G (4 tamanhos)
- `fixtures/pedido_preenchido_v2.pdf` — PDF com campos preenchidos (v2)
- `fixtures/pedido_preenchido_v1.pdf` — formato legado
- `fixtures/pedido_flattened.pdf` — PDF achatado (sem AcroForm)
- `fixtures/catalogo_real_oasis.pdf` — catálogo real (não commitado, .gitignore)

### Integration Tests — `tests/integration/`

Testam o pipeline completo com banco PostgreSQL real (Testcontainers) e S3 mockado (moto):

```python
async def test_catalog_pipeline_full(db_session, s3_mock, sample_pdf):
    # 1. Upload
    catalog = await create_catalog(db_session, sample_pdf, brand_id=TEST_BRAND)
    
    # 2. Processar (roda a task Celery de forma síncrona no teste)
    await process_catalog_task(catalog.id)
    
    # 3. Verificar resultado
    catalog = await get_catalog(db_session, catalog.id)
    assert catalog.status == "ready"
    assert catalog.n_fields > 0
    
    # 4. Download
    pdf_bytes = await download_catalog(catalog.id)
    assert len(pdf_bytes) > 0
    
    # 5. Verificar campos AcroForm no PDF resultante
    doc = pymupdf.open(stream=pdf_bytes)
    widgets = [w for p in doc for w in p.widgets()]
    assert len(widgets) == catalog.n_fields
```

### E2E Tests — `tests/e2e/`

Testam a API via HTTP real com servidor FastAPI em modo test (usando `httpx.AsyncClient`):

```python
async def test_full_order_flow(client, auth_headers, sample_catalog_pdf, sample_filled_pdf):
    # Fase 1: processar catálogo
    resp = await client.post("/api/v1/catalogs/process", 
                             files={"file": sample_catalog_pdf},
                             headers=auth_headers)
    assert resp.status_code == 202
    job_id = resp.json()["data"]["job_id"]
    
    # Polling até sucesso (máx 30s)
    catalog_id = await poll_until_success(client, job_id)
    
    # Fase 2: extrair pedido
    resp = await client.post("/api/v1/orders/extract",
                             files={"file": sample_filled_pdf},
                             data={"catalog_id": catalog_id},
                             headers=auth_headers)
    assert resp.status_code == 202
    order_job_id = resp.json()["data"]["job_id"]
    order_id = await poll_until_success(client, order_job_id)
    
    # Verificar pedido
    resp = await client.get(f"/api/v1/orders/{order_id}", headers=auth_headers)
    assert resp.json()["data"]["totals"]["total_pecas"] > 0
```

### Testes de regressão

Ao corrigir qualquer bug:
1. Criar fixture que reproduz o bug
2. Escrever teste que falha antes da correção
3. Corrigir o bug
4. Confirmar que o teste passa

### Isolamento de testes — regras obrigatórias (Sprint 06)

1. Testes que dependem de variáveis de ambiente (INTERNAL_SECRET, etc.) devem
   injetá-las via monkeypatch.setenv ou override forçado no conftest.py.
   Nunca usar os.environ.setdefault() — não sobrescreve valores do CI.

2. Fixtures que criam registros com campos únicos (celery_id, etc.) devem usar
   valores gerados (str(uuid4())) para evitar UniqueViolation em re-runs.

3. O comando exato de cobertura do CI é:
   pytest tests/ src/catalogflow --cov=src/catalogflow --cov-fail-under=80
   Rodar este comando localmente antes de abrir qualquer PR.

---

## 11. Pipeline CI/CD

### GitHub Actions — `ci.yml`

Roda em: todo `push` e `pull_request` para `main` e `develop`.

```yaml
jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - setup Python 3.12
      - install dependencies (cache pip)
      - ruff check .          # lint
      - ruff format --check . # format
      - mypy src/             # type check (strict)
      - pip-audit             # vulnerabilidades em dependências
      - bandit -r src/        # SAST

  test:
    needs: quality
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
      redis:
        image: redis:7
    steps:
      - checkout
      - setup Python 3.12
      - install dependencies
      - pytest tests/ src/catalogflow --cov=src/catalogflow --cov-fail-under=80 --cov-report=xml
      - upload coverage to Codecov

  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - docker buildx build --target production .
      - docker run --rm image pytest tests/ -x -q  # smoke test na imagem
```

### Deploy

Deploy atual: manual via VPS + Docker Compose. Procedimento no README.md.
CI/CD automatizado planejado para Sprint futura.

### Branch strategy

- `main` — produção. Protegido. Merge apenas via PR aprovado + CI verde
- `develop` — staging. Integração de features
- `feature/<nome>` — features individuais (branch curta, PR pequeno)
- `fix/<nome>` — correções de bug
- `chore/<nome>` — infra, deps, docs
- Merge em main requer CI 100% verde (sem admin override a partir da Sprint 06).

### Commits: Conventional Commits

```
feat(catalog): add multi-color row support in form grid
fix(orders): handle flattened PDF without AcroForm fields
test(romaneio): add regression test for two-product page layout
chore(ci): add pip-audit to security pipeline
docs(adr): document PyMuPDF license decision
```

---

## 12. Segurança

### Camada 1 — Input validation
- MIME type detectado server-side (libmagic), não confiar no Content-Type do cliente
- Sanitização de nomes de arquivo (path traversal, caracteres especiais)
- Limite de tamanho de payload: 50MB (configurável por plano)
- PDFs processados em diretório temporário isolado (`/tmp/<uuid>/`)

### Camada 2 — Autenticação
- API Keys com hash SHA-256 (nunca armazenar plaintext)
- Prefixo `cf_` para identificação visual
- Expiração configurável
- Rate limiting por API key (Rack::Attack equivalent: slowapi)

### Camada 3 — Isolamento multi-tenant
- Todo query inclui `brand_id` no WHERE (não confiar apenas no ID da entidade)
- Nenhum endpoint público sem autenticação (exceto `/api/v1/health`)
- S3 keys incluem `brand_id/` como prefixo

### Camada 4 — Dados em repouso
- Secrets via variáveis de ambiente (nunca em código ou git)
- `.env.example` com descrição, sem valores reais
- `DATABASE_URL` e `REDIS_URL` com credenciais via secrets do deploy

### Camada 5 — Proteção contra abuse
- Rate limiting global: 100 req/min por API key
- Rate limiting de processamento: 5 jobs paralelos por brand (Starter), 20 (Growth)
- Quota de storage: 10GB/brand (Starter), 50GB (Growth)
- Jobs expiram após 24h de inatividade

### Camada 6 — Dependências
- `pip-audit` no CI (falha se houver vuln crítica ou alta)
- `Dependabot` configurado para PRs automáticos de atualização
- Dockerfile com `USER catalogflow` (non-root)

---

## 13. Requisitos Não-Funcionais

| Requisito | Meta | Medido por |
|-----------|------|------------|
| Latência da API (p95) | < 200ms para endpoints síncronos | Sentry Performance |
| Tempo de processamento — catálogo 70 páginas | < 30s | Job duration no banco |
| Tempo de processamento — extração de pedido | < 10s | Job duration no banco |
| Uptime | 99.5% monthly | UptimeRobot |
| Tamanho máximo de PDF aceito | 50MB | Validação na API |
| Retenção de arquivos | 30 dias (Starter), 90 dias (Growth) | Job de limpeza diário |
| Suporte a PDFs com | até 200 páginas | Teste de carga |
| Compatibilidade do PDF gerado | Adobe Reader, Foxit, Xodo | Smoke test manual |

---

## 14. Roadmap de Fases

### Fase 1 — MVP (8–10 semanas) ✅ Especificado aqui ✅ Implantado — https://catalogo.thiagoscutari.com.br

**Entregáveis:**
- [ ] Módulos: `catalog`, `orders`, `romaneio`, `auth`
- [ ] API completa com 7 endpoints
- [ ] Web UI básica (upload + download + status)
- [ ] Pipeline CI/CD completo
- [ ] Suite de testes ≥ 80% cobertura
- [ ] Deploy em Fly.io com domínio customizado
- [ ] Documentação: README + ADRs + OpenAPI

**Critério de aceitação da Fase 1:**
> Uma gerente comercial da Oasis Resortwear consegue fazer upload do catálogo MOTION via browser, receber o PDF editável em menos de 60 segundos, enviar para uma lojista de teste, receber de volta preenchido, fazer upload na plataforma e obter o romaneio PDF completo — sem intervenção técnica.

**Sprints concluídas:**

| Sprint | Entrega |
|--------|---------|
| 01 | Backend: catálogo PDF → AcroForm (PDFAnalyzer + FieldInjector) |
| 02 | Backend: extração de pedido → romaneio PDF |
| 03 | Interface web mobile-first + identidade Oasis |
| 03.5 | Auth email/senha + magic link Resend + aprovação admin |
| 04 | Integração ERP: MockAdapter + ConsistemAdapter (estoque) |
| Deploy | Produção VPS + Traefik + MinIO + HTTPS |
| 05 | Fix PDFAnalyzer: SKU 9 dígitos + Voronoi zones (ADR-007) |
| 06 | CI verde + lint na fonte + pre-commit (ADR-008, ADR-009) |

**Pendente Fase 1:**
- ConsistemAdapter.submit_order — aguardando endpoint da Oasis
- _build_cod_item — aguardando mapeamento real SKU → codItem

---

### Fase 2 — Integração de Estoque (6–8 semanas após Fase 1)

**Entregáveis:**
- [ ] Módulo `stock` com interface `StockAdapter`
- [ ] Adapter genérico HTTP (configurável por brand)
- [ ] Romaneio enriquecido com status de disponibilidade
- [ ] Endpoint: `POST /api/v1/orders/{id}/stock-check`
- [ ] Notificação por webhook quando pedido verificado

---

### Fase 3 — Reserva Automática (6–8 semanas após Fase 2)

**Entregáveis:**
- [ ] Módulo `reservation` com SELECT FOR UPDATE
- [ ] TTL de reserva configurável (default: 48h)
- [ ] Endpoint: `POST /api/v1/orders/{id}/reserve`
- [ ] Flow de confirmação: lojista recebe link de confirmação
- [ ] Rollback automático por expiração (Celery Beat)
- [ ] Integração com ERP para débito de estoque confirmado

---

## 15. Fora de Escopo (YAGNI)

Os itens abaixo não fazem parte de nenhuma fase atual e **não devem ser implementados antecipadamente**:

- ❌ App mobile (React Native, Flutter) — web responsivo é suficiente
- ❌ Visão computacional para fotos manuscritas — Fase futura, não comprometida
- ❌ WhatsApp Business API nativa — integração futura
- ❌ Multi-idioma (i18n) — sistema é pt-BR por ora
- ❌ Marketplace de templates de catálogo — fora do produto atual
- ❌ Geração de catálogo do zero (apenas processar existente) — Fase 1
- ❌ Assinatura digital de romaneios — complexidade jurídica fora do escopo
- ❌ Integração com Shopify/VTEX/Tiny — ERP genérico cobre isso na Fase 2
- ❌ Kubernetes — Fly.io machines é suficiente até 100k req/dia
- ❌ GraphQL — REST com OpenAPI serve o caso de uso

---

## 16. Glossário

| Termo | Definição |
|-------|-----------|
| **Brand** | Empresa de moda cliente do CatalogFlow (ex: Oasis Resortwear) |
| **Lojista** | Compradora da marca (multivarcas ou monomarca) que recebe o catálogo |
| **Catálogo** | PDF visual de coleção gerado por agência de marketing |
| **PDF editável** | Catálogo processado com campos AcroForm inseridos |
| **AcroForm** | Padrão ISO 32000 de campos interativos em PDF |
| **Widget** | Termo PyMuPDF para campo AcroForm |
| **Swatch** | Quadrado colorido no rodapé de produto representando uma opção de cor |
| **Grade** | Intervalo de tamanhos disponíveis para o produto (ex: "PP-M", "PP-G") |
| **Romaneio** | Documento de resumo do pedido com quantidades, cores, tamanhos e valores |
| **SKU** | Código único do produto no sistema da marca (ex: `0442500941-0`) |
| **Field name** | Convenção: `qty__<SKU>__cor<N>__<TAM>` (v2) ou `qty__<SKU>__<TAM>` (v1 legado) |
| **Job** | Tarefa assíncrona Celery com status rastreado no banco |
| **Flatten** | PDF que foi "achatado" (impresso como PDF), perdendo os campos AcroForm |
| **ADR** | Architecture Decision Record — documento de decisão arquitetural permanente |
| **PMO** | Thiago Scutari e time — responsáveis pelo produto e aprovação de PRs |
| **Executor** | Claude Code — responsável pela implementação técnica |

---

*Este documento é um contrato vivo. Qualquer mudança de decisão arquitetural deve gerar um novo ADR ou revisão do existente. O spec.md é atualizado antes da implementação, nunca depois.*
