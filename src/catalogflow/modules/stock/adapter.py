"""Interface abstrata para adapters de ERP (Adapter Pattern).

`StockAdapter` é a única dependência que `StockService` enxerga. Cada ERP
(Consistem, Linx, Bling, etc.) implementa um adapter concreto. Trocar de
ERP em runtime é uma única variável de ambiente (`ERP_ADAPTER`) — o resto
do sistema não muda.

Convenções do contrato:

- `check_availability` é **idempotente** — pode ser chamado N vezes para
  o mesmo pedido sem efeitos colaterais. Falhas de rede são absorvidas
  como `status="unknown"` por item (nunca levanta exceção parcial).
- `submit_order` **não é idempotente** por natureza (envio cria pedido
  no ERP). Adapters devem retornar `erp_reference` para deduplicação no
  lado do CatalogFlow.
- `available_qty` é `None` apenas quando `status="unknown"` (falha de
  consulta). Em qualquer outro status, é inteiro >= 0.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

StockStatus = Literal["available", "partial", "out_of_stock", "unknown"]


@dataclass(frozen=True)
class StockQuery:
    """Input para consulta de disponibilidade de um SKU/tamanho/cor."""

    sku: str
    size: str
    color_index: int
    requested_qty: int


@dataclass(frozen=True)
class StockResult:
    """Saída da consulta de disponibilidade para um item.

    `available_qty` é `None` apenas quando `status == "unknown"`
    (falha transitória do adapter — timeout, 5xx, parse error). Em
    qualquer outro status, é inteiro >= 0 e o valor é confiável.
    """

    sku: str
    size: str
    color_index: int
    requested_qty: int
    available_qty: int | None
    status: StockStatus


class StockAdapter(ABC):
    """Contrato comum a todos os adapters de ERP.

    Implementações concretas: `MockStockAdapter` (demonstração) e
    `ConsistemAdapter` (ERP Consistem, AMC Têxtil). Selecionado via
    `settings.erp_adapter` no `StockService`.
    """

    @abstractmethod
    async def check_availability(
        self,
        items: list[StockQuery],
    ) -> list[StockResult]:
        """Consulta a disponibilidade real de cada item no ERP.

        Deve retornar **um StockResult por StockQuery**, na mesma ordem.
        Falhas pontuais (timeout, 5xx) viram `status="unknown"` para o
        item afetado — nunca falha o batch inteiro.
        """

    @abstractmethod
    async def submit_order(
        self,
        order_reference: str,
        customer_code: str,
        items: list[StockQuery],
    ) -> dict[str, Any]:
        """Envia o pedido ao ERP. Retorna payload com a referência gerada.

        Estrutura esperada do retorno::

            {
                "accepted": bool,
                "erp_reference": str | None,
                "rejected_items": list[dict],
                "message": str,
            }

        Adapters que ainda não suportam envio devem levantar
        `NotImplementedError` com mensagem explícita do que falta.
        """
