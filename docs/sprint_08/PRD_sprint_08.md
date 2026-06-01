# PRD Sprint 08 — Multi-format Catalog Analyzer

**Status:** Draft — aguardando aprovação do PMO
**Data:** 2026-06-01
**Sprint:** 08
**Branch alvo:** `feature/sprint-08-multi-format-analyzer`
**Base:** `develop`
**Duração estimada:** 7–10 dias úteis (5 fases sequenciais)
**ADR de referência:** [ADR-010](../adr/ADR-010-multi-format-catalog-support.md)
**Aprovado por:** Thiago Scutari (PMO) — _pendente_

---

## 1. Objetivo (one-liner)

Tornar o `PDFAnalyzer` agnóstico ao formato de catálogo via Strategy Pattern e
`BrandFormatProfile`, preservando comportamento bit-a-bit sobre o catálogo Oasis
MOTION em produção e habilitando suporte explícito a catálogos do tipo FERLA.

---

## 2. Contexto e motivação

Resumo executivo do problema (detalhamento completo na ADR-010):

- O `pdf_analyzer.py` é hoje monolítico e acoplado ao formato Oasis MOTION.
  Qualquer catálogo levemente diferente trava com `PDF_NO_PRODUCTS` ou degrada
  silenciosamente (`grade=PP-M`, `name=None`, `price=None`).
- Risco de produto: na demo a cliente potencial, um catálogo com diferença
  mínima quebra a venda.
- Acoplamento contraria o princípio multi-tenant da ADR-001.
- Fallbacks silenciosos já são bombas-relógio em produção para Oasis (categoria
  nova vira `name=None` sem aviso).

A Sprint 08 implementa as 4 decisões coordenadas da ADR-010:

| Decisão | Resumo |
|---|---|
| D1 | Strategy Pattern por eixo de extração (SKU, grade, preço, swatches, nome) |
| D2 | `BrandFormatProfile` em JSON versionado no código, referenciado por `brand.format_profile_id` |
| D3 | `AnalyzerWarning` estruturado em vez de fallbacks silenciosos |
| D4 | Nome extraído por posição/tipografia (não vocabulário) como default |

---

## 3. Escopo

### 3.1 Entra na sprint

- **Infraestrutura Strategy Pattern**: ABCs, registry, dataclasses de output,
  JSONSchema do profile, loader/validator de profiles.
- **Estratégias iniciais (8 ao todo):**
  - `sku/regex_hyphenated` (porta Oasis), `sku/regex_prefixed` (FERLA-like)
  - `grade/alpha_range` (porta Oasis + opção `tolerate_spaces`)
  - `price/br_currency` (porta Oasis), `price/labeled_dual` (FERLA-like)
  - `swatches/geometric_bottom` (porta Oasis com params)
  - `name/positional_title` (novo default), `name/category_vocabulary` (porta `NAME_RE`)
- **Profiles iniciais (2):** `oasis_default`, `ferla_like`
- **Modelo `AnalyzerWarning` + códigos:** `GRADE_NOT_DETECTED`,
  `NAME_NOT_DETECTED`, `PRICE_NOT_DETECTED`, `SWATCHES_NOT_DETECTED`
- **Refatoração do `pdf_analyzer.py`** para orquestrar estratégias via profile
- **Migrations Alembic:**
  - `brands.format_profile_id VARCHAR(64) NOT NULL DEFAULT 'oasis_default'`
  - `catalogs.warnings JSONB DEFAULT '[]'`
- **Atualização do serviço de catalog** para persistir warnings
- **Exposição API:** campo `warnings` adicionado ao response existente de
  `GET /api/v1/catalogs/{id}` (sem novo endpoint)
- **Suite de regressão** byte-a-byte sobre fixture Oasis
- **Fixture FERLA sintética via ReportLab**
- **Documentação:** atualização de `CLAUDE.md`, `README.md`, entrada em
  `CHANGELOG.md`

### 3.2 Fora de escopo (conforme ADR-010)

- Auto-detecção de profile no upload (sprint futura, quando houver ≥ 3 profiles)
- Fallback Vision LLM (sprint futura, pré-requisito: módulo `infra/llm/`)
- UI admin para edição de profile sem deploy
- Validação cruzada SKU/swatch/zona como discriminador anti-falso-positivo
- Banner de warnings na UI web — backend persiste e expõe via API; consumo no
  frontend fica para sprint separada de UI

