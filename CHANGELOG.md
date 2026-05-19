# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) e
versionamento [SemVer](https://semver.org/spec/v2.0.0.html).

---

## [0.6.0] — 2026-05-18 — Sprint 06: CI verde + lint na fonte + pre-commit

Encerra a dívida técnica de qualidade acumulada nas sprints anteriores: o CI
passa a exigir verde de primeira (sem `|| true`, sem overrides amplos de
`ruff`/`mypy`) e o `pre-commit` se torna portão obrigatório no setup local.

### Added

- `.pre-commit-config.yaml` com hooks de `ruff check`, `ruff format` e
  `mypy` — versões pinadas para corresponder ao ambiente local e evitar
  drift de stubs.
- `pre-commit install` documentado como passo obrigatório de onboarding em
  `CLAUDE.md` e `README.md`.
- 132 testes novos elevando a cobertura agregada de 65 % para 89 %
  (`web/` + `shared/` + caminhos de erro pouco cobertos).

### Fixed

- Resolução de **todos** os 42 erros pendentes de `ruff` na fonte (sem
  supressão `# noqa`).
- Resolução de **todos** os erros de `mypy --strict`; `ignore_errors = true`
  removido dos 23 módulos onde estava ativo.
- 4 testes que estavam falhando há sprints (incluindo o uso de `hashlib.md5`
  sem `usedforsecurity=False` apontado pelo Bandit B324).
- `chore(ci): extend mypy override for SQLAlchemy Result.rowcount drift` —
  drift de stubs sob SQLAlchemy 2.0.36.
- `fix(main): validation handler 500 in CI` — `starlette.status.HTTP_422_UNPROCESSABLE_ENTITY` foi deprecada e o `filterwarnings = ["error", …]` promovia a deprecation a exceção dentro do handler, gerando 500 silencioso. Trocado pelo literal `422`.
- `fix(ci): upgrade httpx to 0.28+` — `ASGITransport` mudou de assinatura
  entre 0.27 e 0.28; testes E2E quebravam ao instanciar `AsyncClient` com
  `transport=ASGITransport(app)`.

### Decisões registradas

- ADR-008: política definitiva de `mypy` — `ignore_missing_imports` apenas
  para libs externas; supressão cirúrgica por arquivo ou linha, nunca por
  módulo no `pyproject.toml`.
- ADR-009: `pre-commit` como portão local obrigatório. Sem ele, o CI falha
  no primeiro push — perda garantida de minutos de Actions e poluição do
  histórico com commits "fix CI".
- CI a partir desta sprint **não aceita admin override** para mergear em
  `main` com check vermelho.

---

## [0.5.0] — 2026-05-15 — Sprint 05: PDFAnalyzer robusto (Voronoi + SKU 9 dígitos)

Corrige dois bugs do `PDFAnalyzer` descobertos em catálogos reais da Oasis
após o piloto em produção. Sprint cirúrgica — não toca em outras partes do
sistema.

### Fixed

- **SKU de 9 dígitos** — o catálogo MOTION contém SKUs como `442500908-0`
  (9 dígitos antes do hífen) que o regex original `\b(\d{10,13}-\d)\b`
  rejeitava silenciosamente, deixando produtos fora do processamento. Novo
  padrão `\b(\d{9,13}-\d)\b` aceita 9–13 dígitos. Fixture
  `catalogo_sku_9_digitos.pdf` adicionada para regressão.
- **Vazamento de nome entre produtos em página multi-produto** — quando
  uma página tinha 2+ produtos em layout assimétrico, o nome de um produto
  era atribuído ao SKU vizinho porque o código usava `page_w / 2` como
  fronteira hardcoded. Substituído por zonas de Voronoi horizontal calculadas
  dinamicamente a partir das coordenadas X dos SKUs detectados. Fixture
  `catalogo_dois_produtos_nomes_distintos.pdf` adicionada.

### Decisões registradas

- ADR-007: zonas de Voronoi horizontal como mecanismo padrão de delimitação
  de "zona de busca por SKU". Nenhuma posição é hardcoded; o algoritmo
  funciona para 1, 2, 3 ou N produtos por página. `_assign_name_zones()`
  é testável em isolamento, sem PDF real.

---

## [0.4.0] — 2026-05-14 — Sprint 04: Integração ERP (estoque + envio)

Adiciona dois fluxos de integração com ERP ao CatalogFlow:

1. **Consulta de estoque** — ao receber um pedido extraído, o operador
   dispara uma consulta que pergunta ao ERP a disponibilidade real por
   `(sku, cor, tamanho)`. O resultado popula `order_items.stock_status`
   / `available_qty` e renderiza, no detalhe do pedido, uma sub-linha
   "Disponível" abaixo de cada cor com células coloridas conforme a
   regra contábil (verde sábio = ok, âmbar = parcial, vermelho escuro
   = zerado, muted = não pedido).
2. **Envio do pedido ao ERP** — após a consulta de estoque, o operador
   pode enviar o pedido ao ERP informando o `customer_code` (código da
   lojista no Consistem). O envio é assíncrono, com idempotência por
   `order_id` (UNIQUE) e estados terminais (`accepted`,
   `partially_accepted`, `rejected`).

Arquitetura: **Adapter Pattern**. `StockAdapter` é a interface única;
implementações concretas (`MockStockAdapter`, `ConsistemAdapter`) são
intercambiáveis via variável de ambiente `ERP_ADAPTER` em runtime, sem
rebuild da imagem.

Além disso: fotos dos produtos passam a aparecer nos PDFs de romaneio e
relatório de pendências (reusando o scraping do AMC QRCode já usado nas
thumbnails da UI web). Novo botão "Regenerar romaneio" no detalhe do
pedido para reprocessar com dados novos sem mexer no banco.

### Added — Schema & migrations (Fase A)

- Migration `0005_erp_integration.py` reversível criando `stock_checks`
  (registro de cada consulta de disponibilidade, JSONB `result` com
  snapshot completo para auditoria histórica) e `erp_submissions`
  (UNIQUE em `order_id` — um envio ativo por pedido). Índices
  `idx_stock_checks_order` e `idx_erp_submissions_order` cobrem o
  acesso por pedido. CHECK constraints alinhados aos status válidos.
- `modules/stock/models.py` com `StockCheck` e `ErpSubmission` em
  SQLAlchemy 2.0, FKs `ON DELETE CASCADE` para `orders` e `brands`.
- `infra/settings.py` ganha bloco ERP_*: `erp_adapter` (`mock` |
  `consistem`), `erp_base_url`, `erp_api_key` (SecretStr), `erp_empresa`
  (default `"50"` = AMC Têxtil), `erp_cod_natureza` (default `505` =
  estoque nacional), `erp_timeout`.
- `migrations/env.py` importa `modules.stock.models` para o autogenerate
  enxergar as novas tabelas.

Nota: `order_items.stock_status` / `available_qty` já existiam desde a
migration 0003 (Sprint 02 preparou) — esta migration não as toca.

### Added — Adapter Pattern (Fase B)

- `modules/stock/adapter.py` — ABC `StockAdapter` com `check_availability`
  e `submit_order` async. Dataclasses `StockQuery` e `StockResult` frozen.
  Contrato: falhas por item viram `status="unknown"` (não derrubam o
  batch); `available_qty=None` apenas quando status é `unknown`.
- `modules/stock/mock_adapter.py` — `MockStockAdapter` determinístico
  via hash MD5 do `(sku, size, color_index)`. Distribuição 70/20/10
  (available / partial / out_of_stock). `submit_order` aceita sempre e
  devolve `MOCK-<8 hex>`. Delay simulado de 0.5s para a UI exercitar
  spinners. Útil para demo, dev e CI sem dependência de rede.
- `modules/stock/consistem_adapter.py` — Adapter HTTP real para o ERP
  Consistem da AMC Têxtil. `check_availability`:
  `GET /saldoEstoqueAtual/{codItem}/{codNatureza}` com header
  `empresa`, fórmula contábil `disponivel = estoqueAtual -
  estReservPedido - estReservProducao - estReservLotes`, paralelismo
  via `asyncio.gather` + `Semaphore(5)`, timeout 3s por request. Erros
  transitórios (5xx, timeout, payload corrupto) viram `unknown`.
  `_build_cod_item(sku, size, color_index)` isolado como única função
  a alterar quando a Oasis fornecer o mapeamento real — formato
  provisório `"{sku}.{size}.{color_index}"`. `submit_order` levanta
  `NotImplementedError` com mensagem explícita (endpoint do Consistem
  ainda não definido).
- 26 testes (10 mock + 16 consistem com `respx` mockando httpx)
  cobrem determinismo, fórmula, classificação, timeouts, 5xx, payload
  inválido, falha parcial não-derruba-batch.

### Added — Service + tasks + API (Fase C)

- `modules/stock/service.StockService`:
  - `get_adapter()` lê `settings.erp_adapter` em **runtime** — trocar
    `ERP_ADAPTER=mock` ↔ `ERP_ADAPTER=consistem` é só reiniciar o
    container, sem rebuild.
  - `enqueue_stock_check(order_id, brand_id)` cria StockCheck(pending)
    + Job + enfileira `stock.check`. Multi-tenant: pedido de outra
    brand → `NotFoundError` (`ORDER_NOT_FOUND`).
  - `check_order_stock(...)` pipeline executado pela task: claim job
    (race-safe via `UPDATE WHERE status='pending'`) → carrega
    OrderItems → consulta adapter → atualiza `order_items` →
    persiste snapshot completo no `stock_check.result` JSONB →
    marca completed.
  - `enqueue_submission(order_id, brand_id, customer_code)` — UNIQUE
    `order_id` impede dois envios ativos; pedidos em estado terminal
    (`accepted` / `partially_accepted` / `rejected`) levantam
    `ConflictError` (409 `ORDER_ALREADY_SUBMITTED`); estados não-terminais
    são reutilizados (retry manual).
  - `submit_order_to_erp(...)` chama o adapter e mapeia o resultado
    para `accepted` / `partially_accepted` / `rejected`.
  - `get_stock_check` retorna a consulta mais recente; `get_submission`
    retorna a (única) submissão.
  - `summarize_stock_check(stock_check)` — função pura que agrega
    contadores por status, reusada pelo router web e pelo JSON.
- `modules/stock/tasks.py` — `check_stock_task` (`stock.check`) e
  `submit_order_task` (`stock.submit`) com retry exponencial
  `60s × 2^n` (max 3). `NotImplementedError` (do Consistem submit) é
  tratado como **permanente** e não retry — o estado do job vai para
  `error` direto.
- `modules/stock/router.py` — 4 endpoints sob `/api/v1/orders/{id}/`:
  - `POST /stock-check` (202) — dispara consulta.
  - `GET /stock-check` (200) — summary + items com per-status.
  - `POST /submit` (202, body `{customer_code}`) — dispara envio.
  - `GET /submission` (200) — status + erp_reference.
- `main.py` registra `stock_router`; `infra/celery_app.py` adiciona
  routing `stock.* → queue "stock"` + autodiscover. `docker-compose`
  worker consome a fila `stock` junto com as demais.
- 31 testes (15 service + 16 router) cobrem happy path, isolamento
  multi-tenant, `ConflictError`, `NotImplementedError` do Consistem,
  validação de `customer_code`, 401/404/409/422/202/200.

### Added — Web UI + relatório de pendências (Fase D)

- `templates/orders/detail.html` **reestruturado**: cada `(sku, cor)`
  agora ocupa duas linhas na tabela.
  - **Linha 1 (pedido)**: foto, produto, cor, qtds por tamanho, total.
  - **Linha 2 (disponível)**: label "Disponível" em muted, qtds por
    tamanho coloridas (verde / âmbar / vermelho / muted-traço), total
    da cor. Só renderiza quando há consulta concluída.
  - **Mobile**: cards ganham mini-tabela de 3 linhas (tamanhos /
    pedido / disponível) com a mesma regra de cores.
  - Macros `avail_class` e `avail_text` centralizam a regra: `requested
    == 0 → "-"` (muted); `requested > 0` + `available is None → "0"`
    (out); valor numérico → cor segundo `available >= requested`.
- `templates/orders/_stock_action.html` (novo) — fragmento HTMX com 4
  estados: `absent` (botão "Consultar estoque"), `checking` (spinner +
  polling 2s), `completed` (summary "N disponíveis / N parciais / N
  zerados" + botão "Reconsultar"), `error` (mensagem + retry).
  Resposta inclui header `HX-Trigger: stock-check-completed` ao
  finalizar — handler client-side faz `location.reload()` pra exibir
  a sub-linha "Disponível" nos itens (renderizada server-side).
- `templates/orders/_submit_action.html` (novo) — fragmento com 5
  estados: `absent` (form `customer_code` + botão), `submitting`
  (spinner + polling), `accepted` (✓ + `erp_reference`),
  `partially_accepted` (△ + ref), `rejected` (✕ + form de reenvio),
  `error` (form com mensagem).
- **Bloco "Pendências"** no detalhe — quando há itens com
  `stock_status` em `partial` ou `out_of_stock`, aparece "N itens
  com pendência de estoque" + botão **"↓ Gerar relatório de
  pendências"** que abre o PDF em nova aba.
- `web/data.py`:
  - `OrderDetail` carrega `stock_check` (mais recente) e `submission`
    eager.
  - `build_stock_map(stock_check) -> dict[(sku, color, size),
    int | None]` constrói o lookup que o template consulta por célula.
  - `count_pendency_items(...)` agrega o contador para o bloco de
    pendências.
- `web/router.py` — 4 rotas web (`POST /stock-check-web`, `GET
  /stock-check-poll`, `POST /submit-web`, `GET /submit-poll`) que
  proxam para a API REST via httpx ASGI. Nova rota
  **`GET /orders/{id}/pendency-report`** gera o PDF on-the-fly
  (sem persistir no storage) usando `RomaneioBuilder` no modo
  pendência.
- `modules/romaneio/builder.py` ganha:
  - Kwarg `available_map` em `build()` — quando set, cada cor recebe
    uma sub-linha "Disponível" com qtds por tamanho coloridas.
  - `RomaneioConfig.footer_note` — frase em itálico no rodapé
    (relatório usa "Itens acima não puderam ser atendidos
    integralmente.").
  - Backward compat preservada: sem `available_map`, layout idêntico
    ao original.

### Added — Fotos de produtos nos PDFs

- `shared/image_fetcher.py` (novo) centraliza scraping do AMC QRCode
  (movido de `web/product_image.py`). Três helpers:
  - `fetch_product_image_url(sku, *, timeout=3.0)` — URL via scraping.
  - `fetch_product_image_bytes(sku, *, timeout=3.0)` — URL + GET dos
    bytes.
  - `fetch_product_images(skus, *, max_concurrent=5, timeout=3.0)` —
    batch async com `asyncio.Semaphore`. SKUs sem foto são omitidos
    do dict (sem entrada com None). Dedup automático.
- `web/product_image.py` vira shim re-exportando do shared (preserva
  imports e monkeypatch dos testes existentes).
- `modules/romaneio/builder.py`:
  - Kwarg `product_images: dict[str, bytes]` em `build()`.
  - `_draw_product_image()` insere thumbnail 50×50pt à esquerda do
    cabeçalho do bloco via `page.insert_image(stream=bytes,
    keep_proportion=True)`. Texto desloca para `x=MARGIN_X+60`.
  - Bytes inválidos / formato não suportado: pymupdf levanta,
    capturamos e o PDF sai sem foto (best-effort).
- `modules/romaneio/service.py`: `RomaneioService.__init__` aceita
  `image_fetcher: ImageFetcher | None`. Default `None` — PDF sai sem
  fotos (mantém testes existentes verdes sem mockar rede). Produção
  injeta `fetch_product_images` via `tasks.py`.
- `web/router.py /pendency-report` busca fotos dos SKUs pendentes
  antes de chamar o builder (mesmo padrão best-effort).
- Smoke test no container: backward compat (1587 bytes), com 1 foto
  (4355 bytes), bytes inválidos (1587 bytes — sem crash), pendency +
  foto (4488 bytes).

### Added — Regenerar romaneio

- `POST /orders/{id}/regenerate-romaneio` (web) — deleta o `Romaneio`
  existente do storage (`output_key`) e do banco, então re-dispara o
  fluxo de geração. Retorna o fragmento HTMX no estado `processing`,
  com polling existente assumindo o restante. UX: spinner aparece
  imediatamente, e quando o novo PDF fica pronto (`auto_download`),
  ele abre na mesma aba — agora com as fotos embedadas.
- Link discreto "Regenerar romaneio" no fragment
  `_romaneio_action.html` (estado `ready`), com `hx-confirm` para
  evitar regeneração acidental. CSS `.romaneio-regen-wrap` usa
  `flex-basis: 100%` pra forçar quebra de linha dentro de
  `.detail-actions` (flex-row wrap).

### Fixed

- `fix(web): show per-size stock row in order detail and fix ? in
  pendency report` — dois bugs encontrados na revisão PMO:
  1. **Linha "Disponível" não renderizava** — bug clássico de escopo
     Jinja: `{% set color_has_stock = true %}` dentro de `{% for %}`
     é local ao loop e não vaza para o escopo externo. Substituído
     pelo padrão `{% set ns = namespace(has_stock=false) %}` + `{%
     set ns.has_stock = true %}` no loop + `{% if ns.has_stock %}`
     depois. Mesmo fix em desktop e mobile.
  2. **PDF de pendências exibia "?" em células** — duas causas:
     (a) Lógica errada: tratava `requested == 0` + `available is
     None` como "?". Regra correta (PMO): `requested == 0 → "-"`
     sempre; `requested > 0` + `available is None → "0"` vermelho.
     (b) Font fallback — `helv` (Helvetica core PDF) não contém
     em-dash (U+2014); pymupdf substituía por "?". Trocado por
     hyphen ASCII, padrão do resto do builder.
  3. **Footer note não aparecia** — `insert_textbox` com altura 14pt
     vs fontsize 9 era rejeitada silenciosamente. Substituído por
     `insert_text` com baseline calculado.

### Tests

- 57 testes novos na Sprint 04:
  - 10 `stock/tests/test_mock_adapter.py` (determinismo, distribuição,
    submit MOCK-*, idempotência por item).
  - 16 `stock/tests/test_consistem_adapter.py` (com `respx`):
    `_build_cod_item`, fórmula contábil, classificação, timeout,
    HTTP 500, payload inválido, falha parcial, header `empresa`.
  - 15 `stock/tests/test_service.py`: `get_adapter` por settings,
    enqueue + execute, rejected/partially_accepted, `ConflictError`
    em já-aceito, `NotImplementedError` do Consistem, isolamento
    multi-tenant, summarize util.
  - 16 `stock/tests/test_router.py`: 4 endpoints com auth, 401/404/
    409/422/202/200, cross-tenant, customer_code validation.
- Existing tests intocados — `RomaneioService(db)` sem `image_fetcher`
  continua passando (PDF sem fotos, sem chamadas de rede em CI).

### Decisões registradas

- **`get_adapter()` lê settings em runtime** — não no construtor —
  permite trocar `ERP_ADAPTER` sem rebuild da imagem. Override via
  `adapter=` no `__init__` ganha precedência (testes).
- **JSONB livre em `stock_check.result`** — snapshot completo do que
  o adapter retornou no momento da consulta, enriquecido com
  `product_name` / `color_hex` dos `order_items`. Auditoria histórica
  preservada mesmo se o pedido for alterado depois.
- **UNIQUE em `erp_submissions.order_id`** — força reutilização da
  mesma linha em retries; estados terminais (`accepted` / `partially_
  accepted` / `rejected`) bloqueiam novo envio (409 em vez de duplicar
  pedido no ERP).
- **`NotImplementedError` é permanente** — task não dispara retry;
  retry só atrasaria o diagnóstico enquanto o contrato do Consistem
  submit não chega.
- **`image_fetcher` injetável (opcional)** — preserva contrato dos
  testes existentes sem alterá-los. Produção plumba o real via
  `tasks.py`; testes recebem `None` e geram PDFs offline.
- **Bytes de imagem inválidos são best-effort** — pymupdf levanta com
  format desconhecido; capturamos e seguimos sem a foto. Romaneio
  não pode falhar por causa de uma foto.

### Next (Sprint 05 — preview)

- Implementar `ConsistemAdapter.submit_order` quando a Oasis definir
  o endpoint de criação de pedido.
- Reserva de estoque com TTL (Fase 3 do roadmap).
- Webhook do ERP para o CatalogFlow (eventos de status do pedido).
- Sincronização periódica de estoque via Celery Beat.

---

## [0.3.0] — Sprint 03: Web UI para gerente comercial

Fecha o ciclo da gerente comercial via navegador. Toda a Sprint 02 já
funcionava por API; agora a Oasis Resortwear faz login com a API Key,
envia catálogos, acompanha processamento em tempo real, abre detalhes
de pedidos recebidos e baixa romaneios sem encostar em terminal. UI é
Jinja2 + HTMX + Alpine.js servida pelo próprio FastAPI — sem build
step, sem porta extra (mesma 8004 da API).

### Decisão arquitetural — frontend

- **Jinja2 + HTMX + Alpine.js** em vez de React/Vue/Next. Caso de uso
  estreito (uma persona, poucas telas, baixa interatividade complexa),
  polling de jobs nativo do HTMX (`hx-trigger="every 2s"`), zero
  serviço novo, zero build. Decisão revisitável na Sprint 04 se surgir
  interatividade que justifique SPA.
- **Sem nova porta** — UI e API compartilham o container e a porta 8004.

### Added — Fundação (Fase A)

- `pyproject.toml`: adiciona `jinja2>=3.1` e `itsdangerous>=2.1`
  (python-multipart já existia desde Sprint 01).
- `static/css/app.css`: tokens da paleta Oasis (off-white `#FAF8F5`,
  bordô `#6B3A2A`, verde sábio `#4A7C59`, âmbar `#9A6B1A`, vermelho
  escuro `#A63228`) + Cormorant Garamond / Inter. Reset mínimo,
  utilitários, breakpoint mobile→desktop em 768px.
- `static/js/app.js`: `window.uploadProgress()` (XHR com `lengthComputable`
  para barra real, não `fetch` que não expõe progresso), `Alpine.store("toasts")`
  e `Alpine.data("uploadFlow")` registrados em `alpine:init`.
- `web/auth.py`: `create_session` / `verify_session` com
  `URLSafeTimedSerializer`, cookie `cf_session` (`httponly`, `samesite=lax`,
  `secure` em prod), TTL 8h. Dependency `require_session` retorna a
  API Key; `require_session_brand` adiciona o lookup da `Brand`.

### Added — Layout + login (Fase B)

- `templates/base.html`: layout mobile-first com header hambúrguer
  (Alpine inline `x-data="{ open: false }"`, drawer com `x-show`),
  Google Fonts via CDN, HTMX 1.9.12 e Alpine.js 3.14.1 (app.js carrega
  ANTES do Alpine para o listener `alpine:init` chegar a tempo), nav
  desktop horizontal a partir de 768px, área de toasts persistente.
- `templates/login.html`: tela isolada (sobrescreve o bloco `shell`)
  com card centralizado, "OASIS Resortwear" em Cormorant 32px, input
  API Key como password, botão bordô full-width, erro inline.
- `web/router.py`: GET `/login`, POST `/login` (valida direto via
  `auth_service.verify_api_key` — `/api/v1/health` é público e não
  serviria), GET `/logout`, GET `/` (redirect conforme sessão).

### Added — Dashboard + lista de catálogos (Fase C)

- `templates/dashboard.html`: saudação `Bem-vinda, {brand.name}`, data
  em pt-BR ("Terça-feira, 12 de maio de 2026"), 4 contadores em grid
  2x2 (mobile) / linha (desktop), atividade recente unificando
  catálogos e pedidos.
- `templates/catalogs/list.html` + `_badge.html`: tabela desktop /
  cards mobile + paginação 20/página + badge com polling HTMX every
  3s enquanto `pending`/`processing`. Estado vazio elegante.
- `web/data.py`: `DashboardCounts`, `ActivityItem`,
  `CatalogListPage`, `ProductListPage` — dataclasses de leitura para
  manter os templates limpos. Toda query inclui `brand_id` explícito.
- `web/_helpers.py`: `format_date_long_pt` / `format_date_short_pt` /
  `humanize_when` + mapeamento de status para `(label_pt, css_variant)`.

### Added — Upload + detalhe de catálogo (Fase D)

- `templates/catalogs/upload.html`: formulário com dropzone (clique
  no celular abre seletor, drag & drop no desktop com highlight
  bordô), state machine Alpine `idle → uploading → polling →
  success | error`. Upload via `window.uploadProgress` (XHR com
  progresso real), depois Alpine instala HTMX no `$nextTick` para
  começar o polling.
- `templates/catalogs/_upload_progress.html`: 3 estados:
  pending/running com barra animada, success com "✓ Catálogo pronto"
  + n_skus / n_fields + CTAs Baixar e Ver detalhes, erro com tile
  vermelho + mensagem amigável mapeada de códigos (PDF_ENCRYPTED →
  "PDF protegido com senha", FILE_TOO_LARGE → "Arquivo maior que 50
  MB", PDF_NO_PRODUCTS → "Nenhum produto detectado no catálogo").
- `templates/catalogs/detail.html` + `_actions_strip.html`:
  breadcrumb, título + badge polling, metadata strip, tabela
  paginada de produtos (mobile cards / desktop tabela 20/página).
  Strip de ações muda conforme status (botão Download em ready;
  barra animada em processing; tile de erro em error).
- `templates/errors/404.html`: template 404 elegante mantendo o
  shell, usado por catálogo inexistente, de outra brand ou download
  indisponível.
- POST `/catalogs/upload` faz proxy via httpx ASGI in-process para
  `/api/v1/catalogs/process` — mesma rota que cliente externo
  usaria, sem TCP roundtrip. Mapeia erros para mensagens amigáveis.

### Added — Lista + detalhe de pedidos (Fase E)

- `templates/orders/list.html` + `_badge.html`: tabela desktop /
  cards mobile com Lojista, Catálogo (via JOIN), Peças, Status, Data
  humanizada (hoje 10:45 / ontem 18:30 / 12/05 14:00). Polling do
  badge em pedidos `draft`.
- `templates/orders/detail.html`: items agrupados por SKU → cor →
  tamanho. **Mobile** — card por SKU com mini-grid de tamanhos ×
  quantidades (sub-seção por cor com swatch dot quando múltiplas).
  **Desktop** — tabela com colunas dinâmicas Produto | Cor | (sizes
  presentes) | Total, footer com total geral em pt-BR.
- `templates/orders/_romaneio_action.html`: 3 estados — `absent`
  (botão "Gerar romaneio" via HTMX POST), `processing` (spinner +
  polling every 2s), `ready` (link de download direto + script de
  auto-download na primeira detecção do estado pronto via polling).
- `web/data.py`: `OrderListPage`, `OrderDetail`,
  `group_items_by_sku()` (ordem canônica PP/P/M/G/GG/XG).
- POST `/orders/{id}/romaneio` → proxy via httpx para
  `GET /api/v1/orders/{id}/romaneio` (a API combina disparar +
  consultar no GET).
- GET `/orders/{id}/romaneio/poll` inspeciona estado direto via
  banco — não dispara nova geração.

### Added — Polimento (Fase F)

- `templates/errors/500.html`: página estéril (não vaza traceback
  nem request_id literal). Mantém o shell para usuário voltar.
- `main.py`: `_http_exception_handler` registra `StarletteHTTPException`
  → renderiza HTML 404 quando o path NÃO começa com `/api/v1/`,
  delegando para o handler padrão do FastAPI nos demais casos.
  `_unhandled_error_handler` agora renderiza HTML 500 em rotas web
  e mantém envelope JSON em rotas API.
- Acessibilidade: hambúrguer com `:aria-expanded="open"` +
  `aria-controls`; drawer com `role="dialog"` + `aria-modal="true"`
  + `aria-label`; ícones decorativos com `aria-hidden="true"`.
- `[x-cloak] { display: none !important; }` no CSS para futuros
  usos (evita flash de elementos com `x-show=false` antes do Alpine
  inicializar).

### Tests

- 34 testes em `src/catalogflow/web/tests/`:
  10 `test_web_auth.py` (login/logout/redirect com sessão),
  24 `test_web_pages.py` (dashboard, catalogs list/upload/detail,
  orders list/detail, badge fragment 404, 404 elegante para
  recursos inexistentes / de outra brand, handlers globais
  renderizam HTML para web e JSON para API).
- Suite total: **275 passed**, cobertura mantida ≥ 80%.

### Fixed

- `fix(web): register Alpine components and store before init` —
  `app.js` agora carrega antes do Alpine CDN no `base.html`. Com
  `defer`, scripts executam na ordem do DOM; o Alpine disparava
  `alpine:init` antes do listener ser instalado.
- `fix(web): fix hamburger menu not opening on mobile` — substituiu
  `<template x-if="open">` por `x-show="open"` (listener atachado
  desde o boot), `.stop` no clique do hambúrguer, removido
  `@click.outside` redundante do aside (overlay já cobre o caso).

### Decisões registradas

- **Validação de login direto no auth_service** — `/api/v1/health` é
  público (não exige Bearer) e portanto não pode diferenciar uma
  chave válida de uma inválida. Roteamos pela camada de serviço sem
  indireção HTTP.
- **POST `/catalogs/upload` proxy via httpx ASGI** — mesma rota que
  um cliente externo usaria, mas sem TCP roundtrip. Mantém a UI
  desacoplada da camada de domínio (não importa services aqui).
- **POST web `/orders/{id}/romaneio` apesar do GET na API** — a API
  expõe apenas GET (combina disparar+consultar). No web criamos POST
  para que o botão fique semanticamente correto (dispara ação),
  internamente chama o GET.
- **Inline `x-data="{...}"` em vez de `Alpine.data(...)` registrado** —
  componentes do shell (hambúrguer) usam objeto inline pra não
  depender de ordem de carregamento. `uploadFlow` permanece via
  `Alpine.data` (factory complexa o suficiente para justificar JS).

### Next (Sprint 04 — preview)

- Cadastro de novas brands via interface (sai do `/internal/`).
- Visualização de produtos com thumbnail extraído do PDF.
- Notificações push do navegador quando o processamento termina.
- Dashboard com gráficos (catálogos mais pedidos, lojistas top, etc.).

---

## [0.2.0] — Sprint 02: Order extraction + Romaneio

Fecha o ciclo de pedido ponta a ponta. Quando uma lojista preenche o PDF
editável gerado na Sprint 01 e devolve, o sistema extrai os campos, estrutura
o pedido (com enriquecimento opcional via `catalog_id`) e gera o romaneio
PDF profissional. Toda a parte de extração/geração é função pura (bytes-in,
bytes-out) — I/O fica confinado nos services.

### Added — Schema & migrations (Fase A)

- Migration `0003_orders_schema.py` (reversível) criando `orders`,
  `order_items` (UNIQUE `(order_id, sku, color_index, size)` + CHECK
  `quantity > 0`), `romaneios` (UNIQUE `order_id` para 1:1) e a coluna
  `brands.logo_key` (S3 key da logo da marca, opcional).
- `auth/models.Brand.logo_key: Mapped[str | None]`.
- Índices: `idx_orders_brand_id`, `idx_orders_catalog_id`,
  `idx_order_items_order_id`, `idx_romaneios_brand_id`.

### Added — Fixtures de pedido (Fase B)

- `tests/fixtures/generate_order_fixtures.py` reusa `PDFAnalyzer` +
  `FieldInjector` da Sprint 01 e produz 6 fixtures determinísticas:
  `pedido_preenchido_v2.pdf`, `pedido_preenchido_v1.pdf` (legado),
  `pedido_campos_vazios.pdf`, `pedido_valores_invalidos.pdf`,
  `pedido_flattened.pdf` (sem `/AcroForm`), `pedido_mixed_v1_v2.pdf`.
- Validado que `widget.field_name = ...` + `widget.update()` no PyMuPDF
  persiste rename para gerar v1 a partir de v2.

### Added — Extractor + Normalizer (Fase C)

- `orders/extractor.py` — `OrderExtractor` puro (`bytes → RawOrderData`):
  regex v2 (`qty__SKU__corN__TAM`) tentado antes do v1
  (`qty__SKU__TAM`, color_index=1); valores não-numéricos/float/negativos/
  zero descartados silenciosamente; PDF sem `/AcroForm` levanta
  `PDFFlattenedError`. Helpers `_parse_quantity`, `_parse_field_name`,
  `_consolidate_source_format` testáveis isoladamente.
- `orders/normalizer.py` — `OrderNormalizer` puro: agrega duplicatas em
  `(sku, color_index, size)`, enriquece via `CatalogProduct` (nome, preço,
  hex do swatch), warnings para SKU órfão, totais (peças, valor, n_skus),
  ordenação por `page_index` quando catálogo disponível.
- `orders/{models,schemas}.py` — Order/OrderItem SQLAlchemy 2.0 com
  selectinload-friendly relationship; schemas Pydantic v2
  (`OrderResponse`, `OrderTotals`, `ExtractOrderResponse`,
  `RomaneioStatusResponse`).

### Added — RomaneioBuilder (Fase D)

- `romaneio/models.py` — `Romaneio` 1:1 com `Order`. `Order.romaneio`
  back_populates via string forward reference (padrão SQLAlchemy 2.0).
- `romaneio/builder.py` — `RomaneioBuilder` puro (`OrderData + Config →
  bytes`): cabeçalho com logo opcional (`page.insert_image(stream=)`),
  faixa brand, lojista, data em pt-BR; bloco por SKU com grid cor x
  tamanho; paginação automática com cabeçalho repetido; totalizador
  final. Formato monetário pt-BR via string mangling (sem
  `locale.setlocale`); `format_currency` e `format_date_pt_br` exportados.

### Added — Services + tasks (Fase E)

- `orders/service.OrderService`: `create_order` valida MIME/tamanho/
  catalog cross-tenant, `get_order` (selectinload),
  `process_order` (download → extract → normalize → persist).
- `orders/tasks.extract_order_task`: `PDFFlattenedError` é tratado como
  **permanente** e NÃO dispara `self.retry()` (Armadilha #3 do PRD);
  erros transitórios sobem com backoff exponencial `60s × 2^n`.
- `romaneio/service.RomaneioService`: `generate_romaneio` reaproveita
  Romaneio existente (UNIQUE `order_id`); `process_romaneio` baixa logo
  do storage se `brand.logo_key`, constrói PDF e faz upload com chave
  `{brand}/orders/{order}/romaneio.pdf`; `get_download_url` retorna
  presigned URL.
- `romaneio/tasks.generate_romaneio_task` com retry exponencial para
  todos os erros (geração sem classe "permanente" — falhas de
  storage/builder são por natureza transientes).

### Added — Routers + health (Fase F)

- `orders/router.py` montado em `main.py`:
  - `POST /api/v1/orders/extract` (202) — multipart upload, valida e
    enfileira `order.extract`.
  - `GET /api/v1/orders/{id}` (200) — order completo com items + totals.
  - `GET /api/v1/orders/{id}/romaneio` — 302 redirect para presigned URL
    quando pronto; 202 com `job_id` em andamento ou enfileira nova
    geração.
- `GET /api/v1/health` estendido com contagens `jobs.{catalog_pending,
  order_pending, romaneio_pending}` — útil para dashboards e alertas.
- `shared/jobs_router.py` já era genérico — reconhece automaticamente
  `order.extract` e `romaneio.generate`.

### Added — Tests (Sprint 02)

- 34 testes em `orders/tests/test_extractor.py` cobrindo todas as 6
  fixtures + edge cases + funções puras + pureza.
- 18 testes em `orders/tests/test_normalizer.py` (sem/com catálogo,
  warnings, agregação, totais, ordenação, source_format propagation).
- 24 testes em `romaneio/tests/test_builder.py` (PDF válido, conteúdo
  textual, logo presente/ausente/corrompida, paginação, sem preço, sem
  itens, helpers de formato).
- 11 testes em `orders/tests/test_service.py` (criação, validação,
  isolamento, processo, PDF flatten, race condition).
- 15 testes em `romaneio/tests/test_service.py` (generate, process com/
  sem logo, get_download_url, isolamento, bookkeeping).
- 13 testes em `orders/tests/test_router.py` (HTTP integration via
  httpx — auth, MIME, size, isolamento, romaneio endpoint redirects).
- 10 testes em `*/tests/test_tasks.py` cobrindo o wrapper Celery de
  catalog/orders/romaneio (resolve dívida da Armadilha #5 do PRD).
- `tests/integration/test_order_pipeline.py` cobre o pipeline ponta a
  ponta: catalog → fill widgets → extract → romaneio → download.
- `_TABLES_TO_TRUNCATE` em `conftest.py` agora inclui `romaneios`,
  `order_items`, `orders` (ordem dependência-respeitada).

### Fixed / Infra

- `alembic.ini` ganhou `path_separator = os` — silencia
  `DeprecationWarning` introduzido em Alembic 1.14+ que era promovido
  a erro pelo `filterwarnings = ["error", ...]` do pyproject.
- Singleton de `infra.storage._storage` continua respeitando
  `dispose_engine` em testes — testcontainer não vaza entre runs.

### Decisões registradas

- **`PDFFlattenedError` permanente** — Erro de dados: novo retry não
  recupera. Estado de erro gravado no `Order` e no `Job` antes da
  exceção subir, garantindo observabilidade mesmo sem retry.
- **Builder com `(order_data, config)`** — Mantém a assinatura do PRD;
  `RomaneioConfig` mescla branding (logo, brand_name) e contexto do
  pedido (lojista_name, emitted_at), simplificando a chamada do service.
- **Catálogo opcional** — `process_order` sem `catalog_id` produz items
  sem enriquecimento (`product_name` / `unit_price` = `None`), conforme
  PRD. Romaneio funciona com totais sem valor monetário.
- **Logo opcional + fail-soft** — Download da logo do storage com
  `try/except`: logo corrompida ou ausente cai pro cabeçalho textual,
  nunca derruba a geração do romaneio.

### Next (Sprint 03 — preview)

- Webhook de notificação (`catalog.ready`, `order.extracted`,
  `romaneio.ready`).
- Módulo `stock` com `StockAdapter` (Fase 2 do roadmap).
- Web UI mínima (upload + status + download).
- Módulo `User` com login/senha.

---

## [0.1.0] — 2026-05-11 — Sprint 01: Foundation

Primeira sprint do CatalogFlow. Entrega a fundação completa do projeto e o
pipeline ponta-a-ponta de processamento de catálogos PDF, do upload
autenticado à entrega do PDF com campos AcroForm injetados.

### Added — Infra & estrutura (Fase A)

- Estrutura modular `src/catalogflow/{modules,shared,infra,scripts}` +
  `tests/{integration,e2e,fixtures}` com todos os `__init__.py`.
- `pyproject.toml` com dependências completas (FastAPI, SQLAlchemy 2.0,
  Celery, PyMuPDF, pdfplumber, aioboto3, etc.) e configuração de
  `ruff`/`mypy --strict`/`pytest`/`coverage` (threshold 80%).
- `.env.example` documentando todas as variáveis suportadas.
- `docker/Dockerfile` multi-stage (builder + production), rodando como
  usuário não-root `catalogflow`.
- `docker/docker-compose.yml` levantando `api`, `worker`, `beat`, `flower`,
  `postgres`, `redis` e `minio` (substituto de R2/S3 em dev).
- `infra/settings.py` (Pydantic BaseSettings com `SecretStr`),
  `infra/database.py` (SQLAlchemy async + `get_db()`),
  `infra/cache.py` (Redis async pool),
  `infra/storage.py` (`StorageClient` upload/download/presigned/delete).
- `.gitignore` com regras para `.env`, `example/*.pdf`, caches e venv.

### Added — Auth & multi-tenancy (Fase B)

- Alembic configurado em modo async (`migrations/env.py`), com
  `0001_auth_tables.py` reversível criando `brands` + `api_keys` (hash
  SHA-256, prefixo `cf_`).
- `auth/{models,schemas,service,router,dependencies}.py`.
- `get_current_brand()` (dependency) com `BackgroundTasks` para
  `last_used`; `require_internal_secret()` com comparação constant-time.
- Rotas administrativas `POST /internal/brands` e
  `POST /internal/brands/{id}/api-keys` (gated por `X-Internal-Secret`).
- Testes (≥ 26 casos): criação, slug duplicado, key inválida/expirada,
  rotação invalida o token antigo, gate interno 401 sem/errado/correto.
- Script de seed `python -m catalogflow.scripts.seed_dev` cria a brand
  `oasis` + uma API key (raw retornado uma única vez).

### Added — App principal (Fase C)

- `main.py` com `create_app()` factory + lazy `app` via PEP 562 — testes
  importam o módulo sem disparar `get_settings()`.
- Lifespan: testa Postgres e Redis no startup, dispõe pools no shutdown.
- `shared/responses.py`: envelope padrão `StandardResponse[T]` com
  `request_id` e `timestamp` em `meta`.
- `shared/middleware.py`: `RequestIdMiddleware` lê/gera UUID4 no header
  `X-Request-ID` e ecoa na resposta.
- 3 exception handlers: `DomainError` → 4xx via envelope;
  `RequestValidationError` → 422; `Exception` (catch-all) → 500
  estéril (não vaza traceback).
- `GET /api/v1/health` retorna 200 quando ok, **503** se alguma
  dependência respondeu erro (probe-friendly).
- 11 integration tests (handlers, request_id, CORS preflight, envelope,
  health, traceback isolation).

### Added — Catalog: pipeline completo (Fase D)

- Migration `0002_catalog_tables.py` cria `catalogs`, `catalog_products`
  (UNIQUE `(catalog_id,sku,page_index)`), `jobs` (CHECK status + progress).
- `catalog/models.py` com Catalog/CatalogProduct/Job em SQLAlchemy 2.0.
- `catalog/schemas.py` com DTOs (`CatalogResponse`,
  `ProcessCatalogResponse`, `JobResponse`, `CatalogProductResponse`).
- `catalog/pdf_analyzer.py` — engine **puro** (`bytes → CatalogMetadata`),
  migrado de `oasis_form_v2.py`: regex SKU/grade fiéis, threshold de
  swatch 0.92, lógica `single`/`left`/`right`, dataclasses `frozen+slots`.
- `catalog/field_injector.py` — engine **puro** (`bytes + metadata → bytes`),
  todas as constantes idênticas ao POC. Compressão à esquerda quando há
  vizinho direito; helpers públicos `field_name_for()` e `count_fields()`.
- `infra/celery_app.py` com routes por módulo, JSON-only,
  `acks_late + prefetch_multiplier=1` para reliability.
- `catalog/tasks.py` com `process_catalog_task` (bind=True, max_retries=3,
  backoff exponencial). Erros permanentes vs. transitórios distintos.
- `catalog/service.py` com isolamento multi-tenant em todo SELECT,
  validação de assinatura `%PDF`, validação de tamanho contra
  `max_pdf_size_bytes`, e `_claim_job` race-safe via
  `UPDATE WHERE status='pending' RETURNING id`.
- `catalog/router.py` (3 endpoints) + `shared/jobs_router.py`
  (`GET /api/v1/jobs/{id}` filtrado por brand).
- 6 fixtures sintéticas geradas via `tests/fixtures/generate_fixtures.py`
  (1 produto/1 cor, 1 produto/2 cores, 2 produtos/página, grade PP-G,
  sem produtos, criptografado).
- ≥ 70 testes do módulo (analyzer, injector, service com FakeStorage,
  router HTTP).

### Added — Esqueletos para Sprints futuras (Fase E)

- `orders/{models,schemas,service,router,tasks,extractor,normalizer}.py`
  como esqueleto com `NotImplementedError("Sprint 02")`. Router não está
  registrado no `create_app`.
- `romaneio/{service,router,tasks,builder}.py` mesmo padrão.

### Added — CI & finalização (Fase F)

- `.github/workflows/ci.yml` com 4 jobs: `quality` (ruff + mypy),
  `test` (pytest + coverage 80%), `build` (docker multi-stage + smoke),
  `security` (pip-audit + bandit). Concurrency cancela runs anteriores.
- `tests/integration/test_catalog_pipeline.py` exercita o pipeline real
  com Postgres + storage in-memory + engines reais (sem Celery).
- `tests/e2e/test_api_flows.py` cobre o flow HTTP completo via httpx
  (health → upload → poll → simular worker → poll → download).
- `tests/fakes.py` centraliza `FakeStorage` (compartilhado entre
  conftests).
- `README.md` com setup em 5 minutos, smoke test do upload, descrição
  da stack local e troubleshooting.

### Decisões arquiteturais relevantes

- **Funções de PDF puras** (bytes-in, bytes-out) — testáveis sem I/O,
  preparadas para extração para microserviço.
- **PostgreSQL + Redis sempre** (ADR-003) — sem SQLite mesmo em testes;
  `testcontainers` provê Postgres efêmero.
- **PyMuPDF AGPL** (ADR-004) — licença comercial Artifex obrigatória
  antes do go-live em produção.
- **S3-compatible storage** (ADR-005) — banco grava só metadados +
  chave; bytes vivem no R2.
- **API key SHA-256 com prefixo `cf_`** — plaintext exposto uma única
  vez; comparação por hash é O(1) por índice UNIQUE.
- **`UPDATE WHERE status='pending'` race-safe** — impede dois workers
  pegarem o mesmo job mesmo sem locks distribuídos.
- **Envelope JSON único** — `success`/`data`/`error`/`meta` em toda
  resposta; `meta.request_id` propaga via `X-Request-ID`.

### Next (Sprint 02 — preview)

- Implementação de `orders/extractor` + `normalizer` (parse de campos
  AcroForm preenchidos, suporte a v1 e v2 do formato).
- `romaneio/builder` gerando o PDF profissional (header, grids, totais,
  paginação).
- Webhook de notificação (`catalog.ready`, `order.extracted`).
- Detecção de PDF achatado (`PDF_FLATTENED`) e fallback documentado.
