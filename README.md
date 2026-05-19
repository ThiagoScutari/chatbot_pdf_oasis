# CatalogFlow

> **Status:** Sprint 04 / Integração ERP (estoque + envio) — completa.

B2B SaaS que transforma catálogos PDF visuais de moda em instrumentos
interativos de captura de pedido (Sprint 01), processa pedidos preenchidos
em romaneios estruturados (Sprint 02), expõe uma interface web em
português pt-BR para a gerente comercial operar o ciclo completo sem
terminal (Sprint 03), e fecha o ciclo integrando o ERP da marca para
consulta de estoque e envio de pedidos (Sprint 04).

A fonte de verdade técnica é [`spec.md`](./spec.md). O escopo da sprint
ativa está em [`docs/sprint_04/PRD_sprint_04.md`](./docs/sprint_04/PRD_sprint_04.md).

---

## Setup local em 5 minutos

Pré-requisitos: **Docker Desktop**, **Python 3.12+** e **git**.

```bash
git clone https://github.com/ThiagoScutari/chatbot_pdf_oasis.git
cd chatbot_pdf_oasis
cp .env.example .env

# 1) Sobe o stack completo
docker compose -f docker/docker-compose.yml up -d

# 2) Roda as migrations
docker compose -f docker/docker-compose.yml exec api alembic upgrade head

# 3) Cria a brand `oasis` + uma API key de dev (a key aparece UMA vez)
docker compose -f docker/docker-compose.yml exec api \
  python -m catalogflow.scripts.seed_dev
# Copie a linha `export CATALOGFLOW_API_KEY="cf_..."`

# 4) Smoke check
curl http://localhost:8004/api/v1/health
```

### Acesso via navegador (Sprint 03)

A gerente comercial usa a UI servida no mesmo container da API:

1. Abra <http://localhost:8004/login> no celular ou no desktop.
2. Cole a `CATALOGFLOW_API_KEY` (gerada pelo `seed_dev`) no campo de login.
3. Após autenticar, o ciclo completo está disponível pelo menu:
   `Dashboard` · `Catálogos` · `Pedidos` · `Sair`.
4. Sessão dura 8 horas (cookie `cf_session` assinado HMAC, `httponly`).

A UI é Jinja2 + HTMX + Alpine.js servida pelo próprio FastAPI — sem
build step, sem porta extra. O cookie carrega a API Key assinada; cada
request da web faz chamadas internas autenticadas à API REST.

### Smoke test do upload

```bash
export CATALOGFLOW_API_KEY="cf_xxxxx"  # vindo do seed_dev

curl -X POST http://localhost:8004/api/v1/catalogs/process \
  -H "Authorization: Bearer $CATALOGFLOW_API_KEY" \
  -F "file=@example/CATÁLOGO OASIS MOTION_original.pdf" \
  -F "name=Inverno 26 MOTION" \
  -F "collection=MOTION"

# resposta 202 com { catalog_id, job_id, poll_url }

# polling
curl -H "Authorization: Bearer $CATALOGFLOW_API_KEY" \
  http://localhost:8004/api/v1/jobs/<job_id>

# quando status == "success", baixar
curl -L -H "Authorization: Bearer $CATALOGFLOW_API_KEY" \
  -o editable.pdf \
  http://localhost:8004/api/v1/catalogs/<catalog_id>/download
```

---

## Fluxo completo (Sprint 02)

Ciclo ponta a ponta: catálogo → PDF editável → preenchimento pela lojista
→ extração → romaneio. Cada passo é assíncrono (retorna `job_id` para
polling). `Bearer cf_*` em todos os endpoints.

