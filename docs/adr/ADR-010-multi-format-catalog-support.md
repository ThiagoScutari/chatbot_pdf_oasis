# ADR-010 — Suporte a catálogos multi-formato via Strategy Pattern e BrandFormatProfile

**Status:** Proposed (revisão 2 — D3 movida para ADR-011)
**Data:** 2026-06-01
**Sprint alvo:** a definir no PRD da sprint correspondente
**Relacionada:** ADR-011 (warnings estruturados — pressuposta por esta ADR)

---

## Contexto

O `PDFAnalyzer` (`catalogflow/modules/catalog/pdf_analyzer.py`) foi desenhado para o
formato visual e tipográfico do catálogo MOTION da Oasis Resortwear. Cinco eixos de
extração estão hoje hardcoded como `ClassVar`:

| Eixo     | Constante / método                         | Premissa atual                               |
|----------|--------------------------------------------|----------------------------------------------|
| SKU      | `SKU_RE = r"\b(\d{9,13}-\d)\b"`            | 9–13 dígitos + hífen + 1 dígito              |
| Grade    | `GRADE_RE` + `SIZE_MAP`                    | 6 faixas alfabéticas (PP–GG)                 |
| Preço    | `PRICE_RE = r"R\$\s*([\d.]+,\d{2})"`       | `R$` obrigatório, decimal com vírgula        |
| Swatches | `_detect_swatches`                         | Drawings retangulares na zona inferior       |
| Nome     | `NAME_RE` (10 categorias)                  | Vocabulário hardcoded de moda feminina       |

Em 2026-06-01, durante teste exploratório, o catálogo FERLA (moda masculina premium)
foi submetido ao sistema e rejeitado com `PDF_NO_PRODUCTS`. A análise técnica dos
cinco eixos identificou que:

1. **SKU**: FERLA usa `Ref: 01010012` — 8 dígitos sem hífen. O regex de SKU é
   gatekeeper único do pipeline: sem match, todas as páginas são descartadas e
   `PDFNoProductsError` é levantada (linhas 192–196 do `pdf_analyzer.py`).
2. **Grade**: FERLA usa `P - GG` com espaços ao redor do hífen — `GRADE_RE` exige
   ausência de espaços.
3. **Preço**: FERLA traz dois valores (`Atacado - 299` / `Varejo - 319`) sem prefixo
   `R$` e sem decimal.
4. **Nome**: FERLA é moda masculina ("Camisa Polo", "Camiseta", "Bermuda"). O
   `NAME_RE` cobre 10 categorias femininas — nenhuma casa.
5. **Swatches**: provavelmente passariam sem mudanças (mesma geometria — quadrados
   pequenos no rodapé). Único eixo resiliente.

Os achados expõem dois problemas estruturais:

- **Acoplamento estrutural ao formato Oasis.** O produto não consegue absorver
  variações razoáveis sem mudança de código. Contraria o princípio multi-tenant
  estabelecido na ADR-001.
- **Gap funcional na demo de produto.** Em apresentação a cliente potencial, a
  gerente comercial pode subir um catálogo com diferença mínima (coleção
  diferente, novo gênero, marca afiliada) e ver o sistema travar com erro
  500. Compromete a venda.

> **Nota sobre fallbacks silenciosos.** A análise identificou também um terceiro
> problema — degradação silenciosa via `DEFAULT_GRADE = "PP-M"` e tratamento
> `None` sem aviso para `name` e `price`. Esse problema é independente da questão
> multi-formato (afeta o Oasis mesmo isolado) e foi endereçado em ADR separada
> (**ADR-011**). Esta ADR pressupõe que a ADR-011 esteja implementada — em
> particular, os critérios de não-regressão abaixo dependem da existência do
> mecanismo de warnings estruturados.

A invariante de não-regressão sobre o catálogo MOTION em produção
(`catalogo.thiagoscutari.com.br`) é não-negociável.

---

## Decisão

A ADR estabelece **três decisões coordenadas** para tornar o `PDFAnalyzer`
agnóstico a formato preservando estabilidade do pipeline atual.

### D1 — Strategy Pattern por eixo de extração

Cada eixo de extração (SKU, grade, preço, swatches, nome) torna-se uma **estratégia
plugável**, com interface comum definida em ABCs (`strategies/base.py`).
Estratégias são implementadas como classes concretas vivendo em
`catalog/strategies/<eixo>/<nome>.py` e registradas em um `STRATEGY_REGISTRY` por
eixo.

