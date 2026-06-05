# ADR-011 — `AnalyzerWarning` como observabilidade não-bloqueante para degradações do pipeline de catálogo

**Status:** Accepted (implementada na Sprint 08, Fases C–E)
**Data:** 2026-06-01
**Sprint alvo:** 08 (Fase C)
**Substitui:** —
**Substituída por:** —
**Relacionada a:** [ADR-010](./ADR-010-multi-format-catalog-support.md)

---

## Contexto

A Sprint 08, conforme [ADR-010](./ADR-010-multi-format-catalog-support.md),
torna o `PDFAnalyzer` agnóstico ao formato de catálogo via Strategy Pattern.
Durante a análise técnica dos cinco eixos de extração (SKU, grade, preço,
swatches, nome), foram identificados **fallbacks silenciosos** que existem
no comportamento atual e que se propagam para o romaneio final sem
qualquer aviso ao operador:

| Fallback atual | Localização | Efeito downstream |
|---|---|---|
| `grade` não detectada → `DEFAULT_GRADE = "PP-M"` | `pdf_analyzer.py` linha 130 | Romaneio sai com 3 tamanhos errados |
| `name` não detectado → `None` | `pdf_analyzer.py` (sem fallback declarado) | Romaneio sem nome do produto |
| `price` não detectado → `None` | `pdf_analyzer.py` (sem fallback declarado) | Romaneio sem preço |
| Nenhum swatch detectado → `n_colors = max(1, 0) = 1` | `pdf_analyzer.py` linha 170 | Produto entra com 1 cor sem swatch real |

Esses fallbacks são bombas-relógio **independentes** do problema multi-formato:
mesmo para o catálogo Oasis em produção hoje, qualquer coleção nova com uma
categoria fora do vocabulário do `NAME_RE` produz `name=None` silenciosamente.

A Sprint 08 — Fase C trata esse problema **de forma estrutural**, e não
apenas para o caso multi-formato. Esta ADR formaliza as decisões.

A questão que esta ADR responde é: **como tornar a degradação observável
sem bloquear o pipeline nem mudar comportamento do romaneio em produção?**

### Restrições

- **Regressão zero sobre Oasis MOTION** em produção. O comportamento de
  processamento não pode mudar (todos os produtos atuais têm grade, nome
  e preço detectáveis — então warnings vazios é o resultado esperado).
- **Sem breaking change na API** existente. Clientes que consomem
  `GET /api/v1/catalogs/{id}` não podem quebrar.
- **`PDFNoProductsError` permanece como exceção bloqueante**. Catálogo
  sem nenhum produto detectável é falha global, não degradação local.

---

## Decisão

A ADR estabelece **quatro decisões coordenadas** para introduzir
observabilidade não-bloqueante na extração do catálogo.

### D1 — `AnalyzerWarning` como primitiva de observabilidade

Dataclass `AnalyzerWarning` (frozen + slots) registrada por instância de
degradação detectada durante a análise. Cada warning carrega:

```python
@dataclass(frozen=True, slots=True)
class AnalyzerWarning:
    code: str                       # códigos padronizados (ver §D3)
    severity: str                   # "info" | "warning" | "error"
    page_index: int                 # página onde foi detectado
    sku: str | None                 # SKU do produto afetado, quando aplicável
    message: str                    # mensagem humana em pt-BR
    detected_value: str | None      # valor parcial detectado (diagnóstico)
```

`CatalogMetadata` ganha `warnings: list[AnalyzerWarning]` (default factory =
lista vazia). Comportamento dos catálogos sem degradação preserva
`warnings=[]` — não é breaking change semântico.

### D2 — Política de não-bloqueio

O pipeline de análise **nunca** levanta exceção por degradação local. Quando
uma estratégia retorna `None`/`[]` em sinalização de "não detectado",
o orquestrador:

1. Registra um `AnalyzerWarning` estruturado no buffer.
2. Persiste o produto com o campo correspondente como `None` (não aplica
   mais o `DEFAULT_GRADE` silencioso).
3. Continua processando o restante.

`PDFNoProductsError`, `PDFCorruptError` e `PDFEncryptedError` permanecem
como exceções bloqueantes — esses são falhas catastróficas (catálogo inteiro
inviável), não degradação local.

### D3 — Códigos de warning padronizados

Cinco códigos definidos na Sprint 08. Lista extensível em sprints futuras.

