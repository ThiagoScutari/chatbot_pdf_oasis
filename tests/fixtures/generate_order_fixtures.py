"""Gerador de PDFs sintéticos de PEDIDO PREENCHIDO para testes do `orders`.

Estratégia (Sprint 02 / Fase B do PRD):

    catálogo cru (`generate_fixtures.build_*`)
        ↓ PDFAnalyzer.analyze()
    CatalogMetadata
        ↓ FieldInjector.inject()
    PDF com widgets AcroForm vazios (idêntico ao output de produção)
        ↓ post-processamento (rename / fill / flatten)
    PDF de pedido na variante desejada

Reusar a engine real garante que as fixtures não divergem da lógica que o
extractor encontrará em produção.

Cenários (correspondem 1:1 à tabela de fixtures do PRD Sprint 02):

    pedido_preenchido_v2.pdf     → happy path v2 (`qty__SKU__corN__TAM`)
    pedido_preenchido_v1.pdf     → legado v1 (`qty__SKU__TAM`, color_index=1 implícito)
    pedido_campos_vazios.pdf     → AcroForm presente, todos os campos em branco
    pedido_valores_invalidos.pdf → campos com `abc`, `3.5`, `-1`, `0`
    pedido_flattened.pdf         → PDF sem `/AcroForm` (achatado / impresso-pra-PDF)
    pedido_mixed_v1_v2.pdf       → metade dos campos em v1, metade em v2

Os PDFs gerados são commitados em `tests/fixtures/`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pymupdf

from catalogflow.modules.catalog.field_injector import FieldInjector
from catalogflow.modules.catalog.pdf_analyzer import PDFAnalyzer
from tests.fixtures.generate_fixtures import (
    build_1_produto_1_cor,
    build_1_produto_2_cores,
    build_2_produtos_pagina,
)

FIXTURES_DIR = Path(__file__).resolve().parent

# Quantidade por tamanho — cobre tanto valores > 0 quanto células "vazias" (0).
# Mantém variedade para os testes do extractor poderem ranquear SKUs.
QTY_POR_TAMANHO: dict[str, str] = {
    "PP": "2",
    "P": "3",
    "M": "1",
    "G": "4",
    "GG": "0",  # zero deve ser descartado pelo extractor
}

VALORES_INVALIDOS: tuple[str, ...] = ("abc", "3.5", "-1", "0")


# ──────────────────────────────────────────────
#  Helpers — injetar widgets a partir de um catálogo cru
# ──────────────────────────────────────────────


def _inject_acroform(catalog_bytes: bytes) -> bytes:
    """Aplica PDFAnalyzer + FieldInjector — produz o mesmo PDF da Sprint 01."""
    metadata = PDFAnalyzer().analyze(catalog_bytes)
    output_bytes, _warnings = FieldInjector().inject(catalog_bytes, metadata)
    return output_bytes


def _apply(
    pdf_bytes: bytes,
    transform: Callable[[pymupdf.Widget], None],
) -> bytes:
    """Itera os widgets do PDF e aplica `transform` em cada um."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            for widget in page.widgets() or []:
                transform(widget)
        data: bytes = doc.tobytes(clean=True, garbage=4, deflate=True)
    finally:
        doc.close()
    return data


def _strip_cor_segment(field_name: str) -> str:
    """`qty__SKU__corN__TAM` → `qty__SKU__TAM` (formato v1 legado)."""
    parts = field_name.split("__")
    # Esperado: ["qty", "<SKU>", "corN", "<TAM>"]
    if len(parts) == 4 and parts[2].startswith("cor") and parts[2][3:].isdigit():
        return f"{parts[0]}__{parts[1]}__{parts[3]}"
    return field_name


# ──────────────────────────────────────────────
#  Builders por cenário
# ──────────────────────────────────────────────


def build_pedido_v2() -> bytes:
    """Pedido válido em formato v2 — 1 produto, 2 cores, todos os campos preenchidos."""
    injected = _inject_acroform(build_1_produto_2_cores())

    def fill(widget: pymupdf.Widget) -> None:
        # Nome canônico: qty__SKU__corN__TAM — extrai TAM do final.
        size = widget.field_name.rsplit("__", 1)[1]
        widget.field_value = QTY_POR_TAMANHO.get(size, "1")
        widget.update()

    return _apply(injected, fill)


def build_pedido_v1() -> bytes:
    """Pedido em formato legado v1 — 1 produto, 1 cor, campos renomeados sem `__corN__`."""
    injected = _inject_acroform(build_1_produto_1_cor())

    def rename_and_fill(widget: pymupdf.Widget) -> None:
        widget.field_name = _strip_cor_segment(widget.field_name)
        size = widget.field_name.rsplit("__", 1)[1]
        widget.field_value = QTY_POR_TAMANHO.get(size, "1")
        widget.update()

    return _apply(injected, rename_and_fill)