**Contrato de cada estratégia:** input = região de texto/desenhos do PDF + parâmetros
do profile; output = dataclass específica do eixo (`SkuMatch`, `GradeMatch`,
`PriceMatch`, etc.) ou `None`. Estratégias são funções puras testáveis em
isolamento, sem dependência de PDF de fixture.

**Estratégias da sprint inicial:**

| Eixo     | Strategy ID            | Substitui / cobre                                          |
|----------|------------------------|------------------------------------------------------------|
| sku      | `regex_hyphenated`     | Porta direta do `SKU_RE` atual (Oasis)                     |
| sku      | `regex_prefixed`       | `(?:Ref\|Cód\|SKU)[:\s]+(\d{6,13})` (FERLA-like)           |
| grade    | `alpha_range`          | Porta do `SIZE_MAP` com opção `tolerate_spaces`            |
| price    | `br_currency`          | Porta do `PRICE_RE` atual                                  |
| price    | `labeled_dual`         | Captura `Atacado` + `Varejo` (rótulos configuráveis)       |
| swatches | `geometric_bottom`     | Porta do `_detect_swatches` com constantes parametrizáveis |
| name     | `positional_title`     | **Default novo** — texto de maior peso tipográfico         |
| name     | `category_vocabulary`  | Porta do `NAME_RE` atual; opt-in via profile               |

### D2 — `BrandFormatProfile` como agregador via JSON versionado em código

Cada `Brand` recebe um campo `format_profile_id` (VARCHAR). O profile é um arquivo
JSON em `catalogflow/modules/catalog/format_profiles/<id>.json`, validado contra
`schema.json` (JSONSchema Draft 2020-12).

**Estrutura mínima:**

```json
{
  "id": "oasis_default",
  "name": "Oasis Resortwear (default)",
  "version": "1.0.0",
  "strategies": {
    "sku":      { "id": "regex_hyphenated",   "params": {"pattern": "\\b(\\d{9,13}-\\d)\\b"} },
    "grade":    { "id": "alpha_range",        "params": {"patterns": ["PP-GG","PP-G","PP-M","P-GG","P-G","P-M"]} },
    "price":    { "id": "br_currency",        "params": {"require_prefix": true} },
    "swatches": { "id": "geometric_bottom",   "params": {"threshold_frac": 0.92, "max_size_pt": 45} },
    "name":     { "id": "positional_title",   "params": {} }
  }
}
```

**Profiles ficam versionados em código, não no banco.** Razões:

- Estratégias evoluem com o código — profile e código têm que ser deployados juntos.
- Profiles ficam revisáveis em PR (diff legível, code review obrigatório).
- Banco mantém apenas a referência (`brand.format_profile_id`), permitindo migração
  futura para profiles editáveis via UI sem mudança de schema.

**Profiles iniciais:**

- `oasis_default` — porta direta do comportamento atual sobre o catálogo Oasis MOTION, comportamento idêntico
- `ferla_like` — suporte a SKU prefixado, grade com espaços, preço dual

### D3 — Nome extraído por posição e tipografia (não vocabulário)

A estratégia default `positional_title` substitui o `NAME_RE` hardcoded. Dentro
da zona Voronoi do SKU (ADR-007), busca a linha de texto com maior peso
tipográfico (`font_size` máximo na zona, com tie-break em bold via `font_name`
contendo "Bold" ou "Heavy"). Heurística zero-vocabulário, funciona em qualquer
marca cujos catálogos tenham hierarquia tipográfica clara.

A estratégia `category_vocabulary` permanece disponível para marcas que prefiram
restringir o nome a categorias conhecidas — opt-in via profile.

---

## Consequências

### Positivas

- **Multi-tenant honrado.** ADR-001 sai do papel para o código. Cada marca tem seu
  profile, sem acoplamento entre marcas.
- **Testabilidade granular.** Cada estratégia é testável em isolamento com inputs
  sintéticos (texto/regiões mockados), sem necessidade de PDF de fixture.
- **Demo robusta.** Catálogo levemente diferente do esperado é processado com
  warnings (ADR-011) em vez de erro 500.
- **Open/Closed.** Adicionar marca nova é, na maioria dos casos, criar JSON.
  Adicionar formato novo é criar uma estratégia e registrá-la.

### Negativas

- **Custo inicial de fundação.** ~1 dia adicional para criar registry, ABCs e
  JSONSchema.
- **Curva de aprendizado.** Novo desenvolvedor precisa entender que "o profile da
  marca aponta para estratégias que vivem em outra pasta". Mitigação:
  documentação no CLAUDE.md e exemplo comentado.
- **Mais arquivos no módulo `catalog/`.** A árvore cresce de ~6 arquivos para ~20.

### Operacionais (banco e CI)

