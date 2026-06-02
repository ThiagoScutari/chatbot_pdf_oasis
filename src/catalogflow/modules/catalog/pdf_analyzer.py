"""Engine puro de análise de catálogos PDF.

Contrato (ADR-001, CLAUDE.md):
    bytes → CatalogMetadata
    Zero I/O. Zero acesso a disco, banco, storage ou rede.

A partir da Sprint 08 (ADR-010), o `PDFAnalyzer` é um **orquestrador**
que delega cada eixo de extração (SKU, grade, preço, swatches, nome)
para uma estratégia plugável selecionada por `BrandFormatProfile`.

Comportamento preservado bit-a-bit sobre o catálogo Oasis MOTION em
modo `profile_id="oasis_default"` — coberto pela suite de regressão
golden file `tests/test_pdf_analyzer_regression.py`.

Helpers ainda no analyzer:
    - `_open_pymupdf`: instancia `pymupdf.Document` a partir de bytes.
    - `_assign_name_zones`: zonas Voronoi horizontais (ADR-007).

Defaults `DEFAULT_GRADE` / `DEFAULT_SIZES` continuam aplicados de forma
silenciosa nesta fase. Eles viram warnings explícitos na Fase C
(ADR-011).
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar

import pdfplumber
import pymupdf

from catalogflow.modules.catalog.format_profiles import load_profile
from catalogflow.modules.catalog.strategies.base import (
    StrategyContext,
    ZoneContext,
)
from catalogflow.modules.catalog.strategies.grade import get_grade_strategy
from catalogflow.modules.catalog.strategies.name import get_name_strategy
from catalogflow.modules.catalog.strategies.price import get_price_strategy
from catalogflow.modules.catalog.strategies.sku import get_sku_strategy
from catalogflow.modules.catalog.strategies.swatches import (
    get_swatches_strategy,
)
from catalogflow.modules.catalog.strategies.swatches.geometric_bottom import (
    GeometricBottomSwatches,
)
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
    """Quadrado de cor detectado na zona inferior da página.

    Mantida nesta sprint como tipo legado consumido downstream
    (`field_injector`, `service`). A estratégia produz `SwatchMatch`
    novo e o orquestrador converte para `SwatchInfo` aqui. Em sprint
    futura, downstream migra para `SwatchMatch` e este tipo é
    removido.
    """

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

    Quando a página tem 2 produtos, há 2 instâncias com o mesmo
    `page_index` e `side` em ("left", "right"). Em página de produto
    único, `side="single"`.
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
#  Analyzer / Orquestrador
# ──────────────────────────────────────────────


class PDFAnalyzer:
    """Orquestra estratégias plugáveis (ADR-010) sobre o PDF de catálogo."""

    # Defaults silenciosos. Migrados para warnings explícitos na Fase C.
    DEFAULT_GRADE: ClassVar[str] = "PP-M"
    DEFAULT_SIZES: ClassVar[list[str]] = ["PP", "P", "M"]

    # ── API pública ───────────────────────────

    def analyze(
        self,
        pdf_bytes: bytes,
        profile_id: str = "oasis_default",
    ) -> CatalogMetadata:
        """Analisa o PDF e retorna o metadado completo do catálogo.

        Args:
            pdf_bytes: bytes do PDF a analisar.
            profile_id: identificador do `BrandFormatProfile` (default
                preserva comportamento Oasis legado).

        Levanta:
            PDFCorruptError — bytes inválidos ou arquivo não-PDF.
            PDFEncryptedError — PDF protegido por senha.
            PDFNoProductsError — nenhuma página de produto detectada.
            BrandFormatProfileNotFoundError — profile inexistente.
            BrandFormatProfileInvalidError — profile mal formado.
        """
        if not pdf_bytes:
            raise PDFCorruptError("pdf vazio", code="PDF_CORRUPT")

        profile = load_profile(profile_id)

        # Resolve cada eixo uma única vez (não por página).
        sku_strat = get_sku_strategy(profile.strategies["sku"]["id"])()
        grade_strat = get_grade_strategy(profile.strategies["grade"]["id"])()
        price_strat = get_price_strategy(profile.strategies["price"]["id"])()
        swatches_strat = get_swatches_strategy(profile.strategies["swatches"]["id"])()
        name_strat = get_name_strategy(profile.strategies["name"]["id"])()

        sku_params = profile.strategies["sku"].get("params", {})
        grade_params = profile.strategies["grade"].get("params", {})
        price_params = profile.strategies["price"].get("params", {})
        swatches_params = profile.strategies["swatches"].get("params", {})
        name_params = profile.strategies["name"].get("params", {})

        # `bot_words` (usados para bounding box do painel de pedido) ainda
        # dependem do mesmo threshold geométrico da estratégia de swatches.
        # Acoplamento explícito documentado: o threshold vive como ClassVar
        # do `GeometricBottomSwatches`. Em fases futuras o threshold pode
        # virar parâmetro do profile e ser propagado ao orquestrador.
        bot_threshold_frac = GeometricBottomSwatches.DEFAULT_THRESHOLD_FRAC

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
                    ctx = StrategyContext(
                        page_pymupdf=page_m,
                        page_plumb=page_p,
                        page_width=float(page_m.rect.width),
                        page_height=float(page_m.rect.height),
                        page_index=idx,
                    )

                    sku_matches = sku_strat.extract(ctx, sku_params)
                    if not sku_matches:
                        continue

                    # Voronoi zones (ADR-007).
                    sku_rects = [(m.sku, m.rect) for m in sku_matches]
                    zones = self._assign_name_zones(sku_rects, ctx.page_width, ctx.page_height)

                    words = page_p.extract_words()
                    threshold = ctx.page_height * bot_threshold_frac
                    bot_words = [w for w in words if float(w["top"]) > threshold]
                    if not bot_words:
                        continue

                    sorted_skus = sorted(sku_rects, key=lambda item: item[1].x0)
                    n = len(sorted_skus)

                    for i, (sku, _sku_rect) in enumerate(sorted_skus):
                        zone = zones[sku]
                        zone_words = [
                            w
                            for w in words
                            if float(w["x0"]) >= zone.x0 and float(w["x1"]) <= zone.x1
                        ]
                        zone_text = " ".join(w["text"] for w in zone_words)
                        zctx = ZoneContext(
                            sku=sku,
                            zone=zone,
                            zone_words=zone_words,
                            zone_text=zone_text,
                        )

                        grade_m = grade_strat.extract(zctx, grade_params)
                        price_m = price_strat.extract(zctx, price_params)
                        name_m = name_strat.extract(zctx, name_params)
                        block_swatches = swatches_strat.extract(ctx, sku, zone, swatches_params)

                        # Fallbacks silenciosos preservados (viram warnings na Fase C).
                        if grade_m is None:
                            grade = self.DEFAULT_GRADE
                            sizes = list(self.DEFAULT_SIZES)
                        else:
                            grade = grade_m.grade
                            sizes = list(grade_m.sizes)

                        subset = [
                            w
                            for w in bot_words
                            if float(w["x0"]) >= zone.x0 and float(w["x1"]) <= zone.x1
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

                        n_colors = max(1, len(block_swatches))

                        # Conversão SwatchMatch → SwatchInfo (tipo legado downstream).
                        swatches_legacy = [
                            SwatchInfo(
                                x0=s.x0,
                                y0=s.y0,
                                fill_rgb=s.fill_rgb,
                                fill_hex=s.fill_hex,
                            )
                            for s in block_swatches
                        ]

                        product_pages.append(
                            ProductPageMeta(
                                sku=sku,
                                name=name_m.name if name_m else None,
                                price=price_m.value if price_m else None,
                                grade=grade,
                                sizes=sizes,
                                n_colors=n_colors,
                                swatches=swatches_legacy,
                                page_index=idx,
                                x_block_start=min(xs),
                                x_block_end=max(xe),
                                y_start=min(ys),
                                y_end=max(ye),
                                side=side,
                                n_products_on_page=n,
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

    def _assign_name_zones(
        self,
        sku_rects: list[tuple[str, pymupdf.Rect]],
        page_width: float,
        page_height: float,
    ) -> dict[str, pymupdf.Rect]:
        """Calcula a zona de busca de texto para cada SKU.

        As fronteiras são os pontos médios entre as coordenadas X dos
        SKUs vizinhos detectados na página — nenhum valor de posição
        hardcoded. Funciona para 1, 2, 3 ou N produtos por página. Para
        1 SKU, a zona é a página inteira (comportamento idêntico ao
        anterior). Para layouts assimétricos, a fronteira segue os
        dados, não o centro geométrico da página.

        Edge case: se dois SKUs compartilharem o mesmo `x0` (layout
        degenerado, improvável em catálogos reais), registra warning e
        atribui a página inteira como zona para todos. Não levanta
        exceção.
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
