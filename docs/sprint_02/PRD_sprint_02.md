# PRD Sprint 02 — Orders + Romaneio

> **Projeto:** CatalogFlow
> **Sprint:** 02 / Order Extraction + Romaneio Generation
> **Status:** Aprovado
> **Data de início:** A definir
> **Duração estimada:** 5–7 dias de trabalho do executor
> **PMO:** Thiago Scutari
> **Executor:** Claude Code
> **Referência obrigatória:** `spec.md` (contrato técnico do projeto)
> **Dependência:** Sprint 01 concluída e em main ✅

---

## Objetivo da Sprint

Completar o ciclo de pedido ponta a ponta: a lojista preenche o PDF editável gerado na Sprint 01, envia de volta, e o sistema extrai os campos preenchidos, estrutura o pedido e gera o romaneio PDF profissional.

Ao final desta sprint, o fluxo completo deve funcionar:

1. Receber um PDF preenchido via `POST /api/v1/orders/extract`
2. Extrair os campos AcroForm (formatos v1 e v2) e estruturar os itens do pedido
3. Cruzar SKUs com o catálogo de origem quando `catalog_id` for fornecido
4. Disponibilizar o pedido estruturado via `GET /api/v1/orders/{id}`
5. Gerar o romaneio PDF via `GET /api/v1/orders/{id}/romaneio`
6. Manter cobertura de testes ≥ 80% com CI verde

---

## Contexto

O repositório já contém os esqueletos dos módulos `orders/` e `romaneio/` com a estrutura de arquivos correta — todos os métodos levantam `NotImplementedError("Sprint 02")`. Esta sprint **preenche esses esqueletos** com lógica real.

A lógica de referência está em `oasis_romaneio.py`. O mesmo padrão de migração aplicado ao `oasis_form_v2.py` na Sprint 01 se repete aqui: migrar para funções puras (`extractor.py`, `normalizer.py`, `builder.py`), sem I/O interno, todo I/O isolado no `service.py`.

Os modelos de banco (`orders`, `order_items`, `romaneios`) já existem no spec mas **ainda não foram criados via Alembic** — a migration é parte desta sprint.

---

## Entregáveis

### E1 — Alembic migrations

#### `migrations/versions/003_orders_schema.py`

Criar as tabelas conforme o spec.md §7 **e** adicionar `logo_key` à tabela `brands` existente:

```sql
-- brands: adicionar logo_key (S3 key da logo da marca)
ALTER TABLE brands ADD COLUMN logo_key VARCHAR(512);

-- orders
CREATE TABLE orders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_id        UUID NOT NULL REFERENCES brands(id),
    catalog_id      UUID REFERENCES catalogs(id),
    lojista_token   VARCHAR(64),
    lojista_name    VARCHAR(255),
    status          VARCHAR(32) NOT NULL DEFAULT 'draft',
    source_pdf_key  VARCHAR(512),
    total_pecas     INTEGER,
    valor_total     NUMERIC(12,2),
    extracted_at    TIMESTAMPTZ,
    confirmed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- order_items
CREATE TABLE order_items (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id     UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    sku          VARCHAR(64) NOT NULL,
    product_name VARCHAR(255),
    color_index  INTEGER NOT NULL DEFAULT 1,
    color_hex    VARCHAR(7),
    size         VARCHAR(8) NOT NULL,
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    unit_price   NUMERIC(10,2),
    stock_status VARCHAR(32),
    available_qty INTEGER,
    UNIQUE(order_id, sku, color_index, size)
);

-- romaneios
CREATE TABLE romaneios (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id     UUID NOT NULL UNIQUE REFERENCES orders(id),
    brand_id     UUID NOT NULL REFERENCES brands(id),
    output_key   VARCHAR(512),
    generated_at TIMESTAMPTZ DEFAULT NOW()
);
```

Também adicionar índices de performance:
```sql
CREATE INDEX idx_orders_brand_id     ON orders(brand_id);
CREATE INDEX idx_orders_catalog_id   ON orders(catalog_id);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_romaneios_brand_id  ON romaneios(brand_id);
```

---