| Código | Severidade | Origem | Significado |
|---|---|---|---|
| `GRADE_NOT_DETECTED` | `error` | `PDFAnalyzer` | Produto não tem grade detectável — AcroForm fica inviável |
| `NAME_NOT_DETECTED` | `warning` | `PDFAnalyzer` | Produto sem nome legível — pedido ainda funciona, mas operador deve revisar |
| `PRICE_NOT_DETECTED` | `warning` | `PDFAnalyzer` | Produto sem preço — pedido funciona, valor não calculado |
| `SWATCHES_NOT_DETECTED` | `info` | `PDFAnalyzer` | Nenhum swatch detectado para o produto — `n_colors=1` |
| `FIELDS_NOT_INJECTED_NO_GRADE` | `error` | `field_injector` | Consequência de `GRADE_NOT_DETECTED` — produto não recebeu campos AcroForm |

**Severidade ≠ bloqueio.** Severidade indica gravidade da degradação para o
operador comercial; **nenhum** dos códigos interrompe o pipeline. `error`
significa "produto inutilizável", não "exceção lançada".

### D4 — Mudança de schema em `ProductPageMeta`

Para suportar D2, o schema da dataclass relaxa:

```python
# Antes (Sprint 07)
grade: str
sizes: list[str]

# Depois (Sprint 08 Fase C)
grade: str | None
sizes: list[str] | None
```

Campos `name: str | None` e `price: Decimal | None` já eram opcionais —
sem mudança neles. Constantes `DEFAULT_GRADE` e `DEFAULT_SIZES` no
`PDFAnalyzer` são **removidas**.

**Impacto downstream:**

- `field_injector.py` precisa lidar com `grade=None` / `sizes=None`. Quando
  isso ocorre, não injeta campos AcroForm para o produto e emite
  `FIELDS_NOT_INJECTED_NO_GRADE`.
- `service.py` propaga a lista de warnings ao banco.

### D5 — Persistência e exposição

**Banco:** coluna nova `catalogs.warnings JSONB DEFAULT '[]'` (migration
Alembic 0008, planejada na ADR-010). Persiste a lista serializada de
warnings por catálogo processado.

**API:** o response existente de `GET /api/v1/catalogs/{id}` ganha campo
`warnings` (array opcional, default vazio). Sem endpoint dedicado — decisão
consolidada na seção 7.1 do PRD Sprint 08.

