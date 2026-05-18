# PRD Sprint 05 — Correção de Bugs do PDFAnalyzer

> **Projeto:** CatalogFlow
> **Sprint:** 05 / Bug Fix — PDF Analyzer
> **Status:** Aprovação Pendente
> **Data:** 2026-05-17
> **PMO:** Thiago Scutari
> **Executor:** Claude Code
> **Referência obrigatória:** `spec.md`, `CLAUDE.md`

---

## Sumário Executivo

| ID | Severidade | Descrição | Páginas afetadas |
|----|-----------|-----------|-----------------|
| S05-01 | 🔴 Crítico | SKU com 9 dígitos não detectado — páginas sem AcroForm | 30, 36 |
| S05-02 | 🟡 Médio | Nome de produto trocado em páginas com N produtos lado a lado | 5, 52, 61, 69 |

**Impacto no catálogo Oasis MOTION (70 páginas):**
- 2 páginas inteiramente sem campos de pedido (VESTIDO SALOMÉ, ambas as cores)
- 4 páginas com nome de produto incorreto no banco e no romaneio
- SKU `0062500062-0` (SHORT ALFAIATARIA LEIA) com nome errado em 2 das 3 páginas onde aparece

**Sem migrations de banco.** Ambos os bugs estão no `PDFAnalyzer` — camada de
funções puras. A correção não afeta routers, serviços, Celery ou banco de dados.

---

## S05-01 — SKU com 9 dígitos não detectado

### Evidência

O VESTIDO SALOMÉ aparece nas páginas 30 e 36 do catálogo com SKU `442500908-0`
(9 dígitos antes do traço). O padrão correto seria `0442500908-0` (10 dígitos),
mas o catálogo foi gerado pela agência sem o zero à esquerda.

O `PDFAnalyzer._detect_product_pages()` usa um regex que exige exatamente
10 dígitos antes do traço. Qualquer SKU com 9 dígitos passa invisível e
a página não recebe campos AcroForm.

**Evidência no PDF:**
- Página 30: texto extraído contém `442500908-0 / VESTIDO SALOMÉ / R$ 1.288,00 / PP-M` — sem painel de pedido
- Página 36: mesma situação, segunda cor do mesmo produto

**Regex atual (provável):**
```python
SKU_PATTERN = re.compile(r'\d{10}-\d')
```

**Regex corrigido:**
```python
SKU_PATTERN = re.compile(r'\d{9,10}-\d')
```

### O que NÃO mudar

O campo `sku` persistido no banco deve permanecer exatamente como lido do PDF
(`442500908-0`), sem normalização automática. A normalização para zero-padding
é responsabilidade do mapeamento SKU→ERP em `_build_cod_item()`, não do analyzer.

### Testes de regressão obrigatórios

```python
# tests/fixtures/generate_fixtures.py — adicionar fixture:
# catalogo_sku_9_digitos.pdf — PDF mínimo com SKU "442500908-0"

def test_sku_9_digits_is_detected(pdf_sku_9_digits):
    """SKU com 9 dígitos antes do traço deve ser detectado como página de produto."""
    result = PDFAnalyzer().analyze(pdf_bytes=pdf_sku_9_digits)
    assert result.n_product_pages == 1
    assert result.product_pages[0].sku == "442500908-0"

def test_sku_9_digits_fields_are_injected(pdf_sku_9_digits):
    """PDF com SKU de 9 dígitos deve receber campos AcroForm após injeção."""
    metadata = PDFAnalyzer().analyze(pdf_bytes=pdf_sku_9_digits)
    output = FieldInjector().inject(pdf_sku_9_digits, metadata)
    doc = pymupdf.open(stream=output, filetype="pdf")
    widgets = [w for page in doc for w in (page.widgets() or [])]
    assert len(widgets) > 0

def test_sku_10_digits_unaffected(pdf_1_produto_1_cor):
    """Regressão: SKU padrão de 10 dígitos não deve ser afetado."""
    result = PDFAnalyzer().analyze(pdf_bytes=pdf_1_produto_1_cor)
    assert result.n_product_pages >= 1
    assert re.match(r'^\d{10}-\d$', result.product_pages[0].sku)
```