```bash
export API="http://localhost:8004/api/v1"
export KEY="cf_xxxxx"

# ── 1) Submete o catálogo PDF visual
curl -X POST "$API/catalogs/process" \
  -H "Authorization: Bearer $KEY" \
  -F "file=@catalogo.pdf" \
  -F "name=Inverno 26 MOTION"
# → 202 { data: { catalog_id, job_id, poll_url: "/api/v1/jobs/..." } }

# ── 2) Polling do job de processamento (até status="success")
curl -H "Authorization: Bearer $KEY" "$API/jobs/$JOB_ID"

# ── 3) Download do PDF editável (com campos AcroForm injetados)
curl -L -H "Authorization: Bearer $KEY" \
  -o editavel.pdf \
  "$API/catalogs/$CATALOG_ID/download"
# → Lojista preenche os campos no Adobe Reader / Foxit / Xodo

# ── 4) Lojista devolve o PDF preenchido. A gerente faz upload:
curl -X POST "$API/orders/extract" \
  -H "Authorization: Bearer $KEY" \
  -F "file=@preenchido.pdf" \
  -F "catalog_id=$CATALOG_ID" \
  -F "lojista_name=Loja Moda e Arte"
# → 202 { data: { order_id, job_id, poll_url } }
# Sem catalog_id também funciona — items não serão enriquecidos.

# ── 5) Polling até a extração completar
curl -H "Authorization: Bearer $KEY" "$API/jobs/$ORDER_JOB_ID"

# ── 6) Pedido estruturado (items, totais, lojista)
curl -H "Authorization: Bearer $KEY" "$API/orders/$ORDER_ID"

# ── 7) Romaneio PDF.
#   - Se ainda não gerado: 202 com job_id (a geração começa em background).
#   - Quando pronto: 302 redirect para presigned URL.
curl -L -H "Authorization: Bearer $KEY" \
  -o romaneio.pdf \
  "$API/orders/$ORDER_ID/romaneio"
```

**Erros relevantes:**

- `INVALID_FILE_TYPE` (400) — arquivo não é PDF.
- `FILE_TOO_LARGE` (400) — passou de `MAX_PDF_SIZE_MB`.
- `PDF_FLATTENED` (422) — PDF veio sem `/AcroForm` (foi impresso como PDF
  em vez de "Salvar como PDF"). Erro **permanente** — Celery não tenta
  de novo.
- `CATALOG_NOT_FOUND` / `ORDER_NOT_FOUND` (404) — recurso não pertence à
  brand autenticada. Não vaza existência.

---

## Integração ERP (Sprint 04)

A partir da Sprint 04 o CatalogFlow conversa com o ERP da marca em dois
fluxos: consulta de disponibilidade de estoque e envio do pedido. A
arquitetura usa o **Adapter Pattern** — `StockAdapter` é a interface
única, implementada por dois adapters intercambiáveis em runtime.

### Configuração

Variáveis de ambiente (todas opcionais, com defaults sensatos):

```bash
ERP_ADAPTER=mock              # "mock" | "consistem"
ERP_BASE_URL=https://api.consistem.com.br
ERP_API_KEY=                  # token de autenticação do Consistem
ERP_EMPRESA=50                # código da AMC Têxtil
ERP_COD_NATUREZA=505          # natureza de estoque nacional AMC
ERP_TIMEOUT=30                # timeout do client (request individual = 3s)
```

Trocar de adapter é **uma única variável** — reiniciar o container `api`
+ `worker` re-lê settings e usa o novo backend, sem rebuild da imagem.

### Modo `mock` (default)

- Respostas **determinísticas** via hash MD5 do `(sku, size, color)`:
  ~70% available, ~20% partial, ~10% out_of_stock.
- `submit_order` aceita sempre e devolve `MOCK-<8 hex>`.
- Sem dependência de rede — ideal para demo comercial, dev local e CI.
- Mesmo input ⇒ mesmo output em todas as execuções (testes não-flaky).

### Modo `consistem`

- `check_availability` consulta
  `GET {erp_base_url}/saldoEstoqueAtual/{codItem}/{erp_cod_natureza}`
  com header `empresa={erp_empresa}`, paralelismo limitado a 5 requests
  simultâneos, timeout 3s por item.
- Fórmula contábil: `disponivel = estoqueAtual - estReservPedido -
  estReservProducao - estReservLotes`.
- Mapeamento `(sku, size, color_index) → codItem` está isolado em
  `_build_cod_item()` — formato provisório `"{sku}.{size}.{color_index}"`.
  Quando a Oasis fornecer o de-para real, apenas essa função muda.
- `submit_order` levanta `NotImplementedError` enquanto o endpoint de
  criação de pedido no Consistem não é definido pela Oasis. O job vai
  para `error` (sem retry) com mensagem clara.

### Fluxo completo (API)