```jsonc
{
  "data": {
    "id": "uuid",
    /* ... campos existentes ... */
    "warnings": [
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

Campo é opcional no schema da resposta (default `[]`), portanto sem
breaking change para clientes existentes.

---

## Consequências

### Positivas

- **Fim das bombas-relógio silenciosas.** Categoria nova no Oasis sem
  `name` reconhecido agora aparece como warning visível, não passa
  despercebido.
- **Diagnóstico operacional.** Operador comercial recebe lista clara do
  que foi e do que não foi detectado, com `sku`, `page_index` e
  `detected_value` para investigar.
- **Demo robusta.** Catálogo com diferença mínima processa com warnings
  em vez de erro 500 ou silenciosamente errado.
- **Política de severidade alinhada com produto.** `info`/`warning`/`error`
  comunica gravidade ao operador sem alterar fluxo de processamento.
- **Extensível.** Adicionar código de warning novo é uma constante; não
  requer mudança de schema.

### Negativas

- **Mais ruído na UI.** Para Oasis MOTION em produção, warnings devem ser
  raros (todos os produtos atuais detectam corretamente). Mas catálogos
  marginais agora geram mais informação na tela. É informação correta
  sendo exposta — não inventada — mas mudança de UX.
- **Schema downstream relaxado.** `field_injector.py` e código que consome
  `ProductPageMeta` precisam tratar `grade=None`. Custo cobrável na Fase C.
- **Mudança em comportamento de processamento.** Mesmo preservando o
  romaneio gerado para Oasis MOTION bit-a-bit (warnings vazios), o
  `CatalogMetadata` agora carrega o campo `warnings`. Suite de regressão
  golden file da Fase B foi gerada **antes** desta mudança; vai precisar
  ser regenerada para acomodar `warnings=[]` na serialização (decisão
  operacional da Fase C, registrada no PRD).

### Operacionais (banco, CI, downstream)

- **Migration Alembic 0008:** `ALTER TABLE catalogs ADD COLUMN warnings
  JSONB DEFAULT '[]';`. Reversível via `alembic downgrade`. Catálogos
  existentes herdam `[]` automaticamente.
- **Re-geração do golden file** ao incluir `warnings: []` na serialização.
  PR isolado descrevendo a mudança intencional (política rígida do golden
  preservada).
- **CI:** suite de regressão continua bloqueando merge — diff zero
  contra golden v2 (que inclui `warnings: []`).
- **Frontend:** consumo dos warnings na UI fica para sprint dedicada de UI
  (fora de escopo da Sprint 08).

---

## Alternativas descartadas

- **Manter `DEFAULT_GRADE` e converter para warning depois.** Geraria
  produto com grade incorreta + warning informativo. Reportar e produzir
  romaneio errado é pior que reportar e não produzir AcroForm. Descartado
  porque mantém o problema original (romaneio degradado silenciosamente).
- **Levantar exceção por degradação.** Bloquearia o catálogo inteiro
  porque um produto não foi reconhecido. Descartado: viola a expectativa
  de processamento parcial e bloqueia demos/produção desnecessariamente.
- **Severidade como enum.** Tipagem mais forte mas adiciona complexidade
  marginal. Strings `"info"|"warning"|"error"` são suficientes, mais fáceis
  de serializar em JSON, e a validação via JSONSchema/Pydantic é trivial.
  Pode evoluir para enum se a lista crescer.
- **Endpoint dedicado `GET /catalogs/{id}/warnings`.** Mais limpo
  arquiteturalmente mas exige round-trip adicional. Descartado em favor
  do campo no response existente (decisão consolidada no PRD Sprint 08
  §7.1).
- **`AnalyzerWarning` como Exception (sem `raise`).** Híbrido confuso —
  exception não-levantada é anti-padrão. Descartado em favor de dataclass
  POD.

---

## Out of scope (decisões para ADRs/sprints futuras)

- **Banner de warnings na UI web.** Backend persiste e expõe; frontend
  consome em sprint de UI dedicada.
- **Métricas/alertas em produção** (Datadog, Sentry, etc.) com base nos
  warnings. Sprint de observabilidade.
- **Warnings parametrizáveis via profile.** Hoje a lista de códigos é fixa
  no código. Tornar customizável por profile (ex.: marca X considera
  `NAME_NOT_DETECTED` como `error` em vez de `warning`) fica para sprint
  futura.
- **Severidade configurável por marca.** Mesma motivação acima.
- **Internacionalização das mensagens.** Mensagens em pt-BR hardcoded
  nesta versão. i18n fica para fase de internacionalização do produto.
- **Histórico de warnings por catálogo ao longo do tempo.** Hoje warnings
  são snapshot do processamento atual. Audit log fica para outra fase.

---

## Critérios de aceitação arquitetural

A implementação desta ADR está completa quando:

- [ ] `AnalyzerWarning` implementado como dataclass frozen + slots em
      `src/catalogflow/modules/catalog/models.py`
- [ ] `CatalogMetadata.warnings: list[AnalyzerWarning]` adicionado com
      `default_factory=list`
- [ ] `ProductPageMeta.grade` e `ProductPageMeta.sizes` relaxados para
      `str | None` e `list[str] | None`
- [ ] `DEFAULT_GRADE` e `DEFAULT_SIZES` removidos do `PDFAnalyzer`
- [ ] Os 5 códigos de warning definidos em §D3 são gerados nos cenários
      adequados, com `severity`, `page_index` e `sku` corretos
- [ ] `field_injector.py` tolera `grade=None` / `sizes=None`, não injeta
      campos quando ausentes, e emite `FIELDS_NOT_INJECTED_NO_GRADE`
- [ ] Migration Alembic `0008` aplica `catalogs.warnings JSONB DEFAULT '[]'`
      e é reversível (`alembic downgrade -1` funciona)
- [ ] `catalog/service.py` persiste a lista de warnings em
      `catalogs.warnings`
- [ ] `catalog/schemas.py` adiciona campo opcional `warnings` no response
      de `GET /api/v1/catalogs/{id}`
- [ ] Golden file regenerado com `warnings: []` (PR isolado, aprovação
      explícita do PMO conforme política do PRD Sprint 08 §8.3)
- [ ] Cobertura ≥ 90% nos novos códigos de warning e seus geradores
- [ ] Cobertura ≥ 80% sobre os arquivos modificados
- [ ] Suite de regressão golden file passa com diff zero contra golden v2
- [ ] `pytest`, `ruff`, `mypy --strict` e `pre-commit` verdes
- [ ] Documentação do `AnalyzerWarning` no `CLAUDE.md` (códigos +
      severidades + política de não-bloqueio)

---

## Referências

- [ADR-010](./ADR-010-multi-format-catalog-support.md) — Suporte multi-formato
  via Strategy Pattern (decisão D3 prevê esta ADR)
- ADR-001 — Monolito modular multi-tenant
- [PRD Sprint 08](../sprint_08/PRD_sprint_08.md) — escopo da sprint, em
  particular Fase C e seção 7.1 (exposição via API)
- `src/catalogflow/modules/catalog/pdf_analyzer.py` (Sprint 08 Fase B) —
  estado atual com fallbacks silenciosos ainda preservados
- `src/catalogflow/modules/catalog/field_injector.py` — consumidor downstream
  que precisa tolerar `grade=None`