---

## 4. Entregáveis

### 4.1 Estrutura de arquivos resultante

```
catalogflow/modules/catalog/
├── pdf_analyzer.py                       # ORQUESTRADOR, fica enxuto
├── field_injector.py                     # alterado para tolerar grade=None
├── models.py                             # +AnalyzerWarning, +warnings em CatalogMetadata
├── service.py                            # persiste warnings em catalogs.warnings
├── schemas.py                            # +warnings no response de GET /catalogs/{id}
├── router.py                             # inalterado (warnings vão no response existente)
├── format_profiles/
│   ├── __init__.py                       # loader + validator + cache
│   ├── schema.json                       # JSONSchema do profile
│   ├── oasis_default.json
│   └── ferla_like.json
├── strategies/
│   ├── __init__.py                       # registry público
│   ├── base.py                           # ABCs por eixo + dataclasses de output
│   ├── sku/
│   │   ├── __init__.py                   # SKU_STRATEGIES
│   │   ├── regex_hyphenated.py
│   │   └── regex_prefixed.py
│   ├── grade/
│   │   ├── __init__.py
│   │   └── alpha_range.py
│   ├── price/
│   │   ├── __init__.py
│   │   ├── br_currency.py
│   │   └── labeled_dual.py
│   ├── swatches/
│   │   ├── __init__.py
│   │   └── geometric_bottom.py
│   └── name/
│       ├── __init__.py
│       ├── positional_title.py
│       └── category_vocabulary.py
└── tests/
    ├── test_pdf_analyzer.py              # integrado (com PDF)
    ├── test_format_profiles.py           # loader, schema, defaults
    ├── strategies/
    │   ├── test_sku_strategies.py
    │   ├── test_grade_strategies.py
    │   ├── test_price_strategies.py
    │   ├── test_swatches_strategies.py
    │   └── test_name_strategies.py
    └── fixtures/
        ├── catalogo_real_oasis.pdf       # .gitignore, baseline regressão
        ├── catalogo_ferla_like.pdf       # fixture sintética FERLA-like (gerada por ReportLab)
        ├── _ferla_fixture_builder.py     # script de geração da fixture
        └── catalog_metadata_oasis_golden.json  # golden file para regressão
```

### 4.2 Mudanças no banco (Alembic)

Duas migrations sequenciais. **Ambas reversíveis** (`alembic downgrade -1`
testado no CI).

```sql
-- migration 08-01: brand format profile reference
ALTER TABLE brands
    ADD COLUMN format_profile_id VARCHAR(64) NOT NULL DEFAULT 'oasis_default';

-- migration 08-02: catalog warnings
ALTER TABLE catalogs
    ADD COLUMN warnings JSONB DEFAULT '[]';
```

### 4.3 Contratos públicos (API)

A interface pública do módulo muda em três pontos:

```python
# Antes
class PDFAnalyzer:
    def analyze(self, pdf_bytes: bytes) -> CatalogMetadata: ...

# Depois
class PDFAnalyzer:
    def analyze(
        self,
        pdf_bytes: bytes,
        profile_id: str = "oasis_default",
    ) -> CatalogMetadata: ...
```

```python
# Antes
@dataclass(frozen=True, slots=True)
class CatalogMetadata:
    n_pages: int
    n_product_pages: int
    product_pages: list[ProductPageMeta]

# Depois
@dataclass(frozen=True, slots=True)
class CatalogMetadata:
    n_pages: int
    n_product_pages: int
    product_pages: list[ProductPageMeta]
    warnings: list[AnalyzerWarning] = field(default_factory=list)  # NOVO
```

```python
# Mudança em ProductPageMeta:
# - grade: str  → grade: str | None  (era PP-M default, agora None + warning)
# - sizes: list[str]  → sizes: list[str] | None
```

**Response da API** — `GET /api/v1/catalogs/{id}` ganha campo `warnings`:

```jsonc
{
  "data": {
    "id": "uuid",
    "n_pages": 12,
    "n_product_pages": 8,
    "products": [/* ... */],
    "warnings": [                                  // NOVO — array, pode ser vazio
      {
        "code": "NAME_NOT_DETECTED",
        "severity": "warning",
        "page_index": 4,
        "sku": "0442500912-0",
        "message": "Nome do produto não pôde ser extraído da página 5",
        "detected_value": null
      }
    ]
  }
}
```

