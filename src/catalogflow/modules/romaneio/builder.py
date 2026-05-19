# mypy: disable-error-code="no-untyped-call,attr-defined"
# ↑ pymupdf não tem stubs de tipo; este módulo chama pymupdf extensivamente
# (Document, Rect, Point, Widget, add_widget, draw_rect, insert_text, etc.).
# As supressões por linha gerariam dezenas de comentários repetitivos — a
# pragma de arquivo é o idioma do mypy para libs externas sem stubs. Quando
# pymupdf publicar stubs (ou se migrarmos para PyPDFForm com stubs), basta
# remover esta linha.
"""Engine puro de construção do PDF de romaneio.

Contrato (CLAUDE.md):
    NormalizedOrderData + RomaneioConfig → bytes
    Zero I/O. O service é quem faz upload pro storage.

Layout (baseado em `oasis_romaneio.py` + `example/romaneio_demo.pdf`):
    - Cabeçalho: faixa brand com logo opcional + título + lojista + data
    - Por SKU: bloco com nome, ref, preço unitário, grid cor x tamanho, subtotal
    - Paginação automática — cabeçalho repetido em cada nova página
    - Rodapé final: total de peças, total de SKUs, valor total
    - Formato monetário pt_BR via string mangling (sem `locale.setlocale`)
    - Datas em pt_BR via `strftime("%d/%m/%Y")`
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

import pymupdf

from catalogflow.modules.orders.normalizer import (
    NormalizedOrderData,
    NormalizedOrderItem,
    NormalizedTotals,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  Paleta (RGB 0-1) — adaptada de oasis_romaneio.py
# ──────────────────────────────────────────────

C_PRETO: Final[tuple[float, float, float]] = (0.05, 0.05, 0.05)
C_BRAND: Final[tuple[float, float, float]] = (0.12, 0.10, 0.09)
C_BRAND_T: Final[tuple[float, float, float]] = (1.00, 1.00, 1.00)
C_ACENTO: Final[tuple[float, float, float]] = (0.65, 0.50, 0.25)
C_VERDE: Final[tuple[float, float, float]] = (0.10, 0.55, 0.25)
C_TEXTO: Final[tuple[float, float, float]] = (0.18, 0.16, 0.14)
C_MUTED: Final[tuple[float, float, float]] = (0.50, 0.48, 0.45)
C_BRANCO: Final[tuple[float, float, float]] = (1.00, 1.00, 1.00)
C_BORDA: Final[tuple[float, float, float]] = (0.80, 0.78, 0.75)
C_CINZA_E: Final[tuple[float, float, float]] = (0.92, 0.91, 0.89)
C_CINZA_C: Final[tuple[float, float, float]] = (0.75, 0.73, 0.70)
C_FUNDO_ZEBRA: Final[tuple[float, float, float]] = (0.95, 0.94, 0.92)
C_HEADER_GRADE: Final[tuple[float, float, float]] = (0.88, 0.86, 0.83)
C_BRAND_ACCENT_T: Final[tuple[float, float, float]] = (0.80, 0.75, 0.65)

# Cores específicas para a linha "Disponível" do relatório de pendências.
# Espelham as cores do CSS (--color-stock-ok / partial / out).
C_STOCK_OK: Final[tuple[float, float, float]] = (0.18, 0.36, 0.24)
C_STOCK_PARTIAL: Final[tuple[float, float, float]] = (0.44, 0.31, 0.07)
C_STOCK_OUT: Final[tuple[float, float, float]] = (0.54, 0.16, 0.13)
C_PENDENCY_BG: Final[tuple[float, float, float]] = (0.97, 0.965, 0.96)

# ──────────────────────────────────────────────
#  Dimensões (A4 portrait — PRD margem 40pt)
# ──────────────────────────────────────────────

PAGE_W: Final[float] = 595.0
PAGE_H: Final[float] = 842.0
MARGIN_X: Final[float] = 40.0
MARGIN_Y: Final[float] = 40.0
CONTENT_W: Final[float] = PAGE_W - 2 * MARGIN_X

FONT: Final[str] = "helv"
FONT_B: Final[str] = "hebo"

# Geometria da grade cor x tamanho
COL_COR_W: Final[float] = 70.0
COL_TAM_W: Final[float] = 60.0
COL_TOTAL_W: Final[float] = 55.0
ROW_H_DADOS: Final[float] = 20.0
ROW_H_HEADER: Final[float] = 18.0
ROW_H_SKU: Final[float] = 24.0
BLOCO_PADDING_BOTTOM: Final[float] = 12.0

# Foto do produto (Sprint 04) — quadrado pequeno à esquerda do SKU header.
# Pequeno o suficiente para não dominar o layout, grande o bastante para
# a Loja reconhecer visualmente o produto.
PRODUCT_IMAGE_SIZE: Final[float] = 50.0
PRODUCT_IMAGE_GAP: Final[float] = 10.0
# Quando há foto, a seção SKU header expande verticalmente para acomodar
# a altura da imagem (50 + 3pt padding em cima e em baixo).
ROW_H_SKU_WITH_IMAGE: Final[float] = 56.0

# Tamanhos canônicos do romaneio — ordem fixa de exibição
COLS_TAMANHOS: Final[tuple[str, ...]] = ("PP", "P", "M", "G", "GG")

# Reserva de espaço para o totalizador final + rodapé
FOOTER_RESERVE: Final[float] = 100.0


# ──────────────────────────────────────────────
#  Config + helpers
# ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RomaneioConfig:
    """Parâmetros visuais e de contexto para a geração do PDF.

    Mescla branding (brand_name, logo_bytes, currency_symbol) e metadata
    por pedido (lojista_name, emitted_at, collection). PRD especifica
    `build(order_data, config)` — dois argumentos.

    `footer_note` (Sprint 04) é uma frase em itálico colocada abaixo do
    totalizador — usada pelo relatório de pendências para a mensagem
    "Itens acima não puderam ser atendidos integralmente." Vazio nos
    romaneios normais (default None).
    """

    brand_name: str
    logo_bytes: bytes | None = None
    lojista_name: str = "—"
    emitted_at: datetime | None = None  # None → resolvido para `datetime.now()`
    collection: str | None = None
    title: str = "ROMANEIO DE PEDIDO"
    show_prices: bool = True
    currency_symbol: str = "R$"
    footer_note: str | None = None


def format_currency(value: Decimal | float | int, symbol: str = "R$") -> str:
    """`R$ 1.598,00` — formato pt_BR sem `locale.setlocale`."""
    v = float(value)
    formatted = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{symbol} {formatted}"


def format_date_pt_br(dt: datetime) -> str:
    """`11/05/2026 14:22` — formato brasileiro com hora."""
    return dt.strftime("%d/%m/%Y  %H:%M")


# ──────────────────────────────────────────────
#  Builder
# ──────────────────────────────────────────────


AvailableMap = dict[tuple[str, int, str], int | None]
ProductImages = dict[str, bytes]


class RomaneioBuilder:
    """Gera o PDF do romaneio. Zero I/O — input bytes/dataclasses, output bytes."""

    def build(
        self,
        order_data: NormalizedOrderData,
        config: RomaneioConfig,
        *,
        available_map: AvailableMap | None = None,
        product_images: ProductImages | None = None,
    ) -> bytes:
        """Retorna o PDF em bytes. Suporta pedido vazio (gera apenas cabeçalho).

        Quando `available_map` é fornecido, cada cor de cada SKU ganha uma
        sub-linha "Disponível" diretamente abaixo da linha de pedido, com
        a quantidade disponível por tamanho colorida conforme a regra
        contábil (verde / âmbar / vermelho). Usado pelo relatório de
        pendências da Sprint 04.

        Quando `product_images` é fornecido (dict `{sku: bytes}`), cada SKU
        cujo código está no dict ganha um thumbnail 50x50pt à esquerda do
        cabeçalho do bloco. SKUs ausentes do dict renderizam sem foto —
        sem placeholder. Bytes inválidos não derrubam o PDF (pymupdf
        levanta e capturamos no `_draw_product_block`).
        """
        doc = pymupdf.open()
        try:
            emitted_at = config.emitted_at or datetime.now()
            page, y = self._start_page(doc, config, emitted_at)

            grouped = self._group_by_sku(order_data.items)

            for sku, items_sku in grouped.items():
                has_image = bool(product_images and sku in product_images)
                block_h = self._estimate_block_height(
                    items_sku,
                    include_pendency_row=available_map is not None,
                    has_image=has_image,
                )
                if y + block_h > PAGE_H - FOOTER_RESERVE:
                    page, y = self._start_page(doc, config, emitted_at)
                y = self._draw_product_block(
                    page=page,
                    sku=sku,
                    items=items_sku,
                    y_start=y,
                    config=config,
                    available_map=available_map,
                    product_images=product_images,
                )

            # Totalizador — se não couber, nova página apenas para ele.
            if y + 70.0 > PAGE_H - MARGIN_Y:
                page, y = self._start_page(doc, config, emitted_at)
            self._draw_totalizer(page, order_data.totals, y, config)

            if config.footer_note:
                self._draw_footer_note(page, config.footer_note)

            data: bytes = doc.tobytes(clean=True, garbage=4, deflate=True)
        finally:
            doc.close()
        return data

    # ── Estado / iteração ─────────────────────

    def _start_page(
        self,
        doc: pymupdf.Document,
        config: RomaneioConfig,
        emitted_at: datetime,
    ) -> tuple[pymupdf.Page, float]:
        """Cria nova página, desenha cabeçalho, devolve (page, y_inicial_conteudo)."""
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        y = self._draw_header(page, config, emitted_at)
        return page, y

    def _group_by_sku(
        self,
        items: list[NormalizedOrderItem],
    ) -> dict[str, list[NormalizedOrderItem]]:
        """Mantém a ordem em que os SKUs aparecem (normalizer já ordenou)."""
        grouped: dict[str, list[NormalizedOrderItem]] = defaultdict(list)
        for item in items:
            grouped[item.sku].append(item)
        return grouped

    def _estimate_block_height(
        self,
        items: list[NormalizedOrderItem],
        *,
        include_pendency_row: bool = False,
        has_image: bool = False,
    ) -> float:
        n_colors = len({item.color_index for item in items})
        per_color = ROW_H_DADOS * (2 if include_pendency_row else 1)
        sku_h = ROW_H_SKU_WITH_IMAGE if has_image else ROW_H_SKU
        return sku_h + ROW_H_HEADER + n_colors * per_color + BLOCO_PADDING_BOTTOM

    # ── Cabeçalho ─────────────────────────────

    def _draw_header(
        self,
        page: pymupdf.Page,
        config: RomaneioConfig,
        emitted_at: datetime,
    ) -> float:
        """Faixa brand + título + logo (se houver) + lojista + data. Retorna y após."""
        y = MARGIN_Y
        header_h = 56.0

        # Faixa brand
        page.draw_rect(
            pymupdf.Rect(0, y, PAGE_W, y + header_h),
            color=None,
            fill=C_BRAND,
            width=0,
        )

        # Logo opcional — quadrado 40x40 à esquerda da faixa
        text_x = MARGIN_X
        if config.logo_bytes is not None:
            logo_size = 40.0
            logo_rect = pymupdf.Rect(
                MARGIN_X,
                y + (header_h - logo_size) / 2,
                MARGIN_X + logo_size,
                y + (header_h + logo_size) / 2,
            )
            try:
                page.insert_image(logo_rect, stream=config.logo_bytes)
                text_x = MARGIN_X + logo_size + 12
            except Exception:
                # Logo corrompida não derruba o romaneio — segue só textual.
                text_x = MARGIN_X

        page.insert_text(
            (text_x, y + 24),
            config.brand_name.upper(),
            fontname=FONT_B,
            fontsize=16,
            color=C_BRAND_T,
        )
        subtitle = config.title
        if config.collection:
            subtitle = f"{config.title}  -  {config.collection}"
        page.insert_text(
            (text_x, y + 44),
            subtitle,
            fontname=FONT,
            fontsize=9,
            color=C_BRAND_ACCENT_T,
        )
        y += header_h + 6

        # Linha de info: lojista (esq) + data (dir)
        page.insert_text(
            (MARGIN_X, y + 12),
            f"Lojista:  {config.lojista_name}",
            fontname=FONT_B,
            fontsize=9,
            color=C_TEXTO,
        )
        data_txt = f"Emitido em:  {format_date_pt_br(emitted_at)}"
        w_data = pymupdf.get_text_length(data_txt, fontname=FONT, fontsize=9)
        page.insert_text(
            (PAGE_W - MARGIN_X - w_data, y + 12),
            data_txt,
            fontname=FONT,
            fontsize=9,
            color=C_MUTED,
        )
        y += 22

        # Divisor dourado
        page.draw_line(
            pymupdf.Point(MARGIN_X, y),
            pymupdf.Point(PAGE_W - MARGIN_X, y),
            color=C_ACENTO,
            width=1.5,
        )
        y += 10
        return y

    # ── Header da grade de tamanhos ───────────

    def _draw_grade_header(
        self,
        page: pymupdf.Page,
        y: float,
        x_cor: float,
        x_tams: float,
        x_total: float,
    ) -> float:
        page.draw_rect(
            pymupdf.Rect(MARGIN_X, y, PAGE_W - MARGIN_X, y + ROW_H_HEADER),
            color=None,
            fill=C_HEADER_GRADE,
            width=0,
        )
        page.insert_textbox(
            pymupdf.Rect(x_cor, y + 2, x_cor + COL_COR_W, y + ROW_H_HEADER),
            "Cor",
            fontname=FONT_B,
            fontsize=8,
            color=C_TEXTO,
            align=pymupdf.TEXT_ALIGN_LEFT,
        )
        for i, tam in enumerate(COLS_TAMANHOS):
            xc = x_tams + i * COL_TAM_W
            page.insert_textbox(
                pymupdf.Rect(xc, y + 2, xc + COL_TAM_W, y + ROW_H_HEADER),
                tam,
                fontname=FONT_B,
                fontsize=8,
                color=C_TEXTO,
                align=pymupdf.TEXT_ALIGN_CENTER,
            )
        page.insert_textbox(
            pymupdf.Rect(x_total, y + 2, x_total + COL_TOTAL_W, y + ROW_H_HEADER),
            "TOTAL",
            fontname=FONT_B,
            fontsize=8,
            color=C_TEXTO,
            align=pymupdf.TEXT_ALIGN_CENTER,
        )
        return y + ROW_H_HEADER

    # ── Foto do produto (Sprint 04) ───────────

    def _draw_product_image(
        self,
        page: pymupdf.Page,
        y_start: float,
        image_bytes: bytes | None,
    ) -> bool:
        """Tenta inserir a foto 50x50pt à esquerda do bloco. Retorna True se
        a imagem foi desenhada com sucesso.

        Foto é melhoria visual — bytes inválidos NÃO derrubam o PDF.
        pymupdf levanta se a stream não for um formato suportado (JPG,
        PNG, etc.); capturamos qualquer exceção e seguimos sem foto.
        """
        if not image_bytes:
            return False
        try:
            rect = pymupdf.Rect(
                MARGIN_X,
                y_start + 3,
                MARGIN_X + PRODUCT_IMAGE_SIZE,
                y_start + 3 + PRODUCT_IMAGE_SIZE,
            )
            page.insert_image(rect, stream=image_bytes, keep_proportion=True)
            return True
        except Exception:
            # Bytes corrompidos, formato não suportado, etc. — segue sem foto.
            logger.warning(
                "romaneio: falha ao embedar foto de produto, seguindo sem ela",
                exc_info=True,
            )
            return False

    # ── Bloco de produto ──────────────────────

    def _draw_product_block(
        self,
        *,
        page: pymupdf.Page,
        sku: str,
        items: list[NormalizedOrderItem],
        y_start: float,
        config: RomaneioConfig,
        available_map: AvailableMap | None = None,
        product_images: ProductImages | None = None,
    ) -> float:
        """Desenha um bloco SKU: linha de cabeçalho + grade cor x tamanho. Retorna y após.

        Quando `product_images` contém o SKU, insere um thumbnail 50x50pt
        à esquerda do cabeçalho. O texto do produto desloca para acomodar
        a imagem, e a altura do cabeçalho expande para 56pt. Sem imagem,
        o layout permanece idêntico ao original.
        """
        product_name = items[0].product_name or sku
        unit_price = items[0].unit_price  # mesmo para todas as linhas do SKU
        total_pecas_sku = sum(item.quantity for item in items)
        subtotal_sku = sum(
            (item.subtotal for item in items if item.subtotal is not None),
            start=Decimal("0"),
        )

        # Foto do produto — opcional, à esquerda do cabeçalho.
        image_bytes = product_images.get(sku) if product_images else None
        has_image = self._draw_product_image(page, y_start, image_bytes)

        # Layout do texto: desloca para a direita quando há imagem.
        if has_image:
            text_x = MARGIN_X + PRODUCT_IMAGE_SIZE + PRODUCT_IMAGE_GAP
            # Vertical center dentro de ROW_H_SKU_WITH_IMAGE.
            text_y_name = y_start + 24
            text_y_ref = y_start + 38
            sku_section_h = ROW_H_SKU_WITH_IMAGE
        else:
            text_x = MARGIN_X + 4
            text_y_name = y_start + 14
            text_y_ref = y_start + 14
            sku_section_h = ROW_H_SKU

        # Linha de SKU
        page.insert_text(
            (text_x, text_y_name),
            product_name.upper(),
            fontname=FONT_B,
            fontsize=9,
            color=C_TEXTO,
        )
        # "Ref: <sku>" — quando há imagem, vai abaixo do nome para evitar
        # competir pelo mesmo eixo X. Sem imagem, fica na mesma linha do nome.
        ref_x = text_x if has_image else MARGIN_X + 240
        page.insert_text(
            (ref_x, text_y_ref),
            f"Ref: {sku}",
            fontname=FONT,
            fontsize=7,
            color=C_MUTED,
        )

        resumo = self._build_resumo_text(
            unit_price=unit_price,
            total_pecas=total_pecas_sku,
            subtotal=subtotal_sku,
            config=config,
        )
        if resumo:
            w_res = pymupdf.get_text_length(resumo, fontname=FONT_B, fontsize=8)
            # Resumo sempre alinhado à direita — com imagem, fica no topo
            # (linha do nome); sem imagem, mesma linha de tudo (compat).
            resumo_y = text_y_name
            page.insert_text(
                (PAGE_W - MARGIN_X - 4 - w_res, resumo_y),
                resumo,
                fontname=FONT_B,
                fontsize=8,
                color=C_ACENTO,
            )
        y = y_start + sku_section_h

        # Calcula posições x — alinhado à margem esquerda
        x_cor = MARGIN_X + 4
        x_tams = x_cor + COL_COR_W
        x_total = x_tams + len(COLS_TAMANHOS) * COL_TAM_W

        y = self._draw_grade_header(page, y, x_cor, x_tams, x_total)

        # Linhas de cor — agrupa items por color_index
        by_color: dict[int, dict[str, int]] = defaultdict(dict)
        color_hex_by_idx: dict[int, str | None] = {}
        for item in items:
            by_color[item.color_index][item.size] = item.quantity
            color_hex_by_idx.setdefault(item.color_index, item.color_hex)

        for ci, color_index in enumerate(sorted(by_color.keys())):
            row_y = y
            if ci % 2 == 1:
                page.draw_rect(
                    pymupdf.Rect(
                        MARGIN_X + 2,
                        row_y,
                        PAGE_W - MARGIN_X - 2,
                        row_y + ROW_H_DADOS,
                    ),
                    color=None,
                    fill=C_FUNDO_ZEBRA,
                    width=0,
                )

            # Label da cor (com hex se disponível)
            color_hex = color_hex_by_idx.get(color_index)
            cor_label = f"Cor {color_index}  {color_hex}" if color_hex else f"Cor {color_index}"
            page.insert_textbox(
                pymupdf.Rect(
                    x_cor + 2,
                    row_y + 2,
                    x_cor + COL_COR_W - 2,
                    row_y + ROW_H_DADOS,
                ),
                cor_label,
                fontname=FONT,
                fontsize=8,
                color=C_TEXTO,
                align=pymupdf.TEXT_ALIGN_LEFT,
            )

            qtys_for_color = by_color[color_index]
            total_cor = 0
            for ti, tam in enumerate(COLS_TAMANHOS):
                qtd = qtys_for_color.get(tam, 0)
                total_cor += qtd
                x_cel = x_tams + ti * COL_TAM_W
                page.insert_textbox(
                    pymupdf.Rect(
                        x_cel,
                        row_y + 2,
                        x_cel + COL_TAM_W,
                        row_y + ROW_H_DADOS,
                    ),
                    str(qtd) if qtd > 0 else "-",
                    fontname=FONT_B if qtd > 0 else FONT,
                    fontsize=9,
                    color=C_PRETO if qtd > 0 else C_TEXTO,
                    align=pymupdf.TEXT_ALIGN_CENTER,
                )

            page.insert_textbox(
                pymupdf.Rect(
                    x_total,
                    row_y + 2,
                    x_total + COL_TOTAL_W,
                    row_y + ROW_H_DADOS,
                ),
                str(total_cor),
                fontname=FONT_B,
                fontsize=9,
                color=C_VERDE if total_cor > 0 else C_MUTED,
                align=pymupdf.TEXT_ALIGN_CENTER,
            )
            y += ROW_H_DADOS

            # Sub-linha "Disponível" — só aparece quando o caller pediu
            # (available_map is not None) e há ao menos um size deste
            # (sku, cor) no mapa. Caso contrário a linha é omitida.
            if available_map is not None:
                qtys_for_color = by_color[color_index]
                color_has_stock = any(
                    (sku, color_index, tam) in available_map for tam in COLS_TAMANHOS
                )
                if color_has_stock:
                    y = self._draw_pendency_row(
                        page=page,
                        y=y,
                        sku=sku,
                        color_index=color_index,
                        qtys_for_color=qtys_for_color,
                        available_map=available_map,
                        x_cor=x_cor,
                        x_tams=x_tams,
                        x_total=x_total,
                    )

        # Separador entre blocos
        page.draw_line(
            pymupdf.Point(MARGIN_X, y + 4),
            pymupdf.Point(PAGE_W - MARGIN_X, y + 4),
            color=C_CINZA_C,
            width=0.5,
        )
        return y + BLOCO_PADDING_BOTTOM

    # ── Sub-linha "Disponível" (relatório de pendências) ──

    def _draw_pendency_row(
        self,
        *,
        page: pymupdf.Page,
        y: float,
        sku: str,
        color_index: int,
        qtys_for_color: dict[str, int],
        available_map: AvailableMap,
        x_cor: float,
        x_tams: float,
        x_total: float,
    ) -> float:
        """Desenha a sub-linha com a quantidade disponível por tamanho.

        Layout: mesmas colunas da linha de pedido. Background levemente
        diferente para diferenciar visualmente, sem borda superior — fica
        visualmente "grudada" na linha de pedido.
        """
        page.draw_rect(
            pymupdf.Rect(
                MARGIN_X + 2,
                y,
                PAGE_W - MARGIN_X - 2,
                y + ROW_H_DADOS,
            ),
            color=None,
            fill=C_PENDENCY_BG,
            width=0,
        )

        # Label "Disponível" na coluna de cor
        page.insert_textbox(
            pymupdf.Rect(
                x_cor + 2,
                y + 2,
                x_cor + COL_COR_W - 2,
                y + ROW_H_DADOS,
            ),
            "Disponível",
            fontname=FONT,
            fontsize=7,
            color=C_MUTED,
            align=pymupdf.TEXT_ALIGN_LEFT,
        )

        avail_total = 0
        avail_any = False
        for ti, tam in enumerate(COLS_TAMANHOS):
            requested = qtys_for_color.get(tam, 0)
            available = available_map.get((sku, color_index, tam))
            x_cel = x_tams + ti * COL_TAM_W

            # Regra (PMO):
            #   requested == 0                  → "-" muted (não pedido)
            #   requested  > 0, available None  → "0" out  (tratar ausência
            #                                     como zero — sem retorno do
            #                                     ERP equivale a zerado para
            #                                     o relatório de pendências)
            #   requested  > 0, available valor → str(available) colorido
            # `helv` (Helvetica core PDF font) NÃO contém em-dash (U+2014) —
            # pymupdf cai no fallback "?". Por isso usamos hyphen ASCII "-",
            # idêntico ao padrão do resto do builder.
            if requested == 0:
                text = "-"
                color = C_MUTED
                fontname = FONT
            elif available is None:
                text = "0"
                color = C_STOCK_OUT
                fontname = FONT_B
            else:
                avail_any = True
                avail_total += available
                text = str(available)
                fontname = FONT_B
                if available <= 0:
                    color = C_STOCK_OUT
                elif available >= requested:
                    color = C_STOCK_OK
                else:
                    color = C_STOCK_PARTIAL

            page.insert_textbox(
                pymupdf.Rect(
                    x_cel,
                    y + 2,
                    x_cel + COL_TAM_W,
                    y + ROW_H_DADOS,
                ),
                text,
                fontname=fontname,
                fontsize=9,
                color=color,
                align=pymupdf.TEXT_ALIGN_CENTER,
            )

        page.insert_textbox(
            pymupdf.Rect(
                x_total,
                y + 2,
                x_total + COL_TOTAL_W,
                y + ROW_H_DADOS,
            ),
            str(avail_total) if avail_any else "-",
            fontname=FONT_B if avail_any else FONT,
            fontsize=9,
            color=C_MUTED if not avail_any else (C_STOCK_OK if avail_total > 0 else C_MUTED),
            align=pymupdf.TEXT_ALIGN_CENTER,
        )
        return y + ROW_H_DADOS

    def _build_resumo_text(
        self,
        *,
        unit_price: Decimal | None,
        total_pecas: int,
        subtotal: Decimal,
        config: RomaneioConfig,
    ) -> str:
        """`R$ 1.598,00 / un  |  8 pc  ->  R$ 12.784,00` — partes opcionais."""
        if not config.show_prices or unit_price is None:
            return f"{total_pecas} pc"
        return (
            f"{format_currency(unit_price, config.currency_symbol)} / un  |  "
            f"{total_pecas} pc  ->  "
            f"{format_currency(subtotal, config.currency_symbol)}"
        )

    # ── Totalizador ───────────────────────────

    def _draw_totalizer(
        self,
        page: pymupdf.Page,
        totals: NormalizedTotals,
        y: float,
        config: RomaneioConfig,
    ) -> None:
        """Faixa brand final com totais consolidados."""
        y_top = y + 8
        strip_h = 56.0

        page.draw_rect(
            pymupdf.Rect(MARGIN_X, y_top, PAGE_W - MARGIN_X, y_top + strip_h),
            color=None,
            fill=C_BRAND,
            width=0,
        )

        page.insert_text(
            (MARGIN_X + 8, y_top + 20),
            f"{totals.n_skus} referencias  |  {totals.total_pecas} pecas",
            fontname=FONT_B,
            fontsize=11,
            color=C_BRAND_T,
        )

        if config.show_prices:
            label = "VALOR TOTAL DO PEDIDO"
            w_lbl = pymupdf.get_text_length(label, fontname=FONT, fontsize=8)
            page.insert_text(
                (PAGE_W - MARGIN_X - 8 - w_lbl, y_top + 18),
                label,
                fontname=FONT,
                fontsize=8,
                color=C_BRAND_ACCENT_T,
            )
            valor_str = format_currency(totals.valor_total, config.currency_symbol)
            w_val = pymupdf.get_text_length(valor_str, fontname=FONT_B, fontsize=16)
            page.insert_text(
                (PAGE_W - MARGIN_X - 8 - w_val, y_top + 44),
                valor_str,
                fontname=FONT_B,
                fontsize=16,
                color=C_ACENTO,
            )

    # ── Footer note (relatório de pendências) ──

    def _draw_footer_note(self, page: pymupdf.Page, note: str) -> None:
        """Frase informativa centralizada no rodapé da última página.

        Usado pelo relatório de pendências ("Itens acima não puderam ser
        atendidos integralmente."). Posicionada perto da margem inferior.

        `insert_textbox` precisa de altura suficiente para a linha; com
        14pt vs fontsize 9 o pymupdf às vezes recusa silenciosamente.
        Usamos `insert_text` com baseline calculado — mais previsível.
        """
        text_size = 9
        # Centro horizontal: calcula largura e posiciona.
        text_w = pymupdf.get_text_length(note, fontname=FONT, fontsize=text_size)
        x = (PAGE_W - text_w) / 2
        y = PAGE_H - MARGIN_Y / 2  # baseline a meio da margem inferior
        page.insert_text(
            (x, y),
            note,
            fontname=FONT,
            fontsize=text_size,
            color=C_MUTED,
        )
