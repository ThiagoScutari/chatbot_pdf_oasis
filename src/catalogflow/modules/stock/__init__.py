"""Módulo `stock` — integração de estoque e envio de pedidos ao ERP (Sprint 04).

Arquitetura: **Adapter Pattern**. `StockAdapter` é a interface abstrata
(definida em `adapter.py`); implementações concretas vivem em
`mock_adapter.py` (demonstração) e `consistem_adapter.py` (ERP Consistem
da AMC Têxtil). O `StockService` seleciona o adapter via
`settings.erp_adapter` — alternar entre demo e produção é só variável
de ambiente, sem rebuild.

Modelos ORM (`StockCheck`, `ErpSubmission`) registram cada consulta de
disponibilidade e cada envio de pedido para auditoria.
"""