---

## S05-02 — Nome de produto trocado em páginas com múltiplos produtos

### Evidência

Nas páginas com **N produtos lado a lado**, o analyzer extrai o nome
incorretamente — o bloco de texto de nome de um produto vizinho acaba
associado ao SKU errado.

**Casos confirmados no catálogo Oasis MOTION:**

| Página | SKU esquerda | SKU direita | Nome correto (direita) | Nome incorreto extraído |
|--------|-------------|-------------|------------------------|------------------------|
| 5 | `0322500004-0` JAQUETA BERENICE | `0142500001-0` | CALÇA CAPRI ESTHER | "JAQUETA" |
| 52 | `0422500012-0` BODY REBECA | `0062500062-0` | SHORT ALFAIATARIA LEIA | "BODY" |
| 61 | `0362500186-0` BLUSA YOANA | `0022500149-0` | CALÇA RAQUEL | "BLUSA" |
| 69 | `0582500025-0` BLAZER SARAH | `0062500062-0` | SHORT ALFAIATARIA LEIA | "BLAZER" |

### Causa-raiz

O `PDFAnalyzer._extract_page_metadata()` localiza cada SKU e depois busca
o bloco de texto de nome mais próximo **em toda a extensão horizontal da página**,
sem nenhuma restrição de zona. Em páginas com múltiplos produtos, o bloco de
nome de um produto pode estar geometricamente mais próximo (em Y) do SKU do
produto vizinho do que do seu próprio SKU.

### Princípio da correção — Zonas de Voronoi horizontais

**Regra fundamental: nenhum valor de posição pode ser hardcoded.**

A divisão `page_width / 2` seria frágil: quebraria em layouts assimétricos
(produto maior à esquerda), em catálogos de outras marcas com grids diferentes,
ou em páginas com 3+ produtos. A solução correta calcula as fronteiras
**dinamicamente a partir das posições reais de todos os SKUs detectados
na mesma página**.

**Algoritmo:**

```
Dados: lista de SKUs detectados na página, ordenados por x0 crescente
       [(sku_A, rect_A), (sku_B, rect_B), (sku_C, rect_C), ...]

Para cada SKU_i:
  x_left  = midpoint(rect_{i-1}.x0, rect_i.x0)   → ou 0 se primeiro
  x_right = midpoint(rect_i.x0, rect_{i+1}.x0)   → ou page_width se último

  zona_i = Rect(x_left, 0, x_right, page_height)

Buscar nome, preço, grade e swatches de SKU_i APENAS dentro de zona_i.
```

**Visualização — 2 produtos (catálogo Oasis atual):**

```
SKU_A em x=180          SKU_B em x=540
     |                       |
     |      zona A     |     zona B      |
     0                360               720
                        ↑
              midpoint(180, 540) = 360   ← calculado dos dados, não fixo
```

**Visualização — layout assimétrico (produto grande à esquerda):**

```
SKU_A em x=100                SKU_B em x=480
     |                             |
     |         zona A         |   zona B   |
     0                        290          720
                               ↑
                     midpoint(100, 480) = 290
```

**Visualização — 3 produtos (layout futuro):**

```
SKU_A x=120    SKU_B x=360    SKU_C x=600
     |               |               |
     | zona A  | zona B  | zona C  |
     0         240       480        720
```

**Visualização — 1 produto (comportamento atual preservado integralmente):**

```
SKU_A em x=qualquer
     |
     |         zona A = página inteira          |
     0                                       page_width
```

### Implementação de referência

