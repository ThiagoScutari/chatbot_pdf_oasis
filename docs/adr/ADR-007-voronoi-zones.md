# ADR-007: Zonas de Voronoi horizontal para extração de metadados por SKU

**Status:** Vigente
**Data:** 2026-05-15 (Sprint 05)

## Contexto

Catálogos de moda podem ter N produtos por página com layouts assimétricos e
variáveis entre coleções e marcas. Hardcoding de posições (como `page_w / 2`)
quebra silenciosamente em layouts não previstos.

## Decisão

O `PDFAnalyzer` calcula zonas de busca de texto **dinamicamente** via
`_assign_name_zones()`, usando os pontos médios entre as coordenadas X dos
SKUs detectados na página como fronteiras. Nenhum valor de posição é
hardcoded. Funciona para 1, 2, 3 ou N produtos por página. As extrações de
`name`, `price`, `grade` e `swatches` são restritas à zona do respectivo SKU.

## Consequências

- Layouts assimétricos tratados corretamente — fronteira segue os dados.
- `_assign_name_zones()` é testável em isolamento, sem PDF real.
- Página com 1 produto: zona = página inteira (comportamento anterior
  preservado).

## Alternativas descartadas

- **`page_w / 2` fixo** — quebra em layouts assimétricos e em páginas com 3+
  produtos. Foi a causa-raiz do bug que motivou este ADR.