### E2 — Módulo `orders` completo

#### `orders/models.py`

SQLAlchemy 2.0 com type hints completos:

- `Order`: todos os campos da migration acima. Relationship `items` com `selectinload` (mesma lição aprendida na Sprint 01 com `Catalog.products`). Relationship `romaneio`.
- `OrderItem`: todos os campos. Relationship back para `order`.

> **Nota:** O model `Brand` existente (em `auth/models.py`) deve receber o novo campo `logo_key: Mapped[str | None] = mapped_column(String(512))`. Alembic detecta via `--autogenerate`. Não criar novo model — apenas adicionar o campo.

#### `orders/schemas.py`

Pydantic v2:

- `OrderCreateRequest`: `catalog_id` (UUID | None), `lojista_name` (str | None), `lojista_token` (str | None)
- `OrderItemResponse`: sku, product_name, color_index, color_hex, size, quantity, unit_price, subtotal (calculado)
- `OrderResponse`: todos os campos públicos de Order + lista de `OrderItemResponse` + totals
- `OrderTotals`: total_items (n de linhas), total_pecas, valor_total, n_skus
- `ExtractOrderResponse`: order_id, job_id, status, poll_url
- `RomaneioStatusResponse`: romaneio_id, status, download_url | None, job_id | None

#### `orders/extractor.py` — migrado de `oasis_romaneio.py`

**Função pura:** recebe `bytes` do PDF preenchido, retorna `RawOrderData`. Zero I/O.

```python
@dataclass
class RawOrderItem:
    field_name: str        # campo original: qty__SKU__cor1__PP
    sku: str
    color_index: int       # 1 para v1 legado, N para v2
    size: str
    quantity: int
    source_format: Literal["v1", "v2"]

@dataclass
class RawOrderData:
    items: list[RawOrderItem]
    n_pages_scanned: int
    n_fields_found: int
    n_fields_filled: int
    has_acroform: bool     # False = PDF achatado (flatten)
    source_format: Literal["v1", "v2", "mixed"]

class OrderExtractor:
    def extract(self, pdf_bytes: bytes) -> RawOrderData:
        """
        Itera todos os widgets de todas as páginas.
        Parseia qty__SKU__corN__TAM (v2) e qty__SKU__TAM (v1 legado).
        Se has_acroform=False, levanta PDFFlattenedError.
        Ignora valores não-numéricos ou <= 0 silenciosamente.
        """
```

**Regras de parsing:**

- Padrão v2: `^qty__(?P<sku>[^_]+(?:_[^_]+)*)__cor(?P<color>\d+)__(?P<size>[^_]+)$`
- Padrão v1 legado: `^qty__(?P<sku>[^_]+(?:_[^_]+)*)__(?P<size>[^_]+)$` → color_index=1
- Campos fora desses padrões: ignorar silenciosamente (pode haver campos de metadados como `_meta_lojista_token`)
- Valor inválido (texto, float, negativo, zero): ignorar silenciosamente, incrementar contador de descartados
- PDF sem `/AcroForm`: `has_acroform=False` → levantar `PDFFlattenedError`

#### `orders/normalizer.py`

**Função pura:** recebe `RawOrderData` + lista opcional de `CatalogProduct`, retorna `OrderData` canônico.

```python
@dataclass
class OrderData:
    items: list[NormalizedOrderItem]
    totals: OrderTotals
    source_format: str
    warnings: list[str]   # SKUs não encontrados no catálogo, etc.

class OrderNormalizer:
    def normalize(
        self,
        raw: RawOrderData,
        catalog_products: list[CatalogProduct] | None = None
    ) -> OrderData:
        """
        Quando catalog_products fornecido:
          - Enriquece cada item com product_name, unit_price, color_hex (via swatch)
          - Adiciona warning para SKUs presentes no PDF mas ausentes no catálogo
        Quando catalog_products=None:
          - Normaliza sem enriquecimento (campos ficam None)
        Sempre: calcula totais, agrupa por SKU, ordena por page_index se disponível.
        """
```

#### `orders/service.py`

