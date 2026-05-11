"""Engine puro que injeta campos AcroForm de pedido nas páginas de produto.

Contrato (CLAUDE.md):
    bytes + CatalogMetadata → bytes
    Zero I/O. Mesma regra do `PDFAnalyzer`.

Constantes (dimensões, paleta, fonte) são idênticas a `oasis_form_v2.py`.
Não as altere sem PR explícito — produzir o mesmo output visual do POC é o
critério de regressão visual da Sprint 01.

Nomenclatura dos widgets:
    qty__<SKU>__cor<N>__<TAM>
        ex: qty__0442500912-0__cor2__M
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pymupdf

from catalogflow.modules.catalog.pdf_analyzer import (
    CatalogMetadata,
    ProductPageMeta,
    SwatchInfo,
)
from catalogflow.shared.errors import PDFCorruptError, PDFEncryptedError

# ──────────────────────────────────────────────
#  Paleta (RGB 0-1) — fiel a oasis_form_v2.py
# ──────────────────────────────────────────────

COR_HEADER_BG: Final[tuple[float, float, float]] = (0.12, 0.10, 0.09)
COR_HEADER_TEXT: Final[tuple[float, float, float]] = (1.00, 1.00, 1.00)
COR_FUNDO_PAINEL: Final[tuple[float, float, float]] = (0.97, 0.96, 0.94)
COR_BORDA_PAINEL: Final[tuple[float, float, float]] = (0.75, 0.72, 0.68)
COR_LABEL_TAM: Final[tuple[float, float, float]] = (0.20, 0.18, 0.16)
COR_LABEL_COR: Final[tuple[float, float, float]] = (0.30, 0.28, 0.26)
COR_CAMPO_FUNDO: Final[tuple[float, float, float]] = (1.00, 1.00, 1.00)
COR_CAMPO_BORDA: Final[tuple[float, float, float]] = (0.60, 0.57, 0.53)
COR_CAMPO_TEXTO: Final[tuple[float, float, float]] = (0.08, 0.08, 0.08)
COR_LINHA_DIV: Final[tuple[float, float, float]] = (0.82, 0.79, 0.75)
COR_SWATCH_BORDA: Final[tuple[float, float, float]] = (0.40, 0.40, 0.40)

# ──────────────────────────────────────────────
#  Dimensões (pontos PDF) — fiel ao POC
# ──────────────────────────────────────────────

HEADER_H: Final[int] = 22
LABEL_TAM_H: Final[int] = 20
CAMPO_H: Final[int] = 38
COR_COL_W: Final[int] = 70
CAMPO_W: Final[int] = 82
PAD_V: Final[int] = 8
PAD_H: Final[int] = 6
SWATCH_SZ: Final[int] = 14
PAD_FIELD: Final[int] = 4
FONTE: Final[str] = "helv"

PAGE_RIGHT_MARGIN: Final[int] = 16
PAGE_BOTTOM_MARGIN: Final[int] = 4
PANEL_OFFSET_X: Final[int] = 18
MIN_CAMPO_W: Final[int] = 50
NEIGHBOR_MIN_GAP: Final[int] = 16


# ──────────────────────────────────────────────
#  Dataclass auxiliar
# ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PanelRect:
    """Retângulo do painel de pedido + largura efetiva por coluna de tamanho."""

    x0: float
    y0: float
    x1: float
    y1: float
    campo_w: float


# ──────────────────────────────────────────────
#  FieldInjector
# ──────────────────────────────────────────────


class FieldInjector:
    """Insere campos AcroForm nas páginas de produto detectadas."""

    def inject(self, pdf_bytes: bytes, metadata: CatalogMetadata) -> bytes:
        """Recebe o PDF original + metadados e devolve o PDF anotado.

        O total de widgets inseridos pode ser inspecionado via
        `count_fields(metadata)` antes da injeção (cálculo determinístico).
        """
        if not pdf_bytes:
            raise PDFCorruptError("pdf vazio", code="PDF_CORRUPT")

        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            raise PDFCorruptError(
                "pdf corrompido ou em formato inválido",
                code="PDF_CORRUPT",
                details={"reason": str(exc)},
            ) from exc

        try:
            if doc.is_encrypted:
                raise PDFEncryptedError(
                    "pdf protegido por senha",
                    code="PDF_ENCRYPTED",
                )

            for product in metadata.product_pages:
                page = doc[product.page_index]
                siblings = [
                    p
                    for p in metadata.product_pages
                    if p.page_index == product.page_index
                ]
                rect = self._calculate_panel_rect(
                    product,
                    page_w=float(page.rect.width),
                    page_h=float(page.rect.height),
                    siblings=siblings,
                )
                self._draw_panel(page, product, rect)

            data: bytes = doc.tobytes(clean=True, garbage=4, deflate=True)
        finally:
            doc.close()
        return data

    # ── Cálculo de geometria ──────────────────

    def _calculate_panel_rect(
        self,
        product: ProductPageMeta,
        *,
        page_w: float,
        page_h: float,
        siblings: list[ProductPageMeta],
    ) -> PanelRect:
        """Replica `calcular_painel_rect` do POC, incluindo compressão à esquerda."""
        sizes = product.sizes
        n_cores = max(1, product.n_colors)
        n_tam = len(sizes)

        painel_w = COR_COL_W + n_tam * CAMPO_W + 2 * PAD_H
        painel_h = PAD_V + HEADER_H + LABEL_TAM_H + n_cores * CAMPO_H + PAD_V

        # Ancora vertical no topo do bloco de legenda.
        y0 = product.y_start - PAD_V
        y1 = y0 + painel_h
        if y1 > page_h - PAGE_BOTTOM_MARGIN:
            y1 = page_h - PAGE_BOTTOM_MARGIN
            y0 = y1 - painel_h

        # Horizontal: à direita do bloco de texto.
        x0 = product.x_block_end + PANEL_OFFSET_X
        x1 = x0 + painel_w
        if x1 > page_w - PAGE_RIGHT_MARGIN:
            x0 = page_w - painel_w - PAGE_RIGHT_MARGIN
            x1 = page_w - PAGE_RIGHT_MARGIN

        campo_w: float = float(CAMPO_W)

        # Compressão à esquerda quando há produto direito ocupando o espaço.
        if product.side == "left":
            forbidden = min(
                (p.x_block_start for p in siblings if p.side == "right"),
                default=page_w,
            )
            if x1 > forbidden - NEIGHBOR_MIN_GAP:
                x1 = forbidden - NEIGHBOR_MIN_GAP
                available = x1 - x0
                campo_w_int = max(
                    MIN_CAMPO_W,
                    int((available - COR_COL_W - 2 * PAD_H) // n_tam),
                )
                campo_w = float(campo_w_int)
                painel_w = COR_COL_W + n_tam * campo_w_int + 2 * PAD_H
                x1 = x0 + painel_w

        return PanelRect(
            x0=float(x0),
            y0=float(y0),
            x1=float(x1),
            y1=float(y1),
            campo_w=campo_w,
        )

    # ── Desenho + widgets ─────────────────────

    def _draw_panel(
        self,
        page: pymupdf.Page,
        product: ProductPageMeta,
        rect: PanelRect,
    ) -> int:
        """Desenha o painel visual e adiciona widgets. Retorna nº de widgets."""
        sku = product.sku
        grade = product.grade
        sizes = product.sizes
        n_cores = max(1, product.n_colors)
        n_tam = len(sizes)
        swatches = product.swatches
        x0, y0, x1, y1, campo_w = rect.x0, rect.y0, rect.x1, rect.y1, rect.campo_w

        # Fundo do painel.
        page.draw_rect(
            pymupdf.Rect(x0, y0, x1, y1),
            color=COR_BORDA_PAINEL,
            fill=COR_FUNDO_PAINEL,
            width=0.7,
        )

        # Header "PEDIDO > grade".
        y_hdr = y0
        page.draw_rect(
            pymupdf.Rect(x0, y_hdr, x1, y_hdr + HEADER_H),
            color=None,
            fill=COR_HEADER_BG,
            width=0,
        )
        page.insert_textbox(
            pymupdf.Rect(x0 + 5, y_hdr + 3, x1 - 3, y_hdr + HEADER_H),
            f"PEDIDO  >  {grade}",
            fontname=FONTE,
            fontsize=9,
            color=COR_HEADER_TEXT,
            align=pymupdf.TEXT_ALIGN_LEFT,
        )

        # Labels de tamanho.
        y_tam = y_hdr + HEADER_H
        for i, tam in enumerate(sizes):
            xc = x0 + COR_COL_W + i * campo_w
            page.insert_textbox(
                pymupdf.Rect(xc, y_tam, xc + campo_w, y_tam + LABEL_TAM_H),
                tam,
                fontname=FONTE,
                fontsize=10,
                color=COR_LABEL_TAM,
                align=pymupdf.TEXT_ALIGN_CENTER,
            )

        # Linha divisória entre header de tamanhos e o grid.
        y_div = y_tam + LABEL_TAM_H
        page.draw_line(
            pymupdf.Point(x0 + 2, y_div),
            pymupdf.Point(x1 - 2, y_div),
            color=COR_LINHA_DIV,
            width=0.5,
        )

        n_fields = 0
        for ci in range(n_cores):
            y_row = y_div + ci * CAMPO_H
            swatch = swatches[ci] if ci < len(swatches) else None
            txt_x = self._draw_color_label(page, x0=x0, y_row=y_row, swatch=swatch)
            page.insert_textbox(
                pymupdf.Rect(
                    txt_x,
                    y_row + 2,
                    x0 + COR_COL_W - 2,
                    y_row + CAMPO_H - 2,
                ),
                f"Cor {ci + 1}",
                fontname=FONTE,
                fontsize=8,
                color=COR_LABEL_COR,
                align=pymupdf.TEXT_ALIGN_LEFT,
            )

            # Linha divisória vertical entre coluna de cor e tamanhos.
            page.draw_line(
                pymupdf.Point(x0 + COR_COL_W, y_row),
                pymupdf.Point(x0 + COR_COL_W, y_row + CAMPO_H),
                color=COR_LINHA_DIV,
                width=0.5,
            )

            for ti, tam in enumerate(sizes):
                self._add_widget(
                    page,
                    sku=sku,
                    color_index=ci + 1,
                    size=tam,
                    x0=x0,
                    y_row=y_row,
                    ti=ti,
                    campo_w=campo_w,
                )
                n_fields += 1

            # Linha divisória entre linhas de cor.
            if ci < n_cores - 1:
                y_sep = y_row + CAMPO_H
                page.draw_line(
                    pymupdf.Point(x0 + 2, y_sep),
                    pymupdf.Point(x1 - 2, y_sep),
                    color=COR_LINHA_DIV,
                    width=0.3,
                )

        return n_fields

    def _draw_color_label(
        self,
        page: pymupdf.Page,
        *,
        x0: float,
        y_row: float,
        swatch: SwatchInfo | None,
    ) -> float:
        """Desenha o swatch (se houver) e retorna o x onde o label da cor começa."""
        if swatch is None:
            return x0 + PAD_H
        sq_x = x0 + PAD_H
        sq_y = y_row + (CAMPO_H - SWATCH_SZ) / 2.0
        page.draw_rect(
            pymupdf.Rect(sq_x, sq_y, sq_x + SWATCH_SZ, sq_y + SWATCH_SZ),
            color=COR_SWATCH_BORDA,
            fill=swatch.fill_rgb,
            width=0.5,
        )
        return sq_x + SWATCH_SZ + 4

    def _add_widget(
        self,
        page: pymupdf.Page,
        *,
        sku: str,
        color_index: int,
        size: str,
        x0: float,
        y_row: float,
        ti: int,
        campo_w: float,
    ) -> None:
        """Cria e adiciona o widget AcroForm para uma célula (cor × tamanho)."""
        xc = x0 + COR_COL_W + ti * campo_w
        rect = pymupdf.Rect(
            xc + PAD_FIELD,
            y_row + PAD_FIELD,
            xc + campo_w - PAD_FIELD,
            y_row + CAMPO_H - PAD_FIELD,
        )

        widget = pymupdf.Widget()
        widget.rect = rect
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = field_name_for(sku, color_index, size)
        widget.field_value = ""
        widget.text_maxlen = 4
        widget.text_fontsize = 13
        widget.text_font = FONTE
        widget.text_color = COR_CAMPO_TEXTO
        widget.fill_color = COR_CAMPO_FUNDO
        widget.border_color = COR_CAMPO_BORDA
        widget.border_width = 0.8
        widget.field_label = f"Qtd {size} / Cor {color_index} - {sku}"
        page.add_widget(widget)


# ──────────────────────────────────────────────
#  Helpers públicos
# ──────────────────────────────────────────────


def field_name_for(sku: str, color_index: int, size: str) -> str:
    """Nome canônico do widget — fonte única de verdade para v2 do formato.

    A extração de pedido (Sprint 02) também aceita o formato legado v1
    (sem `__cor<N>__`) — mas escritas pelo CatalogFlow sempre usam v2.
    """
    return f"qty__{sku}__cor{color_index}__{size}"


def count_fields(metadata: CatalogMetadata) -> int:
    """Quantidade total de widgets que `inject()` produzirá para `metadata`.

    Calculado a partir dos metadados, sem abrir o PDF — útil para o service
    persistir `Catalog.n_fields` antes mesmo da injeção.
    """
    return sum(
        max(1, p.n_colors) * len(p.sizes) for p in metadata.product_pages
    )
