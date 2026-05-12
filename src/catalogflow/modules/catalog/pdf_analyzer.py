"""Engine puro de análise de catálogos PDF.

Contrato (ADR-001, CLAUDE.md):
    bytes → CatalogMetadata
    Zero I/O. Zero acesso a disco, banco, storage ou rede.

Lógica migrada de `oasis_form_v2.py`:
    - `detectar_swatches`: drawings vetoriais na zona inferior (y0 ≥ 92% da altura),
       largura < 45pt e altura < 45pt, com fill != branco.
    - `extrair_blocos_legenda`: regex de SKU (`\\d{10,13}-\\d`) e grade (PP-M..PP-GG)
       sobre o texto da página; classificação `single` / `left` / `right` por
       posição relativa ao meio horizontal.
    - `swatches_para`: filtra swatches pertencentes a cada bloco via x_mid.
"""

from __future__ import annotations

import io
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
    SKU_RE: ClassVar[re.Pattern[str]] = re.compile(r"\b(\d{10,13}-\d)\b")
    GRADE_RE: ClassVar[re.Pattern[str]] = re.compile(r"\b(PP-GG|PP-G|PP-M|P-GG|P-G|P-M)\b")
    NAME_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(JAQUETA|CAL[ÇC]A|VESTIDO|CONJUNTO|BLUSA|BODY|SHORT|BLAZER|SAIA|TOP)\b",
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
            doc.close()

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
            return pymupdf.open(stream=pdf_bytes, filetype="pdf")
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
        return "#{:02x}{:02x}{:02x}".format(
            int(round(rgb[0] * 255)),
            int(round(rgb[1] * 255)),
            int(round(rgb[2] * 255)),
        )

    def _extract_legend_blocks(
        self,
        page_p: pdfplumber.page.Page,
        page_w: float,
    ) -> list[dict[str, Any]]:
        """Extrai blocos de legenda (SKU + grade + bounding box) da página.

        Replica `extrair_blocos_legenda` do POC.
        """
        text = page_p.extract_text() or ""
        skus = self.SKU_RE.findall(text)
        if not skus:
            return []

        grades = self.GRADE_RE.findall(text)
        names = self.NAME_RE.findall(text)
        prices = self.PRICE_RE.findall(text)

        words = page_p.extract_words()
        h = float(page_p.height)
        threshold = h * self.SWATCH_THRESHOLD_FRAC
        bot_words = [w for w in words if float(w["top"]) > threshold]
        if not bot_words:
            return []

        blocks: list[dict[str, Any]] = []
        n = len(skus)
        x_mid = page_w / 2.0

        for i, sku in enumerate(skus):
            if grades:
                grade = grades[i] if i < len(grades) else grades[0]
            else:
                grade = self.DEFAULT_GRADE
            sizes = self.SIZE_MAP.get(grade, self.DEFAULT_SIZES)
            name = names[0].upper() if names else None
            price = self._parse_price(prices[i]) if i < len(prices) else None

            if n == 1:
                side = "single"
                subset = bot_words
            elif i == 0:
                side = "left"
                subset = [w for w in bot_words if float(w["x0"]) < x_mid]
            else:
                side = "right"
                subset = [w for w in bot_words if float(w["x0"]) >= x_mid]

            if not subset:
                continue

            xs = [float(w["x0"]) for w in subset]
            xe = [float(w["x1"]) for w in subset]
            ys = [float(w["top"]) for w in subset]
            ye = [float(w["bottom"]) for w in subset]

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
                },
            )
        return blocks

    def _swatches_for(
        self,
        block: dict[str, Any],
        all_swatches: list[SwatchInfo],
        page_w: float,
    ) -> list[SwatchInfo]:
        """Filtra os swatches que pertencem ao bloco corrente.

        Replica `swatches_para` do POC.
        """
        n = int(block["n_prods"])
        side = str(block["side"])
        if n == 1 or side == "single":
            return list(all_swatches)

        x_mid = page_w / 2.0
        if side == "left":
            return [s for s in all_swatches if s.x0 < x_mid]
        return [s for s in all_swatches if s.x0 >= x_mid]
