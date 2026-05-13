# Prompts de Execução Sprint 02 — Faseados
# Use um prompt por vez no Claude Code. Só avance para o próximo quando o anterior estiver concluído e funcionando.
# Todos os testes da Sprint 01 devem continuar passando após cada fase.

---

## PROMPT 0 — Leitura e confirmação (colar PRIMEIRO)

```
Leia os seguintes arquivos na ordem e confirme o entendimento:
1. spec.md
2. docs/sprint_02/PRD_sprint_02.md
3. oasis_romaneio.py
4. CLAUDE.md

Após ler, responda com:
- Objetivo da Sprint 02 em uma frase
- Diferença entre OrderExtractor e OrderNormalizer (por que são classes separadas?)
- Como a logo da marca chega ao RomaneioBuilder (fluxo completo: upload → storage → builder)
- Confirme que entendeu que PDFFlattenedError NÃO deve gerar retry no Celery

Não escreva código ainda.
```

---

## PROMPT 1 — Fase A: Migration + Brand.logo_key (colar após aprovação do Prompt 0)

```
Execute a Fase A do PRD Sprint 02.

Entregáveis:
1. migrations/versions/003_orders_schema.py com:
   - ALTER TABLE brands ADD COLUMN logo_key VARCHAR(512)
   - CREATE TABLE orders (todos os campos do spec.md §7)
   - CREATE TABLE order_items (todos os campos + CHECK quantity > 0)
   - CREATE TABLE romaneios
   - Índices: idx_orders_brand_id, idx_orders_catalog_id, idx_order_items_order_id, idx_romaneios_brand_id
2. auth/models.py — adicionar campo logo_key: Mapped[str | None] ao model Brand existente
3. Executar: alembic upgrade head — deve rodar sem erro

Regras:
- NÃO modificar nenhuma migration existente (001, 002)
- A migration 003 deve ser reversível (implementar downgrade)
- Após aplicar, rodar pytest tests/ — todos os 111 testes da Sprint 01 devem continuar passando

Não escreva código de módulo ainda — apenas migration e o campo no Brand.

Faça commit: `feat(db): add orders/order_items/romaneios tables and brands.logo_key (migration 003)`
```

---

## PROMPT 2 — Fase B: Fixtures de PDF para orders (colar após Fase A)

```
Execute a Fase B do PRD Sprint 02.

Entregáveis:
1. tests/fixtures/generate_order_fixtures.py — gerar programaticamente:
   - pedido_preenchido_v2.pdf (usar FieldInjector da Sprint 01 + preencher campos)
   - pedido_preenchido_v1.pdf (campos no formato legado qty__SKU__TAM)
   - pedido_campos_vazios.pdf (AcroForm presente mas todos em branco)
   - pedido_valores_invalidos.pdf (campos com "abc", "3.5", "-1", "0")
   - pedido_flattened.pdf (PDF sem AcroForm — apenas insert_text, sem add_widget)
   - pedido_mixed_v1_v2.pdf (metade v1, metade v2 na mesma página)
   Executar o script e commitar os PDFs gerados em tests/fixtures/

Regras:
- Gerar fixtures usando as fixtures de catálogo da Sprint 01 como base (FieldInjector)
- Não commitar PDFs reais da Oasis
- pytest deve continuar verde (111 testes passando)

Faça commit: `test: generate order PDF fixtures for Sprint 02`
```

---

## PROMPT 3 — Fase C: OrderExtractor + OrderNormalizer (colar após Fase B)

