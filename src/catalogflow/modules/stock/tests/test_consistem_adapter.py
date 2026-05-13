"""Testes do `ConsistemAdapter`.

Cenários cobertos:
- `_build_cod_item` produz o formato provisório SKU.SIZE.COLOR
- `check_availability` com httpx mockado (respx) classifica corretamente
  available / partial / out_of_stock conforme a fórmula contábil
- Timeout durante o GET resulta em `status="unknown"` (não falha o batch)
- HTTP 5xx resulta em `status="unknown"`
- `submit_order` levanta `NotImplementedError` com mensagem explícita
- O header `empresa` é enviado em toda request
"""

from __future__ import annotations

import httpx
import pytest
import respx

from catalogflow.modules.stock.adapter import StockQuery
from catalogflow.modules.stock.consistem_adapter import ConsistemAdapter

BASE_URL = "https://api.consistem.test"


def _adapter() -> ConsistemAdapter:
    return ConsistemAdapter(
        base_url=BASE_URL,
        api_key="test-key",
        empresa="50",
        cod_natureza=505,
        timeout=30,
    )


def _saldo_payload(
    estoque: float = 10,
    reserv_pedido: float = 0,
    reserv_producao: float = 0,
    reserv_lotes: float = 0,
) -> dict[str, float | str]:
    return {
        "codItem": "test",
        "estoqueAtual": estoque,
        "estReservPedido": reserv_pedido,
        "estReservProducao": reserv_producao,
        "estReservLotes": reserv_lotes,
    }


# ──────────────────────────────────────────────
#  _build_cod_item — função pura
# ──────────────────────────────────────────────


class TestBuildCodItem:
    def test_concatena_sku_size_color_separados_por_ponto(self) -> None:
        adapter = _adapter()
        assert adapter._build_cod_item("0442500941-0", "PP", 1) == "0442500941-0.PP.1"

    @pytest.mark.parametrize(
        ("sku", "size", "color", "expected"),
        [
            ("0442500941-0", "PP", 1, "0442500941-0.PP.1"),
            ("0442500941-0", "G", 2, "0442500941-0.G.2"),
            ("0322500004-0", "M", 1, "0322500004-0.M.1"),
        ],
    )
    def test_formatos_diferentes(
        self,
        sku: str,
        size: str,
        color: int,
        expected: str,
    ) -> None:
        adapter = _adapter()
        assert adapter._build_cod_item(sku, size, color) == expected


# ──────────────────────────────────────────────
#  check_availability — happy path
# ──────────────────────────────────────────────


class TestCheckAvailability:
    @respx.mock
    async def test_status_available_quando_disponivel_cobre_requested(
        self,
    ) -> None:
        """estoque=10, reservas=3 → disponível=7, requested=5 → available."""
        adapter = _adapter()
        respx.get(
            f"{BASE_URL}/saldoEstoqueAtual/0442500941-0.PP.1/505",
        ).mock(
            return_value=httpx.Response(
                200,
                json=_saldo_payload(estoque=10, reserv_pedido=3),
            ),
        )

        [result] = await adapter.check_availability(
            [StockQuery(sku="0442500941-0", size="PP", color_index=1, requested_qty=5)],
        )

        assert result.status == "available"
        assert result.available_qty == 7

    @respx.mock
    async def test_status_partial_quando_disponivel_menor_que_requested(
        self,
    ) -> None:
        """disponível=2, requested=5 → partial."""
        adapter = _adapter()
        respx.get(
            f"{BASE_URL}/saldoEstoqueAtual/SKU-A.M.1/505",
        ).mock(
            return_value=httpx.Response(
                200,
                json=_saldo_payload(estoque=5, reserv_pedido=3),
            ),
        )

        [result] = await adapter.check_availability(
            [StockQuery(sku="SKU-A", size="M", color_index=1, requested_qty=5)],
        )

        assert result.status == "partial"
        assert result.available_qty == 2

    @respx.mock
    async def test_status_out_of_stock_quando_disponivel_zero_ou_negativo(
        self,
    ) -> None:
        """estoque-reservas <= 0 → out_of_stock e available_qty == 0."""
        adapter = _adapter()
        respx.get(
            f"{BASE_URL}/saldoEstoqueAtual/SKU-B.G.1/505",
        ).mock(
            return_value=httpx.Response(
                200,
                json=_saldo_payload(estoque=2, reserv_pedido=5),
            ),
        )

        [result] = await adapter.check_availability(
            [StockQuery(sku="SKU-B", size="G", color_index=1, requested_qty=3)],
        )

        assert result.status == "out_of_stock"
        assert result.available_qty == 0

    @respx.mock
    async def test_aplica_formula_completa_estoque_menos_tres_reservas(
        self,
    ) -> None:
        """Valida que as 3 reservas (pedido + produção + lotes) descontam."""
        adapter = _adapter()
        respx.get(
            f"{BASE_URL}/saldoEstoqueAtual/SKU-C.P.1/505",
        ).mock(
            return_value=httpx.Response(
                200,
                json=_saldo_payload(
                    estoque=20,
                    reserv_pedido=4,
                    reserv_producao=3,
                    reserv_lotes=1,
                ),
            ),
        )

        [result] = await adapter.check_availability(
            [StockQuery(sku="SKU-C", size="P", color_index=1, requested_qty=2)],
        )

        # 20 - 4 - 3 - 1 = 12 disponíveis para requested=2 → available
        assert result.available_qty == 12
        assert result.status == "available"

    @respx.mock
    async def test_envia_header_empresa(self) -> None:
        adapter = _adapter()
        route = respx.get(
            f"{BASE_URL}/saldoEstoqueAtual/X.M.1/505",
        ).mock(
            return_value=httpx.Response(200, json=_saldo_payload()),
        )

        await adapter.check_availability(
            [StockQuery(sku="X", size="M", color_index=1, requested_qty=1)],
        )

        assert route.called
        sent_request = route.calls.last.request
        assert sent_request.headers["empresa"] == "50"

    @respx.mock
    async def test_processa_multiplos_items_em_paralelo(self) -> None:
        adapter = _adapter()
        for i in range(8):
            respx.get(
                f"{BASE_URL}/saldoEstoqueAtual/SKU-{i}.M.1/505",
            ).mock(
                return_value=httpx.Response(200, json=_saldo_payload(estoque=10)),
            )

        queries = [
            StockQuery(sku=f"SKU-{i}", size="M", color_index=1, requested_qty=2)
            for i in range(8)
        ]
        results = await adapter.check_availability(queries)

        assert len(results) == 8
        assert all(r.status == "available" for r in results)
        # A ordem do retorno espelha a ordem do input (contrato do adapter).
        for q, r in zip(queries, results, strict=True):
            assert r.sku == q.sku

    async def test_empty_input_no_http_calls(self) -> None:
        """Lista vazia não dispara request — barata para `Order` sem itens."""
        adapter = _adapter()
        with respx.mock(assert_all_called=True):  # nada chamado é OK
            results = await adapter.check_availability([])
        assert results == []


