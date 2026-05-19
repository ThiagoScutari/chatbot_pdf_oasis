# PRD Sprint 06 — CI Verde + Lint na Fonte + Pre-commit + Spec Atualizado

> **Projeto:** CatalogFlow
> **Sprint:** 06 / CI Health + Documentação
> **Status:** Aprovação Pendente
> **Data:** 2026-05-18
> **PMO:** Thiago Scutari
> **Executor:** Claude Code
> **Referência obrigatória:** `spec.md`, `CLAUDE.md`

---

## Contexto

O diagnóstico da Sprint 05 revelou que o CI **nunca passou em main** desde que os
gates foram adicionados. Todo merge anterior foi feito via admin override. Esta sprint
tem dois objetivos: deixar o CI 100% verde de forma sustentável, corrigindo os
problemas **na fonte** — não suprimindo-os — e atualizar o `spec.md` para refletir
o estado real do projeto após 6 sprints de implementação.

---

## Sumário Executivo

| ID | Severidade | Descrição | Esforço |
|----|-----------|-----------|---------|
| S06-01 | 🔴 Crítico | 4 testes falhando no CI | Baixo–Médio |
| S06-02 | 🔴 Crítico | Cobertura 65% no CI (alvo ≥ 80%) | Alto |
| S06-03 | 🟡 Médio | Bandit B324 — `# nosec` na linha errada | Trivial |
| S06-04 | 🟡 Médio | Ruff lint corrigido na fonte (remover ignores) | Médio |
| S06-05 | 🟡 Médio | Mypy corrigido na fonte (remover overrides do nosso código) | Alto |
| S06-06 | 🟢 Baixo | Pre-commit hooks instalados e funcionando | Baixo |
| S06-07 | 🟢 Baixo | `spec.md` atualizado para refletir o estado real do projeto | Médio |

**Sem migrations de banco.** Nenhuma mudança de API ou comportamento de produto.

---

## S06-01 — 4 testes falhando no CI

### Evidência

```
FAIL tests/integration/test_app.py::TestValidationErrorHandler
     ::test_invalid_payload_returns_422_envelope
     assert 401 == 422

FAIL src/catalogflow/modules/auth/tests/test_dependencies.py
     ::TestInternalSecretGate::test_correct_secret_creates_brand
     assert 401 == 201

FAIL src/catalogflow/modules/auth/tests/test_dependencies.py
     ::TestInternalSecretGate::test_create_api_key_returns_raw_once
     assert 401 == 201

FAIL src/catalogflow/modules/stock/tests/test_service.py
     ::TestGetStockCheck::test_returns_latest_when_multiple_runs
     UniqueViolationError uq_jobs_celery_id
```

### Causa provável

- **Auth 401s (3 testes):** `INTERNAL_SECRET` não está definido no CI. Correção:
  injetar via `monkeypatch.setenv` ou `override_settings` na fixture — sem depender
  de variável de ambiente externa.

- **UniqueViolation (1 teste):** Fixture cria Job com `celery_id` fixo. Correção:
  usar `str(uuid4())` na fixture.

### Regra

A correção deve ser nos testes — não remover constraints do banco.

---

## S06-02 — Cobertura ≥ 80%

### Evidência

CI reporta 65.09% com `pytest tests/ src/catalogflow/modules --cov=src/catalogflow`.

### Abordagem

Identificar módulos com gap via `--cov-report=term-missing`, adicionar testes nos
módulos com maior gap priorizando branches de erro e edge cases.

### Restrições

- Não usar `# pragma: no cover` para inflar artificialmente
- Não remover código de produção
- Focar em branches não cobertas que representam risco real

---

## S06-03 — Bandit B324 (`# nosec` na linha errada)

### Correção

```python
# BEFORE (2 linhas):
# nosec B324 — md5 usado para distribuição estável, não para segurança
bucket = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % 100

# AFTER (1 linha):
bucket = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % 100  # nosec B324
```

---

## S06-04 — Ruff lint corrigido na fonte

