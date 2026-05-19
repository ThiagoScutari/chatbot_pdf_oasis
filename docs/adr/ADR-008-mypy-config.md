# ADR-008: Mypy — `ignore_missing_imports` para libs externas, `type: ignore` nos call sites

**Status:** Vigente
**Data:** 2026-05-18 (Sprint 06)

## Contexto

PyMuPDF não tem stubs de tipo. A configuração inicial de `mypy --strict`
causou 164 erros de `no-untyped-call` — todos falsos positivos. A solução
temporária (`ignore_errors = true` em 23 módulos) removeu toda a verificação
de tipo do código core.

## Decisão

`[[tool.mypy.overrides]]` com `ignore_missing_imports = true` **apenas para
libs externas** (`pymupdf`, `celery`, `redis`, etc.). Para arquivos nossos
com uso intensivo de pymupdf, usar diretiva de arquivo
`# mypy: disable-error-code="no-untyped-call"` no topo. Para casos pontuais,
`# type: ignore[no-untyped-call]` no call site.

## Consequências

- `mypy` verifica 100 % do código nosso, **exceto** as linhas de chamada
  pymupdf.
- Supressão cirúrgica: o arquivo ou a linha, **não** o módulo inteiro no
  `pyproject.toml`.
- Quando pymupdf publicar stubs, `warn_unused_ignores = true` avisará
  automaticamente — permitindo remover as diretivas em massa.