```python
def _assign_name_zones(
    self,
    sku_rects: list[tuple[str, fitz.Rect]],
    page_width: float,
    page_height: float,
) -> dict[str, fitz.Rect]:
    """
    Calcula a zona de busca de texto para cada SKU usando os pontos médios
    entre as coordenadas X dos SKUs vizinhos como fronteiras.

    Não contém nenhum valor hardcoded de posição ou proporção de página.
    Funciona para 1, 2, 3 ou N produtos por página.

    Args:
        sku_rects: lista de (sku, rect) com todos os SKUs detectados na página.
        page_width: largura total da página em pontos PDF.
        page_height: altura total da página em pontos PDF.

    Returns:
        dict {sku: zona_rect} — bounding box de busca para cada SKU.
    """
    # Ordenar por posição horizontal para identificar vizinhos
    sorted_skus = sorted(sku_rects, key=lambda item: item[1].x0)

    zones: dict[str, fitz.Rect] = {}
    for i, (sku, rect) in enumerate(sorted_skus):
        x_left = (
            0.0
            if i == 0
            else (sorted_skus[i - 1][1].x0 + rect.x0) / 2.0
        )
        x_right = (
            page_width
            if i == len(sorted_skus) - 1
            else (rect.x0 + sorted_skus[i + 1][1].x0) / 2.0
        )
        zones[sku] = fitz.Rect(x_left, 0.0, x_right, page_height)

    return zones


def _extract_page_metadata(self, page, page_idx: int) -> list[ProductPageMeta]:
    """
    Extrai metadados de todos os produtos da página.
    Usa _assign_name_zones() para garantir que nome, preço, grade e swatches
    de cada SKU sejam buscados apenas dentro da zona desse SKU.
    """
    page_width = page.rect.width
    page_height = page.rect.height

    sku_rects = self._detect_skus_on_page(page)  # [(sku, rect), ...]
    if not sku_rects:
        return []

    zones = self._assign_name_zones(sku_rects, page_width, page_height)

    products = []
    for sku, sku_rect in sku_rects:
        zone = zones[sku]
        name   = self._find_product_name(page, sku_rect, zone)
        price  = self._find_product_price(page, sku_rect, zone)
        grade  = self._find_product_grade(page, sku_rect, zone)
        sizes  = self._parse_sizes(grade)
        swatches = self._detect_swatches(page, zone)
        products.append(ProductPageMeta(
            sku=sku, name=name, price=price,
            grade=grade, sizes=sizes,
            swatches=swatches, page_index=page_idx,
            ...
        ))

    return products
```

### Restrições de implementação

- `_assign_name_zones()` deve ser uma **função pura**: recebe listas e floats,
  retorna dict. Sem acesso à página, sem I/O, sem estado.
- As buscas de `name`, `price`, `grade` e `swatches` devem ser restritas à
  `zone` do respectivo SKU — não apenas o nome.
- Se dois SKUs tiverem o mesmo `x0` (caso degenerado, improvável), registrar
  aviso via `logger.warning` e usar a página inteira como fallback para ambos.
  Não levantar exceção.
- Páginas com 1 SKU: `zone = Rect(0, 0, page_width, page_height)` — idêntico
  ao comportamento atual, sem impacto.

### Testes de regressão obrigatórios