```
Execute a Fase C do PRD Sprint 02.

Entregáveis:
1. orders/models.py — Order + OrderItem (SQLAlchemy 2.0, type hints completos):
   - Order.items com selectinload configurado
   - Order.romaneio relationship
   - OrderItem com relationship back para order
2. orders/schemas.py — todos os schemas Pydantic v2:
   - OrderCreateRequest, OrderItemResponse, OrderResponse
   - OrderTotals, ExtractOrderResponse, RomaneioStatusResponse
3. orders/extractor.py — classe OrderExtractor PURA (zero I/O):
   - Dataclasses: RawOrderItem, RawOrderData
   - Regex v2: ^qty__(?P<sku>[^_]+(?:_[^_]+)*)__cor(?P<color>\d+)__(?P<size>[^_]+)$
   - Regex v1: ^qty__(?P<sku>[^_]+(?:_[^_]+)*)__(?P<size>[^_]+)$ → color_index=1
   - Campos fora do padrão: ignorar silenciosamente
   - PDF sem AcroForm: levantar PDFFlattenedError (adicionar em shared/errors.py)
4. orders/tests/test_extractor.py — testar com todas as fixtures:
   - pedido_preenchido_v2.pdf → items e contagens corretos
   - pedido_preenchido_v1.pdf → color_index=1, source_format="v1"
   - pedido_campos_vazios.pdf → items=[], has_acroform=True
   - pedido_valores_invalidos.pdf → items=[], todos descartados
   - pedido_flattened.pdf → levanta PDFFlattenedError
   - pedido_mixed_v1_v2.pdf → source_format="mixed"
5. orders/normalizer.py — classe OrderNormalizer PURA (zero I/O):
   - Aceita RawOrderData + list[CatalogProduct] | None
   - Sem catalog: items sem enriquecimento, sem warnings
   - Com catalog: enriquece product_name, unit_price, color_hex via swatches
   - SKU no PDF ausente no catálogo: warning adicionado, item preservado
   - Calcula totais: total_items, total_pecas, valor_total, n_skus
6. orders/tests/test_normalizer.py — todos os cenários acima

Regra crítica: extractor.py e normalizer.py são FUNÇÕES PURAS.
pymupdf.open(stream=pdf_bytes, filetype="pdf") — NUNCA open("arquivo.pdf")

Faça commit: `feat(orders): add OrderExtractor and OrderNormalizer (pure functions)`
```

---

## PROMPT 4 — Fase D: RomaneioBuilder (colar após Fase C)

```
Execute a Fase D do PRD Sprint 02.

Entregáveis:
1. romaneio/models.py — model Romaneio (SQLAlchemy 2.0):
   - Todos os campos da migration 003
   - Relationship para Order
2. romaneio/builder.py — classe RomaneioBuilder PURA (zero I/O):
   - Dataclass RomaneioConfig: brand_name, logo_bytes (bytes|None), title, show_prices, currency_symbol, locale
   - Método build(order_data: OrderData, config: RomaneioConfig) -> bytes
   - Layout A4 (595×842pt), margens 40pt, fonte Helvetica
   - Cabeçalho: logo (se logo_bytes não None) + título + lojista + data
   - Por SKU: bloco com nome, ref, preço unitário, grid cor×tamanho, subtotal
   - Paginação automática: calcular altura antes de inserir, nova página se não couber
   - Cabeçalho repetido em cada nova página
   - Rodapé final: total peças, total SKUs, valor total
   - Valores monetários: formato pt_BR sem locale.setlocale
     (usar f"R$ {v:,.2f}".replace(",","X").replace(".","," ).replace("X","."))
   - Datas: strftime("%d/%m/%Y")
   - Migrar lógica visual do oasis_romaneio.py, adaptando para bytes in → bytes out
3. romaneio/tests/test_builder.py:
   - PDF gerado tem tamanho > 0 e é PDF válido (abrir com pymupdf)
   - PDF contém texto do SKU e nome da lojista
   - Pedido com muitos SKUs → múltiplas páginas, cabeçalho repetido
   - Com logo_bytes → logo presente (verificar via page.get_images())
   - Sem logo_bytes → sem erro, cabeçalho apenas textual
   - Produto sem preço → coluna valor exibe "—" ou é omitida sem erro

Regra crítica: builder.py é FUNÇÃO PURA. Zero I/O.
Basear layout visual no example/romaneio_demo.pdf.

Faça commit: `feat(romaneio): add RomaneioBuilder (pure function, PT-BR layout)`
```

---

## PROMPT 5 — Fase E: Services + Tasks (colar após Fase D)

