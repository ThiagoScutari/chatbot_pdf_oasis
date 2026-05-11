# CatalogFlow

> **Status:** Sprint 01 / Foundation — completa.

B2B SaaS que transforma catálogos PDF visuais de moda em instrumentos
interativos de captura de pedido (Sprint 01) e processa pedidos preenchidos
em romaneios estruturados (Sprint 02).

A fonte de verdade técnica é [`spec.md`](./spec.md). O escopo da sprint
ativa está em [`docs/sprint_01/PRD_sprint_01.md`](./docs/sprint_01/PRD_sprint_01.md).

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
curl http://localhost:8000/api/v1/health
```

### Smoke test do upload

```bash
export CATALOGFLOW_API_KEY="cf_xxxxx"  # vindo do seed_dev

curl -X POST http://localhost:8000/api/v1/catalogs/process \
  -H "Authorization: Bearer $CATALOGFLOW_API_KEY" \
  -F "file=@example/CATÁLOGO OASIS MOTION_original.pdf" \
  -F "name=Inverno 26 MOTION" \
  -F "collection=MOTION"

# resposta 202 com { catalog_id, job_id, poll_url }

# polling
curl -H "Authorization: Bearer $CATALOGFLOW_API_KEY" \
  http://localhost:8000/api/v1/jobs/<job_id>

# quando status == "success", baixar
curl -L -H "Authorization: Bearer $CATALOGFLOW_API_KEY" \
  -o editable.pdf \
  http://localhost:8000/api/v1/catalogs/<catalog_id>/download
```

---

## Endpoints (Sprint 01)

| Método | Caminho | Auth | Descrição |
|---|---|---|---|
| `GET` | `/api/v1/health` | público | Status da API + dependências |
| `POST` | `/api/v1/catalogs/process` | `Bearer cf_*` | Submete catálogo PDF para processamento (multipart) |
| `GET` | `/api/v1/catalogs/{id}` | `Bearer cf_*` | Metadados + produtos detectados |
| `GET` | `/api/v1/catalogs/{id}/download` | `Bearer cf_*` | 302 → presigned URL do PDF editável |
| `GET` | `/api/v1/jobs/{id}` | `Bearer cf_*` | Polling de status de job assíncrono |
| `POST` | `/internal/brands` | `X-Internal-Secret` | Cria nova brand (admin) |
| `POST` | `/internal/brands/{id}/api-keys` | `X-Internal-Secret` | Cria API key (raw_key retornado uma única vez) |

OpenAPI completo em <http://localhost:8000/api/v1/docs> (apenas em
`ENVIRONMENT != production`).

---

## Stack local

| Serviço | Porta | Notas |
|---|---|---|
| API (FastAPI) | 8000 | `uvicorn` com hot-reload em dev |
| Celery worker | — | Concurrency 2; queues `catalog`, `orders`, `romaneio` |
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
│   ├── auth/                # Brand + ApiKey, multi-tenant
│   ├── catalog/             # Pipeline de processamento de PDF
│   ├── orders/              # Esqueleto — Sprint 02
│   ├── romaneio/            # Esqueleto — Sprint 02
│   ├── stock/               # Esqueleto — Sprint 03+ (ERP)
│   └── reservation/         # Esqueleto — Sprint 03+
├── shared/
│   ├── errors.py            # DomainError + subclasses
│   ├── responses.py         # StandardResponse[T] envelope
│   ├── middleware.py        # RequestIdMiddleware
│   └── jobs_router.py       # GET /api/v1/jobs/{id}
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
