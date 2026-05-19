# ADR-001: Monolito Modular (não microserviços)

**Status:** Vigente
**Data:** 2026-05-11

## Contexto

Todo processamento de PDF é CPU-bound. A equipe é pequena (vibe coding + AI
executor). O domínio ainda está sendo descoberto.

## Decisão

Monolito modular com separação clara de responsabilidades por domínio. Cada
módulo tem sua pasta, seus testes, seus modelos. Módulos se comunicam via
imports diretos — não via HTTP ou mensagem.

## Consequências

- Deploy simples (um único container).
- Refatoração segura (type hints + testes como rede de segurança).
- Quando um módulo justificar extração (ex.: `stock` com contrato de ERP
  estável), pode virar microserviço sem reescrever.

## Alternativas descartadas

- **Microserviços** — complexidade operacional sem benefício na fase atual.
- **Serverless** — cold start inaceitável para PDF processing e limites de
  tamanho de payload.
