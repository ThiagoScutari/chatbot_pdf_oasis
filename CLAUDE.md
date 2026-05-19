# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**CatalogFlow** (codename: PDF Oasis) — B2B SaaS platform that transforms visual fashion catalog PDFs (AcroForm-free) into interactive order-capture instruments, then extracts filled orders into structured romaneio (invoice) PDFs.

**Current status:** Specification + POC phase. The `spec.md` at the root is the authoritative technical contract. No FastAPI app, database, or test suite exists yet — implementation begins from scratch following the spec.

**Active sprint:** Check `docs/sprint_XX/PRD_sprint_XX.md` for the current sprint scope and acceptance criteria. Never implement beyond the active sprint's scope.

---

## Development Commands

Once implementation starts (none of these files exist yet — create them per `spec.md §5`):

```bash
# Quality
ruff check .                               # Lint
ruff format .                              # Auto-format
mypy src/                                  # Type check (strict mode)
pip-audit                                  # Dependency vulnerability scan
bandit -r src/                             # SAST scan

# Tests
pytest tests/ --cov=src --cov-fail-under=80                     # Full suite
pytest src/catalogflow/modules/catalog/tests/ -k "test_name" -v # Single module
pytest tests/integration/ -v                                     # Integration only
pytest tests/e2e/ -v                                            # E2E only

# Database migrations
alembic upgrade head                       # Apply all pending migrations
alembic revision --autogenerate -m "desc"  # Generate new migration
alembic downgrade -1                       # Rollback last migration
alembic history                            # Show migration history

# Infrastructure (local dev)
docker-compose -f docker/docker-compose.yml up       # API + Worker + PostgreSQL 16 + Redis 7
docker-compose -f docker/docker-compose.yml up -d     # Detached mode
docker-compose -f docker/docker-compose.yml logs -f api worker  # Follow logs

# Celery worker (outside Docker, for debugging)
celery -A catalogflow.infra.celery_app worker --loglevel=info
celery -A catalogflow.infra.celery_app flower --port=5555  # Monitoring UI

# Dev seed (creates test brand + API key, prints key to stdout)
python -m catalogflow.scripts.seed_dev

# Current POC scripts (reference only — not part of final product)
python oasis_form_v2.py                    # Transform catalog PDF → editable AcroForm PDF
python oasis_romaneio.py <filled.pdf> [retailer_name]  # Extract order → generate romaneio
```

```bash
# Pre-commit (obrigatório no setup — executar UMA VEZ após clonar)
pre-commit install            # instala hooks locais
pre-commit run --all-files    # verificação manual de todos os arquivos
```

---

## Architecture

### Core Design (ADR-001 to ADR-006)

- **Monolito modular** — no microservices. Modules communicate via direct Python imports, not HTTP. Extract to microservice only when a module's contract is stable and team is larger.
- **FastAPI + Celery** — PDF processing is CPU-bound (2–15s). API endpoints return `job_id` immediately; clients poll `GET /api/v1/jobs/{job_id}` or receive webhooks. Never run PDF processing synchronously in an HTTP handler.
- **PostgreSQL + Redis always** — no SQLite even in dev/tests. Multiple Celery workers require concurrent writes. Redis doubles as Celery broker and result cache.
- **PyMuPDF (fitz)** is the primary PDF engine (AcroForm manipulation). AGPL license — requires commercial license (~US$500/yr Artifex) **before production go-live**. Fallback: PyPDFForm (MIT).
- **S3-compatible storage (Cloudflare R2)** for all PDFs. Database stores only metadata + S3 object key. Never store PDF bytes in the database.
- **All routes under `/api/v1/`**. Multi-tenant from day 1: every query filters by `brand_id`.

### Module Structure (`src/catalogflow/`)

