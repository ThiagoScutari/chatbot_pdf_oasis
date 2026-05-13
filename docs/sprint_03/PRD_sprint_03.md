# PRD Sprint 03 — Interface Web

> **Projeto:** CatalogFlow
> **Sprint:** 03 / Web UI — Gerente Comercial
> **Status:** Aprovado
> **Data de início:** A definir
> **Duração estimada:** 5–7 dias de trabalho do executor
> **PMO:** Thiago Scutari
> **Executor:** Claude Code
> **Referência obrigatória:** `spec.md` (contrato técnico do projeto)
> **Dependência:** Sprint 01 e Sprint 02 concluídas e em main ✅

---

## Objetivo da Sprint

Entregar uma interface web elegante e funcional para a gerente comercial da Oasis Resortwear operar o CatalogFlow sem precisar de terminal — do upload do catálogo ao download do romaneio — com identidade visual alinhada ao padrão de sofisticação da marca.

Ao final desta sprint, a gerente deve conseguir:

1. Fazer login com sua API Key via browser
2. Enviar um catálogo PDF e acompanhar o processamento em tempo real
3. Baixar o PDF editável gerado
4. Ver todos os pedidos recebidos organizados por status
5. Baixar o romaneio de qualquer pedido com um clique

---

## Decisão Arquitetural — Frontend

**Decisão:** Jinja2 templates + HTMX + Alpine.js servidos diretamente pelo FastAPI existente.

**Motivo:** evita um serviço separado (sem nova porta, sem Vite, sem build step), deploy idêntico ao backend, e HTMX trata o polling assíncrono de jobs nativamente via `hx-trigger="every 2s"`. Para uma ferramenta interna de uma usuária, é a solução com menor complexidade operacional e maior velocidade de entrega.

**Não usar:** React, Vue, Next.js — seriam overengineering para este caso de uso. A decisão pode ser revisada na Sprint 04 se houver necessidade de interatividade mais complexa.

**Porta:** nenhuma nova — a UI é servida pelo mesmo container da API na porta **8004**.

---

## Identidade Visual — Oasis Resortwear

A interface é uma ferramenta interna da Oasis. Deve transmitir a mesma sofisticação da marca — não pode parecer um sistema genérico.

### Tokens de design

```css
/* Cores */
--color-bg:          #FAF8F5;   /* off-white quente — fundo das páginas */
--color-surface:     #FFFFFF;   /* branco puro — cards e painéis */
--color-border:      #E8E0D5;   /* bege claro — bordas e divisores */
--color-text:        #1A1A1A;   /* quase preto — texto principal */
--color-text-muted:  #7A6E65;   /* marrom claro — labels e metadados */
--color-accent:      #6B3A2A;   /* bordô/terracota — ação primária */
--color-accent-hover:#4F2A1D;   /* bordô escuro — hover */
--color-success:     #4A7C59;   /* verde sábio — status "ready" */
--color-warning:     #9A6B1A;   /* âmbar — status "processing" */
--color-error:       #A63228;   /* vermelho escuro — status "error" */

/* Tipografia */
--font-display: 'Cormorant Garamond', Georgia, serif;  /* títulos e headers */
--font-body:    'Inter', system-ui, sans-serif;         /* dados e UI */

/* Espaçamento e forma */
--radius:  4px;    /* bordas levemente arredondadas — não muito moderno */
--shadow:  0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
```

### Princípios de interface

- **Muito espaço em branco** — elementos respiram, nunca comprimidos
- **Hierarquia tipográfica clara** — títulos em Cormorant, dados em Inter
- **Sem cores saturadas** — paleta é toda quente e dessaturada
- **Status como badges sutis** — não semáforos gritantes
- **Ações primárias em bordô** — um único CTA por tela
- **Tabelas minimalistas** — sem faixas zebradas coloridas, só linhas finas

---

## Entregáveis

### E1 — Estrutura do frontend

#### Pastas e arquivos a criar

```
src/catalogflow/
├── web/                          # Novo módulo — UI
│   ├── __init__.py
│   ├── router.py                 # FastAPI router para páginas HTML
│   ├── auth.py                   # Session cookie com API Key
│   └── templates/
│       ├── base.html             # Layout base com nav e tokens CSS
│       ├── login.html            # Tela de login
│       ├── dashboard.html        # Visão geral
│       ├── catalogs/
│       │   ├── list.html         # Lista de catálogos
│       │   ├── upload.html       # Upload de novo catálogo
│       │   └── detail.html       # Detalhes + download
│       └── orders/
│           ├── list.html         # Lista de pedidos
│           └── detail.html       # Detalhes + romaneio
├── static/
│   ├── css/
│   │   └── app.css               # Tokens CSS + utilitários customizados
│   └── js/
│       └── app.js                # Alpine.js helpers (upload progress, etc.)
```

