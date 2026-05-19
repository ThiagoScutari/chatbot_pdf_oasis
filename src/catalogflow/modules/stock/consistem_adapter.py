"""ConsistemAdapter — integração real com o ERP Consistem (AMC Têxtil).

Documentação do fornecedor: https://demo.consistem.com.br/api/

Endpoint de estoque consumido nesta sprint::

    GET {base_url}/saldoEstoqueAtual/{codItem}/{codNatureza}
    Header: empresa = "50"  (código da AMC Têxtil)

Cálculo de disponibilidade (espelha a regra contábil do Consistem)::

    disponivel = estoqueAtual
               - estReservPedido
               - estReservProducao
               - estReservLotes

Mapeamento de status (PRD Sprint 04):

- `disponivel >= requested`     → "available"
- `0 < disponivel < requested`  → "partial"
- `disponivel <= 0`             → "out_of_stock"
- Falha de rede / 4xx / 5xx     → "unknown" (available_qty = None)

Concorrência: requests paralelas via `asyncio.gather()` com semáforo
de 5 — limita a pressão no ERP para evitar throttling.

`submit_order` ainda não está implementado: o contrato do endpoint de
criação de pedido no Consistem aguarda definição da Oasis. Levanta
`NotImplementedError` com mensagem explícita até lá.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from catalogflow.modules.stock.adapter import (
    StockAdapter,
    StockQuery,
    StockResult,
    StockStatus,
)

logger = logging.getLogger(__name__)

# Timeout aplicado a cada GET individual. Curto de propósito: estoque é
# um dado "vivo" — se o ERP demorou mais que isso para responder um SKU,
# a resposta provavelmente já é stale e melhor reportar "unknown".
_PER_REQUEST_TIMEOUT_SECONDS = 3.0

# Limite de paralelismo contra o ERP. Catálogos grandes podem disparar
# centenas de queries — sem o semáforo, derrubaríamos o Consistem.
_MAX_PARALLEL_REQUESTS = 5


class ConsistemAdapter(StockAdapter):
    """Adapter HTTP para o ERP Consistem da AMC Têxtil."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        empresa: str = "50",
        cod_natureza: int = 505,
        timeout: int = 30,
    ) -> None:
        # Remove a barra final para que `_build_url` componha sempre com
        # exatamente um separador, independente da configuração do .env.
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.empresa = empresa
        self.cod_natureza = cod_natureza
        # `timeout` agregado (configurável via settings) — usado para o
        # `httpx.AsyncClient` global. O timeout *por request* é menor
        # (_PER_REQUEST_TIMEOUT_SECONDS) e fixo, porque é uma decisão de
        # contrato com o ERP, não algo a expor como configuração.
        self.timeout = timeout

    # ─────────────────────────────────────────
    #  Conversão SKU/tamanho/cor → codItem
    # ─────────────────────────────────────────

    def _build_cod_item(self, sku: str, size: str, color_index: int) -> str:
        """Converte (sku, size, color_index) para `codItem` do Consistem.

        Formato provisório: ``"{sku}.{size}.{color_index}"``
        Ex.: `("0442500941-0", "PP", 1)` → ``"0442500941-0.PP.1"``.

        O mapeamento real será definido pela Oasis no futuro (pode
        envolver tabela de-para, padding ou prefixos de coleção).
        Quando chegar, **apenas esta função muda** — `check_availability`,
        service, tasks e testes de integração permanecem intactos.
        """
        return f"{sku}.{size}.{color_index}"

    # ─────────────────────────────────────────
    #  check_availability — paralelizado com Semaphore(5)
    # ─────────────────────────────────────────

    async def check_availability(
        self,
        items: list[StockQuery],
    ) -> list[StockResult]:
        if not items:
            return []

        semaphore = asyncio.Semaphore(_MAX_PARALLEL_REQUESTS)

        async with httpx.AsyncClient(timeout=_PER_REQUEST_TIMEOUT_SECONDS) as client:

            async def bounded_query(item: StockQuery) -> StockResult:
                async with semaphore:
                    return await self._query_item(client, item)

            return await asyncio.gather(*(bounded_query(it) for it in items))

    async def _query_item(
        self,
        client: httpx.AsyncClient,
        item: StockQuery,
    ) -> StockResult:
        """Consulta um SKU/tamanho/cor. Erros viram `status="unknown"`."""
        cod_item = self._build_cod_item(item.sku, item.size, item.color_index)
        url = f"{self.base_url}/saldoEstoqueAtual/{cod_item}/{self.cod_natureza}"
        headers: dict[str, str] = {"empresa": self.empresa}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
            disponivel = self._calc_disponivel(payload)
            status, available_qty = self._classify(disponivel, item.requested_qty)
            return StockResult(
                sku=item.sku,
                size=item.size,
                color_index=item.color_index,
                requested_qty=item.requested_qty,
                available_qty=available_qty,
                status=status,
            )
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            logger.warning(
                "ConsistemAdapter: falha consultando %s (%s) — status=unknown",
                cod_item,
                exc.__class__.__name__,
            )
            return self._unknown(item)
        except (ValueError, KeyError, TypeError) as exc:
            # `response.json()` ou cast de campos falhou — payload do ERP
            # mudou ou veio incompleto. Não derruba o batch inteiro.
            logger.warning(
                "ConsistemAdapter: resposta inesperada para %s (%s) — status=unknown",
                cod_item,
                exc,
            )
            return self._unknown(item)

    # ─────────────────────────────────────────
    #  Helpers puros (testáveis isoladamente)
    # ─────────────────────────────────────────

    @staticmethod
    def _calc_disponivel(payload: dict[str, Any]) -> int:
        """Aplica a fórmula contábil do Consistem ao payload bruto.

        Os campos numéricos chegam como floats (o ERP usa decimal de 3
        casas). Como SKU têxtil é unidade inteira, truncamos no fim —
        peças fracionárias não existem no domínio.
        """
        estoque_atual = float(payload.get("estoqueAtual", 0))
        reserv_pedido = float(payload.get("estReservPedido", 0))
        reserv_producao = float(payload.get("estReservProducao", 0))
        reserv_lotes = float(payload.get("estReservLotes", 0))
        return int(estoque_atual - reserv_pedido - reserv_producao - reserv_lotes)

    @staticmethod
    def _classify(disponivel: int, requested: int) -> tuple[StockStatus, int]:
        """Mapeia disponivel x requested para (status, available_qty)."""
        if disponivel <= 0:
            return "out_of_stock", 0
        if disponivel >= requested:
            return "available", disponivel
        return "partial", disponivel

    @staticmethod
    def _unknown(item: StockQuery) -> StockResult:
        return StockResult(
            sku=item.sku,
            size=item.size,
            color_index=item.color_index,
            requested_qty=item.requested_qty,
            available_qty=None,
            status="unknown",
        )

    # ─────────────────────────────────────────
    #  submit_order — pendente (aguardando Oasis)
    # ─────────────────────────────────────────

    async def submit_order(
        self,
        order_reference: str,
        customer_code: str,
        items: list[StockQuery],
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "ConsistemAdapter.submit_order: aguardando definição do endpoint "
            "de pedido no Consistem.",
        )