```
main.py                 # FastAPI app factory (create_app)
modules/
  catalog/              # PDF intake, AcroForm field injection, swatch detection
    models.py           # SQLAlchemy: Catalog, CatalogProduct
    schemas.py          # Pydantic: request/response DTOs
    service.py          # Business logic (orchestrates analyzer + injector + storage)
    router.py           # FastAPI endpoints
    tasks.py            # Celery async tasks
    pdf_analyzer.py     # PURE: bytes → CatalogMetadata (no I/O)
    field_injector.py   # PURE: bytes + metadata → bytes (no I/O)
    tests/
  orders/               # AcroForm extraction, field parsing (v1 + v2 format)
  romaneio/             # Invoice PDF generation
  auth/                 # JWT + API keys (SHA-256 hash, `cf_` prefix), multi-tenant
  stock/                # [Phase 2] ERP integration via httpx
  reservation/          # [Phase 3] Stock reservation on order submit
shared/                 # Sibling of modules/ — NOT inside it
  errors.py             # Domain exceptions (NotFoundError, PDFEncryptedError, etc.)
  pagination.py         # Page, PageParams
  responses.py          # Standard response envelope
  utils/                # file sanitization, MIME detection
infra/                  # External dependencies — sibling of modules/
  database.py           # SQLAlchemy async engine + session factory + get_db()
  storage.py            # boto3/R2 client wrapper
  cache.py              # Redis async client
  celery_app.py         # Celery app factory with task routing
  settings.py           # Pydantic BaseSettings (all config via env vars)
```

### PDF Engine Functions Must Be Pure

**This is the most important architectural rule for testability.**

`pdf_analyzer.py` and `field_injector.py` must be **pure functions** — they receive `bytes`, return `bytes` or dataclasses. They never open files from disk, never write to disk, never call storage or database.

```python
# CORRECT — pure, testable, no I/O
class PDFAnalyzer:
    def analyze(self, pdf_bytes: bytes) -> CatalogMetadata: ...

class FieldInjector:
    def inject(self, pdf_bytes: bytes, metadata: CatalogMetadata) -> bytes: ...

# WRONG — file I/O inside engine
class PDFAnalyzer:
    def analyze(self, file_path: str) -> CatalogMetadata:
        doc = pymupdf.open(file_path)  # ← NEVER do this
```

All file I/O happens in `service.py`, which downloads bytes from storage, passes them to the engine, then uploads the result back.

### AcroForm Field Naming Convention

Fields inserted into catalog PDFs follow this exact pattern:
```
qty__<SKU>__cor<N>__<TAM>
```

Examples:
```
qty__0442500912-0__cor1__PP    # SKU 0442500912-0, color 1, size PP
qty__0442500912-0__cor2__M     # SKU 0442500912-0, color 2, size M
qty__0322500004-0__cor1__P     # Single-color product, still uses cor1
```

Legacy v1 format (must also be parseable by order extractor):
```
qty__0442500912-0__PP          # No color index — treat as cor1
```

**Never change this convention** — filled PDFs in the wild already use it.

### Key Processing Pipelines

1. **Catalog pipeline** (`POST /api/v1/catalogs/process`): Upload PDF → validate (MIME server-side, size ≤50MB, not encrypted) → S3 → create Catalog record (status=pending) → Celery job → PDFAnalyzer.analyze() → persist CatalogProducts → FieldInjector.inject() → output PDF to S3 → update Catalog (status=ready) → update Job (status=success).

2. **Order extraction** (`POST /api/v1/orders/extract`): Upload filled PDF → validate → S3 → create Order (status=draft) → Celery job → read AcroForm widgets → parse field names (v1 + v2) → normalize → persist OrderItems → generate Romaneio PDF → S3 → update Order (status=extracted).

### Standard Response Envelope

Every API response follows this shape:
```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "meta": { "request_id": "uuid", "timestamp": "ISO-8601" }
}
```

---

## Testing Standards

- **Coverage minimum: 80%** (enforced in CI via `--cov-fail-under=80`).
- **No SQLite in tests** — use `testcontainers` for real PostgreSQL. Mock only external services (S3 via moto, ERP via respx/httpx mock).
- **Tests live inside each module** (`modules/catalog/tests/`, not `tests/unit/catalog/`), except integration and E2E tests which live at the root `tests/` level.
- **Required test fixtures** — generate programmatically via `tests/fixtures/generate_fixtures.py` (committed as actual PDFs). Never commit real client PDFs (Oasis catalog) to the repo.
- Test pyramid: unit (service + engine logic) → integration (full pipeline, real DB, mocked S3) → E2E (HTTP via httpx AsyncClient).
- **Every bug fix gets a regression test.** No exceptions.

---

## Critical Constraints