```python
# ── Testes unitários de _assign_name_zones (sem PDF) ─────────────────────

def test_assign_name_zones_single_sku():
    """1 SKU → zona ocupa a página inteira."""
    zones = PDFAnalyzer()._assign_name_zones(
        [("SKU-A", fitz.Rect(100, 200, 200, 220))],
        page_width=720, page_height=1080,
    )
    assert zones["SKU-A"].x0 == 0.0
    assert zones["SKU-A"].x1 == 720.0

def test_assign_name_zones_two_skus_midpoint():
    """2 SKUs → fronteira é o ponto médio entre os dois x0."""
    zones = PDFAnalyzer()._assign_name_zones(
        [
            ("SKU-A", fitz.Rect(180, 900, 280, 920)),
            ("SKU-B", fitz.Rect(540, 900, 640, 920)),
        ],
        page_width=720, page_height=1080,
    )
    mid = (180 + 540) / 2  # 360.0
    assert zones["SKU-A"].x0 == 0.0
    assert zones["SKU-A"].x1 == pytest.approx(mid)
    assert zones["SKU-B"].x0 == pytest.approx(mid)
    assert zones["SKU-B"].x1 == 720.0

def test_assign_name_zones_three_skus():
    """3 SKUs → cada zona delimitada pelos midpoints dos vizinhos."""
    zones = PDFAnalyzer()._assign_name_zones(
        [
            ("SKU-A", fitz.Rect(120, 900, 200, 920)),
            ("SKU-B", fitz.Rect(360, 900, 440, 920)),
            ("SKU-C", fitz.Rect(600, 900, 680, 920)),
        ],
        page_width=720, page_height=1080,
    )
    mid_ab = (120 + 360) / 2  # 240.0
    mid_bc = (360 + 600) / 2  # 480.0
    assert zones["SKU-A"] == pytest.approx(fitz.Rect(0.0,   0, mid_ab, 1080))
    assert zones["SKU-B"] == pytest.approx(fitz.Rect(mid_ab, 0, mid_bc, 1080))
    assert zones["SKU-C"] == pytest.approx(fitz.Rect(mid_bc, 0, 720.0, 1080))

def test_assign_name_zones_asymmetric_layout():
    """Layout assimétrico: fronteira segue os dados, não o centro da página."""
    zones = PDFAnalyzer()._assign_name_zones(
        [
            ("SKU-A", fitz.Rect(100, 900, 200, 920)),  # produto maior à esquerda
            ("SKU-B", fitz.Rect(480, 900, 580, 920)),  # produto menor à direita
        ],
        page_width=720, page_height=1080,
    )
    mid = (100 + 480) / 2  # 290.0 — não é page_width/2 = 360
    assert zones["SKU-A"].x1 == pytest.approx(mid)
    assert zones["SKU-B"].x0 == pytest.approx(mid)

def test_assign_name_zones_contiguous_and_non_overlapping():
    """As zonas devem cobrir a página inteira sem lacunas ou sobreposições."""
    zones = PDFAnalyzer()._assign_name_zones(
        [
            ("SKU-A", fitz.Rect(50,  900, 150, 920)),
            ("SKU-B", fitz.Rect(300, 900, 400, 920)),
            ("SKU-C", fitz.Rect(550, 900, 650, 920)),
        ],
        page_width=720, page_height=1080,
    )
    sorted_z = sorted(zones.values(), key=lambda r: r.x0)
    assert sorted_z[0].x0 == pytest.approx(0.0)
    assert sorted_z[-1].x1 == pytest.approx(720.0)
    for i in range(len(sorted_z) - 1):
        assert sorted_z[i].x1 == pytest.approx(sorted_z[i + 1].x0)

# ── Testes de integração com PDFs de fixture ─────────────────────────────

def test_two_products_names_not_swapped(pdf_dois_produtos_nomes_distintos):
    """Página com 2 produtos: cada um deve ter seu próprio nome."""
    result = PDFAnalyzer().analyze(pdf_bytes=pdf_dois_produtos_nomes_distintos)
    skus = {p.sku: p.name for p in result.product_pages}
    assert skus.get("0322500004-0") == "JAQUETA BERENICE"
    assert skus.get("0142500001-0") == "CALÇA CAPRI ESTHER"

def test_single_product_name_unaffected(pdf_1_produto_1_cor):
    """Regressão: página com 1 produto deve ter nome extraído normalmente."""
    result = PDFAnalyzer().analyze(pdf_bytes=pdf_1_produto_1_cor)
    assert result.product_pages[0].name is not None
    assert len(result.product_pages[0].name) > 0

def test_two_products_each_receives_acroform_fields(pdf_dois_produtos_nomes_distintos):
    """Página com 2 produtos: ambos devem receber campos AcroForm."""
    metadata = PDFAnalyzer().analyze(pdf_bytes=pdf_dois_produtos_nomes_distintos)
    output = FieldInjector().inject(pdf_dois_produtos_nomes_distintos, metadata)
    doc = pymupdf.open(stream=output, filetype="pdf")
    field_names = [w.field_name for page in doc for w in (page.widgets() or [])]
    assert any("0322500004-0" in f for f in field_names), "Sem campos para JAQUETA BERENICE"
    assert any("0142500001-0" in f for f in field_names), "Sem campos para CALÇA CAPRI ESTHER"
```

---

## ADR-007 — Zonas de Voronoi horizontal para extração de metadados

