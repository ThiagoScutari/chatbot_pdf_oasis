"""ABCs e dataclasses base do Strategy Pattern (ADR-010, Sprint 08 Fase A).

Define:

- Dois contextos de execução compartilhados — `StrategyContext` (página
  inteira) e `ZoneContext` (zona Voronoi de um SKU já identificado).
- Cinco ABCs (uma por eixo de extração): `SkuStrategy`, `GradeStrategy`,
  `PriceStrategy`, `NameStrategy`, `SwatchesStrategy`.
- Cinco dataclasses de output (`SkuMatch`, `GradeMatch`, `PriceMatch`,
  `NameMatch`, `SwatchMatch`), todas frozen + slots.

As estratégias concretas vivem em `strategies/<eixo>/<id>.py` e são
registradas no registry do eixo (`strategies/<eixo>/__init__.py`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pdfplumber.page
    import pymupdf


# ──────────────────────────────────────────────
#  Contextos de execução
# ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Contexto que cobre a página inteira do PDF.

    Usado pelas estratégias de SKU (rodam antes da definição das zonas
    Voronoi) e Swatches (precisam de drawings de qualquer coordenada).
    """

    page_pymupdf: pymupdf.Page
    page_plumb: pdfplumber.page.Page
    page_width: float
    page_height: float
    page_index: int


@dataclass(frozen=True, slots=True)
class ZoneContext:
    """Contexto restrito à zona Voronoi de um SKU já identificado.

    Usado pelas estratégias de Grade, Preço e Nome — eixos que devem
    operar apenas sobre o texto da fatia horizontal pertencente ao
    produto, evitando vazamento entre produtos vizinhos na mesma página.
    """

    sku: str
    zone: pymupdf.Rect
    zone_words: list[dict[str, Any]]
    zone_text: str


# ──────────────────────────────────────────────
#  Dataclasses de output
# ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SkuMatch:
    """SKU identificado em uma página, com sua bounding box no documento."""

    sku: str
    rect: pymupdf.Rect


@dataclass(frozen=True, slots=True)
class GradeMatch:
    """Grade detectada e seus tamanhos expandidos.

    `sizes` é tupla (não lista) por imutabilidade — `GradeMatch` é
    frozen + slots e precisa ser hashable.
    """

    grade: str
    sizes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PriceMatch:
    """Preço detectado.

    `label` é `None` quando o catálogo expõe um único valor; é o rótulo
    bruto (ex.: "Atacado", "Varejo") quando há múltiplos preços por
    produto.
    """

    value: Decimal
    label: str | None


@dataclass(frozen=True, slots=True)
class NameMatch:
    """Nome do produto extraído da zona do SKU."""

    name: str


@dataclass(frozen=True, slots=True)
class SwatchMatch:
    """Quadrado de cor (swatch) detectado na zona inferior da página.

    Invariante de `fill_hex`: string lowercase com prefixo "#", 7
    caracteres no total — mesmo formato emitido pelo helper
    `_rgb_to_hex` no `pdf_analyzer.py` atual. A invariante é
    **responsabilidade do produtor** (estratégias de swatches); não há
    validação no `__init__` para manter a dataclass como POD enxuto.
    """

    x0: float
    y0: float
    fill_rgb: tuple[float, float, float]
    fill_hex: str


# ──────────────────────────────────────────────
#  ABCs por eixo de extração
# ──────────────────────────────────────────────


class SkuStrategy(ABC):
    """Identifica todos os SKUs presentes em uma página de catálogo."""

    @abstractmethod
    def extract(
        self,
        ctx: StrategyContext,
        params: dict[str, Any],
    ) -> list[SkuMatch]:
        """Retorna a lista de SKUs encontrados na página.

        Lista vazia significa "página sem produtos" (ex.: capa, contra-capa,
        índice). Nunca levanta exceção por ausência de SKU.
        """


class GradeStrategy(ABC):
    """Detecta a grade de tamanhos do produto na zona Voronoi do SKU."""

    @abstractmethod
    def extract(
        self,
        zctx: ZoneContext,
        params: dict[str, Any],
    ) -> GradeMatch | None:
        """Retorna a grade detectada ou `None` se não houver match.

        `None` sinaliza ausência — a decisão de aplicar default ou emitir
        warning fica com o orquestrador no `PDFAnalyzer`.
        """


class PriceStrategy(ABC):
    """Detecta o(s) preço(s) do produto na zona Voronoi do SKU."""

    @abstractmethod
    def extract(
        self,
        zctx: ZoneContext,
        params: dict[str, Any],
    ) -> PriceMatch | None:
        """Retorna o preço detectado ou `None` se não houver match."""


class NameStrategy(ABC):
    """Detecta o nome do produto na zona Voronoi do SKU."""

    @abstractmethod
    def extract(
        self,
        zctx: ZoneContext,
        params: dict[str, Any],
    ) -> NameMatch | None:
        """Retorna o nome detectado ou `None` se não houver match."""


class SwatchesStrategy(ABC):
    """Detecta os swatches de cor associados ao produto."""

    @abstractmethod
    def extract(
        self,
        ctx: StrategyContext,
        sku: str,
        zone: pymupdf.Rect,
        params: dict[str, Any],
    ) -> list[SwatchMatch]:
        """Retorna a lista de swatches que pertencem ao produto `sku`.

        Lista vazia é um resultado válido (produto sem swatch detectado).
        """
