"""MockStockAdapter — adapter de demonstração com respostas determinísticas.

Permite que o sistema rode ponta-a-ponta sem credenciais de ERP nem rede:
útil para demo comercial e para a suíte de testes. O determinismo vem de
um hash MD5 do `(sku, size, color_index)` — o mesmo item sempre cai no
mesmo bucket, o que evita testes flaky.

Distribuição alvo (PRD Sprint 04):
- 70% dos itens → `available` (disponível = requested)
- 20% dos itens → `partial`   (disponível ≈ metade do requested, mínimo 1)
- 10% dos itens → `out_of_stock` (disponível = 0)

`submit_order` aceita qualquer pedido e devolve `MOCK-<8 hex>`. Isso é
intencional — o mock simula o "happy path" do ERP, não cenários de
rejeição (esses são testados via `_inject_error` em fixtures dedicadas
quando necessário).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from typing import Any

from catalogflow.modules.stock.adapter import (
    StockAdapter,
    StockQuery,
    StockResult,
    StockStatus,
)

logger = logging.getLogger(__name__)

# Latência simulada para se aproximar do comportamento de uma chamada real
# (UX da interface web precisa lidar com spinners — sem delay, o frontend
# nunca exercita o estado "consultando…").
_SIMULATED_DELAY_SECONDS = 0.5


class MockStockAdapter(StockAdapter):
    """Adapter de demonstração. Sem rede, determinístico, seguro p/ CI."""

    async def check_availability(
        self,
        items: list[StockQuery],
    ) -> list[StockResult]:
        await asyncio.sleep(_SIMULATED_DELAY_SECONDS)
        return [self._roll(item) for item in items]

    async def submit_order(
        self,
        order_reference: str,
        customer_code: str,
        items: list[StockQuery],
    ) -> dict[str, Any]:
        await asyncio.sleep(_SIMULATED_DELAY_SECONDS)
        erp_reference = f"MOCK-{uuid.uuid4().hex[:8]}"
        logger.info(
            "MockStockAdapter: aceitando pedido %s (cliente=%s) como %s",
            order_reference,
            customer_code,
            erp_reference,
        )
        return {
            "accepted": True,
            "erp_reference": erp_reference,
            "rejected_items": [],
            "message": "Pedido aceito pelo MockStockAdapter.",
        }

    @staticmethod
    def _roll(item: StockQuery) -> StockResult:
        """Decide o status do item via hash — mesmo input ⇒ mesmo output.

        Bucket calculado por MD5 (não-criptográfico — usado apenas para
        distribuição uniforme estável entre execuções). Buckets:

        - 0–69  → available  (70%)
        - 70–89 → partial    (20%)
        - 90–99 → out_of_stock (10%)
        """
        key = f"{item.sku}|{item.size}|{item.color_index}"
        # nosec B324 — md5 usado para distribuição estável, não para segurança
        bucket = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % 100

        status: StockStatus
        available_qty: int
        if bucket < 70:
            status = "available"
            available_qty = item.requested_qty
        elif bucket < 90:
            status = "partial"
            # Garante ao menos 1 unidade disponível no caso parcial, mesmo
            # quando o requested é 1 — caso contrário "partial" colapsaria
            # em "out_of_stock" para qty=1, distorcendo a demo.
            available_qty = max(1, item.requested_qty // 2)
            # Se o "partial" calculado igualar ou ultrapassar o requested
            # (acontece com requested<=2), rebaixamos para não confundir
            # a UI que espera available < requested em status parcial.
            if available_qty >= item.requested_qty:
                available_qty = max(0, item.requested_qty - 1)
                if available_qty == 0:
                    status = "out_of_stock"
        else:
            status = "out_of_stock"
            available_qty = 0

        return StockResult(
            sku=item.sku,
            size=item.size,
            color_index=item.color_index,
            requested_qty=item.requested_qty,
            available_qty=available_qty,
            status=status,
        )
