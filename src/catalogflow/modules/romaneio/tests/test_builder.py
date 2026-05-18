"""Testes do `RomaneioBuilder` — função pura NormalizedOrderData → bytes PDF.

Cenários cobertos:
    - PDF gerado é abrível pelo PyMuPDF e tem tamanho > 0
    - Texto do SKU e lojista presente
    - Muitos SKUs → múltiplas páginas, cabeçalho repetido
    - Logo presente quando `logo_bytes` fornecido
    - Sem logo → cabeçalho apenas textual (sem erro)
    - Produto sem preço → não quebra, omite resumo
    - Formato monetário pt_BR
    - Pedido vazio gera apenas cabeçalho
"""

from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal

import pymupdf
import pytest

from catalogflow.modules.orders.normalizer import (
    NormalizedOrderData,
    NormalizedOrderItem,
    NormalizedTotals,
)
from catalogflow.modules.romaneio.builder import (
    RomaneioBuilder,
    RomaneioConfig,
    format_currency,
    format_date_pt_br,
)

# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────


def make_item(
    sku: str,
    color_index: int,
    size: str,
    quantity: int,
    unit_price: Decimal | None = Decimal("100.00"),
    product_name: str | None = "Produto Teste",
    color_hex: str | None = None,
) -> NormalizedOrderItem:
    return NormalizedOrderItem(
        sku=sku,
        product_name=product_name,
        color_index=color_index,
        color_hex=color_hex,
        size=size,
        quantity=quantity,
        unit_price=unit_price,
    )


def make_order_data(
    items: list[NormalizedOrderItem],
    source_format: str = "v2",
) -> NormalizedOrderData:
    total_pecas = sum(i.quantity for i in items)
    valor_total = sum(
        (i.subtotal for i in items if i.subtotal is not None),
        start=Decimal("0"),
    )
    n_skus = len({i.sku for i in items})
    totals = NormalizedTotals(
        total_items=len(items),
        total_pecas=total_pecas,
        valor_total=valor_total,
        n_skus=n_skus,
    )
    return NormalizedOrderData(
        items=items,
        totals=totals,
        source_format=source_format,  # type: ignore[arg-type]
        warnings=[],
    )


