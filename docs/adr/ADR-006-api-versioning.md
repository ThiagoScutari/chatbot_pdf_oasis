# ADR-006: Versionamento de API com prefixo `/api/v1/`

**Status:** Vigente
**Data:** 2026-05-11

## Contexto

API pública que clientes vão integrar. Precisamos evoluir sem quebrar
contratos.

## Decisão

Todas as rotas públicas sob `/api/v1/`. Quando uma v2 for necessária,
`/api/v2/` coexiste. Versão anterior deprecada com **6 meses de aviso**.

## Consequências

- Clientes podem migrar de v1 para v2 no seu próprio ritmo, dentro da
  janela de deprecação.
- Documentação OpenAPI separa as versões automaticamente.