#### Dependências a adicionar em `pyproject.toml`

```toml
"jinja2>=3.1",
"python-multipart>=0.0.9",   # já existe — confirmar
"itsdangerous>=2.1",          # assinar cookies de sessão
```

HTMX e Alpine.js via CDN no `base.html` — sem build step.

---

### E2 — Autenticação por sessão

#### `web/auth.py`

A gerente entra com a API Key uma vez. O backend assina um cookie de sessão com `itsdangerous.URLSafeTimedSerializer`. A cada request de página, o cookie é verificado e a API Key é recuperada para usar nas chamadas internas à API.

```python
SESSION_COOKIE = "cf_session"
SESSION_MAX_AGE = 60 * 60 * 8  # 8 horas

def create_session(api_key: str) -> str:
    """Assina e serializa a API Key em cookie seguro."""

def verify_session(request: Request) -> str | None:
    """Lê o cookie e retorna a API Key ou None se inválido/expirado."""

def require_session(request: Request) -> str:
    """FastAPI Dependency — redireciona para /login se sem sessão."""
```

**Segurança:**
- Cookie com `httponly=True`, `samesite="lax"`
- Em produção: `secure=True` (HTTPS)
- Expiração de 8 horas (jornada de trabalho)
- Logout limpa o cookie

---

### E3 — Layout base (`base.html`)

**Estrutura mobile (primária):**

```
┌─────────────────────────────┐
│ [≡]  OASIS  CatalogFlow     │  ← header fixo, hambúrguer à esquerda
├─────────────────────────────┤
│                             │
│  [conteúdo da página]       │
│                             │
└─────────────────────────────┘
```

**Menu lateral (ao tocar [≡]):**

```
┌─────────────────────────────┐
│ [✕]                         │
│                             │
│   Dashboard                 │
│   Catálogos                 │
│   Pedidos                   │
│                             │
│   ─────────────────         │
│   Sair                      │
└─────────────────────────────┘
```

Menu desliza da esquerda, fundo escurece com overlay semitransparente.
Fechar ao tocar no overlay ou no [✕]. Alpine.js: `x-show`, `x-transition`, `@click.outside`.

**Estrutura desktop (≥ 768px):**

```
┌─────────────────────────────────────────────────┐
│  OASIS RESORTWEAR          [Catálogos] [Pedidos] │
│  CatalogFlow               [Sair →]              │
├─────────────────────────────────────────────────┤
│  [conteúdo da página]                            │
└─────────────────────────────────────────────────┘
```

**Elementos fixos do layout:**
- Logo textual "OASIS" em Cormorant Garamond, tracking largo
- Subtítulo "CatalogFlow" em Inter light, muted
- Fundo `#FAF8F5` em toda a página
- Conteúdo centralizado, max-width 1100px, padding lateral 16px mobile / 40px desktop
- Hambúrguer visível apenas em mobile (oculto em ≥ 768px via CSS)

---

### E4 — Tela de Login (`/login`)

**Layout:**

```
┌──────────────────────────────────┐
│                                  │
│         OASIS RESORTWEAR         │  ← Cormorant, 32px, centered
│           CatalogFlow            │  ← Inter light, muted
│                                  │
│    ┌──────────────────────────┐  │
│    │  Sua chave de acesso     │  │  ← label sutil
│    │  cf_________________     │  │  ← input elegante, bordô no focus
│    └──────────────────────────┘  │
│                                  │
│         [ Entrar → ]             │  ← botão bordô, full width
│                                  │
│    Precisa de acesso? Fale com   │
│    o administrador do sistema.   │  ← texto muted, pequeno
│                                  │
└──────────────────────────────────┘
```

**Comportamento:**
- POST `/login` valida a API Key contra `GET /api/v1/health` (chamada interna)
- Se válida: cria sessão, redireciona para `/`
- Se inválida: exibe mensagem inline "Chave de acesso inválida" (sem recarregar)
- Sem "lembrar de mim" — sessão de 8h é suficiente

---

