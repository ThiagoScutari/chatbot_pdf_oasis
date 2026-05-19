# ADR-002: FastAPI + Celery (não Django, não Flask)

**Status:** Vigente
**Data:** 2026-05-11

## Contexto

Processamento de PDF é lento (2–15 s por catálogo de 70 páginas). Clientes
não podem esperar numa request HTTP síncrona.

## Decisão

FastAPI para a camada HTTP (async nativo, Pydantic v2, OpenAPI automático).
Celery + Redis para jobs assíncronos. Workers separados do servidor web.

## Consequências

- Endpoints de processamento retornam `job_id` imediatamente.
- Cliente faz polling em `GET /api/v1/jobs/{job_id}` ou recebe webhook.
- Workers escalam horizontalmente sem modificar a API.