Campo é opcional no schema (default `[]`), portanto sem breaking change para
clientes existentes.

**Impacto downstream:** `field_injector.py` precisa lidar com `grade=None` /
`sizes=None`. Comportamento esperado: se o produto não tem grade, **não injeta
campos AcroForm** para esse produto e gera warning adicional
`FIELDS_NOT_INJECTED_NO_GRADE`. Isso é coberto na Fase C.

---

## 5. Fases de implementação

Cinco fases sequenciais. Cada fase vira um **prompt separado** para o Claude
Code (limite de 32k tokens por prompt). Não executar próxima fase antes do PMO
aprovar a anterior.

### Fase A — Fundação (Strategy Pattern infra) · 1–2 dias

**Entregáveis:**
- `strategies/base.py` com ABCs por eixo e dataclasses de output
- Registries em cada `strategies/<eixo>/__init__.py`
- `format_profiles/schema.json` (JSONSchema Draft 2020-12)
- `format_profiles/__init__.py` com `load_profile(id) → BrandFormatProfile`
- Testes unitários da infraestrutura (carregamento, validação, registry)

**Não toca em:** `pdf_analyzer.py`. Funciona em paralelo, sem afetar produção.

**DoD da fase:** `pytest catalogflow/modules/catalog/tests/strategies/` verde,
sem coverage de pipeline ainda.

### Fase B — Portar comportamento Oasis para estratégias (regressão zero) · 2–3 dias

**Entregáveis:**
- Estratégias que **replicam comportamento atual**:
  - `sku/regex_hyphenated.py`
  - `grade/alpha_range.py`
  - `price/br_currency.py`
  - `swatches/geometric_bottom.py`
  - `name/category_vocabulary.py`
- Profile `format_profiles/oasis_default.json`
- Refatoração do `pdf_analyzer.py`: vira orquestrador que recebe profile e
  delega cada eixo à estratégia correspondente
- **AINDA não introduzir warnings** — preservar bit-a-bit o comportamento atual
- Suite de regressão golden file: `catalogo_real_oasis.pdf` processado com
  `oasis_default` produz JSON idêntico ao golden gravado em `main`

**DoD da fase:** suite de regressão verde, **diff zero** no `CatalogMetadata`
serializado entre `main` e o branch.

### Fase C — `AnalyzerWarning` + structured observability · 1–2 dias

**Entregáveis:**
- `AnalyzerWarning` dataclass em `models.py`
- `warnings: list[AnalyzerWarning]` em `CatalogMetadata`
- Buffer de warnings no `PDFAnalyzer` durante o processamento
- Migrar fallbacks silenciosos para warnings explícitos:
  - `DEFAULT_GRADE = "PP-M"` removido — agora `grade=None` + warning
  - `name=None` agora gera warning
  - `price=None` agora gera warning
- Tratamento downstream em `field_injector.py`: produto sem grade → não injeta
  campos + warning `FIELDS_NOT_INJECTED_NO_GRADE`
- Migration Alembic 08-02 (`catalogs.warnings JSONB`)
- `catalog/service.py` persiste warnings em `catalogs.warnings`
- `catalog/schemas.py` adiciona `warnings` no response de `GET /catalogs/{id}`
- Testes unitários para cada código de warning

**DoD da fase:** golden file da Fase B **continua válido** para warnings
vazias (catálogo Oasis tem grade/preço/nome em todos os produtos). Novos
testes cobrem cenários com fallback.

### Fase D — Estratégias FERLA + profile ferla_like + positional_title · 2 dias

**Entregáveis:**
- `sku/regex_prefixed.py`
- `price/labeled_dual.py`
- `name/positional_title.py` (extração por `font_size` + bold)
- Opção `tolerate_spaces` em `grade/alpha_range.py`
- Profile `format_profiles/ferla_like.json`
- Migration Alembic 08-01 (`brands.format_profile_id`)
- Adicionar `reportlab` como dependência de desenvolvimento em `pyproject.toml`
  (group `dev`)
- Script `tests/fixtures/_ferla_fixture_builder.py` gera
  `catalogo_ferla_like.pdf` no setup do teste