### E5 — Dashboard (`/`)

**Layout:**

```
  Bem-vinda, Oasis Resortwear          Terça-feira, 12 de maio de 2026

  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐
  │  Catálogos │  │  Prontos   │  │  Pedidos   │  │  Romaneios │
  │     3      │  │     2      │  │     7      │  │     5      │
  │  ativos    │  │  p/ baixar │  │  recebidos │  │  gerados   │
  └────────────┘  └────────────┘  └────────────┘  └────────────┘

  Atividade recente
  ┌──────────────────────────────────────────────────────────────┐
  │  Pedido — Loja Moda & Arte       hoje 10:45    [ Ver ]       │
  │  Pedido — Boutique Riviera       hoje 09:12    [ Ver ]       │
  │  Catálogo — Inverno 26 MOTION    ontem 18:30   [ Ver ]       │
  └──────────────────────────────────────────────────────────────┘

                              [ + Novo catálogo ]
```

**Comportamento:**
- Cards com contagem buscada via `GET /api/v1/catalogs` e `GET /api/v1/orders`
- Atividade recente: últimos 5 eventos (catálogos + pedidos) ordenados por data
- Botão "Novo catálogo" leva para `/catalogs/upload`

---

### E6 — Lista de catálogos (`/catalogs`)

**Layout:**

```
  Catálogos                                    [ + Novo catálogo ]

  ┌─────────────────────────────────────────────────────────────┐
  │ Nome                    Coleção    SKUs   Status    Criado   │
  ├─────────────────────────────────────────────────────────────┤
  │ Inverno 26 MOTION       MOTION      36    ● Pronto  12/05   │
  │ Verão 26 BAHIA          BAHIA       28    ● Pronto  10/05   │
  │ Outono 26 PARIS         PARIS        —    ○ Process  12/05  │
  └─────────────────────────────────────────────────────────────┘
```

**Badges de status:**
- `● Pronto` — verde sábio, clicável (leva ao detail)
- `○ Processando` — âmbar + spinner minimalista (HTMX polling a cada 3s)
- `✕ Erro` — vermelho escuro

**Comportamento:**
- Clique em qualquer linha → `/catalogs/{id}`
- Linha com status "processando" tem HTMX `hx-get` que atualiza só aquela linha
- Paginação simples: 20 por página, navegação anterior/próxima

---

### E7 — Upload de catálogo (`/catalogs/upload`)

**Layout:**

```
  Novo catálogo

  Nome do catálogo *
  ┌─────────────────────────────────┐
  │  Inverno 26 MOTION              │
  └─────────────────────────────────┘

  Coleção (opcional)
  ┌─────────────────────────────────┐
  │  MOTION                         │
  └─────────────────────────────────┘

  Arquivo PDF *
  ┌─────────────────────────────────────────────────────────┐
  │                                                         │
  │         [ ↑ Selecionar PDF ]                            │
  │                                                         │
  │         Toque para selecionar · arraste no desktop      │
  │         PDF até 50 MB                                   │
  │                                                         │
  └─────────────────────────────────────────────────────────┘

                    [ Processar catálogo → ]
```

**Estado após envio (mesmo painel, sem trocar de página):**

```
  Processando...
  ████████████░░░░░░░░  65%

  Analisando páginas do catálogo
  Aguarde — isso leva alguns segundos
```

**Estado de conclusão:**

```
  ✓ Catálogo pronto

  36 produtos detectados · 148 campos inseridos

  [ Baixar PDF editável ]     [ Ver detalhes ]
```

**Comportamento:**
- Upload via `fetch` + `FormData` com progress event (Alpine.js)
- Polling do job via HTMX `hx-trigger="every 2s"` enquanto `status != success/error`
- Em erro: exibe o `error.code` em texto amigável (ex: "PDF protegido com senha")
- Mobile: botão "Selecionar PDF" abre seletor nativo do celular (`<input type="file" accept=".pdf">`)
- Desktop: área aceita drag & drop com highlight de borda bordô ao arrastar

---

### E8 — Detalhe do catálogo (`/catalogs/{id}`)

**Layout:**