```bash
export API="http://localhost:8004/api/v1"
export KEY="cf_xxxxx"

# Dispara consulta de estoque para o pedido (assíncrono)
curl -X POST -H "Authorization: Bearer $KEY" \
  "$API/orders/$ORDER_ID/stock-check"
# → 202 { data: { stock_check_id, job_id, status: "pending" } }

# Consulta o resultado quando job_id estiver success
curl -H "Authorization: Bearer $KEY" \
  "$API/orders/$ORDER_ID/stock-check"
# → 200 { data: { status: "completed", summary: {...}, items: [...] } }

# Envia o pedido ao ERP
curl -X POST -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"customer_code": "12345"}' \
  "$API/orders/$ORDER_ID/submit"
# → 202 { data: { submission_id, job_id, status: "pending" } }

# Status do envio
curl -H "Authorization: Bearer $KEY" \
  "$API/orders/$ORDER_ID/submission"
# → 200 { data: { status: "accepted", erp_reference: "MOCK-a7f3e91b" } }
```

### Pelo navegador

O detalhe do pedido em `/orders/{id}` ganha dois blocos novos
("Disponibilidade em estoque" e "Envio ao ERP") e, quando há itens
com pendência, um terceiro bloco com botão **"↓ Gerar relatório de
pendências"** — PDF on-the-fly com os itens não-atendidos e a
quantidade disponível por tamanho.

A tabela de itens, após a consulta de estoque, passa a mostrar para
cada `(sku, cor)` uma sub-linha "Disponível" com qtds por tamanho
coloridas (verde sábio = atende; âmbar = parcial; vermelho escuro =
zerado).

Os PDFs de romaneio e o relatório de pendências mostram a foto do
produto à esquerda do bloco (mesmo scraping do AMC QRCode usado
pelas thumbnails da UI). Botão **"Regenerar romaneio"** no detalhe
do pedido reprocessa o PDF com os dados atuais (ex: depois de uma
consulta de estoque) sem necessidade de mexer no banco.

### Erros relevantes

- `ORDER_NOT_FOUND` (404) — pedido inexistente ou de outra brand.
- `ORDER_ALREADY_SUBMITTED` (409) — tentou enviar um pedido que já
  está em estado terminal (`accepted` / `partially_accepted` /
  `rejected`). Se precisar reenviar, use o admin.
- `STOCK_CHECK_NOT_FOUND` (404) — `GET /stock-check` sem nenhuma
  consulta prévia para o pedido.
- `SUBMISSION_NOT_FOUND` (404) — `GET /submission` sem envio prévio.

---

## Endpoints

| Método | Caminho | Auth | Descrição |
|---|---|---|---|
| `GET` | `/api/v1/health` | público | Status + contagem de jobs pendentes por tipo |
| `POST` | `/api/v1/catalogs/process` | `Bearer cf_*` | Submete catálogo PDF (multipart) |
| `GET` | `/api/v1/catalogs/{id}` | `Bearer cf_*` | Metadados + produtos detectados |
| `GET` | `/api/v1/catalogs/{id}/download` | `Bearer cf_*` | 302 → presigned URL do PDF editável |
| `POST` | `/api/v1/orders/extract` | `Bearer cf_*` | Submete PDF preenchido para extração |
| `GET` | `/api/v1/orders/{id}` | `Bearer cf_*` | Pedido completo (items + totais) |
| `GET` | `/api/v1/orders/{id}/romaneio` | `Bearer cf_*` | 302 → romaneio quando pronto; 202 + job_id em andamento |
| `POST` | `/api/v1/orders/{id}/stock-check` | `Bearer cf_*` | Dispara consulta de estoque no ERP (Sprint 04) |
| `GET` | `/api/v1/orders/{id}/stock-check` | `Bearer cf_*` | Resultado da última consulta (summary + items) |
| `POST` | `/api/v1/orders/{id}/submit` | `Bearer cf_*` | Envia pedido ao ERP (body `{customer_code}`) |
| `GET` | `/api/v1/orders/{id}/submission` | `Bearer cf_*` | Status do envio (status + erp_reference) |
| `GET` | `/api/v1/jobs/{id}` | `Bearer cf_*` | Polling — reconhece `catalog.process`, `order.extract`, `romaneio.generate`, `stock.check`, `stock.submit` |
| `POST` | `/internal/brands` | `X-Internal-Secret` | Cria nova brand (admin) |
| `POST` | `/internal/brands/{id}/api-keys` | `X-Internal-Secret` | Cria API key (raw retornado uma única vez) |

OpenAPI completo em <http://localhost:8004/api/v1/docs> (apenas em
`ENVIRONMENT != production`).

---

## Stack local