```
Execute a Fase E do PRD Sprint 02.

Entregáveis:
1. orders/service.py — OrderService:
   - create_order(brand_id, pdf_bytes, catalog_id, lojista_name, lojista_token) -> tuple[Order, Job]
     Valida PDF (MIME, tamanho, não criptografado), faz upload, cria Order + Job, enfileira task
   - get_order(order_id, brand_id) -> Order
     selectinload(Order.items). Levanta NotFoundError se não for da brand.
   - process_order(order_id) -> None
     Chamado pela task. Baixa PDF, extrai, normaliza, persiste OrderItems.
     Se PDFFlattenedError: atualiza Order.status="error", error_message="PDF_FLATTENED", NÃO faz retry.
   - get_romaneio_status(order_id, brand_id) -> dict
     Se romaneio existe e pronto: retorna download_url (presigned URL)
     Se não existe: enfileira geração, retorna job_id
2. orders/tasks.py — extract_order_task:
   - bind=True, max_retries=3, name="order.extract"
   - PDFFlattenedError: capturar, NÃO chamar self.retry() — erro permanente
   - Erros transitórios: self.retry(exc=exc, countdown=60 * 2**self.request.retries)
3. orders/tests/test_service.py — com mocks de storage e Celery:
   - create_order enfileira job
   - Pedido de outra brand → NotFoundError
   - process_order com PDF válido → status "extracted", items persistidos
   - process_order com PDF flattened → status "error", código "PDF_FLATTENED"
   - get_romaneio_status sem romaneio → enfileira geração
4. romaneio/service.py — RomaneioService:
   - generate_romaneio(order_id, brand_id) -> tuple[Romaneio, Job]
   - process_romaneio(romaneio_id) -> None
     Carrega Order com items e brand (selectinload). Busca logo do storage se brand.logo_key.
     Chama RomaneioBuilder.build(). Faz upload do PDF. Atualiza Romaneio.output_key.
   - get_download_url(order_id, brand_id) -> str
     Gera presigned URL. Levanta NotReadyError se romaneio não existe.
5. romaneio/tasks.py — generate_romaneio_task:
   - bind=True, max_retries=3, name="romaneio.generate"
6. romaneio/tests/test_service.py

Faça commit: `feat(orders): add OrderService and extract_order_task`
Faça commit separado: `feat(romaneio): add RomaneioService and generate_romaneio_task`
```

---

## PROMPT 6 — Fase F: Routers + health + integração + finalização (colar após Fase E)

```
Execute a Fase F do PRD Sprint 02 — fase final obrigatória.

Entregáveis:
1. orders/router.py — substituir esqueleto pela implementação real:
   - POST /api/v1/orders/extract (multipart: file, catalog_id, lojista_name, lojista_token) → 202
   - GET /api/v1/orders/{order_id} → 200 com OrderResponse completo
   - GET /api/v1/orders/{order_id}/romaneio:
     Se pronto → 302 redirect para presigned URL
     Se em andamento ou não iniciado → 202 com job_id
2. main.py — registrar orders_router (já existe import de esqueleto, ativar)
3. shared/jobs_router.py — verificar que job_type "order.extract" e "romaneio.generate"
   são retornados corretamente pelo GET /api/v1/jobs/{job_id}
4. GET /api/v1/health — adicionar contagem de jobs pendentes por tipo:
   {"status":"ok","db":"ok","redis":"ok","jobs":{"catalog_pending":0,"order_pending":0,"romaneio_pending":0}}
5. orders/tests/test_router.py — integration tests via httpx.AsyncClient:
   - Sem auth → 401
   - Arquivo não-PDF → 400 INVALID_FILE_TYPE
   - Upload > 50MB → 400 FILE_TOO_LARGE
   - Upload válido → 202 com order_id + job_id
   - GET pedido de outra brand → 404
   - GET /romaneio quando não pronto → 202 com job_id
   - GET /romaneio quando pronto → 302 redirect
   - Pedido brand A com API key brand B → 404
6. tests/integration/test_order_pipeline.py — pipeline completo (task.apply() síncrono):
   a. Criar catálogo com fixture PDF da Sprint 01
   b. Processar catálogo (catalog task síncrona) — resolve dívida de 0% em catalog/tasks.py
   c. Gerar PDF preenchido programaticamente (preencher widgets do output)
   d. POST /orders/extract com catalog_id
   e. Processar pedido (order task síncrona)
   f. Verificar Order.status == "extracted"
   g. Verificar OrderItems enriquecidos (product_name, unit_price não None)
   h. Trigger romaneio (romaneio task síncrona)
   i. Verificar Romaneio.output_key preenchido
   j. Download romaneio — bytes > 0, é PDF válido
7. CHANGELOG.md — entry Sprint 02 conforme PRD
8. README.md — adicionar seção "Fluxo completo" com exemplos curl:
   # 1. Processar catálogo
   curl -X POST /api/v1/catalogs/process -H "Authorization: Bearer cf_..." \
     -F "file=@catalogo.pdf" -F "name=Inverno 26"
   # 2. Polling até success
   curl /api/v1/jobs/{job_id} -H "Authorization: Bearer cf_..."
   # 3. Download PDF editável
   curl -L /api/v1/catalogs/{id}/download -H "Authorization: Bearer cf_..."
   # 4. Upload PDF preenchido
   curl -X POST /api/v1/orders/extract -H "Authorization: Bearer cf_..." \
     -F "file=@preenchido.pdf" -F "catalog_id={catalog_id}"
   # 5. Download romaneio
   curl -L /api/v1/orders/{id}/romaneio -H "Authorization: Bearer cf_..."

Verificação final:
- [ ] pytest tests/ --cov=src --cov-fail-under=80 passa
- [ ] ruff check . && mypy src/ sem erros
- [ ] docker-compose up funciona
- [ ] Smoke test manual: PDF preenchido real da Oasis → romaneio gerado

Faça commit: `feat(orders): add order extraction and romaneio endpoints`
Faça commit separado: `feat: complete Sprint 02 — orders extraction and romaneio generation`
Não faça push — PMO revisa antes.
```