- **PyMuPDF license**: Do not deploy to production without commercial license or switching to PyPDFForm fallback.
- **Multi-tenancy**: Every database query must include `brand_id` in WHERE clause. S3 keys must be prefixed with `brand_id/`. A request authenticated as Brand A must never access Brand B's resources — test this explicitly.
- **File validation**: Server-side MIME detection required (`python-magic`), don't trust `Content-Type`. Max upload: 50MB. Validate PDF is not encrypted before processing.
- **No synchronous PDF processing in HTTP request handlers** — always dispatch to Celery. The endpoint returns 202 with a `job_id`.
- **Alembic for all schema changes** — never alter database schema manually. Never use `Base.metadata.create_all()` in production code.
- **No `print()` in production code** — use `logging` with module-level logger: `logger = logging.getLogger(__name__)`.
- **Conventional Commits** enforced: `feat(catalog):`, `fix(orders):`, `test(auth):`, `chore(ci):`, `docs(adr):`.

---

## Common Mistakes to Avoid

1. **Opening PDF from file path instead of bytes stream.** POC scripts use `pymupdf.open("file.pdf")`. Production code must use `pymupdf.open(stream=pdf_bytes, filetype="pdf")`.

2. **Forgetting `NeedAppearances`.** When adding AcroForm widgets with PyMuPDF and saving, some viewers won't render the fields unless the document's `/AcroForm` dictionary includes `/NeedAppearances true`. PyMuPDF handles this when using `page.add_widget()`, but verify in the output.

3. **Swatch detection threshold.** The POC uses `page_height * 0.920` as the threshold for the drawing zone where color swatches appear. This works for the Oasis catalog format (1179×2556pt pages). If page dimensions differ, the threshold must be adaptive — base it on the legend text positions (use pdfplumber to find the text zone first, then scan for drawings above that zone).

4. **Celery task serialization.** Celery tasks receive `catalog_id: str` (UUID as string), not ORM objects. The task body creates its own DB session — never share sessions across task boundaries.

5. **Race condition on Job status update.** Multiple workers exist. Use `UPDATE jobs SET status='running' WHERE id=X AND status='pending'` — the `AND status='pending'` prevents two workers from picking up the same job.

6. **Presigned URL expiration.** S3 presigned URLs default to 1 hour. Never store them in the database as permanent references — generate fresh on each `GET /download` request.

7. **PDF flattening detection.** When a filled PDF arrives without `/AcroForm` in the catalog dictionary, it's been flattened (printed-to-PDF). Return `error.code = "PDF_FLATTENED"`, not a generic 500.

8. **SKU regex deve aceitar 9–13 dígitos antes do hífen.**
   O catálogo Oasis MOTION contém SKUs com 9 dígitos (ex: `442500908-0`).
   O padrão correto é `r"\b(\d{9,13}-\d)\b"` — não `\d{10}` ou `\d{10,13}`.

9. **Nunca hardcodar divisão de página para páginas multi-produto.**
   Usar `_assign_name_zones()` (ADR-007) para calcular zonas dinamicamente.
   `page_w / 2` é hardcode — quebra em layouts assimétricos e N > 2 produtos.

10. **`pre-commit install` é obrigatório após clonar o repositório.**
    Sem isso, ruff/mypy não rodam localmente e o CI falhará no primeiro push.
    Nunca commitar sem rodar `pre-commit run --all-files` localmente.

11. **Nunca usar `os.environ.setdefault()` em conftest.py para injetar secrets.**
    `setdefault` não sobrescreve variáveis já definidas — o CI define
    INTERNAL_SECRET com valor diferente do teste, causando 401 silencioso.
    Usar `os.environ["INTERNAL_SECRET"] = "test-value"` (override forçado).

---

## Reference Documents

| Document | Location | Purpose |
|----------|----------|---------|
| Technical spec | `spec.md` | **Source of truth.** Data models, SQL schemas, full API contract, CI/CD pipeline, security layers, roadmap. Read this before implementing any module. |
| Sprint PRD | `docs/sprint_XX/PRD_sprint_XX.md` | Scope, acceptance criteria, and definition of done for the active sprint. |
| Sprint execution prompt | `docs/sprint_XX/PROMPT_EXECUCAO_sprint_XX.md` | Detailed implementation instructions and ordering for the sprint. |
| PDF research | `docs/doc_pdf_editavel.md` | AcroForm vs XFA vs PDF/A, reader compatibility matrix, JavaScript limitations. |
| POC: form injector | `oasis_form_v2.py` | Working reference for swatch detection, field positioning, panel drawing. Migrate logic to `catalog/pdf_analyzer.py` + `catalog/field_injector.py`. |
| POC: romaneio | `oasis_romaneio.py` | Working reference for order extraction and romaneio PDF generation. Migrate in Sprint 02. |
| Example catalog | `example/` | Real Oasis catalog (original + editable + demo romaneio). For manual smoke testing only — never commit to CI fixtures. |
