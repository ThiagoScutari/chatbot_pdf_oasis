"""Gerador de PDFs sintéticos para testes do `catalog`.

Os PDFs gerados aqui imitam a ESTRUTURA do catálogo Oasis no que importa
para o analisador:

- Texto com SKU no padrão `\\d{10,13}-\\d` e grade `PP-M..PP-GG` aparece em
  `page_p.extract_words()` com `top > 0.92 * page_height`.
- Swatches são `draw_rect` vetoriais (não imagens), com `y0 ≥ 0.92 * h`,
  largura/altura < 45pt e fill diferente de branco.

Cenários gerados (correspondem 1:1 às fixtures listadas no PRD Sprint 01):

    catalogo_1_produto_1_cor.pdf      → 1 SKU, 1 swatch.
    catalogo_1_produto_2_cores.pdf    → 1 SKU, 2 swatches.
    catalogo_2_produtos_pagina.pdf    → 2 SKUs (left + right), 1 swatch cada.
    catalogo_pp_g.pdf                 → 1 SKU, grade PP-G (4 tamanhos).
    pdf_sem_produtos.pdf              → texto editorial sem SKU.
    pdf_criptografado.pdf             → 1 SKU mas protegido por senha.

Não commitamos catálogos reais da Oasis em `tests/fixtures/` (CLAUDE.md).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

FIXTURES_DIR = Path(__file__).resolve().parent
PAGE_W: float = 595.0
PAGE_H: float = 842.0  # A4 portrait — proporção mantida do POC.
LEGEND_Y_BASELINE: float = 800.0
SWATCH_Y: float = 820.0
SWATCH_SIDE: float = 20.0
FONT: str = "helv"


# ──────────────────────────────────────────────
#  Builders
# ──────────────────────────────────────────────


def _new_doc_with_page() -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    return doc, page


def _add_decorative_top(page: pymupdf.Page, text: str) -> None:
    """Texto no topo da página — fora da zona de legenda (ignorado pelo analyzer)."""
    page.insert_text((50, 100), text, fontname=FONT, fontsize=16)


def _add_legend(
    page: pymupdf.Page,
    *,
    x: float,
    sku: str,
    grade: str,
    product_name: str,
) -> None:
    """Linha de legenda na zona inferior (`top > 0.92 * page_height`)."""
    line = f"{product_name} REF: {sku}  {grade}"
    page.insert_text(
        (x, LEGEND_Y_BASELINE),
        line,
        fontname=FONT,
        fontsize=9,
        color=(0.1, 0.1, 0.1),
    )


def _add_swatch(
    page: pymupdf.Page,
    *,
    x: float,
    fill: tuple[float, float, float],
) -> None:
    """Quadrado colorido vetorial (não imagem) na zona inferior."""
    page.draw_rect(
        pymupdf.Rect(x, SWATCH_Y, x + SWATCH_SIDE, SWATCH_Y + SWATCH_SIDE),
        color=(0.0, 0.0, 0.0),
        fill=fill,
        width=0.5,
    )


# ──────────────────────────────────────────────
#  Fixtures
# ──────────────────────────────────────────────


def build_1_produto_1_cor() -> bytes:
    doc, page = _new_doc_with_page()
    _add_decorative_top(page, "JAQUETA Premium — Coleção Demo")
    _add_legend(
        page,
        x=50,
        sku="0442500941-0",
        grade="PP-G",
        product_name="JAQUETA",
    )
    _add_swatch(page, x=50, fill=(0.20, 0.30, 0.70))
    data: bytes = doc.tobytes()
    doc.close()
    return data


def build_1_produto_2_cores() -> bytes:
    doc, page = _new_doc_with_page()
    _add_decorative_top(page, "VESTIDO Joana — 2 cores")
    _add_legend(
        page,
        x=50,
        sku="0442500912-0",
        grade="PP-G",
        product_name="VESTIDO",
    )
    _add_swatch(page, x=50, fill=(0.50, 0.20, 0.10))
    _add_swatch(page, x=80, fill=(0.10, 0.50, 0.20))
    data: bytes = doc.tobytes()
    doc.close()
    return data


def build_2_produtos_pagina() -> bytes:
    doc, page = _new_doc_with_page()
    _add_decorative_top(page, "JAQUETA + VESTIDO na mesma página")
    # Esquerdo (x < page_w/2 = 297.5)
    _add_legend(
        page,
        x=40,
        sku="0442500941-0",
        grade="PP-M",
        product_name="JAQUETA",
    )
    _add_swatch(page, x=40, fill=(0.70, 0.30, 0.20))
    # Direito (x ≥ 297.5)
    _add_legend(
        page,
        x=320,
        sku="0322500004-0",
        grade="PP-G",
        product_name="VESTIDO",
    )
    _add_swatch(page, x=320, fill=(0.20, 0.30, 0.70))
    data: bytes = doc.tobytes()
    doc.close()
    return data


def build_grade_pp_g() -> bytes:
    """Variação focada em grade PP-G (4 tamanhos: PP, P, M, G)."""
    doc, page = _new_doc_with_page()
    _add_decorative_top(page, "CALÇA Larga — Grade PP-G")
    _add_legend(
        page,
        x=50,
        sku="0500000001-0",
        grade="PP-G",
        product_name="CALCA",
    )
    _add_swatch(page, x=50, fill=(0.30, 0.40, 0.50))
    data: bytes = doc.tobytes()
    doc.close()
    return data


def build_sem_produtos() -> bytes:
    doc, page = _new_doc_with_page()
    page.insert_text(
        (50, 100),
        "Página editorial — sem SKUs nem grades.",
        fontname=FONT,
        fontsize=14,
    )
    page.insert_text(
        (50, 200),
        "Texto descritivo de coleção sem informação parsável.",
        fontname=FONT,
        fontsize=10,
    )
    data: bytes = doc.tobytes()
    doc.close()
    return data


def build_dois_produtos_nomes_distintos() -> bytes:
    """Página com 2 produtos lado a lado e nomes DISTINTOS — regressão S05-02.

    Reproduz o cenário das páginas 5, 52, 61 e 69 do catálogo Oasis MOTION,
    onde o nome de um produto vizinho era atribuído ao SKU errado por falta
    de partição horizontal na busca de texto.

    SKUs em posições X claramente distintas (160 e 500 em página de 720pt),
    garantindo que o midpoint dinâmico calculado por `_assign_name_zones`
    isole cada produto na sua coluna.
    """
    doc = pymupdf.open()
    page_w = 720.0
    page_h = 842.0
    page = doc.new_page(width=page_w, height=page_h)

    page.insert_text(
        (50, 100),
        "Dois produtos lado a lado — nomes não devem trocar",
        fontname=FONT,
        fontsize=14,
    )

    # Produto da esquerda — JAQUETA BERENICE (SKU em x≈160)
    page.insert_text((160, 780), "0322500004-0", fontname=FONT, fontsize=9)
    page.insert_text((160, 790), "JAQUETA BERENICE", fontname=FONT, fontsize=9)
    page.insert_text((160, 800), "R$ 3.488,00", fontname=FONT, fontsize=9)
    page.insert_text((160, 810), "PP-M", fontname=FONT, fontsize=9)
    page.draw_rect(
        pymupdf.Rect(160, SWATCH_Y, 160 + SWATCH_SIDE, SWATCH_Y + SWATCH_SIDE),
        color=(0.0, 0.0, 0.0),
        fill=(0.50, 0.20, 0.10),
        width=0.5,
    )

    # Produto da direita — CALÇA CAPRI ESTHER (SKU em x≈500)
    page.insert_text((500, 780), "0142500001-0", fontname=FONT, fontsize=9)
    page.insert_text((500, 790), "CALÇA CAPRI ESTHER", fontname=FONT, fontsize=9)
    page.insert_text((500, 800), "R$ 588,00", fontname=FONT, fontsize=9)
    page.insert_text((500, 810), "PP-M", fontname=FONT, fontsize=9)
    page.draw_rect(
        pymupdf.Rect(500, SWATCH_Y, 500 + SWATCH_SIDE, SWATCH_Y + SWATCH_SIDE),
        color=(0.0, 0.0, 0.0),
        fill=(0.20, 0.40, 0.60),
        width=0.5,
    )

    data: bytes = doc.tobytes()
    doc.close()
    return data


def build_sku_9_digits() -> bytes:
    """Catálogo com SKU de 9 dígitos antes do hífen — regressão S05-01.

    Reproduz o cenário VESTIDO SALOMÉ (catálogo Oasis MOTION páginas 30 e 36):
    a agência gerou o catálogo sem o zero à esquerda do código (9 dígitos).
    O analyzer deve detectá-lo como página de produto válida.
    """
    doc, page = _new_doc_with_page()
    _add_decorative_top(page, "VESTIDO SALOMÉ — Coleção Demo")
    # Legenda em linhas separadas para incluir o preço no formato R$ X.XXX,XX.
    page.insert_text((50, 780), "442500908-0", fontname=FONT, fontsize=9)
    page.insert_text((50, 790), "VESTIDO SALOMÉ", fontname=FONT, fontsize=9)
    page.insert_text((50, 800), "R$ 1.288,00", fontname=FONT, fontsize=9)
    page.insert_text((50, 810), "PP-M", fontname=FONT, fontsize=9)
    _add_swatch(page, x=50, fill=(0.40, 0.10, 0.20))
    data: bytes = doc.tobytes()
    doc.close()
    return data


def build_criptografado() -> bytes:
    """PDF protegido por senha — exercita o branch `PDF_ENCRYPTED`."""
    doc, page = _new_doc_with_page()
    _add_legend(
        page,
        x=50,
        sku="0123456789-0",
        grade="PP-M",
        product_name="JAQUETA",
    )
    _add_swatch(page, x=50, fill=(0.40, 0.20, 0.30))
    data: bytes = doc.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret-user",
        owner_pw="secret-owner",
    )
    doc.close()
    return data


# ──────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────


FIXTURES: dict[str, callable] = {  # type: ignore[type-arg]
    "catalogo_1_produto_1_cor.pdf": build_1_produto_1_cor,
    "catalogo_1_produto_2_cores.pdf": build_1_produto_2_cores,
    "catalogo_2_produtos_pagina.pdf": build_2_produtos_pagina,
    "catalogo_pp_g.pdf": build_grade_pp_g,
    "catalogo_sku_9_digitos.pdf": build_sku_9_digits,
    "catalogo_dois_produtos_nomes_distintos.pdf": build_dois_produtos_nomes_distintos,
    "pdf_sem_produtos.pdf": build_sem_produtos,
    "pdf_criptografado.pdf": build_criptografado,
}


def main() -> None:
    print(f"Gerando fixtures em {FIXTURES_DIR}/")
    for name, builder in FIXTURES.items():
        data = builder()
        target = FIXTURES_DIR / name
        target.write_bytes(data)
        print(f"  [OK] {name} ({len(data):,} bytes)")


if __name__ == "__main__":  # pragma: no cover
    main()
