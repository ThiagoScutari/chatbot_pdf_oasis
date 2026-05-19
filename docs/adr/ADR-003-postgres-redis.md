# ADR-003: PostgreSQL + Redis (não SQLite)

**Status:** Vigente
**Data:** 2026-05-11

## Contexto

Multi-tenant desde o dia 1. Múltiplos workers precisam de coordenação.
Redis já é necessário para Celery.

## Decisão

PostgreSQL para dados relacionais (`brands`, `catalogs`, `orders`, `jobs`,
etc.). Redis para fila Celery + cache de resultados. **Sem SQLite mesmo em
desenvolvimento** (parity com produção).

## Consequências

- Múltiplos workers escrevendo concorrentemente são suportados sem locks
  patológicos.
- Testes usam Postgres efêmero via `testcontainers` — mesma engine de
  produção em CI.

## Motivo de rejeitar SQLite

Múltiplos workers escrevendo concorrentemente causariam locks. Migrar de
SQLite para Postgres mid-flight é custoso.
