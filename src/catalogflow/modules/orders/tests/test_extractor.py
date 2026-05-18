"""Testes do `OrderExtractor` contra as 6 fixtures geradas em Sprint 02 / Fase B.

Fixtures são geradas por `tests/fixtures/generate_order_fixtures.py` e
commitadas. Se a estratégia de geração mudar, regere com:
    python -m tests.fixtures.generate_order_fixtures
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from catalogflow.modules.orders.extractor import (
    OrderExtractor,
    RawOrderData,
    RawOrderItem,
    _consolidate_source_format,
    _parse_field_name,
    _parse_quantity,
)
from catalogflow.shared.errors import PDFCorruptError, PDFFlattenedError

FIXTURES_DIR = Path(__file__).resolve().parents[5] / "tests" / "fixtures"


@pytest.fixture(scope="module")
def extractor() -> OrderExtractor:
    return OrderExtractor()


def _load(name: str) -> bytes:
    path = FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(
            f"fixture {name} ausente — rode `python -m tests.fixtures.generate_order_fixtures`",
        )
    return path.read_bytes()


# ──────────────────────────────────────────────
#  Happy paths
# ──────────────────────────────────────────────


class TestV2Format:
    """`pedido_preenchido_v2.pdf` — 1 SKU x 2 cores x 4 tamanhos, todos > 0."""

    def test_returns_raw_order_data_with_all_items(
        self,
        extractor: OrderExtractor,
    ) -> None:
        result = extractor.extract(_load("pedido_preenchido_v2.pdf"))
        assert isinstance(result, RawOrderData)
        assert result.has_acroform is True
        assert result.source_format == "v2"
        assert result.n_fields_found == 8
        assert result.n_fields_filled == 8
        assert result.n_fields_discarded == 0
        assert len(result.items) == 8

    def test_items_carry_sku_color_size_and_quantity(
        self,
        extractor: OrderExtractor,
    ) -> None:
        result = extractor.extract(_load("pedido_preenchido_v2.pdf"))
        skus = {item.sku for item in result.items}
        assert skus == {"0442500912-0"}

        colors = {item.color_index for item in result.items}
        assert colors == {1, 2}

        sizes = {item.size for item in result.items}
        assert sizes == {"PP", "P", "M", "G"}

        # Todas as quantities devem ser > 0 (zeros são descartados pelo extractor).
        assert all(item.quantity > 0 for item in result.items)
        # Soma bate com QTY_POR_TAMANHO * 2 cores: (2+3+1+4) * 2 = 20
        assert sum(item.quantity for item in result.items) == 20

    def test_every_item_marked_as_v2_format(
        self,
        extractor: OrderExtractor,
    ) -> None:
        result = extractor.extract(_load("pedido_preenchido_v2.pdf"))
        assert {item.source_format for item in result.items} == {"v2"}


class TestV1LegacyFormat:
    """`pedido_preenchido_v1.pdf` — campos `qty__SKU__TAM`, color_index implícito = 1."""

    def test_color_index_defaults_to_one(self, extractor: OrderExtractor) -> None:
        result = extractor.extract(_load("pedido_preenchido_v1.pdf"))
        assert result.source_format == "v1"
        assert len(result.items) == 4
        assert all(item.color_index == 1 for item in result.items)
        assert {item.source_format for item in result.items} == {"v1"}

    def test_sku_and_sizes_parsed_correctly(
        self,
        extractor: OrderExtractor,
    ) -> None:
        result = extractor.extract(_load("pedido_preenchido_v1.pdf"))
        skus = {item.sku for item in result.items}
        assert skus == {"0442500941-0"}
        sizes = {item.size for item in result.items}
        assert sizes == {"PP", "P", "M", "G"}


# ──────────────────────────────────────────────
#  Cenários defensivos
# ──────────────────────────────────────────────


class TestEmptyFields:
    """`pedido_campos_vazios.pdf` — AcroForm presente, todos os campos em branco."""

    def test_returns_empty_items_list(self, extractor: OrderExtractor) -> None:
        result = extractor.extract(_load("pedido_campos_vazios.pdf"))
        assert result.has_acroform is True
        assert result.items == []
        assert result.n_fields_found == 4
        assert result.n_fields_filled == 0
        assert result.n_fields_discarded == 0


class TestInvalidValues:
    """`pedido_valores_invalidos.pdf` — `abc`, `3.5`, `-1`, `0` (todos descartados)."""

    def test_all_invalid_values_are_discarded(
        self,
        extractor: OrderExtractor,
    ) -> None:
        result = extractor.extract(_load("pedido_valores_invalidos.pdf"))
        assert result.items == []
        assert result.n_fields_found == 4
        # Todos os 4 widgets têm valor não-vazio (filled), mas todos os 4 são descartes.
        assert result.n_fields_filled == 4
        assert result.n_fields_discarded == 4


class TestFlattenedPDF:
    """`pedido_flattened.pdf` — sem `/AcroForm` → erro permanente, NÃO retryable."""

    def test_raises_pdf_flattened_error(self, extractor: OrderExtractor) -> None:
        with pytest.raises(PDFFlattenedError) as exc_info:
            extractor.extract(_load("pedido_flattened.pdf"))
        assert exc_info.value.code == "PDF_FLATTENED"


class TestMixedV1V2:
    """`pedido_mixed_v1_v2.pdf` — metade v1, metade v2."""

    def test_source_format_consolidates_to_mixed(
        self,
        extractor: OrderExtractor,
    ) -> None:
        result = extractor.extract(_load("pedido_mixed_v1_v2.pdf"))
        assert result.source_format == "mixed"
        formats_in_items = {item.source_format for item in result.items}
        assert formats_in_items == {"v1", "v2"}

    def test_distinct_skus_preserved(self, extractor: OrderExtractor) -> None:
        result = extractor.extract(_load("pedido_mixed_v1_v2.pdf"))
        skus = {item.sku for item in result.items}
        # Construção: SKU `0442500941-0` foi renomeado para v1, `0322500004-0` ficou v2.
        assert skus == {"0442500941-0", "0322500004-0"}


# ──────────────────────────────────────────────
#  Erros de entrada
# ──────────────────────────────────────────────


class TestInputErrors:
    def test_empty_bytes_raises_pdf_corrupt(
        self,
        extractor: OrderExtractor,
    ) -> None:
        with pytest.raises(PDFCorruptError) as exc_info:
            extractor.extract(b"")
        assert exc_info.value.code == "PDF_CORRUPT"

    def test_garbage_bytes_raises_pdf_corrupt(
        self,
        extractor: OrderExtractor,
    ) -> None:
        with pytest.raises(PDFCorruptError):
            extractor.extract(b"not a real pdf at all")


# ──────────────────────────────────────────────
#  Funções puras isoladas — boost de cobertura de edge cases
# ──────────────────────────────────────────────


class TestParseQuantity:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1", 1),
            ("42", 42),
            ("999", 999),
        ],
    )
    def test_valid_positive_integers(self, raw: str, expected: int) -> None:
        assert _parse_quantity(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["0", "-1", "-99", "abc", "3.5", "1.0", "", " ", "1e3"],
    )
    def test_returns_none_for_invalid(self, raw: str) -> None:
        assert _parse_quantity(raw) is None


class TestParseFieldName:
    def test_v2_canonical(self) -> None:
        out = _parse_field_name("qty__0442500941-0__cor2__PP")
        assert out == ("0442500941-0", 2, "PP", "v2")

    def test_v1_legacy(self) -> None:
        out = _parse_field_name("qty__0442500941-0__PP")
        assert out == ("0442500941-0", 1, "PP", "v1")

    def test_v2_takes_precedence_over_v1(self) -> None:
        # Confirma que `corN` é tentado antes do fallback v1.
        out = _parse_field_name("qty__SKU__cor5__GG")
        assert out is not None
        assert out[3] == "v2"
        assert out[1] == 5

    def test_returns_none_for_unrelated_widgets(self) -> None:
        # Widgets de metadados (ex: lojista_token) não devem casar.
        assert _parse_field_name("_meta_lojista_token") is None
        assert _parse_field_name("signature_field") is None


class TestConsolidateSourceFormat:
    def test_v1_only(self) -> None:
        assert _consolidate_source_format({"v1"}) == "v1"

    def test_v2_only(self) -> None:
        assert _consolidate_source_format({"v2"}) == "v2"

    def test_mixed(self) -> None:
        assert _consolidate_source_format({"v1", "v2"}) == "mixed"

    def test_empty_defaults_to_v2(self) -> None:
        assert _consolidate_source_format(set()) == "v2"


# ──────────────────────────────────────────────
#  Pureza — extractor não toca disco/banco/storage
# ──────────────────────────────────────────────


class TestPurity:
    def test_two_runs_yield_equivalent_results(
        self,
        extractor: OrderExtractor,
    ) -> None:
        """Idempotência simples — duas chamadas com os mesmos bytes batem."""
        pdf = _load("pedido_preenchido_v2.pdf")
        r1 = extractor.extract(pdf)
        r2 = extractor.extract(pdf)
        assert r1.items == r2.items
        assert r1.n_fields_filled == r2.n_fields_filled
        assert r1.source_format == r2.source_format

    def test_raw_order_item_is_frozen(self) -> None:
        item = RawOrderItem(
            field_name="qty__X__cor1__PP",
            sku="X",
            color_index=1,
            size="PP",
            quantity=1,
            source_format="v2",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            item.quantity = 99  # type: ignore[misc]