def make_logo_png() -> bytes:
    """Gera um PNG mínimo válido (10x10 vermelho) via PyMuPDF Pixmap."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10))
    pix.clear_with(200)  # cinza claro — qualquer valor não-branco serve
    buf = io.BytesIO(pix.tobytes("png"))
    return buf.getvalue()


@pytest.fixture
def builder() -> RomaneioBuilder:
    return RomaneioBuilder()


@pytest.fixture
def basic_config() -> RomaneioConfig:
    return RomaneioConfig(
        brand_name="Oasis Resortwear",
        lojista_name="Loja Moda e Arte",
        emitted_at=datetime(2026, 5, 11, 14, 22),
        collection="Winter 26 / MOTION",
    )


# ──────────────────────────────────────────────
#  PDF estruturalmente válido
# ──────────────────────────────────────────────


class TestPDFOutput:
    def test_returns_non_empty_bytes(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        data = make_order_data([make_item("X", 1, "PP", 2)])
        pdf = builder.build(data, basic_config)
        assert isinstance(pdf, bytes)
        assert len(pdf) > 0

    def test_pdf_is_openable_by_pymupdf(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        data = make_order_data([make_item("X", 1, "PP", 2)])
        pdf = builder.build(data, basic_config)
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        try:
            assert doc.page_count >= 1
        finally:
            doc.close()

    def test_single_sku_fits_in_one_page(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        data = make_order_data([make_item("X", 1, "PP", 2)])
        pdf = builder.build(data, basic_config)
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        try:
            assert doc.page_count == 1
        finally:
            doc.close()


# ──────────────────────────────────────────────
#  Conteúdo textual
# ──────────────────────────────────────────────


class TestTextContent:
    def test_contains_sku_and_lojista_text(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        data = make_order_data(
            [
                make_item(
                    "0442500941-0",
                    1,
                    "PP",
                    2,
                    product_name="Vestido Joana",
                ),
            ]
        )
        pdf = builder.build(data, basic_config)
        text = _extract_all_text(pdf)
        assert "0442500941-0" in text
        assert "Loja Moda e Arte" in text
        assert "VESTIDO JOANA" in text  # product_name upper

    def test_brand_name_appears_in_header(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        data = make_order_data([make_item("X", 1, "PP", 1)])
        pdf = builder.build(data, basic_config)
        text = _extract_all_text(pdf)
        assert "OASIS RESORTWEAR" in text

    def test_emitted_date_in_pt_br_format(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        data = make_order_data([make_item("X", 1, "PP", 1)])
        pdf = builder.build(data, basic_config)
        text = _extract_all_text(pdf)
        assert "11/05/2026" in text

    def test_currency_format_pt_br(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        data = make_order_data(
            [
                make_item(
                    "X",
                    1,
                    "PP",
                    1,
                    unit_price=Decimal("1598.00"),
                ),
            ]
        )
        pdf = builder.build(data, basic_config)
        text = _extract_all_text(pdf)
        assert "R$ 1.598,00" in text


# ──────────────────────────────────────────────
#  Logo
# ──────────────────────────────────────────────


class TestLogo:
    def test_logo_present_when_bytes_provided(
        self,
        builder: RomaneioBuilder,
    ) -> None:
        config = RomaneioConfig(
            brand_name="Brand X",
            lojista_name="L",
            emitted_at=datetime(2026, 5, 11),
            logo_bytes=make_logo_png(),
        )
        data = make_order_data([make_item("X", 1, "PP", 1)])
        pdf = builder.build(data, config)
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        try:
            first_page = doc[0]
            images = first_page.get_images()
            assert len(images) >= 1
        finally:
            doc.close()

    def test_no_logo_no_error(self, builder: RomaneioBuilder) -> None:
        config = RomaneioConfig(
            brand_name="Brand X",
            lojista_name="L",
            emitted_at=datetime(2026, 5, 11),
            logo_bytes=None,
        )
        data = make_order_data([make_item("X", 1, "PP", 1)])
        pdf = builder.build(data, config)
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        try:
            assert doc[0].get_images() == []
            # Cabeçalho textual da brand segue presente
            assert "BRAND X" in _extract_all_text(pdf)
        finally:
            doc.close()

    def test_corrupted_logo_does_not_crash(self, builder: RomaneioBuilder) -> None:
        """Logo inválida → builder segue sem imagem (fallback textual)."""
        config = RomaneioConfig(
            brand_name="Brand X",
            lojista_name="L",
            emitted_at=datetime(2026, 5, 11),
            logo_bytes=b"not a valid image",
        )
        data = make_order_data([make_item("X", 1, "PP", 1)])
        pdf = builder.build(data, config)
        assert len(pdf) > 0


# ──────────────────────────────────────────────
#  Paginação
# ──────────────────────────────────────────────


class TestPagination:
    def test_many_skus_create_multiple_pages(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        # Cada bloco ~60pt; ~10 SKUs por página → 30 SKUs cria 3+ páginas.
        items = [
            make_item(f"SKU{i:03d}", 1, "PP", 2, product_name=f"Produto {i}") for i in range(30)
        ]
        data = make_order_data(items)
        pdf = builder.build(data, basic_config)
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        try:
            assert doc.page_count > 1
        finally:
            doc.close()

    def test_header_repeats_on_each_page(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        items = [
            make_item(f"SKU{i:03d}", 1, "PP", 1, product_name=f"Produto {i}") for i in range(30)
        ]
        data = make_order_data(items)
        pdf = builder.build(data, basic_config)
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        try:
            assert doc.page_count > 1
            for page in doc:
                page_text = page.get_text()
                assert "OASIS RESORTWEAR" in page_text
                assert "Loja Moda e Arte" in page_text
        finally:
            doc.close()


# ──────────────────────────────────────────────
#  Cenários defensivos
# ──────────────────────────────────────────────


class TestEdgeCases:
    def test_item_without_price_renders_gracefully(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        data = make_order_data(
            [
                make_item(
                    "X",
                    1,
                    "PP",
                    3,
                    unit_price=None,  # produto sem preço (catálogo ausente)
                    product_name=None,
                ),
            ]
        )
        pdf = builder.build(data, basic_config)
        text = _extract_all_text(pdf)
        # SKU vira fallback do product_name quando este é None.
        assert "X" in text
        # Total de peças (apenas) deve aparecer no resumo, sem `R$` por unidade.
        assert "3 pc" in text

    def test_show_prices_false_omits_currency_strings(
        self,
        builder: RomaneioBuilder,
    ) -> None:
        config = RomaneioConfig(
            brand_name="Brand X",
            lojista_name="L",
            emitted_at=datetime(2026, 5, 11),
            show_prices=False,
        )
        data = make_order_data(
            [
                make_item(
                    "X",
                    1,
                    "PP",
                    2,
                    unit_price=Decimal("100.00"),
                ),
            ]
        )
        pdf = builder.build(data, config)
        text = _extract_all_text(pdf)
        assert "R$" not in text
        # Mas o resumo de peças do bloco continua presente.
        assert "2 pc" in text

    def test_empty_order_produces_header_only_pdf(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        data = make_order_data([])
        pdf = builder.build(data, basic_config)
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        try:
            assert doc.page_count == 1
            text = doc[0].get_text()
            assert "OASIS RESORTWEAR" in text
            assert "0 referencias" in text
            assert "0 pecas" in text
        finally:
            doc.close()

    def test_collection_appears_in_subtitle_when_provided(
        self,
        builder: RomaneioBuilder,
        basic_config: RomaneioConfig,
    ) -> None:
        data = make_order_data([make_item("X", 1, "PP", 1)])
        pdf = builder.build(data, basic_config)
        text = _extract_all_text(pdf)
        assert "Winter 26 / MOTION" in text


# ──────────────────────────────────────────────
#  Helpers de formatação
# ──────────────────────────────────────────────


class TestFormatCurrency:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Decimal("0"), "R$ 0,00"),
            (Decimal("1.5"), "R$ 1,50"),
            (Decimal("100"), "R$ 100,00"),
            (Decimal("1598.00"), "R$ 1.598,00"),
            (Decimal("12345.67"), "R$ 12.345,67"),
            (Decimal("1000000"), "R$ 1.000.000,00"),
        ],
    )
    def test_formats_pt_br_style(self, value: Decimal, expected: str) -> None:
        assert format_currency(value) == expected

    def test_custom_symbol(self) -> None:
        assert format_currency(Decimal("10"), symbol="USD") == "USD 10,00"


class TestFormatDate:
    def test_pt_br_with_hour(self) -> None:
        dt = datetime(2026, 5, 11, 14, 22)
        assert format_date_pt_br(dt) == "11/05/2026  14:22"


# ──────────────────────────────────────────────
#  Helpers internos do teste
# ──────────────────────────────────────────────


def _extract_all_text(pdf_bytes: bytes) -> str:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
