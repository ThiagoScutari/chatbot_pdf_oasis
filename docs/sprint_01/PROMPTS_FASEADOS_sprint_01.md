# Prompts de Execução Sprint 01 — Faseados
# Use um prompt por vez no Claude Code. Só avance para o próximo quando o anterior estiver concluído e funcionando.

---

## PROMPT 0 — Leitura e confirmação (colar PRIMEIRO)

```
Leia os seguintes arquivos na ordem e confirme o entendimento:
1. spec.md
2. docs/sprint_01/PRD_sprint_01.md
3. oasis_form_v2.py
4. CLAUDE.md

Após ler, responda com:
- Objetivo da Sprint 01 em uma frase
- Os 3 maiores riscos técnicos
- Confirme que entendeu a regra de funções puras no PDF engine

Não escreva código ainda.
```

---

## PROMPT 1 — Fase A: Fundação (colar após aprovação do Prompt 0)

```
Execute a Fase A do PRD Sprint 01. Referência: docs/sprint_01/PRD_sprint_01.md, seção "Fase A — Fundação".

Entregáveis desta fase:
1. Criar toda a estrutura de pastas conforme spec.md §5 (com __init__.py e docstrings)
2. pyproject.toml com todas as dependências do spec.md §4
3. .env.example com todas as variáveis necessárias
4. docker/Dockerfile (multi-stage, non-root user "catalogflow")
5. docker/docker-compose.yml (api + worker + postgres + redis)
6. src/catalogflow/infra/settings.py (Pydantic BaseSettings)
7. src/catalogflow/infra/database.py (SQLAlchemy async engine + session)
8. src/catalogflow/infra/cache.py (Redis async wrapper)
9. src/catalogflow/infra/storage.py (S3/R2 wrapper — upload, download, presigned_url, delete)

Regras:
- Python 3.12+ com type hints completos
- mypy strict deve passar em cada arquivo
- Não escreva lógica de negócio ainda — apenas infraestrutura
- Verifique que `docker-compose up postgres redis` levanta sem erro

Faça commit: `chore: scaffold project structure and infrastructure`
```

---

## PROMPT 2 — Fase B: Auth (colar após Fase A concluída)

```
Execute a Fase B do PRD Sprint 01. Referência: docs/sprint_01/PRD_sprint_01.md, seção "Fase B — Auth".

Entregáveis:
1. Alembic init + configuração async em migrations/env.py
2. Migration 001: tabelas brands + api_keys (SQL exato no spec.md §7)
3. auth/models.py — Brand + ApiKey (SQLAlchemy 2.0)
4. auth/service.py — create_brand, create_api_key (SHA-256 hash), verify_api_key
5. auth/dependencies.py — get_current_brand() como FastAPI Depends
6. auth/router.py — rotas internas: POST /internal/brands, POST /internal/brands/{id}/api-keys
7. auth/tests/test_service.py — criar brand, criar key, verificar hash, key inválida, expiração
8. auth/tests/test_dependencies.py — header válido, inválido, ausente → 401
9. Script de seed: src/catalogflow/scripts/seed_dev.py (cria brand "oasis" + imprime API key)

Regras:
- API key nunca exposta em plaintext após criação (retornar uma única vez)
- Prefixo `cf_` em toda API key gerada
- Testes usando banco Postgres real (testcontainers, não SQLite)

Faça commit: `feat(auth): add brand and api key authentication module`
```

---

## PROMPT 3 — Fase C: App principal (colar após Fase B)

```
Execute a Fase C do PRD Sprint 01.

Entregáveis:
1. src/catalogflow/main.py — create_app() factory com lifespan (testar DB + Redis no startup)
2. shared/errors.py — exceções de domínio (NotFoundError, PDFEncryptedError, etc.)
3. shared/responses.py — envelope de resposta padrão (success, data, error, meta)
4. Registrar auth/router.py no app
5. GET /api/v1/health → {"status": "ok", "db": "ok", "redis": "ok"}
6. Exception handlers globais: domínio → 4xx, inesperado → 500 com request_id
7. Middleware: request_id (UUID por request), CORS configurável

Teste: `docker-compose up` e depois `curl http://localhost:8000/api/v1/health` retorna 200.

Faça commit: `feat: add FastAPI app factory with health check and error handling`
```

---

## PROMPT 4 — Fase D parte 1: Catalog models + PDF engine (colar após Fase C)

```
Execute a primeira parte da Fase D do PRD Sprint 01.

Entregáveis:
1. Alembic migration 002: tabelas catalogs, catalog_products, jobs (SQL do spec.md §7)
2. catalog/models.py — Catalog, CatalogProduct (SQLAlchemy 2.0)
3. catalog/schemas.py — todos os Pydantic schemas (request + response)
4. tests/fixtures/generate_fixtures.py — gerar PDFs mínimos de teste programaticamente:
   - 1 produto 1 cor, 1 produto 2 cores, 2 produtos por página, grade PP-G, PDF sem produtos, PDF criptografado