```
  ← Catálogos

  Inverno 26 MOTION                              ● Pronto
  Coleção MOTION · 36 produtos · 148 campos · Criado 12/05/2026

                                    [ ↓ Baixar PDF editável ]

  Produtos detectados
  ┌──────────────────────────────────────────────────────────────┐
  │ SKU             Nome                  Preço     Grade  Cores │
  ├──────────────────────────────────────────────────────────────┤
  │ 0442500941-0    Vestido Joana         R$1.598   PP-G    2    │
  │ 0322500004-0    Jaqueta Berenice      R$3.488   PP-M    1    │
  │ 0142500001-0    Calça Capri Esther    R$588     PP-M    1    │
  │ ...                                                          │
  └──────────────────────────────────────────────────────────────┘
```

**Comportamento:**
- Botão "Baixar PDF editável" → `GET /api/v1/catalogs/{id}/download`
- Tabela de produtos paginada (20 por página)
- Se status "processando": exibe barra de progresso com polling (mesmo padrão do upload)

---

### E9 — Lista de pedidos (`/orders`)

**Layout:**

```
  Pedidos recebidos

  ┌─────────────────────────────────────────────────────────────┐
  │ Lojista              Catálogo       Peças   Status   Data   │
  ├─────────────────────────────────────────────────────────────┤
  │ Loja Moda & Arte     Inv 26 MOTION   39     ● Pronto  hoje  │
  │ Boutique Riviera     Inv 26 MOTION   17     ● Pronto  hoje  │
  │ Casa de Moda Bela    Inv 26 MOTION    —     ○ Process ontem │
  └─────────────────────────────────────────────────────────────┘
```

**Comportamento:**
- Clique em linha → `/orders/{id}`
- Status "processando": polling HTMX a cada 3s
- Sem filtros por ora — paginação simples

---

### E10 — Detalhe do pedido (`/orders/{id}`)

**Layout:**

```
  ← Pedidos

  Loja Moda & Arte                               ● Pronto
  Catálogo: Inverno 26 MOTION · 7 refs · 39 peças · R$ 47.316,00

                                         [ ↓ Baixar romaneio ]

  Itens do pedido

  Mobile — cards por produto (sem scroll horizontal):
  ┌─────────────────────────────┐
  │ Vestido Joana               │
  │ 0442500941-0                │
  ├──────┬──────┬──────┬────────┤
  │  PP  │  P   │  M   │  G     │
  │   2  │  4   │  2   │  2     │
  ├──────┴──────┴──────┴────────┤
  │ 10 peças · R$ 15.980,00     │
  └─────────────────────────────┘
  ┌─────────────────────────────┐
  │ Jaqueta Berenice            │
  │ 0322500004-0                │
  ├──────┬──────┬──────┬────────┤
  │  PP  │  P   │  M              │
  │   1  │  2   │  1              │
  ├──────┴──────┴────────────────┤
  │ 4 peças · R$ 13.952,00       │
  └──────────────────────────────┘

  Desktop — tabela tradicional:
  ┌──────────────────────────────────────────────────────────────┐
  │ Produto              Cor    PP   P    M    G   GG  Total     │
  ├──────────────────────────────────────────────────────────────┤
  │ Vestido Joana        Cor 1   2    4    2    2   —    10      │
  │ Jaqueta Berenice     Cor 1   1    2    1    —   —     4      │
  ├──────────────────────────────────────────────────────────────┤
  │                                        Total: 39 peças       │
  │                                        R$ 47.316,00          │
  └──────────────────────────────────────────────────────────────┘
```

**Comportamento do botão romaneio:**
- Se romaneio pronto: download direto
- Se não gerado ainda: dispara geração + exibe spinner + polling até pronto + download automático
- Tudo sem trocar de página (HTMX)

---

### E11 — Erros e estados vazios

**Estados vazios elegantes (sem dados ainda):**

```
  Nenhum catálogo ainda

  Comece enviando o catálogo da sua coleção.

            [ + Enviar primeiro catálogo ]
```

**Página de erro (404, 500):**

```
         OASIS RESORTWEAR

  Algo não correu como esperado.

  [ ← Voltar ao início ]
```

**Toast de feedback (Alpine.js, canto superior direito):**
- Sucesso: fundo verde sábio, ícone ✓, desaparece em 4s
- Erro: fundo bordô, ícone ✕, persiste até fechar

---

### E12 — Registrar a web no `main.py`

```python
# main.py
from catalogflow.web.router import router as web_router
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app.mount("/static", StaticFiles(directory="src/catalogflow/static"), name="static")
app.include_router(web_router)
```

Rota raiz `/` redireciona para `/dashboard` se sessão ativa, senão para `/login`.

---

### E13 — Testes da web