- Testes unitários das novas estratégias
- Teste integrado: profile `ferla_like` processa fixture FERLA com ≥ 5 dos 7
  produtos detectados, todos com SKU e grade corretos

**DoD da fase:** `pytest catalogflow/modules/catalog/` verde, cobertura ≥ 80%,
golden Oasis continua intacto.

### Fase E — Documentação + integração CI · 1 dia

**Entregáveis:**
- `CLAUDE.md` atualizado: novo ponto sobre profiles + como adicionar profile
  novo + invariantes (`Brand.format_profile_id` nunca null)
- `README.md`: parágrafo sobre multi-format na seção de arquitetura
- `spec.md`: inclusão da ADR-010 inline
- `CHANGELOG.md`: entrada `feat(catalog): multi-format support via strategy profiles`
- CI: garantir job dedicado de regressão golden file
- Status da ADR-010: `Proposed` → `Accepted`

**DoD da fase:** PR pronto para review do PMO. Todos os critérios da seção 6
checados.

---

## 6. Definition of Done (DoD da sprint)

A sprint está pronta para merge em `develop` quando **todos** os itens abaixo
estiverem verdes:

- [ ] Todos os critérios de aceitação arquiteturais da ADR-010 atendidos
- [ ] `pytest` verde com cobertura ≥ 80% em `catalogflow/modules/catalog/`
- [ ] Cobertura ≥ 90% em `catalogflow/modules/catalog/strategies/`
- [ ] Suite de regressão golden file: zero diff entre `main` e o branch sobre
      `catalogo_real_oasis.pdf`
- [ ] Profile `ferla_like` detecta ≥ 5/7 produtos do catálogo FERLA com SKU e
      grade corretos
- [ ] `ruff check` e `ruff format --check` sem warnings
- [ ] `mypy --strict` verde
- [ ] `pip-audit` sem vulnerabilidades novas
- [ ] `pre-commit run --all-files` verde
- [ ] Migration `08-01` e `08-02` aplicadas e revertidas com sucesso em
      ambiente local
- [ ] CI no GitHub Actions verde no último push
- [ ] CLAUDE.md, README.md, spec.md, CHANGELOG.md atualizados
- [ ] ADR-010 com status `Accepted`
- [ ] PR aberto para revisão do PMO, **sem push para `main`**

---

## 7. Decisões operacionais consolidadas

As três pendências da versão Draft foram aprovadas pelo PMO em 2026-06-01:

| # | Pergunta | Decisão |
|---|---|---|
| 7.1 | Exposição de warnings via API | **A — adicionar campo `warnings` ao response existente** de `GET /api/v1/catalogs/{id}`. Campo opcional, default `[]`, sem breaking change. Sem endpoint dedicado. |
| 7.2 | Fixture FERLA — sintética ou real | **A — fixture sintética via ReportLab.** `reportlab` entra como dependência de desenvolvimento. Script `_ferla_fixture_builder.py` gera o PDF reproduzindo os padrões textuais e tipográficos do FERLA real. |
| 7.3 | Nome do profile default da Oasis | **`oasis_default`** (id no profile JSON, valor em `brand.format_profile_id`). Nome legível no JSON: `"Oasis Resortwear (default)"`. Profile é por marca, não por coleção — uma futura coleção que mude o formato ganha profile próprio. |

---

## 8. Estratégia de testes

### 8.1 Pirâmide

- **Unit (estratégias):** cada estratégia testada isoladamente, sem PDF.
  Inputs sintéticos (strings, regiões mockadas), outputs verificados.
  Cobertura mínima por estratégia: 90%.
- **Integração (analyzer):** pipeline completo com fixture PDF. Cobertura
  mínima: 80%.
- **Regressão (golden file):** `catalogo_real_oasis.pdf` → JSON serializado,
  comparado byte-a-byte com snapshot gravado.

### 8.2 Fixtures

- `catalogo_real_oasis.pdf` — **NÃO commitado** (já em `.gitignore`).
  Disponibilizado via secret no CI.
- `catalog_metadata_oasis_golden.json` — **commitado**. Atualizado apenas via
  PR explícito com aprovação do PMO.
- `catalogo_ferla_like.pdf` — **gerado via ReportLab** pelo script
  `_ferla_fixture_builder.py` em `tests/fixtures/`. O script é commitado; o
  PDF gerado pode ser commitado (estável) ou regerado em CI (decisão técnica
  na Fase D — preferência por commitar para evitar não-determinismo).