**Contexto:** Catálogos de moda podem ter N produtos por página, com layouts
assimétricos e variáveis entre coleções e marcas. Hardcoding de posições (como
`page_width / 2`) quebra silenciosamente em layouts não previstos.

**Decisão:** O `PDFAnalyzer` calcula zonas de busca de texto dinamicamente via
`_assign_name_zones()`, usando os pontos médios entre as coordenadas X dos SKUs
detectados na página como fronteiras. Nenhum valor de posição é hardcoded.

**Consequências:**
- Funciona para 1, 2, 3 ou N produtos por página sem alteração de código
- Layouts assimétricos são tratados naturalmente — a fronteira segue os dados
- `_assign_name_zones()` é testável em isolamento, sem PDF real
- A mesma zona restringe name, price, grade e swatches — não só o nome

**Alternativas descartadas:**
- `page_width / 2` fixo: quebra em layouts assimétricos e N > 2 produtos
- Detecção de separador visual (linha, coluna vazia): frágil, depende do design

---

## Acceptance Criteria

| ID | Critério | Verificação |
|----|---------|------------|
| AC-01 | Páginas 30 e 36 recebem campos AcroForm após reprocessamento | Manual |
| AC-02 | SKU `442500908-0` aparece em `catalog_products` no banco | SQL |
| AC-03 | Página 5: `0142500001-0` tem `name = "CALÇA CAPRI ESTHER"` | SQL |
| AC-04 | Página 52: `0062500062-0` tem `name = "SHORT ALFAIATARIA LEIA"` | SQL |
| AC-05 | Página 61: `0022500149-0` tem `name = "CALÇA RAQUEL"` | SQL |
| AC-06 | Página 69: `0062500062-0` tem `name = "SHORT ALFAIATARIA LEIA"` | SQL |
| AC-07 | `pytest tests/` passa com cobertura ≥ 80% | CI |
| AC-08 | SKUs com 10 dígitos continuam detectados (sem regressão) | CI |
| AC-09 | Página com 1 produto: comportamento inalterado | CI |
| AC-10 | `_assign_name_zones()` passa nos 5 testes unitários | CI |
| AC-11 | `ruff check . && mypy src/` sem erros | CI |

---

## Definition of Done

- [ ] `pdf_analyzer.py` corrigido — regex `\d{9,10}-\d` + `_assign_name_zones()`
- [ ] Fixture `catalogo_sku_9_digitos.pdf` gerada e commitada
- [ ] Fixture `catalogo_dois_produtos_nomes_distintos.pdf` gerada e commitada
- [ ] Todos os testes escritos e passando (5 unitários + 3 integração)
- [ ] Catálogo Oasis reprocessado manualmente — ACs 01–06 verificados
- [ ] CI verde (quality + test + build)
- [ ] PR criado com description listando todos os ACs e referenciando ADR-007

---

## Out of Scope (esta sprint)

- ❌ Reprocessamento automático dos catálogos existentes em produção
- ❌ Correção do `field_injector.py` — não afetado por estes bugs
- ❌ Normalização de SKU para zero-padding no `_build_cod_item()`
- ❌ Outros bugs do backlog (event loop warning, UniqueViolation nos testes)
- ❌ Upload de pedido via web e soft-delete (sprints separadas)

---

## Ordem de Implementação

```
1. Inspeção do código atual (PROMPT 0)
2. Fix S05-01 — regex + fixture + testes (PROMPT 1)
3. Fix S05-02 — _assign_name_zones() + refatoração + fixture + testes (PROMPT 2)
4. Suite completa + quality check + commits atômicos (PROMPT 3)
5. Reprocessamento manual do catálogo Oasis em produção (manual, pós-deploy)
```

---

## Referências

| Documento | Uso |
|-----------|-----|
| `spec.md` | Convenções de SKU, estrutura do PDFAnalyzer, ADRs |
| `CLAUDE.md` | Nenhum hardcode de posição; bytes não file path |
| `oasis_form_v2.py` | Lógica original de referência |
| `example/CATÁLOGO OASIS MOTION_original.pdf` | Smoke test manual |
