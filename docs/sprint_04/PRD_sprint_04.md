# PRD Sprint 04 — Integração ERP (Estoque + Envio de Pedidos)

> **Projeto:** CatalogFlow
> **Sprint:** 04 / ERP Integration
> **Status:** Aprovado
> **PMO:** Thiago Scutari
> **Executor:** Claude Code
> **Dependência:** Sprint 03.5 concluída ✅

---

## Objetivo da Sprint

Adicionar dois fluxos de integração com ERP ao CatalogFlow:

1. **Consulta de estoque** — ao receber um pedido, o sistema consulta o ERP para saber a disponibilidade de cada item (SKU × tamanho × cor) e gera um relatório de retorno para a gerente mostrando o que tem e o que não tem em estoque.

2. **Envio de pedido** — após a gerente aprovar o pedido, o sistema envia os itens ao ERP (POST/PUT) para registro no sistema de gestão da marca.

A arquitetura usa o **Adapter Pattern**: uma interface abstrata que qualquer ERP implementa. Para esta sprint, dois adapters são entregues:

- **MockAdapter** — simula respostas do ERP com dados de demonstração. Permite testar o fluxo completo sem depender de ambiente real.
- **ConsistemAdapter** — esqueleto pronto com a estrutura de chamadas HTTP que será preenchido quando a documentação da API Consistem estiver disponível.

---

## Contexto