```python
class OrderService:
    async def create_order(
        self,
        brand_id: UUID,
        pdf_bytes: bytes,
        catalog_id: UUID | None,
        lojista_name: str | None,
        lojista_token: str | None
    ) -> tuple[Order, Job]:
        """Valida PDF, faz upload, cria registros, enfileira task."""

    async def get_order(self, order_id: UUID, brand_id: UUID) -> Order:
        """Busca com selectinload(Order.items). Levanta NotFoundError se não for da brand."""

    async def process_order(self, order_id: UUID) -> None:
        """Lógica de processamento chamada pela Celery task."""

    async def get_romaneio_status(self, order_id: UUID, brand_id: UUID) -> dict:
        """Retorna status do romaneio ou enfileira geração se não existir."""
```

#### `orders/tasks.py`

```python
@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="order.extract"
)
def extract_order_task(self, order_id: str) -> dict:
    """
    Wrapper síncrono. Atualiza Job: pending → running → success/error.
    Retry com backoff exponencial em caso de falha transitória.
    Não faz retry em PDFFlattenedError (erro permanente do arquivo).
    """
```

#### `orders/router.py`

```python
router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

@router.post("/extract", status_code=202)
async def extract_order(
    file: UploadFile,
    catalog_id: UUID | None = Form(None),
    lojista_name: str | None = Form(None),
    lojista_token: str | None = Form(None),
    brand: Brand = Depends(get_current_brand),
    service: OrderService = Depends()
) -> StandardResponse[ExtractOrderResponse]: ...

@router.get("/{order_id}")
async def get_order(
    order_id: UUID,
    brand: Brand = Depends(get_current_brand),
    service: OrderService = Depends()
) -> StandardResponse[OrderResponse]: ...

@router.get("/{order_id}/romaneio")
async def get_romaneio(
    order_id: UUID,
    brand: Brand = Depends(get_current_brand),
    service: OrderService = Depends(),
    romaneio_service: RomaneioService = Depends()
) -> Response:
    """
    Se romaneio já gerado: 302 redirect para presigned URL do PDF.
    Se em geração: 202 com job_id.
    Se não iniciado: enfileira geração e retorna 202 com job_id.
    """
```

#### `orders/tests/`

**`test_extractor.py`** — testa com PDFs de fixture:

| Fixture | Cenário |
|---------|---------|
| `pedido_preenchido_v2.pdf` | Happy path formato v2 — n_fields e valores corretos |
| `pedido_preenchido_v1.pdf` | Formato legado v1 — color_index=1 implícito |
| `pedido_campos_vazios.pdf` | PDF com campos mas todos vazios — items=[] |
| `pedido_valores_invalidos.pdf` | Campos com texto e float — todos ignorados |
| `pedido_flattened.pdf` | PDF achatado — levanta PDFFlattenedError |
| `pedido_mixed_v1_v2.pdf` | Mix de formatos na mesma página — source_format="mixed" |

**`test_normalizer.py`**:
- Normalizar sem catálogo → items com product_name=None, sem warnings
- Normalizar com catálogo → items enriquecidos com nome, preço, cor hex
- SKU no PDF ausente no catálogo → warning adicionado, item preservado
- Totais calculados corretamente (total_pecas, valor_total)
- Agrupamento por SKU correto (mesmo SKU, cores diferentes = itens separados)

**`test_service.py`**:
- Criar pedido enfileira job (mock Celery)
- Pedido de outra brand retorna NotFoundError
- `process_order` com PDF válido → status extracted, items persistidos
- `process_order` com PDF flattened → status error, error_message="PDF_FLATTENED"
- `get_romaneio_status` sem romaneio existente → enfileira geração
- `get_romaneio_status` com romaneio pronto → retorna download_url

**`test_router.py`** (integration via `httpx.AsyncClient`):
- Upload sem auth → 401
- Upload de arquivo não-PDF → 400 `INVALID_FILE_TYPE`
- Upload de PDF > 50MB → 400 `FILE_TOO_LARGE`
- Upload de PDF flattened → job criado, status vai para error com código `PDF_FLATTENED`
- Upload válido → 202 com order_id + job_id
- GET pedido de outra brand → 404
- GET /romaneio quando não pronto → 202 com job_id
- GET /romaneio quando pronto → 302 redirect