### Meta

Após as correções, `ignore` em `[tool.ruff.lint]` deve conter **apenas `B008`**.

### Estratégia por categoria

| Regra | Estratégia |
|-------|-----------|
| `F401` | Remover imports mortos |
| `F841` | Renomear para `_` ou remover |
| `E402` | Mover imports para o topo (ou `# noqa: E402` justificado) |
| `I001` | `ruff check --fix` (automático) |
| `N818` | Renomear exceções para terminar em `Error` |
| `S324` | Adicionar `usedforsecurity=False` em MD5/SHA1 não-segurança |
| `ASYNC109` | Corrigir padrão async |
| `UP032/046/047` | `ruff check --fix` (automático) |
| `RUF*` | Corrigir manualmente por item |

---

## S06-05 — Mypy corrigido na fonte

### Meta

Remover todos os `ignore_errors = true` de módulos do nosso código.
Manter apenas `ignore_missing_imports = true` para libs externas sem stubs
(pymupdf, celery, redis, etc.).

### Estratégia

Para cada `catalogflow.*` com `ignore_errors = true`, corrigir os erros:
- `no-untyped-call` pymupdf → `# type: ignore[no-untyped-call]` no call site
- `attr-defined` Document.__iter__ → `# type: ignore[attr-defined]` no uso
- Erros legítimos no nosso código → corrigir o tipo

---

## S06-06 — Pre-commit hooks

### Entregáveis

1. Atualizar `.pre-commit-config.yaml` com ruff + mypy
2. `pre-commit install` + `pre-commit run --all-files` verde
3. Documentar em `CLAUDE.md` e `README.md`

---

## S06-07 — Atualização do `spec.md`

### Contexto

O `spec.md` foi escrito em 2026-05-11, antes de qualquer implementação. Após 6
sprints, há divergências significativas entre o documento e o código real em
produção. O spec é a **fonte de verdade** — precisa refletir o que foi construído.

### Seções que precisam de atualização

#### §3 — Decisões Arquiteturais (ADRs)

Adicionar os ADRs das sprints que não existiam no spec original:

**ADR-007: Zonas de Voronoi horizontal para extração de metadados (Sprint 05)**

```
Contexto: Catálogos podem ter N produtos por página com layouts assimétricos.
Hardcoding de posições (page_w / 2) quebra silenciosamente em layouts não previstos.

Decisão: PDFAnalyzer calcula zonas de busca dinamicamente via _assign_name_zones(),
usando pontos médios entre coordenadas X dos SKUs detectados na página como
fronteiras. Nenhum valor de posição é hardcoded.

Consequências:
- Funciona para 1, 2, 3 ou N produtos por página
- Layouts assimétricos tratados corretamente
- _assign_name_zones() é testável em isolamento, sem PDF real
- name, price, grade e swatches extraídos dentro da zona do respectivo SKU

Alternativas descartadas: page_w / 2 fixo (quebra em layouts assimétricos).
```

**ADR-008: PyMuPDF — mypy via ignore_missing_imports, não ignore_errors (Sprint 06)**

```
Contexto: PyMuPDF não tem stubs de tipo. A configuração inicial de mypy strict
gerou 164 erros de no-untyped-call, todos falsos positivos para libs externas.
A solução inicial (ignore_errors = true em 23 módulos nossos) removeu toda a
verificação de tipo do código core.

Decisão: Usar [[tool.mypy.overrides]] com ignore_missing_imports = true APENAS
para libs externas (pymupdf, celery, redis, etc.). Para código nosso que usa
pymupdf, adicionar # type: ignore[no-untyped-call] nos call sites específicos.
Isso preserva verificação de tipo para o resto do módulo.

Consequências:
- mypy ainda verifica 100% do código nosso exceto as linhas de chamada pymupdf
- Libs externas sem stubs não geram ruído
- Escopo cirúrgico: suprimir a linha, não o módulo
```

**ADR-009: pre-commit como portão local obrigatório (Sprint 06)**