| Serviço | Porta | Notas |
|---|---|---|
| API (FastAPI) | 8004 | `uvicorn` com hot-reload em dev (serve API REST **e** UI web) |
| Celery worker | — | Concurrency 2; queues `catalog`, `orders`, `romaneio`, `stock` |
| Celery beat | — | Scheduler (sem jobs periódicos por ora) |
| Flower | 5555 | Monitoring do Celery (dev only) |
| PostgreSQL | 5432 | `catalogflow:catalogflow@postgres:5432/catalogflow` |
| Redis | 6379 | broker (db 1), backend (db 2), cache (db 0) |
| MinIO | 9000 / 9001 | Substitui R2/S3 em dev local. Console: <http://localhost:9001> |

---

## Desenvolvimento

```bash
# Setup do venv local (opcional — testes podem rodar via docker)
python -m venv .venv
.venv\Scripts\activate              # Windows
# source .venv/bin/activate         # Linux/Mac
pip install -e ".[dev]"
pre-commit install  # obrigatório: roda ruff + mypy antes de cada commit

# Lint + format
ruff check .
ruff format .

# Type check
mypy src/

# Testes (precisa Docker rodando — testcontainers sobe Postgres efêmero)
pytest tests/ src/catalogflow/modules --cov=src/catalogflow --cov-fail-under=80

# Roda só o módulo `catalog`
pytest src/catalogflow/modules/catalog/tests/ -v

# Pula testes que precisam de DB (úteis para iteração rápida)
pytest src/catalogflow/modules/catalog/tests/test_pdf_analyzer.py \
       src/catalogflow/modules/catalog/tests/test_field_injector.py \
       --no-cov
```

### Migrations

```bash
# Aplicar migrations pendentes
alembic upgrade head

# Gerar nova migration a partir das mudanças nos modelos
alembic revision --autogenerate -m "<descrição>"

# Rollback da última
alembic downgrade -1
```

### Regenerar fixtures de teste

```bash
python tests/fixtures/generate_fixtures.py
```

---

## Estrutura

```
src/catalogflow/
├── main.py                  # create_app() factory
├── modules/
│   ├── auth/                # Brand + ApiKey + WebUser, multi-tenant
│   ├── catalog/             # Pipeline de processamento de PDF
│   ├── orders/              # Extração de pedidos + persistência
│   ├── romaneio/            # Geração do romaneio PDF (com fotos)
│   ├── stock/               # Integração ERP — Adapter Pattern (Sprint 04)
│   └── reservation/         # Esqueleto — Sprint 05+
├── shared/
│   ├── errors.py            # DomainError + subclasses
│   ├── responses.py         # StandardResponse[T] envelope
│   ├── middleware.py        # RequestIdMiddleware
│   ├── jobs_router.py       # GET /api/v1/jobs/{id}
│   └── image_fetcher.py     # AMC QRCode scraping (UI + PDFs)
├── infra/
│   ├── settings.py          # Pydantic BaseSettings
│   ├── database.py          # SQLAlchemy 2.0 async
│   ├── cache.py             # Redis async pool
│   ├── storage.py           # S3/R2 wrapper (aioboto3)
│   └── celery_app.py        # Celery factory + routing
└── scripts/
    └── seed_dev.py          # Cria brand `oasis` + API key
```

---

## Troubleshooting

| Sintoma | Causa | Resolução |
|---|---|---|
| `pytest` falha com `Cannot connect to Docker daemon` | Docker Desktop parado | Subir o Docker Desktop |
| `alembic upgrade head` falha com `gen_random_uuid() does not exist` | Postgres sem `pgcrypto` | A migration 0001 cria a extensão; verifique se conectou no banco certo |
| Endpoint retorna 401 mas a key está correta | `cache_clear` do `get_settings` não rodou após mudar `.env` | Reiniciar o container `api` |
| `python -m catalogflow.scripts.seed_dev` falha com `connection refused` | Postgres ainda não está pronto | Aguarde o healthcheck (~5s) ou rode `docker compose ps` |
| Build Docker falha em `pip install pymupdf` | Falta de libs C | A imagem `python:3.12-slim` no Dockerfile já instala `build-essential`; cheque se editou |

---

## Licença e contribuição

- Código proprietário (não publicado em PyPI).
- Decisões arquiteturais permanentes em [`docs/adr/`](./docs/adr/) (a popular).
- Contribuições seguem **Conventional Commits**: `feat(catalog):`, `fix(orders):`, `test(auth):`, `chore(ci):`, `docs(adr):`.
- Revisão obrigatória do PMO (`Thiago Scutari`) antes de merge em `main`.