### 8.3 Política de golden file

- Diff no golden = **portão de merge fechado** até PMO aprovar explicitamente.
- Atualização do golden requer PR isolado descrevendo a mudança intencional.
- CI compara golden em todos os pushes.

---

## 9. Plano de rollback

**Em ambiente local / staging:**
```bash
alembic downgrade -1   # reverte migration 08-02
alembic downgrade -1   # reverte migration 08-01
git checkout develop
```

**Em produção (improvável — o merge só sai após PMO + CI):**

O risco operacional principal é a coluna `format_profile_id NOT NULL` com
default. Se rollback for necessário, a migration de downgrade dropa a coluna.
Brands não perdem dados. Catálogos já processados permanecem com seus
warnings em `catalogs.warnings` (a coluna pode ser dropada também sem perda
funcional).

**Comportamento de fallback durante incidentes:**

Se o profile referenciado por `brand.format_profile_id` não existir no
código (deploy parcial, etc.), o `load_profile()` levanta exceção
estruturada `BrandFormatProfileNotFoundError` em vez de fallback silencioso.
O job marca como `failed` com código claro. Operador é notificado.

---

## 10. Riscos e mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Regressão silenciosa no Oasis | Média | Alto | Golden file + CI bloqueante |
| `positional_title` falha em catálogo sem hierarquia tipográfica | Média | Médio | Fallback para warning + opt-in `category_vocabulary` |
| Profile JSON inválido carregado em runtime | Baixa | Alto | JSONSchema validado no startup, fail-fast |
| Migration sem downgrade testado | Baixa | Alto | CI testa downgrade obrigatoriamente |
| Performance degrada com múltiplas estratégias | Baixa | Médio | Benchmark antes/depois (script em `scripts/`) |
| Fixture FERLA sintética não reflete catálogo real | Média | Médio | Validação manual com PDF original durante Fase D |
| `field_injector.py` quebra com `grade=None` | Alta | Alto | Cobertura específica na Fase C com produto sem grade |
| ReportLab gera PDF não-determinístico entre execuções | Média | Médio | Definir seed/timestamp fixo no `_ferla_fixture_builder.py`; commit do PDF gerado |

---

## 11. Branch strategy e política de commits

- **Branch:** `feature/sprint-08-multi-format-analyzer`, sai de `develop`
- **Nunca:** rebase em `main` durante a sprint
- **Commits:** Conventional Commits, atômicos por fase
- **PR:** review obrigatório do PMO antes de merge em `develop`
- **CI:** verde + suite de regressão verde como portão

**Commits esperados (uma referência por fase):**

```
feat(catalog): add strategy pattern infrastructure for multi-format analyzer
refactor(catalog): port oasis baseline behavior to strategy classes
feat(catalog): introduce AnalyzerWarning for structured observability
feat(catalog): add ferla_like profile with prefixed SKU and dual price strategies
docs(adr): finalize ADR-010 to Accepted; update CLAUDE.md, README.md, spec.md
```

---

## 12. Próximos passos após aprovação deste PRD

Após o PMO aprovar este PRD, o trabalho prossegue com a geração dos prompts
faseados para o Claude Code, **um por fase**, respeitando o limite de 32k
tokens. Ordem:

1. `PROMPT_sprint_08_fase_A_foundation.md`
2. `PROMPT_sprint_08_fase_B_port_oasis_strategies.md`
3. `PROMPT_sprint_08_fase_C_analyzer_warnings.md`
4. `PROMPT_sprint_08_fase_D_ferla_strategies.md`
5. `PROMPT_sprint_08_fase_E_docs_and_ci.md`

Cada prompt contém: contexto da fase, escopo "Faça" e "Não faça", arquivos
afetados, critérios de aceitação locais, testes esperados, mensagem de commit
prevista, e a instrução final **"Não faça push — o PMO revisa antes."**

---

## 13. Referências

- [ADR-010](../adr/ADR-010-multi-format-catalog-support.md) — Decisão arquitetural
- ADR-001 — Monolito modular multi-tenant
- ADR-007 — Zonas Voronoi horizontais
- `catalogflow/modules/catalog/pdf_analyzer.py` (Sprint 07)
- `spec.md` — contrato técnico mestre
- `CLAUDE.md` — guia operacional do Claude Code
