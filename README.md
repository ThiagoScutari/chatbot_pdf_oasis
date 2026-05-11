# CatalogFlow

> **Status:** Sprint 01 / Foundation — em construção.

B2B SaaS que transforma catálogos PDF visuais de moda em instrumentos
interativos de captura de pedido e extrai pedidos preenchidos em romaneios
estruturados.

Ver `spec.md` para o contrato técnico completo e `docs/sprint_01/` para o
escopo ativo da sprint.

## Setup local

```bash
cp .env.example .env
docker compose -f docker/docker-compose.yml up
```

Endpoints disponíveis quando o stack está no ar:

- API: <http://localhost:8000>
- Flower (Celery monitoring): <http://localhost:5555>
- MinIO console: <http://localhost:9001>
- Postgres: `localhost:5432`
- Redis: `localhost:6379`

Documentação detalhada de setup chega ao final da Sprint 01 (Prompt 7).
