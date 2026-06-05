"""Gerador da fixture sintética do formato `prefixed_dual_price`.

Espelha o estilo de `generate_fixtures.py` (pymupdf, **não** ReportLab):
constrói um catálogo que reproduz os padrões do formato de SKU prefixado
+ preço dual — observado em catálogos de moda masculina premium (ex.: o
catálogo FERLA que motivou a ADR-010):

- SKU prefixado por rótulo: `Ref: 01010012` (8 dígitos, sem hífen).
- Grade alfabética com espaços ao redor do hífen: `Grade: P - GG`.
- Preço dual rotulado, sem `R$`: `Atacado - 299` / `Varejo - 319`.
- Nome SEM vocabulário fixo, isolável apenas por peso tipográfico:
  o nome do produto é impresso em `fontsize=16`; todo o resto em
  `fontsize=9`. Isso permite ao `positional_title` selecioná-lo.
- Swatches geométricos no rodapé (mesmo padrão geométrico do Oasis).

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
FONT: str = "helv"

# Hierarquia tipográfica: nome do produto em destaque, resto pequeno.
NAME_FONTSIZE: float = 16.0
DETAIL_FONTSIZE: float = 9.0
SWATCH_SIDE: float = 18.0


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

    O nome vai em `NAME_FONTSIZE` (maior peso tipográfico da zona); os
    demais campos em `DETAIL_FONTSIZE`, todos na zona inferior da página
    (`top > 0.92 * page_height`) para que o analyzer reconheça a página
    como página de produto.
    """
    page.insert_text((x, 770), name, fontname=FONT, fontsize=NAME_FONTSIZE)
    page.insert_text((x, 792), f"Ref: {ref}", fontname=FONT, fontsize=DETAIL_FONTSIZE)
    page.insert_text((x, 804), f"Grade: {grade}", fontname=FONT, fontsize=DETAIL_FONTSIZE)
    page.insert_text((x, 816), f"Atacado - {atacado}", fontname=FONT, fontsize=DETAIL_FONTSIZE)
    page.insert_text((x, 828), f"Varejo - {varejo}", fontname=FONT, fontsize=DETAIL_FONTSIZE)
    page.draw_rect(
        pymupdf.Rect(x, 832, x + SWATCH_SIDE, 832 + SWATCH_SIDE),
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