---

### E3 — Módulo `romaneio` completo

#### `romaneio/models.py`

SQLAlchemy 2.0:
- `Romaneio`: todos os campos da migration. Relationship para `Order`.

#### `romaneio/builder.py` — migrado de `oasis_romaneio.py`

**Função pura:** recebe `OrderData` + `Brand`, retorna `bytes` do PDF romaneio. Zero I/O.

```python
@dataclass
class RomaneioConfig:
    brand_name: str
    brand_logo_bytes: bytes | None   # PNG/JPG da logo, opcional
    title: str = "ROMANEIO DE PEDIDO"
    show_prices: bool = True
    currency_symbol: str = "R$"
    locale: str = "pt_BR"

class RomaneioBuilder:
    def build(self, order_data: OrderData, config: RomaneioConfig) -> bytes:
        """
        Gera PDF romaneio profissional com PyMuPDF.
        Layout:
          - Cabeçalho: logo (se houver) + título + lojista + data + n_pedido
          - Por SKU: bloco com nome do produto, ref, preço unitário
                     grid cor×tamanho com quantidades
                     subtotal do SKU (peças + valor)
          - Paginação automática — cabeçalho repetido em cada página
          - Rodapé final: total de peças, total de SKUs, valor total
        Retorna bytes do PDF gerado. Nunca abre arquivo do disco.
        """
```

**Detalhes de layout (baseado no `oasis_romaneio.py`):**
- Página A4 (595 × 842 pt), margens de 40pt
- Fonte: Helvetica (disponível no PyMuPDF sem instalação)
- Cabeçalho: altura fixa de 80pt, linha divisória
- Por produto: calcular altura necessária antes de inserir — se não couber na página atual, iniciar nova página com cabeçalho repetido
- Grid de tamanhos: células de largura fixa, centralizado na coluna de cada tamanho
- Cores representadas por: índice numérico (cor1, cor2) + hex do swatch em fonte pequena abaixo
- Valores monetários: `locale.currency()` com `pt_BR` — formato `R$ 1.598,00`
- Datas: `datetime.strftime("%d/%m/%Y")` — formato brasileiro

#### `romaneio/service.py`

```python
class RomaneioService:
    async def generate_romaneio(self, order_id: UUID, brand_id: UUID) -> tuple[Romaneio, Job]:
        """Carrega order completo, enfileira task de geração."""

    async def process_romaneio(self, romaneio_id: UUID) -> None:
        """Lógica de geração chamada pela Celery task."""

    async def get_download_url(self, order_id: UUID, brand_id: UUID) -> str:
        """Retorna presigned URL. Levanta NotReadyError se romaneio não existir."""
```

#### `romaneio/tasks.py`

```python
@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="romaneio.generate"
)
def generate_romaneio_task(self, romaneio_id: str) -> dict:
    """
    Carrega OrderData do banco, chama RomaneioBuilder.build(),
    faz upload do PDF para o storage, atualiza Romaneio.output_key.
    """
```

#### `romaneio/tests/`

**`test_builder.py`**:
- PDF gerado tem tamanho > 0
- PDF gerado tem número correto de páginas (1 para pedido pequeno, N para pedido grande)
- PDF contém texto do SKU, nome do produto, lojista
- Pedido com produto sem preço → coluna de valor omitida ou exibida como "—"
- Pedido com muitos SKUs → paginação correta (cabeçalho repetido na página 2)
- Pedido com logo → logo presente no cabeçalho (verificar via PyMuPDF `page.get_images()`)
- Pedido sem logo → cabeçalho apenas textual, sem erro

**`test_service.py`**:
- `generate_romaneio` enfileira task
- `process_romaneio` gera PDF e persiste output_key
- `get_download_url` retorna URL quando pronto
- `get_download_url` levanta NotReadyError quando romaneio não existe

---

### E4 — Fixtures de PDF para testes de orders

Script `tests/fixtures/generate_order_fixtures.py` que gera programaticamente:

```python
# pedido_preenchido_v2.pdf
# PDF com campos AcroForm preenchidos no padrão v2
# Baseado nas fixtures de catálogo da Sprint 01 — usar catalog fixture + field injector + preencher campos

# pedido_preenchido_v1.pdf
# Campos no formato legado: qty__SKU__TAM (sem color_index)

# pedido_campos_vazios.pdf
# PDF com AcroForm mas todos os campos em branco

# pedido_valores_invalidos.pdf
# Campos com valores "abc", "3.5", "-1", "0"

# pedido_flattened.pdf
# PDF sem /AcroForm — gerado com page.insert_text() apenas, sem add_widget()

# pedido_mixed_v1_v2.pdf
# Mix: metade dos campos em v1, metade em v2
```

**Regra:** gerar a partir das fixtures de catálogo existentes quando possível (usar `FieldInjector` + preencher os widgets programaticamente). Garante que as fixtures de orders sejam consistentes com as de catalog.

---

### E5 — Registrar routers no `main.py`

Na Sprint 01, `orders/router.py` e `romaneio/` foram criados como esqueleto mas **não montados no app**. Esta sprint os ativa:

```python
# main.py — adicionar ao create_app()
from catalogflow.modules.orders.router import router as orders_router
from catalogflow.modules.romaneio.router import router as romaneio_router  # se existir endpoint próprio

app.include_router(orders_router)
```

Também atualizar `GET /api/v1/jobs/{job_id}` no `shared/jobs_router.py` para reconhecer os novos `job_type`: `order.extract` e `romaneio.generate` (provavelmente já funciona pelo design genérico, mas verificar).

---

### E6 — Atualizar `GET /api/v1/health`

Adicionar ao health check a contagem de jobs pendentes por tipo — útil para monitoramento:

```json
{
  "status": "ok",
  "db": "ok",
  "redis": "ok",
  "jobs": {
    "catalog_pending": 0,
    "order_pending": 0,
    "romaneio_pending": 0
  }
}
```

---

### E7 — Testes de integração (pipeline completo)

`tests/integration/test_order_pipeline.py`:

```python
async def test_full_order_pipeline(db_session, s3_mock, brand, api_key):
    """
    1. Criar catálogo de referência com fixture PDF
    2. Processar catálogo (task síncrona no teste)
    3. Gerar PDF preenchido programaticamente
    4. Upload via POST /api/v1/orders/extract com catalog_id
    5. Processar pedido (task síncrona)
    6. Verificar Order.status == "extracted"
    7. Verificar OrderItems persistidos e enriquecidos (product_name, unit_price)
    8. Trigger geração romaneio
    9. Verificar Romaneio.output_key preenchido
    10. Download romaneio — verificar bytes > 0 e é PDF válido
    """
```

---

### E8 — Atualizar CHANGELOG e README

`CHANGELOG.md` — adicionar entry:
```
## [0.2.0] — Sprint 02

### Added
- Order extraction pipeline (PDF preenchido → OrderData estruturado)
- Support for AcroForm field formats v1 (legacy) and v2
- PDF flatten detection with structured error response
- Romaneio PDF generation with automatic pagination and brand logo
- Brand logo upload via POST /internal/brands/{id}/logo (S3 storage)
- Endpoints: POST /orders/extract, GET /orders/{id}, GET /orders/{id}/romaneio
- Alembic migration 003: orders, order_items, romaneios tables + brands.logo_key
```

`README.md` — adicionar seção "Fluxo completo" com `curl` exemplos do ciclo upload catálogo → download PDF editável → upload preenchido → download romaneio.

---

### E9 — Endpoint de upload de logo da marca *(opcional — implementar se houver tempo)*

> Este entregável é opcional para a Sprint 02. O `RomaneioBuilder` já suporta `logo_bytes=None` e funciona sem logo. O endpoint pode entrar numa sprint de polimento posterior sem impacto no ciclo de pedido.

Adicionar em `auth/router.py` (junto com os outros endpoints internos):