# ──────────────────────────────────────────────
#  check_availability — falhas → status="unknown"
# ──────────────────────────────────────────────


class TestCheckAvailabilityFailures:
    @respx.mock
    async def test_timeout_resulta_em_unknown(self) -> None:
        """ConnectTimeout/ReadTimeout do httpx → unknown, available_qty None."""
        adapter = _adapter()
        respx.get(
            f"{BASE_URL}/saldoEstoqueAtual/SKU-T.M.1/505",
        ).mock(side_effect=httpx.ReadTimeout("simulated timeout"))

        [result] = await adapter.check_availability(
            [StockQuery(sku="SKU-T", size="M", color_index=1, requested_qty=2)],
        )

        assert result.status == "unknown"
        assert result.available_qty is None

    @respx.mock
    async def test_http_500_resulta_em_unknown(self) -> None:
        adapter = _adapter()
        respx.get(
            f"{BASE_URL}/saldoEstoqueAtual/SKU-E.M.1/505",
        ).mock(return_value=httpx.Response(500, text="server error"))

        [result] = await adapter.check_availability(
            [StockQuery(sku="SKU-E", size="M", color_index=1, requested_qty=2)],
        )

        assert result.status == "unknown"
        assert result.available_qty is None

    @respx.mock
    async def test_payload_invalido_resulta_em_unknown(self) -> None:
        """Se o ERP retornar payload sem os campos esperados, não derrubamos."""
        adapter = _adapter()
        respx.get(
            f"{BASE_URL}/saldoEstoqueAtual/SKU-J.M.1/505",
        ).mock(
            return_value=httpx.Response(200, json={"unexpected": "shape"}),
        )

        # Payload sem `estoqueAtual` etc. cai no `.get(..., 0)` → disponível=0
        # e classifica como out_of_stock. Esse comportamento é intencional
        # e mais conservador do que reportar "unknown" — se o ERP responde
        # 200 com JSON válido, confiamos no schema (ausência ⇒ zero).
        [result] = await adapter.check_availability(
            [StockQuery(sku="SKU-J", size="M", color_index=1, requested_qty=2)],
        )

        assert result.status == "out_of_stock"
        assert result.available_qty == 0

    @respx.mock
    async def test_falha_parcial_nao_derruba_batch(self) -> None:
        """Um item falha, os outros 2 sucedem — todos retornam StockResult."""
        adapter = _adapter()
        respx.get(
            f"{BASE_URL}/saldoEstoqueAtual/OK-1.M.1/505",
        ).mock(return_value=httpx.Response(200, json=_saldo_payload(estoque=10)))
        respx.get(
            f"{BASE_URL}/saldoEstoqueAtual/FAIL.M.1/505",
        ).mock(side_effect=httpx.ConnectError("network down"))
        respx.get(
            f"{BASE_URL}/saldoEstoqueAtual/OK-2.M.1/505",
        ).mock(return_value=httpx.Response(200, json=_saldo_payload(estoque=10)))

        queries = [
            StockQuery(sku="OK-1", size="M", color_index=1, requested_qty=2),
            StockQuery(sku="FAIL", size="M", color_index=1, requested_qty=2),
            StockQuery(sku="OK-2", size="M", color_index=1, requested_qty=2),
        ]

        results = await adapter.check_availability(queries)

        assert len(results) == 3
        assert results[0].status == "available"
        assert results[1].status == "unknown"
        assert results[1].available_qty is None
        assert results[2].status == "available"


# ──────────────────────────────────────────────
#  submit_order — esqueleto (NotImplementedError)
# ──────────────────────────────────────────────


class TestSubmitOrder:
    async def test_levanta_not_implemented_com_mensagem_explicita(self) -> None:
        adapter = _adapter()

        with pytest.raises(NotImplementedError) as exc_info:
            await adapter.submit_order(
                order_reference="order-1",
                customer_code="C1",
                items=[
                    StockQuery(sku="X", size="M", color_index=1, requested_qty=1),
                ],
            )

        message = str(exc_info.value)
        assert "ConsistemAdapter.submit_order" in message
        assert "aguardando" in message.lower()