A Oasis usa o **ERP Consistem** (cloud, API REST documentada em https://demo.consistem.com.br/api/). No entanto, os contratos específicos dos endpoints de estoque e pedido ainda não foram definidos. Esta sprint entrega a infraestrutura completa — quando os contratos chegarem, é só preencher o adapter.

---

## Modelo de dados

### Novas tabelas (migration 005)

```sql
-- stock_checks: registro de cada consulta de estoque
CREATE TABLE stock_checks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES orders(id),
    brand_id        UUID NOT NULL REFERENCES brands(id),
    status          VARCHAR(32) NOT NULL DEFAULT 'pending',
    -- pending | checking | completed | error
    checked_at      TIMESTAMPTZ,
    result          JSONB DEFAULT '{}',
    -- { items: [{sku, size, color_index, requested, available, status}] }
    error_message   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- erp_submissions: registro de cada envio de pedido ao ERP
CREATE TABLE erp_submissions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL UNIQUE REFERENCES orders(id),
    brand_id        UUID NOT NULL REFERENCES brands(id),
    status          VARCHAR(32) NOT NULL DEFAULT 'pending',
    -- pending | submitting | accepted | partially_accepted | rejected | error
    submitted_at    TIMESTAMPTZ,
    erp_reference   VARCHAR(255),
    -- código do pedido no ERP (retornado pelo ERP após aceitar)
    result          JSONB DEFAULT '{}',
    error_message   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_stock_checks_order ON stock_checks(order_id);
CREATE INDEX idx_erp_submissions_order ON erp_submissions(order_id);
```

### Atualizar order_items

Adicionar colunas para resultado de estoque:

```sql
ALTER TABLE order_items ADD COLUMN stock_status VARCHAR(32);
-- available | partial | out_of_stock | unknown
ALTER TABLE order_items ADD COLUMN available_qty INTEGER;
```

---

## Módulo `stock`

### `stock/adapter.py` — Interface abstrata

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class StockQuery:
    sku: str
    size: str
    color_index: int
    requested_qty: int

@dataclass
class StockResult:
    sku: str
    size: str
    color_index: int
    requested_qty: int
    available_qty: int
    status: str  # "available" | "partial" | "out_of_stock"

class StockAdapter(ABC):
    @abstractmethod
    async def check_availability(
        self, items: list[StockQuery]
    ) -> list[StockResult]: ...

    @abstractmethod
    async def submit_order(
        self,
        order_reference: str,
        customer_code: str,
        items: list[StockQuery]
    ) -> dict:
        """
        Retorna: {
            "accepted": True/False,
            "erp_reference": "12345",
            "rejected_items": [...],
            "message": "..."
        }
        """
        ...
```

### `stock/mock_adapter.py` — Demonstração

```python
class MockStockAdapter(StockAdapter):
    """
    Simula respostas do ERP para demonstração.
    
    Regras do mock:
    - 70% dos itens: available (qty disponível = requested)
    - 20% dos itens: partial (qty disponível = metade do requested)
    - 10% dos itens: out_of_stock (qty = 0)
    
    Pedido: sempre aceito pelo mock, erp_reference gerado como "MOCK-{uuid[:8]}"
    
    Delay simulado: 0.5s (simula latência de rede)
    """
```

### `stock/consistem_adapter.py` — Adapter real (consulta implementada)

```python
class ConsistemAdapter(StockAdapter):
    """
    Adapter para o ERP Consistem (AMC Têxtil).
    
    Documentação: https://demo.consistem.com.br/api/
    
    Endpoint de estoque:
        GET /saldoEstoqueAtual/{codItem}/{codNatureza}
        Header: empresa = "50"  (AMC Têxtil)
        codNatureza = 505       (Estoque nacional AMC)
    
    Response (campos relevantes):
        {
            "codItem": "string",
            "estoqueAtual": 9999999.999,
            "estReservPedido": 9999999.999,
            "estReservProducao": 9999999.999,
            "estReservLotes": 9999999.999
        }
    
    Cálculo de disponibilidade:
        disponivel = estoqueAtual - estReservPedido - estReservProducao - estReservLotes
    
    Mapeamento de codItem:
        Cada combinação SKU+tamanho+cor é um codItem diferente no ERP.
        Formato provisório: "referencia.tamanho.cor"
        Ex: "0442500941-0.PP.1"
        
        NOTA: mapeamento real será fornecido pela Oasis futuramente.
        A função _build_cod_item() centraliza a conversão — quando
        o mapeamento real chegar, APENAS essa função muda.
    """
    
    def __init__(
        self,
        base_url: str,
        api_key: str,
        empresa: str = "50",
        cod_natureza: int = 505,
        timeout: int = 30,
    ): ...
    
    def _build_cod_item(self, sku: str, size: str, color_index: int) -> str:
        """
        Converte SKU+tamanho+cor para codItem do Consistem.
        Formato provisório: "referencia.tamanho.cor"
        Quando o mapeamento real chegar, alterar APENAS esta função.
        """
        return f"{sku}.{size}.{color_index}"
    
    async def check_availability(self, items: list[StockQuery]) -> list[StockResult]:
        """
        Para cada item:
        1. Monta codItem via _build_cod_item()
        2. GET {base_url}/saldoEstoqueAtual/{codItem}/{cod_natureza}
           Header: empresa={empresa}
        3. Parseia response:
           disponivel = estoqueAtual - estReservPedido
                      - estReservProducao - estReservLotes
        4. Compara disponivel com requested_qty:
           disponivel >= requested → "available"
           0 < disponivel < requested → "partial"
           disponivel <= 0 → "out_of_stock"
        
        Timeout: 3s por request (httpx).
        Se um item falhar: status="unknown", available_qty=None.
        Requests em paralelo via asyncio.gather() com semáforo de 5
        (máximo 5 requests simultâneas ao ERP).
        """
    
    async def submit_order(self, order_reference, customer_code, items):
        """
        TODO: endpoint de criação de pedido no Consistem.
        Aguardando definição do contrato pela Oasis.
        """
        raise NotImplementedError(
            "ConsistemAdapter.submit_order: aguardando definição "
            "do endpoint de pedido no Consistem."
        )
```

### `stock/service.py`

```python
class StockService:
    async def check_order_stock(
        self, order_id: UUID, brand_id: UUID
    ) -> StockCheck:
        """
        1. Carrega OrderItems do pedido
        2. Converte para list[StockQuery]
        3. Chama adapter.check_availability()
        4. Atualiza order_items com stock_status e available_qty
        5. Persiste StockCheck com resultado completo
        """

    async def submit_order_to_erp(
        self, order_id: UUID, brand_id: UUID, customer_code: str
    ) -> ErpSubmission:
        """
        1. Carrega pedido com items
        2. Chama adapter.submit_order()
        3. Persiste ErpSubmission com erp_reference
        4. Atualiza Order.status se aceito
        """

    def get_adapter(self) -> StockAdapter:
        """
        Retorna o adapter configurado via settings.
        ERP_ADAPTER=mock → MockStockAdapter
        ERP_ADAPTER=consistem → ConsistemAdapter
        """
```

### `stock/tasks.py`

```python
@celery_app.task(name="stock.check")
def check_stock_task(order_id: str) -> dict: ...

@celery_app.task(name="stock.submit")
def submit_order_task(order_id: str, customer_code: str) -> dict: ...
```

---

## Endpoints da API

### `POST /api/v1/orders/{order_id}/stock-check`

Dispara consulta de estoque para todos os itens do pedido.

Request: body vazio (autenticado por Bearer)
Response 202:
```json
{
  "data": {
    "stock_check_id": "uuid",
    "job_id": "uuid",
    "status": "pending"
  }
}
```

### `GET /api/v1/orders/{order_id}/stock-check`

Retorna resultado da última consulta de estoque.

Response 200:
```json
{
  "data": {
    "status": "completed",
    "checked_at": "ISO-8601",
    "summary": {
      "total_items": 19,
      "available": 13,
      "partial": 4,
      "out_of_stock": 2
    },
    "items": [
      {
        "sku": "0442500941-0",
        "product_name": "Vestido Joana",
        "size": "PP",
        "color_index": 1,
        "requested": 2,
        "available": 2,
        "status": "available"
      },
      {
        "sku": "0442500941-0",
        "product_name": "Vestido Joana",
        "size": "G",
        "color_index": 1,
        "requested": 2,
        "available": 0,
        "status": "out_of_stock"
      }
    ]
  }
}
```

### `POST /api/v1/orders/{order_id}/submit`

Envia pedido ao ERP.

Request:
```json
{
  "customer_code": "12345"
}
```

Response 202:
```json
{
  "data": {
    "submission_id": "uuid",
    "job_id": "uuid",
    "status": "pending"
  }
}
```

### `GET /api/v1/orders/{order_id}/submission`

Retorna status do envio ao ERP.

Response 200:
```json
{
  "data": {
    "status": "accepted",
    "erp_reference": "MOCK-a7f3e91b",
    "submitted_at": "ISO-8601"
  }
}
```

---

## Interface Web

### Detalhe do pedido (`/orders/{id}`) — novos elementos

Após os itens do pedido, adicionar:

**Bloco de estoque:**

```
  Disponibilidade em estoque
  ┌──────────────────────────────────────────────┐
  │  [ Consultar estoque → ]  botão bordô         │
  │                                                │
  │  Após consulta:                                │
  │  ● 13 disponíveis  ○ 4 parciais  ✕ 2 zerados  │
  │                                                │
  │  Cada item na tabela/card ganha badge:          │
  │  ● Disponível (verde)                          │
  │  ○ Parcial (âmbar) — "2 de 4 disponíveis"     │
  │  ✕ Indisponível (vermelho)                     │
  └──────────────────────────────────────────────┘
```

**Bloco de envio ao ERP:**

```
  Enviar pedido ao ERP
  ┌──────────────────────────────────────────────┐
  │  Código do cliente no ERP                      │
  │  ┌──────────────────────────────┐              │
  │  │  12345                       │              │
  │  └──────────────────────────────┘              │
  │                                                │
  │  [ Enviar ao ERP → ]  botão bordô              │
  │                                                │
  │  Após envio:                                   │
  │  ✓ Pedido aceito — Ref: MOCK-a7f3e91b          │
  └──────────────────────────────────────────────┘
```

---

## Settings

Adicionar em `settings.py`:

```python
# ERP Integration
erp_adapter: str = "mock"       # "mock" | "consistem"
erp_base_url: str = "https://api.consistem.com.br"
erp_api_key: SecretStr | None = None
erp_empresa: str = "50"         # Código da empresa AMC Têxtil
erp_cod_natureza: int = 505     # Natureza de estoque nacional AMC
erp_timeout: int = 30
```

`.env.example`:
```
ERP_ADAPTER=mock
ERP_BASE_URL=https://api.consistem.com.br
ERP_API_KEY=
ERP_EMPRESA=50
ERP_COD_NATUREZA=505
ERP_TIMEOUT=30
```

---

## Acceptance Criteria

| ID | Critério | Verificação |
|----|---------|------------|
| AC-01 | POST /orders/{id}/stock-check retorna 202 + job_id | Automated |
| AC-02 | GET /orders/{id}/stock-check retorna resultado com summary e items | Automated |
| AC-03 | MockAdapter retorna mix de available/partial/out_of_stock | Automated |
| AC-04 | order_items.stock_status e available_qty atualizados após check | Automated |
| AC-05 | POST /orders/{id}/submit com customer_code retorna 202 | Automated |
| AC-06 | MockAdapter aceita pedido e retorna erp_reference | Automated |
| AC-07 | Interface web mostra badges de estoque por item | Manual |
| AC-08 | Interface web mostra campo customer_code + botão enviar | Manual |
| AC-09 | ConsistemAdapter levanta NotImplementedError com mensagem clara | Automated |
| AC-10 | Troca de adapter via variável ERP_ADAPTER funciona sem rebuild | Automated |
| AC-11 | pytest ≥ 80% cobertura | CI |

---

## Ordem de Implementação

```
1.  Migration 005 (stock_checks, erp_submissions, alter order_items)
2.  stock/adapter.py (interface abstrata)
3.  stock/mock_adapter.py + testes
4.  stock/consistem_adapter.py (check_availability implementado, submit_order esqueleto)
5.  stock/models.py (StockCheck, ErpSubmission)
6.  stock/service.py + testes
7.  stock/tasks.py
8.  stock/router.py (4 endpoints) + testes
9.  Registrar rotas em main.py
10. orders/detail.html — bloco estoque + bloco envio ERP
11. CHANGELOG + README
```

---

## O que essa sprint NÃO faz

- ❌ Implementar submit_order real no Consistem (aguardando endpoint de pedido)
- ❌ Reserva de estoque com TTL (Sprint futura)
- ❌ Webhook do ERP para o CatalogFlow
- ❌ Sincronização periódica de estoque (Celery Beat)