```python
@router.post("/internal/brands/{brand_id}/logo", status_code=200)
async def upload_brand_logo(
    brand_id: UUID,
    file: UploadFile,
    _: str = Depends(require_internal_secret),
    db: AsyncSession = Depends(get_db),
    storage: StorageClient = Depends(get_storage),
) -> StandardResponse[dict]:
    """
    Recebe PNG ou JPG da logo da marca.
    Valida MIME server-side (image/png ou image/jpeg).
    Limita tamanho a 2MB.
    Faz upload para storage com chave: {brand_id}/logo.{ext}
    Atualiza Brand.logo_key no banco.
    Retorna: {"logo_key": "uuid/logo.png"}
    """
```

**Testes (`auth/tests/test_router.py`):**
- Upload de PNG válido → 200, `brand.logo_key` atualizado no banco
- Upload de arquivo não-imagem → 400 `INVALID_FILE_TYPE`
- Upload > 2MB → 400 `FILE_TOO_LARGE`
- Sem `INTERNAL_SECRET` → 401

---

| ID | Critério | Verificação |
|----|---------|------------|
| AC-01 | `POST /orders/extract` com PDF v2 válido retorna 202 + order_id + job_id | Automated |
| AC-02 | Polling `GET /jobs/{id}` atinge `success` em < 30s para PDF preenchido padrão | Automated |
| AC-03 | `GET /orders/{id}` retorna itens com sku, color_index, size, quantity corretos | Automated |
| AC-04 | PDF em formato legado v1 (`qty__SKU__TAM`) processado corretamente (color_index=1) | Automated |
| AC-05 | PDF achatado retorna 400 com código `PDF_FLATTENED` após processamento | Automated |
| AC-06 | Quando `catalog_id` fornecido, itens são enriquecidos com product_name e unit_price | Automated |
| AC-07 | `GET /orders/{id}/romaneio` com pedido pronto retorna 302 redirect para PDF | Automated |
| AC-08 | Romaneio gerado é PDF válido e abrível (verificado com PyMuPDF) | Automated |
| AC-09 | Romaneio contém todos os SKUs do pedido com quantidades corretas | Automated |
| AC-10 | Pedido de brand A não acessível com API key de brand B | Automated |
| AC-11 | `pytest` passa com cobertura ≥ 80% (módulos orders + romaneio incluídos) | CI |
| AC-12 | Smoke test com PDF preenchido real da Oasis → romaneio gerado em < 30s | Manual |
| AC-13 | Romaneio gerado visualmente comparável ao `example/romaneio_demo.pdf` | Manual |
| AC-14 *(opcional)* | Upload de logo PNG via `POST /internal/brands/{id}/logo` → `brand.logo_key` atualizado | Automated |
| AC-15 *(opcional)* | Romaneio gerado com logo quando `brand.logo_key` preenchido | Automated |

---

## Definition of Done (DoD)

Uma tarefa está **pronta** quando:

- [ ] Código implementado e commitado em branch `feature/sprint-02-<nome>`
- [ ] Testes unitários escritos e passando (não "vou escrever depois")
- [ ] Mypy sem erros no arquivo modificado
- [ ] Ruff sem warnings no arquivo modificado
- [ ] PR criado com description descrevendo o que muda e por quê
- [ ] CI verde no PR (quality + test + build)
- [ ] `spec.md` consultado — nenhuma decisão que contradiz os ADRs

A sprint está **concluída** quando:

- [ ] Todos os entregáveis E1–E8 completos
- [ ] Todos os ACs passando
- [ ] Smoke test com PDF preenchido real da Oasis concluído
- [ ] `pytest tests/ --cov=src --cov-fail-under=80` verde em máquina limpa
- [ ] CHANGELOG.md atualizado
- [ ] README.md atualizado com fluxo completo

---

## Out of Scope (esta sprint)

- ❌ Webhook de notificação (Sprint 03)
- ❌ Integração com estoque / ERP (Sprint 03)
- ❌ Reserva automática de estoque (Sprint 04)
- ❌ Web UI / Frontend (Sprint 03)
- ❌ Módulo `User` com login/senha (Sprint 03)
- ❌ QR Code por produto no PDF editável (postergado)
- ❌ Pipeline de visão computacional para PDF achatado (Fase 2 do produto)
- ❌ Deploy em produção (Sprint 04)