`web/tests/test_web_auth.py`:
- GET `/login` retorna 200
- POST `/login` com key válida → 302 redirect + cookie
- POST `/login` com key inválida → 200 com mensagem de erro
- GET `/` sem sessão → 302 para `/login`
- GET `/logout` → limpa cookie, redireciona para `/login`

`web/tests/test_web_pages.py`:
- GET `/` com sessão → 200
- GET `/catalogs` com sessão → 200
- GET `/catalogs/upload` com sessão → 200
- GET `/orders` com sessão → 200
- GET `/catalogs/{id}` inexistente → 404 elegante
- GET `/orders/{id}` de outra brand → 404 elegante

---

## Acceptance Criteria

| ID | Critério | Verificação |
|----|---------|------------|
| AC-01 | Login com API Key válida cria sessão e redireciona ao dashboard | Automated |
| AC-02 | Login com key inválida exibe erro inline sem recarregar | Automated |
| AC-03 | Upload de catálogo PDF mostra progresso em tempo real | Manual |
| AC-04 | Após processamento, botão "Baixar PDF editável" funciona | Manual |
| AC-05 | Lista de catálogos exibe status correto com polling automático | Manual |
| AC-06 | Upload de pedido preenchido → romaneio disponível sem trocar de página | Manual |
| AC-07 | Tabela de itens do pedido exibe grade cor × tamanho corretamente | Manual |
| AC-08 | Interface renderiza corretamente em Chrome, Edge e Safari | Manual |
| AC-09 | Interface renderiza corretamente em mobile (320px–768px) | Manual |
| AC-10 | Sessão expira após 8 horas e redireciona para login | Automated |
| AC-11 | Cores, tipografia e espaçamento seguem os tokens definidos | Manual (aprovação PMO) |
| AC-12 | Testes web passam sem quebrar cobertura ≥ 80% da suite total | CI |

---

## Definition of Done (DoD)

Uma tarefa está **pronta** quando:

- [ ] Template HTML implementado e visível no browser
- [ ] Comportamento HTMX/Alpine funcionando (sem erros no console)
- [ ] Testes escritos e passando
- [ ] Responsivo em mobile (320px mínimo)
- [ ] Aprovação visual do PMO antes do commit

A sprint está **concluída** quando:

- [ ] Todos os entregáveis E1–E13 completos
- [ ] Todos os ACs passando
- [ ] Gerente comercial da Oasis consegue operar o ciclo completo via browser
- [ ] `pytest tests/ --cov=src --cov-fail-under=80` verde
- [ ] CHANGELOG.md atualizado

---

## Out of Scope (esta sprint)

- ❌ Cadastro de novas brands via interface (continua via `/internal/`)
- ❌ Visualização de produtos com imagem (só tabela de metadados)
- ❌ Histórico de versões de catálogo
- ❌ Notificações por email ou WhatsApp
- ❌ Dashboard com gráficos e analytics
- ❌ Modo escuro
- ❌ Internacionalização

---

## Ordem de Implementação Recomendada

```
1.  pyproject.toml — adicionar jinja2, itsdangerous
2.  static/css/app.css — tokens CSS + utilitários base
3.  templates/base.html — layout, nav, CDN links (HTMX, Alpine, fontes)
4.  web/auth.py — create_session, verify_session, require_session
5.  web/router.py — rotas de login/logout + dependency require_session
6.  templates/login.html + POST /login
7.  web/tests/test_web_auth.py
8.  templates/dashboard.html + GET /
9.  templates/catalogs/list.html + GET /catalogs
10. templates/catalogs/upload.html + lógica de upload + polling
11. templates/catalogs/detail.html + GET /catalogs/{id}
12. templates/orders/list.html + GET /orders
13. templates/orders/detail.html + GET /orders/{id} + romaneio download
14. Estados vazios e páginas de erro
15. web/tests/test_web_pages.py
16. Registrar web no main.py
17. Teste manual do ciclo completo no browser
18. CHANGELOG.md + README.md
```

---

## Referências de Design

| Referência | Por quê |
|---|---|
| oasisresortwear.com.br | Identidade visual da marca — paleta, tipografia, sensação |
| @oasisresortwear (Instagram) | Linguagem visual — elegante, tropical, arejado |
| Tagline "Escape. Enjoy." | Tom da interface — calmo, sem ansiedade, eficiente |

**Fontes (Google Fonts):**
```html
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;500&family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
```
