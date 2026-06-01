# PRD Sprint 07 — Robustez: Stock Check Stuck + Placeholder de Imagem

> **Projeto:** CatalogFlow
> **Sprint:** 07 / Robustez
> **Status:** Aprovação Pendente
> **Data:** 2026-05-26
> **PMO:** Thiago Scutari
> **Executor:** Claude Code
> **Referência obrigatória:** `spec.md`, `CLAUDE.md`

---

## Contexto

Durante demonstração do produto para potencial cliente, dois problemas de UX
ocorreram simultaneamente:

1. A tela de "Consultando estoque no ERP..." ficou girando indefinidamente
2. Duas linhas de produto apareceram sem imagem e sem descrição visual

Ambos ocorreram em produção com usuário real presente. Esta sprint resolve
os dois na fonte, com proteção contra recorrência.

---

## Sumário Executivo

| ID | Severidade | Descrição | Impacto |
|----|-----------|-----------|---------|
| S07-01 | 🔴 Crítico | Stock check trava infinitamente quando job fica stuck | UX quebrada, polling eterno, nenhuma saída para o usuário |
| S07-02 | 🟡 Médio | Produto sem imagem não exibe placeholder visual | Linha em branco — parece bug de dados |

S07-01 requer uma migration para adicionar `started_at` ao modelo `Job`.
S07-02 é puramente frontend/proxy — sem migration.

---

## S07-01 — Stock check trava infinitamente

### Causa raiz confirmada (banco de produção)

```
StockCheck ad7189da → status: pending desde 2026-05-26 17:35:07, checked_at: NULL
Job bb409c29        → status: pending, updated_at = created_at (nunca transitou)
```

O worker recebeu a task, executou `_claim_job` (UPDATE WHERE status='pending'),
mas retornou `skipped: True` — o job nunca transitou para `running`. O
`StockCheck.status` ficou `pending` para sempre.

`_classify_stock_state()` classifica qualquer status diferente de `completed`
ou `error` como `"checking"`. HTMX poleia a cada 2s sem timeout, sem limite.

### Solução — 3 camadas de proteção

#### Camada 1 — Timeout no backend (detecção de stuck)

Adicionar campo `started_at: datetime | None` ao modelo `Job` via migration.
Atualizar `_claim_job` para gravar `started_at = NOW()` ao transitar para
`running`.

Em `_classify_stock_state()` (router.py), adicionar detecção de stuck:
qualquer StockCheck com job criado há mais de 5 minutos ainda em
`pending` ou `checking` deve ser classificado como `"error"`.

```python
STOCK_CHECK_TIMEOUT_MINUTES = 5

def _classify_stock_state(detail: OrderDetail) -> str:
    if detail.stock_check is None:
        return "absent"
    sc = detail.stock_check
    if sc.status == "completed":
        return "completed"
    if sc.status == "error":
        return "error"
    # Stuck detection: job criado há > 5 min ainda não completou
    age = datetime.now(UTC) - sc.created_at
    if age > timedelta(minutes=STOCK_CHECK_TIMEOUT_MINUTES):
        return "error"
    return "checking"
```

Quando `_classify_stock_state` detectar `"error"` por timeout, a UI
exibe a mensagem de erro existente com botão "Tentar novamente" —
sem necessidade de transitar o status no banco (evita write desnecessário
no hot path de polling).

#### Camada 2 — Limite de polls no frontend

Adicionar contador de polls ao fragmento HTMX. Após 90 polls (3 minutos),
parar automaticamente e exibir erro com botão de retry.

O contador é passado via query param `?poll_count=N` incrementado a cada
render do fragmento.

```html
<!-- _stock_action.html — quando stock_state == "checking" -->
{% if poll_count < 90 %}
<div
  hx-get="/orders/{{ order_id }}/stock-check-poll?poll_count={{ poll_count + 1 }}"
  hx-trigger="load delay:2s"
  hx-swap="outerHTML"
>
  <!-- spinner -->
</div>
{% else %}
<div>
  <p>A consulta de estoque demorou mais que o esperado.</p>
  <button hx-post="/orders/{{ order_id }}/stock-check"
          hx-swap="outerHTML" hx-target="closest div">
    Tentar novamente
  </button>
</div>
{% endif %}
```

O endpoint `/stock-check-poll` recebe `poll_count` como query param int
(default 0) e repassa ao template.

#### Camada 3 — Idempotência no enqueue

Antes de criar um novo `StockCheck`, verificar se já existe um em estado
`pending` ou `checking` criado nos últimos 5 minutos para o mesmo `order_id`.
Se sim, retornar o existente — evitando o duplo dispatch que causou o incidente.

```python
# Em enqueue_stock_check (service.py):
async def _get_active_stock_check(
    self, order_id: UUID
) -> StockCheck | None:
    """Retorna StockCheck pending/checking criado nos últimos 5 min, se existir."""
    cutoff = datetime.now(UTC) - timedelta(minutes=5)
    result = await self.db.execute(
        select(StockCheck)
        .where(
            StockCheck.order_id == order_id,
            StockCheck.status.in_(["pending", "checking"]),
            StockCheck.created_at > cutoff,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()

async def enqueue_stock_check(self, order_id: UUID, brand_id: UUID):
    await _load_order_owned(order_id, brand_id)
    existing = await self._get_active_stock_check(order_id)
    if existing:
        logger.info("stock.enqueue: returning existing active check %s", existing.id)
        return existing, None
    # ... lógica original de criação
```

### Migration

```python
# alembic/versions/0007_add_started_at_to_jobs.py
# Adicionar coluna nullable ao modelo Job:
op.add_column("jobs", sa.Column(
    "started_at", sa.DateTime(timezone=True), nullable=True
))

# Modelo Job (models.py):
started_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True), nullable=True
)
```