```
Contexto: CI falhava sistematicamente porque ruff/mypy não eram executados
localmente antes de commitar. Cada PR exigia múltiplos commits de correção de CI.

Decisão: pre-commit hooks obrigatórios com ruff check --fix, ruff format e mypy.
Documentado em CLAUDE.md, README.md e reforçado no CLAUDE.md como "Common Mistake".

Consequências:
- Erros de lint/format/type são detectados antes do push
- CI passa na primeira tentativa em vez de exigir 5+ commits de correção
- pre-commit install é parte do onboarding obrigatório
```

#### §4 — Stack Técnico

Atualizar/adicionar as tecnologias implantadas mas ausentes do spec original:

| Camada | Tecnologia | Versão | Nota |
|--------|-----------|--------|------|
| Deploy (atual) | VPS + Docker Compose + Traefik | — | Substituiu Fly.io/Railway |
| File Storage (atual) | MinIO (S3-compatible) | latest | Dev/produção; R2 em produção futura |
| Web UI | Jinja2 + HTMX + Alpine.js | latest | Adicionado Sprint 03 |
| Email | Resend | latest | Magic link + notificações |
| Image cache | Redis (2 níveis: URL 7d + bytes 24h) | — | AMC QRCode proxy |
| ERP | ConsistemAdapter (HTTP) | — | Sprint 04; submit_order pendente |
| Pre-commit | ruff + mypy hooks | latest | Adicionado Sprint 06 |

Remover da tabela: `Fly.io ou Railway` (substituído por VPS).
Atualizar status de `Monitoring (Sentry)` e `APM (OpenTelemetry)` para
`[Sprint 04+ — não implantado]`.

#### §10 — Estratégia de Testes

Adicionar após a seção de "Testes de regressão":

**Isolamento de testes — regras obrigatórias (lição aprendida Sprint 06)**

```
1. Testes que dependem de variáveis de ambiente (INTERNAL_SECRET, DATABASE_URL,
   etc.) devem injetá-las via monkeypatch.setenv ou fixture de override_settings.
   NUNCA depender de .env do desenvolvedor — o CI não tem esse arquivo.

2. Fixtures que criam registros com campos únicos (celery_id, etc.) devem usar
   valores gerados (uuid4()) para evitar UniqueViolation em re-runs.

3. O comando de cobertura do CI é:
   pytest tests/ src/catalogflow/modules --cov=src/catalogflow --cov-fail-under=80
   Rodar exatamente este comando localmente antes de abrir PR.
```

#### §11 — Pipeline CI/CD

Atualizar para refletir o pipeline real implantado (não o especificado originalmente):

- Substituir `pytest tests/ --cov=src` pelo comando real:
  `pytest tests/ src/catalogflow/modules --cov=src/catalogflow --cov-fail-under=80`
- Remover referências a `deploy.yml` com Fly.io (não implantado)
- Adicionar nota sobre deploy manual atual na VPS
- Adicionar passo `pre-commit run --all-files` como portão local

#### §3 — Adicionados — SKU regex

Atualizar `CLAUDE.md` §"Common Mistakes to Avoid" para adicionar:

**8. SKU regex deve aceitar 9–13 dígitos antes do hífen.**
O catálogo Oasis MOTION contém SKUs com 9 dígitos (ex: `442500908-0`).
O regex correto é `r"\b(\d{9,13}-\d)\b"` — não `\d{10}` ou `\d{10,13}`.

**9. Nunca hardcodar posição de divisão de página.**
Em páginas com múltiplos produtos, usar `_assign_name_zones()` (ADR-007)
para calcular zonas dinamicamente. `page_w / 2` é hardcode inaceitável.

**10. pre-commit install é obrigatório após clonar.**
Sem isso, ruff/mypy não rodam localmente e o CI falhará no primeiro push.

#### §14 — Roadmap de Fases

Atualizar status das fases:

**Fase 1 — MVP:** marcar como ✅ implantado em produção (https://catalogo.thiagoscutari.com.br)

**Sprints concluídas (para referência):**
| Sprint | Entrega |
|--------|---------|
| 01 | Backend: catálogo PDF → AcroForm |
| 02 | Backend: extração de pedido → romaneio PDF |
| 03 | Interface web mobile-first + identidade Oasis |
| 03.5 | Modal de foto zoom + auth email/senha + magic link |
| 04 | Integração ERP: MockAdapter + ConsistemAdapter |
| Deploy | Produção na VPS com Traefik + MinIO + HTTPS |
| 05 | Fix PDFAnalyzer: SKU 9 dígitos + Voronoi zones |
| 06 | CI verde + lint na fonte + pre-commit |

### O que NÃO mudar no spec.md

- Seções §1 e §2 (visão e problema) — permanecem válidas
- §5 estrutura do projeto — reflete o real
- §6 módulos e responsabilidades — reflete o real
- §7 modelos de dados — reflete o real
- §8 API contract — reflete o real
- §9 pipelines de processamento — reflete o real (com pequena atualização no
  nome `CatalogAnalyzer` → `PDFAnalyzer`)
- §12 segurança — reflete o real
- §13 requisitos não-funcionais — reflete o real
- §15 e §16 — refletem o real

---

## Acceptance Criteria

| ID | Critério | Verificação |
|----|---------|------------|
| AC-01 | `pytest tests/ src/catalogflow/modules --cov=src/catalogflow --cov-fail-under=80` passa | CI |
| AC-02 | 0 testes falhando na suite completa | CI |
| AC-03 | `bandit -r src/` retorna exit 0 | CI |
| AC-04 | `ruff check .` retorna exit 0 com `ignore = ["B008"]` apenas | CI |
| AC-05 | `ruff format --check .` retorna exit 0 | CI |
| AC-06 | `mypy src/` retorna exit 0 sem `ignore_errors = true` em código nosso | CI |
| AC-07 | `pre-commit run --all-files` retorna exit 0 | Local |
| AC-08 | CI completo passa sem admin override | CI |
| AC-09 | `spec.md` versão atualizada para `0.2.0`, data `2026-05-18` | Revisão |
| AC-10 | ADR-007, ADR-008 e ADR-009 documentados no spec.md | Revisão |
| AC-11 | Stack técnico do spec.md reflete o que está em produção | Revisão |
| AC-12 | `CLAUDE.md` atualizado com common mistakes 8, 9 e 10 | Revisão |

---

## Definition of Done

- [ ] 4 testes corrigidos
- [ ] Cobertura ≥ 80% com o comando exato do CI
- [ ] `# nosec B324` na linha correta
- [ ] `ignore` no ruff contém apenas `B008`
- [ ] Nenhum `ignore_errors = true` em módulos do nosso código no mypy
- [ ] `.pre-commit-config.yaml` funcional + documentado
- [ ] CI 100% verde sem admin override
- [ ] `spec.md` atualizado (versão 0.2.0, ADRs 007–009, stack, testes, CI/CD, roadmap)
- [ ] `CLAUDE.md` atualizado (common mistakes 8–10)

---

## Out of Scope (esta sprint)

- ❌ Correção de comportamento de produto
- ❌ Novas features ou endpoints
- ❌ Refatoração arquitetural
- ❌ ConsistemAdapter.submit_order (aguarda Oasis)
- ❌ Upload de pedido via web / soft-delete (sprint separada)

---

## Ordem de Implementação

```
1.  Inspeção completa (PROMPT 0)
2.  S06-03 + S06-01: bandit + 4 testes (PROMPT 1)
3.  S06-04: ruff lint na fonte (PROMPT 2)
4.  S06-05: mypy na fonte (PROMPT 3)
5.  S06-02: cobertura ≥ 80% (PROMPT 4)
6.  S06-06: pre-commit hooks (PROMPT 5)
7.  S06-07: spec.md + CLAUDE.md (PROMPT 6)
8.  Suite completa + commits + CI (PROMPT 7)
```