5. catalog/pdf_analyzer.py — migrar lógica de oasis_form_v2.py:
   - Classe PDFAnalyzer com analyze(pdf_bytes: bytes) -> CatalogMetadata
   - FUNÇÃO PURA: recebe bytes, retorna dataclass, zero I/O
   - Usar pymupdf.open(stream=pdf_bytes, filetype="pdf") — NUNCA open("arquivo.pdf")
   - Preservar lógica exata de swatch detection (threshold 92%), SKU regex, grade parsing
6. catalog/tests/test_pdf_analyzer.py — testar com todas as fixtures

Regras:
- O output de PDFAnalyzer.analyze() aplicado ao catálogo Oasis deve retornar os mesmos metadados que oasis_form_v2.py retorna
- Dataclasses: SwatchInfo, ProductPageMeta, CatalogMetadata

Faça commit: `feat(catalog): add pdf analyzer with swatch detection and product extraction`
```

---

## PROMPT 5 — Fase D parte 2: Field injector + Celery + Service (colar após Prompt 4)

```
Execute a segunda parte da Fase D.

Entregáveis:
1. catalog/field_injector.py — migrar lógica de oasis_form_v2.py:
   - Classe FieldInjector com inject(pdf_bytes: bytes, metadata: CatalogMetadata) -> bytes
   - FUNÇÃO PURA: recebe bytes + metadata, retorna bytes, zero I/O
   - Campos AcroForm com nomenclatura exata: qty__<SKU>__cor<N>__<TAM>
   - Preservar dimensões de painéis, cores, posicionamento do original
2. catalog/tests/test_field_injector.py — todos os cenários
3. infra/celery_app.py — Celery app factory com Redis broker
4. catalog/tasks.py — process_catalog_task com bind=True, max_retries=3
5. catalog/service.py — CatalogService com create_catalog, get_catalog, process_catalog, get_download_url
6. catalog/tests/test_service.py — com mocks de storage

Regras:
- O PDF gerado por FieldInjector.inject() deve ser visualmente idêntico ao OASIS_MOTION_v2_editavel.pdf
- Job status update: UPDATE ... WHERE status='pending' (evitar race condition)

Faça commit: `feat(catalog): add field injector, celery tasks, and catalog service`
```

---

## PROMPT 6 — Fase D parte 3: Router + Jobs endpoint (colar após Prompt 5)

```
Execute a terceira parte da Fase D.

Entregáveis:
1. catalog/router.py:
   - POST /api/v1/catalogs/process (multipart, retorna 202 + job_id)
   - GET /api/v1/catalogs/{catalog_id} (metadados + status)
   - GET /api/v1/catalogs/{catalog_id}/download (302 redirect para presigned URL)
2. Shared jobs router:
   - GET /api/v1/jobs/{job_id} (polling de status)
3. Registrar routers no main.py
4. catalog/tests/test_router.py — integration tests via httpx:
   - Sem auth → 401
   - Arquivo não-PDF → 400
   - Upload válido → 202
   - GET catálogo de outra brand → 404

Faça commit: `feat(catalog): add API endpoints for catalog processing`
```

---

## PROMPT 7 — Fase E + F: Esqueletos, CI, finalização (colar por último)

```
Execute as Fases E e F do PRD Sprint 01.

Entregáveis:
1. orders/ — esqueleto: models.py, schemas.py, service.py com raise NotImplementedError("Sprint 02")
2. romaneio/ — esqueleto: idem
3. .github/workflows/ci.yml — jobs: quality (ruff + mypy), test (pytest + postgres + redis), build (docker), security (pip-audit + bandit)
4. tests/integration/test_catalog_pipeline.py — pipeline completo: upload → process → download → verificar AcroForm
5. tests/e2e/test_api_flows.py — flow HTTP completo via httpx
6. README.md — setup em 5 minutos (clone → docker-compose up → seed → curl)
7. CHANGELOG.md — entry Sprint 01

Verificação final:
- [ ] `docker-compose up` funciona
- [ ] `pytest tests/ --cov=src --cov-fail-under=80` passa
- [ ] `ruff check . && mypy src/` sem erros
- [ ] Upload do catálogo Oasis real → PDF editável funcional

Faça commit: `feat: complete Sprint 01 — foundation + catalog module`
Crie o PR com description listando todos os ACs do PRD.
```

---

# Resumo da sequência

| Prompt | Fase | Escopo | Commit |
|--------|------|--------|--------|
| 0 | Leitura | Confirmar entendimento | nenhum |
| 1 | A | Estrutura + infra | `chore: scaffold...` |
| 2 | B | Auth completo | `feat(auth): ...` |
| 3 | C | App + health | `feat: add FastAPI...` |
| 4 | D.1 | Models + Analyzer | `feat(catalog): pdf analyzer...` |
| 5 | D.2 | Injector + Service | `feat(catalog): field injector...` |
| 6 | D.3 | Router + endpoints | `feat(catalog): API endpoints...` |
| 7 | E+F | Esqueletos + CI + README | `feat: complete Sprint 01` |
