"""Testes do `MockStockAdapter`.

Cenários cobertos:
- 10 items → distribuição com mix de available/partial/out_of_stock
- Determinismo: mesmo input ⇒ mesmo resultado (testes não-flaky)
- `submit_order` aceita sempre e retorna `erp_reference` prefixado `MOCK-`
- A ordem do retorno espelha a ordem do input
"""

from __future__ import annotations

import re

import pytest

from catalogflow.modules.stock.adapter import StockQuery
from catalogflow.modules.stock.mock_adapter import MockStockAdapter

VALID_STATUSES = {"available", "partial", "out_of_stock"}


def _queries(n: int) -> list[StockQuery]:
    """Gera N queries com SKUs distintos (varia bucket de hash)."""
    return [
        StockQuery(
            sku=f"SKU-{i:04d}",
            size="M",
            color_index=(i % 3) + 1,
            requested_qty=4,
        )
        for i in range(n)
    ]


class TestCheckAvailability:
    async def test_returns_one_result_per_query_in_order(self) -> None:
        adapter = MockStockAdapter()
        queries = _queries(10)

        results = await adapter.check_availability(queries)

        assert len(results) == len(queries)
        for query, result in zip(queries, results, strict=True):
            assert result.sku == query.sku
            assert result.size == query.size
            assert result.color_index == query.color_index
            assert result.requested_qty == query.requested_qty
            assert result.status in VALID_STATUSES

    async def test_distribution_includes_mix_of_statuses(self) -> None:
        """Com amostra grande, todos os 3 status devem aparecer."""
        adapter = MockStockAdapter()
        # 200 queries dão margem para que mesmo o bucket out_of_stock (10%)
        # apareça pelo menos uma vez — Pr(zero out_of_stock em 200) ≈ 0.
        queries = _queries(200)

        results = await adapter.check_availability(queries)

        statuses = {r.status for r in results}
        assert statuses == VALID_STATUSES

    async def test_deterministic_same_input_same_output(self) -> None:
        """Mesma StockQuery executada 2x retorna idêntico StockResult."""
        adapter = MockStockAdapter()
        queries = _queries(20)

        first = await adapter.check_availability(queries)
        second = await adapter.check_availability(queries)

        assert first == second, "MockStockAdapter precisa ser determinístico"

    async def test_status_consistent_with_available_qty(self) -> None:
        """`available` ≥ requested, `partial` em (0, requested), `out` == 0."""
        adapter = MockStockAdapter()
        queries = _queries(50)

        results = await adapter.check_availability(queries)

        for r in results:
            assert r.available_qty is not None  # mock nunca devolve unknown
            if r.status == "available":
                assert r.available_qty >= r.requested_qty
            elif r.status == "partial":
                assert 0 < r.available_qty < r.requested_qty
            else:  # out_of_stock
                assert r.available_qty == 0

    async def test_empty_input_returns_empty_list(self) -> None:
        adapter = MockStockAdapter()
        results = await adapter.check_availability([])
        assert results == []


class TestSubmitOrder:
    async def test_returns_accepted_with_mock_reference(self) -> None:
        adapter = MockStockAdapter()
        queries = _queries(3)

        result = await adapter.submit_order(
            order_reference="order-123",
            customer_code="LOJA-42",
            items=queries,
        )

        assert result["accepted"] is True
        assert isinstance(result["erp_reference"], str)
        assert re.fullmatch(r"MOCK-[0-9a-f]{8}", result["erp_reference"])
        assert result["rejected_items"] == []
        assert isinstance(result["message"], str)

    async def test_each_call_produces_unique_reference(self) -> None:
        adapter = MockStockAdapter()
        queries = _queries(2)

        first = await adapter.submit_order("o1", "C1", queries)
        second = await adapter.submit_order("o2", "C2", queries)

        # uuid4().hex[:8] colide a cada ~16M envios — em uma sessão de
        # teste isolada a chance é zero.
        assert first["erp_reference"] != second["erp_reference"]


@pytest.mark.parametrize(
    ("sku", "size", "color_index", "requested_qty"),
    [
        ("0442500941-0", "PP", 1, 2),
        ("0442500941-0", "G", 1, 5),
        ("0322500004-0", "M", 2, 1),
    ],
)
async def test_single_query_is_idempotent(
    sku: str,
    size: str,
    color_index: int,
    requested_qty: int,
) -> None:
    """Casos pontuais — protege contra regressão de determinismo por item."""
    adapter = MockStockAdapter()
    query = StockQuery(
        sku=sku,
        size=size,
        color_index=color_index,
        requested_qty=requested_qty,
    )

    [first] = await adapter.check_availability([query])
    [second] = await adapter.check_availability([query])

    assert first == second