def build_pedido_campos_vazios() -> bytes:
    """Pedido com AcroForm presente mas todos os campos em branco."""
    # Sem transformação — campos já nascem vazios após FieldInjector.inject().
    return _inject_acroform(build_1_produto_1_cor())


def build_pedido_valores_invalidos() -> bytes:
    """Campos preenchidos com lixo: texto, float, negativo, zero — tudo descartável."""
    injected = _inject_acroform(build_1_produto_1_cor())

    counter = {"i": 0}

    def fill_invalid(widget: pymupdf.Widget) -> None:
        widget.field_value = VALORES_INVALIDOS[counter["i"] % len(VALORES_INVALIDOS)]
        counter["i"] += 1
        widget.update()

    return _apply(injected, fill_invalid)


def build_pedido_flattened() -> bytes:
    """PDF sem `/AcroForm` — simula impressão-para-PDF.

    Renderiza visualmente um "pedido preenchido à mão" usando apenas
    `insert_text` e `draw_rect`. Não adiciona nenhum widget — exercita o
    branch `has_acroform=False` / `PDFFlattenedError`.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=595.0, height=842.0)

    # Cabeçalho fictício
    page.insert_text((50, 80), "PEDIDO — Loja Demo", fontname="helv", fontsize=14)
    page.insert_text(
        (50, 110),
        "Quantidades preenchidas à mão, PDF achatado.",
        fontname="helv",
        fontsize=9,
    )

    # Simula uma tabelinha: SKU + PP/P/M/G + quantidades
    headers = ["REF: 0442500941-0", "PP", "P", "M", "G"]
    quantidades = ["", "2", "3", "1", "0"]
    base_y = 160.0
    base_x = 50.0
    col_w = 90.0
    for i, txt in enumerate(headers):
        page.insert_text(
            (base_x + i * col_w, base_y),
            txt,
            fontname="helv",
            fontsize=10,
        )
    for i, q in enumerate(quantidades):
        page.insert_text(
            (base_x + i * col_w, base_y + 20),
            q,
            fontname="helv",
            fontsize=12,
        )

    # Boxes simulando os campos preenchidos
    for i in range(1, 5):
        page.draw_rect(
            pymupdf.Rect(
                base_x + i * col_w - 8,
                base_y + 8,
                base_x + i * col_w + 30,
                base_y + 32,
            ),
            color=(0.5, 0.5, 0.5),
            width=0.5,
        )

    data: bytes = doc.tobytes(clean=True, garbage=4, deflate=True)
    doc.close()
    return data


def build_pedido_mixed_v1_v2() -> bytes:
    """Mix v1+v2 na mesma página.

    Usa `build_2_produtos_pagina` (2 SKUs distintos). Os widgets do primeiro
    SKU são renomeados para v1 (`qty__SKU__TAM`), os do segundo permanecem
    em v2 (`qty__SKU__corN__TAM`). Todos preenchidos.
    """
    injected = _inject_acroform(build_2_produtos_pagina())

    # Descobrir o primeiro SKU presente para escolher quais widgets reescrever.
    doc_peek = pymupdf.open(stream=injected, filetype="pdf")
    skus_encontrados: list[str] = []
    try:
        for page in doc_peek:
            for w in page.widgets() or []:
                # qty__<SKU>__corN__TAM
                sku = w.field_name.split("__")[1]
                if sku not in skus_encontrados:
                    skus_encontrados.append(sku)
    finally:
        doc_peek.close()

    if not skus_encontrados:
        raise RuntimeError("Catálogo base não produziu widgets — fixture inviável")
    sku_v1 = skus_encontrados[0]

    def transform(widget: pymupdf.Widget) -> None:
        sku = widget.field_name.split("__")[1]
        if sku == sku_v1:
            widget.field_name = _strip_cor_segment(widget.field_name)
        size = widget.field_name.rsplit("__", 1)[1]
        widget.field_value = QTY_POR_TAMANHO.get(size, "1")
        widget.update()

    return _apply(injected, transform)


# ──────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────


FIXTURES: dict[str, Callable[[], bytes]] = {
    "pedido_preenchido_v2.pdf": build_pedido_v2,
    "pedido_preenchido_v1.pdf": build_pedido_v1,
    "pedido_campos_vazios.pdf": build_pedido_campos_vazios,
    "pedido_valores_invalidos.pdf": build_pedido_valores_invalidos,
    "pedido_flattened.pdf": build_pedido_flattened,
    "pedido_mixed_v1_v2.pdf": build_pedido_mixed_v1_v2,
}


def main() -> None:
    print(f"Gerando fixtures de pedido em {FIXTURES_DIR}/")
    for name, builder in FIXTURES.items():
        data = builder()
        target = FIXTURES_DIR / name
        target.write_bytes(data)
        print(f"  [OK] {name} ({len(data):,} bytes)")


if __name__ == "__main__":  # pragma: no cover
    main()
