"""Gerador da fixture sintética do formato `prefixed_dual_price`.

Espelha o estilo de `generate_fixtures.py` (pymupdf, **não** ReportLab):
constrói um catálogo que reproduz os padrões do formato de SKU prefixado
+ preço dual — observado em catálogos de moda masculina premium (ex.: o
catálogo FERLA que motivou a ADR-010):

- SKU prefixado por rótulo: `Ref: 01010012` (8 dígitos, sem hífen).
- Grade alfabética com espaços ao redor do hífen: `Grade: P - GG`.
- Preço dual rotulado, sem `R$`: `Atacado - 299` / `Varejo - 319`.
- Nome SEM vocabulário fixo, isolável por exclusão dos demais eixos.

ESPELHO FIEL DO REAL (hotfix FERLA): a versão anterior desta fixture
divergia do catálogo real em duas dimensões e, com isso, mascarava dois
bugs que só apareceram no PDF real:

1. **Posição do texto.** O FERLA real imprime o produto no terço
   médio-superior da página (top ~495-569 numa página de 842), com
   NENHUM texto na zona inferior. A fixture antiga punha tudo no rodapé
   (top 770-828), o que mascarava o gate `bot_words` que descartava
   páginas sem texto no rodapé. Agora o texto fica no meio da página,
   exercitando o fallback de coordenadas por zona do SKU.

2. **Hierarquia tipográfica.** No FERLA real o PREÇO é impresso em peso
   MAIOR (13.0, negrito) que o NOME (12.0, regular). A fixture antiga
   invertia isso (nome 16, preço 9), mascarando o bug em que
   `positional_title` devolvia a linha de preço como nome. Agora a
   fixture replica o real: preço mais pesado que o nome, exercitando a
   exclusão de ruído da estratégia de nome.

O swatch permanece desenhado na zona inferior (única coisa no rodapé):
ele é um DESENHO vetorial, não texto, então não popula `bot_words` (que
conta palavras) — o fallback continua sendo exercitado — mas mantém a
detecção de swatch e, com ela, a fixture livre de warnings (ADR-011).

O PDF gerado tem 2 páginas:

    página 0 → 1 produto (mínimo viável, `side="single"`).
    página 1 → 2 produtos lado a lado (exercita Voronoi neste formato).

Saída: `tests/fixtures/catalogo_prefixed_dual_price.pdf` (commitada —
fixture sintética, sem dado de cliente).

Determinismo: `tobytes()` pode variar entre execuções por metadados de
timestamp, mas os testes checam o conteúdo EXTRAÍDO (SKU, grade, preço,
nome), não os bytes do PDF — então a variação é irrelevante. Commite a
versão gerada e use-a como fixture fixa.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

FIXTURES_DIR = Path(__file__).resolve().parent

# Tipografia espelhando o FERLA real: nome regular, preço em peso MAIOR.
NAME_FONT: str = "helv"  # Helvetica regular
PRICE_FONT: str = "hebo"  # Helvetica-Bold
NAME_FONTSIZE: float = 12.0
DETAIL_FONTSIZE: float = 12.0  # Ref / Grade — mesmo peso do nome (como no real)
PRICE_FONTSIZE: float = 13.0  # preço maior que o nome (como no real)
SWATCH_SIDE: float = 18.0

# Posições verticais (baseline) no terço médio da página — como o real,
# longe da zona inferior (`top > 0.92 * 842 ≈ 775`), de modo que
# `bot_words` (texto no rodapé) fique vazio e o fallback seja exercitado.
Y_NAME: float = 500.0
Y_REF: float = 518.0
Y_GRADE: float = 536.0
Y_ATACADO: float = 554.0
Y_VAREJO: float = 572.0
# Swatch desenhado no rodapé (única marca na zona inferior).
SWATCH_TOP: float = 792.0


def _add_product(
    page: pymupdf.Page,
    *,
    x: float,
    name: str,
    ref: str,
    grade: str,
    atacado: str,
    varejo: str,
    swatch_fill: tuple[float, float, float],
) -> None:
    """Imprime um produto no formato prefixado a partir da coluna `x`.

    O nome vai em `NAME_FONTSIZE` (regular) e o preço em `PRICE_FONTSIZE`
    (negrito, MAIOR que o nome) — espelhando o FERLA real, em que o preço
    tem peso maior. Todo o texto fica no terço médio da página; só o
    swatch (desenho vetorial) ocupa a zona inferior.
    """
    page.insert_text((x, Y_NAME), name, fontname=NAME_FONT, fontsize=NAME_FONTSIZE)
    page.insert_text((x, Y_REF), f"Ref: {ref}", fontname=NAME_FONT, fontsize=DETAIL_FONTSIZE)
    page.insert_text((x, Y_GRADE), f"Grade: {grade}", fontname=NAME_FONT, fontsize=DETAIL_FONTSIZE)
    page.insert_text(
        (x, Y_ATACADO),
        f"Atacado - {atacado}",
        fontname=PRICE_FONT,
        fontsize=PRICE_FONTSIZE,
    )
    page.insert_text(
        (x, Y_VAREJO),
        f"Varejo - {varejo}",
        fontname=PRICE_FONT,
        fontsize=PRICE_FONTSIZE,
    )
    page.draw_rect(
        pymupdf.Rect(x, SWATCH_TOP, x + SWATCH_SIDE, SWATCH_TOP + SWATCH_SIDE),
        color=(0.0, 0.0, 0.0),
        fill=swatch_fill,
        width=0.5,
    )


def build_prefixed_dual_price() -> bytes:
    """Catálogo prefixado sintético: página 0 (1 produto) + página 1 (2 produtos)."""
    doc = pymupdf.open()

    # ── Página 0 — produto único (A4 retrato).
    page0 = doc.new_page(width=595.0, height=842.0)
    _add_product(
        page0,
        x=50,
        name="Camisa Polo Pima Clássica",
        ref="01010012",
        grade="P - GG",
        atacado="299",
        varejo="319",
        swatch_fill=(0.20, 0.30, 0.60),
    )

    # ── Página 1 — dois produtos lado a lado (página mais larga p/ separação).
    page1 = doc.new_page(width=720.0, height=842.0)
    _add_product(
        page1,
        x=60,
        name="Camiseta Gola V Premium",
        ref="01010013",
        grade="P - GG",
        atacado="199",
        varejo="219",
        swatch_fill=(0.60, 0.20, 0.20),
    )
    _add_product(
        page1,
        x=420,
        name="Bermuda Sarja Slim",
        ref="01010014",
        grade="P - GG",
        atacado="159",
        varejo="179",
        swatch_fill=(0.20, 0.50, 0.30),
    )

    data: bytes = doc.tobytes()
    doc.close()
    return data


FIXTURES: dict[str, object] = {
    "catalogo_prefixed_dual_price.pdf": build_prefixed_dual_price,
}


def main() -> None:
    print(f"Gerando fixtures do formato prefixado em {FIXTURES_DIR}/")
    for name, builder in FIXTURES.items():
        data = builder()  # type: ignore[operator]
        target = FIXTURES_DIR / name
        target.write_bytes(data)
        print(f"  [OK] {name} ({len(data):,} bytes)")


if __name__ == "__main__":  # pragma: no cover
    main()
