"""Engine puro de extração de pedidos a partir de PDFs preenchidos.

Contrato (CLAUDE.md):
    bytes → RawOrderData
    Zero I/O. Zero acesso a disco, banco, storage ou rede.

Formatos suportados:
    v2 (canônico): `qty__<SKU>__cor<N>__<TAM>` — produzido pelo `FieldInjector`
    v1 (legado):   `qty__<SKU>__<TAM>` — single-color, color_index implícito = 1

Regras de filtragem:
    - Campo fora dos padrões acima é ignorado silenciosamente (pode haver
      widgets de metadados como `_meta_lojista_token`).
    - Valor não-numérico, float, negativo ou zero é descartado (contado em
      `n_fields_discarded` para observabilidade).
    - PDF sem `/AcroForm` → `PDFFlattenedError` (erro permanente, não-retryable).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Literal

import pymupdf

from catalogflow.shared.errors import PDFCorruptError, PDFFlattenedError

# ──────────────────────────────────────────────
#  Regex de parsing
# ──────────────────────────────────────────────

# SKU tolera underscores internos (ex: "0442_500941-0") — o grupo é guloso
# até encontrar o separador `__` correto. As variantes v1/v2 são distinguidas
# pela presença do segmento `cor<N>`.
RE_V2: Final = re.compile(r"^qty__(?P<sku>.+?)__cor(?P<color>\d+)__(?P<size>[^_]+)$")
RE_V1: Final = re.compile(r"^qty__(?P<sku>.+?)__(?P<size>[^_]+)$")


# ──────────────────────────────────────────────
#  Dataclasses de saída
# ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RawOrderItem:
    """Item bruto extraído de um widget — sem enriquecimento de catálogo."""

    field_name: str
    sku: str
    color_index: int
    size: str
    quantity: int
    source_format: Literal["v1", "v2"]


@dataclass(frozen=True, slots=True)
class RawOrderData:
    """Resultado da extração — input do `OrderNormalizer`."""

    items: list[RawOrderItem] = field(default_factory=list)
    n_pages_scanned: int = 0
    n_fields_found: int = 0
    n_fields_filled: int = 0
    n_fields_discarded: int = 0
    has_acroform: bool = True
    source_format: Literal["v1", "v2", "mixed"] = "v2"


# ──────────────────────────────────────────────
#  Extractor
# ──────────────────────────────────────────────


class OrderExtractor:
    """Lê widgets AcroForm de um PDF preenchido e devolve `RawOrderData`."""

    def extract(self, pdf_bytes: bytes) -> RawOrderData:
        """Extrai itens do PDF preenchido.

        Levanta:
            PDFCorruptError: bytes vazios ou formato inválido.
            PDFFlattenedError: PDF sem `/AcroForm` (impresso-para-PDF).
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
            has_acroform = bool(doc.is_form_pdf)
            if not has_acroform:
                raise PDFFlattenedError(
                    "pdf sem campos AcroForm — provavelmente achatado",
                    code="PDF_FLATTENED",
                )

            items: list[RawOrderItem] = []
            n_fields_found = 0
            n_fields_filled = 0
            n_fields_discarded = 0
            formats_seen: set[str] = set()
            n_pages = doc.page_count

            for page_idx in range(n_pages):
                page = doc[page_idx]
                for widget in page.widgets() or []:
                    if widget.field_type != pymupdf.PDF_WIDGET_TYPE_TEXT:
                        continue

                    n_fields_found += 1
                    name = widget.field_name or ""
                    raw_value = (widget.field_value or "").strip()
                    if not raw_value:
                        # Widget existe mas está vazio: não conta como filled,
                        # não é descarte (é o estado natural de um campo não
                        # preenchido).
                        continue
                    n_fields_filled += 1

                    quantity = _parse_quantity(raw_value)
                    if quantity is None:
                        n_fields_discarded += 1
                        continue

                    parsed = _parse_field_name(name)
                    if parsed is None:
                        n_fields_discarded += 1
                        continue

                    sku, color_index, size, item_format = parsed
                    formats_seen.add(item_format)

                    items.append(
                        RawOrderItem(
                            field_name=name,
                            sku=sku,
                            color_index=color_index,
                            size=size,
                            quantity=quantity,
                            source_format=item_format,
                        )
                    )

            return RawOrderData(
                items=items,
                n_pages_scanned=n_pages,
                n_fields_found=n_fields_found,
                n_fields_filled=n_fields_filled,
                n_fields_discarded=n_fields_discarded,
                has_acroform=True,
                source_format=_consolidate_source_format(formats_seen),
            )
        finally:
            doc.close()


# ──────────────────────────────────────────────
#  Helpers puros (testáveis isoladamente)
# ──────────────────────────────────────────────


def _parse_quantity(raw: str) -> int | None:
    """Aceita apenas inteiros positivos.

    Floats (`"3.5"`), texto (`"abc"`), negativos (`"-1"`) e zero são None.
    """
    try:
        value = int(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _parse_field_name(
    name: str,
) -> tuple[str, int, str, Literal["v1", "v2"]] | None:
    """Tenta v2 primeiro (com `cor<N>`), depois fallback para v1.

    Retorna `(sku, color_index, size, source_format)` ou `None` se nenhum
    padrão casa.
    """
    m2 = RE_V2.match(name)
    if m2:
        return (m2.group("sku"), int(m2.group("color")), m2.group("size"), "v2")
    m1 = RE_V1.match(name)
    if m1:
        return (m1.group("sku"), 1, m1.group("size"), "v1")
    return None


def _consolidate_source_format(seen: set[str]) -> Literal["v1", "v2", "mixed"]:
    """`v2`, `v1`, `mixed` — ou `v2` como default em pedidos vazios."""
    if seen == {"v1"}:
        return "v1"
    if seen == {"v2"}:
        return "v2"
    if "v1" in seen and "v2" in seen:
        return "mixed"
    return "v2"