---

## Ordem de Implementação Recomendada

```
1.  generate_order_fixtures.py — criar fixtures antes de qualquer teste
2.  migrations/versions/003_orders_schema.py — banco antes dos models
3.  auth/models.py — adicionar Brand.logo_key
4.  orders/models.py + romaneio/models.py
5.  orders/schemas.py
6.  orders/extractor.py + tests/test_extractor.py
7.  orders/normalizer.py + tests/test_normalizer.py
8.  romaneio/builder.py + romaneio/tests/test_builder.py
9.  orders/service.py + tests/test_service.py
10. orders/tasks.py
11. romaneio/service.py + romaneio/tasks.py + tests/test_service.py
12. orders/router.py + tests/test_router.py
13. Registrar routers em main.py
14. tests/integration/test_order_pipeline.py
15. Atualizar health check (E6)
16. CHANGELOG.md + README.md
17. *(opcional)* auth/router.py — endpoint POST /internal/brands/{id}/logo (E9)
```

**Regra:** nenhum passo depende de um step posterior. O sistema deve compilar e os testes existentes da Sprint 01 devem continuar passando após cada passo.

---

## Armadilhas conhecidas (lições da Sprint 01)

**1. `selectinload` em relacionamentos async.**
O mesmo bug de `MissingGreenlet` que aconteceu em `Catalog.products` vai acontecer em `Order.items` se não usar `selectinload(Order.items)` no `get_order`. Adicionar desde o primeiro commit.

**2. Fixtures de PDF para orders.**
Gerar a partir das fixtures de catálogo existentes usando `FieldInjector` + preenchimento programático dos campos. Garante consistência e evita criar PDFs "manualmente" que podem divergir da lógica real.

**3. Task Celery não faz retry em erros permanentes.**
`PDFFlattenedError` é erro permanente do arquivo — retry não vai resolver. Capturar essa exceção na task e fazer `raise self.retry(max_retries=0)` ou simplesmente não chamar `raise self.retry()`. Apenas erros transitórios (falha de rede, banco indisponível) devem ter retry.

**4. Locale pt_BR para valores monetários.**
`locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')` pode falhar em containers sem o locale instalado. Usar `babel` (já deve estar como dep transitiva) ou formatar manualmente: `f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")`.

**5. `catalog/tasks.py` ainda está em 0% de cobertura.**
Aproveitar a Sprint 02 para criar um padrão de teste de tasks que cubra tanto `order.extract` quanto `catalog.process`. Usar `task.apply()` (execução síncrona) nos testes de integração.

**6. Logo da marca no romaneio.**
A logo é buscada do storage S3 via `brand.logo_key` — nunca de arquivo local. O `RomaneioService.process_romaneio()` deve fazer:
```python
logo_bytes: bytes | None = None
if order.brand.logo_key:
    logo_bytes = await storage.download(order.brand.logo_key)
pdf_bytes = RomaneioBuilder().build(order_data, RomaneioConfig(
    brand_name=order.brand.name,
    logo_bytes=logo_bytes,
))
```
O builder funciona com `logo_bytes=None` sem erro — exibe apenas o nome textual da marca. Para fazer upload da logo, usar o endpoint interno `POST /internal/brands/{id}/logo` (E9).

---

## Referências

| Documento | Localização | Uso |
|-----------|------------|-----|
| Spec técnico | `spec.md` | Fonte de verdade — modelos, SQL, API contract, pipelines |
| Script de referência | `oasis_romaneio.py` | Lógica de extração e geração a migrar |
| Romaneio exemplo | `example/romaneio_demo.pdf` | Referência visual do output esperado |
| PDF preenchido exemplo | `example/OASIS_MOTION_v2_editavel.pdf` | Fixture manual para smoke test |
| PRD Sprint 01 | `docs/sprint_01/PRD_sprint_01.md` | Padrões estabelecidos — seguir mesmas convenções |
