"""Engine puro de análise de catálogos PDF.

Contrato (ADR-001, CLAUDE.md):
    bytes → CatalogMetadata
    Zero I/O. Zero acesso a disco, banco, storage ou rede.

Lógica migrada de `oasis_form_v2.py`:
    - `detectar_swatches`: drawings vetoriais na zona inferior (y0 ≥ 92% da altura),
       largura < 45pt e altura < 45pt, com fill != branco.
    - `extrair_blocos_legenda`: regex de SKU (`\\d{9,13}-\\d`) e grade (PP-M..PP-GG)
       sobre o texto da página; classificação `single` / `left` / `right` por
       posição relativa ao meio horizontal.
    - `swatches_para`: filtra swatches pertencentes a cada bloco via x_mid.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar, Final

import pdfplumber
import pymupdf

from catalogflow.shared.errors import (
    PDFCorruptError,
    PDFEncryptedError,
    PDFNoProductsError,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  Dataclasses de saída
# ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SwatchInfo:
    """Quadrado de cor detectado na zona inferior da página."""

    x0: float
    y0: float
    fill_rgb: tuple[float, float, float]
    fill_hex: str

    def to_dict(self) -> dict[str, object]:
        return {
            "x0": self.x0,
            "y0": self.y0,
            "fill_rgb": list(self.fill_rgb),
            "fill_hex": self.fill_hex,
        }


@dataclass(frozen=True, slots=True)
class ProductPageMeta:
    """Metadados de UM produto em UMA página.

    Quando a página tem 2 produtos, há 2 instâncias com o mesmo `page_index`
    e `side` em ("left", "right"). Em página de produto único, `side="single"`.
    """

    sku: str
    name: str | None
    price: Decimal | None
    grade: str
    sizes: list[str]
    n_colors: int
    swatches: list[SwatchInfo]
    page_index: int
    x_block_start: float
    x_block_end: float
    y_start: float
    y_end: float
    side: str  # "single" | "left" | "right"
    n_products_on_page: int


@dataclass(frozen=True, slots=True)
class CatalogMetadata:
    """Resultado completo da análise."""

    n_pages: int
    n_product_pages: int
    product_pages: list[ProductPageMeta] = field(default_factory=list)

    @property
    def n_skus(self) -> int:
        """Quantidade de produtos detectados (1 produto = 1 SKU)."""
        return len(self.product_pages)


# ──────────────────────────────────────────────
#  Analyzer
# ──────────────────────────────────────────────


class PDFAnalyzer:
    """Analisa o conteúdo bruto de um PDF de catálogo de moda."""

    # Regex idênticas ao POC oasis_form_v2.py
    SKU_RE: ClassVar[re.Pattern[str]] = re.compile(r"\b(\d{9,13}-\d)\b")
    GRADE_RE: ClassVar[re.Pattern[str]] = re.compile(r"\b(PP-GG|PP-G|PP-M|P-GG|P-G|P-M)\b")
    NAME_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"\b((?:JAQUETA|CAL[ÇC]A|VESTIDO|CONJUNTO|BLUSA|BODY|SHORT|BLAZER|SAIA|TOP)"
        r"(?:\s+[A-Za-zÀ-Ýà-ý]{2,})*)\b",
        re.IGNORECASE,
    )
    # Preço no padrão "R$ 3.488,00" — ponto = milhar, vírgula = decimal.
    PRICE_RE: ClassVar[re.Pattern[str]] = re.compile(r"R\$\s*([\d.]+,\d{2})")

    # Constantes do swatch (replicadas exatamente do POC)
    SWATCH_THRESHOLD_FRAC: Final[float] = 0.920
    SWATCH_MAX_SIZE: Final[float] = 45.0
    SWATCH_BG_RGB: Final[tuple[float, float, float]] = (1.0, 1.0, 1.0)

    SIZE_MAP: ClassVar[dict[str, list[str]]] = {
        "PP-M": ["PP", "P", "M"],
        "PP-G": ["PP", "P", "M", "G"],
        "PP-GG": ["PP", "P", "M", "G", "GG"],
        "P-M": ["P", "M"],
        "P-G": ["P", "M", "G"],
        "P-GG": ["P", "M", "G", "GG"],
    }
    DEFAULT_SIZES: ClassVar[list[str]] = ["PP", "P", "M"]
    DEFAULT_GRADE: ClassVar[str] = "PP-M"

    # ── API pública ───────────────────────────

    def analyze(self, pdf_bytes: bytes) -> CatalogMetadata:
        """Analisa o PDF e retorna o metadado completo do catálogo.

        Levanta:
            PDFCorruptError — bytes inválidos ou arquivo não-PDF.
            PDFEncryptedError — PDF protegido por senha.
            PDFNoProductsError — nenhuma página de produto detectada.
        """
        if not pdf_bytes:
            raise PDFCorruptError("pdf vazio", code="PDF_CORRUPT")

        doc = self._open_pymupdf(pdf_bytes)
        try:
            if doc.is_encrypted:
                raise PDFEncryptedError(
                    "pdf protegido por senha",
                    code="PDF_ENCRYPTED",
                )

            n_pages = doc.page_count
            product_pages: list[ProductPageMeta] = []

            with pdfplumber.open(io.BytesIO(pdf_bytes)) as plumb:
                for idx in range(n_pages):
                    page_m = doc[idx]
                    page_p = plumb.pages[idx]
                    page_w = float(page_m.rect.width)

                    blocks = self._extract_legend_blocks(page_p, page_w)
                    if not blocks:
                        continue

                    all_swatches = self._detect_swatches(page_m)

                    for block in blocks:
                        block_swatches = self._swatches_for(block, all_swatches, page_w)
                        n_colors = max(1, len(block_swatches))
                        product_pages.append(
                            ProductPageMeta(
                                sku=block["sku"],
                                name=block["name"],
                                price=block["price"],
                                grade=block["grade"],
                                sizes=block["sizes"],
                                n_colors=n_colors,
                                swatches=block_swatches,
                                page_index=idx,
                                x_block_start=block["x_ini"],
                                x_block_end=block["x_fim"],
                                y_start=block["y_ini"],
                                y_end=block["y_fim"],
                                side=block["side"],
                                n_products_on_page=block["n_prods"],
                            ),
                        )
        finally:
            doc.close()  # type: ignore[no-untyped-call]

        if not product_pages:
            raise PDFNoProductsError(
                "pdf não contém páginas de produto reconhecíveis",
                code="PDF_NO_PRODUCTS",
            )

        n_product_pages = len({p.page_index for p in product_pages})
        return CatalogMetadata(
            n_pages=n_pages,
            n_product_pages=n_product_pages,
            product_pages=product_pages,
        )

    # ── Helpers internos ──────────────────────

    def _open_pymupdf(self, pdf_bytes: bytes) -> pymupdf.Document:
        """Abre o PDF a partir de bytes. Levanta `PDFCorruptError` em falha."""
        try:
            return pymupdf.open(stream=pdf_bytes, filetype="pdf")  # type: ignore[no-untyped-call]
        except Exception as exc:
            raise PDFCorruptError(
                "pdf corrompido ou em formato inválido",
                code="PDF_CORRUPT",
                details={"reason": str(exc)},
            ) from exc

    def _detect_swatches(self, page_m: pymupdf.Page) -> list[SwatchInfo]:
        """Detecta quadrados coloridos (drawings) na zona inferior da página.

        Critérios — idênticos ao POC oasis_form_v2.py:
            - y0 ≥ altura_pagina * 0.920
            - largura < 45pt e altura < 45pt
            - fill presente e diferente de branco
        """
        page_h = page_m.rect.height
        threshold = page_h * self.SWATCH_THRESHOLD_FRAC
        out: list[SwatchInfo] = []
        for d in page_m.get_drawings():
            rect = d["rect"]
            fill = d.get("fill")
            if (
                rect.y0 >= threshold
                and rect.width < self.SWATCH_MAX_SIZE
                and rect.height < self.SWATCH_MAX_SIZE
                and fill is not None
                and tuple(fill) != self.SWATCH_BG_RGB
            ):
                rgb = (
                    round(float(fill[0]), 4),
                    round(float(fill[1]), 4),
                    round(float(fill[2]), 4),
                )
                out.append(
                    SwatchInfo(
                        x0=float(rect.x0),
                        y0=float(rect.y0),
                        fill_rgb=rgb,
                        fill_hex=self._rgb_to_hex(rgb),
                    ),
                )
        out.sort(key=lambda s: s.x0)
        return out

    @staticmethod
    def _parse_price(raw: str) -> Decimal | None:
        """Converte "3.488,00" → Decimal("3488.00"). Retorna None se inválido."""
        try:
            return Decimal(raw.replace(".", "").replace(",", "."))
        except (ArithmeticError, ValueError):
            return None

    @staticmethod
    def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
        return f"#{round(rgb[0] * 255):02x}{round(rgb[1] * 255):02x}{round(rgb[2] * 255):02x}"

    def _assign_name_zones(
        self,
        sku_rects: list[tuple[str, pymupdf.Rect]],
        page_width: float,
        page_height: float,
    ) -> dict[str, pymupdf.Rect]:
        """Calcula a zona de busca de texto para cada SKU.

        As fronteiras são os pontos médios entre as coordenadas X dos SKUs
        vizinhos detectados na página — nenhum valor de posição hardcoded.
        Funciona para 1, 2, 3 ou N produtos por página. Para 1 SKU, a zona
        é a página inteira (comportamento idêntico ao anterior). Para
        layouts assimétricos, a fronteira segue os dados, não o centro
        geométrico da página.

        Edge case: se dois SKUs compartilharem o mesmo `x0` (layout
        degenerado, improvável em catálogos reais), registra warning e
        atribui a página inteira como zona para todos. Não levanta exceção.
        """
        sorted_skus = sorted(sku_rects, key=lambda item: item[1].x0)

        x0_values = [rect.x0 for _, rect in sorted_skus]
        if len(set(x0_values)) < len(x0_values):
            logger.warning(
                "Dois ou mais SKUs compartilham o mesmo x0 na mesma página; "
                "usando página inteira como zona para todos. SKUs: %s",
                [sku for sku, _ in sorted_skus],
            )
            full_page = pymupdf.Rect(0.0, 0.0, page_width, page_height)  # type: ignore[no-untyped-call]
            return {sku: full_page for sku, _ in sorted_skus}

        zones: dict[str, pymupdf.Rect] = {}
        for i, (sku, rect) in enumerate(sorted_skus):
            x_left = 0.0 if i == 0 else (sorted_skus[i - 1][1].x0 + rect.x0) / 2.0
            x_right = (
                page_width
                if i == len(sorted_skus) - 1
                else (rect.x0 + sorted_skus[i + 1][1].x0) / 2.0
            )
            zones[sku] = pymupdf.Rect(x_left, 0.0, x_right, page_height)  # type: ignore[no-untyped-call]
        return zones

    def _extract_legend_blocks(
        self,
        page_p: pdfplumber.page.Page,
        page_w: float,
    ) -> list[dict[str, Any]]:
        """Extrai blocos de legenda (SKU + nome + preço + grade) da página.

        Refatorado em S05-02 (ADR-007): em vez do split `page_w / 2.0` para
        páginas com múltiplos produtos, calcula zonas dinâmicas via
        `_assign_name_zones()` a partir das posições reais dos SKUs. As
        buscas de nome, preço, grade, bounding box e swatches são todas
        restritas à zona do respectivo SKU — eliminando o vazamento de
        nome entre produtos vizinhos.
        """
        text = page_p.extract_text() or ""
        skus = self.SKU_RE.findall(text)
        if not skus:
            return []

        words = page_p.extract_words()
        h = float(page_p.height)
        threshold = h * self.SWATCH_THRESHOLD_FRAC
        bot_words = [w for w in words if float(w["top"]) > threshold]
        if not bot_words:
            return []

        # STEP A — localiza cada SKU detectado no fluxo de palavras para
        # obter seu rect (x0, top, x1, bottom). SKUs que não puderem ser
        # mapeados a uma palavra (caso raro) são descartados.
        sku_rects: list[tuple[str, pymupdf.Rect]] = []
        for sku in skus:
            sku_word = next((w for w in words if w["text"] == sku), None)
            if sku_word is None:
                sku_word = next((w for w in words if sku in w["text"]), None)
            if sku_word is None:
                continue
            sku_rects.append(
                (
                    sku,
                    pymupdf.Rect(  # type: ignore[no-untyped-call]
                        float(sku_word["x0"]),
                        float(sku_word["top"]),
                        float(sku_word["x1"]),
                        float(sku_word["bottom"]),
                    ),
                ),
            )
        if not sku_rects:
            return []

        # STEP B — zonas de busca via midpoints (Voronoi horizontal).
        zones = self._assign_name_zones(sku_rects, page_w, h)

        sorted_skus = sorted(sku_rects, key=lambda item: item[1].x0)
        n = len(sorted_skus)

        blocks: list[dict[str, Any]] = []
        for i, (sku, _sku_rect) in enumerate(sorted_skus):
            zone = zones[sku]

            # STEP C — filtra palavras para a zona deste SKU e roda regex
            # apenas no texto da zona (sem vazamento entre vizinhos).
            zone_words = [
                w for w in words if float(w["x0"]) >= zone.x0 and float(w["x1"]) <= zone.x1
            ]
            zone_text = " ".join(w["text"] for w in zone_words)

            zone_grades = self.GRADE_RE.findall(zone_text)
            zone_names = self.NAME_RE.findall(zone_text)
            zone_prices = self.PRICE_RE.findall(zone_text)

            grade = zone_grades[0] if zone_grades else self.DEFAULT_GRADE
            sizes = self.SIZE_MAP.get(grade, self.DEFAULT_SIZES)
            name = zone_names[0].upper() if zone_names else None
            price = self._parse_price(zone_prices[0]) if zone_prices else None

            # STEP D — bot_words restritos à zona (usados pelo bounding box
            # do painel de pedido em `field_injector`).
            subset = [
                w for w in bot_words if float(w["x0"]) >= zone.x0 and float(w["x1"]) <= zone.x1
            ]
            if not subset:
                continue

            xs = [float(w["x0"]) for w in subset]
            xe = [float(w["x1"]) for w in subset]
            ys = [float(w["top"]) for w in subset]
            ye = [float(w["bottom"]) for w in subset]

            if n == 1:
                side = "single"
            elif i == 0:
                side = "left"
            else:
                side = "right"

            blocks.append(
                {
                    "sku": sku,
                    "name": name,
                    "price": price,
                    "grade": grade,
                    "sizes": list(sizes),
                    "x_ini": min(xs),
                    "x_fim": max(xe),
                    "y_ini": min(ys),
                    "y_fim": max(ye),
                    "side": side,
                    "n_prods": n,
                    "zone": zone,
                },
            )
        return blocks

    def _swatches_for(
        self,
        block: dict[str, Any],
        all_swatches: list[SwatchInfo],
        page_w: float,
    ) -> list[SwatchInfo]:
        """Filtra os swatches dentro da zona horizontal do bloco.

        S05-02 (ADR-007): substitui o split `page_w / 2.0` por filtro
        baseado na zona dinâmica calculada em `_extract_legend_blocks`.
        """
        if int(block["n_prods"]) == 1 or str(block["side"]) == "single":
            return list(all_swatches)

        zone: pymupdf.Rect = block["zone"]
        return [s for s in all_swatches if zone.x0 <= s.x0 < zone.x1]
