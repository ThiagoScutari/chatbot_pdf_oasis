# ADR-009: `pre-commit` como portão local obrigatório

**Status:** Vigente
**Data:** 2026-05-18 (Sprint 06)

## Contexto

O CI falhava sistematicamente porque `ruff`/`mypy` não eram executados
localmente antes de commitar. Cada PR exigia múltiplos commits de correção
de CI, queimando minutos do GitHub Actions e poluindo o histórico.

## Decisão

`pre-commit` hooks obrigatórios com `ruff check`, `ruff format` e `mypy`.
Versões das dependências no hook **pinadas** para corresponder ao ambiente
local e evitar drift de stubs. `pre-commit install` é parte do onboarding
documentado em `CLAUDE.md` e `README.md`.

## Consequências

- Erros de lint/format/tipo detectados **antes** do push.
- CI passa na primeira tentativa.
- Histórico do `main` fica limpo (sem commits "fix CI" intermediários).
