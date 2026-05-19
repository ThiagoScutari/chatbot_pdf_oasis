# Architecture Decision Records (ADRs)

Este diretório é a **fonte única** do conteúdo dos ADRs do CatalogFlow.
O `spec.md §3` apenas indexa esses arquivos — qualquer mudança de decisão
deve ser feita aqui.

## Formato

Cada ADR segue o padrão Michael Nygard simplificado:

- **Contexto** — por que a decisão precisou ser tomada
- **Decisão** — o que foi decidido (em uma frase, idealmente)
- **Consequências** — efeitos práticos, positivos e negativos
- **Alternativas descartadas** — quando relevante
- **Última atualização** — data ISO da última revisão

ADRs nunca são apagados — quando uma decisão é revertida, criamos um novo
ADR que substitui o anterior (com link bidirecional).

## Índice

| # | Título | Status |
|---:|---|---|
| [001](ADR-001-monolito-modular.md) | Monolito modular (não microserviços) | Vigente |
| [002](ADR-002-fastapi-celery.md) | FastAPI + Celery (não Django, não Flask) | Vigente |
| [003](ADR-003-postgres-redis.md) | PostgreSQL + Redis (não SQLite) | Vigente |
| [004](ADR-004-pymupdf-license.md) | PyMuPDF (AGPL) — repositório público como conformidade | Vigente |
| [005](ADR-005-s3-storage.md) | S3-compatible storage para arquivos PDF | Vigente |
| [006](ADR-006-api-versioning.md) | Versionamento de API com prefixo `/api/v1/` | Vigente |
| [007](ADR-007-voronoi-zones.md) | Zonas de Voronoi horizontal para extração de metadados por SKU | Vigente |
| [008](ADR-008-mypy-config.md) | Mypy — `ignore_missing_imports` para libs externas, `type: ignore` nos call sites | Vigente |
| [009](ADR-009-pre-commit.md) | `pre-commit` como portão local obrigatório | Vigente |