- **Migration Alembic:** `ALTER TABLE brands ADD COLUMN format_profile_id
  VARCHAR(64) NOT NULL DEFAULT 'oasis_default';`. Todas as brands existentes
  herdam o profile Oasis automaticamente — comportamento de processamento
  preservado bit-a-bit.
- **Suite de regressão obrigatória no CI:** fixture `catalogo_real_oasis.pdf`
  processado com profile `oasis_motion` deve produzir `CatalogMetadata`
  byte-a-byte idêntico ao comportamento de `main`. Diff = portão de merge.
- **Nova fixture:** `catalogo_ferla_like_sintetico.pdf` (gerado a partir do PDF
  FERLA exploratório, ou sinteticamente). Suite de teste do profile
  `ferla_like`.

---

## Alternativas descartadas

- **Caminho A puro (estender regex atual).** Adiciona branches no
  `pdf_analyzer.py` para acomodar FERLA. Resolve no curto prazo mas vira
  espaguete de regex a partir da 3ª marca. **Razão de descarte:** viola
  Open/Closed e cria débito técnico crescente.
- **Caminho B monolítico (1 JSON gigante por marca, sem estratégias
  decompostas).** Mais simples de mentalizar, mas duplica configuração entre
  marcas com padrões parcialmente compartilhados. **Razão de descarte:** ponto
  de inflexão de custo na 3ª marca; bugs em padrão compartilhado exigem editar
  múltiplos profiles.
- **Caminho C puro (Vision LLM como primário).** Chamar Claude Vision em toda
  página. **Razão de descarte:** latência (5–15s/página vs <1s atual), custo
  recorrente (~US$ 0,01/página) e reliability variável. Inadequado como primário.
- **Vision LLM como fallback após falha das estratégias.** Arquiteturalmente
  desejável, mas adiado. **Razão:** dependência operacional (chave de API,
  custo, observabilidade de chamadas LLM) que merece ADR própria. Ver "Out of
  scope" abaixo.

---

## Out of scope (decisões para ADRs futuras)

- **Auto-detecção de profile no upload.** No upload do PDF, o sistema testaria
  cada profile registrado, escolhendo o de melhor match. Hoje o profile é
  determinado por `brand_id` (uma marca = um profile). Auto-detecção fica para
  ADR futura quando houver ≥ 3 profiles registrados.
- **Fallback Vision LLM.** Chamar Claude Vision quando todas as estratégias do
  profile falham. Fica para ADR futura. Pré-requisito: módulo `infra/llm/` com
  observabilidade, rate limiting e budgeting.
- **Profile editável via UI admin.** Hoje profile é JSON em código (PR review).
  UI para edição sem deploy fica para fase de scaling do produto.
- **Validação cruzada SKU/swatch/zona.** Usar a presença de swatch como
  discriminador anti-falso-positivo de SKU. Útil mas adicionará complexidade —
  ADR separada.

---

## Critérios de aceitação arquitetural

A implementação desta ADR está completa quando:

- [ ] `PDFAnalyzer.analyze(pdf_bytes, profile_id: str)` aceita o parâmetro de
      profile (default vindo da brand do contexto)
- [ ] O profile `oasis_default` produz `CatalogMetadata` byte-a-byte idêntico ao
      comportamento atual sobre `catalogo_real_oasis.pdf` (presumindo ADR-011
      implementada — emitir warnings de degradação não conta como diff)
- [ ] O profile `ferla_like` processa o catálogo FERLA exploratório com ≥ 5 dos
      7 produtos detectados, todos com SKU e grade corretos
- [ ] Cobertura de teste por estratégia ≥ 90% (testes puros sem PDF)
- [ ] Cobertura de teste integrada do pipeline ≥ 80% (com fixture PDF)
- [ ] Migration Alembic aplicada e reversível (`alembic downgrade -1` funciona)
- [ ] `CLAUDE.md` atualizado com o ponto "como adicionar profile novo"
- [ ] `CHANGELOG.md` com entrada `feat(catalog): multi-format support via strategy profiles`
- [ ] Branch isolada (`feature/sprint-XX-multi-format-analyzer`) com CI verde

---

## Referências

- ADR-001 — Monolito modular multi-tenant
- ADR-007 — Zonas Voronoi horizontais para extração por SKU
- **ADR-011 — Warnings estruturados (pressuposta por esta ADR)**
- `catalogflow/modules/catalog/pdf_analyzer.py` (Sprint 07)
- `docs/exploratory_fixtures/ferla_sample_notes.md` (a criar antes da Sprint)
- Análise técnica dos 5 eixos: registrada em conversa de design 2026-06-01