### Testes de regressão obrigatórios

```python
def test_classify_stock_state_returns_error_for_stuck_job():
    """StockCheck com created_at > 5 min atrás deve retornar 'error'."""

def test_classify_stock_state_returns_checking_when_recent():
    """StockCheck recente (< 5 min) deve retornar 'checking', não 'error'."""

def test_stock_check_poll_stops_at_90(client, stock_check_checking):
    """Fragmento com poll_count=90 não deve conter hx-trigger de polling."""

def test_stock_check_poll_continues_below_90(client, stock_check_checking):
    """Fragmento com poll_count=89 deve conter hx-trigger de polling."""

def test_enqueue_returns_existing_when_pending_recent(db_session, brand):
    """enqueue_stock_check retorna existente se há pending < 5 min."""

def test_enqueue_creates_new_when_no_active(db_session, brand):
    """enqueue_stock_check cria novo se não há pending recente."""

def test_claim_job_sets_started_at(db_session):
    """_claim_job deve gravar started_at ao transitar para running."""
```

---

## S07-02 — Produto sem imagem não exibe placeholder visual

### Causa raiz confirmada

Todos os `order_items` do pedido afetado têm `product_name` correto —
o problema não é de dados. A investigação aponta que o endpoint
`/product-image/{sku}` pode estar retornando status de erro (404 ou 500)
quando o scraper AMC falha, em vez de retornar o SVG placeholder com
status `200`. O browser exibe o ícone de imagem quebrada em vez do
placeholder porque recebeu um erro HTTP.

### Solução

Garantir que `/product-image/{sku}` **sempre retorna status 200** —
nunca propaga erro HTTP ao browser:

```python
@router.get("/product-image/{sku}")
async def product_image_proxy(
    sku: str,
    name: str = "",
    brand: Brand = Depends(require_session_brand),
):
    try:
        image_bytes = await fetch_product_image_bytes(sku)
        if image_bytes:
            return Response(
                content=image_bytes,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400"},
                status_code=200,
            )
    except Exception:
        logger.warning("product_image_proxy: falha ao buscar %s", sku)

    # Sempre retorna placeholder — nunca deixa o browser ver erro
    return _placeholder_svg_response(name or sku)
```

Verificar que `_placeholder_svg_response` retorna:
- `status_code=200`
- `Content-Type: image/svg+xml`
- SVG válido com as iniciais do produto

### Testes de regressão obrigatórios

```python
def test_product_image_proxy_returns_placeholder_when_fetch_returns_none(
    client, monkeypatch
):
    """Quando fetch retorna None, endpoint retorna 200 com SVG."""

def test_product_image_proxy_returns_placeholder_on_exception(
    client, monkeypatch
):
    """Quando fetch lança Exception, endpoint retorna 200 com SVG."""

def test_product_image_proxy_returns_200_always(client, monkeypatch):
    """Endpoint nunca retorna status != 200."""

def test_placeholder_svg_response_is_valid():
    """_placeholder_svg_response retorna 200, Content-Type svg+xml, SVG válido."""
```

---

## Acceptance Criteria

| ID | Critério | Verificação |
|----|---------|------------|
| AC-01 | StockCheck com job > 5 min → `_classify_stock_state` retorna `"error"` | Teste unitário |
| AC-02 | StockCheck recente continua retornando `"checking"` | Teste unitário |
| AC-03 | Fragmento com `poll_count=90` não contém `hx-trigger` de polling | Teste de template |
| AC-04 | `enqueue_stock_check` retorna existente se já há pending < 5 min | Teste unitário |
| AC-05 | `_claim_job` grava `started_at` ao transitar para `running` | Teste unitário |
| AC-06 | `/product-image/{sku}` retorna 200 + SVG quando fetch falha | Teste de endpoint |
| AC-07 | `/product-image/{sku}` retorna 200 + SVG quando fetch lança exceção | Teste de endpoint |
| AC-08 | `pytest` passa, cobertura ≥ 80% | CI |
| AC-09 | CI 100% verde sem admin override | CI |
| AC-10 | Smoke test manual em produção: pedido `f3980967` sem spinner infinito | Manual |

---

## Definition of Done

- [ ] Migration `0007_add_started_at_to_jobs` criada e testada
- [ ] `_claim_job` grava `started_at`
- [ ] `_classify_stock_state` detecta stuck por timeout (5 min)
- [ ] `enqueue_stock_check` com idempotência para pending recente
- [ ] Fragmento HTMX com limite de 90 polls + mensagem de erro
- [ ] `/stock-check-poll` recebe e repassa `poll_count`
- [ ] `/product-image/{sku}` sempre retorna 200
- [ ] `_placeholder_svg_response` verificado (200 + SVG válido)
- [ ] Todos os testes de regressão escritos e passando
- [ ] CI verde, deploy em produção, smoke test manual

---

## Out of Scope (esta sprint)

- ❌ `ConsistemAdapter.submit_order` (aguarda Oasis)
- ❌ Upload de pedido via web / soft-delete
- ❌ Notificação SSE quando job termina
- ❌ Watchdog Celery beat para recovery automático de jobs stuck (sprint futura)

---

## Ordem de Implementação

```
1. Inspeção (PROMPT 0)
2. S07-01A: Migration + _claim_job + detecção de stuck (PROMPT 1)
3. S07-01B: Idempotência no enqueue + limite de polls no frontend (PROMPT 2)
4. S07-02: Placeholder sempre 200 (PROMPT 3)
5. Suite + commits + CI (PROMPT 4)
```