---

## PROMPT 7 — Fase G (opcional): Logo da marca

> Só executar se os Prompts 0–6 estiverem concluídos e houver tempo na sprint.

```
Execute a Fase G opcional do PRD Sprint 02 — endpoint de logo.

Entregáveis:
1. auth/router.py — adicionar endpoint POST /internal/brands/{brand_id}/logo:
   - Aceita multipart UploadFile (PNG ou JPG)
   - Valida MIME server-side: apenas image/png e image/jpeg
   - Limita a 2MB
   - Faz upload para storage com chave: {brand_id}/logo.{ext}
   - Atualiza Brand.logo_key no banco
   - Protegido por require_internal_secret (mesmo padrão dos outros endpoints internos)
   - Retorna StandardResponse com {"logo_key": "..."}
2. auth/tests/test_router.py — adicionar testes:
   - Upload PNG válido → 200, logo_key atualizado no banco
   - Upload não-imagem → 400 INVALID_FILE_TYPE
   - Upload > 2MB → 400 FILE_TOO_LARGE
   - Sem INTERNAL_SECRET → 401
3. Verificar que RomaneioService já busca brand.logo_key do storage (implementado no Prompt 5)
   e que o builder renderiza a logo corretamente quando presente

Faça commit: `feat(auth): add brand logo upload endpoint (optional E9)`
Não faça push — PMO revisa antes.
```

---

# Resumo da sequência

| Prompt | Fase | Escopo | Commit |
|--------|------|--------|--------|
| 0 | Leitura | Confirmar entendimento | nenhum |
| 1 | A | Migration 003 + Brand.logo_key | `feat(db): add orders tables...` |
| 2 | B | Fixtures de PDF para orders | `test: order fixtures` |
| 3 | C | Extractor + Normalizer (puros) | `feat(orders): extractor and normalizer` |
| 4 | D | RomaneioBuilder (puro) | `feat(romaneio): RomaneioBuilder` |
| 5 | E | Services + Tasks | `feat(orders): service + task` + `feat(romaneio): service + task` |
| 6 | F | Routers + health + integração + docs | `feat(orders): endpoints` + `feat: complete Sprint 02` |
| 7 *(opcional)* | G | Logo endpoint | `feat(auth): brand logo upload` |

**Regra geral:** após cada prompt, os 111 testes da Sprint 01 devem continuar passando. Qualquer regressão deve ser corrigida antes de avançar.
